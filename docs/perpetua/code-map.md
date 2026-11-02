# Finding the right engine code

For an author changing Perpetua, choose a behavior in the table below, open its
implementation and read the linked contract and tests. No full glossary, ADR or
lifecycle read is required before a focused fix. Plugin setup instructions,
the updater and live E2E tooling are indexed in the source repository's README;
they are not part of this copied engine.

Paths below are relative to a loop's repository root. In the plugin source add
`engine/`: `.github/loop/loop.py` means `engine/.github/loop/loop.py`. Setup copies
the contents of that directory to the target root. Documentation links here stay
within that copy.

For a running session, start with `.session/issue.md`, the engine contract in system instructions and
the staged role instructions. Read this map when investigating engine behavior
or preparing an engine-change proposal. The engine remains owner-controlled.

## Sources of truth

| Question | Read |
|---|---|
| What do the domain terms mean? | `CONTEXT.md` at the repository root (copied separately at setup) |
| What must every session read and produce? | [Session protocol](../../.github/loop/session-protocol.md), selected with role-specific sections and supplied in system instructions |
| What may the Policymaker change or order? | [Policymaker contract](../../.github/loop/policymaker.md) |
| How should roles do their work? | `<layer>/protocol.md`, `<layer>/roles/<role>/SKILL.md` (`execution` or `policy`) |
| Which settings are owner-controlled? | `engine.yml`; omitted values fall back to `common.DEFAULTS` |
| Which settings may the Policymaker tune? | `policy/params.yml`, both layers' `profiles.yml` |
| Why did a session or change get this outcome? | Its staged report; [session reports](session-reports.md) and [change records](policy-changes.md) explain provenance |
| How are instructions staged and the machine boundary kept? | [Session boundary](session-boundary.md) |
| Which historical data can a role request? | [Session exports](session-exports.md) |
| How do startup inputs, working memory and historical discovery fit together? | [Session context](session-context.md) |

The engine contract defines formats and ownership. Policy defines methods. Keep
format changes in the contract and implementation together; link to the contract
from role instructions instead of copying its schemas into each role.

## Execution and tests

All Python paths below are in `.github/loop/`. Workflows call `loop.py main`.
The engine uses the accepted checkout; files left by a session (committed or not) enter through `fetch_candidate`.

| Behavior | Implementation entry points | Contract | Tests in `tests/` |
|---|---|---|---|
| Session resolution and staging | `loop.resolve`, `loop.stage` | [Session boundary](session-boundary.md); [session lifecycle](session-boundary.md#session-lifecycle-and-outputs) | `test_session.py`, `test_agent_chain.py` |
| Session finalization, retries and outputs | `loop.finalize`, `loop.resume` | [Session lifecycle and outputs](session-boundary.md#session-lifecycle-and-outputs) | `test_session_finalize.py` |
| Iteration lifecycle and session launch | `iteration.start_iteration`, `instantiate`, `advance`, `close_iteration` | [One iteration](engine.md#one-iteration) | `test_flow.py`, `test_iteration_import.py` |
| Task validation, dependencies, inputs and frozen graph | `task_graph.validate_tasks`, `create_tasks`, `read`, `width`; `iteration.validate_tasks` adds `policy/graph-rules.py` | [Task graph format](../../.github/loop/task-graph.md) | `test_task_graph.py`, `test_flow.py` |
| Candidate checks, change gate, evaluator verification | `loop.merge_branch`, `settle_verification`; `iteration.run_gate`, `common.run_tokenless` | [Output admission](session-boundary.md#output-admission); [change gate](engine.md#the-change-gate) | `test_merge.py`, `test_flow_gate.py`, `test_change_records.py` |
| Policymaker triggers, orders and policy phases | `iteration.start_policymaker`, `policymaker_due`; `loop.apply_policy` | [Policymaker contract](../../.github/loop/policymaker.md); [layered activation](managed-resources.md#layered-activation-and-recovery) | `test_flow_policymaker.py`, `test_policy_admission.py`, `test_activation.py` |
| Owner consent and approved secrets | `owner_requests.consent_status`, `secrets_of_consent`, `limits_exceeded`; `loop.secret_pool`, `stage`, `finalize` | [Owner requests](engine.md#owner-requests); [session secrets](session-boundary.md#session-secrets) | `test_flow_consent.py` |
| Goal gate and owner confirmation | `iteration.run_goal_gate`; `loop.on_request_closed` | [The goal gate](engine.md#the-goal-gate-and-the-end) | `test_flow_goal.py` |
| Owner requests | `owner_requests.open_request`, `process_proposals`, `settle`; `loop.on_request_closed` | [Owner requests](engine.md#owner-requests) | `test_owner_requests.py` |
| Reports and the suggestion archive | `session_reports.py`; `policy/session-state.py`; report parsing/publication in `common.py` | [Session reports](session-reports.md) | `test_reports.py`, `test_pure.py` |
| Fresh-template integration (simulated GitHub/provider) | `setup_roles.py`, `loop.py`, `iteration.py` | [Validation](validation.md) | `test_template_flow.py` |
| Setup role registration and baseline validation | `setup_roles.prepare`, `install`, `validate` | [New-loop setup](managed-resources.md#new-loop-setup) | `test_setup_roles.py` |
| Managed definitions, locations and revision identity | `managed_resources.py`; `policy/layer-rules.py`, `policy/graph-rules.py` | [Managed definitions](managed-resources.md) | `test_layered_resources.py`, `test_merge.py` |
| Accepted-change and verifier records | `iteration.record_change`, `record_verification` | [Change provenance](policy-changes.md) | `test_change_records.py` |
| Historical input staging and recipient views | `session_exports.py`, `session_inputs.py` | [Session exports](session-exports.md) | `test_exports.py`, `test_input_boundary.py` |
| Startup notices (the reads, prompt and estimate are `policy/session-startup.py`) and offline history | `session_context.py`, `session_startup.py` | [Session context](session-context.md) | `test_context.py` |
| Isolation, credentials and data-only admission; the session artifact | `session_security.py`, `executor_boundary.py`, `session_artifact.py`, `agent.sh` | [Session boundary](session-boundary.md) | `test_security.py`, `test_session_artifact.py`, `test_static.py` |
| Recovery and parked sessions | `loop.watch`, `drive`, `resume`; `stream_filter.py` | [The watchdog](engine.md#the-watchdog) | `test_watchdog.py`, `test_stream.py` |

`managed_resources.py` describes the managed definition tree and its role, prompt,
configuration, admission, revision and restore boundaries. See
[managed definitions](managed-resources.md) for the supported layout and activation contract.
`common.py` owns shared configuration, GitHub and git operations. Tests replace
GitHub operations with `tests/fakes.py` and use real temporary git repositories.
Shared test preparation lives beside the suites, without importing test cases:
`session_fixtures.py` supplies `SessionCase`, its `work()` staging checkout,
`LayeredSessionCase` and `read`; `flow_fixtures.py` supplies `Flow` for iteration
scenarios; `gate_fixtures.py` holds the script gate and `proposal_fixtures.py` a
shared suggestion. Scenario-specific data, such as the secret pool workflow,
stays with its tests. For a new secrets regression start with
`test_flow_consent.py` and `flow_fixtures.py`; for session context start with
`test_context.py` and `session_fixtures.py`, then follow either to `fakes.py`
only when changing the underlying world.
`test_iteration_import.py` deliberately runs selected lifecycle and Policymaker
scenarios in a child process after reloading `iteration`; it tests fresh imports
without contaminating the other suites.

For a new graph rule, read `task_graph.py` and `test_task_graph.py`, then the
shaper-to-record scenario in `test_flow.py` if the rule affects execution. Before
this split, that work required navigating `iteration.py` alongside lifecycle,
policy-phase and owner-request rules, then finding graph tests in `test_pure.py`.
Consent rules now lead directly to `owner_requests.py`; export rules still lead
to `session_exports.py`. Neither requires reading unrelated session-finalization
or recovery code.

## Run the relevant tests

From the **plugin source root**, choose a test file from the table:

```sh
python -m unittest discover -s engine/.github/loop/tests -p test_merge.py
```

From the **installed loop root**, omit `engine/`:

```sh
python -m unittest discover -s .github/loop/tests -p test_merge.py
```

Repeat with the other named files for the behavior you changed. Omit `-p` and its
filename to run the engine suite. Use `python3` if needed. Python with PyYAML and
git on PATH are required; some checks also use a POSIX shell.
Windows skips kernel isolation, symlink and native-CLI cases. For Linux packages,
kernel settings, the pinned CLI and opt-in live-provider checks, follow
[validation](validation.md). A skip leaves that check unverified. Local suites
simulate GitHub/provider outputs and never arm a real loop.

When moving code or tests, update this table in the same change so each route
still reaches its implementation, contract and checks.

## Details that are easy to misread

- `task_graph.py` validates and materializes tasks, wires blocking and consent,
  resolves input references and saves the frozen graph. It imports neither
  `iteration` nor `loop`. `iteration.instantiate` validates and saves first,
  updates phase/statistics second, then launches sessions. Policymaker orders use
  the same graph implementation while their phase handling stays in `iteration`.
- `owner_requests.py` owns request creation, frozen proposals and consent rules;
  `loop.on_request_closed` owns the resulting continuation. Startup checks consent
  again during staging so a changed profile or decision cannot use a stale check.
- `iteration.begin_work` imports and calls `loop.apply_policy` when work begins.
  This local import keeps the dependency explicit without requiring callers to
  import `loop.py` first; the two modules still depend on each other.
- A Policymaker session's iteration label identifies its trigger. Policy changes
  take effect when its pass ends (or at the end of the policy phase); implementation-layer
  orders start in the execution phase. Completion and
  application are distinct events.
- Graph `width` measures the largest dependency-depth level. It is a record
  statistic, not a concurrency cap or a prediction of simultaneous running jobs.
- `iteration.evaluator_rev` is a legacy comma-separated hash string for state
  and iteration records; `managed_resources.evaluator_revision` maps paths to hashes for
  change and verification records. Preserve these persisted shapes unless migrating
  their readers and schema deliberately.

## Plugin E2E tool

In the plugin source, [`e2e/README.md`](../../../e2e/README.md) owns the live
experiment procedure and offline replay instructions. `e2e/live.py` dispatches
commands; `experiment.py` controls the real loop, `collection.py` downloads
evidence, and `analysis.py` computes reports from saved data. For a statistic fix,
start with `analysis.py` and the saved collection; use `live.py analyze OUT`
without GitHub or local live-run state. Tests: `python -m unittest discover -s e2e`.
These tools are not copied into a loop repository by setup.
