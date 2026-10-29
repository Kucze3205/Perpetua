# Perpetua

The development and test repository of the `kucze:perpetua-setup` skill, which prepares an
autonomous agent loop in a target repository (setup) and moves a running loop to a newer
engine (`update`). The skill is not installed from here: the built version lives in the
`kucze` plugin ([kucze-skills](../kucze-skills)). A running loop uses the copy installed in
its own repository.

## Release

1. Change `skills/perpetua-setup/` or `engine/`; bump `engine/ENGINE_VERSION` when releasing an engine change.
2. Run the checks below.
3. `python3 build.py ../kucze-skills` replaces `kucze-skills/skills/perpetua-setup/`.
4. In kucze-skills: bump `version` in both `plugin.json` files and update the plugin
   (see its README).

## Use (from the kucze plugin)

```text
/kucze:perpetua-setup <goal and optional owner context>
/kucze:perpetua-setup update
```

In Codex the same skill is `$kucze:perpetua-setup`. It needs the `kucze` plugin and the
`mattpocock-skills` `domain-modeling`, `research` and `prototype` skills; `kucze:wayfinder`
and `kucze:grilling` come with `kucze`. Setup creates the GitHub setup map and continues
through wayfinder.

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
- **Change setup:** [skills/perpetua-setup/SKILL.md](skills/perpetua-setup/SKILL.md) is the entry point;
  its linked brief and setup notes guide the setup map.
- **Investigate or change the engine:** [code map](engine/docs/perpetua/code-map.md)
  locates behavior, contracts and tests;
  [engine lifecycle](engine/docs/perpetua/engine.md) explains how they fit together.
- **Change role behavior:** `engine/execution/` holds implementation definitions,
  `engine/policy/` holds policy definitions, including the four shipped policy
  roles. The setup map writes the execution roles and registers them.
  Use the code map to distinguish definitions from engine-owned contracts.

Setup copies the contents of `engine/` to the target repository root. Paths in its
documentation therefore omit the `engine/` prefix. Changes here reach an already
running loop only through an update; see [ADR-0001](docs/adr/0001-engine-copied-into-loop-repo.md).

## Update a running loop

Update the kucze plugin, restart the client, then run in the loop repository:

```text
/kucze:perpetua-setup update
```

[The update procedure](skills/perpetua-setup/update.md) pauses the loop and merges the plugin's engine into the loop's
copy with git's three-way merge. The loop's `perpetua/engine` branch keeps the untouched engine
as the base, so the loop's own changes survive. The skill resolves conflicts, runs the tests,
publishes the result and restarts the loop. You only confirm a moved `setup-baseline` when managed
definitions changed. A loop without that branch cannot be updated.

## Verify a change

From this repository root, run:

```sh
python -m unittest discover -s engine/.github/loop/tests
python -m unittest discover -s skills/perpetua-setup
```

The [layered validation guide](engine/docs/perpetua/validation.md) maps controlled
end-to-end coverage and required platform/provider evidence. It also describes prerequisites, environment skips and
[deployment checks](engine/docs/perpetua/validation.md#deployment-checks).
