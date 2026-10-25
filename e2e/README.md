# E2E: one real loop iteration on GitHub Actions

Runs the real engine (`engine/`) in this repository on real Actions with the real `CLAUDE_CODE_OAUTH_TOKEN`, every role on
Haiku 5.5, one trivial goal, and collects what the loop did for later analysis. `live.py` documents the steps; its
docstring is the procedure.

What it creates in the repo: branch `e2e-loop` and tag `setup-baseline` (throwaway, rebuilt with `build`), variables
`AUTOPILOT` and `E2E_CAPTURE` (removed by `finish`), and issues/labels/artifacts/releases made by the loop itself
(`loop:iteration N`, `role:*`, `stream-*`, `capture-*`, `probe`). The workflows exist only on the default branch, so the
owner switches the default branch to `e2e-loop` for the run and back to `main` after `collect`; nothing in `main` changes.

`probe/` is copied onto the test branch only (`e2e-probe.yml`, `probe.py`); it stages, never runs, the next iteration's sessions.
