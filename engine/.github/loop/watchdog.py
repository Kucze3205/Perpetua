"""The watchdog: parking a session until its limit resets, resuming it, reconciling the loop's state and
kicking it when nothing runs.
"""
import time
from datetime import timedelta

import closing
import common as c
import iteration as it
import iteration_start as start


MAX_AGE_DAYS = 30
MAX_KICKS = 3
CRASH_STREAK = 3                # consecutive `crashed` without a commit
MAX_SLEEP_S = 340 * 60          # resume.yml: a job is limited to 360 min; a longer park is the watchdog's
GRACE_MIN = 30                  # covers dispatch latency, never working time
STAGE_RETRY_MIN = (5, 30)       # the waits before a session GitHub kept from starting is tried again


def park(n, i, f, upd, prose, reset, head):
    """Park for the provider; the clock is the limit reset. Returns None when parked, else why the session ends.
    Waiting costs nothing, so it is bounded by the age limit and by the progress breaker: K resumes in a row that used
    the agent and moved nothing (no turns, the branch head unmoved) open an outage."""
    kk = int(f.get("attempts", 0)) + 1
    if (c.now() - c.parse_time(i["created_at"])).days >= MAX_AGE_DAYS:
        return "The issue is too old."
    spent = bool(upd.get("agent_s") or (upd.get("tokens_in") or 0) + (upd.get("tokens_out") or 0))
    moved = bool(upd.get("turns")) or bool(f.get("head") and head != f.get("head"))
    idle = 0 if moved else int(f.get("idle_resumes", 0)) + (spent and bool(f.get("paused_at")))   # only a resume counts
    limit = c.cfg()["idle_resumes"]
    if limit and idle >= limit:
        c.open_outage("Session #%s resumed %s times in a row without progress" % (n, idle),
                      "Each resume used the agent and produced no turn and no commit.",
                      "Look at the session's stream and report, fix the cause, then close this issue.")
        return "%s resumes in a row used the agent without progress." % idle
    backoff = c.limits()["backoff_hours"]      # when the session result carries no limit reset time
    when = reset or c.now() + timedelta(hours=backoff[min(kk - 1, len(backoff) - 1)])
    c.update_report(n, dict(upd, status="paused", attempts=kk, idle_resumes=idle, head=head, resume_after=c.iso(when), paused_at=c.iso()), prose)
    c.edit_labels(n, add=["blocked:rate-limit"])
    c.workflow_run("resume.yml", issue=n)      # the clock lives in a workflow: GitHub's cron lags by hours
    return None


def stage_failed(n, why):
    """GitHub kept session n from starting (*Prepare the session* failed, or the policy scripts' job left no verdict): not
    the session's fault, so it is parked and resumed, not left to the watchdog's cron. The third time in a row is an outage."""
    if not c.guard_ok() or c.outage_open():
        return
    i = c.issue(n)
    if i["state"] != "open" or not c.in_loop(c.label_names(i)):        # a start that ended it on purpose closed it
        return
    k = int(c.report_fields(n).get("stage_failures") or 0)
    if k >= len(STAGE_RETRY_MIN):
        c.update_report(n, {"stage_failures": None})
        c.open_outage("session #%s failed to start %s times in a row" % (n, k + 1), why,
                      "Look at the latest runs of session #%s in the Actions tab, fix the cause, then close this issue." % n)
        return
    when = c.now() + timedelta(minutes=STAGE_RETRY_MIN[k])
    c.update_report(n, {"status": "paused", "cause": "stage-failed", "stage_failures": k + 1, "resume_after": c.iso(when)},
                    "The session did not start (%s); next attempt at %s." % (why, c.iso(when)))
    c.edit_labels(n, add=["blocked:rate-limit"])
    c.workflow_run("resume.yml", issue=n)


def reconcile():
    """End of every session: a loop with no commitments moves on at once, not at the next cron (it lags by hours)."""
    if c.guard_ok() and not c.outage_open():
        it.advance()
        drive()


def drive():
    """Common tail of the watchdog and the epilogue: a crash streak -> outage; parked past due -> resume; stalled ->
    roll back policy; no commitments -> kick or start the next iteration."""
    if crash_streak():
        c.open_outage("%s sessions in a row crashed without a commit" % CRASH_STREAK,
                      "The last %s sessions ended `crashed` with no commit. The counter is blind to the cause." % CRASH_STREAK,
                      "Look at the machine cause (`cause`) in the reports of the latest sessions and fix it.")
        c.say("outage: %s sessions in a row crashed without a commit" % CRASH_STREAK)
        return 1
    watch_parked()
    if it.overdue():
        it.fail_iteration(it.iteration_number(it.open_iteration()), "the iteration ran past the ceiling in policy/params.yml")
        return 0
    if it.stalled():
        handle_stall()
        return 0
    # Policymaker activity alone must not hold up the next iteration; core work keeps its commitments.
    if not it.open_iteration() and not any(c.roles_of(c.label_names(x)) != ["policymaker"] for x in loop_open()):
        return kick()
    if commitments():
        c.say("quiet: commitments in progress")
        return 0
    return kick()


def handle_stall():
    """The iteration did not close in time: return `policy/` to the last good state, give up the iteration and start over;
    the next iteration's Policymaker sees the notice."""
    cur = it.open_iteration()
    reverted = it.revert_policy("the iteration did not close within the quiet time")
    it.fail_iteration(it.iteration_number(cur), "stalled%s" % ("; policy/ rolled back" if reverted else ""))


def crash_streak():
    # counted from the close of the last outage: the same crashes do not open a second one
    closed = c.list_issues(state="closed", labels=("outage",))
    since = max([x["closed_at"] for x in closed if x.get("closed_at")], default="")
    sessions = [x for x in c.recent_closed() if x["closed_at"] > since and c.in_loop(c.label_names(x))]
    if len(sessions) < CRASH_STREAK:
        return False
    for x in sessions[:CRASH_STREAK]:
        f = c.report_fields(x["number"])
        if f.get("status") != "crashed" or f.get("commit"):
            return False
    return True


def parked_until(n):
    if "blocked:rate-limit" not in c.label_names(c.issue(n)):
        return None
    return c.report_fields(n).get("resume_after")


def resume(n):
    """Sleeps to `resume_after` and resumes the parked issue. The watchdog repeating it is harmless: launch deduplicates.
    A park with a new date has its own resume run from the epilogue."""
    due = c.report_fields(n).get("resume_after")
    if not due:
        c.say("#%s: no resume_after, the resume is the watchdog's" % n)
        return
    wait = (c.parse_time(due) - c.now()).total_seconds()
    if wait > MAX_SLEEP_S:
        c.say("#%s: due %s is past the job limit, sleeping %s s and handing the clock on" % (n, due, MAX_SLEEP_S))
        time.sleep(MAX_SLEEP_S)
        if parked_until(n) == due:
            c.workflow_run("resume.yml", issue=n)
        return
    if wait > 0:
        c.say("#%s: sleeping %s s until %s" % (n, int(wait), due))
        time.sleep(wait)
    if parked_until(n) == due and c.launch(n):
        c.say("#%s: resumed on time" % n)


def watch_parked():
    for x in c.list_issues(labels=("awaiting:orders",)):         # a Policymaker waiting for its orders is bounded by the age limit too
        if (c.now() - c.parse_time(x["created_at"])).days >= MAX_AGE_DAYS:
            closing.close_out(x["number"], x, "crashed", {"cause": "age-limit"}, "Age limit: %s days." % MAX_AGE_DAYS)
            it.advance()
    for x in c.list_issues(labels=("blocked:rate-limit",)):
        f = c.report_fields(x["number"])
        if (c.now() - c.parse_time(x["created_at"])).days >= MAX_AGE_DAYS:
            closing.close_out(x["number"], x, "crashed", {"cause": "age-limit"}, "Age limit: %s days." % MAX_AGE_DAYS)
            continue
        if f.get("resume_after") and c.parse_time(f["resume_after"]) <= c.now():
            c.launch(x["number"])


def loop_open():
    """The only source of issues for the watchdog and for commitments: without `loop:iteration N` and a role the loop does not see an issue."""
    return [x for x in c.list_issues() if c.in_loop(c.label_names(x))]


def commitments():
    """The loop lives <=> there is a commitment: a run in progress, a park with a date ahead, a fresh dispatch, a wait for the owner or verifier."""
    if c.LAUNCHED:
        return True
    runs = c.session_runs()
    if any(r["status"] in ("queued", "in_progress", "waiting") for r in runs):
        return True
    recent = {c.run_issue(r["displayTitle"]) for r in runs if c.now() - c.parse_time(r["createdAt"]) < timedelta(minutes=GRACE_MIN)}
    for x in loop_open():
        names = c.label_names(x)
        f = c.report_fields(x["number"])
        if "blocked:rate-limit" in names and f.get("resume_after") and c.parse_time(f["resume_after"]) > c.now():
            return True
        if names & {"awaiting:owner", "awaiting:verifier"}:
            return True
        if "session #%s" % x["number"] in recent:
            return True
    return False


def kick():
    """A jam: kick before calling. Fruitless kicks are counted in the issue's report."""
    if not it.open_iteration():
        start.begin()          # with the jobs that start an iteration after this check, they do it (iteration_start.prepare)
        return 0
    ready = [x for x in loop_open() if c.is_unblocked(x["number"]) and not (c.label_names(x) & {"blocked:rate-limit", "awaiting:owner", "awaiting:verifier", "awaiting:iteration", "awaiting:orders"})]
    if not ready:
        it.advance()
        return 0
    for x in ready[:12]:
        n = x["number"]
        k = int(c.report_fields(n).get("kicks", 0)) + 1
        if k > MAX_KICKS:
            c.open_outage("the watchdog kicked #%s %s times, no run appeared" % (n, MAX_KICKS),
                          "Dispatch creates no run (broken workflow, rejected call, lost event).",
                          "Check `.github/workflows/`, the Actions tab and the token permissions.")
            c.say("outage: kicked #%s %s times, no run appeared" % (n, MAX_KICKS))
            return 1
        c.update_report(n, {"kicks": k, "kicked": c.iso()})
        if c.launch(n):
            c.say("kicked #%s" % n)
    return 0
