#!/usr/bin/env python3
"""What a session receives as its state and its searchable history. The engine runs this in the tokenless job
(`python3 session-state.py facts.json`) when it stages a session, and prints nothing else: one JSON object
{"loop_state": <object or null>, "files": {"history/<path>": "<text>"}}. The engine writes `loop_state` to
`.session/loop-state.json` and the files under `.session/`, after checking paths and sizes. An invalid answer fails the
iteration and rolls `policy/` back.

facts.json: role, state (the Policymaker/shaper snapshot the engine stored, already limited to what the role may see; null
for other roles), full (the whole history, or only what the task selected), checkout, records (iteration records), reports
(report snapshots: {issue, source, snapshot, body}).
"""
import json
import sys

facts = json.load(open(sys.argv[1], encoding="utf-8"))
full = bool(facts.get("full"))
files, tasks, snapshots = {}, {}, {}


def field(body, key):
    for line in body.splitlines():
        if line.startswith(key + ":"):
            return line.split(":", 1)[1].strip()
    return ""


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=1)


for record in facts.get("records", []):
    n = record["iteration"]
    files["history/records/%04d.json" % n] = dump(record) + "\n"
    for task in record.get("tasks", []):
        if type(task.get("issue")) is int:
            tasks[task["issue"]] = dict(task, iteration=n)
    for session in record.get("outcome", {}).get("sessions", []):
        tasks[session["issue"]] = dict(tasks.get(session["issue"], {}), **session, iteration=n)

# Identity, not a mutable "latest" alias, names a report body; the last copy of an identity wins.
for report in facts.get("reports", []):
    snapshots[(report["issue"], report["snapshot"])] = report

for (issue, snapshot), report in snapshots.items():
    task = tasks.setdefault(issue, {"issue": issue, "title": "Session #%s" % issue})
    body = report["body"]
    header = ("# Session #%s\nTask: %s\nRole: %s\nIteration: %s\nStatus: %s\nVerdict: %s\nSource: %s\nSnapshot: %s\n\n" %
              (issue, str(task.get("title", "")).replace("\n", " "), task.get("role", ""), task.get("iteration"),
               field(body, "status") or task.get("status", ""), field(body, "verdict") or task.get("verdict", ""),
               report["source"], snapshot))
    files["history/sessions/%s/%s.md" % (issue, snapshot)] = header + body
for issue, task in tasks.items():
    detail = {key: value for key, value in task.items() if key != "body"}
    files["history/sessions/%s/task.md" % issue] = ("# Session #%s task and result\n\n```json\n%s\n```\n\n%s\n" %
                                                    (issue, dump(detail), task.get("body", "")))

scope = "all permitted history in the prepared checkout" if full else "task-selected inputs"
manifest = {"scope": scope, "checkout": facts.get("checkout"), "records": len(facts.get("records", [])),
            "report_snapshots": len(snapshots),
            "paths": {"records": "records/<iteration>.json", "sessions": "sessions/<issue>/"},
            "search": "Search Session titles, roles, outcomes and report text; read selected files. "
                      "No match does not establish that work was never attempted. "
                      "Current report references and pinned evidence are distinguished in ../reports/index.json."}
files["history/manifest.json"] = dump(manifest) + "\n"

loop_state = None
if isinstance(facts.get("state"), dict):
    state = facts["state"]
    # The previous outcome is current context; the rest is discoverable history.
    loop_state = dict(state, iterations=state.get("iterations", [])[-1:],
                      history={"manifest": ".session/history/manifest.json", "records": manifest["records"],
                               "report_snapshots": len(snapshots), "scope": scope})
print(json.dumps({"loop_state": loop_state, "files": files}))
