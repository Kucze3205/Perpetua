# Session reports and suggestions

Before the agent starts, the engine exports trusted marked comment bodies into
`.session/reports/` for Policymaker and shaper sessions. `index.json` lists issue IDs,
local paths, comment URLs and body SHA-256 snapshots. Each issue JSON includes the
complete body. Reports cover the last five iterations in the input state, plus
sources of pending suggestions. The existing trusted-author check and report marker
are both required. Agents need neither `gh` nor credentials to read this input.
Explicit report selectors for other roles use the declarative exports contract.

Any session can produce `.session/suggestions.json`. The engine does not read it: when the session ends, the
epilogue archives the raw text (at most 256 KiB) as `iterations/suggestions/<issue>.json`, a protected path outside
`.github/`, once per session. A final report is also snapshotted below the protected
`.github/loop/data/session-reports/<issue>/` directory, and iteration session summaries refer to those paths.

Everything else about suggestions is policy. `policy/session-state.py` validates the archived outboxes (an array of at
most 100 objects with exactly `kind`, `what`, `why` and `evidence`; nonempty strings of at most 20,000 characters, `kind` a
lowercase identifier), gives each entry an ID derived from the source issue and its content, and lists the pending ones in
`loop-state.json` `suggestions` with their source report, plus `suggestion_diagnostics` for refused entries.
The Policymaker decides a suggestion by writing `policy/suggestion-decisions.json`,
`{"<id>": {"status": "handled" | "rejected", "reason": "..."}}`, as part of its policy change; the decision holds when that
change merges. `policy/policymaker-trigger.py` receives the outboxes no Policymaker session has been shown yet
(`new_outboxes`) and decides whether they call it.
