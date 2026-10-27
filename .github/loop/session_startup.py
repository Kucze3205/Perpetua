"""One declared startup read set, a provider estimate, and nonblocking policy notices."""
import hashlib
import json
import os
import urllib.error
import urllib.request

import common as c
from session_context import write


MARK = "<!-- startup-budget -->"
STATE = ".github/loop/data/startup-budget.json"


def count_tokens(model, system, content):
    """The provider's document estimate, not a local character heuristic or live context size."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is not configured for trusted staging")
    request = urllib.request.Request("https://api.anthropic.com/v1/messages/count_tokens",
        data=json.dumps({"model": model, "system": system,
                         "messages": [{"role": "user", "content": content}]}).encode("utf-8"),
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": key})
    with urllib.request.urlopen(request, timeout=10) as response:
        tokens = json.load(response)["input_tokens"]
    if type(tokens) is not int or tokens < 0:
        raise ValueError("provider returned an invalid token estimate")
    return tokens


def notices():
    text = c.safe_read(".", STATE)
    return json.loads(text) if text else {}


def pending():
    return [value for value in notices().values() if not value.get("delivered_to")]


def change_notices(n, change):
    for attempt in range(5):
        baseline = c.file_digest(STATE)
        old = notices()
        new = {key: dict(value) for key, value in old.items()}
        change(new)
        if new == old:
            return list(new.values())
        try:
            c.commit_main({STATE: json.dumps(new, ensure_ascii=False, indent=1) + "\n"},
                          "session #%s: startup budget notice" % n, expected={STATE: baseline})
            return list(new.values())
        except c.ConcurrentChange:
            if attempt == 4:
                raise
            c.sync_main()


def record(n, role, budget):
    """One active incident per role; repeated observations update it, without repeated policy sessions."""
    body = MARK + "\n" + json.dumps(budget, ensure_ascii=False, indent=1)
    previous = next((x for x in c.comments(n) if x["body"].startswith(MARK + "\n")
                     and x.get("user", {}).get("login") == c.BOT), None)
    if previous and previous["body"] != body:
        c.patch_comment(previous["id"], body)
    elif not previous:
        c.post_comment(n, body)

    def update(new):
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
    return change_notices(n, update)


def acknowledge(n):
    """Consume only incidents presented to a successfully completed Policymaker Session."""
    def update(new):
        for value in new.values():
            if value.get("presented_to") == n:
                value["delivered_to"] = n
    return change_notices(n, update)


def summary(n):
    for comment in c.comments(n):
        if (comment["body"].startswith(MARK + "\n")
                and comment.get("user", {}).get("login") == c.BOT):
            value = json.loads(comment["body"].split("\n", 1)[1])
            return {key: value[key] for key in ("status", "model", "tokens", "limit")}
    return None


def prepare(n, role, model, work, trusted, shaper=False):
    """Generate both the bootstrap reads and their estimate from the same recipient files."""
    import iteration as it

    metadata = it.frontmatter(c.safe_read(work, ".session/role/SKILL.md"))[0]
    defaults = [path for path in (".session/policymaker.md", ".session/loop-state.json", ".session/capabilities.json")
                if c.safe_read(work, path) is not None]
    memory = "policy/working-memory.md" if role == "policymaker" else "journal/working-memory.md" if shaper else None
    if memory and c.safe_read(work, memory) is not None:
        defaults.append(memory)
    declared = metadata.get("startup", defaults)
    errors = []
    if not isinstance(declared, list) or any(not isinstance(path, str) for path in declared):
        errors.append("startup must be a list of concrete workspace-relative file paths")
        declared = defaults
    paths = [".session/issue.md", ".session/role/SKILL.md"] + declared
    documents, contents, readable, seen = [], [], [], set()
    for path in paths:
        if (not path or os.path.isabs(path) or "\\" in path or ".." in path.split("/")
                or any(char in path for char in "*?[\n\r`")):
            errors.append("invalid startup path: " + path)
            continue
        path = os.path.normpath(path).replace(os.sep, "/")
        identity = os.path.normcase(os.path.realpath(os.path.join(work, path)))
        git_dir = os.path.normcase(os.path.realpath(os.path.join(work, ".git")))
        if identity == git_dir or identity.startswith(git_dir + os.sep):
            errors.append("Git internals are not startup documents: " + path)
            continue
        text = c.safe_read(work, path)
        if text is None:
            errors.append("startup document unavailable to this recipient: " + path)
            continue
        if identity in seen:
            continue
        seen.add(identity)
        readable.append(path)
        if "\0" in text or "\ufffd" in text:
            errors.append("startup token counting requires UTF-8 text: " + path)
        data = text.encode("utf-8")
        documents.append({"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        contents.append("# " + path + "\n\n" + text)
    prompt_path = os.path.join(trusted, "prompt.md")
    prompt = c.safe_read(trusted, "prompt.md") or ""
    prompt = ("You are role `%s` of this loop. Read these startup documents once:\n%s\n"
              "Write .session/report.md. Startup diagnostics: .session/startup.json.\n"
              "Find historical inputs through .session/history/manifest.json when needed.\n\n" %
              (role, "\n".join("- " + json.dumps(path) for path in readable))) + prompt
    with open(prompt_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(prompt)
    budget = {"model": model, "limit": c.cfg()["startup_token_warning"], "tokens": None,
              "status": "unmeasured", "detail": "", "documents": documents,
              "measurement": "Provider estimate of the contract, bootstrap and declared documents; "
                             "excludes this diagnostic output, CLI internals and later reads."}
    try:
        if errors:
            raise ValueError("; ".join(errors))
        budget["tokens"] = count_tokens(model, c.safe_read(trusted, "contract.md") or "",
                                         prompt + "\n\n" + "\n\n".join(contents))
        budget["status"] = "over_budget" if budget["tokens"] > budget["limit"] else "ok"
        budget["detail"] = "Reduce unnecessary startup reads" if budget["status"] == "over_budget" else ""
    except urllib.error.HTTPError as error:
        budget["detail"] = "Provider token count unavailable (HTTP %s)" % error.code
    except ValueError as error:
        budget["detail"] = str(error)
    except Exception:
        budget["detail"] = "Provider token count unavailable"
    try:
        active = record(n, role, budget)
        if role == "policymaker":
            budget["policy_notices"] = active
    except (Exception, SystemExit):
        # Provider/notification failures must never turn a diagnostic into a session gate.
        c.say("session #%s: startup notice could not be persisted" % n)
    write(os.path.join(work, ".session"), "startup.json", budget)
    if budget["status"] != "ok":
        with open(prompt_path, "a", encoding="utf-8") as fh:
            fh.write("\nStartup budget warning (%s): read .session/startup.json; continue your task.\n" % budget["status"])
    elif budget.get("policy_notices"):
        with open(prompt_path, "a", encoding="utf-8") as fh:
            fh.write("\nStartup notices need attention: read .session/startup.json and address them.\n")
