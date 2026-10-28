"""Session resolution and staging on real temporary repositories."""
import json
import os
import unittest
from unittest.mock import patch

from fakes import common as c, iteration as it, loop
import owner_requests as requests
from session_fixtures import SessionCase, read


class ResolveTest(SessionCase):
    def test_labels_and_limits_decide_the_session(self):
        n = self.issue(labels=["model:opus", "effort:high"], body='## Goal\nx\n<!-- limits: {"timeout_minutes": 30, "max_turns": 20} -->\n')
        loop.resolve(n)
        o = self.outputs()
        self.assertEqual((o["kind"], o["role"], o["model"], o["effort"], o["start"]), ("agent", "worker", "claude-opus-5-5", "high", "main"))
        self.assertEqual((o["agent_timeout"], o["max_turns"], o["job_timeout"]), ("30", "20", "180"))     # + 120 gate + 30 epilogue
        self.assertEqual(o["tools"], "Read,Grep,Glob,Write,Edit,Skill")
        self.assertEqual((o["consented"], o["iteration"]), ("false", "0001"))

    def test_defaults_come_from_the_profile_and_the_policy_params(self):
        n = self.issue()
        loop.resolve(n)
        o = self.outputs()
        self.assertEqual((o["model"], o["effort"], o["agent_timeout"], o["max_turns"]), ("claude-sonnet-5-5", "medium", "205", "150"))

    def test_a_task_cannot_exceed_the_ceilings(self):
        n = self.issue(body='<!-- limits: {"timeout_minutes": 9999, "max_turns": 9999} -->\n')
        loop.resolve(n)
        o = self.outputs()
        self.assertEqual((o["agent_timeout"], o["max_turns"]), ("205", "150"))      # 355 - 30 - 120 for the gate

    def test_the_policymaker_session_uses_the_engines_own_profile(self):
        n = self.hub.create_issue("P", "b", labels=["role:policymaker", "loop:iteration 1"])
        loop.resolve(n)
        o = self.outputs()
        self.assertEqual((o["model"], o["effort"], o["max_turns"]), ("claude-opus-5-5", "high", "150"))

    def test_a_script_role_is_a_script(self):
        n = self.issue("rule")
        loop.resolve(n)
        self.assertEqual(self.outputs()["kind"], "script")

    def test_the_start_branch_is_the_marker_or_the_existing_task_branch(self):
        n = self.issue(body="x\n<!-- start-branch: task/77 -->\n")
        loop.resolve(n)
        self.assertEqual(self.outputs()["start"], "task/77")
        self.w.branch(n, {"src/a.txt": "x"})
        loop.resolve(n)
        self.assertEqual(self.outputs()["start"], "task/%s" % n)

    def test_refusals_close_the_issue_as_blocked(self):
        for role, why in (("nobody", "no profile"), ("ghost", "no skill"), ("cmdless", "no command")):
            n = self.issue(role)
            with self.assertRaises(SystemExit):
                loop.resolve(n)
            self.assertEqual(self.hub.report(n)["status"], "blocked", role)
            self.assertIn(why, self.hub.prose(n), role)

    def test_an_issue_outside_the_loop_is_not_touched(self):
        n = self.hub.create_issue("T", "b", labels=["role:worker"])             # no loop:iteration
        with self.assertRaises(SystemExit):
            loop.resolve(n)
        self.assertEqual(self.hub.issues[n]["state"], "open")


class StageTest(SessionCase):
    def test_stage_writes_the_secrets_file_agent_sh_reads(self):
        n = self.issue(body="## Goal\nx\n")
        trusted = os.path.join(self.w.tmp, "trusted-secrets")
        with patch.object(requests, "consent_status", return_value=(True, None)), \
                patch.object(requests, "secrets_of_consent", return_value=["A", "B"]):
            loop.stage(n, self.work(), trusted)
        self.assertEqual(read(os.path.join(trusted, "secrets")), "A\nB\n")
        self.assertFalse(os.path.exists(os.path.join(trusted, "unapproved")))

    def test_everything_the_agent_needs_is_put_in_place(self):
        n = self.issue(body="## Goal\nstage me\n")
        self.hub.post_comment(n, "trusted words")
        self.hub.cmts[n].append({"id": 9, "body": "stranger words", "author_association": "NONE", "user": {"login": "stranger"}})
        work, trusted = self.work(), os.path.join(self.w.tmp, "trusted")
        loop.stage(n, work, trusted)
        issue_md = read(os.path.join(work, ".session", "issue.md"))
        self.assertIn("stage me", issue_md)
        self.assertIn("trusted words", issue_md)
        self.assertNotIn("stranger words", issue_md)
        self.assertFalse(os.path.exists(os.path.join(work, ".session", "protocol.md")))
        self.assertIn("Session protocol", read(os.path.join(trusted, "contract.md")))
        for f in ("agent.sh", "stream_filter.py", "remaining.py", "settings.json"):
            self.assertTrue(os.path.exists(os.path.join(trusted, f)), f)
        self.assertTrue(os.path.exists(os.path.join(work, ".session", "role", "SKILL.md")))
        self.assertIn(".session/", read(os.path.join(work, ".git", "info", "exclude")))
        hook = json.loads(read(os.path.join(trusted, "settings.json")))
        self.assertIn("remaining.py", hook["hooks"]["PostToolUse"][0]["hooks"][0]["command"])

    def test_the_audit_gets_the_streams_of_the_last_iterations(self):
        self.hub.artifacts = [{"id": 1, "name": "stream-0001-3", "workflow_run": {"id": 101}},
                              {"id": 2, "name": "stream-0004-9", "workflow_run": {"id": 104}},
                              {"id": 3, "name": "stream-0005-11", "workflow_run": {"id": 105}},
                              {"id": 4, "name": "stream-0005-12", "workflow_run": {"id": 106}, "expired": True}]
        n = self.hub.create_issue("A", "## Goal\nmeasure\n", labels=["role:audit", "loop:iteration 5"])
        work = self.work()
        loop.stage(n, work, os.path.join(self.w.tmp, "t"))
        got = [a for a in self.hub.gh_calls if a[:2] == ("run", "download")]
        self.assertEqual([(a[2], a[4]) for a in got], [("104", "stream-0004-9"), ("105", "stream-0005-11")])
        self.assertTrue(all(a[6].startswith(os.path.join(work, ".session", "streams")) for a in got))

    def test_a_script_role_gets_its_command_and_no_skill(self):
        n = self.issue("rule")
        trusted = os.path.join(self.w.tmp, "trusted")
        loop.stage(n, self.work(), trusted)
        self.assertEqual(read(os.path.join(trusted, "command.sh")), "echo shaping\n")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "skills", "rule")))

    def test_the_policymaker_gets_its_prompt_and_its_own_state_and_the_shaper_the_iterations(self):
        it.start_iteration()
        shaper = self.hub.role_issues("orchestrator")[0]["number"]
        work = self.work()
        loop.stage(shaper, work, os.path.join(self.w.tmp, "t1"))
        state = json.loads(read(os.path.join(work, ".session", "loop-state.json")))
        self.assertEqual((state["iteration"], state["engine_version"]), (0, c.engine_version()))
        self.assertFalse(os.path.exists(os.path.join(work, ".session", "macro-plan.md")))
        pol = it.start_policymaker(0, "a test")
        work2 = self.work()
        loop.stage(pol, work2, os.path.join(self.w.tmp, "t3"))
        self.assertIn("You are the Policymaker", read(os.path.join(work2, ".session", "policymaker.md")))
        state = json.loads(read(os.path.join(work2, ".session", "loop-state.json")))
        self.assertEqual(state["trigger"], "a test")

if __name__ == "__main__":
    unittest.main()
