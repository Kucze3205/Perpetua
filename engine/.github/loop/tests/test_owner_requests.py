"""Owner decisions use frozen repository state and real optimistic git updates."""
import json
import unittest
from unittest.mock import patch

import fakes
from fakes import common as c, iteration as it, loop
import owner_requests as req


class OwnerRequestsTest(unittest.TestCase):
    CONFIG = ("# retained header\nowner: ow\nlimits:\n  tools: [Read]\n  internet_write: []\n"
              "\n# retained policymaker comment\npolicymaker: {max_turns: 12}\ncadence: {daily_iterations: 4}\n")
    LIMITS = "tools: [Read, Grep]\ninternet_write: []\n"

    def setUp(self):
        self.w = fakes.World(engine_yml=self.CONFIG)
        self.h = self.w.hub
        self.tail = patch.object(loop, "reconcile")
        self.advance = patch.object(it, "advance")
        self.tail.start()
        self.advance.start()

    def tearDown(self):
        self.tail.stop()
        self.advance.stop()
        self.w.close()

    def proposal(self, kind="limits-change", **kwargs):
        return it.open_request(kind, "request", "what", "why", "unblocks",
                               frozen=kwargs.pop("frozen", self.LIMITS), **kwargs)

    def answer(self, n, yes=True, owner="ow"):
        self.h.answer(n, yes)
        self.h.issues[n]["closed_by"] = {"login": owner}
        loop.on_request_closed(n)

    def confirm(self, n, marker, login="ow"):
        self.h.post_comment(n, "<!-- %s: %s -->" % (marker, req.read(n)["proposal_sha"]))
        self.h.cmts[n][-1]["user"]["login"] = login

    def test_acceptance_changes_only_limits_and_repeat_is_noop(self):
        n = self.proposal()
        self.answer(n)
        after = self.w.origin_file("engine.yml")
        self.assertIn("- Grep", after)
        self.assertEqual(after[after.index("\n# retained policymaker comment"):],
                         self.CONFIG[self.CONFIG.index("\n# retained policymaker comment"):])
        self.assertTrue(after.startswith("# retained header\nowner: ow\n"))
        self.assertEqual(req.read(n)["status"], "approved")
        commit = c.head()
        comments = len(self.h.comments(n))
        loop.on_request_closed(n)
        self.assertEqual(c.head(), commit)
        self.assertEqual(len(self.h.comments(n)), comments)

    def test_decline_and_nonowner_do_not_widen_limits(self):
        n = self.proposal()
        self.answer(n, owner="collaborator")
        self.assertEqual(self.w.origin_file("engine.yml"), self.CONFIG)
        self.assertNotIn(it.HANDLED, c.label_names(c.issue(n)))
        self.answer(n, False)
        self.assertEqual(req.read(n)["status"], "declined")
        self.assertEqual(self.w.origin_file("engine.yml"), self.CONFIG)

    def test_editing_both_proposal_and_displayed_hash_is_rejected(self):
        n = self.proposal()
        self.h.issues[n]["body"] = self.h.issues[n]["body"].replace(self.LIMITS, "tools: [Bash]\ninternet_write: []\n")
        self.h.issues[n]["body"] = self.h.issues[n]["body"].replace(it.sha256(self.LIMITS), "forged-hash")
        self.answer(n)
        self.assertEqual(req.read(n)["status"], "invalid")
        self.assertEqual(self.w.origin_file("engine.yml"), self.CONFIG)

    def test_newer_configuration_is_a_conflict(self):
        n = self.proposal()
        changed = self.CONFIG.replace("daily_iterations: 4", "daily_iterations: 5")
        self.w.push_main({"engine.yml": changed})
        c.sync_main()
        self.answer(n)
        self.assertEqual(req.read(n)["status"], "conflict")
        self.assertEqual(self.w.origin_file("engine.yml"), changed)

    def test_push_retry_checks_the_configuration_again(self):
        n = self.proposal()
        original_git = c.git
        raced = False
        changed = self.CONFIG.replace("daily_iterations: 4", "daily_iterations: 8")

        def race(cwd, *args, **kwargs):
            nonlocal raced
            if args[:2] == ("push", "origin") and not raced:
                raced = True
                self.w.push_main({"engine.yml": changed})
            return original_git(cwd, *args, **kwargs)

        with patch.object(c, "git", race):
            self.answer(n)
        self.assertTrue(raced)
        self.assertEqual(self.w.origin_file("engine.yml"), changed)
        self.assertEqual(req.read(n)["status"], "conflict")

    def test_restart_after_atomic_apply_only_repairs_labels(self):
        n = self.proposal()
        self.h.answer(n, True)
        self.h.issues[n]["closed_by"] = {"login": "ow"}
        self.assertEqual(req.settle(n, c.issue(n)), "approved")
        commit = c.head()
        loop.sweep_requests()
        self.assertEqual(c.head(), commit)
        self.assertIn(req.APPROVED, c.label_names(c.issue(n)))

    def test_capability_needs_explicit_owner_readiness_and_releases_dependency(self):
        worker = self.h.create_issue("worker", "", labels=(c.ITER + "0", "role:worker"))
        n = self.proposal("capability", frozen="Configure SECRET_NAME in repository secrets.",
                          readiness="Run the documented credential probe; disclose no value.", blocks=[worker])
        self.answer(n)
        self.assertEqual(req.read(n)["status"], "waiting")
        self.assertEqual(it.request_summary(c.issue(n))["decision"], "waiting")
        self.assertFalse(c.is_unblocked(worker))
        self.h.edit_labels(n, add=[req.APPROVED], remove=["owner-request:capability"])
        self.assertFalse(c.is_unblocked(worker))
        count = len(self.h.comments(n))
        loop.on_request_closed(n)
        self.assertEqual(len(self.h.comments(n)), count)
        self.confirm(n, "capability-ready", login="github-actions[bot]")
        loop.sweep_requests()
        self.assertFalse(c.is_unblocked(worker))
        self.confirm(n, "capability-ready")
        loop.sweep_requests()
        self.assertEqual(req.read(n)["status"], "approved")
        self.assertTrue(c.is_unblocked(worker))
        self.assertIn(worker, c.LAUNCHED)
        self.assertEqual(self.w.origin_file("engine.yml"), self.CONFIG)

    def test_engine_change_remains_manual(self):
        n = self.proposal("engine-change", frozen="diff --git a/engine.yml b/engine.yml\nApply and check manually.")
        self.answer(n)
        self.assertEqual(req.read(n)["status"], "waiting")
        self.assertEqual(self.w.origin_file("engine.yml"), self.CONFIG)
        self.confirm(n, "engine-applied")
        loop.sweep_requests()
        self.assertEqual(req.read(n)["status"], "approved")
        self.assertEqual(self.w.origin_file("engine.yml"), self.CONFIG)
        self.assertEqual(self.h.gh_calls, [])

    def test_retrying_a_denied_proposal_does_not_open_it_again(self):
        text = json.dumps([{"kind": "limits-change", "title": "limits", "frozen": self.LIMITS}])
        (n,) = it.process_proposals(text, 0, source=100)
        self.answer(n, False)
        self.assertEqual(it.process_proposals(text, 0, source=100), [])
        fresh = json.loads(text)
        fresh[0]["revision"] = 2
        self.assertEqual(len(it.process_proposals(json.dumps(fresh), 0, source=100)), 1)

    def test_yaml_flow_limits_preserves_other_fields(self):
        original = "limits: {tools: [Read], internet_write: []}\n# keep\npolicy: {max_turns: 2}\n"
        after = req.replace_limits(original, self.LIMITS)
        self.assertTrue(after.endswith("# keep\npolicy: {max_turns: 2}\n"))


if __name__ == "__main__":
    unittest.main()
