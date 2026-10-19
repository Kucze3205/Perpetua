# Managed definitions

The engine-owned `.github/loop/managed_resources.py` describes both supported
managed resource formats. Configuration loading, role resolution and staging,
change classification, accepted-change receipts, iteration revisions and
rollback use that boundary rather than choosing their own managed locations.

The default legacy format has one managed tree, `policy/`. It contains `params.yml`,
`profiles.yml`, `protocol.md`, `prompts/role.md`, `prompts/policymaker.md` and
`roles/<role>/` with the complete skill and its references. The Policymaker's
session profile and protected contract still belong to the engine. Session
write permission is independent of a resource's location: the engine's existing
`protected` rules take precedence, followed by the role's `write` patterns.
Evaluator writes additionally require `owns_evaluator` and independent
verification. `direct_paths` continues to determine which changes bypass the
change gate; changing resource handling does not widen those paths or any role.

Compatibility is deliberate:

- The policy revision is the Git tree hash of `policy`, or an empty string if
  absent. Receipt schema version 1 and existing records keep their meaning.
- Evaluator receipts map configured `evaluator_paths` to Git object hashes.
  Iteration records retain their comma-separated evaluator hash string in
  configured order. Custom evaluator paths remain supported, including those
  outside `policy/`; accepted changes there still produce receipts.
- Rollback compares and restores `policy/` from the last closed iteration's
  `policy.commit_end`, or the `setup-baseline` tag before any closed iteration.
  It removes added definitions and restores removed ones. Products, journal,
  reports and change receipts remain. A custom evaluator outside `policy/`
  retains its previous behavior and is outside this legacy rollback tree.
- The existing trigger rules, accepted limits, trusted change gate command and
  credential boundary remain in force.

## Additive layered format (ADR-0004)

The owner selects `resources: {format: layered}` in protected `engine.yml`.
Omitting it (or setting `format: legacy`) preserves the shipped legacy layout.
No existing installation is migrated. This is an additive format, pending the
final template switch. Offline script executors also use the filesystem and
admission boundary described below. Model and online isolation remains separate
work; this option is still for controlled preparation and tests.

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
to both managed roots in layered mode. Explicit owner `policymaker.write`
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
legacy revision hashes keep their original meaning.

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
directory. Only OS runtime directories, the sanitized work snapshot and the
selected script/offline enforcement program are mounted. Neither the engine
checkout, staged engine metadata, host home, host temp nor other host checkouts
are mounted. Seccomp also denies socket creation by the script and its children.
Missing bubblewrap, namespace denial or missing admission metadata fails closed.

The work snapshot excludes managed definitions in both layers, inactive
`templates/`, engine internals/configuration, policy iteration/audit records and
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
