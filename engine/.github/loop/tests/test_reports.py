"""Producer -> trusted epilogue -> staged evidence -> accepted Policymaker resolution."""
import json
import os
from unittest.mock import patch

from fakes import common as c, iteration as it, loop
import session_reports as reports
from test_session import SessionCase


PROPOSAL = {"kind": "role-change", "what": "Reduce worker context", "why": "Repeated expensive reads",
            "evidence": "Report: worker read the entire tree three times"}


class ReportsTest(SessionCase):
    def producer(self, text=None):
        n = self.issue()
        work = self.w.session(n, files={"src/report.txt": "measured"},
                              report_extra="Evidence: three full-tree reads.",
                              session_files={"suggestions.json": text if text is not None else json.dumps([PROPOSAL])})
        return n, work

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def stage(self, n):
        work = os.path.join(self.w.tmp, "stage-%s" % n)
        os.makedirs(os.path.join(work, ".git", "info"), exist_ok=True)
        loop.stage(n, work, os.path.join(self.w.tmp, "trusted-%s" % n))
        return os.path.join(work, ".session")

    def test_epilogue_stages_proposal_evidence_and_accepted_policymaker_resolves_once(self):
        n, work = self.producer()
        pending = reports.pending()
        self.assertEqual(len(pending), 1)
        proposal = pending[0]
        self.assertEqual(proposal["evidence"], PROPOSAL["evidence"])
        self.assertEqual(proposal["source"]["issue"], n)
        immutable = {path: self.w.origin_file(path) for path, _ in reports.source_records(n)}
        reports.capture(n, work)
        self.assertEqual([s["id"] for s in reports.pending()], [proposal["id"]])
        self.assertEqual(it.policymaker_due(1), "new session suggestions")
        pol = it.start_policymaker(1, "new session suggestions")
        sess = self.stage(pol)
        state = self.read(os.path.join(sess, "loop-state.json"))
        self.assertEqual(state["suggestions"][0]["id"], proposal["id"])
        report = self.read(os.path.join(sess, "reports", "%s.json" % n))
        self.assertIn("Evidence: three full-tree reads", report["body"])
        evidence = self.read(os.path.join(sess, "reports", "%s-%s.json" % (n, proposal["source"]["snapshot"])))
        self.assertEqual(evidence["snapshot"], proposal["source"]["snapshot"])
        decision = [{"id": proposal["id"], "status": "handled", "reason": "Updated the worker's context budget"}]
        self.w.session(pol, files={"policy/context.md": "Read only the selected files."},
                       session_files={"suggestion-decisions.json": json.dumps(decision)})
        self.assertEqual(len(reports.pending()), 1)  # completed is not yet accepted
        loop.apply_policy()
        self.assertEqual(reports.pending(), [])
        final = self.w.origin_file(reports.STATE)
        reports.apply_decisions(pol)
        self.assertEqual(self.w.origin_file(reports.STATE), final)
        for path, text in immutable.items():
            self.assertEqual(self.w.origin_file(path), text)
        self.assertIsNone(it.policymaker_due(1))
        self.assertIn(proposal["id"], it.session_summary(c.issue(n))["suggestion_ids"])

    def test_shaper_reads_report_body_without_network_and_untrusted_marker_is_ignored(self):
        good, _ = self.producer()
        bad = self.issue()
        self.hub.cmts[bad] = [{"id": 999, "body": c.MARK + "\nstranger report", "author_association": "NONE",
                               "user": {"login": "stranger"}}]
        shaper = self.issue("orchestrator", labels=["loop:shaper"])
        it.store_state(shaper, {"iterations": [{"n": 1}]})
        sess = self.stage(shaper)
        index = self.read(os.path.join(sess, "reports", "index.json"))
        self.assertEqual([r["issue"] for r in index], [good])
        self.assertIn("Evidence: three full-tree reads", self.read(os.path.join(sess, "reports", "%s.json" % good))["body"])
        self.assertEqual(self.hub.gh_calls, [])

    def test_bad_suggestions_keep_report_and_persist_clear_diagnostics(self):
        for text in ("{bad json", json.dumps([PROPOSAL, {"kind": "invalid"}])):
            n, _ = self.producer(text)
            snapshots = [doc for _, doc in reports.source_records(n)]
            self.assertTrue(any(doc["diagnostics"] for doc in snapshots))
            self.assertTrue(all("Prose of" in doc["report"]["body"] for doc in snapshots))
        self.assertEqual(len(reports.pending()), 1)

    def test_pending_trigger_consumed_once_but_new_suggestion_still_triggers(self):
        self.producer()
        pol = it.start_policymaker(1, "new session suggestions")
        self.assertIsNone(it.policymaker_due(1))  # Policymaker is open
        self.producer()
        self.hub.close_issue(pol)
        self.assertEqual(it.policymaker_due(1), "new session suggestions")
        later = it.start_policymaker(1, "new session suggestions")
        self.hub.close_issue(later)
        self.assertIsNone(it.policymaker_due(1))  # still pending; already delivered

    def test_crashed_or_rejected_policymaker_does_not_resolve_suggestions(self):
        self.producer()
        for status, content in (("crashed", "okay"), ("done", "BAD")):
            pol = it.start_policymaker(1, "test")
            decision = [{"id": reports.pending()[0]["id"], "status": "rejected", "reason": "Not useful"}]
            self.w.session(pol, status=status, files={"policy/context.md": content},
                           session_files={"suggestion-decisions.json": json.dumps(decision)})
            if status == "done":
                self.w.push_main({"policy/context.md": "Owner's competing change"}, "owner update")
            loop.apply_policy()
            self.assertEqual(len(reports.pending()), 1)

    def test_bad_or_unstaged_decisions_are_refused_and_worker_cannot_handle(self):
        n, work = self.producer()
        item = reports.pending()[0]
        self.w.write(work, ".session/suggestion-decisions.json", json.dumps([
            {"id": item["id"], "status": "handled", "reason": "worker claim"}]))
        loop.finalize(n, work)
        self.assertEqual(reports.decisions(), {})
        pol = it.start_policymaker(1, "test")
        self.w.session(pol, files={"policy/context.md": "Read few files"}, session_files={
            "suggestion-decisions.json": json.dumps([{"id": "unknown", "status": "handled", "reason": "wrong"}])})
        loop.apply_policy()
        self.assertEqual(len(reports.pending()), 1)

    def test_concurrent_resolution_reloads_without_losing_previous_decisions(self):
        self.producer()
        pol = it.start_policymaker(1, "test")
        item = reports.pending()[0]
        work = os.path.join(self.w.tmp, "decision-work")
        self.w.write(work, ".session/suggestion-decisions.json", json.dumps([
            {"id": item["id"], "status": "rejected", "reason": "Keep existing design"}]))
        reports.freeze_decisions(pol, work, [item])
        commit = c.commit_main
        raced = []

        def concurrent(files, message, **kwargs):
            if not raced:
                raced.append(True)
                commit({reports.STATE: reports.encoded({"other": {"status": "handled", "reason": "Already done"}})}, "other decision")
                raise c.ConcurrentChange("another epilogue won")
            return commit(files, message, **kwargs)

        with patch.object(c, "commit_main", side_effect=concurrent):
            reports.apply_decisions(pol)
        self.assertIn("other", reports.decisions())
        self.assertEqual(reports.decisions()[item["id"]]["status"], "rejected")

    def test_script_without_agent_report_keeps_suggestions_with_its_engine_report(self):
        n = self.issue("rule")
        self.w.session(n, report=False, session_files={"suggestions.json": json.dumps([PROPOSAL])})
        self.assertEqual(len(reports.pending()), 1)
        source = reports.pending()[0]["source"]
        frozen = next(doc for _, doc in reports.source_records(n) if doc["report"]["snapshot"] == source["snapshot"])
        self.assertEqual(c.fields(frozen["report"]["body"])["status"], "done")
        self.assertIn("No changes to merge", frozen["report"]["body"])
