"""Recipient history and startup budgets through the session staging interface."""
import json
import os

from unittest.mock import patch

from fakes import common as c, iteration as it, loop
import session_reports as reports
import session_startup as startup
import session_fixtures as sessions

read = sessions.read
BIG = "x" * 400004      # about 100001 tokens: over the warning threshold of 100000


class ContextTest(sessions.SessionCase):
    def stage(self, role="policymaker", state=None, labels=(), body="## Goal\nx\n"):
        n = self.issue(role, labels=labels, body=body)
        if state is not None:
            it.store_state(n, state)
        work = self.work()
        trusted = os.path.join(self.w.tmp, "context-trusted-%s" % n)
        loop.stage(n, work, trusted)
        return n, work, trusted

    def test_policymaker_reads_one_state_snapshot_and_keeps_task_comments(self):
        n = self.issue("policymaker")
        it.store_state(n, {"trigger": "inspect costs", "iterations": []})
        self.hub.post_comment(n, "Owner context worth keeping")
        work = self.work()
        loop.stage(n, work, os.path.join(self.w.tmp, "one-state"))
        issue = read(os.path.join(work, ".session/issue.md"))
        self.assertNotIn(it.STATE_MARK, issue)
        self.assertIn("Owner context worth keeping", issue)
        self.assertEqual(json.loads(read(os.path.join(work, ".session/loop-state.json")))["trigger"],
                         "inspect costs")

    def test_long_history_is_searchable_offline_without_loading_the_full_index(self):
        files = {}
        for n in range(180):
            record = {"iteration": n, "status": "closed", "tasks": [],
                      "policy": {"orders_refused": "old refusal" if n == 0 else None},
                      "outcome": {"verdict": {"value": "rejected", "improved": False}}}
            files["iterations/%04d.json" % n] = json.dumps(record)
        c.commit_main(files, "long history")
        state = {"iteration": 180, "iterations": it.index_lines(), "no_progress": 180}
        _, work, _ = self.stage(state=state)
        staged = json.loads(read(os.path.join(work, ".session/loop-state.json")))
        self.assertEqual(staged["no_progress"], 180)
        self.assertEqual([x["n"] for x in staged["iterations"]], [179])
        self.assertEqual(staged["history"]["records"], 180)
        self.assertIn("old refusal", read(os.path.join(work, ".session/history/records/0000.json")))

    def test_shaper_can_find_old_frozen_reports(self):
        public = self.issue()
        loop.resolve(public)
        c.update_report(public, {"status": "done"}, "Old public finding\nUseful second line")
        reports.capture(public)
        frozen = reports.source_records(public)[0][1]["report"]
        c.update_report(public, {"status": "done"}, "New report text")
        record = {"iteration": 0, "status": "closed", "tasks": [
            {"issue": public, "title": "Validate old data", "role": "worker", "body": "Check source"}],
            "outcome": {"sessions": [{"issue": public, "role": "worker", "status": "done"}]}}
        c.commit_main({"iterations/0000.json": json.dumps(record)}, "old evidence")
        _, work, _ = self.stage("orchestrator", labels=["loop:shaper"],
                                state={"iteration": 20, "iterations": [{"n": n} for n in range(20)]})
        path = ".session/history/sessions/%s/%s.md" % (public, frozen["snapshot"])
        text = read(os.path.join(work, path))
        self.assertIn("Validate old data", text)
        self.assertIn("Old public finding\nUseful second line", text)

    def test_startup_declaration_drives_reads_and_counts_only_selected_documents(self):
        c.commit_main({"execution/roles/worker/SKILL.md":
                       "---\nname: worker\nstartup: [journal/focus.md, .session/role/reference.md, journal/focus.md]\n---\nDo the work.\n",
                       "execution/roles/worker/reference.md": "Selected reference",
                       "journal/focus.md": "Current line of work",
                       "journal/old.md": "OLD ARCHIVE NOT A STARTUP INPUT"}, "declare startup")
        _, work, trusted = self.stage("worker")
        budget = json.loads(read(os.path.join(work, ".session/startup.json")))
        paths = [x["path"] for x in budget["documents"]]
        self.assertEqual(paths.count("journal/focus.md"), 1)
        self.assertIn(".session/role/reference.md", paths)
        self.assertNotIn("journal/old.md", paths)
        prompt = read(os.path.join(trusted, "prompt.md"))
        self.assertIn("journal/focus.md", prompt)
        self.assertIn(".session/role/reference.md", prompt)

    def over(self, text=BIG):
        """The worker's startup reads become `text`."""
        c.commit_main({"execution/roles/worker/SKILL.md": "---\nname: worker\nstartup: [journal/focus.md]\n---\nWork.\n",
                       "journal/focus.md": text}, "worker startup memory")

    def test_budget_warning_continues_and_reaches_policy_once_until_recovery(self):
        self.over()
        worker, work, trusted = self.stage("worker")
        self.assertEqual(json.loads(read(os.path.join(work, ".session/startup.json")))["status"], "over_budget")
        self.assertIn("continue your task", read(os.path.join(trusted, "prompt.md")))
        self.assertEqual(it.policymaker_due(1), "startup budget needs attention")
        pol, policy_work, _ = self.stage(state={"iterations": []})
        policy_budget = json.loads(read(os.path.join(policy_work, ".session/startup.json")))
        self.assertEqual(policy_budget["policy_notices"][0]["issue"], worker)
        loop.close_out(pol, c.issue(pol), "done", {}, "Handled startup notice")
        self.over(BIG * 2)
        self.stage("worker")
        self.assertIsNone(it.policymaker_due(2))
        self.over("small")
        self.stage("worker")  # successful measurement closes the incident
        self.over()
        self.stage("worker")
        self.assertEqual(it.policymaker_due(3), "startup budget needs attention")

    def test_unmeasurable_startup_warns_without_blocking_and_policy_warns_itself(self):
        c.commit_main({"policy/roles/policymaker/SKILL.md": "---\nname: policymaker\nstartup: [policy/missing.md]\n---\nOrganize.\n"},
                      "a missing read")
        n, work, trusted = self.stage()
        budget = json.loads(read(os.path.join(work, ".session/startup.json")))
        self.assertEqual(budget["status"], "unmeasured")
        self.assertIsNone(budget["tokens"])
        self.assertEqual(budget["policy_notices"][0]["issue"], n)
        self.assertTrue(os.path.isfile(os.path.join(trusted, "agent.sh")))
        loop.close_out(n, c.issue(n), "done", {}, "Handled own startup notice")
        self.assertIsNone(it.policymaker_due(1))

    def test_staging_does_not_consume_a_notice_if_policymaker_fails(self):
        self.over()
        self.stage("worker")
        pol, work, trusted = self.stage(state={"iterations": []})
        self.assertIn("read .session/startup.json", read(os.path.join(trusted, "prompt.md")))
        loop.close_out(pol, c.issue(pol), "crashed", {}, "Session failed before handling the notice")
        self.assertEqual(it.policymaker_due(2), "startup budget needs attention")

    def test_policy_can_narrow_shaper_history_using_existing_export_selectors(self):
        record = {"iteration": 0, "status": "closed", "tasks": [], "outcome": {}}
        c.commit_main({"iterations/0000.json": json.dumps(record),
                       "execution/roles/orchestrator/SKILL.md":
                       "---\nname: orchestrator\nexports: []\n---\nShape the work.\n"}, "select inputs")
        _, work, _ = self.stage("orchestrator", labels=["loop:shaper"],
                                state={"iterations": [{"n": 0}]})
        self.assertFalse(os.path.isfile(os.path.join(work, ".session/history/records/0000.json")))
        manifest = json.loads(read(os.path.join(work, ".session/history/manifest.json")))
        self.assertEqual(manifest["scope"], "task-selected inputs")

    def test_the_estimate_counts_one_declared_package_without_any_api_key(self):
        c.commit_main({"execution/roles/worker/SKILL.md":
                       "---\nname: worker\nstartup: [journal/focus.md, ./journal/focus.md]\n---\nWork.\n",
                       "journal/focus.md": "Selected initial memory " * 4000,
                       "journal/old.md": BIG}, "counter inputs")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-counter-key"}):
            n, work, trusted = self.stage("worker")
        budget = json.loads(read(os.path.join(work, ".session/startup.json")))
        self.assertEqual(budget["status"], "ok")
        self.assertTrue(20000 < budget["tokens"] < 30000, budget["tokens"])
        self.assertEqual(it.session_summary(c.issue(n))["startup"]["tokens"], budget["tokens"])
        for root, _, names in os.walk(os.path.join(work, ".session")):
            for name in names:
                self.assertNotIn("test-counter-key", read(os.path.join(root, name)))

    def test_missing_or_private_startup_files_warn_without_sending_them_to_the_provider(self):
        c.commit_main({"policy/private.md": "PRIVATE SOURCE BODY",
                       "execution/roles/worker/SKILL.md":
                       "---\nname: worker\nstartup: [policy/private.md, ../outside.md, ./.git/config]\n---\nWork.\n"}, "invalid input selection")
        _, work, _ = self.stage("worker")
        budget = json.loads(read(os.path.join(work, ".session/startup.json")))
        self.assertEqual(budget["status"], "unmeasured")
        self.assertNotIn("PRIVATE SOURCE BODY", json.dumps(budget))
        self.assertNotIn(".git/config", [item["path"] for item in budget["documents"]])

    def test_nontext_startup_document_is_unmeasured_instead_of_a_false_token_count(self):
        c.commit_main({"docs/binary.dat": "\0binary",
                       "execution/roles/worker/SKILL.md":
                       "---\nname: worker\nstartup: [docs/binary.dat]\n---\nWork.\n"}, "nontext input")
        _, work, _ = self.stage("worker")
        budget = json.loads(read(os.path.join(work, ".session/startup.json")))
        self.assertEqual(budget["status"], "unmeasured")
        self.assertIn("UTF-8 text", budget["detail"])

    def test_policy_can_replace_its_own_startup_memory(self):
        c.commit_main({"policy/roles/policymaker/SKILL.md":
                       "---\nname: policymaker\nstartup: [policy/current.md]\n---\nOrganize the loop.\n",
                       "policy/current.md": "Decision and reason",
                       "policy/working-memory.md": "Replaced default"}, "adapt policy memory")
        _, work, trusted = self.stage(state={"iterations": []})
        budget = json.loads(read(os.path.join(work, ".session/startup.json")))
        paths = [doc["path"] for doc in budget["documents"]]
        self.assertIn("policy/current.md", paths)
        self.assertNotIn("policy/working-memory.md", paths)

    def test_new_notices_arriving_during_policy_are_not_acknowledged_unseen(self):
        self.over()
        self.stage("worker")
        c.commit_main({"policy/roles/policymaker/SKILL.md":
                       "---\nname: policymaker\nstartup: []\n---\nOrganize.\n"}, "custom policy reads")
        pol, work, trusted = self.stage(state={"iterations": []})
        self.assertIn("read .session/startup.json", read(os.path.join(trusted, "prompt.md")))
        c.commit_main({"policy/roles/audit/SKILL.md": "---\nname: audit\nstartup: [journal/focus.md]\n---\nAudit.\n"}, "audit reads")
        self.stage("audit")
        loop.close_out(pol, c.issue(pol), "done", {}, "Handled the worker notice")
        self.assertEqual([item["role"] for item in startup.pending()], ["audit"])

    def test_parallel_notice_update_reloads_other_roles_instead_of_overwriting_them(self):
        commit = c.commit_main
        raced = []

        def concurrent(files, message, **kwargs):
            if startup.STATE in files and not raced:
                raced.append(True)
                commit({startup.STATE: json.dumps({"audit": {"id": "other", "role": "audit"}})}, "other notice")
                raise c.ConcurrentChange("another stage won")
            return commit(files, message, **kwargs)

        self.over()
        with patch.object(c, "commit_main", side_effect=concurrent):
            self.stage("worker")
        self.assertEqual(set(startup.notices()), {"audit", "worker"})


class SessionStateScriptTest(sessions.SessionCase):
    SCRIPT = "policy/session-state.py"
    stage = ContextTest.stage

    def test_a_policymaker_change_to_the_script_changes_what_the_next_session_receives(self):
        _, work, _ = self.stage(state={"iteration": 1, "iterations": [{"n": 0}, {"n": 1}]})
        self.assertEqual([x["n"] for x in json.loads(read(os.path.join(work, ".session/loop-state.json")))["iterations"]], [1])
        c.commit_main({self.SCRIPT: "import json\nprint(json.dumps({'loop_state': {'mine': True}, 'files': {'history/note.md': 'hi'}}))\n"},
                      "the Policymaker changes the state")
        _, work, _ = self.stage(state={"iteration": 2, "iterations": []})
        self.assertEqual(json.loads(read(os.path.join(work, ".session/loop-state.json"))), {"mine": True})
        self.assertEqual(read(os.path.join(work, ".session/history/note.md")), "hi")
        self.assertFalse(os.path.exists(os.path.join(work, ".session/history/manifest.json")))

    def test_invalid_script_output_stops_the_staging(self):
        for body in ("print('{\"loop_state\": 3}')", "print('{\"files\": {\"../x\": \"y\"}}')",
                     "print('{\"files\": {\"loop-state.json\": \"y\"}}')", "print('nope')"):
            c.commit_main({self.SCRIPT: body + "\n"}, "bad state script")
            with self.assertRaises(SystemExit):
                self.stage(state={"iterations": []})


class SessionStartupScriptTest(sessions.SessionCase):
    SCRIPT = "policy/session-startup.py"
    stage = ContextTest.stage

    def test_a_policymaker_change_to_the_script_changes_the_bootstrap_and_the_warning_threshold(self):
        c.commit_main({self.SCRIPT: "import json\nprint(json.dumps({'reads': ['.session/issue.md'], 'prompt': 'MY BOOTSTRAP',"
                       " 'budget': {'status': 'over_budget', 'tokens': 7, 'detail': 'mine'}}))\n"}, "the Policymaker changes startup")
        _, work, trusted = self.stage("worker")
        self.assertEqual(read(os.path.join(trusted, "prompt.md")), "MY BOOTSTRAP")
        budget = json.loads(read(os.path.join(work, ".session/startup.json")))
        self.assertEqual((budget["status"], budget["tokens"], budget["detail"]), ("over_budget", 7, "mine"))
        self.assertEqual([x["path"] for x in budget["documents"]], [".session/issue.md"])
        self.assertEqual(it.policymaker_due(1), "startup budget needs attention")

    def test_invalid_script_output_stops_the_staging(self):
        for body in ("print('nope')", "print('{\"reads\": [\"policy/params.yml\"], \"prompt\": \"\", \"budget\": {\"status\": \"ok\"}}')",
                     "print('{\"reads\": [], \"prompt\": \"\", \"budget\": {\"status\": \"fine\"}}')"):
            c.commit_main({self.SCRIPT: body + "\n"}, "bad startup script")
            with self.assertRaises(SystemExit):
                self.stage("worker")
