# Managed definitions

The engine-owned `.github/loop/managed_resources.py` defines the supported
layered format. Configuration loading, role resolution, staging, admission,
receipts, iteration revisions and rollback use this boundary.

New templates declare `resources: {format: layered}`. Omission uses that same
format; explicit legacy formats are rejected. Existing loops keep their own
versioned engine (ADR-0001); this release supplies no migration or update.

## Active definitions and inactive sources

The execution layer uses `execution/profiles.yml`, `execution/roles/<role>/`,
`execution/protocol.md`, and `execution/prompts/role.md`. The policy layer uses
`policy/profiles.yml`, `policy/roles/<role>/`, `policy/protocol.md`, and
`policy/prompts/role.md`. `policy/prompts/policymaker.md` supplies the Policymaker
bootstrap template. Shared managed parameters remain in `policy/params.yml`;
the engine-invoked measurement remains in `policy/evaluator/` (or the accepted
`evaluator_paths`). The measurement is separate from its maintaining role.

A profile file maps role names to permission profiles, with the same tool,
network, write and session tuning fields as before. Its location assigns the
execution layer; profiles must not provide a `layer` field. For example:

```yaml
# execution/profiles.yml
builder:
  tools: [Read, Write]
  write: [src/**]
# policy/profiles.yml (a separate file)
audit:
  tools: [Read]
  write: [audit/**]
```

The catalog rejects duplicate YAML keys, a role appearing in both profile
files, conflicting skill directories, redirected definitions (including skill
and reference symlinks), explicit layer overrides, and policy
roles selected as the execution shaper. The configured shaper belongs to
execution; `audit`, `evaluator` (maintenance), and the configured verifier role
belong to policy. Other goal roles are installed in execution; additional
policy roles may be installed in the policy profile file. Inactive skill
materials without a profile are not advertised as roles.

The Policymaker is always a policy role. Its permission profile remains owned
by `engine.yml` with existing owner ceilings and tuning constraints; it cannot
be supplied in either profile file. Its mutable local skill lives at
`policy/roles/policymaker/SKILL.md`. The default engine profile permits writes
to both managed roots. Explicit owner `policymaker.write`
restrictions override this default. It may create and edit local definitions
in either layer, including its own skill. Protected engine paths and evaluator
ownership/independent verification still take precedence.

The engine stages each model role's complete local skill and references at
`.session/role/`, including the Policymaker, for both network modes. Staging
uses the accepted checkout and the selected layer, with no fallback to the
other layer and no writes to global skill installations. Script roles receive
their own profile's command. Layer selection does not grant additional tools
or network permissions: profiles retain independent permissions and existing
accepted-limit checks.

Execution roles cannot admit changes to managed definitions even if their
`write` patterns include those locations; they use suggestions instead.
Permitted definition changes in either root are managed changes, bypassing
the product change gate while preserving separate evaluator verification.
The engine rejects an ambiguous candidate catalog before publication. Products
continue through protected/write checks and the accepted-tree change gate.

## Layered activation and recovery

A layered revision has a `layered-v1:` identity over both managed roots and the
accepted evaluator paths. Receipts record this same before/after identity;
historical iteration records remain readable.

A finished Policymaker session queues its entire candidate for the next
iteration boundary. Only `apply_policy` activates that candidate. Independent
evaluator verification and owner acceptance can release a held candidate back
to that queue, but neither completion activates it during an iteration. At the
boundary the engine rechecks the pinned evaluator snapshot, permissions,
accepted-tree change gate and complete shaper profile/skill before publishing
the candidate and its receipt in one ref update. Pending, rejected or stale
verification and failed publication
leave the accepted set in force. Products in a mixed candidate stay with the
same candidate and retain their gate requirements.

Rollback restores both roots and accepted evaluator paths from the last closed
iteration's commit, or `setup-baseline` before the first closed iteration. It
restores deleted definitions and removes newly added definitions, retaining
products, reports, receipts and Git history. Quiet time, consecutive failed
Policymaker sessions and owner-wait accounting retain their existing rules.

## New-loop setup

The shipped template has empty `execution/profiles.yml` and `policy/profiles.yml`, no active skills and no selected shaper. Candidate skills and profiles live under protected `templates/`. `setup_roles.py prepare` checks this fresh state before the setup branch is committed; it does not convert definitions or change the plugin installation.

The setup map chooses sources from project/global/plugin skills (including optional Matt Pocock skills), inactive templates, or a newly authored skill when no source fits. `setup_roles.py install` copies a complete skill directory and installs the explicitly supplied profile in one layer. Adaptations modify local copies only; separate names allow independent adaptations of one source in both layers. Policymaker instructions are local to policy, while its permissions stay owner-controlled.

Before enabling execution, `setup_roles.py validate --baseline setup-baseline` requires a selected execution shaper, all required policy roles, local skills and layer instructions, valid profiles within accepted limits, and a clean accepted baseline matching HEAD. Every local managed definition and skill reference must also exist with matching content in that commit, including files hidden by ignore rules; ignored work products outside managed definitions remain allowed. It prints the common managed revision without starting any session. The setup notes contain the selection and arming procedure. This adds no migration, update or reconfiguration mode.
