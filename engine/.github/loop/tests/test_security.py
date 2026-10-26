"""Session instruction precedence and network boundaries; live checks require a Linux runner."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fakes
from fakes import loop
import session_security as security
import test_session
from test_session import SessionCase, read


class SecurityTest(unittest.TestCase):
    def test_offline_and_script_profiles_are_validated(self):
        for profile in ({"internet": "false"}, {"agent": "false"}, {"tools": "Bash"},
                        {"tools": ["WebFetch"]}, {"tools": ["mcp__server__exec"]}, {"tools": ["Agent"]}):
            self.assertIsNotNone(security.profile_error(profile, "2.1.284"), profile)
        self.assertIsNotNone(security.profile_error({}, "2.1.100"))
        self.assertIsNone(security.profile_error({"agent": False}, "2.1.100"))
        self.assertIsNone(security.profile_error({"internet": True, "tools": ["WebFetch"]}, "2.1.284"))

    def test_offline_settings_have_no_retry_or_proxy_escape(self):
        settings = security.settings({}, "/trusted")
        sandbox = settings["sandbox"]
        self.assertTrue(sandbox["enabled"] and sandbox["failIfUnavailable"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertEqual(sandbox["excludedCommands"], [])
        self.assertEqual(sandbox["network"]["allowedDomains"], [])
        self.assertTrue(sandbox["network"]["strictAllowlist"])
        self.assertNotIn("sandbox", security.settings({"internet": True}, "/trusted"))

    def test_offline_skills_cannot_run_commands_outside_bash(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "SKILL.md")
            for text in ("---\nname: sample\nhooks: {PreToolUse: []}\n---\nx", "---\nname: sample\n---\n!`curl example.com`"):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(SystemExit):
                    security.check_skills(root)


class CompositionTest(SessionCase):
    work = test_session.StageTest.work

    def test_staging_refuses_redirected_session_directories_before_writing(self):
        n = self.issue()
        for name in (".session", ".session/inputs"):
            with self.subTest(path=name):
                work = self.work()
                target = tempfile.mkdtemp(dir=self.w.tmp)
                link = os.path.join(work, *name.split("/"))
                os.makedirs(os.path.dirname(link), exist_ok=True)
                if os.name == "nt":
                    import _winapi
                    _winapi.CreateJunction(target, link)
                else:
                    os.symlink(target, link, target_is_directory=True)
                with self.assertRaisesRegex(SystemExit, "session.*redirect|redirect.*session"):
                    loop.stage(n, work, os.path.join(self.w.tmp, "redirected-trusted"))
                self.assertEqual(os.listdir(target), [])

    def test_next_stage_uses_accepted_policy_rules_but_keeps_engine_contract(self):
        for role in ("worker", "policymaker"):
            n = self.issue(role)
            prompt_name = "policymaker" if role == "policymaker" else "role"
            layer = "policy" if role == "policymaker" else "execution"
            for revision in ("first", "second"):
                with self.subTest(role=role, revision=revision):
                    self.w.write(self.w.loop, layer + "/protocol.md", "Rules " + revision)
                    self.w.write(self.w.loop, layer + "/prompts/%s.md" % prompt_name, "Prompt " + revision)
                    work = self.work()
                    # A task branch cannot supply its own engine contract or accepted policy.
                    self.w.write(work, ".github/loop/policymaker.md", "Untrusted contract")
                    self.w.write(work, layer + "/protocol.md", "Unaccepted rules")
                    self.w.write(work, layer + "/prompts/%s.md" % prompt_name, "Unaccepted prompt")
                    trusted = os.path.join(self.w.tmp, role + "-" + revision)
                    loop.stage(n, work, trusted)
                    self.assertFalse(os.path.exists(os.path.join(work, ".session", "protocol.md")))
                    contract = read(os.path.join(trusted, "contract.md"))
                    prompt = read(os.path.join(trusted, "prompt.md"))
                    if role == "policymaker":
                        self.assertIn("Read .session/policymaker.md", prompt)
                        self.assertNotIn("Prompt " + revision, prompt)
                        prompt += read(os.path.join(work, ".session", "policymaker.md"))
                    self.assertIn("Required files, formats, field ownership", contract)
                    self.assertEqual(prompt.count("Rules " + revision), 1)
                    self.assertIn("Prompt " + revision, prompt)
                    self.assertNotIn("Unaccepted", contract + prompt)
                    self.assertNotIn("Untrusted contract", contract)
                    self.assertNotIn("Rules " + revision, contract)
                    self.assertNotIn("Required files, formats, field ownership", prompt)
                    self.assertEqual(read(os.path.join(trusted, "network-mode")), "offline\n")


@unittest.skipUnless(sys.platform == "linux", "requires Linux kernel network isolation")
class OfflineKernelTest(unittest.TestCase):
    def test_script_socket_restriction_survives_exec_and_subprocesses(self):
        helper = os.path.join(fakes.LOOP_DIR, "offline_exec.py")
        code = """import socket, subprocess, sys
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
    try:
        socket.socket(family)
    except PermissionError:
        continue
    raise SystemExit('socket creation was allowed')
subprocess.run([sys.executable, '-c', 'import socket; socket.socket()'], check=False).returncode == 0 and sys.exit(1)
print('blocked')
"""
        result = subprocess.run([sys.executable, helper, sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("blocked", result.stdout)

    @unittest.skipUnless(os.environ.get("LOOP_NETWORK_INTEGRATION") == "1" and
                         os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), "opt in with LOOP_NETWORK_INTEGRATION=1 and provider credential")
    def test_real_provider_works_while_bash_python_and_raw_sockets_are_offline(self):
        # Run the actual pinned CLI with the same settings/flags as agent.sh. No simulated provider.
        import urllib.request
        import socket
        import yaml
        pin = yaml.safe_load(Path(fakes.ENGINE, "engine.yml").read_text())["cli_version"]
        version = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=True).stdout
        self.assertTrue(version.startswith(pin + " ") or version.strip() == pin, "install the template-pinned CLI")
        with urllib.request.urlopen("https://example.com", timeout=15) as response:
            self.assertEqual(response.status, 200)  # control: the host has working network
        with tempfile.TemporaryDirectory() as root, socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(8)
            loopback_port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", loopback_port), timeout=3):
                pass  # control: this listener is reachable outside the tool sandbox
            work, trusted = Path(root, "work"), Path(root, "trusted")
            work.mkdir()
            trusted.mkdir()
            probe = work / "probe.py"
            probe.write_text("""import json, socket, urllib.request
results = {}
for name, action in [('http', lambda: urllib.request.urlopen('https://example.com', timeout=3)),
                     ('ipv4', lambda: socket.create_connection(('1.1.1.1', 443), timeout=3)),
                     ('loopback', lambda: socket.create_connection(('127.0.0.1', 9), timeout=3))]:
    try:
        action()
        results[name] = 'ALLOWED'
    except OSError:
        results[name] = 'blocked'
open('proof.json', 'w').write(json.dumps(results))
""".replace("127.0.0.1', 9", "127.0.0.1', " + str(loopback_port)), encoding="utf-8")
            shutil.copy(os.path.join(fakes.LOOP_DIR, "remaining.py"), trusted)
            settings = trusted / "settings.json"
            settings.write_text(json.dumps(security.settings({}, str(trusted))), encoding="utf-8")
            env = dict(os.environ, CLAUDE_CODE_SUBPROCESS_ENV_SCRUB="1", CLAUDE_CODE_DISABLE_BACKGROUND_TASKS="1")
            command = ["claude", "-p", "Use Bash to run python3 probe.py exactly once, then finish. Do not write proof.json yourself.",
                       "--model", "claude-sonnet-5-5", "--max-turns", "5", "--tools", "Bash",
                       "--allowedTools", "Bash(python3:*)", "--setting-sources", "", "--strict-mcp-config",
                       "--disable-slash-commands",
                       "--mcp-config", '{"mcpServers":{}}', "--settings", str(settings), "--output-format", "json"]
            result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, "provider or sandbox integration failed")
            output = json.loads(result.stdout)
            self.assertFalse(output.get("is_error"), "real provider session failed")
            self.assertEqual(json.loads((work / "proof.json").read_text()),
                             {"http": "blocked", "ipv4": "blocked", "loopback": "blocked"})
