# GitHub Actions on Node 24: versions of checkout and upload/download-artifact

Research for [#414](https://github.com/Kucze3205/Perpetua/issues/414) (map [#405](https://github.com/Kucze3205/Perpetua/issues/405)).
Sources are primary: `action.yml` at each major tag and the GitHub release notes of `actions/checkout`,
`actions/upload-artifact`, `actions/download-artifact`, the `actions/runner` source and release notes, and the GitHub
changelog. English only (all of these publish in English). Checked 2026-11-24.

## Answer

| Action | Last major on `node20` | First major on `node24` | Latest major |
| --- | --- | --- | --- |
| `actions/checkout` | v4 | **v5** | v7 (v7.0.1) |
| `actions/upload-artifact` | v5 (v5 only "supports" Node 24, `using` is still `node20`) | **v6** | v7 (v7.0.2) |
| `actions/download-artifact` | v6 (same: `node20`) | **v7** | v8 (v8.0.2) |

Evidence: `runs.using` in `action.yml` at the `v4`..`v8` tags: checkout v4 `node20`, v5/v6/v7 `node24`; upload-artifact
v4/v5 `node20`, v6/v7 `node24`; download-artifact v4/v5/v6 `node20`, v7/v8 `node24`. The v6/v7 release notes of the two
artifact actions say so: "v5 had preliminary support for Node.js 24, however this action was by default still running on
Node.js 20." The node24 majors (checkout v5, upload v6, download v7) require runner >= 2.327.1 (matters for self-hosted
runners only).

The "Node.js 20 is deprecated" warning does **not** come from the action's code. It is a runner annotation (`actions/runner`
PR #4242, "Add Node.js 20 deprecation warning annotation"). It goes away once no step uses a `node20` action, so bumping
is the fix. But the runner now forces Node 24 regardless (below), so the bump is no longer needed to make actions run,
only to silence the warning and be on supported tags.

## Runner-side enforcement (timeline)

- 2025-09-19 GitHub changelog "Deprecation of Node 20 on GitHub Actions runners": runner v2.328.0 supports both, Node 20
  still default; `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` opts in early. Editor's notes moved the dates: from
  **2026-06-16** runners use Node 24 by default; opt-out `ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION=true` works "only until
  we upgrade the runner and remove Node20 on **September 23rd, 2026**".
- `actions/runner` `HandlerFactory.cs` on `main`: "Keep accepting legacy action manifests, but execute all JavaScript
  actions using node24" (`nodeData.NodeVersion = "node24"` unconditionally). Commits: 2026-03-17 "Node 24 enforcement",
  2026-11-04 "remove bundled node20 (#4836)".
- Consequence for the engine today: `@v4` steps work only because the runner runs `node20` manifests on Node 24. The risk
  is not "breaks at date X" but "depends on the runner's compatibility shim for legacy manifests, and prints the warning".

## Does the bump change artifacts? (what the engine uses)

Engine usage (`engine/.github/workflows/*.yml`): 8x `checkout@v4`, 11x `upload-artifact@v4`, 12x `download-artifact@v4`
(31 in total, matching the issue). Outside the engine: `.github/workflows/build-spec.yml` and
`.github/workflows/engine-tests.yml` (5x `checkout@v4`, 1 upload, 1 download). Uploads use only `name`, `path` and
(once) `if-no-files-found`; downloads only `name` and `path`. No `merge-multiple`, `pattern`, `artifact-ids`, `overwrite`,
`include-hidden-files`, `run-id`, `github-token`. `setup-node` is not used.

- **Upload/download compatibility between versions.** v4 through v8 use the same artifact backend (`@actions/artifact`
  v2+). The release notes of v5/v6 say the Node 24 support "is not a breaking change per-se", and v6/v7 list no format
  change. The incompatible line is v3 (old backend), irrelevant here. Mixed versions are fine, but bumping upload and
  download together keeps one rule.
- **Names.** Unchanged (v8.0.1 only adds CJK names).
- **`merge-multiple`.** Unchanged, not used. download-artifact v5 changed the extraction path of a single artifact
  downloaded by `artifact-ids` (now directly in `path/`); the engine uses `name:`, which was already direct.
- **Hidden files.** `include-hidden-files` defaults to `false` since upload-artifact v4.4.0 and still does in v7, so
  dotfiles are not uploaded in v4 either. The bump changes nothing. (Whether any engine artifact directory contains
  dotfiles was not checked.)
- **upload v7.** New optional `archive: false` (single file, no zip); default `true`, so nothing changes. v7 and v8 are
  ESM; transparent to callers.
- **download v8 (the only real behavior change).** (1) A mismatch between the downloaded zip and the digest the server
  recorded now **fails** by default (`digest-mismatch: error`; it used to warn). (2) The action no longer unzips
  everything: it checks `Content-Type` and skips non-zip downloads; `skip-decompress: true` keeps the zip. Engine artifacts
  are zips from v4+ uploads, so (2) is a no-op and (1) only adds failures on a corrupt download.
- **checkout.** v6.0.0 persists credentials in a separate file instead of `.git/config` ("Persist creds to a separate
  file"); later git steps still authenticate, but anything reading the token out of `.git/config` would not find it.
  v7 (and backports v5.1.0 and v6.1.0, marked **[BREAKING]**): `allow-unsafe-pr-checkout` blocks checking out a fork PR
  head under `pull_request_target` / `workflow_run` by default. Matters only if a workflow triggers on those events (not
  checked here).

## `artifact_pin.py` (ADR-0006)

`engine/.github/loop/artifact_pin.py` hashes the extracted directory, not the zip: sha256 of `sha256sum *` over its plain
files, rejecting symlinks and sub-directories. It does not touch the action, the zip, the artifact ID or the server digest,
so it does not depend on the action version as long as the extracted file set is the same. The v8 server-digest check is
a separate layer (GitHub's upload-vs-download integrity); it does not replace the job-output pin, which defends against a
job uploading a different artifact under the same name.

## Recommendation (for the spec)

Minimal bump that clears the warning: `checkout@v5`, `upload-artifact@v6`, `download-artifact@v7`. Current majors:
`checkout@v7`, `upload-artifact@v7`, `download-artifact@v8`. With the inputs the engine uses, the added risks are v8's
`digest-mismatch: error` (desirable) and, from checkout v6, the relocated persisted credentials. One mechanical sweep
`@v4` -> latest across `engine/.github/workflows` and the two other workflows is low-risk. Not verified: no real
workflow run was done with the new versions; run one real-Actions loop in this repo after the sweep.
