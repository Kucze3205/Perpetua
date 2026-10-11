Chart the **setup map** of an autonomous agent loop for this repository. Name the destination as below; put everything under "Notes" into the map's Notes verbatim, so the map is self-sufficient for every later session.

## Destination

An armed loop in `{{REPO}}` that works toward this goal until the owner accepts the result:

> {{GOAL}}

The map is done when the environment is ready, goal reformulations are with the owner, and the loop is armed (topic H).

## Notes

**What this map is.** Its main job is to prepare the environment: the loop can reach every source, piece of information and tool it needs. Its second job is to propose reformulations of the goal that make it easier for the loop to reach. Only the owner accepts a reformulation. Unlike plain wayfinding, this map carries execution: each topic writes its piece of configuration on the setup branch `{{BRANCH}}` when its ticket closes (the decision lives in the file, the ticket points at it). The map does no work toward the goal itself. Main is untouched until topic H.

**Tracker.** GitHub issues of `{{REPO}}`, driven with `gh`; this repo has no tracker doc.
- map: the issue labelled `wayfinder:map`; ticket: an issue labelled `wayfinder:<type>`
- child ticket: `gh api repos/{{REPO}}/issues/<map>/sub_issues -F sub_issue_id=<id>`, where `<id>` is the ticket's database id (`gh api repos/{{REPO}}/issues/<n> --jq .id`), not its number
- blocking: `gh api repos/{{REPO}}/issues/<blocked>/dependencies/blocked_by -F issue_id=<id>` with the blocker's database id
- claim: assign `@me`; frontier: open, unassigned child issues with no open blocker

**Topics.** Every topic gets a ticket. For each, investigate what the goal and this repo need, then discuss it with the owner; you judge the depth and which topic deserves a grilling session. A topic that does not apply to this goal is recorded as "not applicable" with the reason, never skipped silently. From the first iteration the Policy (an agent that runs after each iteration) maintains the evaluator, roles and parameters, so this map sets their **starting state**.

| # | Topic | Content |
|---|---|---|
| A | Raw goal | Understand the goal as it stands. |
| B | Environment | Sources, information and tools; access to them from the runner; secrets and credentials; the machine-preparation script (`.github/loop/prepare.sh`); the loop's token without the `workflows` scope; enable "Allow GitHub Actions to create pull requests". |
| C | Starting evaluator and change gate | A script, a judge's rubric, or "owner only"; the check command; protected paths. |
| D | Goal reformulation and goal gate | Proposed reformulations (realistic only after B and C); whether a goal gate exists and the `owner_confirms` field. |
| E | Starting roles | Propose existing skills first (plugins, personal, project) and build agents on them; write a new skill only when nothing fits. |
| F | Actions and spending | The boundary (Accepted limits) within which the Policy may widen roles and profiles on its own; configuration in the engine zone, changed by pull request. |
| G | Starting parameters | Cadence, design criterion, the watchdog's "time without an iteration", and whether time spent waiting for the owner counts toward it; optionally the macro plan of iteration 0 (`iterations/0000/macro-plan.md`, else the first iteration begins with the Policy). Every key and file is described in `docs/perpetua/engine.md`. |
| H | Arming | A `task` ticket, last: merge `{{BRANCH}}` into main, tag the merge commit `setup-baseline`, switch on `AUTOPILOT`, then `gh workflow run watchdog.yml` to start iteration 0 now. No trial run: the first iteration is the trial. |

Dependencies: A → B → C → D; E needs B and C; F needs B and E; G needs C, E and F; H needs all.

**Defaults.** The map only confirms these.

| Topic | Default |
|---|---|
| G: cadence, criterion | cadence "cycle after cycle"; design criterion "shortest wall-clock time to the goal" |
| Orchestrator | the generic one from the engine, an LLM at the start; the Policy later chooses the iteration shape (orchestrator or rule), setup does not |
| E | existing skills first, a new skill only when nothing fits |
| C | the Policy owns the evaluator; it changes only through the evaluator verifier |
| F | **none**; no hard ban, every loop sets its limits from scratch |
| D | **none** for the goal gate; without an accepted gate the loop never declares its own end |
| G: watchdog | **none** for "time without an iteration" and for the waiting-time question; they follow from the goal |

**Fixed.** Substrate is GitHub Actions. The goal gate is accepted only by the owner. The Policy never touches the engine, the goal gate, `AUTOPILOT` or the token. Engine zones: workflows change only by the owner; scripts and configuration by the loop through a pull request the owner merges; `policy/` is the Policy's. The loop's token has no `workflows` scope. Owner requests (issues the loop opens for the owner) are the only way the loop asks for a decision, a consent or a confirmation.

**Where things are.** Glossary: `docs/perpetua/glossary.md`. Engine documentation: `docs/perpetua/engine.md`. Engine version: `{{ENGINE_VERSION}}`, stamped in `ENGINE_VERSION`. The engine is a copy that belongs to this repo; this map may change it for this loop.

**Language.** Write the map and tickets in the language the owner used to state the goal.
