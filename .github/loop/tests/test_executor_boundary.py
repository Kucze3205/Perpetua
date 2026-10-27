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
        self.w.write(self.w.loop, "policy/SKILL.md", "POLICY_SECRET_NEVER_VISIBLE")
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
        # These are real host copies, including runtime parents, not just /tmp.
        runtime_copies = [Path(parent, "perpetua-policy-canary.txt") for parent in
                          ("/usr/local/share", "/usr/bin", "/bin", "/lib", "/lib64",
                           str(Path(os.__file__).parent)) if Path(parent).is_dir()]
        for copy in runtime_copies:
            copy.write_text("POLICY_SECRET_NEVER_VISIBLE")
            self.addCleanup(copy.unlink, missing_ok=True)
        command = r'''set -eu
test "$(cat src/input.txt)" = "allowed work"
test "$(git show HEAD:src/input.txt)" = "allowed work"
python3 - <<'PY'
import os, socket, subprocess
from pathlib import Path
secret = b'POLICY_SECRET_NEVER_VISIBLE'
for path in ['policy/private.txt', '/work/policy/private.txt', HOST, TRUSTED, '/proc/1/root' + HOST, *RUNTIME_COPIES]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        continue
    assert secret not in data, path
for args in [('log', '-p', '--all'), ('fsck', '--full', '--no-reflogs', '--unreachable'), ('cat-file', '-p', OID), ('show', OLD + ':policy/private.txt')]:
    r = subprocess.run(['git', *args], capture_output=True)
    assert secret not in r.stdout + r.stderr
for oid in (OID, OLD):
    assert subprocess.run(['git', 'cat-file', '-e', oid], capture_output=True).returncode != 0
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
git add -f src/product.bin src/run.sh
git add -u src/remove.bin
git commit -qm 'Useful local Git work'
test "$(git show HEAD:src/run.sh)" = '#!/bin/sh
echo result'
'''.replace("HOST", repr(str(host))).replace("TRUSTED", repr(self.w.loop + "/policy/private.txt")).replace("RUNTIME_COPIES", repr([str(p) for p in runtime_copies]))
        self.configure(command, write="[src/**, .gitattributes]")
        oid = fakes.sh(self.w.loop, "git", "rev-parse", "HEAD:policy/private.txt")
        old = fakes.sh(self.w.loop, "git", "rev-parse", "HEAD")
        self.assertEqual(fakes.sh(self.w.loop, "git", "cat-file", "-p", oid), "POLICY_SECRET_NEVER_VISIBLE")
        self.assertEqual(fakes.sh(self.w.loop, "git", "show", old + ":policy/private.txt"), "POLICY_SECRET_NEVER_VISIBLE")
        # Pin the canary-bearing objects after configuration, before staging.
        profile = Path(self.w.loop, "execution/profiles.yml")
        profile.write_text(profile.read_text().replace("OID", repr(oid)).replace("OLD", repr(old)))
        self.w.write(self.w.loop, ".gitignore", "src/*\n")
        fakes.sh(self.w.loop, "git", "add", ".gitignore", "execution/profiles.yml")
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

    def test_candidate_check_cannot_export_policy_in_final_report(self):
        command = """cat > src/check.py <<'PY'
from pathlib import Path
try:
    print(Path('policy/private.txt').read_text())
except OSError:
    print('private input unavailable')
raise SystemExit(1)
PY
"""
        self.configure(command)
        with open(Path(self.w.loop, "engine.yml"), "a") as stream:
            stream.write("check: python3 src/check.py\n")
        fakes.sh(self.w.loop, "git", "add", "engine.yml")
        fakes.sh(self.w.loop, "git", "commit", "-qm", "accepted product check")
        fakes.sh(self.w.loop, "git", "push", "-q", "origin", "HEAD:main")
        n = self.issue()
        work, _, _ = self.run_script(n)
        loop.finalize(n, work)
        self.assertNotIn("POLICY_SECRET_NEVER_VISIBLE", self.hub.prose(n))
        self.assertNotIn("POLICY_SECRET_NEVER_VISIBLE", str(self.hub.report(n)))
        self.assertIn("private input unavailable", self.hub.prose(n))

    def test_missing_check_isolation_cannot_fallback_to_host(self):
        marker = Path(self.w.tmp, "check-must-not-run")
        self.configure("echo result > src/product.txt")
        with open(Path(self.w.loop, "engine.yml"), "a") as stream:
            stream.write("check: touch " + str(marker) + "\n")
        fakes.sh(self.w.loop, "git", "add", "engine.yml")
        fakes.sh(self.w.loop, "git", "commit", "-qm", "accepted check")
        fakes.sh(self.w.loop, "git", "push", "-q", "origin", "HEAD:main")
        n = self.issue()
        work, _, _ = self.run_script(n)
        with patch.object(boundary.shutil, "which", return_value=None):
            loop.finalize(n, work)
        self.assertFalse(marker.exists())
        self.assertEqual(self.hub.report(n)["merged"], "false")
        self.assertIn("isolation unavailable", self.hub.prose(n))

    def test_useful_check_and_verification_run_in_product_view(self):
        command = """cat > src/check.py <<'PY'
from pathlib import Path
import subprocess
assert Path('src/input.txt').read_text() == 'allowed work'
assert not Path('policy').exists()
assert subprocess.check_output(['git', 'show', 'HEAD:src/input.txt']).decode() == 'allowed work'
Path('src/check-output.txt').write_text('temporary')
print('useful check passed')
PY
"""
        self.configure(command)
        with open(Path(self.w.loop, "engine.yml"), "a") as stream:
            stream.write("check: python3 src/check.py\n")
        fakes.sh(self.w.loop, "git", "add", "engine.yml")
        fakes.sh(self.w.loop, "git", "commit", "-qm", "accepted product check")
        fakes.sh(self.w.loop, "git", "push", "-q", "origin", "HEAD:main")
        n = self.issue()
        work, _, _ = self.run_script(n)
        self.assertTrue(loop.fetch_candidate(work, n)[0])
        with loop.Tree("task/%s" % n) as candidate:
            ok, detail = loop.run_verification(candidate, "## Verification\n`python3 src/check.py`", role="worker")
            self.assertTrue(ok, detail)
            self.assertFalse(Path(candidate, "src/check-output.txt").exists())
        with patch.object(loop.it, "run_gate", return_value={"verdict": "accepted"}):
            result = loop.merge_branch(n, "worker", self.hub.issue(n))
        self.assertEqual(result["result"], "merged", result)
        self.assertIsNone(self.w.origin_file("src/check-output.txt"))

    def test_verification_cannot_read_full_candidate_or_host(self):
        host = Path(self.w.tmp, "verification-host-private")
        host.write_text("POLICY_SECRET_NEVER_VISIBLE")
        command = """cat > src/verify.py <<'PY'
from pathlib import Path
for path in ['policy/private.txt', HOST]:
    try:
        print(Path(path).read_text())
    except OSError:
        pass
print('private input unavailable')
raise SystemExit(1)
PY
""".replace('HOST', repr(str(host)))
        self.configure(command)
        n = self.issue()
        work, _, _ = self.run_script(n)
        self.assertTrue(loop.fetch_candidate(work, n)[0])
        with loop.Tree("task/%s" % n) as candidate:
            ok, detail = loop.run_verification(candidate, "## Verification\n`python3 src/verify.py`", role="worker")
        self.assertFalse(ok)
        self.assertNotIn("POLICY_SECRET_NEVER_VISIBLE", detail)
        self.assertIn("private input unavailable", detail)

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


class SandboxStubTest(sessions.SessionCase):
    """The CLI sandbox creates empty placeholder files beside the agent's work; real stage -> real admission."""
    work = sessions.StageTest.work
    STUBS = (".env", ".env.local", ".env.development", ".env.development.local", ".env.test", ".env.test.local",
             ".env.production", ".env.production.local", ".npmrc", ".yarnrc", ".yarnrc.yml", "bunfig.toml",
             "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", ".gitmodules")

    def admit(self, **files):
        self.w.write(self.w.loop, "src/input.txt", "allowed work")
        fakes.sh(self.w.loop, "git", "add", "-A")
        fakes.sh(self.w.loop, "git", "commit", "-qm", "fixture")
        fakes.sh(self.w.loop, "git", "push", "-q", "origin", "HEAD:main")
        n = self.issue()
        work = self.work()
        trusted = os.path.join(self.w.tmp, "run", "trusted")
        os.environ["RUNNER_TEMP"] = os.path.dirname(trusted)
        for root, _, names in os.walk(os.path.join(work, ".git")):  # Windows: prepare() removes the clone's read-only objects
            for name in names:
                os.chmod(os.path.join(root, name), 0o644)
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

    def test_a_real_file_outside_write_is_still_refused_beside_the_placeholders(self):
        n, work = self.admit(**{"outside.txt": "x", "package.json": "{}"})
        self.assertTrue(loop.fetch_candidate(work, n)[0])
        detail = loop.merge_branch(n, "worker", self.hub.issue(n))["detail"]
        self.assertIn("outside.txt", detail)
        self.assertIn("package.json", detail)  # content makes it the agent's file
        self.assertNotIn(".npmrc", detail)
        self.assertNotIn("yarn.lock", detail)


class MissingIsolationTest(unittest.TestCase):
    def test_restricted_model_and_online_admission_require_engine_metadata(self):
        for profile in ({"agent": True, "internet": False}, {"agent": True, "internet": True},
                        {"agent": False, "internet": True}):
            with self.subTest(profile=profile), \
                    patch.object(loop.os.path, "isfile", return_value=False), \
                    patch.object(c, "issue", return_value={"labels": [{"name": "role:worker"}]}), \
                    patch.object(loop, "profile_of", return_value=profile), \
                    patch.object(loop.session_inputs, "restricted", return_value=True), \
                    patch.object(c, "git") as worker_git:
                self.assertEqual(loop.fetch_candidate("/unused", 40), (None, []))
                worker_git.assert_not_called()

    def test_restricted_verification_fails_closed_without_running_host_command(self):
        with patch.object(loop.session_inputs, "restricted", return_value=True), \
                patch.object(boundary, "run_candidate", return_value=subprocess.CompletedProcess('cmd', 1, '', 'Required candidate check isolation unavailable')), \
                patch.object(c, "run_command") as host:
            ok, detail = loop.run_verification('/unused', '## Verification\n`echo check`', role='worker')
        self.assertFalse(ok)
        self.assertIn('isolation unavailable', detail)
        host.assert_not_called()

    def test_missing_namespace_tool_fails_closed(self):
        with patch.object(boundary.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "isolation unavailable"):
                boundary.launch("/unused", "/unused")
