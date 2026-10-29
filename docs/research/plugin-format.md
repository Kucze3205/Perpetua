# Claude Code plugin format for Perpetua

Research for [#2](https://github.com/Kucze3205/Perpetua/issues/2), child of map [#1](https://github.com/Kucze3205/Perpetua/issues/1).
Checked 2026-10-09 against Claude Code docs (fetched as Markdown from `code.claude.com/docs/en/*.md`), `anthropics/claude-code-action` at commit `2dca132`, and the local Claude Code 2.1.292 install.
Every claim carries its source. **[tested]** = verified by running Claude Code locally. **[unverified]** = inference, not confirmed by a source or a run.

## TL;DR

| # | Question | Answer |
| - | - | - |
| 1 | Structure, private install | One repo can be plugin + marketplace (`.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` with `"source": "./"`). Private repo works with the user's existing git credentials only (SSH key or stored HTTPS credential, e.g. `gh auth setup-git`); Claude Code never prompts and has no token field. |
| 2 | Bundled files | Skill body gets `${CLAUDE_PLUGIN_ROOT}` (plugin root) and `${CLAUDE_SKILL_DIR}` substituted with absolute paths; the skill tells Claude to copy from there into the repo. Only files inside the plugin dir are shipped. |
| 3 | Calling another plugin's skill | Possible for model-invocable skills via the Skill tool. **`mattpocock-skills:wayfinder` is `disable-model-invocation: true`, so no skill can invoke it [tested].** Missing skill: `Unknown skill: <name>` [tested]. Plugins can declare `dependencies` (cross-marketplace needs an allowlist). |
| 4 | Listing available skills | Model sees names + descriptions of model-invocable skills only; user-only skills (like wayfinder) are absent. Full list needs a disk scan: `claude plugin list --json` → `installPath`, plus `~/.claude/skills/`, `.claude/skills/`, `~/.claude/skills/synced/`. |
| 5 | Plugins in GitHub Actions | Yes: `claude plugin marketplace add` + `claude plugin install` before `claude -p`, or `--plugin-dir <checkout>`, or claude-code-action's `plugin_marketplaces`/`plugins` inputs. Repo-level `extraKnownMarketplaces` is **ignored** in `-p`. Private marketplace needs a token for that other repo on the runner. Project `.claude/skills/` load with no install at all. |

## 1. Plugin and marketplace structure; private install

**Plugin.** A directory with `.claude-plugin/plugin.json`; `name` is the only required key and namespaces every component ([manifest reference](https://code.claude.com/docs/en/plugins/manifest-reference)). Skills are scanned from `skills/<name>/SKILL.md` by default; a `skills` field (path or array) *adds* directories to that scan ([manifest reference, Fields](https://code.claude.com/docs/en/plugins/manifest-reference)). Plugin skills are invoked as `/plugin-name:skill-name` ([skills, where skills live](https://code.claude.com/docs/en/skills#where-skills-live)).

**Marketplace.** `.claude-plugin/marketplace.json` with `name`, `owner`, `plugins[]`; each entry has `name` and `source`; relative sources must start with `./` ([create a marketplace](https://code.claude.com/docs/en/plugins/create-marketplace), [marketplace reference](https://code.claude.com/docs/en/plugins/marketplace-reference)). To publish from the plugin's own repo, put `marketplace.json` beside `plugin.json` with one entry `"source": "./"` and the same `name` ([publish](https://code.claude.com/docs/en/plugins/publish)). `mattpocock/skills` does exactly this (local copy: `~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/1.2.3/.claude-plugin/{plugin,marketplace}.json`; marketplace `name` is `mattpocock`, entry `source: "./"`).

Proposed Perpetua layout (single repo = marketplace + plugin) [unverified, follows the docs above]:

```
.claude-plugin/plugin.json        { "name": "perpetua", "version": ... }
.claude-plugin/marketplace.json   { "name": "perpetua", "owner": {...}, "plugins": [{ "name": "perpetua", "source": "./" }] }
skills/<skill>/SKILL.md
engine/...                        # files the skill copies into the target repo
```

**Install.** `/plugin marketplace add Kucze3205/Perpetua`, then `/plugin install perpetua@perpetua`; shell equivalents `claude plugin marketplace add` / `claude plugin install`; scopes user / project / local ([install](https://code.claude.com/docs/en/plugins/install)). `claude plugin validate .` checks both manifests when both exist ([CLI reference, plugin validate](https://code.claude.com/docs/en/plugins/cli-reference)).

**Private repo auth** ([host marketplace, grant access](https://code.claude.com/docs/en/plugins/host-marketplace#grant-access-to-a-private-marketplace); [install, add a private marketplace](https://code.claude.com/docs/en/plugins/install#add-a-private-marketplace)):
- Claude Code runs `git` non-interactively with whatever credentials the machine already has; it has no token of its own and `marketplace.json` has no token field.
- `owner/repo` form: probes SSH (`ssh -T git@github.com`), falls back to HTTPS; `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` skips the probe.
- HTTPS: a credential helper that already stores a credential works (`gh auth login` + `gh auth setup-git`); one that would prompt fails.
- SSH: key usable without passphrase prompt, host in `known_hosts`.
- `GITHUB_TOKEN` in the environment alone does not authenticate; it only takes effect through a helper such as gh's.
- Background auto-update of a private marketplace uses the same non-prompting helpers; `CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE=1` keeps the last checkout on auth failure.

## 2. Referencing and copying bundled files

Substitutions available in a skill's Markdown body and in its `allowed-tools` Bash rules ([skills, available string substitutions](https://code.claude.com/docs/en/skills#available-string-substitutions)):

| Variable | Value |
| - | - |
| `${CLAUDE_SKILL_DIR}` | Directory of this `SKILL.md` (for a plugin skill: its subdirectory, not the plugin root) |
| `${CLAUDE_PLUGIN_ROOT}` | Plugin install dir; plugin skills only |
| `${CLAUDE_PLUGIN_DATA}` | `~/.claude/plugins/data/<id>/`, survives updates; plugin skills only |
| `${CLAUDE_PROJECT_DIR}` | Project dir |

- Using the same variable in the body and in `allowed-tools` (e.g. `Bash(${CLAUDE_SKILL_DIR}/scripts/x.sh *)`) lets the skill run a bundled script without a permission prompt ([skills](https://code.claude.com/docs/en/skills#available-string-substitutions)).
- So the copy step is just an instruction in `SKILL.md`, e.g. "copy `${CLAUDE_PLUGIN_ROOT}/engine/` into `.github/loop/`", or a bundled script run with that path.
- `${CLAUDE_PLUGIN_ROOT}` is `cache/<marketplace>/<plugin>/<version>/` and changes on every version; don't write state there ([loading, find plugins on disk](https://code.claude.com/docs/en/plugins/loading#find-plugins-on-disk)).
- Marketplace plugins are **copied** into the cache at install; files outside the plugin directory are not copied, and component paths/symlinks escaping the plugin root are rejected ([loading, in-place and copied plugins](https://code.claude.com/docs/en/plugins/loading#in-place-and-copied-plugins)). With `"source": "./"` the whole repo is the plugin, so everything in it ships (the local mattpocock-skills cache holds the full repo, incl. `docs/`, `.github/`, `node_modules/`).
- When a skill loads, Claude Code also prints `Base directory for this skill: <abs path>` above its content [tested: seen in this session for `mattpocock-skills:research`].
- On Windows the substituted path is a `C:\...` path; whether Git Bash `cp` handles it unquoted is untested. Quote it [unverified].

## 3. Invoking a skill from another plugin; dependencies

**Mechanism.** Claude invokes skills through the Skill tool, one skill per call; any skill without `disable-model-invocation: true` is reachable ([skills, restrict Claude's skill access](https://code.claude.com/docs/en/skills#control-who-invokes-a-skill)). The mattpocock repo's convention: operative cross-skill calls are written as "Call the Skill tool with `<name>`" (`.agents/invocation.md` in the plugin).

**Blocker for Perpetua's design.** `wayfinder` (and `to-spec`, `to-tickets`, `grill-me`, `teach`, `implement`, ...) carry `disable-model-invocation: true` (local `skills/engineering/wayfinder/SKILL.md`). Effects:

| Frontmatter | User can invoke | Claude can invoke | Description in context |
| - | - | - | - |
| default | yes | yes | yes |
| `disable-model-invocation: true` | yes | no | no |
| `user-invocable: false` | no | yes | yes |

([skills, control who invokes](https://code.claude.com/docs/en/skills#control-who-invokes-a-skill)). A Skill tool call to it returns [tested, Claude Code 2.1.292, `claude -p`]:

> Skill mattpocock-skills:wayfinder cannot be used with Skill tool due to disable-model-invocation. Ask the user to run /mattpocock-skills:wayfinder themselves — it cannot be invoked via the Skill tool. Do not replicate this skill's workflow by other means — it is reserved for explicit user invocation.

The docs confirm Claude Code blocks such a call and tells Claude not to reproduce the steps another way ([skills](https://code.claude.com/docs/en/skills#control-who-invokes-a-skill)). Ways around it:

| Option | How | Source |
| - | - | - |
| Stacking | User types `/perpetua:<skill> /mattpocock-skills:wayfinder <goal>`; both skills load, trailing text goes to each as `$ARGUMENTS`. Max 6 stacked; stops at first token that isn't an inline user-invocable skill | [skills, pass arguments](https://code.claude.com/docs/en/skills#pass-arguments-to-skills) (stacking of these two specific skills [unverified]) |
| Hand-off | Perpetua skill does its prep, writes the prepared prompt, then tells the user to run `/mattpocock-skills:wayfinder` with it | the tool's own error text above |
| Vendor a copy | Ship our own copy of wayfinder (MIT licence, `LICENSE` in the plugin) as a model-invocable skill inside Perpetua | licence: local `plugin.json` `"license": "MIT"` |

**Missing skill.** Skill tool call to an unknown name returns `Unknown skill: perpetua-nonexistent:foo` [tested]. Claude sees it as a tool error and continues; the skill should say what to do then (e.g. tell the user how to install).

**Plugin dependencies** ([plugin dependencies](https://code.claude.com/docs/en/plugins/dependencies)):
- `plugin.json` `"dependencies": ["name", "name@marketplace", {"name", "version", "marketplace"}]`; installing the plugin installs them; semver ranges resolve against `<plugin>--v<version>` git tags.
- Cross-marketplace dependency (Perpetua → `mattpocock-skills@claude-plugins-official`) is installed only if the root marketplace lists the target in `allowCrossMarketplaceDependenciesOn`, **or** the user already has it enabled at the same scope. Declared in `plugin.json` without the allowlist: the install completes without the dependency and the plugin then **fails to load**.
- Dependency install also runs on `/reload-plugins`, marketplace auto-update, re-install, `marketplace add`.
- A dependency only guarantees the other plugin is present; it does not lift `disable-model-invocation`.

## 4. Discovering available skills

What the model already sees: a listing of every skill's name plus description, budgeted at 1% of the context window, dropping descriptions of least-used skills first ([skills, descriptions cut short](https://code.claude.com/docs/en/skills#skill-descriptions-are-cut-short)). Skills with `disable-model-invocation: true` have no description in context ([skills](https://code.claude.com/docs/en/skills#control-who-invokes-a-skill)); in this session they were absent from the list entirely [tested by observation]. In `--bare` mode Claude gets no skill list at all ([headless, bare mode](https://code.claude.com/docs/en/headless#start-faster-with-bare-mode)).

So to propose *all* skills as role bases, the skill must scan disk ([skills, where skills live](https://code.claude.com/docs/en/skills#where-skills-live); [loading, find plugins on disk](https://code.claude.com/docs/en/plugins/loading#find-plugins-on-disk)):

| Source | Location | Name |
| - | - | - |
| Plugins | `claude plugin list --json` → each `installPath` (or `readFromFolder`) → `skills/*/SKILL.md` + dirs in the manifest's `skills` field | `/plugin:skill` |
| Personal | `~/.claude/skills/<name>/SKILL.md` (and legacy `~/.claude/commands/*.md`) | `/name` |
| Synced from claude.ai | `~/.claude/skills/synced/` | `anthropic-skills:<name>` |
| Project | `.claude/skills/<name>/SKILL.md` up to repo root (nested ones load lazily) | `/name` |
| Enterprise | `.claude/skills/` in the managed settings dir | `/name` |

`claude plugin list --json` fields: `id` (`name@marketplace`), `version`, `scope`, `enabled`, `installPath` ([CLI reference, plugin list](https://code.claude.com/docs/en/plugins/cli-reference#plugin-list)); `--available` adds uninstalled plugins from known marketplaces. Raw records: `~/.claude/plugins/installed_plugins.json`, `known_marketplaces.json` ([loading](https://code.claude.com/docs/en/plugins/loading#find-plugins-on-disk)). Each `SKILL.md` frontmatter gives `name`, `description`, `disable-model-invocation`.

Caveat for roles: personal (`~/.claude/skills`) and user-enabled plugin skills exist only on the owner's machine; a role that uses them in Actions needs them copied into the repo or the plugin installed on the runner (section 5). Cloud sessions/routines don't read `~/.claude/skills/` ([skills](https://code.claude.com/docs/en/skills#skills-in-cowork-and-cloud-sessions)).

## 5. Plugins and skills in GitHub Actions

| Way | Notes | Source |
| - | - | - |
| Project skills | `.claude/skills/` in the checked-out repo load in `claude -p` with no install (not in `--bare`). v1 already works this way. | [skills](https://code.claude.com/docs/en/skills#where-skills-live), [github-actions, run a skill](https://code.claude.com/docs/en/github-actions#run-a-skill) |
| CLI install step | `claude plugin marketplace add <url-or-path>` then `claude plugin install <name>@<marketplace>` before `claude -p`. Exactly what claude-code-action does internally. | [CLI reference](https://code.claude.com/docs/en/plugins/cli-reference), `base-action/src/install-plugins.ts` |
| `--plugin-dir <path>` | Session-only plugin from a directory/zip (e.g. a checkout); works also in `--bare`. `--plugin-url` for a zip URL. | [CLI reference, flags](https://code.claude.com/docs/en/plugins/cli-reference#flags-that-load-a-plugin-for-one-session), [headless](https://code.claude.com/docs/en/headless#start-faster-with-bare-mode) |
| claude-code-action | `plugin_marketplaces` (newline list of `https://...git` URLs or local paths) + `plugins` (`name@marketplace`, marketplace name from its manifest), prompt `/plugin:skill`. | [github-actions](https://code.claude.com/docs/en/github-actions#run-a-skill), `action.yml`, `install-plugins.ts` |
| Repo `extraKnownMarketplaces` / `enabledPlugins` | **Not used** in `claude -p` (needs workspace trust, which `-p` never grants) unless `hasTrustDialogAccepted` is set in `~/.claude.json`. | [permissions, what runs before you trust a folder](https://code.claude.com/docs/en/permissions#what-runs-before-you-trust-a-folder), [settings](https://code.claude.com/docs/en/settings) |

Details:
- In `-p`, a user-invoked skill works when the prompt starts with `/skill-name` ([headless](https://code.claude.com/docs/en/headless)). So a role can be `claude -p "/mattpocock-skills:wayfinder ..."` even though the model can't invoke it.
- If plugins come via settings, `CLAUDE_CODE_SYNC_PLUGIN_INSTALL=1` makes `-p` wait for the install before the first turn ([env vars](https://code.claude.com/docs/en/env-vars)). The `system/init` stream event lists loaded `plugins` and `plugin_errors`, usable to fail CI ([headless](https://code.claude.com/docs/en/headless)).
- **`--bare` cannot be used with the Claude subscription**: bare mode never reads OAuth credentials, so `CLAUDE_CODE_OAUTH_TOKEN` (used by v1) won't authenticate; it needs `ANTHROPIC_API_KEY` ([headless](https://code.claude.com/docs/en/headless#start-faster-with-bare-mode), [env vars, `CLAUDE_CODE_SIMPLE`](https://code.claude.com/docs/en/env-vars)).
- **Private Perpetua marketplace on a runner in another repo**: claude-code-action only rewrites the repo-local credential of the checked-out repo (`src/github/operations/git-config.ts`), and `GITHUB_TOKEN` is scoped to the workflow's repo, so cloning `Kucze3205/Perpetua` would fail without a token that can read it. Options [unverified, follow from section 1]: `actions/checkout` of Perpetua with a PAT/deploy key into a path, then `--plugin-dir` or a local-path marketplace; or a global `git config url."https://x-access-token:$TOKEN@github.com/".insteadOf`. Simplest: copy the engine and role skills into the loop repo at setup and need no plugin on the runner.
- Public plugins (e.g. mattpocock-skills) install on a runner with no credentials: `https://github.com/mattpocock/skills.git` gives `mattpocock-skills@mattpocock`; `https://github.com/anthropics/claude-plugins-official.git` gives `mattpocock-skills@claude-plugins-official` (marketplace names from local manifests).

## Implications for Perpetua (for the map)

- "Skill = thin layer that **invokes** wayfinder" can't work as a Skill tool call. Pick: stacking (`/perpetua:x /mattpocock-skills:wayfinder`), hand-off to the user, or vendoring wayfinder.
- The engine and role skills are best **copied into the loop repo** (`.github/loop/`, `.claude/skills/`); then the runner needs neither the private Perpetua repo nor plugin installs. Plugins on the runner only for third-party skills a role wants unchanged.
- The skill-proposal step needs a disk scan, not the model's own listing, or user-only skills are missed.
