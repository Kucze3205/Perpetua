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
The wayfinder map that connects a clarified goal to an initial approach and the
environment the loop needs to pursue it; closing it arms the loop.
_Avoid_: onboarding, wizard

**Engine**:
The goal-independent machinery of a loop: workflows, scripts, protected configuration and session contracts. Copied into the loop's repository by setup, where it
belongs to that loop; a setup map may change it. Changed only by the owner.
_Avoid_: framework, runtime

**Role**:
A pair of skill + permission profile, selected by a label on an issue. Built
per loop during setup, preferably from existing skills.
_Avoid_: persona, agent type

**Goal**:
The outcome the loop works toward, with a clear scope and verifiable success
criteria and evidence; numerical metrics are optional. Only the owner accepts
the goal and its reformulations.

**Initial approach**:
A revisable plan of how the loop could reach the goal: steps, dependencies and
unknowns from which its environment requirements follow.

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

**Iteration record**:
The immutable account of one iteration, written by the engine when the
iteration ends, closed or failed: its starting state, the tasks as planned,
and the outcome (verdict, token use, wall time, resources).
The last good state is the last closed one.
_Avoid_: log, report

**Policy**:
The rules and artifacts the policymaker creates and maintains to govern the
implementation layer: work rules, role instructions, profiles, parameters and
evaluation rules.
_Avoid_: agent, policymaker

**Policymaker**:
The agent in the policy layer that manages the loop's architecture by creating
and revising policy; it does not perform the implementation layer's work.
_Avoid_: policy, politician, strategy, scheduler

**Policy layer**:
The part of the loop in which the policymaker defines how the implementation
layer works, within the owner's accepted limits.

**Implementation layer**:
The part of the loop that applies policy to plan, execute and evaluate work
toward the goal.

**Orchestrator**:
A role that turns the goal and the loop's state into concrete work: what to
analyse, build, search, whom to bring in. Decides what the loop does today;
may propose changes to its own prompt, which the policymaker judges. The
policymaker may replace it with a deterministic rule or remove it.
_Avoid_: manager

**Shaper**:
The orchestrator, or a script role acting as a deterministic rule: whatever turns
the goal and the loop's state into a task graph. It writes the graph to a file; the
engine creates the issues. It has no GitHub credentials.
_Avoid_: planner, scheduler

**Task graph**:
The tasks of one iteration with their blocking edges, as issues. Its width is the
number of tasks in the largest dependency-depth level, computed from the graph,
not set. It is a structural statistic, not a cap or prediction of concurrent jobs.

**Managed definitions**:
The version-controlled definitions maintained by the Policymaker in both
`execution/` and `policy/`: skills, profiles, prompts, parameters and measurement.
They share one revision, activate at iteration boundaries and roll back together.
Governance ownership is independent of the layer that executes a definition.

**Implementation resources**:
The local definitions in `execution/` used by the selected shaper and work roles.
Implementation sessions receive their own instructions and public contracts;
they cannot read policy internals and submit suggestions to the Policymaker.

**Policy resources**:
The local definitions in `policy/` used by the Policymaker, audit, evaluator
maintenance and evaluator verifier. The engine invokes measurement separately.

**Role template**:
An inactive candidate under `templates/`, or another project/global/plugin source.
Setup selects and copies complete skills into exactly one active layer. Sources
remain unchanged, and separate local adaptations may share the same source.

**Evaluator**:
The measurement that tells whether work moved the loop toward the goal: a
script, which may call a model. Its verdict feeds the change gate. The work it
measures can never change it, only suggest a change to the policymaker.
_Avoid_: benchmark, progress tests

**Evaluator verifier**:
An independent session that checks a change to the evaluator before it takes
effect: does the changed evaluator still measure progress toward the goal.

**Audit**:
A role the policymaker orders to learn and monitor the loop's whole environment,
every session included: what the loop consumes, where it waits, what the
substrate offers that the loop leaves unused. The policymaker decides, per project,
what is measured and what the audit may see and do. Each measurement is done
by the audit session itself or, when the audit finds it mechanical, turned
into a script it keeps for later audits. Proposes changes to the policymaker and
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
the loop is armed, marked by a tag. The engine returns the managed definitions to
it when they are rolled back before any iteration has closed.

**Owner request**:
A question the loop puts to the owner, answerable only by them: a consent, a
proposed goal or goal gate, a confirmation of a result. The owner answers by
closing it; a notice only informs.
_Avoid_: ticket for the owner, approval

**Accepted limits**:
The bounds the owner has accepted for what roles and their profiles may do.
The policymaker widens roles within them on its own; beyond them, work waits for an
owner request or for the owner to move the limits.
_Avoid_: permissions, allowlist
