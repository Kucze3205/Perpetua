"""Frozen owner proposals and restart-safe decisions. No agent code or secret values are read."""
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile

import common as c

KINDS = ("capability", "limits-change", "engine-change")
APPROVED = "request:approved"
PATCH_MINUTES = 30         # the tokenless job's time limit for applying a patch and running its tests
RESULT_BYTES = 4 * 1024 * 1024
REASON_BYTES = 1500


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
    if "tools" not in block or not isinstance(block["tools"], list) or any(not isinstance(x, str) for x in block["tools"]):
        raise ValueError("limits.tools must be a list of strings")
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


def prepare(kind, frozen, readiness=None, test=None):
    """Called by the trusted epilogue, using its accepted checkout as the proposal's base."""
    if kind not in KINDS:
        return None
    if kind == "capability":
        if not isinstance(readiness, str) or not readiness.strip():
            raise ValueError("capability needs a readiness instruction (no secret values)")
        frozen = frozen or "Owner action: provide the capability described above."
    if not isinstance(frozen, str) or not frozen.strip():
        raise ValueError("a concrete frozen proposal is required")
    if test is not None and (kind != "engine-change" or not isinstance(test, str) or not test.strip()):
        raise ValueError("only an engine-change takes a test command, and it must be a nonempty string")
    base = config_text()
    if kind == "limits-change":
        import yaml
        try:
            replace_limits(base, frozen)       # validate before asking the owner
        except yaml.YAMLError as e:
            raise ValueError("invalid limits YAML") from e
    return {"kind": kind, "frozen": frozen, "test": test, "base_commit": c.head(), "base_engine_sha": digest(base),
            "proposal_sha": digest(frozen if test is None else json.dumps([frozen, test])),
            "readiness": readiness, "status": "pending"}


def instructions(proposal):
    sha = proposal["proposal_sha"]
    base = "Base commit: `%s`; engine.yml SHA-256: `%s`; proposal SHA-256: `%s`." % (
        proposal["base_commit"], proposal["base_engine_sha"], sha)
    if proposal["kind"] == "limits-change":
        action = ("Completed automatically replaces only `limits` in engine.yml with the frozen block, "
                  "if both the proposal and configuration still match. Not planned changes nothing.")
    elif proposal["kind"] == "engine-change":
        action = ("Completed makes the engine apply the frozen diff to the current `main`%s, in a job that holds no token, "
                  "and open a PR from `engine-change/%s`; merging the PR applies it, closing the PR unmerged declines it. "
                  "A diff that touches `.github/workflows/**`, does not apply cleanly or fails its test gets no PR and "
                  "the request says why: apply it manually, then post `<!-- engine-applied: %s -->` and close as "
                  "completed. Not planned changes nothing." % (" and run the test command" if proposal.get("test") else "", sha[:12], sha))
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


def section(body, title):
    m = re.search(r"^## %s\n\n(.*?)(?=\n## |\n<!--|\Z)" % re.escape(title), body, re.S | re.M)
    return m.group(1).strip() if m else ""


# Runs in the tokenless job on the staged tree: apply the diff, record what it changed as data, run the test command.
# Exit 3: the diff does not apply; 4: the test command failed; 5: the diff changes something other than regular files.
COLLECT = r'''import base64, json, os, subprocess, sys
raw = subprocess.run(["git", "diff", "--cached", "--raw", "--no-renames", "-z"], capture_output=True, check=True).stdout.split(b"\0")
files, deleted = {}, []
for meta, name in zip(raw[0::2], raw[1::2]):
    mode_new, status = meta.split()[1], meta.split()[4][:1]
    path = name.decode("utf-8", "surrogateescape")
    if status == "D":
        deleted.append(path)
    elif mode_new in (b"100644", b"100755"):
        with open(path, "rb") as fh:
            files[path] = {"x": mode_new == b"100755", "data": base64.b64encode(fh.read()).decode()}
    else:
        print("%s is not a regular file" % path, file=sys.stderr)
        sys.exit(5)
json.dump({"files": files, "deleted": deleted}, open(sys.argv[1], "w"))
'''


def stage_patch(tree, diff, test):
    """The data the tokenless job receives: the accepted tree (`tree/`), the diff, and `run.sh` applying and testing it."""
    bundle = tempfile.mkdtemp(prefix="bundle-")
    shutil.copytree(tree, os.path.join(bundle, "tree"), symlinks=True, ignore=shutil.ignore_patterns(".git"))
    for name, text in (("engine-change.diff", diff), ("collect.py", COLLECT), ("run.sh", (
            'B="$(cd "$(dirname "$0")" && pwd)"\ncd "$B/tree"\ngit init -q\ngit add -A\n'
            'git -c user.name=engine -c user.email=engine@localhost commit -q -m base\n'
            'git apply --index "$B/engine-change.diff" || exit 3\n'
            'python3 "$B/collect.py" "$B/result.json" || exit 5\n%scat "$B/result.json"\n') % (
                "bash -c %s >&2 || exit 4\n" % shlex.quote(test) if test else ""))):
        with open(os.path.join(bundle, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return bundle


def unsafe(path):
    parts = path.split("/")
    return (not path or path.startswith("/") or "\0" in path or any(x in ("", ".", "..") for x in parts) or
            parts[0] == ".git" or path.startswith(".github/workflows/"))


def read_result(out):
    """(result, None) from the tokenless job's output directory, or (None, why). The job's answer is data: every
    path and size is checked before anything is written."""
    def read(name, limit):
        try:
            with open(os.path.join(out, name), encoding="utf-8", errors="replace") as fh:
                return fh.read(limit)
        except (OSError, TypeError):
            return None
    code = (read("exit-code", 20) or "").strip()
    if not code:
        return None, "the job that applies the diff returned no result"
    if code != "0":
        stage = {"3": "the diff does not apply cleanly to the current `main`", "4": "the test command failed",
                 "5": "the diff changes something other than regular files"}.get(code, "the job failed (exit %s)" % code)
        return None, "%s: %s" % (stage, (read("stderr.txt", 1 << 20) or "")[-REASON_BYTES:].strip())
    text = read("stdout.txt", RESULT_BYTES + 1)
    try:
        if text is None or len(text) > RESULT_BYTES:
            raise ValueError("missing or oversized")
        result = json.loads(text)
        files, deleted = result["files"], result["deleted"]
        assert isinstance(files, dict) and isinstance(deleted, list) and (files or deleted)
        for path, f in files.items():
            assert isinstance(f, dict) and isinstance(f["x"], bool)
            f["data"] = base64.b64decode(f["data"], validate=True)
        paths = list(files) + deleted
        assert all(isinstance(x, str) for x in paths)
    except (ValueError, KeyError, TypeError, AssertionError):
        return None, "the job returned a malformed result"
    if any(unsafe(x) for x in paths):
        return None, "the diff touches `.github/workflows/**` or a path outside the repository, which the loop's token cannot write"
    return result, None


def prepare_pr(n, state, body, branch):
    """Apply the frozen diff in the tokenless job, test it there, and push the result to `branch` as a commit on the tree
    it was applied to. Returns (PR title/body, None) or (None, reason)."""
    title = "Engine change: %s" % (section(body, "What").splitlines() or ["request #%s" % n])[0][:60]
    diff = state["frozen"].rstrip("\n") + "\n"
    if any(".github/workflows/" in line for line in diff.splitlines()
           if line.startswith(("diff --git ", "--- ", "+++ ", "rename ", "copy "))):
        return None, "the diff touches `.github/workflows/**`, which the loop's token cannot write"
    c.sync_main()
    base = c.head()
    bundle = stage_patch(".", diff, state.get("test"))
    try:
        result, why = read_result(c.run_tokenless(bundle, PATCH_MINUTES))
    finally:
        shutil.rmtree(bundle, ignore_errors=True)
    if why:
        return None, why
    try:
        c.git(".", "checkout", "-q", "-B", branch, base)
        for path in result["deleted"]:
            c.git(".", "rm", "-q", "-f", "--ignore-unmatch", "--", path)
        for path, f in result["files"].items():
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            if os.path.islink(path):
                os.remove(path)
            with open(path, "wb") as fh:
                fh.write(f["data"])
            os.chmod(path, 0o755 if f["x"] else 0o644)
        c.git(".", "add", "-A", "--", *(list(result["files"]) + result["deleted"]))
        files = sorted(result["files"]) + sorted(result["deleted"])
        c.git(".", "commit", "-q", "-m", title)
        c.git(".", "push", "-f", "origin", "HEAD:refs/heads/%s" % branch, remote=True)
    finally:
        c.sync_main()
    text = ("%s\n\n**Why:** %s\n\n**Unblocks:** %s\n\nFiles:\n%s\n\n%sOwner request: #%s\n\n---\n"
            "Proposal SHA-256: `%s`; base commit: `%s`; engine.yml SHA-256: `%s`.\n" % (
                section(body, "What"), section(body, "Why"), section(body, "What it unblocks"),
                "\n".join("- `%s`" % f for f in files),
                "Tested in the tokenless job with: `%s`\n\n" % state["test"] if state.get("test") else "",
                n, state["proposal_sha"], state["base_commit"], state["base_engine_sha"]))
    return (title, text), None


def engine_pr(n, state, body):
    """The PR is the application: merged = approved, closed unmerged = declined, otherwise the owner keeps waiting."""
    branch = "engine-change/" + state["proposal_sha"][:12]
    pr = c.pr_for_branch(branch)
    if pr is None and not state.get("prep_failed"):
        made, why = None, None
        try:
            made, why = prepare_pr(n, state, body, branch)
            if made:
                c.open_pr(branch, *made)
                pr = c.pr_for_branch(branch) or c.pr_for_branch(branch)      # read again once: the PR may not be listed yet
        except SystemExit as e:
            why = str(e)
        if pr is None:
            return "waiting", ("No PR was opened: %s. Apply the diff manually, then post `<!-- engine-applied: %s -->`; "
                               "dependencies remain blocked." % (why or "the PR could not be created", state["proposal_sha"])), {"prep_failed": True}
    if pr is None:
        return "waiting", state["detail"], {}
    extra = {"pr": pr["url"]}
    if pr["merged"]:
        return "approved", "Merged %s; dependencies may resume." % pr["url"], extra
    if pr["state"] == "closed":
        return "declined", "Closed without merging %s; the engine was not changed." % pr["url"], extra
    return "waiting", "Waiting for the owner to merge %s; dependencies remain blocked." % pr["url"], extra


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
    files, extra = {}, {}
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
        elif state["kind"] == "engine-change":
            status, detail, extra = engine_pr(n, state, issue.get("body") or "")
        else:
            status, detail = "waiting", "Waiting for the owner's explicit `%s` confirmation; dependencies remain blocked." % marker
    if state["status"] == status and all(state.get(k) == v for k, v in extra.items()):
        return status
    state.update(extra, status=status, detail=detail, decided_at=c.iso())
    files[path(n)] = json.dumps(state, indent=2) + "\n"
    try:
        c.commit_main(files, "owner request #%s: %s" % (n, status), expected=expected)
    except c.ConcurrentChange:
        # A push race: reload the winning decision/configuration, never reapply stale bytes.
        return settle(n, issue)
    c.post_comment(n, detail)
    return status
