#!/usr/bin/env python3
"""Live view of a session: stdin = NDJSON from `claude --output-format stream-json --verbose`.
The step log is public: no tool_result, no thinking. Never fails and always drains stdin (a dead filter would
SIGPIPE the agent). Writes $1/stream.ndjson (the raw stream, kept as an artifact) and $1/claude-execution-output.json
(only the `result` event, because the epilogue parses that file as one JSON object)."""
import json
import os
import subprocess
import sys


def show(ev):
    if ev.get("type") != "assistant":
        return
    for b in ev.get("message", {}).get("content", []):
        if b.get("type") == "text":
            print("💬 " + b["text"].strip()[:300], flush=True)
        elif b.get("type") == "tool_use":
            i = b.get("input", {})
            arg = i.get("command") or i.get("file_path") or i.get("pattern") or i.get("path") or json.dumps(i, ensure_ascii=False)
            print("🔧 %s %s" % (b.get("name"), str(arg).replace("\n", " ")[:150]), flush=True)


def context_fill(ev):
    """Tokens in the context of one model call: what it read (fresh, from cache, written to cache)."""
    if ev.get("type") != "assistant":
        return 0
    u = ev.get("message", {}).get("usage") or {}
    return sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def end_session():
    # SIGTERM to `timeout`, which passes it to claude; ending the filter alone would kill claude only at its next write
    subprocess.run(["pkill", "-TERM", "-f", "claude -p"], check=False)


def limit_rejected(ev):
    """CLAUDE_CODE_RETRY_WATCHDOG retries a subscription-limit 429 too: without this the session hangs until `timeout`
    and the epilogue sees no limit (there is no `result`). The `rejected` event is the only signal; it carries the reset time."""
    info = ev.get("rate_limit_info") or {}
    if ev.get("type") != "rate_limit_event" or info.get("status") != "rejected" or info.get("isUsingOverage"):
        return None
    return {"type": "result", "is_error": True, "terminal_reason": "rate_limit",
            "result": "hit your %s limit (rate_limit rejected)" % info.get("rateLimitType", ""), "resetsAt": info.get("resetsAt")}


def main(out):
    result, peak = None, 0
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # the log shows what the agent says in any locale
    with open(os.path.join(out, "stream.ndjson"), "w") as raw:
        for line in sys.stdin:
            raw.write(line)
            try:
                ev = json.loads(line)
                peak = max(peak, context_fill(ev))
                rejected = limit_rejected(ev) if result is None else None
                if rejected:
                    result = rejected
                    print("⛔ subscription limit: ending the session", flush=True)
                    end_session()
                elif ev.get("type") == "result":
                    result = ev
                    print("✅ turns=%s error=%s" % (ev.get("num_turns"), ev.get("is_error")), flush=True)
                else:
                    show(ev)
            except Exception:
                pass
    if result is not None:
        result["peak_context"] = peak            # the fullest context of the session: are sessions entering the "dumb zone"
        with open(os.path.join(out, "claude-execution-output.json"), "w") as fh:
            json.dump(result, fh)


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RUNNER_TEMP", "/tmp"))
    except BaseException:
        for _ in sys.stdin:
            pass
    sys.exit(0)
