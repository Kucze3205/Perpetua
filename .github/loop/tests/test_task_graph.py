"""Task graph validation, dependencies, issue materialization and frozen storage."""
import subprocess
import sys
import unittest

import fakes
from fakes import common as c, iteration as it
import task_graph as graph


class TaskGraphTest(unittest.TestCase):
    def setUp(self):
        self.w = fakes.worker_world()

    def tearDown(self):
        self.w.close()

    def errs(self, *tasks, **kw):
        return graph.validate_tasks({"tasks": list(tasks)}, **kw)

    def test_a_valid_graph(self):
        self.assertEqual(self.errs(fakes.task("a"), fakes.task("b", blocked_by=["a"], model="opus", effort="high")), [])

    def test_needs_tasks(self):
        self.assertTrue(graph.validate_tasks({"tasks": []}))
        self.assertTrue(graph.validate_tasks([]))

    def test_unknown_role_duplicate_and_fields(self):
        self.assertTrue(self.errs(fakes.task("a", role="nobody")))
        self.assertTrue(self.errs(fakes.task("a"), fakes.task("a")))
        self.assertTrue(self.errs(fakes.task("a", width=3)))
        self.assertTrue(self.errs(fakes.task("a", role="policymaker")))

    def test_unknown_alias_effort_and_edge(self):
        self.assertTrue(self.errs(fakes.task("a", model="gpt")))
        self.assertTrue(self.errs(fakes.task("a", effort="max")))
        self.assertTrue(self.errs(fakes.task("a", blocked_by=["zzz"])))

    def test_limits_are_positive_integers(self):
        self.assertEqual(self.errs(fakes.task("a", limits={"timeout_minutes": 20, "max_turns": 30})), [])
        for limits in ({"timeout_minutes": "20"}, {"max_turns": "..."}, {"max_turns": 0}, {"max_turns": True},
                       {"width": 3}, ["max_turns"]):
            self.assertTrue(self.errs(fakes.task("a", limits=limits)), limits)

    def test_cycle(self):
        self.assertTrue(self.errs(fakes.task("a", blocked_by=["b"]), fakes.task("b", blocked_by=["a"])))

    def test_malformed_agent_json_returns_errors_instead_of_crashing(self):
        malformed = [None, "task", [], fakes.task([]), fakes.task({}), fakes.task("bad id"),
                     fakes.task("a", role=[]), fakes.task("a", model={"alias": "opus"}),
                     fakes.task("a", blocked_by=7), fakes.task("a", blocked_by="a"),
                     fakes.task("a", blocked_by=[[]])]
        for task in malformed:
            with self.subTest(task=task):
                self.assertTrue(self.errs(task))
                before = self.w.hub.list_issues("all")
                self.assertIn("invalid tasks.json:", it.instantiate(0, {"tasks": [task]}))
                self.assertEqual(self.w.hub.list_issues("all"), before)

    def test_forward_reference_is_valid(self):
        self.assertEqual(self.errs(fakes.task("b", blocked_by=["a"]), fakes.task("a")), [])

    def test_policymaker_only_roles_come_from_orders_and_orders_only_of_them(self):
        self.assertTrue(self.errs(fakes.task("a", role="evaluator")))
        self.assertEqual(self.errs(fakes.task("a", role="evaluator"), ordered=True), [])
        self.assertTrue(self.errs(fakes.task("a"), ordered=True))

    def test_an_id_the_iteration_already_uses_is_refused(self):
        self.assertTrue(self.errs(fakes.task("a"), taken={"a"}))

    def test_width_is_the_widest_layer(self):
        ts = [dict(fakes.task("a")), dict(fakes.task("b")), dict(fakes.task("c", blocked_by=["a", "b"])), dict(fakes.task("d", blocked_by=["c"]))]
        self.assertEqual(graph.width(ts), 2)

    def test_order_ids_are_prefixed_with_the_policymaker_session_and_their_edges_follow(self):
        it.store_state(7, {"tasks": [fakes.task("e", role="evaluator"), fakes.task("f", role="audit", blocked_by=["e"])]}, it.ORDERS_MARK)
        orders = it.read_orders(7)
        self.assertEqual([o["id"] for o in orders], ["order-7-e", "order-7-f"])
        self.assertEqual(orders[1]["blocked_by"], ["order-7-e"])


class GraphImportTest(unittest.TestCase):
    def test_graph_can_be_imported_without_loading_lifecycle_drivers(self):
        result = subprocess.run([sys.executable, "-c", "import sys; import task_graph; "
                                 "assert 'iteration' not in sys.modules; assert 'loop' not in sys.modules"],
                                cwd=fakes.LOOP_DIR, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
