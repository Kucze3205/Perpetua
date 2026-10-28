"""Trusted report snapshots and the raw suggestion outbox of each session, archived by the engine.

What a suggestion is, which are pending and how they are decided belongs to policy (`policy/session-state.py`,
`policy/policymaker-trigger.py`); the engine only keeps the raw outbox where no session can write.
"""
import glob
import hashlib
import json
import os

import common as c
import session_inputs

ROOT = ".github/loop/data/session-reports"
OUTBOXES = "iterations/suggestions"
OUTBOX_BYTES = 256 * 1024
FINAL = ("done", "partial", "crashed", "rejected")      # a parked (`paused`) session freezes nothing


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def report_snapshot(n):
    report = c.find_report(n)
    if not report:
        return None
    return {"issue": n, "source": report.get("html_url") or
            "https://github.com/%s/issues/%s#issuecomment-%s" % (c.REPO, n, report["id"]),
            "snapshot": digest(report["body"]), "comment": report["id"], "body": report["body"]}


def source_records(n=None):
    pattern = "%s/%s/*.json" % (ROOT, n if n is not None else "[0-9]*")
    out = []
    for path in sorted(glob.glob(pattern)):
        text = c.safe_read(".", path)
        if text:
            out.append((path, json.loads(text)))
    return out


def capture(n, work=None):
    """Freeze a trusted report and archive the raw suggestion outbox. Replaying identical output is a no-op."""
    report = report_snapshot(n)
    if not report or c.fields(report["body"]).get("status") not in FINAL:
        return
    files = {}
    record = {"schema_version": 1, "report": report}
    path = "%s/%s/%s.json" % (ROOT, n, digest(encoded(record)))
    if not os.path.exists(path):
        files[path] = encoded(record)
    outbox = "%s/%s.json" % (OUTBOXES, n)
    text = c.safe_read(work, ".session/suggestions.json") if work else None
    if text is not None and not os.path.exists(outbox):
        files[outbox] = text[:OUTBOX_BYTES]
    if files:
        c.commit_main(files, "session #%s: freeze report and suggestion outbox" % n)


def outboxes(visible=lambda issue: True):
    """The archived raw outboxes: [{issue, raw}] by session number."""
    out = []
    for path in glob.glob(OUTBOXES + "/[0-9]*.json"):
        issue = int(os.path.basename(path)[:-5])
        text = c.safe_read(".", path)
        if text is not None and visible(issue):
            out.append({"issue": issue, "raw": text})
    return sorted(out, key=lambda x: x["issue"])


def references(n):
    return {"report_snapshots": [path for path, _ in source_records(n)]}


def stage(state, directory, role=None):
    """Actual trusted comment bodies, readable without gh or credentials."""
    reports, seen = [], set()
    for line in state.get("iterations", [])[-5:]:
        for issue in c.list_issues(state="all", labels=(c.ITER + str(line["n"]),)):
            n = issue["number"]
            if (role and session_inputs.restricted(role) and not session_inputs.visible(n)):
                continue
            if n in seen or not c.roles_of(c.label_names(issue)):
                continue
            seen.add(n)
            report = report_snapshot(n)
            if report:
                reports.append(report)
    out = os.path.join(directory, "reports")
    os.makedirs(out, exist_ok=True)
    index = []
    for report in reports:
        with open(os.path.join(out, "%s.json" % report["issue"]), "w", encoding="utf-8") as fh:
            fh.write(encoded(report))
        index.append({"issue": report["issue"], "path": "%s.json" % report["issue"],
                      "source": report["source"], "snapshot": report["snapshot"]})
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as fh:
        fh.write(encoded(index))
