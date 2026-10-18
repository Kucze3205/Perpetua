# Setup notes

The standing rules of the setup map of this loop. Every session working the map reads this file first. Written by the Perpetua setup skill in Claude Code or Codex; the final arming ticket deletes it.

**What this map is.** First clarify the goal with the owner, then analyse how to reach it, then prepare the environment required by those steps. Unlike plain wayfinding, this map carries execution: each implementation ticket writes its plan or configuration on the setup branch `{{BRANCH}}` when its ticket closes (the decision lives in the file, the ticket points at it). The map does no work toward the goal itself. The default branch is untouched until arming.

**Branch.** Work on `{{BRANCH}}` (`git switch {{BRANCH}}`, `git pull`); commit and push when the ticket closes. Research and prototype findings go in the ticket's resolution comment: no branch, no file.

## Owner context and questions

{{OWNER_CONTEXT}}

These separate fields carry the owner's architecture choices or constraints, requested engine changes, other background and question preference through charting and every map continuation. Read them before choosing topics or asking questions. Explicit owner choices take precedence over Defaults; keep requests distinct from accepted decisions and implemented changes. Write only the accepted outcome to `goal/goal.md`.

- `none`: proceed from the supplied context and defaults, with no optional interview or breadth-first grilling.
- `key topics`: skip breadth-first grilling; discuss consequential or unclear topics, using grilling where it helps resolve them. This is the default when the owner supplied no preference.
- `all`: run breadth-first grilling and discuss every applicable topic in depth; use supplied answers rather than asking them again.

Every mode still requires a precise question when a necessary owner decision remains unresolved, including acceptance of a goal reformulation or a change outside existing authority. `none` reduces optional questions; it does not supply that decision. Update these fields and the map's Notes when the owner changes context or preference, and record resulting decisions in configuration and ticket resolutions. Continuations apply the latest recorded choices rather than resetting to Defaults. At arming, preserve this context and the resulting decisions in `docs/perpetua/setup-decisions.md` before deleting these notes.

## Map rules

**Client skill calls.** In Claude Code use the Skill tool. In Codex, where wayfinder or its dependencies say to call that tool, read and follow the named skill's `SKILL.md` with the same input in the current conversation. Resolve installed skills by their available paths and plugin-qualified names; filesystem-backed skills also work. Give research subagents the research skill path and the applicable map notes. Continue the map with `/wayfinder <URL>` in Claude Code or `$wayfinder <URL>` / `Use wayfinder for <URL>` in Codex. The setup client does not select the loop's provider; retain the Defaults unless the owner changes them.

**Tracker.** By default the tracker is GitHub issues of `{{REPO}}`, driven with `gh`. The commands below describe that integration; if the owner changes the tracker or substrate, replace them with the implemented equivalents before using them. Do not run `/setup-matt-pocock-skills`.
- map: the issue labelled `wayfinder:map`; ticket: an issue labelled `wayfinder:<type>`
- child ticket: `gh api repos/{{REPO}}/issues/<map>/sub_issues -F sub_issue_id=<id>`, where `<id>` is the ticket's database id (`gh api repos/{{REPO}}/issues/<n> --jq .id`), not its number
- blocking: `gh api repos/{{REPO}}/issues/<blocked>/dependencies/blocked_by -F issue_id=<id>` with the blocker's database id
- claim: assign `@me`; frontier: open, unassigned child issues with no open blocker

**Glossary.** One glossary: `CONTEXT.md` at the repo root. Add terms of the goal's domain there. When an owner decision changes a Perpetua concept, update its definition and its uses in the setup, configuration and engine documentation together; keep owner, policy (rules and artifacts), Policymaker (the agent), engine, goal gate and owner request distinct.

**Goal clarification (during charting).** Discuss the desired outcome and scope with the owner before analysing steps or choosing the environment. Resolve vague wording into concrete, verifiable success criteria and the evidence that would demonstrate them; use numerical metrics where useful, without requiring them. This stage is complete when the owner accepts that formulation in `goal/goal.md`. Respect the question mode and reuse supplied answers; if the supplied goal already meets this bar and is accepted, proceed. These criteria guide later checks; accepting a goal gate remains a separate owner decision.

**Required outcomes.** The clarified goal was accepted when the map was charted (`goal/goal.md`). The table is a coverage checklist, not a list of issues or a prescribed sequence. Choose ticket count, types and boundaries to fit the work; an outcome may span several tickets and a small ticket may cover related outcomes. Retain accepted choices and applicable defaults without opening a discussion solely to fill the table. Track coverage in the map's Notes with links to resolutions and implementing files; use Not yet specified for outcomes not yet sharp enough to ticket. Record an inapplicable outcome with its reason; an initial approach is always required. The Policymaker maintains the evaluator, roles and parameters in separate triggered sessions beside iterations, so this map sets their **starting state**. Every key and file is described in `docs/perpetua/engine.md`.

**Session scope and replanning.** Use wayfinder's one-ticket-per-session rule and size guidance when charting and continuing. Each ticket states one bounded question or deliverable, the evidence that completes it and what is deferred. Separate missing facts, owner decisions and substantial implementation when each needs its own investigation or verification. For example, multi-provider setup can need independent tickets for credential persistence, provider adapters, model selection and failover verification; choose boundaries from actual dependencies rather than copying this example as a template. Simple accepted defaults can be recorded and checked in a small implementation ticket; the `all` question mode deepens discussion without enlarging a ticket's scope.

Before starting a ticket and whenever its scope grows, check whether it still fits one session. If a new investigation, decision or substantial implementation would exceed that scope, checkpoint valid implementation work on the setup branch (research findings stay in the ticket), record findings, checks, blockers and exact next steps in the ticket, and create or revise bounded child issues of the map with their blocking edges. Keep unfinished work open and release its claim before ending the session. Close a narrowed ticket only when its revised completion criterion is met and the remaining work is explicitly tracked. After a resolution, update coverage and fog, create newly specifiable tickets, then stop; work the next non-research ticket in a fresh session. Waiting for owner input or external readiness is a checkpoint, not a reason to keep retrying or take on another ticket. Load the map as an index and zoom into relevant tickets and files on demand. These scope rules guide behavior; they do not enforce a token ceiling in either client.

| Outcome | Required result |
|---|---|
| Initial approach | Analyse possible approaches, steps, dependencies, unknowns and evidence of progress toward the accepted goal. Record a revisable initial plan in `docs/perpetua/approach.md`, with each step's required sources, information, tools and access, and verification needs. It is complete when these requirements are explicit enough to choose and prepare the environment; the loop may revise the plan as it learns. |
| Environment | Derive the environment from the recorded approach's steps and requirements. For each requirement, implement and check the needed sources, information, tools and access from the chosen substrate; provider, credential handling and readiness checks; tools' secrets and machine preparation (by default repository secrets and `.github/loop/prepare.sh`). Implement any changed provider or substrate integration. Record how the environment covers the approach's requirements in `docs/perpetua/approach.md`. |
| Starting evaluator, change gate and goal gate | Implement progress measurement and checks from the accepted goal's criteria and the approach's verification needs using the prepared environment. Evaluator: a script (it may call a model with a rubric), or "owner only"; the check command; protected paths. Separately decide with the owner whether a goal gate exists, its command and the `owner_confirms` field. |
| Starting roles | Propose existing skills first (plugins, personal, project); a role built on one gets a copy of it in `policy/roles/<role>/`, since only that folder reaches the runner. Write a new skill only when nothing fits. |
| Accepted limits and owner interaction | The boundary within which the Policymaker may widen roles and profiles on its own (`limits` in `engine.yml`); the owner-request channel and its acceptance, refusal and wake-up semantics. Implement any changed channel and its authority checks. |
| Starting parameters | The watchdog's "time without an iteration" and whether time spent waiting for the owner counts toward it; ceilings and models; the shaper's starting instructions and Policymaker triggers in `policy/params.yml`. |
| Arming | A separate `task` ticket, last, after all applicable outcomes are implemented and checked and all in-scope fog and open setup work are resolved. Preserve owner decisions, specific lost guarantees and replacement checks in `docs/perpetua/setup-decisions.md`, with links to the implementing files, then delete `docs/perpetua/setup-notes.md`. By default merge `{{BRANCH}}` into the default branch, tag the merge commit `setup-baseline`, switch on `AUTOPILOT`, then `gh workflow run watchdog.yml` to start iteration 0 now. For a changed substrate, use the implemented equivalents for accepting the setup, recording the baseline, enabling the owner's switch and starting work; verify its credentials and controls before enabling it. No trial run: the first iteration is the trial. |

**Dependencies.** Preserve accepted goal, then initial approach, then environment derived from that approach. Beyond this, add blockers only where a ticket needs another ticket's answer or artifact, including actual environment, evaluator, role, limit or parameter prerequisites. A precise research question can be investigated before the decision it informs; an unrelated investigation need not wait for an entire outcome. Keep arming blocked by all unfinished setup work, including newly discovered tickets, and defer it while in-scope fog remains. If analysis invalidates the approach or an accepted decision, revise it and recheck affected work; obtain the owner's acceptance before changing the goal.

**Defaults.** These are the shipped starting choices, all open to change by the owner during setup. With no owner change, retain the existing behavior. Charting preserves supplied constraints; each topic resolves its remaining choices when its prerequisites are complete.

| Area | Default |
|---|---|
| Cadence, criterion | cadence "iteration after iteration"; design criterion "shortest wall-clock time to the goal" |
| Orchestrator | the generic LLM role from the engine; the owner may choose a different starting shaper during setup, and the Policymaker may later choose an orchestrator or rule within its authority |
| Accepted limits | the engine's limits (`limits` in `engine.yml`) |
| Starting roles | existing skills first, a new skill only when nothing fits |
| Evaluator | the Policymaker owns the evaluator; it changes only through the evaluator verifier |
| Goal gate | **none** for the goal gate; without an accepted gate the loop never declares its own end |
| Watchdog | **none** for "time without an iteration" and for the waiting-time question; they follow from the goal |

**Architecture decisions.** The shipped defaults are GitHub Actions, a Claude subscription, and GitHub issue owner requests. The engine is a copy owned by this repo; the owner may change any starting architecture choice during setup. Record the decision in this file while the map is active and in the implementing configuration, code and `docs/perpetua/engine.md`. For each change, name the specific guarantee lost and any replacement, rather than a generic warning. For example:

- A different provider removes the shipped Claude credential probe and subscription-limit parking/reset guarantees until equivalent behavior is implemented and checked.
- A different owner-request channel removes the issue-close acceptance/refusal, frozen-payload checks and close-event/watchdog wake-ups unless replacements implement them.
- A different execution substrate removes reliance on GitHub Actions workflow permissions, runner isolation and watchdog scheduling; identify which controls the replacement supplies.

These consequences inform the owner's choice; they do not automatically block it. The engine has no generic backend abstraction: choosing another substrate or channel means changing and checking the relevant engine integration, topic instructions, tools and arming procedure. An unimplemented integration is unfinished setup, not a reason to reject the choice.

**Authority after arming.** The owner's power to change setup does not pass to the Policymaker. Under the shipped boundaries only the owner accepts a goal gate or changes the engine, accepted limits and `AUTOPILOT`; the Policymaker changes only its permitted artifacts and proposes other changes through owner requests. A setup decision changes those boundaries only when the owner explicitly chooses it and the map implements and documents it. See `docs/perpetua/engine.md`, "Who may change what".

**Where things are.** Glossary: `CONTEXT.md`. Engine documentation: `docs/perpetua/engine.md`. Engine version: `{{ENGINE_VERSION}}`, stamped in `ENGINE_VERSION`. For provider integration points see `docs/perpetua/engine.md`, "Sessions".

**Language.** These notes stay in English. Write tickets, comments and the conversation in the language the owner used to state the goal.
