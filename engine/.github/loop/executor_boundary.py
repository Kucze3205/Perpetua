"""Private executor filesystem and data-only admission (never trust worker Git).

The worker repository has a new root commit and no alternates, remotes, host
configuration or original objects. Its root filesystem is an allowlist, not a
read-only view of the host. Admission reconstructs a child of the original
baseline using regular file bytes; no worker Git program/config/hook is run.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


HIDDEN = {"policy", "execution", "templates", ".github", ".git", ".claude", ".codex", "iterations", "audit"}


def visible(path):
    return path.split("/", 1)[0] not in HIDDEN and path != "engine.yml"


def environment():
    # Do not inherit GIT_CONFIG_*, hooks, Python startup, shell injection or tokens.
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/home/executor",
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


def prepare(work, trusted):
    """Run only after recipient input filtering, before any untrusted process."""
    baseline = git(work, "rev-parse", "HEAD").decode().strip()
    entries = git(work, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0")
    manifest = []
    with tempfile.TemporaryDirectory(prefix="executor-snapshot-") as tmp:
        for entry in entries:
            if not entry:
                continue
            metadata, raw = entry.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            rel = raw.decode("utf-8")
            if not visible(rel) or rel.startswith(".session/"):
                continue
            if mode not in (b"100644", b"100755") or kind != b"blob":
                raise SystemExit("executor snapshot requires regular files")
            destination = Path(tmp, rel)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(git(work, "cat-file", "blob", oid.decode()))
            destination.chmod(0o755 if mode == b"100755" else 0o644)
            manifest.append(rel)
        # Staging already validates the complete recipient view against redirects.
        if Path(work, ".session").exists():
            shutil.copytree(Path(work, ".session"), Path(tmp, ".session"))
        for entry in Path(work).iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        shutil.copytree(tmp, work, dirs_exist_ok=True)
    git(work, "init", "-q", "-b", "executor")
    Path(work, ".git", "info", "exclude").write_text(".session/\n", encoding="utf-8")
    if manifest:
        # Original tracked files remain tracked even when current ignores match.
        # Feed explicit paths as NUL data, without admitting staged .session data.
        git(work, "add", "-f", "--pathspec-from-file=-", "--pathspec-file-nul",
            data=b"\0".join(path.encode("utf-8") for path in manifest) + b"\0")
    git(work, "commit", "-q", "--allow-empty", "-m", "Executor input snapshot")
    Path(trusted, "executor-baseline.json").write_text(json.dumps({
        "baseline": baseline, "paths": manifest, "work": os.path.abspath(work)}), encoding="utf-8")
    Path(trusted, "executor-mode").write_text("isolated\n", encoding="utf-8")


def files(work):
    """Reject redirects and special files before reading any candidate bytes."""
    result = {}
    for root, dirs, names in os.walk(work, followlinks=False):
        dirs[:] = [name for name in dirs if name not in (".git", ".session")]
        for name in dirs + names:
            path = Path(root, name)
            rel = path.relative_to(work).as_posix()
            if rel.startswith((".git/", ".session/")) or rel in (".git", ".session"):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode) or path.stat().st_nlink != 1:
                raise ValueError("executor output contains a redirect or special file")
            result[rel] = (path.read_bytes(), "100755" if mode & 0o111 else "100644")
    return result


def import_candidate(work, metadata, trusted, ref):
    """Write objects/index in the engine repo, without consulting worker .git."""
    if os.path.abspath(work) != metadata["work"]:
        raise ValueError("executor output does not match its staged origin")
    current = files(Path(work))
    with tempfile.TemporaryDirectory(prefix="executor-admission-") as tmp:
        index = os.path.join(tmp, "index")
        env = {"GIT_INDEX_FILE": index}
        git(trusted, "read-tree", metadata["baseline"], env=env)
        for rel in set(metadata["paths"]) - set(current):
            git(trusted, "update-index", "--force-remove", "--", rel, env=env)
        for rel, (data, mode) in current.items():
            # Hidden additions must reach protected/write checks, not be discarded.
            oid = git(trusted, "hash-object", "-w", "--stdin", data=data).decode().strip()
            git(trusted, "update-index", "--add", "--cacheinfo", mode, oid, rel, env=env)
        tree = git(trusted, "write-tree", env=env).decode().strip()
        commit = git(trusted, "commit-tree", tree, "-p", metadata["baseline"],
                     data=b"Executor output admitted as data\n").decode().strip()
    git(trusted, "update-ref", ref, commit)
    return commit


def runtime_files():
    """Resolve a small trusted runtime, never expose a host directory wholesale.

    The isolated interpreter probe enumerates individual imported module files;
    arbitrary files added beside executables, libraries or stdlib stay private.
    Extend the explicit program/module lists when supporting additional tools.
    """
    programs = ("bash", "sh", "git", "python3", "cat", "mkdir", "dirname", "echo",
                "ln", "rm", "cp", "mv", "chmod", "touch", "ls", "find", "sort",
                "head", "tail", "wc", "cut", "tr", "sed", "grep", "printf", "env")
    paths = set()
    for name in programs:
        executable = shutil.which(name, path=environment()["PATH"])
        if not executable:
            raise RuntimeError("required executor runtime unavailable: " + name)
        paths.add(executable)
    probe = '''import ctypes, socket, subprocess, pathlib, json, shutil, tempfile, platform
import hashlib, math, random, datetime, collections, itertools, functools, typing
import encodings.utf_8, encodings.ascii, encodings.latin_1
import sys
print(json.dumps(sorted({m.__file__ for m in sys.modules.values()
                        if getattr(m, '__file__', None)})))'''
    python = shutil.which("python3", path=environment()["PATH"])
    result = subprocess.run([python, "-I", "-S", "-c", probe], cwd="/", env=environment(),
                            capture_output=True, check=True, text=True)
    paths.update(json.loads(result.stdout))
    # ldd is run only by the trusted engine on explicitly selected runtime files.
    # Every dependency becomes an individual bind; no lib directory is admitted.
    for path in list(paths):
        with open(path, "rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        result = subprocess.run(["ldd", path], cwd="/", env=environment(),
                                capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            if "not found" in line:
                raise RuntimeError("executor runtime dependency unavailable")
            for word in line.split():
                if word.startswith("/"):
                    paths.add(word)
    if not all(Path(path).is_file() for path in paths):
        raise RuntimeError("executor runtime requires regular files")
    return sorted(paths)


def launch(work, trusted, online=False):
    if sys.platform != "linux" or not shutil.which("bwrap"):
        raise RuntimeError("required executor filesystem isolation unavailable")
    # OS directory names are not a trust boundary: host policy copies may live
    # under /usr or beside Python. Admit only the explicit runtime file closure.
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
    if online:
        command += ["--share-net"]
    for path in runtime_files():
        command += ["--ro-bind", path, path]
    command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home/executor",
                "--bind", os.path.abspath(work), "/work", "--dir", "/engine",
                "--ro-bind", os.path.join(trusted, "command.sh"), "/engine/command.sh",
                "--ro-bind", os.path.join(trusted, "offline_exec.py"), "/engine/offline_exec.py",
                "--chdir", "/work"]
    command += ["python3", "/engine/offline_exec.py", "bash", "/engine/command.sh"] if not online else ["bash", "/engine/command.sh"]
    return subprocess.run(command, env=environment()).returncode


if __name__ == "__main__":
    try:
        sys.exit(launch(sys.argv[1], sys.argv[2]))
    except (OSError, RuntimeError):
        print("Required executor filesystem isolation unavailable", file=sys.stderr)
        sys.exit(1)
