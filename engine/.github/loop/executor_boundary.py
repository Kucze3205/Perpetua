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


HIDDEN = {"policy", "execution", ".github", ".git", ".claude", ".codex", "iterations", "audit"}


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


def visible(path):
    return path.split("/", 1)[0] not in HIDDEN and path != "engine.yml"


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
                if os.name == "nt":
                    def remove_readonly(func, path, exc):
                        if not isinstance(exc[1], PermissionError):
                            raise exc[1]
                        os.chmod(path, stat.S_IWRITE)
                        func(path)
                    shutil.rmtree(entry, onerror=remove_readonly)
                else:
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


def record(work, trusted):
    """Policy-layer staging: the session's baseline, written before any untrusted process (the tree is not rebuilt)."""
    names = git(work, "ls-tree", "-rz", "--name-only", "--full-tree", "HEAD").split(b"\0")
    Path(trusted, "policy-baseline.json").write_text(json.dumps({
        "baseline": git(work, "rev-parse", "HEAD").decode().strip(),
        "paths": [n.decode("utf-8") for n in names if n], "work": os.path.abspath(work)}), encoding="utf-8")


def admit(work, metadata, trusted, ref, message, ignore):
    """Write objects/index in the engine repo, without consulting worker .git: one commit on the baseline from the file
    bytes the session left. `ignore(rel, data, tracked)` drops environment debris before it reaches any check."""
    if os.path.abspath(work) != metadata["work"]:
        raise ValueError("executor output does not match its staged origin")
    tracked = set(metadata["paths"])
    current = {rel: v for rel, v in files(Path(work)).items() if not ignore(rel, v[0], rel in tracked)}
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
        commit = git(trusted, "commit-tree", tree, "-p", metadata["baseline"], data=message.encode(),
                     # fixed dates: the same output is the same commit, so "the head did not move" is observable
                     env={"GIT_AUTHOR_DATE": "@0 +0000", "GIT_COMMITTER_DATE": "@0 +0000"}).decode().strip()
    git(trusted, "update-ref", ref, commit)
    return commit


def import_candidate(work, metadata, trusted, ref):
    # A stub is empty and was not in the baseline: nothing the agent wrote. Content or a baseline file makes it real.
    return admit(work, metadata, trusted, ref, "Executor output admitted as data\n",
                 lambda rel, data, tracked: rel in SANDBOX_STUBS and not data and not tracked)


def runtime_files(extra=()):
    """Resolve a small trusted runtime, never expose a host directory wholesale.

    The isolated interpreter probe exercises the guard and enumerates imported module files;
    arbitrary files added beside executables, libraries or stdlib stay private.
    Extend the explicit program/module lists when supporting additional tools.
    """
    programs = ("bash", "sh", "git", "python3", "cat", "mkdir", "dirname", "echo",
                "ln", "rm", "cp", "mv", "chmod", "touch", "ls", "find", "sort",
                "head", "tail", "wc", "cut", "tr", "sed", "grep", "printf", "env")
    paths = set()
    for name in (*programs, *extra):
        executable = shutil.which(name, path=environment()["PATH"])
        if not executable:
            raise RuntimeError("required executor runtime unavailable: " + name)
        paths.add(executable)
    probe = '''import socket, subprocess, pathlib, json, shutil, tempfile, platform
import hashlib, math, random, datetime, collections, itertools, functools, typing
import encodings.utf_8, encodings.ascii, encodings.latin_1, encodings.idna
import ssl, urllib.request
import sys
# Run the actual guard path: ctypes can load further modules when structures are defined.
namespace = {'__name__': 'runtime_probe', '__file__': sys.argv[1]}
with open(sys.argv[1], 'rb') as source:
    exec(compile(source.read(), sys.argv[1], 'exec'), namespace)
namespace['restrict']()
print(json.dumps(sorted({m.__file__ for m in sys.modules.values()
                        if getattr(m, '__file__', None)})))'''
    python = shutil.which("python3", path=environment()["PATH"])
    result = subprocess.run([python, "-I", "-S", "-c", probe, str(Path(__file__).with_name("offline_exec.py"))],
                            cwd="/", env=environment(),
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


def model_command(work, trusted, arguments):
    """Isolate every CLI file tool, independently of its permission settings.

    Only the native pinned CLI is supported. A script/package installation must
    not cause its parent directory (possibly containing policy) to be mounted.
    The provider connection remains in the CLI; offline Bash uses the existing
    strict tool sandbox inside this filesystem boundary.
    """
    if sys.platform != "linux" or not shutil.which("bwrap"):
        raise RuntimeError("required model filesystem isolation unavailable")
    cli = shutil.which("claude", path=environment()["PATH"])
    if not cli:
        raise RuntimeError("isolated models require the native pinned Claude CLI")
    with open(cli, "rb") as stream:
        native = stream.read(4) == b"\x7fELF"
    if not native:
        raise RuntimeError("isolated models require the native pinned Claude CLI")
    command = ["bwrap", "--unshare-all", "--share-net", "--die-with-parent",
               "--new-session", "--cap-drop", "ALL"]
    for path in runtime_files(("claude", "bwrap", "socat", "timeout")):
        command += ["--ro-bind", path, path]
    # Explicit connection resources, never /etc or the certificate directory.
    for path in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf",
                 "/etc/ssl/certs/ca-certificates.crt"):
        if Path(path).is_file():
            command += ["--ro-bind", path, path]
    # Private procfs needs real process maps for the native runtime. Nothing may be mounted over it: the CLI's own bwrap
    # mounts a fresh procfs for its Bash, and the kernel refuses that ("Can't mount proc on /newroot/proc") when this one
    # has locked overmounts. The credential is therefore not in any environ (see launch_model), not masked there.
    command += ["--as-pid-1", "--proc", "/proc",
                "--dev", "/dev", "--tmpfs", "/tmp",
                "--dir", "/home/executor", "--bind", os.path.abspath(work), "/work",
                "--dir", "/engine"]
    command += ["--ro-bind", str(Path(__file__).with_name("offline_exec.py")), "/engine/process_guard.py"]
    for name in ("contract.md", "mcp.json", "settings.json", "remaining.py"):
        command += ["--ro-bind", os.path.join(trusted, name), "/engine/" + name]
    command += ["--chdir", "/work", "python3", "/engine/process_guard.py", "--process-only", cli, *arguments]
    return command


def launch_model(work, trusted, arguments, capture=False):
    env = environment()
    for key in ("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "CLAUDE_CODE_RETRY_WATCHDOG",
                "BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS", "SESSION_END"):
        if key in os.environ:
            env[key] = os.environ[key]
    # The credential goes through a pipe the CLI reads at start, never through an environ (readable via /proc).
    token, pass_fds = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), ()
    if token:
        reader, writer = os.pipe()
        with os.fdopen(writer, "w") as stream:
            stream.write(token)
        env["CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"] = str(reader)
        pass_fds = (reader,)
    try:
        return subprocess.run(model_command(work, trusted, arguments), env=env, pass_fds=pass_fds,
                              capture_output=capture, text=capture, check=False)
    finally:
        for fd in pass_fds:
            os.close(fd)


def launch(work, trusted, online=False, capture=False):
    if sys.platform != "linux" or not shutil.which("bwrap"):
        raise RuntimeError("required executor filesystem isolation unavailable")
    # OS directory names are not a trust boundary: host policy copies may live
    # under /usr or beside Python. Admit only the explicit runtime file closure.
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
    if online:
        command += ["--share-net"]
    for path in runtime_files():
        command += ["--ro-bind", path, path]
    if online:
        for path in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf",
                     "/etc/ssl/certs/ca-certificates.crt"):
            if Path(path).is_file():
                command += ["--ro-bind", path, path]
    command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home/executor",
                "--bind", os.path.abspath(work), "/work", "--dir", "/engine",
                "--ro-bind", os.path.join(trusted, "command.sh"), "/engine/command.sh",
                "--ro-bind", os.path.join(trusted, "offline_exec.py"), "/engine/offline_exec.py",
                "--chdir", "/work"]
    command += ["python3", "/engine/offline_exec.py", "bash", "/engine/command.sh"] if not online else ["bash", "/engine/command.sh"]
    result = subprocess.run(command, env=environment(), capture_output=capture, text=capture, timeout=3600 if capture else None)
    return result if capture else result.returncode


def run_candidate(command, candidate):
    """Run candidate-owned checks in a disposable product-only namespace.

    Snapshot committed regular blobs through trusted Git, never copy candidate
    metadata or use its config/hooks. No policy or host path reaches the child.
    Keep the full candidate untouched for the accepted measurement gate.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="candidate-check-") as tmp:
            work, trusted = Path(tmp, "work"), Path(tmp, "trusted")
            work.mkdir()
            trusted.mkdir()
            entries = git(candidate, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0")
            for entry in entries:
                if not entry:
                    continue
                metadata, raw = entry.split(b"\t", 1)
                mode, kind, oid = metadata.split()
                rel = raw.decode("utf-8")
                if not visible(rel) or rel.startswith(".session/"):
                    continue
                if mode not in (b"100644", b"100755") or kind != b"blob":
                    raise ValueError("candidate check requires regular files")
                destination = work / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(git(candidate, "cat-file", "blob", oid.decode()))
                destination.chmod(0o755 if mode == b"100755" else 0o644)
            git(work, "init", "-q", "-b", "executor")
            git(work, "add", "-f", "--all")
            git(work, "commit", "-q", "--allow-empty", "-m", "Product check snapshot")
            (trusted / "command.sh").write_text(command + "\n", encoding="utf-8")
            shutil.copyfile(Path(__file__).with_name("offline_exec.py"), trusted / "offline_exec.py")
            return launch(str(work), str(trusted), capture=True)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        # Never return an exception containing private paths or subprocess output.
        return subprocess.CompletedProcess(command, 1, "", "Required candidate check isolation unavailable")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "model":
            sys.exit(launch_model(sys.argv[2], sys.argv[3], sys.argv[4:]).returncode)
        work, trusted = sys.argv[1:3]
        online = Path(trusted, "network-mode").read_text().strip() == "online"
        sys.exit(launch(work, trusted, online=online))
    except (OSError, RuntimeError):
        print("Required executor filesystem isolation unavailable", file=sys.stderr)
        sys.exit(1)
