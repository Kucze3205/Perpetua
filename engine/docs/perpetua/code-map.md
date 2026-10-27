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
| Why did a session or change get this outcome? | Its staged report; [session reports](session-reports.md) and [change receipts](policy-changes.md) explain provenance |
| How are instructions staged and network access enforced? | [Session sandbox](session-sandbox.md) |
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
| Session setup and outputs | `loop.resolve`, `loop.stage`, `loop.finalize`; `agent.sh` | [Session lifecycle and outputs](session-sandbox.md#session-lifecycle-and-outputs) | `test_session.py`, `test_agent_chain.py` |
| Iteration lifecycle and task graph | `iteration.start_iteration`, `validate_tasks`, `instantiate`, `advance`, `close_iteration` | [One iteration](engine.md#one-iteration); [task format](../../.github/loop/session-protocol.md) | `test_flow.py`, `test_pure.py` |
| Candidate admission and change gate | `loop.fetch_candidate`, `loop.merge_branch`; `iteration.run_gate` | [Filesystem and admission](session-sandbox.md#filesystem-isolation-and-output-admission); [change gate](engine.md#the-change-gate) | `test_merge.py`, `test_policy_admission.py` |
| Evaluator verification and definition activation | `loop.settle_verification`, `loop.apply_policy`; `policy_changes.py` | [Activation and recovery](managed-resources.md#layered-activation-and-recovery); [receipts](policy-changes.md) | `test_activation.py`, `test_merge.py`, `test_policy_changes.py` |
| Policymaker tuning and applying finished sessions | `policymaker_session.profile`; `loop.apply_policy` | [Policymaker contract](../../.github/loop/policymaker.md) | `test_exports.py`, `test_flow.py` |
| Owner requests | `iteration.open_request`, `process_proposals`; `owner_requests.py`; `loop.on_request_closed` | [Owner requests](engine.md#owner-requests) | `test_owner_requests.py` |
| Reports and suggestions | `session_reports.py`; report parsing/publication in `common.py` | [Session reports and iteration records](session-reports.md) | `test_reports.py`, `test_pure.py` |
| Fresh-template integration (simulated GitHub/provider) | `setup_roles.py`, `loop.py`, `iteration.py` | [Validation](validation.md) | `test_template_flow.py` |
| Setup role registration and baseline validation | `setup_roles.prepare`, `setup_roles.install`, `setup_roles.validate` | [New-loop setup](managed-resources.md#new-loop-setup) | `test_setup_roles.py` |
| Managed definitions, locations and revision identity | `managed_resources.py` | [Managed definitions](managed-resources.md) | `test_layered_resources.py`, `test_session.py`, `test_policy_changes.py` |
| Accepted-change receipts | `policy_changes.py` | [Change receipts](policy-changes.md) | `test_policy_changes.py` |
| Historical input staging and recipient views | `session_exports.py`, `session_inputs.py` | [Session exports](session-exports.md) | `test_exports.py`, `test_input_boundary.py` |
| Startup packages, token notices and offline history | `session_context.py`, `session_startup.py` | [Session context](session-context.md) | `test_context.py` |
| Isolation, credentials and data-only admission | `session_security.py`, `executor_boundary.py`, `agent.sh`, `offline_exec.py` | [Session sandbox](session-sandbox.md) | `test_security.py`, `test_executor_boundary.py`, `test_model_boundary.py`, `test_static.py` |
| Recovery and parked sessions | `loop.watch`, `drive`, `resume`; `stream_filter.py` | [Watchdog](engine.md#the-watchdog) | `test_watchdog.py`, `test_stream.py` |

`managed_resources.py` describes the managed definition tree and its role, prompt,
configuration, admission, revision and restore boundaries. See
[managed definitions](managed-resources.md) for the supported layout and activation contract.
`common.py` owns shared configuration, GitHub and git operations. Tests replace
GitHub operations with `tests/fakes.py` and use real temporary git repositories.

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
git on PATH are required; gate and agent-chain checks also use a POSIX shell.
Windows skips kernel isolation, symlink and native-CLI cases. For Linux packages,
kernel settings, the pinned CLI and opt-in live-provider checks, follow
[validation](validation.md). A skip leaves that check unverified. Local suites
simulate GitHub/provider outputs and never arm a real loop.

When moving code or tests, update this table in the same change so each route
still reaches its implementation, contract and checks.

## Details that are easy to misread

- `iteration.begin_work` imports and calls `loop.apply_policy` when work begins.
  This local import keeps the dependency explicit without requiring callers to
  import `loop.py` first; the two modules still depend on each other.
- A Policymaker session's iteration label identifies its trigger. Accepted orders
  and policy changes take effect at a later iteration start. Completion and
  application are distinct events.
- Graph `width` measures the largest dependency-depth level. It is a record
  statistic, not a concurrency cap or a prediction of simultaneous running jobs.
- `iteration.evaluator_rev` is a legacy comma-separated hash string for state
  and iteration records; `policy_changes.evaluator_rev` maps paths to hashes for
  receipts and verification. Preserve these persisted shapes unless migrating
  their readers and schema deliberately.
