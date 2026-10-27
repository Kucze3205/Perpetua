"""Offline recipient history: searchable Session text backed by existing records.

These are disposable views, not another archive. Selection precedes rendering.
"""
import glob
import json
import os

import common as c
import session_inputs
import session_reports


def write(directory, path, value):
    target = os.path.join(directory, path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1) + "\n")


def history(directory, role, full=False):
    """Expose permitted records and exact report snapshots without a model-readable global index."""
    import iteration as it

    restricted = bool(role and session_inputs.restricted(role))
    visibility = {}

    def visible(issue):
        if not restricted:
            return True
        if issue not in visibility:
            visibility[issue] = session_inputs.visible(issue)
        return visibility[issue]

    record_paths = it.record_files() if full else sorted(glob.glob(os.path.join(directory, "exports/records/*.json")))
    tasks, records = {}, 0
    for path in record_paths:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        if full and restricted:
            record = session_inputs.record(record, can_read=visible)
        n = record["iteration"]
        write(directory, "history/records/%04d.json" % n, record)
        records += 1
        for task in record.get("tasks", []):
            if type(task.get("issue")) is int:
                tasks[task["issue"]] = dict(task, iteration=n)
        for session in record.get("outcome", {}).get("sessions", []):
            issue = session["issue"]
            tasks[issue] = dict(tasks.get(issue, {}), **session, iteration=n)

    snapshots = {}
    if full:
        for _, record in session_reports.source_records():
            report = record["report"]
            if visible(report["issue"]):
                snapshots[(report["issue"], report["snapshot"])] = report
    # Keep current reports and pinned suggestion evidence distinct in reports/index.json.
    # Both are searchable as text; identity, not a mutable 'latest' alias, names the body.
    for pattern in ("reports/*.json", "exports/reports/*.json"):
        for path in sorted(glob.glob(os.path.join(directory, pattern))):
            with open(path, encoding="utf-8") as fh:
                report = json.load(fh)
            if isinstance(report, dict) and "body" in report and visible(report["issue"]):
                snapshots[(report["issue"], report["snapshot"])] = report

    for (issue, snapshot), report in snapshots.items():
        task = tasks.setdefault(issue, {"issue": issue, "title": "Session #%s" % issue})
        fields = c.fields(report["body"])
        header = ("# Session #%s\nTask: %s\nRole: %s\nIteration: %s\n"
                  "Status: %s\nVerdict: %s\nSource: %s\nSnapshot: %s\n\n" %
                  (issue, str(task.get("title", "")).replace("\n", " "), task.get("role", ""),
                   task.get("iteration"), fields.get("status", task.get("status", "")),
                   fields.get("verdict", task.get("verdict", "")),
                   report["source"], snapshot))
        write(directory, "history/sessions/%s/%s.md" % (issue, snapshot), header + report["body"])
    for issue, task in tasks.items():
        if not visible(issue):
            continue
        body = task.get("body", "")
        facts = {key: value for key, value in task.items() if key != "body"}
        write(directory, "history/sessions/%s/task.md" % issue,
              "# Session #%s task and result\n\n```json\n%s\n```\n\n%s\n" %
              (issue, json.dumps(facts, ensure_ascii=False, indent=1), body))

    manifest = {"scope": "all permitted history in the prepared checkout" if full else "task-selected inputs",
                "checkout": c.head(), "records": records, "report_snapshots": len(snapshots),
                "paths": {"records": "records/<iteration>.json", "sessions": "sessions/<issue>/"},
                "search": "Search Session titles, roles, outcomes and report text; read selected files. "
                          "No match does not establish that work was never attempted. "
                          "Current report references and pinned evidence are distinguished in ../reports/index.json."}
    write(directory, "history/manifest.json", manifest)
    return {"manifest": ".session/history/manifest.json", "records": records,
            "report_snapshots": len(snapshots), "scope": manifest["scope"]}


def startup_state(state, directory, role, full=True):
    """A compact starting view, with complete evidence retained outside mandatory reads."""
    view = dict(state)
    # The previous outcome is current context; the rest is discoverable history.
    view["iterations"] = state.get("iterations", [])[-1:]
    view["history"] = history(directory, role, full=full)
    return view
