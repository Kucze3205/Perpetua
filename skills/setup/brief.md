Chart the **setup map** of an autonomous agent loop for this repository. First read `docs/perpetua/setup-notes.md` on the branch `{{BRANCH}}`: the standing rules of this map (tracker, topics, defaults).

## Destination

An armed loop in `{{REPO}}` that works toward this goal until the owner accepts the result:

> {{GOAL}}

The map is done when the environment is ready, goal reformulations are with the owner, and the loop is armed (topic H).

## Notes

The map's Notes are this one line, nothing else:

> Before anything else: `git switch {{BRANCH}}`, `git pull`, then read `docs/perpetua/setup-notes.md`: the standing rules of this map.

## Charting this map

Where this differs from wayfinder's "Chart the map", this wins. Unlike plain charting, this session settles the goal and the defaults itself:

1. Name the destination as above. With the owner, settle the goal as it stands and write it, as the owner accepts it, to `goal/goal.md` on `{{BRANCH}}`; commit and push.
2. Ask the owner once whether the defaults in the setup notes hold: cadence, design criterion, orchestrator, accepted limits. Write only what the owner changes.
3. Skip the breadth-first grilling and do not ask whether a map is needed: the topics in the setup notes are the frontier.
4. Create the map, one ticket per topic B–H, then wire the dependencies listed in the setup notes. Fire the research subagents; their findings go in the ticket's resolution comment, not on a branch.

Write the map and tickets in the language the owner used to state the goal.
