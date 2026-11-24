"""The start of an iteration as jobs of the run that found it due: "Take in results" (or "Check the loop") bundles the
decision on the engine's facts, "Decide whether the Policymaker runs" runs it without the token, and "Start the iteration"
uses its result only from the job's output and only for the same facts, falling back to a Tokenless run otherwise."""
import os
import unittest
from datetime import timedelta
from unittest import mock

import fakes
from fakes import common as c, iteration as it, task
import iteration_start as start
import loop_status
from flow_fixtures import Flow

TRIGGER = "policy script policymaker-trigger.py"
SCRIPT = "policy/policymaker-trigger.py"


class StartCase(Flow):
    def set_script(self, body):
        self.w.push_main({SCRIPT: body}, "the Policymaker changes the trigger rule")
        c.sync_main()

    def set_gate(self, reached):
        self.w.push_main({"goal/gate.yml": "command: \"echo '{\\\"reached\\\": %s}'\"\nowner_confirms: false\n" % ("true" if reached else "false")}, "gate")
        c.sync_main()

    def iteration(self, k):
        return it.find_iteration_issue(k, state="all")

    def report(self, k):
        return c.report_fields(self.iteration(k)["number"])

    def first(self):
        """Iteration 0 starts at once (as a test calls it), its shaper and one task run; the task's end closes it."""
        start.start_iteration()
        self.hub.tokenless_purposes.clear()           # that start happened at once
        self.finish(0)


class DecisionInTheJobTest(StartCase):
    def test_the_trigger_skips_the_policymaker_in_the_job_and_loop_status_says_why(self):
        self.first()
        self.assertEqual(self.hub.decided, [TRIGGER])
        self.assertNotIn(TRIGGER, self.hub.tokenless_purposes)
        self.assertEqual(self.hub.role_issues("policymaker"), [])                 # no session, no issue
        self.assertEqual(self.report(1).get("policy_not_needed"), "no trigger is due")
        self.assertNotIn("policy_trigger", self.report(1))
        self.assertEqual(self.report(1)["phase"], "shape")
        loop_status.refresh()
        (x,) = self.hub.by_label(loop_status.LABEL)
        self.assertIn("Policymaker: not needed — no trigger is due", x["body"])

    def test_a_skipped_policymaker_leaves_the_record_and_the_failed_streak_as_they_were(self):
        self.first()
        self.finish(1)
        record = self.record(1)
        self.assertEqual(record["status"], "closed")
        self.assertEqual({k: record["policy"][k] for k in ("session", "trigger", "passes", "orders_refused")},
                         {"session": None, "trigger": None, "passes": 0, "orders_refused": None})
        self.assertNotIn("not_needed", record["policy"])
        self.assertEqual(it.failed_streak(), 0)

    def test_a_due_trigger_opens_the_policymaker_from_the_job(self):
        self.set_script("import json\nprint(json.dumps({'reason': 'my own rule'}))\n")
        start.start_iteration()                       # iteration 0 at once: its Policymaker
        self.hub.tokenless_purposes.clear()
        self.w.session(self.session_issue("policymaker"))
        self.finish(0)
        pol = self.session_issue("policymaker")
        self.assertEqual(it.read_state(pol)["trigger"], "my own rule")
        self.assertEqual(self.report(1)["policy_trigger"], "my own rule")
        self.assertNotIn("policy_not_needed", self.report(1))
        self.assertIn(TRIGGER, self.hub.decided)
        self.assertNotIn(TRIGGER, self.hub.tokenless_purposes)

    def test_bad_trigger_output_in_the_job_fails_the_iteration_and_rolls_policy_back(self):
        good = self.w.origin_file(SCRIPT)
        start.start_iteration()
        self.play_shaper(task("t0"), k=0)
        closed = start.after_close

        def break_the_rule(n, issue):                     # after iteration 0 closed on the good rule, before its start is planned
            if n == 0:
                self.set_script("print('hello')\n")
            closed(n, issue)
        with mock.patch.object(start, "after_close", break_the_rule):
            self.w.session(self.task_issue("t0"), files={"src/t0.txt": "t0"})
        self.assertEqual(self.record(0)["status"], "closed")
        self.assertEqual(self.hub.decided, [TRIGGER])
        self.assertEqual((self.record(1)["status"], self.record(1)["failure"]),
                         ("failed", "invalid policy script output: policy/policymaker-trigger.py did not print JSON"))
        self.assertEqual(self.w.origin_file(SCRIPT), good)                              # rolled back
        self.assertTrue(any(x["title"] == "policy/ rolled back" for x in self.hub.issues.values()))
        pol = self.session_issue("policymaker")                                           # the next one opens with it
        self.assertEqual(it.read_state(pol)["trigger"], "iteration 0001 failed: invalid policy script output: "
                         "policy/policymaker-trigger.py did not print JSON")
        self.assertEqual(it.failed_streak(), 1)

    def test_the_goal_gate_runs_in_the_job_and_ends_the_loop(self):
        self.set_gate(True)
        self.first()
        self.assertEqual(self.hub.decided, [TRIGGER, "goal gate"])
        self.assertNotIn("goal gate", self.hub.tokenless_purposes)
        self.assertIn("reached at", self.w.origin_file("GOAL_REACHED"))
        self.assertIsNone(it.open_iteration())                                       # nothing starts after it
        self.assertNotIn("goal_check", self.report(0))

    def test_a_goal_not_reached_lets_the_next_iteration_start(self):
        self.set_gate(False)
        self.first()
        self.assertEqual(self.hub.decided, [TRIGGER, "goal gate"])
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.assertEqual(it.iteration_number(it.open_iteration()), 1)
        self.assertNotIn("goal_check", self.report(0))

    def test_without_the_jobs_the_start_happens_at_once_in_tokenless_runs(self):
        self.w.start_jobs = None
        self.set_gate(False)
        self.first()
        self.assertEqual(self.hub.decided, [])
        self.assertIn(TRIGGER, self.hub.tokenless_purposes)
        self.assertIn("goal gate", self.hub.tokenless_purposes)
        self.assertEqual(self.report(1)["policy_not_needed"], "no trigger is due")

    def test_when_main_moves_before_the_start_the_trigger_runs_again_in_a_tokenless_run(self):
        start.start_iteration()
        self.play_shaper(task("t0"), k=0)
        moved = self.w.start
        self.w.start = lambda *a: (self.w.push_main({"policy/notes.md": "moved\n"}, "main moved"), moved(*a))
        self.w.session(self.task_issue("t0"), files={"src/t0.txt": "t0"})
        self.assertEqual(self.hub.decided, [TRIGGER])
        self.assertIn(TRIGGER, self.hub.tokenless_purposes)         # policy/ changed: not the bundle the job ran
        self.assertEqual(self.report(1)["policy_not_needed"], "no trigger is due")


class ListLagTest(StartCase):
    """GitHub's issue list shows a just-closed iteration as open for a while."""
    def open_maps(self):
        return [n for n, x in self.hub.issues.items() if it.MAP in x["_labels"] and x["state"] == "open"]

    def test_a_list_that_still_shows_the_closed_iteration_does_not_stop_the_loop(self):
        self.set_gate(False)
        start.start_iteration()
        self.hub.lagging_close = it.MAP
        self.finish(0)
        self.assertEqual(self.hub.decided, [TRIGGER, "goal gate"])                # planned by `prepare`, not left to the watchdog
        self.assertNotIn("goal_check", self.report(0))
        cur = it.open_iteration()
        self.assertEqual(it.iteration_number(cur), 1)
        self.assertEqual(self.open_maps(), [cur["number"]])                           # not closed as a duplicate of iteration 0

    def test_without_the_jobs_the_start_at_once_is_not_stopped_either(self):
        self.w.start_jobs = None
        start.start_iteration()
        self.hub.lagging_close = it.MAP
        self.finish(0)
        cur = it.open_iteration()
        self.assertEqual(it.iteration_number(cur), 1)
        self.assertEqual(self.open_maps(), [cur["number"]])


class OnlyTheJobOutputsCountTest(StartCase):
    def test_a_result_planted_in_the_artifact_is_refused(self):
        self.w.start_jobs = "lost"                     # the session's run ends with no start; the watchdog's run is played by hand
        self.first()
        self.assertIsNone(it.open_iteration())
        self.set_script("import json\nprint(json.dumps({'reason': 'the real rule'}))\n")
        rt = os.path.join(self.w.tmp, "watch-by-hand")
        os.makedirs(rt)
        facts, outputs = os.path.join(rt, "start-facts"), os.path.join(rt, "outputs")
        with mock.patch.dict(os.environ, {"START_PLAN": facts, "GITHUB_OUTPUT": outputs, "RUNNER_TEMP": rt}):
            start.prepare()
        planned = fakes.outputs_of(outputs)
        self.assertEqual((planned["start"], planned["count"]), ("true", "1"))
        # a job that ran foreign code uploads its own result next to the facts: the decision job's download no longer matches
        with open(os.path.join(facts, "results.json"), "w", encoding="utf-8") as fh:
            fh.write('{"reason": "planted"}\n')
        with self.assertRaises(ValueError):
            fakes.check_job(facts, planned["digest"], os.path.join(rt, "decide"))
        with mock.patch.dict(os.environ, {"DECIDE_RESULTS": ""}):           # the job failed: no output
            start.act()
        pol = self.session_issue("policymaker")
        self.assertEqual(it.read_state(pol)["trigger"], "the real rule")
        self.assertIn(TRIGGER, self.hub.tokenless_purposes)

    def test_an_output_that_does_not_validate_is_no_result(self):
        self.w.start_jobs = "lost"
        self.first()
        planted = '{"%s": {"exit": "0", "stdout": "{\\"reason\\": \\"planted\\"}", "tail": "", "more": 1}}' % ("0" * 64)
        with mock.patch.dict(os.environ, {"DECIDE_RESULTS": planted}):
            start.act()
        self.assertEqual(self.hub.role_issues("policymaker"), [])
        self.assertIn(TRIGGER, self.hub.tokenless_purposes)


class LostStartTest(StartCase):
    def test_a_start_whose_jobs_never_ran_is_started_by_the_watchdog(self):
        self.w.start_jobs = "lost"
        self.set_gate(False)
        self.first()
        self.assertIsNone(it.open_iteration())
        self.assertEqual(self.report(0)["goal_check"], "due")
        self.w.start_jobs = "run"
        self.hub.finish_runs()
        self.w.watch()
        self.assertEqual(it.iteration_number(it.open_iteration()), 1)
        self.assertEqual(self.report(1)["phase"], "shape")
        self.assertNotIn("goal_check", self.report(0))
        self.assertEqual(self.hub.decided.count("goal gate"), 2)        # planned in both runs, the second one was used

    def test_a_goal_check_whose_start_was_lost_still_ends_the_loop(self):
        self.w.start_jobs = "lost"
        self.set_gate(True)
        self.first()
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.w.start_jobs = "run"
        self.hub.finish_runs()
        self.w.watch()
        self.assertIn("reached at", self.w.origin_file("GOAL_REACHED"))
        self.assertIsNone(it.open_iteration())

    def test_a_start_that_died_before_it_decided_is_decided_again_once_it_is_old(self):
        start.start_iteration()
        self.play_shaper(task("t0"), k=0)
        with mock.patch.object(start, "decide", side_effect=SystemExit("the runner died")):
            with self.assertRaises(SystemExit):
                self.w.session(self.task_issue("t0"), files={"src/t0.txt": "t0"})
        cur = it.open_iteration()
        self.assertEqual(c.report_fields(cur["number"])["phase"], "starting")
        self.hub.finish_runs()
        self.w.watch()                                          # it may still be deciding: nobody else does
        self.assertEqual(c.report_fields(cur["number"])["phase"], "starting")
        self.assertEqual(self.w.started[0]["start"], "false")
        old = c.iso(c.now() - timedelta(minutes=start.START_MINUTES + 1))
        c.update_report(cur["number"], {"started": old})
        self.w.watch()
        self.assertEqual(c.report_fields(cur["number"])["phase"], "shape")
        self.assertEqual(c.report_fields(cur["number"])["policy_not_needed"], "no trigger is due")
        self.assertEqual(len(self.hub.by_label(it.MAP, "all")), 2)                # no second iteration 1
        self.assertEqual(len(self.hub.role_issues("orchestrator", "all")), 2)     # one shaper each


if __name__ == "__main__":
    unittest.main()
