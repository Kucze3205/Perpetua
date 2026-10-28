# Finding the right engine code

Paths below are relative to a loop's repository root. In the plugin source they
live under `engine/`; setup copies that directory's contents to the target root.

For a running session, start with `.session/issue.md`, the engine contract in system instructions and
the staged role instructions. Read this map when investigating engine behavior
or preparing an engine-change proposal. The engine remains owner-controlled.

## Sources of truth

| Question | Read |
|---|---|
| What do the domain terms mean? | `CONTEXT.md` |
| What must every session read and produce? | `.github/loop/session-protocol.md`, selected with role-specific sections and supplied in system instructions |
| What may the Policymaker change or order? | `.github/loop/policymaker.md` |
| How should roles do their work? | `<layer>/protocol.md`, `<layer>/roles/<role>/SKILL.md` (`execution` or `policy`) |
| Which settings are owner-controlled? | `engine.yml`; omitted values fall back to `common.DEFAULTS` |
| Which settings may the Policymaker tune? | `policy/params.yml`, both layers' `profiles.yml` |
| Why did a session or change get this outcome? | Its staged report; `session-reports.md` and `policy-changes.md` explain provenance |
| How are instructions staged and network access enforced? | `session-sandbox.md` |
| Which historical data can a role request? | `session-exports.md` |
| How do startup inputs, working memory and historical discovery fit together? | `session-context.md` |

The engine contract defines formats and ownership. Policy defines methods. Keep
format changes in the contract and implementation together; link to the contract
from role instructions instead of copying its schemas into each role.

## Execution and tests

All Python paths below are in `.github/loop/`. Workflows call `loop.py main`.
The engine uses the accepted checkout; files left by a session (committed or not) enter through `fetch_candidate`.

| Behavior | Implementation entry points | Tests in `tests/` |
|---|---|---|
| Session setup and outputs | `loop.resolve`, `loop.stage`, `loop.finalize` | `test_session.py` |
| Iteration lifecycle and task graph | `iteration.start_iteration`, `validate_tasks`, `instantiate`, `advance`, `close_iteration` | `test_flow.py`, `test_pure.py` |
| Candidate checks, change gate, evaluator verification | `loop.merge_branch`, `settle_verification`; `iteration.run_gate`, `common.run_tokenless` | `test_merge.py`, `test_change_records.py` |
| Policymaker tuning and applying finished sessions | `policymaker_session.profile`; `loop.apply_policy` | `test_exports.py`, `test_flow.py` |
| Owner requests | `iteration.open_request`, `process_proposals`; `owner_requests.py`; `loop.on_request_closed` | `test_owner_requests.py` |
| Reports and suggestions | `session_reports.py`; report parsing/publication in `common.py` | `test_reports.py`, `test_pure.py` |
| Fresh-template end-to-end integration | `setup_roles.py`, `loop.py`, `iteration.py` | `test_template_flow.py`; [validation guide](validation.md) |
| Setup role registration and baseline validation | `setup_roles.py` | `test_setup_roles.py` |
| Managed definitions, locations and revision identity | `managed_resources.py` | `test_session.py`, `test_merge.py` |
| Accepted-change and verifier records | `iteration.record_change`, `record_verification` | `test_change_records.py` |
| Historical input staging and recipient views | `session_exports.py`, `session_inputs.py` | `test_exports.py`, `test_input_boundary.py` |
| Startup notices (the reads, prompt and estimate are `policy/session-startup.py`) and offline history | `session_context.py`, `session_startup.py` | `test_context.py` |
| Isolation, credentials and data-only admission; the session artifact | `session_security.py`, `executor_boundary.py`, `session_artifact.py`, `agent.sh`, `offline_exec.py` | `test_security.py`, `test_session_artifact.py`, `test_executor_boundary.py`, `test_model_boundary.py`, `test_static.py` |
| Recovery and parked sessions | `loop.watch`, `drive`, `resume`; `stream_filter.py` | `test_watchdog.py`, `test_stream.py` |

`managed_resources.py` describes the managed definition tree and its role, prompt,
configuration, admission, revision and restore boundaries. See
[managed definitions](managed-resources.md) for the supported layout and activation contract.
`common.py` owns shared configuration, GitHub and git operations. Tests replace
GitHub operations with `tests/fakes.py` and use real temporary git repositories.
Run the suite from the loop repository root:
`python -m unittest discover -s .github/loop/tests`.
From the plugin source use `-s engine/.github/loop/tests` instead.
See [validation](validation.md) for prerequisites and limits of this verification.

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
  and iteration records; `managed_resources.evaluator_revision` maps paths to hashes for
  change and verification records. Preserve these persisted shapes unless migrating
  their readers and schema deliberately.
