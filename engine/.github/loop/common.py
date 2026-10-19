#!/usr/bin/env python3
"""Shared plumbing of the loop engine: configuration, GitHub access, session reports, git.

No agent code runs here. Everything is called from `loop.py` and `iteration.py` in a checkout of the
default branch, never from a task branch: an agent cannot change the code that watches it.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import managed_resources as resources

REPO = os.environ.get("GITHUB_REPOSITORY", "")
MARK = "<!-- session-report -->"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
BOT = "github-actions[bot]"
ITER = "loop:iteration "                 # label `loop:iteration N`: the loop's field of view
ROLE = "role:"
AGENT_STATUSES = {"done", "partial", "blocked", "rejected"}
# Report fields written only by the engine. An agent's value for them is dropped when its report is published.
OWNED = ("attempts", "resume_after", "kicks", "kicked", "conflicts", "cause", "verification", "verdict", "improved",
         "merged", "detail", "model", "effort", "turns", "peak_context", "tokens_in", "tokens_out", "cache_read", "cache_write",
         "models", "agent_s", "gate_s", "total_s", "parked_s", "paused_at", "runner", "branch", "iteration", "artifacts",
         "verifier_issue", "verified_commit", "verifier_passed", "owner_accepted", "owner_accepted_commit")
SECRETS = ("GH_TOKEN", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

DEFAULT_TOOLS = ["Read", "Grep", "Glob", "Write", "Edit", "Skill", "Bash(git:*)", "Bash(python3:*)"]
DEFAULTS = {
    "resources": {"format": "layered"},
    "cli_version": "2.1.284",
    "author": {"name": "", "email": ""},
    "owner": "",
    "check": "",
    "protected": [".github/", "engine.yml", "ENGINE_VERSION", "iterations/", "goal/", "GOAL_REACHED", "templates/"],
    "direct_paths": [resources.EXECUTION + "/", resources.ROOT + "/", "audit/", "journal/"],
    "notes_paths": ["journal/"],
    "evaluator_paths": [resources.EVALUATOR],
    "policymaker_only_roles": ["evaluator", "audit"],
    "verifier_role": "evaluator-verifier",
    "large_file_bytes": 5000000,
    "gate": {"kind": "owner", "command": "", "timeout_minutes": 120, "env_passthrough": []},
    "limits": {"tools": DEFAULT_TOOLS, "internet_write": []},
    "policymaker": {"model": "claude-opus-5-5", "effort": "high", "max_turns": 150, "timeout_minutes": 90,
               "tools": DEFAULT_TOOLS, "write": [resources.EXECUTION + "/", resources.ROOT + "/"]},
    "policymaker_session_ceilings": None,
    "params": {"no_progress_iterations": None, "quiet_minutes": None, "owner_wait_counts": None,
               "stream_retention_iterations": None},
    "cadence": {"min_interval_minutes": None, "daily_iterations": None},
}


# ---------------------------------------------------------------- configuration

def load_yaml(path):
    import yaml
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def merged(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = merged(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def cfg():
    """`engine.yml` over the built-in defaults. Cwd is the checkout of the default branch."""
    return merged(DEFAULTS, load_yaml("engine.yml"))


def params():
    p = {"shaper": "orchestrator", "criterion": "", "models": {"default": "claude-sonnet-5-5"}, "efforts": ["low", "medium", "high"],
         "ceilings": {"job_minutes": 240, "iteration_minutes": None, "max_turns": 150},
         "policymaker_triggers": {"every_iterations": None, "no_progress": None}}
    return merged(p, load_yaml(resources.PARAMS))


def profiles():
    return resources.role_profiles()


def engine_version():
    try:
        with open("ENGINE_VERSION", encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return ""


def default_branch():
    return os.environ.get("DEFAULT_BRANCH", "main")


# ---------------------------------------------------------------- time

def now():
    return datetime.now(timezone.utc)


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(dt=None):
    return (dt or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- glob

def glob_re(p):
    out, i = "", 0
    while i < len(p):
        if p[i:i + 3] == "**/":
            out += "(?:.*/)?"
            i += 3
        elif p[i:i + 2] == "**":
            out += ".*"
            i += 2
        elif p[i] == "*":
            out += "[^/]*"
            i += 1
        elif p[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(p[i])
            i += 1
    return re.compile("^" + out + "$")


def path_match(path, patterns):
    """A pattern ending in `/` is a directory prefix; otherwise a glob (`*` stays in a segment, `**` crosses them)."""
    for p in patterns or []:
        if p.endswith("/"):
            if path.startswith(p):
                return True
        elif glob_re(p).match(path):
            return True
    return False


# ---------------------------------------------------------------- GitHub (thin: tests replace these functions)

def gh(*args, inp=None, check=True):
    r = subprocess.run(["gh", *args], input=inp, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit("gh %s: %s" % (" ".join(args[:3]), r.stderr.strip()))
    return r.stdout


def api(path, *fields, method="GET", inp=None):
    args = ["api", path, "-X", method]
    for f in fields:
        args += ["-F", f]
    out = gh(*args, inp=inp)
    return json.loads(out) if out.strip() else None


def api_list(path):
    out = gh("api", path, "--paginate", "--jq", ".[]")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def issue(n):
    return api("repos/%s/issues/%s" % (REPO, n))


def label_names(i):
    return {l["name"] if isinstance(l, dict) else l for l in i["labels"]}


def list_issues(state="open", labels=()):
    q = "repos/%s/issues?state=%s&per_page=100" % (REPO, state)
    if labels:
        q += "&labels=" + ",".join(urllib.parse.quote(l, safe=":") for l in labels)      # `loop:iteration 3` has a space
    return [x for x in api_list(q) if "pull_request" not in x]


def recent_closed(limit=30):
    return [x for x in api_list("repos/%s/issues?state=closed&sort=updated&direction=desc&per_page=%s" % (REPO, limit))
            if "pull_request" not in x]


def create_issue(title, body, labels=(), assignees=()):
    """A new issue; labels are created when missing. An owner who cannot be assigned (an organization, say) still gets
    the @mention in the body."""
    for l in labels:
        ensure_label(l)
    base = ["issue", "create", "--title", title, "--body-file", "-"]
    for l in labels:
        base += ["--label", l]
    for a in ([list(assignees)] if assignees else []) + [[]]:
        args = base + [x for who in a for x in ("--assignee", who)]
        r = subprocess.run(["gh", *args], input=body, capture_output=True, text=True)
        if r.returncode == 0:
            return int(r.stdout.strip().rsplit("/", 1)[1])
    raise SystemExit("gh issue create: %s" % r.stderr.strip())


def edit_labels(n, add=(), remove=()):
    args = ["issue", "edit", str(n)]
    for a in add:
        ensure_label(a)
        args += ["--add-label", a]
    for r in remove:
        args += ["--remove-label", r]
    if len(args) > 3:
        gh(*args, check=False)


def close_issue(n, reason="completed"):
    gh("issue", "close", str(n), "--reason", reason, check=False)


_ENSURED = set()


def ensure_label(name, color="C5DEF5", desc=""):
    if name not in _ENSURED:
        gh("label", "create", name, "--color", color, "--description", desc, "--force", check=False)
        _ENSURED.add(name)


def comments(n):
    return api_list("repos/%s/issues/%s/comments" % (REPO, n))


def post_comment(n, body):
    return api("repos/%s/issues/%s/comments" % (REPO, n), "body=@-", method="POST", inp=body)


def patch_comment(cid, body):
    api("repos/%s/issues/comments/%s" % (REPO, cid), "body=@-", method="PATCH", inp=body)


def blockers(n):
    return api_list("repos/%s/issues/%s/dependencies/blocked_by" % (REPO, n))


def blocking(n):
    return api_list("repos/%s/issues/%s/dependencies/blocking" % (REPO, n))


def add_blocker(blocked, blocker):
    """`blocked` waits for `blocker` (native dependency; issue ids, not numbers, go to the API)."""
    api("repos/%s/issues/%s/dependencies/blocked_by" % (REPO, blocked), "issue_id=%s" % issue(blocker)["id"], method="POST")


def is_unblocked(n):
    import owner_requests

    def ready(b):
        if b["state"] != "closed":
            return False
        frozen = owner_requests.read(b["number"])
        if frozen:
            return frozen["status"] == "approved"
        # Labels can never grant capability/engine authority without a trusted snapshot.
        return not (label_names(b) & {"owner-request:" + k for k in owner_requests.KINDS})

    return all(ready(b) for b in blockers(n))


def workflow_run(workflow, **inputs):
    args = ["workflow", "run", workflow]
    for k, v in inputs.items():
        args += ["-f", "%s=%s" % (k, v)]
    gh(*args, check=False)


def run_list(workflow):
    out = gh("run", "list", "--workflow", workflow, "--limit", "100", "--json", "databaseId,displayTitle,status,createdAt")
    return json.loads(out or "[]")


def list_artifacts():
    out = api("repos/%s/actions/artifacts?per_page=100" % REPO)
    return (out or {}).get("artifacts", [])


def delete_artifact(artifact_id):
    gh("api", "-X", "DELETE", "repos/%s/actions/artifacts/%s" % (REPO, artifact_id), check=False)


def cancel_run(run_id):
    gh("run", "cancel", str(run_id), check=False)


def variable(name):
    r = subprocess.run(["gh", "api", "repos/%s/actions/variables/%s" % (REPO, name), "--jq", ".value"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


# ---------------------------------------------------------------- labels

def roles_of(names):
    return sorted(l[len(ROLE):] for l in names if l.startswith(ROLE))


def iteration_of(names):
    for l in names:
        if l.startswith(ITER) and l[len(ITER):].isdigit():
            return int(l[len(ITER):])
    return None


def in_loop(names):
    """An issue belongs to the loop <=> it has `loop:iteration N` (field of view) and a role (whom to run)."""
    return iteration_of(names) is not None and bool(roles_of(names))


def label_value(names, prefix):
    return next((l[len(prefix):] for l in sorted(names) if l.startswith(prefix)), None)


# ---------------------------------------------------------------- guard

def autopilot_on():
    """Live read of the variable; when the API denies it, the value from the run context. Fail-safe: only a literal `on`."""
    return (variable("AUTOPILOT") or os.environ.get("VARS_AUTOPILOT", "")) == "on"


def goal_reached():
    return os.path.exists("GOAL_REACHED")        # cwd = checkout of the default branch


def say(msg):
    """A sentence for the log and the run summary: a run never ends without a reason on its face."""
    print(msg)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n\n")


def guard_ok():
    if not autopilot_on():
        say("AUTOPILOT != on: stop")
        return False
    if goal_reached():
        say("GOAL_REACHED: stop")
        return False
    return True


def set_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join("%s=%s\n" % (k, re.sub(r"[\r\n]+", " ", str(v))) for k, v in kv.items())
    if path:
        with open(path, "a") as fh:
            fh.write(lines)
    else:
        print(lines, end="")


def probe():
    """Credential probe: 200 alive, 401 dead, 403 revoked. Costs no subscription quota."""
    req = urllib.request.Request("https://api.anthropic.com/v1/models", headers={
        "Authorization": "Bearer " + os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""),
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
    })
    try:
        return urllib.request.urlopen(req, timeout=20).status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0    # a transient network failure is not a dead credential


def outage_open():
    out = list_issues(labels=("outage",))
    if out:
        say("quiet: outage open #%s" % out[0]["number"])
    return bool(out)


def open_outage(title, why, todo):
    body = "## What stopped\n\n%s\n\n## What was tried\n\nThe watchdog: plain script, no agent.\n\n## What you have to do\n\n%s\n" % (why, todo)
    create_issue("OUTAGE: " + title, body, labels=("outage",), assignees=owner_login())


def owner_login():
    o = cfg()["owner"] or os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    return [o] if o else []


# ---------------------------------------------------------------- session report (one comment with a YAML block)

def trusted(c):
    return c["author_association"] in TRUSTED or c["user"]["login"] == BOT


def trusted_comments(n):
    return [c for c in comments(n) if trusted(c)]


def find_report(n):
    found = None
    for c in trusted_comments(n):
        if c["body"].startswith(MARK):
            found = c
    return found


YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.S)


def fields(body):
    m = YAML_BLOCK.search(body)
    d = {}
    for line in (m.group(1).splitlines() if m else []):
        k, sep, v = line.partition(":")
        if sep and k.strip():
            d[k.strip()] = re.split(r"\s+#", v.strip())[0]
    return d


def set_fields(body, upd):
    if not body.startswith(MARK):
        body = MARK + "\n" + body
    if not YAML_BLOCK.search(body):
        body = body.replace(MARK, MARK + "\n```yaml\n```", 1)
    m = YAML_BLOCK.search(body)
    lines = m.group(1).splitlines()
    for k, v in upd.items():
        lines = [l for l in lines if l.split(":", 1)[0].strip() != k]
        if v is not None:
            lines.append("%s: %s" % (k, v))
    return body[:m.start(1)] + "".join(l + "\n" for l in lines) + body[m.end(1):]


def write_report(n, body):
    cur = find_report(n)
    if cur:
        if cur["body"] != body:
            patch_comment(cur["id"], body)
    else:
        post_comment(n, body)


def report_fields(n):
    r = find_report(n)
    return fields(r["body"]) if r else {}


def update_report(n, upd, append=""):
    cur = find_report(n)
    body = set_fields(cur["body"] if cur else MARK + "\n", upd)
    if append:
        body = body.rstrip("\n") + "\n\n" + append + "\n"
    write_report(n, body)
    return body


def publish(n, text):
    """The agent's report -> the one marked comment. Engine-owned fields survive and the agent cannot set them."""
    if not text.startswith(MARK):
        text = MARK + "\n" + text
    cur = find_report(n)
    keep = {k: v for k, v in (fields(cur["body"]) if cur else {}).items() if k in OWNED}
    text = set_fields(text, {k: None for k in OWNED})
    write_report(n, set_fields(text, keep) if keep else text)


def safe_read(root, rel):
    """Text of `root/rel`, or None. Refuses symlinks and anything resolving outside `root`: the agent controls the tree."""
    p = os.path.join(root, rel)
    if os.path.islink(p) or not os.path.isfile(p):
        return None
    if not os.path.realpath(p).startswith(os.path.realpath(root) + os.sep):
        return None
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def export_issue(n, path, omit_markers=()):
    """Agent input: the issue body and the comments of trusted authors. The agent has no `gh`."""
    i = issue(n)
    parts = ["# Issue #%s: %s\n\n%s\n" % (n, i["title"], i["body"] or "")]
    for c in trusted_comments(n):
        if any(marker in c["body"] for marker in omit_markers):
            continue
        parts.append("\n---\ncomment %s (%s)\n\n%s\n" % (c["id"], c["user"]["login"], c["body"]))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))


def section(body, name):
    m = re.search(r"^## %s\s*\n(.*?)(?=^## |\Z)" % re.escape(name), body or "", re.S | re.M)
    return m.group(1).strip() if m else ""


def marker(body, name):
    """Value of a `<!-- name: value -->` line in an issue body."""
    m = re.search(r"<!-- %s: (.*?) -->" % re.escape(name), body or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- launching sessions

LAUNCHED = set()     # dispatches from this process: `gh run list` shows a new run late


def run_issue(title):
    """'session #12 · implementer' -> 'session #12' (do not confuse #12 with #123)."""
    return title.split(" · ")[0]


def session_runs():
    """Session runs except our own: the epilogue asks about the rest of the loop while its own run is still going."""
    own = os.environ.get("GITHUB_RUN_ID", "")
    return [r for r in run_list("session.yml") if str(r["databaseId"]) != own]


def launch(n):
    """The only way to start a session: validation, deduplication, dispatch. Strangers are filtered by trusted()."""
    if not guard_ok():
        return False
    i = issue(n)
    if i["state"] != "open" or not in_loop(label_names(i)):
        return False
    if not is_unblocked(n):
        return False
    if any(run_issue(r["displayTitle"]) == "session #%s" % n and r["status"] in ("queued", "in_progress", "waiting")
           for r in session_runs()):
        return False
    roles = roles_of(label_names(i))
    workflow_run("session.yml", issue=n, role=roles[0] if len(roles) == 1 else "")
    LAUNCHED.add(n)
    say("launch #%s" % n)
    return True


# ---------------------------------------------------------------- git

def auth_args():
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        return []
    b64 = base64.b64encode(("x-access-token:%s" % token).encode()).decode()
    return ["-c", "http.https://github.com/.extraheader=AUTHORIZATION: basic %s" % b64]


def git(cwd, *args, check=True, remote=False, inp=None):
    cmd = ["git"] + (auth_args() if remote else []) + list(args)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=inp, env=env)
    if check and r.returncode:
        raise SystemExit("git %s: %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r


def head(cwd="."):
    return git(cwd, "rev-parse", "HEAD").stdout.strip()


def tree_hash(rev, path, cwd="."):
    r = git(cwd, "rev-parse", "%s:%s" % (rev, path.rstrip("/")), check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def sync_main():
    """Make the checkout the current tip of the default branch (a throwaway checkout: nothing local is kept)."""
    d = default_branch()
    git(".", "fetch", "-q", "origin", d, remote=True)
    git(".", "checkout", "-q", "-f", "--detach", "origin/%s" % d)


class ConcurrentChange(Exception):
    """An owner-approved update no longer has the configuration it was reviewed against."""


def file_digest(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return None


def commit_main(files, message, remove=(), restore=None, expected=None):
    """Engine commit straight to the default branch: write `files` (path -> text), delete `remove`, optionally
    restore paths from a commit (`restore=(sha, [paths])`). Push races are retried from a fresh tip."""
    d = default_branch()
    for _ in range(5):
        git(".", "fetch", "-q", "origin", d, remote=True)
        git(".", "checkout", "-q", "-f", "--detach", "origin/%s" % d)
        for path, digest in (expected or {}).items():
            if file_digest(path) != digest:
                raise ConcurrentChange("%s changed since the proposal was frozen" % path)
        if restore:
            sha, paths = restore
            git(".", "rm", "-r", "-q", "-f", "--ignore-unmatch", *paths)
            present = [path for path in paths if tree_hash(sha, path)]
            if present:
                git(".", "checkout", sha, "--", *present)
        for p in remove:
            git(".", "rm", "-q", "-f", "--ignore-unmatch", p)
        for p, text in files.items():
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            git(".", "add", "-f", "--", p)
        git(".", "add", "-A")
        if git(".", "diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        git(".", "commit", "-q", "-m", message)
        if git(".", "push", "origin", "HEAD:refs/heads/%s" % d, check=False, remote=True).returncode == 0:
            return True
    raise SystemExit("cannot push to %s after 5 attempts" % d)


def clean_env(extra=()):
    env = {k: v for k, v in os.environ.items() if k not in SECRETS}
    for k in extra:
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def run_command(cmd, cwd, env=None, timeout=3600, extra_env=None):
    """A shell command from the repo (check, verification, gate, goal gate) in a PID namespace when `bwrap` is there:
    candidate code must not read the engine's token from /proc/<parent>/environ."""
    argv = [os.environ.get("LOOP_BASH", "bash"), "-c", cmd]
    if os.environ.get("LOOP_SANDBOX") == "1" and shutil.which("bwrap"):
        argv = ["bwrap", "--unshare-pid", "--die-with-parent", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                "--bind", os.path.realpath(cwd), os.path.realpath(cwd), "--bind", tempfile.gettempdir(), tempfile.gettempdir()] + argv
    e = dict(env if env is not None else clean_env())
    e.update(extra_env or {})
    return subprocess.run(argv, cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout)
