---
name: update
description: Update the engine of a running Perpetua loop in the current repository to the engine shipped with this plugin, keeping the loop's local changes.
disable-model-invocation: true
---

Update the loop in this repository to this plugin's engine. Resolve the path of this loaded `SKILL.md`, following symlinks/junctions. Call its directory `SKILL_DIR`; `PLUGIN_ROOT` is two directories above it, containing `engine/`. Terms are defined in the loop's `CONTEXT.md`.

The engine was copied into the loop at setup and has changed on both sides since: Perpetua released fixes, and the loop (its owner, its engine-change PRs, its Policymaker) changed its own copy. `SKILL_DIR/engine_update.py` merges the two with git's three-way merge. The base is the untouched engine kept on the loop's `perpetua/engine` branch; loops set up before that branch existed get their base found once in Perpetua's history. You run it, resolve what git cannot, check, and publish. The owner only confirms a moved setup baseline.

Run every command in the loop repository root. Use `python3`, or `python` where that is the interpreter. Quote paths: on Windows they contain `C:\`.

## 1. Preflight (read-only)

Stop at the first failure and say what to fix, in one line each:

- a git work tree with a clean status, on the default branch, equal to `origin` after `git fetch` (`gh repo view --json nameWithOwner,defaultBranchRef`), with `ENGINE_VERSION` in the root
- `gh auth status` is logged in, and `gh api repos/{owner}/{repo} --jq .permissions` shows `push` and `admin`. A cloud session usually lacks them: then do steps 2–6 on a pushed branch and hand the owner steps 7–9 as copyable commands.
- `PLUGIN_ROOT/engine/ENGINE_VERSION` exists. Say both versions: the loop's and the plugin's.

## 2. Pause the loop

Read `gh variable get AUTOPILOT` and remember it. If it is `on`, set it to `off`. Wait until no `session`, `resume`, `owner-request` or `watchdog` run is queued or in progress (`gh run list --status in_progress` and `--status queued`); check every few minutes and tell the owner what you are waiting for. A session that finishes meanwhile merges into the default branch, so pull before the next step.

## 3. Merge

```sh
git switch -c perpetua/update
python3 SKILL_DIR/engine_update.py merge PLUGIN_ROOT/engine
```

It prints a JSON report: `from`/`to` versions, `base`, `snapshot` (the new untouched engine commit), `bootstrapped` (how a missing base was found), `status`, and the changed paths split into `engine`, `workflows` and `definitions`. Nothing is committed or pushed.

- `up-to-date`: nothing to merge. Switch back, delete the branch, restore `AUTOPILOT` (step 8) and report. If the plugin's version equals the loop's, the owner probably has to update the plugin first (`/plugin marketplace update perpetua`, then restart the client).
- `bootstrapped` with `files_differing_from_setup_copy` above 0: the found base is approximate. Say so in the report; the conflicts will be wider.
- `merged` or `conflicts`: continue.

## 4. Resolve conflicts

Every path in `conflicts` holds git conflict markers. For each, read both intents: `git diff <base> <snapshot> -- <path>` is what Perpetua changed, `git diff <base> HEAD -- <path>` is what the loop changed. Write the result that keeps both:

- Engine code and docs (`.github/loop/`, `docs/perpetua/`): take Perpetua's change and keep the loop's own additions. Where the loop had fixed the same problem its own way, prefer Perpetua's fix unless that drops loop behavior the owner depends on; then keep the loop's and say so.
- Definitions (`policy/`, `execution/`, `engine.yml`, `goal/`): the loop's values are decisions of its owner and Policymaker; keep them. Add what Perpetua introduced (new keys, sections, rules, scripts such as `policy/policymaker-trigger.py`), with Perpetua's defaults.
- Workflows (`.github/workflows/`): keep the loop's secrets, environments and runners; take Perpetua's logic.
- `ENGINE_VERSION`: Perpetua's.

Decide routine cases yourself and list them in the report. Ask the owner only when keeping one side visibly removes something the other relies on. No markers may remain: `git grep -nE '^(<<<<<<<|=======|>>>>>>>)( |$)'` is empty. Stage the results.

## 5. Check

- `python3 -m unittest discover -s .github/loop/tests`. The isolation tests need Linux with `bwrap`; on another system run the suite in a container:
  `docker run --rm --privileged -v "$PWD":/src:ro python:3.12-bookworm bash -c "apt-get -qq update; apt-get -qq install -y bubblewrap git socat; pip -q install pyyaml; cp -r /src /w && cd /w && git config --global user.email t@t && git config --global user.name t && python3 -m unittest discover -s .github/loop/tests"`.
  Fix failures the merge caused. Report environment skips separately; a skip is not a pass. If neither Linux nor Docker is available, say which tests did not run.
- `python3 .github/loop/setup_roles.py validate` passes.

## 6. Commit

Commit as `Perpetua engine update <from> → <to>` with the resolved conflicts listed in the body. Push `perpetua/update`.

## 7. Publish

```sh
git switch <default> && git merge --ff-only perpetua/update && git push origin <default>
git push origin <snapshot>:refs/heads/perpetua/engine
git branch -d perpetua/update && git push origin --delete perpetua/update
```

If the default branch moved meanwhile, rebase `perpetua/update` onto it and re-run step 5 before merging. Without push to the default branch, the owner merges `perpetua/update` and pushes the snapshot line.

## 8. Baseline and restart

If the report's `definitions` is not empty, the managed definitions changed and `setup-baseline` must move. Show the owner that list and ask once to move it. On yes: `git tag -f setup-baseline HEAD && git push -f origin setup-baseline`. Then `python3 .github/loop/setup_roles.py validate --baseline setup-baseline` must pass. A change outside the definitions keeps the tag.

Restore `AUTOPILOT` to the value from step 2. If it is `on`, run `gh workflow run watchdog.yml`: the schedule alone can take hours to start.

## 9. Report

Versions from → to; how the base was found; merged paths by group; each conflict and how it was resolved; workflow changes; tests run, failed and skipped; whether the baseline moved; the `AUTOPILOT` state.
