# Managed definitions

The engine-owned `.github/loop/managed_resources.py` defines the supported
layered format. Configuration loading, role resolution, staging, admission,
iteration revisions and rollback use this boundary.

New templates declare `resources: {format: layered}`. Omission uses that same
format; explicit legacy formats are rejected. Existing loops keep their own
versioned engine. Explicit plugin updates are separate from format migration;
see [setup and updates](engine.md#setup-and-updates).

## Active definitions

The execution layer uses `execution/profiles.yml`, `execution/roles/<role>/`,
`execution/protocol.md`, and `execution/prompts/role.md`. The policy layer uses
`policy/profiles.yml`, `policy/roles/<role>/`, `policy/protocol.md`, and
`policy/prompts/role.md`. `policy/prompts/policymaker.md` supplies the Policymaker
bootstrap template. Shared managed parameters remain in `policy/params.yml`;
the engine-invoked measurement remains in `policy/evaluator/` (or the accepted
`evaluator_paths`). The measurement is separate from its maintaining role.

Role skills may declare `startup` reads and default `exports` in their frontmatter.
The Policymaker controls these declarations, including its own. See
[Session context](session-context.md) for working memory, historical discovery and
the nonblocking startup budget.

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
policy roles may be installed in the policy profile file. Skill
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
`.session/role/`, including the Policymaker. Staging
uses the accepted checkout and the selected layer, with no fallback to the
other layer and no writes to global skill installations. Script roles receive
their own profile's command. Layer selection does not grant additional tools
or network permissions: profiles retain independent permissions and existing
accepted-limit checks. Filesystem isolation and output admission have their
contract in [session boundary](session-boundary.md#output-admission).

Execution roles cannot admit changes to managed definitions even if their
`write` patterns include those locations; they use suggestions instead.
Permitted definition changes in either root are managed changes, bypassing
the product change gate while preserving separate evaluator verification.
The engine rejects an ambiguous candidate catalog before publication. Products
continue through protected/write checks and the accepted-tree change gate.

## Layer rules and activation validation

The engine checks the structure of the catalog (valid YAML, role names, one profile per role, no `layer` field, no links,
no Policymaker profile in a layer). Which layer a role belongs to, and whether definitions are complete enough to become
active, is `policy/layer-rules.py`: the engine writes the catalog as facts, the script runs in the tokenless job and prints
`{"errors": [...]}` (at most 20 strings of 300 characters). The shipped script requires `audit`, `evaluator` and the verifier
in the policy layer, refuses a policy role as shaper and a skill directory in the wrong or both layers, and at activation
requires the shaper to have an execution profile with a complete skill (or a command). A change to `execution/` or `policy/`
merges only if the script, run from the active `policy/`, finds no error; a change to the script applies from the next
change. Invalid output is not an error list: it fails the iteration and rolls `execution/` and `policy/` back.

Likewise `policy/graph-rules.py`, when present, adds task-graph rules: facts `{tasks, ordered, taken, roles}`, answer
`{"errors": [...]}`. It runs only for a graph the engine accepted, so policy can refuse more and never less.

## Layered activation and recovery

A layered revision has a `layered-v1:` identity over both managed roots and the
accepted evaluator paths. Change records in the iteration record carry this same before/after identity;
historical iteration records remain readable.

A finished Policymaker pass queues its entire candidate for the pass boundary
(with `"merge": "later"`, the end of the policy phase). Only `apply_policy` activates that candidate. Independent
evaluator verification and owner acceptance can release a held candidate back
to that queue, but neither completion activates it. At the
boundary the engine rechecks the pinned evaluator snapshot, permissions,
accepted-tree change gate and complete shaper profile/skill before publishing
the candidate in one ref update. Pending, rejected or stale
verification and failed publication
leave the accepted set in force. Products in a mixed candidate stay with the
same candidate and retain their gate requirements.

Rollback restores both roots and accepted evaluator paths from the last closed
iteration's commit, or `setup-baseline` before the first closed iteration. It
restores deleted definitions and removes newly added definitions, retaining
products, reports and Git history. Quiet time, consecutive failed
Policymaker sessions and owner-wait accounting retain their existing rules. A third trigger: an iteration closed
without a record, or failed on invalid policy-supplied data (for example, no shaper and no valid Policymaker orders),
rolls both roots back and opens a `notice` that the next Policymaker session sees.

## New-loop setup

The shipped template has an empty `execution/profiles.yml`, no execution skills and no selected shaper; the policy layer ships the Policymaker, audit, evaluator and evaluator-verifier skills with their profiles. `setup_roles.py prepare` checks this fresh state before the setup branch is committed; it does not convert definitions or change the plugin installation.

The setup map writes each execution role's complete skill in `execution/roles/<role>/`, inspired by the Skill reference library (Matt Pocock's skills) rather than copied from it. `setup_roles.py install` then registers the explicitly supplied profile for a skill already written in one layer; it copies nothing. The shipped policy roles are edited in place when the goal needs it. Policymaker instructions are local to policy, while its permissions stay owner-controlled.

Rules for a role: one responsibility (do not combine skills "just in case"); a narrow `write`, only the paths the role produces; no tool beyond what the owner accepted (`limits` in `engine.yml`); no `gh` and no token, since the engine does the rest. A model role is a `SKILL.md` with `name` and `description` frontmatter and sections for method, output (checkable items) and never. Protocol: the engine contract in system instructions; terms: `CONTEXT.md`.

Before enabling execution, `setup_roles.py validate --baseline setup-baseline` requires all required policy roles, local skills and layer instructions, valid profiles within accepted limits, and a clean accepted baseline whose managed definitions and configuration match HEAD. `shaper: null` is valid; if selected, the shaper must be a complete execution role. Every local managed definition and skill reference must also exist with matching content in that commit, including files hidden by ignore rules; ignored work products outside managed definitions remain allowed. It prints the common managed revision without starting any session. The setup notes contain the selection and arming procedure. This is fresh-setup validation; it does not migrate formats or reconfigure an existing loop.

## Ownership after arming

These are the shipped boundaries after arming. The owner's ability to change architecture during setup gives the
Policymaker no additional authority. Any owner-chosen change to these boundaries must be explicit, implemented and
documented during setup; otherwise they remain in force.

| Zone | Contents | Changed by |
|---|---|---|
| Workflows | `.github/workflows/` | the owner only (the loop's token cannot write them) |
| Engine scripts and config | `.github/loop/`, `engine.yml`, `ENGINE_VERSION` | the owner only (the loop may ask through a `question` request); sessions cannot write them (`protected`) |
| Managed definitions | `execution/`, `policy/` | the Policymaker, with evaluator changes delegated as below; merged without the change gate, still within the role's `write` paths |
| Audit and journal | `audit/`, `journal/` | the audit role (`audit/`), the journal role (the generic orchestrator, when installed) (`journal/`); merged without the change gate, still within each role's `write` paths |
| Evaluator | `policy/evaluator/` | only a role whose profile has `owns_evaluator`, ordered only by the Policymaker, and only after the evaluator verifier passes |
| Records and goal | `iterations/`, `goal/`, `GOAL_REACHED` | the engine only, except goal files the setup map writes before arming; the owner accepts what goes into `goal/` |
| Work | everything else | task sessions, through the change gate |

The Policymaker never touches the engine, the goal gate, `AUTOPILOT` or a token. `AUTOPILOT` (repository variable) is the
owner's switch; it is the first condition of every workflow and anything but a literal `on` means stop.

## Setup baseline after an engine change

`validate --baseline <tag>` compares the revision of the managed definitions (`execution/`, `policy/`, evaluator paths) at `HEAD` with the one in the tag's commit, and `engine.yml` and `goal/` file by file; it does not compare commit SHAs. Two operations follow:

- **Engine fix after arming**: a commit changing only `.github/loop/` (scripts, tests). The tag stays; validation passes.
- **Definition change**: a change to `execution/`, `policy/`, `engine.yml` or `goal/`. Validation fails until a new `setup-baseline` tag is agreed with the owner.
