# Setup notes

The standing rules of the setup map of this loop. Every session working the map reads this file first. Written by `/perpetua:setup`; topic H deletes it.

**What this map is.** Its main job is to prepare the environment: the loop can reach every source, piece of information and tool it needs. Its second job is to propose reformulations of the goal that make it easier for the loop to reach. Only the owner accepts a reformulation. Unlike plain wayfinding, this map carries execution: each topic writes its piece of configuration on the setup branch `{{BRANCH}}` when its ticket closes (the decision lives in the file, the ticket points at it). The map does no work toward the goal itself. The default branch is untouched until topic H.

**Branch.** Work on `{{BRANCH}}` (`git switch {{BRANCH}}`, `git pull`); commit and push when the ticket closes. Research and prototype findings go in the ticket's resolution comment: no branch, no file.

**Tracker.** This is the tracker: GitHub issues of `{{REPO}}`, driven with `gh`. Do not run `/setup-matt-pocock-skills`.
- map: the issue labelled `wayfinder:map`; ticket: an issue labelled `wayfinder:<type>`
- child ticket: `gh api repos/{{REPO}}/issues/<map>/sub_issues -F sub_issue_id=<id>`, where `<id>` is the ticket's database id (`gh api repos/{{REPO}}/issues/<n> --jq .id`), not its number
- blocking: `gh api repos/{{REPO}}/issues/<blocked>/dependencies/blocked_by -F issue_id=<id>` with the blocker's database id
- claim: assign `@me`; frontier: open, unassigned child issues with no open blocker

**Glossary.** One glossary: `CONTEXT.md` at the repo root. Its Perpetua terms are fixed; terms of the goal's domain are added to it.

**Topics.** The goal was settled when the map was charted (`goal/goal.md`). Every topic below has a ticket. For each, investigate what the goal and this repo need, then discuss it with the owner; you judge the depth and which topic deserves a grilling session. A topic that does not apply to this goal is recorded as "not applicable" with the reason, never skipped silently. From the first iteration the Policy (an agent that runs at the start of every iteration) maintains the evaluator, roles and parameters, so this map sets their **starting state**. Every key and file is described in `docs/perpetua/engine.md`.

| # | Topic | Content |
|---|---|---|
| B | Environment | Sources, information and tools; access to them from the runner; the model provider's credential and the tools' secrets as repository secrets; the machine-preparation script (`.github/loop/prepare.sh`). |
| C | Starting evaluator and change gate | A script (it may call a model with a rubric), or "owner only"; the check command; protected paths. |
| D | Goal reformulation and goal gate | Proposed reformulations (realistic only after B and C); whether a goal gate exists and the `owner_confirms` field. |
| E | Starting roles | Propose existing skills first (plugins, personal, project); a role built on one gets a copy of it in `policy/roles/<role>/`, since only that folder reaches the runner. Write a new skill only when nothing fits. |
| F | Accepted limits | The boundary within which the Policy may widen roles and profiles on its own (`limits` in `engine.yml`): what the goal needs beyond the engine's defaults. |
| G | Starting parameters | The watchdog's "time without an iteration" and whether time spent waiting for the owner counts toward it; ceilings and models; the macro plan of iteration 0 (`iterations/0000/macro-plan.md`). |
| H | Arming | A `task` ticket, last: delete `docs/perpetua/setup-notes.md`, merge `{{BRANCH}}` into the default branch, tag the merge commit `setup-baseline`, switch on `AUTOPILOT`, then `gh workflow run watchdog.yml` to start iteration 0 now. No trial run: the first iteration is the trial. |

Dependencies: B → C → D; E needs B and C; F needs B and E; G needs C, E and F; H needs all.

**Defaults.** The map only confirms these; the charting session confirmed the first four with the owner.

| Topic | Default |
|---|---|
| G: cadence, criterion | cadence "iteration after iteration"; design criterion "shortest wall-clock time to the goal" |
| Orchestrator | the generic one from the engine, an LLM at the start; the Policy later chooses the iteration shape (orchestrator or rule), setup does not |
| F | the engine's limits (`limits` in `engine.yml`) |
| E | existing skills first, a new skill only when nothing fits |
| C | the Policy owns the evaluator; it changes only through the evaluator verifier |
| D | **none** for the goal gate; without an accepted gate the loop never declares its own end |
| G: watchdog | **none** for "time without an iteration" and for the waiting-time question; they follow from the goal |

**Fixed.** Substrate is GitHub Actions. The goal gate is accepted only by the owner. The Policy never touches the engine, the goal gate or `AUTOPILOT`. Who may change what: `docs/perpetua/engine.md`, "Who may change what". Owner requests (issues the loop opens for the owner) are the only way the loop asks for a decision, a consent or a confirmation.

**Where things are.** Glossary: `CONTEXT.md`. Engine documentation: `docs/perpetua/engine.md`. Engine version: `{{ENGINE_VERSION}}`, stamped in `ENGINE_VERSION`. The engine is a copy that belongs to this repo; this map may change it for this loop (another model provider, for one: see `docs/perpetua/engine.md`, "Sessions").

**Language.** These notes stay in English. Write tickets, comments and the conversation in the language the owner used to state the goal.
