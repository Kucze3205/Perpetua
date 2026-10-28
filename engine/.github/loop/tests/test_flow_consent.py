"""Owner consent and per-session approved secrets."""
import os
import unittest
from unittest import mock

import fakes
from fakes import common as c, iteration as it, loop, task
import owner_requests as requests
from flow_fixtures import Flow


POOL_YML = """jobs:
  session:
    steps:
      - name: Other
        env:
          NOT_A_SECRET: x
      - name: Session
        env:
          CLAUDE_CODE_OAUTH_TOKEN: t
          %s
"""


class ConsentTest(Flow):
    def setUp(self):
        super().setUp()
        self.w.push_main({"src/.keep": ""}, "x")

    def start(self):
        it.start_iteration()
        self.play_shaper(task("n", role="net", consent={"what": "Search the web once", "why": "need a fact"}), task("w"))
        return self.task_issue("n")

    def test_a_task_beyond_the_limits_waits_for_the_owner(self):
        n = self.start()
        launched = {int(i["issue"]) for _, i in self.hub.dispatched}
        self.assertNotIn(n, launched)
        (req,) = self.hub.by_label("owner-request:consent")
        self.assertIn("(Search the web once)" if False else "Search the web once", self.hub.issues[req["number"]]["body"])
        self.assertIn((n, req["number"]), self.hub.deps)
        self.assertIn("consent:%s" % req["number"], self.hub.issues[n]["_labels"])
        self.assertIn(self.task_issue("w"), launched)                              # the rest goes on

    def test_consent_granted_starts_the_session_and_resolve_agrees(self):
        n = self.start()
        (req,) = self.hub.by_label("owner-request:consent")
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertIn(n, {int(i["issue"]) for _, i in self.hub.dispatched})
        out = os.path.join(self.w.tmp, "out")
        os.environ["GITHUB_OUTPUT"] = out
        loop.resolve(n)
        with open(out) as fh:
            text = fh.read()
        self.assertIn("consented=true", text)
        self.assertIn("kind=agent", text)

    def test_resolve_refuses_without_the_consent(self):
        n = self.start()
        with self.assertRaises(SystemExit) as e:
            loop.resolve(n)
        self.assertIn("beyond the accepted limits", str(e.exception))
        self.assertEqual(self.hub.report(n)["status"], "blocked")

    def test_consent_declined_closes_the_task_as_blocked(self):
        n = self.start()
        (req,) = self.hub.by_label("owner-request:consent")
        self.hub.answer(req["number"], False)
        loop.on_request_closed(req["number"])
        self.assertEqual(self.hub.issues[n]["state"], "closed")
        self.assertEqual(self.hub.report(n)["status"], "blocked")


class ConsentSecretsTest(Flow):
    def setUp(self):
        super().setUp()
        self.w.push_main({"src/.keep": "", ".github/workflows/session.yml": POOL_YML % "STRIPE_KEY: x\n          SMTP_PASS: y"}, "x")
        c.sync_main()
        it.start_iteration()
        self.play_shaper(task("a", consent={"secrets": ["STRIPE_KEY"]}), task("b", consent={"secrets": ["SMTP_PASS"]}), task("c"))

    def request_of(self, tid):
        label = next(l for l in self.hub.issues[self.task_issue(tid)]["_labels"] if l.startswith("consent:"))
        return int(label.split(":")[1])

    def resolved(self, tid):
        out = os.path.join(self.w.tmp, "out-" + tid)
        os.environ["GITHUB_OUTPUT"] = out
        loop.resolve(self.task_issue(tid))
        with open(out) as fh:
            return dict(l.rstrip("\n").split("=", 1) for l in fh)

    def grant(self, tid):
        self.hub.answer(self.request_of(tid), True)
        loop.on_request_closed(self.request_of(tid))

    def test_names_enter_the_frozen_text_and_a_later_edit_is_not_applied(self):
        n = self.request_of("a")
        body = self.hub.issues[n]["body"]
        self.assertEqual(requests.frozen_text(body), "secrets: STRIPE_KEY")
        self.hub.issues[n]["body"] = body.replace("STRIPE_KEY", "OTHER_KEY")
        self.assertIsNone(requests.frozen_text(self.hub.issues[n]["body"]))
        self.assertEqual(requests.secrets_of_consent(self.hub.issues[self.task_issue("a")]["_labels"]), [])

    def test_two_sessions_get_their_own_names(self):
        self.grant("a")
        self.grant("b")
        self.assertEqual(self.resolved("a")["secrets"], "|STRIPE_KEY|")
        self.assertEqual(self.resolved("b")["secrets"], "|SMTP_PASS|")

    def test_a_task_within_the_limits_has_the_consent_it_was_granted(self):
        self.grant("a")
        o = self.resolved("a")
        self.assertEqual(o["consented"], "true")
        self.assertEqual(self.resolved("c")["consented"], "false")
        self.assertEqual(self.resolved("c")["secrets"], "")

    def staged(self, tid):
        work = os.path.join(self.w.tmp, "stagework-" + tid)
        fakes.sh(self.w.tmp, "git", "clone", "-q", self.w.origin, work)
        trusted = os.path.join(self.w.tmp, "trusted-" + tid)
        os.environ["RUNNER_TEMP"] = os.path.join(self.w.tmp, "run-" + tid)
        # the real stage; only the native-ELF executor preparation is left out (it needs Linux)
        with mock.patch.object(loop.executor_boundary, "prepare"):
            loop.stage(self.task_issue(tid), work, trusted)
        return work, trusted

    def test_stage_writes_the_approved_names_for_agent_sh(self):
        self.grant("a")
        _, trusted = self.staged("a")
        with open(os.path.join(trusted, "secrets")) as fh:
            self.assertEqual(fh.read(), "STRIPE_KEY\n")
        _, trusted = self.staged("c")
        with open(os.path.join(trusted, "secrets")) as fh:
            self.assertEqual(fh.read(), "")

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell: bash on Windows may be WSL")
    def test_an_empty_approved_secret_stops_a_really_staged_start(self):
        import subprocess
        self.grant("a")
        work, trusted = self.staged("a")
        out = os.environ["RUNNER_TEMP"]
        os.makedirs(out, exist_ok=True)
        env = dict(os.environ, WORK=work, KIND="agent", ISSUE="1", AGENT_TIMEOUT="1", STRIPE_KEY="")
        r = subprocess.run(["bash", os.path.join(trusted, "agent.sh")], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("No such file", r.stderr)
        with open(os.path.join(out, "secret-missing")) as fh:
            self.assertEqual(fh.read().strip(), "STRIPE_KEY")

    def test_the_pool_is_the_env_of_the_session_step(self):
        self.assertEqual(loop.secret_pool(), ["SMTP_PASS", "STRIPE_KEY"])

    def test_a_name_outside_the_pool_blocks_the_task_and_asks_for_the_capability(self):
        self.w.push_main({".github/workflows/session.yml": POOL_YML % "SMTP_PASS: y"}, "drop STRIPE_KEY from the pool")
        c.sync_main()
        self.grant("a")
        n = self.task_issue("a")
        with self.assertRaises(SystemExit) as e:
            self.resolved("a")
        self.assertEqual(str(e.exception), "secret-missing:STRIPE_KEY")
        self.assertEqual(self.hub.issues[n]["state"], "closed")
        self.assertEqual(self.hub.report(n)["status"], "blocked")
        self.assertEqual(self.hub.report(n)["cause"], "secret-missing:STRIPE_KEY")
        (cap,) = self.hub.by_label("owner-request:capability")
        self.assertNotIn("hunter2", self.hub.issues[cap["number"]]["body"])

    def test_a_refusal_stops_the_start_and_a_new_session_keeps_the_same_consent(self):
        self.grant("a")
        self.assertEqual(self.resolved("a")["secrets"], "|STRIPE_KEY|")
        self.assertEqual(self.resolved("a")["secrets"], "|STRIPE_KEY|")      # a session after a crash: the same frozen consent
        self.hub.answer(self.request_of("b"), False)
        loop.on_request_closed(self.request_of("b"))
        self.assertEqual(self.hub.report(self.task_issue("b"))["cause"], "consent-declined")
        with self.assertRaises(SystemExit):
            self.resolved("b")

    def test_the_epilogue_turns_the_missing_secret_marker_into_blocked_and_a_capability(self):
        self.grant("a")
        n = self.task_issue("a")
        tmp = os.path.join(self.w.tmp, "runner")
        os.makedirs(tmp)
        with open(os.path.join(tmp, "secret-missing"), "w") as fh:
            fh.write("STRIPE_KEY\n")
        os.environ["RUNNER_TEMP"] = tmp
        loop.finalize(n, os.path.join(self.w.tmp, "nowork"))
        self.assertEqual(self.hub.report(n)["cause"], "secret-missing:STRIPE_KEY")
        self.assertEqual(len(self.hub.by_label("owner-request:capability")), 1)

    def test_a_marker_for_a_name_without_consent_is_ignored(self):
        n = self.task_issue("c")
        tmp = os.path.join(self.w.tmp, "runner")
        os.makedirs(tmp)
        with open(os.path.join(tmp, "secret-missing"), "w") as fh:
            fh.write("STRIPE_KEY\n")
        os.environ["RUNNER_TEMP"] = tmp
        try:
            loop.finalize(n, os.path.join(self.w.tmp, "nowork"))
        except Exception:
            pass
        self.assertEqual(self.hub.by_label("owner-request:capability"), [])

    def test_declined_consent_gives_no_secrets(self):
        self.hub.answer(self.request_of("a"), False)
        loop.on_request_closed(self.request_of("a"))
        labels = self.hub.issues[self.task_issue("a")]["_labels"]
        self.assertEqual(requests.consent_status(c.profiles()["worker"], labels), (False, None))
        self.assertEqual(requests.secrets_of_consent(labels), [])

    def test_invalid_names_are_refused_without_echoing_them(self):
        for bad in (["lower"], ["A=hunter2"], ["A", "A"], "A", [1]):
            errors = requests.consent_errors({"secrets": bad})
            self.assertTrue(errors, bad)
            self.assertNotIn("hunter2", " ".join(errors))
        self.assertEqual(requests.consent_errors({"what": "w", "secrets": ["A_1", "B"]}), [])

if __name__ == "__main__":
    unittest.main()
