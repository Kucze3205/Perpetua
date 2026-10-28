# Session context

The Policymaker and shaper start from a compact `loop-state.json`: current facts,
the previous Iteration's outcome and a history pointer. The engine still computes
`no_progress` from the full history and retains full snapshots and immutable records.
The state comment is omitted from `issue.md`.

`policy/session-state.py` builds `loop-state.json` and the files under `.session/history/` for every role. The engine writes
the facts (the stored state and the records and report snapshots the role may see) and runs the script in the tokenless job;
the answer is `{"loop_state": {...}|null, "files": {"history/...": "text"}}` (at most 8 MB). Invalid output fails the
iteration and rolls `execution/` and `policy/` back. A change to the script reaches sessions staged after it is accepted.

## Three ways to reach history

1. **Working memory** carries the current approach, open threads, attempts and
   reasons, with exact sources and an indication of which results it considers.
   Initially the Policymaker maintains `policy/working-memory.md` and the shaper
   maintains `journal/working-memory.md`; other roles report findings. Update in
   an existing Session when understanding changes. Memory may be stale, including
   after rollback; use current state and sources to resolve discrepancies.
2. **Searchable Session documents** live at `.session/history/sessions/<issue>/`.
   `task.md` has task metadata, results and task text. `<snapshot>.md` has a factual
   header and the original report with real line breaks. Use Grep/Glob to find
   filenames or short matches, then Read selected sections. Chronology is a filter,
   not a prerequisite for finding work.
3. **Records and exact snapshots** provide detail. Prepared Iteration records live
   at `.session/history/records/<iteration>.json`. Report documents retain their
   source URL and body hash. `reports/index.json` lists the current reports; a stored snapshot is not automatically current.

`.session/history/manifest.json` describes scope, checkout and counts, not every
entry. No match is not proof that something was never attempted. See
[reports](session-reports.md) and [exports](session-exports.md) for source semantics.
The Policymaker and shaper default to all permitted history in the prepared checkout.
Other roles receive task-selected inputs.

## Declarations controlled by the Policymaker

Role `SKILL.md` frontmatter may declare concrete workspace-relative startup paths:

```yaml
startup:
  - .session/loop-state.json
  - .session/capabilities.json
  - journal/working-memory.md
  - .session/role/references/current-method.md
```

The engine includes its contract/bootstrap, task and role skill. One resolved list
generates startup Read instructions and the provider estimate; file aliases count
once. Further references are read when needed. Remove duplicate unconditional Read
instructions from role prose/prompts. Paths select allowed recipient files, not
commands, globs, parent traversal or Git internals. Missing files are diagnostics.

Without `startup`, existing staged state/capabilities/Policymaker prompt and working
memory (when present for Policymaker/shaper) are initial reads. The Policymaker can
replace these defaults, including its own. Its own profile stays engine-owned.

Skill frontmatter may also supply default `exports` using the existing selectors.
An explicit issue declaration takes precedence. Either declaration changes the
Policymaker/shaper's archive scope to task-selected inputs; `exports: []` opts out
of the full archive view. Mandatory current inputs
remain independent. Memory organization and authorship are mutable policy choices.

## Nonblocking startup budget

Every model Session gets `.session/startup.json`: model, provider estimate,
document paths/hashes/byte sizes and `ok`, `over_budget` or `unmeasured` status.
Owner `engine.yml` `startup_token_warning` defaults to 100000, a warning threshold
and not a target. Script-only sessions have no token budget.

Trusted staging makes one request to
[`messages/count_tokens`](https://platform.claude.com/docs/en/build-with-claude/token-counting)
using `ANTHROPIC_API_KEY`, wired only into the workflow's staging step. The key
never goes to the Session. Counting/summarizing starts no extra model Session.
The estimate covers contract, bootstrap and declared documents; diagnostic output,
CLI internals and later reads are excluded. It is not full active-context usage
or a guarantee about model quality.

Missing credentials, unsupported models, provider errors, nontext documents and missing declared files
produce `unmeasured`; characters are not reported as tokens. Over-budget estimates
and provider/notification failures never stop the Session.

The Session sees a warning and the Policymaker receives it at its next permitted
Session, at an Iteration boundary. One active incident per role updates without
repeated policy triggers after a successful Policymaker Session has received it.
An unsuccessful Session leaves its notices pending. Successful measurement clears
the incident; recurrence triggers again. Policymaker sees its own warning directly.
Immutable Iteration summaries
retain startup size/status independently of later incident resolution.
