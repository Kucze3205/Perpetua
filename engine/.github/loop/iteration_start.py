"""The start of an iteration in three steps that the jobs of a run can hold apart: the facts, the decision, the act.

- `prepare` (GitHub token; at the end of "Take in results" and of "Check the loop", when `START_PLAN` says the jobs that
  start an iteration follow): when an iteration is due to start, or the goal gate after one closed, it walks the code the
  start walks under `check_results.planning`: `policy/policymaker-trigger.py` on the engine's facts and the goal gate's
  command are recorded, not run. `check_results.ship` packs them, pinned by digest. Job outputs: `start`, and `count`,
  `minutes`, `digest` of the bundle.
- "Decide whether the Policymaker runs" (`permissions: {}`, no secret, no checkout): `check_results.run` runs them; what
  they printed goes only to its job output `results`.
- "Start the iteration" (`act`, GitHub token): takes that output in as data (`check_results.accept`) and runs the same code
  as everywhere else (`begin`): the goal check, then the start, which opens the Policymaker or begins the execution phase.
  A bundle with the same digest takes its result from the output; any other (main moved, a decision job that failed) goes
  to a Tokenless run, so correctness never depends on the jobs.

Without those jobs (`START_PLAN` unset: tests, "Start the iteration" itself) everything happens at once. A start that was
lost (its run died after it opened the iteration and before it decided) is decided again by whoever looks next (`lost`),
and a goal check that was due is kept on the closed iteration's report (`goal_check`), so a lost run never stalls the loop.
"""
import os

import check_results
import common as c
import iteration as it
import merge
import owner_requests as requests
import session_reports
import session_startup

START_MINUTES = 45      # a start that decided nothing this long after it opened its iteration was lost: deciding takes at
                        # most the trigger's Tokenless run (policy_script.MINUTES and the wait for the run, 25 minutes)


def jobs():
    """Where the bundle goes when the jobs that start an iteration follow this command; None: the start happens at once."""
    return os.environ.get("START_PLAN") or None


def after_close(n, issue):
    """Iteration n (issue `issue`) closed: its goal check is due, then the next iteration."""
    if goal_gate():
        c.update_report(issue, {"goal_check": "due"})
    begin()


def begin():
    """The goal check a closed iteration left due, then the start of the next iteration. Nothing while the jobs follow:
    `prepare` hands both to them."""
    if jobs():
        return None
    due = due_goal()
    if due:
        check_goal(due[0])
        c.update_report(due[1], {"goal_check": None})
    return start_iteration()


def act():
    """The command of "Start the iteration": the output of "Decide whether the Policymaker runs" as data, then the start."""
    check_results.accept(os.environ.get("DECIDE_RESULTS"))
    return begin()


# ---------------------------------------------------------------- the facts, bundled for the decision job

def due():
    """(the iteration a start would decide, the closed iteration whose goal check is due); either may be None."""
    if not c.guard_ok() or c.outage_open():
        return None, None
    c.sync_main()
    cur = it.open_iteration()
    if cur:
        return (it.iteration_number(cur) if lost(cur) else None), None
    goal = due_goal()
    return (it.next_number() if cadence_ok() else None), (goal[0] if goal else None)


def prepare():
    """The bundle of the decision for the jobs that follow, when a start or a goal check is due. Never raises: what it misses,
    "Start the iteration" runs in a Tokenless run."""
    out = jobs()
    if not out:
        return
    try:
        n, goal = due()
    except (Exception, SystemExit) as error:
        c.say("the start of the next iteration was not planned: %s" % error)
        n, goal = None, None
    if n is None and goal is None:
        c.set_output(start="false")
        return
    root = os.path.join(os.environ.get("RUNNER_TEMP") or os.path.dirname(os.path.abspath(out)), "start")
    with check_results.planning(os.path.join(root, "bundles")) as plan:
        try:
            if n is not None:
                policymaker_due(n)
            if goal is not None:
                check_goal(goal)
        except (Exception, SystemExit) as error:      # a prediction: "Start the iteration" runs what is missing
            c.say("the start of the next iteration was planned only in part: %s" % error)
    check_results.ship(plan, out)
    c.set_output(start="true")


# ---------------------------------------------------------------- the start

def cadence_ok():
    cad = c.cadence()
    recs = [r for r in it.records() if r]
    if cad.get("min_interval_minutes") and recs:
        if (c.now() - c.parse_time(recs[-1]["ended"])).total_seconds() < cad["min_interval_minutes"] * 60:
            return False
    if cad.get("daily_iterations"):
        day = [r for r in recs if (c.now() - c.parse_time(r["started"])).total_seconds() < 86400]
        if len(day) >= cad["daily_iterations"]:
            return False
    return True


def lost(cur):
    """The open iteration `cur` was opened and nothing decided whether the Policymaker runs, long enough ago that the run
    that opened it is gone."""
    f = c.report_fields(cur["number"])
    if f.get("phase", "") not in ("", "starting") or f.get("policy_trigger") or f.get("policy_not_needed"):
        return False
    return (c.now() - c.parse_time(f.get("started") or cur["created_at"])).total_seconds() > START_MINUTES * 60


def start_iteration():
    """Begin the next iteration if none is open, or decide the one whose start was lost. The policy layer goes first: a due
    Policymaker session opens the iteration and the iteration waits for it, and for the policy-layer work it orders,
    before the execution phase. Serial by construction: one open iteration at most."""
    if not c.guard_ok() or c.outage_open():
        return None
    c.sync_main()
    cur = it.open_iteration()
    if cur:
        return decide(it.iteration_number(cur), cur["number"]) if lost(cur) else None
    if (gone := it.closed_without_record()) is not None:
        it.revert_policy("iteration %s was closed without a record" % it.tag(gone))
    if not cadence_ok():
        c.say("cadence: next iteration not due yet")
        return None
    if it.failed_streak() >= 3:
        c.open_outage("three iterations in a row failed", "The last three iterations ended `failed` (infrastructure or an invalid task graph).",
                      "Read the iteration records in `iterations/`, fix the cause, close this issue.")
        return None
    n = it.next_number()
    label = c.ITER + str(n)
    for l in (label, it.MAP):
        c.ensure_label(l, "0E8A16" if l == it.MAP else "C5DEF5", "iteration of the loop")
    issue = c.create_issue("Iteration %s" % it.tag(n), "Iteration %s of the loop. State is in the comments and in `iterations/`.\n" % it.tag(n),
                           labels=(it.MAP, label))
    if any(x["number"] < issue for x in c.list_issues(labels=(it.MAP,))):      # two runs started one at once: the lower number stays
        c.close_issue(issue, "not planned")
        return None
    c.update_report(issue, {"phase": "starting", "started": c.iso(), "policy_rev_start": it.policy_rev()})
    return decide(n, issue)


def decide(n, issue):
    """Whether the Policymaker opens iteration n, and the act that follows: it opens the policy phase, or the execution phase
    begins on the last organization. The report says which and why (`policy_trigger` or `policy_not_needed`). Invalid
    output of a policy script fails the iteration and rolls policy back; a script GitHub never ran fails it and rolls nothing back."""
    try:
        why, skip = policymaker_due(n)
        if why and c.guard_ok():
            c.update_report(issue, {"phase": "policy", "policy_issue": start_policymaker(n, why), "policy_trigger": why})
            return n
        c.update_report(issue, {"policy_not_needed": skip or "the loop is stopped"})
        it.begin_work(n, issue)
    except it.PolicyScriptError as e:
        it.fail_iteration(n, "invalid policy script output: %s" % e, rollback=True)
    except check_results.TokenlessUnavailable as e:
        it.fail_iteration(n, str(e))
    return n


# ---------------------------------------------------------------- the Policymaker on triggers

def policymaker_due(n):
    """(why the Policymaker opens iteration n, why it is not needed): one of them is None. The engine decides three things
    whatever policy says: one session at a time, until a session ended `done` every iteration starts with one (it sets up
    how the execution layer works), and an iteration after a failed one starts with one (the policy did not work).
    Otherwise `policy/policymaker-trigger.py` decides from the engine's facts.
    Raises PolicyScriptError when the script's output is invalid."""
    if c.list_issues(labels=("role:policymaker",)):
        return None, "a Policymaker session is still open"
    if not any(c.report_fields(x["number"]).get("status") == "done"
               for x in c.list_issues(state="closed", labels=("role:policymaker",))):
        return "the first iteration of the loop", None
    last = (it.records() or [None])[-1]
    if last and last["status"] == "failed":
        return ("iteration %s failed: %s" % (it.tag(last["iteration"]), last.get("failure") or "no reason recorded"))[:200], None
    sessions = c.list_issues(state="all", labels=("role:policymaker",))
    last = max(sessions, key=lambda x: x["number"], default=None)
    state = (it.read_state(last["number"], it.SEEN_MARK, last=True) or it.read_state(last["number"]) or {}) if last else {}
    audit = it.audit_revision()
    seen = {issue for session in sessions for issue in (it.read_state(session["number"]) or {}).get("outboxes", [])}
    out = it.run_policy_script("policymaker-trigger.py", {
        "iteration": n, "no_progress": it.no_progress(), "triggers": c.params()["policymaker_triggers"],
        "audit_changed": bool(audit and audit != state.get("audit_rev")),
        "new_outboxes": [x for x in session_reports.outboxes() if x["issue"] not in seen],
        "startup_pending": bool(session_startup.pending())})
    reason = (out or {}).get("reason")
    if reason is not None and not (isinstance(reason, str) and 0 < len(reason) <= 200):
        raise it.PolicyScriptError("policy/policymaker-trigger.py: `reason` must be null or a string of 1 to 200 characters")
    return (reason, None) if reason else (None, "no trigger is due")


def start_policymaker(n, why):
    """A Policymaker session that opens iteration n: the iteration's policy phase. The iteration waits for it and for the
    policy-layer sessions it orders; all its passes are this one issue."""
    p = c.create_issue("Policymaker at the start of iteration %s" % it.tag(n),
                       "Runs because: %s. Change `policy/` and `execution/` where needed; order work in `.session/orders.json`.\n\n"
                       "Input: `.session/loop-state.json`. Rules: `.session/policymaker.md`. Iteration %s waits for you; "
                       "orders of policy-layer roles start when you finish, and you run again when they end.\n" % (why, it.tag(n)), labels=("role:policymaker", c.ITER + str(n)))
    it.store_state(p, dict(it.build_state(n), trigger=why))
    c.launch(p)
    return p


# ---------------------------------------------------------------- the goal gate

def goal_gate():
    """The owner-accepted goal gate while the goal is not reached yet; None: there is nothing to check."""
    if os.path.exists("GOAL_REACHED"):
        return None
    gate = c.load_yaml("goal/gate.yml")
    return gate if gate and gate.get("command") else None


def due_goal():
    """(n, issue) of the latest iteration when it is closed and its goal check is due; else None."""
    latest = max(c.list_issues(state="all", labels=(it.MAP,), limit=5), key=lambda x: x["number"], default=None)
    if latest and latest["state"] == "closed" and c.report_fields(latest["number"]).get("goal_check") == "due":
        return it.iteration_number(latest), latest["number"]
    return None


def check_goal(n):
    """After iteration n: if an owner-accepted goal gate exists and its command, run on the committed tree of the default
    branch, says the goal is reached, either ask the owner to confirm the result or end. Without an accepted gate the loop
    never ends by itself."""
    gate = goal_gate()
    if not gate:
        return
    with merge.Tree(c.head()) as tree:
        r = check_results.run_command(gate["command"], tree, int(c.cfg()["gate"].get("timeout_minutes") or 120), purpose="goal gate",
                                      keep=it.VERDICT_BYTES)
    verdict = it.parse_verdict(r.stdout)
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
        c.commit_main({"GOAL_REACHED": "reached at %s\n" % ref}, "iteration %s: goal reached (gate)" % n)


def confirmation_declined(ref):
    return any(c.marker(x["body"], "request-ref") == ref and x.get("state_reason") == "not_planned"
               for x in c.list_issues(state="closed", labels=(requests.REQ + ":result-confirmation",), limit=100))
