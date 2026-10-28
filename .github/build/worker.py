"""Build one ticket in one Claude session: implement, pass the engine tests (fix rounds resume the same session), merge
the integration branch in (conflicts resumed in the same session too) and push it. Writes out/status.json:
{"status": "done" | "failed" | "blocked" | "limited", "reason", "resets_at", "summary", "commit"}.

The session never gets the GitHub token: Claude runs with GH_TOKEN removed, and git talks to GitHub only from here."""
import base64
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from common import PARENT, REPO, api, gh  # noqa: E402

ISSUE = int(os.environ["ISSUE"])
BRANCH = os.environ["BRANCH"]
TEMP = os.environ["RUNNER_TEMP"]
OUT = os.path.join(TEMP, "out")
BRIEF = os.path.join(TEMP, "brief")
WORK = os.path.abspath("work")
CTL = os.path.dirname(os.path.abspath(__file__))
CLI = os.environ["AGENT_CLI"]
MODEL, EFFORT = "claude-sonnet-5-5", "medium"
FIX_ROUNDS = 3
TESTS = [["/usr/bin/python3", "-m", "unittest", "discover", "-s", "engine/.github/loop/tests"],
         ["python3", "-m", "unittest", "discover", "-s", "skills/update", "-p", "test_engine_update.py"]]
os.makedirs(OUT, exist_ok=True)
os.makedirs(BRIEF, exist_ok=True)
AUTH = "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + os.environ["GH_TOKEN"]).encode()).decode()


class Limited(Exception):
    pass


def finish(status, reason="", **extra):
    doc = dict(status=status, reason=reason[-4000:], issue=ISSUE, **extra)
    with open(os.path.join(OUT, "status.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    print("status:", status, reason[:300])
    sys.exit(0)


def git(*args, net=False, check=True):
    pre = ["-c", "http.https://github.com/.extraheader=" + AUTH] if net else []
    r = subprocess.run(["git", *pre, *args], cwd=WORK, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit("git %s: %s" % (args[0], r.stderr.strip()))
    return r


def brief():
    """The ticket with its comments (earlier attempts included) and the parent spec, outside the repository."""
    issue = json.loads(gh("issue", "view", str(ISSUE), "--json", "title,body,comments"))
    spec = json.loads(gh("issue", "view", str(PARENT), "--json", "title,body"))
    subs = api("repos/%s/issues/%d/sub_issues?per_page=100" % (REPO, PARENT))
    parts = ["# Ticket #%d: %s" % (ISSUE, issue["title"]), "", issue["body"]]
    if issue["comments"]:
        parts += ["", "## Comments on the ticket"] + ["\n---\n%s" % c["body"] for c in issue["comments"]]
    parts += ["", "# Sibling tickets of the spec (state)"] + ["- #%d [%s] %s" % (s["number"], s["state"], s["title"]) for s in subs]
    parts += ["", "# Parent spec #%d: %s" % (PARENT, spec["title"]), "", spec["body"]]
    path = os.path.join(BRIEF, "issue.md")
    with open(path, "w") as fh:
        fh.write("\n".join(parts))
    return path


SESSION = {"id": None, "n": 0}


def claude(prompt):
    """One turn of the ticket's session (the first starts it, later ones resume it). Returns the final message."""
    SESSION["n"] += 1
    log = os.path.join(OUT, "transcript-%d.ndjson" % SESSION["n"])
    # The engine tests run `pkill -TERM -f "claude -p"` (stream_filter.py, test_stream.py), which would kill this very
    # session: its command line must not contain that string. Hence `-p` comes last and the prompt arrives on stdin.
    cmd = [CLI, "--model", MODEL, "--effort", EFFORT, "--dangerously-skip-permissions",
           "--output-format", "stream-json", "--verbose"]
    if SESSION["id"]:
        cmd += ["--resume", SESSION["id"]]
    cmd += ["-p"]
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    with open(log, "w") as fh:
        subprocess.run(cmd, cwd=WORK, env=env, input=prompt, text=True, encoding="utf-8",
                       stdout=fh, stderr=subprocess.STDOUT)
    result, limited, resets = None, False, None
    for raw in open(log, errors="replace"):
        try:
            ev = json.loads(raw)
        except ValueError:
            if "limit" in raw.lower() and ("reset" in raw.lower() or "hit your" in raw.lower()):
                limited = True
            continue
        if ev.get("session_id"):
            SESSION["id"] = ev["session_id"]
        info = ev.get("rate_limit_info") or {}
        if ev.get("type") == "rate_limit_event" and info.get("status") == "rejected" and not info.get("isUsingOverage"):
            limited, resets = True, info.get("resetsAt") or resets
        if ev.get("type") == "result":
            result = ev
    text = (result or {}).get("result") or ""
    if limited or ((result or {}).get("is_error") and "limit" in text.lower()):
        raise Limited(resets)
    if result is None:
        raise SystemExit("the Claude session ended without a result (see %s)" % log)
    return text


def commit_leftovers():
    if git("status", "--porcelain").stdout.strip():
        git("add", "-A")
        git("commit", "-q", "-m", "#%d: changes left uncommitted by the session" % ISSUE)


def leave_workflows():
    """The Actions token cannot push changes to .github/workflows/ (GitHub refuses them), so the ticket's changes there
    are set aside as a patch for the owner and the files are restored to the integration branch's version."""
    git("fetch", "-q", "origin", BRANCH, net=True)
    base = git("merge-base", "FETCH_HEAD", "HEAD").stdout.strip()
    paths = git("diff", "--name-only", base, "HEAD", "--", ":(top).github/workflows").stdout.split()
    if not paths:
        return
    patch = git("diff", base, "HEAD", "--", ":(top).github/workflows").stdout
    with open(os.path.join(OUT, "workflows.patch"), "w") as fh:
        fh.write(patch)
    for path in paths:
        if git("cat-file", "-e", "%s:%s" % (base, path), check=False).returncode == 0:
            git("checkout", base, "--", path)
        else:
            git("rm", "-q", "-f", "--", path)
    git("commit", "-q", "-m", "#%d: leave changes to .github/workflows/ to the owner (the build cannot push them)" % ISSUE)


def workflows_patch():
    try:
        with open(os.path.join(OUT, "workflows.patch")) as fh:
            return fh.read()[-20000:]
    except OSError:
        return ""


def tests():
    out = []
    for cmd in TESTS:
        try:
            r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=3600)
        except subprocess.TimeoutExpired:
            return False, "$ %s\ntimed out after 60 minutes" % " ".join(cmd)
        out.append("$ %s\n%s%s" % (" ".join(cmd), r.stdout[-3000:], r.stderr[-12000:]))
        if r.returncode:
            return False, "\n".join(out)
    return True, ""


def green(why):
    """Tests pass, or fix rounds in the same session until they do."""
    for k in range(FIX_ROUNDS + 1):
        commit_leftovers()
        leave_workflows()
        ok, log = tests()
        if ok:
            return True, ""
        if k == FIX_ROUNDS:
            return False, log
        claude("%s, the engine test suites fail. Fix the cause (not the tests, unless a test asserts behaviour this "
               "ticket changes on purpose), run both suites again and commit. Failure output, trimmed:\n\n%s" % (why, log[-8000:]))


def main():
    git("config", "user.name", "perpetua-build")
    git("config", "user.email", "perpetua-build@users.noreply.github.com")
    work_branch = "build/issue-%d" % ISSUE
    if git("ls-remote", "--exit-code", "origin", work_branch, net=True, check=False).returncode == 0:
        git("fetch", "-q", "origin", work_branch, net=True)
        git("checkout", "-q", "-B", work_branch, "FETCH_HEAD")
        git("merge", "-q", "--no-edit", "origin/" + BRANCH, check=False)
        if git("diff", "--name-only", "--diff-filter=U").stdout.strip():
            git("merge", "--abort")
            git("checkout", "-q", "-B", work_branch, "origin/" + BRANCH)
    else:
        git("checkout", "-q", "-B", work_branch, "origin/" + BRANCH)
    base = git("rev-parse", "origin/" + BRANCH).stdout.strip()
    with open(os.path.join(CTL, "worker-prompt.md")) as fh:
        prompt = fh.read().replace("{ISSUE}", str(ISSUE)).replace("{PARENT}", str(PARENT)) \
            .replace("{BRIEF}", brief()).replace("{BRANCH}", work_branch)
    try:
        summary = claude(prompt)
        if summary.lstrip().startswith("BLOCKED"):
            commit_leftovers()
            push_work(work_branch)
            finish("blocked", summary)
        ok, log = green("After your work on the ticket")
        if not ok:
            push_work(work_branch)
            finish("failed", "engine tests still fail after %d fix rounds:\n%s" % (FIX_ROUNDS, log), summary=summary)
        for attempt in range(6):
            git("fetch", "-q", "origin", BRANCH, net=True)
            if git("merge", "-q", "--no-edit", "FETCH_HEAD", check=False).returncode:
                conflicted = git("diff", "--name-only", "--diff-filter=U").stdout
                claude("Other tickets were merged into `%s` meanwhile and merging it into your branch conflicts in:\n%s\n"
                       "Resolve every conflict keeping both sides' intent, `git add` the files and finish the merge with "
                       "`git commit --no-edit`. Then run both test suites." % (BRANCH, conflicted))
                if git("diff", "--name-only", "--diff-filter=U").stdout.strip():
                    push_work(work_branch)
                    finish("failed", "merge conflicts with %s left unresolved" % BRANCH, summary=summary)
                commit_leftovers()
            ok, log = green("After merging `%s` into your branch" % BRANCH)
            if not ok:
                push_work(work_branch)
                finish("failed", "engine tests fail after merging %s:\n%s" % (BRANCH, log), summary=summary)
            pushed = git("push", "-q", "origin", "HEAD:" + BRANCH, net=True, check=False)
            if pushed.returncode == 0:
                push_work(work_branch)
                head = git("rev-parse", "HEAD").stdout.strip()
                changed = git("diff", "--stat", base, head).stdout[-3000:]
                finish("done", summary=summary, commit=head, diffstat=changed, workflows_patch=workflows_patch())
            print("push to %s, attempt %d: %s" % (BRANCH, attempt + 1, pushed.stderr.strip()), flush=True)
            time.sleep(20 + 20 * attempt)
        push_work(work_branch)
        finish("failed", "could not push to %s in six attempts; last error:\n%s" % (BRANCH, pushed.stderr.strip()), summary=summary)
    except Limited as e:
        commit_leftovers()
        push_work(work_branch)
        finish("limited", "Claude usage limit reached", resets_at=e.args[0] if e.args else None)


def push_work(work_branch):
    """The ticket's branch stays on origin: a later attempt continues from it, and the owner can read it."""
    r = git("push", "-q", "-f", "origin", "HEAD:refs/heads/" + work_branch, net=True, check=False)
    if r.returncode:
        print("push of %s failed: %s" % (work_branch, r.stderr.strip()), flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if isinstance(e.code, str):
            finish("failed", e.code)
        raise
