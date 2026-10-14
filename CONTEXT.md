# Perpetua

A Claude Code plugin that sets up an autonomous agent loop in a repository and
leaves it running until it reaches its goal. The goal is not limited to code.

## Language

**Loop**:
A self-sustaining chain of iterations in GitHub Actions that works toward a
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
belongs to that loop; a setup map may change it. Changed only by the owner.
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

**Macro plan**:
What the policy fixes for the next iteration: its aim and constraints. It says
what the iteration is for, not which roles or models do the work; the
orchestrator or the deterministic rule turns it into tasks.
_Avoid_: strategy

**Iteration record**:
The immutable account of one iteration, written by the engine when the
iteration ends, closed or failed: what the policy saw, the macro plan, the
tasks as planned, and the outcome (verdict, token use, wall time, resources).
The last good state is the last closed one.
_Avoid_: log, report

**Policy**:
The agent that runs at the start of every iteration and owns the loop's
architecture: the macro plan, the evaluator, the shaper, the roles and the
parameters. Never touches what the iteration core produces.
_Avoid_: strategy, scheduler

**Orchestrator**:
A role that turns the policy's macro plan into concrete work: what to
analyse, build, search, whom to bring in. Decides what the loop does today;
may propose changes to its own prompt, which the policy judges. The policy may
replace it with a deterministic rule or remove it.
_Avoid_: manager

**Shaper**:
The orchestrator, or a script role acting as a deterministic rule: whatever turns
the macro plan into a task graph. It writes the graph to a file; the engine
creates the issues. It has no shell and no token.
_Avoid_: planner, scheduler

**Task graph**:
The tasks of one iteration with their blocking edges, as issues. Its width (how
many tasks can run side by side) is computed from it, not set.

**Policy artifacts**:
What the policy owns, kept in version control: the orchestrator's prompt, role
skills, profiles, parameters, a deterministic rule, the evaluator.

**Evaluator**:
The measurement that tells whether work moved the loop toward the goal: a
script, which may call a model. Its verdict feeds the change gate. The work it
measures can never change it, only suggest a change to the policy.
_Avoid_: benchmark, progress tests

**Evaluator verifier**:
An independent session that checks a change to the evaluator before it takes
effect: does the changed evaluator still measure progress toward the goal.

**Audit**:
A role the policy orders to learn and monitor the loop's whole environment,
every session included: what the loop consumes, where it waits, what the
substrate offers that the loop leaves unused. The policy decides, per project,
what is measured and what the audit may see and do. Each measurement is done
by the audit session itself or, when the audit finds it mechanical, turned
into a script it keeps for later audits. Proposes changes to the policy and
changes nothing else. Judges how the loop uses its environment, never the
quality of the work (that is the evaluator).
_Avoid_: monitoring, telemetry

**Change gate**:
The verdict "not worse than what is accepted" that a piece of work must get
before it enters the main line. Issued by the evaluator or the owner, as the
setup map decides.
_Avoid_: review, merge check

**Setup baseline**:
The state of the loop's repository at the moment the setup map is closed and
the loop is armed, marked by a tag. The engine returns the policy artifacts to
it when they are rolled back before any iteration has closed.

**Owner request**:
A question the loop puts to the owner, answerable only by them: a consent, a
proposed goal or goal gate, a confirmation of a result. The owner answers by
closing it; a notice only informs.
_Avoid_: ticket for the owner, approval

**Accepted limits**:
The bounds the owner has accepted for what roles and their profiles may do.
The policy widens roles within them on its own; beyond them, work waits for an
owner request or for the owner to move the limits.
_Avoid_: permissions, allowlist
