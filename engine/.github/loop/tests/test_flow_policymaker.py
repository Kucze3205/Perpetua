"""Policymaker triggers, first iteration, policy phases and failures."""
import json
import os
import unittest
from datetime import timedelta

import fakes
from fakes import common as c, iteration as it, loop, task, tasks_json
from flow_fixtures import Flow
from gate_fixtures import SCRIPT_GATE


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


class PolicyScriptTest(Flow):
    """`policy/policymaker-trigger.py` decides when the Policymaker is called; the engine validates what it prints."""
    SCRIPT = "policy/policymaker-trigger.py"

    def set_script(self, body):
        self.w.push_main({self.SCRIPT: body}, "the Policymaker changes the trigger rule")
        c.sync_main()

    def test_the_trigger_rule_is_policy_and_the_policymaker_can_change_it(self):
        self.set_script("import json\nprint(json.dumps({'reason': 'my own rule'}))\n")
        it.start_iteration()
        self.assertEqual(it.read_state(self.session_issue("policymaker"))["trigger"], "my own rule")

    def test_a_script_that_never_calls_it_leaves_the_last_organization(self):
        self.set_script("print('{\"reason\": null}')\n")
        it.start_iteration()
        self.assertEqual(self.hub.role_issues("policymaker"), [])
        self.assertEqual(c.report_fields(self.open_iteration()["number"])["phase"], "shape")

    def assert_refused(self, body):
        good = open(self.SCRIPT, encoding="utf-8").read()
        fakes.sh(self.w.loop, 'git', 'tag', '-f', 'setup-baseline')
        self.set_script(body)
        it.start_iteration()
        self.assertEqual(it.read_json(it.record_path(0))["status"], "failed")
        c.sync_main()
        self.assertEqual(open(self.SCRIPT, encoding="utf-8").read(), good)             # rolled back
        self.assertTrue(any(x["title"] == "policy/ rolled back" for x in self.hub.issues.values()))

    def test_output_that_is_not_json_fails_the_iteration_and_rolls_policy_back(self):
        self.assert_refused("print('hello')\n")

    def test_output_that_is_not_an_object_is_refused(self):
        self.assert_refused("print('[1]')\n")

    def test_a_reason_that_is_not_a_string_is_refused(self):
        self.assert_refused("print('{\"reason\": 3}')\n")

    def test_an_empty_reason_is_refused(self):
        self.assert_refused("print('{\"reason\": \"\"}')\n")

    def test_oversized_output_is_refused(self):
        self.assert_refused("print('{\"reason\": \"' + 'x' * 5000 + '\"}')\n")

    def test_a_failing_script_is_refused(self):
        self.assert_refused("import sys\nprint('{\"reason\": \"a\"}')\nsys.exit(1)\n")

    def test_a_script_that_cannot_run_in_the_tokenless_job_fails_the_iteration(self):
        self.hub.tokenless_fault = "no-artifact"
        n = it.start_iteration()
        self.assertEqual(it.read_json(it.record_path(n))["status"], "failed")

    def test_one_open_policymaker_whatever_the_script_returns(self):
        self.set_script("raise SystemExit(1)\n")
        self.policymaker_session(0)
        self.assertIsNone(it.policymaker_due(1))


class FirstPolicymakerScriptTest(Flow):
    FIRST = True

    def test_until_the_first_done_the_policymaker_is_called_whatever_the_script_returns(self):
        self.w.push_main({"policy/policymaker-trigger.py": "print('{\"reason\": null}')\n"}, "no trigger ever")
        c.sync_main()
        it.start_iteration()
        self.assertEqual(it.read_state(self.session_issue("policymaker"))["trigger"], "the first iteration of the loop")


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
        self.assertEqual(c.report_fields(cur["number"])["phase"], "shape")       # no wake: the iteration goes on at once
        self.assertIsNotNone(self.session_issue("orchestrator"))
        self.assertEqual(self.hub.issues[pol]["state"], "closed")

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

    def test_the_change_merges_in_its_iteration_when_the_label_filter_lags(self):
        self.hub.lagging_label = "awaiting:iteration"
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, files={"policy/params.yml": fakes.POLICY_PARAMS + "criterion: faster" + chr(10)})
        self.assertIn("faster", self.w.origin_file("policy/params.yml"))
        self.assertEqual(self.hub.issues[pol]["state"], "closed")
        cur = self.open_iteration()
        self.assertEqual(it.read_state(cur["number"])["policy_params"]["criterion"], "faster")

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

    def test_an_iteration_closed_without_a_record_rolls_policy_back(self):
        fakes.sh(self.w.loop, 'git', 'tag', '-f', 'setup-baseline')
        it.start_iteration()
        self.w.push_main({"policy/params.yml": "criterion: broken\n"}, "what a Policymaker session merged")
        c.sync_main()
        self.hub.close_issue(self.open_iteration()["number"], "not planned")                   # closed by hand: no record
        it.start_iteration()
        self.assertNotIn("broken", self.w.origin_file("policy/params.yml"))
        self.assertIn("closed without a record", self.hub.by_label("owner-request:notice")[0]["body"])
        self.assertEqual(it.iteration_number(self.open_iteration()), 1)

    def test_invalid_orders_without_a_shaper_roll_back_policy_and_tell_the_next_policymaker(self):
        fakes.sh(self.w.loop, 'git', 'tag', '-f', 'setup-baseline')
        self.w.push_main({"policy/params.yml": fakes.POLICY_PARAMS.replace("shaper: orchestrator", "shaper: null")}, "no shaper")
        c.sync_main()
        it.start_iteration()
        self.w.session(self.session_issue("policymaker"), session_files={"orders.json": tasks_json(task("w", role="nobody"))})
        self.assertEqual(self.record(0)["status"], "failed")
        self.assertIn("shaper: orchestrator", self.w.origin_file("policy/params.yml"))          # back to the setup baseline
        (note,) = self.hub.by_label("owner-request:notice")
        self.assertIn("no shaper is set", note["body"])
        state = it.read_state(self.open_iteration()["number"])
        self.assertIn(note["number"], [x["number"] for x in state["owner_requests"]["open"]])


class PolicymakerTest(Flow):
    FIRST = True
    ORDER = tasks_json(task("e", role="evaluator", title="Fix evaluator"))

    def test_the_iteration_records_the_orders_of_the_policy_phase(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.ORDER})
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.finish(0)
        rec = self.record(0)
        self.assertEqual(sorted(t["id"] for t in rec["tasks"]), ["order-%s-e" % pol, "t0"])
        self.assertEqual(rec["outcome"]["width"], 2)
        self.assertEqual(rec["policy"]["passes"], 1)          # without `wake` the session does not run again
        self.assertEqual({t["id"]: t.get("pass") for t in rec["tasks"]}, {"order-%s-e" % pol: 1, "t0": None})    # the shaper's task has no pass

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
    """The policy phase is one step: the Policymaker's session ends, its orders start and their dependencies set the order
    of work. With `wake` the same issue runs again after the ordered tasks end."""
    FIRST = True
    YML = SCRIPT_GATE + "policy_passes_notice: 3\n"
    E = tasks_json(task("e", role="evaluator", title="Fix evaluator"))
    A = tasks_json(task("a", role="audit", title="Audit"))
    WAKE_E = json.dumps({"tasks": [task("e", role="evaluator", title="Fix evaluator")], "wake": True})
    WAKE_A = json.dumps({"tasks": [task("a", role="audit", title="Audit")], "wake": True})

    def runs_of(self, n):
        return [i for _, i in self.hub.dispatched if int(i["issue"]) == n]

    def phase(self):
        return c.report_fields(self.open_iteration()["number"])["phase"]

    def test_the_policymaker_returns_when_an_ordered_session_failed_and_sees_it(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        orders = json.dumps({"tasks": [task("e", role="evaluator"), task("a", role="audit")], "wake": True})
        self.w.session(pol, session_files={"orders.json": orders})
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.assertEqual(len(self.runs_of(pol)), 1)                              # one is still running
        self.w.session(self.task_issue("order-%s-a" % pol), report=False, exit_code="1")
        self.assertEqual(len(self.runs_of(pol)), 2)
        ended = it.read_state(pol, it.PASS_MARK, last=True)["ordered"]
        self.assertEqual(sorted((o["role"], o["status"]) for o in ended), [("audit", "crashed"), ("evaluator", "done")])
        sess = os.path.join(self.w.tmp, "sess")
        self.assertEqual(it.stored_state(pol, sess, role="policymaker")["policy_pass"]["pass"], 2)

    def test_a_pass_may_defer_its_changes_to_the_end_of_the_policy_phase(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        later = json.dumps({"merge": "later", "wake": True, "tasks": json.loads(self.E)["tasks"]})
        self.w.session(pol, files={"policy/params.yml": fakes.POLICY_PARAMS + "criterion: one" + chr(10)}, session_files={"orders.json": later})
        self.assertNotIn("criterion: one", self.w.origin_file("policy/params.yml"))      # the ordered session sees the old organization
        self.assertEqual(self.hub.issues[pol]["state"], "open")
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.assertEqual(len(self.runs_of(pol)), 2)
        self.assertNotIn("criterion: one", self.w.origin_file("policy/params.yml"))
        self.w.session(pol, files={"policy/other.txt": "two"}, base="origin/task/%s" % pol)                           # the branch still carries the first change
        self.assertEqual(self.phase(), "shape")
        self.assertIn("criterion: one", self.w.origin_file("policy/params.yml"))
        self.assertEqual(self.hub.issues[pol]["state"], "closed")
        self.assertEqual(it.read_state(self.open_iteration()["number"])["policy_params"]["criterion"], "one")

    def test_a_deferred_change_of_the_evaluator_is_refused_at_the_end_of_the_phase_with_the_rest_of_the_branch(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        later = json.dumps({"merge": "later", "wake": True, "tasks": json.loads(self.E)["tasks"]})
        self.w.session(pol, files={"policy/evaluator/gate.sh": "echo hacked", "policy/params.yml": fakes.POLICY_PARAMS + "criterion: one" + chr(10)},
                       session_files={"orders.json": later})
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.w.session(pol, base="origin/task/%s" % pol)
        self.assertEqual(self.hub.report(pol)["status"], "blocked")
        self.assertNotIn("hacked", self.w.origin_file("policy/evaluator/gate.sh"))
        self.assertNotIn("criterion: one", self.w.origin_file("policy/params.yml"))
        self.assertEqual(self.phase(), "shape")

    def test_an_audit_ordered_and_read_in_the_iteration_does_not_call_the_policymaker_in_the_next(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.WAKE_A})
        self.w.session(self.task_issue("order-%s-a" % pol), files={"audit/latest.md": "numbers"})
        self.w.session(pol)                                                      # reads the report, orders nothing
        self.finish(0)
        self.assertEqual(c.report_fields(self.open_iteration()["number"])["phase"], "shape")
        self.assertEqual(len(self.hub.role_issues("policymaker", "all")), 1)     # iteration 1: no session for the report
        self.w.push_main({"audit/latest.md": "changed"}, "a later audit")
        c.sync_main()
        self.assertEqual(it.policymaker_due(2), "a new audit report")

    def test_a_long_policy_phase_opens_one_informational_notice_that_blocks_nothing(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        notices = lambda: self.hub.by_label("owner-request:notice", "all")
        for k, (orders, tid) in enumerate([(self.WAKE_E, "e"), (self.WAKE_A, "a")] * 3, 1):
            self.w.session(pol, session_files={"orders.json": orders})
            self.assertEqual(len(notices()), 1 if k >= 3 else 0)                 # nothing below the threshold, once at it
            if k == 3:
                self.hub.close_issue(notices()[0]["number"])                      # answered: the iteration is unaffected
            self.assertEqual(self.phase(), "policy")
            self.w.session(self.task_issue("order-%s-%s%s" % (pol, "" if k == 1 else "%s-" % k, tid)))
        self.assertEqual(len(notices()), 1)                                      # not opened again, not even after it was answered
        self.w.session(pol)
        self.assertEqual(self.phase(), "shape")
        self.assertIsNotNone(self.session_issue("orchestrator"))

    def test_refused_orders_end_the_policy_phase(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": json.dumps({"tasks": [task("e", role="evaluator"), task("w")], "wake": True})})
        self.assertIsNone(self.task_issue("order-%s-e" % pol))                   # refused whole
        self.assertEqual(self.phase(), "shape")
        self.assertIn("orders only", c.report_fields(self.open_iteration()["number"])["orders_refused"])

    def test_the_watchdog_resumes_a_policymaker_whose_ordered_session_ended_without_an_event(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.WAKE_E})
        self.hub.close_issue(self.task_issue("order-%s-e" % pol))                # the end-of-session event is lost
        self.hub.runs.clear()
        c.LAUNCHED.clear()                                                       # the watchdog is a fresh process
        self.assertEqual(len(self.runs_of(pol)), 1)
        loop.drive()
        self.assertEqual(len(self.runs_of(pol)), 2)
        self.assertNotIn("awaiting:orders", self.hub.issue(pol)["_labels"])

    def test_a_policymaker_waiting_for_orders_past_the_age_limit_ends_and_the_iteration_fails_open(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.WAKE_E})
        self.hub.runs.clear()
        c.LAUNCHED.clear()
        saved, base = c.now, c.now()
        c.now = lambda: base + timedelta(days=loop.MAX_AGE_DAYS + 1)
        try:
            loop.drive()
        finally:
            c.now = saved
        self.assertEqual(self.hub.report(pol)["status"], "crashed")
        self.assertEqual(self.hub.report(pol)["cause"], "age-limit")
        self.assertEqual(self.hub.issues[pol]["state"], "closed")
        self.assertEqual(self.phase(), "shape")

    def test_a_failed_iteration_does_not_leave_the_waiting_policymaker_open(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.WAKE_E})
        it.fail_iteration(0, "test")
        self.assertEqual(self.hub.issues[pol]["state"], "closed")

    def test_a_crashed_second_run_fails_open(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.WAKE_E})
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.w.session(pol, report=False, exit_code="1")
        self.assertEqual(self.hub.report(pol)["status"], "crashed")
        self.assertEqual(self.phase(), "shape")

    def test_the_record_gives_the_pass_that_placed_each_order(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.WAKE_E})
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.w.session(pol, session_files={"orders.json": self.A})
        self.w.session(self.task_issue("order-%s-2-a" % pol))                    # the audit runs beside the shaper's tasks
        rec = self.record_after_finish()
        self.assertEqual(rec["policy"]["passes"], 2)
        self.assertEqual({t["id"]: t.get("pass") for t in rec["tasks"]},
                         {"order-%s-e" % pol: 1, "order-%s-2-a" % pol: 2, "t0": None})

    def test_a_woken_run_may_wake_again(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        for k, (orders, tid, role) in enumerate([(self.WAKE_E, "e", "evaluator"), (self.WAKE_A, "a", "audit")] * 3, 1):
            self.w.session(pol, session_files={"orders.json": orders})
            ordered = self.task_issue("order-%s-%s%s" % (pol, "" if k == 1 else "%s-" % k, tid))
            self.assertEqual(c.roles_of(c.label_names(self.hub.issue(ordered))), [role])
            self.assertEqual(self.phase(), "policy")
            self.w.session(ordered)
        self.assertEqual(len(self.runs_of(pol)), 7)
        self.w.session(pol)
        self.assertEqual(self.phase(), "shape")

    def test_the_relaunched_branch_holds_what_merged_and_a_second_run_merges_its_own_change(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        orders = json.dumps({"tasks": [task("e", role="evaluator", title="Fix evaluator")], "wake": True})
        self.w.session(pol, files={"policy/params.yml": fakes.POLICY_PARAMS + "criterion: one" + chr(10)}, session_files={"orders.json": orders})
        self.assertIn("criterion: one", self.w.origin_file("policy/params.yml"))     # before the ordered session starts
        self.assertEqual(self.phase(), "policy")
        self.w.session(self.task_issue("order-%s-e" % pol))
        self.assertEqual(fakes.sh(self.w.origin, "git", "rev-parse", "task/%s" % pol), fakes.sh(self.w.origin, "git", "rev-parse", "main"))
        self.w.session(pol, files={"policy/params.yml": fakes.POLICY_PARAMS + "criterion: two" + chr(10)})
        self.assertIn("criterion: two", self.w.origin_file("policy/params.yml"))
        self.assertEqual(self.phase(), "shape")
        self.assertEqual(it.read_state(self.open_iteration()["number"])["policy_params"]["criterion"], "two")

    def record_after_finish(self):
        self.finish(0)
        return self.record(0)

    def test_with_wake_the_shaper_starts_only_after_the_run_that_follows_the_orders_ordered_nothing(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.WAKE_E})
        e = self.task_issue("order-%s-e" % pol)
        self.assertEqual(len(self.runs_of(pol)), 1)
        self.assertIn("awaiting:orders", self.hub.issue(pol)["_labels"])
        self.assertEqual((self.phase(), self.session_issue("orchestrator")), ("policy", None))
        self.w.session(e)                                                        # the ordered session ends
        self.assertEqual(len(self.runs_of(pol)), 2)                              # the same issue runs again
        self.assertNotIn("awaiting:orders", self.hub.issue(pol)["_labels"])
        self.assertEqual((self.phase(), self.session_issue("orchestrator")), ("policy", None))
        self.w.session(pol)                                                      # orders nothing
        self.assertEqual(self.phase(), "shape")
        self.assertIsNotNone(self.session_issue("orchestrator"))
        self.assertEqual(self.hub.issues[pol]["state"], "closed")
        self.assertEqual(len(self.hub.role_issues("policymaker", "all")), 1)     # all its runs are one issue
        self.assertEqual(len(self.record_after_finish()["tasks"]), 2)

    def test_no_pass_sequence_an_audit_ordered_before_workers_runs_first_through_dependencies(self):
        self.w.push_main({"policy/params.yml": fakes.POLICY_PARAMS.replace("shaper: orchestrator", "shaper: null")}, "no shaper")
        c.sync_main()
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": tasks_json(task("a", role="audit"), task("w", blocked_by=["a"]))})
        a, w = self.task_issue("order-%s-a" % pol), self.task_issue("order-%s-w" % pol)
        started = {int(i["issue"]) for _, i in self.hub.dispatched}
        self.assertEqual((a in started, w in started), (True, False))            # the worker is blocked by the audit
        self.assertEqual(self.phase(), "run")
        self.w.session(a)
        self.assertIn(w, {int(i["issue"]) for _, i in self.hub.dispatched})
        self.w.session(w, files={"src/w.txt": "w"})
        self.assertEqual(sorted(t["id"] for t in self.record(0)["tasks"]), ["order-%s-a" % pol, "order-%s-w" % pol])

    def test_without_wake_the_orders_run_beside_the_shaper_and_the_policymaker_does_not_return(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.E})
        e = self.task_issue("order-%s-e" % pol)
        self.assertIn(e, {int(i["issue"]) for _, i in self.hub.dispatched})
        self.assertEqual((self.phase(), self.hub.issues[pol]["state"]), ("shape", "closed"))
        self.assertIsNotNone(self.session_issue("orchestrator"))
        self.w.session(e)
        self.assertEqual(len(self.runs_of(pol)), 1)


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

    def test_a_session_of_several_runs_counts_once_toward_the_rollback(self):
        calls = []
        saved = it.revert_policy
        it.revert_policy = lambda why: calls.append(why) or True
        try:
            it.start_iteration()
            pol = self.session_issue("policymaker")
            for _ in range(2):
                self.w.session(pol, session_files={"orders.json": json.dumps({"tasks": [task("e", role="evaluator")], "wake": True})})
                self.w.session([x["number"] for x in self.hub.role_issues("evaluator", "open")][-1])
            self.w.session(pol, report=False, exit_code="1")                    # the third run crashes
            self.assertEqual([s["status"] for s in it.policymaker_sessions()], ["crashed"])
            self.finish(0)
            self.assertEqual(calls, [])                                          # one failed session, not three
            self.w.session(self.session_issue("policymaker"), report=False, exit_code="1")
            self.assertEqual(len(calls), 1)
        finally:
            it.revert_policy = saved

    def test_a_policymaker_issue_closed_unstarted_is_not_a_failed_session(self):
        calls = []
        saved = it.revert_policy
        it.revert_policy = lambda why: calls.append(why) or True
        try:
            it.start_iteration()
            self.w.session(self.session_issue("policymaker"))
            self.finish(0)
            pol = self.session_issue("policymaker")
            probe = self.hub.create_issue("probe", "", labels=("role:policymaker",))      # as the E2E probe leaves it
            self.hub.close_issue(probe, "not planned")
            self.w.session(pol, report=False, exit_code="1")
            self.assertEqual([s["status"] for s in it.policymaker_sessions()], ["crashed", "done"])
            self.assertEqual(calls, [])
        finally:
            it.revert_policy = saved

    def test_a_done_session_the_closed_list_does_not_return_yet_stops_the_count(self):
        calls = []
        saved, listed = it.revert_policy, c.list_issues
        it.revert_policy = lambda why: calls.append(why) or True
        try:
            it.start_iteration()
            for k in range(2):
                self.w.session(self.session_issue("policymaker"), report=False, exit_code="1")
                self.finish(k)
            self.assertEqual(len(calls), 1)
            pol = self.session_issue("policymaker")
            c.list_issues = lambda state="open", labels=(): [x for x in listed(state, labels) if x["number"] != pol]
            self.w.session(pol)
            self.assertEqual(len(calls), 1)
        finally:
            it.revert_policy, c.list_issues = saved, listed


if __name__ == "__main__":
    unittest.main()
