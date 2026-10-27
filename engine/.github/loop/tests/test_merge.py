"""The change gate and the merge, on real git repositories: what may enter the default branch and what may not."""
import sys
import unittest

import fakes
from fakes import common as c, iteration as it, loop

GATE_SH = """if grep -q BAD "$CANDIDATE_DIR/src/a.txt" 2>/dev/null; then
  echo '{"verdict": "rejected", "detail": "the candidate says BAD"}'
elif [ -f "$CANDIDATE_DIR/src/silent.txt" ]; then
  echo "no verdict printed"
else
  echo "{\\"verdict\\": \\"accepted\\", \\"improved\\": true, \\"detail\\": \\"changed: $(tr '\\n' ' ' < "$CHANGED_FILES_FILE")\\"}"
fi
"""
SCRIPT_GATE = "gate:\n  kind: script\n  command: bash policy/evaluator/gate.sh\n"


class MergeCase(unittest.TestCase):
    YML = SCRIPT_GATE

    def setUp(self):
        self.w = fakes.worker_world(self.YML)
        self.w.write(self.w.loop, "policy/evaluator/gate.sh", GATE_SH)
        fakes.sh(self.w.loop, "git", "add", "-A")
        fakes.sh(self.w.loop, "git", "commit", "-q", "-m", "gate")
        fakes.sh(self.w.loop, "git", "push", "-q", "origin", "main")
        self.n = self.w.hub.create_issue("Task", "body", labels=["role:worker", "loop:iteration 1"])

    def tearDown(self):
        self.w.close()

    def merge(self, files, role="worker", **kw):
        self.w.branch(self.n, files, delete=kw.pop("delete", ()))
        return loop.merge_branch(self.n, role, self.w.hub.issue(self.n), **kw)


class GateTest(MergeCase):
    def test_accepted_work_enters_the_default_branch(self):
        r = self.merge({"src/a.txt": "good"})
        self.assertEqual((r["result"], r["verdict"]["verdict"], r["verdict"]["improved"]), ("merged", "accepted", True))
        self.assertEqual(self.w.origin_file("src/a.txt"), "good")
        self.assertIn("src/a.txt", r["verdict"]["detail"])        # the gate saw the list of changed files

    def test_rejected_work_stays_off(self):
        r = self.merge({"src/a.txt": "BAD"})
        self.assertEqual(r["result"], "gate-rejected")
        self.assertIsNone(self.w.origin_file("src/a.txt"))
        st, upd, prose = loop.merge_outcome(r)
        self.assertEqual((st, upd["verdict"], upd["merged"]), ("rejected", "rejected", "false"))

    def test_no_verdict_is_not_a_yes(self):
        r = self.merge({"src/silent.txt": "x"})
        self.assertEqual(r["result"], "gate-inconclusive")
        self.assertIsNone(self.w.origin_file("src/silent.txt"))
        self.assertEqual(loop.merge_outcome(r)[0], "partial")

    def test_direct_paths_skip_the_gate(self):
        r = self.merge({"journal/0001.md": "notes"}, role="orchestrator")
        self.assertEqual((r["result"], r["verdict"]), ("merged", None))

    def test_the_gate_command_is_missing(self):
        w2 = fakes.worker_world("gate:\n  kind: script\n  command: ''\n")
        try:
            w2.branch(5, {"src/a.txt": "x"})
            i = w2.hub.create_issue("T", "b", labels=["role:worker", "loop:iteration 1"])
            self.assertEqual(loop.merge_branch(5, "worker", w2.hub.issue(i))["result"], "gate-inconclusive")
        finally:
            w2.close()

    def test_a_check_failure_stops_before_the_gate(self):
        w2 = fakes.worker_world(SCRIPT_GATE + "check: 'exit 3'\n")
        try:
            w2.branch(5, {"src/a.txt": "x"})
            i = w2.hub.create_issue("T", "b", labels=["role:worker", "loop:iteration 1"])
            self.assertEqual(loop.merge_branch(5, "worker", w2.hub.issue(i))["result"], "check")
        finally:
            w2.close()

    @unittest.skipUnless(sys.platform == "linux", "candidate check requires Linux bubblewrap namespaces")
    def test_a_passing_check_runs_in_the_candidate_tree(self):
        w2 = fakes.worker_world(SCRIPT_GATE + "check: 'test -f src/a.txt'\n")
        try:
            w2.write(w2.loop, "policy/evaluator/gate.sh", GATE_SH)
            fakes.sh(w2.loop, "git", "add", "-A")
            fakes.sh(w2.loop, "git", "commit", "-q", "-m", "g")
            fakes.sh(w2.loop, "git", "push", "-q", "origin", "main")
            w2.branch(5, {"src/a.txt": "x"})
            i = w2.hub.create_issue("T", "b", labels=["role:worker", "loop:iteration 1"])
            self.assertEqual(loop.merge_branch(5, "worker", w2.hub.issue(i))["result"], "merged")
        finally:
            w2.close()


class OwnerGateTest(MergeCase):
    YML = ""        # the default: the owner is the gate

    def test_waits_for_the_owner(self):
        r = self.merge({"src/a.txt": "x"})
        self.assertEqual(r["result"], "awaiting-owner")
        self.assertIsNone(self.w.origin_file("src/a.txt"))

    def test_the_owners_yes_merges_without_running_the_gate(self):
        r = self.merge({"src/a.txt": "x"}, override={"verdict": "accepted", "improved": True, "detail": "owner"})
        self.assertEqual(r["result"], "merged")
        self.assertEqual(self.w.origin_file("src/a.txt"), "x")


class RefusalTest(MergeCase):
    def test_protected_paths(self):
        for path in (".github/workflows/x.yml", "engine.yml", "iterations/0001.json", "goal/gate.yml", "GOAL_REACHED"):
            r = self.merge({path: "x"})
            self.assertEqual(r["result"], "protected", path)
            self.assertIn(path, r["detail"])

    def test_outside_the_role_write_paths(self):
        self.assertEqual(self.merge({"policy/params.yml": "x"})["result"], "scope")
        self.assertEqual(self.merge({"src/a.txt": "ok", "other/b.txt": "no"})["result"], "scope")

    def test_the_evaluator_is_closed_to_roles_without_the_flag(self):
        r = self.merge({"policy/evaluator/x.sh": "x"})
        self.assertEqual(r["result"], "scope")

    def test_a_big_file_belongs_in_artifacts(self):
        w2 = fakes.worker_world(SCRIPT_GATE + "large_file_bytes: 50\n")
        try:
            w2.branch(5, {"src/big.bin": "x" * 100})
            i = w2.hub.create_issue("T", "b", labels=["role:worker", "loop:iteration 1"])
            r = loop.merge_branch(5, "worker", w2.hub.issue(i))
            self.assertEqual(r["result"], "large")
        finally:
            w2.close()

    def test_an_empty_change_has_nothing_to_merge(self):
        self.w.branch(self.n, {})
        r = loop.merge_branch(self.n, "worker", self.w.hub.issue(self.n))
        self.assertEqual(r["result"], "nothing")
        self.assertEqual(loop.merge_outcome(r)[0], "done")

    def test_a_conflict_is_reported_not_forced(self):
        self.w.push_main({"src/a.txt": "base\n"})
        self.w.branch(self.n, {"src/a.txt": "mine\n"}, base="origin/main")
        self.w.push_main({"src/a.txt": "theirs\n"})
        r = loop.merge_branch(self.n, "worker", self.w.hub.issue(self.n))
        self.assertEqual(r["result"], "conflict")
        self.assertEqual(self.w.origin_file("src/a.txt"), "theirs\n")

    def test_a_rebase_lets_independent_work_through(self):
        self.w.branch(self.n, {"src/a.txt": "mine"}, base="origin/main")
        self.w.push_main({"src/other.txt": "theirs"})
        self.assertEqual(loop.merge_branch(self.n, "worker", self.w.hub.issue(self.n))["result"], "merged")
        self.assertEqual((self.w.origin_file("src/a.txt"), self.w.origin_file("src/other.txt")), ("mine", "theirs"))


class EvaluatorChangeTest(MergeCase):
    def test_the_evaluator_role_waits_for_the_verifier(self):
        r = self.merge({"policy/evaluator/new.sh": "x"}, role="evaluator")
        self.assertEqual(r["result"], "awaiting-verifier")
        self.assertIsNone(self.w.origin_file("policy/evaluator/new.sh"))

    def test_after_the_verifier_the_change_merges(self):
        self.w.branch(self.n, {"policy/evaluator/new.sh": "x"})
        r = loop.merge_branch(self.n, "evaluator", self.w.hub.issue(self.n), verified=True)
        self.assertEqual(r["result"], "merged")
        self.assertEqual(self.w.origin_file("policy/evaluator/new.sh"), "x")


class NotesTest(MergeCase):
    def test_an_unfinished_session_still_leaves_its_journal(self):
        self.w.branch(self.n, {"journal/0001.md": "state", "src/a.txt": "half"})
        self.assertTrue(loop.merge_notes(self.n, "orchestrator"))
        self.assertEqual(self.w.origin_file("journal/0001.md"), "state")
        self.assertIsNone(self.w.origin_file("src/a.txt"))

    def test_nothing_to_leave(self):
        self.w.branch(self.n, {"src/a.txt": "half"})
        self.assertFalse(loop.merge_notes(self.n, "worker"))


class RollbackTest(MergeCase):
    def test_policy_goes_back_to_the_state_of_the_last_good_iteration(self):
        good = fakes.sh(self.w.loop, "git", "rev-parse", "HEAD")
        self.w.push_main({"policy/params.yml": "shaper: broken\n"}, "the Policymaker broke it")
        fakes.sh(self.w.loop, "git", "tag", "setup-baseline", good)
        self.assertTrue(it.revert_policy("test"))
        self.assertIn("shaper: orchestrator", self.w.origin_file("policy/params.yml"))
        self.assertTrue(self.w.hub.by_label("owner-request:notice"))
        self.assertFalse(it.revert_policy("again"))         # already there: nothing to roll back

    def test_rollback_removes_files_the_policymaker_added(self):
        good = fakes.sh(self.w.loop, "git", "rev-parse", "HEAD")
        self.w.push_main({"policy/roles/new/SKILL.md": "x"}, "added")
        fakes.sh(self.w.loop, "git", "tag", "setup-baseline", good)
        it.revert_policy("test")
        self.assertIsNone(self.w.origin_file("policy/roles/new/SKILL.md"))


if __name__ == "__main__":
    unittest.main()
