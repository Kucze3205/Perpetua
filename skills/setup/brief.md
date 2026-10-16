Chart the **setup map** of an autonomous agent loop for this repository. First read `docs/perpetua/setup-notes.md` on the branch `{{BRANCH}}`: the standing rules of this map (tracker, topics, architecture defaults and owner decisions).

## Destination

An armed loop in `{{REPO}}` that works toward this goal until the owner accepts the result:

> {{GOAL}}

The map is done when the environment is ready, goal reformulations are with the owner, and the loop is armed (topic H).

## Owner context

{{OWNER_CONTEXT}}

Apply this context during charting according to the question modes in the setup notes. Keep it separate from the goal written to `goal/goal.md`.

## Notes

Start the map's Notes with:

> Before anything else: `git switch {{BRANCH}}`, `git pull`, then read `docs/perpetua/setup-notes.md`: the standing rules of this map.

Then include the owner-context block above, with its separate architecture, requested engine changes, other context and question preference fields. Keep it current as the owner changes those choices; the setup notes are the standing source for each continuation.

## Charting this map

Where this differs from wayfinder's "Chart the map", this wins. Unlike plain charting, this session settles the goal and the defaults itself:

1. Name the destination as above. With the owner, settle the goal as it stands and write it, as the owner accepts it, to `goal/goal.md` on `{{BRANCH}}`; commit and push.
2. Apply supplied owner choices before Defaults, including architecture, provider and owner-request channel as well as cadence, design criterion, orchestrator and accepted limits. Use the selected question mode to discuss remaining choices; ask for unresolved decisions that require the owner even under `none`. Every architecture choice is a default the owner may change during setup. Record each change and the specific guarantee it removes or replaces in the setup notes and the implementing configuration; a lost guarantee informs the decision, it does not veto it. The owner's setup authority grants the Policymaker no authority to change the engine after arming.
3. Use the question mode from the setup notes for breadth-first grilling and topic discussions. The topics in those notes are the initial frontier, adapted to the owner's context. The owner has requested a map; proceed with charting it.
4. Create the map, one ticket per topic B–H, then wire the dependencies listed in the setup notes. Adapt the topics, tools, dependencies and arming steps to the owner's choices; another substrate needs an implemented integration, not a claimed generic backend. Fire the research subagents; their findings go in the ticket's resolution comment, not on a branch.

Write the map and tickets in the language the owner used to state the goal.
