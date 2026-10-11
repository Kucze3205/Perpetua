#!/usr/bin/env python3
"""The iteration layer: Policy -> macro plan -> task graph -> parallel sessions -> record; owner requests;
the goal gate; the watchdog's rollback of `policy/`.

An iteration is an issue labelled `loop:map` + `loop:iteration N`; its sessions (the Policy session, the shaper,
the tasks) are issues labelled `loop:iteration N` + `role:<name>`. The iteration issue carries the phase in a
report comment; everything else is derived from the issues and from `iterations/` on the default branch.
"""
import glob
import hashlib
import json
import os
import re

import common as c

MAP = "loop:map"
REQ = "owner-request"
REQUEST_KINDS = ("consent", "merge-acceptance", "goal-gate", "goal-change", "result-confirmation", "question", "notice")
SESSION_KINDS = ("goal-gate", "goal-change", "question")      # what an agent may propose; the engine opens the issue
STATE_MARK = "<!-- input-state -->"
HANDLED = "request:handled"


def tag(n):
    return "%04d" % n


def plan_path(n):
    return "iterations/%s/macro-plan.md" % tag(n)


def tasks_path(n):
    return "iterations/%s/tasks.json" % tag(n)


def record_path(n):
    return "iterations/%s.json" % tag(n)


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def read_json(path):
    t = read_text(path)
    return json.loads(t) if t else None


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def frontmatter(text):
    """(dict, body) of a markdown file with a YAML frontmatter block."""
    import yaml
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text or "", re.S)
    if not m:
        return {}, text or ""
    d = yaml.safe_load(m.group(1))
    return (d if isinstance(d, dict) else {}), m.group(2)


# ---------------------------------------------------------------- owner requests

def fence_for(text):
    longest = max([len(m) for m in re.findall(r"`+", text)] or [0])
    return "`" * max(3, longest + 1)


def open_request(kind, title, what, why, unblocks, ref=None, frozen=None, blocks=(), iteration=None):
    """The only way the loop asks the owner for a decision, a consent or a confirmation. Only engine code calls it.
    The answer is the closing of the issue: completed = yes, not planned = no."""
    assert kind in REQUEST_KINDS, kind
    owner = c.owner_login()
    body = "%s\n\n## What\n\n%s\n\n## Why\n\n%s\n\n## What it unblocks\n\n%s\n" % (
        " ".join("@" + o for o in owner), what, why, unblocks)
    if frozen is not None:
        f = fence_for(frozen)
        body += "\n## Proposed text\n\n%s\n%s\n%s\n\n<!-- frozen-sha: %s -->\n" % (f, frozen, f, sha256(frozen))
    body += "\n<!-- request-kind: %s -->\n" % kind
    if ref:
        body += "<!-- request-ref: %s -->\n" % ref
    labels = [REQ, "%s:%s" % (REQ, kind)] + (([c.ITER + str(iteration)]) if iteration is not None else [])
    for l in labels:
        c.ensure_label(l, "FBCA04" if l.startswith(REQ) else "C5DEF5")
    n = c.create_issue(title, body, labels=labels, assignees=owner)
    for t in blocks:
        c.add_blocker(t, n)
    c.say("owner request #%s: %s" % (n, title))
    return n


def frozen_text(body):
    """The proposed text of a request when it still matches the hash taken at creation; else None."""
    m = re.search(r"## Proposed text\n\n(`{3,})\n(.*?)\n\1\n", body or "", re.S)
    sha = c.marker(body, "frozen-sha")
    if not m or not sha or sha256(m.group(2)) != sha:
        return None
    return m.group(2)


def open_requests():
    return c.list_issues(labels=(REQ,))


def request_open(kind, ref=None, title=None):
    for x in open_requests():
        b = x["body"] or ""
        if c.marker(b, "request-kind") == kind and (ref is None or c.marker(b, "request-ref") == ref) \
                and (title is None or x["title"] == title):
            return x["number"]
    return None


def process_proposals(text, iteration):
    """`.session/requests.json` of an agent: `[{kind, title, what, why, unblocks, frozen?}]`. Returns the issue numbers."""
    try:
        items = json.loads(text)
    except ValueError:
        return []
    made = []
    for p in items if isinstance(items, list) else []:
        if not isinstance(p, dict) or p.get("kind") not in SESSION_KINDS or not p.get("title"):
            continue
        if p["kind"] != "question" and not p.get("frozen"):
            continue
        if request_open(p["kind"], title=p["title"]):
            continue
        made.append(open_request(p["kind"], p["title"], p.get("what", ""), p.get("why", ""), p.get("unblocks", ""),
                                 frozen=p.get("frozen"), iteration=iteration))
    return made


# ---------------------------------------------------------------- records and state

def record_files():
    return sorted(glob.glob("iterations/[0-9][0-9][0-9][0-9].json"))


def records():
    return [read_json(f) for f in record_files()]


def last_closed():
    return next((r for r in reversed(records()) if r["status"] == "closed"), None)


def index_lines():
    out = []
    for r in records():
        o = r.get("outcome", {})
        out.append({"n": r["iteration"], "status": r["status"], "aim": (r.get("macro_plan") or {}).get("aim"),
                    "verdict": (o.get("verdict") or {}).get("value"), "improved": (o.get("verdict") or {}).get("improved", False),
                    "tasks": len(r.get("tasks", [])), "wall_s": o.get("wall_s"), "tokens": o.get("tokens"),
                    "policy": ((r.get("policy") or {}).get("session") or {}).get("status")})
    return out


def no_progress(idx=None):
    """Trailing iterations without an improving accepted change."""
    k = 0
    for line in reversed(idx if idx is not None else index_lines()):
        if line["improved"]:
            break
        k += 1
    return k


def policy_failures(idx=None):
    k = 0
    for line in reversed(idx if idx is not None else index_lines()):
        if line["policy"] in (None, "done"):
            break
        k += 1
    return k


def policy_rev():
    return c.tree_hash("HEAD", "policy")


def evaluator_rev():
    return ",".join(c.tree_hash("HEAD", p) for p in c.cfg()["evaluator_paths"])


def request_summary(x):
    return {"number": x["number"], "kind": c.marker(x["body"], "request-kind"), "title": x["title"]}


def build_state(n):
    cfg = c.cfg()
    idx = index_lines()
    goal = read_text("goal/goal.md")
    gate = read_text("goal/gate.yml")
    closed = c.list_issues(state="closed", labels=(REQ,))[:10]
    return {
        "schema_version": 1, "iteration": n, "engine_version": c.engine_version(),
        "policy_rev": policy_rev(), "evaluator_rev": evaluator_rev(),
        "goal": {"text": goal, "gate_accepted": gate is not None, "gate": gate},
        "policy_params": c.params(), "engine_params": cfg["params"], "limits": cfg["limits"], "gate": cfg["gate"],
        "owner_requests": {"open": [request_summary(x) for x in open_requests()],
                           "recently_closed": [dict(request_summary(x), answer=x.get("state_reason")) for x in closed]},
        "no_progress": no_progress(idx),
        "audit_report": "audit/latest.md" if os.path.exists("audit/latest.md") else None,
        "iterations": idx,
    }


def find_iteration_issue(n, state="open"):
    found = c.list_issues(state=state, labels=(MAP, c.ITER + str(n)))
    return found[0] if found else None


def open_iteration():
    found = c.list_issues(labels=(MAP,))
    return found[0] if found else None


def iteration_number(x):
    return c.iteration_of(c.label_names(x))


def store_state(it, state):
    c.post_comment(it, "%s\n```json\n%s\n```\n" % (STATE_MARK, json.dumps(state, indent=1, ensure_ascii=False)))


def read_state(it):
    for x in c.comments(it):
        if x["body"].startswith(STATE_MARK) and c.trusted(x):
            m = re.search(r"```json\n(.*?)\n```", x["body"], re.S)
            return json.loads(m.group(1)) if m else None
    return None


def export_state(n, directory):
    """`.session/loop-state.json` (the snapshot taken when the iteration began) and the macro plan, for the Policy
    and the shaper."""
    labels = c.label_names(c.issue(n))
    k = c.iteration_of(labels)
    it = find_iteration_issue(k, state="all") if k is not None else None
    state = read_state(it["number"]) if it else None
    os.makedirs(directory, exist_ok=True)
    if state is not None:
        with open(os.path.join(directory, "loop-state.json"), "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, ensure_ascii=False)
    plan = read_text(plan_path(k)) if k is not None else None
    if plan is not None:
        with open(os.path.join(directory, "macro-plan.md"), "w", encoding="utf-8") as fh:
            fh.write(plan)


# ---------------------------------------------------------------- starting an iteration

def next_number():
    nums = [iteration_number(x) for x in c.list_issues(state="all", labels=(MAP,))]
    files = [int(os.path.basename(f)[:4]) for f in record_files()]
    done = [x for x in nums + files if x is not None]
    return max(done) + 1 if done else 0


def cadence_ok():
    cad = c.cfg()["cadence"]
    recs = [r for r in records() if r]
    if cad.get("min_interval_minutes") and recs:
        if (c.now() - c.parse_time(recs[-1]["ended"])).total_seconds() < cad["min_interval_minutes"] * 60:
            return False
    if cad.get("daily_iterations"):
        day = [r for r in recs if (c.now() - c.parse_time(r["started"])).total_seconds() < 86400]
        if len(day) >= cad["daily_iterations"]:
            return False
    return True


def start_iteration():
    """Begin the next iteration if none is open: the Policy session first, or straight to the shaper for an
    iteration whose macro plan the setup already wrote. Serial by construction: one open iteration at most."""
    if not c.guard_ok() or c.outage_open():
        return None
    c.sync_main()
    if open_iteration():
        return None
    if not cadence_ok():
        c.say("cadence: next iteration not due yet")
        return None
    if failed_streak() >= 3:
        c.open_outage("three iterations in a row failed", "The last three iterations ended `failed` (infrastructure or an invalid plan).",
                      "Read the iteration records in `iterations/`, fix the cause, close this issue.")
        return None
    n = next_number()
    label = c.ITER + str(n)
    for l in (label, MAP):
        c.ensure_label(l, "0E8A16" if l == MAP else "C5DEF5", "iteration of the loop")
    it = c.create_issue("Iteration %s" % tag(n), "Iteration %s of the loop. State and plan are in the comments and in `iterations/`.\n" % tag(n),
                        labels=(MAP, label))
    if any(x["number"] < it for x in c.list_issues(labels=(MAP,))):      # two runs started one at once: the lower number stays
        c.close_issue(it, "not planned")
        return None
    c.update_report(it, {"phase": "starting", "started": c.iso(), "policy_rev_start": policy_rev()})
    store_state(it, build_state(n))
    if os.path.exists(plan_path(n)):
        c.update_report(it, {"plan_source": "setup"})
        begin_shape(n, it)
        return n
    p = c.create_issue("Policy for iteration %s" % tag(n),
                       "Decide the macro plan of iteration %s and, where needed, change `policy/`.\n\nInput: `.session/loop-state.json`. "
                       "Output: `.session/macro-plan.md`. Rules: `.session/policy.md`.\n" % tag(n),
                       labels=("role:policy", label))
    c.update_report(it, {"phase": "policy", "policy_issue": p})
    c.launch(p)
    return n


def failed_streak():
    """Trailing failed iterations since the owner last closed an outage."""
    closed = [x["closed_at"] for x in c.list_issues(state="closed", labels=("outage",)) if x.get("closed_at")]
    since = max(closed, default="")
    k = 0
    for r in reversed(records()):
        if r["status"] != "failed" or r["ended"] < since:
            break
        k += 1
    return k


def policy_done(n, status, plan_text):
    """The Policy session ended (its `policy/` changes are already merged or rejected). Fail-open: without a new plan the
    previous one stands, and a failed session is counted toward the watchdog's two-in-a-row rollback."""
    it = find_iteration_issue(n)
    if not it:
        return
    source = "policy"
    if status != "done" or not plan_text or not frontmatter(plan_text)[0].get("aim"):
        source, plan_text = "stood", stand_plan(n)
        if plan_text is None:
            return fail_iteration(n, "no macro plan: the Policy produced none and there is no earlier one")
    if not os.path.exists(plan_path(n)):
        c.commit_main({plan_path(n): plan_text}, "iteration %s: macro plan (%s)" % (tag(n), source))
    c.update_report(it["number"], {"plan_source": source, "policy_rev_end": policy_rev()})
    if status != "done" and policy_failures() + 1 >= 2:
        revert_policy("two Policy sessions in a row failed")
    begin_shape(n, it["number"])


def stand_plan(n):
    for k in range(n - 1, -1, -1):
        t = read_text(plan_path(k))
        if t:
            return t
    return None


def begin_shape(n, it):
    """The shaper turns the macro plan into a task graph: an LLM orchestrator or a script role (a deterministic rule)."""
    shaper = c.params()["shaper"]
    if shaper not in c.profiles():
        return fail_iteration(n, "shaper role `%s` has no profile" % shaper)
    s = c.create_issue("Shape iteration %s" % tag(n),
                       "Turn the macro plan of iteration %s into a task graph.\n\nInput: `.session/macro-plan.md`, `.session/loop-state.json`. "
                       "Output: `.session/tasks.json` (format: `.session/protocol.md`).\n" % tag(n),
                       labels=("role:" + shaper, c.ITER + str(n), "loop:shaper"))
    c.update_report(it, {"phase": "shape", "shape_issue": s, "shaper": shaper})
    c.launch(s)


# ---------------------------------------------------------------- the task graph

TASK_FIELDS = {"id", "title", "role", "body", "model", "effort", "blocked_by", "inputs", "start_branch", "limits", "consent"}


def limits_exceeded(profile):
    """Why a profile is beyond the owner's accepted limits (empty: within)."""
    lim = c.cfg()["limits"]
    why = []
    allowed = lim.get("tools") or c.DEFAULT_TOOLS
    import fnmatch
    for t in profile.get("tools", []) or []:
        if not any(fnmatch.fnmatchcase(t, a) for a in allowed):
            why.append("tool `%s`" % t)
    if profile.get("internet"):
        ok = lim.get("internet_write") or []
        for g in profile.get("write", []) or []:
            if not any(g.startswith(p) for p in ok):
                why.append("internet together with writing `%s`" % g)
    return why


def validate_tasks(doc, orders=()):
    """Errors of a task graph (empty: valid). `orders` are the Policy's own tasks, the only way into policy-only roles."""
    prof, prm, cfg = c.profiles(), c.params(), c.cfg()
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list) or not tasks:
        return ["`tasks` must be a non-empty list"]
    errs, ids = [], set()
    order_ids = {o["id"] for o in orders}
    for t in tasks:
        if not isinstance(t, dict):
            errs.append("a task is not an object")
            continue
        i = t.get("id")
        if not isinstance(i, str) or not i or i in ids:
            errs.append("task id missing or duplicated: %r" % i)
        ids.add(i)
        extra = set(t) - TASK_FIELDS
        if extra:
            errs.append("task %s: unknown fields %s" % (i, sorted(extra)))
        role = t.get("role")
        if role not in prof or role == "policy":
            errs.append("task %s: role %r has no profile" % (i, role))
        elif role in cfg["policy_only_roles"] and i not in order_ids:
            errs.append("task %s: role `%s` is ordered only by the Policy" % (i, role))
        for k in ("title", "body"):
            if not isinstance(t.get(k), str) or not t[k].strip():
                errs.append("task %s: `%s` missing" % (i, k))
        if t.get("model") and t["model"] not in prm["models"]:
            errs.append("task %s: unknown model alias %r" % (i, t["model"]))
        if t.get("effort") and t["effort"] not in prm["efforts"]:
            errs.append("task %s: unknown effort %r" % (i, t["effort"]))
    all_ids = ids | order_ids
    graph = {}
    for t in tasks:
        deps = t.get("blocked_by") or []
        bad = [d for d in deps if d not in all_ids]
        if bad:
            errs.append("task %s: blocked_by unknown %s" % (t.get("id"), bad))
        graph[t.get("id")] = [d for d in deps if d in ids]
    if not errs and topological(graph) is None:
        errs.append("blocked_by has a cycle")
    return errs


def topological(graph):
    """Task ids in an order where blockers come first; None on a cycle."""
    order, seen, state = [], set(), {}

    def visit(x):
        if state.get(x) == 1:
            return False
        if state.get(x) == 2:
            return True
        state[x] = 1
        for d in graph.get(x, []):
            if not visit(d):
                return False
        state[x] = 2
        order.append(x)
        return True
    for x in graph:
        if not visit(x):
            return None
    return order


def width(tasks):
    """The widest layer of the graph: how many tasks can run side by side."""
    depth = {}
    byid = {t["id"]: t for t in tasks}

    def d(x):
        if x not in depth:
            depth[x] = 1 + max([d(b) for b in byid[x].get("blocked_by") or [] if b in byid] or [0])
        return depth[x]
    layers = {}
    for t in tasks:
        layers[d(t["id"])] = layers.get(d(t["id"]), 0) + 1
    return max(layers.values() or [0])


def plan_orders(n):
    """The Policy's own tasks from the macro plan: ids get the prefix `order-`, edges between them follow."""
    fm, _ = frontmatter(read_text(plan_path(n)) or "")
    raw = [o for o in (fm.get("orders") or []) if isinstance(o, dict)]
    ids = {"order-%s" % (o.get("id") or k): o for k, o in enumerate(raw, 1)}
    out = []
    for oid, o in ids.items():
        deps = ["order-%s" % d if "order-%s" % d in ids else d for d in o.get("blocked_by") or []]
        out.append(dict(o, id=oid, blocked_by=deps))
    return out


def instantiate(n, doc, shape_issue):
    """Create the issues of a validated task graph, wire blocking and consent, freeze the plan, start what is free.
    Returns the number of tasks, or an error text."""
    orders = plan_orders(n)
    errs = validate_tasks(doc, orders)
    if not errs:
        errs = validate_tasks({"tasks": doc["tasks"] + orders}, orders)
    if errs:
        return "invalid tasks.json: " + "; ".join(errs[:6])
    tasks = doc["tasks"] + orders
    prof, label = c.profiles(), c.ITER + str(n)
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        for l in ("role:" + t["role"],):
            c.ensure_label(l, "1D76DB")
    number = {}
    for tid in topological({t["id"]: [d for d in t.get("blocked_by") or [] if d in by_id] for t in tasks}):
        t = by_id[tid]
        body = t["body"].rstrip() + "\n\n<!-- task-id: %s -->\n" % tid
        if t.get("limits"):
            body += "<!-- limits: %s -->\n" % json.dumps(t["limits"], sort_keys=True)
        if t.get("start_branch"):
            body += "<!-- start-branch: %s -->\n" % t["start_branch"]
        if t.get("inputs"):
            body += "<!-- inputs: %s -->\n" % json.dumps(resolve_inputs(t["inputs"], number), sort_keys=True)
        labels = ["role:" + t["role"], label, "generation:0"]
        if t.get("model"):
            labels.append("model:" + t["model"])
        if t.get("effort"):
            labels.append("effort:" + t["effort"])
        number[tid] = c.create_issue(t["title"], body, labels=labels)
    for t in tasks:
        for d in t.get("blocked_by") or []:
            if d in number:
                c.add_blocker(number[t["id"]], number[d])
        why = limits_exceeded(prof[t["role"]])
        if why or t.get("consent"):
            ask = t.get("consent") or {}
            req = open_request("consent", "Consent: `%s` session for task \"%s\"" % (t["role"], t["title"]),
                               ask.get("what") or "Run one `%s` session beyond the accepted limits: %s." % (t["role"], ", ".join(why) or "see below"),
                               ask.get("why") or t["title"],
                               "The session starts only when you close this as completed; the wider profile applies to that one session.",
                               ref="task-%s" % number[t["id"]], blocks=(number[t["id"]],), iteration=n)
            c.edit_labels(number[t["id"]], add=["consent:%s" % req])
    frozen = [dict(t, issue=number[t["id"]]) for t in tasks]
    c.commit_main({tasks_path(n): json.dumps(frozen, indent=1, ensure_ascii=False) + "\n"}, "iteration %s: task graph" % tag(n))
    it = find_iteration_issue(n)
    c.update_report(it["number"], {"phase": "run", "tasks": len(tasks), "width": width(tasks)})
    for tid, num in number.items():
        c.launch(num)
    return len(tasks)


def resolve_inputs(inputs, number):
    out = []
    for x in inputs:
        if isinstance(x, dict) and x.get("from") in number:
            out.append({"issue": number[x["from"]], "name": x.get("name")})
        elif isinstance(x, dict):
            out.append(x)
    return out


# ---------------------------------------------------------------- advancing and closing

def iteration_issues(n):
    return [x for x in c.list_issues(state="all", labels=(c.ITER + str(n),)) if "pull_request" not in x]


def advance():
    """Idempotent: look at the facts and take the next step. Called at the end of every session, by the owner-request
    handler and by the watchdog, so a lost event never stalls the loop."""
    it = open_iteration()
    if not it:
        return None
    n = iteration_number(it)
    f = c.report_fields(it["number"])
    phase = f.get("phase", "")
    if phase == "policy":
        p = c.issue(int(f["policy_issue"]))
        if p["state"] == "closed":
            policy_done(n, "crashed", None)         # the epilogue died before it could report
    elif phase == "shape":
        s = c.issue(int(f["shape_issue"]))
        if s["state"] == "closed":
            fail_iteration(n, "the shaper produced no valid task graph")
    elif phase == "run":
        pending = [x for x in iteration_issues(n) if x["state"] == "open" and c.roles_of(c.label_names(x))]
        if not pending:
            close_iteration(n, "closed")
    elif phase == "starting":
        pass
    return n


def fail_iteration(n, why):
    it = find_iteration_issue(n)
    if it:
        for x in iteration_issues(n):
            if x["state"] == "open" and c.roles_of(c.label_names(x)):
                for r in c.session_runs():
                    if c.run_issue(r["displayTitle"]) == "session #%s" % x["number"] and r["status"] in ("queued", "in_progress", "waiting"):
                        c.cancel_run(r["databaseId"])
                c.update_report(x["number"], {"status": "crashed", "cause": "iteration-failed"}, why)
                c.close_issue(x["number"], "not planned")
        close_iteration(n, "failed", why)


def session_summary(x):
    f = c.report_fields(x["number"])
    names = c.label_names(x)

    def num(k):
        return int(float(f[k])) if f.get(k, "").replace(".", "", 1).isdigit() else 0
    return {"issue": x["number"], "role": (c.roles_of(names) or [""])[0], "model": f.get("model"), "effort": f.get("effort"),
            "status": f.get("status"), "verdict": f.get("verdict"), "improved": f.get("improved") == "true",
            "merged": f.get("merged") == "true", "branch": f.get("branch"), "commit": f.get("commit"),
            "tokens": {"in": num("tokens_in"), "out": num("tokens_out"), "cache_read": num("cache_read"), "cache_write": num("cache_write")},
            "models": json.loads(f["models"]) if f.get("models", "").startswith("{") else {},
            "turns": num("turns"), "peak_context": num("peak_context"), "attempts": num("attempts"), "agent_s": num("agent_s"), "gate_s": num("gate_s"), "total_s": num("total_s"),
            "parked_s": num("parked_s"), "runner": f.get("runner"), "artifacts": num("artifacts"),
            "stream": "stream-%s-%s" % (tag(c.iteration_of(names) or 0), x["number"])}


def critical_path(sessions, tasks):
    wall = {s["issue"]: s["total_s"] for s in sessions}
    deps = {t["issue"]: [next((u["issue"] for u in tasks if u["id"] == b), None) for b in t.get("blocked_by") or []] for t in tasks}
    memo = {}

    def longest(i):
        if i not in memo:
            memo[i] = wall.get(i, 0) + max([longest(d) for d in deps.get(i, []) if d] or [0])
        return memo[i]
    return max([longest(t["issue"]) for t in tasks] or [0])


CLAIM_MIN = 10          # a closer that died blocks the iteration this long, no longer


def claim_closing(it):
    """A lock made of comments, which GitHub keeps in order: of the closers that claimed in the last few minutes the
    first one proceeds. Two last sessions of an iteration ending together must not both write its record."""
    me = "<!-- closing: %s -->" % os.environ.get("GITHUB_RUN_ID", "local-%s" % os.getpid())
    c.post_comment(it, "%s\nclosing at %s\n" % (me, c.iso()))
    claims = []
    for x in c.comments(it):
        m = re.match(r"<!-- closing: (\S+) -->\nclosing at (\S+)", x["body"])
        if m and c.trusted(x) and (c.now() - c.parse_time(m.group(2))).total_seconds() < CLAIM_MIN * 60:
            claims.append(m.group(1))
    return bool(claims) and claims[0] == me.split()[2]


def close_iteration(n, status, failure=None):
    """Write the iteration record (only the engine does), close the iteration, then look at what follows:
    stream pruning, no-progress, the goal gate, the next iteration."""
    c.sync_main()
    it = find_iteration_issue(n)
    if not it:
        return
    if not claim_closing(it["number"]):
        c.say("iteration %s: another run is closing it" % tag(n))
        return
    f = c.report_fields(it["number"])
    if not os.path.exists(record_path(n)):
        issues = iteration_issues(n)
        sessions = {x["number"]: session_summary(x) for x in issues if c.roles_of(c.label_names(x))}
        pol = sessions.get(int(f["policy_issue"])) if f.get("policy_issue", "").isdigit() else None
        shp = sessions.get(int(f["shape_issue"])) if f.get("shape_issue", "").isdigit() else None
        tasks = read_json(tasks_path(n)) or []
        work = [s for s in sessions.values() if s is not pol and s is not shp]
        verdicts = [s["verdict"] for s in work if s.get("verdict")]
        value = "accepted" if "accepted" in verdicts else ("rejected" if "rejected" in verdicts else ("inconclusive" if verdicts else "none"))
        sums = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
        by_model = {}
        for s in sessions.values():
            for k in sums:
                sums[k] += s["tokens"][k]
            for m, t in s["models"].items():
                b = by_model.setdefault(m, {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0})
                for k in b:
                    b[k] += t.get(k, 0)
        started = c.parse_time(f["started"]) if f.get("started") else c.now()
        plan, _ = frontmatter(read_text(plan_path(n)) or "")
        opened = [x["number"] for x in c.list_issues(state="all", labels=(REQ, c.ITER + str(n)))]
        record = {
            "schema_version": 1, "iteration": n, "status": status, "failure": failure,
            "started": f.get("started"), "ended": c.iso(), "engine_version": c.engine_version(),
            "input_state": {k: v for k, v in (read_state(it["number"]) or {}).items() if k != "iterations"},
            "policy": {"session": pol, "rev_start": f.get("policy_rev_start"), "rev_end": f.get("policy_rev_end") or policy_rev(),
                       "commit_end": c.head(), "plan_source": f.get("plan_source")},
            "macro_plan": dict(plan, decided_by=f.get("shaper")),
            "tasks": tasks,
            "outcome": {"sessions": [s for s in sessions.values() if s is not pol], "shaper": shp,
                        "verdict": {"value": value, "issuer": c.cfg()["gate"]["kind"], "evaluator_rev": evaluator_rev(),
                                    "improved": any(s["improved"] for s in work)},
                        "tokens": dict(sums, by_model=by_model), "limit_hits": sum(s["attempts"] for s in sessions.values()), "wall_s": int((c.now() - started).total_seconds()),
                        "critical_path_s": critical_path(list(sessions.values()), tasks), "width": int(f.get("width", 0) or 0),
                        "owner_requests": {"opened": opened, "open_at_close": [x["number"] for x in open_requests()]}},
        }
        c.commit_main({record_path(n): json.dumps(record, indent=1, ensure_ascii=False) + "\n"}, "iteration %s: record (%s)" % (tag(n), status))
    c.update_report(it["number"], {"phase": "closed", "status": status})
    c.close_issue(it["number"], "completed" if status == "closed" else "not planned")
    prune_streams(n)
    note_no_progress(n)
    check_goal()
    start_iteration()


def prune_streams(n):
    keep = c.cfg()["params"].get("stream_retention_iterations")
    if not keep:
        return
    for a in c.list_artifacts():
        m = re.match(r"stream-(\d{4})-", a["name"])
        if m and int(m.group(1)) <= n - keep:
            c.delete_artifact(a["id"])


def note_no_progress(n):
    k = c.cfg()["params"].get("no_progress_iterations")
    if k and no_progress() >= k and not request_open("notice", ref="no-progress"):
        open_request("notice", "No progress for %s iterations" % no_progress(),
                     "The last %s iterations produced no accepted improvement." % no_progress(),
                     "The Policy has been told and keeps working toward the current goal; nothing was rolled back.",
                     "Nothing is blocked. Look at the iteration records and tell the Policy or change the goal.", ref="no-progress", iteration=n)


# ---------------------------------------------------------------- the goal gate

def check_goal():
    """After an iteration: if an owner-accepted goal gate exists and its command says the goal is reached, either
    ask the owner to confirm the result or end. Without an accepted gate the loop never ends by itself."""
    if os.path.exists("GOAL_REACHED"):
        return
    gate = c.load_yaml("goal/gate.yml")
    if not gate or not gate.get("command"):
        return
    r = c.run_command(gate["command"], ".", env=c.clean_env(gate.get("env_passthrough") or c.cfg()["gate"]["env_passthrough"]),
                      timeout=3600)
    verdict = parse_verdict(r.stdout)
    if r.returncode or not verdict or not verdict.get("reached"):
        return
    ref = c.head()
    if gate.get("owner_confirms"):
        if request_open("result-confirmation") or confirmation_declined(ref):
            return
        open_request("result-confirmation", "Confirm the result",
                     "The goal gate says the goal is reached.\n\n%s" % (verdict.get("artifact") or verdict.get("detail") or ""),
                     "Without your confirmation the loop keeps working.",
                     "Completed ends the loop (`GOAL_REACHED`). Not planned: the loop continues; say why in a comment.", ref=ref)
    else:
        c.commit_main({"GOAL_REACHED": "reached at %s\n" % ref}, "goal reached (gate: %s)" % gate["command"])


def confirmation_declined(ref):
    return any(c.marker(x["body"], "request-ref") == ref and x.get("state_reason") == "not_planned"
               for x in c.list_issues(state="closed", labels=(REQ + ":result-confirmation",)))


def parse_verdict(stdout):
    """The last line of an evaluator's or gate's output that is a JSON object."""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------- change gate

def run_gate(cand, base, ctx):
    """The change gate's verdict for a candidate tree. `owner`: the owner decides (the caller opens the request).
    `script`: the command runs from the accepted tree (never from the candidate) and prints a JSON object last:
    {"verdict": "accepted|rejected|inconclusive", "improved": bool, "detail": "..."}."""
    g = c.cfg()["gate"]
    if g["kind"] == "owner":
        return {"verdict": "pending-owner"}
    if not g.get("command"):
        return {"verdict": "inconclusive", "detail": "the change gate has no command"}
    env = c.clean_env(g.get("env_passthrough") or [])
    extra = {"CANDIDATE_DIR": cand, "BASELINE_DIR": base, "CANDIDATE_SHA": ctx["candidate_sha"], "BASELINE_SHA": ctx["baseline_sha"],
             "CHANGED_FILES_FILE": ctx["changed_file"], "TASK": str(ctx["task"]), "ITERATION": str(ctx["iteration"])}
    try:
        r = c.run_command(g["command"], base, env=env, extra_env=extra, timeout=int(g.get("timeout_minutes") or 120) * 60)
    except Exception as e:      # timeout, missing shell
        return {"verdict": "inconclusive", "detail": "gate command failed to run: %s" % e}
    v = parse_verdict(r.stdout)
    if not v or v.get("verdict") not in ("accepted", "rejected", "inconclusive"):
        return {"verdict": "inconclusive", "detail": "no verdict from the gate command (exit %s): %s" % (r.returncode, (r.stdout + r.stderr)[-500:])}
    v.setdefault("improved", v["verdict"] == "accepted")
    return v


# ---------------------------------------------------------------- rolling back policy/

def revert_policy(why):
    """Return `policy/` to the state in which the last iteration closed (at the start: the setup baseline), open a
    notice for the owner. Does nothing when the tree is already there. The Policy is called by the next iteration."""
    c.sync_main()
    last = last_closed()
    target = (last or {}).get("policy", {}).get("commit_end")
    if not target:
        r = c.git(".", "rev-list", "-n", "1", "setup-baseline", check=False)
        target = r.stdout.strip()
    if not target:
        c.open_outage("cannot roll back policy/", "Nothing to return to: no closed iteration and no `setup-baseline` tag. Reason: %s." % why,
                      "Restore `policy/` by hand to a state you trust.")
        return False
    if c.tree_hash("HEAD", "policy") == c.tree_hash(target, "policy"):
        return False
    c.commit_main({}, "roll back policy/ to %s: %s" % (target[:8], why), restore=(target, ["policy"]))
    open_request("notice", "policy/ rolled back", "The watchdog returned `policy/` to %s. Reason: %s." % (target[:8], why),
                 "The loop was not progressing or the Policy failed twice in a row.",
                 "Nothing is blocked. The Policy sees this notice in its next iteration.", ref="rollback-%s" % c.head()[:8])
    return True


def waiting_on_owner():
    open_tasks = [x for x in c.list_issues() if "awaiting:owner" in c.label_names(x)]
    return bool(open_tasks)


def overdue():
    """The open iteration has run longer than the Policy's ceiling for an iteration (waiting for the owner excepted)."""
    m = c.params()["ceilings"].get("iteration_minutes")
    cur = open_iteration()
    if not (m and cur) or waiting_on_owner():
        return False
    return (c.now() - c.parse_time(cur["created_at"])).total_seconds() > m * 60


def stalled():
    """The open iteration has not closed within `quiet_minutes` (the watchdog's net, set by the setup: it depends on how
    long an iteration of this goal takes). Time spent waiting for the owner counts only if the setup says so."""
    q = c.cfg()["params"].get("quiet_minutes")
    cur = open_iteration()
    if not q or not cur:
        return False
    if waiting_on_owner() and not c.cfg()["params"].get("owner_wait_counts"):
        return False
    return (c.now() - c.parse_time(cur["created_at"])).total_seconds() > q * 60
