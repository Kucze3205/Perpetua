#!/usr/bin/env python3
"""Three-way update of a loop's engine copy, run in the loop repository.

The branch `perpetua/engine` holds the untouched engine as Perpetua shipped it: one commit per installed version,
the tree equal to the plugin's `engine/` (the loop root layout). An update merges the plugin's current engine into
HEAD with that commit as the merge base, so local changes (fork fixes, the Policymaker's definitions) survive and
only real collisions become conflicts.

  engine_update.py snapshot ENGINE_DIR             commit ENGINE_DIR as the base; prints the commit (setup uses it)
  engine_update.py merge ENGINE_DIR [--source URL]  merge into the work tree; prints a JSON report

`merge` leaves the result staged and uncommitted, with conflict markers in the files it lists. It never pushes.
A loop without `perpetua/engine` (set up before this existed) gets its base found once: the Perpetua commit whose
`engine/` matches the loop's setup commit (`Perpetua engine <version>`), fetched from --source.
"""
import json
import os
import subprocess
import sys
import tempfile

BRANCH = "perpetua/engine"
SOURCE = "https://github.com/Kucze3205/Perpetua"
SOURCE_REF = "refs/perpetua-source/main"
IDENTITY = {"GIT_AUTHOR_NAME": "Perpetua", "GIT_AUTHOR_EMAIL": "perpetua@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "Perpetua", "GIT_COMMITTER_EMAIL": "perpetua@users.noreply.github.com"}
DEFINITIONS = ("execution/", "policy/", "engine.yml", "goal/")   # a change needs a new setup-baseline


def git(*args, env=None, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True, env=env)
    if check and r.returncode:
        sys.exit("git %s failed: %s" % (" ".join(args[:3]), r.stderr.strip()))
    return r.stdout.strip()


def rev(name):
    r = subprocess.run(["git", "rev-parse", "-q", "--verify", name + "^{commit}"], capture_output=True, text=True)
    return r.stdout.strip() or None


def version_of(tree_ish):
    r = subprocess.run(["git", "show", tree_ish + ":ENGINE_VERSION"], capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def snapshot(engine_dir, parent=None):
    """A commit whose tree is exactly engine_dir (LF line endings, no bytecode)."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
        git("-c", "core.autocrlf=input", "--work-tree", os.path.abspath(engine_dir), "add", "-A", "--", ".",
            ":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc", env=env)
        tree = git("write-tree", env=env)
    version = open(os.path.join(engine_dir, "ENGINE_VERSION"), encoding="utf-8").read().strip()
    if parent and git("rev-parse", parent + "^{tree}") == tree:
        return parent
    return git("commit-tree", tree, *(["-p", parent] if parent else []), "-m", "Perpetua engine %s" % version,
               env=dict(os.environ, **IDENTITY))


def bootstrap(source):
    """The base for a loop without `perpetua/engine` (set up before it existed): the Perpetua commit whose `engine/`
    the setup commit copied. Hand-ported fixes since then merge as local changes: identical ones cleanly, the rest as
    conflicts. A newer base would read missing upstream fixes as deliberate local reverts and drop them silently."""
    setup = git("log", "--reverse", "--format=%H", "--grep=^Perpetua engine ", "HEAD").split("\n")[0]
    if not setup:
        sys.exit("no `Perpetua engine <version>` commit in this repository: is this a Perpetua loop?")
    git("fetch", "-q", "--no-tags", source, "+refs/heads/main:" + SOURCE_REF)
    copy = blobs(setup)
    best = None
    for c in git("log", "--format=%H", SOURCE_REF, "--", "engine").split():
        off = sum(1 for path, oid in blobs(c + ":engine").items() if copy.get(path) != oid)
        if best is None or off < best[0]:
            best = (off, c)
        if not off:
            break
    git("update-ref", "-d", SOURCE_REF)
    if best is None:
        sys.exit("no engine history in %s" % source)
    off, c = best
    base = git("commit-tree", git("rev-parse", c + ":engine"),
               "-m", "Perpetua engine %s (base found at Perpetua %s)" % (version_of(c + ":engine"), c[:12]),
               env=dict(os.environ, **IDENTITY))
    return base, {"setup_commit": setup, "perpetua_commit": c, "files_differing_from_setup_copy": off}


def blobs(tree_ish):
    """path -> blob id of every file in tree_ish."""
    out = {}
    for line in git("ls-tree", "-r", tree_ish).splitlines():
        meta, path = line.split("\t", 1)
        out[path] = meta.split()[2]
    return out


def merge(engine_dir, source):
    if git("status", "--porcelain"):
        sys.exit("the work tree is not clean")
    if not os.path.isfile("ENGINE_VERSION"):
        sys.exit("no ENGINE_VERSION here: run this in the loop repository root")
    found = None
    base = rev("origin/" + BRANCH) or rev(BRANCH)
    if not base:
        base, found = bootstrap(source)
    new = snapshot(engine_dir, parent=base)
    report = {"from": version_of(base), "to": version_of(new), "base": base, "snapshot": new, "bootstrapped": found}
    if new == base:
        report["status"] = "up-to-date"
        return report
    r = subprocess.run(["git", "merge-tree", "--write-tree", "--name-only", "--merge-base=" + base, "HEAD", new],
                       capture_output=True, text=True)
    if r.returncode not in (0, 1):
        sys.exit("git merge-tree failed: " + r.stderr.strip())
    lines = r.stdout.split("\n")
    merged = lines[0].strip()
    conflicts = []
    for line in lines[1:]:
        if not line.strip():
            break
        conflicts.append(line.strip())
    git("read-tree", "-m", "-u", "HEAD", merged)
    changed = git("diff", "--cached", "--name-only", "HEAD").split()
    report.update(status="conflicts" if conflicts else "merged", conflicts=sorted(set(conflicts)),
                  workflows=[p for p in changed if p.startswith(".github/workflows/")],
                  definitions=[p for p in changed if p.startswith(DEFINITIONS)],
                  engine=[p for p in changed if not p.startswith((".github/workflows/",) + DEFINITIONS)])
    return report


def main(argv):
    if len(argv) >= 2 and argv[0] == "snapshot":
        print(snapshot(argv[1], parent=rev("origin/" + BRANCH) or rev(BRANCH)))
    elif len(argv) >= 2 and argv[0] == "merge":
        source = argv[argv.index("--source") + 1] if "--source" in argv else SOURCE
        print(json.dumps(merge(argv[1], source), indent=2))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
