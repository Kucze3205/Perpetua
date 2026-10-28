# Policy and evaluator change provenance

The engine stores immutable receipts in `iterations/changes/`. Existing iteration records
remain readable; absent new fields mean no provenance was recorded, not that no change occurred.
The engine never enriches a closed iteration record later.

`change-<session issue>.json` records each accepted policy/evaluator change. It names the
actual authoring role and issue, the branch's `base_commit`, the rebased `applied_base_commit`,
and the checked code's `accepted_commit`. `changed_paths` is the diff of the latter two
commits; concurrent changes by other roles are excluded. `policy_rev_before/after` and
`evaluator_rev_before/after` describe those same trees. Revisions are tree hashes, not commits.

The receipt is an engine-only commit immediately following the accepted code commit. Both
commits enter the default branch in one atomic push after checks and the change gate; a
failed push publishes neither receipt nor code. `applied_at` is the timestamp of that
publication attempt, not the later iteration-close timestamp. A retry creates a new
publication timestamp; only the successful attempt survives. Replaying an accepted session
returns its existing receipt without rewriting it.

`source_iteration` identifies the triggering session's label. `applied_iteration` names the
iteration open at publication, or is null when between iterations. `effective_from_iteration`
is that iteration, or the next one when it is null. New iteration records add `policy.change_ids` and
`evaluator_verifications` referring to events associated with that iteration. Receipts keep
the exact application history even if a later publication occurs after its source iteration
has closed. An evaluator ordered by Policymaker is attributed to its evaluator role.

Verifier issues pin both `checked-commit` and `checked-evaluator-rev` and start from that
commit. A branch mutation or an evaluator changed by rebase requires fresh verification.
The terminal `verification-<verifier issue>.json` keeps the change issue, verifier issue,
session status (`done`, `blocked`, `crashed`, etc.), reported result (`pass`, `fail`, or null),
checked snapshot, and `application_result`. `pass` may accompany `conflict`,
`verification-stale`, or `awaiting-owner`; it never implies a successful application.

An owner decision after a passing verification creates a separate
`verification-<verifier issue>-owner.json`, preserving the original verdict and pending
application outcome. Owner approval still checks the pinned snapshot. Repeated terminal
events cannot overwrite receipts. Policymaker sessions retain the existing pass-boundary
application of their policy changes and orders. The evaluator role owns evaluator changes;
the Policymaker cannot write evaluator files directly.
