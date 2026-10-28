"""Test world: an in-memory GitHub (the thin functions of `common`) and real git repositories (a bare origin, the
engine's checkout of the default branch, agent working copies). Nothing here talks to the network."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_DIR = os.path.dirname(HERE)
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, LOOP_DIR)

import common  # noqa: E402
import iteration  # noqa: E402
import loop  # noqa: E402
import session_artifact  # noqa: E402

PATCHED = ["variable", "issue", "list_issues", "create_issue", "edit_labels", "close_issue", "ensure_label", "comments",
           "post_comment", "patch_comment", "blockers", "blocking", "add_blocker", "workflow_run", "run_list",
           "list_artifacts", "delete_artifact", "cancel_run", "recent_closed", "gh", "pr_for_branch", "open_pr", "run_tokenless", "probe_limit"]


class FakeHub:
    def __init__(self):
        self.base = datetime.now(timezone.utc)
        self.tick = 0
        self.issues = {}
        self.cmts = {}
        self.deps = []                      # (blocked, blocker)
        self.vars = {"AUTOPILOT": "on"}
        self.runs, self.dispatched, self.cancelled, self.deleted, self.gh_calls = [], [], [], [], []
        self.artifacts = []
        self.prs = {}                       # head branch -> {"number", "url", "state", "merged", "title", "body"}
        self.next_n = 1
        self.tokenless = []                 # (bundle files) of every command sent to the tokenless job
        self.tokenless_fault = None         # "no-artifact" | "failed": what the next tokenless job does wrong instead
        self.limit = (True, None)           # the engine's own probe of a usage limit: (limited, reset)

    def ts(self):
        self.tick += 1
        return (self.base + timedelta(seconds=self.tick)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # -- the functions of common
    def variable(self, name):
        return self.vars.get(name, "")

    def _view(self, x):
        d = dict(x)
        d["labels"] = [{"name": l} for l in sorted(x["_labels"])]
        return d

    def issue(self, n):
        return self._view(self.issues[int(n)])

    def list_issues(self, state="open", labels=()):
        out = [self._view(x) for x in self.issues.values()
               if (state == "all" or x["state"] == state) and set(labels) <= x["_labels"]]
        return sorted(out, key=lambda x: x["number"])

    def create_issue(self, title, body, labels=(), assignees=()):
        n = self.next_n
        self.next_n += 1
        self.issues[n] = {"number": n, "id": 1000 + n, "title": title, "body": body, "state": "open", "state_reason": None,
                          "_labels": set(labels), "assignees": list(assignees), "created_at": self.ts(), "closed_at": None}
        return n

    def edit_labels(self, n, add=(), remove=()):
        x = self.issues[int(n)]
        x["_labels"] |= set(add)
        x["_labels"] -= set(remove)

    def close_issue(self, n, reason="completed"):
        x = self.issues[int(n)]
        x.update(state="closed", state_reason="not_planned" if reason == "not planned" else "completed", closed_at=self.ts())

    def ensure_label(self, *a, **k):
        pass

    def comments(self, n):
        return list(self.cmts.get(int(n), []))

    def post_comment(self, n, body):
        c = {"id": 5000 + sum(len(v) for v in self.cmts.values()), "body": body, "author_association": "NONE",
             "user": {"login": "github-actions[bot]"}}
        self.cmts.setdefault(int(n), []).append(c)
        return c

    def patch_comment(self, cid, body):
        for v in self.cmts.values():
            for c in v:
                if c["id"] == cid:
                    c["body"] = body

    def blockers(self, n):
        return [self._view(self.issues[b]) for (x, b) in self.deps if x == int(n)]

    def blocking(self, n):
        return [self._view(self.issues[x]) for (x, b) in self.deps if b == int(n)]

    def add_blocker(self, blocked, blocker):
        self.deps.append((int(blocked), int(blocker)))

    def workflow_run(self, workflow, **inputs):
        self.dispatched.append((workflow, dict(inputs)))
        if workflow == "session.yml":
            self.runs.append({"databaseId": 9000 + len(self.runs), "displayTitle": "session #%s" % inputs["issue"], "status": "queued",
                              "createdAt": self.ts()})

    def run_list(self, workflow):
        return list(self.runs) if workflow == "session.yml" else []

    def list_artifacts(self):
        return list(self.artifacts)

    def delete_artifact(self, aid):
        self.deleted.append(aid)

    def cancel_run(self, rid):
        self.cancelled.append(rid)

    def recent_closed(self, limit=30):
        done = [self._view(x) for x in self.issues.values() if x["state"] == "closed"]
        return sorted(done, key=lambda x: x["closed_at"], reverse=True)[:limit]

    def pr_for_branch(self, branch):
        pr = self.prs.get(branch)
        return pr and {k: pr[k] for k in ("number", "url", "state", "merged")}

    def open_pr(self, branch, title, body):
        n = 900 + len(self.prs)
        self.prs[branch] = {"number": n, "url": "https://github.com/o/r/pull/%s" % n, "state": "open", "merged": False,
                            "title": title, "body": body}

    def run_tokenless(self, bundle, timeout_minutes, poll=15):
        """The tokenless job: runs the bundle's `run.sh` in a clean environment and returns the output directory."""
        self.tokenless.append(sorted(os.listdir(bundle)))
        fault, self.tokenless_fault = self.tokenless_fault, None
        if fault == "no-artifact":
            return None
        out = tempfile.mkdtemp(prefix="tokenless-out-")
        r = subprocess.run([os.environ.get("LOOP_BASH", "bash"), os.path.join(bundle, "run.sh")], env=common.clean_env(),
                           capture_output=True, text=True, timeout=timeout_minutes * 60)
        for name, text in (("stdout.txt", r.stdout), ("stderr.txt", r.stderr), ("exit-code", "%d\n" % (1 if fault == "failed" else r.returncode))):
            with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        return out

    def probe_limit(self):
        return self.limit

    def gh(self, *args, inp=None, check=True):
        self.gh_calls.append(args)
        return ""

    # -- helpers for tests
    def install(self):
        self.saved = {k: getattr(common, k) for k in PATCHED}
        for k in PATCHED:
            setattr(common, k, getattr(self, k))
        common.REPO = "o/r"

    def uninstall(self):
        for k, v in self.saved.items():
            setattr(common, k, v)

    def answer(self, n, yes):
        """The owner closes an issue: completed = yes, not planned = no."""
        self.close_issue(n, "completed" if yes else "not planned")

    def finish_runs(self):
        for r in self.runs:
            r["status"] = "completed"

    def by_label(self, label, state="open"):
        return [x for x in self.list_issues(state) if label in common.label_names(x)]

    def role_issues(self, role, state="open"):
        return self.by_label("role:" + role, state)

    def report(self, n):
        for c in self.cmts.get(n, []):
            if c["body"].startswith(common.MARK):
                return common.fields(c["body"])
        return {}

    def prose(self, n):
        for c in self.cmts.get(n, []):
            if c["body"].startswith(common.MARK):
                return c["body"]
        return ""


def sh(cwd, *args):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("%s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


class World:
    """A hub, a bare origin, the engine's checkout (cwd) and a place for agent copies."""

    def __init__(self, engine_yml=None, files=None, first_iteration=False):
        self.tmp = tempfile.mkdtemp(prefix="perpetua-test-")
        self.env_saved = dict(os.environ)
        self.cwd_saved = os.getcwd()
        # Do not inspect or inherit the invoking user's global skills or Git config.
        home = os.path.join(self.tmp, "fixture-home")
        os.mkdir(home)
        os.environ["HOME"] = home
        if os.name == "nt":
            os.environ["USERPROFILE"] = home
        for k, v in dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                         DEFAULT_BRANCH="main", GITHUB_REPOSITORY="o/r", GITHUB_REPOSITORY_OWNER="ow").items():
            os.environ[k] = v
        if os.name == "nt":      # `bash` on PATH may be WSL's, which cannot run engine commands: put Git's first
            for cand in ("C:/Program Files/Git/bin/bash.exe", "C:/Program Files (x86)/Git/bin/bash.exe"):
                if os.path.exists(cand):
                    os.environ["LOOP_BASH"] = cand
                    os.environ["PATH"] = os.path.dirname(cand) + os.pathsep + os.environ["PATH"]
                    break
        os.environ.pop("GH_TOKEN", None)
        self.n_work = 0
        self.before_finalize = None         # a callable: something happens on origin while the agent works
        self.origin = os.path.join(self.tmp, "origin.git")
        self.loop = os.path.join(self.tmp, "loop")
        sh(self.tmp, "git", "init", "-q", "--bare", "-b", "main", self.origin)
        # Git starts a detached gc/maintenance after pushes and clones (also from the child processes of a local
        # transport, which do not inherit environment config). It can repack origin while a later clone copies its
        # objects, so the repository that is cloned says no background maintenance.
        for key, value in (("gc.auto", "0"), ("maintenance.auto", "false"), ("receive.autogc", "false")):
            sh(self.origin, "git", "config", key, value)
        # hermetic: the engine's code plus a small policy fixture, never whatever policy/ holds in the repository we run in
        shutil.copytree(os.path.join(ENGINE, ".github"), os.path.join(self.loop, ".github"),
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
        shutil.copy(os.path.join(ENGINE, "ENGINE_VERSION"), self.loop)
        self.write(self.loop, "engine.yml", engine_yml if engine_yml is not None else "")
        self.write(self.loop, "policy/params.yml", POLICY_PARAMS)
        self.write(self.loop, "execution/profiles.yml", EXECUTION_PROFILES)
        self.write(self.loop, "policy/profiles.yml", POLICY_PROFILES)
        shutil.copy(os.path.join(ENGINE, "policy", "policymaker-trigger.py"), os.path.join(self.loop, "policy"))
        shutil.copy(os.path.join(ENGINE, "policy", "session-state.py"), os.path.join(self.loop, "policy"))
        shutil.copy(os.path.join(ENGINE, "policy", "session-startup.py"), os.path.join(self.loop, "policy"))
        shutil.copy(os.path.join(ENGINE, "policy", "layer-rules.py"), os.path.join(self.loop, "policy"))
        for layer, roles in (("execution", ("orchestrator", "worker", "net")),
                             ("policy", ("evaluator", "evaluator-verifier", "audit", "policymaker"))):
            for role in roles:
                self.write(self.loop, "%s/roles/%s/SKILL.md" % (layer, role), "---\nname: %s\ndescription: fixture\n---\nx\n" % role)
        for p, text in (files or {}).items():
            self.write(self.loop, p, text)
        sh(self.loop, "git", "init", "-q", "-b", "main")
        sh(self.loop, "git", "config", "core.autocrlf", "false")
        sh(self.loop, "git", "add", "-A")
        sh(self.loop, "git", "commit", "-q", "-m", "setup")
        sh(self.loop, "git", "remote", "add", "origin", self.origin)
        sh(self.loop, "git", "push", "-q", "origin", "main")
        os.chdir(self.loop)
        self.hub = FakeHub()
        self.hub.install()
        common.LAUNCHED.clear()
        if not first_iteration:     # an earlier Policymaker session (#0, closed, done): the first-iteration trigger is spent
            self.hub.issues[0] = {"number": 0, "id": 1000, "title": "Policymaker at the start of iteration 0", "body": "",
                                  "state": "closed", "state_reason": "completed", "_labels": {"role:policymaker"},
                                  "assignees": [], "created_at": self.hub.ts(), "closed_at": self.hub.ts()}
            common.update_report(0, {"status": "done"}, "seed")

    def close(self):
        self.hub.uninstall()
        os.chdir(self.cwd_saved)
        os.environ.clear()
        os.environ.update(self.env_saved)
        def remove_error(func, path, exc_info):
            if isinstance(exc_info[1], FileNotFoundError):
                return  # A Git background process removed the entry concurrently.
            if not isinstance(exc_info[1], PermissionError):
                raise exc_info[1]
            try:
                os.chmod(path, 0o700)
                func(path)
            except FileNotFoundError:
                pass
        shutil.rmtree(self.tmp, onerror=remove_error)

    @staticmethod
    def write(root, rel, text):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def origin_file(self, rel, ref="main"):
        r = subprocess.run(["git", "show", "%s:%s" % (ref, rel)], cwd=self.origin, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    def origin_has_ref(self, ref):
        return subprocess.run(["git", "rev-parse", "--verify", "-q", ref], cwd=self.origin, capture_output=True).returncode == 0

    def push_main(self, files, message="direct"):
        """A commit by a human straight on the origin's default branch."""
        self.n_work += 1
        d = os.path.join(self.tmp, "human%s" % self.n_work)
        sh(self.tmp, "git", "clone", "-q", "--no-hardlinks", "-c", "core.autocrlf=false", self.origin, d)
        for p, t in files.items():
            self.write(d, p, t)
        sh(d, "git", "add", "-A")
        sh(d, "git", "commit", "-q", "-m", message)
        sh(d, "git", "push", "-q", "origin", "main")

    def session(self, n, files=None, status="done", report_extra="", session_files=None, exit_code="0", result=None,
                commit=True, report=True, base=None, delete=(), yaml="", links=None):
        """Play the agent of issue `n` and run the epilogue on what it left. `files` are committed on `task/N`
        (`commit=False`: left in the tree; `links`: name -> target of symlinks left there);
        `session_files` go to `.session/` (report extras, tasks.json, orders.json, requests.json, artifacts/...)."""
        self.n_work += 1
        work = os.path.join(self.tmp, "work%s" % self.n_work)
        sh(self.tmp, "git", "clone", "-q", "--no-hardlinks", "-c", "core.autocrlf=false", self.origin, work)
        sh(work, "git", "config", "core.autocrlf", "false")
        sh(work, "git", "checkout", "-q", "-B", "task/%s" % n, base or "origin/main")
        rt = os.path.join(self.tmp, "rt%s" % self.n_work)
        os.makedirs(rt)
        os.environ["RUNNER_TEMP"] = rt
        # Model the real staging/admission handshake (the baseline) even when the session's
        # output itself is supplied by the test rather than executed.
        loop.stage(n, work, os.path.join(rt, "trusted"))
        for p, t in (files or {}).items():
            self.write(work, p, t)
        for p in delete:
            os.remove(os.path.join(work, p))
        for p, target in (links or {}).items():
            os.makedirs(os.path.dirname(os.path.join(work, p)), exist_ok=True)
            os.symlink(target, os.path.join(work, p))
        if commit and (files or delete):
            sh(work, "git", "add", "-A")
            sh(work, "git", "commit", "-q", "--allow-empty", "-m", "work of #%s" % n)
        if report:
            self.write(work, ".session/report.md", "%s\n```yaml\nstatus: %s\ncommit: abc\n%s```\nProse of #%s.\n%s" % (
                common.MARK, status, yaml, n, report_extra))
        for p, t in (session_files or {}).items():
            self.write(work, ".session/" + p, t)
        self.write(rt, "agent-exit", exit_code)
        self.write(rt, "session-start", str(int(datetime.now().timestamp()) - 50))
        self.write(rt, "agent-start", str(int(datetime.now().timestamp()) - 40))
        self.write(rt, "agent-end", str(int(datetime.now().timestamp()) - 10))
        self.write(rt, "claude-execution-output.json", json.dumps(result if result is not None else {
            "type": "result", "is_error": False, "num_turns": 7, "usage": {"input_tokens": 1, "output_tokens": 2},
            "modelUsage": {"m1": {"inputTokens": 100, "outputTokens": 50, "cacheReadInputTokens": 10, "cacheCreationInputTokens": 5}}}))
        os.environ["RUNNER_TEMP"] = rt
        os.environ["MODEL"], os.environ["EFFORT"] = "m1", "high"
        self.hub.finish_runs()
        common.LAUNCHED.clear()            # every epilogue is a fresh process
        if self.before_finalize:
            self.before_finalize()
        self.epilogue(n, work, rt)
        return work

    def epilogue(self, n, work, rt):
        """The session job packs its results; the epilogue job, on a fresh runner, takes the artifact and the baseline
        (a channel of its own) in."""
        artifact, baseline = os.path.join(rt, "artifact"), os.path.join(rt, "baseline")
        session_artifact.pack(work, rt, artifact)
        os.makedirs(baseline)
        shutil.move(os.path.join(rt, "trusted", "executor-baseline.json"), baseline)
        with open(os.path.join(baseline, "executor-baseline.json"), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        fresh = os.path.join(rt, "epilogue")
        os.makedirs(fresh)
        os.environ["RUNNER_TEMP"] = fresh
        loop.epilogue(n, artifact, baseline, digest)

    def branch(self, n, files, base="origin/main", delete=()):
        """What the epilogue finds on origin after fetching an agent's work: `task/N` with one commit."""
        self.n_work += 1
        work = os.path.join(self.tmp, "br%s" % self.n_work)
        sh(self.tmp, "git", "clone", "-q", "--no-hardlinks", "-c", "core.autocrlf=false", self.origin, work)
        sh(work, "git", "config", "core.autocrlf", "false")
        sh(work, "git", "checkout", "-q", "-B", "task/%s" % n, base)
        for p, t in files.items():
            self.write(work, p, t)
        for p in delete:
            os.remove(os.path.join(work, p))
        sh(work, "git", "add", "-A")
        sh(work, "git", "commit", "-q", "--allow-empty", "-m", "work of #%s" % n)
        sh(work, "git", "push", "-q", "-f", "origin", "task/%s" % n)

    def next_dispatch(self, workflow="session.yml"):
        """The issue number of the oldest dispatched session not yet played."""
        done = getattr(self, "_played", set())
        for w, inp in self.hub.dispatched:
            if w == workflow and int(inp["issue"]) not in done and self.hub.issues[int(inp["issue"])]["state"] == "open":
                self._played = done | {int(inp["issue"])}
                return int(inp["issue"])
        return None


def tasks_json(*tasks):
    return json.dumps({"tasks": list(tasks)})


def task(tid, role="worker", title=None, body=None, **kw):
    return dict(id=tid, role=role, title=title or "Task %s" % tid, body=body or "## Goal\nx\n## Acceptance\n- [ ] y\n", **kw)


WORKER_PROFILES = """orchestrator:
  write: ["journal/**"]
  tools: [Read, Grep, Glob, Write, Edit]
  model: opus
worker:
  write: ["src/**", "docs/**"]
  tools: [Read, Grep, Glob, Write, Edit]
net:
  write: ["src/**"]
  tools: [Read, WebSearch, Write]
evaluator:
  owns_evaluator: true
  write: ["policy/evaluator/**"]
  tools: [Read, Write]
evaluator-verifier:
  write: []
  tools: [Read, Write]
audit:
  write: ["audit/**"]
  tools: [Read, Write]
"""


POLICY_PARAMS = """shaper: orchestrator
models:
  default: claude-sonnet-5-5
  opus: claude-opus-5-5
efforts: [low, medium, high]
"""

EXECUTION_PROFILES, POLICY_PROFILES = WORKER_PROFILES.split("evaluator:\n", 1)
POLICY_PROFILES = "evaluator:\n" + POLICY_PROFILES


def worker_world(engine_yml=None, first_iteration=False):
    """The world of most tests: a `worker` and a `net` role next to the engine's roles; `engine_yml` goes over the defaults.
    Unless `first_iteration`, an iteration starts straight from the shaper (see World)."""
    return World(engine_yml=engine_yml, first_iteration=first_iteration)
