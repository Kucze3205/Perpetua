"""Frozen owner proposals and restart-safe decisions. No agent code or secret values are read."""
import hashlib
import json
import re

import common as c

KINDS = ("capability", "limits-change", "engine-change")
APPROVED = "request:approved"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def path(n):
    return ".github/loop/request-state/%s.json" % n


def read(n):
    try:
        with open(path(n), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def config_text():
    with open("engine.yml", encoding="utf-8", newline="") as fh:
        return fh.read()


def limits_block(text):
    """Validate the entire proposed limits block, never a command or a patch to engine files."""
    import yaml
    block = yaml.safe_load(text)
    if not isinstance(block, dict) or not block or any(not isinstance(k, str) for k in block):
        raise ValueError("limits must be a nonempty mapping")
    for key in ("tools", "internet_write"):
        if key not in block or not isinstance(block[key], list) or any(not isinstance(x, str) for x in block[key]):
            raise ValueError("limits.%s must be a list of strings" % key)
    # Bound the shape to plain configuration, including extensions such as owner ceilings.
    json.dumps(block, allow_nan=False)
    return block


def replace_limits(text, proposed):
    """Replace only the top-level YAML limits node; leave other bytes and comments alone."""
    import yaml
    if any(isinstance(t, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)) for t in yaml.scan(text)):
        raise ValueError("automatic limits changes require an engine.yml without YAML anchors or aliases")
    node = yaml.compose(text)
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("engine.yml must be a mapping")
    keys = [k.value for k, _ in node.value]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate engine.yml keys")
    matches = [(k, v) for k, v in node.value if k.value == "limits"]
    replacement = yaml.safe_dump({"limits": limits_block(proposed)}, sort_keys=False)
    if not matches:
        return text.rstrip("\r\n") + "\n\n" + replacement
    key, value = matches[0]

    def content_end(n):
        if isinstance(n, yaml.ScalarNode) or n.flow_style:
            return n.end_mark.index
        children = ([x for pair in n.value for x in pair] if isinstance(n, yaml.MappingNode) else n.value)
        return max([content_end(x) for x in children] or [n.start_mark.index])

    end = text.find("\n", content_end(value))
    end = len(text) if end < 0 else end + 1
    return text[:key.start_mark.index] + replacement + text[end:]


def prepare(kind, frozen, readiness=None):
    """Called by the trusted epilogue, using its accepted checkout as the proposal's base."""
    if kind not in KINDS:
        return None
    if kind == "capability":
        if not isinstance(readiness, str) or not readiness.strip():
            raise ValueError("capability needs a readiness instruction (no secret values)")
        frozen = frozen or "Owner action: provide the capability described above."
    if not isinstance(frozen, str) or not frozen.strip():
        raise ValueError("a concrete frozen proposal is required")
    base = config_text()
    if kind == "limits-change":
        import yaml
        try:
            replace_limits(base, frozen)       # validate before asking the owner
        except yaml.YAMLError as e:
            raise ValueError("invalid limits YAML") from e
    return {"kind": kind, "frozen": frozen, "proposal_sha": digest(frozen), "base_commit": c.head(),
            "base_engine_sha": digest(base), "readiness": readiness, "status": "pending"}


def instructions(proposal):
    sha = proposal["proposal_sha"]
    base = "Base commit: `%s`; engine.yml SHA-256: `%s`; proposal SHA-256: `%s`." % (
        proposal["base_commit"], proposal["base_engine_sha"], sha)
    if proposal["kind"] == "limits-change":
        action = ("Completed automatically replaces only `limits` in engine.yml with the frozen block, "
                  "if both the proposal and configuration still match. Not planned changes nothing.")
    elif proposal["kind"] == "engine-change":
        action = ("The owner applies the frozen diff manually against the base commit, reviews it and decides whether to merge. "
                  "Closing this issue never applies engine files or opens a PR. After applying and checking it, "
                  "post `<!-- engine-applied: %s -->` and close as completed to release dependencies." % sha)
    else:
        action = ("The owner must provide the tool, access or secret manually, then check: %s\n\n"
                  "Never paste a secret value here. Closing this issue does not create or verify a secret. "
                  "After checking readiness, the owner posts `<!-- capability-ready: %s -->` and closes as completed. "
                  "Until that explicit confirmation dependencies remain blocked." % (proposal["readiness"], sha))
    return "\n## Decision and application\n\n%s\n\n%s\n" % (action, base)


def freeze(n, proposal, body):
    proposal = dict(proposal, issue=n, body_sha=digest(body))
    c.commit_main({path(n): json.dumps(proposal, indent=2) + "\n"}, "freeze owner request #%s" % n,
                  expected={path(n): None})


def owner_confirmed(n, name, sha):
    owners = {o.casefold() for o in c.owner_login()}
    return any(x.get("user", {}).get("login", "").casefold() in owners and
               re.search(r"^<!-- %s: %s -->\s*$" % (re.escape(name), re.escape(sha)), x.get("body", ""), re.M)
               for x in c.comments(n))


def settle(n, issue):
    """Persist decision together with the accepted limits. Replay only repairs labels/dependencies."""
    state = read(n)
    if state is None:
        c.post_comment(n, "Not applied: no trusted frozen proposal exists for this request.")
        c.edit_labels(n, add=["request:handled"])
        return "invalid"
    if state["status"] in ("approved", "declined", "invalid", "conflict"):
        return state["status"]
    before = c.file_digest(path(n))
    closer = (issue.get("closed_by") or {}).get("login", "").casefold()
    if closer not in {o.casefold() for o in c.owner_login()}:
        # GitHub write access alone is not authority to widen the owner's accepted limits.
        return "waiting"
    accepted = issue.get("state_reason") == "completed"
    files = {}
    expected = {path(n): before}
    if not accepted:
        status, detail = "declined", "The owner declined; configuration was not changed."
    elif digest(issue.get("body") or "") != state["body_sha"]:
        status, detail = "invalid", "Not applied: the request changed after it was frozen. Open a new proposal."
    elif state["kind"] == "limits-change":
        if c.file_digest("engine.yml") != state["base_engine_sha"]:
            status, detail = "conflict", "Not applied: engine.yml changed since the proposal's base. Open a new proposal."
        else:
            files["engine.yml"] = replace_limits(config_text(), state["frozen"])
            expected["engine.yml"] = state["base_engine_sha"]
            status, detail = "approved", "Applied only the frozen limits block."
    else:
        marker = "capability-ready" if state["kind"] == "capability" else "engine-applied"
        if owner_confirmed(n, marker, state["proposal_sha"]):
            status, detail = "approved", "The owner explicitly confirmed %s; dependencies may resume." % marker
        else:
            status, detail = "waiting", "Waiting for the owner's explicit `%s` confirmation; dependencies remain blocked." % marker
    if state["status"] == status:
        return status
    state.update(status=status, detail=detail, decided_at=c.iso())
    files[path(n)] = json.dumps(state, indent=2) + "\n"
    try:
        c.commit_main(files, "owner request #%s: %s" % (n, status), expected=expected)
    except c.ConcurrentChange:
        # A push race: reload the winning decision/configuration, never reapply stale bytes.
        return settle(n, issue)
    c.post_comment(n, detail)
    return status
