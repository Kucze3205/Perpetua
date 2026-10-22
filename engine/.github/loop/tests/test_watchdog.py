"""The watchdog: arming, jams, crashes, stalls, rollback of policy/, no-progress, cadence."""
import json
import unittest
from datetime import timedelta

import fakes
from fakes import common as c, iteration as it, loop, task, tasks_json
from test_merge import GATE_SH, SCRIPT_GATE


class WatchCase(unittest.TestCase):
    YML = SCRIPT_GATE
    PARAMS = None

    def setUp(self):
        self.w = fakes.worker_world(self.YML)
        self.hub = self.w.hub
        files = {"policy/evaluator/gate.sh": GATE_SH}
        if self.PARAMS:
            files["policy/params.yml"] = self.PARAMS
        self.w.push_main(files, "setup")
        c.sync_main()
        fakes.sh(self.w.loop, "git", "tag", "setup-baseline", "HEAD")      # what the arming ticket does
        self.probe_saved = c.probe
        c.probe = lambda: 200
        self.now_saved = c.now

    def tearDown(self):
        c.probe = self.probe_saved
        c.now = self.now_saved
        self.w.close()

    def later(self, minutes):
        base = self.now_saved()
        c.now = lambda: base + timedelta(minutes=minutes)

    def shaper(self, k=0):
        found = [x for x in self.hub.role_issues("orchestrator") if c.iteration_of(c.label_names(x)) == k]
        return found[-1]["number"] if found else None

    def task_issue(self, tid):
        for x in self.hub.list_issues("all"):
            if c.marker(x["body"], "task-id") == tid:
                return x["number"]

    def run_iteration_zero(self, *files):
        it.start_iteration()
        self.w.session(self.shaper(0), session_files={"tasks.json": tasks_json(task("a"))}, files={"journal/0000.md": "s"})
        self.w.session(self.task_issue("a"), files=files[0] if files else {"src/a.txt": "A"})


class ArmingTest(WatchCase):
    def test_the_first_tick_after_arming_starts_the_first_iteration(self):
        self.assertEqual(loop.watch(), 0)
        self.assertEqual(it.iteration_number(it.open_iteration()), 0)
        self.assertEqual(self.hub.dispatched[-1][0], "session.yml")

    def test_a_dead_credential_is_an_outage_and_a_red_run(self):
        c.probe = lambda: 401
        self.assertEqual(loop.watch(), 1)
        (o,) = self.hub.by_label("outage")
        self.assertIn("dead Claude credential", o["title"])
        self.assertEqual(loop.watch(), 0)                  # an open outage is the only quiet state without runs

    def test_a_network_blip_is_not_a_dead_credential(self):
        c.probe = lambda: 0
        self.assertEqual(loop.watch(), 0)
        self.assertEqual(self.hub.by_label("outage"), [])

    def test_the_switch_off_stops_the_watchdog(self):
        self.hub.vars["AUTOPILOT"] = "off"
        self.assertEqual(loop.watch(), 0)
        self.assertEqual(set(self.hub.issues), {0})        # only the fixture's earlier Policymaker session


class JamTest(WatchCase):
    def test_a_ready_session_nobody_started_is_kicked_and_counted(self):
        it.start_iteration()
        s = self.shaper(0)
        self.hub.runs.clear()
        c.LAUNCHED.clear()
        before = len(self.hub.dispatched)
        self.assertEqual(loop.kick(), 0)
        self.assertEqual(len(self.hub.dispatched), before + 1)
        self.assertEqual(self.hub.report(s)["kicks"], "1")

    def test_three_fruitless_kicks_are_an_outage(self):
        it.start_iteration()
        s = self.shaper(0)
        for _ in range(4):
            self.hub.runs.clear()
            c.LAUNCHED.clear()
            rc = loop.kick()
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.hub.by_label("outage")), 1)

    def test_a_run_in_progress_is_a_commitment(self):
        it.start_iteration()
        c.LAUNCHED.clear()
        self.assertTrue(loop.commitments())            # the shaper's run is queued
        self.hub.runs.clear()
        self.assertFalse(loop.commitments())

    def test_three_crashes_without_a_commit_are_an_outage_but_one_commit_is_not(self):
        for k in range(3):
            n = self.hub.create_issue("T%s" % k, "b", labels=["role:worker", "loop:iteration 1"])
            c.update_report(n, {"status": "crashed"})
            self.hub.close_issue(n, "not planned")
        self.assertEqual(loop.drive(), 1)
        self.assertEqual(len(self.hub.by_label("outage")), 1)

    def test_a_crash_with_a_commit_does_not_count(self):
        for k in range(3):
            n = self.hub.create_issue("T%s" % k, "b", labels=["role:worker", "loop:iteration 1"])
            c.update_report(n, {"status": "crashed", "commit": "abc" if k == 1 else None})
            self.hub.close_issue(n, "not planned")
        self.assertFalse(loop.crash_streak())

    def test_closing_an_outage_resets_the_count(self):
        for k in range(3):
            n = self.hub.create_issue("T%s" % k, "b", labels=["role:worker", "loop:iteration 1"])
            c.update_report(n, {"status": "crashed"})
            self.hub.close_issue(n, "not planned")
        loop.drive()
        (o,) = self.hub.by_label("outage")
        self.hub.close_issue(o["number"])
        self.assertFalse(loop.crash_streak())


class StallTest(WatchCase):
    YML = SCRIPT_GATE + "params:\n  quiet_minutes: 30\n"

    def test_an_iteration_that_does_not_close_fails_and_policy_goes_back(self):
        self.run_iteration_zero()                                  # iteration 0 closed; its record marks the good state
        self.w.push_main({"policy/params.yml": "criterion: broken\n"}, "what a Policymaker session merged")
        c.sync_main()
        self.later(45)
        self.hub.runs.clear()
        c.LAUNCHED.clear()
        loop.drive()
        self.assertNotIn("broken", self.w.origin_file("policy/params.yml"))          # rolled back to the good state
        self.assertEqual(json.loads(self.w.origin_file(it.record_path(1)))["status"], "failed")
        self.assertIn("stalled; policy/ rolled back", json.loads(self.w.origin_file(it.record_path(1)))["failure"])
        self.assertEqual(it.iteration_number(it.open_iteration()), 2)                 # started over
        (note,) = self.hub.by_label("owner-request:notice")
        self.assertIn("rolled back", note["title"])

    def test_not_stalled_before_the_quiet_time(self):
        it.start_iteration()
        self.later(10)
        self.assertFalse(it.stalled())

    def test_waiting_for_the_owner_does_not_count_unless_the_setup_says_so(self):
        it.start_iteration()
        n = self.hub.create_issue("T", "b", labels=["role:worker", "loop:iteration 0", "awaiting:owner"])
        self.later(45)
        self.assertFalse(it.stalled())
        self.hub.close_issue(n)
        self.assertTrue(it.stalled())

    def test_the_setup_can_count_the_wait(self):
        w2 = fakes.worker_world(SCRIPT_GATE + "params:\n  quiet_minutes: 30\n  owner_wait_counts: true\n")
        saved = c.now
        try:
            it.start_iteration()
            w2.hub.create_issue("T", "b", labels=["role:worker", "loop:iteration 0", "awaiting:owner"])
            base = saved()
            c.now = lambda: base + timedelta(minutes=45)
            self.assertTrue(it.stalled())
        finally:
            c.now = saved
            w2.close()

    def test_nothing_to_roll_back_still_starts_over(self):
        it.start_iteration()
        self.later(45)
        self.hub.runs.clear()
        loop.drive()
        self.assertEqual(json.loads(self.w.origin_file(it.record_path(0)))["status"], "failed")
        self.assertEqual(it.iteration_number(it.open_iteration()), 1)


class CeilingTest(WatchCase):
    PARAMS = "ceilings:\n  iteration_minutes: 5\n"

    def test_the_policymakers_iteration_ceiling_ends_a_long_iteration_without_a_rollback(self):
        it.start_iteration()
        self.later(10)
        self.hub.runs.clear()
        loop.drive()
        rec = json.loads(self.w.origin_file(it.record_path(0)))
        self.assertEqual(rec["status"], "failed")
        self.assertIn("ceiling", rec["failure"])
        self.assertEqual(self.hub.by_label("owner-request:notice"), [])

    def test_running_sessions_of_a_failed_iteration_are_cancelled(self):
        it.start_iteration()
        self.hub.runs[0]["status"] = "in_progress"
        self.later(10)
        loop.drive()
        self.assertEqual(self.hub.cancelled, [self.hub.runs[0]["databaseId"]])


class NoProgressTest(WatchCase):
    YML = SCRIPT_GATE + "params:\n  no_progress_iterations: 1\n"

    def test_an_iteration_without_improvement_opens_one_notice(self):
        self.run_iteration_zero({"src/a.txt": "BAD"})
        (note,) = self.hub.by_label("owner-request:notice")
        self.assertIn("No progress", note["title"])
        self.assertEqual(it.no_progress(), 1)
        it.note_no_progress(0)
        self.assertEqual(len(self.hub.by_label("owner-request:notice")), 1)

    def test_an_improving_iteration_opens_nothing(self):
        self.run_iteration_zero()
        self.assertEqual(self.hub.by_label("owner-request:notice"), [])
        self.assertEqual(it.no_progress(), 0)


class CadenceTest(WatchCase):
    YML = SCRIPT_GATE + "cadence:\n  min_interval_minutes: 60\n"

    def test_the_next_iteration_waits_for_the_interval(self):
        self.run_iteration_zero()
        self.assertIsNone(it.open_iteration())
        self.later(30)
        self.assertIsNone(it.start_iteration())
        self.later(70)
        self.assertEqual(it.start_iteration(), 1)


class DailyCapTest(WatchCase):
    YML = SCRIPT_GATE + "cadence:\n  daily_iterations: 1\n"

    def test_a_daily_cap(self):
        self.run_iteration_zero()
        self.assertIsNone(it.open_iteration())


class StreamRetentionTest(WatchCase):
    YML = SCRIPT_GATE + "params:\n  stream_retention_iterations: 2\n"

    def test_old_streams_are_deleted_and_recent_ones_stay(self):
        self.hub.artifacts = [{"id": 1, "name": "stream-0000-5"}, {"id": 2, "name": "stream-0001-6"},
                              {"id": 3, "name": "stream-0002-7"}, {"id": 4, "name": "stream-0003-8"}, {"id": 5, "name": "other"}]
        it.prune_streams(3)
        self.assertEqual(sorted(self.hub.deleted), [1, 2])


if __name__ == "__main__":
    unittest.main()
