---
status: accepted
---

# Separate engine, iteration core and policy-layer resources

## Historical status

This ADR records the accepted design and the evidence available when it was
written. The access gap and file locations under "Existing isolation and remaining
gap" and "Evidence" describe that earlier state, not the current engine.
[ADR-0005](0005-no-role-templates.md) supersedes the role-template choice below:
policy roles now ship as active definitions and execution roles are written per
loop, with the Skill reference library used for inspiration. Setup leaves
`shaper: null`; the first Policymaker session chooses the organization.
Current behavior is documented in [managed definitions](../../engine/docs/perpetua/managed-resources.md),
[session isolation](../../engine/docs/perpetua/session-sandbox.md) and
[validation](../../engine/docs/perpetua/validation.md). Explicit engine updates
are described in [the update skill](../../skills/update/SKILL.md); the exclusion
of migration/reconfiguration in this ADR is the scope of the original change.

## Decision

The owner has raised a boundary problem: setup installs implementation-role skills
under `policy/roles/`, alongside policy-layer resources, and ships an orchestrator
there before the setup map chooses the starting shaper. The current glossary uses
policy artifacts to mean artifacts maintained by the Policymaker, including skills
executed by the implementation layer. That ownership does not establish which layer
uses an artifact.

The agreed direction is to distinguish engine machinery, implementation-layer
resources and policy-layer resources in both storage and session access to skills
and tools. Active skills must not overlap between the two layers. Governance
ownership is separate from execution: the Policymaker may create and edit skills
across layers, including its own skill. Protected engine contracts remain owner-only;
global installations are sources rather than targets of Policymaker edits.
Distinct skills in different layers may derive from the same source. Tools may
be used in both layers, with independently assigned permissions.

The owner requires that implementation sessions cannot see policy-layer internals,
including by reading their skills as reference material. They receive the
instructions needed for their own work and may submit suggestions for changes;
they do not inspect or execute the policy layer. This is an enforced access
boundary, not merely separation of skill discovery or prompting. Policymaker
cross-layer editing remains allowed.

The implementation layer contains the orchestrator and goal-directed work roles.
The policy layer contains the Policymaker, audit, evaluator-maintenance role and
evaluator verifier. The engine starts sessions, enforces access, invokes gates,
merges changes and performs rollback. The evaluator-maintenance role is distinct
from the measurement command invoked by the engine.

**Historical choice, superseded by [ADR-0005](0005-no-role-templates.md).**
The owner accepted supplying role templates outside active loop resources, with
setup selecting or creating and then installing the roles it needs. Setup must
also draw on existing global skills, explicitly including Matt Pocock's skills;
shipped templates are not its only source. A starting orchestrator is selected
during setup rather than preinstalled as an active role.

Governed definitions across both layers share one revision and are activated
together at an iteration boundary. Rollback restores the dependent definitions
in both layers together, retaining work products, reports and history. Existing
rollback triggers remain unchanged: an iteration exceeding configured quiet time
or two consecutive failed Policymaker sessions. Evaluator changes still require
independent verification before taking effect.

The owner confirmed the consolidated design. This records the accepted target
architecture. The shipped template and setup now use the layered format; runtime
implementation and its validation limits are documented in
[managed resources](../../engine/docs/perpetua/managed-resources.md) and
[validation](../../engine/docs/perpetua/validation.md).

## Existing isolation and remaining gap (at decision time)

The suggestions interface already exists in
`engine/.github/loop/session-protocol.md` (`.session/suggestions.json`). Role write
scope is checked at merge, and engine-owned settings restrict session tools and
network access. These mechanisms are reusable.

They do not yet hide policy-layer internals from implementation sessions.
`engine/.github/workflows/session.yml` checks out the full work repository with
history. Removing the separate trusted engine checkout does not remove policy
files from that work checkout. `session_security.settings` denies filesystem reads
of `/proc` for offline sessions, but does not deny policy reads. Staging one role
skill does not hide the other skills stored in the repository. The target access
boundary must account for both working-tree files and repository history.

## Existing installations (scope at decision time)

The owner excluded migration of installed loops from the current change. They
also requested a future ability to invoke setup again against an installed loop
to review it and optionally fill gaps or change its configuration. This is a
review/reconfiguration mode rather than a fresh installation or automatic upgrade;
its detailed behavior and implementation scope remain to be resolved separately.
The current setup explicitly stops when it finds an existing installation.

## Evidence (at decision time)

- `skills/setup/SKILL.md`, section 3, copies the entire engine template.
- `skills/setup/setup-notes.md`, Starting roles, requires copied skills under
  `policy/roles/<role>/`; Defaults supplies the generic orchestrator.
- `engine/.github/loop/loop.py`, `stage`, stages role skills from that shared location
  and combines shared policy work rules with the engine session contract.
- `engine/engine.yml` uses policy paths for Policymaker write authority, direct
  merges and evaluator protection; `engine/.github/loop/iteration.py`,
  `revert_policy`, restores the entire policy directory.

