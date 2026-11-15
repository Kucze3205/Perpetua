"""Admission writes the same commit whatever it skips doing for unchanged files, and its git cost follows the changes."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import fakes  # noqa: F401  (puts the loop modules on the path)
import executor_boundary as boundary

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@localhost"]


def run(root, *args):
    return subprocess.run([*GIT, "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(root, rel, data, mode=0o644):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)


@unittest.skipIf(os.name == "nt", "executable bits are a POSIX mode")
class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="admission-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        run(self.tmp, "init", "-q", "repo")
        write(self.repo, "keep.txt", b"keep\n")
        write(self.repo, "run.sh", b"#!/bin/sh\n", 0o755)
        write(self.repo, "edit.txt", b"before\n")
        write(self.repo, "gone.txt", b"gone\n")
        write(self.repo, "dir/inner.txt", b"inner\n")
        write(self.repo, "was-file", b"file\n")
        write(self.repo, "was-dir/x", b"x\n")
        write(self.repo, "café name.txt", b"quoted\n")
        run(self.repo, "add", "-A")
        run(self.repo, "commit", "-q", "-m", "baseline")
        self.work = os.path.join(self.tmp, "work")
        run(self.tmp, "clone", "-q", "repo", "work")
        self.trusted = os.path.join(self.tmp, "trusted")
        os.mkdir(self.trusted)
        boundary.record(self.work, self.trusted)
        with open(os.path.join(self.trusted, "executor-baseline.json"), encoding="utf-8") as fh:
            self.meta = json.load(fh)

    def admit(self, skip=lambda rel, tracked: False):
        commit = boundary.import_candidate(self.work, self.meta, self.repo, "refs/heads/task/1", skip)
        return commit, run(self.repo, "rev-parse", commit + "^{tree}")

    def expected_tree(self):
        # The tree git itself builds from the files the session left.
        run(self.work, "add", "-A")
        return run(self.work, "write-tree")

    def test_the_commit_holds_what_the_session_left(self):
        write(self.work, "edit.txt", b"after\n")
        write(self.work, "run.sh", b"#!/bin/sh\n", 0o644)           # mode change only
        write(self.work, "keep.txt", b"keep\n", 0o755)               # mode change only
        write(self.work, "new/deep.txt", b"new\n")
        write(self.work, "new-exec", b"#!/bin/sh\n", 0o755)
        write(self.work, "café name.txt", b"changed\n")
        os.remove(os.path.join(self.work, "gone.txt"))
        os.remove(os.path.join(self.work, "was-file"))               # file becomes a directory
        write(self.work, "was-file/now", b"now\n")
        shutil.rmtree(os.path.join(self.work, "was-dir"))            # directory becomes a file
        write(self.work, "was-dir", b"flat\n")
        commit, tree = self.admit()
        self.assertEqual(tree, self.expected_tree())
        self.assertEqual(run(self.repo, "rev-parse", commit + "^"), self.meta["baseline"])

    def test_an_untouched_tree_gives_the_baseline_tree(self):
        _, tree = self.admit()
        self.assertEqual(tree, run(self.repo, "rev-parse", self.meta["baseline"] + "^{tree}"))

    def test_the_same_output_is_the_same_commit(self):
        write(self.work, "edit.txt", b"after\n")
        self.assertEqual(self.admit()[0], self.admit()[0])

    def test_skipped_paths_are_not_read_and_a_skipped_tracked_path_is_dropped(self):
        write(self.work, "debris/x", b"junk\n")
        write(self.work, "dir/inner.txt", b"edited\n")
        _, tree = self.admit(lambda rel, tracked: rel.startswith("debris") or rel == "dir/inner.txt")
        listing = run(self.repo, "ls-tree", "-r", "--name-only", tree).splitlines()
        self.assertNotIn("debris/x", listing)
        self.assertNotIn("dir/inner.txt", listing)

    def test_a_symlink_is_refused(self):
        os.symlink("keep.txt", os.path.join(self.work, "link"))
        with self.assertRaises(ValueError):
            self.admit()

    def test_git_runs_do_not_grow_with_unchanged_files(self):
        def runs():
            calls = []
            real = boundary.git
            with mock.patch.object(boundary, "git", lambda *a, **k: calls.append(a[1]) or real(*a, **k)):
                self.admit()
            return len(calls)
        write(self.work, "edit.txt", b"after\n")
        few = runs()
        for i in range(40):
            write(self.repo, "bulk/%d.txt" % i, b"%d\n" % i)
        run(self.repo, "add", "-A")
        run(self.repo, "commit", "-q", "-m", "more files")
        run(self.work, "pull", "-q")
        boundary.record(self.work, self.trusted)
        with open(os.path.join(self.trusted, "executor-baseline.json"), encoding="utf-8") as fh:
            self.meta = json.load(fh)
        self.assertEqual(runs(), few)
