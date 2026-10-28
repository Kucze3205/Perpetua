# Data of one real loop iteration on GitHub Actions (Haiku 5.5)

Collected by `e2e/live.py collect`. Start with `report.json`, then follow `analysis_file` of a session. Nothing in it is a
verdict: extrapolations (x50) apply only to what accumulates (repository sizes) and to session inputs (reports capped
at 5 iterations, the `iterations` index linear); times and tokens of an iteration are per iteration. The context
threshold is 250000 tokens. Token estimates are characters / 4; `session_inputs` compares them with the
context the CLI really reported on its first model call.

- `report.json`: `run` (repo, commits, times, platform); `sessions` (report fields as the engine published them);
  `iteration_record`; `actions` (queue, start, job and step times, cron delay, concurrency, billable-minute estimate);
  `actions_overhead` (prep, Tools, Stage, Epilogue around the agent per session run; width of the iteration: sessions, report sizes);
  `growth` (`accumulating` x50, `per_iteration_not_scaled`, and what one iteration adds to the next sessions' inputs);
  `session_inputs` (estimated vs measured context, two projection models); `visibility` (did the planted step, the
  iteration's results and the exports reach the next iteration's sessions); `mechanics` (could the Policymaker change
  policy: tools, writes, diff, receipts, orders); `environment_facts` (sandbox mentions, stray files, trusted files incl.
  `unapproved`/`secrets`); `next_iteration_probe`; `contract` (the only pass/fail items); `collect_errors`.
- `sessions/<issue>-<role>/`: `session/` (the whole `.session/` as it was after the session: staged inputs and its
  outputs), `trusted/` (prompt, system contract, settings, tools and the helper files `agent.sh` used), `capture/` (machine,
  environment, files in the checkout), `stream.ndjson` (raw CLI stream), `analysis.json`, exit/time/stderr files.
- `github/`: `repo.json` (repository, variables, secrets names, workflows, branches), `issues/N.json` (issue, comments,
  timeline), `runs/<id>/` (run, jobs with steps, full log), `artifacts.json`, `commits.txt`.
- `repository/`: clone of the test branch (final state of `iterations/`, `journal/`, `policy/`, `execution/`, `src/`, history).
- `artifacts/`: every downloaded Actions artifact as GitHub holds it (`stream-*`, `capture-*`, `probe`).

| issue | role | status | turns | agent s | merged |
|---|---|---|---|---|---|
| 21 | worker | done | 2 | 45 | accepted |
| 22 | policymaker | done | 2 | 45 | accepted |
