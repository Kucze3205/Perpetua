"""Where the engine runs a bundle: a directory with `run.sh` that runs a command on data (the candidate tree, the accepted
tree, a policy script and its facts), never on a machine with the token (ADR-0006).

A bundle goes to a Tokenless run (`common.run_tokenless`) unless the job "Check the results" of this session's run already
ran the same bundle: its result stands in when the bundle has the same digest (the same trees, baseline, command and facts),
read only from that job's outputs (`accept`). So main that moved, a check nobody planned, or outputs that are missing or
do not validate all end where they ended before: in a Tokenless run.

`planning` is the other side ("Prepare the checks", `candidate_checks`): a bundle is recorded for that job instead of run,
and counts as passed, so the engine's own code goes on to the next check exactly as it will in "Take in results".
`run` is the job itself (`run-checks`): standard library only, no token, no checkout.
"""
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile

import common as c

HEAD = 64 * 1024 + 1        # what a caller reads of the start of stdout: the gate's verdict, a policy script's JSON (and one more)
TAIL = 1500                 # what the engine keeps of the end of a command's output: the error a check or a verification shows
OUTPUT_CHARS = 512 * 1024   # of the job output that carries the results (GitHub takes 1 MB per job)
PASSED = '{"verdict": "accepted"}\n'        # what a recorded bundle "printed": a passing command, gate and policy script alike
RESULTS = {}                # digest -> {"exit", "stdout", "tail"}: what "Check the results" said, accepted as data
JOB_ENV = ("GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_STATE", "GITHUB_STEP_SUMMARY")
_plan = []                  # while planning: [the plan] (bundles recorded for the job)


def digest(directory):
    """sha256 of a bundle: every path under it, its kind, the executable bit and the bytes (or a link's target)."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(directory):
        dirs.sort()
        for name in sorted(dirs + files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            if os.path.islink(path):
                h.update(("l %s %s\0" % (rel, os.readlink(path))).encode("utf-8", "surrogateescape"))
            elif os.path.isdir(path):
                h.update(("d %s\0" % rel).encode("utf-8", "surrogateescape"))
            else:
                with open(path, "rb") as fh:
                    data = hashlib.sha256(fh.read()).hexdigest()
                h.update(("f %s %d %s\0" % (rel, os.access(path, os.X_OK), data)).encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def write_out(entry):
    """A Tokenless run's output directory (`stdout.txt`, `stderr.txt`, `exit-code`) holding `entry`: what its readers read."""
    out = tempfile.mkdtemp(prefix="checked-")
    for name, text in (("stdout.txt", entry["stdout"]), ("stderr.txt", entry["tail"]), ("exit-code", entry["exit"])):
        if text is not None:
            with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    return out


def run_bundle(bundle, minutes, purpose, keep=0):
    """The output directory of `bundle/run.sh` (as `common.run_tokenless` returns it). `keep`: how much of the start of stdout
    the caller reads (0: it reads the end of stdout and stderr)."""
    if _plan:
        plan = _plan[0]
        name = "%02d" % len(plan["checks"])
        shutil.copytree(bundle, os.path.join(plan["root"], name), symlinks=True)
        plan["checks"].append({"dir": name, "minutes": minutes, "purpose": purpose, "keep": keep, "digest": digest(bundle)})
        return write_out({"stdout": PASSED, "tail": "", "exit": "0"})
    hit = RESULTS.get(digest(bundle)) if RESULTS else None
    if hit:
        c.say("%s: the result of \"Check the results\"" % purpose)
        return write_out(dict(hit, stdout=hit["stdout"][:keep]))
    return c.run_tokenless(bundle, minutes, purpose)


@contextlib.contextmanager
def planning(root):
    """Record the bundles in `root` instead of running them; yields the plan: {"root", "checks": [...]}."""
    os.makedirs(root, exist_ok=True)
    _plan[:] = [{"root": root, "checks": []}]
    try:
        yield _plan[0]
    finally:
        _plan[:] = []


def run_command(cmd, tree, timeout_minutes=60, purpose="command"):
    """A shell command from the repo (check, verification, goal gate) run on a copy of `tree` on a machine without the token.
    Returns a CompletedProcess; a job that left no output counts as a failed command."""
    bundle = tempfile.mkdtemp(prefix="bundle-")
    try:
        shutil.copytree(tree, os.path.join(bundle, "tree"), symlinks=True, ignore=shutil.ignore_patterns(".git"))
        with open(os.path.join(bundle, "run.sh"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write('cd "$(dirname "$0")/tree"\nbash -c %s\n' % shlex.quote(cmd))
        out = run_bundle(bundle, timeout_minutes, purpose)
    finally:
        shutil.rmtree(bundle, ignore_errors=True)

    def read(name):
        try:
            with open(os.path.join(out, name), encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except (OSError, TypeError):
            return None
    stdout, stderr, code = read("stdout.txt"), read("stderr.txt"), read("exit-code")
    if out:
        shutil.rmtree(out, ignore_errors=True)
    if stdout is None or code is None or not code.strip().isdigit():
        return subprocess.CompletedProcess(cmd, 1, "", "the tokenless job returned no result for the command")
    return subprocess.CompletedProcess(cmd, int(code.strip()), stdout, stderr or "")


def accept(text):
    """Take the job output `results` of "Check the results" in as data: bounded, and every entry of the shape the job writes.
    Anything else is no result (its bundles go to Tokenless runs)."""
    RESULTS.clear()
    try:
        value = json.loads(text) if text and len(text) <= OUTPUT_CHARS else {}
    except ValueError:
        value = {}
    for key, e in (value.items() if isinstance(value, dict) else ()):
        if (re.fullmatch(r"[0-9a-f]{64}", key) and isinstance(e, dict) and set(e) == {"exit", "stdout", "tail"}
                and (e["exit"] is None or isinstance(e["exit"], str) and re.fullmatch(r"\d{1,3}", e["exit"]))
                and isinstance(e["stdout"], str) and len(e["stdout"]) <= HEAD and isinstance(e["tail"], str) and len(e["tail"]) <= TAIL):
            RESULTS[key] = e


# ---------------------------------------------------------------- the job "Check the results"

def ends(path, keep):
    """(the first `keep` characters, the last TAIL characters) of a file the command wrote."""
    with open(path, "rb") as fh:
        head = fh.read(keep * 4).decode("utf-8", "replace")[:keep]
        fh.seek(max(0, os.path.getsize(path) - TAIL * 4))
        return head, fh.read().decode("utf-8", "replace")[-TAIL:]


def run(root):
    """Run the bundles of `root/plan.json` in order, as the tokenless job runs one (`bash run.sh`), until one fails (exit code,
    or a policy script's `errors`); the results go only to the job output `results`."""
    with open(os.path.join(root, "plan.json"), encoding="utf-8") as fh:
        plan = json.load(fh)
    env = {k: v for k, v in c.clean_env().items() if k not in JOB_ENV}
    results, size = {}, 2
    for item in plan:
        out = tempfile.mkdtemp(prefix="check-")
        stdout, stderr = os.path.join(out, "stdout.txt"), os.path.join(out, "stderr.txt")
        print("::group::%s" % item["purpose"])
        try:
            with open(stdout, "wb") as so, open(stderr, "wb") as se:
                code = subprocess.run(["bash", os.path.join(root, item["dir"], "run.sh")], stdout=so, stderr=se, env=env,
                                      timeout=item["minutes"] * 60).returncode
            code = str(code if code >= 0 else 128 - code)
        except subprocess.TimeoutExpired:
            code = None                 # as a tokenless job that ran out of time: no exit code
        head, tail = ends(stdout, item["keep"])[0], (ends(stdout, 0)[1] + ends(stderr, 0)[1])[-TAIL:]
        print("exit %s\n%s\n::endgroup::" % (code, tail))
        entry = {"exit": code, "stdout": head, "tail": tail}
        size += len(json.dumps({item["digest"]: entry})) + 1
        if size > OUTPUT_CHARS:
            break
        results[item["digest"]] = entry
        if code != "0" or refused(head):
            break
    c.set_output(results=json.dumps(results))


def refused(stdout):
    """A policy script's refusal: what follows it is not needed."""
    try:
        value = json.loads(stdout)
    except ValueError:
        return False
    return isinstance(value, dict) and bool(value.get("errors"))
