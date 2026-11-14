"""Session summary: the run's Summary block the epilogue writes from the session report, the stream and the startup package."""
import json
import os
import tempfile
import unittest
from unittest import mock

import fakes  # noqa: F401  (puts the loop directory on the path)
import session_summary as summary

REPORT = """<!-- session-report -->
```yaml
turns: 3
peak_context: 900
tokens_in: 10
tokens_out: 50
cache_read: 300
cache_write: 90
model: claude-x
effort: low
agent_s: 75
commit: abc1234
status: done
```
Did the thing.
"""


def assistant(mid, blocks, tokens=100):
    return {"type": "assistant", "timestamp": "2026-01-01T00:00:%02dZ" % len(mid),
            "message": {"id": mid, "content": blocks, "usage": {"input_tokens": tokens, "cache_read_input_tokens": 0}}}


def use(i, name, **inp):
    return {"type": "tool_use", "id": i, "name": name, "input": inp}


STREAM = [{"type": "system", "subtype": "init", "cwd": "/w"},
          assistant("m1", [{"type": "thinking", "thinking": ""}, use("1", "Read", file_path="/w/docs/a.md")]),
          {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "1", "content": "SECRET RESULT"}]}},
          assistant("m22", [{"type": "text", "text": "a | b"}, use("2", "Edit", file_path="/w/src/b.py"),
                            use("3", "Write", file_path="/w/.session/report.md")], tokens=500),
          assistant("m333", [{"type": "text", "text": "Done."}], tokens=900)]


class SessionSummaryTest(unittest.TestCase):
    def render(self, stream=STREAM, startup=("docs/a.md",)):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r"}):
            return summary.render(7, "worker", REPORT, stream, set(startup))

    def test_header_tables_timeline_files_result_in_that_order(self):
        text = self.render()
        heads = ["## ✅ worker #7: done", "| claude-x | low | 3 | 1m 15s | 900 |", "| 10 | 50 | 300 | 90 | **75%** |",
                 "### Session timeline", "### Files", "### Result"]
        self.assertEqual([text.index(h) for h in heads], sorted(text.index(h) for h in heads))
        self.assertTrue(text.rstrip().endswith("Did the thing."))

    def test_timeline_rows_count_turns_like_the_report_and_show_context_once_per_call(self):
        text = self.render()
        rows = text[text.index("|--:|--:|--:|---|"):text.index("### Files")].strip().splitlines()[1:]
        self.assertEqual([r.split("|")[1].strip() for r in rows], ["1", "1", "2", "2", "3", "4"])
        self.assertEqual([r.split("|")[3].strip() for r in rows], ["100", "", "500", "", "", "900"])
        self.assertIn("🧠 thinking · <50 tokens", rows[0])
        self.assertIn("💬 a \| b", rows[2])                                 # a pipe does not break the table
        self.assertNotIn("SECRET RESULT", self.render())                    # tool results stay out of the Summary

    def test_files_are_a_tree_with_markers_and_links_to_the_diff(self):
        text = self.render()
        self.assertIn("- 📁 `docs/`\n  - `a.md` — 📌 read at start · [file](https://github.com/o/r/blob/abc1234/docs/a.md)", text)
        self.assertIn("`b.py` — ✏️ changed · [diff](https://github.com/o/r/commit/abc1234#diff-", text)
        self.assertIn("`report.md` — ✏️ changed (not kept)", text)
        self.assertIn("📖 read later", self.render(startup=()))

    def test_thinking_text_unfolds_when_the_model_gives_it(self):
        stream = [assistant("m1", [{"type": "thinking", "thinking": "because"}, {"type": "text", "text": "ok"}])]
        self.assertIn("<details><summary>🧠 thinking · ~1 tokens</summary><i>because</i></details>", self.render(stream))

    def test_a_session_without_a_stream_still_has_its_header_and_result(self):
        text = self.render(stream=[])
        self.assertNotIn("### Session timeline", text)
        self.assertIn("Did the thing.", text)

    def test_it_fits_the_step_summary_limit(self):
        long = [assistant("m%d" % k, [{"type": "text", "text": "y" * 50000}]) for k in range(40)]
        text = self.render(stream=long)
        self.assertLessEqual(len(text.encode("utf-8")), summary.LIMIT + 1024)
        self.assertIn("Did the thing.", text)

    def test_writing_never_fails_the_epilogue(self):
        out = tempfile.mkdtemp()
        path = os.path.join(out, "summary.md")
        with open(os.path.join(out, "stream.ndjson"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(e) for e in STREAM) + "\nnot json\n")
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": path}), \
                mock.patch.object(summary.c, "find_report", return_value={"body": REPORT}):
            summary.write(7, "worker", out, out)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("### Session timeline", fh.read())
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": path}), \
                mock.patch.object(summary.c, "find_report", side_effect=RuntimeError("api down")):
            summary.write(7, "worker", out, out)                             # no exception


if __name__ == "__main__":
    unittest.main()
