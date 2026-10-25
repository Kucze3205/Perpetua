"""Pure logic: reports, globs, causes of failure, task graphs, limits. No network, no git."""
import json
import os
import tempfile
import unittest

import fakes
from fakes import common as c, iteration as it, loop

REPORT = c.MARK + "\n```yaml\nstatus: done\ncommit: 9f3c1ab\n```\nProse.\n\n## What next\n- nothing\n"


class ReportTest(unittest.TestCase):
    def test_fields_reads_scalars(self):
        self.assertEqual(c.fields(REPORT), {"status": "done", "commit": "9f3c1ab"})

    def test_fields_cuts_an_inline_comment(self):
        self.assertEqual(c.fields("```yaml\nresult: pass   # or fail\n```")["result"], "pass")

    def test_set_fields_overwrites_adds_and_deletes(self):
        b = c.set_fields(REPORT, {"status": "partial", "attempts": 2, "commit": None})
        self.assertEqual(c.fields(b), {"status": "partial", "attempts": "2"})
        self.assertIn("## What next", b)
        self.assertTrue(b.startswith(c.MARK))

    def test_set_fields_makes_the_block_when_missing(self):
        b = c.set_fields("Prose only.\n", {"status": "crashed"})
        self.assertEqual(c.fields(b), {"status": "crashed"})
        self.assertIn("Prose only.", b)

    def test_section_cuts_to_the_next_one(self):
        body = "## Goal\nx\n\n## Verification\n`make test`\n\n## Context\ny\n"
        self.assertEqual(c.section(body, "Verification"), "`make test`")
        self.assertEqual(c.section(body, "Budget"), "")

    def test_marker(self):
        self.assertEqual(c.marker("x\n<!-- limits: {\"a\": 1} -->\n", "limits"), '{"a": 1}')
        self.assertIsNone(c.marker("x", "limits"))

    def test_trust_in_the_author(self):
        self.assertTrue(c.trusted({"author_association": "OWNER", "user": {"login": "x"}}))
        self.assertTrue(c.trusted({"author_association": "NONE", "user": {"login": "github-actions[bot]"}}))
        self.assertFalse(c.trusted({"author_association": "CONTRIBUTOR", "user": {"login": "stranger"}}))

    def test_set_output_keeps_a_value_on_one_line(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            path = fh.name
        os.environ["GITHUB_OUTPUT"] = path
        try:
            c.set_output(tools="a,b\nevil=1")
            with open(path) as fh:
                self.assertEqual(fh.read(), "tools=a,b evil=1\n")
        finally:
            os.environ.pop("GITHUB_OUTPUT")
            os.unlink(path)


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.w = fakes.World()
        self.n = self.w.hub.create_issue("t", "b", labels=["role:worker"])

    def tearDown(self):
        self.w.close()

    def test_engine_fields_survive_and_cannot_be_forged(self):
        c.update_report(self.n, {"verdict": "accepted", "attempts": 2})
        c.publish(self.n, c.MARK + "\n```yaml\nstatus: done\nverdict: forged\nmerged: true\n```\nx\n")
        f = self.w.hub.report(self.n)
        self.assertEqual(f["verdict"], "accepted")
        self.assertEqual(f["attempts"], "2")
        self.assertEqual(f["status"], "done")
        self.assertNotIn("merged", f)

    def test_one_marked_comment_only(self):
        c.publish(self.n, "first")
        c.publish(self.n, "second")
        self.assertEqual(len(self.w.hub.cmts[self.n]), 1)
        self.assertIn("second", self.w.hub.cmts[self.n][0]["body"])


class SafeReadTest(unittest.TestCase):
    def test_refuses_symlinks_and_escapes(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".session"))
        secret = os.path.join(tempfile.mkdtemp(), "secret")
        for path, text in ((secret, "token"), (os.path.join(d, ".session", "ok.md"), "fine")):
            with open(path, "w") as fh:
                fh.write(text)
        self.assertEqual(c.safe_read(d, ".session/ok.md"), "fine")
        self.assertIsNone(c.safe_read(d, ".session/missing.md"))
        try:
            os.symlink(secret, os.path.join(d, ".session", "link.md"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        self.assertIsNone(c.safe_read(d, ".session/link.md"))


class GlobTest(unittest.TestCase):
    def test_patterns(self):
        self.assertTrue(c.path_match("journal/0001.md", ["journal/**"]))
        self.assertTrue(c.path_match("journal/a/b.md", ["journal/"]))
        self.assertTrue(c.path_match("engine.yml", ["engine.yml"]))
        self.assertFalse(c.path_match("src/engine.yml", ["engine.yml"]))
        self.assertTrue(c.path_match("a/b/c.py", ["**/c.py"]))
        self.assertTrue(c.path_match("c.py", ["**/c.py"]))
        self.assertTrue(c.path_match("src/x.py", ["src/*.py"]))
        self.assertFalse(c.path_match("src/a/x.py", ["src/*.py"]))
        self.assertFalse(c.path_match("x", []))


class CauseTest(unittest.TestCase):
    def cause(self, payload, exit_code="0"):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            fh.write(payload if isinstance(payload, str) else json.dumps(payload))
        try:
            return loop.machine_cause(fh.name, exit_code)
        finally:
            os.unlink(fh.name)

    def test_auth_401_despite_subtype_success(self):
        cause, limited, _ = self.cause({"subtype": "success", "is_error": True, "api_error_status": 401, "terminal_reason": "api_error"})
        self.assertEqual(cause, "api_error_status=401 terminal_reason=api_error")
        self.assertFalse(limited)

    def test_subscription_limit(self):
        self.assertTrue(self.cause({"is_error": True, "result": "You've hit your session limit"})[1])

    def test_reset_in_seconds_and_in_milliseconds(self):
        self.assertEqual(self.cause('{"resetsAt": 1790000000}')[2], self.cause('{"resets_at": 1790000000000}')[2])

    def test_reset_from_the_cli_text(self):
        ref = c.parse_time("2026-09-26T06:24:22Z")
        self.assertEqual(loop.text_reset("hit your session limit · resets 7:20am (UTC)", ref), c.parse_time("2026-09-26T07:20:00Z"))
        self.assertEqual(loop.text_reset("resets 5am (UTC)", ref), c.parse_time("2026-09-27T05:00:00Z"))
        self.assertEqual(loop.text_reset("resets 3pm (Europe/Warsaw)", ref), c.parse_time("2026-09-26T13:00:00Z"))
        self.assertEqual(loop.text_reset("resets Oct 2, 5am (UTC)", ref), c.parse_time("2026-10-02T05:00:00Z"))
        self.assertIsNone(loop.text_reset("no date", ref))

    def test_plain_failure(self):
        self.assertEqual(self.cause({"is_error": False, "result": "ok"}, exit_code="124"), ("exit=124", False, None))

    def test_no_file(self):
        cause, limited, _ = loop.machine_cause("/no/such.json", "1")
        self.assertIn("no-execution-file", cause)
        self.assertFalse(limited)

    def test_usage_from_the_model_breakdown(self):
        got = {"num_turns": 4, "usage": {"input_tokens": 1, "output_tokens": 1},
               "modelUsage": {"a": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 3, "cacheCreationInputTokens": 2},
                              "b": {"inputTokens": 1, "outputTokens": 1}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(got, fh)
        try:
            u = loop.usage_fields(fh.name)
        finally:
            os.unlink(fh.name)
        self.assertEqual((u["tokens_in"], u["tokens_out"], u["cache_read"], u["cache_write"], u["turns"]), (11, 6, 3, 2, 4))
        self.assertEqual(json.loads(u["models"])["a"]["in"], 10)

    def test_usage_without_a_file(self):
        self.assertIsNone(loop.usage_fields("/no/such.json")["turns"])


class RunIssueTest(unittest.TestCase):
    def test_run_issue_cuts_the_role(self):
        self.assertEqual(c.run_issue("session #12 · worker"), "session #12")
        self.assertNotEqual(c.run_issue("session #123"), "session #12")


class TaskGraphTest(unittest.TestCase):
    def setUp(self):
        self.w = fakes.worker_world()

    def tearDown(self):
        self.w.close()

    def errs(self, *tasks, **kw):
        return it.validate_tasks({"tasks": list(tasks)}, **kw)

    def test_a_valid_graph(self):
        self.assertEqual(self.errs(fakes.task("a"), fakes.task("b", blocked_by=["a"], model="opus", effort="high")), [])

    def test_needs_tasks(self):
        self.assertTrue(it.validate_tasks({"tasks": []}))
        self.assertTrue(it.validate_tasks([]))

    def test_unknown_role_duplicate_and_fields(self):
        self.assertTrue(self.errs(fakes.task("a", role="nobody")))
        self.assertTrue(self.errs(fakes.task("a"), fakes.task("a")))
        self.assertTrue(self.errs(fakes.task("a", width=3)))
        self.assertTrue(self.errs(fakes.task("a", role="policymaker")))

    def test_unknown_alias_effort_and_edge(self):
        self.assertTrue(self.errs(fakes.task("a", model="gpt")))
        self.assertTrue(self.errs(fakes.task("a", effort="max")))
        self.assertTrue(self.errs(fakes.task("a", blocked_by=["zzz"])))

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
        self.assertEqual(it.width(ts), 2)

    def test_order_ids_are_prefixed_with_the_policymaker_session_and_their_edges_follow(self):
        it.store_state(7, {"tasks": [fakes.task("e", role="evaluator"), fakes.task("f", role="audit", blocked_by=["e"])]}, it.ORDERS_MARK)
        orders = it.read_orders(7)
        self.assertEqual([o["id"] for o in orders], ["order-7-e", "order-7-f"])
        self.assertEqual(orders[1]["blocked_by"], ["order-7-e"])


class LimitsTest(unittest.TestCase):
    def setUp(self):
        self.w = fakes.World(engine_yml="limits:\n  tools: [Read, Write, 'Bash(git:*)']\n  internet_write: ['docs/']\n")

    def tearDown(self):
        self.w.close()

    def test_within(self):
        self.assertEqual(it.limits_exceeded({"tools": ["Read", "Bash(git:*)"], "internet": True, "write": ["docs/research/**"]}), [])

    def test_tool_beyond(self):
        self.assertEqual(len(it.limits_exceeded({"tools": ["Read", "Bash(rm:*)"]})), 1)

    def test_internet_together_with_writing_code(self):
        self.assertEqual(len(it.limits_exceeded({"internet": True, "write": ["src/**"]})), 1)
        self.assertEqual(it.limits_exceeded({"internet": False, "write": ["src/**"]}), [])


class RequestTextTest(unittest.TestCase):
    def test_fence_is_longer_than_any_backtick_run(self):
        self.assertEqual(it.fence_for("a ``` b"), "````")
        self.assertEqual(it.fence_for("plain"), "```")

    def test_frozen_text_survives_a_round_trip_and_detects_edits(self):
        w = fakes.World()
        try:
            n = it.open_request("goal-gate", "t", "w", "y", "u", frozen="command: x\nowner_confirms: true ``` odd\n")
            body = w.hub.issues[n]["body"]
            self.assertEqual(it.frozen_text(body), "command: x\nowner_confirms: true ``` odd\n")
            self.assertIsNone(it.frozen_text(body.replace("command: x", "command: y")))
            self.assertIn("@ow", body)
            self.assertEqual(w.hub.issues[n]["assignees"], ["ow"])
        finally:
            w.close()

    def test_frontmatter(self):
        fm, body = it.frontmatter("---\naim: x\nconstraints: [a]\n---\nrest\n")
        self.assertEqual((fm["aim"], fm["constraints"], body), ("x", ["a"], "rest\n"))
        self.assertEqual(it.frontmatter("no front")[0], {})


class VerdictTest(unittest.TestCase):
    def test_last_json_line_wins(self):
        self.assertEqual(it.parse_verdict('log\n{"verdict": "x"}\nmore\n{"verdict": "accepted"}\n'), {"verdict": "accepted"})
        self.assertIsNone(it.parse_verdict("no json here"))
        self.assertIsNone(it.parse_verdict("{broken"))


if __name__ == "__main__":
    unittest.main()
