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
The goal-independent machinery of a loop: workflows, scripts, watchdog. Shipped
by the plugin; a setup map may change it for one loop.
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
The component that decides the shape of the next iteration: which roles,
which model, how many parallel candidates, time and budget limits, and
whether the choice is deterministic or made by an LLM. Owns the evaluator:
designs it and keeps it trustworthy, changing it only through the evaluator
verifier. Cannot write to the goal gate or the owner's switch; it may propose
a goal change to the owner while the loop keeps working toward the current
goal.
_Avoid_: strategy, scheduler

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
