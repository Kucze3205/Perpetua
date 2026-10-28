"""Recipient history: the kernel gathers facts, `policy/session-state.py` shapes what the session receives.

Selection (what the role may see) precedes shaping. The views are disposable, backed by existing records.
"""
import glob
import json
import os
import re

import common as c
import session_inputs
import session_reports

STATE_BYTES = 8 * 1024 * 1024       # what the engine accepts from policy/session-state.py
MAX_FILES = 20000
SAFE = re.compile(r"history/[\w.@=+-]+(/[\w.@=+-]+)*")


def write(directory, path, value):
    target = os.path.join(directory, path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1) + "\n")


def facts(directory, role, state, full):
    """The records and report snapshots this role may see, and the stored state it may see."""
    import iteration as it

    restricted = bool(role and session_inputs.restricted(role))
    visibility = {}

    def visible(issue):
        if not restricted:
            return True
        if issue not in visibility:
            visibility[issue] = session_inputs.visible(issue)
        return visibility[issue]

    records, reports = [], []
    for path in it.record_files() if full else sorted(glob.glob(os.path.join(directory, "exports/records/*.json"))):
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        records.append(session_inputs.record(record, can_read=visible) if full and restricted else record)
    if full:
        reports += [record["report"] for _, record in session_reports.source_records() if visible(record["report"]["issue"])]
    for pattern in ("reports/*.json", "exports/reports/*.json"):
        for path in sorted(glob.glob(os.path.join(directory, pattern))):
            with open(path, encoding="utf-8") as fh:
                report = json.load(fh)
            if isinstance(report, dict) and "body" in report and visible(report["issue"]):
                reports.append(report)
    outboxes = session_reports.outboxes(visible)
    return {"role": role, "state": state, "outboxes": outboxes, "full": full, "checkout": c.head(), "records": records, "reports": reports}


def stage(directory, role, state=None, full=True):
    """Write `.session/history/` and, with a `state`, `.session/loop-state.json`, as `policy/session-state.py` shapes them.
    No script: nothing is written. Raises iteration.PolicyScriptError for an answer the engine refuses."""
    import iteration as it

    out = it.run_policy_script("session-state.py", facts(directory, role, state, full), max_bytes=STATE_BYTES)
    if out is None:
        return
    files, loop_state = out.get("files"), out.get("loop_state")
    if (not isinstance(files, dict) or len(files) > MAX_FILES or not (loop_state is None or isinstance(loop_state, dict))
            or any(not isinstance(k, str) or not isinstance(v, str) or not SAFE.fullmatch(k) or ".." in k.split("/")
                   for k, v in files.items())):
        raise it.PolicyScriptError("policy/session-state.py: `files` must map history/ paths to text and `loop_state` be an object or null")
    for path, text in files.items():
        write(directory, path, text)
    if loop_state is not None:
        write(directory, "loop-state.json", loop_state)
