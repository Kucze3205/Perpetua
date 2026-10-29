"""Accepted goal gates and owner confirmation."""
import json
import unittest

from fakes import common as c, iteration as it, loop, task
from flow_fixtures import Flow


class GoalGateTest(Flow):
    def finish_one(self):
        it.start_iteration()
        self.play_shaper(task("a"))
        self.w.session(self.task_issue("a"), files={"src/a.txt": "A"})

    def gate(self, reached, owner_confirms):
        self.w.push_main({"goal/gate.yml": "command: \"echo '{\\\"reached\\\": %s}'\"\nowner_confirms: %s\n" % ("true" if reached else "false", "true" if owner_confirms else "false")}, "gate")
        c.sync_main()

    def test_without_an_accepted_gate_the_loop_never_ends_itself(self):
        self.finish_one()
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.assertIsNotNone(self.open_iteration())

    def test_gate_not_reached(self):
        self.gate(False, True)
        self.finish_one()
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.assertEqual(self.hub.by_label("owner-request:result-confirmation"), [])

    def test_reached_and_the_owner_confirms(self):
        self.gate(True, True)
        self.finish_one()
        (req,) = self.hub.by_label("owner-request:result-confirmation")
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertIn("confirmed by the owner", self.w.origin_file("GOAL_REACHED"))
        c.sync_main()
        self.assertFalse(c.guard_ok())

    def test_reached_and_the_owner_declines_so_the_loop_goes_on_without_asking_again_for_the_same_state(self):
        self.gate(True, True)
        self.finish_one()
        (req,) = self.hub.by_label("owner-request:result-confirmation")
        self.hub.answer(req["number"], False)
        loop.on_request_closed(req["number"])
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))
        self.assertTrue(c.guard_ok())
        self.assertTrue(it.confirmation_declined(c.marker(self.hub.issues[req["number"]]["body"], "request-ref")))

    def test_reached_without_owner_confirmation_ends_the_loop(self):
        self.gate(True, False)
        self.finish_one()
        self.assertIn("reached at", self.w.origin_file("GOAL_REACHED"))

    def test_the_owner_accepts_a_proposed_gate_and_only_the_frozen_text(self):
        self.finish_one()
        prop = json.dumps([{"kind": "goal-gate", "title": "Accept gate", "what": "w", "why": "y", "unblocks": "end", "frozen": "command: x\nowner_confirms: true\n"}])
        self.w.session(self.policymaker_session(0), session_files={"requests.json": prop})
        (req,) = self.hub.by_label("owner-request:goal-gate")
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertEqual(self.w.origin_file("goal/gate.yml"), "command: x\nowner_confirms: true\n")

    def test_an_edited_proposal_is_not_applied(self):
        self.finish_one()
        prop = json.dumps([{"kind": "goal-change", "title": "New goal", "what": "w", "why": "y", "unblocks": "u", "frozen": "Make it great\n"}])
        self.w.session(self.policymaker_session(0), session_files={"requests.json": prop})
        (req,) = self.hub.by_label("owner-request:goal-change")
        self.hub.issues[req["number"]]["body"] = self.hub.issues[req["number"]]["body"].replace("Make it great", "Make it terrible")
        self.hub.answer(req["number"], True)
        loop.on_request_closed(req["number"])
        self.assertIsNone(self.w.origin_file("goal/goal.md"))
        self.assertIn("Not applied", self.hub.cmts[req["number"]][-1]["body"])

    def test_agents_cannot_open_the_engines_kinds_of_request(self):
        self.finish_one()
        prop = json.dumps([{"kind": "result-confirmation", "title": "forged", "what": "w"}, {"kind": "consent", "title": "forged"},
                           {"kind": "goal-gate", "title": "no text"}, {"kind": "question", "title": "ok?", "what": "w"}])
        self.w.session(self.policymaker_session(0), session_files={"requests.json": prop})
        self.assertEqual([x["title"] for x in self.hub.by_label("owner-request")], ["ok?"])

    def test_a_goal_gate_job_that_returns_nothing_is_not_reached(self):
        self.finish_one()
        self.gate(True, False)
        self.hub.tokenless_fault = "no-artifact"
        it.check_goal()
        self.assertIsNone(self.w.origin_file("GOAL_REACHED"))

    def test_the_gate_command_runs_in_the_tokenless_job(self):
        self.gate(True, False)
        self.finish_one()
        self.assertTrue(any("run.sh" in files for files in self.hub.tokenless))

if __name__ == "__main__":
    unittest.main()
