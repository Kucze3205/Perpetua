"""Actual staged script/process → epilogue admission, without a model/provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import executor_boundary as boundary
import fakes
from fakes import common as c, loop
import test_session as sessions


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bwrap"), "requires Linux bubblewrap namespaces")
class OfflineExecutorTest(sessions.SessionCase):
    work = sessions.StageTest.work

    def setUp(self):
        super().setUp()
        shutil.rmtree(os.path.join(self.w.loop, "policy/roles/worker"))
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
        self.w.write(self.w.loop, "policy/private.txt", "POLICY_SECRET_NEVER_VISIBLE")
        self.w.write(self.w.loop, "templates/policy/SKILL.md", "POLICY_SECRET_NEVER_VISIBLE")
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

    def test_real_offline_process_cannot_read_policy_and_epilogue_accepts_binary_modes_deletion(self):
        host = Path(self.w.tmp, "host-policy-copy")
        host.write_text("POLICY_SECRET_NEVER_VISIBLE")
        oid = fakes.sh(self.w.loop, "git", "rev-parse", "HEAD:policy/roles/audit/SKILL.md")
        old = fakes.sh(self.w.loop, "git", "rev-parse", "HEAD")
        command = r'''set -eu
test "$(cat src/input.txt)" = "allowed work"
test "$(git show HEAD:src/input.txt)" = "allowed work"
python3 - <<'PY'
import os, socket, subprocess
from pathlib import Path
secret = b'POLICY_SECRET_NEVER_VISIBLE'
for path in ['policy/private.txt', '/work/policy/private.txt', 'templates/policy/SKILL.md', HOST, TRUSTED, '/proc/1/root' + HOST]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        continue
    assert secret not in data, path
for args in [('log', '-p', '--all'), ('fsck', '--full', '--no-reflogs', '--unreachable'), ('cat-file', '-p', OID), ('show', OLD + ':policy/private.txt')]:
    r = subprocess.run(['git', *args], capture_output=True)
    assert secret not in r.stdout + r.stderr
for family, target in [(socket.AF_INET, ('1.1.1.1', 80)), (socket.AF_UNIX, HOST)]:
    try:
        s = socket.socket(family)
        s.settimeout(1)
        s.connect(target)
    except OSError:
        pass
    else:
        raise AssertionError('offline socket connected')
assert not any(name in os.environ for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN'))
Path('src/product.bin').write_bytes(bytes(range(256)))
Path('src/run.sh').write_text('#!/bin/sh\necho result\n')
Path('src/run.sh').chmod(0o755)
Path('src/remove.bin').unlink()
PY
'''.replace("HOST", repr(str(host))).replace("TRUSTED", repr(self.w.loop + "/policy/private.txt")).replace("OID", repr(oid)).replace("OLD", repr(old))
        self.configure(command, write="[src/**, .gitattributes]")
        self.w.write(self.w.loop, ".gitignore", "src/*\n")
        fakes.sh(self.w.loop, "git", "add", ".gitignore")
        fakes.sh(self.w.loop, "git", "commit", "-qm", "ignore already tracked inputs")
        fakes.sh(self.w.loop, "git", "push", "-q", "origin", "HEAD:main")
        n = self.issue()
        work, output, trusted = self.run_script(n)
        self.assertFalse(Path(work, "policy").exists())
        self.assertFalse(Path(work, ".git/objects/info/alternates").exists())
        # Worker-controlled upload-pack hook, filters and fsmonitor must never run
        # in the trusted epilogue, even when its process has a GitHub credential.
        marker = os.path.join(self.w.tmp, "credential-leak")
        poison = "printf '%s' \"$GH_TOKEN\" > " + marker
        Path(work, ".git/config").write_text('[core]\n fsmonitor = "' + poison.replace('"', '\\"') + '"\n[uploadpack]\n packObjectsHook = "' + poison.replace('"', '\\"') + '"\n[filter "steal"]\n clean = "' + poison.replace('"', '\\"') + '"\n required = true\n')
        Path(work, ".gitattributes").write_text("src/* filter=steal\n")
        Path(work, ".git/hooks/pre-commit").write_text("#!/bin/sh\n" + poison)
        Path(work, ".git/hooks/pre-commit").chmod(0o755)
        os.environ["GH_TOKEN"] = "FAKE_EPILOGUE_CREDENTIAL"
        original = loop.merge_branch
        with patch.object(loop, "merge_branch", wraps=original) as merge:
            # Owner gate is the existing path; provide its verdict at that seam.
            merge.side_effect = lambda *a, **kw: original(*a, **dict(kw, override={"verdict": "accepted"}))
            loop.finalize(n, work)
        self.assertEqual(self.hub.report(n)["merged"], "true")
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(fakes.sh(self.w.origin, "git", "show", "main:src/input.txt"), "allowed work")
        self.assertEqual(subprocess.check_output(["git", "show", "main:src/product.bin"], cwd=self.w.origin), bytes(range(256)))
        self.assertIsNone(self.w.origin_file("src/remove.bin"))
        self.assertTrue(fakes.sh(self.w.origin, "git", "ls-tree", "main", "src/run.sh").startswith("100755"))
        self.assertEqual(self.w.origin_file("policy/private.txt"), "POLICY_SECRET_NEVER_VISIBLE")

    def test_data_admission_preserves_protected_scope_and_accepted_gate(self):
        for path, expected in ((".github/hacked.yml", "protected"), ("outside.txt", "scope"), ("src/product.txt", "gate-rejected")):
            with self.subTest(path=path):
                command = "mkdir -p $(dirname %s)\necho result > %s" % (path, path)
                if expected == "gate-rejected":
                    command += "\necho \"echo '{\\\"verdict\\\":\\\"accepted\\\"}'\" > gate.sh"
                gate = '''set -eu
test "$(git -C "$CANDIDATE_DIR" rev-parse HEAD)" = "$CANDIDATE_SHA"
test "$(git -C "$BASELINE_DIR" rev-parse HEAD)" = "$BASELINE_SHA"
test "$CANDIDATE_SHA" != "$BASELINE_SHA"
test -f "$CANDIDATE_DIR/src/product.txt"
test ! -f "$BASELINE_DIR/src/product.txt"
echo '{"verdict":"rejected","detail":"accepted gate ran with correct candidate and baseline"}'
'''
                self.configure(command, write="[src/**, gate.sh]", gate=gate)
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

    def test_missing_isolation_tool_does_not_execute_script_or_fallback(self):
        marker = os.path.join(self.w.tmp, "must-not-run")
        self.configure("echo fallback > " + marker)
        n = self.issue()
        work = self.work()
        trusted = os.path.join(self.w.tmp, "unavailable-trusted")
        loop.stage(n, work, trusted)
        result = subprocess.run([sys.executable, os.path.join(trusted, "executor_boundary.py"), work, trusted],
                                env=dict(os.environ, PATH="/nonexistent"), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("isolation unavailable", result.stderr)
        self.assertFalse(os.path.exists(marker))

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


class MissingIsolationTest(unittest.TestCase):
    def test_missing_namespace_tool_fails_closed(self):
        with patch.object(boundary.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "isolation unavailable"):
                boundary.launch("/unused", "/unused")
