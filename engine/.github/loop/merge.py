"""The candidate and its way into the default branch: the agent's commits fetched into a trusted tree, the
task's verification, the merge, its notes, and the evaluator verifier.
"""
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import check_results
import closing
import common as c
import executor_boundary
import iteration as it
import managed_resources as resources
import owner_requests as requests
import policymaker_session
import session_reports


PUSH_TRIES = 3


class Tree:
    """A throwaway worktree of the trusted checkout, at `ref`."""
    def __init__(self, ref):
        self.ref = ref

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="cand-")
        self.path = os.path.join(self.tmp, "cand")
        c.git(".", "worktree", "add", "-q", "--detach", self.path, self.ref)
        return self.path

    def __exit__(self, *exc):
        c.git(".", "worktree", "remove", "--force", self.path, check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)


def admission_message(role, n):
    """`<role> #N: <task title>`: how the session's work reads in the commit list. Trusted data only, never the session's text."""
    title = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", c.issue(n).get("title") or "").split())
    return "%s #%s: %s" % (role, n, title[:72] or "session output")


def fetch_candidate(work, n):
    """The files the session left, as one commit on its staged baseline in `refs/heads/task/N` of the trusted checkout:
    objects only, nothing of the agent's repository (its commits, config, hooks) is ever read or executed. Returns
    (short sha, protected paths the change touches). The branch goes to origin only when it touches none: GitHub refuses
    a token's push of workflow files, and such a change is refused at merge anyway."""
    trusted = os.path.join(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()), "trusted")
    baseline = os.path.join(trusted, "executor-baseline.json")
    ref = "refs/heads/task/%s" % n
    try:
        if not os.path.isdir(work) or not os.path.isfile(baseline):
            print("Required session admission metadata unavailable")
            return None, []
        with open(baseline, encoding="utf-8") as fh:
            meta = json.load(fh)
        role = c.roles_of(c.label_names(c.issue(n)))[0]
        # A new path outside a role's write paths is environment debris (runner home files, IDE
        # settings, a tool's virtualenv links): neither inspected nor admitted. Everything else reaches the checks.
        write = policymaker_session.profile_of(role).get("write") or []
        skip = lambda rel, tracked: not tracked and not c.path_match(rel, write)
        executor_boundary.import_candidate(work, meta, ".", ref, admission_message(role, n), skip)
    except (ValueError, OSError, subprocess.SubprocessError):
        print("Session output could not be admitted safely")
        return None, []
    d = c.default_branch()
    c.git(".", "fetch", "-q", "origin", d, check=False, remote=True)
    touched = [f for f in c.git(".", "diff", "--name-only", "origin/%s...task/%s" % (d, n), check=False).stdout.split()
               if resources.is_protected(f)]
    if not touched:
        r = c.git(".", "push", "-q", "-f", "origin", "task/%s:refs/heads/task/%s" % (n, n), check=False, remote=True)
        if r.returncode:
            print("task/%s was not pushed: %s" % (n, (r.stderr or r.stdout).strip()))
    return c.git(".", "rev-parse", "--short", "task/%s" % n).stdout.strip(), touched


def verification_commands(body):
    """Commands of `## Verification`: code blocks or backticked lines."""
    text = c.section(body, "Verification")
    return [x.strip() for x in re.findall(r"```[a-z]*\n(.*?)```", text, re.S)] or re.findall(r"`([^`\n]+)`", text)


def verification_rules(body):
    """Permission rules letting a session run its own verification exactly as the epilogue will (one-line commands)."""
    return ["Bash(%s)" % re.sub(r"([\\()*])", r"\\\1", cmd) for cmd in verification_commands(body) if "\n" not in cmd]


def run_for_agent(cmd, cand, purpose):
    """A command whose failure the agent is asked to fix (`## Verification`, `check`). A job that never ran it is GitHub's
    failure: it raises TokenlessUnavailable (the run goes red, the session stays open) and costs no fix round."""
    r = check_results.run_command(cmd, cand, purpose=purpose)
    if r.returncode == check_results.NO_RESULT:
        raise check_results.TokenlessUnavailable("%s did not run: the tokenless job left no output" % purpose)
    return r


def run_verification(cand, body, n):
    """`## Verification` of the task: commands from code blocks or backticked lines, each on a machine without the token."""
    for cmd in verification_commands(body):
        r = run_for_agent(cmd, cand, "verification for #%s" % n)
        print("$ %s\n%s%s" % (cmd, r.stdout, r.stderr))
        if r.returncode:
            return False, cmd + "\n" + (r.stdout + r.stderr)[-1500:]
    return True, ""


def merge_branch(n, role, i, override=None, verified=False, checked_commit=None, checked_evaluator_rev=None, verifier=None, boundary=False):
    """Rebase `task/N` on the default branch, run the checks, get the change gate's verdict, push.
    Returns {"result", "detail", "verdict", "gate_s", "changed", "check"}. Git orders competitors: a rejected push is retried
    on a fresh rebase, whose checks run again (a result of "Check the results" stands in only for the same trees)."""
    k = it.policy_pass_of(n) if role == "policymaker" else 1
    res = {"result": "conflict"}
    for _ in range(PUSH_TRIES):
        with attempt(n, role, i, k, override, verified, checked_commit, checked_evaluator_rev, boundary) as (res, cand):
            if cand is None:
                return res
            change = it.record_change(cand, n, role, res["session_base"], res["baseline"], c.head(cand), res["changed"], verifier, k)
            if c.git(cand, "push", "origin", "HEAD:refs/heads/%s" % c.default_branch(), check=False, remote=True).returncode == 0:
                if change:
                    it.record_applied("changes", change)
                return dict(res, result="merged", change=change)
    return dict(res, result="conflict")


@contextlib.contextmanager
def attempt(n, role, i, k=1, override=None, verified=False, checked_commit=None, checked_evaluator_rev=None, boundary=False):
    """One try of a merge up to its push, on the current default branch: yields (res, cand). `cand` is the rebased tree that
    passed every check, or None when `res` is the final result. The rebase is reproducible (committer date of the baseline):
    the same candidate on the same main is the same commit, so the checks "Prepare the checks" planned are this try's."""
    prof = policymaker_session.profile_of(role) or {}
    br = "task/%s" % n
    res = {"result": "conflict", "detail": "", "verdict": None, "gate_s": 0, "changed": []}
    c.sync_main()
    existing = it.applied("changes", it.change_id(n, k))
    if existing:        # a merged session's branch is gone
        yield dict(res, result="merged", change=existing), None
        return
    c.git(".", "fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br), remote=True)
    branch_sha = c.git(".", "rev-parse", "origin/%s" % br).stdout.strip()
    baseline = res["baseline"] = c.head()
    res["session_base"] = c.git(".", "merge-base", baseline, branch_sha).stdout.strip()
    res["changed"] = changed = c.git(".", "diff", "--name-only", "-z", "origin/%s...origin/%s" % (c.default_branch(), br)).stdout.split("\0")[:-1]
    bad = [f for f in changed if resources.is_protected(f)]
    out = [f for f in changed if not resources.may_write(prof, f)]
    early = (dict(result="verification-stale", detail="The task branch changed after the verified snapshot.") if checked_commit and branch_sha != checked_commit
             else dict(result="nothing") if not changed else dict(result="protected", detail=", ".join(bad)) if bad
             else dict(result="scope", detail=", ".join(out)) if out
             else dict(result="awaiting-verifier") if any(resources.is_evaluator(f) for f in changed) and not verified else None)
    if early:
        yield dict(res, **early), None
        return
    with Tree("origin/%s" % br) as cand:
        date = "@%s +0000" % c.git(".", "log", "-1", "--format=%ct", baseline).stdout.strip()
        final = checks(n, role, i, cand, res, override, checked_evaluator_rev, boundary, date)
        yield (final, None) if final else (res, cand)


def checks(n, role, i, cand, res, override, checked_evaluator_rev, boundary, date):
    """The rebase of `cand` and the checks of the merge; the final result when one refuses it, None when all pass."""
    cfg, baseline, work_paths = c.cfg(), res["baseline"], [f for f in res["changed"] if not resources.is_direct(f)]
    if c.git(cand, "rebase", "origin/%s" % c.default_branch(), check=False, env={"GIT_COMMITTER_DATE": date}).returncode:
        c.git(cand, "rebase", "--abort", check=False)
        return dict(res, result="conflict")
    if resources.layered():
        try:
            resources.role_profiles(cand)
            if boundary or any(resources.is_managed(f) for f in res["changed"]):
                refused = it.layer_errors(cand, boundary)
                if refused:
                    return dict(res, result="invalid-resources", detail="; ".join(refused[:6]))
        except (ValueError, it.PolicyScriptError) as error:
            return dict(res, result="invalid-resources", detail=str(error))
    if checked_evaluator_rev and resources.evaluator_revision("HEAD", cand) != checked_evaluator_rev:
        return dict(res, result="verification-stale", detail="The rebased evaluator differs from the verified snapshot.")
    # The accepted range contains this session's rebased commits only.
    res["changed"] = changed = c.git(cand, "diff", "--name-only", "-z", baseline, "HEAD").stdout.split("\0")[:-1]
    big = [f for f in changed if os.path.isfile(os.path.join(cand, f)) and os.path.getsize(os.path.join(cand, f)) > cfg["large_file_bytes"]]
    if big:
        return dict(res, result="large", detail=", ".join(big))
    if work_paths:
        if cfg["check"]:
            t = run_for_agent(cfg["check"], cand, "check for #%s" % n)
            res["check"] = "fail" if t.returncode else "pass"
            if t.returncode:
                return dict(res, result="check", detail=(t.stdout + t.stderr)[-1500:])
        if override:
            res["verdict"] = override
        else:
            t0 = time.time()
            with Tree(baseline) as accepted:            # the accepted tree: main as committed, nothing the engine left beside it
                res["verdict"] = it.run_gate(cand, accepted, {"candidate_sha": c.head(cand), "baseline_sha": baseline, "changed": changed,
                                                               "task": n, "iteration": c.iteration_of(c.label_names(i)) or 0})
            res["gate_s"] = int(time.time() - t0)
        if res["verdict"]["verdict"] == "pending-owner":
            return dict(res, result="awaiting-owner")
        if res["verdict"]["verdict"] == "rejected":
            return dict(res, result="gate-rejected", detail=res["verdict"].get("detail", ""))
        if res["verdict"]["verdict"] != "accepted":
            return dict(res, result="gate-inconclusive", detail=res["verdict"].get("detail", ""))
    if resources.layered() and role == "policymaker" and not boundary:
        return dict(res, result="awaiting-iteration")
    return None


def merge_notes(n, role):
    """An unfinished session does not merge its work, but its journal must not stay on `task/N`: the files are queued
    for the epilogue's one record commit of this session (`session_reports.capture`)."""
    cfg, d = c.cfg(), c.default_branch()
    prof = policymaker_session.profile_of(role) or {}
    br = "task/%s" % n
    c.sync_main()
    if c.git(".", "fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (br, br), check=False, remote=True).returncode:
        return False
    files = [f for f in c.git(".", "diff", "--name-only", "--diff-filter=AM", "origin/%s...origin/%s" % (d, br)).stdout.split()
             if c.path_match(f, cfg["notes_paths"]) and resources.may_write(prof, f)]
    if not files:
        return False
    session_reports.queue_notes(n, "origin/%s" % br, files)
    return True


def merge_outcome(r):
    """(status, report fields, prose) of a merge attempt. `conflict` and the waiting results are handled by the caller."""
    v = r.get("verdict") or {}
    upd = {"check": r["check"]} if r.get("check") else {}
    if v and v.get("verdict") != "pending-owner":
        upd.update(verdict=v.get("verdict"), improved=str(bool(v.get("improved"))).lower(), detail=(v.get("detail") or "")[:300] or None)
    res, detail = r["result"], r.get("detail", "")
    if res == "merged":
        return "done", dict(upd, merged="true"), ""
    if res == "nothing":
        return "done", dict(upd, merged="false"), "No changes to merge."
    if res == "protected":
        return "blocked", dict(upd, merged="false"), "The change touches protected paths: %s. Merge refused." % detail
    if res == "scope":
        return "blocked", dict(upd, merged="false"), "The change writes outside the role's `write` paths: %s. Merge refused." % detail
    if res == "invalid-resources":
        return "blocked", dict(upd, merged="false"), "Invalid managed definitions: %s. Merge refused." % detail
    if res == "large":
        return "blocked", dict(upd, merged="false"), "Files over the size limit: %s. Put large outputs in `.session/artifacts/`." % detail
    if res == "check":
        return "partial", dict(upd, merged="false"), "The check command failed after rebase:\n```\n%s\n```" % detail
    if res == "gate-rejected":
        return "rejected", dict(upd, merged="false"), "The change gate rejected the change. %s" % detail
    if res == "gate-inconclusive":
        return "partial", dict(upd, merged="false"), "The change gate gave no verdict. %s" % detail
    if res == "verification-stale":
        return "blocked", dict(upd, merged="false"), detail
    return "partial", dict(upd, merged="false"), "Rebase on the default branch conflicts."


def tag_unmerged(n, k, upd):
    """A branch that did not enter the main line stays reachable: tag `iter/NNNN/<n>`."""
    if upd.get("merged") == "true" or not upd.get("commit"):
        return
    ahead = c.git(".", "rev-list", "--count", "origin/%s..refs/heads/task/%s" % (c.default_branch(), n), check=False).stdout.strip()
    if ahead not in ("", "0"):
        c.git(".", "push", "-q", "-f", "origin", "refs/heads/task/%s:refs/tags/iter/%04d/%s" % (n, k, n), check=False, remote=True)


def request_verifier(n, i, k, changed):
    role = c.cfg()["verifier_role"]
    c.ensure_label("role:" + role, "1D76DB")
    checked = c.git(".", "rev-parse", "origin/task/%s" % n).stdout.strip()
    rev = resources.evaluator_revision(checked)
    body = ("Verify the evaluator change of task #%s (branch `task/%s`).\n\nChanged evaluator files: %s\n\n"
            "Decide whether the changed evaluator still measures progress toward the goal; the accepted state in force is the default branch.\n\n"
            "<!-- verifies: %s -->\n<!-- start-branch: %s -->\n<!-- checked-commit: %s -->\n"
            "<!-- checked-evaluator-rev: %s -->\n") % (n, n, ", ".join(changed[:20]), n, checked, checked, json.dumps(rev, sort_keys=True))
    v = c.create_issue("Verify evaluator change of task #%s" % n, body, labels=("role:" + role, c.ITER + str(k), "generation:0"))
    c.update_report(n, {"verifier_issue": v, "verified_commit": checked})
    c.edit_labels(n, add=["awaiting:verifier"])
    c.launch(v)
    return v


def settle_verification(n, i, status, result, prose):
    """The verifier ended: `result: pass` merges the evaluator change it verified, anything else sends it back."""
    target = c.marker(i["body"], "verifies")
    if not target or not target.isdigit():
        return status, prose
    t = int(target)
    ti = c.issue(t)
    if ti["state"] != "open" or "awaiting:verifier" not in c.label_names(ti):
        return status, prose                  # repeated epilogue cannot reapply a closed change
    target_fields = c.report_fields(t)
    if target_fields.get("verifier_issue") != str(n):
        return status, prose                  # only the engine-requested verifier may settle this change
    trole = c.roles_of(c.label_names(ti))[0]
    if status == "done" and result == "pass":
        checked = c.marker(i["body"], "checked-commit")
        rev = json.loads(c.marker(i["body"], "checked-evaluator-rev") or "null")
        if not checked or not rev:
            r = {"result": "verification-stale", "detail": "Verifier has no pinned snapshot; request a fresh verification."}
        elif resources.layered() and trole == "policymaker":
            # Verification is evidence, not authority to activate during an iteration.
            # The boundary rechecks the entire pinned candidate and rebased measurement.
            r = {"result": "pending-iteration"}
            it.record_verification(n, i, status, result, r)
            c.edit_labels(t, add=["awaiting:iteration"], remove=["awaiting:verifier"])
            c.update_report(t, {"merged": "pending-iteration", "verifier_passed": "true"},
                            "Verified; the complete managed set waits for the next iteration boundary.")
            return status, prose
        else:
            r = merge_branch(t, trole, ti, verified=True, checked_commit=checked, checked_evaluator_rev=rev, verifier=n)
        it.record_verification(n, i, status, result, r)
        if r["result"] == "awaiting-owner":
            req = requests.open_request("merge-acceptance", "Accept the change of task #%s: %s" % (t, ti["title"]), "The evaluator change of task #%s was verified; its work paths still need your verdict." % t,
                                  "The change gate is set to you.", "Completed merges the branch.", ref="task-%s" % t, iteration=c.iteration_of(c.label_names(ti)))
            c.edit_labels(t, add=["awaiting:owner"], remove=["awaiting:verifier"])
            c.update_report(t, {"merged": "pending-owner", "verifier_passed": "true"}, "Verified; waiting for the owner: request #%s." % req)
            return status, prose
        st, upd, pr = merge_outcome(r)
    else:
        it.record_verification(n, i, status, result, {"result": "not-applied"})
        st, upd, pr = "rejected", {"merged": "false", "verdict": "rejected"}, "The evaluator verifier did not pass the change (result: %s, status: %s)." % (result, status)
    c.edit_labels(t, remove=["awaiting:verifier"])
    closing.close_out(t, ti, st, upd, pr)
    return status, prose
