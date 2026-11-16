"""The candidate's checks in the jobs "Prepare the checks" and "Check the results" of the session run: "Take in results"
uses their results only from the job's outputs and only for the same bundles, and falls back to a Tokenless run otherwise."""
import json
import os
import unittest

import fakes
from fakes import common as c
import check_results
from session_fixtures import SessionCase
from gate_fixtures import SCRIPT_GATE

VERIFY = "## Goal\nx\n## Verification\n`test -f src/a.txt`\n"


class CheckJobsCase(SessionCase):
    YML = SCRIPT_GATE + "check: '! grep -rq NOCHECK src'\n"

    def tokenless(self):
        """The Tokenless runs of checks (the Policymaker's trigger at the start of an iteration is not one: #375)."""
        return [p for p in self.hub.tokenless_purposes if p != "policy script policymaker-trigger.py"]

    def outcome(self, n):
        f = self.hub.report(n)
        return {k: f.get(k) for k in ("status", "verification", "check", "verdict", "merged", "fixes")}


class NormalPathTest(CheckJobsCase):
    def test_the_checks_run_in_the_check_job_and_no_tokenless_run_is_dispatched(self):
        n = self.issue(body=VERIFY)
        self.w.session(n, files={"src/a.txt": "good"})
        self.assertEqual(self.tokenless(), [])
        self.assertEqual(self.hub.checked, ["verification for #%s" % n, "check for #%s" % n, "change gate for #%s" % n])
        self.assertEqual(self.outcome(n), {"status": "done", "verification": "pass", "check": "pass", "verdict": "accepted",
                                           "merged": "true", "fixes": None})
        self.assertEqual(self.w.origin_file("src/a.txt"), "good")

    def test_the_job_stops_at_the_first_check_that_fails(self):
        n = self.issue(body="## Goal\nx\n## Verification\n`exit 3`\n")
        self.w.session(n, files={"src/a.txt": "good"})
        self.assertEqual(self.hub.checked, ["verification for #%s" % n])
        self.assertEqual(self.tokenless(), [])
        self.assertEqual((self.outcome(n)["verification"], self.outcome(n)["fixes"]), ("fail", "1"))

    def test_a_session_that_did_not_end_done_has_nothing_to_check(self):
        n = self.issue(body=VERIFY)
        self.w.session(n, files={"src/a.txt": "good"}, status="partial")
        self.assertEqual(self.w.checks[0]["count"], "0")
        self.assertEqual(self.hub.checked, [])

    def test_the_summary_and_the_record_show_the_checks(self):
        n = self.issue(body=VERIFY)
        summary = os.path.join(self.w.tmp, "summary.md")
        os.environ["GITHUB_STEP_SUMMARY"] = summary
        self.w.session(n, files={"src/a.txt": "good"})
        with open(summary, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("### Checks", text)
        self.assertIn("| Verification | ✅ pass |", text)
        self.assertIn("| Change gate | ✅ accepted |", text)
        import iteration as it
        import loop_status
        s = it.session_summary(self.hub.issue(n))
        self.assertEqual((s["verification"], s["check"], s["verdict"]), ("pass", "pass", "accepted"))
        self.assertEqual(loop_status.checks(s), "✅ verification · ✅ check · ✅ gate accepted")


class VerdictChannelTest(CheckJobsCase):
    def test_a_verdict_planted_in_the_session_results_is_ignored(self):
        """The session knows every digest it could forge (it has the trees): its artifact may carry a passing result for each.
        "Take in results" reads results only from the outputs of the job that ran the checks."""
        n = self.issue(body="## Goal\nx\n## Verification\n`exit 3`\n")
        forged = {}

        def plant():
            for key in json.loads(self.w.checks[1]["results"]):
                forged[key] = {"exit": "0", "stdout": "", "tail": ""}
            for directory in (os.path.join(os.environ["RUNNER_TEMP"], "..", "artifact"), os.environ["RUNNER_TEMP"]):
                with open(os.path.join(directory, "results.json"), "w", encoding="utf-8") as fh:
                    json.dump(forged, fh)
        self.w.after_checks = plant
        self.w.session(n, files={"src/a.txt": "good"}, session_files={"results.json": "{}"})
        self.assertTrue(forged)
        self.assertEqual(self.outcome(n)["verification"], "fail")

    def test_outputs_that_do_not_validate_are_no_result(self):
        good = {"exit": "0", "stdout": "", "tail": ""}
        for text in ("", "not json", "[]", json.dumps({"x" * 64: good}), json.dumps({"a" * 64: dict(good, exit="zero")}),
                     json.dumps({"a" * 64: dict(good, stdout="x" * (check_results.HEAD + 1))}),
                     json.dumps({"a" * 64: dict(good, extra=1)}), "{" + " " * check_results.OUTPUT_CHARS + "}"):
            check_results.accept(text)
            self.assertEqual(check_results.RESULTS, {}, text[:40])
        check_results.accept(json.dumps({"a" * 64: good, "b" * 64: dict(good, exit=None)}))
        self.assertEqual(set(check_results.RESULTS), {"a" * 64, "b" * 64})
        check_results.RESULTS.clear()

    def test_a_check_job_that_left_no_outputs_falls_back_to_tokenless_runs(self):
        n = self.issue(body=VERIFY)
        saved = self.w.check

        def failed_job(*a):
            saved(*a)
            return ""
        self.w.check = failed_job
        self.w.session(n, files={"src/a.txt": "good"})
        self.assertEqual(self.tokenless(), ["verification for #%s" % n, "check for #%s" % n, "change gate for #%s" % n])
        self.assertEqual(self.outcome(n)["merged"], "true")


class MainMovedTest(CheckJobsCase):
    def test_when_main_moved_the_checks_of_the_rebase_run_again_in_tokenless_runs(self):
        n = self.issue(body=VERIFY)
        self.w.after_checks = lambda: self.w.push_main({"docs/other.md": "another session merged"}, "another merge")
        self.w.session(n, files={"src/a.txt": "good"})
        # the verification ran on the candidate as the session left it: unchanged; the check and the gate saw the old main
        self.assertEqual(self.tokenless(), ["check for #%s" % n, "change gate for #%s" % n])
        self.assertEqual(self.outcome(n)["merged"], "true")
        self.assertEqual(self.w.origin_file("docs/other.md"), "another session merged")
        self.assertEqual(self.w.origin_file("src/a.txt"), "good")

    def test_the_rebase_is_the_same_commit_in_both_jobs(self):
        n = self.issue()
        self.w.session(n, files={"src/a.txt": "good"})
        self.assertEqual(self.tokenless(), [])            # the gate's bundle names CANDIDATE_SHA: only an equal commit matches


class SameOutcomeTest(CheckJobsCase):
    """Every check kind ends as it ended when each check was a Tokenless run."""
    CASES = {      # {k}: a path of this play only, so a merge of the first play leaves the second its own change
        "verification passes": ("## Goal\nx\n## Verification\n`test -f src/v{k}.txt`\n", {"src/v{k}.txt": "good"}),
        "verification fails": ("## Goal\nx\n## Verification\n`exit 3`\n", {"src/f{k}.txt": "good"}),
        "check fails": ("## Goal\nx\n", {"src/c{k}.txt": "NOCHECK"}),
        "gate rejects": ("## Goal\nx\n", {"src/a.txt": "BAD"}),
        "gate gives no verdict": ("## Goal\nx\n", {"src/silent.txt": "x"}),
        "gate accepts": ("## Goal\nx\n", {"src/g{k}.txt": "good"}),
        "direct paths only": ("## Goal\nx\n", {"journal/{k}.md": "notes"}),
    }

    def play(self, body, files, jobs, k):
        self.w.check_jobs = jobs
        files = {p.format(k=k): t for p, t in files.items()}
        role = "orchestrator" if any(p.startswith("journal/") for p in files) else "worker"
        n = self.issue(role=role, body=body.format(k=k))
        self.w.session(n, files=files)
        return self.outcome(n)

    def test_every_kind(self):
        for k, (name, (body, files)) in enumerate(self.CASES.items()):
            with self.subTest(name):
                self.hub.tokenless_purposes.clear()
                with_jobs = self.play(body, files, True, "%sa" % k)
                self.assertEqual(self.tokenless(), [], name)
                without = self.play(body, files, False, "%sb" % k)
                self.assertTrue(self.tokenless() or name == "direct paths only", name)
                self.assertEqual(with_jobs, without, name)


if __name__ == "__main__":
    unittest.main()
