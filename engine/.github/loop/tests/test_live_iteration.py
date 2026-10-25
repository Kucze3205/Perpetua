"""One real iteration of the loop on light models, with measurements for the scale of ~50 iterations.

Opt-in like the provider matrix of test_model_boundary.py: it needs a non-root Linux host with bubblewrap, socat and the
pinned native Claude CLI, and runs only with LOOP_NETWORK_INTEGRATION=1 plus CLAUDE_CODE_OAUTH_TOKEN. It is outside the
default suite's reach: a skip is an outstanding check. From engine/:
    LOOP_NETWORK_INTEGRATION=1 CLAUDE_CODE_OAUTH_TOKEN=... PYTHONPATH=.github/loop \
        python3 -m unittest discover -s .github/loop/tests -p test_live_iteration.py -v

A fresh shipped template carries one goal, one worker role and one planted defect (a duplicated step in the worker's
skill). Every role runs on Haiku 5.5 through the real agent.sh and the real CLI, in the real executor boundary. GitHub
is the fake hub of fakes.py and git is real, as in test_template_flow.py. The test plays one whole iteration:
Policymaker -> shaper -> tasks -> iteration record. Sessions run one after another, not in parallel.

It collects data for a later analysis and writes it to $LOOP_E2E_OUT (default: a new directory in the temp
directory; the path is printed). README.md in that directory says what each file holds. Extrapolated values (x50) and
scale thresholds are reported, never asserted. Assertions cover the engine contract only: sessions end, reports parse,
the iteration record exists, policy changes are decided, session limits hold. Never the content of a decision.
"""
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import fakes
from fakes import common as c, iteration as it, loop
import policy_changes
import setup_roles

HAIKU = "claude-haiku-5-5"
SCALE = 50                      # iterations the one measured iteration is extrapolated to
CONTEXT_TOKENS = 250_000        # the session context the loop should stay under at that scale
MAX_TURNS, MINUTES = 30, 10     # limits of every role in this environment
MAX_SESSIONS = 12               # one iteration of this environment needs 3-5; more is a runaway
PLANT = "Read `.session/issue.md` in full a second time"
GOAL = "# Goal\n\nCreate `src/greeting.txt` containing the single line `hello from perpetua`.\n"
GATE = "echo '{\"verdict\": \"accepted\", \"improved\": true, \"detail\": \"e2e gate\"}'\n"
NOTE = ("\n## Environment note\n\nThis is a small trial environment. The `worker` skill in "
        "`execution/roles/worker/SKILL.md` may contain steps that waste effort or are inconsistent. If you find one, "
        "fix it in that file and say so in the report. Keep the session short.\n")
WORKER_SKILL = f"""---
name: worker
description: Writes the one small text file its task asks for, then reports.
---

# Worker

Do exactly what `.session/issue.md` asks.

## Steps

1. Read `.session/issue.md` and `.session/protocol.md`.
2. {PLANT} and copy every acceptance line into your notes before touching anything.
3. Read `.session/issue.md` in full a third time and compare it with your notes line by line.
4. Write the requested file under `src/`.
5. Read the file back and confirm its content.
6. Write `.session/report.md` with status `done`.
"""
LIVE = (sys.platform == "linux" and shutil.which("bwrap") and shutil.which("claude")
        and os.environ.get("LOOP_NETWORK_INTEGRATION") == "1" and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))


def tokens(text):
    return math.ceil(len(text) / 4)                 # characters / 4: the same rough estimate everywhere


def read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def snapshot(directory):
    """relative path -> (bytes, sha256, estimated tokens) for every regular file; links are not followed."""
    found = {}
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            found[path.relative_to(directory).as_posix()] = (
                len(raw), hashlib.sha256(raw).hexdigest(), tokens(raw.decode("utf-8", "replace")))
    return found


def seen_in(directory, needle):
    return sorted(p.relative_to(directory).as_posix() for p in Path(directory).rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(directory).parts and needle in read(p))


def keep(source, destination):
    if Path(source).is_dir():
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
    elif Path(source).is_file():
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def analyze_stream(path):
    """What the CLI actually did, from its raw stream-json: model call, tools offered and used, errors, repeats."""
    events = []
    for line in (read(path).splitlines() if Path(path).is_file() else []):
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    calls, answers, fills = [], {}, []
    for event in events:
        blocks = (event.get("message") or {}).get("content")
        for block in blocks if isinstance(blocks, list) else []:
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                calls.append({"id": block.get("id"), "tool": block.get("name"), "input": block.get("input")})
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                answers[block.get("tool_use_id")] = block
        if event.get("type") == "assistant":
            usage = event["message"].get("usage") or {}
            fills.append(sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
    errors = [{"tool": x["tool"], "input": x["input"], "error": json.dumps(answers[x["id"]].get("content"))[:300]}
              for x in calls if answers.get(x["id"], {}).get("is_error")]
    repeats = Counter(json.dumps([x["tool"], x["input"]], sort_keys=True) for x in calls)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    return {"events": len(events), "model": init.get("model"), "tools_offered": init.get("tools"),
            "tool_calls": dict(Counter(x["tool"] for x in calls)), "calls": [[x["tool"], x["input"]] for x in calls],
            "tool_errors": errors, "unanswered_calls": sum(1 for x in calls if x["id"] not in answers),
            "repeated_calls": {k: v for k, v in repeats.items() if v > 1},
            "first_call_context_tokens": fills[0] if fills else None, "peak_context_tokens": max(fills, default=None),
            "model_calls": len(fills), "result": result}


def input_tokens(inputs, trusted):
    """Estimated tokens of everything the engine staged for a session: its files, prompt and system contract."""
    fixed = {name: tokens(read(Path(trusted, name))) for name in ("prompt.md", "contract.md") if Path(trusted, name).is_file()}
    return {"staged_files": sum(v[2] for v in inputs.values()), **fixed}


def accumulating(inputs, session_dir):
    """Tokens of staged input that grows with the number of iterations: history, reports, exports, streams."""
    total = sum(v[2] for rel, v in inputs.items() if re.match(r"(reports|exports|streams|inputs)/", rel))
    state = Path(session_dir, "loop-state.json")
    if state.is_file():
        doc = json.loads(read(state))
        total += sum(tokens(json.dumps(doc.get(k, []), ensure_ascii=False)) for k in ("iterations", "suggestions"))
    return total


@unittest.skipUnless(LIVE, "requires Linux bubblewrap, the pinned native Claude CLI, LOOP_NETWORK_INTEGRATION=1 and CLAUDE_CODE_OAUTH_TOKEN")
class LiveIterationTest(unittest.TestCase):
    def setUp(self):
        self.token = os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN")      # only agent.sh may hand it to a CLI
        self.addCleanup(os.environ.__setitem__, "CLAUDE_CODE_OAUTH_TOKEN", self.token)
        self.w = fakes.World(first_iteration=True)
        self.addCleanup(self.w.close)
        self.out = Path(os.environ.get("LOOP_E2E_OUT") or tempfile.mkdtemp(prefix="perpetua-e2e-"))
        self.out.mkdir(parents=True, exist_ok=True)
        root = Path(self.w.loop)
        for name in ("execution", "policy", "templates"):
            shutil.rmtree(root / name, ignore_errors=True)
            shutil.copytree(Path(fakes.ENGINE, name), root / name)
        shutil.copyfile(Path(fakes.ENGINE, "engine.yml"), root / "engine.yml")
        setup_roles.prepare(root)
        light = {"effort": "low", "max_turns": MAX_TURNS, "timeout_minutes": MINUTES}
        for role, layer in [("orchestrator", "execution"), ("audit", "policy"), ("evaluator", "policy"),
                            ("evaluator-verifier", "policy"), ("policymaker", "policy")]:
            profile = c.load_yaml(root / "templates" / layer / "profiles.yml").get(role)
            if profile:
                profile = dict({k: v for k, v in profile.items() if k != "model"}, **light)
            setup_roles.install(root, role, layer, root / "templates" / layer / "roles" / role, profile)
        source = Path(self.w.tmp, "worker-skill")
        source.mkdir()
        (source / "SKILL.md").write_text(WORKER_SKILL, encoding="utf-8", newline="\n")
        setup_roles.install(root, "worker", "execution", source, dict(
            light, tools=["Read", "Grep", "Glob", "Write", "Edit", "Bash(git:*)", "Bash(python3:*)"], write=["src/**"]))
        params = c.load_yaml(root / "policy/params.yml")
        params.update(shaper="orchestrator", models={"default": HAIKU, "opus": HAIKU},
                      policymaker_triggers={"every_iterations": None, "no_progress": None},
                      policymaker_session={"model": HAIKU, "effort": "low", "max_turns": MAX_TURNS, "timeout_minutes": MINUTES})
        setup_roles.write_yaml(root / "policy/params.yml", params)
        config = c.load_yaml(root / "engine.yml")
        config["gate"] = {"kind": "script", "command": "bash policy/evaluator/gate.sh"}
        config["policymaker_session_ceilings"] = {"models": [HAIKU], "efforts": ["low"], "max_turns": MAX_TURNS, "timeout_minutes": MINUTES}
        config["cadence"] = {"min_interval_minutes": 10 ** 6, "daily_iterations": None}     # one iteration, not two
        setup_roles.write_yaml(root / "engine.yml", config)
        with open(root / "policy/prompts/policymaker.md", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(NOTE)
        self.w.write(root, "policy/evaluator/gate.sh", GATE)
        self.w.write(root, "goal/goal.md", GOAL)
        fakes.sh(root, "git", "add", "-A")
        fakes.sh(root, "git", "commit", "-qm", "owner accepted explicit setup")
        fakes.sh(root, "git", "tag", "setup-baseline")
        fakes.sh(root, "git", "push", "-q", "origin", "main")
        self.initial = setup_roles.validate(root, "setup-baseline")
        self.baseline = fakes.sh(root, "git", "rev-parse", "setup-baseline")
        os.environ["GITHUB_OUTPUT"] = str(root.parent / "outputs")
        home = Path(self.w.tmp, "home")
        home.mkdir()
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)

    # ---------------------------------------------------------------- playing the iteration

    def play(self, issue, seq):
        """What session.yml does for one issue: resolve, check out, stage, run agent.sh, then the epilogue."""
        w, outputs = self.w, Path(os.environ["GITHUB_OUTPUT"])
        outputs.write_text("")
        try:
            loop.resolve(issue)
        except SystemExit as stopped:
            return {"issue": issue, "seq": seq, "not_started": str(stopped)}
        opts = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
        role = opts["role"]
        directory = self.out / "sessions" / ("%02d-%s-issue%d" % (seq, role, issue))
        work, rt = Path(w.tmp, "work-%d" % issue), Path(w.tmp, "rt-%d" % issue)
        trusted = rt / "trusted"
        fakes.sh(w.tmp, "git", "clone", "-q", "-c", "core.autocrlf=false", w.origin, str(work))
        fakes.sh(work, "git", "config", "core.autocrlf", "false")
        if opts["start"] != "main":
            fakes.sh(work, "git", "checkout", "-q", opts["start"])
        rt.mkdir()
        os.environ["RUNNER_TEMP"] = str(rt)
        (rt / "session-start").write_text(str(int(time.time())))
        loop.stage(issue, str(work), str(trusted))
        before = snapshot(work / ".session")
        planted = seen_in(work, PLANT)
        keep(work / ".session", directory / "inputs")
        for name in ("prompt.md", "contract.md", "settings.json", "tools", "network-mode", "executor-mode"):
            keep(trusted / name, directory / "trusted" / name)
        env = dict(os.environ, WORK=str(work), ISSUE=str(issue), KIND=opts["kind"], ROLE=role, MODEL=opts["model"],
                   EFFORT=opts["effort"], TOOLS=opts["tools"], MAX_TURNS=opts["max_turns"], AGENT_TIMEOUT=opts["agent_timeout"],
                   ITERATION=opts["iteration"], PROFILE_DIGEST=opts["profile_digest"], DEFAULT_BRANCH="main", LOOP_SANDBOX="1",
                   CLAUDE_CODE_OAUTH_TOKEN=self.token if opts["kind"] == "agent" else "")
        started = time.time()
        proc = subprocess.run(["bash", str(trusted / "agent.sh")], env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=int(opts["agent_timeout"]) * 60 + 300)
        wall = time.time() - started
        after = snapshot(work / ".session")
        # files the session left outside .session/ and not committed: the epilogue admits them as the session's writes
        left = [line for line in fakes.sh(work, "git", "status", "--porcelain", "--untracked-files=all").splitlines()]
        for rel in after:
            if before.get(rel, (0, ""))[1] != after[rel][1]:
                keep(work / ".session" / rel, directory / "outputs" / rel)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "console.txt").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
        for name in ("stream.ndjson", "claude-execution-output.json", "claude-stderr.txt", "agent-exit", "agent-start", "agent-end"):
            keep(rt / name, directory / name)
        stream = analyze_stream(rt / "stream.ndjson")
        os.environ.update(RUNNER_TEMP=str(rt), MODEL=opts["model"], EFFORT=opts["effort"])
        w.hub.finish_runs()
        c.LAUNCHED.clear()                          # every epilogue is a fresh process
        loop.finalize(issue, str(work))
        report = w.hub.report(issue)
        (directory / "report-published.md").write_text(w.hub.prose(issue), encoding="utf-8")
        session = {"issue": issue, "seq": seq, "role": role, "dir": directory.name, "options": opts, "wall_s": round(wall),
                   "agent_exit": read(rt / "agent-exit").strip() if (rt / "agent-exit").is_file() else None,
                   "report": report, "state": w.hub.issue(issue)["state"], "stream": stream,
                   "staged": {"files": {k: v[0] for k, v in before.items()}, "tokens": input_tokens(before, trusted),
                              "accumulating_tokens": accumulating(before, work / ".session")},
                   "planted_step_visible_in": planted, "uncommitted_in_work_after_session": left,
                   "written_by_session": sorted(rel for rel in after if before.get(rel, (0, ""))[1] != after[rel][1]),
                   "stream_bytes": (rt / "stream.ndjson").stat().st_size if (rt / "stream.ndjson").is_file() else 0,
                   "token_in_output": any(self.token.encode() in p.read_bytes() for p in directory.rglob("*") if p.is_file())}
        (directory / "analysis.json").write_text(json.dumps(session, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        return session

    def drive(self):
        it.start_iteration()
        sessions = []
        while (issue := self.w.next_dispatch()) is not None:
            self.assertLess(len(sessions), MAX_SESSIONS, "runaway: more sessions than one iteration of this environment needs")
            sessions.append(self.play(issue, len(sessions) + 1))
        return sessions

    def probe_next_policymaker(self):
        """Stage (not run) the next Policymaker session: what would it see after this one iteration?"""
        w = self.w
        c.sync_main()
        with patch.object(loop, "reconcile"):
            issue = it.start_policymaker(1, "e2e probe: the inputs of the next Policymaker session")
            work = Path(w.tmp, "probe-work")
            fakes.sh(w.tmp, "git", "clone", "-q", "-c", "core.autocrlf=false", w.origin, str(work))
            loop.stage(issue, str(work), str(Path(w.tmp, "probe-trusted")))
        w.hub.close_issue(issue, "not planned")
        keep(work / ".session", self.out / "next-policymaker" / "session")
        tree = {p.relative_to(work).as_posix(): p for p in work.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(work).parts and ".session" not in p.relative_to(work).parts}
        (self.out / "next-policymaker" / "repository-files.json").parent.mkdir(parents=True, exist_ok=True)
        (self.out / "next-policymaker" / "repository-files.json").write_text(
            json.dumps({k: v.stat().st_size for k, v in tree.items()}, indent=1), encoding="utf-8")
        return {"issue": issue, "session": snapshot(work / ".session"), "tree": {k: v.stat().st_size for k, v in tree.items()},
                "texts": {**{"session/" + k: read(work / ".session" / k) for k in snapshot(work / ".session")},
                          **{k: read(v) for k, v in tree.items()}}}

    # ---------------------------------------------------------------- measurements

    def measure(self, sessions, probe):
        w, done = self.w, [s for s in sessions if "not_started" not in s]
        c.sync_main()
        record_text = w.origin_file(it.record_path(0))
        record = json.loads(record_text) if record_text else None
        worker = next((s for s in done if s["role"] == "worker"), None)
        policymaker = next((s for s in done if s["role"] == "policymaker"), None)
        first_state = next((s for s in done if "loop-state.json" in s["staged"]["files"]), None)
        state_now = probe["session"].get("loop-state.json", (0,))[0]
        growth = {
            "iteration record, bytes": len(record_text or ""),
            "loop-state.json after one iteration vs. at its start, bytes": state_now - (first_state["staged"]["files"]["loop-state.json"] if first_state else 0),
            "journal/, bytes": sum(v for k, v in probe["tree"].items() if k.startswith("journal/")),
            "audit/, bytes": sum(v for k, v in probe["tree"].items() if k.startswith("audit/")),
            "raw session streams, bytes": sum(s["stream_bytes"] for s in done),
            "session reports and comments on the iteration issue, bytes": sum(len(x["body"]) for v in w.hub.cmts.values() for x in v),
            "tokens in + out of all sessions": sum(int(s["report"].get("tokens_in", 0) or 0) + int(s["report"].get("tokens_out", 0) or 0) for s in done),
            "turns of all sessions": sum(int(s["report"].get("turns", 0) or 0) for s in done),
            "agent seconds of all sessions": sum(int(s["report"].get("agent_s", 0) or 0) for s in done),
            "wall seconds of the iteration": (record or {}).get("outcome", {}).get("wall_s"),
        }
        scale = {k: {"this_iteration": v, f"x{SCALE}": v * SCALE if isinstance(v, (int, float)) else None} for k, v in growth.items()}
        inputs = []
        for s in done:
            t = s["staged"]["tokens"]
            total = sum(t.values())
            grows = s["staged"]["accumulating_tokens"]
            inputs.append({"issue": s["issue"], "role": s["role"], "estimated_input_tokens": total, **t,
                           "accumulating_part": grows, f"projection_x{SCALE}_everything": total * SCALE,
                           f"projection_x{SCALE}_accumulating_part_only": total - grows + grows * SCALE,
                           "measured_first_call_context_tokens": s["stream"]["first_call_context_tokens"],
                           "measured_peak_context_tokens": s["stream"]["peak_context_tokens"],
                           f"over_{CONTEXT_TOKENS}_tokens": total - grows + grows * SCALE > CONTEXT_TOKENS})
        needles = {
            "worker session listed in the iteration record": f'"issue": {worker["issue"]}' if worker else None,
            "worker's report prose": re.sub(r"\s+", " ", w.hub.prose(worker["issue"]).split("```", 2)[-1]).strip()[:60] if worker else None,
            "the planted step, in the worker's skill": PLANT,
            "the worker's repeated Read of its issue, in a stream": "issue.md",
        }
        seen = {name: sorted(k for k, text in probe["texts"].items() if needle and needle in (text if name != "worker's report prose" else re.sub(r"\s+", " ", text)))
                for name, needle in needles.items()}
        seen["the worker's repeated Read of its issue, in a stream"] = [k for k in seen["the worker's repeated Read of its issue, in a stream"] if k.startswith("session/streams/")]
        manifest = probe["texts"].get("session/exports/manifest.json")
        pm =policymaker["stream"] if policymaker else {}
        managed = [x for x in pm.get("calls", []) if x[0] in ("Write", "Edit") and re.search(r"(^|/)(execution|policy)/", str((x[1] or {}).get("file_path", "")))]
        origin_branch = fakes.sh(w.origin, "git", "branch", "--list", "task/%d" % policymaker["issue"]) if policymaker else ""
        changed = fakes.sh(w.origin, "git", "diff", "--name-only", self.baseline, "task/%d" % policymaker["issue"], "--", "execution", "policy") if origin_branch else ""
        mechanics = {
            "policymaker tools offered": pm.get("tools_offered"), "policymaker tool errors": pm.get("tool_errors"),
            "policymaker tried to write execution/ or policy/": [x[1].get("file_path") for x in managed],
            "files of execution/ and policy/ changed on its branch": changed.splitlines(),
            "its report": (policymaker or {}).get("report"),
            "receipt of the accepted change": bool(policymaker and w.origin_file(policy_changes.path(policymaker["issue"]))),
            "orders in the record": [t.get("id") for t in (record or {}).get("tasks", []) if str(t.get("id", "")).startswith("order-")],
            "orders refused": ((record or {}).get("policy") or {}).get("orders_refused"),
        }
        return {"record": record, "growth": scale, "session_inputs": inputs, "visibility_to_next_policymaker": seen,
                "planted_step_visible_to_sessions": {f"{s['seq']} {s['role']}": s["planted_step_visible_in"] for s in done},
                "exports_manifest_of_next_policymaker": json.loads(manifest) if manifest else None, "mechanics": mechanics}

    def write_report(self, sessions, measured):
        repo = Path(fakes.ENGINE).parent
        run = {"platform": platform.platform(), "python": sys.version.split()[0],
               "claude_cli": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
               "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip(),
               "dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout.strip()),
               "command": " ".join(sys.argv), "model": HAIKU, "scale": SCALE, "context_tokens": CONTEXT_TOKENS,
               "limits": {"max_turns": MAX_TURNS, "minutes": MINUTES},
               "sessions": [{k: s.get(k) for k in ("seq", "issue", "role", "dir", "wall_s", "state", "not_started")} for s in sessions]}
        (self.out / "report.json").write_text(json.dumps(dict(run=run, **measured), indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        rows = ["| %s | %s | %s | %s | %s | %s |" % (s["seq"], s["role"], s["issue"], s["report"].get("status"), s["report"].get("turns"), s["wall_s"])
                for s in sessions if "not_started" not in s]
        (self.out / "README.md").write_text(f"""# Data of one real loop iteration (Haiku 5.5)

Written by `test_live_iteration.py`. Read `report.json` first, then follow `dir` of a session to `sessions/<dir>/`.

- `report.json`: the run (platform, CLI, commit, command), the sessions, the iteration record, growth of everything that
  accumulates with iterations and its x{SCALE} extrapolation (value of this iteration x {SCALE}, no simulated history),
  the estimated input of each session against {CONTEXT_TOKENS} tokens (characters / 4), what the next Policymaker session
  would be given, and the Policymaker's mechanical ability to change policy. Nothing in it is a verdict.
- `sessions/<dir>/inputs/`: the whole `.session/` the engine staged. `outputs/`: the files the session wrote or changed.
  `trusted/`: prompt, system contract, settings, tools. `stream.ndjson`: the raw CLI stream. `analysis.json`: tools
  offered and used, tool errors, repeated calls, context fill, the result event. `report-published.md`: the report as
  the engine published it. `console.txt`: what agent.sh printed.
- `next-policymaker/`: the `.session/` staged for a Policymaker session after this iteration (staged, not run), and the
  sizes of the repository files it would check out.
- Harness limits: sessions ran one after another; GitHub is a fake hub, so Actions artifacts do not exist and the `streams`
  and `actions` exports are `unavailable` by construction.

| # | role | issue | status | turns | wall s |
|---|---|---|---|---|---|
""" + "\n".join(rows) + "\n", encoding="utf-8")
        for path in self.out.rglob("*"):            # the credential never goes into the data
            if path.is_file() and self.token.encode() in path.read_bytes():
                path.write_bytes(path.read_bytes().replace(self.token.encode(), b"[credential]"))
        print("\nE2E data: %s" % self.out)

    # ---------------------------------------------------------------- the test

    def test_one_real_iteration_and_its_scale_measurements(self):
        sessions = self.drive()
        measured = self.measure(sessions, self.probe_next_policymaker())
        self.write_report(sessions, measured)
        record, done = measured["record"], [s for s in sessions if "not_started" not in s]
        self.assertEqual([s for s in sessions if "not_started" in s], [], "a session did not start")
        self.assertIsNotNone(record, "no iteration record was written")
        self.assertEqual((record["schema_version"], record["iteration"]), (1, 0))
        self.assertIn(record["status"], ("closed", "failed"))
        self.assertEqual([x for x in self.w.hub.list_issues(state="all") if "outage" in c.label_names(x)], [], "the engine opened an outage")
        self.assertEqual([x["number"] for x in self.w.hub.list_issues() if c.in_loop(c.label_names(x))], [], "an issue of the loop is still open")
        for s in done:
            where = "session #%s (%s)" % (s["issue"], s["role"])
            result = s["stream"]["result"]
            self.assertIsNotNone(result, where + ": the CLI left no result; stderr: " + read(Path(self.out, "sessions", s["dir"], "claude-stderr.txt"))[:500])
            self.assertTrue(not result.get("is_error") or result.get("subtype") == "error_max_turns", where + ": the provider call failed: " + str(result.get("result"))[:300])
            self.assertGreaterEqual(result.get("num_turns", 0), 1, where)
            self.assertIn(HAIKU, result.get("modelUsage", {}), where + ": not the model the profile named")
            self.assertLessEqual(result["num_turns"], MAX_TURNS + 1, where + ": max_turns not respected")
            self.assertLessEqual(int(s["report"].get("agent_s") or 0), MINUTES * 60 + 60, where + ": timeout not respected")
            self.assertIn(s["report"].get("status"), c.AGENT_STATUSES | {"crashed"}, where + ": the report did not parse to a status")
            self.assertEqual(s["state"], "closed", where)
            self.assertFalse(s["token_in_output"], where + ": the credential reached the session's data")
        pm = measured["mechanics"]["its report"] or {}
        self.assertNotIn(pm.get("merged"), ("pending-iteration", "pending-owner", "pending-verifier"), "the Policymaker's change is neither applied nor refused")
        self.assertEqual(len(record["outcome"]["sessions"]), len([s for s in done if s["role"] != "policymaker"]))


if __name__ == "__main__":
    unittest.main()
