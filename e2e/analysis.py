"""Analyze a saved E2E collection. No GitHub access or experiment state is used."""
import hashlib
import json
import math
import re
import shutil
import subprocess
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from scenario import TAG, HAIKU, REPORT_WINDOW, SCALE, CONTEXT_TOKENS, MAX_TURNS, MINUTES, PLANT, OUTPUTS


def saved_context(out):
    path = out / "collection.json"
    if path.is_file():
        return json.loads(read(path))
    # Older collections kept the start time in report.json and the selected
    # iteration only in its record (start + SHIFT can differ from start).
    previous = json.loads(read(out / "report.json"))
    record = previous.get("iteration_record") or {}
    if "iteration" not in record:
        raise ValueError("legacy report has no selected iteration; collection.json is required")
    return {"iteration": record["iteration"], "run": previous["run"],
            "collect_errors": [e for e in previous.get("collect_errors", []) if e["step"] != "measure"]}


def saved_issues(out, numbers=None):
    paths = sorted((out / "github" / "issues").glob("*.json")) if numbers is None else [
        out / "github" / "issues" / ("%d.json" % n) for n in numbers]
    return [json.loads(read(p)) for p in paths]


def saved_runs(out, numbers=None):
    paths = sorted((out / "github" / "runs").glob("*/run.json"),
                   key=lambda p: int(p.parent.name), reverse=True) if numbers is None else [
        out / "github" / "runs" / str(n) / "run.json" for n in numbers]
    runs = []
    for path in paths:
        r = json.loads(read(path))
        r["jobs_list"] = json.loads(read(path.parent / "jobs.json"))
        if "issue" not in r:  # Legacy run.json predates the saved log association.
            found = re.search(r"ISSUE: (\d+)", read(path.parent / "log.txt"))
            r["issue"] = int(found.group(1)) if found and r["name"] == "session" else None
        runs.append(r)
    return runs


def analyze(out):
    """Rebuild derived files using only evidence and metadata saved in OUT."""
    out = Path(out).resolve()
    context = saved_context(out)
    errors = list(context.get("collect_errors", []))
    try:
        issues = saved_issues(out, context.get("issues"))
        runs = saved_runs(out, context.get("runs"))
        report = measure(out, issues, runs, context["iteration"],
                         (context["run"].get("started") or {}).get("at", ""))
    except Exception as error:
        errors.append({"step": "measure", "error": repr(error)[:500], "trace": traceback.format_exc()[-1500:]})
        report = {}
    report["collect_errors"] = errors
    report["run"] = context["run"]
    write_json(out / "report.json", report)
    write_readme(out, report)
    return report


def passed(report):
    return bool(report.get("contract")) and all(v["ok"] for v in report["contract"].values())


def analyze_command(args):
    report = analyze(args.out)
    ok = passed(report)
    print("analyzed %s; contract: %s" % (args.out, "OK" if ok else "FAILED"))
    if not ok:
        raise SystemExit(1)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def tokens(text):
    return math.ceil(len(text) / 4)


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def seconds(a, b):
    a, b = parse_time(a), parse_time(b)
    return round((b - a).total_seconds(), 1) if a and b else None


def git(repo_dir, *args):
    """Read only the saved clone; no remotes, credentials or network."""
    return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def snapshot(directory):
    """relative path -> (bytes, sha256, estimated tokens) for every regular file; links are not followed."""
    found = {}
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            found[path.relative_to(directory).as_posix()] = (
                len(raw), hashlib.sha256(raw).hexdigest(), tokens(raw.decode("utf-8", "replace")))
    return found


def analyze_stream(path):
    """What the CLI actually did, from its raw stream-json: model call, tools offered and used, errors, repeats, cost."""
    events = []
    for line in (read(path).splitlines() if Path(path).is_file() else []):
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    calls, answers, fills, sandbox = [], {}, [], []
    for event in events:
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        blocks = message.get("content")
        for block in blocks if isinstance(blocks, list) else []:
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                calls.append({"id": block.get("id"), "tool": block.get("name"), "input": block.get("input")})
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                answers[block.get("tool_use_id")] = block
                text = json.dumps(block.get("content"), ensure_ascii=False)
                if re.search(r"bwrap|sandbox|Permission denied|not permitted|denied", text, re.I):
                    sandbox.append(text[:400])
        if event.get("type") == "assistant":
            usage = message.get("usage") or {}
            fills.append(sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
    errors = [{"tool": x["tool"], "input": x["input"], "error": json.dumps(answers[x["id"]].get("content"), ensure_ascii=False)[:400]}
              for x in calls if answers.get(x["id"], {}).get("is_error")]
    repeats = Counter(json.dumps([x["tool"], x["input"]], sort_keys=True) for x in calls)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    return {"events": len(events), "model": init.get("model"), "tools_offered": init.get("tools"), "cli_version": init.get("claude_code_version"),
            "permission_mode": init.get("permissionMode"), "skills_seen": init.get("skills"), "mcp_servers": init.get("mcp_servers"),
            "tool_calls": dict(Counter(x["tool"] for x in calls)), "calls": [[x["tool"], x["input"]] for x in calls],
            "tool_errors": errors, "sandbox_or_denial_mentions": sandbox[:20], "unanswered_calls": sum(1 for x in calls if x["id"] not in answers),
            "repeated_calls": {k: v for k, v in repeats.items() if v > 1}, "context_fill_by_model_call": fills,
            "first_call_context_tokens": fills[0] if fills else None, "peak_context_tokens": max(fills, default=None),
            "model_calls": len(fills), "result": result}


def report_fields(comments):
    """Fields of the engine's marked report (the last comment that has the marker and a yaml block)."""
    found = None
    for comment in comments:
        if comment["body"].startswith("<!-- session-report -->"):
            found = comment
    if not found:
        return {}, None
    block = re.search(r"```yaml\n(.*?)```", found["body"], re.S)
    try:
        return (yaml.safe_load(block.group(1)) if block else {}) or {}, found
    except yaml.YAMLError:
        return {}, found


def category(rel):
    """What kind of staged input a `.session/` file is, for the growth analysis."""
    head = rel.split("/")[0]
    return {"reports": "history", "exports": "history", "streams": "history", "inputs": "history",
            "loop-state.json": "state", "role": "role skill", "protocol.md": "protocol"}.get(head, "other")


def find_dir(base, name):
    return next((p for p in Path(base).rglob(name) if p.is_dir()), None)


def measure(out, issues, runs, iteration, begun=""):
    repo_dir = out / "repository"
    sessions = []
    for i in issues:
        names = {l["name"] for l in i["labels"]}
        roles = [n[5:] for n in names if n.startswith("role:")]
        issue_iteration = next((int(n.split()[-1]) for n in names if n.startswith("loop:iteration ")), None)
        if not roles or issue_iteration != iteration or i["title"].startswith("e2e probe") or i["created_at"] < begun:
            continue
        n = i["number"]
        fields, comment = report_fields(i["comments_list"])
        title_runs = [r for r in runs if r.get("issue") == n]
        sessions.append({"issue": n, "role": roles[0], "iteration": iteration, "title": i["title"], "state": i["state"],
                         "labels": sorted(names), "created_at": i["created_at"], "closed_at": i["closed_at"],
                         "report": fields, "report_bytes": len((comment or {}).get("body", "")),
                         "runs": [r["id"] for r in title_runs], "run_conclusions": [r["conclusion"] for r in title_runs],
                         "comments": len(i["comments_list"]), "comment_bytes": sum(len(c["body"]) for c in i["comments_list"])})
    sessions.sort(key=lambda s: s["issue"])
    for s in sessions:
        d = out / "sessions" / ("%03d-%s" % (s["issue"], s["role"]))
        s["dir"] = d.name
        s["analysis"] = analyze_session(out, d, s, runs)
    record_path = repo_dir / "iterations" / ("%04d.json" % iteration)
    record = json.loads(read(record_path)) if record_path.is_file() else None
    timing = actions_timing(runs)
    probe_data, probe_texts = probe_view(out)
    report = {"sessions": [{k: v for k, v in s.items() if k != "analysis"} | {"analysis_file": "sessions/%s/analysis.json" % s["dir"]} for s in sessions],
              "iteration_record": record, "actions": timing, "actions_overhead": actions_overhead(sessions, runs), "growth": growth(repo_dir, issues, sessions, record, probe_data, iteration),
              "session_inputs": session_inputs(sessions, probe_data), "visibility": visibility(out, sessions, probe_texts),
              "mechanics": policymaker_mechanics(repo_dir, sessions, record),
              "environment_facts": environment_facts(sessions, probe_data),
              "next_iteration_probe": probe_data}
    report["contract"] = contract(sessions, record, issues, iteration)
    return report


def analyze_session(out, d, s, runs):
    """Everything about one session: its stream, what was staged and what it left (from the capture artifact)."""
    n = s["issue"]
    info = {}
    arts = out / "artifacts"
    stream_dir = next(iter(sorted(arts.glob("stream-*-%d" % n))), None)
    capture_dir = next(iter(sorted(arts.glob("capture-*-%d" % n))), None)
    d.mkdir(parents=True, exist_ok=True)
    if stream_dir:
        stream = next(stream_dir.rglob("stream.ndjson"), None)
        if stream:
            shutil.copyfile(stream, d / "stream.ndjson")
            info["stream"] = analyze_stream(stream)
            info["stream_bytes"] = stream.stat().st_size
    if "stream" not in info and capture_dir:
        stream = next(capture_dir.rglob("stream.ndjson"), None)
        if stream:
            shutil.copyfile(stream, d / "stream.ndjson")
            info["stream"] = analyze_stream(stream)
            info["stream_bytes"] = stream.stat().st_size
            info["stream_source"] = "capture"
    info["has_stream"] = "stream" in info
    if capture_dir:
        session = find_dir(capture_dir, ".session")
        trusted = find_dir(capture_dir, "trusted")
        extra = find_dir(capture_dir, "capture")
        if session:
            staged = {k: v for k, v in snapshot(session).items()}
            inputs = {k: v for k, v in staged.items() if k not in OUTPUTS and not k.startswith("artifacts/")}
            info["staged"] = {"files": {k: v[0] for k, v in sorted(staged.items())}, "inputs": {k: v[0] for k, v in sorted(inputs.items())},
                              "outputs": sorted(k for k in staged if k in OUTPUTS or k.startswith("artifacts/")),
                              "tokens_estimated": {"inputs": sum(v[2] for v in inputs.values()),
                                                   "by_category": dict(Counter({c: sum(v[2] for k, v in inputs.items() if category(k) == c) for c in {category(k) for k in inputs}}))}}
            shutil.copytree(session, d / "session", dirs_exist_ok=True)
        if trusted:
            fixed = {name: tokens(read(trusted / name)) for name in ("prompt.md", "contract.md") if (trusted / name).is_file()}
            info["trusted"] = {"files": sorted(p.relative_to(trusted).as_posix() for p in trusted.rglob("*") if p.is_file()), "tokens_estimated": fixed,
                               "unapproved_file": (trusted / "unapproved").is_file(), "secrets_file": (trusted / "secrets").is_file()}
            shutil.copytree(trusted, d / "trusted", dirs_exist_ok=True)
        if extra:
            shutil.copytree(extra, d / "capture", dirs_exist_ok=True)
            info["files_created_by_session_outside_session_dir"] = (read(d / "capture" / "work-new-files.txt").splitlines()
                                                                    if (d / "capture" / "work-new-files.txt").is_file() else None)
        for name in ("agent-exit", "agent-start", "agent-end", "claude-stderr.txt", "script-output.txt", "secret-missing", "claude-execution-output.json"):
            found = next(capture_dir.rglob(name), None)
            if found:
                shutil.copyfile(found, d / name)
        info["agent_exit"] = read(d / "agent-exit").strip() if (d / "agent-exit").is_file() else None
        info["claude_stderr"] = read(d / "claude-stderr.txt")[:3000] if (d / "claude-stderr.txt").is_file() else None
    info["has_capture"] = capture_dir is not None
    write_json(d / "analysis.json", info)
    return info


def actions_timing(runs):
    """Times from Actions: queue, start, job and step durations, cron delay, concurrency, billable estimate."""
    rows, intervals, minutes = [], [], 0
    for r in sorted(runs, key=lambda r: r["id"]):
        jobs = []
        for j in r.get("jobs_list", []):
            jobs.append({"name": j["name"], "queue_s": seconds(j.get("created_at"), j.get("started_at")),
                         "duration_s": seconds(j.get("started_at"), j.get("completed_at")), "conclusion": j.get("conclusion"),
                         "runner": j.get("runner_name"), "labels": j.get("labels"),
                         "steps": [{"name": s["name"], "duration_s": seconds(s.get("started_at"), s.get("completed_at")), "conclusion": s.get("conclusion")}
                                   for s in j.get("steps", [])]})
            if j.get("started_at") and j.get("completed_at"):
                minutes += math.ceil(seconds(j["started_at"], j["completed_at"]) / 60)
                if r["name"] == "session":
                    intervals.append((parse_time(j["started_at"]), parse_time(j["completed_at"]), r["display_title"]))
        row = {"run": r["id"], "workflow": r["name"], "title": r.get("display_title"), "event": r["event"], "status": r["status"],
               "conclusion": r["conclusion"], "created_at": r["created_at"], "run_started_at": r.get("run_started_at"),
               "updated_at": r["updated_at"], "attempt": r.get("run_attempt"), "jobs": jobs}
        if r["event"] == "schedule":
            created = parse_time(r["created_at"])
            slot = created.replace(minute=0 if created.minute < 30 else 30, second=0, microsecond=0)
            row["cron_delay_s"] = round((created - slot).total_seconds())
        rows.append(row)
    peak = max((sum(1 for a, b, _ in intervals if a <= start < b) for start, _, _ in intervals), default=0)
    return {"runs": rows, "billable_minutes_estimate_rounded_up_per_job": minutes, "max_concurrent_session_jobs": peak,
            "note": "queue_s = job created -> started; cron_delay_s only for scheduled runs (distance to the :00/:30 slot)"}


def probe_view(out):
    """What the e2e-probe run staged for the next iteration's sessions: (summary for the report, texts per view)."""
    base = out / "artifacts" / "probe"
    if not base.is_dir():
        return None, {}
    summary = next(base.rglob("probe.json"), None)
    data = json.loads(read(summary)) if summary else {}
    views, texts = {}, {}
    for name in ("policymaker", "audit", "worker"):
        session = find_dir(base / name, ".session") if (base / name).is_dir() else None
        if not session:
            continue
        snap = snapshot(session)
        trusted = next((p for p in (base / name).rglob("trusted") if p.is_dir()), None)
        fixed = {n: tokens(read(trusted / n)) for n in ("prompt.md", "contract.md") if trusted and (trusted / n).is_file()}
        views[name] = {"files": {k: v[0] for k, v in snap.items()}, "estimated_tokens": sum(v[2] for v in snap.values()) + sum(fixed.values()),
                       "tokens_by_category": dict(Counter({c: sum(v[2] for k, v in snap.items() if category(k) == c) for c in {category(k) for k in snap}})),
                       "fixed_tokens": fixed}
        texts[name] = {k: read(session / k) for k in snap if (session / k).stat().st_size < 3_000_000}
    data["views"] = views
    return data, texts


def growth(repo_dir, issues, sessions, record, probe_data, iteration):
    """What accumulates with iterations (this iteration's value and x SCALE: the repository only grows) against what one
    iteration costs (reported as is: a time or token ceiling is per iteration, multiplying it compares nothing), and how
    the sessions' inputs grow (see `input_projection`)."""
    def tree(prefix):
        base = repo_dir / prefix
        return sum(p.stat().st_size for p in base.rglob("*") if p.is_file()) if base.exists() else 0
    state_sizes = [s["analysis"]["staged"]["files"].get("loop-state.json") for s in sessions if s["analysis"].get("staged")]
    record_file = repo_dir / "iterations" / ("%04d.json" % iteration)
    accumulating = {
        "iterations/ directory, bytes": tree("iterations"), "journal/, bytes": tree("journal"), "audit/, bytes": tree("audit"),
        "policy/ + execution/, bytes": tree("policy") + tree("execution"),
        # from the analysed streams, not from artifacts: artifacts may be absent (quota), the capture release still has them
        "raw session streams, bytes": sum(s["analysis"].get("stream_bytes") or 0 for s in sessions),
        "issue comments of the loop (reports, state, orders), bytes": sum(len(c["body"]) for i in issues if any(l["name"].startswith("loop:iteration ") for l in i["labels"]) for c in i["comments_list"]),
        "commits on the branch since the baseline, count": len(git(repo_dir, "rev-list", "%s..HEAD" % TAG).split()),
    }
    per_iteration = {
        "iteration record, bytes": record_file.stat().st_size if record_file.is_file() else None,
        "loop-state.json as staged, bytes (largest)": max([x for x in state_sizes if x], default=None),
        "issues of the iteration, count": sum(1 for i in issues if any(l["name"].startswith("loop:iteration ") for l in i["labels"])),
        "tokens in + out of all sessions": sum(int(s["report"].get("tokens_in") or 0) + int(s["report"].get("tokens_out") or 0) for s in sessions),
        "cache read + write tokens of all sessions": sum(int(s["report"].get("cache_read") or 0) + int(s["report"].get("cache_write") or 0) for s in sessions),
        "turns of all sessions": sum(int(s["report"].get("turns") or 0) for s in sessions),
        "agent seconds of all sessions": sum(int(s["report"].get("agent_s") or 0) for s in sessions),
        "wall seconds of the iteration": ((record or {}).get("outcome") or {}).get("wall_s"),
    }
    return {"accumulating": {k: {"this_iteration": v, "x%d" % SCALE: v * SCALE} for k, v in accumulating.items()},
            "per_iteration_not_scaled": per_iteration, "session_input_growth_per_iteration": input_projection(sessions, probe_data),
            "note": "accumulating: sizes the repository keeps; per_iteration_not_scaled: ceilings (iteration_minutes, tokens) apply per iteration"}


def input_projection(sessions, probe_data):
    """Role -> input of its session at SCALE iterations. The probe's session is what the next iteration is given; its excess
    over the role's first session is what one iteration adds, by kind: `history` (reports, exports: the engine keeps the last
    REPORT_WINDOW iterations, so it stops growing there), `state` (the `iterations` index in loop-state.json: linear), the
    rest (skills, protocol) does not grow with iterations. The width of an iteration (sessions, report sizes) is measured in
    `iteration_width`, not assumed."""
    first = {}
    for s in sessions:
        if s["analysis"].get("staged"):
            first.setdefault(s["role"], s)
    result = {}
    for role, now in ((probe_data or {}).get("views") or {}).items():
        before = first.get(role)
        if not before:
            continue
        a = before["analysis"]
        start_by = a["staged"]["tokens_estimated"]["by_category"]
        fixed = sum((a.get("trusted") or {}).get("tokens_estimated", {}).values())
        start = a["staged"]["tokens_estimated"]["inputs"] + fixed
        step = {c: now["tokens_by_category"].get(c, 0) - start_by.get(c, 0) for c in ("history", "state")}
        extra = step["history"] * min(SCALE, REPORT_WINDOW) + step["state"] * SCALE
        result[role] = {"first_session_estimated_tokens": start, "next_iteration_session_estimated_tokens": now["estimated_tokens"],
                        "added_by_one_iteration": now["estimated_tokens"] - start, "added_history_tokens": step["history"],
                        "added_state_tokens": step["state"], "projection_x%d_extra_tokens" % SCALE: extra,
                        "projection_x%d_tokens" % SCALE: start + extra}
    return result


def session_inputs(sessions, probe_data):
    """Estimated tokens of everything staged for each session against the context the CLI really used. The CLI's own
    overhead is taken from that session itself, so it is per role (14.0k with Bash, 9.4k without in run 2), not shared."""
    projection = input_projection(sessions, probe_data)
    rows = []
    for s in sessions:
        a = s["analysis"]
        if not a.get("staged"):
            continue
        fixed = sum((a.get("trusted") or {}).get("tokens_estimated", {}).values())
        estimate = a["staged"]["tokens_estimated"]["inputs"] + fixed
        history = a["staged"]["tokens_estimated"]["by_category"].get("history", 0)
        stream = a.get("stream") or {}
        measured = stream.get("first_call_context_tokens")
        row = {"issue": s["issue"], "role": s["role"], "has_bash_tool": any(str(t).startswith("Bash") for t in stream.get("tools_offered") or []),
               "estimated_input_tokens": estimate, "estimated_history_tokens": history,
               "fixed_tokens": fixed, "measured_first_call_context_tokens": measured,
               "measured_peak_context_tokens": stream.get("peak_context_tokens"),
               "cli_overhead_tokens (measured first call - estimate)": measured - estimate if measured is not None else None,
               "ratio measured / estimate": round(measured / estimate, 2) if measured and estimate else None}
        extra = (projection.get(s["role"]) or {}).get("projection_x%d_extra_tokens" % SCALE)
        if measured and extra is not None:
            constant = measured + extra
            proportional = round(measured / estimate * (estimate + extra))
            row.update({"projection_x%d_constant_overhead_model" % SCALE: constant, "projection_x%d_proportional_model" % SCALE: proportional,
                        "over_%d_tokens" % CONTEXT_TOKENS: max(constant, proportional) > CONTEXT_TOKENS})
        rows.append(row)
    return rows


def actions_overhead(sessions, runs):
    """What Actions spends around the agent, per session run, and how wide the iteration is: these, not the agent, decide
    how an iteration scales. Steps: prep job; in the session job `Tools`, `Prepare the machine` + `Stage the session`,
    `Session` (the agent), `Epilogue`."""
    def step(job, *names):
        return round(sum(seconds(x.get("started_at"), x.get("completed_at")) or 0 for x in job.get("steps", []) if x["name"] in names), 1)
    rows = []
    by_id = {r["id"]: r for r in runs}
    for s in sessions:
        for run_id in s["runs"]:
            jobs = {j["name"]: j for j in by_id[run_id].get("jobs_list", [])}
            prep, job = jobs.get("prep"), jobs.get("session")
            if not job:
                continue
            row = {"issue": s["issue"], "role": s["role"], "run": run_id, "prep_s": seconds(prep.get("started_at"), prep.get("completed_at")) if prep else None,
                   "tools_s": step(job, "Tools"), "stage_s": step(job, "Prepare the machine", "Stage the session"),
                   "agent_step_s": step(job, "Session"), "epilogue_s": step(job, "Epilogue"),
                   "session_job_s": seconds(job.get("started_at"), job.get("completed_at")), "agent_s": s["report"].get("agent_s")}
            row["around_agent_s"] = round((row["prep_s"] or 0) + (row["session_job_s"] or 0) - (row["agent_step_s"] or 0), 1)
            rows.append(row)
    width = {"sessions": len(sessions), "by_role": dict(Counter(s["role"] for s in sessions)),
             "report_bytes_total": sum(s["report_bytes"] for s in sessions), "report_bytes_max": max((s["report_bytes"] for s in sessions), default=0),
             "comment_bytes_total": sum(s["comment_bytes"] for s in sessions)}
    around, agent = sum(r["around_agent_s"] for r in rows), sum(r["agent_step_s"] for r in rows)
    return {"per_session_run": rows, "iteration_width": width,
            "totals": {"around_agent_s": round(around, 1), "agent_step_s": round(agent, 1),
                       "around_agent_share": round(around / (around + agent), 2) if around + agent else None}}


def visibility(out, sessions, probe_texts):
    """Did the planted problem, the iteration's results and the exports reach the sessions of the next iteration, and which
    sessions of this iteration had the planted step in their inputs."""
    worker = next((s for s in sessions if s["role"] == "worker"), None)
    needles = {"the planted step (the worker's skill)": (PLANT, None),
               "the worker session, by issue number": (("#%d" % worker["issue"]) if worker else None, None),
               "the worker's repeated Read of its issue, in a stream": ("issue.md", ("streams/", "exports/"))}
    seen = {}
    for name, (needle, only) in needles.items():
        seen[name] = {view: sorted(k for k, text in files.items() if needle and needle in text and (not only or k.startswith(only)))
                      for view, files in probe_texts.items()}
    planted_in = {}
    for s in sessions:
        base = out / "sessions" / s["dir"] / "session"
        if base.is_dir():
            planted_in["%s#%d" % (s["role"], s["issue"])] = sorted(
                p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file() and PLANT in p.read_text(encoding="utf-8", errors="replace"))
    return {"needles_in_next_iteration_sessions": seen, "planted_step_in_inputs_of_this_iteration": planted_in,
            "exports_manifest_of_next_sessions": {view: json.loads(files["exports/manifest.json"]) for view, files in probe_texts.items() if "exports/manifest.json" in files}}


def policymaker_mechanics(repo_dir, sessions, record):
    pm = next((s for s in sessions if s["role"] == "policymaker"), None)
    if not pm:
        return {"policymaker": None}
    stream = pm["analysis"].get("stream") or {}
    managed = [x[1].get("file_path") for x in stream.get("calls", []) if x[0] in ("Write", "Edit") and re.search(r"(^|/)(execution|policy)/", str((x[1] or {}).get("file_path", "")))]
    diff = git(repo_dir, "diff", "--stat", TAG, "HEAD", "--", "execution", "policy")
    return {"policymaker_issue": pm["issue"], "report": pm["report"], "tools_offered": stream.get("tools_offered"), "tool_errors": stream.get("tool_errors"),
            "tried_to_write_execution_or_policy": managed, "diff_of_execution_and_policy_since_baseline": diff, "change_receipts": ((record or {}).get("policy") or {}).get("changes"),
            "orders_in_record": [t.get("id") for t in (record or {}).get("tasks", []) if str(t.get("id", "")).startswith("order-")],
            "orders_refused": ((record or {}).get("policy") or {}).get("orders_refused")}


def environment_facts(sessions, probe_data):
    """Facts for the open questions about the runner: sandbox, stray files, files `agent.sh` reads and nothing writes."""
    facts = {"sessions": {}}
    for s in sessions:
        a = s["analysis"]
        facts["sessions"]["%s#%d" % (s["role"], s["issue"])] = {
            "trusted_files": (a.get("trusted") or {}).get("files"), "unapproved_file_present": (a.get("trusted") or {}).get("unapproved_file"),
            "secrets_file_present": (a.get("trusted") or {}).get("secrets_file"), "agent_exit": a.get("agent_exit"),
            "files_created_outside_session_dir": a.get("files_created_by_session_outside_session_dir"),
            "sandbox_or_denial_mentions": (a.get("stream") or {}).get("sandbox_or_denial_mentions"),
            "tool_errors": (a.get("stream") or {}).get("tool_errors"), "claude_stderr": a.get("claude_stderr")}
    return facts


def contract(sessions, record, issues, iteration):
    """Engine contract only; never the content of a decision, never an extrapolated value."""
    def check(ok, detail=""):
        return {"ok": bool(ok), "detail": detail}
    out = {"iteration record exists and parses": check(record and record.get("schema_version") == 1 and record.get("iteration") == iteration, "iterations/%04d.json" % iteration),
           "iteration record status closed or failed": check(record and record.get("status") in ("closed", "failed"), (record or {}).get("status")),
           "no outage was opened": check(not any("outage" in {l["name"] for l in i["labels"]} for i in issues), ""),
           "every session issue is closed": check(all(s["state"] == "closed" for s in sessions), [s["issue"] for s in sessions if s["state"] != "closed"]),
           "every session reached a status": check(all(s["report"].get("status") for s in sessions), [s["issue"] for s in sessions if not s["report"].get("status")]),
           "the Policymaker's change is applied or refused": check(
               (next((s for s in sessions if s["role"] == "policymaker"), {}).get("report") or {}).get("merged") not in ("pending-iteration", "pending-owner", "pending-verifier"), "report.merged")}
    for s in sessions:
        a = s["analysis"].get("stream") or {}
        result = a.get("result") or {}
        if s["role"] and a:
            out["session #%d (%s): model call made on %s" % (s["issue"], s["role"], HAIKU)] = check(HAIKU in (result.get("modelUsage") or {}), list((result.get("modelUsage") or {})))
            out["session #%d (%s): turn limit held" % (s["issue"], s["role"])] = check((result.get("num_turns") or 0) <= MAX_TURNS + 1, result.get("num_turns"))
            out["session #%d (%s): time limit held" % (s["issue"], s["role"])] = check(int(s["report"].get("agent_s") or 0) <= MINUTES * 60 + 60, s["report"].get("agent_s"))
    return out


def write_readme(out, report):
    rows = ["| %s | %s | %s | %s | %s | %s |" % (s["issue"], s["role"], s["report"].get("status"), s["report"].get("turns"), s["report"].get("agent_s"), s["report"].get("merged"))
            for s in report.get("sessions", [])]
    (out / "README.md").write_text(f"""# Data of one real loop iteration on GitHub Actions (Haiku 5.5)

Collected by `e2e/live.py collect`. Start with `report.json`, then follow `analysis_file` of a session. Nothing in it is a
verdict: extrapolations (x{SCALE}) apply only to what accumulates (repository sizes) and to session inputs (reports capped
at {REPORT_WINDOW} iterations, the `iterations` index linear); times and tokens of an iteration are per iteration. The context
threshold is {CONTEXT_TOKENS} tokens. Token estimates are characters / 4; `session_inputs` compares them with the
context the CLI really reported on its first model call.

- `report.json`: `run` (repo, commits, times, platform); `sessions` (report fields as the engine published them);
  `iteration_record`; `actions` (queue, start, job and step times, cron delay, concurrency, billable-minute estimate);
  `actions_overhead` (prep, Tools, Stage, Epilogue around the agent per session run; width of the iteration: sessions, report sizes);
  `growth` (`accumulating` x{SCALE}, `per_iteration_not_scaled`, and what one iteration adds to the next sessions' inputs);
  `session_inputs` (estimated vs measured context, two projection models); `visibility` (did the planted step, the
  iteration's results and the exports reach the next iteration's sessions); `mechanics` (could the Policymaker change
  policy: tools, writes, diff, receipts, orders); `environment_facts` (sandbox mentions, stray files, trusted files incl.
  `unapproved`/`secrets`); `next_iteration_probe`; `contract` (the only pass/fail items); `collect_errors`.
- `sessions/<issue>-<role>/`: `session/` (the whole `.session/` as it was after the session: staged inputs and its
  outputs), `trusted/` (prompt, system contract, settings, tools and the helper files `agent.sh` used), `capture/` (machine,
  environment, files in the checkout), `stream.ndjson` (raw CLI stream), `analysis.json`, exit/time/stderr files.
- `github/`: `repo.json` (repository, variables, secrets names, workflows, branches), `issues/N.json` (issue, comments,
  timeline), `runs/<id>/` (run, jobs with steps, full log), `artifacts.json`, `commits.txt`.
- `repository/`: clone of the test branch (final state of `iterations/`, `journal/`, `policy/`, `execution/`, `src/`, history).
- `artifacts/`: every downloaded Actions artifact as GitHub holds it (`stream-*`, `capture-*`, `probe`).

| issue | role | status | turns | agent s | merged |
|---|---|---|---|---|---|
""" + "\n".join(rows) + "\n", encoding="utf-8")
