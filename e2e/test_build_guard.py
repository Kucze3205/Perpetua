"""`live.py build` refuses a checkout behind origin/main. Temporary git repos only: no network, no GitHub.
Run: python -m unittest e2e/test_build_guard.py"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment as live


class Reached(Exception):
    """Raised by the stubbed first step after the guard: `build` got past it."""


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


def unlock(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)


class BuildGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="perpetua-guard-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, **{("onexc" if sys.version_info >= (3, 12) else "onerror"): unlock}))
        self.origin, self.checkout, self.other = (self.tmp / n for n in ("origin.git", "checkout", "other"))
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(self.tmp, "clone", "-q", str(self.origin), str(self.checkout))
        git(self.checkout, "commit", "-q", "--allow-empty", "-m", "base")
        git(self.checkout, "push", "-q", "origin", "HEAD:main")
        git(self.tmp, "clone", "-q", str(self.origin), str(self.other))

    def advance_origin(self):
        git(self.other, "commit", "-q", "--allow-empty", "-m", "newer main")
        git(self.other, "push", "-q", "origin", "HEAD:main")

    def build(self):
        args = argparse.Namespace(iterations=1, no_push=False)
        with mock.patch.object(live, "PLUGIN", self.checkout), \
                mock.patch.object(live, "default_branch", side_effect=Reached), \
                mock.patch.object(live, "run", wraps=live.run) as run:
            try:
                live.build(args)
            finally:
                self.pushes = [c for c in run.call_args_list if "push" in c.args[0]]

    def test_behind_origin_main_is_refused_before_any_push(self):
        self.advance_origin()
        with self.assertRaises(SystemExit) as e:
            self.build()
        self.assertIn("1 commit(s) behind", str(e.exception))
        self.assertEqual(self.pushes, [])

    def test_equal_to_origin_main_passes(self):
        with self.assertRaises(Reached):
            self.build()

    def test_ahead_of_origin_main_passes(self):
        git(self.checkout, "commit", "-q", "--allow-empty", "-m", "fix on a branch")
        with self.assertRaises(Reached):
            self.build()


if __name__ == "__main__":
    unittest.main()
