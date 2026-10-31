"""Download GitHub evidence and save all inputs needed for offline analysis."""
import json
import os
import platform
import re
import shutil
import sys
import traceback
from datetime import timedelta
from pathlib import Path

import analysis
from analysis import read, write_json
from experiment import (PLUGIN, STATE, api, api_items, default_branch, gh, iso,
                        repo, run, say, target)
from scenario import BRANCH, HAIKU, SCALE, CONTEXT_TOKENS, MAX_TURNS, MINUTES

SECRET_RE = re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}")


def since():
    """The start of the run's window: `start`, minus the watchdog it dispatched just before recording the time. Evidence of
    earlier runs in the repository is not this run's and is not downloaded."""
    started = json.loads(read(STATE / "started.json")) if (STATE / "started.json").is_file() else {}
    if not started.get("at"):
        raise SystemExit("no `start` recorded in e2e/.state/started.json: nothing to bound the collection by")
    return iso(analysis.parse_time(started["at"]) - timedelta(minutes=5))


def collect(args):
    """Download this run's evidence into OUT and analyze the selected iteration. `args.fetched`: a directory a collect of
    the same run already filled; it is copied instead of downloading the same evidence again."""
    out, R = Path(args.out).resolve(), repo()
    fetched = getattr(args, "fetched", None)
    if fetched and Path(fetched).resolve() != out:
        shutil.copytree(fetched, out, dirs_exist_ok=True, ignore=shutil.ignore_patterns("report.json", "README.md", "sessions"))
        previous = json.loads(read(out / "collection.json"))
        return finish(out, dict(previous, iteration=target()))
    out.mkdir(parents=True, exist_ok=True)
    errors = []

    def attempt(label, fn, *a):
        try:
            return fn(*a)
        except BaseException as error:           # keep going: a missing piece is data, the rest is still worth having
            errors.append({"step": label, "error": repr(error)[:500], "trace": traceback.format_exc()[-1500:]})
            say("collect: %s failed: %s" % (label, repr(error)[:200]))

    begun = since()
    meta = attempt("meta", fetch_meta, out, R) or {}
    issues = attempt("issues", fetch_issues, out, R, begun) or []
    runs = attempt("runs", fetch_runs, out, R, begun) or []
    attempt("artifacts", fetch_artifacts, out, R, begun)
    attempt("captures", fetch_captures, out, R, begun)
    attempt("repository", fetch_repository, out, R)
    context = {"iteration": target(), "collect_errors": errors,
               "issues": [i["number"] for i in issues], "runs": [r["id"] for r in runs]}
    context["run"] = dict(meta, harness_commit=run(["git", "rev-parse", "HEAD"], cwd=PLUGIN, check=False).stdout.strip(),
                          harness_dirty=bool(run(["git", "status", "--porcelain"], cwd=PLUGIN, check=False).stdout.strip()),
                          collected_at=iso(), collector_platform=platform.platform(), command=" ".join(sys.argv),
                          model=HAIKU, scale=SCALE, context_tokens=CONTEXT_TOKENS, limits={"max_turns": MAX_TURNS, "minutes": MINUTES})
    for name in ("built", "started", "waited"):
        if (STATE / (name + ".json")).is_file():
            context["run"][name] = json.loads(read(STATE / (name + ".json")))
    finish(out, context)


def finish(out, context):
    write_json(out / "collection.json", context)
    report = analysis.analyze(out)
    redacted = 0
    for path in out.rglob("*"):                  # a credential never goes into the data
        if path.is_file() and path.stat().st_size < 50_000_000:
            raw = path.read_bytes()
            if SECRET_RE.search(raw):
                path.write_bytes(SECRET_RE.sub(b"[credential]", raw))
                redacted += 1
    passed = analysis.passed(report)
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


def fetch_issues(out, R, begun):
    issues = [i for i in api_items("repos/%s/issues?state=all&since=%s&per_page=100" % (R, begun))
              if "pull_request" not in i and i["created_at"] >= begun]
    for issue in issues:
        n = issue["number"]
        issue["comments_list"] = api_items("repos/%s/issues/%s/comments" % (R, n))
        write_json(out / "github" / "issues" / ("%d.json" % n), issue)
    return issues


def fetch_runs(out, R, begun):
    """The run's workflow runs with their jobs. No logs: the sessions' streams and the issues carry what the analysis reads."""
    runs = api_items("repos/%s/actions/runs?per_page=100&created=%%3E%%3D%s" % (R, begun), "workflow_runs")
    for r in runs:
        d = out / "github" / "runs" / str(r["id"])
        r["jobs_list"] = api_items("repos/%s/actions/runs/%s/jobs?per_page=100" % (R, r["id"]), "jobs")
        write_json(d / "jobs.json", r["jobs_list"])
        found = re.match(r"session #(\d+)", r.get("display_title") or "")       # session.yml's run-name
        r["issue"] = int(found.group(1)) if found and r["name"] == "session" else None
        write_json(d / "run.json", {k: v for k, v in r.items() if k != "jobs_list"})
    return runs


KEPT_ARTIFACTS = ("stream-", "probe")     # what the analysis reads; staged inputs come from the captures


def fetch_artifacts(out, R, begun):
    arts = [a for a in api_items("repos/%s/actions/artifacts?per_page=100" % R, "artifacts") if a["created_at"] >= begun]
    write_json(out / "github" / "artifacts.json", arts)
    for a in arts:
        run_id = (a.get("workflow_run") or {}).get("id")
        if a.get("expired") or not run_id or not a["name"].startswith(KEPT_ARTIFACTS):
            continue
        gh("run", "download", str(run_id), "--name", a["name"], "--dir", str(out / "artifacts" / a["name"]), check=False)


def fetch_captures(out, R, begun):
    """Release `e2e-capture`: the session captures and the probe (E2E_CAPTURE; release assets, not artifacts), only the
    assets this run uploaded."""
    tmp = out / "release-assets"
    release = api("repos/%s/releases/tags/e2e-capture" % R)
    for asset in release["assets"]:
        if asset["created_at"] >= begun:
            gh("release", "download", "e2e-capture", "--pattern", asset["name"], "--dir", str(tmp), "--clobber")
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
