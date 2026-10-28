#!/usr/bin/env python3
"""The hard code of the loop: session start, epilogue, change gate, watchdog. Zero agent.

Always run from a checkout of the default branch, never from a task branch. The agent's work arrives as commits
fetched into this checkout; nothing is executed inside the agent's directory.

Commands: guard, probe, resolve, stage, epilogue, request-closed, watch, resume, start, git-identity.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone

import common as c
import managed_resources as resources
import iteration as it
import session_security as security
import session_inputs
import session_exports
import session_artifact
import session_reports
import policymaker_session
import executor_boundary
import session_context
import session_startup

BACKOFF_H = (1, 5, 24)          # when the session result carries no limit reset time
MAX_AGE_DAYS = 30
MAX_KICKS = 3
CRASH_STREAK = 3                # consecutive `crashed` without a commit
MAX_SLEEP_S = 340 * 60          # resume.yml: a job is limited to 360 min; a longer park is the watchdog's
GRACE_MIN = 30                  # covers dispatch latency, never working time
MAX_GEN = 3                     # successor generations
MAX_CONFLICTS = 3
MAX_FIXES = 2                   # correction rounds after a failed `## Verification` or `check`
PUSH_TRIES = 3
JOB_MAX_MIN = 355               # GitHub kills a job at 360 min; the epilogue must fit


# ---------------------------------------------------------------- start of a session

def consent_status(profile, labels):
    """(granted, why-not). Granted by the closed (completed) consent request of the task, also within the limits.
    A profile beyond the accepted limits runs only with that consent."""
    if _consent_request(labels):
        return True, None
    exceeded = it.limits_exceeded(profile)
    if not exceeded:
        return False, None
    return False, "beyond the accepted limits (%s) and no granted consent" % ", ".join(exceeded)


def _consent_request(labels):
    """The closed-as-completed consent request assigned to the task, else None."""
    req = c.label_value(labels, "consent:")
    if req and req.isdigit():
        r = c.issue(int(req))
        if r["state"] == "closed" and r.get("state_reason") == "completed":
            return r
    return None


def secrets_of_consent(labels):
    """Secret names of the frozen text of the task's granted consent; empty without one."""
    r = _consent_request(labels)
    text = it.frozen_text(r["body"]) if r else None
    m = re.match(r"secrets: (.+)$", text or "")
    return [s.strip() for s in m.group(1).split(",")] if m else []


def secret_pool():
    """Secret names the owner wired into the `Session` step of session.yml (default branch); the token of the
    Claude credential is not a session secret. Empty when the file or the step is not there."""
    import yaml
    text = c.safe_read(".", ".github/workflows/session.yml")
    try:
        steps = (((yaml.safe_load(text or "") or {}).get("jobs") or {}).get("session") or {}).get("steps") or []
    except (yaml.YAMLError, AttributeError):
        return []
    step = next((x for x in steps if isinstance(x, dict) and x.get("name") == "Session"), {})
    return sorted(k for k in (step.get("env") or {}) if k != "CLAUDE_CODE_OAUTH_TOKEN")


def block_secret_missing(n, i, name, work=None):
    """The task is blocked and the owner is asked (capability, no value) to provide the secret `name`."""
    title = "Secret `%s` for task #%s" % (name, n)
    if not it.request_open("capability", title=title):
        it.open_request("capability", title,
                        "Task #%s has your consent to use the secret `%s`, but the session cannot get it." % (n, name),
                        "The secret is not in the secret pool of the `Session` step of session.yml, or the repository secret is empty.",
                        "Nothing by itself: closing this request creates no secret. Add the name to the `Session` step and set the repository secret, then restart the task.",
                        ref="secret-%s-%s" % (n, name), iteration=c.iteration_of(c.label_names(i)),
                        readiness="Check that `%s` is a repository secret and a line of the `Session` step. Disclose no value." % name)
    close_out(n, i, "blocked", {"cause": "secret-missing:" + name}, "The session did not start: secret `%s` is missing." % name, work)


def profile_of(role):
    if role == "policymaker":
        return policymaker_session.profile()
    return c.profiles().get(role)


def model_id(alias):
    return c.params()["models"].get(alias, alias)


def session_options(i, role, p):
    """Execution parameters shared by prep and the startup recheck."""
    labels = c.label_names(i)
    ceil = c.params()["ceilings"]
    lim = json.loads(c.marker(i["body"], "limits") or "{}")
    alias = c.label_value(labels, "model:") or p.get("model") or "default"
    model = p["model"] if role == "policymaker" else model_id(alias)
    effort = p["effort"] if role == "policymaker" else c.label_value(labels, "effort:") or p.get("effort") or "medium"
    cfg = c.cfg()
    gate_min = int(cfg["gate"].get("timeout_minutes") or 0) if cfg["gate"]["kind"] == "script" else 0
    cap = JOB_MAX_MIN - 30 - gate_min
    timeout = min(int(x) for x in (lim.get("timeout_minutes") or cap, p.get("timeout_minutes") or ceil["job_minutes"], ceil["job_minutes"], cap))
    turns = min(int(x) for x in (lim.get("max_turns") or 10**6, p.get("max_turns") or ceil["max_turns"], ceil["max_turns"]))
    fingerprint = {"profile": p, "limits": cfg["limits"], "cli_version": cfg["cli_version"], "gate": cfg["gate"],
                   "policymaker_session_ceilings": cfg.get("policymaker_session_ceilings")}
    return {"kind": "script" if p.get("agent") is False else "agent", "role": role,
            "model": model, "effort": effort, "tools": ",".join(list(p.get("tools") or []) + ["Skill"] + verification_rules(i["body"])),
            "max_turns": turns, "agent_timeout": timeout, "job_timeout": timeout + gate_min + 30,
            "iteration": "%04d" % (c.iteration_of(labels) or 0),
            "profile_digest": hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()}


def resolve(n):
    i = c.issue(n)
    labels = c.label_names(i)
    roles = c.roles_of(labels)
    if i["state"] != "open" or len(roles) != 1 or not c.in_loop(labels):
        raise SystemExit("issue %s: not open, outside the loop (no `%sN`) or not exactly one role (%s)" % (n, c.ITER, roles))
    role = roles[0]
    try:
        p = profile_of(role)
    except ValueError as error:
        close_out(n, i, "blocked", {"cause": "start:policymaker-session"}, str(error))
        reconcile()
        raise SystemExit(str(error))
    why = security.profile_error(p, c.cfg()["cli_version"]) if p else "no profile for role `%s`" % role
    granted = False
    if not why and role != "policymaker":
        granted, why = consent_status(p, labels)
    if not why and (role != "policymaker" or resources.layered()) and p.get("agent") is not False and not os.path.exists(resources.role_directory(role) + "/SKILL.md"):
        why = "no skill for role `%s`" % role
    if not why and p.get("agent") is False and not p.get("command"):
        why = "script role `%s` has no command" % role
    if why:
        if role == c.cfg()["verifier_role"]:      # a verifier that cannot start sends the evaluator change back
            settle_verification(n, i, "blocked", None, why)
        close_out(n, i, "blocked", {"cause": "start:" + why.split()[0]}, "The session did not start: " + why + ".")
        reconcile()
        raise SystemExit(why)
    names = secrets_of_consent(labels) if granted else []
    pool = secret_pool()
    for name in names:
        if name not in pool:
            block_secret_missing(n, i, name)
            reconcile()
            raise SystemExit("secret-missing:" + name)
    options = session_options(i, role, p)
    branch = "task/%s" % n
    has = c.git(".", "ls-remote", "--exit-code", "--heads", "origin", branch, check=False, remote=True).returncode == 0
    start = branch if has else (c.marker(i["body"], "start-branch") or c.default_branch())
    session_inputs.capture_origin(n, role)
    c.edit_labels(n, remove=["blocked:rate-limit", "conflict"])
    c.set_output(**options, start=start, consented="true" if granted else "false",
                 secrets="|%s|" % "|".join(names) if names else "")


def session_contract(role, profile, labels):
    """Engine-owned instructions selected from accepted sources, never from the task tree."""
    paths = [".github/loop/session-protocol.md"]
    if role == "policymaker" or "loop:shaper" in labels:
        paths.append(".github/loop/task-graph.md")
    if role == "policymaker":
        paths.append(".github/loop/policymaker.md")
    parts = []
    for path in paths:
        text = c.safe_read(".", path)
        if not text:
            raise SystemExit("missing engine contract: " + path)
        parts.append(text)
    shell = "Bash" in security.tool_names(profile) or profile.get("agent") is False
    parts.append("## This session's verification and delivery")
    if shell:
        parts.append("Run the task's verification command before reporting `done`. "
                     "If it fails, report the failure; do not change the command.")
    else:
        parts.append("You have no shell and cannot run commands. `done` means the requested files "
                     "are written; the engine runs verification.")
    parts.append("Do not create a branch or commit: the engine imports the files you wrote, not your commits.")
    return "\n\n".join(parts) + "\n"


def stage(n, work, trusted):
    """Everything the agent needs, put in place by trusted code before the engine checkout is removed from the disk."""
    i = c.issue(n)
    labels = c.label_names(i)
    role = c.roles_of(labels)[0]
    try:
        p = profile_of(role)
    except ValueError as error:
        raise SystemExit("invalid session profile: " + str(error))
    error = security.profile_error(p, c.cfg()["cli_version"])
    if error:
        raise SystemExit("invalid session profile: " + error)
    if role != "policymaker":
        _, error = consent_status(p, labels)
        if error:
            raise SystemExit("session profile changed or consent is no longer valid: " + error)
    # Real workflow sessions carry KIND. Test/finalize callers may have unrelated MODEL env values.
    if "KIND" in os.environ:
        expected = session_options(i, role, p)
        for key, value in expected.items():
            env_key = key.upper()
            if env_key in os.environ and os.environ[env_key] != str(value):
                raise SystemExit("session configuration changed after prep: " + env_key)
    security.check_session_directory(work)
    session_inputs.capture_origin(n, role)
    sess = os.path.join(work, ".session")
    # Discard prior recipient views before staging a new scope.
    if os.path.isdir(sess):
        shutil.rmtree(sess)
    os.makedirs(sess, exist_ok=True)
    os.makedirs(trusted, exist_ok=True)
    os.makedirs(os.path.join(work, ".git", "info"), exist_ok=True)
    with open(os.path.join(work, ".git", "info", "exclude"), "a", encoding="utf-8") as fh:
        fh.write("\n.session/\n")
    c.export_issue(n, os.path.join(sess, "issue.md"),
                   omit_markers=(it.STATE_MARK, session_startup.MARK) +
                   ((it.ORDERS_MARK, session_reports.DECISIONS_MARK, session_inputs.ORIGIN_MARK)
                    if session_inputs.restricted(role) else ()))
    metadata = it.frontmatter(c.safe_read(".", resources.role_directory(role) + "/SKILL.md"))[0]
    fetch_inputs(i["body"], os.path.join(sess, "inputs"), role=role)
    session_exports.stage(i["body"], role, c.iteration_of(labels) or 0, sess, defaults=metadata.get("exports"))
    if role == "policymaker" or "loop:shaper" in labels:
        full = c.marker(i["body"], "exports") is None and "exports" not in metadata
        it.export_state(n, sess, role=role, full_history=full)
    else:
        session_context.history(sess, role)
    if "loop:shaper" in labels and session_inputs.restricted(role):
        with open(os.path.join(sess, "capabilities.json"), "w", encoding="utf-8") as fh:
            json.dump(session_inputs.capabilities(), fh, indent=1)
    contract = session_contract(role, p, labels)
    rules = c.safe_read(".", resources.rules(role)) or ""
    template = c.safe_read(".", resources.prompt(role)) or ""
    # Only the engine contract is a system instruction; accepted policy stays user-level.
    with open(os.path.join(trusted, "contract.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(contract)
    with open(os.path.join(trusted, "prompt.md"), "w", encoding="utf-8", newline="\n") as fh:
        # The Policymaker reads its template from the staged file named in bootstrap.
        # Include it once, rather than loading the same instructions again through that pointer.
        fh.write("# Policy work rules (subordinate to the engine contract)\n\n"
                 + rules + "\n\n" + ("" if role == "policymaker" else template))
    with open(os.path.join(trusted, "network-mode"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("online\n" if p.get("internet", False) else "offline\n")
    # The approved names, for agent.sh's empty-value check. Unapproved pool names need no file: the workflow expression
    # `contains(needs.prep.outputs.secrets, '|NAME|') && secrets.NAME || ''` already hands them over empty.
    granted = role != "policymaker" and consent_status(p, labels)[0]
    with open(os.path.join(trusted, "secrets"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(x + "\n" for x in secrets_of_consent(labels) if granted))
    with open(os.path.join(trusted, "tools"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(",".join(security.tool_names(p)))
    with open(os.path.join(trusted, "mcp.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {}}, fh)
    for f in ("agent.sh", "stream_filter.py", "remaining.py", "offline_exec.py", "sandbox.sh", "prepare.sh", "session_artifact.py"):
        shutil.copy(".github/loop/" + f, trusted)
    with open(os.path.join(trusted, "cli-version"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(str(c.cfg()["cli_version"]) + "\n")
    if resources.layered() and p.get("agent") is not False:
        staged_role = os.path.join(sess, "role")
        shutil.rmtree(staged_role, ignore_errors=True)
        shutil.copytree(resources.role_directory(role), staged_role)
        security.check_skills(staged_role)
    if role == "policymaker":
        with open(os.path.join(sess, "policymaker.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("You are the Policymaker. Follow the engine contract supplied in system instructions.\n\n" + template)
    elif p.get("agent") is False:
        with open(os.path.join(trusted, "command.sh"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(p["command"] + "\n")
    # Skill hooks and interpolation execute outside the Bash tool sandbox.
    if p.get("agent") is not False and not p.get("internet", False):
        for root in (os.path.join(work, ".claude", "skills"), os.path.join(os.path.expanduser("~"), ".claude", "skills")):
            security.check_skills(root)
    with open(os.path.join(trusted, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(security.settings(p, "/engine" if session_inputs.restricted(role) else trusted), fh)
    if session_inputs.restricted(role):
        shutil.copy(".github/loop/executor_boundary.py", trusted)
        executor_boundary.prepare(work, trusted)
    else:
        executor_boundary.record(work, trusted)
    if p.get("agent") is not False:
        session_startup.prepare(n, role, session_options(i, role, p)["model"], work, trusted,
                                shaper="loop:shaper" in labels)


# ---------------------------------------------------------------- artifacts: large outputs live in release assets

ARTIFACT_LINE = re.compile(r"^- artifact: (\S+) \| release: (\S+) \| size: (\d+) \| sha256: ([0-9a-f]{64})$", re.M)


def upload_artifacts(n, work, k):
    """Files the session left in `.session/artifacts/` go to the release `iter-NNNN`; the manifest goes into the report."""
    try:
        # Check ancestors too: a directory link can resolve only on the host
        # after the namespace ends, exposing files the worker could not read.
        security.check_session_directory(work)
    except SystemExit:
        print("Session artifacts refused: redirected output")
        return 0
    d = os.path.join(work, ".session", "artifacts")
    if not os.path.isdir(d):
        return 0
    rel = "iter-%04d" % k
    c.gh("release", "create", rel, "--title", rel, "--notes", "Artifacts of iteration %04d" % k, "--target", c.default_branch(), check=False)
    lines = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.islink(p) or not os.path.isfile(p) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            continue
        asset = "%s-%s" % (n, name)
        tmp = os.path.join(tempfile.mkdtemp(), asset)
        shutil.copy(p, tmp)
        c.gh("release", "upload", rel, tmp, "--clobber")
        with open(p, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        lines.append("- artifact: %s | release: %s/%s | size: %s | sha256: %s" % (name, rel, asset, os.path.getsize(p), sha))
    if lines:
        c.update_report(n, {"artifacts": len(lines)}, "## Artifacts\n\n" + "\n".join(lines))
    return len(lines)


def fetch_inputs(body, dest, role=None):
    """Fetch only engine-published assets of sessions visible to this recipient."""
    refs = json.loads(c.marker(body, "inputs") or "[]")
    results = []
    restricted = bool(role and session_inputs.restricted(role))
    for ref in refs:
        rel, asset = ref.get("release"), None
        issue = ref.get("issue")
        if restricted:
            # A direct release selector must identify an engine-published asset;
            # arbitrary release patterns/globs have no trustworthy origin.
            match = re.fullmatch(r"iter-[0-9]{4,}/([0-9]+)-[A-Za-z0-9._-]+", rel or "")
            if issue is None and match:
                issue = int(match.group(1))
            if type(issue) is not int or not session_inputs.visible(issue):
                results.append({"status": "denied", "detail": "artifact is not available to this recipient"})
                continue
            report = c.find_report(issue)
            candidates = [m for m in ARTIFACT_LINE.finditer(report["body"] if report else "")
                          if (m.group(1) == ref.get("name") if "issue" in ref else m.group(2) == rel)]
            if not candidates:
                results.append({"status": "unavailable", "detail": "no matching published artifact"})
                continue
            rel = candidates[0].group(2)
            if not re.fullmatch(r"iter-[0-9]{4,}/%s-[A-Za-z0-9._-]+" % issue, rel):
                results.append({"status": "denied", "detail": "artifact has no supported origin"})
                continue
        elif "issue" in ref:
            report = c.find_report(issue)
            for match in ARTIFACT_LINE.finditer(report["body"] if report else ""):
                if match.group(1) == ref.get("name"):
                    rel = match.group(2)
        if not rel or "/" not in rel:
            continue
        tag, asset = rel.split("/", 1)
        os.makedirs(dest, exist_ok=True)
        try:
            c.gh("release", "download", tag, "--pattern", asset, "--dir", dest, "--clobber")
            results.append({"status": "downloaded"})
        except (SystemExit, OSError):
            results.append({"status": "unavailable", "detail": "artifact download failed"})
    if restricted and refs:
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"schema_version": 1, "results": results}, fh, indent=1)


# ---------------------------------------------------------------- the candidate: the agent's commits in a trusted tree

class Tree:
    """A throwaway worktree of the trusted checkout, at `ref`."""
    def __init__(self, ref):
        self.ref = ref

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="cand-")
        self.path = os.path.join(self.tmp, "cand")
        c.git(".", "worktree", "add", "-q", "--detach", self.path, self.ref)
        return self.path

    def __exit__(self, *exc):
        c.git(".", "worktree", "remove", "--force", self.path, check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)


def fetch_candidate(work, n):
    """The files the session left, as one commit on its staged baseline in `refs/heads/task/N` of the trusted checkout:
    objects only, nothing of the agent's repository (its commits, config, hooks) is ever read or executed. Returns
    (short sha, protected paths the change touches). The branch goes to origin only when it touches none: GitHub refuses
    a token's push of workflow files, and such a change is refused at merge anyway."""
    trusted = os.path.join(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()), "trusted")
    baseline = os.path.join(trusted, "executor-baseline.json")
    ref = "refs/heads/task/%s" % n
    try:
        if not os.path.isdir(work) or not os.path.isfile(baseline):
            print("Required session admission metadata unavailable")
            return None, []
        with open(baseline, encoding="utf-8") as fh:
            meta = json.load(fh)
        role = c.roles_of(c.label_names(c.issue(n)))[0]
        skip = lambda rel, tracked: False
        if not session_inputs.restricted(role):
            # A new path outside a policy role's write paths is environment debris (CLI stubs, runner home files, IDE
            # settings, a tool's virtualenv links): neither inspected nor admitted. Everything else reaches the checks.
            write = profile_of(role).get("write") or []
            skip = lambda rel, tracked: not tracked and not c.path_match(rel, write)
        executor_boundary.import_candidate(work, meta, ".", ref, skip)
    except (ValueError, OSError, subprocess.SubprocessError):
        print("Session output could not be admitted safely")
        return None, []
    d = c.default_branch()
    c.git(".", "fetch", "-q", "origin", d, check=False, remote=True)
    touched = [f for f in c.git(".", "diff", "--name-only", "origin/%s...task/%s" % (d, n), check=False).stdout.split()
               if resources.is_protected(f)]
    if not touched:
        c.git(".", "push", "-q", "-f", "origin", "task/%s:refs/heads/task/%s" % (n, n), check=False, remote=True)
    return c.git(".", "rev-parse", "--short", "task/%s" % n).stdout.strip(), touched


def may_write(profile, path):
    return resources.may_write(profile, path)


def verification_commands(body):
    """Commands of `## Verification`: code blocks or backticked lines."""
    text = c.section(body, "Verification")
    return [x.strip() for x in re.findall(r"```[a-z]*\n(.*?)```", text, re.S)] or re.findall(r"`([^`\n]+)`", text)


def verification_rules(body):
    """Permission rules letting a session run its own verification exactly as the epilogue will (one-line commands)."""
    return ["Bash(%s)" % re.sub(r"([\\()*])", r"\\\1", cmd) for cmd in verification_commands(body) if "\n" not in cmd]


def run_verification(cand, body):
    """`## Verification` of the task: commands from code blocks or backticked lines, run in the tokenless job."""
    for cmd in verification_commands(body):
        r = c.run_command(cmd, cand)
        print("$ %s\n%s%s" % (cmd, r.stdout, r.stderr))
        if r.returncode:
            return False, cmd + "\n" + (r.stdout + r.stderr)[-1500:]
    return True, ""


def fix_round(n, f, upd, why):
    """A failed `## Verification` or `check` is an error the agent can fix: run the same issue again, its report carrying
    the error, up to MAX_FIXES times. False when the rounds are spent."""
    kk = int(f.get("fixes", 0)) + 1
    if kk > MAX_FIXES:
        return False
    upd["fixes"] = kk
    c.update_report(n, dict(upd, status="partial"), "%s\nCorrection round %s of %s." % (why, kk, MAX_FIXES))
    c.launch(n)
    return True


def merge_branch(n, role, i, override=None, verified=False, checked_commit=None, checked_evaluator_rev=None, verifier=None, boundary=False):
    """Rebase `task/N` on the default branch, run the checks, get the change gate's verdict, push.
    Returns {"result", "detail", "verdict", "gate_s", "changed"}. Git orders competitors: a rejected push is retried."""
    cfg, d = c.cfg(), c.default_branch()
    prof = profile_of(role) or {}
    br = "task/%s" % n
    res = {"result": "conflict", "detail": "", "verdict": None, "gate_s": 0, "changed": []}
    k = it.policy_pass_of(n) if role == "policymaker" else 1
    for _ in range(PUSH_TRIES):
        c.sync_main()
        c.git(".", "fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br), remote=True)
        existing = it.applied("changes", it.change_id(n, k))
        if existing:
            return dict(res, result="merged", change=existing)
        branch_sha = c.git(".", "rev-parse", "origin/%s" % br).stdout.strip()
        if checked_commit and branch_sha != checked_commit:
            return dict(res, result="verification-stale", detail="The task branch changed after the verified snapshot.")
        baseline = c.head()
        session_base = c.git(".", "merge-base", baseline, branch_sha).stdout.strip()
        changed = c.git(".", "diff", "--name-only", "-z", "origin/%s...origin/%s" % (d, br)).stdout.split("\0")[:-1]
        res["changed"] = changed
        if not changed:
            return dict(res, result="nothing")
        bad = [f for f in changed if resources.is_protected(f)]
        if bad:
            return dict(res, result="protected", detail=", ".join(bad))
        out = [f for f in changed if not may_write(prof, f)]
        if out:
            return dict(res, result="scope", detail=", ".join(out))
        work_paths = [f for f in changed if not resources.is_direct(f)]
        if any(resources.is_evaluator(f) for f in changed) and not verified:
            return dict(res, result="awaiting-verifier")
        with Tree("origin/%s" % br) as cand:
            if c.git(cand, "rebase", "origin/%s" % d, check=False).returncode:
                c.git(cand, "rebase", "--abort", check=False)
                return dict(res, result="conflict")
            if resources.layered():
                try:
                    resources.role_profiles(cand)
                    if boundary:
                        resources.validate_activation(cand)
                except ValueError as error:
                    return dict(res, result="invalid-resources", detail=str(error))
            if checked_evaluator_rev and resources.evaluator_revision("HEAD", cand) != checked_evaluator_rev:
                return dict(res, result="verification-stale", detail="The rebased evaluator differs from the verified snapshot.")
            # The accepted range contains this session's rebased commits only.
            changed = c.git(cand, "diff", "--name-only", "-z", baseline, "HEAD").stdout.split("\0")[:-1]
            res["changed"] = changed
            big = [f for f in changed if os.path.isfile(os.path.join(cand, f)) and os.path.getsize(os.path.join(cand, f)) > cfg["large_file_bytes"]]
            if big:
                return dict(res, result="large", detail=", ".join(big))
            if work_paths:
                if cfg["check"]:
                    t = c.run_command(cfg["check"], cand)
                    if t.returncode:
                        return dict(res, result="check", detail=(t.stdout + t.stderr)[-1500:])
                if override:
                    res["verdict"] = override
                else:
                    t0 = time.time()
                    v = it.run_gate(cand, os.path.abspath("."), {"candidate_sha": c.head(cand), "baseline_sha": c.head("."),
                                                                  "changed": changed, "task": n,
                                                                  "iteration": c.iteration_of(c.label_names(i)) or 0})
                    res["gate_s"] = int(time.time() - t0)
                    res["verdict"] = v
                if res["verdict"]["verdict"] == "pending-owner":
                    return dict(res, result="awaiting-owner")
                if res["verdict"]["verdict"] == "rejected":
                    return dict(res, result="gate-rejected", detail=res["verdict"].get("detail", ""))
                if res["verdict"]["verdict"] != "accepted":
                    return dict(res, result="gate-inconclusive", detail=res["verdict"].get("detail", ""))
            if resources.layered() and role == "policymaker" and not boundary:
                return dict(res, result="awaiting-iteration")
            change = it.record_change(cand, n, role, session_base, baseline, c.head(cand), changed, verifier, k)
            if c.git(cand, "push", "origin", "HEAD:refs/heads/%s" % d, check=False, remote=True).returncode == 0:
                if change:
                    it.record_applied("changes", change)
                return dict(res, result="merged", change=change)
    return dict(res, result="conflict")


def merge_notes(n, role):
    """An unfinished session does not merge its work, but its journal must not stay on `task/N`."""
    cfg, d = c.cfg(), c.default_branch()
    prof = profile_of(role) or {}
    br = "task/%s" % n
    for _ in range(PUSH_TRIES):
        c.sync_main()
        if c.git(".", "fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br), check=False, remote=True).returncode:
            return False
        files = [f for f in c.git(".", "diff", "--name-only", "--diff-filter=AM", "origin/%s...origin/%s" % (d, br)).stdout.split()
                 if c.path_match(f, cfg["notes_paths"]) and may_write(prof, f)]
        if not files:
            return False
        with Tree("origin/%s" % d) as cand:
            c.git(cand, "checkout", "origin/%s" % br, "--", *files)
            c.git(cand, "commit", "-q", "-m", "Notes from an unfinished session: %s" % ", ".join(files), check=False)
            if c.git(cand, "push", "origin", "HEAD:refs/heads/%s" % d, check=False, remote=True).returncode == 0:
                return True
    return False


def merge_outcome(r):
    """(status, report fields, prose) of a merge attempt. `conflict` and the waiting results are handled by the caller."""
    v = r.get("verdict") or {}
    upd = {}
    if v and v.get("verdict") != "pending-owner":
        upd.update(verdict=v.get("verdict"), improved=str(bool(v.get("improved"))).lower(), detail=(v.get("detail") or "")[:300] or None)
    res, detail = r["result"], r.get("detail", "")
    if res == "merged":
        return "done", dict(upd, merged="true"), ""
    if res == "nothing":
        return "done", dict(upd, merged="false"), "No changes to merge."
    if res == "protected":
        return "blocked", dict(upd, merged="false"), "The change touches protected paths: %s. Merge refused." % detail
    if res == "scope":
        return "blocked", dict(upd, merged="false"), "The change writes outside the role's `write` paths: %s. Merge refused." % detail
    if res == "invalid-resources":
        return "blocked", dict(upd, merged="false"), "Invalid managed definitions: %s. Merge refused." % detail
    if res == "large":
        return "blocked", dict(upd, merged="false"), "Files over the size limit: %s. Put large outputs in `.session/artifacts/`." % detail
    if res == "check":
        return "partial", dict(upd, merged="false"), "The check command failed after rebase:\n```\n%s\n```" % detail
    if res == "gate-rejected":
        return "rejected", dict(upd, merged="false"), "The change gate rejected the change. %s" % detail
    if res == "gate-inconclusive":
        return "partial", dict(upd, merged="false"), "The change gate gave no verdict. %s" % detail
    if res == "verification-stale":
        return "blocked", dict(upd, merged="false"), detail
    return "partial", dict(upd, merged="false"), "Rebase on the default branch conflicts."


# ---------------------------------------------------------------- epilogue

def machine_result(path):
    """The `result` event of a Claude session as a dict ({} when there is none)."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    try:
        d = json.loads(raw)
        d = d[-1] if isinstance(d, list) and d else d
    except ValueError:
        d = {}
    return raw, d if isinstance(d, dict) else {}


def machine_cause(exec_path, exit_code):
    """Cause verbatim from the execution file, no interpretation. Never by `subtype`."""
    got = machine_result(exec_path)
    if got is None:
        return "no-execution-file exit=%s" % exit_code, False, None
    raw, d = got
    parts = []
    if d.get("api_error_status"):
        parts.append("api_error_status=%s" % d["api_error_status"])
    if d.get("terminal_reason"):
        parts.append("terminal_reason=%s" % d["terminal_reason"])
    if d.get("is_error") and not parts:
        parts.append("is_error=true")
    if exit_code not in ("0", "", None):
        parts.append("exit=%s" % exit_code)
    limited = bool(re.search(r"hit your .{0,40}limit|rate_limit", raw, re.I))
    m = re.search(r'"resets?_?[aA]t"\s*:\s*(\d{10,13})', raw)
    reset = None
    if m:
        ts = int(m.group(1))
        reset = datetime.fromtimestamp(ts / 1000 if ts > 10**11 else ts, timezone.utc)
    return " ".join(parts), limited, reset or text_reset(raw)


def usage_fields(exec_path):
    """Tokens and turns of a session from its `result` event: per model when `modelUsage` is there, else `usage`."""
    got = machine_result(exec_path)
    d = got[1] if got else {}
    u, mu = d.get("usage") or {}, d.get("modelUsage") or {}
    per = {m: {"in": v.get("inputTokens", 0), "out": v.get("outputTokens", 0), "cache_read": v.get("cacheReadInputTokens", 0),
               "cache_write": v.get("cacheCreationInputTokens", 0)} for m, v in mu.items()}
    if per:
        tot = {k: sum(v[k] for v in per.values()) for k in ("in", "out", "cache_read", "cache_write")}
    else:
        tot = {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
               "cache_write": u.get("cache_creation_input_tokens", 0)}
    return {"turns": d.get("num_turns"), "peak_context": d.get("peak_context") or None, "tokens_in": tot["in"], "tokens_out": tot["out"], "cache_read": tot["cache_read"],
            "cache_write": tot["cache_write"], "models": json.dumps(per, separators=(",", ":"), sort_keys=True) if per else None}


RESET_TEXT = re.compile(r"resets\s+(?:([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(?:at\s+)?)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*(?:\(([^)]+)\))?", re.I)


def text_reset(raw, ref=None):
    """'resets 7:20am (UTC)' / 'resets Oct 2, 5am (Europe/Warsaw)': the CLI's wording when there is no `resetsAt`."""
    m = RESET_TEXT.search(raw)
    if not m:
        return None
    mon, day, h, mi, ap, tz = m.groups()
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz) if tz else timezone.utc
    except Exception:
        zone = timezone.utc
    ref = (ref or c.now()).astimezone(zone)
    h = int(h) % 12 + (12 if ap.lower() == "pm" else 0)
    t = ref.replace(hour=h, minute=int(mi or 0), second=0, microsecond=0)
    if mon:
        t = t.replace(month=datetime.strptime(mon[:3].title(), "%b").month, day=int(day))
        if t < ref - timedelta(days=1):
            t = t.replace(year=t.year + 1)
    elif t <= ref:
        t += timedelta(days=1)
    return t.astimezone(timezone.utc)


def read_int(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def finalize(n, work):
    tmp = os.environ.get("RUNNER_TEMP", tempfile.gettempdir())
    i = c.issue(n)
    labels = c.label_names(i)
    role = c.roles_of(labels)[0]
    k = c.iteration_of(labels) or 0
    p = profile_of(role) or {}
    script = p.get("agent") is False
    text = c.safe_read(work, ".session/report.md")
    if text:
        c.publish(n, text)
    f = c.report_fields(n)
    status = f.get("status", "")
    exit_code = ""
    if os.path.exists(os.path.join(tmp, "agent-exit")):
        with open(os.path.join(tmp, "agent-exit")) as fh:
            exit_code = fh.read().strip()
    missing = (c.safe_read(tmp, "secret-missing") or "").strip()
    if missing and re.fullmatch(r"[A-Z][A-Z0-9_]*", missing) and missing in secrets_of_consent(labels):
        block_secret_missing(n, i, missing, work)
        return
    exec_path = os.path.join(tmp, "claude-execution-output.json")
    limited, reset, prose = False, None, ""
    upd = {"branch": "task/%s" % n, "iteration": k, "runner": os.environ.get("RUNNER_OS") or None}
    if script:
        upd["cause"] = "exit=%s" % exit_code if exit_code not in ("0", "") else None
        status = "done" if exit_code == "0" else "crashed"
        prose = "Script %s.\n" % ("finished" if status == "done" else "failed (exit %s)" % exit_code) + (c.safe_read(tmp, "script-output.txt") or "")[-1500:]
    else:
        cause, limited, reset = machine_cause(exec_path, exit_code)
        if limited and status not in c.AGENT_STATUSES:
            # the status files are the session's data: only the engine's own probe parks the loop, and it sets the clock
            limited, reset = c.probe_limit()
        upd["cause"] = cause or None
        upd.update(usage_fields(exec_path))
        upd.update(model=os.environ.get("MODEL") or None, effort=os.environ.get("EFFORT") or None)
        if status not in c.AGENT_STATUSES:
            status = "paused" if limited else "crashed"
            prose = "Subscription limit; resume scheduled." if limited else "The agent left no terminal status (%s)." % (cause or "no cause")
    a0, a1 = read_int(os.path.join(tmp, "agent-start")), read_int(os.path.join(tmp, "agent-end"))
    if a0 and a1:
        upd["agent_s"] = a1 - a0
    t0 = read_int(os.path.join(tmp, "session-start"))
    if t0:
        upd["total_s"] = int(time.time()) - t0
        if f.get("paused_at"):
            upd["parked_s"] = int(f.get("parked_s", 0) or 0) + max(0, t0 - int(c.parse_time(f["paused_at"]).timestamp()))
            upd["paused_at"] = None
    sha, touched = fetch_candidate(work, n)          # nothing on the branch dies with the runner
    if sha is None and status in ("done", "partial"):
        status, prose = "crashed", "The epilogue could not read the agent's repository, so nothing was kept."
    if touched and status in ("done", "partial"):
        status, prose, sha = "blocked", "The change touches protected paths: %s. The branch was not published and nothing was merged." % ", ".join(touched), None
        upd["merged"] = "false"
    if sha and status in c.AGENT_STATUSES | {"paused"}:
        upd["commit"] = f.get("commit", sha)
    if status != "paused":
        upload_artifacts(n, work, k)
    if status == "done" and sha and not script:
        with Tree("task/%s" % n) as cand:
            ok, out = run_verification(cand, i["body"])
        if not ok:
            status, prose = "partial", "The epilogue ran `## Verification` and got an error:\n```\n%s\n```" % out
            upd["verification"] = "fail"
            if fix_round(n, f, upd, prose):
                return
    pending = None
    if status == "done" and sha and role == "policymaker":
        pending = "pending-iteration"            # its changes and orders take effect when the iteration it opened continues
        prose = hold_policymaker_output(n, work)
        c.edit_labels(n, add=["awaiting:iteration"])
        c.update_report(n, dict(upd, status="done", merged=pending), prose)
    elif status == "done" and sha:
        r = merge_branch(n, role, i)
        upd["gate_s"] = r["gate_s"] or None
        if r["result"] == "conflict":
            kk = int(f.get("conflicts", 0)) + 1
            upd["conflicts"] = kk
            if kk < MAX_CONFLICTS:
                c.update_report(n, dict(upd, status="partial"), "Rebase on the default branch: conflict (attempt %s)." % kk)
                c.edit_labels(n, add=["conflict"])
                c.launch(n)
                return
            status, prose = "crashed", "Rebase conflict %s times in a row." % kk
        elif r["result"] == "awaiting-owner":
            pending = "pending-owner"
            req = it.open_request("merge-acceptance", "Accept the change of task #%s: %s" % (n, i["title"]),
                                  "Task #%s finished; its branch `task/%s` (%s) is ready to enter the main line.\nFiles: %s" % (
                                      n, n, upd.get("commit", ""), ", ".join(r["changed"][:20])),
                                  "Every change needs a verdict before it enters the main line, and the change gate is set to you.",
                                  "Completed merges the branch; not planned discards it (the branch stays). Say why in a comment.",
                                  ref="task-%s" % n, iteration=k)
            c.edit_labels(n, add=["awaiting:owner"])
            c.update_report(n, dict(upd, status="done", merged=pending), "Waiting for the owner: request #%s." % req)
        elif r["result"] == "awaiting-verifier":
            pending = "pending-verifier"
            v = request_verifier(n, i, k, r["changed"])
            c.edit_labels(n, add=["awaiting:verifier"])
            c.update_report(n, dict(upd, status="done", merged=pending), "The evaluator changed; waiting for the verifier (#%s)." % v)
        else:
            status, more, prose = merge_outcome(r)
            upd.update(more)
            if r["result"] == "check" and fix_round(n, f, upd, prose):
                return
    elif status not in ("done", "paused") and sha and role != "policymaker":
        merge_notes(n, role)
    if status == "paused":
        ended = park(n, i, f, upd, prose, reset, sha)
        if ended is None:
            return
        status, prose = "crashed", ended
    if not pending:
        tag_unmerged(n, k, upd)
    proposals(work, k, source=n)
    if pending:
        session_reports.capture(n, work)
    after_session(n, i, labels, role, k, work, status, upd, prose, pending)


def epilogue(n, artifact, baseline, digest):
    """The epilogue job, on a fresh runner: takes the session job's artifact in as data (`session_artifact`), puts the
    executor baseline from the channel the session cannot touch (`baseline/`, pinned by `digest`) where admission reads
    it, and finalizes. A tree that cannot be taken in is as good as no tree."""
    tmp = os.environ.get("RUNNER_TEMP", tempfile.gettempdir())
    with open(os.path.join(baseline, "executor-baseline.json"), "rb") as fh:
        raw = fh.read()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SystemExit("the executor baseline does not match the one staged")
    work = os.path.join(tmp, "work")
    session_artifact.receive_status(artifact, tmp)
    try:
        session_artifact.receive_tree(artifact, work)
    except (ValueError, OSError, tarfile.TarError) as error:
        print("Session output refused: %s" % error)
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "trusted"), exist_ok=True)
    meta = json.loads(raw)
    meta["work"] = os.path.abspath(work)
    with open(os.path.join(tmp, "trusted", "executor-baseline.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    session_artifact.receive_commits(artifact, ".", n)
    finalize(n, work)


def hold_policymaker_output(n, work):
    """Keep a finished Policymaker session's orders on its issue until the iteration continues; a run without orders keeps
    an empty entry, so the orders of an earlier run are never read again. Returns the report prose."""
    session_reports.freeze_decisions(n, work, (it.read_state(n) or {}).get("suggestions", []))
    text = c.safe_read(work, ".session/orders.json")
    try:
        doc = json.loads(text) if text else {}
    except ValueError:
        doc = {}
        text = None
        prose = "`.session/orders.json` is not JSON: no orders. "
    else:
        prose = ""
    tasks = doc.get("tasks") if isinstance(doc, dict) else None
    wake = isinstance(doc, dict) and doc.get("wake") is True
    it.store_state(n, {"pass": it.policy_pass_of(n), "tasks": tasks if isinstance(tasks, list) else [], "wake": wake}, it.ORDERS_MARK)
    return prose + "Waiting for the iteration to continue: then `policy/` merges%s." % (" and the orders start" if text else "")


def apply_policy(keeps_open=lambda number: False):
    """Merge the finished Policymaker sessions, oldest first, and return the numbers of those that merged. An unmerged one
    counts as a failed Policymaker session. A merged one is closed, unless `keeps_open(number)`: it is relaunched after
    the tasks it ordered with `wake`, so it waits (`awaiting:orders`)."""
    merged = []
    for x in sorted(c.list_issues(labels=("role:policymaker", "awaiting:iteration")), key=lambda x: x["number"]):
        fields = c.report_fields(x["number"])
        verified = resources.layered() and fields.get("verifier_passed") == "true"
        verification = {}
        if verified:
            verifier = int(fields["verifier_issue"])
            body = c.issue(verifier)["body"]
            verification = dict(verified=True, verifier=verifier,
                                checked_commit=c.marker(body, "checked-commit"),
                                checked_evaluator_rev=json.loads(c.marker(body, "checked-evaluator-rev") or "null"))
            if not verification["checked_commit"] or not verification["checked_evaluator_rev"]:
                close_out(x["number"], x, "blocked", {"merged": "false"}, "Verifier has no pinned snapshot; request a fresh verification.")
                continue
        if fields.get("owner_accepted") == "true" and fields.get("owner_accepted_commit"):
            verification.setdefault("checked_commit", fields["owner_accepted_commit"])
            verification["override"] = {"verdict": "accepted", "improved": True, "detail": "accepted by the owner"}
        r = merge_branch(x["number"], "policymaker", x, boundary=True, **verification)
        if r["result"] == "awaiting-owner":
            req = it.open_request("merge-acceptance", "Accept the change of task #%s: %s" % (x["number"], x["title"]),
                                  "The complete Policymaker candidate needs your change gate verdict.",
                                  "The change gate is set to you.", "Completed accepts the candidate for the next iteration boundary.",
                                  ref="task-%s" % x["number"], iteration=c.iteration_of(c.label_names(x)))
            c.edit_labels(x["number"], add=["awaiting:owner"], remove=["awaiting:iteration"])
            c.update_report(x["number"], {"status": "done", "merged": "pending-owner"}, "Waiting for the owner: request #%s." % req)
            continue
        if r["result"] == "awaiting-verifier":
            v = request_verifier(x["number"], x, c.iteration_of(c.label_names(x)) or 0, r["changed"])
            c.edit_labels(x["number"], add=["awaiting:verifier"], remove=["awaiting:iteration"])
            c.update_report(x["number"], {"status": "done", "merged": "pending-verifier"},
                            "The complete managed set waits for the evaluator verifier (#%s)." % v)
            continue
        st, upd, pr = merge_outcome(r)
        if st == "done":
            merged.append(x["number"])
            session_reports.apply_decisions(x["number"])
            if keeps_open(x["number"]):
                c.update_report(x["number"], dict(upd, status="done"), pr)
                c.edit_labels(x["number"], add=["awaiting:orders"], remove=["awaiting:iteration"])
                continue
        else:
            tag_unmerged(x["number"], c.iteration_of(c.label_names(x)) or 0, {"commit": "x"})
        close_out(x["number"], x, st, upd, pr)
    return merged


def proposals(work, k, source=None):
    text = c.safe_read(work, ".session/requests.json")
    if text:
        it.process_proposals(text, k, source=source)


def tag_unmerged(n, k, upd):
    """A branch that did not enter the main line stays reachable: tag `iter/NNNN/<n>`."""
    if upd.get("merged") == "true" or not upd.get("commit"):
        return
    ahead = c.git(".", "rev-list", "--count", "origin/%s..refs/heads/task/%s" % (c.default_branch(), n), check=False).stdout.strip()
    if ahead not in ("", "0"):
        c.git(".", "push", "-q", "-f", "origin", "refs/heads/task/%s:refs/tags/iter/%04d/%s" % (n, k, n), check=False, remote=True)


def park(n, i, f, upd, prose, reset, head):
    """Park for the provider; the clock is the limit reset. Returns None when parked, else why the session ends.
    Waiting costs nothing, so it is bounded by the age limit and by the progress breaker: K resumes in a row that used
    the agent and moved nothing (no turns, the branch head unmoved) open an outage."""
    kk = int(f.get("attempts", 0)) + 1
    if (c.now() - c.parse_time(i["created_at"])).days >= MAX_AGE_DAYS:
        return "The issue is too old."
    spent = bool(upd.get("agent_s") or (upd.get("tokens_in") or 0) + (upd.get("tokens_out") or 0))
    moved = bool(upd.get("turns")) or bool(f.get("head") and head != f.get("head"))
    idle = 0 if moved else int(f.get("idle_resumes", 0)) + (spent and bool(f.get("paused_at")))   # only a resume counts
    limit = c.cfg()["idle_resumes"]
    if limit and idle >= limit:
        c.open_outage("Session #%s resumed %s times in a row without progress" % (n, idle),
                      "Each resume used the agent and produced no turn and no commit.",
                      "Look at the session's stream and report, fix the cause, then close this issue.")
        return "%s resumes in a row used the agent without progress." % idle
    when = reset or c.now() + timedelta(hours=BACKOFF_H[min(kk - 1, len(BACKOFF_H) - 1)])
    c.update_report(n, dict(upd, status="paused", attempts=kk, idle_resumes=idle, head=head, resume_after=c.iso(when), paused_at=c.iso()), prose)
    c.edit_labels(n, add=["blocked:rate-limit"])
    c.workflow_run("resume.yml", issue=n)      # the clock lives in a workflow: GitHub's cron lags by hours
    return None


def after_session(n, i, labels, role, k, work, status, upd, prose, merged_pending):
    """What a finished session means for the iteration; then close it unless it waits for the owner, a verifier or the
    next iteration."""
    if "loop:shaper" in labels and status == "done" and not merged_pending:
        doc, err = None, None
        try:
            doc = json.loads(c.safe_read(work, ".session/tasks.json") or "")
        except ValueError:
            err = "`.session/tasks.json` is missing or not JSON"
        got = it.instantiate(k, doc) if doc is not None else err
        if isinstance(got, str):
            status, prose = "partial", got
    if role == c.cfg()["verifier_role"] and merged_pending is None:
        status, prose = settle_verification(n, i, status, f_result(n), prose)
    if merged_pending:
        return reconcile()
    close_out(n, i, status, upd, prose, work=work)
    reconcile()


def f_result(n):
    return c.report_fields(n).get("result")


# ---------------------------------------------------------------- the evaluator verifier

def request_verifier(n, i, k, changed):
    role = c.cfg()["verifier_role"]
    c.ensure_label("role:" + role, "1D76DB")
    checked = c.git(".", "rev-parse", "origin/task/%s" % n).stdout.strip()
    rev = resources.evaluator_revision(checked)
    body = ("Verify the evaluator change of task #%s (branch `task/%s`).\n\nChanged evaluator files: %s\n\n"
            "Decide whether the changed evaluator still measures progress toward the goal; the accepted state in force is the default branch.\n\n"
            "<!-- verifies: %s -->\n<!-- start-branch: %s -->\n<!-- checked-commit: %s -->\n"
            "<!-- checked-evaluator-rev: %s -->\n") % (n, n, ", ".join(changed[:20]), n, checked, checked, json.dumps(rev, sort_keys=True))
    v = c.create_issue("Verify evaluator change of task #%s" % n, body, labels=("role:" + role, c.ITER + str(k), "generation:0"))
    c.update_report(n, {"verifier_issue": v, "verified_commit": checked})
    c.edit_labels(n, add=["awaiting:verifier"])
    c.launch(v)
    return v


def settle_verification(n, i, status, result, prose):
    """The verifier ended: `result: pass` merges the evaluator change it verified, anything else sends it back."""
    target = c.marker(i["body"], "verifies")
    if not target or not target.isdigit():
        return status, prose
    t = int(target)
    ti = c.issue(t)
    if ti["state"] != "open" or "awaiting:verifier" not in c.label_names(ti):
        return status, prose                  # repeated epilogue cannot reapply a closed change
    target_fields = c.report_fields(t)
    if target_fields.get("verifier_issue") != str(n):
        return status, prose                  # only the engine-requested verifier may settle this change
    trole = c.roles_of(c.label_names(ti))[0]
    if status == "done" and result == "pass":
        checked = c.marker(i["body"], "checked-commit")
        rev = json.loads(c.marker(i["body"], "checked-evaluator-rev") or "null")
        if not checked or not rev:
            r = {"result": "verification-stale", "detail": "Verifier has no pinned snapshot; request a fresh verification."}
        elif resources.layered() and trole == "policymaker":
            # Verification is evidence, not authority to activate during an iteration.
            # The boundary rechecks the entire pinned candidate and rebased measurement.
            r = {"result": "pending-iteration"}
            it.record_verification(n, i, status, result, r)
            c.edit_labels(t, add=["awaiting:iteration"], remove=["awaiting:verifier"])
            c.update_report(t, {"merged": "pending-iteration", "verifier_passed": "true"},
                            "Verified; the complete managed set waits for the next iteration boundary.")
            return status, prose
        else:
            r = merge_branch(t, trole, ti, verified=True, checked_commit=checked, checked_evaluator_rev=rev, verifier=n)
        it.record_verification(n, i, status, result, r)
        if r["result"] == "awaiting-owner":
            req = it.open_request("merge-acceptance", "Accept the change of task #%s: %s" % (t, ti["title"]), "The evaluator change of task #%s was verified; its work paths still need your verdict." % t,
                                  "The change gate is set to you.", "Completed merges the branch.", ref="task-%s" % t, iteration=c.iteration_of(c.label_names(ti)))
            c.edit_labels(t, add=["awaiting:owner"], remove=["awaiting:verifier"])
            c.update_report(t, {"merged": "pending-owner", "verifier_passed": "true"}, "Verified; waiting for the owner: request #%s." % req)
            return status, prose
        st, upd, pr = merge_outcome(r)
    else:
        it.record_verification(n, i, status, result, {"result": "not-applied"})
        st, upd, pr = "rejected", {"merged": "false", "verdict": "rejected"}, "The evaluator verifier did not pass the change (result: %s, status: %s)." % (result, status)
    c.edit_labels(t, remove=["awaiting:verifier"])
    close_out(t, ti, st, upd, pr)
    return status, prose


# ---------------------------------------------------------------- closing an issue

def spawn_successor(n, i, report_body):
    m = re.search(r"^## Successor\s*\ntitle:\s*(.+)\nbody:\s*(.*?)(?=^## |\Z)", report_body, re.S | re.M)
    if not m:
        return None
    labels = c.label_names(i)
    gen = max([int(l.split(":")[1]) for l in labels if l.startswith("generation:")] or [0])
    if gen >= MAX_GEN:
        return None
    keep = [l for l in labels if l.startswith(("role:", "model:", "effort:", c.ITER, "loop:shaper"))] + ["generation:%s" % (gen + 1)]
    for l in keep:
        if l.startswith("generation:"):
            c.ensure_label(l, "BFD4F2")
    s = c.create_issue(m.group(1).strip(), m.group(2).strip() + "\n\n<!-- start-branch: task/%s -->\n" % n, labels=keep)
    for dep in c.blocking(n):       # the successor inherits what waited for its parent
        c.add_blocker(dep["number"], s)
    return s


def close_out(n, i, status, upd, prose, work=None):
    body = c.update_report(n, dict(upd, status=status), prose)
    session_reports.capture(n, work)
    if status == "done" and "role:policymaker" in c.label_names(i):
        try:
            session_startup.acknowledge(n)
        except (Exception, SystemExit):
            c.say("session #%s: startup notices remain pending" % n)
    c.edit_labels(n, remove=["blocked:rate-limit", "conflict", "awaiting:owner", "awaiting:verifier", "awaiting:iteration", "awaiting:orders"])
    succ = spawn_successor(n, i, body) if status == "partial" else None
    c.close_issue(n, "completed" if status == "done" else "not planned")
    if status == "done":
        unblock(n)
    elif not succ:
        halt_dependents(n, status)
    if succ:
        c.launch(succ)


def unblock(closed):
    for d in c.blocking(closed):
        if d["state"] == "open":
            c.launch(d["number"])


def halt_dependents(closed, status):
    """The producer did not deliver: a consumer does not start on nothing, it closes as `blocked` (cascading)."""
    for d in c.blocking(closed):
        if d["state"] != "open":
            continue
        if "loop:shaper" in c.label_names(d):
            c.launch(d["number"])
        else:
            close_out(d["number"], d, "blocked", {"cause": "producer-#%s-%s" % (closed, status)},
                      "Producer #%s closed as `%s`: it did not deliver, so this task did not start." % (closed, status))


# ---------------------------------------------------------------- owner requests

def on_request_closed(n):
    x = c.issue(n)
    names = c.label_names(x)
    if it.REQ not in names or it.HANDLED in names or x["state"] != "closed":
        return
    kind = c.marker(x["body"], "request-kind")
    # The trusted snapshot controls the type even if someone edits the issue's markers.
    snapshot = it.requests.read(n)
    if snapshot or kind in it.requests.KINDS:
        status = it.requests.settle(n, x)
        if status == "waiting":
            return
        if status == "approved":
            c.edit_labels(n, add=[it.requests.APPROVED])
        for d in c.blocking(n):
            if d["state"] == "open":
                if status == "approved":
                    c.launch(d["number"])
                else:
                    close_out(d["number"], d, "blocked", {"cause": "owner-request-" + status},
                              "Owner request #%s ended as %s." % (n, status))
        c.edit_labels(n, add=[it.HANDLED])
        it.advance()
        reconcile()
        return
    yes = x.get("state_reason") == "completed"
    ref = c.marker(x["body"], "request-ref") or ""
    if kind in ("goal-gate", "goal-change") and yes:
        text = it.frozen_text(x["body"])
        if text is None:
            c.post_comment(n, "Not applied: the proposed text changed after the request was opened. The loop will open a new request.")
        else:
            c.commit_main({"goal/gate.yml" if kind == "goal-gate" else "goal/goal.md": text.rstrip("\n") + "\n"}, "owner accepted: %s (#%s)" % (kind, n))
    elif kind == "result-confirmation" and yes:
        c.commit_main({"GOAL_REACHED": "confirmed by the owner (#%s)\n" % n}, "goal reached: confirmed by the owner (#%s)" % n)
    elif kind == "merge-acceptance" and ref.startswith("task-"):
        settle_owner_merge(int(ref[5:]), yes)
    elif kind == "consent":
        for d in c.blocking(n):
            if d["state"] == "open":
                if yes:
                    c.launch(d["number"])
                else:
                    close_out(d["number"], d, "blocked", {"cause": "consent-declined"}, "The owner declined the consent request #%s." % n)
    c.edit_labels(n, add=[it.HANDLED])
    it.advance()
    reconcile()


def settle_owner_merge(t, yes):
    ti = c.issue(t)
    if ti["state"] != "open" or "awaiting:owner" not in c.label_names(ti):
        return
    role = c.roles_of(c.label_names(ti))[0]
    fields = c.report_fields(t)
    verifier = int(fields["verifier_issue"]) if fields.get("verifier_passed") == "true" and fields.get("verifier_issue", "").isdigit() else None
    vi = c.issue(verifier) if verifier else None
    if yes and resources.layered() and role == "policymaker":
        c.edit_labels(t, add=["awaiting:iteration"], remove=["awaiting:owner"])
        c.git(".", "fetch", "-q", "origin", "+refs/heads/task/%s:refs/remotes/origin/task/%s" % (t, t), remote=True)
        accepted = c.git(".", "rev-parse", "origin/task/%s" % t).stdout.strip()
        c.update_report(t, {"merged": "pending-iteration", "owner_accepted": "true", "owner_accepted_commit": accepted},
                        "Accepted by the owner; the complete managed set waits for the next iteration boundary.")
        if verifier:
            vf = c.report_fields(verifier)
            it.record_verification(verifier, vi, vf.get("status"), vf.get("result"), {"result": "pending-iteration"}, owner=True)
        return
    if yes:
        r = merge_branch(t, role, ti, override={"verdict": "accepted", "improved": True, "detail": "accepted by the owner"},
                         verified=bool(verifier), verifier=verifier,
                         checked_commit=c.marker(vi["body"], "checked-commit") if vi else None,
                         checked_evaluator_rev=json.loads(c.marker(vi["body"], "checked-evaluator-rev") or "null") if vi else None)
    else:
        r = {"result": "gate-rejected", "detail": "declined by the owner", "verdict": {"verdict": "rejected", "detail": "declined by the owner"}}
    if verifier:
        vf = c.report_fields(verifier)
        it.record_verification(verifier, vi, vf.get("status"), vf.get("result"), r, owner=True)
    st, upd, pr = merge_outcome(r)
    k = c.iteration_of(c.label_names(ti)) or 0
    if st != "done":
        tag_unmerged(t, k, {"commit": "x"})
    close_out(t, ti, st, upd, pr)


def sweep_requests():
    """The safety net for a lost `issues: closed` event."""
    for x in c.list_issues(state="closed", labels=(it.REQ,)):
        if it.HANDLED not in c.label_names(x):
            on_request_closed(x["number"])


# ---------------------------------------------------------------- watchdog

def reconcile():
    """End of every session: a loop with no commitments moves on at once, not at the next cron (it lags by hours)."""
    if c.guard_ok() and not c.outage_open():
        it.advance()
        drive()


def watch():
    if not c.guard_ok() or c.outage_open():
        return 0
    code = c.probe()
    if code in (401, 403):
        c.open_outage("dead Claude credential (HTTP %s)" % code, "The probe of `CLAUDE_CODE_OAUTH_TOKEN` returned %s. No session can start." % code,
                      "Generate a new token (`claude setup-token`) and store it as the repo secret. Then close this issue.")
        c.say("outage: dead Claude credential (HTTP %s)" % code)
        return 1        # a red run from cron = an e-mail to the author of the workflow file
    sweep_requests()
    return drive()


def drive():
    """Common tail of the watchdog and the epilogue: a crash streak -> outage; parked past due -> resume; stalled ->
    roll back policy; no commitments -> kick or start the next iteration."""
    if crash_streak():
        c.open_outage("%s sessions in a row crashed without a commit" % CRASH_STREAK,
                      "The last %s sessions ended `crashed` with no commit. The counter is blind to the cause." % CRASH_STREAK,
                      "Look at the machine cause (`cause`) in the reports of the latest sessions and fix it.")
        c.say("outage: %s sessions in a row crashed without a commit" % CRASH_STREAK)
        return 1
    watch_parked()
    if it.overdue():
        it.fail_iteration(it.iteration_number(it.open_iteration()), "the iteration ran past the ceiling in policy/params.yml")
        return 0
    if it.stalled():
        handle_stall()
        return 0
    # Policymaker activity alone must not hold up the next iteration; core work keeps its commitments.
    if not it.open_iteration() and not any(c.roles_of(c.label_names(x)) != ["policymaker"] for x in loop_open()):
        return kick()
    if commitments():
        c.say("quiet: commitments in progress")
        return 0
    return kick()


def handle_stall():
    """The iteration did not close in time: return `policy/` to the last good state, give up the iteration and start over;
    the next iteration's Policymaker sees the notice."""
    cur = it.open_iteration()
    reverted = it.revert_policy("the iteration did not close within the quiet time")
    it.fail_iteration(it.iteration_number(cur), "stalled%s" % ("; policy/ rolled back" if reverted else ""))


def crash_streak():
    # counted from the close of the last outage: the same crashes do not open a second one
    closed = c.list_issues(state="closed", labels=("outage",))
    since = max([x["closed_at"] for x in closed if x.get("closed_at")], default="")
    sessions = [x for x in c.recent_closed() if x["closed_at"] > since and c.in_loop(c.label_names(x))]
    if len(sessions) < CRASH_STREAK:
        return False
    for x in sessions[:CRASH_STREAK]:
        f = c.report_fields(x["number"])
        if f.get("status") != "crashed" or f.get("commit"):
            return False
    return True


def parked_until(n):
    if "blocked:rate-limit" not in c.label_names(c.issue(n)):
        return None
    return c.report_fields(n).get("resume_after")


def resume(n):
    """Sleeps to `resume_after` and resumes the parked issue. The watchdog repeating it is harmless: launch deduplicates.
    A park with a new date has its own resume run from the epilogue."""
    due = c.report_fields(n).get("resume_after")
    if not due:
        c.say("#%s: no resume_after, the resume is the watchdog's" % n)
        return
    wait = (c.parse_time(due) - c.now()).total_seconds()
    if wait > MAX_SLEEP_S:
        c.say("#%s: due %s is past the job limit, sleeping %s s and handing the clock on" % (n, due, MAX_SLEEP_S))
        time.sleep(MAX_SLEEP_S)
        if parked_until(n) == due:
            c.workflow_run("resume.yml", issue=n)
        return
    if wait > 0:
        c.say("#%s: sleeping %s s until %s" % (n, int(wait), due))
        time.sleep(wait)
    if parked_until(n) == due and c.launch(n):
        c.say("#%s: resumed on time" % n)


def watch_parked():
    for x in c.list_issues(labels=("awaiting:orders",)):         # a Policymaker waiting for its orders is bounded by the age limit too
        if (c.now() - c.parse_time(x["created_at"])).days >= MAX_AGE_DAYS:
            close_out(x["number"], x, "crashed", {"cause": "age-limit"}, "Age limit: %s days." % MAX_AGE_DAYS)
            it.advance()
    for x in c.list_issues(labels=("blocked:rate-limit",)):
        f = c.report_fields(x["number"])
        if (c.now() - c.parse_time(x["created_at"])).days >= MAX_AGE_DAYS:
            close_out(x["number"], x, "crashed", {"cause": "age-limit"}, "Age limit: %s days." % MAX_AGE_DAYS)
            continue
        if f.get("resume_after") and c.parse_time(f["resume_after"]) <= c.now():
            c.launch(x["number"])


def loop_open():
    """The only source of issues for the watchdog and for commitments: without `loop:iteration N` and a role the loop does not see an issue."""
    return [x for x in c.list_issues() if c.in_loop(c.label_names(x))]


def commitments():
    """The loop lives <=> there is a commitment: a run in progress, a park with a date ahead, a fresh dispatch, a wait for the owner or verifier."""
    if c.LAUNCHED:
        return True
    runs = c.session_runs()
    if any(r["status"] in ("queued", "in_progress", "waiting") for r in runs):
        return True
    recent = {c.run_issue(r["displayTitle"]) for r in runs if c.now() - c.parse_time(r["createdAt"]) < timedelta(minutes=GRACE_MIN)}
    for x in loop_open():
        names = c.label_names(x)
        f = c.report_fields(x["number"])
        if "blocked:rate-limit" in names and f.get("resume_after") and c.parse_time(f["resume_after"]) > c.now():
            return True
        if names & {"awaiting:owner", "awaiting:verifier"}:
            return True
        if "session #%s" % x["number"] in recent:
            return True
    return False


def kick():
    """A jam: kick before calling. Fruitless kicks are counted in the issue's report."""
    if not it.open_iteration():
        it.start_iteration()
        return 0
    ready = [x for x in loop_open() if c.is_unblocked(x["number"]) and not (c.label_names(x) & {"blocked:rate-limit", "awaiting:owner", "awaiting:verifier", "awaiting:iteration", "awaiting:orders"})]
    if not ready:
        it.advance()
        return 0
    for x in ready[:12]:
        n = x["number"]
        k = int(c.report_fields(n).get("kicks", 0)) + 1
        if k > MAX_KICKS:
            c.open_outage("the watchdog kicked #%s %s times, no run appeared" % (n, MAX_KICKS),
                          "Dispatch creates no run (broken workflow, rejected call, lost event).",
                          "Check `.github/workflows/`, the Actions tab and the token permissions.")
            c.say("outage: kicked #%s %s times, no run appeared" % (n, MAX_KICKS))
            return 1
        c.update_report(n, {"kicks": k, "kicked": c.iso()})
        if c.launch(n):
            c.say("kicked #%s" % n)
    return 0


# ---------------------------------------------------------------- main

def git_identity():
    a = c.cfg()["author"]
    c.git(".", "config", "--global", "user.name", "github-actions[bot]")
    c.git(".", "config", "--global", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    if a.get("name") and a.get("email"):
        c.git(".", "config", "--global", "author.name", a["name"])
        c.git(".", "config", "--global", "author.email", a["email"])


def main(argv):
    cmd, args = argv[1], argv[2:]
    if cmd == "guard":
        return 0 if c.guard_ok() else 10
    if cmd == "probe":
        code = c.probe()
        print("probe: HTTP %s" % code)
        return 1 if code in (401, 403) else 0
    if cmd == "resolve":
        resolve(int(args[0]))
    elif cmd == "stage":
        stage(int(args[0]), args[1], args[2])
    elif cmd == "epilogue":
        epilogue(int(args[0]), *args[1:4])
    elif cmd == "request-closed":
        on_request_closed(int(args[0]))
    elif cmd == "watch":
        return watch()
    elif cmd == "resume":
        resume(int(args[0]))
    elif cmd == "start":
        it.start_iteration()
    elif cmd == "git-identity":
        git_identity()
    else:
        raise SystemExit("unknown command: " + cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
