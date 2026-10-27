"""Policymaker triggers, first iteration, policy phases and failures."""
import json
import os
import unittest

import fakes
from fakes import common as c, iteration as it, task, tasks_json
from flow_fixtures import Flow


class PolicymakerTriggerTest(Flow):
    PARAMS = "policymaker_triggers:\n  every_iterations: 2\n  no_progress: 3\n"

    def test_the_policymaker_opens_the_iteration_on_its_count(self):
        it.start_iteration()
        self.assertEqual(self.hub.role_issues("policymaker"), [])                # iteration 0: no trigger due
        self.finish(0)
        self.finish(1)
        pol = self.session_issue("policymaker")
        self.assertEqual(c.iteration_of(c.label_names(self.hub.issue(pol))), 2)  # labelled with the iteration it opens
        self.assertIn(pol, {int(i["issue"]) for _, i in self.hub.dispatched})
        nxt = self.open_iteration()
        self.assertEqual(it.iteration_number(nxt), 2)
        self.assertEqual(c.report_fields(nxt["number"])["phase"], "policy")      # the iteration waits for it
        self.assertIsNone(self.session_issue("orchestrator", n=2))
        state = it.read_state(pol)
        self.assertEqual((state["trigger"], state["iterations"][-1]["n"]), ("every 2 iterations", 1))
        self.w.session(pol)
        self.assertEqual(c.report_fields(nxt["number"])["phase"], "shape")
        self.assertIsNotNone(self.session_issue("orchestrator", n=2))

    def test_without_a_due_trigger_the_iteration_starts_on_the_last_organization(self):
        it.start_iteration()
        self.assertEqual(c.report_fields(self.open_iteration()["number"])["phase"], "shape")
        self.finish(0)                                                           # iteration 1: no trigger due either
        self.assertEqual(c.report_fields(self.open_iteration()["number"])["phase"], "shape")
        self.assertEqual(self.hub.role_issues("policymaker", "all")[-1]["number"], 0)      # only the fixture's old session

    def test_one_policymaker_session_at_a_time(self):
        it.start_iteration()
        self.finish(0)
        self.finish(1)
        self.assertEqual(len(self.hub.role_issues("policymaker")), 1)
        self.assertIsNone(it.policymaker_due(3))

    def test_no_progress_calls_the_policymaker(self):
        self.w.push_main({"policy/params.yml": fakes.POLICY_PARAMS + "policymaker_triggers:\n  no_progress: 1\n"}, "params")
        c.sync_main()
        it.start_iteration()
        self.play_shaper(task("a"))
        self.w.session(self.task_issue("a"), files={"src/a.txt": "BAD"})       # rejected by the gate: no progress
        self.assertEqual(it.read_state(self.session_issue("policymaker"))["trigger"], "1 iterations without progress")

    def test_a_new_audit_report_calls_the_policymaker(self):
        it.start_iteration()
        self.policymaker_session(0)
        self.assertIsNone(it.policymaker_due(1))                                      # one is open
        self.hub.close_issue(self.session_issue("policymaker"))
        self.assertIsNone(it.policymaker_due(1))
        self.w.push_main({"audit/latest.md": "numbers\n"}, "the audit merged")
        c.sync_main()
        self.assertEqual(it.policymaker_due(1), "a new audit report")

    def test_audit_revision_is_acknowledged_once_even_when_commit_clock_is_older(self):
        it.start_iteration()
        first = self.policymaker_session(0)
        self.hub.close_issue(first)
        self.w.push_main({"audit/latest.md": "numbers\n"}, "audit revision")
        c.sync_main()
        self.assertEqual(it.policymaker_due(1), "a new audit report")
        second = self.policymaker_session(0)
        self.hub.close_issue(second)
        self.assertIsNone(it.policymaker_due(1))


class FirstPolicymakerTest(Flow):
    """No Policymaker session has run yet: the first iteration of the loop starts with one."""
    FIRST = True
    ORDER = tasks_json(task("e", role="evaluator", title="Fix evaluator"))

    def test_the_first_iteration_starts_with_the_policymaker_and_its_orders_become_its_tasks(self):
        self.assertEqual(it.start_iteration(), 0)
        cur = self.open_iteration()
        pol = self.session_issue("policymaker")
        self.assertEqual(c.report_fields(cur["number"])["phase"], "policy")
        self.assertEqual(it.read_state(pol)["trigger"], "the first iteration of the loop")
        self.assertIsNone(self.session_issue("orchestrator"))                    # the iteration waits for the Policymaker
        self.assertEqual(c.iteration_of(c.label_names(self.hub.issue(pol))), 0)
        self.w.session(pol, session_files={"orders.json": self.ORDER})
        e = self.task_issue("order-%s-e" % pol)
        self.assertEqual(c.iteration_of(c.label_names(self.hub.issue(e))), 0)    # the same iteration
        self.assertIn(e, {int(i["issue"]) for _, i in self.hub.dispatched})
        self.assertEqual(c.report_fields(cur["number"])["phase"], "policy")      # the shaper does not start beside it
        self.assertIsNone(self.session_issue("orchestrator"))
        self.assertIn("awaiting:orders", self.hub.issue(pol)["_labels"])

    def test_the_first_trigger_returns_until_a_policymaker_session_ends_done(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        c.update_report(pol, {"status": "crashed"})
        self.hub.close_issue(pol)
        self.assertEqual(it.policymaker_due(1), "the first iteration of the loop")
        retry = self.policymaker_session(1)
        c.update_report(retry, {"status": "done"})
        self.hub.close_issue(retry)
        self.assertIsNone(it.policymaker_due(2))

    def test_a_policy_change_of_the_policy_phase_takes_effect_before_the_shaper_starts(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        rev = it.policy_rev()
        self.w.session(pol, files={"policy/params.yml": fakes.POLICY_PARAMS + "criterion: faster" + chr(10)})
        self.assertIn("faster", self.w.origin_file("policy/params.yml"))
        self.assertEqual(self.hub.issues[pol]["state"], "closed")
        cur = self.open_iteration()
        self.assertEqual(it.read_state(cur["number"])["policy_params"]["criterion"], "faster")
        self.finish(0)
        policy = self.record(0)["policy"]
        self.assertEqual(policy["rev_start"], rev)
        self.assertNotEqual(policy["rev_end"], rev)
        self.assertEqual((policy["session"], policy["trigger"]), (pol, "the first iteration of the loop"))

    def test_the_orders_alone_make_an_iteration_when_no_shaper_is_set(self):
        self.w.push_main({"policy/params.yml": fakes.POLICY_PARAMS.replace("shaper: orchestrator", "shaper: null")}, "no shaper")
        c.sync_main()
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": tasks_json(task("w", title="Build"))})   # a worker: any role is orderable
        w = self.task_issue("order-%s-w" % pol)
        cur = self.open_iteration()
        self.assertEqual(c.report_fields(cur["number"])["phase"], "run")
        self.assertIsNone(self.session_issue("orchestrator"))
        self.w.session(w, files={"src/w.txt": "w"})
        rec = self.record(0)
        self.assertEqual((rec["status"], rec["decided_by"]), ("closed", None))
        self.assertEqual([t["id"] for t in rec["tasks"]], ["order-%s-w" % pol])

    def test_no_shaper_and_no_orders_fail_the_iteration_with_the_reason(self):
        self.w.push_main({"policy/params.yml": fakes.POLICY_PARAMS.replace("shaper: orchestrator", "shaper: null")}, "no shaper")
        c.sync_main()
        it.start_iteration()
        self.w.session(self.session_issue("policymaker"))
        rec = self.record(0)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("no shaper", rec["failure"])

    def test_a_crashed_policymaker_fails_open_with_the_last_organization(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, files={"policy/params.yml": "criterion: x" + chr(10)}, report=False, exit_code="1")
        self.assertEqual(self.hub.report(pol)["status"], "crashed")
        self.assertNotIn("criterion: x", self.w.origin_file("policy/params.yml"))
        self.assertEqual(c.report_fields(self.open_iteration()["number"])["phase"], "shape")
        self.finish(0)
        self.assertEqual(it.iteration_number(self.open_iteration()), 1)


class PolicymakerTest(Flow):
    FIRST = True
    ORDER = tasks_json(task("e", role="evaluator", title="Fix evaluator"))

    def test_the_iteration_records_the_orders_of_the_policy_phase(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.ORDER})
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.w.session(pol)                                                      # the second pass orders nothing
        self.finish(0)
        rec = self.record(0)
        self.assertEqual(sorted(t["id"] for t in rec["tasks"]), ["order-%s-e" % pol, "t0"])
        self.assertEqual(rec["outcome"]["width"], 2)

    def test_an_order_of_a_role_outside_policymaker_only_roles_is_refused(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": tasks_json(task("w"))})
        self.assertIsNone(self.task_issue("order-%s-w" % pol))
        self.assertIn("orders only", c.report_fields(self.open_iteration()["number"])["orders_refused"])
        self.finish(0)
        self.assertIn("orders only", self.record(0)["policy"]["orders_refused"])
        # the next Policymaker session reads the refusal in its loop-state `iterations`
        self.assertIn("orders only", it.index_lines()[0]["orders_refused"])

    def test_a_policymaker_change_outside_its_zone_is_refused(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, files={"engine.yml": "# hacked" + chr(10)})
        self.assertEqual(self.hub.report(pol)["status"], "blocked")
        self.finish(0)
        self.assertNotIn("hacked", self.w.origin_file("engine.yml"))

    def test_the_policymaker_cannot_touch_the_evaluator(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, files={"policy/evaluator/gate.sh": "echo hacked"})
        self.finish(0)
        self.assertEqual(self.hub.report(pol)["status"], "blocked")
        self.assertNotIn("hacked", self.w.origin_file("policy/evaluator/gate.sh"))


class PolicyPhaseTest(Flow):
    """The policy phase: the Policymaker's passes on one issue, the ordered policy-layer sessions between them."""
    FIRST = True
    E = tasks_json(task("e", role="evaluator", title="Fix evaluator"))
    A = tasks_json(task("a", role="audit", title="Audit"))

    def runs_of(self, n):
        return [i for _, i in self.hub.dispatched if int(i["issue"]) == n]

    def phase(self):
        return c.report_fields(self.open_iteration()["number"])["phase"]

    def test_the_shaper_starts_only_after_the_pass_that_follows_the_orders_ordered_nothing(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.E})
        e = self.task_issue("order-%s-e" % pol)
        self.assertEqual(len(self.runs_of(pol)), 1)
        self.w.session(e)                                                        # the ordered session ends
        self.assertEqual(len(self.runs_of(pol)), 2)                              # the same issue runs again
        self.assertNotIn("awaiting:orders", self.hub.issue(pol)["_labels"])
        self.assertEqual((self.phase(), self.session_issue("orchestrator")), ("policy", None))
        self.w.session(pol)                                                      # the pass orders nothing
        self.assertEqual(self.phase(), "shape")
        self.assertIsNotNone(self.session_issue("orchestrator"))
        self.assertEqual(self.hub.issues[pol]["state"], "closed")
        self.assertEqual(len(self.hub.role_issues("policymaker", "all")), 1)     # all its passes are one issue

    def test_the_policymaker_returns_when_an_ordered_session_failed_and_sees_it(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": tasks_json(task("e", role="evaluator"), task("a", role="audit"))})
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.assertEqual(len(self.runs_of(pol)), 1)                              # one is still running
        self.w.session(self.task_issue("order-%s-a" % pol), report=False, exit_code="1")
        self.assertEqual(len(self.runs_of(pol)), 2)
        ended = it.read_state(pol, it.PASS_MARK, last=True)["ordered"]
        self.assertEqual(sorted((o["role"], o["status"]) for o in ended), [("audit", "crashed"), ("evaluator", "done")])
        sess = os.path.join(self.w.tmp, "sess")
        it.export_state(pol, sess, role="policymaker")
        with open(os.path.join(sess, "loop-state.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["policy_pass"]["pass"], 2)

    def test_the_relaunched_branch_holds_what_merged_and_a_second_pass_merges_its_own_change(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, files={"policy/params.yml": fakes.POLICY_PARAMS + "criterion: one" + chr(10)}, session_files={"orders.json": self.E})
        self.assertIn("criterion: one", self.w.origin_file("policy/params.yml"))     # before the ordered session starts
        self.assertEqual(self.phase(), "policy")
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.assertEqual(fakes.sh(self.w.origin, "git", "rev-parse", "task/%s" % pol), fakes.sh(self.w.origin, "git", "rev-parse", "main"))
        self.w.session(pol, files={"policy/params.yml": fakes.POLICY_PARAMS + "criterion: two" + chr(10)})
        self.assertIn("criterion: two", self.w.origin_file("policy/params.yml"))
        self.assertEqual(self.phase(), "shape")
        self.assertEqual(it.read_state(self.open_iteration()["number"])["policy_params"]["criterion"], "two")

    def test_a_pass_may_order_again_and_nothing_caps_the_passes(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        for k, (orders, tid, role) in enumerate([(self.E, "e", "evaluator"), (self.A, "a", "audit")] * 3, 1):
            self.w.session(pol, session_files={"orders.json": orders})
            ordered = self.task_issue("order-%s-%s%s" % (pol, "" if k == 1 else "%s-" % k, tid))
            self.assertEqual(c.roles_of(c.label_names(self.hub.issue(ordered))), [role])
            self.assertEqual(self.phase(), "policy")
            self.w.session(ordered)
        self.assertEqual(len(self.runs_of(pol)), 7)
        self.w.session(pol)
        self.assertEqual(self.phase(), "shape")

    def test_implementation_layer_orders_wait_for_the_execution_phase(self):
        self.w.push_main({"policy/params.yml": fakes.POLICY_PARAMS.replace("shaper: orchestrator", "shaper: null")}, "no shaper")
        c.sync_main()
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": tasks_json(task("e", role="evaluator"), task("w", blocked_by=["e"]))})
        self.assertIsNone(self.task_issue("order-%s-w" % pol))                   # held
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.assertIsNone(self.task_issue("order-%s-w" % pol))                   # still held: the Policymaker decides first
        self.w.session(pol)
        w = self.task_issue("order-%s-w" % pol)
        self.assertIn(w, {int(i["issue"]) for _, i in self.hub.dispatched})
        self.assertEqual(self.phase(), "run")
        self.w.session(w, files={"src/w.txt": "w"})
        self.assertEqual(sorted(t["id"] for t in self.record(0)["tasks"]), ["order-%s-e" % pol, "order-%s-w" % pol])

    def test_refused_orders_end_the_policy_phase(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": tasks_json(task("e", role="evaluator"), task("w"))})
        self.assertIsNone(self.task_issue("order-%s-e" % pol))                   # refused whole
        self.assertEqual(self.phase(), "shape")
        self.assertIn("orders only", c.report_fields(self.open_iteration()["number"])["orders_refused"])

    def test_a_crashed_second_pass_fails_open(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.E})
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.w.session(pol, report=False, exit_code="1")
        self.assertEqual(self.hub.report(pol)["status"], "crashed")
        self.assertEqual(self.phase(), "shape")

    def test_a_failed_iteration_does_not_leave_the_waiting_policymaker_open(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.E})
        it.fail_iteration(0, "test")
        self.assertEqual(self.hub.issues[pol]["state"], "closed")


class PolicymakerFailureTest(Flow):
    FIRST = True
    PARAMS = "policymaker_triggers:\n  every_iterations: 1\n"

    def test_two_failed_policymaker_sessions_in_a_row_roll_policy_back(self):
        calls = []
        saved = it.revert_policy
        it.revert_policy = lambda why: calls.append(why) or True
        try:
            it.start_iteration()
            self.w.session(self.session_issue("policymaker"), report=False, exit_code="1")
            self.assertEqual(calls, [])
            self.finish(0)
            self.assertEqual(it.iteration_number(self.open_iteration()), 1)
            self.w.session(self.session_issue("policymaker"), report=False, exit_code="1")
            self.assertEqual([s["status"] for s in it.policymaker_sessions()], ["crashed", "crashed"])
            self.assertEqual(len(calls), 1)
            self.assertIn("two Policymaker sessions", calls[0])
        finally:
            it.revert_policy = saved

if __name__ == "__main__":
    unittest.main()
