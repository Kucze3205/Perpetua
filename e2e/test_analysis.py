"""Offline replay against a report frozen from live.py at eee61fd.

Only temporary repositories and synthetic captures; never call GitHub.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis
import collection
import experiment
import live
from test_build_guard import git, unlock

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "analysis"
AT = "2026-10-01T00:00:00Z"
END = "2026-10-01T00:02:00Z"


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def put_json(path, value):
    # The same fixture bytes on Windows and Linux: growth measures file sizes.
    put(path, json.dumps(value, indent=1, ensure_ascii=False) + "\n")


def dataset(out):
    """Two measured roles, excluded issues, stream fallback, probe, real git diff."""
    repo = out / "repository"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "test")
    put(repo / "policy" / "params.yml", "value: 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "baseline")
    git(repo, "tag", "setup-baseline")
    put(repo / "policy" / "params.yml", "value: 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "policy update")
    # The original .state starts at 7; the selected second iteration is 8.
    record = {"schema_version": 1, "iteration": 8, "status": "closed",
              "tasks": [{"id": "order-a"}], "policy": {"orders_refused": []},
              "outcome": {"wall_s": 120}}
    put_json(repo / "iterations" / "0008.json", record)
    put_json(repo / "iterations" / "0007.json", dict(record, iteration=7))
    put(repo / "journal" / "notes.md", "notes\n")
    put(repo / "policy" / "changes" / "receipt.json", "{}\n")
    put(repo / "execution" / "roles" / "worker.md", "worker\n")
    issues, runs = [], []
    for n, role in ((21, "worker"), (22, "policymaker")):
        fields = {"status": "done", "turns": 2, "agent_s": 45, "merged": "accepted",
                  "tokens_in": 100, "tokens_out": 20, "cache_read": 30, "cache_write": 10}
        comments = [{"body": "<!-- session-report -->\n```yaml\n" +
                            "\n".join("%s: %s" % x for x in fields.items()) + "\n```"}]
        issue = {"number": n, "title": role + " task", "state": "closed",
                 "labels": [{"name": "role:" + role}, {"name": "loop:iteration 8"}],
                 "created_at": AT, "closed_at": END, "comments_list": comments}
        issues.append(issue)
        job = {"name": "session", "created_at": AT, "started_at": AT, "completed_at": END,
               "conclusion": "success", "runner_name": "test", "labels": ["linux"],
               "steps": [{"name": name, "started_at": AT, "completed_at": END,
                          "conclusion": "success"} for name in ("Tools", "Session", "Epilogue")]}
        r = {"id": n + 100, "name": "session", "display_title": role,
             "event": "schedule", "status": "completed", "conclusion": "success",
             "created_at": AT, "run_started_at": AT, "updated_at": END,
             "run_attempt": 1, "jobs_list": [job], "issue": n}
        runs.append(r)
        capture = out / "artifacts" / ("capture-8-%d" % n)
        for name, text in {"issue.md": "task\n", "role/SKILL.md": analysis.PLANT,
                           "loop-state.json": "{}", "reports/7.md": "prior report",
                           "report.md": "done", "artifacts/result.txt": "result"}.items():
            put(capture / ".session" / name, text)
        for name in ("prompt.md", "contract.md", "tools.json"):
            put(capture / "trusted" / name, "fixed\n")
        put(capture / "capture" / "work-new-files.txt", "src/output.txt\n")
        put(capture / "agent-exit", "0\n")
        put(capture / "claude-stderr.txt", "warning\n")
        stream = [
            {"type": "system", "subtype": "init", "model": analysis.HAIKU,
             "tools": ["Read", "Write", "Bash"], "claude_code_version": "test"},
            {"type": "assistant", "message": {"usage": {"input_tokens": 1000}, "content": [
                {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": ".session/issue.md"}},
                {"type": "tool_use", "id": "b", "name": "Read", "input": {"file_path": ".session/issue.md"}},
                {"type": "tool_use", "id": "c", "name": "Edit", "input": {"file_path": "policy/params.yml"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "task"},
                {"type": "tool_result", "tool_use_id": "b", "is_error": True, "content": "Permission denied"}]}},
            {"type": "result", "num_turns": 2, "modelUsage": {analysis.HAIKU: {"inputTokens": 1000}}}]
        stream_dir = out / "artifacts" / ("stream-8-%d" % n) if role == "worker" else capture
        put(stream_dir / "stream.ndjson", "invalid line\n" + "\n".join(map(json.dumps, stream)))
    for n, title, iteration, created in ((23, "previous iteration", 7, AT),
                                         (24, "e2e probe worker", 8, AT),
                                         (25, "stale issue", 8, "2026-09-01T00:00:00Z")):
        issues.append(dict(issues[0], number=n, title=title, created_at=created,
                           labels=[{"name": "role:worker"}, {"name": "loop:iteration %d" % iteration}]))
    for issue in issues:
        put_json(out / "github" / "issues" / ("%d.json" % issue["number"]), issue)
    for r in runs:
        d = out / "github" / "runs" / str(r["id"])
        # Legacy run.json has neither jobs_list nor issue association.
        put_json(d / "run.json", {k: v for k, v in r.items() if k not in ("jobs_list", "issue")})
        put_json(d / "jobs.json", r["jobs_list"])
        put(d / "log.txt", "ISSUE: %d\n" % r["issue"])
    for role in ("worker", "policymaker"):
        base = out / "artifacts" / "probe" / role
        for name, text in {"issue.md": "new task", "role/SKILL.md": analysis.PLANT,
                           "loop-state.json": "{\"iterations\": [7, 8]}",
                           "reports/8.md": "result of #21", "streams/21.txt": "issue.md",
                           "exports/manifest.json": "{}"}.items():
            put(base / ".session" / name, text)
        put(base / "trusted" / "prompt.md", "fixed\n")
    put_json(out / "artifacts" / "probe" / "probe.json", {"iteration": 9})
    return issues, runs


class OfflineAnalysis(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="perpetua-analysis-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, **{
            ("onexc" if sys.version_info >= (3, 12) else "onerror"): unlock}))
        self.out = self.root / "data"
        self.issues, self.runs = dataset(self.out)
        self.expected = json.loads((FIXTURES / "report.json").read_text(encoding="utf-8"))
        self.context = {"iteration": 8, "run": self.expected["run"], "collect_errors": [],
                        "issues": [i["number"] for i in self.issues], "runs": [r["id"] for r in self.runs]}
        analysis.write_json(self.out / "collection.json", self.context)

    def test_full_report_matches_before_refactor_without_live_state_or_github(self):
        real_run = subprocess.run

        def local_git_only(args, **kwargs):
            self.assertEqual(args[0], "git")
            self.assertIn(args[1], ("diff", "rev-list"))
            self.assertEqual(Path(kwargs["cwd"]), self.out / "repository")
            return real_run(args, **kwargs)

        with mock.patch.object(experiment, "STATE", self.root / "absent"), \
                mock.patch.object(experiment, "gh", side_effect=AssertionError("GitHub forbidden")), \
                mock.patch.object(subprocess, "run", side_effect=local_git_only):
            report = analysis.analyze(self.out)
            self.assertEqual(report, self.expected)
            first = (self.out / "report.json").read_bytes()
            self.assertEqual(analysis.analyze(self.out), self.expected)
            self.assertEqual((self.out / "report.json").read_bytes(), first)
        self.assertEqual((self.out / "README.md").read_text(), (FIXTURES / "README.md").read_text())
        for session in report["sessions"]:
            path = Path(session["analysis_file"])
            self.assertEqual(json.loads((self.out / path).read_text()),
                             json.loads((FIXTURES / (str(session["issue"]) + ".json")).read_text()))

    def test_legacy_report_uses_record_iteration_instead_of_start_iteration(self):
        (self.out / "collection.json").unlink()
        analysis.write_json(self.out / "report.json", self.expected)
        self.assertEqual(analysis.analyze(self.out), self.expected)

    def test_cli_replays_collection_from_another_working_directory(self):
        result = subprocess.run([sys.executable, live.__file__, "analyze", str(self.out)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((self.out / "report.json").read_text()), self.expected)

    def test_failed_contract_exits_one_and_keeps_collection_errors(self):
        (self.out / "repository" / "iterations" / "0008.json").unlink()
        self.context["collect_errors"] = [{"step": "captures", "error": "missing"}]
        analysis.write_json(self.out / "collection.json", self.context)
        with self.assertRaises(SystemExit) as caught:
            analysis.analyze_command(argparse.Namespace(out=str(self.out)))
        self.assertEqual(caught.exception.code, 1)
        report = json.loads((self.out / "report.json").read_text())
        self.assertFalse(report["contract"]["iteration record exists and parses"]["ok"])
        self.assertEqual(report["collect_errors"], self.context["collect_errors"])

    def test_collect_then_replay_same_report_without_state(self):
        state = self.root / "state"
        analysis.write_json(state / "started.json", self.expected["run"]["started"])
        with mock.patch.object(collection, "STATE", state), \
                mock.patch.object(collection, "target", return_value=8), \
                mock.patch.object(collection, "repo", return_value="test/repo"), \
                mock.patch.object(collection, "fetch_meta", return_value={}), \
                mock.patch.object(collection, "fetch_issues", return_value=self.issues), \
                mock.patch.object(collection, "fetch_runs", return_value=self.runs), \
                mock.patch.object(collection, "fetch_artifacts"), \
                mock.patch.object(collection, "fetch_captures"), \
                mock.patch.object(collection, "fetch_repository"):
            collection.collect(argparse.Namespace(out=str(self.out)))
        before = json.loads((self.out / "report.json").read_text())
        shutil.rmtree(state)
        self.assertEqual(analysis.analyze(self.out), before)

    def test_run_keeps_one_output_and_selected_iteration_per_measurement(self):
        args = argparse.Namespace(out="data", iterations=2)
        selected = []
        with mock.patch.object(live, "start"), mock.patch.object(live, "wait"), \
                mock.patch.object(live, "probe"), mock.patch.object(live, "iterations", return_value=2), \
                mock.patch.object(experiment, "SHIFT", 0), \
                mock.patch.object(live, "collect", side_effect=lambda a: selected.append((experiment.SHIFT, a.out))):
            live.run_all(args)
        self.assertEqual(selected, [(0, "data-1"), (1, "data-2")])


if __name__ == "__main__":
    unittest.main()
