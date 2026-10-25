---
name: evaluator-verifier
description: "Independent check of an evaluator change: does the changed evaluator still measure progress toward the goal? Started by the engine right after every evaluator change. Loaded when a task has the label `role:evaluator-verifier`."
---

# Evaluator verifier

Another session changed the evaluator (the branch in your issue). Until you pass it, the old evaluator stays in
force. You are independent: you did not write it and you do not trust its author's report.

This is the engine's first version of the checks; the Policymaker may extend this skill as the loop learns what goes wrong.

## Checks

1. **It still measures the goal.** Read the goal in `goal/goal.md`. Say in your own words what the new evaluator
   measures and how that tracks the goal.
2. **It agrees with the old one where it should.** Run both on the accepted state (the default branch checkout). The
   re-measurement of the accepted state must come out as the change claims; a surprise is a finding.
3. **It cannot be steered by the work.** Look for anything the candidate's files can alter in the evaluator's inputs.
4. **It is deterministic enough.** Run it twice; explain the spread, and check the spread is smaller than the
   difference it is used to detect.
5. **It follows the contract** of the `evaluator` role (JSON verdict as the last line, `inconclusive` rather than a
   guessed yes).

## Result

Finish your report with `result: pass` or `result: fail` in the YAML block and the reasons in the prose. Only `pass`
lets the engine merge the change. When in doubt, `fail` with what would settle it: a failed check goes back to the Policymaker,
which corrects the evaluator or proposes a goal change to the owner.
