"""Plan one wave: the open sub-issues of the spec whose blockers are all closed, not failed, not left to the owner.
With nothing ready, post the final summary on the spec and open the integration pull request when everything built."""
import json
import os

from common import FAILED, MAX_WAVES, OWNER, PARENT, REPO, blockers, comment, ensure_labels, gh, labels, sub_issues

wave = int(os.environ["WAVE"])
ensure_labels()
subs = sub_issues()
open_ = [s for s in subs if s["state"] == "open"]
ready, waiting = [], []
for s in sorted(open_, key=lambda s: s["number"]):
    if labels(s) & {FAILED, OWNER}:
        continue
    pending = [b["number"] for b in blockers(s["number"]) if b["state"] != "closed"]
    (waiting if pending else ready).append((s["number"], pending))
if wave > MAX_WAVES:
    ready = []
numbers = [n for n, _ in ready]
print("wave %d ready: %s" % (wave, numbers))
for n, pending in waiting:
    print("  #%d waits for %s" % (n, pending))
with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
    fh.write("ready=%s\n" % json.dumps(numbers))

if not numbers and os.environ.get("DRY_RUN") != "true":
    def line(s):
        return "- #%d %s" % (s["number"], s["title"])
    done = [s for s in subs if s["state"] == "closed"]
    failed = [s for s in open_ if FAILED in labels(s)]
    owner = [s for s in open_ if OWNER in labels(s)]
    stuck = [s for s in open_ if not labels(s) & {FAILED, OWNER}]
    branch = os.environ["BRANCH"]
    parts = ["## Unattended build finished (wave %d)" % wave, "", "Integration branch: `%s`." % branch]
    for title, group in (("Built", done), ("Failed twice (label `%s`)" % FAILED, failed),
                         ("Left to the owner (`%s`)" % OWNER, owner), ("Blocked by a failure", stuck)):
        if group:
            parts += ["", "**%s**" % title] + [line(s) for s in group]
    if wave > MAX_WAVES:
        parts += ["", "Stopped at the wave limit (%d)." % MAX_WAVES]
    if done:
        url = gh("pr", "create", "--base", "main", "--head", branch, "--title", "Spec #%d: build from %s" % (PARENT, branch),
                 "--body", "Built unattended from the sub-issues of #%d. Summary on the spec." % PARENT, check=False).strip()
        parts += ["", "Pull request: %s" % (url or "not opened (create it from `%s` by hand)" % branch)]
    comment(PARENT, "\n".join(parts))
