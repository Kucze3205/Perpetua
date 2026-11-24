"""The policy scripts that shape a session's inputs (`policy/session-state.py`, `policy/session-startup.py`) run in a job of
their own that holds no token (ADR-0006), between "Prepare the session" and the session:

- `pack` (with the token): the engine's code, `policy/` and the facts the scripts need, as the bundle that job runs;
- `run` (the job, `loop.py run-policy-scripts`): runs the scripts, applies the engine's checks to what they print and
  leaves the validated results (files for `.session/`, the bootstrap prompt) as an artifact; the job's outputs say `ok`
  or the `error`, and pin the artifact by digest (`artifact_pin`);
- `refusal` and `budget` (with the token, in "Take in results"): whether the results are accepted, whose fault a refusal is,
  and the startup budget they hold.
"""
import json
import os
import shutil
import tarfile

import artifact_pin
import common as c
import managed_resources as resources
import policy_script
import session_context
import session_startup

ERROR_CHARS = 300
BUDGET_BYTES = 1024 * 1024


def pack(bundle, state_facts, startup_plan):
    """`bundle`: the engine's code (what the job imports), `policy/` (the scripts and the data they read) and the plan. A part
    with no script to run is None."""
    shutil.copytree(".github/loop", os.path.join(bundle, "engine"), ignore=shutil.ignore_patterns("tests", "data", "__pycache__"))
    shutil.copytree(resources.ROOT, os.path.join(bundle, resources.ROOT), symlinks=True)
    with open(os.path.join(bundle, "plan.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"state": state_facts, "startup": startup_plan}, fh)


def run(bundle, work, out):
    """The job's command: `bundle` and `work` are the unpacked artifacts, `out` is where the results are left."""
    with open(os.path.join(bundle, "plan.json"), encoding="utf-8") as fh:
        plan = json.load(fh)
    session = os.path.join(work, ".session")
    os.makedirs(out)
    try:
        written = session_context.settle(bundle, plan["state"], session) if plan["state"] is not None else []
        got = session_startup.settle(bundle, work, plan["startup"]) if plan["startup"] else None
        if got:
            with open(os.path.join(out, "prompt.md"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(got[0])
            session_context.write(session, "startup.json", got[1])
            written.append("startup.json")
    except policy_script.PolicyScriptError as error:
        c.set_output(ok="false", error=str(error)[:ERROR_CHARS])
        return
    with tarfile.open(os.path.join(out, "session.tar.gz"), "w:gz") as tar:
        for path in written:
            tar.add(os.path.join(session, path), arcname=path, recursive=False)
    c.set_output(ok="true", digest=artifact_pin.digest(out))


def refusal(ok, error, directory, digest):
    """(why "Take in results" refuses what the policy scripts' job left, whether the policy is at fault); (None, False): it
    takes it in. The policy is at fault only when the job ran the scripts and said no (`ok` false). A job that left no
    verdict (it never ran, or died) or an artifact that did not arrive as pinned is GitHub's failure: refused all the same."""
    if ok == "false":
        return (error or "the policy scripts' output was refused")[:ERROR_CHARS], True
    if ok != "true":
        return "the job that runs the policy scripts left no result", False
    try:
        artifact_pin.verify(directory, digest)
    except (OSError, ValueError):
        return "the policy script results are not the ones that job pinned", False
    return None, False


def budget(directory):
    """The startup budget in the results (None: the session had no startup script, or what is there is not a budget)."""
    try:
        with tarfile.open(os.path.join(directory, "session.tar.gz")) as tar:
            value = json.loads(tar.extractfile("startup.json").read(BUDGET_BYTES))
    except (OSError, KeyError, ValueError, tarfile.TarError):
        return None
    return value if session_startup.budget_ok(value) else None
