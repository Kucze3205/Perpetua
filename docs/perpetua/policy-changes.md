# Policy and evaluator change provenance

The iteration record carries the provenance: no separate receipt files exist. Records closed
before this change stay readable; absent fields mean nothing was recorded. The engine never
enriches a closed iteration record later.

`policy.changes` lists each policy/evaluator change accepted while the iteration was open. An
entry names the actual authoring role and issue (`id` is `change-<session issue>`, with the
run number appended after the first run of a woken Policymaker session), the branch's `base_commit`, the rebased
`applied_base_commit`, and the checked code's `accepted_commit`. `changed_paths` is the diff
of the latter two commits; concurrent changes by other roles are excluded.
`policy_rev_before/after` describe the same trees as tree hashes, not commits.
`applied_at` is the timestamp of the successful publication attempt, not of the iteration
close. An entry is written once, after the push; replaying an accepted session returns the
existing entry. Until the iteration closes the entries live in the `changes` field of its
report. The iteration's `source_iteration` is the session's label (the trigger); the entry
belongs to the iteration open at publication.

`evaluator_verifications` lists the verifier events of the iteration. Verifier issues pin both
`checked-commit` and `checked-evaluator-rev` and start from that commit. A branch mutation or
an evaluator changed by rebase requires fresh verification. The event `verification-<verifier
issue>` keeps the change issue, verifier issue, session status (`done`, `blocked`,
`crashed`, etc.), reported result (`pass`, `fail`, or null), checked snapshot, and
`application_result`. `pass` may accompany `conflict`, `verification-stale`, or
`awaiting-owner`; it never implies a successful application.

An owner decision after a passing verification adds a separate
`verification-<verifier issue>-owner` event, preserving the original verdict and pending
application outcome. Owner approval still checks the pinned snapshot. Repeated terminal
events cannot overwrite entries. Policymaker sessions retain the existing pass-boundary
application of their policy changes and orders. The evaluator role owns evaluator changes;
the Policymaker cannot write evaluator files directly.

Rollback does not read these entries: it restores the commit the last closed iteration
recorded in `policy.commit_end`.
