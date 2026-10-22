Chart the **setup map** of an autonomous agent loop for this repository. First read `docs/perpetua/setup-notes.md` on the branch `{{BRANCH}}`: the standing rules of this map (tracker, required outcomes, architecture defaults and owner decisions).

## Destination

An armed loop in `{{REPO}}` that works toward this goal until the owner accepts the result:

> {{GOAL}}

The map is done when the owner has accepted a clear, verifiable goal, an initial approach has informed the prepared environment, and the loop is armed (the final arming ticket).

## Owner context

{{OWNER_CONTEXT}}

Apply this context during charting according to the question modes in the setup notes. Keep it separate from the goal written to `goal/goal.md`.

## Notes

Start the map's Notes with:

> Before anything else: `git switch {{BRANCH}}`, `git pull`, then read `docs/perpetua/setup-notes.md`: the standing rules of this map.

Then include the owner-context block above, with its separate architecture, requested engine changes, other context and question preference fields. Keep it current as the owner changes those choices; the setup notes are the standing source for each continuation.

## Charting this map

Where this differs from wayfinder's "Chart the map", this wins. This session clarifies the goal before charting the setup work:

1. Name the destination as above. With the owner, clarify the outcome, scope, success criteria and evidence using the goal-clarification rule in the setup notes. Write the accepted result to `goal/goal.md` on `{{BRANCH}}`; commit and push. Complete this before analysing the approach or choosing the environment.
2. Preserve supplied owner choices as constraints, including architecture, provider and owner-request channel as well as cadence, design criterion and accepted limits. Resolve remaining choices when their prerequisites are known: the initial approach informs environment decisions. Every architecture choice is a default the owner may change during setup. Record each change and the specific guarantee it removes or replaces in the setup notes and the implementing configuration; a lost guarantee informs the decision, it does not veto it. The owner's setup authority grants the Policymaker no authority to change the engine after arming.
3. Use the question mode from the setup notes for breadth-first grilling and topic discussions, respecting the goal → initial approach → environment order. Ask for unresolved decisions that require the owner even under `none`. The owner has requested a map; proceed with charting it.
4. Chart with wayfinder's native frontier and fog-of-war process. The setup notes specify required outcomes, not a ticket template. Choose ticket count, scope, type and dependencies from this goal, repository, owner context and remaining uncertainty. A complex outcome may need several investigations, decisions and implementation tickets; combine simple related outcomes only when they fit one bounded session. Use research for missing facts, prototype for behavior that needs a concrete example, grilling for unresolved owner decisions and task for bounded setup implementation. Reuse accepted choices and working defaults at the depth warranted by the question mode.
5. Create only tickets whose question or deliverable can already be stated precisely; keep the remaining in-scope work in Not yet specified. Wire actual blockers in a second pass. Research can precede an environment decision when its question is already precise. Follow the setup notes for ticket scope, replanning and readiness for arming. Fire research subagents only for unblocked work; their findings go in the ticket's resolution comment, not on a branch. Stop after charting and dispatching research; leave ticket resolution to continuations.

Write the map and tickets in the language the owner used to state the goal.
