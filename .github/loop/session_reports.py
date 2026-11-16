"""Trusted report snapshots and the raw suggestion outbox of each session, archived by the engine.

What a suggestion is, which are pending and how they are decided belongs to policy (`policy/session-state.py`,
`policy/policymaker-trigger.py`); the engine only keeps the raw outbox where no session can write.
"""
import glob
import hashlib
import json
import os

import common as c

ROOT = ".github/loop/data/session-reports"
OUTBOXES = "iterations/suggestions"
OUTBOX_BYTES = 256 * 1024
FINAL = ("done", "partial", "crashed", "rejected")      # a parked (`paused`) session freezes nothing
NOTES = {}      # session -> (ref, files): the journal of an unfinished session, committed together with its report


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


def queue_notes(n, ref, files):
    """The journal files of an unfinished session wait for `capture`, so the session's records are one commit."""
    NOTES[n] = (ref, files)


def capture(n, work=None):
    """Freeze a trusted report, archive the raw suggestion outbox and bring in the journal of an unfinished session: one
    commit `epilogue #N: session report`, its files listed in the body. Replaying identical output is a no-op."""
    files = {}
    report = report_snapshot(n)
    if report and c.fields(report["body"]).get("status") in FINAL:
        record = {"schema_version": 1, "report": report}
        path = "%s/%s/%s.json" % (ROOT, n, digest(encoded(record)))
        if not os.path.exists(path):
            files[path] = encoded(record)
        outbox = "%s/%s.json" % (OUTBOXES, n)
        text = c.safe_read(work, ".session/suggestions.json") if work else None
        if text is not None and not os.path.exists(outbox):
            files[outbox] = text[:OUTBOX_BYTES]
    notes = NOTES.pop(n, None)
    if files or notes:
        listed = list(files) + (notes[1] if notes else [])
        body = "\n".join("- " + p for p in listed)
        c.commit_main(files, "epilogue #%s: session report\n\n%s" % (n, body), restore=notes)


def outboxes():
    """The archived raw outboxes: [{issue, raw}] by session number."""
    out = []
    for path in glob.glob(OUTBOXES + "/[0-9]*.json"):
        issue = int(os.path.basename(path)[:-5])
        text = c.safe_read(".", path)
        if text is not None:
            out.append({"issue": issue, "raw": text})
    return sorted(out, key=lambda x: x["issue"])


def references(n):
    return {"report_snapshots": [path for path, _ in source_records(n)]}


def stage(state, directory):
    """Actual trusted comment bodies, readable without gh or credentials."""
    reports, seen = [], set()
    for line in state.get("iterations", [])[-5:]:
        for issue in c.list_issues(state="all", labels=(c.ITER + str(line["n"]),)):
            n = issue["number"]
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
