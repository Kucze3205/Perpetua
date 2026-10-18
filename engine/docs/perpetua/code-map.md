# Finding the right engine code

Paths below are relative to a loop's repository root. In the plugin source they
live under `engine/`; setup copies that directory's contents to the target root.

For a running session, start with `.session/issue.md`, `.session/protocol.md` and
the staged role instructions. Read this map when investigating engine behavior
or preparing an engine-change proposal. The engine remains owner-controlled.

## Sources of truth

| Question | Read |
|---|---|
| What do the domain terms mean? | `CONTEXT.md` |
| What must every session read and produce? | `.github/loop/session-protocol.md`, staged in `.session/protocol.md` |
| What may the Policymaker change or order? | `.github/loop/policymaker.md` |
| How should roles do their work? | `policy/protocol.md`, `policy/roles/<role>/SKILL.md` |
| Which settings are owner-controlled? | `engine.yml`; omitted values fall back to `common.DEFAULTS` |
| Which settings may the Policymaker tune? | `policy/params.yml`, `policy/profiles.yml` |
| Why did a session or change get this outcome? | Its staged report; `session-reports.md` and `policy-changes.md` explain provenance |
| How are instructions staged and network access enforced? | `session-contract.md` |
| Which historical data can a role request? | `session-exports.md` |

The engine contract defines formats and ownership. Policy defines methods. Keep
format changes in the contract and implementation together; link to the contract
from role instructions instead of copying its schemas into each role.

## Execution and tests

All Python paths below are in `.github/loop/`. Workflows call `loop.py main`.
The engine uses the accepted checkout; agent commits enter through `fetch_candidate`.

| Behavior | Implementation entry points | Tests in `tests/` |
|---|---|---|
| Session setup and outputs | `loop.resolve`, `loop.stage`, `loop.finalize` | `test_session.py` |
| Iteration lifecycle and task graph | `iteration.start_iteration`, `validate_tasks`, `instantiate`, `advance`, `close_iteration` | `test_flow.py`, `test_pure.py` |
| Candidate checks, change gate, evaluator verification | `loop.merge_branch`, `settle_verification`; `iteration.run_gate` | `test_merge.py`, `test_policy_changes.py` |
| Policymaker tuning and applying finished sessions | `policymaker_session.profile`; `loop.apply_policy` | `test_exports.py`, `test_flow.py` |
| Owner requests | `iteration.open_request`, `process_proposals`; `owner_requests.py`; `loop.on_request_closed` | `test_owner_requests.py` |
| Reports and suggestions | `session_reports.py`; report parsing/publication in `common.py` | `test_reports.py`, `test_pure.py` |
| Managed definitions, locations and revision identity | `managed_resources.py` | `test_session.py`, `test_merge.py`, `test_policy_changes.py` |
| Accepted-change receipts | `policy_changes.py` | `test_policy_changes.py` |
| Historical input staging | `session_exports.py` | `test_exports.py` |
| Isolation and credentials | `session_security.py`, `agent.sh`, `offline_exec.py` | `test_security.py`, `test_static.py` |
| Recovery and parked sessions | `loop.watch`, `drive`, `resume`; `stream_filter.py` | `test_watchdog.py`, `test_stream.py` |

`managed_resources.py` describes the managed definition tree and its role, prompt,
configuration, admission, revision and restore boundaries. See
[managed definitions](managed-resources.md) for the compatibility contract.
`common.py` owns shared configuration, GitHub and git operations. Tests replace
GitHub operations with `tests/fakes.py` and use real temporary git repositories.
Run the suite from the loop repository root:
`python -m unittest discover -s .github/loop/tests`.
From the plugin source use `-s engine/.github/loop/tests` instead.
See `engine.md` for prerequisites and limits of this verification.

## Details that are easy to misread

- `loop.py` currently installs `iteration.apply_policy` during import. Reading
  `iteration.start_iteration` alone hides the merge implementation. Production
  enters through `loop.py`; calling `iteration` alone does not install that hook.
- A Policymaker session's iteration label identifies its trigger. Accepted orders
  and policy changes take effect at a later iteration start. Completion and
  application are distinct events.
- Graph `width` measures the largest dependency-depth level. It is a record
  statistic, not a concurrency cap or a prediction of simultaneous running jobs.
- `iteration.evaluator_rev` is a legacy comma-separated hash string for state
  and iteration records; `policy_changes.evaluator_rev` maps paths to hashes for
  receipts and verification. Preserve these persisted shapes unless migrating
  their readers and schema deliberately.
