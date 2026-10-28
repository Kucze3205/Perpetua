# Frozen analysis output

`report.json`, `21.json`, `22.json` and `README.md` were produced by the original
`e2e/live.py` from commit `eee61fd` against `test_analysis.dataset` (synthetic
issues, Actions runs, streams, captures and probe; a temporary local Git repo).
The original analyzer read `started.iteration = 7`, `SHIFT = 1`, selecting 8.
Only the report run metadata was fixed to synthetic values for portability.

These are expected outputs, not inputs for the new analyzer. Keep their values
when refactoring; change them only for an intentional calculation/report change.
No GitHub run or credentials were used to produce this fixture.
