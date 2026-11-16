"""Session summary: one block in the run's Summary, written by the epilogue from data that already exists: the session
report (its fields and prose), the raw stream (`stream.ndjson`) and the startup package (`.session/startup.json`).
From the top: a header, two tables for the whole session, the checks of its results (report fields `verification`,
`check`, `verdict`, `detail`), the Session timeline (`Turn | At | Context | What`, what the
model says between the tool rows, no tool results), the project tree of files the session read and changed, the result."""
import hashlib
import json
import os
from datetime import datetime

import common as c
from stream_filter import ICON, arg, short, thinking_label

LIMIT = 900 * 1024             # GitHub takes 1 MiB per step summary; the epilogue's own lines share it
STATUS = {"done": "✅", "partial": "🟡", "blocked": "⛔", "rejected": "⛔", "crashed": "❌", "paused": "⏸️"}
MARK = {"start": "📌 read at start", "later": "📖 read later", "changed": "✏️ changed"}
WRITES = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def load_stream(path):
    events = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return [e for e in events if isinstance(e, dict)]


def stamp(e):
    try:
        return datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, AttributeError, ValueError):
        return None


def steps(events):
    """One dict per thing the model did: `think`, `say` or `use` (a tool call). `n` is the turn as the report counts
    `turns`: every tool call is a turn and so is a closing message; words or thinking before a call belong to its turn."""
    cwd = next((e.get("cwd") for e in events if e.get("type") == "system" and e.get("subtype") == "init"), "") or ""
    t0 = next((t for t in map(stamp, events) if t), None)
    out, tokens = [], None
    for e in events:
        if e.get("type") == "system" and e.get("subtype") == "thinking_tokens":
            tokens = e.get("estimated_tokens")
        if e.get("type") != "assistant":
            continue
        m = e.get("message") or {}
        t = stamp(e)
        u = m.get("usage") or {}
        base = {"at": round(t - t0) if t and t0 else None, "mid": m.get("id"),
                "ctx": sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
                "calls": any(b.get("type") == "tool_use" for b in m.get("content") or [])}
        for b in m.get("content") or []:
            if b.get("type") == "thinking":
                out.append(dict(base, k="think", text=(b.get("thinking") or "").strip(), tokens=tokens))
                tokens = None
            elif b.get("type") == "text" and b.get("text", "").strip():
                out.append(dict(base, k="say", text=b["text"].strip()))
            elif b.get("type") == "tool_use":
                i = b.get("input") if isinstance(b.get("input"), dict) else {}
                path = i.get("file_path") or i.get("notebook_path")
                out.append(dict(base, k="use", tool=b.get("name", "?"), arg=arg(i, cwd),
                                path=path[len(cwd) + 1:] if cwd and isinstance(path, str) and path.startswith(cwd + "/") else path))
    n = 0
    for s in out:
        if s["k"] == "use" or (s["k"] == "say" and not s["calls"]):
            n += 1
            s["n"] = n
        else:
            s["n"] = n + 1
    return out


def fmt(x):
    return "{:,}".format(int(x or 0)).replace(",", " ")


def duration(seconds):
    s = int(seconds or 0)
    return "%dm %02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s


def cell(text, cap=None):
    """Text inside one table cell: pipes escaped, line breaks as <br>."""
    text = text if cap is None or len(text) <= cap else text[:cap - 1] + "…"
    return text.replace("|", "\\|").replace("\n", "<br>")


def tables(f):
    tin, out, cr, cw = (int(f.get(k) or 0) for k in ("tokens_in", "tokens_out", "cache_read", "cache_write"))
    hit = 100.0 * cr / (tin + cr + cw) if tin + cr + cw else 0.0
    return ["| Model | Effort | Turns | Time | Peak context |", "|---|---|---:|---:|---:|",
            "| %s | %s | %s | %s | %s |" % (f.get("model", ""), f.get("effort", ""), f.get("turns", ""), duration(f.get("agent_s")),
                                           fmt(f.get("peak_context"))),
            "", "| Tokens in | Tokens out | Cache read | Cache write | Cache hit |", "|---:|---:|---:|---:|---:|",
            "| %s | %s | %s | %s | **%.0f%%** |" % (fmt(tin), fmt(out), fmt(cr), fmt(cw), hit)]


def checks(f):
    """What the checks of the results said: `## Verification`, the `check` command, the change gate. None ran: nothing."""
    gate = f.get("verdict")
    rows = [("Verification", f.get("verification"), "" if f.get("verification") != "fail" else "a command failed (see the result)"),
            ("Check command", f.get("check"), "" if f.get("check") != "fail" else "it failed after the rebase (see the result)"),
            ("Change gate", gate, (f.get("detail") or "") if gate else "")]
    rows = [r for r in rows if r[1]]
    if not rows:
        return []
    icon = {"pass": "✅", "accepted": "✅", "fail": "❌", "rejected": "❌", "inconclusive": "🟡"}
    return ["### Checks", "", "| Check | Result | Reason |", "|---|---|---|"] + [
        "| %s | %s %s | %s |" % (name, icon.get(value, "•"), value, cell(why, 300)) for name, value, why in rows] + [""]


def timeline(st, cap=None):
    rows, seen = ["### Session timeline", "", "| Turn | At | Context | What |", "|--:|--:|--:|---|"], set()
    for s in st:
        if s["k"] == "think":
            label = thinking_label(s["tokens"], s["text"])
            what = "<details><summary>%s</summary><i>%s</i></details>" % (label, cell(s["text"], cap)) if s["text"] else label
        elif s["k"] == "say":
            what = "💬 " + cell(s["text"], cap)
        else:
            what = "%s **%s** `%s`" % (ICON.get(s["tool"], "🔧"), s["tool"], cell(short(s["arg"], 90)).replace("`", "'"))
        ctx = ""
        if s["mid"] not in seen:             # the context of a model call, shown once per call
            seen.add(s["mid"])
            ctx = fmt(s["ctx"])
        rows.append("| %d | %s | %s | %s |" % (s["n"], "+%ss" % s["at"] if s["at"] is not None else "", ctx, what))
    return rows


def touched(st, startup):
    """{project path: marker}; one marker per file, changed beats read."""
    marks = {}
    for s in st:
        p = s.get("path")
        if s["k"] != "use" or not isinstance(p, str) or p.startswith("/"):
            continue
        if s["tool"] in WRITES:
            marks[p] = "changed"
        elif s["tool"] == "Read" and marks.get(p) != "changed":
            marks[p] = "start" if p in startup else "later"
    return marks


def tree(marks, sha):
    """Nested bullet tree; a kept file links to itself, a changed one also to its diff in the session's commit."""
    repo, root, out = os.environ.get("GITHUB_REPOSITORY", c.REPO), {}, []
    for p, m in sorted(marks.items()):
        node = root
        for d in p.split("/")[:-1]:
            node = node.setdefault(d + "/", {})
        node[p.split("/")[-1]] = (p, m)

    def walk(node, depth):
        for k in sorted(node, key=lambda x: (not x.endswith("/"), x)):
            v = node[k]
            if isinstance(v, dict):
                out.append("%s- 📁 `%s`" % ("  " * depth, k))
                walk(v, depth + 1)
                continue
            p, m = v
            tail = MARK[m]
            if p.startswith(".session/"):            # the handover to the engine never reaches the history
                tail += " (not kept)" if m == "changed" else ""
            elif sha:
                blob = "https://github.com/%s/blob/%s/%s" % (repo, sha, p)
                if m == "changed":
                    tail += " · [diff](https://github.com/%s/commit/%s#diff-%s)" % (repo, sha, hashlib.sha256(p.encode()).hexdigest())
                tail += " · [file](%s)" % blob
            out.append("%s- `%s` — %s" % ("  " * depth, k, tail))
    walk(root, 0)
    return out


def render(n, role, report, events, startup):
    f = c.fields(report)
    m = c.YAML_BLOCK.search(report)
    prose = report[m.end():].strip() if m else ""
    status = f.get("status", "")
    head = ["## %s %s #%s: %s" % (STATUS.get(status, "•"), role, n, status), ""]
    if f.get("model"):
        head += tables(f) + [""]
    head += checks(f)
    st = steps(events)
    marks = touched(st, startup)
    tail = (["", "### Files", "", "📌 read at start · 📖 read later · ✏️ changed", ""] + tree(marks, f.get("commit")) if marks else []) \
        + ["", "### Result", "", prose or "_No report._", ""]
    for cap in (None, 2000, 300):                   # too long for the Summary: shorten what the model says, then cut rows
        body = timeline(st, cap) if st else []
        text = "\n".join(head + body + tail)
        if len(text.encode("utf-8")) <= LIMIT:
            return text
    while body and len("\n".join(head + body + tail).encode("utf-8")) > LIMIT:
        body = body[:-max(1, len(body) // 10)]
    return "\n".join(head + body + ["", "_The rest of the timeline is in the `stream` artifact._"] + tail)


def write(n, role, work, tmp):
    """Append the Session summary to the step summary. A view only: it never fails the epilogue."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        r = c.find_report(n)
        try:
            startup = {d["path"] for d in json.loads(c.safe_read(work, ".session/startup.json") or "{}").get("documents", [])}
        except (ValueError, TypeError, KeyError, AttributeError):
            startup = set()
        text = render(n, role, r["body"] if r else "", load_stream(os.path.join(tmp, "stream.ndjson")), startup)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception as error:
        print("Session summary not written: %s" % error)
