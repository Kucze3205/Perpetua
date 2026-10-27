# Policy working memory

No closed iteration has been considered yet. Maintain the current organization,
open questions and reasons for decisions here, with exact record/report references.
Update when understanding changes; use the archive to revisit a decision or fill a gap.

## Organization (iteration 0003, policymaker issue #116)
- Execution layer: shaper `orchestrator`; roles `orchestrator`, `worker`. Goal is a trivial two-file write, gate not accepted.
- Policy layer: roles `audit`, `evaluator`, `evaluator-verifier`, `policymaker`. Only the audit is policymaker-only work in the orders.
- Policymaker triggers every iteration; no no-progress trigger yet. Keep it until the goal is reached or evidence says otherwise.
- Changes this session: none to `execution/` or `policy/` rules. Ordered the first audit (`first-audit`) per the startup rule to measure the environment first.
- Open: startup budget is unmeasured (no ANTHROPIC_API_KEY for trusted staging); not actionable by the loop, reported only.
- Sources: .session/loop-state.json (iteration 3, policy_rev layered-v1:f8e458ca...), .session/startup.json.
- Revisit when: the audit report lands (it triggers the next Policymaker call), or the gate is accepted.
