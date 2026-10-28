"""Tell the ticket how its build ended. Done closes it; a first failure is retried in a later wave (from the pushed
build/issue-N branch, with this comment in the brief); a second failure labels it build:failed; a usage limit only
waits. A worker that left no status (crash, timeout) counts as a failure."""
import json
import os

from common import FAILED, RETRIED, api, comment, gh, labels, REPO

ISSUE = int(os.environ["ISSUE"])
path = os.path.join(os.environ["RUNNER_TEMP"], "out", "status.json")
try:
    with open(path) as fh:
        st = json.load(fh)
except (OSError, ValueError):
    st = {"status": "failed", "reason": "the worker left no status (crash or step timeout)"}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(dict(st, issue=ISSUE), fh)

run = os.environ["RUN_URL"]
summary = (st.get("summary") or "").strip()
if st["status"] == "done":
    body = "Built in %s and merged into the integration branch as %s.\n\n%s\n\n```\n%s\n```" % (
        run, st.get("commit", "?")[:12], summary, st.get("diffstat", ""))
    if st.get("workflows_patch"):
        body += ("\n\n**For the owner:** the build cannot push changes to `.github/workflows/`, so this ticket's change there "
                 "is not on the integration branch. Apply it with `git apply`:\n\n```diff\n%s\n```" % st["workflows_patch"])
    comment(ISSUE, body)
    gh("issue", "close", str(ISSUE), "--reason", "completed")
elif st["status"] == "limited":
    comment(ISSUE, "Build paused by the Claude usage limit (%s); the work so far is on `build/issue-%d` and continues in "
                   "a later wave." % (run, ISSUE))
else:
    current = labels(api("repos/%s/issues/%d" % (REPO, ISSUE)))
    second = RETRIED in current
    gh("issue", "edit", str(ISSUE), "--add-label", FAILED if second else RETRIED)
    comment(ISSUE, "Build attempt %s **%s** (%s). %s\n\nReason:\n```\n%s\n```\n\n%s" % (
        "2" if second else "1", st["status"], run,
        "Labelled `%s`; dependants stay blocked." % FAILED if second else "It is retried in a later wave from `build/issue-%d`." % ISSUE,
        st.get("reason", "")[-3500:], ("Session summary:\n" + summary) if summary else ""))
