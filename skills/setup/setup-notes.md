# Setup notes

The standing rules of the setup map of this loop. Every session working the map reads this file first. Written by `/perpetua:setup`; topic H deletes it.

**What this map is.** Its main job is to prepare the environment: the loop can reach every source, piece of information and tool it needs. Its second job is to propose reformulations of the goal that make it easier for the loop to reach. Only the owner accepts a reformulation. Unlike plain wayfinding, this map carries execution: each topic writes its piece of configuration on the setup branch `{{BRANCH}}` when its ticket closes (the decision lives in the file, the ticket points at it). The map does no work toward the goal itself. The default branch is untouched until topic H.

**Branch.** Work on `{{BRANCH}}` (`git switch {{BRANCH}}`, `git pull`); commit and push when the ticket closes. Research and prototype findings go in the ticket's resolution comment: no branch, no file.

## Owner context and questions

{{OWNER_CONTEXT}}

These separate fields carry the owner's architecture choices or constraints, requested engine changes, other background and question preference through charting and every map continuation. Read them before choosing topics or asking questions. Explicit owner choices take precedence over Defaults; keep requests distinct from accepted decisions and implemented changes. Write only the accepted outcome to `goal/goal.md`.

- `none`: proceed from the supplied context and defaults, with no optional interview or breadth-first grilling.
- `key topics`: skip breadth-first grilling; discuss consequential or unclear topics, using grilling where it helps resolve them. This is the default when the owner supplied no preference.
- `all`: run breadth-first grilling and discuss every applicable topic in depth; use supplied answers rather than asking them again.

Every mode still requires a precise question when a necessary owner decision remains unresolved, including acceptance of a goal reformulation or a change outside existing authority. `none` reduces optional questions; it does not supply that decision. Update these fields and the map's Notes when the owner changes context or preference, and record resulting decisions in configuration and ticket resolutions. Continuations apply the latest recorded choices rather than resetting to Defaults. At topic H, preserve this context and the resulting decisions in `docs/perpetua/setup-decisions.md` before deleting these notes.

## Map rules

**Tracker.** By default the tracker is GitHub issues of `{{REPO}}`, driven with `gh`. The commands below describe that integration; if the owner changes the tracker or substrate, replace them with the implemented equivalents before using them. Do not run `/setup-matt-pocock-skills`.
- map: the issue labelled `wayfinder:map`; ticket: an issue labelled `wayfinder:<type>`
- child ticket: `gh api repos/{{REPO}}/issues/<map>/sub_issues -F sub_issue_id=<id>`, where `<id>` is the ticket's database id (`gh api repos/{{REPO}}/issues/<n> --jq .id`), not its number
- blocking: `gh api repos/{{REPO}}/issues/<blocked>/dependencies/blocked_by -F issue_id=<id>` with the blocker's database id
- claim: assign `@me`; frontier: open, unassigned child issues with no open blocker

**Glossary.** One glossary: `CONTEXT.md` at the repo root. Add terms of the goal's domain there. When an owner decision changes a Perpetua concept, update its definition and its uses in the setup, configuration and engine documentation together; keep owner, Policy, engine, goal gate and owner request distinct.

**Topics.** The goal was settled when the map was charted (`goal/goal.md`). Every topic below has a ticket. For each, investigate what the goal and this repo need, using the owner context and question mode above to guide discussion and grilling. A topic that does not apply to this goal is recorded as "not applicable" with the reason, never skipped silently. The Policy maintains the evaluator, roles and parameters in separate triggered sessions beside iterations, so this map sets their **starting state**. Adapt the topics and dependencies to the architecture the owner chooses. Every key and file is described in `docs/perpetua/engine.md`.

| # | Topic | Content |
|---|---|---|
| B | Environment | Sources, information and tools; access from the chosen substrate; provider, credential handling and readiness checks; tools' secrets and machine preparation (by default repository secrets and `.github/loop/prepare.sh`). Implement any changed provider or substrate integration. |
| C | Starting evaluator and change gate | A script (it may call a model with a rubric), or "owner only"; the check command; protected paths. |
| D | Goal reformulation and goal gate | Proposed reformulations (realistic only after B and C); whether a goal gate exists and the `owner_confirms` field. |
| E | Starting roles | Propose existing skills first (plugins, personal, project); a role built on one gets a copy of it in `policy/roles/<role>/`, since only that folder reaches the runner. Write a new skill only when nothing fits. |
| F | Accepted limits and owner interaction | The boundary within which the Policy may widen roles and profiles on its own (`limits` in `engine.yml`); the owner-request channel and its acceptance, refusal and wake-up semantics. Implement any changed channel and its authority checks. |
| G | Starting parameters | The watchdog's "time without an iteration" and whether time spent waiting for the owner counts toward it; ceilings and models; the shaper's starting instructions and Policy triggers in `policy/params.yml`. |
| H | Arming | A `task` ticket, last. Preserve owner decisions, specific lost guarantees and replacement checks in `docs/perpetua/setup-decisions.md`, with links to the implementing files, then delete `docs/perpetua/setup-notes.md`. By default merge `{{BRANCH}}` into the default branch, tag the merge commit `setup-baseline`, switch on `AUTOPILOT`, then `gh workflow run watchdog.yml` to start iteration 0 now. For a changed substrate, use the implemented equivalents for accepting the setup, recording the baseline, enabling the owner's switch and starting work; verify its credentials and controls before enabling it. No trial run: the first iteration is the trial. |

Dependencies: B → C → D; E needs B and C; F needs B and E; G needs C, E and F; H needs all.

**Defaults.** These are the shipped starting choices, all open to change by the owner during setup. With no owner change, retain the existing behavior. The charting session establishes which defaults hold; later topics may revise them with the owner.

| Topic | Default |
|---|---|
| G: cadence, criterion | cadence "iteration after iteration"; design criterion "shortest wall-clock time to the goal" |
| Orchestrator | the generic LLM role from the engine; the owner may choose a different starting shaper during setup, and the Policy may later choose an orchestrator or rule within its authority |
| F | the engine's limits (`limits` in `engine.yml`) |
| E | existing skills first, a new skill only when nothing fits |
| C | the Policy owns the evaluator; it changes only through the evaluator verifier |
| D | **none** for the goal gate; without an accepted gate the loop never declares its own end |
| G: watchdog | **none** for "time without an iteration" and for the waiting-time question; they follow from the goal |

**Architecture decisions.** The shipped defaults are GitHub Actions, a Claude subscription, and GitHub issue owner requests. The engine is a copy owned by this repo; the owner may change any starting architecture choice during setup. Record the decision in this file while the map is active and in the implementing configuration, code and `docs/perpetua/engine.md`. For each change, name the specific guarantee lost and any replacement, rather than a generic warning. For example:

- A different provider removes the shipped Claude credential probe and subscription-limit parking/reset guarantees until equivalent behavior is implemented and checked.
- A different owner-request channel removes the issue-close acceptance/refusal, frozen-payload checks and close-event/watchdog wake-ups unless replacements implement them.
- A different execution substrate removes reliance on GitHub Actions workflow permissions, runner isolation and watchdog scheduling; identify which controls the replacement supplies.

These consequences inform the owner's choice; they do not automatically block it. The engine has no generic backend abstraction: choosing another substrate or channel means changing and checking the relevant engine integration, topic instructions, tools and arming procedure. An unimplemented integration is unfinished setup, not a reason to reject the choice.

**Authority after arming.** The owner's power to change setup does not pass to the Policy. Under the shipped boundaries only the owner accepts a goal gate or changes the engine, accepted limits and `AUTOPILOT`; the Policy changes only its permitted artifacts and proposes other changes through owner requests. A setup decision changes those boundaries only when the owner explicitly chooses it and the map implements and documents it. See `docs/perpetua/engine.md`, "Who may change what".

**Where things are.** Glossary: `CONTEXT.md`. Engine documentation: `docs/perpetua/engine.md`. Engine version: `{{ENGINE_VERSION}}`, stamped in `ENGINE_VERSION`. For provider integration points see `docs/perpetua/engine.md`, "Sessions".

**Language.** These notes stay in English. Write tickets, comments and the conversation in the language the owner used to state the goal.
