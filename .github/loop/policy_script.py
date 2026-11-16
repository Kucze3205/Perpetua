"""A policy script's run and what the engine accepts from it: the same checks wherever the script ran, in the tokenless job
(`iteration.run_policy_script`) or in the job that prepares a session's inputs (`policy_inputs`).

Standard library only: this code also runs on a machine that holds no token and no installed packages.
"""
import json
import os
import shutil
import subprocess

OUTPUT_BYTES = 4096      # what the engine accepts from a policy script
MINUTES = 10


class PolicyScriptError(Exception):
    """A policy script returned data the engine refuses."""


def stage(directory, script, facts):
    """`script` (a file) and its `facts` as `script` and `facts.json` in `directory`: what `python3 script facts.json` runs on."""
    shutil.copy(script, os.path.join(directory, "script"))
    with open(os.path.join(directory, "facts.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(facts, fh)


def parse(name, code, text, max_bytes):
    """The JSON object that `policy/<name>` printed (`text`, its first `max_bytes` + 1 characters) when it exited with `code`.
    Anything else - a failed run, over `max_bytes`, not one JSON object - raises PolicyScriptError."""
    if code != "0":
        raise PolicyScriptError("policy/%s exited with %s" % (name, code))
    if len(text) > max_bytes:
        raise PolicyScriptError("policy/%s printed more than %s bytes" % (name, max_bytes))
    try:
        value = json.loads(text)
    except ValueError:
        raise PolicyScriptError("policy/%s did not print JSON" % name)
    if not isinstance(value, dict):
        raise PolicyScriptError("policy/%s did not print a JSON object" % name)
    return value


def run(directory, name, facts, max_bytes=OUTPUT_BYTES):
    """Run `directory/policy/<name>` here, in `directory`, on `facts`. None: no such script. The rest as `parse`."""
    path = os.path.join(directory, "policy", name)
    if not os.path.isfile(path):
        return None
    stage(directory, path, facts)
    printed = os.path.join(directory, "stdout.txt")
    try:
        with open(printed, "wb") as fh:
            code = subprocess.run(["python3", "script", "facts.json"], cwd=directory, stdout=fh, stderr=subprocess.DEVNULL,
                                  timeout=MINUTES * 60).returncode
    except subprocess.TimeoutExpired:
        raise PolicyScriptError("policy/%s ran longer than %s minutes" % (name, MINUTES))
    with open(printed, encoding="utf-8", errors="replace") as fh:
        return parse(name, str(code), fh.read(max_bytes + 1), max_bytes)
