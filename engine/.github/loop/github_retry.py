"""A passing GitHub failure is asked again, never taken for GitHub's answer: one HTTP 500 must not stop the loop.

`gh` names the status of a refused API call as `HTTP <code>`. A 5xx, or no status at all (no answer: a lost connection), is
GitHub's failure and passes. Any other status (404, 409, 422) is an answer the engine's callers rely on, so a call is
asked again only for the first.

Standard library only.
"""
import re
import subprocess
import time

READ_PAUSES_S = (2, 8)


def passing(stderr):
    """GitHub did not answer, or answered 5xx: the same call may succeed in a moment."""
    codes = re.findall(r"HTTP (\d{3})", stderr or "")
    return not codes or codes[-1].startswith("5")


def run(args, inp=None, pauses=READ_PAUSES_S, again=passing):
    """`gh *args` (stdin `inp` on every attempt), asked again after each of `pauses` while it fails and `again(stderr)`
    holds. The last attempt's CompletedProcess."""
    for pause in (*pauses, None):
        r = subprocess.run(["gh", *args], input=inp, capture_output=True, text=True)
        if r.returncode == 0 or pause is None or not again(r.stderr):
            return r
        time.sleep(pause)
