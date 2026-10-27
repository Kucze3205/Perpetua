# Perpetua

A Claude Code and Codex plugin that prepares an autonomous agent loop in a target repository.
This repository contains the setup skill and engine template; a running loop uses
the copy installed in its own repository.

## Install and invoke

In Claude Code:

```text
/plugin marketplace add Kucze3205/Perpetua
/plugin install perpetua@perpetua
/perpetua:setup <goal and optional owner context>
```

In Codex, install from a terminal:

```sh
codex plugin marketplace add Kucze3205/Perpetua --ref main
codex plugin add perpetua@perpetua
```

Then start a new conversation in the target repository and invoke
`$perpetua:setup <goal and optional owner context>`, or select Perpetua Setup from
the skill picker. You can also write `Use perpetua:setup to <goal>`.
Both clients run the same setup, create the same GitHub setup map, and continue
through wayfinder. Install the `mattpocock-skills` dependencies (`wayfinder`,
`grilling`, `domain-modeling`, `research`, `prototype`) for the client first.
Codex follows their instructions directly when no Skill tool is available.

The shipped loop still runs Claude sessions on GitHub Actions. Using Codex for
setup does not switch that provider; provider changes are resolved in the setup map.

Setup first clarifies the goal with the owner, including its scope and verifiable
success criteria and evidence; numerical metrics are optional. An initial approach
informs the environment requirements. The map also covers evaluator and gates,
roles, limits and parameters, with a separate arming ticket last. These are required
outcomes, not a fixed issue list: Wayfinder chooses the tickets and dependencies,
expands the map as it learns, and splits work that outgrows one session.

## Develop Perpetua: start with the task

This is the plugin source. Choose the row for the change, then read the named
contract and tests. [CONTEXT.md](CONTEXT.md) is a glossary to consult for unfamiliar
terms; [ADRs](docs/adr/) explain design constraints when relevant. Neither is a
required reading sequence. The [engine lifecycle](engine/docs/perpetua/engine.md)
is an overview when a change spans several parts of the loop.

| Change | Start here | Contract or procedure | Tests (commands below) |
|---|---|---|---|
| Plugin installation or setup handoff | [.claude-plugin/plugin.json](.claude-plugin/plugin.json), [.codex-plugin/plugin.json](.codex-plugin/plugin.json), [skills/setup/SKILL.md](skills/setup/SKILL.md) | [Setup brief](skills/setup/brief.md), [setup notes](skills/setup/setup-notes.md) | `test_static.py` for template consistency; setup handoff is an instruction review, not a simulated client install |
| Copied template, role registration or setup baseline | [setup_roles.py](engine/.github/loop/setup_roles.py): `prepare`, `install`, `validate` | [New-loop setup](engine/docs/perpetua/managed-resources.md#new-loop-setup); setup notes for arming | `test_setup_roles.py`, `test_template_flow.py` |
| Updating an existing loop | [engine_update.py](skills/update/engine_update.py): `merge`, `snapshot`, `bootstrap` | [Update skill](skills/update/SKILL.md) | `skills/update/test_engine_update.py` |
| Session startup, accepting changes, evaluator verification or security | [Engine code map](engine/docs/perpetua/code-map.md#execution-and-tests): choose the behavior | Each row links its contract | Each row names its focused tests |
| Role behavior | `engine/execution/`, `engine/policy/roles/` | [Managed definitions](engine/docs/perpetua/managed-resources.md); setup notes for writing execution roles | `test_setup_roles.py`, `test_layered_resources.py` |
| Live E2E tooling or measurements | [e2e/live.py](e2e/live.py): `build`, `collect`, `measure`, `analyze_session` | [E2E README](e2e/README.md) and the procedure in `live.py`'s docstring | `e2e/test_build_guard.py` checks the build guard locally; it does not cover collection or measurements |

Setup copies the **contents** of `engine/` to the target repository root:
`engine/.github/loop/loop.py` here becomes `.github/loop/loop.py` there. The
[engine code map](engine/docs/perpetua/code-map.md) uses the latter paths and
remains usable in that copy. `skills/`, `e2e/`, plugin manifests and this README
belong to the plugin source and are not copied. A running session starts with its
staged task and instructions, as described in the code map; these development
routes do not add startup reads. Changes here reach an existing loop through an
update; see [ADR-0001](docs/adr/0001-engine-copied-into-loop-repo.md).

## Update a running loop

Update the plugin, restart the client, then run in the loop repository:

```text
/plugin marketplace update perpetua
/perpetua:update
```

[The update skill](skills/update/SKILL.md) pauses the loop and merges the plugin's engine into the loop's
copy with git's three-way merge. The loop's `perpetua/engine` branch keeps the untouched engine
as the base, so the loop's own changes survive. The skill resolves conflicts, runs the tests,
publishes the result and restarts the loop. You only confirm a moved `setup-baseline` when managed
definitions changed. A loop set up before `perpetua/engine` existed gets its base found in
Perpetua's history on the first update.

## Verify a change

Run from this repository root. Python with PyYAML and git on PATH are needed for
the local suites. To check a single engine behavior, substitute a test filename
from the code map; for example:

```sh
python -m unittest discover -s engine/.github/loop/tests -p test_setup_roles.py
python -m unittest discover -s skills/update -p test_engine_update.py
python -m unittest discover -s e2e -p test_build_guard.py
```

For an engine change, run the engine suite; for an update change, run the update
suite:

```sh
python -m unittest discover -s engine/.github/loop/tests
python -m unittest discover -s skills/update
```

Use `python3` if that is the interpreter name. These suites use temporary local
repositories and simulated GitHub/provider outputs. Some checks need a POSIX shell;
Windows skips Linux isolation, symlink and native-CLI cases. Linux dependencies,
kernel settings and the pinned CLI are in
[engine-tests.yml](.github/workflows/engine-tests.yml); the
[validation guide](engine/docs/perpetua/validation.md) explains the separate
opt-in live-provider checks and how to report skips.

The live [E2E procedure](e2e/README.md) needs authenticated `gh`, repository access,
`CLAUDE_CODE_OAUTH_TOKEN` and the Actions runner environment. It creates a real
loop and requires the owner to switch the default branch. Use it when live
workflow or measurement evidence is needed; ordinary local checks do not invoke
it. See also [deployment checks](engine/docs/perpetua/validation.md#deployment-checks).
