"""Loop status: the pinned issue the engine overwrites; it only shows and never stops the loop."""
import unittest
from unittest import mock

from fakes import common as c, iteration as it, task
from flow_fixtures import Flow
import loop_status as status


class LoopStatusTest(Flow):
    def status_issue(self):
        found = self.hub.by_label(status.LABEL)
        self.assertLessEqual(len(found), 1)
        return found[0] if found else None

    def test_no_issue_before_the_first_iteration(self):
        self.assertIsNone(status.refresh())
        self.assertIsNone(self.status_issue())

    def test_a_running_iteration_shows_running_and_waiting_tasks(self):
        it.start_iteration()
        self.play_shaper(task("a", title="Write the parser"), task("end", title='Say "bye"', blocked_by=["a"]), k=0)
        n = status.refresh()
        x = self.status_issue()
        self.assertEqual(x["number"], n)
        self.assertEqual(self.hub.pinned, n)
        body = x["body"]
        self.assertIn("## 🔵 Iteration 0 · running · goal not reached", body)
        self.assertIn("- **Working on now:** Write the parser", body)
        self.assertIn("- **Waiting:** Say \"bye\"", body)
        self.assertIn('t_a("🔄 Write the parser<br/>worker · running"):::run', body)
        self.assertIn("t_end(\"⏳ Say 'bye'<br/>worker · waiting\"):::wait", body)
        self.assertIn("  t_a --> t_end", body)
        self.assertIn("| orchestrator #", body)
        self.assertIn("/actions/runs/", body)
        self.assertFalse(c.in_loop(c.label_names(x)))          # the loop never sees it

    def test_a_closed_iteration_shows_its_record_and_the_same_issue_is_overwritten(self):
        it.start_iteration()
        self.play_shaper(task("a"), k=0)
        first = status.refresh()
        self.w.session(self.task_issue("a"), files={"src/a.txt": "a"})
        self.assertIsNotNone(self.record(0))
        again = status.refresh()
        self.assertEqual(first, again)
        body = self.status_issue()["body"]
        self.assertIn("Iteration 1 ·", body)            # closing 0 started 1: the open one is shown
        it.fail_iteration(1, "a test")
        with mock.patch.object(it, "open_iteration", return_value=None):      # failing 1 opened 2: show the record
            status.refresh()
        body = self.status_issue()["body"]
        self.assertIn("## 🔴 Iteration 1 · failed", body)
        self.assertIn("**Failed:** a test", body)

    def test_a_role_shows_everything_the_model_read_as_its_input(self):
        tokens = {"in": 10, "out": 1363, "cache_read": 56507, "cache_write": 9329}      # worker #367, iteration 18
        row = status.role_row({"role": "worker", "issue": 367, "model": "claude-haiku-5-5", "effort": "low", "tokens": tokens})
        self.assertEqual(row, "| worker #367 | haiku-5-5 | low | 65.8k | 1.4k | 86% |")
        self.assertIn("| Role | Model | Effort | Input | Output | Cache hit |", status.roles([{"role": "worker", "issue": 1,
                      "tokens": dict(tokens, **{"in": 0, "out": 0, "cache_read": 0, "cache_write": 0})}]))

    def test_an_unchanged_loop_does_not_edit_the_issue(self):
        it.start_iteration()
        status.refresh()
        with mock.patch.object(c, "edit_issue_body") as edit:
            status.refresh()
        edit.assert_not_called()

    def test_a_failure_is_said_not_raised(self):
        it.start_iteration()
        with mock.patch.object(c, "create_issue", side_effect=SystemExit("gh issue create: no")):
            self.assertIsNone(status.refresh())
        self.assertIsNone(self.status_issue())

    def test_a_pin_the_token_may_not_do_is_left_to_the_owner(self):
        it.start_iteration()
        with mock.patch.object(c, "pin_issue", return_value=False), mock.patch.object(c, "say") as say:
            n = status.refresh()
        self.assertIsNotNone(n)
        self.assertIn("pin it once by hand", say.call_args[0][0])


class RenderTest(unittest.TestCase):
    def test_goal_is_one_line_without_headings_and_cut(self):
        self.assertEqual(status.flat_goal("# Goal\n\nMake  it\nwork."), "Make it work.")
        long = status.flat_goal("x " * 300)
        self.assertEqual(len(long), status.GOAL_CHARS)
        self.assertTrue(long.endswith("..."))

    def test_footer_time_alone_is_no_change(self):
        a = "body\n\n%s2026-01-01 10:00 UTC</sub>\n" % status.FOOTER
        b = "body\n\n%s2026-01-01 10:30 UTC</sub>\n" % status.FOOTER
        self.assertTrue(status.same(a, b))
        self.assertFalse(status.same(a, b.replace("body", "other")))


if __name__ == "__main__":
    unittest.main()
