"""Policy-layer sessions publish the files they leave, committed or not; the epilogue is the only seam."""
import json
import os
import sys
import unittest

import fakes
from fakes import common as c, iteration as it, loop
from flow_fixtures import Flow
import session_fixtures as sessions

MEMORY = {"policy/working-memory.md": "# Working memory\nthe interpretation\n"}


class PolicymakerAdmissionTest(Flow):
    FIRST = True

    def pol(self):
        it.start_iteration()
        return self.session_issue("policymaker")

    def receipt(self, pol):
        text = self.w.origin_file("iterations/changes/change-%s.json" % pol)
        return json.loads(text) if text else None

    def outcome(self, commit):
        pol = self.pol()
        self.w.session(pol, files=MEMORY, commit=commit)
        receipt = self.receipt(pol)
        return (self.w.origin_file("policy/working-memory.md"), receipt["changed_paths"],
                receipt["policy_rev_after"])

    def test_working_memory_edited_without_a_commit_is_published_with_a_receipt(self):
        content, paths, _ = self.outcome(commit=False)
        self.assertEqual((content, paths), (MEMORY["policy/working-memory.md"], ["policy/working-memory.md"]))

    def test_the_same_change_with_a_commit_gives_the_same_result(self):
        content, paths, _ = self.outcome(commit=True)
        self.assertEqual((content, paths), (MEMORY["policy/working-memory.md"], ["policy/working-memory.md"]))

    def test_the_receipt_revision_is_the_same_with_and_without_a_commit(self):
        revisions = []
        for commit in (True, False):
            if revisions:
                self.tearDown()
                self.setUp()                 # a fresh world: the same base for both
            revisions.append(self.outcome(commit)[2])
        self.assertEqual(revisions[0], revisions[1])

    def test_environment_debris_beside_a_valid_change_is_dropped_and_the_session_is_not_blocked(self):
        pol = self.pol()
        debris = {".bashrc": "export X=1", ".idea/workspace.xml": "<x/>", "scripts/tool.sh": "echo",
                  ".npmrc": "", "package.json": ""}
        self.w.session(pol, files=dict(MEMORY, **debris), commit=False)
        self.assertEqual(self.hub.report(pol)["status"], "done")
        self.assertEqual(self.w.origin_file("policy/working-memory.md"), MEMORY["policy/working-memory.md"])
        self.assertEqual(self.receipt(pol)["changed_paths"], ["policy/working-memory.md"])
        for path in debris:
            self.assertIsNone(self.w.origin_file(path), path)

    def test_a_tracked_file_modified_outside_the_write_paths_is_refused_as_before(self):
        self.w.push_main({"src/kept.txt": "product"})
        c.sync_main()
        pol = self.pol()
        self.w.session(pol, files=dict(MEMORY, **{"src/kept.txt": "changed"}), commit=False)
        self.assertEqual(self.hub.report(pol)["status"], "blocked")
        self.assertEqual(self.w.origin_file("src/kept.txt"), "product")
        self.assertIsNone(self.w.origin_file("policy/working-memory.md"))

    def test_a_protected_path_changed_without_a_commit_is_refused_as_before(self):
        pol = self.pol()
        self.w.session(pol, files={"engine.yml": "# hacked" + chr(10)}, commit=False)
        self.assertEqual(self.hub.report(pol)["status"], "blocked")
        self.assertNotIn("hacked", self.w.origin_file("engine.yml"))

    def test_a_deleted_tracked_file_is_part_of_the_change(self):
        self.w.push_main({"policy/old.md": "old"})
        c.sync_main()
        pol = self.pol()
        self.w.session(pol, delete=["policy/old.md"], commit=False)
        self.assertIsNone(self.w.origin_file("policy/old.md"))

    @unittest.skipIf(sys.platform == "win32", "symlinks")
    def test_a_symlink_in_the_tree_refuses_the_admission(self):
        pol = self.pol()
        self.w.session(pol, files=MEMORY, commit=False, links={"policy/link.md": "/etc/passwd"})
        self.assertEqual(self.hub.report(pol)["status"], "crashed")
        self.assertIsNone(self.w.origin_file("policy/working-memory.md"))
        self.assertFalse(self.w.origin_has_ref("task/%s" % pol))

    @unittest.skipIf(sys.platform == "win32", "symlinks")
    def test_an_untracked_symlink_outside_the_write_paths_is_debris_not_a_refusal(self):
        pol = self.pol()
        self.w.session(pol, files=MEMORY, commit=False, links={".venv/bin/python": "/usr/bin/python3"})
        self.assertEqual(self.hub.report(pol)["status"], "done")
        self.assertEqual(self.w.origin_file("policy/working-memory.md"), MEMORY["policy/working-memory.md"])
        self.assertIsNone(self.w.origin_file(".venv/bin/python"))


class AuditAdmissionTest(sessions.SessionCase):
    def test_a_report_written_in_audit_without_a_commit_is_admitted(self):
        n = self.issue("audit")
        self.w.session(n, files={"audit/report.md": "numbers\n"}, commit=False)
        self.assertEqual(self.w.origin_file("audit/report.md"), "numbers\n")
        self.assertEqual(self.hub.report(n)["merged"], "true")

    def test_a_session_without_changes_has_nothing_to_merge_and_no_empty_commit(self):
        n = self.issue("audit")
        self.w.session(n, commit=False)
        f = self.hub.report(n)
        self.assertEqual((f["status"], f["merged"]), ("done", "false"))
        self.assertIn("No changes to merge", self.hub.prose(n))
        self.assertEqual(fakes.sh(".", "git", "rev-parse", "task/%s^{tree}" % n).strip(),
                         fakes.sh(".", "git", "rev-parse", "task/%s^^{tree}" % n).strip())

    def test_debris_alone_is_no_change(self):
        n = self.issue("audit")
        self.w.session(n, files={".bashrc": "x", ".npmrc": ""}, commit=False)
        self.assertEqual(self.hub.report(n)["merged"], "false")
        self.assertIsNone(self.w.origin_file(".bashrc"))

    def test_the_audit_cannot_write_outside_its_paths_without_a_commit_either(self):
        self.w.push_main({"audit/old.md": "old"})
        c.sync_main()
        n = self.issue("audit")
        self.w.session(n, files={"audit/old.md": "changed"}, commit=False)
        self.assertEqual(self.w.origin_file("audit/old.md"), "changed")
        m = self.issue("audit")
        self.w.session(m, files={"src/x.txt": "x"}, commit=False)      # a new file outside: debris, not a violation
        self.assertIsNone(self.w.origin_file("src/x.txt"))
        self.assertEqual(self.hub.report(m)["status"], "done")
