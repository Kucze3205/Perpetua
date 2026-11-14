"""PROTOTYPE (throwaway) - Loop status, ticket #336 of map #331.

Question: what should the pinned "Loop status" issue look like?
Three structurally different bodies (A panel, B graph first, C history feed), rendered from
REAL iteration records of e2e runs (e2e/out-* and branch origin/e2e-loop).
Real records have a single task, so `--wide` adds a SYNTHETIC mid-flight iteration (marked in the output)
to show a graph with blocking edges and live statuses.

    python prototypes/loop-status/render.py OUTDIR      # writes A.md B.md C.md (+ *-wide.md)
"""
import json, subprocess, sys, glob, os, copy

REPO = "Kucze3205/Perpetua"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
URL = f"https://github.com/{REPO}"


def load():
    recs = {}
    for f in glob.glob(os.path.join(ROOT, "e2e", "out-*", "report.json")):
        ic = json.load(open(f, encoding="utf-8")).get("iteration_record")
        if ic and ic.get("outcome"):
            recs[ic["iteration"]] = ic
    out = subprocess.run(["git", "show", "origin/e2e-loop:iterations/0017.json"], capture_output=True,
                         text=True, encoding="utf-8", cwd=ROOT).stdout
    if out:
        recs[17] = json.loads(out)
    return [recs[k] for k in sorted(recs)]


# ---- small helpers -------------------------------------------------------------------------
def k(n):
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def mins(s):
    s = int(s or 0)
    return f"{s//60}m {s%60:02d}s" if s >= 60 else f"{s}s"


def link(n, text=None):
    if n < 0:  # synthetic issue: no real page
        return text or "(synthetic)"
    return f"[{text or '#%d' % n}]({URL}/issues/{n})"


def tokens(t):
    total_in = t["in"] + t["cache_read"] + t["cache_write"]
    share = 100 * t["cache_read"] / total_in if total_in else 0
    return t, share


def tok_line(t):
    _, share = tokens(t)
    return f"in {k(t['in'])} · out {k(t['out'])} · cache read {k(t['cache_read'])} ({share:.0f}%) · cache write {k(t['cache_write'])}"


def short_model(m):
    return m.replace("claude-", "")


def task_status(task, sessions):
    s = next((x for x in sessions if x["issue"] == task.get("issue")), None)
    if not s:
        return "⏳", "waiting"
    if s.get("state") == "running":
        return "🔄", "running"
    if s["status"] == "done" and s.get("merged"):
        return "✅", "merged"
    if s["status"] == "done":
        return "⚠️", "done, not merged"
    return "❌", s["status"]


def head(ic):
    st = {"closed": "closed", "failed": "failed", "open": "running"}.get(ic["status"], ic["status"])
    icon = {"closed": "🟢", "failed": "🔴", "running": "🔵"}.get(st, "⚪")
    goal = ic["input_state"]["goal"]
    goal_txt = "reached" if goal.get("gate_accepted") else "not reached"
    return icon, st, goal_txt


def policy_lines(ic):
    ch = (ic.get("policy") or {}).get("changes") or []
    return [f"{link(c['issue'])} · {', '.join(f'`{p}`' for p in c['changed_paths'])} · {c['applied_at'][:16].replace('T',' ')}" for c in ch]


def roles_table(ic):
    p = ic["input_state"]["policy_params"]
    rows = {}
    for s in ic["outcome"]["sessions"]:
        rows.setdefault(s["role"], s)
    plain = [t for t in ic["input_state"]["limits"]["tools"] if "(" not in t]
    bash = [t[5:-3] for t in ic["input_state"]["limits"]["tools"] if t.startswith("Bash(")]
    tools = ", ".join(plain + ([f"Bash ({', '.join(bash)})"] if bash else []))
    out = ["| Role | Model | Effort | Tools |", "|---|---|---|---|"]
    for role, s in rows.items():
        out.append(f"| {role} | {short_model(s['model'])} | {s['effort']} | {tools} |")
    pm = p["policymaker_session"]
    out.append(f"| policymaker | {short_model(pm['model'])} | {pm['effort']} | Read, Grep, Glob, Write, Edit |")
    return "\n".join(out)


def graph_mermaid(ic, sessions):
    lines = ["```mermaid", "flowchart LR"]
    cls = {"✅": "ok", "⚠️": "warn", "❌": "bad", "🔄": "run", "⏳": "wait"}
    for t in ic["tasks"]:
        icon, label = task_status(t, sessions)
        title = t["title"].replace('"', "'")
        lines.append(f'  {t["id"].replace("-", "_")}["{icon} {title}<br/>{t["role"]} · {label}"]:::{cls[icon]}')
    for t in ic["tasks"]:
        for b in t.get("blocked_by") or []:
            lines.append(f'  {b.replace("-", "_")} --> {t["id"].replace("-", "_")}')
    lines += ["  classDef ok fill:#dafbe1,stroke:#1a7f37", "  classDef warn fill:#fff8c5,stroke:#9a6700",
              "  classDef bad fill:#ffebe9,stroke:#cf222e", "  classDef run fill:#ddf4ff,stroke:#0969da",
              "  classDef wait fill:#f6f8fa,stroke:#8c959f", "```"]
    return "\n".join(lines)


def graph_table(ic, sessions):
    out = ["| | Task | Role | After | Result |", "|---|---|---|---|---|"]
    for t in ic["tasks"]:
        icon, label = task_status(t, sessions)
        after = ", ".join(t.get("blocked_by") or []) or "–"
        s = next((x for x in sessions if x["issue"] == t.get("issue")), None)
        res = f"{label} · {mins(s['total_s'])}" if s and s.get("total_s") else label
        out.append(f"| {icon} | {link(t['issue'], t['title']) if t.get('issue') else t['title']} | {t['role']} | {after} | {res} |")
    return "\n".join(out)


def totals(ic):
    t = ic["outcome"]["tokens"]
    return tok_line(t)


def session_line(s, tasks):
    title = next((t["title"] for t in tasks if t.get("issue") == s["issue"]), f"{s['role']} session")
    icon = "🔄" if s.get("state") == "running" else "✅" if s["status"] == "done" else "❌"
    summary = f" · [Session summary]({URL}/issues/{s['issue']})" if s["issue"] > 0 else ""
    return (f"{icon} **{s['role']}** · {link(s['issue'], title)} · {short_model(s['model'])}/{s['effort']} · "
            f"{s['turns']} turns · {mins(s['total_s'])} · {tok_line(s['tokens'])} · "
            f"{summary[3:]}").rstrip(" ·")


# ---- Variant A: panel (tables, everything visible at once) ---------------------------------
def variant_a(history, ic, note=""):
    icon, st, goal = head(ic)
    sessions = ic["outcome"]["sessions"]
    pol = policy_lines(ic)
    all_t = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    for h in history:
        for key in all_t:
            all_t[key] += h["outcome"]["tokens"][key]
    return f"""{note}## {icon} Iteration {ic['iteration']} · {st} · goal {goal}

Started {ic['started'][:16].replace('T',' ')} UTC · took {mins(ic['outcome']['wall_s'])} · engine {ic['engine_version']}

### Active roles
{roles_table(ic)}

### Task graph
{graph_table(ic, sessions)}

### Policy changes
{chr(10).join('- ' + p for p in pol) if pol else '_none this iteration_'}

### Tokens and cache
| | in | out | cache read | cache write |
|---|---|---|---|---|
| This iteration | {k(ic['outcome']['tokens']['in'])} | {k(ic['outcome']['tokens']['out'])} | {k(ic['outcome']['tokens']['cache_read'])} | {k(ic['outcome']['tokens']['cache_write'])} |
| Last {len(history)} iterations | {k(all_t['in'])} | {k(all_t['out'])} | {k(all_t['cache_read'])} | {k(all_t['cache_write'])} |

### Session summaries
{chr(10).join('- ' + (link(s['issue'], f"{s['role']} #{s['issue']}") + f" → [Session summary]({URL}/issues/{s['issue']})" if s['issue'] > 0 else s['role'] + ' (synthetic)') for s in sessions)}

<sub>Updated by the engine at {ic['ended'][:16].replace('T',' ')} UTC</sub>
"""


# ---- Variant B: graph first (mermaid, details folded) --------------------------------------
def variant_b(history, ic, note=""):
    icon, st, goal = head(ic)
    sessions = ic["outcome"]["sessions"]
    pol = policy_lines(ic)
    n_ok = sum(1 for t in ic["tasks"] if task_status(t, sessions)[0] == "✅")
    return f"""{note}# {icon} Iteration {ic['iteration']} — goal {goal}

**{st.capitalize()}** · {n_ok}/{len(ic['tasks'])} tasks merged · {mins(ic['outcome']['wall_s'])} · {k(ic['outcome']['tokens']['out'])} tokens out · cache hit {tokens(ic['outcome']['tokens'])[1]:.0f}%

{graph_mermaid(ic, sessions)}

<details><summary><b>Roles and tools</b></summary>

{roles_table(ic)}
</details>

<details><summary><b>Sessions ({len(sessions)})</b> · tokens: {totals(ic)}</summary>

{chr(10).join('- ' + session_line(s, ic['tasks']) for s in sessions)}
</details>

<details><summary><b>Policy changes ({len(pol)})</b></summary>

{chr(10).join('- ' + p for p in pol) if pol else '_none this iteration_'}
</details>

<sub>Updated by the engine at {ic['ended'][:16].replace('T',' ')} UTC</sub>
"""


# ---- Variant C: history feed (iterations as a changelog, newest first) ---------------------
def variant_c(history, ic, note=""):
    icon, st, goal = head(ic)
    cur = ic
    sessions = cur["outcome"]["sessions"]
    older = [h for h in reversed(history) if h["iteration"] != cur["iteration"]]
    rows = []
    for h in older:
        i2, st2, g2 = head(h)
        pol = len(policy_lines(h))
        rows.append(f"| {i2} {h['iteration']} | {st2} | {len(h['tasks'])} | {pol or '–'} | {mins(h['outcome']['wall_s'])} | {k(h['outcome']['tokens']['out'])} | {tokens(h['outcome']['tokens'])[1]:.0f}% |")
    return f"""{note}> {icon} **Now: iteration {cur['iteration']} {st}** — goal {goal}. {len(cur['tasks'])} task(s), {len(sessions)} sessions, {mins(cur['outcome']['wall_s'])}.

**Iteration {cur['iteration']}**
{chr(10).join('1. ' + session_line(s, cur['tasks']) for s in sessions)}

{('**Policy changed:** ' + '; '.join(policy_lines(cur))) if policy_lines(cur) else '**Policy:** unchanged'}

**Tokens (whole iteration):** {totals(cur)}

---

**Earlier iterations**

| Iteration | State | Tasks | Policy changes | Time | Tokens out | Cache hit |
|---|---|---|---|---|---|---|
{chr(10).join(rows)}

<sub>Updated by the engine at {cur['ended'][:16].replace('T',' ')} UTC</sub>
"""


# ---- SYNTHETIC wide iteration: shows blocking edges and live statuses -----------------------
def make_wide(ic):
    w = copy.deepcopy(ic)
    w["status"] = "open"
    base = w["outcome"]["sessions"][-1]
    ids = [("scaffold", "Create the project skeleton", "worker", [], "merged"),
           ("parser", "Write the parser", "worker", ["scaffold"], "merged"),
           ("renderer", "Write the renderer", "worker", ["scaffold"], "running"),
           ("docs", "Document the CLI", "worker", ["parser", "renderer"], "waiting"),
           ("review", "Review the whole change", "evaluator", ["docs"], "waiting")]
    w["tasks"], sess = [], []
    for n, (tid, title, role, deps, state) in enumerate(ids):
        issue = -(n + 1)
        w["tasks"].append({"id": tid, "title": title, "role": role, "blocked_by": deps, "issue": issue})
        if state != "waiting":
            s = copy.deepcopy(base)
            s.update(issue=issue, role=role, status="done" if state == "merged" else "running",
                     merged=state == "merged")
            if state == "running":
                s["state"] = "running"
            sess.append(s)
    w["outcome"]["sessions"] = sess
    return w


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    hist = load()
    cur = hist[-1]
    wide = make_wide(cur)
    syn = "> ⚠️ _SYNTHETIC: a mid-flight iteration made up to test the graph. Real records have one task._\n\n"
    for name, fn in (("A", variant_a), ("B", variant_b), ("C", variant_c)):
        open(f"{out}/{name}.md", "w", encoding="utf-8").write(fn(hist, cur))
        open(f"{out}/{name}-wide.md", "w", encoding="utf-8").write(fn(hist, wide, syn))
    print("iterations used:", [h["iteration"] for h in hist])
