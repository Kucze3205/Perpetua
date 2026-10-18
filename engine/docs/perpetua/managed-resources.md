# Managed definitions

The engine-owned `.github/loop/managed_resources.py` describes the existing
managed resource format. Configuration loading, role resolution and staging,
change classification, accepted-change receipts, iteration revisions and
rollback use that boundary rather than choosing their own managed locations.

The current format has one managed tree, `policy/`. It contains `params.yml`,
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

This is a prefactor. It introduces no new active format, role layer assignment,
read isolation, activation rules, migration or updates to existing loops.
