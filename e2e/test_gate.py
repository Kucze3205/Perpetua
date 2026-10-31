"""The E2E change gate (fixtures/gate.py) on temporary trees."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import gate


def tree(root, files):
    for path, content in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(content, encoding="utf-8")
    return root


def record(n, improved=True, status="closed"):
    return json.dumps({"iteration": n, "status": status, "outcome": {"sessions": [{"merged": True, "improved": improved}]}})


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.n = 0

    def verdict(self, before, after):
        self.n += 1
        return gate.verdict(tree(self.tmp / ("c%d" % self.n), after), tree(self.tmp / ("b%d" % self.n), before))

    def test_greeting_improves(self):
        v = self.verdict({}, {"src/greeting.txt": "hello from perpetua\n"})
        self.assertEqual((v["verdict"], v["improved"]), ("accepted", True))

    def test_farewell_needs_a_closed_iteration_with_progress(self):
        base = {"src/greeting.txt": "hello from perpetua\n"}
        ok = dict(base, **{"iterations/0011.json": record(11)})
        self.assertEqual(self.verdict(ok, dict(ok, **{"src/farewell.txt": "goodbye after iteration 11\n"}))["improved"], True)
        self.assertEqual(self.verdict(base, dict(base, **{"src/farewell.txt": "goodbye after iteration 11\n"}))["verdict"], "rejected")
        idle = dict(base, **{"iterations/0011.json": record(11, improved=False)})
        self.assertEqual(self.verdict(idle, dict(idle, **{"src/farewell.txt": "goodbye after iteration 11"}))["verdict"], "rejected")

    def test_records_come_from_the_accepted_tree(self):
        base = {"src/greeting.txt": "hello from perpetua\n"}
        forged = dict(base, **{"iterations/0011.json": record(11), "src/farewell.txt": "goodbye after iteration 11\n"})
        self.assertEqual(self.verdict(base, forged)["verdict"], "rejected")

    def test_nothing_left_is_no_progress(self):
        done = {"src/greeting.txt": "hello from perpetua\n", "src/farewell.txt": "goodbye after iteration 11\n",
                "iterations/0011.json": record(11)}
        v = self.verdict(done, dict(done, **{"src/other.txt": "x"}))
        self.assertEqual((v["verdict"], v["improved"]), ("accepted", False))

    def test_wrong_or_removed_file_is_rejected(self):
        self.assertEqual(self.verdict({}, {"src/greeting.txt": "hi"})["verdict"], "rejected")
        self.assertEqual(self.verdict({"src/greeting.txt": "hello from perpetua"}, {})["verdict"], "rejected")


if __name__ == "__main__":
    unittest.main()
