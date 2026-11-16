"""Loop status: one issue, pinned, that the engine overwrites at the end of every session, owner answer and watchdog
check. It only shows: nothing in the loop reads it (no iteration label, no role), and a failure to write it never
fails the command that called it.

The body is rendered from facts the engine already keeps: the iteration record (`iteration.build_record`, the same
function that writes `iterations/NNNN.json`), the task graph, the session reports and the session runs.
"""
import os
import re

import common as c
import iteration as it

LABEL = "loop:status"
TITLE = "Loop status"
FOOTER = "<sub>Updated by the engine at "
MAX_BODY = 60000        # GitHub refuses an issue body over 65 536 characters
GOAL_CHARS = 220

# state of a task: (icon, words, Mermaid class)
STATES = {"run": ("🔄", "running"), "wait": ("⏳", "waiting"), "ok": ("✅", "merged"),
          "warn": ("⚠️", "done, not merged"), "bad": ("❌", None)}
CLASSES = {"ok": "fill:#e9f7ee,stroke:#8fd0a3", "warn": "fill:#fff9db,stroke:#e0c872", "bad": "fill:#fdeeed,stroke:#e5a3a0",
           "run": "fill:#e8f4fd,stroke:#8cc2ee", "wait": "fill:#f6f8fa,stroke:#c4cad1"}
PHASES = {"starting": "starting", "policy": "policy phase", "shape": "shaping the task graph", "run": "running"}


# ---------------------------------------------------------------- facts

def gather():
    """The view of the open iteration, or of the last one recorded; None before the first iteration."""
    cur = it.open_iteration()
    if cur:
        n = it.iteration_number(cur)
        rec = it.build_record(n, cur, "open")
        phase = c.report_fields(cur["number"]).get("phase", "")
        open_issues = {x["number"] for x in it.iteration_issues(n) if x["state"] == "open"}
    else:
        recs = it.records()
        if not recs:
            return None
        rec, phase, open_issues = recs[-1], "", set()
    runs, running = {}, set(c.LAUNCHED)
    own = os.environ.get("GITHUB_RUN_ID", "")
    for r in c.run_list("session.yml"):               # newest first: the first run of an issue is its latest
        m = re.search(r"#(\d+)", c.run_issue(r["displayTitle"]))
        if not m:
            continue
        runs.setdefault(int(m.group(1)), r["databaseId"])
        if r["status"] in ("queued", "in_progress", "waiting") and str(r["databaseId"]) != own:
            running.add(int(m.group(1)))
    pm = (rec.get("policy") or {}).get("session")
    policymaker = None
    if pm:
        x = c.issue(pm)
        policymaker = it.session_summary(x)
        if x["state"] == "open":
            open_issues.add(pm)
    goal = ((rec.get("input_state") or {}).get("goal") or {}).get("text") or it.read_text("goal/goal.md") or ""
    return {"record": rec, "phase": phase, "open": open_issues, "running": running & open_issues, "runs": runs,
            "policymaker": policymaker, "goal": goal, "goal_reached": c.goal_reached(), "now": c.iso(),
            "url": "%s/%s" % (os.environ.get("GITHUB_SERVER_URL", "https://github.com"), c.REPO)}


# ---------------------------------------------------------------- rendering (pure)

def k(n):
    return "%.1fk" % (n / 1000) if n >= 1000 else str(n)


def duration(s):
    s = int(s or 0)
    if s >= 3600:
        return "%dh %02dm" % (s // 3600, s % 3600 // 60)
    return "%dm %02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s


def when(ts):
    return (ts or "")[:16].replace("T", " ")


def task_state(t, sessions, v):
    """(class, words) of a task: from its session report once it ended, from the issue and its runs while it lives."""
    i = t.get("issue")
    if i in v["running"]:
        return "run", "running"
    s = sessions.get(i)
    if i in v["open"] or not s or not s.get("status"):
        return "wait", "waiting"
    if s["status"] == "done":
        return ("ok", "merged") if s.get("merged") else ("warn", "done, not merged")
    return "bad", s["status"]


def checks(s):
    """The checks of a task's results, from its session report (as the Session summary shows them); empty when none ran."""
    marks = {"pass": "✅", "accepted": "✅", "fail": "❌", "rejected": "❌", "inconclusive": "🟡"}
    parts = ["%s %s" % (marks.get(s.get(key), "•"), name) for key, name in (("verification", "verification"), ("check", "check"))
             if s.get(key)] + (["%s gate %s" % (marks.get(s["verdict"], "•"), s["verdict"])] if s.get("verdict") else [])
    return " · ".join(parts)


def flat_goal(text):
    words = " ".join(l for l in text.splitlines() if not l.lstrip().startswith("#")).split() or text.split()
    line = " ".join(words)
    return line if len(line) <= GOAL_CHARS else line[:GOAL_CHARS - 3].rstrip() + "..."


def mermaid_text(s):
    return s.replace('"', "'").replace("<", "#lt;").replace(">", "#gt;").replace("\n", " ")


def node(tid):
    return "t_" + tid.replace("-", "_")            # a bare `end` is a Mermaid keyword


def header(rec, v):
    if rec["status"] == "open":
        icon, state = "🔵", PHASES.get(v["phase"], "running")
    else:
        icon, state = ("🟢", "closed") if rec["status"] == "closed" else ("🔴", rec["status"])
    head = "## %s Iteration %s · %s · goal %s" % (icon, rec["iteration"], state, "reached" if v["goal_reached"] else "not reached")
    took = "running for" if rec["status"] == "open" else "took"
    lines = [head, "", "Started %s UTC · %s %s · engine %s" % (when(rec.get("started")), took, duration(rec["outcome"]["wall_s"]), rec.get("engine_version"))]
    if rec.get("failure"):
        lines.append("\n**Failed:** %s" % rec["failure"])
    return "\n".join(lines)


def working_on(tasks, sessions, v):
    out = ["### Working on", "**Goal:** %s" % (flat_goal(v["goal"]) or "_no goal set (`goal/goal.md`)_")]
    groups = {}
    for t in tasks:
        groups.setdefault(task_state(t, sessions, v)[0], []).append(t["title"])
    for cls, words in (("run", "Working on now"), ("wait", "Waiting"), ("ok", "Done"), ("warn", "Ended without a merge"), ("bad", "Failed")):
        if cls in groups:
            out.append("- **%s:** %s" % (words, "; ".join(groups[cls])))
    if not tasks:
        out.append("- **No tasks yet:** the iteration has no task graph")
    return "\n".join(out)


def role_row(s):
    t = s["tokens"]
    total = t["in"] + t["cache_read"] + t["cache_write"]
    share = " (%d%%)" % round(100 * t["cache_read"] / total) if total else ""
    model = (s.get("model") or "script").replace("claude-", "")
    return "| %s #%s | %s | %s | %s | %s | %s%s | %s |" % (s["role"], s["issue"], model, s.get("effort") or "–",
                                                        k(t["in"]), k(t["out"]), k(t["cache_read"]), share, k(t["cache_write"]))


def roles(sessions):
    if not sessions:
        return "### Active roles\n_no sessions yet_"
    return "\n".join(["### Active roles", "| Role | Model | Effort | In | Out | Cache read | Cache write |",
                      "|---|---|---|---|---|---|---|"] + [role_row(s) for s in sessions])


def task_graph(tasks, sessions, v):
    if not tasks:
        return "### Task graph\n_no tasks yet_"
    out = ["### Task graph", "```mermaid", "%%{init: {'themeVariables': {'fontSize': '13px'}}}%%", "flowchart LR"]
    for t in tasks:
        cls, words = task_state(t, sessions, v)
        done = checks(sessions.get(t.get("issue")) or {})
        out.append('  %s("%s %s<br/>%s · %s%s"):::%s' % (node(t["id"]), STATES[cls][0], mermaid_text(t["title"]), t["role"], words,
                                                         "<br/>" + done if done else "", cls))
    for t in tasks:
        out += ["  %s --> %s" % (node(b), node(t["id"])) for b in t.get("blocked_by") or []]
    out += ["  classDef %s %s,stroke-width:1px,color:#57606a" % kv for kv in CLASSES.items()]
    return "\n".join(out + ["```"])


def policy(rec, v):
    changes = (rec.get("policy") or {}).get("changes") or []
    lines = ["- [#%s](%s/issues/%s) · %s · %s UTC" % (x.get("issue"), v["url"], x.get("issue"),
                                                     ", ".join("`%s`" % p for p in x.get("changed_paths") or []) or "–", when(x.get("applied_at")))
             for x in changes]
    return "### Policy changes\n" + ("\n".join(lines) if lines else "_none this iteration_")


def summaries(sessions, v):
    lines = []
    for s in sessions:
        run = v["runs"].get(s["issue"])
        link = "[Session summary](%s/actions/runs/%s)" % (v["url"], run) if run else "no run found"
        lines.append("- [%s #%s](%s/issues/%s) → %s" % (s["role"], s["issue"], v["url"], s["issue"], link))
    return "### Session summaries\n" + ("\n".join(lines) if lines else "_no sessions yet_")


def render(v):
    """The whole body, in the accepted order. `v` is what `gather` returns, or None before the first iteration."""
    if v is None:
        return "No iteration has started yet.\n\n%s%s UTC</sub>\n" % (FOOTER, when(c.iso()))
    rec = v["record"]
    sessions = list(rec["outcome"]["sessions"]) + ([v["policymaker"]] if v.get("policymaker") else [])
    by_issue = {s["issue"]: s for s in sessions}
    tasks = rec.get("tasks") or []
    parts = [header(rec, v), working_on(tasks, by_issue, v), roles(sessions), task_graph(tasks, by_issue, v),
             policy(rec, v), summaries(sessions, v), "%s%s UTC</sub>" % (FOOTER, when(v["now"]))]
    body = "\n\n".join(parts) + "\n"
    if len(body) > MAX_BODY:            # the graph is the bulk; the rest stays readable
        parts[3] = "### Task graph\n_%s tasks: too many to draw here; see `iterations/`_" % len(tasks)
        body = "\n\n".join(parts) + "\n"
    return body


def same(a, b):
    """Equal apart from the footer's time: an unchanged loop does not edit the issue every half hour."""
    def strip(s):
        return "\n".join(l for l in (s or "").splitlines() if not l.startswith(FOOTER))
    return strip(a) == strip(b)


# ---------------------------------------------------------------- writing

def refresh():
    """Overwrite the Loop status issue; create and pin it the first time. Never raises: the view must not stop the loop."""
    try:
        v = gather()
        if v is None:
            return None                 # a loop that never ran gets no issue
        body = render(v)
        found = c.list_issues(labels=(LABEL,), limit=1)
        if not found:
            n = c.create_issue(TITLE, body, labels=(LABEL,))
            if not c.pin_issue(n):
                c.say("Loop status #%s created; pinning it failed, pin it once by hand" % n)
            return n
        if not same(found[0].get("body"), body):
            c.edit_issue_body(found[0]["number"], body)
        return found[0]["number"]
    except (Exception, SystemExit) as e:
        c.say("Loop status not updated: %s" % e)
        return None
