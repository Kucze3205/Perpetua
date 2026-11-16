"""What the owner sees in history: who wrote a commit and what its message says."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import fakes
from fakes import common as c
from fakes import iteration as it, task
import merge
from flow_fixtures import Flow
import git_identity


def log(repo, ref="HEAD", fmt="%s"):
    return subprocess.run(["git", "log", "--format=" + fmt, ref], cwd=repo, capture_output=True, text=True, check=True).stdout.splitlines()


class IdentityTest(unittest.TestCase):
    """The engine sets its own git identity: no command configures git, and no machine setting is needed."""

    def commit_in_a_bare_machine(self, author):
        with tempfile.TemporaryDirectory() as repo, mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}), \
                mock.patch.object(c, "cfg", return_value={"author": author}):
            for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
                os.environ.pop(var, None)
            c.git(repo, "init", "-q")
            c.git(repo, "commit", "-q", "--allow-empty", "-m", "stage #1: x")
            return log(repo, fmt="%an <%ae>|%cn <%ce>")[0]

    def test_the_engine_commits_as_the_actions_bot(self):
        bot = "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>"
        self.assertEqual(self.commit_in_a_bare_machine({"name": "", "email": ""}), bot + "|" + bot)

    def test_a_named_author_is_the_author_and_the_bot_still_commits(self):
        got = self.commit_in_a_bare_machine({"name": "Ada", "email": "ada@example.test"})
        self.assertEqual(got, "Ada <ada@example.test>|github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>")

    def test_only_calls_that_write_history_get_an_identity(self):
        author = lambda: {}
        self.assertEqual(git_identity.env(("fetch", "-q", "origin"), author), {})
        self.assertEqual(git_identity.env(("-c", "commit.gpgsign=false", "status"), author), {})
        self.assertIn("GIT_COMMITTER_NAME", git_identity.env(("commit", "-q", "-m", "x"), author))
        self.assertIn("GIT_COMMITTER_NAME", git_identity.env(("rebase", "origin/main"), author))


class AdmissionMessageTest(unittest.TestCase):
    def message(self, title):
        with mock.patch.object(c, "issue", return_value={"title": title}):
            return merge.admission_message("worker", 12)

    def test_it_reads_role_issue_and_task_title(self):
        self.assertEqual(self.message("Add the reader"), "worker #12: Add the reader")

    def test_the_title_is_one_short_line(self):
        self.assertEqual(self.message("a\nb\x00c\t d"), "worker #12: a b c d")
        self.assertEqual(self.message("x" * 200), "worker #12: " + "x" * 72)
        self.assertEqual(self.message(""), "worker #12: session output")


class HistoryTest(Flow):
    def one_iteration(self):
        it.start_iteration()
        shaper = self.play_shaper(task("a"))
        t = self.task_issue("a")
        self.w.session(t, files={"src/a.txt": "A"})
        return shaper, t

    def test_everything_the_loop_writes_reads_who_issue_what(self):
        shaper, t = self.one_iteration()
        self.assertEqual(self.w.origin_file("src/a.txt"), "A")
        subjects = log(self.w.origin, "main")
        self.assertIn("worker #%s: %s" % (t, self.hub.issues[t]["title"]), subjects)
        self.assertIn("orchestrator #%s: %s" % (shaper, self.hub.issues[shaper]["title"]), subjects)
        self.assertIn("iteration 0: task graph", subjects)
        self.assertIn("iteration 0: record (closed)", subjects)
        for old in ("Session output", "bundle", "iteration 0000", "session #", "goal reached (gate:"):
            self.assertEqual([s for s in subjects if s.startswith(old)], [], old)

    def test_the_gate_run_is_named_for_the_task(self):
        _, t = self.one_iteration()
        self.assertIn("change gate for #%s" % t, self.hub.checked + self.hub.tokenless_purposes)

    def test_the_session_report_is_one_commit_that_lists_its_files(self):
        _, t = self.one_iteration()
        subject = "epilogue #%s: session report" % t
        self.assertEqual(log(self.w.origin, "main").count(subject), 1)
        body = subprocess.run(["git", "log", "--grep", "^" + subject, "-1", "--format=%b", "main"], cwd=self.w.origin,
                              capture_output=True, text=True).stdout
        self.assertIn(".github/loop/data/session-reports/%s/" % t, body)

    def test_the_journal_of_an_unfinished_session_joins_its_report_commit(self):
        it.start_iteration()
        n = self.session_issue("orchestrator", n=0)
        self.w.session(n, status="blocked", files={"journal/0001.md": "state"}, report_extra="## What next\n- x\n")
        subject = "epilogue #%s: session report" % n
        self.assertEqual(self.w.origin_file("journal/0001.md"), "state")
        self.assertEqual(log(self.w.origin, "main").count(subject), 1)
        body = subprocess.run(["git", "log", "--grep", "^" + subject, "-1", "--format=%b", "main"], cwd=self.w.origin,
                              capture_output=True, text=True).stdout
        self.assertIn("- journal/0001.md", body)
        self.assertEqual([s for s in log(self.w.origin, "main") if s.startswith("Notes from")], [])
