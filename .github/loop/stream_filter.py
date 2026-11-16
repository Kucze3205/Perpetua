#!/usr/bin/env python3
"""Session timeline, live: stdin = NDJSON from `claude --output-format stream-json --verbose`, shown in the job log.
What the agent says stays on top; each tool call is one flat `::group::` with its argument in the title and, inside,
its time, the content it writes and its result. GitHub masks secrets in the log. Never fails and always drains stdin
(a dead filter would SIGPIPE the agent). Writes $1/stream.ndjson (the raw stream, kept as an artifact) and
$1/claude-execution-output.json (only the `result` event, because the epilogue parses that file as one JSON object)."""
import json
import os
import subprocess
import sys
import time

ICON = {"Read": "📖", "Write": "✏️", "Edit": "✏️", "MultiEdit": "✏️", "NotebookEdit": "✏️", "Bash": "▶️", "Glob": "🔎", "Grep": "🔎"}
CONTENT_LINES, RESULT_LINES, LINE_CHARS = 25, 30, 300


def rel(path, cwd):
    """A path of the session's checkout as a project path."""
    return path[len(cwd) + 1:] if cwd and isinstance(path, str) and path.startswith(cwd + "/") else path


def arg(tool_input, cwd=""):
    """The one argument that says what a tool call does: its file, command or pattern."""
    i = tool_input if isinstance(tool_input, dict) else {}
    a = rel(i.get("file_path") or i.get("notebook_path"), cwd) or i.get("command") or i.get("pattern") or i.get("path")
    return str(a if a else json.dumps(i, ensure_ascii=False)).replace("\n", " ⏎ ")


def short(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def result_text(content):
    return content if isinstance(content, str) else "\n".join(
        x.get("text", "") for x in content or [] if isinstance(x, dict))


def thinking_label(tokens, text):
    """`🧠 thinking · ~N tokens`: the stream's estimate; from the text when there is one; `<50` when the stream has none."""
    tk = tokens or (len(text) // 4 if text else None)
    return "🧠 thinking · %s tokens" % ("~%d" % tk if tk else "<50")


class Live:
    """The live Session timeline. A call's group stays open until its result comes, unless the next call comes first."""

    def __init__(self):
        self.cwd, self.t0, self.calls, self.open, self.turn, self.thinking = os.environ.get("WORK", ""), time.time(), {}, None, 0, None

    def out(self, line=""):
        print(line, flush=True)

    def lines(self, text, n, indent="    "):
        rows = str(text).splitlines()
        for l in rows[:n]:
            self.out(indent + l[:LINE_CHARS])
        if len(rows) > n:
            self.out("%s… %d more lines (whole in the stream artifact)" % (indent, len(rows) - n))

    def close(self):
        if self.open:
            self.out("::endgroup::")
            self.open = None

    def show(self, ev):
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            self.cwd = ev.get("cwd") or self.cwd
        elif kind == "system" and ev.get("subtype") == "thinking_tokens":
            self.thinking = ev.get("estimated_tokens")
        elif kind == "assistant":
            for b in ev.get("message", {}).get("content", []):
                self.block(b)
        elif kind == "user":
            content = ev.get("message", {}).get("content")
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self.result(b)

    def block(self, b):
        if b.get("type") == "text" and b.get("text", "").strip():
            self.close()
            self.out("💬 " + b["text"].strip())
        elif b.get("type") == "thinking":
            self.close()
            text = (b.get("thinking") or "").strip()
            label = thinking_label(self.thinking, text)
            self.thinking = None
            if text:
                self.out("::group::" + label)
                self.lines(text, 10**6, "")
                self.out("::endgroup::")
            else:
                self.out(label)
        elif b.get("type") == "tool_use":
            self.close()
            self.turn += 1
            name, i = b.get("name", "?"), b.get("input") or {}
            title = "%s %s %s" % (ICON.get(name, "🔧"), name, short(arg(i, self.cwd), 120))
            self.calls[b.get("id")] = title
            self.out("::group::" + title)
            self.out("at +%ds, turn %d" % (time.time() - self.t0, self.turn))
            for k in ("content", "old_string", "new_string"):
                if isinstance(i.get(k), str):
                    self.out("  ┌ " + k)
                    self.lines(i[k], CONTENT_LINES, "  │ ")
                    self.out("  └")
            self.open = b.get("id")

    def result(self, b):
        title = self.calls.pop(b.get("tool_use_id"), None)
        if title is None:
            return
        if self.open != b.get("tool_use_id"):
            self.close()
            self.out("::group::↩ " + title)
            self.open = b.get("tool_use_id")
        self.out("  ┌ result" + (" (error)" if b.get("is_error") else ""))
        self.lines(result_text(b.get("content")), RESULT_LINES, "  │ ")
        self.out("  └")
        self.close()


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
    result, peak, live = None, 0, Live()
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
                    live.close()
                    print("⛔ subscription limit: ending the session", flush=True)
                    end_session()
                elif ev.get("type") == "result":
                    result = ev
                    live.close()
                    print("✅ turns=%s error=%s" % (ev.get("num_turns"), ev.get("is_error")), flush=True)
                else:
                    live.show(ev)
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
