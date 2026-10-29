"""Session instruction precedence and the engine-owned tool allowlist."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from fakes import loop
import session_security as security
from session_fixtures import SessionCase, read


class SecurityTest(unittest.TestCase):
    def test_profiles_are_validated(self):
        for profile in ({"agent": "false"}, {"tools": "Bash"},
                        {"tools": ["mcp__server__exec"]}, {"tools": ["Agent"]}):
            self.assertIsNotNone(security.profile_error(profile), profile)
        self.assertIsNone(security.profile_error({}))
        self.assertIsNone(security.profile_error({"agent": False}))
        self.assertIsNone(security.profile_error({"tools": ["WebFetch"]}))

    def test_the_allowlist_refuses_mcp_and_subagent_tools_for_every_profile(self):
        for profile in ({}, {"tools": ["WebFetch"]}, {"tools": ["Bash", "Read"]}):
            settings = security.settings(profile, "/trusted")
            self.assertNotIn("sandbox", settings)
            self.assertTrue({"Agent", "Task", "Monitor", "PowerShell"} <= set(settings["permissions"]["deny"]))
        for tool in ("Agent", "Task", "mcp__server__exec"):
            self.assertIsNotNone(security.profile_error({"tools": [tool]}), tool)


class CompositionTest(SessionCase):
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
                        startup = json.loads(read(os.path.join(work, ".session/startup.json")))
                        self.assertIn(".session/policymaker.md", [doc["path"] for doc in startup["documents"]])
                        self.assertIn(".session/policymaker.md", prompt)
                        self.assertNotIn("Prompt " + revision, prompt)
                        prompt += read(os.path.join(work, ".session", "policymaker.md"))
                    self.assertIn("Required files, formats, field ownership", contract)
                    self.assertEqual(prompt.count("Rules " + revision), 1)
                    self.assertIn("Prompt " + revision, prompt)
                    self.assertNotIn("Unaccepted", contract + prompt)
                    self.assertNotIn("Untrusted contract", contract)
                    self.assertNotIn("Rules " + revision, contract)
                    self.assertNotIn("Required files, formats, field ownership", prompt)
                    self.assertFalse(os.path.exists(os.path.join(trusted, "network-mode")))
