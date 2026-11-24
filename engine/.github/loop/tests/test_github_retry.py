"""A passing GitHub failure (a 5xx, a lost connection) is asked again; any other answer is final."""
import subprocess
import unittest
from unittest import mock

import fakes  # noqa: F401  (the path to the engine, and the Windows stop)
import common as c
import github_retry


class Gh:
    """`gh` that answers each call with the next of `answers`: (exit code, stderr)."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, cmd, input=None, **kw):
        self.calls.append((cmd, input))
        code, err = self.answers.pop(0) if self.answers else (0, "")
        return subprocess.CompletedProcess(cmd, code, "{}" if code == 0 else "", err)


class ReadRetryTest(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        p = mock.patch.object(github_retry.time, "sleep", self.sleeps.append)
        p.start()
        self.addCleanup(p.stop)

    def gh(self, *answers):
        fake = Gh(*answers)
        p = mock.patch.object(github_retry.subprocess, "run", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def test_one_http_500_is_asked_again_and_the_read_succeeds(self):
        fake = self.gh((1, "gh: Server Error (HTTP 500)"))
        self.assertEqual(c.gh("api", "repos/o/r/issues/1"), "{}")
        self.assertEqual((len(fake.calls), self.sleeps), (2, [2]))

    def test_a_lost_connection_is_asked_again(self):
        fake = self.gh((1, "Post https://api.github.com/graphql: read: connection reset by peer"))
        self.assertEqual(c.gh("api", "x"), "{}")
        self.assertEqual(len(fake.calls), 2)

    def test_a_404_is_an_answer_and_raises_at_once(self):
        fake = self.gh((1, "gh: Not Found (HTTP 404)"))
        with self.assertRaises(SystemExit):
            c.gh("api", "repos/o/r/issues/9")
        self.assertEqual((len(fake.calls), self.sleeps), (1, []))

    def test_three_500s_in_a_row_raise_after_two_pauses(self):
        fake = self.gh(*[(1, "HTTP 502: Bad Gateway")] * 3)
        with self.assertRaises(SystemExit):
            c.gh("api", "x")
        self.assertEqual((len(fake.calls), self.sleeps), (3, [2, 8]))

    def test_unchecked_callers_get_the_last_answer(self):
        self.gh(*[(1, "HTTP 503")] * 3)
        self.assertEqual(c.gh("run", "cancel", "5", check=False), "")

    def test_the_input_goes_with_every_attempt(self):
        fake = self.gh((1, "HTTP 500"))
        c.api("repos/o/r/issues/1/comments", "body=@-", method="POST", inp="the body")
        self.assertEqual([inp for _, inp in fake.calls], ["the body", "the body"])


class WriteRetryTest(ReadRetryTest):
    """A write the engine must not leave undone is asked again on any failure."""

    def setUp(self):
        super().setUp()
        saved = c.autopilot_on, c.issue, c.ensure_label
        c.autopilot_on = lambda: True
        c.issue = lambda n: {"labels": [{"name": "conflict"}]}
        c.ensure_label = lambda *a, **k: None

        def restore():
            c.autopilot_on, c.issue, c.ensure_label = saved
        self.addCleanup(restore)

    def test_a_dispatch_survives_a_passing_failure(self):
        fake = self.gh((1, "could not create workflow dispatch event: HTTP 500"))
        self.assertTrue(c.workflow_run("session.yml", issue=3))
        self.assertEqual((len(fake.calls), self.sleeps), (2, [2]))

    def test_a_dispatch_refused_three_times_is_false(self):
        self.gh(*[(1, "HTTP 422: workflow not found")] * 3)
        self.assertFalse(c.workflow_run("session.yml", issue=3))
        self.assertEqual(self.sleeps, [2, 10])

    def test_a_close_that_keeps_failing_raises_and_one_that_passes_does_not(self):
        self.gh((1, "HTTP 502"))
        c.close_issue(4)
        self.gh(*[(1, "HTTP 502")] * 3)
        with self.assertRaises(SystemExit):
            c.close_issue(4)

    def test_only_labels_the_issue_has_are_removed(self):
        fake = self.gh()
        c.edit_labels(4, add=["blocked:rate-limit"], remove=["conflict", "awaiting:orders"])
        (cmd, _), = fake.calls
        self.assertEqual(cmd, ["gh", "issue", "edit", "4", "--add-label", "blocked:rate-limit", "--remove-label", "conflict"])

    def test_nothing_to_remove_is_no_call(self):
        fake = self.gh()
        c.edit_labels(4, remove=["awaiting:orders"])
        self.assertEqual(fake.calls, [])

    def test_a_label_edit_that_keeps_failing_is_only_logged(self):
        self.gh(*[(1, "HTTP 500")] * 3)
        c.edit_labels(4, add=["x"])


class LaunchTest(unittest.TestCase):
    def test_a_refused_dispatch_is_no_launch(self):
        w = fakes.worker_world()
        self.addCleanup(w.close)
        n = w.hub.create_issue("T", "b", labels=["role:worker", "loop:iteration 0"])
        w.hub.dispatch_refused = True
        c.LAUNCHED.clear()
        self.assertFalse(c.launch(n))
        self.assertNotIn(n, c.LAUNCHED)


if __name__ == "__main__":
    unittest.main()
