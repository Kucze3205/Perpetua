"""Owner acceptance and independent evaluator verification."""
import unittest

from fakes import common as c, iteration as it, loop, task, tasks_json
from gate_fixtures import GATE_SH
from flow_fixtures import Flow


class OwnerGateTest(Flow):
    YML = ""

    def start_with_one_task(self):
        it.start_iteration()
        self.play_shaper(task("a"))
        return self.task_issue("a")

    def test_the_owner_says_yes(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        self.assertEqual(self.hub.issues[t]["state"], "open")
        self.assertIn("awaiting:owner", c.label_names(self.hub.issues[t] | {"labels": list(self.hub.issues[t]["_labels"])}))
        self.assertEqual(self.hub.report(t)["merged"], "pending-owner")
        self.assertIsNone(self.w.origin_file("src/a.txt"))
        (req,) = [x for x in self.hub.by_label("owner-request:merge-acceptance")]
        self.assertEqual(self.hub.issues[req["number"]]["assignees"], ["ow"])
        self.assertTrue(self.w.origin_has_ref("refs/heads/task/%s" % t))           # the session can still come back
        self.assertIsNone(self.record(0))                                          # the iteration waits
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertEqual(self.w.origin_file("src/a.txt"), "A")
        self.assertEqual(self.hub.issues[t]["state"], "closed")
        self.assertFalse(self.w.origin_has_ref("refs/heads/task/%s" % t))
        self.assertEqual(self.hub.report(t)["merged"], "true")
        self.assertEqual(self.record(0)["status"], "closed")
        self.assertTrue(self.record(0)["outcome"]["verdict"]["improved"])

    def test_the_owner_says_no_and_the_branch_stays_reachable(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        (req,) = self.hub.by_label("owner-request:merge-acceptance")
        self.hub.answer(req["number"], False)
        loop.on_request_closed(req["number"])
        self.assertIsNone(self.w.origin_file("src/a.txt"))
        self.assertEqual(self.hub.report(t)["status"], "rejected")
        self.assertTrue(self.w.origin_has_ref("refs/tags/iter/0000/%s" % t))
        self.assertFalse(self.w.origin_has_ref("refs/heads/task/%s" % t))               # closed for good: only the tag remains
        self.assertFalse(self.record(0)["outcome"]["verdict"]["improved"])

    def test_a_request_is_handled_once(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        (req,) = self.hub.by_label("owner-request:merge-acceptance")
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        loop.on_request_closed(req["number"])
        self.assertIn("request:handled", self.hub.issues[req["number"]]["_labels"])

    def test_the_watchdog_sweeps_an_answer_whose_event_was_lost(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        (req,) = self.hub.by_label("owner-request:merge-acceptance")
        self.hub.answer(req["number"], True)
        loop.sweep_requests()
        self.assertEqual(self.w.origin_file("src/a.txt"), "A")

    def test_waiting_for_the_owner_is_not_a_jam(self):
        t = self.start_with_one_task()
        self.w.session(t, files={"src/a.txt": "A"})
        before = len(self.hub.dispatched)
        self.hub.runs.clear()
        common_launched = c.LAUNCHED.copy()
        self.assertEqual(loop.drive(), 0)
        self.assertEqual(len(self.hub.dispatched), before)
        self.assertTrue(loop.commitments())
        self.assertEqual(common_launched, c.LAUNCHED)


class EvaluatorChangeTest(Flow):
    def start(self):
        """The Policymaker orders the evaluator role while no iteration is open: iteration 0 starts with the order."""
        pol = self.policymaker_session(0)
        self.w.session(pol, session_files={"orders.json": tasks_json(task("e", role="evaluator", title="Fix evaluator"))})
        self.play_shaper(task("a"))
        return self.task_issue("order-%s-e" % pol)

    def test_the_policymaker_orders_the_evaluator_role(self):
        e = self.start()
        self.assertIsNotNone(e)
        self.assertEqual(c.roles_of(c.label_names(self.hub.issue(e))), ["evaluator"])

    def test_the_shaper_cannot_order_it(self):
        it.start_iteration()
        s = self.session_issue("orchestrator")
        self.w.session(s, session_files={"tasks.json": tasks_json(task("e", role="evaluator"))})
        self.assertIn("ordered only by the Policymaker", self.hub.prose(s))

    def test_verified_change_takes_effect_and_the_old_one_stands_meanwhile(self):
        e = self.start()
        self.w.session(e, files={"policy/evaluator/gate.sh": "echo new\n"})
        self.assertEqual(self.w.origin_file("policy/evaluator/gate.sh"), GATE_SH)       # still the old one
        self.assertEqual(self.hub.report(e)["merged"], "pending-verifier")
        v = self.session_issue("evaluator-verifier")
        self.assertIn("<!-- verifies: %s -->" % e, self.hub.issues[v]["body"])
        self.w.session(v, report_extra="", session_files={})
        # the verifier's report has no `result: pass`: the change is sent back
        self.assertEqual(self.hub.report(e)["status"], "rejected")
        self.assertEqual(self.w.origin_file("policy/evaluator/gate.sh"), GATE_SH)

    def test_the_verifier_passes_it(self):
        e = self.start()
        self.w.session(e, files={"policy/evaluator/gate.sh": "echo new\n"})
        self.w.session(self.session_issue("evaluator-verifier"), yaml="result: pass\n")
        self.assertEqual(self.w.origin_file("policy/evaluator/gate.sh"), "echo new\n")
        self.assertEqual(self.hub.report(e)["merged"], "true")
        self.assertEqual(self.hub.issues[e]["state"], "closed")

    def test_a_verifier_that_cannot_start_sends_the_change_back(self):
        e = self.start()
        self.w.session(e, files={"policy/evaluator/gate.sh": "echo new\n"})
        profs = self.w.origin_file("policy/profiles.yml").replace("evaluator-verifier:\n  write: []\n  tools: [Read, Write]",
                                                                  "evaluator-verifier:\n  write: []\n  tools: [Read, 'Bash(rm:*)']")
        self.assertIn("Bash(rm:*)", profs)
        self.w.push_main({"policy/profiles.yml": profs}, "verifier beyond the limits")
        c.sync_main()
        with self.assertRaises(SystemExit):
            loop.resolve(self.session_issue("evaluator-verifier"))
        self.assertEqual(self.hub.report(e)["status"], "rejected")              # not left waiting for a verifier forever
        self.assertNotIn("awaiting:verifier", self.hub.issues[e]["_labels"])
        self.assertEqual(self.w.origin_file("policy/evaluator/gate.sh"), GATE_SH)

if __name__ == "__main__":
    unittest.main()
