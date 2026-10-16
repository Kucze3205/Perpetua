# Perpetua

A Claude Code plugin that prepares an autonomous agent loop in a target repository.
This repository contains the setup skill and engine template; a running loop uses
the copy installed in its own repository.

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
