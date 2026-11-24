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


if __name__ == "__main__":
    unittest.main()
