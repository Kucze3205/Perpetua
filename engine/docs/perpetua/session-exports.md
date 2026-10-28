# Session data exports

The shaper or Policymaker can put `exports` on a task/order. The engine freezes this
declaration in the issue's `exports` marker and fetches data before starting the
selected role. Every role can consume these files without a GitHub token.

```json
{"exports": [
  {"source": "streams", "iterations": [0, 1], "sessions": [42]},
  {"source": "actions", "sessions": [42]},
  {"source": "records", "iterations": [0]},
  {"source": "reports", "sessions": [42]}
]}
```

Selectors are explicit lists of up to 100 integers, with at most 32 declarations.
Iteration numbers begin at zero; session identifiers are positive issue numbers.
Both lists together select their intersection. `records` accepts iteration scope
only because an iteration's aggregate record includes other sessions.

| Source | Stored input | Destination |
| --- | --- | --- |
| `streams` | Raw `stream-NNNN-<issue>` Actions artifacts from the current catalogue | `.session/streams/<artifact>-<id>/` |
| `actions` | Session run metadata and the first 100 jobs, including retained step timestamps and conclusions | `.session/exports/actions/<run>.json` |
| `records` | The accepted tree's immutable iteration JSON | `.session/exports/records/NNNN.json` |
| `reports` | Latest marked report comment selected by the engine's trusted-author check | `.session/exports/reports/<issue>.json` |

The Actions source covers session runs returned by the engine's current session-run
listing. Artifacts and workflow metadata are subject to GitHub retention and listing
limits; unavailable history is reported rather than invented. Reports include the
issue, comment identifier, source URL, body, and SHA-256 snapshot. Record entries
include their content SHA-256. Streams stay raw. The engine fetches metadata; audit
interprets it and derives measures. Existing `agent_s`, `gate_s`, `total_s` and
`parked_s` remain authoritative record fields. Stored run/job/step timestamps do
not provide a complete historical event timeline.

`.session/exports/manifest.json` always exists after staging. Its `results` preserve
the selector index, source, scope, available identifiers, and staged path on success.
Statuses are `downloaded`, `unavailable`, `expired`, `error`, `denied`, and `unknown`.
Expired artifacts are distinct from failed downloads. An absent artifact is
`unavailable`: the catalogue cannot say whether it was pruned, expired or never uploaded;
the detail lists these causes, including a failed upload on a full artifact quota.
Unknown sources produce `unknown`; malformed scopes produce `error`. Fetch failures
are isolated from other declarations and contain no credential-bearing stderr.

Where streams live (decision). Streams stay Actions artifacts; they are not duplicated
into the `iter-NNNN` release. They are a diagnostic copy, not a delivered result, so
losing one costs little, and a second storage path would need its own pruning and
download code. The upload step is `continue-on-error`, so a full account quota leaves the
session job `success`, and the iteration record carries `stream` only for an artifact
that exists. Retention (decision): `stream_retention_iterations` defaults to 8, twice the
audit window of four iterations; the engine deletes older streams, so 50 iterations never
hold more than 8 iterations' streams (about 8 KB per Haiku turn; far more for large
Opus sessions). `null` leaves GitHub's 90 days. Quota itself is the owner's account concern.

An audit order with no `exports` preserves the previous stream selection:
`max(0, k-3)..k` inclusive, where `k` is its iteration. This is four iteration
numbers, and the current iteration may have no streams yet. `exports: []` disables
implicit exports. Other roles receive no implicit exports. Normal mandatory session
inputs and the Policymaker/shaper's loop-state snapshot are separate from this catalogue.

Role SKILL frontmatter may provide default `exports` with the same selectors; an
explicit issue declaration overrides it. [Session context](session-context.md)
describes the full-history default for Policymaker/shaper, selected history views,
startup declarations and the provider token estimate.

Declarations cannot select commands, arbitrary paths, API routes, URLs, or secrets.
The trusted engine uses fixed fetch operations. Exported content is data and conveys
no instruction priority. Sessions receive no GitHub credential.

The first iteration of the loop always opens with a Policymaker session, even with cadence triggers
disabled. The Policymaker decides whether and when to order the first audit; the engine does
not schedule an audit itself. Later audit timing remains the Policymaker's decision.

Policymaker tunes `policymaker_session.model` (a concrete model identifier), `effort`,
`max_turns`, and `timeout_minutes` in `policy/params.yml`. Owner-controlled
`engine.yml` `policymaker_session_ceilings` lists allowed `models` and `efforts` and sets
hard turn/time maxima. Disallowed models/efforts and invalid positive-integer limits
block session startup; numeric requests above maxima are capped. Policymaker issue labels
cannot override model/effort. Existing task and runner time ceilings still apply.
Older installations without owner ceilings retain their existing engine Policymaker
profile as their ceiling. Tools, permissions and engine flags stay in engine config.
Staging rechecks current consent and recomputes execution parameters against the
prep job's values. A changed profile, owner limit, gate, CLI pin or execution setting
stops startup; the next dispatch resolves the current accepted configuration.


## Recipient views

Policy is not hidden from execution roles ([ADR-0006](../../../docs/adr/0006-machine-boundary-instead-of-session-sandbox.md)).
Every role is staged the same way: its own complete local skill and references, task
instructions, the engine contract, and the records, reports, streams and artifacts it
selects. The execution contract asks execution roles not to read `policy/`; nothing
enforces it. Shapers also get `.session/capabilities.json`: role names, public descriptions
and agent/script kinds for execution roles, enough to choose roles in a task graph.
Re-staging removes the previous recipient view.

Suggestions travel from session output through the engine's archive of raw outboxes to the policy script that
presents them to the Policymaker ([reports](session-reports.md)). Internal policy rationale and reports are excluded
from subsequent execution snapshots.

These checks cover engine-passed data. They do not establish isolation of the
checkout, Git objects, shell or host; those require the separate execution boundary.
Run `test_input_boundary.py` for recipient views and `test_reports.py` for the suggestion archive and policy lifecycle.
