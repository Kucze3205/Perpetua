# Policy working memory

## Current state (iteration 0011, policymaker session on issue #265)

- Goal: create `src/greeting.txt` (`hello from perpetua`) and, in a later iteration, `src/farewell.txt`
  (`goodbye after iteration N`). Gate `policy/evaluator/gate.py` is not accepted (`goal.gate_accepted: false`).
- Records: 0 (`iterations/` has no closed iteration yet). Suggestions: none. Audit report: none.
- Organization: `shaper: orchestrator` (LLM role), `policymaker_only_roles: [evaluator, audit]`.
  No change made this session.
- No `no_progress` signal (value 0). Policymaker trigger: every 1 iteration.
- Startup budget: 7365 tokens estimated of 100000 limit, status ok.

## Decision (iteration 0011)

No orders placed and no managed definitions changed. With no closed iteration, no evaluator
verdict, no audit report and no failed session in the evidence, no change has a measured
expected gain. Placing orders now would be busywork.

## Open questions

- `farewell.txt` can only be written after `src/greeting.txt` is merged in an earlier iteration
  with a closed record. Revisit once `iterations/NNNN.json` exists.
- Whether the audit should run before the shaper's first task. Revisit when the first record
  or the first audit trigger exists.

## Sources

- `.session/loop-state.json` (iteration 11, `iterations: []`, `policymaker_sessions` 251, 244 crashed, 160, 155, 147).
- `.session/startup.json` (budget ok).
