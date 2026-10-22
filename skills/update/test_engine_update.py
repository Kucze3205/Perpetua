"""engine_update.py against real git repositories: python3 -m unittest discover -s skills/update"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine_update.py")


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def write(root, path, text):
    path = os.path.join(root, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read(root, path):
    with open(os.path.join(root, path), encoding="utf-8") as fh:
        return fh.read()


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.engine = os.path.join(self.tmp, "engine")
        self.loop = os.path.join(self.tmp, "loop")
        write(self.engine, "ENGINE_VERSION", "1.0.0\n")
        write(self.engine, ".github/loop/loop.py", "a = 1\nb = 2\nc = 3\n")
        write(self.engine, "policy/params.yml", "shaper: null\n")
        write(self.engine, ".github/loop/old.py", "gone soon\n")
        shutil.copytree(self.engine, self.loop)
        git(self.loop, "init", "-q")
        git(self.loop, "add", "-A")
        git(self.loop, "commit", "-qm", "Perpetua engine 1.0.0")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, *args], cwd=self.loop, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def record_base(self):
        git(self.loop, "branch", "perpetua/engine", self.run_script("snapshot", self.engine))

    def release(self):
        write(self.engine, "ENGINE_VERSION", "1.1.0\n")
        write(self.engine, ".github/loop/loop.py", "a = 1\nb = 2\nc = 30\n")
        write(self.engine, ".github/loop/new.py", "new\n")
        os.remove(os.path.join(self.engine, ".github/loop/old.py"))

    def test_the_snapshot_equals_the_setup_copy(self):
        snapshot = self.run_script("snapshot", self.engine)
        self.assertEqual(git(self.loop, "rev-parse", snapshot + "^{tree}"), git(self.loop, "rev-parse", "HEAD^{tree}"))

    def test_local_changes_survive_and_upstream_changes_arrive(self):
        self.record_base()
        write(self.loop, ".github/loop/loop.py", "a = 10\nb = 2\nc = 3\n")
        write(self.loop, "policy/params.yml", "shaper: orchestrator\n")
        git(self.loop, "commit", "-qam", "local")
        self.release()
        report = json.loads(self.run_script("merge", self.engine))
        self.assertEqual((report["status"], report["from"], report["to"]), ("merged", "1.0.0", "1.1.0"))
        self.assertEqual(read(self.loop, ".github/loop/loop.py"), "a = 10\nb = 2\nc = 30\n")
        self.assertEqual(read(self.loop, "policy/params.yml"), "shaper: orchestrator\n")
        self.assertTrue(os.path.exists(os.path.join(self.loop, ".github/loop/new.py")))
        self.assertFalse(os.path.exists(os.path.join(self.loop, ".github/loop/old.py")))
        self.assertEqual(report["definitions"], [])

    def test_a_collision_is_reported_with_markers(self):
        self.record_base()
        write(self.loop, ".github/loop/loop.py", "a = 1\nb = 2\nc = 99\n")
        git(self.loop, "commit", "-qam", "local")
        self.release()
        report = json.loads(self.run_script("merge", self.engine))
        self.assertEqual(report["status"], "conflicts")
        self.assertEqual(report["conflicts"], [".github/loop/loop.py"])
        self.assertIn("<<<<<<<", read(self.loop, ".github/loop/loop.py"))

    def test_nothing_new_is_up_to_date(self):
        self.record_base()
        write(self.loop, "policy/params.yml", "shaper: orchestrator\n")
        git(self.loop, "commit", "-qam", "local")
        self.assertEqual(json.loads(self.run_script("merge", self.engine))["status"], "up-to-date")

    def test_a_loop_without_the_branch_finds_its_base_in_the_source(self):
        source = os.path.join(self.tmp, "perpetua")
        os.makedirs(source)
        git(source, "init", "-q", "-b", "main")
        shutil.copytree(self.engine, os.path.join(source, "engine"))
        git(source, "add", "-A")
        git(source, "commit", "-qm", "1.0.0")
        write(self.loop, ".github/loop/loop.py", "a = 10\nb = 2\nc = 3\n")
        git(self.loop, "commit", "-qam", "local")
        self.release()
        report = json.loads(self.run_script("merge", self.engine, "--source", source))
        self.assertEqual(report["bootstrapped"]["files_differing_from_setup_copy"], 0)
        self.assertEqual(read(self.loop, ".github/loop/loop.py"), "a = 10\nb = 2\nc = 30\n")


if __name__ == "__main__":
    unittest.main()
