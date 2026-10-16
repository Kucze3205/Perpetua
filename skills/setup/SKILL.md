---
name: setup
description: Set up an autonomous agent loop in the current GitHub repository for a goal. Prepares the repo and charts the setup map with wayfinder; closing the map arms the loop.
argument-hint: <goal and optional owner context>
disable-model-invocation: true
---

Set up a Perpetua loop in this repository. Terms (loop, owner, setup map, engine) are defined in `${CLAUDE_PLUGIN_ROOT}/CONTEXT.md`.

You do four things, once, then hand over to wayfinder. You decide no roles, policy, secrets or limits: the setup map does. The copied engine supplies architecture defaults; the owner may change them during setup, including the provider, substrate and owner-request channel. The map records and implements each decision and its specific lost guarantees before arming; this grants no additional authority to the Policy after arming. There is no resume mode: if the setup was already run, say so and stop.

Read `$ARGUMENTS` as free text. Separate the desired outcome (**goal**) from the owner's **context**: architecture choices or constraints, requested engine changes, other background, and the question preference (`none`, `key topics`, or `all`; accept equivalent wording). A goal alone works as before. Keep context out of the goal; preserve the owner's meaning without inventing requirements. If the goal is missing or its boundary with context is genuinely ambiguous, ask one precise question that resolves it and wait; otherwise proceed with the stated goal.

Prepare an owner-context block with separate fields for architecture, requested engine changes, other context, and question preference. Mark absent context fields as not supplied; default an unspecified question preference to `key topics`. Explicit owner choices take precedence over Defaults. Requested engine changes remain requests to resolve and implement in the map, not claims that the engine already supports them. The question modes and their required-decision exception are defined in `setup-notes.md`.

## 1. Preflight (read-only)

Stop at the first failure and say what to fix, in one line each:

- inside a git work tree with a clean status and a GitHub `origin` (`gh repo view --json nameWithOwner,defaultBranchRef`)
- `gh auth status` is logged in; GitHub Actions is enabled (`gh api repos/{owner}/{repo}/actions/permissions --jq .enabled`)
- `$HOME/.claude/skills/wayfinder/SKILL.md` exists. If not, tell the owner to copy the `mattpocock-skills` wayfinder directory there with the `disable-model-invocation` line removed, then stop.
- the skills wayfinder calls are available to you: `grilling`, `domain-modeling`, `research`, `prototype` (the `mattpocock-skills` plugin)
- the repo has no `ENGINE_VERSION` and no open or closed issue labelled `wayfinder:map` (otherwise the loop is already set up here)

## 2. Labels

Create any of `wayfinder:map`, `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, `wayfinder:task` that are missing (`gh label create`). Do not touch existing labels. The engine and the roles create their own labels.

## 3. Engine on the setup branch

Create branch `perpetua/setup` from the default branch. Copy into the repo:

- the contents of `${CLAUDE_PLUGIN_ROOT}/engine/` into the repo root;
- `${CLAUDE_PLUGIN_ROOT}/CONTEXT.md` to `CONTEXT.md`, the loop's one glossary. If the repo already has a `CONTEXT.md`, append the plugin's `## Language` entries to it under a `## Perpetua` heading instead;
- `${CLAUDE_SKILL_DIR}/setup-notes.md` to `docs/perpetua/setup-notes.md`, with `{{REPO}}` (`owner/name`), `{{BRANCH}}`, `{{ENGINE_VERSION}}` and `{{OWNER_CONTEXT}}` (the owner-context block above) replaced. Persist the context before handing over, so map continuations can recover it from this file.

If any other destination file already exists, stop and list them; overwrite nothing. Quote paths: on Windows they contain `C:\`.

Commit as `Perpetua engine <ENGINE_VERSION>` and push the branch. Stay on it: the setup map writes its configuration there. The default branch is untouched until topic H.

## 4. Hand over to wayfinder

Read `${CLAUDE_SKILL_DIR}/brief.md`, replace `{{GOAL}}` with only the goal, `{{OWNER_CONTEXT}}` with the same owner-context block written to the setup notes, and `{{REPO}}` and `{{BRANCH}}`; call the Skill tool with `wayfinder`, passing the result as its arguments. Do not summarise or reword the brief.

Wayfinder charts the map with the owner and stops. Afterwards tell the owner the map's URL and that work continues with `/wayfinder <URL>`.
