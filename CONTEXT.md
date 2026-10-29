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
The goal-independent machinery of a loop: workflows, scripts, configuration
and protected session contracts. Copied into the loop's repository by setup, where it
belongs to that loop; a setup map may change it. Changed only by the owner; the
Policymaker may propose a change as a ready patch. Kept to what must stay out of the
Policymaker's reach: credentials, the goal and its gates, merging, rollback and the
owner's limits. Everything else about how an iteration runs is policy.
_Avoid_: framework, runtime

**Role**:
A pair of skill + permission profile, selected by a label on an issue. The
policy-layer roles ship with the engine; the setup map and the policymaker write
every other role's skill per loop, drawing on the **Skill reference library**.
Each active role skill belongs to exactly one layer.
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
It has two phases. In the policy phase, which a due trigger opens (always in
the first iteration), one Policymaker session runs in passes: it changes policy
and orders work, and, when it asks to wake, is resumed with a clean context when
that work has ended, until a pass does not wake. The execution phase then runs the
shaper's graph on the organization the policy phase produced. With no trigger due there is no policy phase and the iteration runs on
the last organization.
_Avoid_: cycle

**Session**:
One agent run in one job, working one issue under one role. An iteration is
made of sessions.
_Avoid_: run

**Startup package**:
The engine instructions and declared documents a model Session reads before its
work. Its size is estimated separately from later reads and active context.

**Working memory**:
A role-maintained account of the loop's current approach, open threads and reasons
for decisions, linked to historical sources. Its organization belongs to the
Policymaker; immutable Iteration records remain the source of what happened.

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
and revising policy, including skills in both layers and its own skill. It does
not perform the implementation layer's work or change protected engine contracts.
It opens an iteration when a trigger is due (always in the first), and decides how
the implementation layer is organized, including the orders that become tasks and
the order in which they run.
It reads the results of its policy-layer orders and decides what next; the engine
does not limit how many passes that takes.
_Avoid_: policy, politician, strategy, scheduler

**Policy layer**:
The part of the loop in which the policymaker defines how the implementation
layer works, within the owner's accepted limits. Its skills and session tool
access are separate from those of the implementation layer. It includes the
Policymaker, audit, evaluator maintenance and evaluator verification; it runs
in the policy phase.

**Implementation layer**:
The part of the loop that applies policy to plan, execute and evaluate work
toward the goal. Its skills belong to this layer even when the Policymaker
creates or revises them. It includes the shaper (when one is set) and roles performing
goal-directed tasks, and runs in the execution phase. Its sessions are asked not to read policy-layer internals; they
receive their work instructions and may submit suggestions for policy changes.

**Orchestrator**:
A role that turns the goal and the loop's state into concrete work: what to
analyse, build, search, whom to bring in. Decides what the loop does today;
may propose changes to its own prompt, which the policymaker judges. It is one
of the organizations of the implementation layer that the policymaker chooses,
not a fixed first step: the policymaker may replace it with a deterministic rule
or remove it (`shaper: null`), and the first Policymaker session decides whether
it exists. The owner is not asked about it.
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
The version-controlled definitions maintained by the Policymaker across both
layers: skills, profiles, prompts, parameters and measurement.
They share one revision, activate at iteration boundaries and roll back together.
Governance ownership is independent of the layer that executes a definition.

**Implementation resources**:
The local definitions used by the selected shaper and goal-directed work roles.
Implementation sessions receive their own instructions and public contracts;
they are asked not to read policy internals and submit suggestions to the Policymaker.

**Policy resources**:
The local definitions used by the Policymaker, audit, evaluator
maintenance and evaluator verifier. The engine invokes measurement separately.

**Skill reference library**:
Matt Pocock's skills, treated as a good source of ideas. After deciding that a
new or recruited role will do certain tasks, the setup map or the policymaker
searches it for how such a role should behave, then writes the skill itself:
inspired by what it found, never a copy.

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
