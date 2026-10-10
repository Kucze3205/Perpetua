# Perpetua

A Claude Code plugin that sets up an autonomous agent loop in a repository and
leaves it running until it reaches its goal. The goal is not limited to code.

## Language

**Loop**:
A self-sustaining cycle of agent sessions in GitHub Actions that works toward a
goal without a human driving it. Switched on and off only by the owner.

**Owner**:
The human the loop works for. The only one who arms the loop, accepts a goal
gate, and approves what the loop's policy reserves for them.
_Avoid_: user, operator

**Setup map**:
The wayfinder map the skill charts in the loop's repository. Working through it
prepares everything the loop needs; closing it arms the loop.
_Avoid_: onboarding, wizard

**Engine**:
The goal-independent machinery of a loop: workflows, scripts, configuration,
the policy's prompt. Copied into the loop's repository by setup, where it
belongs to that loop; a setup map may change it. Changed only by the owner,
or by the loop through a pull request the owner merges (workflows: the owner
alone).
_Avoid_: framework, runtime

**Role**:
A pair of skill + permission profile, selected by a label on an issue. Built
per loop during setup, preferably from existing skills.
_Avoid_: persona, agent type

**Goal**:
What the loop works toward. May start fuzzy; the loop proposes sharper
versions, only the owner accepts them.

**Goal gate**:
The check that decides the goal is reached. Without an owner-accepted gate the
loop cannot declare its own end.
_Avoid_: success metric, stop condition

**Judge**:
An LLM session that scores work against criteria from the setup map, so the
loop can iterate on subjective goals without waiting for the owner.

**Iteration**:
One unit of the loop's work: a plan executed as one or more parallel jobs,
ending in a verdict on what it produced. Exploring a wrong solution counts.
_Avoid_: cycle

**Session**:
One agent run in one job, working one issue under one role. An iteration is
made of sessions.
_Avoid_: run

**Iteration core**:
The part of an iteration that actually moves the loop toward the goal: the
work done by the roles (building, researching, verifying, exploring).
_Avoid_: step, task

**Policy**:
The agent that runs after every iteration and owns the loop's architecture:
the macro plan (what the next iteration aims at), the evaluator, the
orchestrator (may shape or remove it), roles, parameters, whether the
iteration shape is an orchestrator's plan or a deterministic rule as in v2.
Keeps the evaluator trustworthy, changing it only through the evaluator
verifier. Never touches what the iteration core produces. Cannot write to the
engine, the goal gate or the owner's switch; it may propose a goal change to
the owner while the loop keeps working toward the current goal. Its own prompt
belongs to the engine.
_Avoid_: strategy, scheduler

**Orchestrator**:
A role that turns the policy's macro plan into concrete work: what to
analyse, build, search, whom to bring in. Decides what the loop does today;
may propose changes to its own prompt, which the policy judges. The policy may
replace it with a deterministic rule or remove it.
_Avoid_: manager

**Policy artifacts**:
What the policy may change on its own, kept in version control: the
orchestrator's prompt, role skills, parameters, a deterministic rule, profiles
within what the owner already accepted, the evaluator. When the loop stalls,
the watchdog reverts the last change to them.

**Evaluator**:
The measurement that tells whether work moved the loop toward the goal: a
script, or the judge's rubric. Its verdict feeds the change gate. The work it
measures can never change it, only suggest a change to the policy.
_Avoid_: benchmark, progress tests

**Evaluator verifier**:
An independent session that runs right after every change to the evaluator
and checks that the changed evaluator still measures progress toward the
goal. Until it passes, the previous evaluator stays in force; a failed check
goes back to the policy.

**Change gate**:
The verdict "not worse than what is accepted" that a piece of work must get
before it enters the main line. Issued by an evaluator, the judge or the
owner, as the setup map decides.
_Avoid_: review, merge check

**Setup baseline**:
The state of the loop's repository at the moment the setup map is closed and
the loop is armed, marked by a tag. The watchdog returns the policy artifacts
to it when the policy breaks them before any iteration has closed.

**Owner request**:
A question the loop puts to the owner, answerable only by them: a consent, a
proposed goal or goal gate, a confirmation of a result. It may block the work
that depends on it; the rest of the loop keeps going. The owner answers yes or
no. What it proposes is fixed when it is raised; a change is a new request.
_Avoid_: ticket for the owner, approval

**Accepted limits**:
The bounds the owner has accepted for what roles and their profiles may do.
The policy widens roles within them on its own; beyond them, work waits for an
owner request or for the owner to move the limits.
_Avoid_: permissions, allowlist
