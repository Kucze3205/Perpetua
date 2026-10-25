# Managed definitions

The engine-owned `.github/loop/managed_resources.py` defines the supported
layered format. Configuration loading, role resolution, staging, admission,
receipts, iteration revisions and rollback use this boundary.

New templates declare `resources: {format: layered}`. Omission uses that same
format; explicit legacy formats are rejected. Existing loops keep their own
versioned engine (ADR-0001); this release supplies no migration or update.

## Active definitions

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

## Offline executor boundary

Layered execution profiles with `agent: false` and `internet: false` run in a
Linux bubblewrap namespace with a private process tree, network, home and temp
directory. Only explicit runtime files, the sanitized work snapshot and the
selected script/offline enforcement program are mounted. Neither the engine
checkout, staged engine metadata, host home, host temp nor other host checkouts
are mounted. Seccomp also denies socket creation by the script and its children.
Missing bubblewrap, required runtime files, namespace denial or missing admission
metadata fails closed. Runtime directory names confer no access: policy copies
under `/usr`, executable/library parents and Python's library directory stay
private too.

Candidate-owned product checks and task verification for layered execution roles
use the same offline namespace and narrow runtime after admission. Each command
gets a disposable product-only snapshot with a new Git root; check writes do not
alter the candidate. Missing isolation fails the check, without a host fallback.
The accepted change gate still runs through its trusted measurement path with
the full candidate and baseline required by ADR-0003.

The supported runtime is Bash, Git, Python 3 and the basic file/text commands
listed in `executor_boundary.runtime_files`. Python supports the standard modules
explicitly imported by its trusted `-I -S` probe and their imported dependencies;
site packages and arbitrary additional modules are excluded. The engine resolves
each executable and imported Python file separately, then adds individual ELF
shared-library dependencies from `ldd`. It never mounts a runtime parent
directory. Adding tools or Python modules requires extending this trusted list;
work scripts cannot expand it through their environment, current directory or
Python startup customization. This same file allowlist can be used by the model
and online boundaries without granting access to host directories.

The work snapshot excludes managed definitions in both layers, engine internals/configuration, policy iteration/audit records and
tool discovery directories. It retains regular tracked product files and the
already filtered `.session/` recipient view. Redirected product inputs are
refused. Its fresh Git root commit contains only that view: no original commit
IDs, object store, alternates, reflogs, remotes, credential configuration or
hooks. Scripts can use Git locally, but must leave their final files in the
working tree; the engine admits those bytes, including uncommitted work.

The trusted epilogue never invokes worker Git for these sessions. It reads
regular output files as data and builds an index against the staged original
baseline in the engine repository, retaining excluded definitions unchanged.
Symlinks, hardlinks and special output files are refused. Binary contents,
executable modes and deletion of visible inputs are preserved. Worker Git
configuration, hooks and attributes cannot execute during this import. The
result then passes the existing protected/write checks and merge path; the
change gate still runs from the accepted tree with the reconstructed candidate
and accepted baseline. This does not migrate legacy installations.

Run `python3 -m unittest discover -s tests -p test_executor_boundary.py -v`
from `.github/loop` on Linux with bubblewrap namespaces enabled. These tests
execute the staged script and epilogue, probe host/files/Git/network access,
attempt credential-bearing worker Git hooks/configuration, and check data
preservation and admission refusals. A skip on another platform is not evidence
of isolation. No provider or actual GitHub credential is needed.
## New-loop setup

The shipped template has an empty `execution/profiles.yml`, no execution skills and no selected shaper; the policy layer ships the Policymaker, audit, evaluator and evaluator-verifier skills with their profiles. `setup_roles.py prepare` checks this fresh state before the setup branch is committed; it does not convert definitions or change the plugin installation.

The setup map writes each execution role's complete skill in `execution/roles/<role>/`, inspired by the Skill reference library (Matt Pocock's skills) rather than copied from it. `setup_roles.py install` then registers the explicitly supplied profile for a skill already written in one layer; it copies nothing. The shipped policy roles are edited in place when the goal needs it. Policymaker instructions are local to policy, while its permissions stay owner-controlled.

Rules for a role: one responsibility (do not combine skills "just in case"); a narrow `write`, only the paths the role produces; no `internet` together with writing code beyond what the owner accepted (`limits` in `engine.yml`); no `gh` and no token, since the engine does the rest. A model role is a `SKILL.md` with `name` and `description` frontmatter and sections for method, output (checkable items) and never. Protocol: `.session/protocol.md`; terms: `CONTEXT.md`.

Before enabling execution, `setup_roles.py validate --baseline setup-baseline` requires a selected execution shaper, all required policy roles, local skills and layer instructions, valid profiles within accepted limits, and a clean accepted baseline matching HEAD. Every local managed definition and skill reference must also exist with matching content in that commit, including files hidden by ignore rules; ignored work products outside managed definitions remain allowed. It prints the common managed revision without starting any session. The setup notes contain the selection and arming procedure. This adds no migration, update or reconfiguration mode.
