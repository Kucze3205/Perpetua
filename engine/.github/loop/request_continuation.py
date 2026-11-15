"""What happens after the owner closes an owner request: the answer is applied, an accepted merge
lands, and requests whose `closed` event was lost are swept.
"""
import json

import closing
import common as c
import iteration as it
import managed_resources as resources
import merge
import owner_requests as requests
import watchdog


def on_request_closed(n):
    x = c.issue(n)
    names = c.label_names(x)
    if requests.REQ not in names or requests.HANDLED in names or x["state"] != "closed":
        return
    kind = c.marker(x["body"], "request-kind")
    # The trusted snapshot controls the type even if someone edits the issue's markers.
    snapshot = requests.read(n)
    if snapshot or kind in requests.KINDS:
        status = requests.settle(n, x)
        if status == "waiting":
            return
        if status == "approved":
            c.edit_labels(n, add=[requests.APPROVED])
        for d in c.blocking(n):
            if d["state"] == "open":
                if status == "approved":
                    c.launch(d["number"])
                else:
                    closing.close_out(d["number"], d, "blocked", {"cause": "owner-request-" + status},
                              "Owner request #%s ended as %s." % (n, status))
        c.edit_labels(n, add=[requests.HANDLED])
        it.advance()
        watchdog.reconcile()
        return
    yes = x.get("state_reason") == "completed"
    ref = c.marker(x["body"], "request-ref") or ""
    if kind in ("goal-gate", "goal-change") and yes:
        text = requests.frozen_text(x["body"])
        if text is None:
            c.post_comment(n, "Not applied: the proposed text changed after the request was opened. The loop will open a new request.")
        else:
            c.commit_main({"goal/gate.yml" if kind == "goal-gate" else "goal/goal.md": text.rstrip("\n") + "\n"}, "owner-answer #%s: accepted %s" % (n, kind))
    elif kind == "result-confirmation" and yes:
        c.commit_main({"GOAL_REACHED": "confirmed by the owner (#%s)\n" % n}, "owner-answer #%s: goal reached" % n)
    elif kind == "merge-acceptance" and ref.startswith("task-"):
        settle_owner_merge(int(ref[5:]), yes)
    elif kind == "consent":
        for d in c.blocking(n):
            if d["state"] == "open":
                if yes:
                    c.launch(d["number"])
                else:
                    closing.close_out(d["number"], d, "blocked", {"cause": "consent-declined"}, "The owner declined the consent request #%s." % n)
    c.edit_labels(n, add=[requests.HANDLED])
    it.advance()
    watchdog.reconcile()


def settle_owner_merge(t, yes):
    ti = c.issue(t)
    if ti["state"] != "open" or "awaiting:owner" not in c.label_names(ti):
        return
    role = c.roles_of(c.label_names(ti))[0]
    fields = c.report_fields(t)
    verifier = int(fields["verifier_issue"]) if fields.get("verifier_passed") == "true" and fields.get("verifier_issue", "").isdigit() else None
    vi = c.issue(verifier) if verifier else None
    if yes and resources.layered() and role == "policymaker":
        c.edit_labels(t, add=["awaiting:iteration"], remove=["awaiting:owner"])
        c.git(".", "fetch", "-q", "origin", "+refs/heads/task/%s:refs/remotes/origin/task/%s" % (t, t), remote=True)
        accepted = c.git(".", "rev-parse", "origin/task/%s" % t).stdout.strip()
        c.update_report(t, {"merged": "pending-iteration", "owner_accepted": "true", "owner_accepted_commit": accepted},
                        "Accepted by the owner; the complete managed set waits for the next iteration boundary.")
        if verifier:
            vf = c.report_fields(verifier)
            it.record_verification(verifier, vi, vf.get("status"), vf.get("result"), {"result": "pending-iteration"}, owner=True)
        return
    if yes:
        r = merge.merge_branch(t, role, ti, override={"verdict": "accepted", "improved": True, "detail": "accepted by the owner"},
                         verified=bool(verifier), verifier=verifier,
                         checked_commit=c.marker(vi["body"], "checked-commit") if vi else None,
                         checked_evaluator_rev=json.loads(c.marker(vi["body"], "checked-evaluator-rev") or "null") if vi else None)
    else:
        r = {"result": "gate-rejected", "detail": "declined by the owner", "verdict": {"verdict": "rejected", "detail": "declined by the owner"}}
    if verifier:
        vf = c.report_fields(verifier)
        it.record_verification(verifier, vi, vf.get("status"), vf.get("result"), r, owner=True)
    st, upd, pr = merge.merge_outcome(r)
    k = c.iteration_of(c.label_names(ti)) or 0
    if st != "done":
        merge.tag_unmerged(t, k, {"commit": "x"})
    closing.close_out(t, ti, st, upd, pr)


def sweep_requests():
    """The only way an owner's answer reaches the loop: it looks at closed requests no one has handled yet."""
    for x in c.list_issues(state="closed", labels=(requests.REQ,), limit=30):
        if requests.HANDLED not in c.label_names(x):
            on_request_closed(x["number"])
