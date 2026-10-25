"""Engine-owned, immutable provenance; accepted changes are pushed with their receipt."""
import glob
import json
import os

import common as c
import managed_resources as resources

ROOT = "iterations/changes"


def path(issue):
    return "%s/change-%s.json" % (ROOT, issue)


def read(filename):
    try:
        with open(filename, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def evaluator_rev(rev, cwd="."):
    return resources.evaluator_revision(rev, cwd)


def association(i):
    active = [x for x in c.list_issues(labels=("loop:map",))
              if c.iteration_of(c.label_names(x)) is not None
              and not os.path.exists("iterations/%04d.json" % c.iteration_of(c.label_names(x)))]
    active.sort(key=lambda x: x["number"])
    applied = c.iteration_of(c.label_names(active[0])) if active else None
    closed = [int(os.path.basename(p)[:-5]) for p in glob.glob("iterations/[0-9][0-9][0-9][0-9].json")]
    # The iteration label on a Policymaker session is its trigger, not its application date.
    return {"source_iteration": c.iteration_of(c.label_names(i)),
            "applied_iteration": applied,
            "effective_from_iteration": applied if applied is not None else max(closed, default=-1) + 1}


def receipt(cand, n, role, i, session_base, baseline, candidate, changed, verifier=None):
    """Add a receipt after the checked code commit, before the atomic push of both commits.

    accepted_commit names the code commit. The following engine-only receipt commit does
    not alter policy/evaluator trees. A failed push publishes neither code nor receipt.
    """
    if not any(resources.is_managed(p) for p in changed):
        return None
    record = dict(schema_version=1, id="change-%s" % n, issue=n, role=role,
                  base_commit=session_base, applied_base_commit=baseline, accepted_commit=candidate,
                  policy_rev_before=resources.revision(baseline, cand),
                  policy_rev_after=resources.revision(candidate, cand),
                  evaluator_rev_before=evaluator_rev(baseline, cand),
                  evaluator_rev_after=evaluator_rev(candidate, cand),
                  changed_paths=changed, applied_at=c.iso(), verifier_issue=verifier,
                  **association(i))
    dest = os.path.join(cand, path(n))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    c.git(cand, "add", "-f", "--", path(n))
    c.git(cand, "commit", "-q", "-m", "Record accepted change of session #%s" % n)
    return record


def verification(n, i, status, result, application, owner=False):
    """A verifier verdict and an eventual owner decision are distinct immutable events."""
    target = int(c.marker(i["body"], "verifies"))
    ti = c.issue(target)
    record = dict(schema_version=1, id="verification-%s%s" % (n, "-owner" if owner else ""),
                  issue=target, role=c.roles_of(c.label_names(ti))[0], verifier_issue=n,
                  session_status=status, result=result,
                  checked_commit=c.marker(i["body"], "checked-commit"),
                  checked_evaluator_rev=json.loads(c.marker(i["body"], "checked-evaluator-rev") or "null"),
                  application_result=application["result"],
                  application_detail=application.get("detail") or None,
                  change_id=(application.get("change") or {}).get("id"),
                  recorded_at=c.iso(), **association(ti))
    filename = "%s/%s.json" % (ROOT, record["id"])
    try:
        c.commit_main({filename: json.dumps(record, indent=1, ensure_ascii=False) + "\n"},
                      "Record evaluator verification #%s%s" % (n, " owner decision" if owner else ""),
                      expected={filename: None})
    except c.ConcurrentChange:
        pass                             # repeated epilogue/owner event cannot rewrite a receipt
    return record


def for_iteration(n):
    """Only explicit application associations; old records need no migration or rewriting."""
    changes, verifications = [], []
    for filename in sorted(glob.glob(ROOT + "/*.json")):
        r = read(filename)
        if r and r.get("effective_from_iteration", r.get("applied_iteration")) == n:
            (changes if r["id"].startswith("change-") else verifications).append(r["id"])
    return changes, verifications
