"""Actual staged script/process â†’ epilogue admission, without a model/provider."""
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


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bwrap"), "requires Linux bubblewrap namespaces")
class OfflineExecutorTest(sessions.SessionCase):
    work = sessions.StageTest.work

    def setUp(self):
        super().setUp()
        for path, text in {
            "engine.yml": "resources: {format: layered}\n",
            "policy/params.yml": fakes.POLICY_PARAMS.replace("orchestrator", "worker"),
            "execution/profiles.yml": "worker: {agent: false, tools: [], write: [src/**], command: echo work}\n",
            "policy/profiles.yml": "audit: {tools: [Read], write: [audit/**]}\n",
            "execution/roles/worker/SKILL.md": "---\nname: worker\ndescription: fixture\n---\nExecution role\n",
            "policy/roles/policymaker/SKILL.md": "Policy role\n",
        }.items():
            self.w.write(self.w.loop, path, text)

    def configure(self, command, write="[src/**]", gate=None):
        self.w.write(self.w.loop, "execution/profiles.yml",
                     "worker:\n  agent: false\n  tools: []\n  write: %s\n  command: |\n" % write +
                     "\n".join("    " + line for line in command.splitlines()) + "\n")
        self.w.write(self.w.loop, "src/input.txt", "allowed work")
        self.w.write(self.w.loop, "src/remove.bin", "remove me")
        self.w.write(self.w.loop, "outside.txt", "tracked, outside the write scope")
        if gate:
            self.w.write(self.w.loop, "engine.yml", "resources: {format: layered}\ngate:\n  kind: script\n  command: bash gate.sh\n")
            self.w.write(self.w.loop, "gate.sh", gate)
        fakes.sh(self.w.loop, "git", "add", "-A")
        fakes.sh(self.w.loop, "git", "commit", "-qm", "offline script fixture")
        fakes.sh(self.w.loop, "git", "push", "-q", "origin", "HEAD:main")

    def run_script(self, n):
        work = self.work()
        output = os.path.join(self.w.tmp, "run")
        trusted = os.path.join(output, "trusted")
        os.environ["RUNNER_TEMP"] = output
        loop.stage(n, work, trusted)
        env = dict(os.environ, WORK=work, KIND="script", ISSUE=str(n), AGENT_TIMEOUT="1")
        result = subprocess.run(["bash", os.path.join(trusted, "agent.sh")], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(output, "agent-exit").read_text().strip(), "0",
                         Path(output, "script-output.txt").read_text())
        return work, output, trusted

    def test_data_admission_preserves_protected_scope_and_accepted_gate(self):
        for path, expected in ((".github/hacked.yml", "protected"), ("outside.txt", "scope"), ("src/product.txt", "gate-rejected")):
            with self.subTest(path=path):
                command = "mkdir -p $(dirname %s)\necho result > %s" % (path, path)
                if expected == "gate-rejected":
                    command += "\necho \"echo '{\\\"verdict\\\":\\\"accepted\\\"}'\" > gate.sh"
                gate = '''set -eu
test -n "$CANDIDATE_SHA" -a -n "$BASELINE_SHA"
test "$PWD" = "$BASELINE_DIR"
test "$CANDIDATE_SHA" != "$BASELINE_SHA"
test -f "$CANDIDATE_DIR/src/product.txt"
test ! -f "$BASELINE_DIR/src/product.txt"
echo '{"verdict":"rejected","detail":"accepted gate ran with correct candidate and baseline"}'
'''
                self.configure(command, write="[src/**, gate.sh, .github/**]", gate=gate)
                n = self.issue()
                work, _, _ = self.run_script(n)
                sha, touched = loop.fetch_candidate(work, n)
                self.assertTrue(sha)
                if expected == "protected":
                    self.assertEqual(touched, [path])
                else:
                    result = loop.merge_branch(n, "worker", self.hub.issue(n))
                    self.assertEqual(result["result"], expected)
                    if expected == "gate-rejected":
                        self.assertIn("correct candidate and baseline", result["detail"])

    def test_redirected_output_is_refused_without_reading_host(self):
        self.configure("ln -s /etc/passwd src/escape")
        n = self.issue()
        work, _, _ = self.run_script(n)
        self.assertEqual(loop.fetch_candidate(work, n), (None, []))

    def test_artifact_directory_redirect_does_not_export_host_policy_but_real_artifact_uploads(self):
        for redirected in (True, False):
            with self.subTest(redirected=redirected):
                command = "echo result > src/product.txt\n"
                command += ("ln -s " + self.w.loop + "/policy .session/artifacts" if redirected else
                            "mkdir .session/artifacts\necho legitimate-artifact > .session/artifacts/product.txt")
                self.configure(command)
                n = self.issue()
                work, _, _ = self.run_script(n)
                before = len(self.hub.gh_calls)
                with patch.object(loop.it, "run_gate", return_value={"verdict": "accepted"}):
                    loop.finalize(n, work)
                calls = self.hub.gh_calls[before:]
                uploads = [call for call in calls if call[:2] == ("release", "upload")]
                if redirected:
                    self.assertFalse(uploads)
                    self.assertNotIn("POLICY_SECRET_NEVER_VISIBLE", self.hub.prose(n))
                else:
                    self.assertEqual(len(uploads), 1)
                    self.assertEqual(Path(uploads[0][3]).read_text(), "legitimate-artifact\n")


class SandboxStubTest(sessions.SessionCase):
    """The CLI sandbox creates empty placeholder files beside the agent's work; real stage -> real admission."""
    work = sessions.StageTest.work
    STUBS = (".env", ".env.local", ".env.development", ".env.development.local", ".env.test", ".env.test.local",
             ".env.production", ".env.production.local", ".npmrc", ".yarnrc", ".yarnrc.yml", "bunfig.toml",
             "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", ".gitmodules")

    def admit(self, **files):
        self.w.write(self.w.loop, "src/input.txt", "allowed work")
        self.w.write(self.w.loop, "outside.txt", "tracked, outside the write scope")
        fakes.sh(self.w.loop, "git", "add", "-A")
        fakes.sh(self.w.loop, "git", "commit", "-qm", "fixture")
        fakes.sh(self.w.loop, "git", "push", "-q", "origin", "HEAD:main")
        n = self.issue()
        work = self.work()
        trusted = os.path.join(self.w.tmp, "run", "trusted")
        os.environ["RUNNER_TEMP"] = os.path.dirname(trusted)
        loop.stage(n, work, trusted)
        self.assertTrue(os.path.isfile(os.path.join(trusted, "executor-baseline.json")), "role is not executor-isolated")
        for name in self.STUBS:
            Path(work, name).write_bytes(b"")
        for name in (".claude/agents", ".claude/commands", "node_modules/.bin"):
            os.makedirs(os.path.join(work, name))
        for name, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(work, name)) or work, exist_ok=True)
            Path(work, name).write_text(text)
        return n, work

    def test_empty_sandbox_placeholders_are_not_part_of_the_change(self):
        n, work = self.admit(**{"src/product.txt": "result"})
        self.assertTrue(loop.fetch_candidate(work, n)[0])
        changed = fakes.sh(self.w.loop, "git", "diff", "--name-only", "task/%s^" % n, "task/%s" % n).split()
        self.assertEqual(changed, ["src/product.txt"])

    def test_a_tracked_file_outside_write_is_still_refused_beside_the_placeholders(self):
        n, work = self.admit(**{"outside.txt": "x"})
        self.assertTrue(loop.fetch_candidate(work, n)[0])
        detail = loop.merge_branch(n, "worker", self.hub.issue(n))["detail"]
        self.assertIn("outside.txt", detail)
        self.assertNotIn(".npmrc", detail)
        self.assertNotIn("yarn.lock", detail)

