# Policy working memory

## Current state (iteration 0014, Policymaker session for issue #295)

- No closed iteration has been considered yet: `iterations: []`, `iterations/` records 0.
- Goal: two files under `src/` (`greeting.txt`, `farewell.txt`); gate not accepted (`gate_accepted: false`, `gate: null`).
- Audit: none (`audit_report: null`). Suggestions: none pending.
- Owner requests: none open.
- Recent Policymaker sessions: 272, 265, 251 done; 160 done; 244 crashed (engine-set status; cause not in loop state).

## Organization

- Shaper: `orchestrator`. Policy-only roles: `evaluator`, `audit`.
- Models: `claude-haiku-5-5` for every alias. Efforts: low/medium/high.
- Triggers: every 1 iteration. No-progress trigger disabled.

## Decisions

- Iteration 0014: no changes to `execution/` or `policy/`, no orders. Reason: no closed iteration, audit or suggestion gives evidence for a change, and an order without an expected gain is busywork.

## Open questions

- Why session 244 crashed (check its report when history has it).
- When the first iteration closes, measure whether the greeting file merged and whether the farewell step can run in a later iteration as the goal requires.

## Next step

- Re-check at the first closed iteration record `iterations/NNNN.json` and the audit, if one is ordered.
