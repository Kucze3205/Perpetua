"""The start of a session: its settings (model, effort, secrets, contract) and its staged inputs.
"""
import hashlib
import json
import os
import shutil

import closing
import common as c
import executor_boundary
import iteration as it
import managed_resources as resources
import merge
import owner_requests as requests
import policy_inputs
import policymaker_session
import release_assets
import session_context
import session_exports
import session_inputs
import session_security as security
import session_startup
import watchdog


JOB_MAX_MIN = 355               # GitHub kills a job at 360 min; the epilogue must fit


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
            "model": model, "effort": effort, "tools": ",".join(list(p.get("tools") or []) + ["Skill"] + merge.verification_rules(i["body"])),
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
        p = policymaker_session.profile_of(role)
    except ValueError as error:
        closing.close_out(n, i, "blocked", {"cause": "start:policymaker-session"}, str(error))
        watchdog.reconcile()
        raise SystemExit(str(error))
    why = security.profile_error(p) if p else "no profile for role `%s`" % role
    granted = False
    if not why and role != "policymaker":
        granted, why = requests.consent_status(p, labels)
    if not why and (role != "policymaker" or resources.layered()) and p.get("agent") is not False and not os.path.exists(resources.role_directory(role) + "/SKILL.md"):
        why = "no skill for role `%s`" % role
    if not why and p.get("agent") is False and not p.get("command"):
        why = "script role `%s` has no command" % role
    if why:
        if role == c.cfg()["verifier_role"]:      # a verifier that cannot start sends the evaluator change back
            merge.settle_verification(n, i, "blocked", None, why)
        closing.close_out(n, i, "blocked", {"cause": "start:" + why.split()[0]}, "The session did not start: " + why + ".")
        watchdog.reconcile()
        raise SystemExit(why)
    names = requests.secrets_of_consent(labels) if granted else []
    pool = secret_pool()
    for name in names:
        if name not in pool:
            closing.block_secret_missing(n, i, name)
            watchdog.reconcile()
            raise SystemExit("secret-missing:" + name)
    options = session_options(i, role, p)
    branch = "task/%s" % n
    has = c.git(".", "ls-remote", "--exit-code", "--heads", "origin", branch, check=False, remote=True).returncode == 0
    wanted = c.marker(i["body"], "start-branch")
    start = branch if has else (c.start_ref(wanted) if wanted else c.default_branch())
    if start is None:
        why = "The session did not start: start branch `%s` is on origin neither as a branch nor as an `iter/*` tag." % wanted
        closing.close_out(n, i, "blocked", {"cause": "start:start-branch"}, why)
        watchdog.reconcile()
        raise SystemExit(why)
    c.edit_labels(n, remove=["blocked:rate-limit", "conflict"])
    agent = options["kind"] == "agent"
    c.set_output(**options, start=start, consented="true" if granted else "false",
                 tool="Claude Code" if agent else "", tool_version=str(c.cfg()["cli_version"]) if agent else "",
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
    if resources.role_layer(role) == resources.EXECUTION:
        parts.append("Do not read `policy/`: it holds the Policymaker's working files, not your task.")
    return "\n\n".join(parts) + "\n"


def stage(n, work, trusted, bundle):
    """Everything the agent needs, put in place by trusted code before the engine checkout is removed from the disk, and in
    `bundle` what the job that runs the policy scripts needs (`policy_inputs`): their results are added to this staging later."""
    i = c.issue(n)
    labels = c.label_names(i)
    role = c.roles_of(labels)[0]
    try:
        p = policymaker_session.profile_of(role)
    except ValueError as error:
        raise SystemExit("invalid session profile: " + str(error))
    error = security.profile_error(p)
    if error:
        raise SystemExit("invalid session profile: " + error)
    if role != "policymaker":
        _, error = requests.consent_status(p, labels)
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
                   omit_markers=(it.STATE_MARK, session_startup.MARK))
    metadata = it.frontmatter(c.safe_read(".", resources.role_directory(role) + "/SKILL.md"))[0]
    release_assets.fetch_inputs(i["body"], os.path.join(sess, "inputs"))
    session_exports.stage(i["body"], role, c.iteration_of(labels) or 0, sess, defaults=metadata.get("exports"))
    sees_state = role == "policymaker" or "loop:shaper" in labels
    full = sees_state and c.marker(i["body"], "exports") is None and "exports" not in metadata
    state = it.stored_state(n, sess, role=role) if sees_state else None
    state_facts = session_context.facts(sess, role, state, full) if os.path.isfile(os.path.join(resources.ROOT, "session-state.py")) else None
    if "loop:shaper" in labels:
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
    # The approved names, for agent.sh's empty-value check. Unapproved pool names need no file: the workflow expression
    # `contains(needs.prep.outputs.secrets, '|NAME|') && secrets.NAME || ''` already hands them over empty.
    granted = role != "policymaker" and requests.consent_status(p, labels)[0]
    with open(os.path.join(trusted, "secrets"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(x + "\n" for x in requests.secrets_of_consent(labels) if granted))
    with open(os.path.join(trusted, "tools"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(",".join(security.tool_names(p)))
    with open(os.path.join(trusted, "mcp.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {}}, fh)
    for f in ("agent.sh", "stream_filter.py", "remaining.py", "session_artifact.py"):
        shutil.copy(".github/loop/" + f, trusted)
    # Machine preparation is policy: `policy/prepare.sh` of the accepted tree (none: nothing to prepare).
    prepare = os.path.join(resources.ROOT, "prepare.sh")
    if os.path.isfile(prepare) and not os.path.islink(prepare):
        shutil.copy(prepare, os.path.join(trusted, "prepare.sh"))
    else:
        with open(os.path.join(trusted, "prepare.sh"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("#!/usr/bin/env bash\n")
    if resources.layered() and p.get("agent") is not False:
        staged_role = os.path.join(sess, "role")
        shutil.rmtree(staged_role, ignore_errors=True)
        shutil.copytree(resources.role_directory(role), staged_role)
    if role == "policymaker":
        with open(os.path.join(sess, "policymaker.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("You are the Policymaker. Follow the engine contract supplied in system instructions.\n\n" + template)
    elif p.get("agent") is False:
        with open(os.path.join(trusted, "command.sh"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(p["command"] + "\n")
    with open(os.path.join(trusted, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(security.settings(p, trusted), fh)
    executor_boundary.record(work, trusted)
    startup_plan = None
    if p.get("agent") is not False and os.path.isfile(os.path.join(resources.ROOT, "session-startup.py")):
        startup_plan = session_startup.plan(n, role, session_options(i, role, p)["model"], work, trusted, shaper="loop:shaper" in labels)
    policy_inputs.pack(bundle, state_facts, startup_plan)
