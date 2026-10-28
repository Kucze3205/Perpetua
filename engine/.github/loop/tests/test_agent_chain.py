"""The real agent.sh path with a stand-in CLI: the prompt arrives whole and the session counts as work.

A stand-in CLI is no provider evidence; it proves the engine's own chain:
stage → agent.sh → CLI process → checkpoint.
"""
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from fakes import common as c, loop
import test_session as sessions

BIG = 200 * 1024                      # over the 128 KiB limit of one argv string on Linux
RECORDER = r'''#!/bin/sh
cat > "$RUNNER_TEMP/received-prompt"
printf '%s\n' "$@" > "$RUNNER_TEMP/received-args"
echo '{"type":"result","is_error":false,"num_turns":1}'
'''


class AgentChainCase(sessions.SessionCase):
    work = sessions.StageTest.work

    def setUp(self):
        super().setUp()
        filler = "\n".join("filler line %06d" % i for i in range(BIG // 18)) + "\nEND-OF-PROMPT\n"
        self.w.push_main({"policy/prompts/role.md": filler}, "big prompt")
        c.sync_main()

    def run_agent(self, n, role, path, patch_trusted=None, extra_env=None):
        work = self.work()
        output = os.path.join(self.w.tmp, "run")
        trusted = os.path.join(output, "trusted")
        os.environ["RUNNER_TEMP"] = output
        loop.stage(n, work, trusted)
        if patch_trusted:
            patch_trusted(trusted)
        env = dict(os.environ, WORK=work, KIND="agent", ISSUE=str(n), AGENT_TIMEOUT="1", MODEL="m1", EFFORT="high",
                   MAX_TURNS="5", TOOLS="Read,Write", PATH=path, **(extra_env or {}))
        result = subprocess.run(["bash", os.path.join(trusted, "agent.sh")], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return work, output, trusted


@unittest.skipIf(os.name == "nt", "requires a POSIX shell: bash on Windows may be WSL")
class PlainPromptTest(AgentChainCase):
    def test_a_prompt_over_the_argv_limit_reaches_the_cli_whole(self):
        n = self.issue("audit")
        fakebin = os.path.join(self.w.tmp, "bin")
        os.makedirs(fakebin)
        Path(fakebin, "claude").write_text(RECORDER, newline="\n")
        os.chmod(os.path.join(fakebin, "claude"), 0o755)
        work, output, trusted = self.run_agent(n, "audit", fakebin + os.pathsep + os.environ["PATH"])
        sent = Path(trusted, "prompt.md").read_bytes()
        self.assertGreater(len(sent), 128 * 1024)
        self.assertEqual(Path(output, "received-prompt").read_bytes(), sent)
        args = Path(output, "received-args").read_text().splitlines()
        self.assertIn("-p", args)
        self.assertLess(max(len(a) for a in args), 4096)
        self.assertEqual(Path(output, "agent-exit").read_text().strip(), "0")


    def stand_in(self, body):
        fakebin = os.path.join(self.w.tmp, "bin")
        os.makedirs(fakebin)
        Path(fakebin, "claude").write_text(body, newline="\n")
        os.chmod(os.path.join(fakebin, "claude"), 0o755)
        return fakebin + os.pathsep + os.environ["PATH"]

    def stage_secrets(self, approved):
        def put(trusted):
            Path(trusted, "secrets").write_text("".join(x + "\n" for x in approved))
        return put

    def test_only_the_approved_secret_reaches_the_agent(self):
        n = self.issue("audit")
        path = self.stand_in(RECORDER + 'env > "$RUNNER_TEMP/agent-env"\n')
        # What the workflow hands over: an unapproved pool name is `''` (`contains(...) && secrets.B || ''`), not a value.
        env = {"A": "va", "B": "", "GH_TOKEN": "gh", "GITHUB_TOKEN": "gh"}
        _, output, _ = self.run_agent(n, "audit", path, self.stage_secrets(["A"]), env)
        seen = Path(output, "agent-env").read_text()
        self.assertIn("A=va", seen.splitlines())
        self.assertIn("B=", seen.splitlines())
        names = {line.split("=", 1)[0] for line in seen.splitlines()}
        self.assertFalse(names & {"GH_TOKEN", "GITHUB_TOKEN"})

    def test_an_empty_approved_secret_stops_the_start(self):
        n = self.issue("audit")
        path = self.stand_in(RECORDER)
        _, output, _ = self.run_agent(n, "audit", path, self.stage_secrets(["A"]), {"A": ""})
        self.assertEqual(Path(output, "secret-missing").read_text().strip(), "A")
        self.assertEqual(Path(output, "agent-exit").read_text().strip(), "1")
        self.assertFalse(Path(output, "received-prompt").exists())


if __name__ == "__main__":
    unittest.main()
