# Session reports and suggestions

Before the agent starts, the engine exports trusted marked comment bodies into
`.session/reports/` for Policymaker and shaper sessions. `index.json` lists issue IDs,
local paths, comment URLs and body SHA-256 snapshots. Each issue JSON includes the
complete body. Reports cover the last five iterations in the input state, plus
sources of pending suggestions. The existing trusted-author check and report marker
are both required. Agents need neither `gh` nor credentials to read this input.
Explicit report selectors for other roles use the declarative exports contract.

Any session can produce `.session/suggestions.json`, an array of objects with
exactly `kind`, `what`, `why`, and `evidence`. Values are nonempty strings of at most
20,000 characters; `kind` is a lowercase identifier of at most 64 characters with
digits and hyphens. At most 100 entries are accepted. Evidence names the report
section, file, observation or measurement supporting the proposal. The engine adds
an ID derived from the source issue and canonical content, and the trusted report's
issue, comment URL and snapshot hash. Replaying a session preserves that ID.

The epilogue commits immutable report/proposal snapshots below the protected
`.github/loop/data/session-reports/<issue>/` directory. A final report is also
snapshotted when the session closes. Iteration session summaries refer to these
paths and suggestion IDs. No later lifecycle operation changes a closed source
record. Invalid JSON or entries produce runner diagnostics and durable diagnostics
in the source snapshot; they do not discard the report or other valid entries.

Pending is the default state. Resolutions live separately in the protected
`.github/loop/data/suggestions-state.json`. Only a successfully completed Policymaker
session can freeze `.session/suggestion-decisions.json`, an array of objects with
exactly `id`, `status` (`handled` or `rejected`), and a nonempty `reason`. IDs must
have been staged to that Policymaker. The engine records the responsible Policymaker issue.
Resolution takes effect only after that Policymaker's branch is accepted at an iteration
boundary; a failed merge or crashed session does not resolve suggestions. Replaying
application preserves the original resolution.

`loop-state.json` `suggestions` contains pending entries, evidence and provenance.
The first Policymaker input containing a new pending ID consumes its one-shot trigger.
Later scheduling opportunities still expose unresolved suggestions, without
repeatedly triggering on the same IDs. Suggestions arriving while Policymaker is open
remain eligible for the next opportunity. Resolving a suggestion never changes
its original source snapshot or a closed iteration record.

## Iteration records

`iterations/NNNN.json`, written once by the engine, never edited: `schema_version`, `iteration`, `status`
(`closed`/`failed`; the last good state is the last `closed`), `started`, `ended`, `engine_version`, `input_state` (the
snapshot at iteration start, without the index), `policy` (`rev_start`, `rev_end`, `commit_end`, refused orders),
`decided_by` (the shaper role), `tasks` (the frozen graph), `outcome` (sessions with status,
verdict, tokens in/out/cache and by model, turns, agent/gate/total/parked seconds, runner, branch, commit, stream name;
the shaper; the iteration verdict; token sums; wall time; critical path; width; owner requests opened and open at close).

See [change provenance](policy-changes.md) for separate accepted-change and verifier receipts; closed iteration records are never enriched later.
