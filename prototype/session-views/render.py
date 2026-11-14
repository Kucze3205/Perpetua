#!/usr/bin/env python3
"""PROTOTYPE (issue #335): throwaway renderer. Replays a REAL recorded session as
  - Session timeline, live log form:   render.py log <A|B|C>      -> stdout (GitHub job log, `::group::` lines)
  - Session summary (run Summary):     render.py summary <A|B|C>  -> stdout (GFM for $GITHUB_STEP_SUMMARY)
Inputs: data/stream.ndjson (raw claude stream), data/report.md (session-report comment: tokens, cache, time),
        data/startup.json (startup package documents), data/issue.json.
Nothing here is production code; the real thing is built in the "Session timeline and Session summary" implementation ticket."""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("GITHUB_REPOSITORY", "Kucze3205/Perpetua")
WORK = re.compile(r"^/home/runner/work/[^/]+/[^/]+/work/")          # the session's working copy on the runner: /home/runner/work/<repo>/<repo>/work/


def load():
    ev = [json.loads(l) for l in open(os.path.join(HERE, "data/stream.ndjson"), encoding="utf-8")]
    rep = open(os.path.join(HERE, "data/report.md"), encoding="utf-8").read()
    m = re.search(r"```yaml\n(.*?)```", rep, re.S)
    f = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        f[k.strip()] = v.strip()
    f["_body"] = rep[m.end():].strip()
    start = json.load(open(os.path.join(HERE, "data/startup.json"), encoding="utf-8"))
    issue = json.load(open(os.path.join(HERE, "data/issue.json"), encoding="utf-8"))
    return ev, f, start, issue


def rel(p):
    return WORK.sub("", p)


def ts(e):
    from datetime import datetime
    return datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp() if e.get("timestamp") else None


def steps(ev, think=False):
    """Normalised timeline: one dict per thing the agent said / ran, tool results paired to their calls. turn = model call."""
    t0 = next(ts(e) for e in ev if ts(e))
    out, calls, turn, last_msg, tok = [], {}, 0, None, None
    for e in ev:
        if e["type"] == "system" and e.get("subtype") == "thinking_tokens":
            tok = e.get("estimated_tokens")
        t = ts(e)
        at = round(t - t0, 1) if t else None
        if e["type"] == "assistant":
            mid = e["message"].get("id")
            if mid != last_msg:
                turn, last_msg = turn + 1, mid
            for b in e["message"]["content"]:
                if b["type"] == "thinking" and think:
                    out.append({"k": "think", "turn": turn, "at": at, "tokens": tok, "text": b.get("thinking") or ""})
                    tok = None
                if b["type"] == "text":
                    out.append({"k": "say", "turn": turn, "at": at, "text": b["text"].strip()})
                elif b["type"] == "tool_use":
                    i = b["input"]
                    s = {"k": "use", "turn": turn, "at": at, "tool": b["name"], "input": i, "result": None,
                         "path": rel(i["file_path"]) if "file_path" in i else None}
                    out.append(s)
                    calls[b["id"]] = s
        elif e["type"] == "user":
            for b in e["message"]["content"]:
                if b.get("type") == "tool_result" and b["tool_use_id"] in calls:
                    r = b["content"]
                    calls[b["tool_use_id"]]["result"] = r if isinstance(r, str) else "\n".join(x.get("text", "") for x in r)
                    calls[b["tool_use_id"]]["done"] = at
    return out


def arg(s):
    i = s["input"]
    a = s["path"] or i.get("command") or i.get("pattern") or json.dumps(i, ensure_ascii=False)
    return str(a).replace("\n", " ⏎ ")


def short(t, n):
    t = " ".join(t.split())
    return t if len(t) <= n else t[:n - 1] + "…"


ICON = {"Read": "📖", "Write": "✏️", "Edit": "✏️", "Bash": "▶️", "Glob": "🔎", "Grep": "🔎"}


# ------------------------------------------------------------------ files: the project tree with markers
def files(st, start):
    startup = {d["path"] for d in start["documents"]}
    info = {}
    for s in st:
        if s["k"] != "use" or not s["path"]:
            continue
        p = s["path"]
        if s["tool"] == "Read":
            info.setdefault(p, set()).add("start" if p in startup else "later")
        elif s["tool"] in ("Write", "Edit"):
            info.setdefault(p, set()).add("changed")
    return info


def marker(m):
    # one marker per file; changed beats read
    return "changed" if "changed" in m else ("start" if "start" in m else "later")


MARK = {"start": "📌 read at start", "later": "📖 read later", "changed": "✏️ changed"}


def link_file(p, sha):
    return "https://github.com/%s/blob/%s/%s" % (REPO, sha, p)


def link_diff(p, sha):
    return "https://github.com/%s/commit/%s#diff-%s" % (REPO, sha, hashlib.sha256(p.encode()).hexdigest())


def admitted(p):
    return not p.startswith(".session/")      # engine-internal handover files never reach the history


def tree_lines(info, sha, links=True):
    """Nested bullet tree, links on files."""
    root = {}
    for p, m in sorted(info.items()):
        node = root
        parts = p.split("/")
        for d in parts[:-1]:
            node = node.setdefault(d + "/", {})
        node[parts[-1]] = (p, marker(m))
    out = []

    def walk(n, depth):
        for k in sorted(n, key=lambda x: (not x.endswith("/"), x)):
            v = n[k]
            if isinstance(v, dict):
                out.append("%s- 📁 `%s`" % ("  " * depth, k))
                walk(v, depth + 1)
            else:
                p, mk = v
                if mk == "changed" and admitted(p) and links:
                    tail = "%s · [diff](%s) · [file](%s)" % (MARK[mk], link_diff(p, sha), link_file(p, sha))
                elif admitted(p) and links:
                    tail = "%s · [file](%s)" % (MARK[mk], link_file(p, sha))
                else:
                    tail = MARK[mk] + (" (not kept)" if mk == "changed" else "")
                out.append("%s- `%s` — %s" % ("  " * depth, k, tail))
    walk(root, 0)
    return out


def tree_ascii(info):
    root = {}
    for p, m in sorted(info.items()):
        node = root
        for d in p.split("/")[:-1]:
            node = node.setdefault(d + "/", {})
        node[p.split("/")[-1]] = marker(m)
    sym = {"start": "·", "later": "○", "changed": "●"}
    out = []

    def walk(n, pre):
        keys = sorted(n, key=lambda x: (not x.endswith("/"), x))
        for i, k in enumerate(keys):
            last = i == len(keys) - 1
            v = n[k]
            if isinstance(v, dict):
                out.append("%s%s%s" % (pre, "└─ " if last else "├─ ", k))
                walk(v, pre + ("   " if last else "│  "))
            else:
                out.append("%s%s%s  %s" % (pre, "└─ " if last else "├─ ", k, sym[v]))
    walk(root, "")
    return out


# ------------------------------------------------------------------ numbers (per session, not per file)
def numbers(f):
    n = lambda k: int(f.get(k) or 0)
    tin, out, cr, cw = n("tokens_in"), n("tokens_out"), n("cache_read"), n("cache_write")
    allin = tin + cr + cw
    return {"in": tin, "out": out, "cr": cr, "cw": cw, "hit": 100.0 * cr / allin if allin else 0.0}


def fmt(x):
    return "{:,}".format(x).replace(",", " ")


def header(issue, f):
    ok = {"done": "✅", "partial": "🟡", "blocked": "⛔", "rejected": "⛔"}.get(f.get("status"), "•")
    return "%s %s #%d: %s" % (ok, issue["role"], issue["number"], f.get("status"))


# ================================================================== LOG (the live job log, `::group::` is flat)
def log(variant):
    ev, f, start, issue = load()
    st = steps(ev)
    p = lambda s="": print(s)

    def result_lines(s, n=12):
        r = (s["result"] or "").splitlines()
        for l in r[:n]:
            p("    " + l[:220])
        if len(r) > n:
            p("    … %d more lines" % (len(r) - n))

    p("session %s #%d · %s · %s" % (issue["role"], issue["number"], f["model"], f["effort"]))
    if variant == "A":      # one group per model turn: a turn = what the model said + everything it then ran
        by = {}
        for s in st:
            by.setdefault(s["turn"], []).append(s)
        for t, items in by.items():
            say = [s for s in items if s["k"] == "say"]
            uses = [s for s in items if s["k"] == "use"]
            for s in say:
                p("💬 " + s["text"])
            if uses:
                title = "turn %d · +%ss · %s" % (t, uses[0]["at"], ", ".join(short("%s %s" % (u["tool"], arg(u)), 60) for u in uses))
                p("::group::" + title)
                for u in uses:
                    p("%s %s %s" % (ICON.get(u["tool"], "🔧"), u["tool"], arg(u)))
                    result_lines(u)
                p("::endgroup::")
    elif variant == "B":    # flat transcript, nothing collapsed: closest to reading Claude Code scroll back
        for s in st:
            if s["k"] == "say":
                p("[+%5.1fs] 💬 %s" % (s["at"], s["text"]))
            else:
                p("[+%5.1fs] %s %s %s" % (s["at"], ICON.get(s["tool"], "🔧"), s["tool"], arg(s)))
                result_lines(s, 6)
    else:                   # C: what the agent says stays open; every tool call is its own group with the result inside
        for s in st:
            if s["k"] == "say":
                p("💬 " + s["text"])
            else:
                p("::group::%s %s %s" % (ICON.get(s["tool"], "🔧"), s["tool"], short(arg(s), 90)))
                p("at +%ss, turn %d" % (s["at"], s["turn"]))
                if s["tool"] in ("Write", "Edit"):
                    for k in ("content", "new_string"):
                        if k in s["input"]:
                            p("  ┌ " + k)
                            for l in s["input"][k].splitlines()[:25]:
                                p("  │ " + l[:200])
                            p("  └")
                result_lines(s, 30)
                p("::endgroup::")
    p("✅ turns=%s · in %s · out %s · cache read %s · cache write %s" % (f["turns"], fmt(int(f["tokens_in"])), fmt(int(f["tokens_out"])), fmt(int(f["cache_read"])), fmt(int(f["cache_write"]))))


# ================================================================== SUMMARY (GFM, one-shot after the session step)
def stats_table(f):
    n = numbers(f)
    return [
        "| Model | Effort | Turns | Time | Peak context |",
        "|---|---|---:|---:|---:|",
        "| %s | %s | %s | %ss | %s |" % (f["model"], f["effort"], f["turns"], f["agent_s"], fmt(int(f["peak_context"]))),
        "",
        "| Tokens in | Tokens out | Cache read | Cache write | Cache hit |",
        "|---:|---:|---:|---:|---:|",
        "| %s | %s | %s | %s | **%.0f%%** |" % (fmt(n["in"]), fmt(n["out"]), fmt(n["cr"]), fmt(n["cw"]), n["hit"]),
    ]


def transcript(st, full=True):
    out = []
    for s in st:
        if s["k"] == "say":
            out += ["", "> 💬 " + s["text"].replace("\n", "\n> "), ""]
        else:
            out.append("- `+%ss` %s **%s** `%s`" % (s["at"], ICON.get(s["tool"], "🔧"), s["tool"], short(arg(s), 110)))
            if s["result"] and full:
                body = "\n".join(s["result"].splitlines()[:20])
                out += ["", "  <details><summary>result · %d lines</summary>" % len(s["result"].splitlines()), "", "  ````text"] + \
                       ["  " + l[:200] for l in body.splitlines()] + ["  ````", "", "  </details>", ""]
    return out


def cell(t):
    """Text inside one table cell: pipes escaped, line breaks as <br>."""
    BS, NL = chr(92), chr(10)
    return t.replace("|", BS + "|").replace(NL + NL, "<br><br>").replace(NL, "<br>")


def timeline_table(st):
    """Session timeline as one table; the model's words are rows between the tool rows. Thinking: a row, folded open-able when the CLI gives the text."""
    L = ["### Session timeline", "", "| # | At | What |", "|--:|--:|---|"]
    for i, s in enumerate(st, 1):
        if s["k"] == "think":
            lab = "🧠 thinking" + (" · ~%d tokens" % s["tokens"] if s["tokens"] else "")
            what = "<details><summary>%s</summary><i>%s</i></details>" % (lab, cell(s["text"])) if s["text"] else lab
        elif s["k"] == "say":
            what = "💬 " + cell(s["text"])
        else:
            what = "%s **%s** `%s`" % (ICON.get(s["tool"], "🔧"), s["tool"], cell(short(arg(s), 90)))
        L.append("| %d | +%ss | %s |" % (i, s["at"], what))
    return L


def summary(variant):
    ev, f, start, issue = load()
    st = steps(ev)
    info = files(st, start)
    sha = f.get("commit", "HEAD")
    n = numbers(f)
    L = []
    if variant == "A":      # report card: result and numbers first, files, timeline folded away
        L += ["## " + header(issue, f), "", "_%s · %s · %s turns_" % (issue["title"], f["model"], f["turns"]), "", f["_body"], "", "### Cost of the session", ""]
        L += stats_table(f)
        L += ["", "### Files", "", "📌 read at start · 📖 read later · ✏️ changed", ""] + tree_lines(info, sha)
        L += ["", "<details><summary><b>Session timeline</b> (%d steps)</summary>" % len(st), ""] + transcript(st) + ["", "</details>"]
    elif variant == "B":    # transcript first: the timeline is the page; numbers are the footer
        L += ["## " + header(issue, f), "", "%s · %s · %s turns · %ss · in %s / out %s · cache read %s (**%.0f%%** hit)" %
              (f["model"], f["effort"], f["turns"], f["agent_s"], fmt(n["in"]), fmt(n["out"]), fmt(n["cr"]), n["hit"]), ""]
        L += ["### Session timeline", ""] + transcript(st)
        L += ["", "### Result", "", f["_body"], "", "### Files", "", "```text"] + tree_ascii(info) + ["```", "", "`·` read at start  `○` read later  `●` changed", ""]
        for p, m in sorted(info.items()):
            if marker(m) == "changed" and admitted(p):
                L.append("- [diff of `%s`](%s)" % (p, link_diff(p, sha)))
        L += ["", "<sub>cache write %s · peak context %s</sub>" % (fmt(n["cw"]), fmt(int(f["peak_context"])))]
    elif variant == "D":    # the owner's pick: B's start, C's table with what the model says between the rows, A's tree, prose last
        L += ["## " + header(issue, f), ""] + stats_table(f) + [
              "", "<sub>**Peak context**: the most the model had in view at one moment, i.e. the biggest single call (new + cached input). "
              "The fuller it gets, the worse the model works. **Cache hit**: share of input read from the cache, which is cheaper than reading it fresh.</sub>", ""]
        L += timeline_table(steps(ev, think=True))
        L += ["", "### Files", "", "📌 read at start · 📖 read later · ✏️ changed", ""] + tree_lines(info, sha)
        L += ["", "### Result", "", f["_body"]]
    else:                   # C: everything as tables, timeline = one row per step, results only on demand
        L += ["## " + header(issue, f), "", f["_body"], "",
              "| | |", "|---|---|",
              "| Model | %s (%s) |" % (f["model"], f["effort"]),
              "| Time · turns | %ss · %s turns · peak context %s |" % (f["agent_s"], f["turns"], fmt(int(f["peak_context"]))),
              "| Tokens | in %s · out %s |" % (fmt(n["in"]), fmt(n["out"])),
              "| Cache | read %s · write %s · **%.0f%% hit** |" % (fmt(n["cr"]), fmt(n["cw"]), n["hit"]), "",
              "### Files", "", "| File | What happened | Link |", "|---|---|---|"]
        for p, m in sorted(info.items(), key=lambda kv: (marker(kv[1]) != "changed", kv[0])):
            mk = marker(m)
            lk = "[diff](%s)" % link_diff(p, sha) if mk == "changed" and admitted(p) else ("[file](%s)" % link_file(p, sha) if admitted(p) else "—")
            L.append("| `%s` | %s | %s |" % (p, MARK[mk], lk))
        L += ["", "### Session timeline", "", "| # | Turn | At | What |", "|--:|--:|--:|---|"]
        for i, s in enumerate(st, 1):
            what = "💬 " + short(s["text"], 120) if s["k"] == "say" else "%s **%s** `%s`" % (ICON.get(s["tool"], "🔧"), s["tool"], short(arg(s), 90).replace("|", "\\|"))
            L.append("| %d | %d | +%ss | %s |" % (i, s["turn"], s["at"], what.replace("|", "\\|") if s["k"] == "say" else what))
    print("\n".join(L))


def sample():
    """Timeline of a recording that has thinking text (older model, showThinkingSummaries on): how the folded thinking row looks when text exists."""
    ev = [json.loads(l) for l in open(os.path.join(HERE, "data/thinking-sample.ndjson"), encoding="utf-8")]
    print(chr(10).join(timeline_table(steps(ev, think=True))))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "sample":
        sample()
        sys.exit()
    {"log": log, "summary": summary}[sys.argv[1]](sys.argv[2])
