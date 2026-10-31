"""Producer -> trusted epilogue -> staged evidence -> accepted Policymaker resolution."""
import json
import os
from unittest.mock import patch

from fakes import common as c, iteration as it, loop
import session_reports as reports
from session_fixtures import SessionCase
from proposal_fixtures import PROPOSAL


class ReportsTest(SessionCase):
    def producer(self, text=None):
        n = self.issue()
        loop.resolve(n)
        work = self.w.session(n, files={"src/report.txt": "measured"},
                              report_extra="Evidence: three full-tree reads.",
                              session_files={"suggestions.json": text if text is not None else json.dumps([PROPOSAL])})
        return n, work

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def stage(self, n):
        work = self.work()
        loop.stage(n, work, os.path.join(self.w.tmp, "trusted-%s" % n))
        return os.path.join(work, ".session")

    def pending(self, pol):
        return self.read(os.path.join(self.stage(pol), "loop-state.json"))["suggestions"]

    def test_outbox_is_archived_raw_outside_github_and_policy_presents_and_decides_it(self):
        raw = json.dumps([PROPOSAL])
        n, work = self.producer(raw)
        archive = "iterations/suggestions/%s.json" % n
        self.assertEqual(self.w.origin_file(archive), raw)
        reports.capture(n, work)                                  # replaying changes nothing
        self.assertEqual(self.w.origin_file(archive), raw)
        pol = self.hub.role_issues("policymaker")[0]["number"]    # the worker's end started iteration 0, which it opens
        self.assertEqual(it.read_state(pol)["trigger"], "new session suggestions")
        self.assertEqual(it.read_state(pol)["outboxes"], [n])
        sess = self.stage(pol)
        proposal = self.pending(pol)[0]
        self.assertEqual(proposal["evidence"], PROPOSAL["evidence"])
        self.assertEqual(proposal["source"]["issue"], n)
        with open(os.path.join(sess, "history", "sessions", str(n), proposal["source"]["snapshot"] + ".md"), encoding="utf-8") as fh:
            self.assertIn("Evidence: three full-tree reads", fh.read())
        decision = {proposal["id"]: {"status": "handled", "reason": "Updated the worker's context budget"}}
        self.w.session(pol, files={"policy/suggestion-decisions.json": json.dumps(decision)})
        later = it.start_policymaker(1, "after the decision")
        self.assertEqual(self.pending(later), [])
        self.assertEqual(self.w.origin_file(archive), raw)        # the archive is not rewritten by decisions
        self.assertIsNone(it.policymaker_due(1))

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

    def test_bad_suggestions_keep_report_and_valid_entries_and_policy_reports_the_rest(self):
        raws = ("{bad json", json.dumps([PROPOSAL, {"kind": "invalid"}]))
        issues = []
        for text in raws:
            n, _ = self.producer(text)
            issues.append(n)
            self.assertEqual(self.w.origin_file("iterations/suggestions/%s.json" % n), text)
            self.assertTrue(all("Prose of" in doc["report"]["body"] for _, doc in reports.source_records(n)))
        pol = it.start_policymaker(1, "test")
        state = self.read(os.path.join(self.stage(pol), "loop-state.json"))
        self.assertEqual(len(state["suggestions"]), 1)
        self.assertEqual(state["suggestions"][0]["source"]["issue"], issues[1])
        self.assertEqual(len(state["suggestion_diagnostics"]), 2)

    def test_pending_trigger_consumed_once_but_new_suggestion_still_triggers(self):
        self.producer()                                           # its end started iteration 0, opened by the Policymaker
        pol = self.hub.role_issues("policymaker")[0]["number"]
        self.assertIsNone(it.policymaker_due(1))  # Policymaker is open
        self.producer()
        self.hub.close_issue(pol)
        self.assertEqual(it.policymaker_due(1), "new session suggestions")
        later = it.start_policymaker(1, "new session suggestions")
        self.hub.close_issue(later)
        self.assertIsNone(it.policymaker_due(1))  # still pending; already delivered

    def test_empty_outbox_does_not_trigger(self):
        self.producer("[]")
        self.assertNotIn("new session suggestions",
                         [it.read_state(x["number"])["trigger"] for x in self.hub.role_issues("policymaker")])

    def test_script_without_agent_report_archives_its_outbox_with_its_engine_report(self):
        n = self.issue("rule")
        self.w.session(n, report=False, session_files={"suggestions.json": json.dumps([PROPOSAL])})
        self.assertEqual(json.loads(self.w.origin_file("iterations/suggestions/%s.json" % n)), [PROPOSAL])
        frozen = [doc for _, doc in reports.source_records(n)][-1]
        self.assertEqual(c.fields(frozen["report"]["body"])["status"], "done")
        self.assertIn("No changes to merge", frozen["report"]["body"])
