"""One session: how it starts (resolve, stage) and how it ends (the epilogue), including the ways it goes wrong."""
import json
import os
import subprocess
import types
import unittest
from datetime import timedelta
from unittest.mock import patch

import fakes
from fakes import common as c, iteration as it, loop, task, tasks_json
from test_merge import GATE_SH, SCRIPT_GATE
import managed_resources as resources
import session_reports as reports

def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


EXTRA_PROFILES = """ghost:
  write: ["src/**"]
  tools: [Read]
rule:
  agent: false
  command: "echo shaping"
  write: []
cmdless:
  agent: false
"""


class SessionCase(unittest.TestCase):
    YML = SCRIPT_GATE

    def setUp(self):
        self.w = fakes.worker_world(self.YML)
        self.hub = self.w.hub
        self.w.push_main({"policy/evaluator/gate.sh": GATE_SH, "execution/profiles.yml": fakes.EXECUTION_PROFILES + EXTRA_PROFILES}, "setup")
        c.sync_main()
        self.out = os.path.join(self.w.tmp, "gh-output")
        os.environ["GITHUB_OUTPUT"] = self.out
        self.home = os.path.join(self.w.tmp, "home")
        os.makedirs(self.home)
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.home

    def tearDown(self):
        self.w.close()

    def issue(self, role="worker", body="## Goal\nx\n", labels=(), title="T"):
        return self.hub.create_issue(title, body, labels=["role:" + role, "loop:iteration 1"] + list(labels))

    def outputs(self):
        with open(self.out) as fh:
            return dict(l.rstrip("\n").split("=", 1) for l in fh if "=" in l)


class ResolveTest(SessionCase):
    def test_labels_and_limits_decide_the_session(self):
        n = self.issue(labels=["model:opus", "effort:high"], body='## Goal\nx\n<!-- limits: {"timeout_minutes": 30, "max_turns": 20} -->\n')
        loop.resolve(n)
        o = self.outputs()
        self.assertEqual((o["kind"], o["role"], o["model"], o["effort"], o["start"]), ("agent", "worker", "claude-opus-5-5", "high", "main"))
        self.assertEqual((o["agent_timeout"], o["max_turns"], o["job_timeout"]), ("30", "20", "180"))     # + 120 gate + 30 epilogue
        self.assertEqual(o["tools"], "Read,Grep,Glob,Write,Edit,Skill")
        self.assertEqual((o["consented"], o["iteration"]), ("false", "0001"))

    def test_defaults_come_from_the_profile_and_the_policy_params(self):
        n = self.issue()
        loop.resolve(n)
        o = self.outputs()
        self.assertEqual((o["model"], o["effort"], o["agent_timeout"], o["max_turns"]), ("claude-sonnet-5-5", "medium", "205", "150"))

    def test_a_task_cannot_exceed_the_ceilings(self):
        n = self.issue(body='<!-- limits: {"timeout_minutes": 9999, "max_turns": 9999} -->\n')
        loop.resolve(n)
        o = self.outputs()
        self.assertEqual((o["agent_timeout"], o["max_turns"]), ("205", "150"))      # 355 - 30 - 120 for the gate

    def test_the_policymaker_session_uses_the_engines_own_profile(self):
        n = self.hub.create_issue("P", "b", labels=["role:policymaker", "loop:iteration 1"])
        loop.resolve(n)
        o = self.outputs()
        self.assertEqual((o["model"], o["effort"], o["max_turns"]), ("claude-opus-5-5", "high", "150"))

    def test_a_script_role_is_a_script(self):
        n = self.issue("rule")
        loop.resolve(n)
        self.assertEqual(self.outputs()["kind"], "script")

    def test_the_start_branch_is_the_marker_or_the_existing_task_branch(self):
        n = self.issue(body="x\n<!-- start-branch: task/77 -->\n")
        loop.resolve(n)
        self.assertEqual(self.outputs()["start"], "task/77")
        self.w.branch(n, {"src/a.txt": "x"})
        loop.resolve(n)
        self.assertEqual(self.outputs()["start"], "task/%s" % n)

    def test_refusals_close_the_issue_as_blocked(self):
        for role, why in (("nobody", "no profile"), ("ghost", "no skill"), ("cmdless", "no command")):
            n = self.issue(role)
            with self.assertRaises(SystemExit):
                loop.resolve(n)
            self.assertEqual(self.hub.report(n)["status"], "blocked", role)
            self.assertIn(why, self.hub.prose(n), role)

    def test_an_issue_outside_the_loop_is_not_touched(self):
        n = self.hub.create_issue("T", "b", labels=["role:worker"])             # no loop:iteration
        with self.assertRaises(SystemExit):
            loop.resolve(n)
        self.assertEqual(self.hub.issues[n]["state"], "open")


class StageTest(SessionCase):
    def work(self):
        self.w.n_work += 1
        d = os.path.join(self.w.tmp, "stagework%s" % self.w.n_work)
        fakes.sh(self.w.tmp, "git", "clone", "-q", "-c", "core.autocrlf=false", self.w.origin, d)
        return d

    def test_stage_writes_the_secrets_file_agent_sh_reads(self):
        n = self.issue(body="## Goal\nx\n")
        trusted = os.path.join(self.w.tmp, "trusted-secrets")
        with patch.object(loop, "consent_status", return_value=(True, None)), \
                patch.object(loop, "secrets_of_consent", return_value=["A", "B"]):
            loop.stage(n, self.work(), trusted)
        self.assertEqual(read(os.path.join(trusted, "secrets")), "A\nB\n")
        self.assertFalse(os.path.exists(os.path.join(trusted, "unapproved")))

    def test_everything_the_agent_needs_is_put_in_place(self):
        n = self.issue(body="## Goal\nstage me\n")
        self.hub.post_comment(n, "trusted words")
        self.hub.cmts[n].append({"id": 9, "body": "stranger words", "author_association": "NONE", "user": {"login": "stranger"}})
        work, trusted = self.work(), os.path.join(self.w.tmp, "trusted")
        loop.stage(n, work, trusted)
        issue_md = read(os.path.join(work, ".session", "issue.md"))
        self.assertIn("stage me", issue_md)
        self.assertIn("trusted words", issue_md)
        self.assertNotIn("stranger words", issue_md)
        self.assertIn("Session protocol", read(os.path.join(work, ".session", "protocol.md")))
        for f in ("agent.sh", "stream_filter.py", "remaining.py", "settings.json"):
            self.assertTrue(os.path.exists(os.path.join(trusted, f)), f)
        self.assertTrue(os.path.exists(os.path.join(work, ".session", "role", "SKILL.md")))
        self.assertIn(".session/", read(os.path.join(work, ".git", "info", "exclude")))
        hook = json.loads(read(os.path.join(trusted, "settings.json")))
        self.assertIn("remaining.py", hook["hooks"]["PostToolUse"][0]["hooks"][0]["command"])

    def test_the_audit_gets_the_streams_of_the_last_iterations(self):
        self.hub.artifacts = [{"id": 1, "name": "stream-0001-3", "workflow_run": {"id": 101}},
                              {"id": 2, "name": "stream-0004-9", "workflow_run": {"id": 104}},
                              {"id": 3, "name": "stream-0005-11", "workflow_run": {"id": 105}},
                              {"id": 4, "name": "stream-0005-12", "workflow_run": {"id": 106}, "expired": True}]
        n = self.hub.create_issue("A", "## Goal\nmeasure\n", labels=["role:audit", "loop:iteration 5"])
        work = self.work()
        loop.stage(n, work, os.path.join(self.w.tmp, "t"))
        got = [a for a in self.hub.gh_calls if a[:2] == ("run", "download")]
        self.assertEqual([(a[2], a[4]) for a in got], [("104", "stream-0004-9"), ("105", "stream-0005-11")])
        self.assertTrue(all(a[6].startswith(os.path.join(work, ".session", "streams")) for a in got))

    def test_a_script_role_gets_its_command_and_no_skill(self):
        n = self.issue("rule")
        trusted = os.path.join(self.w.tmp, "trusted")
        loop.stage(n, self.work(), trusted)
        self.assertEqual(read(os.path.join(trusted, "command.sh")), "echo shaping\n")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "skills", "rule")))

    def test_the_policymaker_gets_its_prompt_and_its_own_state_and_the_shaper_the_iterations(self):
        it.start_iteration()
        shaper = self.hub.role_issues("orchestrator")[0]["number"]
        work = self.work()
        loop.stage(shaper, work, os.path.join(self.w.tmp, "t1"))
        state = json.loads(read(os.path.join(work, ".session", "loop-state.json")))
        self.assertEqual((state["iteration"], state["engine_version"]), (0, c.engine_version()))
        self.assertFalse(os.path.exists(os.path.join(work, ".session", "macro-plan.md")))
        pol = it.start_policymaker(0, "a test")
        work2 = self.work()
        loop.stage(pol, work2, os.path.join(self.w.tmp, "t3"))
        self.assertIn("You are the Policymaker", read(os.path.join(work2, ".session", "policymaker.md")))
        state = json.loads(read(os.path.join(work2, ".session", "loop-state.json")))
        self.assertEqual(state["trigger"], "a test")


class ManagedResourcesRegressionTest(SessionCase):
    work = StageTest.work

    def test_layered_role_admission_receipt_and_rollback_stay_consistent(self):
        baseline = c.head()
        initial_revision = resources.revision()
        fakes.sh(self.w.loop, "git", "tag", "setup-baseline", baseline)
        self.assertEqual(it.policy_rev(), initial_revision)

        n = self.issue()
        loop.resolve(n)
        self.assertEqual(self.outputs()["tools"], "Read,Grep,Glob,Write,Edit,Skill")
        work = self.work()
        loop.stage(n, work, os.path.join(self.w.tmp, "trusted-regression"))
        self.assertEqual(read(os.path.join(work, ".session", "role", "SKILL.md")),
                         "---\nname: worker\ndescription: fixture\n---\nx\n")
        self.w.branch(n, {"execution/roles/worker/SKILL.md": "forbidden"})
        self.assertEqual(loop.merge_branch(n, "worker", self.hub.issue(n))["result"], "scope")

        pol = self.issue("policymaker")
        replacement = "---\nname: worker\ndescription: adapted\n---\nnew instructions\n"
        self.w.branch(pol, {"execution/roles/worker/SKILL.md": replacement})
        accepted = loop.merge_branch(pol, "policymaker", self.hub.issue(pol), boundary=True)
        self.assertEqual((accepted["result"], accepted["verdict"]), ("merged", None))
        receipt = accepted["change"]
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(receipt["policy_rev_before"], initial_revision)
        self.assertEqual(receipt["changed_paths"], ["execution/roles/worker/SKILL.md"])
        self.assertEqual(receipt["policy_rev_after"],
                         resources.revision("main", self.w.origin))
        c.sync_main()
        self.assertEqual(it.policy_rev(), receipt["policy_rev_after"])
        work = self.work()
        loop.stage(n, work, os.path.join(self.w.tmp, "trusted-adapted"))
        self.assertEqual(read(os.path.join(work, ".session", "role", "SKILL.md")), replacement)

        self.w.push_main({"src/kept.txt": "product", "journal/kept.md": "report"})
        self.assertTrue(it.revert_policy("layered regression"))
        self.assertEqual(it.policy_rev(), initial_revision)
        self.assertEqual(self.w.origin_file("src/kept.txt"), "product")
        self.assertEqual(self.w.origin_file("journal/kept.md"), "report")
        self.assertEqual(json.loads(self.w.origin_file("iterations/changes/change-%s.json" % pol)), receipt)
        self.assertFalse(it.revert_policy("already restored"))


class EpilogueTest(SessionCase):
    def limit_result(self):
        return {"type": "result", "is_error": True, "result": "You've hit your session limit · resets 7:20am (UTC)"}

    def test_a_subscription_limit_parks_the_session_and_starts_the_clock(self):
        n = self.issue()
        self.w.session(n, report=False, exit_code="1", result=self.limit_result())
        f = self.hub.report(n)
        self.assertEqual((f["status"], f["attempts"]), ("paused", "1"))
        self.assertIn("resume_after", f)
        self.assertIn("blocked:rate-limit", self.hub.issues[n]["_labels"])
        self.assertEqual(self.hub.issues[n]["state"], "open")
        self.assertIn(("resume.yml", {"issue": n}), self.hub.dispatched)

    def test_resume_sleeps_to_the_date_and_launches(self):
        n = self.issue()
        self.w.session(n, report=False, exit_code="1", result=self.limit_result())
        c.update_report(n, {"resume_after": c.iso(c.now() + timedelta(seconds=90))})
        slept = []
        saved = loop.time
        loop.time = types.SimpleNamespace(sleep=slept.append, time=saved.time)
        self.hub.finish_runs()
        c.LAUNCHED.clear()
        try:
            loop.resume(n)
        finally:
            loop.time = saved
        self.assertEqual(len(slept), 1)
        self.assertTrue(0 < slept[0] <= 90)
        self.assertEqual(self.hub.dispatched[-1], ("session.yml", {"issue": n, "role": "worker"}))

    def test_the_watchdog_resumes_a_park_that_is_due(self):
        n = self.issue()
        self.w.session(n, report=False, exit_code="1", result=self.limit_result())
        c.update_report(n, {"resume_after": "2020-01-01T00:00:00Z"})
        self.hub.finish_runs()
        c.LAUNCHED.clear()
        loop.watch_parked()
        self.assertEqual(self.hub.dispatched[-1][1]["issue"], n)

    def test_the_parked_time_is_accounted_for_on_the_next_run(self):
        n = self.issue()
        self.w.session(n, report=False, exit_code="1", result=self.limit_result())
        c.update_report(n, {"paused_at": "2020-01-01T00:00:00Z"})
        self.w.session(n, files={"src/a.txt": "A"})
        self.assertGreater(int(self.hub.report(n)["parked_s"]), 10**8)

    def park_again(self, n, **fields):
        c.update_report(n, fields)
        self.w.session(n, report=False, exit_code="1", result=self.limit_result())
        return self.hub.report(n)

    def outages(self):
        return [x for x in self.hub.issues.values() if x.get("title", "").startswith("OUTAGE:")]

    def test_k_resumes_that_use_the_agent_without_progress_open_an_outage(self):
        n = self.issue()
        loop.resolve(n)
        for _ in range(3):                  # the original run, then two resumes
            self.assertEqual(self.park_again(n)["status"], "paused")
        self.assertEqual(self.outages(), [])
        self.assertEqual(self.park_again(n)["status"], "crashed")      # the third resume
        self.assertEqual(self.hub.issues[n]["state"], "closed")
        self.assertEqual(len(self.outages()), 1)

    def test_waiting_for_a_reset_without_using_the_agent_does_not_trip_the_breaker(self):
        n = self.issue()
        loop.resolve(n)
        with patch.object(loop, "read_int", lambda path: None):
            for _ in range(5):
                f = self.park_again(n, resume_after="2020-01-01T00:00:00Z")
        self.assertEqual((f["status"], f["attempts"], f["idle_resumes"]), ("paused", "5", "0"))
        self.assertEqual(self.outages(), [])

    def test_the_original_run_parking_is_not_a_resume(self):
        n = self.issue()
        loop.resolve(n)
        self.assertEqual(self.park_again(n)["idle_resumes"], "0")
        self.assertEqual(self.park_again(n)["idle_resumes"], "1")

    def test_turns_are_progress(self):
        n = self.issue()
        loop.resolve(n)
        self.park_again(n)
        result = dict(self.limit_result(), num_turns=3)
        c.update_report(n, {"idle_resumes": 2})
        self.w.session(n, report=False, exit_code="1", result=result)
        self.assertEqual(self.hub.report(n)["idle_resumes"], "0")

    def test_progress_between_resumes_resets_the_breaker(self):
        n = self.issue()
        loop.resolve(n)
        self.park_again(n)
        f = self.park_again(n, idle_resumes=2, head="0" * 40)      # the branch head moved since the last park
        self.assertEqual((f["status"], f["idle_resumes"]), ("paused", "0"))

    def test_parking_commits_nothing_and_the_final_state_is_frozen_once(self):
        n = self.issue()
        loop.resolve(n)
        count = lambda: subprocess.run(["git", "rev-list", "--count", "main"], cwd=self.w.origin,
                                       capture_output=True, text=True).stdout
        before = count()
        self.park_again(n)
        self.park_again(n)
        self.assertEqual(count(), before)
        self.assertEqual(reports.source_records(n), [])
        self.w.session(n, files={"src/a.txt": "A"})
        self.assertEqual(len(reports.source_records(n)), 1)

    def test_a_death_without_a_report_is_a_crash_with_its_cause(self):
        n = self.issue()
        self.w.session(n, report=False, exit_code="124", result={"type": "result", "is_error": False})
        f = self.hub.report(n)
        self.assertEqual((f["status"], f["cause"]), ("crashed", "exit=124"))

    def test_usage_timing_and_labels_are_recorded_by_the_engine(self):
        n = self.issue()
        self.w.session(n, files={"src/a.txt": "A"})
        f = self.hub.report(n)
        self.assertEqual((f["tokens_in"], f["tokens_out"], f["cache_read"], f["cache_write"], f["turns"]), ("100", "50", "10", "5", "7"))
        self.assertEqual((f["model"], f["effort"], f["branch"], f["iteration"]), ("m1", "high", "task/%s" % n, "1"))
        self.assertTrue(25 <= int(f["agent_s"]) <= 35)
        self.assertTrue(int(f["total_s"]) >= 45)
        self.assertEqual(json.loads(f["models"])["m1"]["out"], 50)

    def test_an_agent_cannot_forge_what_the_engine_owns(self):
        n = self.issue()
        self.w.session(n, files={"src/a.txt": "BAD"}, yaml="verdict: accepted\nmerged: true\nimproved: true\n")
        f = self.hub.report(n)
        self.assertEqual((f["verdict"], f["merged"]), ("rejected", "false"))

    def test_a_failed_verification_makes_the_work_partial(self):
        n = self.issue(body="## Goal\nx\n## Verification\n`exit 3`\n")
        self.w.session(n, files={"src/a.txt": "A"})
        f = self.hub.report(n)
        self.assertEqual((f["status"], f["verification"]), ("partial", "fail"))
        self.assertIsNone(self.w.origin_file("src/a.txt"))
        self.assertTrue(self.w.origin_has_ref("refs/heads/task/%s" % n))

    def test_a_passing_verification_runs_in_the_candidate_tree(self):
        n = self.issue(body="## Goal\nx\n## Verification\n`test -f src/a.txt`\n")
        self.w.session(n, files={"src/a.txt": "A"})
        self.assertEqual(self.hub.report(n)["status"], "done")

    def test_a_refused_merge_ends_blocked(self):
        n = self.issue()
        self.w.session(n, files={".github/workflows/x.yml": "x"})
        self.assertEqual(self.hub.report(n)["status"], "blocked")
        self.assertEqual(self.hub.report(n)["merged"], "false")
        self.assertIn("protected paths", self.hub.prose(n))
        self.assertFalse(self.w.origin_has_ref("refs/heads/task/%s" % n))        # GitHub would refuse the push of a workflow file anyway

    def test_a_conflict_runs_the_same_issue_again_up_to_three_times(self):
        self.w.push_main({"src/a.txt": "base\n"}, "base")
        n = self.issue()
        rounds = []
        for k in (1, 2, 3):
            c.sync_main()                  # the staged baseline must be in the engine checkout
            self.w.before_finalize = lambda k=k: self.w.push_main({"src/a.txt": "theirs %s\n" % k}, "theirs")
            self.w.session(n, files={"src/a.txt": "mine %s\n" % k})
            rounds.append((self.hub.issues[n]["state"], self.hub.report(n)["conflicts"], self.hub.report(n)["status"]))
        self.assertEqual(rounds, [("open", "1", "partial"), ("open", "2", "partial"), ("closed", "3", "crashed")])
        self.assertIn("conflict", self.hub.issues[n]["_labels"] | {"conflict"})
        self.assertEqual([i["issue"] for w, i in self.hub.dispatched if w == "session.yml"].count(n), 2)

    def test_unfinished_work_does_not_merge_but_leaves_its_journal(self):
        n = self.issue("orchestrator")
        self.w.session(n, status="blocked", files={"journal/0001.md": "state"}, report_extra="## What next\n- x\n")
        self.assertEqual(self.w.origin_file("journal/0001.md"), "state")

    def test_a_successor_continues_from_the_branch_and_inherits_the_edges(self):
        n = self.issue(labels=["model:opus"])
        after = self.issue("worker", title="After")
        self.hub.add_blocker(after, n)
        self.w.session(n, status="partial", files={"src/a.txt": "half"}, report_extra="## What next\n- go on\n\n## Successor\ntitle: Part two\nbody: finish it\n")
        succ = max(self.hub.issues)
        s = self.hub.issues[succ]
        self.assertEqual(s["title"], "Part two")
        self.assertIn("generation:1", s["_labels"])
        self.assertIn("model:opus", s["_labels"])
        self.assertIn("<!-- start-branch: task/%s -->" % n, s["body"])
        self.assertIn((after, succ), self.hub.deps)                                  # what waited for the parent waits for the successor
        self.assertEqual(self.hub.issues[after]["state"], "open")
        self.assertEqual(self.hub.issues[n]["state"], "closed")
        self.assertEqual(self.hub.dispatched[-1][1]["issue"], succ)

    def test_the_third_generation_does_not_delegate(self):
        n = self.issue(labels=["generation:3"])
        self.w.session(n, status="partial", report_extra="## What next\n- x\n\n## Successor\ntitle: More\nbody: x\n")
        self.assertNotIn("More", [x["title"] for x in self.hub.issues.values()])

    def test_a_producer_that_did_not_deliver_stops_its_consumers(self):
        a, b, d = self.issue(), self.issue(title="B"), self.issue(title="D")
        self.hub.add_blocker(b, a)
        self.hub.add_blocker(d, b)
        self.w.session(a, status="blocked", report_extra="## What next\n- x\n")
        self.assertEqual(self.hub.report(b)["status"], "blocked")
        self.assertEqual(self.hub.report(d)["status"], "blocked")                    # the cascade
        self.assertEqual(self.hub.issues[d]["state"], "closed")

    def test_a_script_role_is_judged_by_its_exit_code(self):
        n = self.issue("rule")
        self.w.session(n, report=False, exit_code="0", result={})
        self.assertEqual(self.hub.report(n)["status"], "done")
        m = self.issue("rule")
        self.w.session(m, report=False, exit_code="3", result={})
        self.assertEqual((self.hub.report(m)["status"], self.hub.report(m)["cause"]), ("crashed", "exit=3"))

    def test_large_outputs_become_release_assets_with_a_manifest(self):
        n = self.issue()
        self.w.session(n, files={"src/a.txt": "A"}, session_files={"artifacts/weights.bin": "binary", "artifacts/bad name.bin": "x"})
        self.assertTrue(any(a[:2] == ("release", "upload") and a[3].endswith("%s-weights.bin" % n) for a in self.hub.gh_calls))
        self.assertFalse(any("bad name" in " ".join(a) for a in self.hub.gh_calls))
        self.assertIn("- artifact: weights.bin | release: iter-0001/%s-weights.bin | size: 6 |" % n, self.hub.prose(n))
        self.assertEqual(self.hub.report(n)["artifacts"], "1")

    def test_inputs_of_a_task_are_downloaded_by_the_engine_not_the_agent(self):
        n = self.issue()
        self.w.session(n, files={"src/a.txt": "A"}, session_files={"artifacts/weights.bin": "binary"})
        m = self.issue(body='## Goal\nx\n<!-- inputs: %s -->\n' % json.dumps([{"issue": n, "name": "weights.bin"}]))
        d = os.path.join(self.w.tmp, "inputs")
        loop.fetch_inputs(self.hub.issues[m]["body"], d)
        self.assertTrue(any(a[:2] == ("release", "download") and a[2] == "iter-0001" and "%s-weights.bin" % n in a for a in self.hub.gh_calls))

    def test_the_branch_of_work_that_did_not_merge_is_tagged(self):
        n = self.issue()
        self.w.session(n, files={"src/a.txt": "BAD"})
        self.assertTrue(self.w.origin_has_ref("refs/tags/iter/0001/%s" % n))


if __name__ == "__main__":
    unittest.main()
