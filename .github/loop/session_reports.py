"""Trusted report snapshots and a separate suggestion lifecycle, owned by the engine."""
import glob
import hashlib
import json
import os
import re

import common as c
import session_inputs

ROOT = ".github/loop/data/session-reports"
STATE = ".github/loop/data/suggestions-state.json"
FINAL = ("done", "partial", "crashed", "rejected")      # a parked (`paused`) session freezes nothing
DECISIONS_MARK = "<!-- suggestion-decisions -->"


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


def validate(text):
    try:
        items = json.loads(text)
    except ValueError:
        return [], ["suggestions.json must be valid JSON"]
    if not isinstance(items, list) or len(items) > 100:
        return [], ["suggestions.json must be an array of at most 100 objects"]
    valid, errors = [], []
    for index, item in enumerate(items):
        if (not isinstance(item, dict) or set(item) != {"kind", "what", "why", "evidence"}
                or any(not isinstance(item.get(k), str) or not item[k].strip() or len(item[k]) > 20000
                       for k in ("kind", "what", "why", "evidence"))
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", item.get("kind", ""))):
            errors.append("suggestions.json item %s requires kind, what, why and evidence strings; kind uses lowercase words and hyphens" % index)
        else:
            valid.append(item)
    return valid, errors


def source_records(n=None):
    pattern = "%s/%s/*.json" % (ROOT, n if n is not None else "[0-9]*")
    out = []
    for path in sorted(glob.glob(pattern)):
        text = c.safe_read(".", path)
        if text:
            out.append((path, json.loads(text)))
    return out


def capture(n, work=None):
    """Freeze a trusted report and validated proposals. Replaying identical output is a no-op."""
    report = report_snapshot(n)
    if not report or c.fields(report["body"]).get("status") not in FINAL:
        return
    items, errors = [], []
    if work:
        text = c.safe_read(work, ".session/suggestions.json")
        if text is not None:
            items, errors = validate(text)
    if errors:
        c.say("Session #%s suggestions refused: %s" % (n, "; ".join(errors)))
    suggestions = []
    for item in items:
        suggestions.append(dict(item, id="suggestion-" + digest(encoded([n, item])),
                                source={k: report[k] for k in ("issue", "source", "snapshot")}))
    record = {"schema_version": 1, "report": report, "suggestions": suggestions, "diagnostics": errors}
    path = "%s/%s/%s.json" % (ROOT, n, digest(encoded(record)))
    if not os.path.exists(path):
        c.commit_main({path: encoded(record)}, "session #%s: freeze report and suggestions" % n)


def decisions():
    text = c.safe_read(".", STATE)
    return json.loads(text) if text else {}


def pending():
    handled, items = decisions(), {}
    for _, record in source_records():
        for item in record["suggestions"]:
            if item["id"] not in handled:
                items.setdefault(item["id"], dict(item, status="pending"))
    return [items[k] for k in sorted(items)]


def references(n):
    records = source_records(n)
    return {"report_snapshots": [path for path, _ in records],
            "suggestion_ids": sorted({s["id"] for _, record in records for s in record["suggestions"]})}


def freeze_decisions(n, work, visible):
    """Freeze completed Policymaker output; application waits for acceptance of its branch."""
    text = c.safe_read(work, ".session/suggestion-decisions.json")
    if text is None:
        return
    try:
        items = json.loads(text)
        if not isinstance(items, list):
            raise ValueError("expected an array")
        allowed = {item["id"] for item in visible}
        for item in items:
            if (not isinstance(item, dict) or set(item) != {"id", "status", "reason"}
                    or item.get("id") not in allowed or item.get("status") not in ("handled", "rejected")
                    or not isinstance(item.get("reason"), str) or not item["reason"].strip()):
                raise ValueError("each decision needs a staged id, handled/rejected status and a reason")
        body = DECISIONS_MARK + "\n" + encoded(items)
        if not any(x["body"] == body and c.trusted(x) for x in c.comments(n)):
            c.post_comment(n, body)
    except (ValueError, TypeError) as error:
        c.say("Policymaker #%s suggestion-decisions.json refused: %s" % (n, error))


def apply_decisions(n):
    """Apply only after the Policymaker branch is accepted; immutable resolutions are replay safe."""
    frozen = [x for x in c.comments(n) if c.trusted(x) and x["body"].startswith(DECISIONS_MARK + "\n")]
    if not frozen:
        return
    items = json.loads(frozen[-1]["body"].split("\n", 1)[1])
    for attempt in range(5):
        baseline = c.file_digest(STATE)
        old = decisions()
        new = dict(old)
        for item in items:
            new.setdefault(item["id"], dict(item, policymaker_issue=n))
        if new == old:
            return
        try:
            c.commit_main({STATE: encoded(new)}, "policymaker #%s: resolve suggestions" % n,
                          expected={STATE: baseline})
            return
        except c.ConcurrentChange:
            if attempt == 4:
                raise
            c.sync_main()


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
    for item in state.get("suggestions", []):
        n = item["source"]["issue"]
        if role and session_inputs.restricted(role) and not session_inputs.visible(n):
            continue
        if n in seen:
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
    # Keep the exact immutable evidence cited by pending proposals beside current reports.
    wanted = {(item["source"]["issue"], item["source"]["snapshot"])
              for item in state.get("suggestions", [])}
    for _, record in source_records():
        report = record["report"]
        if (role and session_inputs.restricted(role) and not session_inputs.visible(report["issue"])):
            continue
        if (report["issue"], report["snapshot"]) in wanted:
            rel = "%s-%s.json" % (report["issue"], report["snapshot"])
            with open(os.path.join(out, rel), "w", encoding="utf-8") as fh:
                fh.write(encoded(report))
            if not any(item["path"] == rel for item in index):
                index.append({"issue": report["issue"], "path": rel, "source": report["source"],
                              "snapshot": report["snapshot"], "frozen": True})
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as fh:
        fh.write(encoded(index))
