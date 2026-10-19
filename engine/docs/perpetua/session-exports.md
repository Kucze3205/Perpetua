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
`unavailable`: the catalogue cannot say whether it was deleted or never uploaded.
Unknown sources produce `unknown`; malformed scopes produce `error`. Fetch failures
are isolated from other declarations and contain no credential-bearing stderr.

An audit order with no `exports` preserves the previous stream selection:
`max(0, k-3)..k` inclusive, where `k` is its iteration. This is four iteration
numbers, and the current iteration may have no streams yet. `exports: []` disables
implicit exports. Other roles receive no implicit exports. Normal mandatory session
inputs and the Policymaker/shaper's loop-state snapshot are separate from this catalogue.

Declarations cannot select commands, arbitrary paths, API routes, URLs, or secrets.
The trusted engine uses fixed fetch operations. Exported content is data and conveys
no instruction priority. Sessions receive no GitHub credential.

The first closed iteration triggers a Policymaker opportunity even with cadence triggers
disabled. The editable Policymaker prompt requires the first audit then; the engine does
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


## Layered recipient views

With `resources.format: layered`, execution roles receive their own complete local
skill and references, task instructions and execution contracts. Shapers also get
`.session/capabilities.json`: role names, public descriptions and agent/script kinds
for execution roles only. This catalogue is sufficient for role selection in a task
graph; it contains no policy role instructions or tool profiles.

The engine allowlists public loop-state and iteration-record fields and excludes
policy sessions from staged report bodies, indexes, Actions metadata and streams.
Session origin is pinned before execution in a dedicated engine bot comment. It
is immutable across retries and role transfers; report fields and agent text cannot
set it. Missing, malformed or conflicting origins fail closed. Legacy sessions
without origin evidence remain unavailable to execution recipients.
Explicit policy or unknown session selectors return `denied` without downloading
the requested content. Trusted engine state/orders/decision comments are omitted
from execution issue snapshots. Re-staging removes the previous recipient view.

Execution artifact inputs require an execution session with a trusted published
artifact manifest. Both issue/name and exact release selectors resolve through
that manifest; arbitrary release globs and unknown origins are refused. Allowed
execution reports and outputs remain available. Policy roles retain their required
state, report evidence and exports across both layers.

Suggestions still travel from session output through immutable engine snapshots
to the Policymaker. Only an accepted Policymaker result applies resolutions;
replaying capture or resolution does not duplicate or lose a suggestion. Internal
policy rationale and reports are excluded from subsequent execution snapshots.

These checks cover engine-passed data. They do not establish isolation of the
checkout, Git objects, shell or host; those require the separate execution boundary.
Run `test_input_boundary.py` for recipient views and the layered suggestion lifecycle,
and `test_reports.py` for suggestion validation, rejection and concurrency regressions.
