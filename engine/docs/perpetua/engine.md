# The Perpetua engine

The goal-independent machinery of this loop: workflows, scripts, configuration, the Policy's prompt, a generic
orchestrator. It was copied into this repository by `/perpetua:setup` and belongs to this loop; the setup map may
change it. Terms (loop, owner, iteration, policy, evaluator...) are in `docs/perpetua/glossary.md`.

Engine version: `ENGINE_VERSION`. There is no update path from the plugin in the first release.

## One iteration

Iterations run one after another. An iteration is an issue labelled `loop:map` + `loop:iteration N`; everything it
does is an issue labelled `loop:iteration N` + `role:<name>`.

```
Policy session ──► macro plan ──► shaper session ──► task graph ──► task sessions (parallel) ──► record
 (blocks; fail-open)  iterations/NNNN/       (orchestrator    issues + blocking     epilogue of each:         iterations/
                      macro-plan.md           or a script)     iterations/NNNN/      rebase, check, gate,      NNNN.json
                                                               tasks.json            merge
```

1. **Start.** The watchdog (or the end of the previous iteration) starts iteration N when none is open, if the
   cadence allows. It snapshots the state (`.session/loop-state.json`: goal, parameters, limits, open owner requests,
   an index line per past iteration, `no_progress`) into a comment of the iteration issue.
2. **Policy.** If `iterations/NNNN/macro-plan.md` already exists (the setup wrote the first one), this step is skipped.
   Otherwise a `role:policy` session decides the macro plan and may change `policy/`. It blocks the iteration with a
   time limit. If it fails, produces no plan or is refused at merge, **the previous plan stands** and the iteration
   goes on (fail-open); two failed Policy sessions in a row roll `policy/` back.
3. **Shaper.** The role named by `shaper` in `policy/params.yml` turns the plan into `.session/tasks.json`: an LLM
   orchestrator, or a script role (a deterministic rule: a profile with `agent: false` and a `command`). It has no
   shell and no token; the engine validates the graph and creates the issues, blocking edges and consent requests.
   The Policy's `orders` in the plan are added as tasks (the only way to the roles in `policy_only_roles`).
   The width of the iteration is the widest layer of the graph, computed, not configured.
4. **Tasks.** Each free task is a `session.yml` run (parallel jobs). When a task closes, what it blocked starts. A
   producer that did not deliver closes its consumers as `blocked`.
5. **Close.** When no session of the iteration is open the engine writes `iterations/NNNN.json` (only the engine writes
   it), closes the iteration, prunes old session streams, raises a notice if there was no progress for too long,
   checks the goal gate, and starts the next iteration.

## Who may change what

| Zone | Contents | Changed by |
|---|---|---|
| Workflows | `.github/workflows/` | the owner only (the loop's token cannot write them) |
| Engine scripts and config | `.github/loop/`, `engine.yml`, `ENGINE_VERSION` | the loop through a pull request the owner merges; sessions cannot write them (`protected`) |
| Policy artifacts | `policy/`, `audit/`, `journal/` | the Policy (`policy/`), the audit role (`audit/`, `policy/metrics/`), the orchestrator (`journal/`); merged without the change gate, still within each role's `write` paths |
| Evaluator | `policy/evaluator/` | only a role whose profile has `owns_evaluator`, ordered only by the Policy, and only after the evaluator verifier passes |
| Records and goal | `iterations/`, `goal/`, `GOAL_REACHED` | the engine only; the owner accepts what goes into `goal/` |
| Work | everything else | task sessions, through the change gate |

The Policy never touches the engine, the goal gate, `AUTOPILOT` or a token. `AUTOPILOT` (repository variable) is the
owner's switch; it is the first condition of every workflow and anything but a literal `on` means stop.

## Sessions

A session is one `session.yml` run: `prep` (guard, credential probe, `resolve`) then `session`: two checkouts (the engine
from the default branch, the agent's own working copy), machine preparation (`.github/loop/prepare.sh`, filled in by the
setup), `stage`, the agent, the epilogue.

- **The agent has no GitHub token, no `gh`, no push rights.** Its Claude credential lives only in the `claude`
  process. The engine checkout is removed from the disk while it works. The epilogue re-checks the engine out and fetches
  the agent's commits as plain objects into the trusted checkout; the agent's repository is never used to run git.
- **Input and output are files** in `.session/`: `issue.md` (the issue and trusted comments), `protocol.md`,
  `loop-state.json` and `macro-plan.md` (Policy, shaper), `inputs/`; `report.md`, `tasks.json`, `requests.json`,
  `artifacts/` go out. See `.github/loop/session-protocol.md`, the contract every role reads.
- **Kinds.** `agent` (a Claude session, pinned CLI) and `script` (a role with `agent: false`: no model, no credential).
- **Limits.** Per-task limits never exceed the ceilings in `policy/params.yml`; the engine enforces time (`timeout`) and
  turns (`--max-turns`) hard. Tokens are recorded, not budgeted; there is no money limit anywhere in the engine.
- **Statuses.** Agent: `wip`, `done`, `partial`, `blocked`, `rejected`. Engine: `paused` (subscription limit; it
  resumes at the reset time, `resume.yml` keeps the clock), `crashed`. A task can also wait: `awaiting:owner` (the
  change gate is the owner) or `awaiting:verifier` (an evaluator change).
- **Report.** One marked comment with a YAML block. Fields the engine owns (tokens, verdict, merged, times...) cannot be
  forged by the agent. `status`, `commit`, `result` are the agent's.
- **Large outputs** (> `large_file_bytes`, binaries) go in `.session/artifacts/`; the epilogue uploads them as assets of
  the release `iter-NNNN` and lists them in the report. A later task names them in `inputs` and the engine downloads
  them before the agent starts.
- **Streams.** The raw `stream-json` of every session is kept as the artifact `stream-NNNN-<issue>` (for
  `stream_retention_iterations` iterations when set). Nothing interprets it; the audit can compute new measures from it.

## The change gate

Nothing enters the default branch without a verdict "not worse than what is accepted". After a task ends `done` the
epilogue, in a throwaway worktree: checks the paths (protected, role `write`, file size), rebases on the default branch,
runs `## Verification` and `check`, then asks the gate. Paths in `direct_paths` (`policy/`, `audit/`, `journal/`) are not
work and skip the check and the gate. A rejected push is retried from the new tip, up to three times.

`gate.kind` in `engine.yml`:

- `owner`: the engine opens a `merge-acceptance` request and the task waits (`awaiting:owner`). Completed merges, not
  planned discards (the branch is kept and tagged `iter/NNNN/<issue>`). This is the default until the setup decides.
- `script`: `gate.command` runs from the **accepted tree** (the default branch checkout), never from the candidate, with
  `CANDIDATE_DIR`, `BASELINE_DIR`, `CANDIDATE_SHA`, `BASELINE_SHA`, `CHANGED_FILES_FILE`, `TASK`, `ITERATION`, and no
  secrets except the names in `gate.env_passthrough`. It prints a JSON object as its last line:
  `{"verdict": "accepted|rejected|inconclusive", "improved": true, "detail": "..."}`. No verdict is `inconclusive`, never a
  yes. The engine stores no accepted result: the evaluator compares the candidate with `BASELINE_DIR` itself (and may cache
  its baseline measurement by `BASELINE_SHA`). A judge that calls a model is just a gate command.

`improved` (default: `verdict == accepted`) feeds `no_progress`: the number of trailing iterations without an improving
accepted change.

**Evaluator changes.** A merge that touches `evaluator_paths` does not merge: the engine opens a session of
`verifier_role` (default `evaluator-verifier`, starting from the branch) and merges only if its report says
`result: pass`; until then the old evaluator is the one in force (it lives on the default branch). A failed check goes
back as a rejected task, visible to the Policy.

## The goal gate and the end

`goal/goal.md` (the accepted goal text) and `goal/gate.yml` (`command`, `owner_confirms`) exist only when the owner has
accepted them, in the setup or later through an owner request. After every closed iteration, if a gate command exists
and prints `{"reached": true, ...}`: with `owner_confirms` the engine asks the owner to confirm (a `result-confirmation`
request; completed writes `GOAL_REACHED`, not planned lets the loop continue and is not asked again for the same state);
without it the engine writes `GOAL_REACHED` itself. **Without an accepted gate the loop never ends by itself**; only
switching `AUTOPILOT` off stops it.

## Owner requests

The only way the loop asks the owner for a decision, a consent or a confirmation. An issue assigned to the owner (with an
@mention); the answer is closing it: **completed = yes, not planned = no**, with a comment. Only engine scripts create
them. What the owner accepts is the text frozen when the request was opened (a hash in the issue); a change is a new
request. Kinds: `consent`, `merge-acceptance`, `goal-gate`, `goal-change`, `result-confirmation`, `question`, `notice`.
Sessions may propose `goal-gate`, `goal-change` and `question` in `.session/requests.json`.

Closing a request wakes the loop (`owner-request.yml`); the watchdog sweeps requests whose event was lost.

**Consent.** A task whose role's profile is beyond the accepted limits (`limits` in `engine.yml`: tools outside the list,
internet with writes outside `internet_write`) gets a `consent` request that blocks it with a native edge. `resolve`
starts the session only when that request is closed as completed, and then only that one session runs with the profile.
Secrets for such a session are added by the owner to the `Session` step of `session.yml`, gated on `consented`
(an example is in the file); there is no other path for a secret to reach an agent.

## The watchdog

A plain script (`watchdog.yml`, every 30 minutes and on demand), never an agent:

- starts the first iteration after arming, and the next one when a run's end event was lost;
- guards: `AUTOPILOT`, `GOAL_REACHED`, an open `outage` issue (the only quiet state without runs);
- probes the Claude credential: dead or revoked is an outage and a red run (the e-mail to the author of the workflow file is
  the free push channel);
- three consecutive `crashed` sessions without a commit is an outage; three fruitless kicks of one issue too; three failed
  iterations in a row too;
- resumes parked sessions that are due; relaunches ready sessions nobody started (kicking before calling);
- `ceilings.iteration_minutes` (the Policy's own): fails an iteration that runs past it;
- `params.quiet_minutes` (the setup's): an iteration that does not close in time fails, `policy/` is rolled back to the
  state in which the last iteration closed (at the start: the `setup-baseline` tag), and a `notice` is opened for the owner.
  Time spent waiting for the owner counts only when `params.owner_wait_counts` is true.

## The iteration record

`iterations/NNNN.json`, written once by the engine, never edited: `schema_version`, `iteration`, `status`
(`closed`/`failed`; the last good state is the last `closed`), `started`, `ended`, `engine_version`, `input_state` (the
snapshot the Policy saw, without the index), `policy` (its session, `rev_start`, `rev_end`, `commit_end`, `plan_source`:
`setup`/`policy`/`stood`), `macro_plan` (+ `decided_by`), `tasks` (the frozen graph), `outcome` (sessions with status,
verdict, tokens in/out/cache and by model, turns, agent/gate/total/parked seconds, runner, branch, commit, stream name;
the shaper; the iteration verdict; token sums; wall time; critical path; width; owner requests opened and open at close).
The offline module (not shipped) replays decisions from `input_state` plus the index.

## Setup topics and where they write

| Topic | Writes |
|---|---|
| A goal | `goal/goal.md` (accepted by the owner) |
| B environment | `.github/loop/prepare.sh`; `engine.yml`: `cli_version`, `owner`, `author`; repository secrets and settings; the first macro plan if wanted: `iterations/0000/macro-plan.md` |
| C evaluator and change gate | `engine.yml`: `check`, `gate`, `protected`, `evaluator_paths`; `policy/evaluator/` |
| D goal reformulation and gate | `goal/goal.md`, `goal/gate.yml` (`command`, `owner_confirms`) |
| E starting roles | `policy/roles/<role>/SKILL.md`, `policy/profiles.yml` (from `policy/roles/ROLE-TEMPLATE.md`) |
| F actions and spending | `engine.yml`: `limits`; consent secrets in `session.yml` |
| G parameters | `engine.yml`: `params`, `cadence`; `policy/params.yml`: `criterion`, `ceilings`, `models` |
| H arming | merge the setup branch, tag `setup-baseline`, set `AUTOPILOT=on` (the watchdog starts iteration 0; `gh workflow run watchdog.yml` starts it now) |

If the setup wrote no macro plan for iteration 0, the first iteration begins with the Policy session instead.

## Not exercised here

The test suite (below) runs the engine against an in-memory GitHub and real git repositories. It cannot prove the YAML
of the workflows against GitHub's runner, the exact flags of `gh` for releases, issue assignment and run cancellation, the
bubblewrap sandbox on a runner, or `claude -p` with the hook settings: those run for the first time in the trial loop.

## Tests

`python -m unittest discover -s .github/loop/tests` (needs `pyyaml`, `git`, `bash`). `fakes.py` is the fake GitHub and the
git world. In a loop's repository the suites for the template (`test_static.py`) skip themselves.
