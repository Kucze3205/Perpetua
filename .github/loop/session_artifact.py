"""What leaves the tokenless session job and how the epilogue takes it in (ADR-0006).

The session job packs its results into one artifact: status files (`status/`), the tree it worked in (`work.tar`) and its
commits (`commits.bundle`). The epilogue treats all of it as data: it unpacks only regular files, directories and links
that it never follows, and refuses a path that escapes, passes through a link, or a file over the size cap.
"""
import os
import shutil
import stat
import subprocess
import tarfile

STATUS = ("agent-exit", "agent-start", "agent-end", "session-start", "claude-execution-output.json", "claude-stderr.txt",
          "script-output.txt", "secret-missing", "stream.ndjson")
EXTRA = ("capture.tgz",)         # the E2E capture, stored by the epilogue as it is
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024


def pack(work, tmp, out):
    """On the session's machine: `tmp` holds the status files, `work` is the agent's checkout."""
    os.makedirs(os.path.join(out, "status"), exist_ok=True)
    for name in STATUS:
        if os.path.isfile(os.path.join(tmp, name)):
            shutil.copy(os.path.join(tmp, name), os.path.join(out, "status", name))
    for name in EXTRA:
        if os.path.isfile(os.path.join(tmp, name)):
            shutil.copy(os.path.join(tmp, name), os.path.join(out, name))
    with tarfile.open(os.path.join(out, "work.tar"), "w") as tar:
        for entry in sorted(os.listdir(work)):
            if entry != ".git":
                tar.add(os.path.join(work, entry), arcname=entry)
    # only what origin does not have; an empty range makes no bundle
    subprocess.run(["git", "-C", work, "bundle", "create", os.path.join(out, "commits.bundle"), "--branches", "--not", "--remotes"],
                   capture_output=True, check=False)


def receive_status(artifact, tmp):
    """Copy the status files into `tmp`; a file that is not a small regular file is left out."""
    for name in STATUS + EXTRA:
        path = os.path.join(artifact, "status", name) if name in STATUS else os.path.join(artifact, name)
        if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE:
            continue
        shutil.copy(path, os.path.join(tmp, name))


def receive_tree(artifact, work):
    """Unpack `work.tar` into `work`. Raises ValueError for anything that is not plain data."""
    archive = os.path.join(artifact, "work.tar")
    if os.path.islink(archive) or not os.path.isfile(archive):
        raise ValueError("the session left no tree")
    root = os.path.realpath(work)
    os.makedirs(root)
    total = 0
    with tarfile.open(archive) as tar:
        for member in tar:
            parts = member.name.split("/")
            if member.name.startswith("/") or ".." in parts or not member.name or "\\" in member.name:
                raise ValueError("the session tree has a path that escapes: " + member.name)
            target = os.path.join(root, *parts)
            for depth in range(1, len(parts)):          # a parent that is a link would redirect the write
                if os.path.islink(os.path.join(root, *parts[:depth])):
                    raise ValueError("the session tree writes through a link: " + member.name)
            if member.isdir():
                os.makedirs(target, exist_ok=True)
            elif member.isreg():
                total += member.size
                if member.size > MAX_FILE or total > MAX_TOTAL:
                    raise ValueError("the session tree is oversized: " + member.name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as fh:
                    shutil.copyfileobj(tar.extractfile(member), fh)
                os.chmod(target, 0o755 if member.mode & stat.S_IXUSR else 0o644)
            elif member.issym():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.symlink(member.linkname, target)      # never followed: admission refuses it
            elif member.islnk():
                source = os.path.join(root, *member.linkname.split("/"))
                if ".." in member.linkname.split("/") or member.linkname.startswith("/") or not os.path.isfile(source):
                    raise ValueError("the session tree has a bad hard link: " + member.name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.link(source, target)                  # admission refuses a file with two names
            else:
                raise ValueError("the session tree has a special file: " + member.name)


def receive_commits(artifact, repo, namespace):
    """Fetch the session's commits from its bundle into `refs/session/<namespace>/` of `repo`. They are kept to be read,
    never merged: the candidate is the data import of the tree. Returns whether there were any."""
    bundle = os.path.join(artifact, "commits.bundle")
    if os.path.islink(bundle) or not os.path.isfile(bundle):
        return False
    r = subprocess.run(["git", "-C", repo, "-c", "transfer.fsckObjects=true", "fetch", "-q", "--no-tags", bundle,
                        "+refs/heads/*:refs/session/%s/*" % namespace], capture_output=True, check=False)
    return r.returncode == 0


if __name__ == "__main__":
    import sys
    pack(*sys.argv[2:5])         # pack <work> <tmp> <out>
