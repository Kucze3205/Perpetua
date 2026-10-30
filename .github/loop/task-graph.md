## Historical reports

Policymaker and shaper sessions receive actual trusted marked reports in `.session/reports/`.
Find relevant work through `.session/history/manifest.json`; read selected reports.
Use `reports/index.json` to distinguish current reports from pinned evidence when needed.
Each report JSON has `issue`, `source` (comment URL), `snapshot`
(SHA-256 of `body`) and the full `body`. An untrusted author's marker does not qualify.


## The task graph (shaper and Policymaker only)

The shaper writes `.session/tasks.json`; the engine creates the issues and the blocking edges. A script role
(a deterministic rule) writes the same file. The Policymaker writes its orders in the same format to `.session/orders.json`, with an optional `"wake": true` (run again after the ordered tasks end).

```json
{"tasks": [
  {"id": "a", "title": "...", "role": "researcher", "model": "opus", "effort": "high",
   "body": "## Goal\n...\n## Acceptance\n- [ ] ...\n## Verification\n`python3 check.py`\n## Context\n- link: why go there\n## Budget\n- file-or-issue by name",
   "blocked_by": [], "start_branch": null, "limits": {"timeout_minutes": 60, "max_turns": 80},
   "inputs": [],
   "exports": [{"source": "streams", "iterations": [0], "sessions": [42]}],
   "consent": {"what": "...", "why": "...", "secrets": ["STRIPE_KEY"]}}
]}
```

`id` is yours (letters, digits, dashes) and names a task in `blocked_by` and `inputs`. `role` must have a profile.
`model` and `effort` are optional (omitted: the role's default); `model` is an alias (a key of `models`, never a model id) from `policy/params.yml`, and `limits` never exceed the ceilings there. A shaper without `policy/` finds the allowed aliases, efforts and ceilings in `.session/loop-state.json` under `task_options`, and what each role does in `.session/capabilities.json`. A graph or order naming anything else is refused whole; the Policymaker sees a refused order as `orders_refused` of that iteration in `loop-state.json`. `consent` is optional: the
engine asks the owner by itself when a role's profile is beyond the accepted limits. `consent.secrets` lists the names
(`^[A-Z][A-Z0-9_]*$`, no duplicates, never values) of the repository secrets this one session needs; the owner accepts
or declines the whole list, and a changed list is a new request. The width of the iteration is the
widest layer of the graph; it is computed, not a field. Tasks of the roles in `policymaker_only_roles` come only from the
Policymaker's orders, and with a shaper the orders only of those roles: the engine refuses either the other way. Every task fits **one
session**.

`## Verification` is run by the session itself before it reports `done` and again by the engine afterwards, so it must be
runnable with the role's tools. The default tools allow Bash only as `git …`, `python3 …`, `echo`, `printf` or `mkdir`: write one plain command, with no redirection,
`$(…)`, pipes or `test`. Checking a file's content: `python3 -c "import sys; sys.exit(open('src/a.txt').read() != 'text\n')"`.

To consume an artifact produced by another task in this graph, add
`{"from": "producer-id", "name": "weights.bin"}` to `inputs` and the same
`producer-id` to `blocked_by`. Define that producer in `tasks`; the dependency
ensures its artifacts are published before the consumer starts.

`exports` stages supported data for any role, fetched by the engine before the session
starts. Select `streams`, `actions`, `records` or `reports` and explicit integer
`iterations` or `sessions` (issue numbers); both selectors mean their intersection.
`records` supports iteration scope only. Read `.session/exports/manifest.json` for
each result and staged path: `downloaded`, `unavailable`, `expired`, `error`, or
`unknown`. Unknown sources produce `unknown` and execute nothing. A missing
selector keeps the audit's existing stream window (current iteration and three
predecessors); `exports: []` opts out. Other roles have no implicit exports.
