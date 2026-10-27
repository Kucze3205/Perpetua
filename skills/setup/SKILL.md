---
name: setup
description: Set up an autonomous agent loop in the current GitHub repository for a goal. Prepares the repo and charts the setup map with wayfinder; closing the map arms the loop.
argument-hint: <goal and optional owner context>
disable-model-invocation: true
---

Set up a Perpetua loop in this repository, from Claude Code or Codex. Resolve the path of this loaded `SKILL.md`, following symlinks/junctions. Call its containing directory `SKILL_DIR`; `PLUGIN_ROOT` is two directories above it, containing `engine/` and `CONTEXT.md`. These are resolved paths, not environment variables the client must substitute. Resolve resources from this installation, independently of the target repository's working directory. Terms (loop, owner, setup map, engine) are defined in `PLUGIN_ROOT/CONTEXT.md`.

You do four things, once, then hand over to wayfinder. Wayfinder first clarifies the goal with the owner, then the map develops an initial approach and derives the environment from its steps. You decide no roles, policy, secrets or limits: the setup map does. The copied engine supplies architecture defaults; the owner may change them during setup, including the provider, substrate and owner-request channel. The map records and implements each decision and its specific lost guarantees before arming; this grants no additional authority to the Policymaker after arming. There is no resume mode: if the setup was already run, say so and stop.

Read the user's invocation text as free text (`$ARGUMENTS` in Claude Code; the message accompanying the skill in Codex). Separate the desired outcome (**goal**) from the owner's **context**: architecture choices or constraints, requested engine changes, other background, and the question preference (`none`, `key topics`, or `all`; accept equivalent wording). A goal alone works as before. Keep context out of the goal; preserve the owner's meaning without inventing requirements. If the goal is missing or its boundary with context is genuinely ambiguous, ask one precise question that resolves it and wait; otherwise proceed with the stated goal.

Prepare an owner-context block with separate fields for architecture, requested engine changes, other context, and question preference. Mark absent context fields as not supplied; default an unspecified question preference to `key topics`. Explicit owner choices take precedence over Defaults. Requested engine changes remain requests to resolve and implement in the map, not claims that the engine already supports them. The question modes and their required-decision exception are defined in `setup-notes.md`.

## 1. Preflight (read-only)

Stop at the first failure and say what to fix, in one line each:

- inside a git work tree with a clean status and a GitHub `origin`: `gh repo view --json nameWithOwner,defaultBranchRef` succeeds. This is the only test; do not interpret the `origin` URL
- `gh auth status` is logged in; GitHub Actions is enabled (`gh api repos/{owner}/{repo}/actions/permissions --jq .enabled`)
- `PLUGIN_ROOT/engine/ENGINE_VERSION` and the sibling `brief.md` and `setup-notes.md` exist; read the engine version. If not, identify the incomplete installation and ask for a plugin update before changing the target repo.
- resolve `wayfinder`, `grilling`, `domain-modeling`, `research` and `prototype` from the client's available skills (including plugin-qualified names). For a skill absent from that list, check the target repo's `.agents/skills/` and `.claude/skills/`, then the user's `.agents/skills/`, `.codex/skills/` (or configured Codex skills directory) and `.claude/skills/`, for `<name>/SKILL.md`. Read wayfinder's instructions and resolve its relative resources from that file's real directory. If any dependency is missing, name it and tell the owner to install the corresponding `mattpocock-skills` skill for their client, then stop. A filesystem-backed skill can be followed without a Skill tool.
- arming permissions, without side effects. Arming needs three actions: push a tag, write a repository variable, run a workflow. Check `gh api repos/{owner}/{repo} --jq .permissions` (`push` for tag and workflow run, `admin` for the variable) and that `gh api repos/{owner}/{repo}/actions/variables` and `.../actions/workflows` answer without 403. Never create the tag, variable or run to test. Any 403 or doubt (typical in a cloud session) means the owner arms: do not stop, record the result per action as `agent` or `owner` in the notes' "Arming permissions" line (step 3) — arming then uses the owner variant.
- no destination file of step 3 exists yet (all of `PLUGIN_ROOT/engine/` relative to the repo root, and `docs/perpetua/setup-notes.md`; `CONTEXT.md` may exist). List collisions and stop; overwrite nothing. Quote paths: on Windows they contain `C:\`.
- the repo has no `ENGINE_VERSION` and no open or closed issue labelled `wayfinder:map` (otherwise the loop is already set up here)

## 2. Labels

Create any of `wayfinder:map`, `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, `wayfinder:task` that are missing (`gh label create`). Do not touch existing labels. The engine and the roles create their own labels.

## 3. Engine on the setup branch

Create branch `perpetua/setup` from the default branch. Copy into the repo:

- the contents of `PLUGIN_ROOT/engine/` into the repo root;
- `PLUGIN_ROOT/CONTEXT.md` to `CONTEXT.md`, the loop's one glossary. If the repo already has a `CONTEXT.md`, append the plugin's `## Language` entries to it under a `## Perpetua` heading instead;
- `SKILL_DIR/setup-notes.md` to `docs/perpetua/setup-notes.md`, with `{{REPO}}` (`owner/name`), `{{BRANCH}}`, `{{ENGINE_VERSION}}` and `{{OWNER_CONTEXT}}` (the owner-context block above) replaced. Persist the context before handing over, so map continuations can recover it from this file. Also replace `{{ARMING_PERMISSIONS}}` with the preflight result (`tag: agent|owner; variable: agent|owner; workflow run: agent|owner`).

Before committing, run `python3 .github/loop/setup_roles.py prepare` in the target repository. This checks that the copied engine has no execution roles and no selected shaper; the four policy roles ship with it. Copying the engine is not role selection. If preparation fails, stop before committing; obtain the current engine template instead of converting existing installations. See the role-selection procedure in the setup notes.

Commit as `Perpetua engine <ENGINE_VERSION>` and push the branch. Then record the untouched engine for later updates: `python3 PLUGIN_ROOT/skills/update/engine_update.py snapshot PLUGIN_ROOT/engine` prints a commit; push it with `git push origin <commit>:refs/heads/perpetua/engine`. `/perpetua:update` merges future engine versions against it. Stay on the setup branch: the setup map writes its configuration there. The default branch is untouched until arming.

## 4. Hand over to wayfinder

Read `SKILL_DIR/brief.md`, replace `{{GOAL}}` with only the goal, `{{OWNER_CONTEXT}}` with the same owner-context block written to the setup notes, and `{{REPO}}` and `{{BRANCH}}`. Pass the complete rendered brief to wayfinder. In Claude Code use the Skill tool with its resolved name. In Codex, read and follow the resolved wayfinder `SKILL.md` in this conversation, treating the rendered brief as its invocation text. For wayfinder's nested Skill calls, use the same rule: the client's Skill tool when available, otherwise read and follow the resolved skill instructions. Research subagents receive the research skill path, rendered brief and applicable setup notes. Do not summarise or reword the brief.

Wayfinder chooses the map's ticket structure from the required outcomes and session-scope rules in the setup notes, charts it with the owner and stops. Afterwards tell the owner the map's URL and how to continue in their client: `/wayfinder <URL>` in Claude Code; `$wayfinder <URL>` or `Use wayfinder for <URL>` in Codex. Running setup in Codex retains the shipped Claude-based loop provider unless the owner changes it during the map.
