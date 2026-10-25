"""Allowlisted, declarative session inputs fetched by trusted engine code.

Selectors are data. They never become commands, paths, URLs or API endpoints.
"""
import hashlib
import json
import os
import re
import shutil

import common as c
import session_inputs

SOURCES = {"streams", "actions", "records", "reports"}
STREAM = re.compile(r"stream-(\d{4})-(\d+)$")


def validate(requests):
    if not isinstance(requests, list) or len(requests) > 32:
        return ["exports must be a list of at most 32 selectors"]
    errors = []
    for request in requests:
        if not isinstance(request, dict):
            errors.append("an export selector must be an object")
            continue
        if set(request) - {"source", "iterations", "sessions"}:
            errors.append("exports support only source, iterations and sessions")
        # Unknown sources are retained so staging can give them an explicit result.
        if not isinstance(request.get("source"), str):
            errors.append("export source must be a string")
        if not any(request.get(k) for k in ("iterations", "sessions")):
            errors.append("each export requires explicit iterations or sessions")
        for key in ("iterations", "sessions"):
            if key in request:
                values = request[key]
                if (not isinstance(values, list) or not values or len(values) > 100
                        or any(type(x) is not int or x < (1 if key == "sessions" else 0) for x in values)):
                    errors.append("export %s must contain 1 to 100 nonnegative integer identifiers" % key)
        if request.get("source") == "records" and "sessions" in request:
            errors.append("records supports iteration scope only")
    return errors


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def _issues(request):
    if request.get("sessions"):
        issues = [c.issue(n) for n in sorted(set(request["sessions"]))]
    else:
        issues = []
        for n in sorted(set(request["iterations"])):
            issues.extend(c.list_issues(state="all", labels=(c.ITER + str(n),)))
    return [i for i in issues if c.in_loop(c.label_names(i)) and c.roles_of(c.label_names(i))
            and (not request.get("iterations") or c.iteration_of(c.label_names(i)) in request["iterations"])]


def stage(body, role, iteration, sess):
    """Write exports/manifest.json even for unavailable, invalid or unknown inputs.

    Omitted selectors preserve the existing audit stream window; explicit [] opts out.
    """
    # A retried/re-staged session receives this declaration, never an older scope.
    root = os.path.realpath(sess)
    for name in ("exports", "streams"):
        path = os.path.join(root, name)
        if os.path.islink(path):
            os.unlink(path)
        elif os.path.isdir(path):
            if os.path.commonpath([root, os.path.realpath(path)]) != root:
                raise ValueError("export destination resolves outside the session")
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.unlink(path)
    raw = c.marker(body, "exports")
    manifest = {"schema_version": 1, "results": []}
    results = manifest["results"]
    try:
        requests = json.loads(raw) if raw is not None else ([{
            "source": "streams", "iterations": list(range(max(0, iteration - 3), iteration + 1))
        }] if role == "audit" else [])
        errors = validate(requests)
    except (ValueError, TypeError):
        requests, errors = [], ["exports marker is not valid JSON"]
    if errors:
        results.append({"status": "error", "detail": "; ".join(errors)})
        requests = []

    for index, request in enumerate(requests):
        source = request["source"]
        base = {"selector": index, "source": source, "scope": {k: request[k] for k in ("iterations", "sessions") if k in request}}
        def result(status, **extra):
            results.append(dict(base, status=status, **extra))
        if source not in SOURCES:
            result("unknown", detail="unsupported export source")
            continue
        before = len(results)
        restricted = session_inputs.restricted(role)
        if restricted and request.get("sessions"):
            denied = [n for n in request["sessions"] if not session_inputs.visible(n)]
            for n in sorted(set(denied)):
                result("denied", issue=n, detail="session data is not available to this recipient")
            request = dict(request, sessions=[n for n in request["sessions"] if n not in denied])
            if not request["sessions"]:
                continue
        try:
            if source == "streams":
                covered_iterations, covered_sessions = set(), set()
                for artifact in c.list_artifacts():
                    match = STREAM.fullmatch(artifact.get("name", ""))
                    if not match:
                        continue
                    k, issue = map(int, match.groups())
                    if request.get("iterations") and k not in request["iterations"]:
                        continue
                    if request.get("sessions") and issue not in request["sessions"]:
                        continue
                    if restricted and not session_inputs.visible(issue):
                        continue
                    covered_iterations.add(k)
                    covered_sessions.add(issue)
                    identity = {"issue": issue, "iteration": k, "artifact": artifact["id"]}
                    if artifact.get("expired"):
                        result("expired", **identity)
                        continue
                    run = (artifact.get("workflow_run") or {}).get("id")
                    if type(run) is not int or type(artifact.get("id")) is not int:
                        result("unavailable", detail="artifact has no run identifier", **identity)
                        continue
                    rel = "streams/%s-%s" % (artifact["name"], artifact["id"])
                    dest = os.path.join(sess, *rel.split("/"))
                    os.makedirs(dest, exist_ok=True)
                    try:
                        c.gh("run", "download", str(run), "--name", artifact["name"], "--dir", dest)
                        result("downloaded", path=rel, run=run, **identity)
                    except (SystemExit, OSError):
                        result("error", detail="stream download failed", **identity)
                # The catalogue cannot distinguish deleted artifacts from ones never uploaded.
                missing = False
                for key, covered in (("iterations", covered_iterations), ("sessions", covered_sessions)):
                    for identifier in sorted(set(request.get(key, [])) - covered):
                        missing = True
                        result("unavailable", **{"iteration" if key == "iterations" else "issue": identifier}, detail="no matching artifact in the current Actions catalogue; it may have been deleted")
                if len(results) == before and not missing:
                    result("unavailable", detail="no matching artifact in the current Actions catalogue; it may have been deleted")
            elif source == "records":
                for k in sorted(set(request["iterations"])):
                    text = c.safe_read(".", "iterations/%04d.json" % k)
                    if text is None:
                        result("unavailable", iteration=k, detail="iteration record absent")
                        continue
                    rel = "exports/records/%04d.json" % k
                    value = json.loads(text)
                    if restricted:
                        value = session_inputs.record(value)
                    _write(os.path.join(sess, *rel.split("/")), value)
                    result("downloaded", iteration=k, path=rel, snapshot=hashlib.sha256(text.encode()).hexdigest())
            else:
                issues = _issues(request)
                if restricted:
                    issues = [i for i in issues if session_inputs.visible(i["number"])]
                if source == "reports":
                    for issue in issues:
                        n = issue["number"]
                        report = c.find_report(n)  # Marker AND trusted author, not a marker alone.
                        if not report:
                            result("unavailable", issue=n, detail="no trusted marked report")
                            continue
                        rel = "exports/reports/%s.json" % n
                        snapshot = hashlib.sha256(report["body"].encode()).hexdigest()
                        _write(os.path.join(sess, *rel.split("/")), {
                            "issue": n, "source": report.get("html_url") or "https://github.com/%s/issues/%s#issuecomment-%s" % (c.REPO, n, report["id"]),
                            "snapshot": snapshot, "body": report["body"], "comment": report["id"],
                        })
                        result("downloaded", issue=n, path=rel, snapshot=snapshot)
                else:
                    selected = {i["number"] for i in issues}
                    covered = set()
                    for run in c.session_runs():
                        title = c.run_issue(run["displayTitle"])
                        issue = next((n for n in selected if title == "session #%s" % n), None)
                        if issue is None:
                            continue
                        covered.add(issue)
                        run_id = int(run["databaseId"])
                        data = c.api("repos/%s/actions/runs/%s" % (c.REPO, run_id))
                        jobs = c.api("repos/%s/actions/runs/%s/jobs?per_page=100" % (c.REPO, run_id))
                        rel = "exports/actions/%s.json" % run_id
                        # Fixed metadata fields: job/step timestamps, statuses and conclusions, no logs or env.
                        keys = ("id", "name", "status", "conclusion", "created_at", "started_at", "completed_at", "queued_at", "run_started_at", "updated_at", "html_url", "run_attempt")
                        clean = {k: data[k] for k in keys if k in data}
                        clean["jobs"] = [{**{k: j[k] for k in keys if k in j}, "steps": [
                            {k: s[k] for k in keys if k in s} for s in j.get("steps", [])]} for j in (jobs or {}).get("jobs", [])]
                        _write(os.path.join(sess, *rel.split("/")), clean)
                        result("downloaded", issue=issue, run=run_id, path=rel)
                    for issue in sorted(selected - covered):
                        result("unavailable", issue=issue, detail="session run absent from the current Actions listing")
                if len(results) == before:
                    result("unavailable", detail="no matching supported session data")
        except (SystemExit, OSError, ValueError, KeyError, TypeError):
            result("error", detail="engine could not fetch the selected export")
    _write(os.path.join(sess, "exports", "manifest.json"), manifest)
    return manifest
