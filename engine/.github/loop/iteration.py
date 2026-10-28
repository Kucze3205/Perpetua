#!/usr/bin/env python3
"""The iteration layer: shaper -> task graph -> parallel sessions -> record; the Policymaker on triggers; owner
requests; the goal gate; the rollback of `execution/` and `policy/`.

An iteration is an issue labelled `loop:map` + `loop:iteration N`; its sessions (the shaper, the tasks, the Policymaker's
orders) are issues labelled `loop:iteration N` + `role:<name>`. The iteration issue carries the phase in a report
comment; everything else is derived from the issues and from `iterations/` on the default branch. A Policymaker session
carries the label of the iteration whose close started it and belongs to no iteration.
"""
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile

import common as c
import managed_resources as resources
import owner_requests as requests
import session_exports
import session_inputs
import session_reports
import session_startup

MAP = "loop:map"
REQ = "owner-request"
REQUEST_KINDS = ("consent", "merge-acceptance", "goal-gate", "goal-change", "result-confirmation", "question", "notice") + requests.KINDS
SESSION_KINDS = ("goal-gate", "goal-change", "question") + requests.KINDS
VERDICT_BYTES = 64 * 1024        # what the engine reads of a tokenless job's output
STATE_MARK = "<!-- input-state -->"
ORDERS_MARK = "<!-- policymaker-orders -->"
PASS_MARK = "<!-- policymaker-pass -->"
SEEN_MARK = "<!-- policymaker-audit-seen -->"
HANDLED = "request:handled"
POLICY_OUTPUT_BYTES = 4096      # what the engine accepts from a policy script
POLICY_SCRIPT_MINUTES = 10
MAX_TASKS = 50          # the kernel's cap on the tasks of one iteration's graph: shaper output and Policymaker orders alike


def tag(n):
    return "%04d" % n



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


def open_request(kind, title, what, why, unblocks, ref=None, frozen=None, blocks=(), iteration=None, readiness=None, test=None):
    """The only way the loop asks the owner for a decision, a consent or a confirmation. Only engine code calls it.
    The answer is the closing of the issue: completed = yes, not planned = no."""
    assert kind in REQUEST_KINDS, kind
    proposal = requests.prepare(kind, frozen, readiness, test) if kind in requests.KINDS else None
    if proposal:
        frozen = proposal["frozen"]
    owner = c.owner_login()
    body = "%s\n\n## What\n\n%s\n\n## Why\n\n%s\n\n## What it unblocks\n\n%s\n" % (
        " ".join("@" + o for o in owner), what, why, unblocks)
    if frozen is not None:
        f = fence_for(frozen)
        body += "\n## Proposed text\n\n%s\n%s\n%s\n\n<!-- frozen-sha: %s -->\n" % (f, frozen, f, sha256(frozen))
    if test:
        f = fence_for(test)
        body += "\n## Test command\n\n%s\n%s\n%s\n" % (f, test, f)
    body += "\n<!-- request-kind: %s -->\n" % kind
    if ref:
        body += "<!-- request-ref: %s -->\n" % ref
    if proposal:
        body += requests.instructions(proposal)
    labels = [REQ, "%s:%s" % (REQ, kind)] + (([c.ITER + str(iteration)]) if iteration is not None else [])
    for l in labels:
        c.ensure_label(l, "FBCA04" if l.startswith(REQ) else "C5DEF5")
    n = c.create_issue(title, body, labels=labels, assignees=owner)
    if proposal:
        requests.freeze(n, proposal, body)
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


def process_proposals(text, iteration, source=None):
    """`.session/requests.json` of an agent: `[{kind, title, what, why, unblocks, frozen?}]`. Returns the issue numbers."""
    try:
        items = json.loads(text)
    except ValueError:
        return []
    made = []
    for p in items if isinstance(items, list) else []:
        if not isinstance(p, dict) or p.get("kind") not in SESSION_KINDS or not p.get("title"):
            continue
        if p["kind"] not in ("question", "capability") and not p.get("frozen"):
            continue
        ref = None
        if p["kind"] in requests.KINDS:
            ref = "proposal-" + sha256(json.dumps([source if source is not None else iteration, p], sort_keys=True))
            if any(c.marker(x["body"], "request-ref") == ref for x in c.list_issues(state="all", labels=(REQ,))):
                continue
        if request_open(p["kind"], title=p["title"]):
            continue
        try:
            made.append(open_request(p["kind"], p["title"], p.get("what", ""), p.get("why", ""), p.get("unblocks", ""),
                                     frozen=p.get("frozen"), iteration=iteration, ref=ref, readiness=p.get("readiness"), test=p.get("test")))
        except (ValueError, TypeError) as e:
            c.say("Invalid owner request %s: %s" % (p["title"], e))
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
        out.append({"n": r["iteration"], "status": r["status"],
                    "verdict": (o.get("verdict") or {}).get("value"), "improved": (o.get("verdict") or {}).get("improved", False),
                    "tasks": len(r.get("tasks", [])), "wall_s": o.get("wall_s"), "tokens": o.get("tokens"),
                    "orders_refused": (r.get("policy") or {}).get("orders_refused")})
    return out


def no_progress(idx=None):
    """Trailing iterations without an improving accepted change."""
    k = 0
    for line in reversed(idx if idx is not None else index_lines()):
        if line["improved"]:
            break
        k += 1
    return k


def policymaker_sessions(limit=5):
    """The latest closed Policymaker sessions, newest first, and how they ended."""
    done = sorted(c.list_issues(state="closed", labels=("role:policymaker",)), key=lambda x: -x["number"])[:limit]
    return [{"issue": x["number"], "status": c.report_fields(x["number"]).get("status")} for x in done]


def policymaker_failures():
    """Trailing Policymaker sessions that did not end `done`."""
    k = 0
    for s in policymaker_sessions(2):
        if s["status"] == "done":
            break
        k += 1
    return k


def policy_rev():
    return resources.revision()


def evaluator_rev():
    return resources.evaluator_revision_string()


def request_summary(x):
    out = {"number": x["number"], "kind": c.marker(x["body"], "request-kind"), "title": x["title"]}
    frozen = requests.read(x["number"])
    if frozen:
        out.update(decision=frozen["status"], proposal_sha=frozen["proposal_sha"], base_commit=frozen["base_commit"],
                   detail=frozen.get("detail"), readiness=frozen.get("readiness"))
    return out


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
        "policymaker_sessions": policymaker_sessions(),
        "outboxes": [x["issue"] for x in session_reports.outboxes()],
        "audit_report": "audit/latest.md" if os.path.exists("audit/latest.md") else None,
        "audit_rev": audit_revision(),
        "iterations": idx,
        "startup_notices": list(session_startup.notices().values()),
    }


def find_iteration_issue(n, state="open"):
    found = c.list_issues(state=state, labels=(MAP, c.ITER + str(n)))
    return found[0] if found else None


def open_iteration():
    found = c.list_issues(labels=(MAP,))
    return found[0] if found else None


def applied_list(fields, key):
    raw = fields.get(key, "")
    return json.loads(raw) if raw.startswith("[") else []


def applied(key, ident):
    """The entry `ident` of the open iteration's `changes` or `verifications`, or None."""
    cur = open_iteration()
    return next((e for e in applied_list(c.report_fields(cur["number"]), key) if e["id"] == ident), None) if cur else None


def record_applied(key, entry):
    """Add a change receipt or verifier event to the open iteration's report, which its record is built from.
    An id is written once; a repeated epilogue or owner event cannot rewrite it."""
    cur = open_iteration()
    if not cur:
        return
    items = applied_list(c.report_fields(cur["number"]), key)
    if all(e["id"] != entry["id"] for e in items):
        c.update_report(cur["number"], {key: json.dumps(items + [entry], separators=(",", ":"), ensure_ascii=False)})


def change_id(issue, k=1):
    """The first pass of a Policymaker session keeps the session's plain id; later passes add theirs."""
    return "change-%s" % issue + ("-%s" % k if k > 1 else "")


def record_change(cand, n, role, session_base, baseline, candidate, changed, verifier=None, k=1):
    """The receipt of an accepted policy/evaluator change, kept in the open iteration's record. `changed` is the diff
    from `baseline` to `candidate`; concurrent changes by other roles are excluded. None for a change outside the
    managed definitions."""
    if not any(resources.is_managed(p) for p in changed):
        return None
    return dict(id=change_id(n, k), issue=n, role=role, base_commit=session_base, applied_base_commit=baseline,
                accepted_commit=candidate, policy_rev_before=resources.revision(baseline, cand),
                policy_rev_after=resources.revision(candidate, cand), changed_paths=changed,
                verifier_issue=verifier, applied_at=c.iso())


def record_verification(n, i, status, result, application, owner=False):
    """A verifier verdict and an eventual owner decision are distinct events of the open iteration."""
    target = int(c.marker(i["body"], "verifies"))
    record = dict(id="verification-%s%s" % (n, "-owner" if owner else ""),
                  issue=target, role=c.roles_of(c.label_names(c.issue(target)))[0], verifier_issue=n,
                  session_status=status, result=result,
                  checked_commit=c.marker(i["body"], "checked-commit"),
                  checked_evaluator_rev=json.loads(c.marker(i["body"], "checked-evaluator-rev") or "null"),
                  application_result=application["result"],
                  application_detail=application.get("detail") or None,
                  change_id=(application.get("change") or {}).get("id"), recorded_at=c.iso())
    record_applied("verifications", record)


def iteration_number(x):
    return c.iteration_of(c.label_names(x))


def store_state(it, state, mark=STATE_MARK):
    c.post_comment(it, "%s\n```json\n%s\n```\n" % (mark, json.dumps(state, indent=1, ensure_ascii=False)))


def read_state(it, mark=STATE_MARK, last=False):
    """The first stored state with this mark, or the last one."""
    found = [x for x in c.comments(it) if x["body"].startswith(mark) and c.trusted(x)]
    if not found:
        return None
    m = re.search(r"```json\n(.*?)\n```", found[-1 if last else 0]["body"], re.S)
    return json.loads(m.group(1)) if m else None


def stored_state(n, directory, role=None):
    """The snapshot taken when the Policymaker session was opened, or, for the shaper, when its iteration began, limited to
    what the role may see; its reports are staged beside it. None when there is none. Policy shapes it into
    `.session/loop-state.json` (session_context.stage)."""
    state = read_state(n)
    if state is None:
        k = c.iteration_of(c.label_names(c.issue(n)))
        it = find_iteration_issue(k, state="all") if k is not None else None
        state = read_state(it["number"]) if it else None
    if state is None:
        return None
    if role == "policymaker" and (passed := read_state(n, PASS_MARK, last=True)):
        state = dict(state, policy_pass=passed)
    if role and role != "policymaker":
        state = dict(state, task_options=session_inputs.task_options())
    session_reports.stage(state, directory)
    return state


# ---------------------------------------------------------------- starting an iteration

def next_number():
    nums = [iteration_number(x) for x in c.list_issues(state="all", labels=(MAP,))]
    files = [int(os.path.basename(f)[:4]) for f in record_files()]
    done = [x for x in nums + files if x is not None]
    return max(done) + 1 if done else 0


def closed_without_record():
    """The number of the latest iteration when its issue was closed but no record was written (closed by hand, or the
    closing died); else None."""
    issues = c.list_issues(state="all", labels=(MAP,))
    nums = [iteration_number(x) for x in issues]
    if not nums or max(nums) in [iteration_number(x) for x in issues if x["state"] == "open"]:
        return None
    return None if os.path.exists(record_path(max(nums))) else max(nums)


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
    """Begin the next iteration if none is open. The policy layer goes first: a due Policymaker session opens the
    iteration and the iteration waits for it, and for the policy-layer work it orders, before the execution phase.
    Serial by construction: one open iteration at most."""
    if not c.guard_ok() or c.outage_open():
        return None
    c.sync_main()
    if open_iteration():
        return None
    if (lost := closed_without_record()) is not None:
        revert_policy("iteration %s was closed without a record" % tag(lost))
    if not cadence_ok():
        c.say("cadence: next iteration not due yet")
        return None
    if failed_streak() >= 3:
        c.open_outage("three iterations in a row failed", "The last three iterations ended `failed` (infrastructure or an invalid task graph).",
                      "Read the iteration records in `iterations/`, fix the cause, close this issue.")
        return None
    n = next_number()
    label = c.ITER + str(n)
    for l in (label, MAP):
        c.ensure_label(l, "0E8A16" if l == MAP else "C5DEF5", "iteration of the loop")
    it = c.create_issue("Iteration %s" % tag(n), "Iteration %s of the loop. State is in the comments and in `iterations/`.\n" % tag(n),
                        labels=(MAP, label))
    if any(x["number"] < it for x in c.list_issues(labels=(MAP,))):      # two runs started one at once: the lower number stays
        c.close_issue(it, "not planned")
        return None
    c.update_report(it, {"phase": "starting", "started": c.iso(), "policy_rev_start": policy_rev()})
    try:
        why = policymaker_due(n)
    except PolicyScriptError as e:
        fail_iteration(n, "invalid policy script output: %s" % e, rollback=True)
        return n
    if why and c.guard_ok():
        c.update_report(it, {"phase": "policy", "policy_issue": start_policymaker(n, why), "policy_trigger": why})
        return n
    begin_work(n, it)
    return n


def begin_work(n, it):
    """The execution phase: the policy phase is over. The shaper and the tasks the Policymaker ordered make the
    iteration's graph. A Policymaker that failed twice in a row is reverted: the iteration runs on the last good policy."""
    from loop import apply_policy

    c.update_report(it, {"phase": "starting"})         # leaves "policy": a second run of advance() does not begin again
    orders = orders_of(apply_policy())      # apply_policy: a session left over from an earlier iteration
    if policymaker_failures() >= 2:
        revert_policy("two Policymaker sessions in a row failed")
    store_state(it, build_state(n))
    if c.params()["shaper"]:
        if orders:
            place_orders(n, it, orders)
        return begin_shape(n, it)
    if orders:
        place_orders(n, it, orders)
    ordered = read_json(tasks_path(n)) or []
    if not ordered:
        return fail_iteration(n, "no shaper is set and the Policymaker ordered no valid tasks", rollback=True)
    c.update_report(it, {"phase": "run", "shaper": None, "tasks": len(ordered), "width": width(ordered)})


def policy_pass(n, it):
    """A Policymaker session is over: merge its changes and start the tasks it ordered; their dependencies set the order
    of work. With `wake` the session waits for them and runs again in this iteration; otherwise the policy phase ends."""
    from loop import apply_policy
    kept = []

    def settle(p):
        orders = read_orders(p)
        if not orders:
            return False
        taken = {t["id"] for t in read_json(tasks_path(n)) or []}
        errs = validate_tasks({"tasks": orders}, ordered=True, taken=taken)
        if errs:
            c.update_report(it, {"orders_refused": "; ".join(errs[:6])[:300]}, "The Policymaker's orders were refused: %s." % "; ".join(errs[:6]))
            return False
        numbers = create_tasks(n, orders)
        for num in numbers:
            c.launch(num)
        if not read_wake(p):
            return False
        c.update_report(p, {"ordered": " ".join(map(str, numbers))})
        kept.append(p)
        return True
    for p in apply_policy(settle):
        if p not in kept:
            store_state(p, {"audit_rev": audit_revision()}, SEEN_MARK)       # the audit it ordered and read is not new
    if not kept:
        begin_work(n, it)


def policy_pass_of(p):
    """The pass the Policymaker session on issue p is on: the first until the engine relaunches it."""
    return int(c.report_fields(p).get("pass") or 1)


def orders_ended(p):
    return all(c.issue(int(x))["state"] == "closed" for x in (c.report_fields(p).get("ordered") or "").split())


def resume_policymaker(p):
    """Every session the pass ordered has ended, in any status: the same issue runs again, with a fresh agent context, on
    its branch brought to what has merged, and sees how its orders ended."""
    f = c.report_fields(p)
    k = policy_pass_of(p) + 1
    ordered = [{"issue": int(x), "role": (c.roles_of(c.label_names(c.issue(int(x)))) or [""])[0],
                "status": c.report_fields(int(x)).get("status")} for x in (f.get("ordered") or "").split()]
    store_state(p, {"pass": k, "ordered": ordered}, PASS_MARK)
    spent = ("status", "commit", "fixes", "merged", "verdict", "improved", "detail", "verifier_issue", "verified_commit",
             "verifier_passed", "owner_accepted", "owner_accepted_commit", "ordered")
    c.update_report(p, dict({x: None for x in spent}, **{"pass": k}), "Pass %s: the ordered sessions ended, the Policymaker runs again." % k)
    c.sync_main()
    c.git(".", "push", "-q", "-f", "origin", "origin/%s:refs/heads/task/%s" % (c.default_branch(), p), remote=True)
    c.edit_labels(p, remove=["awaiting:orders"])
    c.launch(p)


def policy_settled(n):
    """The iteration's Policymaker session is over: it ended (any status) or its output waits to be merged."""
    found = c.list_issues(state="all", labels=("role:policymaker", c.ITER + str(n)))
    return all(x["state"] == "closed" or "awaiting:iteration" in c.label_names(x) for x in found)


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


def begin_shape(n, it):
    """The shaper decides what the iteration does and writes its task graph: an LLM orchestrator or a script role (a
    deterministic rule)."""
    shaper = c.params()["shaper"]
    if shaper not in c.profiles():
        return fail_iteration(n, "shaper role `%s` has no profile" % shaper)
    s = c.create_issue("Shape iteration %s" % tag(n),
                       "Decide what iteration %s does and write its task graph.\n\nInput: `.session/loop-state.json`. "
                       "Output: `.session/tasks.json` (format: the engine contract in system instructions).\n" % tag(n),
                       labels=("role:" + shaper, c.ITER + str(n), "loop:shaper"))
    c.update_report(it, {"phase": "shape", "shape_issue": s, "shaper": shaper})
    c.launch(s)


# ---------------------------------------------------------------- the Policymaker on triggers

def audit_revision():
    return c.git(".", "log", "-1", "--format=%H", "--", "audit/", check=False).stdout.strip() or None


class PolicyScriptError(Exception):
    """A policy script returned data the engine refuses."""


def run_policy_script(name, facts, max_bytes=POLICY_OUTPUT_BYTES):
    """The JSON object `policy/<name>` prints for `facts`, run in the tokenless job (`python3 <script> facts.json`). Anything
    else - no output, over `max_bytes`, a failed run, not one JSON object - raises PolicyScriptError. None: no such script."""
    path = "%s/%s" % (resources.ROOT, name)
    if not os.path.isfile(path):
        return None
    bundle = tempfile.mkdtemp(prefix="policy-script-")
    try:
        shutil.copy(path, os.path.join(bundle, "script"))
        shutil.copytree(resources.ROOT, os.path.join(bundle, resources.ROOT), symlinks=True)     # data the script keeps in policy/
        with open(os.path.join(bundle, "facts.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(facts, fh)
        with open(os.path.join(bundle, "run.sh"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write('cd "$(dirname "$0")" && python3 script facts.json\n')
        out = c.run_tokenless(bundle, POLICY_SCRIPT_MINUTES)
    finally:
        shutil.rmtree(bundle, ignore_errors=True)
    try:
        with open(os.path.join(out, "exit-code"), encoding="utf-8") as fh:
            code = fh.read().strip()
        with open(os.path.join(out, "stdout.txt"), encoding="utf-8", errors="replace") as fh:
            text = fh.read(max_bytes + 1)
    except (OSError, TypeError):
        raise PolicyScriptError("policy/%s returned no output from the tokenless job" % name)
    if code != "0":
        raise PolicyScriptError("policy/%s exited with %s" % (name, code))
    if len(text) > max_bytes:
        raise PolicyScriptError("policy/%s printed more than %s bytes" % (name, max_bytes))
    try:
        value = json.loads(text)
    except ValueError:
        raise PolicyScriptError("policy/%s did not print JSON" % name)
    if not isinstance(value, dict):
        raise PolicyScriptError("policy/%s did not print a JSON object" % name)
    return value


def policymaker_due(n):
    """Why the Policymaker opens iteration n (None: it does not, the last organization stands). The engine decides two
    things whatever policy says: one session at a time, and until a session ended `done` every iteration starts with one
    (it sets up how the execution layer works). Otherwise `policy/policymaker-trigger.py` decides from the engine's facts.
    Raises PolicyScriptError when the script's output is invalid."""
    if c.list_issues(labels=("role:policymaker",)):
        return None
    if not any(c.report_fields(x["number"]).get("status") == "done"
               for x in c.list_issues(state="closed", labels=("role:policymaker",))):
        return "the first iteration of the loop"
    sessions = c.list_issues(state="all", labels=("role:policymaker",))
    last = max(sessions, key=lambda x: x["number"], default=None)
    state = (read_state(last["number"], SEEN_MARK, last=True) or read_state(last["number"]) or {}) if last else {}
    audit = audit_revision()
    seen = {issue for session in sessions for issue in (read_state(session["number"]) or {}).get("outboxes", [])}
    out = run_policy_script("policymaker-trigger.py", {
        "iteration": n, "no_progress": no_progress(), "triggers": c.params()["policymaker_triggers"],
        "audit_changed": bool(audit and audit != state.get("audit_rev")),
        "new_outboxes": [x for x in session_reports.outboxes() if x["issue"] not in seen],
        "startup_pending": bool(session_startup.pending())})
    reason = (out or {}).get("reason")
    if reason is not None and not (isinstance(reason, str) and 0 < len(reason) <= 200):
        raise PolicyScriptError("policy/policymaker-trigger.py: `reason` must be null or a string of 1 to 200 characters")
    return reason


def start_policymaker(n, why):
    """A Policymaker session that opens iteration n: the iteration's policy phase. The iteration waits for it and for the
    policy-layer sessions it orders; all its passes are this one issue."""
    p = c.create_issue("Policymaker at the start of iteration %s" % tag(n),
                       "Runs because: %s. Change `policy/` and `execution/` where needed; order work in `.session/orders.json`.\n\n"
                       "Input: `.session/loop-state.json`. Rules: `.session/policymaker.md`. Iteration %s waits for you; "
                       "orders of policy-layer roles start when you finish, and you run again when they end.\n" % (why, tag(n)), labels=("role:policymaker", c.ITER + str(n)))
    store_state(p, dict(build_state(n), trigger=why))
    c.launch(p)
    return p


def read_orders(p):
    """The orders the latest pass of a Policymaker session left, ids made unique across sessions and passes:
    `order-<issue>-<id>`, and from the second pass `order-<issue>-<pass>-<id>`."""
    raw = read_state(p, ORDERS_MARK, last=True)
    tasks = raw.get("tasks") if isinstance(raw, dict) else None
    k = raw.get("pass", 1) if isinstance(raw, dict) else 1
    pre = "order-%s-%s" % (p, "%s-" % k if k > 1 else "")
    return [dict(o, id=pre + str(o.get("id")), blocked_by=[pre + str(d) for d in o.get("blocked_by") or []])
            for o in (tasks if isinstance(tasks, list) else []) if isinstance(o, dict)]


def read_wake(p):
    """True when the latest run of a Policymaker session asked to be woken after the tasks it ordered end."""
    raw = read_state(p, ORDERS_MARK, last=True)
    return isinstance(raw, dict) and raw.get("wake") is True


def orders_of(sessions):
    """The orders the given finished Policymaker sessions left, in the order of the sessions."""
    return [o for p in sessions for o in read_orders(p)]


def place_orders(n, it, orders):
    """The orders of a Policymaker session left over from an earlier iteration become tasks of iteration n, running beside the shaper and its graph. True when placed."""
    errs = validate_tasks({"tasks": orders}, ordered=True)
    if errs:
        c.update_report(it, {"orders_refused": "; ".join(errs[:6])[:300]}, "The Policymaker's orders were refused: %s." % "; ".join(errs[:6]))
        return False
    for num in create_tasks(n, orders):
        c.launch(num)
    return True


# ---------------------------------------------------------------- the task graph

TASK_FIELDS = {"id", "title", "role", "body", "model", "effort", "blocked_by", "inputs", "exports", "start_branch", "limits", "consent"}


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


SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def consent_errors(consent):
    """Errors of a task's `consent` object. `secrets` are names only, never values."""
    if not isinstance(consent, dict):
        return ["`consent` must be an object"]
    errors = ["consent: unknown field %r" % k for k in sorted(set(consent) - {"what", "why", "secrets"})]
    for key in ("what", "why"):
        if key in consent and not isinstance(consent[key], str):
            errors.append("consent.%s must be a string" % key)
    secrets = consent.get("secrets", [])
    if not isinstance(secrets, list):
        return errors + ["consent.secrets must be a list of secret names"]
    for k, name in enumerate(secrets):     # the entry is not echoed: it may be a value
        if not isinstance(name, str) or not SECRET_NAME.fullmatch(name):
            errors.append("consent.secrets[%d] is not a secret name (^[A-Z][A-Z0-9_]*$; names only, never values)" % k)
    if len({s for s in secrets if isinstance(s, str)}) != len([s for s in secrets if isinstance(s, str)]):
        errors.append("consent.secrets has duplicate names")
    return errors


def validate_tasks(doc, ordered=False, taken=()):
    """Errors of a task graph (empty: valid). `ordered`: the Policymaker's orders, the only tasks of the roles in
    `policymaker_only_roles` and only of them, unless no shaper is set: then it orders any role. `taken`: ids the iteration already uses."""
    profiles, parameters, config = c.profiles(), c.params(), c.cfg()
    policymaker_roles = config["policymaker_only_roles"]
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    if not isinstance(tasks, list) or not tasks:
        return ["`tasks` must be a non-empty list"]
    if len(tasks) + len(taken) > MAX_TASKS:
        return ["the task graph would hold %s tasks; the cap is %s per iteration" % (len(tasks) + len(taken), MAX_TASKS)]
    # Validate shapes before membership checks or graph traversal: this is agent-authored JSON.
    # Only structurally valid tasks enter the graph; malformed tasks return diagnostics, not exceptions.
    errors, task_ids, graph = [], set(), {}
    for task in tasks:
        if not isinstance(task, dict):
            errors.append("a task is not an object")
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", task_id):
            errors.append("task id must contain only letters, digits and dashes: %r" % task_id)
            continue
        if task_id in task_ids or task_id in taken:
            errors.append("task id missing or duplicated: %r" % task_id)
        task_ids.add(task_id)
        extra = set(task) - TASK_FIELDS
        if extra:
            errors.append("task %s: unknown fields %s" % (task_id, sorted(extra)))
        if "exports" in task:
            errors.extend("task %s: %s" % (task_id, error) for error in session_exports.validate(task["exports"]))
        if "consent" in task:
            errors.extend("task %s: %s" % (task_id, error) for error in consent_errors(task["consent"]))
        role = task.get("role")
        if not isinstance(role, str) or role not in profiles or role == "policymaker":
            errors.append("task %s: role %r has no profile" % (task_id, role))
        elif ordered and parameters["shaper"] and role not in policymaker_roles:
            errors.append("task %s: the Policymaker orders only %s" % (task_id, ", ".join(policymaker_roles)))
        elif not ordered and role in policymaker_roles:
            errors.append("task %s: role `%s` is ordered only by the Policymaker" % (task_id, role))
        for field in ("title", "body"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                errors.append("task %s: `%s` missing" % (task_id, field))
        if task.get("model") and (not isinstance(task["model"], str) or task["model"] not in parameters["models"]):
            errors.append("task %s: unknown model alias %r" % (task_id, task["model"]))
        if task.get("effort") and (not isinstance(task["effort"], str) or task["effort"] not in parameters["efforts"]):
            errors.append("task %s: unknown effort %r" % (task_id, task["effort"]))
        dependencies = task.get("blocked_by", [])
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list) or any(not isinstance(dependency, str) for dependency in dependencies):
            errors.append("task %s: blocked_by must be a list of task ids" % task_id)
        else:
            graph[task_id] = dependencies
    for task_id, dependencies in graph.items():
        unknown_dependencies = [dependency for dependency in dependencies if dependency not in task_ids]
        if unknown_dependencies:
            errors.append("task %s: blocked_by unknown %s" % (task_id, unknown_dependencies))
    if not errors and topological(graph) is None:
        errors.append("blocked_by has a cycle")
    return errors


def topological(graph):
    """Task ids in an order where blockers come first; None on a cycle."""
    order, state = [], {}

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
    """Largest dependency-depth level; a graph statistic, not a concurrency limit."""
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


def instantiate(n, doc):
    """The shaper's graph: validate it, create its tasks, start what is free. Returns the number of tasks, or an error text."""
    taken = {t["id"] for t in read_json(tasks_path(n)) or []}
    errs = validate_tasks(doc, taken=taken)
    if errs:
        return "invalid tasks.json: " + "; ".join(errs[:6])
    started = create_tasks(n, doc["tasks"])
    frozen = read_json(tasks_path(n))
    it = find_iteration_issue(n)
    c.update_report(it["number"], {"phase": "run", "tasks": len(frozen), "width": width(frozen)})
    for num in started:
        c.launch(num)
    return len(doc["tasks"])


def create_tasks(n, tasks):
    """Create the issues of validated tasks, wire blocking and consent, add them to the frozen graph of the iteration.
    Returns the issue numbers, for the caller to start."""
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
        if "exports" in t:
            body += "<!-- exports: %s -->\n" % json.dumps(t["exports"], sort_keys=True)
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
            secrets = ask.get("secrets") or []
            req = open_request("consent", "Consent: `%s` session for task \"%s\"" % (t["role"], t["title"]),
                               ask.get("what") or "Run one `%s` session %s." % (t["role"], "beyond the accepted limits: " + ", ".join(why) if why else "with the secrets listed below"),
                               ask.get("why") or t["title"],
                               "The session starts only when you close this as completed; the wider profile and the secrets listed apply to that one session.",
                               ref="task-%s" % number[t["id"]], blocks=(number[t["id"]],), iteration=n,
                               frozen="secrets: %s" % ", ".join(secrets) if secrets else None)
            c.edit_labels(number[t["id"]], add=["consent:%s" % req])
    frozen = (read_json(tasks_path(n)) or []) + [dict(t, issue=number[t["id"]]) for t in tasks]
    c.commit_main({tasks_path(n): json.dumps(frozen, indent=1, ensure_ascii=False) + "\n"}, "iteration %s: task graph" % tag(n))
    return list(number.values())


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
    """The issues of iteration n. A Policymaker session carries an iteration label too, but belongs to no iteration."""
    return [x for x in c.list_issues(state="all", labels=(c.ITER + str(n),))
            if "pull_request" not in x and "role:policymaker" not in c.label_names(x)]


def advance():
    """Idempotent: look at the facts and take the next step. Called at the end of every session, by the owner-request
    handler and by the watchdog, so a lost event never stalls the loop."""
    it = open_iteration()
    if not it:
        return None
    n = iteration_number(it)
    f = c.report_fields(it["number"])
    phase = f.get("phase", "")
    if phase == "shape":
        s = c.issue(int(f["shape_issue"]))
        if s["state"] == "closed":
            fail_iteration(n, "the shaper produced no valid task graph")
    elif phase == "run":
        pending = [x for x in iteration_issues(n) if x["state"] == "open" and c.roles_of(c.label_names(x))]
        if not pending:
            close_iteration(n, "closed")
    elif phase == "policy":
        p = int(f["policy_issue"])
        if "awaiting:orders" in c.label_names(c.issue(p)):
            if orders_ended(p):
                resume_policymaker(p)
        elif policy_settled(n):
            policy_pass(n, it["number"])
    elif phase == "starting":
        pass
    return n


def fail_iteration(n, why, rollback=False):
    """`rollback`: the iteration failed on data the policy supplied; `execution/` and `policy/` return to the last good state."""
    it = find_iteration_issue(n)
    if it:
        if rollback:
            revert_policy(why)
        for x in iteration_issues(n):
            if x["state"] == "open" and c.roles_of(c.label_names(x)):
                for r in c.session_runs():
                    if c.run_issue(r["displayTitle"]) == "session #%s" % x["number"] and r["status"] in ("queued", "in_progress", "waiting"):
                        c.cancel_run(r["databaseId"])
                c.update_report(x["number"], {"status": "crashed", "cause": "iteration-failed"}, why)
                c.close_issue(x["number"], "not planned")
        from loop import close_out
        for x in c.list_issues(labels=("role:policymaker", "awaiting:orders")):       # no one would relaunch it
            close_out(x["number"], x, "crashed", {"cause": "iteration-failed"}, why)
        close_iteration(n, "failed", why)


def session_summary(x, streams=()):
    f = c.report_fields(x["number"])
    names = c.label_names(x)

    def num(k):
        return int(float(f[k])) if f.get(k, "").replace(".", "", 1).isdigit() else 0
    return {"issue": x["number"], "role": (c.roles_of(names) or [""])[0], "model": f.get("model"), "effort": f.get("effort"),
            "startup": session_startup.summary(x["number"]),
            **session_reports.references(x["number"]),
            "status": f.get("status"), "verdict": f.get("verdict"), "improved": f.get("improved") == "true",
            "merged": f.get("merged") == "true", "branch": f.get("branch"), "commit": f.get("commit"),
            "tokens": {"in": num("tokens_in"), "out": num("tokens_out"), "cache_read": num("cache_read"), "cache_write": num("cache_write")},
            "models": json.loads(f["models"]) if f.get("models", "").startswith("{") else {},
            "turns": num("turns"), "peak_context": num("peak_context"), "attempts": num("attempts"), "agent_s": num("agent_s"), "gate_s": num("gate_s"), "total_s": num("total_s"),
            "parked_s": num("parked_s"), "runner": f.get("runner"), "artifacts": num("artifacts"),
            # Named only when the artifact exists: a failed upload (quota) leaves no field, not a dangling name.
            **({"stream": s} if (s := "stream-%s-%s" % (tag(c.iteration_of(names) or 0), x["number"])) in streams else {})}


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
        streams = {a["name"] for a in c.list_artifacts()}
        sessions = {x["number"]: session_summary(x, streams) for x in issues if c.roles_of(c.label_names(x))}

        shp = sessions.get(int(f["shape_issue"])) if f.get("shape_issue", "").isdigit() else None
        tasks = read_json(tasks_path(n)) or []
        work = [s for s in sessions.values() if s is not shp]
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

        opened = [x["number"] for x in c.list_issues(state="all", labels=(REQ, c.ITER + str(n)))]
        record = {
            "schema_version": 1, "iteration": n, "status": status, "failure": failure,
            "started": f.get("started"), "ended": c.iso(), "engine_version": c.engine_version(),
            "input_state": {k: v for k, v in (read_state(it["number"]) or {}).items() if k != "iterations"},
            "policy": {"rev_start": f.get("policy_rev_start"), "rev_end": policy_rev(), "commit_end": c.head(),
                       "orders_refused": f.get("orders_refused"), "session": int(f["policy_issue"]) if f.get("policy_issue", "").isdigit() else None,
                       "trigger": f.get("policy_trigger")},
            "decided_by": f.get("shaper"),
            "tasks": tasks,
            "outcome": {"sessions": list(sessions.values()), "shaper": shp,
                        "verdict": {"value": value, "issuer": c.cfg()["gate"]["kind"], "evaluator_rev": evaluator_rev(),
                                    "improved": any(s["improved"] for s in work)},
                        "tokens": dict(sums, by_model=by_model), "limit_hits": sum(s["attempts"] for s in sessions.values()), "wall_s": int((c.now() - started).total_seconds()),
                        "critical_path_s": critical_path(list(sessions.values()), tasks), "width": int(f.get("width", 0) or 0),
                        "owner_requests": {"opened": opened, "open_at_close": [x["number"] for x in open_requests()]}},
        }
        record["policy"]["changes"] = applied_list(f, "changes")
        record["evaluator_verifications"] = applied_list(f, "verifications")
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
                     "The Policymaker has been told and keeps working toward the current goal; nothing was rolled back.",
                     "Nothing is blocked. Look at the iteration records and tell the Policymaker or change the goal.", ref="no-progress", iteration=n)


# ---------------------------------------------------------------- the goal gate

def check_goal():
    """After an iteration: if an owner-accepted goal gate exists and its command says the goal is reached, either
    ask the owner to confirm the result or end. Without an accepted gate the loop never ends by itself."""
    if os.path.exists("GOAL_REACHED"):
        return
    gate = c.load_yaml("goal/gate.yml")
    if not gate or not gate.get("command"):
        return
    r = c.run_command(gate["command"], ".", int(c.cfg()["gate"].get("timeout_minutes") or 120))
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

def stage_bundle(cand, base, ctx, command):
    """The data the tokenless job receives for the gate: the accepted tree (`baseline/`), the candidate tree
    (`candidate/`), the changed paths and `run.sh`, which runs the command from the accepted tree."""
    bundle = tempfile.mkdtemp(prefix="bundle-")
    for name, src in (("baseline", base), ("candidate", cand)):
        shutil.copytree(src, os.path.join(bundle, name), symlinks=True, ignore=shutil.ignore_patterns(".git"))
    with open(os.path.join(bundle, "changed.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(ctx["changed"]) + "\n")
    env = {"CANDIDATE_SHA": ctx["candidate_sha"], "BASELINE_SHA": ctx["baseline_sha"], "TASK": str(ctx["task"]), "ITERATION": str(ctx["iteration"])}
    with open(os.path.join(bundle, "run.sh"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write('B="$(cd "$(dirname "$0")" && pwd)"\nexport CANDIDATE_DIR="$B/candidate" BASELINE_DIR="$B/baseline" CHANGED_FILES_FILE="$B/changed.txt"\n')
        fh.writelines("export %s=%s\n" % (k, shlex.quote(v)) for k, v in env.items())
        fh.write('cd "$BASELINE_DIR"\nbash -c %s\n' % shlex.quote(command))
    return bundle


def read_verdict(out):
    """The change gate's verdict from the tokenless job's output directory. Anything but a well-formed verdict is
    `inconclusive`, and an `accepted` needs a clean exit: a missing, failed or malformed output never passes."""
    def read(name):
        if out is None:
            return None
        try:
            with open(os.path.join(out, name), encoding="utf-8", errors="replace") as fh:
                return fh.read(VERDICT_BYTES)
        except OSError:
            return None
    stdout, code = read("stdout.txt"), read("exit-code")
    if stdout is None or code is None:
        return {"verdict": "inconclusive", "detail": "the tokenless job returned no verdict artifact"}
    v = parse_verdict(stdout)
    if not isinstance(v, dict) or v.get("verdict") not in ("accepted", "rejected", "inconclusive"):
        return {"verdict": "inconclusive", "detail": "malformed verdict from the gate command (exit %s): %s" % (code.strip(), stdout[-500:])}
    if v["verdict"] == "accepted" and code.strip() != "0":
        return {"verdict": "inconclusive", "detail": "the gate command accepted but exited with %s" % code.strip()}
    v.setdefault("improved", v["verdict"] == "accepted")
    return v


def run_gate(cand, base, ctx):
    """The change gate's verdict for a candidate tree. `owner`: the owner decides (the caller opens the request).
    `script`: the command runs in the tokenless job from the accepted tree (never from the candidate) and prints a
    JSON object last: {"verdict": "accepted|rejected|inconclusive", "improved": bool, "detail": "..."}."""
    g = c.cfg()["gate"]
    if g["kind"] == "owner":
        return {"verdict": "pending-owner"}
    if not g.get("command"):
        return {"verdict": "inconclusive", "detail": "the change gate has no command"}
    minutes = int(g.get("timeout_minutes") or 120)
    bundle = stage_bundle(cand, base, ctx, g["command"])
    try:
        return read_verdict(c.run_tokenless(bundle, minutes))
    finally:
        shutil.rmtree(bundle, ignore_errors=True)


# ---------------------------------------------------------------- rolling back execution/ and policy/

def revert_policy(why):
    """Return `execution/` and `policy/` (and the evaluator paths) to the state in which the last iteration closed (at the start: the setup baseline), open a
    notice for the owner. Does nothing when the tree is already there. The Policymaker's next session sees the notice."""
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
    if resources.revision() == resources.revision(target):
        return False
    c.commit_main({}, "roll back policy/ to %s: %s" % (target[:8], why), restore=(target, resources.restore_paths()))
    open_request("notice", "policy/ rolled back", "The engine returned `execution/` and `policy/` to %s. Reason: %s." % (target[:8], why),
                 "The iteration went wrong in a way policy/ or execution/ may have caused: %s." % why,
                 "Nothing is blocked. The Policymaker sees this notice in its next session.", ref="rollback-%s" % c.head()[:8])
    return True


def waiting_on_owner():
    open_tasks = [x for x in c.list_issues() if "awaiting:owner" in c.label_names(x)]
    return bool(open_tasks)


def overdue():
    """The open iteration has run longer than the Policymaker's ceiling for an iteration (waiting for the owner excepted)."""
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
