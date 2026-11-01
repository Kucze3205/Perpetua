Update the loop in this repository to this plugin's engine (`SKILL_DIR/engine`; `SKILL_DIR` is defined in `SKILL.md`). Terms are defined in the loop's `CONTEXT.md`.

The engine was copied into the loop at setup and has changed on both sides since: Perpetua released fixes, and the loop (its owner, its engine-change PRs, its Policymaker) changed its own copy. `SKILL_DIR/engine_update.py` merges the two with git's three-way merge. The base is the untouched engine kept on the loop's `perpetua/engine` branch; a loop without it cannot be updated. You run it, resolve what git cannot, check, and publish. The owner only confirms a moved setup baseline.

Run every command in the loop repository root. Use `python3`, or `python` where that is the interpreter. Quote paths: on Windows they contain `C:\`.

## 1. Preflight (read-only)

Stop at the first failure and say what to fix, in one line each:

- a git work tree with a clean status, on the default branch, equal to `origin` after `git fetch` (`gh repo view --json nameWithOwner,defaultBranchRef`), with `ENGINE_VERSION` in the root
- `gh auth status` is logged in, and `gh api repos/{owner}/{repo} --jq .permissions` shows `push` and `admin`. A cloud session usually lacks them: then do steps 2–6 on a pushed branch and hand the owner steps 7–9 as copyable commands.
- `SKILL_DIR/engine/ENGINE_VERSION` exists. Say both versions: the loop's and the plugin's.

## 2. Pause the loop

Read `gh variable get AUTOPILOT` and remember it. If it is `on`, set it to `off`. Wait until no `session`, `resume`, `owner-request` or `watchdog` run is queued or in progress (`gh run list --status in_progress` and `--status queued`); check every few minutes and tell the owner what you are waiting for. A session that finishes meanwhile merges into the default branch, so pull before the next step.

## 3. Merge

```sh
git switch -c perpetua/update
python3 SKILL_DIR/engine_update.py merge SKILL_DIR/engine
```

It prints a JSON report: `from`/`to` versions, `base`, `snapshot` (the new untouched engine commit), `status`, and the changed paths split into `engine`, `workflows` and `definitions`. Nothing is committed or pushed.

- `up-to-date`: nothing to merge. Switch back, delete the branch, restore `AUTOPILOT` (step 8) and report. If the plugin's version equals the loop's, the owner probably has to update the plugin first (`claude plugin update kucze@kucze`, then restart the client).
- `merged` or `conflicts`: continue.

## 4. Resolve conflicts

Every path in `conflicts` holds git conflict markers. For each, read both intents: `git diff <base> <snapshot> -- <path>` is what Perpetua changed, `git diff <base> HEAD -- <path>` is what the loop changed. Write the result that keeps both:

- Engine code and docs (`.github/loop/`, `docs/perpetua/`): take Perpetua's change and keep the loop's own additions. Where the loop had fixed the same problem its own way, prefer Perpetua's fix unless that drops loop behavior the owner depends on; then keep the loop's and say so.
- Definitions (`policy/`, `execution/`, `engine.yml`, `goal/`): the loop's values are decisions of its owner and Policymaker; keep them. Add what Perpetua introduced (new keys, sections, rules, scripts such as `policy/prepare.sh` (machine preparation; a loop's own `.github/loop/prepare.sh` content moves there), `policy/policymaker-trigger.py`, `policy/session-state.py`, `policy/session-startup.py` and `policy/layer-rules.py` (the layer and activation rules the engine no longer holds; `policy/graph-rules.py` is optional and only adds task-graph refusals); when `policy/session-state.py` is added, the suggestion handling now in it replaces the engine's: the engine only archives raw outboxes, decisions go in `policy/suggestion-decisions.json`), with Perpetua's defaults. `startup_token_warning`, `policymaker_only_roles` and `params.stream_retention_iterations` move from `engine.yml` to `policy/params.yml` (keep the loop's values there), as do the `limits`, `cadence` (policy may only be stricter than the owner's) and `audit_exports_window` keys.
- Workflows (`.github/workflows/`): keep the loop's secrets, environments and runners; take Perpetua's logic.
- `ENGINE_VERSION`: Perpetua's.

Decide routine cases yourself and list them in the report. Ask the owner only when keeping one side visibly removes something the other relies on. No markers may remain: `git grep -nE '^(<<<<<<<|=======|>>>>>>>)( |$)'` is empty. Stage the results.

## 5. Check

- `python3 -m unittest discover -s .github/loop/tests` (needs `git`, `bash` and `pyyaml`; no container or namespace support).
  Fix failures the merge caused. Report environment skips separately; a skip is not a pass.

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

If the report's `definitions` is not empty, the managed definitions changed and `setup-baseline` must move. Show the owner that list and ask once to move it. On yes: `git tag -f setup-baseline HEAD && git push -f origin setup-baseline`. A change outside the definitions keeps the tag. Either way `python3 .github/loop/setup_roles.py validate --baseline setup-baseline` must pass.

Restore `AUTOPILOT` to the value from step 2. If it is `on`, run `gh workflow run watchdog.yml`: the schedule alone can take hours to start.

## 9. Report

Versions from → to; merged paths by group; each conflict and how it was resolved; workflow changes; tests run, failed and skipped; whether the baseline moved; the `AUTOPILOT` state.
