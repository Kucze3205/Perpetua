"""Applying the Policymaker's output: held while its orders run, then merged into `policy/`.
"""
import json

import closing
import common as c
import iteration as it
import managed_resources as resources
import merge
import owner_requests as requests


def hold_policymaker_output(n, work):
    """Keep a finished Policymaker session's orders on its issue until the iteration continues; a run without orders keeps
    an empty entry, so the orders of an earlier run are never read again. Returns the report prose."""
    text = c.safe_read(work, ".session/orders.json")
    try:
        doc = json.loads(text) if text else {}
    except ValueError:
        doc = {}
        text = None
        prose = "`.session/orders.json` is not JSON: no orders. "
    else:
        prose = ""
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    wake = isinstance(doc, dict) and doc.get("wake") is True
    later = {"merge": "later"} if isinstance(doc, dict) and doc.get("merge") == "later" else {}
    it.store_state(n, dict({"pass": it.policy_pass_of(n), "tasks": tasks if isinstance(tasks, list) else [], "wake": wake}, **later), it.ORDERS_MARK)
    return prose + "Waiting for the iteration to continue: then `policy/` merges%s." % (" and the orders start" if text else "")


def apply_policy(keeps_open=lambda number: False, defers=lambda number: False):
    """Merge the finished Policymaker sessions, oldest first, and return the numbers of those that merged. An unmerged one
    counts as a failed Policymaker session. A merged one is closed, unless `keeps_open(number)`: it is relaunched after
    the tasks it ordered with `wake`, so it waits (`awaiting:orders`). One that `defers(number)` is not merged yet: it waits
    the same way."""
    merged = []
    held = [x for x in c.list_issues(labels=("role:policymaker",)) if "awaiting:iteration" in c.label_names(x)]     # a filter on the new label lags
    for x in sorted(held, key=lambda x: x["number"]):
        if defers(x["number"]):
            c.update_report(x["number"], {"status": "done", "deferred": "true"}, "Its changes merge at the end of the policy phase.")
            c.edit_labels(x["number"], add=["awaiting:orders"], remove=["awaiting:iteration"])
            continue
        fields = c.report_fields(x["number"])
        verified = resources.layered() and fields.get("verifier_passed") == "true"
        verification = {}
        if verified:
            verifier = int(fields["verifier_issue"])
            body = c.issue(verifier)["body"]
            verification = dict(verified=True, verifier=verifier,
                                checked_commit=c.marker(body, "checked-commit"),
                                checked_evaluator_rev=json.loads(c.marker(body, "checked-evaluator-rev") or "null"))
            if not verification["checked_commit"] or not verification["checked_evaluator_rev"]:
                closing.close_out(x["number"], x, "blocked", {"merged": "false"}, "Verifier has no pinned snapshot; request a fresh verification.")
                continue
        if fields.get("owner_accepted") == "true" and fields.get("owner_accepted_commit"):
            verification.setdefault("checked_commit", fields["owner_accepted_commit"])
            verification["override"] = {"verdict": "accepted", "improved": True, "detail": "accepted by the owner"}
        r = merge.merge_branch(x["number"], "policymaker", x, boundary=True, **verification)
        if r["result"] == "awaiting-owner":
            req = requests.open_request("merge-acceptance", "Accept the change of task #%s: %s" % (x["number"], x["title"]),
                                  "The complete Policymaker candidate needs your change gate verdict.",
                                  "The change gate is set to you.", "Completed accepts the candidate for the next iteration boundary.",
                                  ref="task-%s" % x["number"], iteration=c.iteration_of(c.label_names(x)))
            c.edit_labels(x["number"], add=["awaiting:owner"], remove=["awaiting:iteration"])
            c.update_report(x["number"], {"status": "done", "merged": "pending-owner"}, "Waiting for the owner: request #%s." % req)
            continue
        if r["result"] == "awaiting-verifier":
            v = merge.request_verifier(x["number"], x, c.iteration_of(c.label_names(x)) or 0, r["changed"])
            c.edit_labels(x["number"], add=["awaiting:verifier"], remove=["awaiting:iteration"])
            c.update_report(x["number"], {"status": "done", "merged": "pending-verifier"},
                            "The complete managed set waits for the evaluator verifier (#%s)." % v)
            continue
        st, upd, pr = merge.merge_outcome(r)
        if st == "done":
            merged.append(x["number"])
            if keeps_open(x["number"]):
                c.update_report(x["number"], dict(upd, status="done"), pr)
                c.edit_labels(x["number"], add=["awaiting:orders"], remove=["awaiting:iteration"])
                continue
        else:
            merge.tag_unmerged(x["number"], c.iteration_of(c.label_names(x)) or 0, {"commit": "x"})
        closing.close_out(x["number"], x, st, upd, pr)
    return merged
