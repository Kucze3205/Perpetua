#!/usr/bin/env python3
"""E2E of one real loop iteration on real GitHub Actions, in the Perpetua repo itself, every role on Haiku 5.5.

Nothing here simulates GitHub or Actions: `build` makes the test branch, the real `session.yml`/`watchdog.yml` run it, and
`collect` reads everything back with `gh`. Needs `gh` (logged in, repo access) and PyYAML. Steps, in order:

    python3 e2e/live.py build            # branch `e2e-loop` (engine at the root, one trivial goal) + tag `setup-baseline`
    # the owner makes `e2e-loop` the default branch (Actions dispatch needs the workflows there); nothing below works before
    python3 e2e/live.py start            # AUTOPILOT=on, E2E_CAPTURE=on, dispatch the watchdog: iteration 0 begins
    python3 e2e/live.py wait             # until the iteration record exists (or an outage / timeout), then AUTOPILOT=off
    python3 e2e/live.py probe            # stage the NEXT iteration's sessions on a runner (e2e-probe.yml), nothing is run
    python3 e2e/live.py collect OUT      # everything GitHub holds about the run -> OUT (README.md there says what is what)
    # the owner restores `main` as the default branch
    python3 e2e/live.py finish           # variables off

`run OUT` = start + wait + probe + collect. Extrapolated values (x50) and scale thresholds are reported, never asserted.
Hard assertions would only cover the engine contract (sessions end, reports parse, policy changes decided, the record
exists, no outage): `collect` lists them under `contract` in report.json and exits 1 when one fails.
"""
import argparse
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.dont_write_bytecode = True        # build runs the engine's code inside the tree it commits: no caches there
HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
ENGINE = PLUGIN / "engine"
SHIFT = 0                       # which of the started iterations `collect` reads (0 = the first)
STATE = HERE / ".state"             # when `start` and `wait` ran: report.json records it
BRANCH, TAG = "e2e-loop", "setup-baseline"
HAIKU = "claude-haiku-5-5"
CLI_VERSION = "2.1.303"         # engine.yml pins 2.1.284, whose binary has no `claude-haiku-5-5` (CLI warns `unrecognized_model`);
                                # 2.1.303 has it (checked in both binaries). `build` raises the pin on `e2e-loop` only.
REPORT_WINDOW = 5               # iterations of reports in a session's input (engine: session_reports.py keeps the last 5)
SCALE = 50                      # iterations the one measured iteration is extrapolated to
CONTEXT_TOKENS = 250_000        # the session context the loop should stay under at that scale
MAX_TURNS, MINUTES = 30, 10     # limits of every role in this environment
ITERATION_MINUTES = 60          # ceilings.iteration_minutes: a stuck iteration fails instead of hanging the test
WAIT_MINUTES = 120              # `wait` gives up after this
PLANT = "Read `.session/issue.md` in full a second time"
GOAL = """# Goal

Create two independent files, and nothing else:

- `src/greeting.txt` containing the single line `hello from perpetua`.
- `src/farewell.txt` containing the single line `goodbye from perpetua`.
"""
GATE = "echo '{\"verdict\": \"accepted\", \"improved\": true, \"detail\": \"e2e gate\"}'\n"
WORKER_SKILL = f"""---
name: worker
description: Writes the one small text file its task asks for, then reports.
---

# Worker

Do exactly what `.session/issue.md` asks.

## Steps

1. Read `.session/issue.md`; follow the engine contract in system instructions.
2. {PLANT} and copy every acceptance line into your notes before touching anything.
3. Read `.session/issue.md` in full a third time and compare it with your notes line by line.
4. Write the requested file under `src/`.
5. Read the file back and confirm its content.
6. Write `.session/report.md` with status `done`.
"""
BRANCH_NOTE = """# Throwaway test branch

Built by `e2e/live.py build` in the Perpetua plugin repository from `engine/`. It is the default branch of
the repo only while the E2E test of one real loop iteration runs; `main` is restored afterwards. Do not work here.
"""
OUTPUTS = ("report.md", "tasks.json", "orders.json", "suggestions.json", "requests.json", "suggestion-decisions.json")
SECRET_RE = re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}")


# ---------------------------------------------------------------- plumbing

def say(*parts):
    print(*parts, flush=True)


def run(args, cwd=None, check=True, inp=None, binary=False):
    r = subprocess.run(args, cwd=cwd, input=inp, capture_output=True, **({} if binary else dict(text=True, encoding="utf-8", errors="replace")))
    if check and r.returncode:
        err = r.stderr if binary is False else r.stderr.decode("utf-8", "replace")
        raise SystemExit("%s: %s" % (" ".join(map(str, args[:4])), err.strip()))
    return r


def gh(*args, check=True, binary=False):
    return run(["gh", *args], check=check, binary=binary)


def api(path, *extra):
    out = gh("api", path, *extra).stdout
    return json.loads(out) if out.strip() else None


def api_items(path, key=None):
    """Every item of a paginated list endpoint (`key`: the array inside an object, as for runs, jobs, artifacts)."""
    out = gh("api", path, "--paginate", "--jq", ".%s[]" % (key or "")).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def repo():
    return gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").stdout.strip()


def default_branch():
    return gh("repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name").stdout.strip()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def write_yaml(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8", newline="\n")


def read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def tokens(text):
    return math.ceil(len(text) / 4)                 # characters / 4: the same rough estimate everywhere


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def seconds(a, b):
    a, b = parse_time(a), parse_time(b)
    return round((b - a).total_seconds(), 1) if a and b else None


def iso(moment=None):
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- build: the test branch

def check_not_behind():
    """The engine is copied from this checkout, not from origin/main: a checkout behind main would test an old engine."""
    run(["git", "fetch", "origin", "main"], cwd=PLUGIN)
    if run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=PLUGIN, check=False).returncode:
        behind = run(["git", "rev-list", "--count", "HEAD..origin/main"], cwd=PLUGIN).stdout.strip()
        raise SystemExit("this checkout is %s commit(s) behind origin/main: its engine/ is not the current one. "
                         "Merge or rebase origin/main (`git merge origin/main`), then run `build` again." % behind)


def build(args):
    """A fresh loop repository as after setup and arming prerequisites, as the branch `e2e-loop`: the real engine at the
    root, every role on Haiku 5.5, one trivial goal, one worker role with a planted wasteful step, cadence that allows
    exactly one iteration. Pushes the branch and the tag `setup-baseline`; touches nothing else."""
    check_not_behind()
    if default_branch() == BRANCH:
        raise SystemExit("`%s` is the default branch: a running test must not be rebuilt under itself" % BRANCH)
    url = run(["git", "remote", "get-url", "origin"], cwd=PLUGIN).stdout.strip()
    name = repo()
    work = Path(tempfile.mkdtemp(prefix="perpetua-e2e-build-"))
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ENGINE, work, ignore=ignore, dirs_exist_ok=True)
    shutil.copyfile(PLUGIN / "CONTEXT.md", work / "CONTEXT.md")
    shutil.copyfile(HERE / "probe" / "e2e-probe.yml", work / ".github" / "workflows" / "e2e-probe.yml")
    (work / "e2e").mkdir()
    shutil.copyfile(HERE / "probe" / "probe.py", work / "e2e" / "probe.py")
    (work / "E2E-BRANCH.md").write_text(BRANCH_NOTE, encoding="utf-8", newline="\n")
    loop_dir = str(work / ".github" / "loop")
    sys.path.insert(0, loop_dir)
    os.chdir(work)
    import setup_roles                                  # the engine's own setup code, from the copy just made
    import common as c
    setup_roles.prepare(work)
    light = {"effort": "low", "max_turns": MAX_TURNS, "timeout_minutes": MINUTES}
    # The policy roles ship with the engine; only their cost is lowered here.
    policy_profiles = c.load_yaml(work / "policy/profiles.yml")
    for profile in policy_profiles.values():
        profile.pop("model", None)
        profile.update(light)
    write_yaml(work / "policy/profiles.yml", policy_profiles)
    (work / "execution/roles/orchestrator").mkdir(parents=True)
    shutil.copyfile(HERE / "fixtures" / "orchestrator" / "SKILL.md", work / "execution/roles/orchestrator/SKILL.md")
    orchestrator = c.load_yaml(HERE / "fixtures" / "orchestrator" / "profile.yml")
    setup_roles.install(work, "orchestrator", "execution", dict({k: v for k, v in orchestrator.items() if k != "model"}, **light))
    (work / "execution/roles/worker").mkdir(parents=True)
    (work / "execution/roles/worker/SKILL.md").write_text(WORKER_SKILL, encoding="utf-8", newline="\n")
    setup_roles.install(work, "worker", "execution", dict(
        light, tools=["Read", "Grep", "Glob", "Write", "Edit", "Bash(git:*)", "Bash(python3:*)", "Bash(echo:*)", "Bash(printf:*)", "Bash(mkdir:*)"],
        write=["src/**"]))
    params = c.load_yaml(work / "policy/params.yml")
    params.update(shaper="orchestrator", models={"default": HAIKU, "opus": HAIKU},
                  policymaker_triggers={"every_iterations": 1, "no_progress": None},
                  policymaker_session={"model": HAIKU, "effort": "low", "max_turns": MAX_TURNS, "timeout_minutes": MINUTES})
    params["ceilings"] = dict(params.get("ceilings") or {}, iteration_minutes=ITERATION_MINUTES)
    write_yaml(work / "policy/params.yml", params)
    config = c.load_yaml(work / "engine.yml")
    config["cli_version"] = CLI_VERSION
    config["gate"] = {"kind": "script", "command": "bash policy/evaluator/gate.sh"}
    config["policymaker_session_ceilings"] = {"models": [HAIKU], "efforts": ["low"], "max_turns": MAX_TURNS, "timeout_minutes": MINUTES}
    config["cadence"] = {"min_interval_minutes": None, "daily_iterations": args.iterations} if args.iterations > 1 else {
        "min_interval_minutes": 10 ** 6, "daily_iterations": None}        # exactly N iterations (default one)
    write_yaml(work / "engine.yml", config)
    (work / "policy/evaluator").mkdir(parents=True, exist_ok=True)
    (work / "policy/evaluator/gate.sh").write_text(GATE, encoding="utf-8", newline="\n")
    (work / "goal").mkdir(exist_ok=True)
    (work / "goal/goal.md").write_text(GOAL, encoding="utf-8", newline="\n")
    git = lambda *a, **k: run(["git", "-c", "core.autocrlf=false", *a], cwd=work, **k).stdout.strip()
    git("init", "-q", "-b", BRANCH)
    git("add", "-A")
    git("commit", "-q", "-m", "E2E test environment: engine %s, every role on %s" % (read(work / "ENGINE_VERSION").strip(), HAIKU))
    git("tag", "-f", TAG)
    run([sys.executable, ".github/loop/setup_roles.py", "validate", "--baseline", TAG], cwd=work)
    sha = git("rev-parse", "HEAD")
    write_json(STATE / "built.json", {"plugin_commit": run(["git", "rev-parse", "HEAD"], cwd=PLUGIN).stdout.strip(),
                                      "engine_dirty": bool(run(["git", "status", "--porcelain", "--", "engine"], cwd=PLUGIN).stdout.strip())})
    say("built %s at %s (tag %s); tree %s" % (BRANCH, sha, TAG, work))
    if args.no_push:
        return
    git("push", "-f", url, BRANCH)
    git("push", "-f", url, "refs/tags/" + TAG)
    say("Next: the owner sets `%s` as the default branch of %s, then `python3 e2e/live.py start`." % (BRANCH, name))


# ---------------------------------------------------------------- start / wait / probe / finish

def set_var(name, value):
    gh("variable", "set", name, "--body", value)


def start(args):
    if default_branch() != BRANCH:
        raise SystemExit("the default branch is `%s`, not `%s`: Actions would not find the workflows" % (default_branch(), BRANCH))
    secrets = gh("secret", "list").stdout
    if "CLAUDE_CODE_OAUTH_TOKEN" not in secrets:
        raise SystemExit("secret CLAUDE_CODE_OAUTH_TOKEN is missing (the owner sets it with `gh secret set`)")
    if loop_issues("open"):
        raise SystemExit("loop issues are already open")
    # Session issues of earlier runs (and probes) still carry `loop:iteration N`; the new iteration reuses that number, so
    # the loop would take them into its record and the Policymaker's input. Without the label the loop does not see them.
    for old in loop_issues("closed"):
        for label in [l["name"] for l in old["labels"] if l["name"].startswith("loop:iteration ")]:
            gh("issue", "edit", str(old["number"]), "--remove-label", label)
    number = len([i for i in api_items("repos/%s/issues?state=all&labels=loop:map&per_page=100" % repo()) if "pull_request" not in i])
    set_var("E2E_CAPTURE", "on")
    set_var("AUTOPILOT", "on")
    gh("workflow", "run", "watchdog.yml")
    commit = api("repos/%s/commits/%s" % (repo(), BRANCH))["sha"]
    write_json(STATE / "started.json", {"at": iso(), "commit": commit, "repo": repo(), "iteration": number, "iterations": args.iterations})
    say("started at %s, commit %s: iteration %d begins with the watchdog run" % (iso(), commit, number))


def target():
    """The iteration the test measures: the number the loop gives its next iteration, fixed by `start` (+ SHIFT: collect of a later one)."""
    f = STATE / "started.json"
    return (json.loads(read(f)).get("iteration", 0) if f.is_file() else 0) + SHIFT


def iterations():
    f = STATE / "started.json"
    return json.loads(read(f)).get("iterations", 1) if f.is_file() else 1


def loop_issues(state="all"):
    return [i for i in api_items("repos/%s/issues?state=%s&per_page=100" % (repo(), state))
            if "pull_request" not in i and any(l["name"].startswith("loop:iteration ") for l in i["labels"])
            and any(l["name"].startswith("role:") for l in i["labels"])]


def record_exists(number=0):
    r = gh("api", "repos/%s/contents/iterations/%04d.json?ref=%s" % (repo(), number, BRANCH), check=False)
    return r.returncode == 0


def wait(args):
    deadline = time.time() + WAIT_MINUTES * 60
    why = "timeout after %d minutes" % WAIT_MINUTES
    try:
        while time.time() < deadline:
            time.sleep(45)
            open_issues = [i for i in loop_issues("open")]
            outage = api_items("repos/%s/issues?state=open&labels=outage" % repo())
            runs = api_items("repos/%s/actions/runs?per_page=30" % repo(), "workflow_runs")
            live = [r["display_title"] for r in runs if r["status"] in ("queued", "in_progress", "waiting")]
            say("%s open loop issues: %s | live runs: %s" % (iso(), [i["number"] for i in open_issues], live))
            if outage:
                why = "outage opened: #%s %s" % (outage[0]["number"], outage[0]["title"])
                break
            if record_exists(target() + iterations() - 1) and not open_issues and not any(t.startswith("session #") for t in live):
                why = "iteration record iterations/%04d.json exists and nothing is running" % (target() + iterations() - 1)
                break
    finally:
        set_var("AUTOPILOT", "off")
    say("wait over: " + why)
    write_json(STATE / "waited.json", {"at": iso(), "why": why})


def probe(args):
    if api("repos/%s/actions/variables/AUTOPILOT" % repo())["value"] == "on":
        raise SystemExit("AUTOPILOT is on: run `wait` first")
    before = {r["id"] for r in api_items("repos/%s/actions/workflows/e2e-probe.yml/runs?per_page=50" % repo(), "workflow_runs")}
    gh("workflow", "run", "e2e-probe.yml")
    for _ in range(60):
        time.sleep(10)
        runs = [r for r in api_items("repos/%s/actions/workflows/e2e-probe.yml/runs?per_page=50" % repo(), "workflow_runs") if r["id"] not in before]
        if runs and runs[0]["status"] == "completed":
            say("probe run %s: %s" % (runs[0]["id"], runs[0]["conclusion"]))
            return
    say("probe did not finish in 10 minutes; `collect` records what exists")


def finish(args):
    set_var("AUTOPILOT", "off")
    gh("variable", "delete", "E2E_CAPTURE", check=False)
    say("AUTOPILOT=off, E2E_CAPTURE removed. The default branch is now `%s`; the owner restores `main`." % default_branch())


# ---------------------------------------------------------------- measurement helpers

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


# ---------------------------------------------------------------- collect

def collect(args):
    out, R = Path(args.out).resolve(), repo()
    out.mkdir(parents=True, exist_ok=True)
    errors = []

    def attempt(label, fn, *a):
        try:
            return fn(*a)
        except BaseException as error:           # keep going: a missing piece is data, the rest is still worth having
            errors.append({"step": label, "error": repr(error)[:500], "trace": traceback.format_exc()[-1500:]})
            say("collect: %s failed: %s" % (label, repr(error)[:200]))

    meta = attempt("meta", fetch_meta, out, R) or {}
    issues = attempt("issues", fetch_issues, out, R) or []
    runs = attempt("runs", fetch_runs, out, R) or []
    attempt("artifacts", fetch_artifacts, out, R)
    attempt("captures", fetch_captures, out, R)
    attempt("repository", fetch_repository, out, R)
    report = attempt("measure", measure, out, R, issues, runs) or {}
    report["collect_errors"] = errors
    report["run"] = dict(meta, harness_commit=run(["git", "rev-parse", "HEAD"], cwd=PLUGIN, check=False).stdout.strip(),
                         harness_dirty=bool(run(["git", "status", "--porcelain"], cwd=PLUGIN, check=False).stdout.strip()),
                         collected_at=iso(), collector_platform=platform.platform(), command=" ".join(sys.argv),
                         model=HAIKU, scale=SCALE, context_tokens=CONTEXT_TOKENS, limits={"max_turns": MAX_TURNS, "minutes": MINUTES})
    for name in ("built", "started", "waited"):
        if (STATE / (name + ".json")).is_file():
            report["run"][name] = json.loads(read(STATE / (name + ".json")))
    write_json(out / "report.json", report)
    write_readme(out, report)
    redacted = 0
    for path in out.rglob("*"):                  # a credential never goes into the data
        if path.is_file() and path.stat().st_size < 50_000_000:
            raw = path.read_bytes()
            if SECRET_RE.search(raw):
                path.write_bytes(SECRET_RE.sub(b"[credential]", raw))
                redacted += 1
    passed = bool(report.get("contract")) and all(v["ok"] for v in report["contract"].values())
    say("collected into %s (%d files redacted of a token-shaped string); contract: %s" % (out, redacted, "OK" if passed else "FAILED"))
    if not passed:
        sys.exit(1)


def fetch_meta(out, R):
    meta = {"repo": R, "default_branch": default_branch(), "branch": BRANCH,
            "repository": api("repos/" + R), "variables": api("repos/%s/actions/variables" % R),
            "secrets": json.loads(gh("secret", "list", "--json", "name,updatedAt").stdout),
            "workflows": api("repos/%s/actions/workflows" % R), "branches": api_items("repos/%s/branches" % R),
            "tags": api_items("repos/%s/tags" % R), "actions_permissions": api("repos/%s/actions/permissions" % R),
            "gh_version": gh("--version").stdout.splitlines()[0]}
    write_json(out / "github" / "repo.json", meta)
    return {k: meta[k] for k in ("repo", "default_branch", "branch", "gh_version")}


def fetch_issues(out, R):
    issues = [i for i in api_items("repos/%s/issues?state=all&per_page=100" % R) if "pull_request" not in i]
    for issue in issues:
        n = issue["number"]
        issue["comments_list"] = api_items("repos/%s/issues/%s/comments" % (R, n))
        issue["timeline"] = api_items("repos/%s/issues/%s/timeline" % (R, n))
        write_json(out / "github" / "issues" / ("%d.json" % n), issue)
    return issues


def fetch_runs(out, R):
    runs = api_items("repos/%s/actions/runs?per_page=100" % R, "workflow_runs")
    for r in runs:
        d = out / "github" / "runs" / str(r["id"])
        write_json(d / "run.json", r)
        r["jobs_list"] = api_items("repos/%s/actions/runs/%s/jobs?per_page=100" % (R, r["id"]), "jobs")
        write_json(d / "jobs.json", r["jobs_list"])
        log = gh("run", "view", str(r["id"]), "--log", check=False)
        (d / "log.txt").write_text(log.stdout or log.stderr, encoding="utf-8")
        found = re.search(r"ISSUE: (\d+)", log.stdout or "")      # run-name does not carry the issue (see the report)
        r["issue"] = int(found.group(1)) if found and r["name"] == "session" else None
    return runs


def fetch_artifacts(out, R):
    arts = api_items("repos/%s/actions/artifacts?per_page=100" % R, "artifacts")
    write_json(out / "github" / "artifacts.json", arts)
    for a in arts:
        run_id = (a.get("workflow_run") or {}).get("id")
        if a.get("expired") or not run_id:
            continue
        gh("run", "download", str(run_id), "--name", a["name"], "--dir", str(out / "artifacts" / a["name"]), check=False)


def fetch_captures(out, R):
    """Release `e2e-capture`: the session captures and the probe (E2E_CAPTURE; release assets, not artifacts)."""
    tmp = out / "release-assets"
    r = gh("release", "download", "e2e-capture", "--dir", str(tmp), "--clobber", check=False)
    if r.returncode:
        raise SystemExit("no release e2e-capture: " + r.stderr.strip())
    for f in tmp.glob("*.tgz"):
        dest = out / "artifacts" / f.stem
        dest.mkdir(parents=True, exist_ok=True)
        import tarfile
        with tarfile.open(f) as tar:
            tar.extractall(dest, filter="data")


def fetch_repository(out, R):
    url = run(["git", "remote", "get-url", "origin"], cwd=PLUGIN).stdout.strip()
    target = out / "repository"
    if target.exists():
        shutil.rmtree(target, onexc=lambda f, p, e: (os.chmod(p, 0o700), f(p)))     # git objects are read-only on Windows
    run(["git", "clone", "-q", "--branch", BRANCH, url, str(target)])
    log = run(["git", "log", "--format=%H%x09%an%x09%aI%x09%s", "--numstat"], cwd=target).stdout
    (out / "github" / "commits.txt").write_text(log, encoding="utf-8")


def measure(out, R, issues, runs):
    repo_dir = out / "repository"
    by_number = {i["number"]: i for i in issues}
    begun = (json.loads(read(STATE / "started.json")).get("at") if (STATE / "started.json").is_file() else "")
    sessions = []
    for i in issues:
        names = {l["name"] for l in i["labels"]}
        roles = [n[5:] for n in names if n.startswith("role:")]
        iteration = next((int(n.split()[-1]) for n in names if n.startswith("loop:iteration ")), None)
        if not roles or iteration != target() or i["title"].startswith("e2e probe") or i["created_at"] < begun:
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
    record_path = repo_dir / "iterations" / ("%04d.json" % target())
    record = json.loads(read(record_path)) if record_path.is_file() else None
    timing = actions_timing(runs)
    probe_data, probe_texts = probe_view(out)
    report = {"sessions": [{k: v for k, v in s.items() if k != "analysis"} | {"analysis_file": "sessions/%s/analysis.json" % s["dir"]} for s in sessions],
              "iteration_record": record, "actions": timing, "actions_overhead": actions_overhead(sessions, runs), "growth": growth(repo_dir, out, issues, sessions, record, probe_data),
              "session_inputs": session_inputs(sessions, probe_data), "visibility": visibility(out, sessions, probe_texts),
              "mechanics": policymaker_mechanics(repo_dir, sessions, record),
              "environment_facts": environment_facts(sessions, probe_data),
              "next_iteration_probe": probe_data}
    report["contract"] = contract(sessions, record, issues, runs)
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


def growth(repo_dir, out, issues, sessions, record, probe_data):
    """What accumulates with iterations (this iteration's value and x SCALE: the repository only grows) against what one
    iteration costs (reported as is: a time or token ceiling is per iteration, multiplying it compares nothing), and how
    the sessions' inputs grow (see `input_projection`)."""
    def tree(prefix):
        base = repo_dir / prefix
        return sum(p.stat().st_size for p in base.rglob("*") if p.is_file()) if base.exists() else 0
    state_sizes = [s["analysis"]["staged"]["files"].get("loop-state.json") for s in sessions if s["analysis"].get("staged")]
    record_file = repo_dir / "iterations" / ("%04d.json" % target())
    accumulating = {
        "iterations/ directory, bytes": tree("iterations"), "journal/, bytes": tree("journal"), "audit/, bytes": tree("audit"),
        "policy/ + execution/, bytes": tree("policy") + tree("execution"),
        # from the analysed streams, not from artifacts: artifacts may be absent (quota), the capture release still has them
        "raw session streams, bytes": sum(s["analysis"].get("stream_bytes") or 0 for s in sessions),
        "issue comments of the loop (reports, state, orders), bytes": sum(len(c["body"]) for i in issues if any(l["name"].startswith("loop:iteration ") for l in i["labels"]) for c in i["comments_list"]),
        "commits on the branch since the baseline, count": len(run(["git", "rev-list", "%s..HEAD" % TAG], cwd=repo_dir, check=False).stdout.split()),
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
    diff = run(["git", "diff", "--stat", TAG, "HEAD", "--", "execution", "policy"], cwd=repo_dir, check=False).stdout
    changes = sorted(p.relative_to(repo_dir).as_posix() for p in (repo_dir / "policy" / "changes").glob("*")) if (repo_dir / "policy" / "changes").is_dir() else []
    return {"policymaker_issue": pm["issue"], "report": pm["report"], "tools_offered": stream.get("tools_offered"), "tool_errors": stream.get("tool_errors"),
            "tried_to_write_execution_or_policy": managed, "diff_of_execution_and_policy_since_baseline": diff, "change_receipts": changes,
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


def contract(sessions, record, issues, runs):
    """Engine contract only; never the content of a decision, never an extrapolated value."""
    def check(ok, detail=""):
        return {"ok": bool(ok), "detail": detail}
    out = {"iteration record exists and parses": check(record and record.get("schema_version") == 1 and record.get("iteration") == target(), "iterations/%04d.json" % target()),
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


def run_all(args):
    global SHIFT
    start(args)
    wait(args)
    probe(args)
    n, out = iterations(), args.out
    for k in range(n):                          # one directory per iteration: OUT, or OUT-1, OUT-2... when there are several
        SHIFT, args.out = k, out if n == 1 else "%s-%d" % (out, k + 1)
        collect(args)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    for name, fn in (("start", start), ("wait", wait), ("probe", probe), ("finish", finish)):
        q = sub.add_parser(name)
        if name == "start":
            q.add_argument("--iterations", type=int, default=1)
        q.set_defaults(fn=fn)
    b = sub.add_parser("build")
    b.add_argument("--iterations", type=int, default=1, help="how many iterations the cadence allows (the Policymaker of the 2nd sees the 1st)")
    b.add_argument("--no-push", action="store_true", help="build the tree and commit locally, push nothing")
    b.set_defaults(fn=build)
    for name, fn in (("collect", collect), ("run", run_all)):
        p = sub.add_parser(name)
        p.add_argument("out")
        if name == "run":
            p.add_argument("--iterations", type=int, default=1)
        p.set_defaults(fn=fn)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
