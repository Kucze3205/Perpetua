"""The startup read set, bootstrap prompt and budget come from `policy/session-startup.py`; incidents are kept here."""
import hashlib
import json
import os

import common as c
import policy_script


MARK = "<!-- startup-budget -->"
STATE = ".github/loop/data/startup-budget.json"
STARTUP_BYTES = 1024 * 1024        # what the engine accepts from policy/session-startup.py


def notices():
    text = c.safe_read(".", STATE)
    return json.loads(text) if text else {}


def pending(held=None):
    return [value for value in (notices() if held is None else held).values() if not value.get("delivered_to")]


def change_notices(n, change, step):
    for attempt in range(5):
        baseline = c.file_digest(STATE)
        old = notices()
        new = {key: dict(value) for key, value in old.items()}
        change(new)
        if new == old:
            return list(new.values())
        try:
            c.commit_main({STATE: json.dumps(new, ensure_ascii=False, indent=1) + "\n"},
                          "%s #%s: startup budget notice" % (step, n), expected={STATE: baseline})
            return list(new.values())
        except c.ConcurrentChange:
            if attempt == 4:
                raise
            c.sync_main()


def note(new, n, role, budget):
    """Put this session's startup budget into `new` (the notices, changed in place): one incident per role, updated by repeated
    observations; a measurement that is `ok` clears it. The Policymaker is shown everything that is open."""
    if budget["status"] == "ok":
        new.pop(role, None)
    else:
        current = new.get(role, {})
        same = current.get("status") == budget["status"] and current.get("model") == budget["model"]
        notice = {key: budget[key] for key in ("status", "model", "tokens", "limit", "detail")}
        notice.update(role=role, issue=n,
                      id=current["id"] if same else "startup-%s-%s" % (role, n))
        for key in ("delivered_to", "presented_to"):
            if same and current.get(key):
                notice[key] = current[key]
        notice["largest_documents_by_bytes"] = sorted(budget["documents"], key=lambda x: -x["bytes"])[:3]
        new[role] = notice
    if role == "policymaker":
        for value in new.values():
            value["presented_to"] = n


def record(n, role, budget):
    """The budget a session started with (the policy scripts' job measured it): a comment on its issue and the incident."""
    budget = {key: value for key, value in budget.items() if key != "policy_notices"}
    body = MARK + "\n" + json.dumps(budget, ensure_ascii=False, indent=1)
    previous = next((x for x in c.comments(n) if x["body"].startswith(MARK + "\n")
                     and x.get("user", {}).get("login") == c.BOT), None)
    if previous and previous["body"] != body:
        c.patch_comment(previous["id"], body)
    elif not previous:
        c.post_comment(n, body)
    return change_notices(n, lambda new: note(new, n, role, budget), "epilogue")


def acknowledge(n):
    """Consume only incidents presented to a successfully completed Policymaker Session."""
    def update(new):
        for value in new.values():
            if value.get("presented_to") == n:
                value["delivered_to"] = n
    return change_notices(n, update, "epilogue")


def summary(n):
    for comment in c.comments(n):
        if (comment["body"].startswith(MARK + "\n")
                and comment.get("user", {}).get("login") == c.BOT):
            value = json.loads(comment["body"].split("\n", 1)[1])
            return {key: value[key] for key in ("status", "model", "tokens", "limit")}
    return None


def candidate(work, path):
    """(workspace-relative path, text) of a file the recipient may read; (None, problem) otherwise."""
    if (not isinstance(path, str) or not path or os.path.isabs(path) or "\\" in path or ".." in path.split("/")
            or any(char in path for char in "*?[\n\r`")):
        return None, "invalid startup path: %s" % (path,)
    path = os.path.normpath(path).replace(os.sep, "/")
    identity = os.path.normcase(os.path.realpath(os.path.join(work, path)))
    git_dir = os.path.normcase(os.path.realpath(os.path.join(work, ".git")))
    if identity == git_dir or identity.startswith(git_dir + os.sep):
        return None, "Git internals are not startup documents: " + path
    text = c.safe_read(work, path)
    if text is None:
        return None, "startup document unavailable to this recipient: " + path
    return path, text


def budget_ok(budget):
    """Whether `budget` has the shape the engine accepts: what the script printed, and what the epilogue reads back."""
    return (isinstance(budget, dict) and budget.get("status") in ("ok", "over_budget", "unmeasured")
            and (budget.get("tokens") is None or type(budget["tokens"]) is int and budget["tokens"] >= 0)
            and all(isinstance(budget.get(key, ""), str) and len(budget.get(key, "")) <= 500 for key in ("detail", "measurement")))


def plan(n, role, model, work, trusted, shaper=False):
    """With the token, before the scripts run: what the startup facts hold besides the files the recipient may read, which the job
    that runs `policy/session-startup.py` reads from its copy of the tree."""
    import iteration as it

    metadata = it.frontmatter(c.safe_read(work, ".session/role/SKILL.md"))[0]
    return {"issue": n, "role": role, "model": model, "limit": c.params()["startup_token_warning"], "shaper": shaper,
            "declared": metadata.get("startup"), "contract": c.safe_read(trusted, "contract.md") or "",
            "prompt": c.safe_read(trusted, "prompt.md") or "", "notices": notices()}


def settle(bundle, work, plan):
    """In the job that holds no token: `policy/session-startup.py` decides the reads, the bootstrap prompt and the budget from
    the recipient's files in `work`; the kernel only shows it what the recipient may read and checks what comes back.
    Returns (prompt, budget). No script: None, no bootstrap, no budget. Raises PolicyScriptError for an answer it refuses."""
    role, declared = plan["role"], plan["declared"]
    defaults = [path for path in (".session/policymaker.md", ".session/loop-state.json", ".session/capabilities.json")
                if c.safe_read(work, path) is not None]
    memory = "policy/working-memory.md" if role == "policymaker" else "journal/working-memory.md" if plan["shaper"] else None
    if memory and c.safe_read(work, memory) is not None:
        defaults.append(memory)
    fixed = [".session/issue.md", ".session/role/SKILL.md"]
    asked = fixed + (declared if isinstance(declared, list) else []) + defaults
    documents, problems = {}, []
    for path in asked:
        found, text = candidate(work, path)
        if found is None:
            problems.append(text)
        else:
            documents[found] = text
    facts = {"role": role, "model": plan["model"], "limit": plan["limit"], "shaper": plan["shaper"],
             "declared": declared, "defaults": defaults, "fixed": fixed, "documents": documents, "problems": problems,
             "contract": plan["contract"], "prompt": plan["prompt"], "pending_notices": pending(plan["notices"])}
    out = policy_script.run(bundle, "session-startup.py", facts, max_bytes=STARTUP_BYTES)
    if out is None:
        return None
    reads, prompt, budget = out.get("reads"), out.get("prompt"), out.get("budget")
    if not (isinstance(reads, list) and all(path in documents for path in reads) and isinstance(prompt, str) and budget_ok(budget)):
        raise policy_script.PolicyScriptError("policy/session-startup.py: expected {\"reads\": [paths it was shown], \"prompt\": text, "
                                              "\"budget\": {\"status\": ok|over_budget|unmeasured, \"tokens\": int or null}}")
    listed, seen = [], set()
    for path in reads:
        identity = os.path.normcase(os.path.realpath(os.path.join(work, path)))
        if identity not in seen:
            seen.add(identity)
            data = documents[path].encode("utf-8")
            listed.append({"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    budget = {"model": plan["model"], "limit": plan["limit"], "tokens": budget.get("tokens"), "status": budget["status"],
              "detail": budget.get("detail", ""), "documents": listed, "measurement": budget.get("measurement", "")}
    if role == "policymaker":        # it is shown the incidents the epilogue is about to open, its own among them
        held = json.loads(json.dumps(plan["notices"]))
        note(held, plan["issue"], role, budget)
        budget["policy_notices"] = list(held.values())
    return prompt, budget
