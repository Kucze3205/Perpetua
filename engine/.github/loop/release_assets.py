"""Large session outputs live in release assets: the epilogue uploads them and lists each one in the
report; the next session's stage fetches the listed ones back.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile

import common as c
import session_security as security


ARTIFACT_LINE = re.compile(r"^- artifact: (\S+) \| release: (\S+) \| size: (\d+) \| sha256: ([0-9a-f]{64})$", re.M)


def upload_artifacts(n, work, k):
    """Files the session left in `.session/artifacts/` go to the release `iter-NNNN`; the manifest goes into the report."""
    try:
        # Check ancestors too: a directory link can resolve only on the host
        # after the namespace ends, exposing files the worker could not read.
        security.check_session_directory(work)
    except SystemExit:
        print("Session artifacts refused: redirected output")
        return 0
    d = os.path.join(work, ".session", "artifacts")
    if not os.path.isdir(d):
        return 0
    rel = "iter-%04d" % k
    c.gh("release", "create", rel, "--title", rel, "--notes", "Artifacts of iteration %04d" % k, "--target", c.default_branch(), check=False)
    lines = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.islink(p) or not os.path.isfile(p) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            continue
        asset = "%s-%s" % (n, name)
        tmp = os.path.join(tempfile.mkdtemp(), asset)
        shutil.copy(p, tmp)
        c.gh("release", "upload", rel, tmp, "--clobber")
        with open(p, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        lines.append("- artifact: %s | release: %s/%s | size: %s | sha256: %s" % (name, rel, asset, os.path.getsize(p), sha))
    if lines:
        c.update_report(n, {"artifacts": len(lines)}, "## Artifacts\n\n" + "\n".join(lines))
    return len(lines)


def fetch_inputs(body, dest):
    """Download the release assets the task body names."""
    for ref in json.loads(c.marker(body, "inputs") or "[]"):
        rel = ref.get("release")
        if "issue" in ref:
            report = c.find_report(ref["issue"])
            for match in ARTIFACT_LINE.finditer(report["body"] if report else ""):
                if match.group(1) == ref.get("name"):
                    rel = match.group(2)
        if not rel or "/" not in rel:
            continue
        tag, asset = rel.split("/", 1)
        os.makedirs(dest, exist_ok=True)
        try:
            c.gh("release", "download", tag, "--pattern", asset, "--dir", dest, "--clobber")
        except (SystemExit, OSError):
            pass
