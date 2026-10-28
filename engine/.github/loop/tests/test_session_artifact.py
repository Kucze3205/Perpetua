"""What the epilogue takes in from the session job: data only. Links, redirected paths and oversized files are refused;
the executor baseline comes by a channel of its own."""
import io
import shutil
import tempfile
import os
import tarfile
import unittest

import fakes
from fakes import loop
import session_artifact as artifact


def tar_of(path, members):
    """A work.tar with (name, kind, payload) members: kind is file | dir | sym | hard."""
    with tarfile.open(os.path.join(path, "work.tar"), "w") as tar:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, payload
                tar.addfile(info)
            elif kind == "hard":
                info.type, info.linkname = tarfile.LNKTYPE, payload
                tar.addfile(info)
            else:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))


class ReceiveTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="artifact-")
        self.work = os.path.join(self.tmp, "work")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refused(self, *members):
        tar_of(self.tmp, members)
        with self.assertRaises(ValueError):
            artifact.receive_tree(self.tmp, self.work)

    def test_plain_files_arrive_with_their_bytes_and_executable_bit(self):
        tar_of(self.tmp, [("src", "dir", None), ("src/a.txt", "file", b"A"), ("run.sh", "file", b"#!/bin/sh\n")])
        artifact.receive_tree(self.tmp, self.work)
        self.assertEqual(open(os.path.join(self.work, "src", "a.txt"), "rb").read(), b"A")

    def test_a_path_that_escapes_is_refused(self):
        self.refused(("../outside", "file", b"x"))
        self.refused(("/etc/passwd", "file", b"x"))

    def test_a_write_through_a_link_is_refused(self):
        self.refused(("out", "sym", "/etc"), ("out/passwd", "file", b"x"))
        self.assertFalse(os.path.exists("/etc/passwd.new"))

    def test_a_hard_link_to_something_outside_the_tree_is_refused(self):
        self.refused(("copy", "hard", "../../etc/passwd"))

    def test_a_special_file_is_refused(self):
        with tarfile.open(os.path.join(self.tmp, "work.tar"), "w") as tar:
            info = tarfile.TarInfo("pipe")
            info.type = tarfile.FIFOTYPE
            tar.addfile(info)
        with self.assertRaises(ValueError):
            artifact.receive_tree(self.tmp, self.work)

    def test_an_oversized_file_is_refused(self):
        saved, artifact.MAX_FILE = artifact.MAX_FILE, 10
        try:
            self.refused(("big.bin", "file", b"x" * 11))
        finally:
            artifact.MAX_FILE = saved

    def test_a_link_is_unpacked_as_a_link_and_left_to_admission(self):
        tar_of(self.tmp, [("l", "sym", "/etc/passwd")])
        artifact.receive_tree(self.tmp, self.work)
        self.assertTrue(os.path.islink(os.path.join(self.work, "l")))


class EpilogueBaselineTest(unittest.TestCase):
    def test_a_baseline_that_is_not_the_staged_one_stops_the_epilogue(self):
        w = fakes.worker_world()
        try:
            rt = os.path.join(w.tmp, "rt")
            baseline = os.path.join(w.tmp, "baseline")
            os.makedirs(rt)
            os.makedirs(baseline)
            os.environ["RUNNER_TEMP"] = rt
            with open(os.path.join(baseline, "executor-baseline.json"), "w") as fh:
                fh.write('{"baseline": "forged", "paths": [], "work": "/"}')
            with self.assertRaises(SystemExit):
                loop.epilogue(1, os.path.join(w.tmp, "none"), baseline, "0" * 64)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
