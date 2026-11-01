# The Perpetua engine

The goal-independent machinery copied into this loop by `/kucze:perpetua-setup`: workflows,
scripts, protected configuration and session contracts. It belongs to this loop.
Terms are in `CONTEXT.md`; `ENGINE_VERSION` identifies the installed engine.

Use the [code map](code-map.md) to find the responsible module and tests.
Details have one home:

- [Managed definitions](managed-resources.md): role layout, ownership, activation,
  rollback and fresh-setup validation.
- [Session instructions and the machine boundary](session-boundary.md): staging, credentials,
  filesystem and network boundaries, output admission and sessions without a shell.
- [Session context](session-context.md): startup reads and working memory.
- [Session data exports](session-exports.md): historical inputs and recipient views.
- [Reports and records](session-reports.md): session reports, the suggestion archive and iteration records.
- [Change provenance](policy-changes.md): accepted changes and evaluator verification in the iteration record.
- [Validation](validation.md): test commands, host requirements and limits of the evidence.

## Setup and updates

Setup clarifies the owner-accepted goal, derives an initial approach, then prepares
the environment it requires. Its active map's `docs/perpetua/setup-notes.md` holds
the outcome checklist, role-writing and arming procedure. Arming preserves decisions
in `docs/perpetua/setup-decisions.md`, records `setup-baseline` and enables the
owner's `AUTOPILOT` switch. The first Policymaker session chooses the execution
organization; setup leaves `shaper: null`.

GitHub Actions, a Claude subscription and GitHub issue owner requests are the
shipped defaults. The owner may replace them during setup by implementing and
checking the integration and documenting each lost or replacement guarantee in
setup decisions. This gives the Policymaker no additional authority after arming.

Plugin changes do not automatically change an installed loop. The explicit
[`/kucze:perpetua-setup update` procedure](https://github.com/Kucze3205/Perpetua/blob/main/skills/perpetua-setup/update.md)
merges the plugin engine with the loop's local changes using the untouched
`perpetua/engine` branch as the merge base. It is separate from fresh setup;
review/reconfiguration by repeating setup is not implemented.

## Parameters of policy

`policy/params.yml` holds the values the Policymaker tunes, each below a kernel hard cap (a higher value is clamped):
`limits.fix_rounds` (cap 5), `limits.successor_generations` (5), `limits.merge_conflict_retries` (5) and
`limits.backoff_hours` (up to 6 steps of 72 h). It also holds the defaults of `policymaker_only_roles`,
`audit_exports_window` (cap 20), `stream_retention_iterations` and `startup_token_warning`.

`cadence` in `policy/params.yml` can only be stricter than the owner's in `engine.yml`: for each bound the engine takes the
stricter value (the longer interval, the smaller daily count), so a looser policy value has no effect.

Machine preparation is `policy/prepare.sh`: the engine stages the accepted copy and the tokenless session job runs it
before the role (none: nothing is prepared).

## One iteration

When a session is staged, `policy/session-state.py` builds its `loop-state.json` and history from the engine's facts
(see `session-context.md`). `policy/session-startup.py` decides its startup reads, bootstrap prompt and budget
warning; the estimate is local, so no step of the engine needs the Anthropic API key.

Iterations run sequentially. Each is an issue labelled `loop:map` and
`loop:iteration N`; its sessions carry `loop:iteration N` and `role:<name>`.

1. **Start.** The watchdog or previous close starts an iteration if cadence allows.
   The trigger rule is `policy/policymaker-trigger.py`: the engine writes facts, the script runs in the tokenless job and
   prints `{"reason": "..."}` or `{"reason": null}` (at most 4 KB). Invalid output fails the iteration and rolls `execution/` and
   `policy/` back. Whatever the script returns, the engine keeps one open Policymaker at a time, calls one at every iteration
   until a session ended `done`, and calls one at the iteration after a failed one (the trigger names the failure).
2. **Policy phase.** When due, the Policymaker session opens the iteration and writes proposed
   definitions and orders. `apply_policy` admits the completed candidate when the session ends
   (or, if it says `"merge": "later"` and waits for its orders, at the end of the phase); completion alone does not activate it.
   The engine starts all valid orders at once; `blocked_by` sets their order. With `"wake": true` the issue waits
   (`awaiting:orders`) and, when all have ended in any status, runs again on the same branch with a fresh context,
   as often as the Policymaker wakes; the engine sets no limit and only opens a
   `notice` owner request at `policy_passes_notice` passes. A session without `wake` ends the phase.
   A failure leaves the accepted organization in force; two consecutive failed sessions (many passes count
   once) roll back managed definitions. See [activation and recovery](managed-resources.md#layered-activation-and-recovery).
   With no trigger due there is no policy phase.
3. **Shape.** The execution phase. The optional execution shaper reads the staged state and writes
   `.session/tasks.json`. The engine
   validates and creates graph issues, blocking edges and consent requests. A graph the engine accepts also goes to
   `policy/graph-rules.py`, if present, which may add refusals (see [layer rules](managed-resources.md#layer-rules-and-activation-validation)).
   Accepted Policymaker orders become tasks alongside it. A shaper without a valid graph, a shaper role without a profile,
   and, with `shaper: null`, no valid orders each fail the iteration and roll both managed roots back.
   See [organizing an iteration](#organizing-an-iteration).
   A graph holds at most 50 tasks (shaper output and orders together); a larger one is refused with that reason and
   creates no issues.
   [Task graph](../../.github/loop/task-graph.md) defines the interface.
4. **Execute.** Free tasks start parallel `session.yml` jobs. Each stages inputs in a token job, executes its role in a job without
   GitHub permissions and hands outputs, as data, to the epilogue job. Closing a
   task releases its consumers; a producer that did not deliver blocks them.
   [Session isolation](session-boundary.md) defines how outputs are admitted.
5. **Close.** Once no session remains open, the engine writes the immutable iteration
   record, closes the issue, prunes streams, checks progress and the goal gate,
   then starts the next iteration.

## Organizing an iteration

The Policymaker chooses how the execution phase is organized; the engine imposes no shape on it. `shaper` in
`policy/params.yml` names the role that writes each iteration's task graph:

- **A model shaper**: an execution role with a local skill (`execution/roles/<role>/SKILL.md`). It plans every
  iteration from the staged state.
- **A script shaper**: an execution role with `agent: false` and a `command` in `execution/profiles.yml`, for example
  `planner: {agent: false, command: "python3 execution/planner.py", write: []}`. The command runs in the worker tree
  without a model or credential, reads `.session/loop-state.json` and `.session/capabilities.json`, writes
  `.session/tasks.json` and exits 0. A policy alone then runs the iterations: no model plans them and the Policymaker
  is called only when a trigger is due or an iteration failed.
- **No shaper** (`shaper: null`): the Policymaker's orders are the only tasks. An iteration without a policy phase has
  none and fails, so this needs a Policymaker at every iteration (`policymaker_triggers.every_iterations: 1`).

Whatever the shaper produces is checked by the same task-graph rules, cap and change gate. A policy that does not work
fails its iteration through the paths above; the rollback returns the last good organization and the next iteration
starts with the Policymaker.

## The change gate

Work enters the default branch after a verdict "not worse than what is accepted". After a task ends `done` the
epilogue, in a throwaway worktree: checks the paths (protected, role `write`, file size), rebases on the default branch,
runs `## Verification` and `check` (each in the tokenless job, on a copy of the candidate tree), then asks the gate. Managed definitions and paths in `direct_paths` (`execution/`, `policy/`, `audit/`, `journal/`) are not
work and skip the check and the gate. A rejected push is retried from the new tip, up to three times.
A failed `## Verification` or `check` is an error the agent can fix: the epilogue runs the same issue again, its
report carrying the error, up to two times (`fixes`). A gate verdict is not retried: `rejected` and `inconclusive` go to the Policymaker.

`gate.kind` in `engine.yml`:

- `owner`: the engine opens a `merge-acceptance` request and the task waits (`awaiting:owner`). Completed merges, not
  planned discards (the branch is kept and tagged `iter/NNNN/<issue>`). This is the default until the setup decides.
- `script`: `gate.command` runs in the **tokenless job** (`tokenless.yml`: `permissions: {}`, no GitHub secret, one
  command on a staged bundle, outputs back as an artifact; [ADR-0006](../../../docs/adr/0006-machine-boundary-instead-of-session-sandbox.md))
  from the **accepted tree**, never from the candidate, with `CANDIDATE_DIR`, `BASELINE_DIR`, `CANDIDATE_SHA`,
  `BASELINE_SHA`, `CHANGED_FILES_FILE`, `TASK`, `ITERATION`, and no secrets (`gate.env_passthrough` does not reach it).
  The epilogue runs no gate command itself; it reads the verdict back. It prints a JSON object as its last line:
  `{"verdict": "accepted|rejected|inconclusive", "improved": true, "detail": "..."}`. A missing, failed or malformed verdict artifact is
  `inconclusive`, never a yes. The engine stores no accepted result: the evaluator compares the candidate with `BASELINE_DIR` itself (and may cache
  its baseline measurement by `BASELINE_SHA`). A judge that calls a model is just a gate command.

`improved` (default: `verdict == accepted`) feeds `no_progress`: the number of trailing iterations without an improving
accepted change.

Evaluator changes additionally require an independent passing verifier pinned to
that evaluator snapshot. See [activation](managed-resources.md#layered-activation-and-recovery)
and [verification records](policy-changes.md).

## The goal gate and the end

`goal/goal.md` (the accepted goal text) and `goal/gate.yml` (`command`, `owner_confirms`) exist only when the owner has
accepted them, in the setup or later through an owner request. After every closed iteration, if a gate command exists
and prints `{"reached": true, ...}` (the command runs in the tokenless job; a job that returns nothing is "not reached"): with `owner_confirms` the engine asks the owner to confirm (a `result-confirmation`
request; completed writes `GOAL_REACHED`, not planned lets the loop continue and is not asked again for the same state);
without it the engine writes `GOAL_REACHED` itself. **Without an accepted gate the loop never ends by itself**; only
switching `AUTOPILOT` off stops it.

## Owner requests

This is the shipped GitHub issue channel. The owner may replace it during setup by implementing its transport,
authority checks and wake-ups. Record which frozen-payload, acceptance/refusal and event-recovery guarantees are
replaced or lost, and update setup topics and arming tools to match. The Policymaker cannot change this channel itself.

The only way the loop asks the owner for a decision, a consent or a confirmation. An issue assigned to the owner (with an
@mention); the answer is closing it: **completed = yes, not planned = no**, with a comment. Only engine scripts create
them. What the owner accepts is the text frozen when the request was opened (a hash in the issue); a change is a new
request. Kinds: `consent`, `merge-acceptance`, `goal-gate`, `goal-change`, `result-confirmation`, `question`, `notice`,
`capability`, `limits-change`, `engine-change`. Sessions propose requests in `.session/requests.json`; the session
protocol defines their fields and the kinds each role may propose.

`capability` asks the owner to supply access, a tool or a named secret and confirm readiness; closing it alone does
not create or verify a secret. `limits-change` automatically replaces only the frozen `limits` block after the owner
accepts it, provided the proposal and its base configuration still match. `engine-change` records a concrete proposal
and base; when the owner closes it as completed, the tokenless job applies the frozen diff to the current `main` and runs the
request's optional test command there; the engine then commits the files it returns (validated as data) and opens
a PR from `engine-change/<proposal_sha12>` (needs "Allow GitHub Actions to create pull requests"). The patch is never
applied or tested on the machine that holds the token. Merging the PR approves, closing it unmerged declines (the watchdog
notices). A diff touching `.github/workflows/**`, one that does not apply cleanly or one that fails its test gets no PR and
the request says why: the owner applies it manually and posts `engine-applied`. These requests
keep trusted snapshots and durable decisions so repeated events cannot reapply a proposal.

Closing a request wakes the loop (`owner-request.yml`); the watchdog sweeps requests whose event was lost.

**Consent.** A task whose role's profile is beyond the accepted limits (`limits` in `engine.yml`: tools outside the list) gets a `consent` request that blocks it with a native edge. `resolve`
starts the session only when that request is closed as completed, and then only that one session runs with the profile.
The task can also carry `consent.secrets` (names only); the frozen text of its granted consent decides which pool
secrets of the `Session` step of `session.yml` that one session gets, and a missing one blocks the task with
`secret-missing:NAME` (see [session secrets](session-boundary.md#session-secrets)). There is no other path for a secret
to reach an agent; `gate.env_passthrough` is the owner's separate list for the gate command (not yet passed to the tokenless job).

## The watchdog

A plain script (`watchdog.yml`, every 30 minutes and on demand), never an agent:

- starts the first iteration after arming, and the next one when a run's end event was lost;
- guards: `AUTOPILOT`, `GOAL_REACHED`, an open `outage` issue (the only quiet state without runs);
- probes the Claude credential: dead or revoked is an outage and a red run (the e-mail to the author of the workflow file is
  the free push channel);
- `idle_resumes` (`engine.yml`, default 3) is the progress breaker: that many consecutive resumes of one parked session
  that used the agent (`agent_s` or tokens) and made no progress (no turns, no new commit) open an outage and the session is not
  resumed again. Waiting for a limit reset without using the agent does not count; parking does not use up resumes, only the
  breaker and the 30-day age limit end a wait. A parked session commits nothing: reports freeze only in a final state.
- three consecutive `crashed` sessions without a commit is an outage; three fruitless kicks of one issue too; three failed
  iterations in a row too;
- resumes parked sessions that are due; relaunches ready sessions nobody started (kicking before calling);
- `ceilings.iteration_minutes` (the Policymaker's own): fails an iteration that runs past it;
- `params.quiet_minutes` (the setup's): an iteration that does not close in time fails, both managed roots are rolled back to the
  state in which the last iteration closed (at the start: the `setup-baseline` tag), and a `notice` is opened for the owner.
  Time spent waiting for the owner counts only when `params.owner_wait_counts` is true.
