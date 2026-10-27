"""Real-git receipts bind each accepted change to its session, diff and verification."""
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import fakes
from fakes import common as c, iteration as it, loop
import policy_changes as changes


class ProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.w = fakes.worker_world()
        self.hub = self.w.hub
        self.no_reconcile = patch.object(loop, "reconcile")
        self.no_reconcile.start()

    def tearDown(self):
        self.no_reconcile.stop()
        self.w.close()

    def issue(self, role="policymaker", iteration=6):
        return self.hub.create_issue("Session", "body", labels=["role:" + role, c.ITER + str(iteration)])

    def read(self, filename):
        text = self.w.origin_file(filename)
        return json.loads(text) if text else None

    def test_evaluator_revisions_preserve_configured_order_and_duplicate_paths(self):
        self.w.push_main({"engine.yml": "evaluator_paths: [checks/, policy/evaluator/, checks/]\n",
                          "checks/measure.txt": "external", "policy/evaluator/measure.txt": "internal"})
        c.sync_main()
        external = fakes.sh(self.w.loop, "git", "rev-parse", "HEAD:checks")
        internal = fakes.sh(self.w.loop, "git", "rev-parse", "HEAD:policy/evaluator")
        self.assertEqual(it.evaluator_rev(), external + "," + internal + "," + external)
        self.assertEqual(changes.evaluator_rev("HEAD"), {"checks/": external, "policy/evaluator/": internal})

    def test_two_policymaker_sessions_have_exact_diffs_and_do_not_rewrite_closed_records(self):
        old = '{"schema_version":1,"iteration":6,"status":"closed"}\n'
        self.w.push_main({"iterations/0006.json": old})
        a, b = self.issue(), self.issue()
        self.w.branch(a, {"policy/a.md": "A"})
        self.w.branch(b, {"policy/b.md": "B"})
        self.w.push_main({"src/unrelated.txt": "another role"})
        for n in (a, b):
            self.hub.edit_labels(n, add=["awaiting:iteration"])
        listing = c.list_issues
        with patch.object(c, "list_issues", side_effect=lambda *args, **kwargs: list(reversed(listing(*args, **kwargs)))):
            loop.apply_policy()
        ra, rb = [self.read(changes.path(n)) for n in (a, b)]
        self.assertEqual(ra["changed_paths"], ["policy/a.md"])
        self.assertEqual(rb["changed_paths"], ["policy/b.md"])
        self.assertEqual(ra["policy_rev_after"], rb["policy_rev_before"])
        self.assertNotEqual(ra["accepted_commit"], rb["accepted_commit"])
        for r in (ra, rb):
            self.assertEqual(r["role"], "policymaker")
            self.assertTrue(r["applied_at"])
            self.assertIsNone(r["applied_iteration"])
            self.assertEqual(r["effective_from_iteration"], 7)
            actual = c.git(self.w.origin, "diff", "--name-only", r["applied_base_commit"], r["accepted_commit"]).stdout.splitlines()
            self.assertEqual(actual, r["changed_paths"])
        self.assertEqual(self.w.origin_file("iterations/0006.json"), old)
        self.assertEqual(it.records()[0]["iteration"], 6)  # legacy record without additive fields
        # A new record references changes applied since the previous closed record.
        current = self.hub.create_issue("Iteration", "", labels=[it.MAP, c.ITER + "7"])
        c.update_report(current, {"started": c.iso()})
        with ExitStack() as stack:
            for name in ("prune_streams", "note_no_progress", "check_goal", "start_iteration"):
                stack.enter_context(patch.object(it, name))
            stack.enter_context(patch.object(it, "policymaker_due", return_value=None))
            it.close_iteration(7, "closed")
        self.assertEqual(self.read(it.record_path(7))["policy"]["change_ids"], [ra["id"], rb["id"]])
        self.assertEqual(self.w.origin_file("iterations/0006.json"), old)
        # Retrying a successful merge cannot alter its provenance.
        replay = loop.merge_branch(a, "policymaker", self.hub.issue(a), boundary=True)
        self.assertEqual(loop.merge_outcome(replay)[1]["merged"], "true")
        self.assertEqual(self.read(changes.path(a)), ra)

    def evaluator(self, extra=None):
        n = self.issue("evaluator", 1)
        self.w.branch(n, dict({"policy/evaluator/new.txt": str(n)}, **(extra or {})))
        i = self.hub.issue(n)
        r = loop.merge_branch(n, "evaluator", i)
        self.assertEqual(r["result"], "awaiting-verifier")
        self.hub.edit_labels(n, add=["awaiting:verifier"])
        c.update_report(n, {"status": "done", "merged": "pending-verifier"})
        v = loop.request_verifier(n, i, 1, r["changed"])
        return n, v

    def test_rejected_push_retries_code_and_receipt_together_from_the_new_base(self):
        n = self.issue()
        self.w.branch(n, {"policy/retried.md": "change"})
        git = c.git
        failed = []

        def racing_push(cwd, *args, **kwargs):
            if cwd != "." and args[:2] == ("push", "origin") and not failed:
                failed.append(True)
                self.assertIsNone(self.read(changes.path(n)))
                self.w.push_main({"src/racing-role.txt": "independent change"})
                return SimpleNamespace(returncode=1)
            return git(cwd, *args, **kwargs)

        with patch.object(c, "git", side_effect=racing_push), patch.object(c, "iso", side_effect=["2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z"]):
            r = loop.merge_branch(n, "policymaker", self.hub.issue(n), boundary=True)
        self.assertEqual(r["result"], "merged")
        record = self.read(changes.path(n))
        self.assertEqual(record["applied_at"], "2026-01-01T00:00:02Z")
        self.assertEqual(record["changed_paths"], ["policy/retried.md"])
        self.assertEqual(self.w.origin_file("src/racing-role.txt"), "independent change")

    def test_verifier_status_result_and_merge_result_are_distinct_and_immutable(self):
        for status, result in (("done", "fail"), ("blocked", None), ("crashed", None), ("done", "pass")):
            with self.subTest(status=status, result=result):
                n, v = self.evaluator()
                vi = self.hub.issue(v)
                loop.settle_verification(v, vi, status, result, "")
                filename = "%s/verification-%s.json" % (changes.ROOT, v)
                record = self.read(filename)
                self.assertEqual((record["issue"], record["verifier_issue"]), (n, v))
                self.assertEqual((record["session_status"], record["result"]), (status, result))
                self.assertTrue(record["checked_commit"])
                self.assertTrue(record["checked_evaluator_rev"])
                self.assertEqual(record["application_result"], "merged" if result == "pass" else "not-applied")
                if result == "pass":
                    accepted = self.read(changes.path(n))
                    self.assertEqual((accepted["role"], accepted["verifier_issue"]), ("evaluator", v))
                    self.assertEqual(record["change_id"], accepted["id"])
                changes.verification(v, vi, "crashed", None, {"result": "not-applied"})
                self.assertEqual(self.read(filename), record)

    def test_passing_verifier_does_not_claim_a_failed_merge_was_applied(self):
        n, v = self.evaluator()
        self.w.push_main({"policy/evaluator/new.txt": "conflicting owner change"})
        loop.settle_verification(v, self.hub.issue(v), "done", "pass", "")
        record = self.read("%s/verification-%s.json" % (changes.ROOT, v))
        self.assertEqual((record["result"], record["application_result"]), ("pass", "conflict"))
        self.assertIsNone(self.read(changes.path(n)))

    def test_mutating_a_verified_branch_requires_fresh_verification(self):
        n, v = self.evaluator()
        self.w.branch(n, {"policy/evaluator/new.txt": "unverified replacement"})
        loop.settle_verification(v, self.hub.issue(v), "done", "pass", "")
        record = self.read("%s/verification-%s.json" % (changes.ROOT, v))
        self.assertEqual(record["application_result"], "verification-stale")
        self.assertIsNone(self.read(changes.path(n)))

    def test_owner_application_is_an_additional_event_and_preserves_verified_snapshot(self):
        profiles = self.w.origin_file("policy/profiles.yml").replace('write: ["policy/evaluator/**"]', 'write: ["policy/evaluator/**", "src/**"]')
        self.w.push_main({"policy/profiles.yml": profiles})
        c.sync_main()
        n, v = self.evaluator({"src/a.txt": "work"})
        loop.settle_verification(v, self.hub.issue(v), "done", "pass", "")
        filename = "%s/verification-%s.json" % (changes.ROOT, v)
        first = self.read(filename)
        self.assertEqual(first["application_result"], "awaiting-owner")
        c.update_report(v, {"status": "done", "result": "pass"})
        loop.settle_owner_merge(n, True)
        final = self.read("%s/verification-%s-owner.json" % (changes.ROOT, v))
        self.assertEqual(final["application_result"], "merged")
        self.assertEqual(self.read(filename), first)
        self.assertEqual(self.read(changes.path(n))["role"], "evaluator")


if __name__ == "__main__":
    unittest.main()
