"""The small scripts that run beside the agent: the live filter of the stream and the remaining-time hook."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import fakes
from fakes import LOOP_DIR, loop


def run_filter(events):
    out = tempfile.mkdtemp()
    r = subprocess.run([sys.executable, os.path.join(LOOP_DIR, "stream_filter.py"), out],
                       input="\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n",
                       capture_output=True, text=True, encoding="utf-8")
    path = os.path.join(out, "claude-execution-output.json")
    result = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            result = json.load(fh)
    return r, out, result


def assistant(tokens, text="hi", tool=None):
    content = [{"type": "text", "text": text}] + ([{"type": "tool_use", "name": "Bash", "input": {"command": tool}}] if tool else [])
    return {"type": "assistant", "message": {"content": content, "usage": {"input_tokens": tokens, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}}}


class StreamFilterTest(unittest.TestCase):
    def test_the_result_event_and_the_peak_of_the_context_are_kept(self):
        events = [assistant(100, tool="ls"), assistant(900), assistant(300), {"type": "result", "num_turns": 3, "is_error": False, "usage": {}}]
        r, out, result = run_filter(events)
        self.assertEqual(r.returncode, 0)
        self.assertEqual((result["num_turns"], result["peak_context"]), (3, 915))
        with open(os.path.join(out, "stream.ndjson"), encoding="utf-8") as fh:
            self.assertEqual(len(fh.readlines()), 4)                         # the raw stream is kept whole
        self.assertIn("Bash ls", r.stdout)

    def test_the_public_log_has_no_tool_results_and_no_thinking(self):
        events = [{"type": "user", "message": {"content": [{"type": "tool_result", "content": "SECRET OUTPUT"}]}},
                  {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "PRIVATE"}]}}, {"type": "result"}]
        r, _, _ = run_filter(events)
        self.assertNotIn("SECRET OUTPUT", r.stdout)
        self.assertNotIn("PRIVATE", r.stdout)

    def test_garbage_lines_do_not_stop_it(self):
        r, _, result = run_filter(["not json", assistant(1), "{broken", {"type": "result", "num_turns": 1}])
        self.assertEqual((r.returncode, result["num_turns"]), (0, 1))

    def test_no_result_no_file(self):
        self.assertIsNone(run_filter([assistant(1)])[2])

    def test_a_rejected_limit_becomes_a_result_the_epilogue_understands(self):
        ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": 1790000000}}
        r, out, result = run_filter([ev])
        self.assertEqual(result["terminal_reason"], "rate_limit")
        cause, limited, reset = loop.machine_cause(os.path.join(out, "claude-execution-output.json"), "143")
        self.assertTrue(limited)
        self.assertEqual(int(reset.timestamp()), 1790000000)

    def test_an_allowed_or_overage_limit_event_is_not_a_stop(self):
        ok = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}
        over = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "isUsingOverage": True}}
        self.assertIsNone(run_filter([ok, over])[2])

    def test_the_peak_reaches_the_report_fields(self):
        r, out, result = run_filter([assistant(500), {"type": "result", "num_turns": 1}])
        self.assertEqual(loop.usage_fields(os.path.join(out, "claude-execution-output.json"))["peak_context"], 515)


class RemainingHookTest(unittest.TestCase):
    def test_it_tells_the_agent_how_many_minutes_are_left(self):
        env = dict(os.environ, SESSION_END=str(int(time.time()) + 600 + 30))
        r = subprocess.run([sys.executable, os.path.join(LOOP_DIR, "remaining.py")], capture_output=True, text=True, env=env)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertEqual(out["additionalContext"], "10 min of the session left.")

    def test_never_negative(self):
        env = dict(os.environ, SESSION_END=str(int(time.time()) - 600))
        r = subprocess.run([sys.executable, os.path.join(LOOP_DIR, "remaining.py")], capture_output=True, text=True, env=env)
        self.assertIn("0 min", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
