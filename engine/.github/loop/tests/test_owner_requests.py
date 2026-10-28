"""Owner decisions use frozen repository state and real optimistic git updates."""
import json
import unittest
from unittest.mock import patch

import fakes
from fakes import common as c, iteration as it, loop
import owner_requests as req


class OwnerRequestsTest(unittest.TestCase):
    CONFIG = ("# retained header\nowner: ow\nlimits:\n  tools: [Read]\n"
              "\n# retained policymaker comment\npolicymaker: {max_turns: 12}\ncadence: {daily_iterations: 4}\n")
    LIMITS = "tools: [Read, Grep]\n"

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
        self.h.issues[n]["body"] = self.h.issues[n]["body"].replace(self.LIMITS, "tools: [Bash]\n")
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

    SCRIPT = ".github/loop/tune.py"
    PATCH = ("diff --git a/.github/loop/tune.py b/.github/loop/tune.py\n--- a/.github/loop/tune.py\n"
             "+++ b/.github/loop/tune.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n")
    WORKFLOW = PATCH.replace(".github/loop/tune.py", ".github/workflows/loop.yml")

    def engine_change(self, frozen=None, **kwargs):
        self.w.push_main({self.SCRIPT: "a = 1\n"})
        c.sync_main()
        return it.open_request("engine-change", "fix", "Tune the script.", "It is slow.", "The worker.",
                               frozen=frozen or self.PATCH, **kwargs)

    def branch(self, n):
        return "engine-change/" + req.read(n)["proposal_sha"][:12]

    def no_pr(self, n, why):
        self.assertEqual(self.h.prs, {})
        self.assertEqual(req.read(n)["status"], "waiting")
        self.assertIn(why, self.h.comments(n)[-1]["body"])
        self.assertFalse(self.w.origin_has_ref(self.branch(n)))

    def test_engine_change_opens_a_pr_and_merge_approves(self):
        worker = self.h.create_issue("worker", "", labels=(c.ITER + "0", "role:worker"))
        n = self.engine_change(blocks=[worker])
        self.answer(n)
        pr = self.h.prs[self.branch(n)]
        self.assertTrue(pr["body"].startswith("Tune the script."))
        self.assertIn("- `.github/loop/tune.py`", pr["body"])
        self.assertIn("#%s" % n, pr["body"])
        self.assertTrue(pr["body"].rsplit("---\n", 1)[1].startswith("Proposal SHA-256"))
        self.assertEqual(self.w.origin_file(self.SCRIPT), "a = 1\n")
        self.assertEqual(self.w.origin_file(self.SCRIPT, self.branch(n)), "a = 2\n")
        self.assertEqual(req.read(n)["status"], "waiting")
        self.assertEqual(req.read(n)["pr"], pr["url"])
        self.assertFalse(c.is_unblocked(worker))
        self.w.push_main({self.SCRIPT: "a = 2\n"})
        pr["state"], pr["merged"] = "closed", True
        loop.sweep_requests()
        self.assertEqual(req.read(n)["status"], "approved")
        self.assertTrue(c.is_unblocked(worker))
        self.assertIn(req.APPROVED, c.label_names(c.issue(n)))

    def test_engine_change_is_applied_and_tested_only_in_the_tokenless_job(self):
        n = self.engine_change(test="grep -q 'a = 2' .github/loop/tune.py && echo tested >&2")
        original_git = c.git
        seen = []

        def spy(cwd, *args, **kwargs):
            seen.append(args)
            return original_git(cwd, *args, **kwargs)

        with patch.object(c, "git", spy):
            self.answer(n)
        self.assertEqual(len(self.h.tokenless), 1)
        self.assertIn("run.sh", self.h.tokenless[0])
        self.assertFalse([a for a in seen if a[:1] == ("apply",)])
        self.assertEqual(self.w.origin_file(self.SCRIPT, self.branch(n)), "a = 2\n")
        self.assertIn("grep -q", self.h.issues[n]["body"])
        self.assertIn("Tested in the tokenless job", self.h.prs[self.branch(n)]["body"])
        self.assertIn(self.h.prs[self.branch(n)]["url"], req.read(n)["pr"])

    def test_engine_change_that_fails_its_test_opens_no_pr_and_says_why(self):
        n = self.engine_change(test="echo the-assertion-that-broke >&2; exit 1")
        self.answer(n)
        self.no_pr(n, "the test command failed")
        self.assertIn("the-assertion-that-broke", self.h.comments(n)[-1]["body"])

    def test_engine_change_without_a_result_from_the_job_opens_no_pr(self):
        n = self.engine_change()
        self.h.tokenless_fault = "no-artifact"
        self.answer(n)
        self.no_pr(n, "returned no result")

    def test_only_an_engine_change_takes_a_test_command(self):
        with self.assertRaises(ValueError):
            self.proposal(test="true")

    def test_engine_change_on_a_workflow_opens_no_pr(self):
        n = self.engine_change(self.WORKFLOW)
        self.answer(n)
        self.no_pr(n, ".github/workflows/**")

    def test_engine_change_on_a_workflow_behind_a_normalized_path_opens_no_pr(self):
        n = self.engine_change(self.WORKFLOW.replace(".github/workflows/", ".github//workflows/"))
        self.w.push_main({".github/workflows/loop.yml": "a = 1\n"})
        c.sync_main()
        self.answer(n)
        self.no_pr(n, ".github/workflows/**")

    def test_engine_change_that_no_longer_applies_opens_no_pr(self):
        n = self.engine_change()
        self.w.push_main({self.SCRIPT: "a = 3\n"})
        self.answer(n)
        self.no_pr(n, "does not apply")

    def test_unrelated_change_on_main_still_gives_a_pr(self):
        n = self.engine_change()
        self.w.push_main({"README.md": "other\n"})
        self.answer(n)
        self.assertEqual(len(self.h.prs), 1)

    def test_same_proposal_has_one_pr(self):
        n = self.engine_change()
        self.answer(n)
        loop.sweep_requests()
        loop.on_request_closed(n)
        self.assertEqual(len(self.h.prs), 1)
        m = it.open_request("engine-change", "fix again", "x", "y", "z", frozen=self.PATCH)
        self.answer(m)
        self.assertEqual(len(self.h.prs), 1)
        self.assertIn(self.h.prs[self.branch(n)]["url"], self.h.comments(m)[-1]["body"])

    def test_closing_the_pr_unmerged_declines(self):
        worker = self.h.create_issue("worker", "", labels=(c.ITER + "0", "role:worker"))
        n = self.engine_change(blocks=[worker])
        self.answer(n)
        self.h.prs[self.branch(n)]["state"] = "closed"
        loop.sweep_requests()
        self.assertEqual(req.read(n)["status"], "declined")
        self.assertFalse(c.is_unblocked(worker))

    def test_engine_applied_marker_is_a_fallback(self):
        n = self.engine_change(self.WORKFLOW)
        self.answer(n)
        self.confirm(n, "engine-applied")
        loop.sweep_requests()
        self.assertEqual(req.read(n)["status"], "approved")

    def test_retrying_a_denied_proposal_does_not_open_it_again(self):
        text = json.dumps([{"kind": "limits-change", "title": "limits", "frozen": self.LIMITS}])
        (n,) = it.process_proposals(text, 0, source=100)
        self.answer(n, False)
        self.assertEqual(it.process_proposals(text, 0, source=100), [])
        fresh = json.loads(text)
        fresh[0]["revision"] = 2
        self.assertEqual(len(it.process_proposals(json.dumps(fresh), 0, source=100)), 1)

    def test_yaml_flow_limits_preserves_other_fields(self):
        original = "limits: {tools: [Read]}\n# keep\npolicy: {max_turns: 2}\n"
        after = req.replace_limits(original, self.LIMITS)
        self.assertTrue(after.endswith("# keep\npolicy: {max_turns: 2}\n"))


if __name__ == "__main__":
    unittest.main()
