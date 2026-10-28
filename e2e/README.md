# E2E: one real loop iteration on GitHub Actions

Runs the real engine (`engine/`) in this repository on real Actions with the real
`CLAUDE_CODE_OAUTH_TOKEN`, every role on Haiku 5.5, one trivial goal, and collects
what the loop did for later analysis. Requires Python, PyYAML and authenticated
`gh` with repository access for live commands. Offline analysis needs Python,
PyYAML and Git, without GitHub access.

## Procedure for a live experiment

Run from the plugin repository root, in order:

```sh
python e2e/live.py build
# Owner: set e2e-loop as the default branch so Actions can find its workflows.
python e2e/live.py start
python e2e/live.py wait
python e2e/live.py probe
python e2e/live.py collect OUT
# Owner: restore main as the default branch.
python e2e/live.py finish
```

`build` creates branch `e2e-loop` and tag `setup-baseline` from the current engine;
it refuses a checkout behind `origin/main`. `build --no-push` builds locally.
`start` sets `AUTOPILOT=on` and `E2E_CAPTURE=on` and dispatches the watchdog.
`wait` waits for the iteration record, outage or timeout and sets `AUTOPILOT=off`.
`probe` stages the next iteration's sessions without running them. `collect`
downloads evidence and generates the report. `finish` turns autopilot off and
removes `E2E_CAPTURE`; the owner restores the default branch.

`run OUT` combines start, wait, probe and collect. For multiple iterations, use
`build --iterations N` followed by `run OUT --iterations N`; results go into
`OUT-1`, `OUT-2`, etc. `start --iterations N` also records that count for `wait`.
`collect` alone selects the first iteration from the last `start`.

The experiment also creates issues, labels, artifacts and releases
(`loop:iteration N`, `role:*`, `stream-*`, `capture-*`, `probe`). Rebuilding replaces
the throwaway branch and baseline tag. `probe/` is copied onto that branch only.
Do not run live commands merely to check a calculation or refactor.

## Reanalyze saved evidence

```sh
python e2e/live.py analyze OUT
```

This reads only `OUT`, including its local Git clone, and overwrites derived
`report.json`, `README.md` and session analyses/copies. It does not fetch data or
read `e2e/.state`; copy the entire collection to another checkout or machine to
repeat a calculation. Both `collect` and `analyze` exit 1 when a contract check
fails. Extrapolated values (x50) and scale thresholds are reported, never asserted.

Keep these inputs together:

- `collection.json`: selected iteration (including an offset during `run`),
  original run metadata, start time, collection errors and ordered issue/run IDs.
- `github/issues/*.json`, `github/runs/*/{run,jobs}.json` and legacy run logs:
  issues with comments and Actions runs/jobs used by the report.
- `artifacts/`: downloaded streams, captures and next-iteration probe.
- `repository/`: final test-branch clone **with Git history and setup-baseline tag**;
  analysis reads local diffs and counts commits without contacting a remote.

Older collections without `collection.json` can be reanalyzed when `report.json`
still contains `run` and `iteration_record.iteration`. The record selects the
iteration, which may differ from `run.started.iteration` for multi-iteration runs.
A legacy report without a selected iteration needs that metadata recovered before
analysis; the tool refuses to guess from the current checkout's state.

The generated `OUT/README.md` describes report fields and captured data. Report
keys, calculations and contract meaning are preserved by this refactor.

## Where to change the tool

| Task | Read |
|---|---|
| Add or dispatch a command | `live.py` |
| Build, start, wait, probe, finish; local live-run state | `experiment.py` |
| Fetch GitHub evidence and save collection metadata | `collection.py` |
| Fix statistics, projections, contracts or report rendering | `analysis.py` and the saved inputs above |
| Change the fixed experiment/report assumptions | `scenario.py` |

For example, fixing the token projection used to require locating it among build,
workflow and download code in `live.py` and tracing `.state`/`target()`. Now start
with `analysis.input_projection` and saved streams/probe data; the selected
iteration and start time arrive explicitly from the collection.

Local regression checks (temporary data/repositories, no GitHub or real loop):

```sh
python -m unittest discover -s e2e -v
```
