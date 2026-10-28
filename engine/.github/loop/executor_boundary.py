"""Data-only admission of a session's output (never trust worker Git).

Admission reconstructs a child of the session's baseline using regular file
bytes; no worker Git program/config/hook is run.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


# Empty placeholders the `claude` CLI's bwrap sandbox leaves in its cwd (its `denyWrite` list: mount points for paths
# that did not exist), appearing ~0.8 s after the agent starts. Source: the CLI binary, versions 2.1.284 (the default pin of
# `cli_version`, see session.yml) and 2.1.303 carry the identical list (cwd-relative: bunfig.toml package.json .npmrc .yarnrc .yarnrc.yml
# .gitmodules package-lock.json yarn.lock pnpm-lock.yaml, plus the eight .env* names). Empty directories among them
# (node_modules/.bin, .claude/agents, .claude/commands) are never candidate files. When the pin changes, re-extract
# the list: `npm pack @anthropic-ai/claude-code-linux-x64@<pin>` and search `claude` for ".yarnrc.yml".
SANDBOX_STUBS = frozenset((".env", ".env.local", ".env.development", ".env.development.local", ".env.test",
                           ".env.test.local", ".env.production", ".env.production.local", ".npmrc", ".yarnrc",
                           ".yarnrc.yml", "bunfig.toml", "package.json", "package-lock.json", "yarn.lock",
                           "pnpm-lock.yaml", ".gitmodules"))


def environment():
    # Do not inherit GIT_CONFIG_*, hooks, Python startup, shell injection or tokens.
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/home/executor",
            "LD_LIBRARY_PATH": "/usr/local/lib",
            "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "executor", "GIT_AUTHOR_EMAIL": "executor@localhost",
            "GIT_COMMITTER_NAME": "executor", "GIT_COMMITTER_EMAIL": "executor@localhost"}


def git(root, *args, data=None, env=None):
    clean = environment()
    if os.name == "nt":
        clean["PATH"] = os.environ["PATH"]
        clean["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")
    clean["HOME"] = tempfile.gettempdir()
    if env:
        clean.update(env)
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                           "-C", str(root), *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=clean, check=True).stdout


def files(work, skip=lambda rel: False):
    """Reject redirects and special files before reading any candidate bytes; `skip` paths are neither checked nor read."""
    result = {}
    for root, dirs, names in os.walk(work, followlinks=False):
        dirs[:] = [name for name in dirs if name not in (".git", ".session")]
        for name in dirs + names:
            path = Path(root, name)
            rel = path.relative_to(work).as_posix()
            if rel.startswith((".git/", ".session/")) or rel in (".git", ".session") or skip(rel):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
                raise ValueError("executor output contains a redirect or special file")
            result[rel] = (path.read_bytes(), "100755" if mode & 0o111 else "100644")
    return result


def record(work, trusted):
    """Staging without a sanitized snapshot: the session's baseline, written before any untrusted process."""
    names = git(work, "ls-tree", "-rz", "--name-only", "--full-tree", "HEAD").split(b"\0")
    Path(trusted, "executor-baseline.json").write_text(json.dumps({
        "baseline": git(work, "rev-parse", "HEAD").decode().strip(),
        "paths": [n.decode("utf-8") for n in names if n], "work": os.path.abspath(work)}), encoding="utf-8")


def import_candidate(work, metadata, trusted, ref, skip=lambda rel, tracked: False):
    """Write objects/index in the engine repo, without consulting worker .git: one commit on the baseline from the file
    bytes the session left, whether or not it committed. Environment debris never reaches any check: `skip(rel, tracked)`
    by path, before the file is inspected or read. A sandbox stub is empty and was not in the baseline: nothing the
    agent wrote; content or a baseline file makes it real."""
    if os.path.abspath(work) != metadata["work"]:
        raise ValueError("executor output does not match its staged origin")
    tracked = set(metadata["paths"])
    current = {rel: v for rel, v in files(Path(work), lambda rel: skip(rel, rel in tracked)).items()
               if not (rel in SANDBOX_STUBS and not v[0] and rel not in tracked)}
    with tempfile.TemporaryDirectory(prefix="executor-admission-") as tmp:
        index = os.path.join(tmp, "index")
        env = {"GIT_INDEX_FILE": index}
        git(trusted, "read-tree", metadata["baseline"], env=env)
        for rel in tracked - set(current):
            git(trusted, "update-index", "--force-remove", "--", rel, env=env)
        for rel, (data, mode) in current.items():
            # Hidden additions must reach protected/write checks, not be discarded.
            oid = git(trusted, "hash-object", "-w", "--stdin", data=data).decode().strip()
            git(trusted, "update-index", "--add", "--cacheinfo", mode, oid, rel, env=env)
        tree = git(trusted, "write-tree", env=env).decode().strip()
        commit = git(trusted, "commit-tree", tree, "-p", metadata["baseline"], data=b"Session output admitted as data\n",
                     # fixed dates: the same output is the same commit, so "the head did not move" is observable
                     env={"GIT_AUTHOR_DATE": "@0 +0000", "GIT_COMMITTER_DATE": "@0 +0000"}).decode().strip()
    git(trusted, "update-ref", ref, commit)
    return commit



