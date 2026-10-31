"""Build and control the real GitHub Actions experiment. Procedure: e2e/README.md."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from analysis import read, write_json
from scenario import BRANCH, TAG, HAIKU, MAX_TURNS, MINUTES, PLANT


sys.dont_write_bytecode = True        # build runs the engine's code inside the tree it commits: no caches there
HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
ENGINE = PLUGIN / "engine"
SHIFT = 0                       # which of the started iterations `collect` reads (0 = the first)
STATE = HERE / ".state"             # when `start` and `wait` ran: report.json records it
CLI_VERSION = "2.1.303"         # engine.yml pins 2.1.284, whose binary has no `claude-haiku-5-5` (CLI warns `unrecognized_model`);
                                # 2.1.303 has it (checked in both binaries). `build` raises the pin on `e2e-loop` only.
ITERATION_MINUTES = 60          # ceilings.iteration_minutes: a stuck iteration fails instead of hanging the test
WAIT_MINUTES = 120              # `wait` gives up after this
GOAL = """# Goal

Create two files, and nothing else:

1. `src/greeting.txt` containing the single line `hello from perpetua`.
2. `src/farewell.txt` containing the single line `goodbye after iteration N`, where N is the number of the iteration in
   which `src/greeting.txt` was merged. N must be a closed iteration with a record `iterations/NNNN.json`; an iteration
   has no record until it closes, so this file can only be written in a later iteration than the first one.

Each file is checked by the change gate (`policy/evaluator/gate.py`).
"""
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


def write_yaml(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8", newline="\n")


def iso(moment=None):
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_not_behind():
    """The engine is copied from this checkout, not from origin/main: a checkout behind main would test an old engine."""
    run(["git", "fetch", "origin", "main"], cwd=PLUGIN)
    if run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=PLUGIN, check=False).returncode:
        behind = run(["git", "rev-list", "--count", "HEAD..origin/main"], cwd=PLUGIN).stdout.strip()
        raise SystemExit("this checkout is %s commit(s) behind origin/main: its engine/ is not the current one. "
                         "Merge or rebase origin/main (`git merge origin/main`), then run `build` again." % behind)


def build(args):
    """A fresh loop repository as after setup and arming prerequisites, as the branch `e2e-loop`: the real engine at the
    root, every role on Haiku 5.5, a two-step goal whose second step needs a later iteration, a change gate that scores
    it, one worker role with a planted wasteful step, cadence that allows exactly N iterations. Pushes the branch and the tag `setup-baseline`; touches nothing else."""
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
    config["gate"] = {"kind": "script", "command": "python3 policy/evaluator/gate.py"}
    config["policymaker_session_ceilings"] = {"models": [HAIKU], "efforts": ["low"], "max_turns": MAX_TURNS, "timeout_minutes": MINUTES}
    config["cadence"] = {"min_interval_minutes": None, "daily_iterations": args.iterations} if args.iterations > 1 else {
        "min_interval_minutes": 10 ** 6, "daily_iterations": None}        # exactly N iterations (default one)
    write_yaml(work / "engine.yml", config)
    (work / "policy/evaluator").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "fixtures" / "gate.py", work / "policy/evaluator/gate.py")
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
