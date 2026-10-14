---
name: setup
description: Set up an autonomous agent loop in the current GitHub repository for a goal. Prepares the repo and charts the setup map with wayfinder; closing the map arms the loop.
argument-hint: <goal>
disable-model-invocation: true
---

Set up a Perpetua loop in this repository. Terms (loop, owner, setup map, engine) are defined in `${CLAUDE_PLUGIN_ROOT}/CONTEXT.md`.

You do four things, once, then hand over to wayfinder. You decide no roles, policy, secrets or limits: the setup map does. There is no resume mode: if the setup was already run, say so and stop.

The goal is `$ARGUMENTS`. If it is empty, ask the owner for it in one line and wait.

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
- `${CLAUDE_SKILL_DIR}/setup-notes.md` to `docs/perpetua/setup-notes.md`, with `{{REPO}}` (`owner/name`), `{{BRANCH}}` and `{{ENGINE_VERSION}}` replaced.

If any other destination file already exists, stop and list them; overwrite nothing. Quote paths: on Windows they contain `C:\`.

Commit as `Perpetua engine <ENGINE_VERSION>` and push the branch. Stay on it: the setup map writes its configuration there. The default branch is untouched until topic H.

## 4. Hand over to wayfinder

Read `${CLAUDE_SKILL_DIR}/brief.md`, replace `{{GOAL}}`, `{{REPO}}` and `{{BRANCH}}`, and call the Skill tool with `wayfinder`, passing the result as its arguments. Do not summarise or reword the brief.

Wayfinder charts the map with the owner and stops. Afterwards tell the owner the map's URL and that work continues with `/wayfinder <URL>`.
