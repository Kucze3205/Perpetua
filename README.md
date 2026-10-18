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

## Where to start

- **Understand the domain:** [CONTEXT.md](CONTEXT.md) defines the shared terms;
  [architecture decisions](docs/adr/) explain the constraints behind the design.
- **Change setup:** [skills/setup/SKILL.md](skills/setup/SKILL.md) is the entry point;
  its linked brief and setup notes guide the setup map.
- **Investigate or change the engine:** [code map](engine/docs/perpetua/code-map.md)
  locates behavior, contracts and tests;
  [engine lifecycle](engine/docs/perpetua/engine.md) explains how they fit together.
- **Change role behavior:** start with `engine/policy/`. Use the code map to
  distinguish editable policy from the engine-owned session contract.

Setup copies the contents of `engine/` to the target repository root. Paths in its
documentation therefore omit the `engine/` prefix. Changes here do not update an
already running loop; see [ADR-0001](docs/adr/0001-engine-copied-into-loop-repo.md).

## Verify a change

From this repository root, run:

```sh
python -m unittest discover -s engine/.github/loop/tests
```

Prerequisites and limits of the checks are in the engine documentation's
[Tests](engine/docs/perpetua/engine.md#tests) and
[Not exercised here](engine/docs/perpetua/engine.md#not-exercised-here) sections.
