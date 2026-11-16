"""The job "Prepare the checks" (`prepare-checks`; GitHub token, the engine's code only): what "Check the results" runs.

It takes the session's results in as "Take in results" does (`session_finalize.receive`), imports the candidate
(`merge.fetch_candidate`: the same commit "Take in results" makes, as the import has fixed dates) and walks the code "Take in
results" is about to walk under `check_results.planning`: every bundle is recorded instead of run, and counts as passed. That
is `## Verification` on the candidate, the merge up to its push (`merge.attempt`: `layer-rules.py`, `check`, the change gate
from the accepted tree), the shaper's task graph and the Policymaker's orders (`graph-rules.py`), the merge of the
Policymaker's managed set at the iteration boundary, and the evaluator change a verifier passed. What it predicts wrong costs
time, never correctness: a bundle it did not plan, or planned on another main, goes to a Tokenless run in "Take in results".

The bundles and the engine's code that runs them (`check_results.run`) are one artifact, pinned by digest (`check_results.ship`).
Nothing planned: no artifact, and "Check the results" does not run.
"""
import json
import os
import tempfile

import check_results
import common as c
import iteration as it
import managed_resources as resources
import merge
import policy_apply
import policymaker_session
import session_finalize
import task_graph as graph


def prepare(n, artifact, baseline, digest, out):
    """Plan the checks of session `n` from its results (`artifact`, the executor `baseline` pinned by `digest`); leave the
    artifact in `out`. Job outputs: `count` (bundles), `minutes` (the time limit of the job that runs them), `digest`."""
    tmp = os.environ.get("RUNNER_TEMP", tempfile.gettempdir())
    work = session_finalize.receive(n, artifact, baseline, digest, tmp)
    root = os.path.join(tmp, "checks")
    with check_results.planning(os.path.join(root, "bundles")) as plan:
        try:
            walk(n, work, tmp)
        except (Exception, SystemExit) as error:        # a prediction: "Take in results" runs what is missing
            c.say("session #%s: the checks were planned only in part: %s" % (n, error))
    check_results.ship(plan, out)


def status(n, work, tmp, script):
    """The status `finalize` will read: a script's exit code, or the one the session's report gives."""
    if script:
        return "done" if (c.safe_read(tmp, "agent-exit") or "").strip() == "0" else "crashed"
    text = c.safe_read(work, ".session/report.md")
    return c.fields(text).get("status") if text else c.report_fields(n).get("status")


def walk(n, work, tmp):
    """The checks "Take in results" runs for session `n`, in its order (`session_finalize.finalize`, `after_session`)."""
    i = c.issue(n)
    labels = c.label_names(i)
    role = c.roles_of(labels)[0]
    script = (policymaker_session.profile_of(role) or {}).get("agent") is False
    k = c.iteration_of(labels) or 0
    sha, touched = merge.fetch_candidate(work, n)
    if not sha or touched or status(n, work, tmp, script) != "done":
        return
    if not script:
        with merge.Tree("task/%s" % n) as cand:
            merge.run_verification(cand, i["body"], n)
    if role == "policymaker":         # held: its managed set merges and its orders start when the iteration goes on
        tried(n, role, i, k=it.policy_pass_of(n), boundary=True)
        orders = it.read_orders(n, policy_apply.orders_state(n, work)[0])
        if orders:
            it.validate_tasks({"tasks": orders}, ordered=True, taken={t["id"] for t in graph.read(k)})
        return
    tried(n, role, i)
    if "loop:shaper" in labels:
        try:
            doc = json.loads(c.safe_read(work, ".session/tasks.json") or "")
        except ValueError:
            doc = None
        if doc is not None:
            it.validate_tasks(doc, taken={t["id"] for t in graph.read(k)})
    if role == c.cfg()["verifier_role"]:
        verified(n, i, work)


def verified(n, i, work):
    """The evaluator change a verifier passed merges in its epilogue (`merge.settle_verification`)."""
    target = c.marker(i["body"], "verifies")
    if not (target and target.isdigit() and c.fields(c.safe_read(work, ".session/report.md") or "").get("result") == "pass"):
        return
    t = int(target)
    ti = c.issue(t)
    trole = c.roles_of(c.label_names(ti))[0]
    checked, rev = c.marker(i["body"], "checked-commit"), json.loads(c.marker(i["body"], "checked-evaluator-rev") or "null")
    if (ti["state"] == "open" and "awaiting:verifier" in c.label_names(ti) and c.report_fields(t).get("verifier_issue") == str(n)
            and checked and rev and not (resources.layered() and trole == "policymaker")):
        tried(t, trole, ti, k=it.policy_pass_of(t) if trole == "policymaker" else 1, verified=True, checked_commit=checked,
              checked_evaluator_rev=rev)


def tried(n, role, i, **kw):
    """The checks of a merge of `task/N`, up to its push (which planning never reaches)."""
    with merge.attempt(n, role, i, **kw):
        pass
