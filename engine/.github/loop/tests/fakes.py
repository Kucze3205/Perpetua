"""Test world: an in-memory GitHub (the thin functions of `common`) and real git repositories (a bare origin, the
engine's checkout of the default branch, agent working copies). Nothing here talks to the network."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

# Every test module imports this one, so this is the one place that stops the suite on Windows: git and bash through
# MSYS cost ~46 ms a process (an hour for the suite), and the sandbox, symlink and POSIX-shell cases cannot run there.
if sys.platform == "win32":
    sys.stderr.write("The engine tests run only on Linux: run them in WSL (Ubuntu-24.04).\n")
    sys.stderr.flush()
    os._exit(1)      # not sys.exit: `unittest discover` catches SystemExit raised by an import and goes on to the next module

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_DIR = os.path.dirname(HERE)
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, LOOP_DIR)

import artifact_pin  # noqa: E402
import candidate_checks  # noqa: E402
import check_results  # noqa: E402
import common  # noqa: E402
import iteration  # noqa: E402
import iteration_start  # noqa: E402
import loop  # noqa: E402
import policy_inputs  # noqa: E402
import session_artifact  # noqa: E402
import session_finalize  # noqa: E402
import session_start  # noqa: E402

PATCHED = ["variable", "issue", "list_issues", "create_issue", "edit_labels", "close_issue", "ensure_label", "comments",
           "post_comment", "patch_comment", "blockers", "blocking", "add_blocker", "workflow_run", "run_list",
           "list_artifacts", "delete_artifact", "cancel_run", "recent_closed", "gh", "pr_for_branch", "open_pr", "run_tokenless", "probe_limit",
           "edit_issue_body", "pin_issue"]
GH_HANDLED = {("run", "download"), ("release", "create"), ("release", "upload"), ("release", "download")}


class Staged:
    """What the policy scripts' job left for one staged session: its outputs (`ok`, `error`, `digest`) and its artifact."""
    def __init__(self, ok, error, digest, results):
        self.ok, self.error, self.digest, self.results = ok, error, digest, results

    def env(self):
        return {"POLICY_OK": self.ok, "POLICY_ERROR": self.error, "POLICY_DIGEST": self.digest}


def policy_job(bundle, work, root):
    """The job "Prepare the session inputs (policy scripts)" on a machine of its own: the bundle's code only, on a copy of the tree
    without its git history, standard library only, no checkout of the repository. Returns Staged."""
    jobwork = os.path.join(root, "work")
    shutil.copytree(work, jobwork, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    results, outputs = os.path.join(root, "results"), os.path.join(root, "outputs")
    env = {"PATH": os.environ["PATH"], "HOME": root, "GITHUB_OUTPUT": outputs}
    r = subprocess.run([sys.executable, "-S", os.path.join(bundle, "engine", "loop.py"), "run-policy-scripts", bundle, jobwork, results],
                       cwd=root, env=env, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("the policy scripts' job failed:\n" + r.stderr)
    with open(outputs, encoding="utf-8") as fh:
        got = dict(line.rstrip("\n").split("=", 1) for line in fh)
    return Staged(got["ok"], got.get("error", ""), got.get("digest", ""), results)


def outputs_of(path):
    """The job outputs a step wrote to `GITHUB_OUTPUT`."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)


def check_job(bundle, digest, root):
    """The job "Check the results" on a machine of its own: the download checked against the digest, unpacked, and the bundles
    run by the bundle's own engine code with `python3 -S` (standard library only, no checkout, no token). Returns its outputs."""
    artifact_pin.verify(bundle, digest)
    os.makedirs(root)
    with tarfile.open(os.path.join(bundle, "checks.tar.gz")) as tar:
        tar.extractall(root, filter="tar")
    outputs = os.path.join(root, "outputs")
    env = {"PATH": os.environ["PATH"], "HOME": root, "GITHUB_OUTPUT": outputs}
    checks = os.path.join(root, "checks")
    r = subprocess.run([sys.executable, "-S", os.path.join(checks, "engine", "loop.py"), "run-checks", os.path.join(checks, "bundles")],
                       cwd=root, env=env, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError("the job that runs the checks failed:\n" + r.stderr)
    with open(os.path.join(checks, "bundles", "plan.json"), encoding="utf-8") as fh:
        planned = json.load(fh)
    return outputs_of(outputs), planned


def stage_session(n, work, trusted, through_epilogue=False):
    """A session's inputs the way the workflow makes them: "Prepare the session" stages and bundles, the policy scripts' job runs
    the scripts, the session job checks the results against the digest and unpacks them over the staging. The head of
    "Take in results" follows (refuses bad results, notes the startup budget) unless the epilogue itself is played later.
    Returns Staged."""
    root = tempfile.mkdtemp(prefix="policy-job-")
    bundle = os.path.join(root, "bundle")
    session_start.stage(n, work, trusted, bundle)
    staged = policy_job(bundle, work, os.path.join(root, "job"))
    if staged.ok == "true":
        artifact_pin.verify(staged.results, staged.digest)
        with tarfile.open(os.path.join(staged.results, "session.tar.gz")) as tar:
            tar.extractall(os.path.join(work, ".session"), filter="data")
        if os.path.exists(os.path.join(staged.results, "prompt.md")):
            shutil.copy(os.path.join(staged.results, "prompt.md"), os.path.join(trusted, "prompt.md"))
    if not through_epilogue:
        why = policy_inputs.refusal(staged.ok, staged.error, staged.results, staged.digest)
        if why:
            session_finalize.refuse_policy_results(n, why)
        session_finalize.note_startup_budget(n, staged.results)
    return staged


class FakeHub:
    def __init__(self):
        self.base = datetime.now(timezone.utc)
        self.tick = 0
        self.lagging_label = None
        self.issues = {}
        self.cmts = {}
        self.deps = []                      # (blocked, blocker)
        self.vars = {"AUTOPILOT": "on"}
        self.runs, self.dispatched, self.cancelled, self.deleted, self.gh_calls = [], [], [], [], []
        self.artifacts = []
        self.prs = {}                       # head branch -> {"number", "url", "state", "merged", "title", "body"}
        self.next_n = 1
        self.tokenless = []                 # (bundle files) of every command sent to the tokenless job
        self.tokenless_purposes = []        # how each of them was named for the owner
        # what the next tokenless job does wrong instead: "no-artifact" (never ran), "no-stdout" (its command never started),
        # "timeout" (no exit code), "failed"
        self.tokenless_fault = None
        self.dispatch_refused = False       # GitHub refuses every dispatch (after the engine's retries)
        self.checked = []                   # the purposes of the bundles "Check the results" ran (and left a result of), in order
        self.decided = []                   # the same for "Decide whether the Policymaker runs"
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

    def list_issues(self, state="open", labels=(), limit=None):
        if self.lagging_label in labels:
            return []                       # GitHub's label-filtered list does not return a label added a moment ago
        out = [self._view(x) for x in self.issues.values()
               if (state == "all" or x["state"] == state) and set(labels) <= x["_labels"]]
        out = sorted(out, key=lambda x: x["number"])
        return out[::-1][:limit] if limit else out

    def create_issue(self, title, body, labels=(), assignees=()):
        n = self.next_n
        self.next_n += 1
        self.issues[n] = {"number": n, "id": 1000 + n, "title": title, "body": body, "state": "open", "state_reason": None,
                          "_labels": set(labels), "assignees": list(assignees), "created_at": self.ts(), "closed_at": None}
        return n

    def edit_issue_body(self, n, body):
        self.issues[int(n)]["body"] = body

    def pin_issue(self, n):
        self.pinned = int(n)
        return True

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
        if self.dispatch_refused:
            return False
        self.dispatched.append((workflow, dict(inputs)))
        if workflow == "session.yml":
            self.runs.append({"databaseId": 9000 + len(self.runs), "displayTitle": "session #%s" % inputs["issue"], "status": "queued",
                              "createdAt": self.ts()})
        return True

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

    def run_tokenless(self, bundle, timeout_minutes, purpose="command", poll=15):
        """The tokenless job: runs the bundle's `run.sh` in a clean environment and returns the output directory."""
        self.tokenless.append(sorted(os.listdir(bundle)))
        self.tokenless_purposes.append(purpose)
        fault, self.tokenless_fault = self.tokenless_fault, None
        if fault == "no-artifact":
            return None
        out = tempfile.mkdtemp(prefix="tokenless-out-")
        r = subprocess.run(["bash", os.path.join(bundle, "run.sh")], env=common.clean_env(),
                           capture_output=True, text=True, timeout=timeout_minutes * 60)
        for name, text in (("stdout.txt", r.stdout), ("stderr.txt", r.stderr), ("exit-code", "%d\n" % (1 if fault == "failed" else r.returncode))):
            if (fault, name) in (("no-stdout", "stdout.txt"), ("timeout", "exit-code")):
                continue
            with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        return out

    def probe_limit(self):
        return self.limit

    def gh(self, *args, inp=None, check=True):
        """The `gh` calls the tests rely on (only recorded). Any other call, `api` ones included, fails: a helper built on
        it would otherwise pass against an empty answer and nothing would show its effect missing."""
        if args[:2] not in GH_HANDLED:
            raise AssertionError("FakeHub.gh does not handle this call: gh %s (inp=%r, check=%r)" % (" ".join(args), inp, check))
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
        for k, v in dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                         DEFAULT_BRANCH="main", GITHUB_REPOSITORY="o/r", GITHUB_REPOSITORY_OWNER="ow").items():
            os.environ[k] = v
        os.environ.pop("GH_TOKEN", None)
        self.n_work = 0
        self.before_finalize = None         # a callable: something happens on origin while the agent works
        self.after_checks = None            # a callable: something happens on origin after "Check the results"
        self.checks = None                  # (outputs of "Prepare the checks", of "Check the results") of the last epilogue
        self.check_jobs = True              # False: a run without the two check jobs (every check goes to a Tokenless run)
        self.start_jobs = "run"             # "lost": the jobs of "Iteration start" never run; None: a run without them (it starts at once)
        self.started = None                 # (outputs of the job before, of "Decide whether the Policymaker runs") of the last start
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
        staged = self.staged = stage_session(n, work, os.path.join(rt, "trusted"), through_epilogue=True)
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
        self.epilogue(n, work, rt, staged)
        return work

    def epilogue(self, n, work, rt, staged):
        """The session job packs its results; "Prepare the checks" and "Check the results" check them; the epilogue job, on a
        fresh runner, takes the artifact, the baseline (a channel of its own) and the outputs of "Check the results" in."""
        artifact, baseline = os.path.join(rt, "artifact"), os.path.join(rt, "baseline")
        session_artifact.pack(work, rt, artifact)
        os.makedirs(baseline)
        shutil.move(os.path.join(rt, "trusted", "executor-baseline.json"), baseline)
        results = self.check(n, artifact, baseline, rt)
        if self.after_checks:
            self.after_checks()
        fresh = os.path.join(rt, "epilogue")
        os.makedirs(fresh)
        os.environ["RUNNER_TEMP"] = fresh
        os.environ.update(staged.env(), CHECKS_RESULTS=results)
        with self.iteration_start(fresh):
            try:
                session_finalize.epilogue(n, artifact, baseline, artifact_pin.digest(baseline), staged.results)
            finally:
                check_results.RESULTS.clear()           # the next epilogue is another process

    def watch(self):
        """A watchdog run: "Check the loop", then the jobs of "Iteration start" when it found a start due."""
        rt = tempfile.mkdtemp(prefix="watch-", dir=self.tmp)
        saved = os.environ.get("RUNNER_TEMP")
        os.environ["RUNNER_TEMP"] = rt
        try:
            with self.iteration_start(rt), mock.patch.object(common, "probe", return_value=200):      # a live credential
                return loop.watch()
        finally:
            os.environ.pop("RUNNER_TEMP")
            if saved is not None:
                os.environ["RUNNER_TEMP"] = saved

    @contextlib.contextmanager
    def iteration_start(self, rt):
        """The command of a job the jobs of "Iteration start" follow (`START_PLAN`), then those jobs: "Decide whether the
        Policymaker runs" from the pinned facts (its own engine code, `python3 -S`, no token), and "Start the iteration" with
        only that job's output. A job that raised still leaves its outputs, and the jobs after it run (`always()`)."""
        if not self.start_jobs:
            yield
            return
        facts, outputs = os.path.join(rt, "start-facts"), os.path.join(rt, "start-outputs")
        saved = os.environ.get("GITHUB_OUTPUT")
        os.environ["START_PLAN"] = facts
        try:
            yield
        finally:
            os.environ["GITHUB_OUTPUT"] = outputs
            try:
                iteration_start.prepare()
            finally:
                os.environ.pop("START_PLAN")
                os.environ.pop("GITHUB_OUTPUT")
                if saved is not None:
                    os.environ["GITHUB_OUTPUT"] = saved
            self.start(rt, outputs_of(outputs), facts)

    def start(self, rt, planned, facts):
        """The jobs of "Iteration start" on the outputs `planned` of the job before and its artifact `facts`. A tokenless fault
        that is armed fails the decision job, so the fallback meets the fault."""
        self.started = (planned, {})
        if planned.get("start") != "true":
            return
        results = ""
        if planned.get("count", "0") != "0" and not self.hub.tokenless_fault:
            try:
                got, plan = check_job(facts, planned["digest"], os.path.join(rt, "decide-job"))
            except ValueError:                      # the download is not the artifact the job before pinned: the job fails
                got, plan = {}, []
            self.started = (planned, got)
            ran = json.loads(got.get("results") or "{}")
            self.hub.decided.extend(x["purpose"] for x in plan if x["digest"] in ran)
            results = got.get("results", "")
        if self.start_jobs == "lost":
            return
        os.environ["DECIDE_RESULTS"] = results
        common.LAUNCHED.clear()                     # "Start the iteration" is a process of its own
        try:
            iteration_start.act()
        finally:
            check_results.RESULTS.clear()
            os.environ.pop("DECIDE_RESULTS")

    def check(self, n, artifact, baseline, rt):
        """"Prepare the checks" (with the token, on a runner of its own) and "Check the results". Returns the job output
        `results` "Take in results" gets: none when no bundle was planned, or when a tokenless fault is armed (the job failed,
        so the fallback meets the fault)."""
        if not self.check_jobs:
            return ""
        prep, outputs = os.path.join(rt, "checks-prep"), os.path.join(rt, "checks-prep-outputs")
        os.makedirs(prep)
        saved = os.environ.get("GITHUB_OUTPUT")
        os.environ.update(RUNNER_TEMP=prep, GITHUB_OUTPUT=outputs)
        try:
            candidate_checks.prepare(n, artifact, baseline, artifact_pin.digest(baseline), os.path.join(rt, "checks-bundle"))
        finally:
            os.environ.pop("GITHUB_OUTPUT")
            if saved is not None:
                os.environ["GITHUB_OUTPUT"] = saved
        planned = outputs_of(outputs)
        self.checks = (planned, {})
        if planned.get("count", "0") == "0" or self.hub.tokenless_fault:
            return ""
        got, plan = check_job(os.path.join(rt, "checks-bundle"), planned["digest"], os.path.join(rt, "checks-job"))
        self.checks = (planned, got)
        ran = json.loads(got.get("results") or "{}")
        self.hub.checked.extend(x["purpose"] for x in plan if x["digest"] in ran)
        return got.get("results", "")

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
