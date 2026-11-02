#!/usr/bin/env python3
"""The iteration layer: shaper -> task graph -> parallel sessions -> record; the Policymaker on triggers; owner
requests; the goal gate; the rollback of `execution/` and `policy/`.

An iteration is an issue labelled `loop:map` + `loop:iteration N`; its sessions (the shaper, the tasks, the Policymaker's
orders) are issues labelled `loop:iteration N` + `role:<name>`. The iteration issue carries the phase in a report
comment; everything else is derived from the issues and from `iterations/` on the default branch. A Policymaker session
carries the label of the iteration whose close started it and belongs to no iteration.
"""
import glob
import json
import os
import re
import shlex
import shutil
import tempfile

import common as c
import managed_resources as resources
import owner_requests as requests
import task_graph as graph
import session_inputs
import session_reports
import session_startup

MAP = "loop:map"
VERDICT_BYTES = 64 * 1024        # what the engine reads of a tokenless job's output
STATE_MARK = "<!-- input-state -->"
ORDERS_MARK = "<!-- policymaker-orders -->"
PASS_MARK = "<!-- policymaker-pass -->"
SEEN_MARK = "<!-- policymaker-audit-seen -->"
POLICY_OUTPUT_BYTES = 4096      # what the engine accepts from a policy script
POLICY_SCRIPT_MINUTES = 10
POLICY_ERRORS = 20       # what the engine accepts from a policy rule script: refusals per answer


def tag(n):
    return "%04d" % n


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


def frontmatter(text):
    """(dict, body) of a markdown file with a YAML frontmatter block."""
    import yaml
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text or "", re.S)
    if not m:
        return {}, text or ""
    d = yaml.safe_load(m.group(1))
    return (d if isinstance(d, dict) else {}), m.group(2)


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


def policymaker_sessions(limit=5, latest=None):
    """The latest Policymaker sessions that ran, newest first, and how they ended. An issue closed before its session
    started (the E2E probe's, say) has no report and is no session. `latest`: a session that just ended, which the list of
    closed issues may not return yet."""
    numbers = {x["number"] for x in c.list_issues(state="closed", labels=("role:policymaker",), limit=3 * limit)}
    out = []
    for n in sorted(numbers | ({latest} if latest else set()), reverse=True):
        status = c.report_fields(n).get("status")
        if status:
            out.append({"issue": n, "status": status})
        if len(out) == limit:
            break
    return out


def policymaker_failures(latest=None):
    """Trailing Policymaker sessions that did not end `done`."""
    k = 0
    for s in policymaker_sessions(2, latest):
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
    closed = c.list_issues(state="closed", labels=(requests.REQ,), limit=10)
    return {
        "schema_version": 1, "iteration": n, "engine_version": c.engine_version(),
        "policy_rev": policy_rev(), "evaluator_rev": evaluator_rev(),
        "goal": {"text": goal, "gate_accepted": gate is not None, "gate": gate},
        "policy_params": c.params(), "engine_params": cfg["params"], "limits": cfg["limits"], "gate": cfg["gate"],
        "owner_requests": {"open": [request_summary(x) for x in requests.open_requests()],
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
    nums = [iteration_number(x) for x in c.list_issues(state="all", labels=(MAP,), limit=5)]
    files = [int(os.path.basename(f)[:4]) for f in record_files()]
    done = [x for x in nums + files if x is not None]
    return max(done) + 1 if done else 0


def closed_without_record():
    """The number of the latest iteration when its issue was closed but no record was written (closed by hand, or the
    closing died); else None."""
    issues = c.list_issues(state="all", labels=(MAP,), limit=5)
    nums = [iteration_number(x) for x in issues]
    if not nums or max(nums) in [iteration_number(x) for x in issues if x["state"] == "open"]:
        return None
    return None if os.path.exists(record_path(max(nums))) else max(nums)


def cadence_ok():
    cad = c.cadence()
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
    try:
        begin_work(n, it)
    except PolicyScriptError as e:
        fail_iteration(n, "invalid policy script output: %s" % e, rollback=True)
    return n


def begin_work(n, it):
    """The execution phase: the policy phase is over. The shaper and the tasks the Policymaker ordered make the
    iteration's graph. A Policymaker that failed twice in a row is reverted: the iteration runs on the last good policy."""
    from loop import apply_policy

    c.update_report(it, {"phase": "starting"})         # leaves "policy": a second run of advance() does not begin again
    orders = orders_of(apply_policy())      # apply_policy: a session left over from an earlier iteration
    latest = c.report_fields(it).get("policy_issue", "")
    if policymaker_failures(int(latest) if latest.isdigit() else None) >= 2:
        revert_policy("two Policymaker sessions in a row failed")
    store_state(it, build_state(n))
    if c.params()["shaper"]:
        if orders:
            place_orders(n, it, orders)
        return begin_shape(n, it)
    if orders:
        place_orders(n, it, orders)
    ordered = graph.read(n)
    if not ordered:
        return fail_iteration(n, "no shaper is set and the Policymaker ordered no valid tasks", rollback=True)
    c.update_report(it, {"phase": "run", "shaper": None, "tasks": len(ordered), "width": graph.width(ordered)})


def policy_pass(n, it):
    """A Policymaker session is over: merge its changes and start the tasks it ordered; their dependencies set the order
    of work. With `wake` the session waits for them and runs again in this iteration; otherwise the policy phase ends."""
    from loop import apply_policy
    placed = {}         # session -> whether it waits for the tasks it ordered

    def settle(p):
        if p in placed:
            return placed[p]
        placed[p] = False
        orders = read_orders(p)
        if not orders:
            return False
        taken = {t["id"] for t in graph.read(n)}
        errs = validate_tasks({"tasks": orders}, ordered=True, taken=taken)
        if errs:
            c.update_report(it, {"orders_refused": "; ".join(errs[:6])[:300]}, "The Policymaker's orders were refused: %s." % "; ".join(errs[:6]))
            return False
        numbers = graph.create_tasks(n, orders)
        for num in numbers:
            c.launch(num)
        if not read_wake(p):
            return False
        c.update_report(p, {"ordered": " ".join(map(str, numbers))})
        placed[p] = True
        return True

    def defer(p):
        """The pass said its changes merge at the end of the policy phase, and it waits for the tasks it ordered: they wait."""
        if not defers(p) or not settle(p):
            return False
        note_long_policy_phase(n, policy_pass_of(p))
        return True
    for p in apply_policy(settle, defer):
        note_long_policy_phase(n, policy_pass_of(p))
        if not placed.get(p):
            store_state(p, {"audit_rev": audit_revision()}, SEEN_MARK)       # the audit it ordered and read is not new
    if not any(placed.values()):
        begin_work(n, it)


def note_long_policy_phase(n, passes):
    """Informational only: nothing waits on it, and the owner's answer changes nothing. Once per iteration, even if answered."""
    k, ref = c.cfg()["policy_passes_notice"], "policy-passes-%s" % n
    if not k or passes < k or any(c.marker(x["body"], "request-ref") == ref
                                  for x in c.list_issues(state="all", labels=(requests.REQ + ":notice",), limit=100)):
        return
    requests.open_request("notice", "Policy phase of iteration %s: %s passes" % (n, passes),
                 "The Policymaker has run %s passes in this iteration's policy phase; each pass is a session with its ordered sessions." % passes,
                 "The engine caps nothing; a long policy phase costs time and quota, so you can see it.",
                 "Nothing is blocked. The iteration continues; closing this request changes nothing.", ref=ref, iteration=n)


def defers(p):
    """Whether the latest pass of Policymaker session p stated `"merge": "later"` in its orders: its changes wait for the end of the policy phase."""
    raw = read_state(p, ORDERS_MARK, last=True)
    return isinstance(raw, dict) and raw.get("merge") == "later"


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
    spent = ("deferred", "status", "commit", "fixes", "merged", "verdict", "improved", "detail", "verifier_issue", "verified_commit",
             "verifier_passed", "owner_accepted", "owner_accepted_commit", "ordered")
    c.update_report(p, dict({x: None for x in spent}, **{"pass": k}), "Pass %s: the ordered sessions ended, the Policymaker runs again." % k)
    c.sync_main()
    if f.get("deferred") == "true":          # its changes have not merged: the branch keeps them and moves onto what has
        from loop import Tree
        c.git(".", "fetch", "-q", "origin", "+refs/heads/task/%s:refs/remotes/origin/task/%s" % (p, p), remote=True)
        with Tree("origin/task/%s" % p) as cand:
            if c.git(cand, "rebase", "origin/%s" % c.default_branch(), check=False).returncode:
                c.git(cand, "rebase", "--abort", check=False)           # the end of the phase reports the conflict
            else:
                c.git(cand, "push", "-q", "-f", "origin", "HEAD:refs/heads/task/%s" % p, remote=True)
    else:
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
        return fail_iteration(n, "shaper role `%s` has no profile" % shaper, rollback=True)
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


def policy_errors(name, facts):
    """The refusals `policy/<name>` adds to the engine's own: it prints {"errors": [...]}. No such script: none. Policy can only
    refuse more, so these are appended to the engine's errors, never replace them. Raises PolicyScriptError for an invalid answer."""
    out = run_policy_script(name, facts)
    errors = (out or {}).get("errors", [])
    if not (isinstance(errors, list) and len(errors) <= POLICY_ERRORS and all(isinstance(x, str) and 0 < len(x) <= 300 for x in errors)):
        raise PolicyScriptError("policy/%s: `errors` must be a list of up to %s strings of 1 to 300 characters" % (name, POLICY_ERRORS))
    return ["policy/%s: %s" % (name, x) for x in errors]


def layer_errors(cwd, activation):
    """Why the managed definitions in `cwd` are refused by `policy/layer-rules.py` (empty: accepted). Raises PolicyScriptError."""
    return policy_errors("layer-rules.py", resources.layer_facts(cwd, activation))


def policymaker_due(n):
    """Why the Policymaker opens iteration n (None: it does not, the last organization stands). The engine decides three
    things whatever policy says: one session at a time, until a session ended `done` every iteration starts with one
    (it sets up how the execution layer works), and an iteration after a failed one starts with one (the policy did not
    work). Otherwise `policy/policymaker-trigger.py` decides from the engine's facts.
    Raises PolicyScriptError when the script's output is invalid."""
    if c.list_issues(labels=("role:policymaker",)):
        return None
    if not any(c.report_fields(x["number"]).get("status") == "done"
               for x in c.list_issues(state="closed", labels=("role:policymaker",))):
        return "the first iteration of the loop"
    last = (records() or [None])[-1]
    if last and last["status"] == "failed":
        return ("iteration %s failed: %s" % (tag(last["iteration"]), last.get("failure") or "no reason recorded"))[:200]
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
    return [dict(o, id=pre + str(o.get("id")), **{"pass": k}, blocked_by=[pre + str(d) for d in o.get("blocked_by") or []])
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
    for num in graph.create_tasks(n, orders):
        c.launch(num)
    return True


def validate_tasks(doc, ordered=False, taken=()):
    """Errors of a task graph (empty: valid): the engine's, and when it finds none, those of `policy/graph-rules.py` (raises
    PolicyScriptError for an answer the engine refuses). Policy may refuse a graph the engine accepts, never the other way round."""
    errors = graph.validate_tasks(doc, ordered=ordered, taken=taken)
    if errors:
        return errors
    return policy_errors("graph-rules.py", {"tasks": doc["tasks"], "ordered": ordered, "taken": sorted(taken),
                                             "roles": {role: profile["layer"] for role, profile in c.profiles().items()}})


# ---------------------------------------------------------------- the shaper graph enters execution

def instantiate(n, doc):
    """The shaper's graph: validate it, create its tasks, start what is free. Returns the number of tasks, or an error text."""
    taken = {t["id"] for t in graph.read(n)}
    errs = validate_tasks(doc, taken=taken)
    if errs:
        return "invalid tasks.json: " + "; ".join(errs[:6])
    started = graph.create_tasks(n, doc["tasks"])
    frozen = graph.read(n)
    it = find_iteration_issue(n)
    c.update_report(it["number"], {"phase": "run", "tasks": len(frozen), "width": graph.width(frozen)})
    for num in started:
        c.launch(num)
    return len(doc["tasks"])


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
            fail_iteration(n, "the shaper produced no valid task graph", rollback=True)
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
            try:
                policy_pass(n, it["number"])
            except PolicyScriptError as e:
                fail_iteration(n, "invalid policy script output: %s" % e, rollback=True)
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
        tasks = graph.read(n)
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

        opened = [x["number"] for x in c.list_issues(state="all", labels=(requests.REQ, c.ITER + str(n)))]
        record = {
            "schema_version": 1, "iteration": n, "status": status, "failure": failure,
            "started": f.get("started"), "ended": c.iso(), "engine_version": c.engine_version(),
            "input_state": {k: v for k, v in (read_state(it["number"]) or {}).items() if k != "iterations"},
            "policy": {"rev_start": f.get("policy_rev_start"), "rev_end": policy_rev(), "commit_end": c.head(),
                       "orders_refused": f.get("orders_refused"), "session": int(f["policy_issue"]) if f.get("policy_issue", "").isdigit() else None,
                       "trigger": f.get("policy_trigger"),
                       "passes": policy_pass_of(int(f["policy_issue"])) if f.get("policy_issue", "").isdigit() else 0},
            "decided_by": f.get("shaper"),
            "tasks": tasks,
            "outcome": {"sessions": list(sessions.values()), "shaper": shp,
                        "verdict": {"value": value, "issuer": c.cfg()["gate"]["kind"], "evaluator_rev": evaluator_rev(),
                                    "improved": any(s["improved"] for s in work)},
                        "tokens": dict(sums, by_model=by_model), "limit_hits": sum(s["attempts"] for s in sessions.values()), "wall_s": int((c.now() - started).total_seconds()),
                        "critical_path_s": critical_path(list(sessions.values()), tasks), "width": int(f.get("width", 0) or 0),
                        "owner_requests": {"opened": opened, "open_at_close": [x["number"] for x in requests.open_requests()]}},
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
    keep = c.params()["stream_retention_iterations"]
    if not keep:
        return
    for a in c.list_artifacts():
        m = re.match(r"stream-(\d{4})-", a["name"])
        if m and int(m.group(1)) <= n - keep:
            c.delete_artifact(a["id"])


def note_no_progress(n):
    k = c.cfg()["params"].get("no_progress_iterations")
    if k and no_progress() >= k and not requests.request_open("notice", ref="no-progress"):
        requests.open_request("notice", "No progress for %s iterations" % no_progress(),
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
        if requests.request_open("result-confirmation") or confirmation_declined(ref):
            return
        requests.open_request("result-confirmation", "Confirm the result",
                     "The goal gate says the goal is reached.\n\n%s" % (verdict.get("artifact") or verdict.get("detail") or ""),
                     "Without your confirmation the loop keeps working.",
                     "Completed ends the loop (`GOAL_REACHED`). Not planned: the loop continues; say why in a comment.", ref=ref)
    else:
        c.commit_main({"GOAL_REACHED": "reached at %s\n" % ref}, "goal reached (gate: %s)" % gate["command"])


def confirmation_declined(ref):
    return any(c.marker(x["body"], "request-ref") == ref and x.get("state_reason") == "not_planned"
               for x in c.list_issues(state="closed", labels=(requests.REQ + ":result-confirmation",), limit=100))


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
    requests.open_request("notice", "policy/ rolled back", "The engine returned `execution/` and `policy/` to %s. Reason: %s." % (target[:8], why),
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
