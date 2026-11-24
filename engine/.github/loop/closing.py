"""Closing a session's issue: the report, a successor for a continuation, unblocking and halting dependents.
"""
import re

import common as c
import owner_requests as requests
import session_reports
import session_startup


def block_secret_missing(n, i, name, work=None):
    """The task is blocked and the owner is asked (capability, no value) to provide the secret `name`."""
    title = "Secret `%s` for task #%s" % (name, n)
    if not requests.request_open("capability", title=title):
        requests.open_request("capability", title,
                        "Task #%s has your consent to use the secret `%s`, but the session cannot get it." % (n, name),
                        "The secret is not in the secret pool of the `Session` step of session.yml, or the repository secret is empty.",
                        "Nothing by itself: closing this request creates no secret. Add the name to the `Session` step and set the repository secret, then restart the task.",
                        ref="secret-%s-%s" % (n, name), iteration=c.iteration_of(c.label_names(i)),
                        readiness="Check that `%s` is a repository secret and a line of the `Session` step. Disclose no value." % name)
    close_out(n, i, "blocked", {"cause": "secret-missing:" + name}, "The session did not start: secret `%s` is missing." % name, work)


def spawn_successor(n, i, report_body):
    m = re.search(r"^## Successor\s*\ntitle:\s*(.+)\nbody:\s*(.*?)(?=^## |\Z)", report_body, re.S | re.M)
    if not m:
        return None
    labels = c.label_names(i)
    gen = max([int(l.split(":")[1]) for l in labels if l.startswith("generation:")] or [0])
    if gen >= c.limits()["successor_generations"]:
        return None
    keep = [l for l in labels if l.startswith(("role:", "model:", "effort:", c.ITER, "loop:shaper"))] + ["generation:%s" % (gen + 1)]
    for l in keep:
        if l.startswith("generation:"):
            c.ensure_label(l, "BFD4F2")
    s = c.create_issue(m.group(1).strip(), m.group(2).strip() + "\n\n<!-- start-branch: task/%s -->\n" % n, labels=keep)
    for dep in c.blocking(n):       # the successor inherits what waited for its parent
        c.add_blocker(dep["number"], s)
    return s


def close_out(n, i, status, upd, prose, work=None):
    body = c.update_report(n, dict(upd, status=status), prose)
    session_reports.capture(n, work)
    if status == "done" and "role:policymaker" in c.label_names(i):
        try:
            session_startup.acknowledge(n)
        except (Exception, SystemExit):
            c.say("session #%s: startup notices remain pending" % n)
    succ = spawn_successor(n, i, body) if status == "partial" else None
    c.close_issue(n, "completed" if status == "done" else "not planned")       # first: a close that fails keeps the labels a retry looks for
    c.edit_labels(n, remove=["blocked:rate-limit", "conflict", "awaiting:owner", "awaiting:verifier", "awaiting:iteration", "awaiting:orders"])
    if not succ:        # the successor starts from this branch
        c.git(".", "push", "-q", "origin", "--delete", "task/%s" % n, check=False, remote=True)
    if status == "done":
        unblock(n)
    elif not succ:
        halt_dependents(n, status)
    if succ:
        c.launch(succ)


def unblock(closed):
    for d in c.blocking(closed):
        if d["state"] == "open":
            c.launch(d["number"])


def halt_dependents(closed, status):
    """The producer did not deliver: a consumer does not start on nothing, it closes as `blocked` (cascading)."""
    for d in c.blocking(closed):
        if d["state"] != "open":
            continue
        if "loop:shaper" in c.label_names(d):
            c.launch(d["number"])
        else:
            close_out(d["number"], d, "blocked", {"cause": "producer-#%s-%s" % (closed, status)},
                      "Producer #%s closed as `%s`: it did not deliver, so this task did not start." % (closed, status))
