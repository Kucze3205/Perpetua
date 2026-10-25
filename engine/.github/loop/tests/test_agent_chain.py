"""The real agent.sh path with a stand-in CLI: the prompt arrives whole and the session counts as work.

A stand-in CLI is no provider evidence (see test_model_boundary.py); it proves the engine's own chain:
stage → executor_boundary.prepare → agent.sh → CLI process → checkpoint → epilogue → accepted product.
The executor chain needs Linux, bubblewrap and a C compiler (the boundary only runs a native ELF CLI);
without them it is skipped with a reason, and a skip is an outstanding check, not a pass.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import fakes
from fakes import common as c, loop
import test_session as sessions

BIG = 200 * 1024                      # over the 128 KiB limit of one argv string on Linux
RECORDER = r'''#!/bin/sh
cat > "$RUNNER_TEMP/received-prompt"
printf '%s\n' "$@" > "$RUNNER_TEMP/received-args"
echo '{"type":"result","is_error":false,"num_turns":1}'
'''
NATIVE = r'''#include <stdio.h>
#include <unistd.h>
int main(int argc, char **argv) {
    FILE *f = fopen("/work/.session/received-prompt", "wb");
    char buf[65536];
    size_t n;
    if (!f) return 3;
    while ((n = fread(buf, 1, sizeof buf, stdin)) > 0) fwrite(buf, 1, n, f);
    fclose(f);
    f = fopen("/work/.session/received-args", "wb");
    if (!f) return 3;
    for (int i = 1; i < argc; i++) fprintf(f, "%s\n", argv[i]);
    fclose(f);
    execlp("python3", "python3", "/work/tools/fake_agent.py", (char *) 0);
    return 4;
}
'''
AGENT = r'''import json, subprocess
from pathlib import Path
# the executor repository has no remote: nothing in the session may need `origin`
assert subprocess.run(["git", "remote"], capture_output=True, text=True, check=True).stdout.strip() == ""
Path("src").mkdir(exist_ok=True)
Path("src/product.txt").write_text("agent product")
subprocess.run(["git", "add", "-f", "src/product.txt"], check=True)
subprocess.run(["git", "commit", "-qm", "agent work"], check=True)
Path(".session/report.md").write_text(MARK + "\n```yaml\nstatus: done\ncommit: abc\n```\nDid the work.\n")
print(json.dumps({"type": "result", "is_error": False, "num_turns": 1,
                  "usage": {"input_tokens": 1, "output_tokens": 1}}))
'''


class AgentChainCase(sessions.SessionCase):
    work = sessions.StageTest.work
    layer = "policy"

    def setUp(self):
        super().setUp()
        filler = "\n".join("filler line %06d" % i for i in range(BIG // 18)) + "\nEND-OF-PROMPT\n"
        self.w.push_main({self.layer + "/prompts/role.md": filler, "tools/fake_agent.py": AGENT.replace("MARK", repr(c.MARK))}, "big prompt")
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


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bwrap"), "requires Linux bubblewrap namespaces")
class ExecutorChainTest(AgentChainCase):
    layer = "execution"

    def test_restricted_role_does_work_through_the_boundary_and_it_is_admitted(self):
        compiler = shutil.which("cc") or shutil.which("gcc")
        if not compiler:
            self.skipTest("requires a C compiler: the boundary runs only a native ELF CLI")
        # not under /tmp: the namespace mounts an empty /tmp over its read-only binds
        fakebin = tempfile.mkdtemp(prefix="stand-in-cli-", dir="/var/tmp")
        self.addCleanup(shutil.rmtree, fakebin, ignore_errors=True)
        source = os.path.join(self.w.tmp, "claude.c")
        Path(source).write_text(NATIVE)
        if subprocess.run([compiler, "-O1", "-o", os.path.join(fakebin, "claude"), source]).returncode:
            self.skipTest("requires a working C toolchain to build the stand-in CLI")

        def put_stand_in_first_on_the_boundary_path(trusted):
            # The boundary resolves the CLI on its own fixed PATH; only this directory is added to it.
            module = Path(trusted, "executor_boundary.py")
            text = module.read_text()
            fixed = '"PATH": "/usr/local/bin:/usr/bin:/bin"'
            self.assertEqual(text.count(fixed), 1)
            module.write_text(text.replace(fixed, '"PATH": "%s:/usr/local/bin:/usr/bin:/bin"' % fakebin))

        n = self.issue("worker")
        work, output, trusted = self.run_agent(n, "worker", os.environ["PATH"], put_stand_in_first_on_the_boundary_path)
        self.assertTrue(Path(trusted, "executor-mode").exists())
        self.assertEqual(Path(output, "agent-exit").read_text().strip(), "0", Path(output, "claude-stderr.txt").read_text())
        sent = Path(trusted, "prompt.md").read_bytes()
        self.assertGreater(len(sent), 128 * 1024)
        self.assertEqual(Path(work, ".session", "received-prompt").read_bytes(), sent)
        self.assertIn("-p", Path(work, ".session", "received-args").read_text().splitlines())
        self.assertEqual(subprocess.run(["git", "remote"], cwd=work, capture_output=True, text=True).stdout.strip(), "")
        original = loop.merge_branch
        with patch.object(loop, "merge_branch", wraps=original) as merge:
            merge.side_effect = lambda *a, **kw: original(*a, **dict(kw, override={"verdict": "accepted"}))
            loop.finalize(n, work)
        report = self.hub.report(n)
        self.assertEqual((report["status"], report["turns"], report["merged"]), ("done", "1", "true"))
        self.assertEqual(fakes.sh(self.w.origin, "git", "show", "main:src/product.txt"), "agent product")


if __name__ == "__main__":
    unittest.main()
