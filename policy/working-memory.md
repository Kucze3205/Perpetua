# Policy working memory

## Current state (iteration 0016, Policymaker session for issue #303)

- Iteration 0015 closed (accepted, improved); record `iterations/0015.json` exists. No audit, no pending suggestions, no open owner requests.
- Goal gate not accepted (`gate_accepted: false`). Remaining goal work (farewell file) belongs to the shaper.
- Decision: no changes to `execution/` or `policy/`, no orders. Reason: no evidence of a problem; an order without expected gain is busywork.

## Earlier state (iteration 0015, Policymaker session for issue #299)

- Iteration 0014 closed (accepted, improved); record `iterations/0014.json` exists. `src/greeting.txt` is present.
- The farewell file (`src/farewell.txt`, N = greeting's merge iteration) is execution work; no policy change needed for it.

## Decisions (iteration 0015)

- No changes to `execution/` or `policy/`, no orders. Reason: the only remaining goal step is the shaper's, no audit or suggestion asks for a change, and an order without expected gain is busywork.

## Earlier state (iteration 0014, Policymaker session for issue #295)

- No closed iteration had been considered yet at that time.
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
