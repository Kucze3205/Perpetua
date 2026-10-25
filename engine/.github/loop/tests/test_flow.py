"""The iteration lifecycle on a fake GitHub and real git: start, shaper, tasks, record, next iteration; the Policymaker on
triggers, at the start of an iteration."""
import json
import os
import unittest
from unittest import mock
from datetime import timedelta

import fakes
from fakes import common as c, iteration as it, loop, task, tasks_json
from test_merge import GATE_SH, SCRIPT_GATE


class Flow(unittest.TestCase):
    YML = SCRIPT_GATE
    PARAMS = None
    FIRST = False       # True: no Policymaker session has run yet, so the first iteration starts with one

    def setUp(self):
        self.w = fakes.worker_world(self.YML, first_iteration=self.FIRST)
        self.hub = self.w.hub
        files = {"policy/evaluator/gate.sh": GATE_SH}
        if self.PARAMS:
            files["policy/params.yml"] = fakes.POLICY_PARAMS + self.PARAMS
        self.w.push_main(files, "setup")
        c.sync_main()

    def tearDown(self):
        self.w.close()

    # -- helpers
    def open_iteration(self):
        return it.open_iteration()

    def session_issue(self, role, state="open", n=None):
        found = [x for x in self.hub.role_issues(role, state) if n is None or c.iteration_of(c.label_names(x)) == n]
        return found[-1]["number"] if found else None

    def play_shaper(self, *tasks, files=None, k=0):
        s = self.session_issue("orchestrator", n=k)
        self.w.session(s, files=files or {"journal/%04d.md" % k: "## State\nx\n"}, session_files={"tasks.json": tasks_json(*tasks)})
        return s

    def task_issue(self, tid):
        for x in self.hub.list_issues("all"):
            if c.marker(x["body"], "task-id") == tid:
                return x["number"]

    def record(self, k):
        t = self.w.origin_file(it.record_path(k))
        return json.loads(t) if t else None

    def finish(self, k, tid=None):
        """Iteration k from its shaper to its record, with one worker task."""
        tid = tid or "t%s" % k
        self.play_shaper(task(tid), k=k)
        self.w.session(self.task_issue(tid), files={"src/%s.txt" % tid: tid})

    def policymaker_session(self, n=0):
        """A Policymaker session opening iteration n, started by hand (the iteration is open without waiting for it)."""
        return it.start_policymaker(n, "a test")


class FirstIterationTest(Flow):
    def test_an_iteration_from_the_shaper_to_its_record_and_the_next_iteration(self):
        k = it.start_iteration()
        self.assertEqual(k, 0)
        cur = self.open_iteration()
        self.assertEqual(c.report_fields(cur["number"])["phase"], "shape")
        self.assertEqual(len(self.hub.role_issues("orchestrator")), 1)
        self.assertEqual(self.hub.role_issues("policymaker"), [])
        self.assertEqual(self.hub.dispatched[-1][0], "session.yml")

        self.play_shaper(task("a"), task("b", blocked_by=["a"]), task("c"))
        self.assertEqual(c.report_fields(cur["number"])["phase"], "run")
        self.assertEqual(c.report_fields(cur["number"])["width"], "2")
        self.assertEqual(self.hub.issues[self.task_issue("a")]["state"], "open")
        launched = {int(i["issue"]) for w, i in self.hub.dispatched if w == "session.yml"}
        self.assertIn(self.task_issue("a"), launched)
        self.assertIn(self.task_issue("c"), launched)
        self.assertNotIn(self.task_issue("b"), launched)               # blocked by a
        self.assertEqual(self.w.origin_file("journal/0000.md"), "## State\nx\n")
        frozen = json.loads(self.w.origin_file(it.tasks_path(0)))
        self.assertEqual([t["id"] for t in frozen], ["a", "b", "c"])

        self.w.session(self.task_issue("a"), files={"src/a.txt": "A"})
        self.assertEqual(self.w.origin_file("src/a.txt"), "A")
        self.assertEqual(self.hub.issues[self.task_issue("a")]["state"], "closed")
        self.assertIn(self.task_issue("b"), {int(i["issue"]) for w, i in self.hub.dispatched})     # unblocked by a closing
        self.w.session(self.task_issue("c"), files={"src/c.txt": "C"})
        self.assertIsNone(self.record(0))                                   # b still open
        self.w.session(self.task_issue("b"), files={"src/b.txt": "B"})

        rec = self.record(0)
        self.assertEqual((rec["iteration"], rec["status"]), (0, "closed"))
        self.assertNotIn("macro_plan", rec)
        self.assertEqual(rec["decided_by"], "orchestrator")
        self.assertEqual(len(rec["tasks"]), 3)
        self.assertEqual(rec["outcome"]["verdict"], {"value": "accepted", "issuer": "script", "evaluator_rev": rec["outcome"]["verdict"]["evaluator_rev"], "improved": True})
        self.assertEqual(rec["outcome"]["width"], 2)
        sess = {s["issue"]: s for s in rec["outcome"]["sessions"]}
        a = sess[self.task_issue("a")]
        self.assertEqual((a["status"], a["merged"], a["verdict"], a["tokens"]["in"], a["tokens"]["out"], a["turns"]), ("done", True, "accepted", 100, 50, 7))
        self.assertEqual(rec["outcome"]["tokens"]["in"], 100 * 4)               # the shaper counts too
        self.assertIn("m1", rec["outcome"]["tokens"]["by_model"])
        self.assertTrue(rec["outcome"]["critical_path_s"] >= 0)
        self.assertEqual(rec["engine_version"], c.engine_version())
        self.assertNotIn("iterations", rec["input_state"])
        self.assertEqual(self.hub.issues[cur["number"]]["state"], "closed")

        # The next iteration starts at once, on the last organization: no trigger is due, so no Policymaker session.
        nxt = self.open_iteration()
        self.assertEqual(it.iteration_number(nxt), 1)
        self.assertEqual(c.report_fields(nxt["number"])["phase"], "shape")
        self.assertEqual(self.hub.role_issues("policymaker"), [])
        state = it.read_state(nxt["number"])
        self.assertEqual(state["iterations"][0]["n"], 0)
        self.assertEqual(state["iterations"][0]["improved"], True)
        self.assertNotIn("aim", state["iterations"][0])
        self.assertEqual(state["no_progress"], 0)

    def test_only_one_iteration_is_open_at_a_time(self):
        it.start_iteration()
        self.assertIsNone(it.start_iteration())
        self.assertEqual(len(self.hub.by_label("loop:map")), 1)

    def test_the_switch_stops_everything(self):
        self.hub.vars["AUTOPILOT"] = "off"
        self.assertIsNone(it.start_iteration())
        self.assertEqual(set(self.hub.issues), {0})                 # only the fixture's earlier Policymaker session


class RaceTest(Flow):
    def test_of_two_closers_only_the_first_writes_the_record(self):
        it.start_iteration()
        cur = self.open_iteration()["number"]
        os.environ["GITHUB_RUN_ID"] = "111"
        self.assertTrue(it.claim_closing(cur))
        os.environ["GITHUB_RUN_ID"] = "222"
        self.assertFalse(it.claim_closing(cur))
        os.environ["GITHUB_RUN_ID"] = "111"
        self.assertTrue(it.claim_closing(cur))                 # the same run may come back

    def test_a_closer_that_died_blocks_only_for_a_while(self):
        it.start_iteration()
        cur = self.open_iteration()["number"]
        os.environ["GITHUB_RUN_ID"] = "111"
        it.claim_closing(cur)
        saved = c.now
        base = saved()
        c.now = lambda: base + timedelta(minutes=11)
        try:
            os.environ["GITHUB_RUN_ID"] = "222"
            self.assertTrue(it.claim_closing(cur))
        finally:
            c.now = saved

    def test_a_second_iteration_started_at_the_same_moment_gives_way(self):
        it.start_iteration()
        first = self.open_iteration()["number"]
        real = it.open_iteration
        it.open_iteration = lambda: None                       # the second run did not see the first yet
        try:
            self.assertIsNone(it.start_iteration())
        finally:
            it.open_iteration = real
        self.assertEqual([x["number"] for x in self.hub.by_label("loop:map")], [first])


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
        self.assertEqual(c.report_fields(cur["number"])["phase"], "shape")
        e = self.task_issue("order-%s-e" % pol)
        self.assertEqual(c.iteration_of(c.label_names(self.hub.issue(e))), 0)    # the same iteration
        self.assertIn(e, {int(i["issue"]) for _, i in self.hub.dispatched})

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

    def test_a_policy_change_of_the_opening_session_holds_in_the_same_iteration(self):
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

    def test_the_iteration_waits_for_the_orders_and_records_them(self):
        it.start_iteration()
        pol = self.session_issue("policymaker")
        self.w.session(pol, session_files={"orders.json": self.ORDER})
        self.finish(0)
        self.assertIsNone(self.record(0))                                        # the order is still open
        self.w.session(self.task_issue("order-%s-e" % pol))
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


class ShaperFailureTest(Flow):
    def test_an_invalid_task_graph_fails_the_iteration_and_the_loop_goes_on(self):
        it.start_iteration()
        s = self.session_issue("orchestrator")
        self.w.session(s, session_files={"tasks.json": tasks_json(task("a", role="nobody"))})
        self.assertEqual(self.hub.report(s)["status"], "partial")
        self.assertIn("role 'nobody'", self.hub.prose(s))
        rec = self.record(0)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(it.iteration_number(self.open_iteration()), 1)         # the next one started

    def test_no_tasks_file_is_a_failure_too(self):
        it.start_iteration()
        self.w.session(self.session_issue("orchestrator"))
        self.assertEqual(self.record(0)["status"], "failed")

    def test_three_failed_iterations_in_a_row_stop_the_loop_with_an_outage(self):
        it.start_iteration()
        for k in range(3):
            self.w.session(self.session_issue("orchestrator", n=k), session_files={"tasks.json": "{}"})
        self.assertEqual([r["status"] for r in it.records()], ["failed"] * 3)
        self.assertIsNone(self.open_iteration())
        self.assertEqual(len(self.hub.by_label("outage")), 1)


class OwnerGateTest(Flow):
    YML = ""

    def start_with_one_task(self):
        it.start_iteration()
        self.play_shaper(task("a"))
        return self.task_issue("a")

    def test_the_owner_says_yes(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        self.assertEqual(self.hub.issues[t]["state"], "open")
        self.assertIn("awaiting:owner", c.label_names(self.hub.issues[t] | {"labels": list(self.hub.issues[t]["_labels"])}))
        self.assertEqual(self.hub.report(t)["merged"], "pending-owner")
        self.assertIsNone(self.w.origin_file("src/a.txt"))
        (req,) = [x for x in self.hub.by_label("owner-request:merge-acceptance")]
        self.assertEqual(self.hub.issues[req["number"]]["assignees"], ["ow"])
        self.assertIsNone(self.record(0))                                          # the iteration waits
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertEqual(self.w.origin_file("src/a.txt"), "A")
        self.assertEqual(self.hub.issues[t]["state"], "closed")
        self.assertEqual(self.hub.report(t)["merged"], "true")
        self.assertEqual(self.record(0)["status"], "closed")
        self.assertTrue(self.record(0)["outcome"]["verdict"]["improved"])

    def test_the_owner_says_no_and_the_branch_stays_reachable(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        (req,) = self.hub.by_label("owner-request:merge-acceptance")
        self.hub.answer(req["number"], False)
        loop.on_request_closed(req["number"])
        self.assertIsNone(self.w.origin_file("src/a.txt"))
        self.assertEqual(self.hub.report(t)["status"], "rejected")
        self.assertTrue(self.w.origin_has_ref("refs/tags/iter/0000/%s" % t))
        self.assertTrue(self.w.origin_has_ref("refs/heads/task/%s" % t))
        self.assertFalse(self.record(0)["outcome"]["verdict"]["improved"])

    def test_a_request_is_handled_once(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        (req,) = self.hub.by_label("owner-request:merge-acceptance")
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        loop.on_request_closed(req["number"])
        self.assertIn("request:handled", self.hub.issues[req["number"]]["_labels"])

    def test_the_watchdog_sweeps_an_answer_whose_event_was_lost(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        (req,) = self.hub.by_label("owner-request:merge-acceptance")
        self.hub.answer(req["number"], True)
        loop.sweep_requests()
        self.assertEqual(self.w.origin_file("src/a.txt"), "A")

    def test_waiting_for_the_owner_is_not_a_jam(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        before = len(self.hub.dispatched)
        self.hub.runs.clear()
        common_launched = c.LAUNCHED.copy()
        self.assertEqual(loop.drive(), 0)
        self.assertEqual(len(self.hub.dispatched), before)
        self.assertTrue(loop.commitments())
        self.assertEqual(common_launched, c.LAUNCHED)


class ConsentTest(Flow):
    def setUp(self):
        super().setUp()
        self.w.push_main({"src/.keep": ""}, "x")

    def start(self):
        it.start_iteration()
        self.play_shaper(task("n", role="net", consent={"what": "Search the web once", "why": "need a fact"}), task("w"))
        return self.task_issue("n")

    def test_a_task_beyond_the_limits_waits_for_the_owner(self):
        n = self.start()
        launched = {int(i["issue"]) for _, i in self.hub.dispatched}
        self.assertNotIn(n, launched)
        (req,) = self.hub.by_label("owner-request:consent")
        self.assertIn("(Search the web once)" if False else "Search the web once", self.hub.issues[req["number"]]["body"])
        self.assertIn((n, req["number"]), self.hub.deps)
        self.assertIn("consent:%s" % req["number"], self.hub.issues[n]["_labels"])
        self.assertIn(self.task_issue("w"), launched)                              # the rest goes on

    def test_consent_granted_starts_the_session_and_resolve_agrees(self):
        n = self.start()
        (req,) = self.hub.by_label("owner-request:consent")
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertIn(n, {int(i["issue"]) for _, i in self.hub.dispatched})
        out = os.path.join(self.w.tmp, "out")
        os.environ["GITHUB_OUTPUT"] = out
        loop.resolve(n)
        with open(out) as fh:
            text = fh.read()
        self.assertIn("consented=true", text)
        self.assertIn("kind=agent", text)

    def test_resolve_refuses_without_the_consent(self):
        n = self.start()
        with self.assertRaises(SystemExit) as e:
            loop.resolve(n)
        self.assertIn("beyond the accepted limits", str(e.exception))
        self.assertEqual(self.hub.report(n)["status"], "blocked")

    def test_consent_declined_closes_the_task_as_blocked(self):
        n = self.start()
        (req,) = self.hub.by_label("owner-request:consent")
        self.hub.answer(req["number"], False)
        loop.on_request_closed(req["number"])
        self.assertEqual(self.hub.issues[n]["state"], "closed")
        self.assertEqual(self.hub.report(n)["status"], "blocked")


POOL_YML = """jobs:
  session:
    steps:
      - name: Other
        env:
          NOT_A_SECRET: x
      - name: Session
        env:
          CLAUDE_CODE_OAUTH_TOKEN: t
          %s
"""


class ConsentSecretsTest(Flow):
    def setUp(self):
        super().setUp()
        self.w.push_main({"src/.keep": "", ".github/workflows/session.yml": POOL_YML % "STRIPE_KEY: x\n          SMTP_PASS: y"}, "x")
        c.sync_main()
        it.start_iteration()
        self.play_shaper(task("a", consent={"secrets": ["STRIPE_KEY"]}), task("b", consent={"secrets": ["SMTP_PASS"]}), task("c"))

    def request_of(self, tid):
        label = next(l for l in self.hub.issues[self.task_issue(tid)]["_labels"] if l.startswith("consent:"))
        return int(label.split(":")[1])

    def resolved(self, tid):
        out = os.path.join(self.w.tmp, "out-" + tid)
        os.environ["GITHUB_OUTPUT"] = out
        loop.resolve(self.task_issue(tid))
        with open(out) as fh:
            return dict(l.rstrip("\n").split("=", 1) for l in fh)

    def grant(self, tid):
        self.hub.answer(self.request_of(tid), True)
        loop.on_request_closed(self.request_of(tid))

    def test_names_enter_the_frozen_text_and_a_later_edit_is_not_applied(self):
        n = self.request_of("a")
        body = self.hub.issues[n]["body"]
        self.assertEqual(it.frozen_text(body), "secrets: STRIPE_KEY")
        self.hub.issues[n]["body"] = body.replace("STRIPE_KEY", "OTHER_KEY")
        self.assertIsNone(it.frozen_text(self.hub.issues[n]["body"]))
        self.assertEqual(loop.secrets_of_consent(self.hub.issues[self.task_issue("a")]["_labels"]), [])

    def test_two_sessions_get_their_own_names(self):
        self.grant("a")
        self.grant("b")
        self.assertEqual(self.resolved("a")["secrets"], "|STRIPE_KEY|")
        self.assertEqual(self.resolved("b")["secrets"], "|SMTP_PASS|")

    def test_a_task_within_the_limits_has_the_consent_it_was_granted(self):
        self.grant("a")
        o = self.resolved("a")
        self.assertEqual(o["consented"], "true")
        self.assertEqual(self.resolved("c")["consented"], "false")
        self.assertEqual(self.resolved("c")["secrets"], "")

    def staged(self, tid):
        work = os.path.join(self.w.tmp, "stagework-" + tid)
        fakes.sh(self.w.tmp, "git", "clone", "-q", self.w.origin, work)
        trusted = os.path.join(self.w.tmp, "trusted-" + tid)
        os.environ["RUNNER_TEMP"] = os.path.join(self.w.tmp, "run-" + tid)
        # the real stage; only the native-ELF executor preparation is left out (it needs Linux)
        with mock.patch.object(loop.executor_boundary, "prepare"):
            loop.stage(self.task_issue(tid), work, trusted)
        return work, trusted

    def test_stage_writes_the_approved_names_for_agent_sh(self):
        self.grant("a")
        _, trusted = self.staged("a")
        with open(os.path.join(trusted, "secrets")) as fh:
            self.assertEqual(fh.read(), "STRIPE_KEY\n")
        _, trusted = self.staged("c")
        with open(os.path.join(trusted, "secrets")) as fh:
            self.assertEqual(fh.read(), "")

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell: bash on Windows may be WSL")
    def test_an_empty_approved_secret_stops_a_really_staged_start(self):
        import subprocess
        self.grant("a")
        work, trusted = self.staged("a")
        out = os.environ["RUNNER_TEMP"]
        os.makedirs(out, exist_ok=True)
        env = dict(os.environ, WORK=work, KIND="agent", ISSUE="1", AGENT_TIMEOUT="1", STRIPE_KEY="")
        r = subprocess.run(["bash", os.path.join(trusted, "agent.sh")], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("No such file", r.stderr)
        with open(os.path.join(out, "secret-missing")) as fh:
            self.assertEqual(fh.read().strip(), "STRIPE_KEY")

    def test_the_pool_is_the_env_of_the_session_step(self):
        self.assertEqual(loop.secret_pool(), ["SMTP_PASS", "STRIPE_KEY"])

    def test_a_name_outside_the_pool_blocks_the_task_and_asks_for_the_capability(self):
        self.w.push_main({".github/workflows/session.yml": POOL_YML % "SMTP_PASS: y"}, "drop STRIPE_KEY from the pool")
        c.sync_main()
        self.grant("a")
        n = self.task_issue("a")
        with self.assertRaises(SystemExit) as e:
            self.resolved("a")
        self.assertEqual(str(e.exception), "secret-missing:STRIPE_KEY")
        self.assertEqual(self.hub.issues[n]["state"], "closed")
        self.assertEqual(self.hub.report(n)["status"], "blocked")
        self.assertEqual(self.hub.report(n)["cause"], "secret-missing:STRIPE_KEY")
        (cap,) = self.hub.by_label("owner-request:capability")
        self.assertNotIn("hunter2", self.hub.issues[cap["number"]]["body"])

    def test_a_refusal_stops_the_start_and_a_new_session_keeps_the_same_consent(self):
        self.grant("a")
        self.assertEqual(self.resolved("a")["secrets"], "|STRIPE_KEY|")
        self.assertEqual(self.resolved("a")["secrets"], "|STRIPE_KEY|")      # a session after a crash: the same frozen consent
        self.hub.answer(self.request_of("b"), False)
        loop.on_request_closed(self.request_of("b"))
        self.assertEqual(self.hub.report(self.task_issue("b"))["cause"], "consent-declined")
        with self.assertRaises(SystemExit):
            self.resolved("b")

    def test_the_epilogue_turns_the_missing_secret_marker_into_blocked_and_a_capability(self):
        self.grant("a")
        n = self.task_issue("a")
        tmp = os.path.join(self.w.tmp, "runner")
        os.makedirs(tmp)
        with open(os.path.join(tmp, "secret-missing"), "w") as fh:
            fh.write("STRIPE_KEY\n")
        os.environ["RUNNER_TEMP"] = tmp
        loop.finalize(n, os.path.join(self.w.tmp, "nowork"))
        self.assertEqual(self.hub.report(n)["cause"], "secret-missing:STRIPE_KEY")
        self.assertEqual(len(self.hub.by_label("owner-request:capability")), 1)

    def test_a_marker_for_a_name_without_consent_is_ignored(self):
        n = self.task_issue("c")
        tmp = os.path.join(self.w.tmp, "runner")
        os.makedirs(tmp)
        with open(os.path.join(tmp, "secret-missing"), "w") as fh:
            fh.write("STRIPE_KEY\n")
        os.environ["RUNNER_TEMP"] = tmp
        try:
            loop.finalize(n, os.path.join(self.w.tmp, "nowork"))
        except Exception:
            pass
        self.assertEqual(self.hub.by_label("owner-request:capability"), [])

    def test_declined_consent_gives_no_secrets(self):
        self.hub.answer(self.request_of("a"), False)
        loop.on_request_closed(self.request_of("a"))
        labels = self.hub.issues[self.task_issue("a")]["_labels"]
        self.assertEqual(loop.consent_status(c.profiles()["worker"], labels), (False, None))
        self.assertEqual(loop.secrets_of_consent(labels), [])

    def test_invalid_names_are_refused_without_echoing_them(self):
        for bad in (["lower"], ["A=hunter2"], ["A", "A"], "A", [1]):
            errors = it.consent_errors({"secrets": bad})
            self.assertTrue(errors, bad)
            self.assertNotIn("hunter2", " ".join(errors))
        self.assertEqual(it.consent_errors({"what": "w", "secrets": ["A_1", "B"]}), [])


class EvaluatorChangeTest(Flow):
    def start(self):
        """The Policymaker orders the evaluator role while no iteration is open: iteration 0 starts with the order."""
        pol = self.policymaker_session(0)
        self.w.session(pol, session_files={"orders.json": tasks_json(task("e", role="evaluator", title="Fix evaluator"))})
        self.play_shaper(task("a"))
        return self.task_issue("order-%s-e" % pol)

    def test_the_policymaker_orders_the_evaluator_role(self):
        e = self.start()
        self.assertIsNotNone(e)
        self.assertEqual(c.roles_of(c.label_names(self.hub.issue(e))), ["evaluator"])

    def test_the_shaper_cannot_order_it(self):
        it.start_iteration()
        s = self.session_issue("orchestrator")
        self.w.session(s, session_files={"tasks.json": tasks_json(task("e", role="evaluator"))})
        self.assertIn("ordered only by the Policymaker", self.hub.prose(s))

    def test_verified_change_takes_effect_and_the_old_one_stands_meanwhile(self):
        e = self.start()
        self.w.session(e, files={"policy/evaluator/gate.sh": "echo new\n"})
        self.assertEqual(self.w.origin_file("policy/evaluator/gate.sh"), GATE_SH)       # still the old one
        self.assertEqual(self.hub.report(e)["merged"], "pending-verifier")
        v = self.session_issue("evaluator-verifier")
        self.assertIn("<!-- verifies: %s -->" % e, self.hub.issues[v]["body"])
        self.w.session(v, report_extra="", session_files={})
        # the verifier's report has no `result: pass`: the change is sent back
        self.assertEqual(self.hub.report(e)["status"], "rejected")
        self.assertEqual(self.w.origin_file("policy/evaluator/gate.sh"), GATE_SH)

    def test_the_verifier_passes_it(self):
        e = self.start()
        self.w.session(e, files={"policy/evaluator/gate.sh": "echo new\n"})
        self.w.session(self.session_issue("evaluator-verifier"), yaml="result: pass\n")
        self.assertEqual(self.w.origin_file("policy/evaluator/gate.sh"), "echo new\n")
        self.assertEqual(self.hub.report(e)["merged"], "true")
        self.assertEqual(self.hub.issues[e]["state"], "closed")

    def test_a_verifier_that_cannot_start_sends_the_change_back(self):
        e = self.start()
        self.w.session(e, files={"policy/evaluator/gate.sh": "echo new\n"})
        profs = self.w.origin_file("policy/profiles.yml").replace("evaluator-verifier:\n  write: []\n  tools: [Read, Write]",
                                                                  "evaluator-verifier:\n  write: []\n  tools: [Read, 'Bash(rm:*)']")
        self.assertIn("Bash(rm:*)", profs)
        self.w.push_main({"policy/profiles.yml": profs}, "verifier beyond the limits")
        c.sync_main()
        with self.assertRaises(SystemExit):
            loop.resolve(self.session_issue("evaluator-verifier"))
        self.assertEqual(self.hub.report(e)["status"], "rejected")              # not left waiting for a verifier forever
        self.assertNotIn("awaiting:verifier", self.hub.issues[e]["_labels"])
        self.assertEqual(self.w.origin_file("policy/evaluator/gate.sh"), GATE_SH)


class GoalGateTest(Flow):
    def finish_one(self):
        it.start_iteration()
        self.play_shaper(task("a"))
        self.w.session(self.task_issue("a"), files={"src/a.txt": "A"})

    def gate(self, reached, owner_confirms):
        self.w.push_main({"goal/gate.yml": "command: \"echo '{\\\"reached\\\": %s}'\"\nowner_confirms: %s\n" % ("true" if reached else "false", "true" if owner_confirms else "false")}, "gate")
        c.sync_main()

    def test_without_an_accepted_gate_the_loop_never_ends_itself(self):
        self.finish_one()
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.assertIsNotNone(self.open_iteration())

    def test_gate_not_reached(self):
        self.gate(False, True)
        self.finish_one()
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.assertEqual(self.hub.by_label("owner-request:result-confirmation"), [])

    def test_reached_and_the_owner_confirms(self):
        self.gate(True, True)
        self.finish_one()
        (req,) = self.hub.by_label("owner-request:result-confirmation")
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertIn("confirmed by the owner", self.w.origin_file("GOAL_REACHED"))
        c.sync_main()
        self.assertFalse(c.guard_ok())

    def test_reached_and_the_owner_declines_so_the_loop_goes_on_without_asking_again_for_the_same_state(self):
        self.gate(True, True)
        self.finish_one()
        (req,) = self.hub.by_label("owner-request:result-confirmation")
        self.hub.answer(req["number"], False)
        loop.on_request_closed(req["number"])
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.assertTrue(c.guard_ok())
        self.assertTrue(it.confirmation_declined(c.marker(self.hub.issues[req["number"]]["body"], "request-ref")))

    def test_reached_without_owner_confirmation_ends_the_loop(self):
        self.gate(True, False)
        self.finish_one()
        self.assertIn("reached at", self.w.origin_file("GOAL_REACHED"))

    def test_the_owner_accepts_a_proposed_gate_and_only_the_frozen_text(self):
        self.finish_one()
        prop = json.dumps([{"kind": "goal-gate", "title": "Accept gate", "what": "w", "why": "y", "unblocks": "end", "frozen": "command: x\nowner_confirms: true\n"}])
        self.w.session(self.policymaker_session(0), session_files={"requests.json": prop})
        (req,) = self.hub.by_label("owner-request:goal-gate")
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertEqual(self.w.origin_file("goal/gate.yml"), "command: x\nowner_confirms: true\n")

    def test_an_edited_proposal_is_not_applied(self):
        self.finish_one()
        prop = json.dumps([{"kind": "goal-change", "title": "New goal", "what": "w", "why": "y", "unblocks": "u", "frozen": "Make it great\n"}])
        self.w.session(self.policymaker_session(0), session_files={"requests.json": prop})
        (req,) = self.hub.by_label("owner-request:goal-change")
        self.hub.issues[req["number"]]["body"] = self.hub.issues[req["number"]]["body"].replace("Make it great", "Make it terrible")
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertIsNone(self.w.origin_file("goal/goal.md"))
        self.assertIn("Not applied", self.hub.cmts[req["number"]][-1]["body"])

    def test_agents_cannot_open_the_engines_kinds_of_request(self):
        self.finish_one()
        prop = json.dumps([{"kind": "result-confirmation", "title": "forged", "what": "w"}, {"kind": "consent", "title": "forged"},
                           {"kind": "goal-gate", "title": "no text"}, {"kind": "question", "title": "ok?", "what": "w"}])
        self.w.session(self.policymaker_session(0), session_files={"requests.json": prop})
        self.assertEqual([x["title"] for x in self.hub.by_label("owner-request")], ["ok?"])


if __name__ == "__main__":
    unittest.main()
