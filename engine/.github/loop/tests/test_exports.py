"""Declarative export scopes, fetch outcomes and immutable owner ceilings."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import fakes
from fakes import common as c, iteration as it, loop
import task_graph as graph
import owner_requests as requests
import policymaker_session
import session_exports as exports
from session_fixtures import SessionCase


class ExportsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = fakes.worker_world()
        self.hub = self.w.hub

    def tearDown(self):
        self.w.close()
        self.tmp.cleanup()

    def stage(self, requests, role="audit", iteration=8):
        return exports.stage("<!-- exports: %s -->" % json.dumps(requests), role, iteration, self.tmp.name)["results"]

    def artifact(self, n, issue, k=2, expired=False):
        return {"id": n, "name": "stream-%04d-%s" % (k, issue), "workflow_run": {"id": n + 100}, "expired": expired}

    def test_intersection_stages_only_selected_session_streams_for_any_role(self):
        self.hub.artifacts = [self.artifact(1, 10), self.artifact(2, 11), self.artifact(3, 10, 3)]
        results = self.stage([{"source": "streams", "iterations": [2], "sessions": [10]}])
        self.assertEqual([(r["status"], r.get("issue"), r.get("iteration")) for r in results], [("downloaded", 10, 2)])
        self.assertEqual(self.hub.gh_calls[0][:5], ("run", "download", "101", "--name", "stream-0002-10"))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "exports", "manifest.json")))

    def test_expired_deleted_and_unknown_are_distinct(self):
        self.hub.artifacts = [self.artifact(1, 10, expired=True)]
        results = self.stage([{"source": "streams", "sessions": [10, 11]}, {"source": "sh; evil", "sessions": [10]}])
        self.assertEqual([r["status"] for r in results], ["expired", "unavailable", "unknown"])
        self.assertEqual(self.hub.gh_calls, [])

    def test_download_failure_does_not_lose_later_sources_or_leak_stderr(self):
        self.hub.artifacts = [self.artifact(1, 10)]
        with patch.object(c, "gh", side_effect=SystemExit("secret stderr")):
            results = self.stage([{"source": "streams", "sessions": [10]}, {"source": "future", "sessions": [10]}])
        self.assertEqual([r["status"] for r in results], ["error", "unknown"])
        self.assertNotIn("secret", json.dumps(results))

    def test_bad_scopes_and_command_fields_never_fetch(self):
        for request in ({"source": "streams"}, {"source": "streams", "sessions": [True]},
                        {"source": "streams", "sessions": [1], "command": "evil"},
                        {"source": "records", "sessions": [1]}):
            self.assertEqual(self.stage([request])[0]["status"], "error")
        self.assertEqual(self.hub.gh_calls, [])

    def test_legacy_audit_window_and_explicit_opt_out(self):
        self.hub.artifacts = [self.artifact(k, k, k) for k in range(1, 8)]
        results = exports.stage("", "audit", 6, self.tmp.name)["results"]
        self.assertEqual([r["iteration"] for r in results if r["status"] == "downloaded"], [3, 4, 5, 6])
        self.assertEqual(self.stage([], role="audit"), [])
        self.assertEqual(exports.stage("", "worker", 6, self.tmp.name)["results"], [])

    def test_restaging_discards_files_from_the_previous_scope(self):
        self.hub.artifacts = [self.artifact(1, 10)]
        self.stage([{"source": "streams", "sessions": [10]}])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp.name, "streams")))
        self.stage([])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "streams")))

    def test_record_scope_never_includes_other_iterations(self):
        with patch.object(c, "safe_read", return_value='{"iteration": 2, "outcome": {}}') as read:
            results = self.stage([{"source": "records", "iterations": [2]}])
        read.assert_called_once_with(".", "iterations/0002.json")
        self.assertEqual(results[0]["status"], "downloaded")
        self.assertEqual(len(results[0]["snapshot"]), 64)

    def test_reports_use_trusted_author_selection_and_source_snapshot(self):
        n = self.hub.create_issue("T", "", labels=["role:worker", c.ITER + "2"])
        trusted = self.hub.post_comment(n, c.MARK + "\ntrusted")
        self.hub.cmts[n].append({"id": 999, "body": c.MARK + "\nforged", "author_association": "NONE", "user": {"login": "stranger"}})
        result = self.stage([{"source": "reports", "sessions": [n]}])[0]
        with open(os.path.join(self.tmp.name, *result["path"].split("/")), encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report["body"], trusted["body"])
        self.assertEqual(report["comment"], trusted["id"])
        self.assertIn("#issuecomment-", report["source"])

    def test_actions_use_only_matching_sessions_and_metadata_allowlist(self):
        n = self.hub.create_issue("T", "", labels=["role:worker", c.ITER + "2"])
        self.hub.runs = [
            {"databaseId": 40, "displayTitle": "session #%s" % n, "status": "completed", "createdAt": "2026-01-01T00:00:00Z"},
            {"databaseId": 41, "displayTitle": "session #999", "status": "completed", "createdAt": "2026-01-01T00:00:00Z"}]
        with patch.object(c, "api", side_effect=[{"id": 40, "status": "completed", "env": {"secret": "hidden"}},
                                                {"jobs": [{"name": "agent", "steps": [{"name": "run", "started_at": "now", "env": "hidden"}]}]}]) as api:
            result = self.stage([{"source": "actions", "sessions": [n]}])[0]
        self.assertEqual(api.call_count, 2)
        self.assertEqual(result["run"], 40)
        with open(os.path.join(self.tmp.name, *result["path"].split("/")), encoding="utf-8") as fh:
            self.assertNotIn("hidden", fh.read())


class PolicymakerSessionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {"policymaker": {"model": "large", "effort": "high", "max_turns": 150, "timeout_minutes": 90,
                                "tools": ["Read"], "write": ["policy/"]},
                    "policymaker_session_ceilings": {"models": ["large", "small"], "efforts": ["low", "medium", "high"],
                                                "max_turns": 100, "timeout_minutes": 60}}

    def profile(self, tune):
        with patch.object(c, "cfg", return_value=self.cfg), patch.object(c, "params", return_value={"policymaker_session": tune}):
            return policymaker_session.profile()

    def test_policymaker_tunes_resources_with_owner_numeric_caps(self):
        p = self.profile({"model": "small", "effort": "low", "max_turns": 9999, "timeout_minutes": 9999})
        self.assertEqual((p["model"], p["effort"], p["max_turns"], p["timeout_minutes"]), ("small", "low", 100, 60))
        self.assertEqual(p["tools"], ["Read"])

    def test_disallowed_models_efforts_permissions_and_invalid_numbers_are_rejected(self):
        for tune in ({"model": "unapproved"}, {"effort": "unapproved"}, {"tools": ["Bash"]},
                     {"internet": True}, {"max_turns": True}, {"timeout_minutes": -1}):
            with self.assertRaises(ValueError, msg=str(tune)):
                self.profile(tune)

    def test_legacy_config_is_an_owner_ceiling(self):
        self.cfg.pop("policymaker_session_ceilings")
        p = self.profile({"max_turns": 999})
        self.assertEqual(p["max_turns"], 150)
        with self.assertRaises(ValueError):
            self.profile({"model": "small"})


class ExportPipelineTest(SessionCase):
    def execution_environment(self, n):
        loop.resolve(n)
        with open(self.out, encoding="utf-8") as fh:
            return {key.upper(): value for key, value in (line.strip().split("=", 1) for line in fh if "=" in line)
                    if key not in ("start", "consented")}

    def test_stage_blocks_a_lowered_owner_ceiling_after_prep(self):
        n = self.issue("policymaker")
        environment = self.execution_environment(n)
        self.w.push_main({"engine.yml": self.YML + "\npolicymaker_session_ceilings:\n  models: [claude-opus-5-5]\n  efforts: [high]\n  max_turns: 5\n  timeout_minutes: 3\n"}, "owner lowered ceiling after prep")
        c.sync_main()
        with patch.dict(os.environ, environment):
            with self.assertRaisesRegex(SystemExit, "configuration changed after prep"):
                loop.stage(n, os.path.join(self.w.tmp, "work"), os.path.join(self.w.tmp, "trusted"))

    def test_stage_blocks_network_permission_change_after_prep(self):
        n = self.issue()
        environment = self.execution_environment(n)
        profiles = c.profiles()
        profiles["worker"]["internet"] = True
        # Even when a consent is otherwise valid, the frozen prep profile must match.
        with patch.dict(os.environ, environment), patch.object(c, "profiles", return_value=profiles), patch.object(requests, "consent_status", return_value=(True, None)):
            with self.assertRaisesRegex(SystemExit, "configuration changed after prep: PROFILE_DIGEST"):
                loop.stage(n, os.path.join(self.w.tmp, "work"), os.path.join(self.w.tmp, "trusted"))

    def test_stage_rechecks_current_owner_consent(self):
        n = self.issue()
        profiles = c.profiles()
        profiles["worker"]["internet"] = True
        with patch.object(c, "profiles", return_value=profiles):
            with self.assertRaisesRegex(SystemExit, "consent is no longer valid"):
                loop.stage(n, os.path.join(self.w.tmp, "work"), os.path.join(self.w.tmp, "trusted"))

    def test_task_graph_declaration_reaches_role_staging(self):
        declaration = [{"source": "records", "iterations": [0]}]
        task = {"id": "selected", "title": "T", "role": "worker", "body": "Consume selected data", "exports": declaration}
        self.assertEqual(graph.validate_tasks({"tasks": [task]}), [])
        n = graph.create_tasks(1, [task])[0]
        self.assertEqual(json.loads(c.marker(self.hub.issue(n)["body"], "exports")), declaration)
        work = os.path.join(self.w.tmp, "selected-work")
        fakes.sh(self.w.tmp, "git", "clone", "-q", "-c", "core.autocrlf=false", self.w.origin, work)
        loop.stage(n, work, os.path.join(self.w.tmp, "trusted"))
        with open(os.path.join(work, ".session", "exports", "manifest.json"), encoding="utf-8") as fh:
            result = json.load(fh)["results"][0]
        self.assertEqual((result["source"], result["status"]), ("records", "unavailable"))

    def test_policymaker_labels_do_not_escape_owner_ceilings(self):
        n = self.issue("policymaker", labels=["model:unapproved", "effort:unapproved"])
        loop.resolve(n)
        with open(self.out, encoding="utf-8") as fh:
            output = dict(line.strip().split("=", 1) for line in fh if "=" in line)
        self.assertEqual((output["model"], output["effort"]), ("claude-opus-5-5", "high"))

    def test_resolve_enforces_owner_ceilings_over_policy_params_and_issue_limits(self):
        self.w.push_main({"engine.yml": self.YML + "\npolicymaker_session_ceilings:\n  models: [claude-sonnet-5-5]\n  efforts: [low]\n  max_turns: 20\n  timeout_minutes: 10\n",
                          "policy/params.yml": fakes.POLICY_PARAMS + "\npolicymaker_session:\n  model: claude-sonnet-5-5\n  effort: low\n  max_turns: 9999\n  timeout_minutes: 9999\n"}, "owner ceiling and policy tuning")
        c.sync_main()
        n = self.issue("policymaker", body='<!-- limits: {"max_turns": 9999, "timeout_minutes": 9999} -->')
        loop.resolve(n)
        with open(self.out, encoding="utf-8") as fh:
            output = dict(line.strip().split("=", 1) for line in fh if "=" in line)
        self.assertEqual((output["model"], output["effort"], output["max_turns"], output["agent_timeout"]),
                         ("claude-sonnet-5-5", "low", "20", "10"))


if __name__ == "__main__":
    unittest.main()
