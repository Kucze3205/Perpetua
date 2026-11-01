"""Iteration lifecycle, task execution and races."""
import json
import os
import runpy
import sys
import unittest
from unittest import mock
from datetime import timedelta

import fakes
from fakes import common as c, iteration as it, loop, task, tasks_json
import task_graph as graph
from flow_fixtures import Flow


class FirstIterationTest(Flow):
    def test_script_entry_begins_work_without_a_due_policymaker(self):
        with mock.patch.object(sys, "argv", [loop.__file__, "start"]):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(loop.__file__, run_name="__main__")
        self.assertEqual(result.exception.code, 0)
        cur = self.open_iteration()
        self.assertEqual(c.report_fields(cur["number"])["phase"], "shape")
        self.assertEqual(len(self.hub.role_issues("orchestrator")), 1)

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
        frozen = json.loads(self.w.origin_file(graph.tasks_path(0)))
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
        self.assertEqual(rec["policy"]["passes"], 0)                        # no policy phase
        self.assertTrue(all("pass" not in t for t in rec["tasks"]))
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

    def test_tasks_are_frozen_and_phase_is_run_before_any_task_launch(self):
        it.start_iteration()
        current = self.open_iteration()["number"]
        seen = []
        launch = c.launch

        def inspect_launch(number):
            task_id = c.marker(c.issue(number)["body"], "task-id")
            if task_id:
                seen.append(task_id)
                self.assertEqual(c.report_fields(current)["phase"], "run")
                frozen = graph.read(0)
                self.assertEqual({t["id"] for t in frozen}, {"a", "b"})
                self.assertEqual(next(t["issue"] for t in frozen if t["id"] == task_id), number)
            return launch(number)

        with mock.patch.object(c, "launch", side_effect=inspect_launch):
            self.play_shaper(task("b", blocked_by=["a"]), task("a"))
        self.assertEqual(set(seen), {"a", "b"})

    def test_an_old_format_record_stays_as_it_is_and_the_next_iteration_reads_it(self):
        old = json.dumps({"schema_version": 1, "iteration": 0, "status": "closed", "policy": {"orders_refused": None},
                          "tasks": [{"id": "x", "issue": 1}], "outcome": {"verdict": {"value": "accepted", "improved": True}}}, indent=1) + "\n"
        self.w.push_main({it.record_path(0): old}, "an old record")
        c.sync_main()
        self.assertEqual(it.start_iteration(), 1)
        self.finish(1)
        self.assertEqual(self.w.origin_file(it.record_path(0)), old)
        self.assertEqual(self.record(1)["policy"]["passes"], 0)
        self.assertEqual([x["n"] for x in it.index_lines()], [0, 1])

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

    def test_partial_ends_the_iteration_and_the_graph_is_not_read(self):
        # Decision (b) of docs/perpetua/session-boundary.md: `partial` is final for the shaper, no retry,
        # no graph loading. The cause (a prompt demanding a shell) is removed at the source instead.
        it.start_iteration()
        s = self.session_issue("orchestrator")
        self.w.session(s, status="partial", files={"journal/0000.md": "## State\nx\n"},
                       session_files={"tasks.json": tasks_json(task("a"))})
        self.assertEqual(self.record(0)["status"], "failed")
        self.assertIsNone(self.task_issue("a"))

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


class TaskCapTest(Flow):
    def test_a_graph_over_the_cap_is_refused_and_creates_no_issues(self):
        it.start_iteration()
        before = len(self.hub.list_issues("all"))
        s = self.session_issue("orchestrator")
        self.w.session(s, session_files={"tasks.json": tasks_json(*[task("t%s" % k) for k in range(graph.MAX_TASKS + 1)])})
        self.assertIn("the cap is %s" % graph.MAX_TASKS, self.hub.prose(s))
        self.assertIsNone(self.task_issue("t0"))
        self.assertEqual(self.record(0)["status"], "failed")
        self.assertEqual([x for x in self.hub.list_issues("all") if x["number"] > before + 2 and "role:worker" in c.label_names(x)], [])

    def test_orders_over_the_cap_are_refused_too(self):
        errors = it.validate_tasks({"tasks": [task("t%s" % k) for k in range(graph.MAX_TASKS + 1)]}, ordered=True)
        self.assertEqual(len(errors), 1)
        self.assertIn("cap", errors[0])


class GraphRulesTest(Flow):
    """`policy/graph-rules.py` adds its refusals to the engine's: policy can only refuse more."""
    SCRIPT = "policy/graph-rules.py"
    NO_FORBIDDEN = ("import json, sys\nfacts = json.load(open(sys.argv[1]))\n"
                    "print(json.dumps({'errors': ['task %s is forbidden' % t['id'] for t in facts['tasks'] if 'forbidden' in t['title']]}))\n")

    def set_script(self, body):
        self.w.push_main({self.SCRIPT: body}, "the Policymaker adds a graph rule")
        c.sync_main()

    def test_a_policy_rule_refuses_a_graph_the_engine_accepts(self):
        self.set_script(self.NO_FORBIDDEN)
        it.start_iteration()
        s = self.session_issue("orchestrator")
        self.w.session(s, session_files={"tasks.json": tasks_json(task("a", title="a forbidden task"))})
        self.assertIn("policy/graph-rules.py: task a is forbidden", self.hub.prose(s))
        self.assertIsNone(self.task_issue("a"))
        self.assertEqual(self.record(0)["status"], "failed")

    def test_a_graph_the_rule_does_not_mind_runs(self):
        self.set_script(self.NO_FORBIDDEN)
        it.start_iteration()
        self.play_shaper(task("a"))
        self.assertIsNotNone(self.task_issue("a"))

    def test_no_policy_rule_accepts_a_graph_the_engine_refuses(self):
        self.set_script("print('{\"errors\": []}')\n")
        it.start_iteration()
        s = self.session_issue("orchestrator")
        self.w.session(s, session_files={"tasks.json": tasks_json(*[task("t%s" % k) for k in range(graph.MAX_TASKS + 1)])})
        self.assertIn("the cap is %s" % graph.MAX_TASKS, self.hub.prose(s))
        self.assertIsNone(self.task_issue("t0"))

    def test_an_answer_the_engine_refuses_fails_the_iteration_and_rolls_policy_back(self):
        fakes.sh(self.w.loop, "git", "tag", "-f", "setup-baseline")
        self.set_script("print('{\"errors\": \"no\"}')\n")
        it.start_iteration()
        self.w.session(self.session_issue("orchestrator"), session_files={"tasks.json": tasks_json(task("a"))})
        self.assertEqual(self.record(0)["status"], "failed")
        c.sync_main()
        self.assertFalse(os.path.exists(self.SCRIPT))
        self.assertTrue(any(x["title"] == "policy/ rolled back" for x in self.hub.issues.values()))


if __name__ == "__main__":
    unittest.main()
