"""Shared helpers of the unattended spec build (`gh` with GH_TOKEN, labels)."""
import json
import os
import subprocess

REPO = os.environ["GH_REPO"]
PARENT = int(os.environ["PARENT"])
FAILED, RETRIED, OWNER = "build:failed", "build:retried", "needs-owner"
MAX_WAVES = 60


def gh(*args, inp=None, check=True):
    r = subprocess.run(["gh", *args], input=inp, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit("gh %s failed: %s" % (" ".join(args[:3]), r.stderr.strip()))
    return r.stdout


def api(path, *args):
    return json.loads(gh("api", path, *args) or "null")


def labels(issue):
    return {l["name"] for l in issue.get("labels", [])}


def sub_issues():
    return api("repos/%s/issues/%d/sub_issues?per_page=100" % (REPO, PARENT))


def blockers(number):
    return api("repos/%s/issues/%d/dependencies/blocked_by?per_page=100" % (REPO, number))


def comment(number, body):
    gh("issue", "comment", str(number), "--body-file", "-", inp=body)


def ensure_labels():
    for name, color in ((FAILED, "B60205"), (RETRIED, "FBCA04"), (OWNER, "5319E7")):
        gh("label", "create", name, "--color", color, "--force", check=False)
