# State of loop v2 (Dream-RSI tree) and how it compares with v1

Research for [#14](https://github.com/Kucze3205/Perpetua/issues/14), child of map [#1](https://github.com/Kucze3205/Perpetua/issues/1); input to the architecture ticket [#15](https://github.com/Kucze3205/Perpetua/issues/15).
Snapshot 2026-10-09 ~16:00 UTC. Read-only sources:

- **v2** = [`Kucze3205/blockblast-Self-Improvement-loop`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop), `main` at [`83866f4`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/tree/83866f4) (links below are pinned to it, prefix `V2/`), its map [v2#1](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/1), issues, and Actions history (177 runs, `gh run list`).
- **v1** = [`Kucze3205/ReinforcmentBlockBlast`](https://github.com/Kucze3205/ReinforcmentBlockBlast) (prefix `V1/`, `main`), its map [v1#1](https://github.com/Kucze3205/ReinforcmentBlockBlast/issues/1), journal `docs/journal/cykl-*.md`, cost cache `site/data/sesje.json`.

`V2/x` means `https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/x`; `V1/x` means `https://github.com/Kucze3205/ReinforcmentBlockBlast/blob/main/x`.
Costs are the `total_cost_usd` that `claude -p` reports. Both loops run on a Claude subscription, so these are **API-equivalent figures, not money spent**. They are still a fair way to compare the two loops.

## TL;DR

| Question | Answer |
|---|---|
| What v2 is | A tree of attempts. A **node** is one Sonnet session that edits code on a chain branch. An **external evaluator** scores each node (simulator, then the real game on an emulator). A **deterministic policy** (`policy.py`, W=4, K=7) picks the next batch of nodes. An **offline** job replays recorded trees, gets M=3 meta sessions to rewrite the policy, and deploys the argmax. Evaluator upkeep (phase 0, maintenance, self-repair) is a separate pipeline outside the tree. No LLM orchestrator. |
| Live state | AUTOPILOT=on. Phase 0 (evaluator calibration) took **4.5 days** (10-04 → 10-09). Tree 1 started 10-09 11:08 UTC. After about 5 h: 6 rounds, 23 nodes, best `s_sym` 0.885 (node 3.3). **No node has 0 simulator losses yet, so no emulator series has run.** Offline/meta has **never run**. This is still dry-run cycle 1 of [v2#34](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/34). |
| Cost so far | Tree 1: 23 sessions, 386 turns, **$5.34**. Phase-0 maintenance and self-repair agents: 46 sessions, 1 601 turns, **≈$30**. v1 to goal: 278 agent sessions, **≈$476**, 8.1 days, 60 cycles. |
| Failures | Evaluator calibration ate the schedule: 11 failed-repair issues (#37–#47), ~15 owner hand-fixes, and a self-repair agent added mid-run. Tree 1 had to be reset once (missing Edit/Write permission). Node sessions run for 1–11 min, while one evaluation takes up to ~59 min. |
| Block-Blast-specific | The whole evaluator (≈1 780 of ≈3 300 machinery lines): simulator scoring, emulator bridge, calibration, phase 0, maintenance, self-repair, the `policies.build` contract, and the two-tier `s_v`. |
| General | Tree scheduler, policy contract + replay, offline argmax, node workflow (park/continue on subscription limit, record, history), AUTOPILOT, sandbox, credential probe (≈1 530 lines). |
| **Does W=1 reproduce v1?** | **No.** At W=1, v2 becomes a single chain of at most K=7 one-session attempts. Every attempt waits for a mandatory ~1 h evaluation. Every tree restarts **from the original root code**, keeping only notes. Nothing plans, nothing decomposes, nothing runs in parallel, and nothing owns the evaluator. The policy still has to decide: stop, continue, or restart from root. Details in §6. |

## 1. What v2 built

The design comes from the v2 map's closed tickets ([v2#1 "Decisions so far"](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/1)). The operative contract is [`V2/docs/loop-config.md`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/docs/loop-config.md).

### 1.1 Tree, chain, node

- **Tree** `t`: state in `trees/<t>.json` (`polityka` hash pinned for the whole tree, `paczki` = batches per round, `koniec`) ([`V2/trees/1.json`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/trees/1.json)). Only the root may branch. A **chain** is a root-to-leaf path and one git branch `trees/<t>/<chain>` ([v2#14](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/14)).
- **Every chain starts from tag `korzen`.** This is a parentless commit that holds the project code as it was at v1's start (`ad729d5`, 2026-09-24), without `.claude/`, `.github/`, or `bench/` ([v2#28](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/28); "drzewa zawsze od ad729d5" in [v2#5](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/5)). **Code never carries over between trees.** A later tree sees earlier trees only as records and notes, without code (`historia()` in [`V2/.github/loop/loop.py`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/loop.py#L258-L291)).
- **Node** `c.d`: one session of the "discovery agent" in [`V2/.github/workflows/wezel.yml`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/workflows/wezel.yml) via [`V2/.github/loop/agent.sh`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/agent.sh).
  - Runs `claude -p` with `claude-sonnet-5-5`, `--effort medium`, `--max-turns 150`, a 300 min ceiling, and a hook that appends "X min left" after each tool call.
  - Tools: Read/Edit/Write/Bash(git, python, pytest, pip), WebSearch/WebFetch, and a `researcher` subagent (Sonnet, read-only, answers but saves nothing; [`V2/.claude/agents/researcher.md`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.claude/agents/researcher.md)).
  - Prompt: load skill `implementer` and "improve the agent's score in the game" ([`V2/.claude/skills/implementer/SKILL.md`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.claude/skills/implementer/SKILL.md)). The skill **forbids the agent from measuring its own score**. Its notes are commit messages.
- **What the node sees:** a checkout with one ref (its chain branch), no remote, no tokens (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`), plus `.historia/`. That directory holds an index, records of evaluated nodes, **patches of other chains' leaves**, and earlier trees' records without code ([`wezel.yml`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/workflows/wezel.yml), [`loop.py historia`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/loop.py#L258-L291)).
- **After the session**, `loop.py koniec` picks the next step ([loop-config "Po sesji"](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/docs/loop-config.md)):

  | Outcome | Next step |
  |---|---|
  | Subscription limit hit | `park`: sleep until the reset, then continue |
  | Turn budget used up | `kontynuuj`: one continuation |
  | SHA unchanged | `dziedzicz`: inherit the parent's score |
  | Anything else | `ocena`: dispatch the evaluator |

  The node record `trees/<t>/<node>.json` holds cost (turns, usd, minutes), waiting time, sessions, notes, and scores per evaluator hash. There is **no "failed" status**: every node gets evaluated.

### 1.2 Evaluator

[`V2/.github/workflows/ocena.yml`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/workflows/ocena.yml), [`V2/.github/evaluator/ocena.py`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/evaluator/ocena.py), [`config.json`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/evaluator/config.json); decisions in [v2#13](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/13) and [v2#27](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/27).

- **Simulator stage:** 10 shards × 30 deals (300 deals, cap 2 000 moves). The deals are seeded from secret `DEALS_SALT` and hidden from the agent. Files the agent must not change are overlaid read-only from `main`.
- **Emulator stage:** only when there are 0 losses in the simulator. It runs a series of 10 games on an Android emulator through the OCR bridge.
- **Score:** `s_v = s_sym` (survival/cap) or `1 + s_emu` once the series is complete. The goal gate is a script: 10/10 games reach 1 M, which writes `trees/cel.json` and stops the loop.
- **Evaluator hash:** split into `sym` and `most` (bridge). A change to the simulator re-scores `s_sym` lazily.

### 1.3 Policy and live scheduler

[`V2/.github/loop/drzewo.py`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/drzewo.py), [`V2/.github/workflows/drzewo.yml`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/workflows/drzewo.yml), [`V2/.github/policy/policy.py`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/policy/policy.py), [`config.json`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/policy/config.json) (W=4, K=7, β=0.01/h, M=3); decisions in [v2#16](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/16), [v2#19](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/19), [v2#20](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/20), [v2#29](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/29), [v2#32](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/32).

- `drzewo.py krok` is event-driven and idempotent. The end of each node's evaluation triggers it. It waits until the whole last batch is evaluated, then asks `solve(question)` for the next batch of at most W actions. Action `None` opens a new chain from root; a leaf name continues that chain. An empty batch, or K rounds, or `cel.json` closes the tree.
- `question` exposes W, K, the round, the baseline, `observed()` (node, chain, depth, `s_v`, delta to parent), and `legal_actions()`.
- **Starting policy π⁰** (27 lines): round 1 opens W chains from root. After that it **only continues** existing chains, dropping a chain after 2 consecutive non-improvements. It never opens a new chain after round 1.
- Value: `V = max s_v − β·T`. `T` is wall time: each batch costs its longest node (session + simulator + series), summed over batches.

### 1.4 Offline / meta layer

[`V2/.github/loop/offline.py`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/offline.py), [`offline.sh`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/offline.sh), [`V2/.github/policy/meta.md`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/policy/meta.md); decision in [v2#30](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/30).

- When a tree closes, M=3 fresh Sonnet sessions (effort high, 80 turns, no Bash, no internet, no subagents) each rewrite `policy.py`.
- They see: the current policy, earlier versions with V and replay traces, tree manifests (structure, `s_v`, cost; no code), and the config.
- A runner then replays every version on all closed trees, using the same `solve` and the same `Pytanie`. A node outside the recording ends its chain with no penalty. Each version is replayed twice with a 600 s cap, so nondeterministic versions are rejected.
- The runner takes the argmax of mean V, with ties going to the current version, and commits the winner to `main`.
- **Replay can only prune what was recorded** ([v2#19](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/19)). That is why π⁰ deliberately over-explores.

### 1.5 Evaluator upkeep (outside the tree)

- **Phase 0** ([`V2/.github/workflows/faza0.yml`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/workflows/faza0.yml), [`V2/.github/evaluator/kalibracja.py`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/evaluator/kalibracja.py)): plays the baseline agent on the emulator and calibrates the simulator to the bridge logs. Tolerances: score tempo within ±15%, ≤2% unknown pieces. The first tree waits for `trees/faza0.json`.
- **Maintenance** ([`V2/.github/workflows/utrzymanie.yml`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/workflows/utrzymanie.yml), [`utrzymanie.sh`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/utrzymanie.sh), skill [`utrzymanie`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.claude/skills/utrzymanie/SKILL.md)):
  - Triggered mechanically by divergence, an interrupted bridge, or phase 0.
  - Runs up to 3 iterations of "Sonnet session → script gate", where the gate is the control set plus 150 moves on the emulator.
  - A patch that passes the gate is fast-forwarded to `main`. If none passes, it opens an issue and pauses emulator series ([v2#15](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/15), [v2#31](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/31)).
- **Self-repair** ([`V2/.github/workflows/naprawa-awarii.yml`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/workflows/naprawa-awarii.yml)):
  - The owner added it mid-run (commit [`db451ee`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/commit/db451ee), 10-07). It was not in the map.
  - A "Ewaluator: …" issue starts a Sonnet session (effort high, 200 turns) with `gh`, `git push` to `main`, and write access to everything except `.github/workflows/`. It fixes the cause, re-runs phase 0, and closes the issue. It stops itself after 5 such issues in 24 h.
  - **In practice this is an orchestrator-like repair agent, reintroduced because the mechanical pipeline could not cope.**

### 1.6 Isolation

- Allowlist instead of blocklist: every LLM session sees only what the machinery places on its disk ([v2#9](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/9)).
- Firewall, proxy and probe were dropped: internet is open for all sessions; what remains is "no tokens + one ref" ([v2#22](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/22)).
- Claude Code's bubblewrap sandbox is required by `SUBPROCESS_ENV_SCRUB` ([`V2/.github/loop/sandbox.sh`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/sandbox.sh)).
- "Zero knowledge of the experiment / of v1" is best effort only ([v2#1 Notes](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/1)). This requirement is specific to the v1-vs-v2 experiment and is irrelevant to Perpetua.

## 2. What actually happened in the live run

Timeline from `git log` of v2 `main` and `gh run list`:

| When (UTC) | Event | Evidence |
|---|---|---|
| 10-02 → 10-04 | Map with 36 tickets: research, grilling, implementation | [v2 issues #2–#36](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues?q=is%3Aissue) |
| 10-04 20:08–22:24 (+02) | History squashed; machinery implemented in 6 commits; old orchestrator removed (791 → ~190 lines of `loop.py` at the time) | [v2#26](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/26), commits `15dbc97`…`275d8be` |
| 10-04 20:45 → 10-09 09:10 | **Phase 0**: 25 `faza0` runs, 23 `utrzymanie` runs, 5 `naprawa-awarii` runs; 11 "naprawa nieudana" issues; ~10 automatically accepted repairs; ~15 owner commits (sandbox/bwrap/AppArmor, rate-limit handling, bridge `pm clear`, OCR counter, χ² gate dropped) | [#37](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/37)…[#47](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/47); owner commits `6f80a2d`, `e34c5cc`, `54f130e`, `43f81ea`, `f2e4700`, `8c7a99f`, `eb35d3c`, `db451ee`, `e225d11`, `7d5b2e1`; calibration passed at [`b875263`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/commit/b875263) |
| 10-09 09:14 | Tree 1 round 1: all 4 sessions could not write (no Edit/Write under SCRUB), 4 evaluations failed (output path) | runs 37910160847, 37910183836, 37910213968, 37910224504 (failure); fix [`f98ab94`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/commit/f98ab94) + reset [`96a40a7`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/commit/96a40a7) |
| 10-09 11:08 → 15:52 | Tree 1 restarted: 6 rounds dispatched, 23 nodes (20 evaluated, 2 evaluating, 1 in session). Chain 3 dropped after 2 misses (3.4, 3.5) | [`V2/trees/1.json`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/trees/1.json), [`V2/trees/1/`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/tree/83866f4/trees/1) |

**Tree 1 numbers** (from the node records):

| Round | Nodes | Session min (each) | Simulator eval min (each) | Best `s_sym` so far |
|---|---|---|---|---|
| 1 | 1.1, 2.1, 3.1, 4.1 | 2.0–4.4 | 1.3–48.9 | 0.562 |
| 2 | x.2 | 2.0–3.1 | 3.3–23.6 | 0.725 |
| 3 | x.3 | 1.3–2.1 | 42.8–57.4 | **0.885** (3.3) |
| 4 | x.4 | 2.1–4.3 | 56.8–58.8 | 0.885 |
| 5 | x.5 | 2.4–10.6 | 57.4–59.1 | 0.885 |
| 6 | 1.6, 2.6, 4.6 | 1.5–7.9 | pending | — |

**What works**

- **The event-driven chain is mechanically sound after the reset.** Six rounds ran with no human involved: dispatch, then session, then record, then evaluation, then the next step.
- **Parking and continuation exist but have not been exercised.** No node has hit the subscription limit or the turn budget yet (`oczekiwanie_min` = 0 and `kontynuacje_tur` = 0 in every record).
- **Phase-0 calibration closed.** The simulator now matches the bridge within tolerance ([`b875263`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/commit/b875263)).

**What has not run yet**

- No node series on the emulator.
- No goal gate.
- No offline/meta phase or policy deployment.
- No replay-vs-live check.
- The six dry-run criteria of [v2#33](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/33) are not reported in [v2#34](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/34) yet.

**Observed problems**

1. **The evaluator dominates wall time.**
   - Sessions take 1.3–10.6 min (12–25 turns). Good nodes take ~57–59 min in the simulator, close to `limit_shardu_s` = 3 300 s.
   - Of 879 node-minutes in tree 1, 77 are agent and 802 are simulator.
   - The owner opened [v2#48](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/48): one Python process per 4-vCPU shard, so 3 of 4 cores sit idle. 40 jobs run at once per batch.
2. **Sessions are tiny compared to their budget.** They use 2–10 min of a 300 min ceiling, because the skill forbids self-measurement. The agent writes a heuristic, runs tests, commits, and stops. Feedback comes only through the tree, about 1 h per round.
3. **The chains converge.** Each node sees other chains' patches. From round 3 onward almost every gist builds on node 3.3: "Kod 3.3", "Wiązka 3.3", "3.3 + ryzyko…", "Powrót do kodu 3.3", "Lookahead 3.3" ([records](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/tree/83866f4/trees/1)). W=4 chains become 4 tweaks of one program. The width buys parallel hill-climbing, not diversity.
4. **The evaluator is fragile and costly to keep alive.**
   - Phase 0 needed 4.5 days and repeatedly rediscovered the same domain facts v1 had already found: the combo ladder, OCR counter jumps, app state surviving reinstall.
   - The cause is that v2 starts from v1's *initial* code ([v2#47 comment](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/47), [v1#1 "Korekty"](https://github.com/Kucze3205/ReinforcmentBlockBlast/issues/1)).
   - The mechanical repair gate also rejected a good patch because of the agent's scratch files ([v2#47](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/47)).
5. **The "no LLM above the machinery" principle did not hold.** The owner made ~15 manual fixes and added a self-repair agent with push to `main` (§1.5).

## 3. Results and cost compared with v1

| | v1 | v2 (so far) |
|---|---|---|
| Start code | `ad729d5` (2026-09-24) | the same code as tag `korzen` |
| Wall time | first session 09-24 18:25 → simulator condition met at cycle 34 (09-30) → **goal at cycle 60, 10-02 ~21:03** ≈ 8.1 days ([cykl-0060](https://github.com/Kucze3205/ReinforcmentBlockBlast/blob/main/docs/journal/cykl-0060.md), [RAPORT.md](https://github.com/Kucze3205/ReinforcmentBlockBlast/blob/main/RAPORT.md)) | phase 0: 4.5 days; tree search: ~5 h; best `s_sym` 0.885 (cap 2 000; some deals still lost); goal not in sight yet |
| Agent sessions | 278 with an agent (333 `session` runs incl. 51 bench) | 23 node + 46 maintenance/self-repair sessions (+ 0 meta) |
| API-equivalent cost | **≈ $476**. By role × model: implementer-Sonnet $182, orchestrator-Opus $163, implementer-Opus $82, verifier $37, researcher $12 ([`V1/site/data/sesje.json`](https://github.com/Kucze3205/ReinforcmentBlockBlast/blob/main/site/data/sesje.json), produced by `tools/zbierz_dane_strony.py`) | tree 1 **$5.34** (sum of `koszt.usd` in records); maintenance + self-repair **≈ $30.05** (summed from `tury=… koszt=$…` lines in the logs of 28 `utrzymanie`/`naprawa-awarii` runs) |
| Turns per worker session | implementer ≈ 41 (Sonnet: 6 517 turns / 159 sessions) | node ≈ 17 (386 / 23) |
| Final bot | beam search 128 + n-tuple network trained for survival, 0/600 losses at cap 64 000 (cycle 34), then 26 cycles of bridge fixes up to the 10/10 series | heuristic beam + next-tray lookahead (no training so far) |

These numbers cannot fairly decide "v2 better or worse" yet. v2 has not finished one tree. Its goal gate is the same as v1's (10/10 real games to 1 M), but its simulator bar is lower (cap 2 000 instead of 64 000).

What *can* be said:

- Tree search is cheap in tokens. The expensive parts in v2 are evaluator compute and evaluator upkeep. v1's biggest token cost was the Opus orchestrator plus long implementer sessions.
- Neither loop avoided paying a large one-time cost to make the real-world evaluator trustworthy. In v1 that cost was cycles 35–60, about 2 days ([RAPORT.md](https://github.com/Kucze3205/ReinforcmentBlockBlast/blob/main/RAPORT.md)). In v2 it was phase 0, about 4.5 days.

## 4. Block-Blast-specific vs general in v2

Line counts are from `wc -l` on v2 `main`.

| General (engine-like, ≈1 530 lines) | Block-Blast-specific (≈1 780 lines) |
|---|---|
| `drzewo.py` (scheduler, `Pytanie`, batch, naming, T, V), `drzewo.yml` | `ocena.py`, `ocena.yml` (300 deals, cap, shards, `DEALS_SALT`, two-tier `s_v`) |
| `policy.py` contract + `config.json` (W, K, β, M) | `seria.yml`, `bridge.yml`, `emulator-probe.yml`, bridge (OCR, ADB, APK) |
| `offline.py`/`offline.sh`/`meta.md` (replay, M meta sessions, argmax) — `meta.md` wording mentions "the agent in the game" once | `kalibracja.py`, `faza0.yml` (score tempo, piece distribution) |
| `loop.py` (credential probe, limit parsing → park, record, `.historia`), `wezel.yml` (minus `pip install torch`/numpy) | `utrzymanie.yml`/`.sh` + skill `utrzymanie`, `naprawa-awarii.yml` (prompt lists bridge-specific known causes) |
| `agent.sh`, `zostalo.py` (time-left hook), `stream_filter.py`, `sandbox.sh`, `zapisz.sh`, AUTOPILOT, permissions | `policies.build(weights)` contract, `tylko_do_odczytu` file list, `weights/` channel ([v2#21](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/21)) |
| skill `implementer` structure (read history, don't self-evaluate, notes in commits) — wording is game-specific | `CONTEXT.md` (Goal, Loss), tag `korzen` content |

Assumptions in the "general" half that do not hold for Perpetua's goal class (fuzzy or subjective goals, non-code artifacts):

- **A numeric, cheap, machine-computed `s_v` for every node.** V, replay and argmax all depend on it. For a subjective goal the scorer is an LLM judge (noisy, possibly drifting) plus an owner gate. v2 has no notion of an owner-accepted gate ([Perpetua #1 Notes](https://github.com/Kucze3205/Perpetua/issues/1)).
- **The artifact is a git branch** that the evaluator can check out and run (`policies.build`). Non-code artifacts are not addressed.
- **Each tree restarts from a fixed root.** This suits "find the best program from scratch". It does not suit "keep building one deliverable".
- **Offline meta-learning needs many closed trees** to give V any signal. v2 has none after 5 days. This is likely too slow for one-off goals.

## 5. Does W = 1 in v2 reproduce v1?

**No.** With W=1, π⁰ opens one chain from `korzen` and continues it until K=7 rounds or 2 non-improving nodes. Then the batch is empty, the tree closes, offline runs M=3 meta sessions, and a **new tree restarts from `korzen`** ([`policy.py`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/policy/policy.py), [`drzewo.py krok`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/drzewo.py)). Compared with v1:

| Aspect | v1 cycle | v2 node at W=1 |
|---|---|---|
| Unit of work | **Cycle:** an Opus orchestrator (effort high) reads its journal state and the reports, decides the direction, writes a **task map of several role-labelled issues with blocking edges**. These run as **parallel sessions** (~4.6 agent sessions per cycle: 278/60), and a join issue wakes the next cycle ([`V1/.claude/skills/orchestrator/SKILL.md`](https://github.com/Kucze3205/ReinforcmentBlockBlast/blob/main/.claude/skills/orchestrator/SKILL.md)) | **One** Sonnet session (effort medium) with an optional read-only researcher subagent; no planning layer |
| Who chooses *what* to try | Orchestrator LLM (direction, decomposition, which role, when to benchmark or run a series, when to change course; a "prognosis" sentence every cycle) | The node agent itself, from `.historia/`; the policy only says continue / stop / restart |
| Roles | implementer, researcher (writes `docs/research/`), verifier (only role with the emulator), bench, orchestrator ([`V1/.claude/skills/`](https://github.com/Kucze3205/ReinforcmentBlockBlast/tree/main/.claude/skills)) | one generic discovery agent; maintenance agent and self-repair agent live outside the tree |
| State accumulation | Work merges to `main`; code accumulates for the whole run | Code accumulates **only within one chain of ≤7 nodes**; every tree restarts from root code; later trees inherit notes and records, not code |
| Evaluation | On demand: the orchestrator orders `rola:bench` only when weights were produced, and a series when it judges the candidate ready. Workers may measure themselves | Mandatory after **every** node (~1 h for a good node); emulator series automatic at 0 simulator losses; the agent is **forbidden** to self-measure |
| Evaluator maintenance | Part of the main loop: the orchestrator issues bridge-fix tasks (cycles 35–60) | Separate mechanical pipeline (phase 0 → maintenance → self-repair agent) |
| Memory | Journal `docs/journal/cykl-NNNN.md` with a compressed `## Stan` (≤150 lines) + `RAPORT.md` for the owner | JSON records + commit notes + generated `INDEKS.md`; no narrative state, no owner report besides job summaries |
| Goal decision | Orchestrator checks two conditions and writes `GOAL_REACHED` + pinned issue | Script gate writes `trees/cel.json` |
| Parallelism | Yes, inside a cycle (bounded by 20 concurrent jobs) | None at W=1, except inside the evaluator (10 shards, 10 emulator games) |
| Session length | implementer median 43 turns / 23 min ([v2#11](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/11)) | 12–25 turns / 1–11 min observed |

**Policy decisions that remain at W=1** (so W=1 is *not* policy-free):

1. **Continue the chain or restart from root.** `None` is always a legal action ([`legal_actions`](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/blob/83866f4/.github/loop/drzewo.py)).
2. **Stop rule:** the number of misses, and K. This is the only real knob of π⁰ at W=1.
3. **When to end a tree**, which triggers offline and a reset to root.
4. **The offline layer still runs after every tree** (M=3 meta sessions, up to 80 min each). At W=1 it can only learn to stop earlier, because replay prunes and cannot extend.
5. **β·T** stays in V. At W=1, T is simply the sum of node times, so β trades depth against time.

**Takeaway.** W=1 v2 is a *sequential hill-climber with an external judge and periodic restart*. It is not v1's "planner + parallel specialised workers + accumulating mainline". To get close to v1 with a tree, a node would need three changes:

- a node becomes a whole cycle (planner + parallel role sessions), or a parallel batch of chains starts from one shared mainline instead of `korzen`;
- trees do not reset code;
- evaluation is on demand, not mandatory.

At that point the "policy" collapses into decisions that v1's orchestrator already makes.

## 6. Points for the architecture decision (#15)

These are facts from v1 and v2. The decision itself belongs to #15.

- **What v2 adds that is worth keeping, regardless of architecture:**
  - an evaluator outside the worker's reach, with read-only overlay, hidden seeds, and a score versioned by evaluator hash;
  - a per-attempt record with measured cost and wall time;
  - park/continue on the subscription limit, driven by the machinery;
  - the time-left hook;
  - one-ref, no-token checkout;
  - a fail-safe AUTOPILOT.

  All of these are general and cheap.
- **What v2 shows is costly:**
  - The tree, replay, offline and argmax layer has produced **no result after 5 days**. It needs many closed trees before V carries any signal.
  - Restart-from-root suits program search, not building one deliverable.
  - Mandatory evaluation per node makes the evaluator the wall-time bottleneck (~91% of node time in tree 1).
- **The orchestrator came back anyway.** v2 removed the LLM orchestrator, then had to add a full-access self-repair agent and ~15 owner fixes to keep the evaluator alive. Planning, decomposition and evaluator upkeep are work some LLM must do. For fuzzy or subjective goals (an LLM judge plus an owner gate) there is even less a fixed script can do.
- **Width without diversity is wasted.** With shared history, v2's W=4 chains converged on one program by round 3. If Perpetua uses parallel attempts, diversity has to be enforced deliberately (different briefs or hidden siblings), or the slots should go to *different tasks*, as v1 did.
- **Cost profile:** v2's node sessions are ~30× cheaper than a v1 cycle ($0.23 vs ~$8 per v1 cycle, $476/60). Token cost is not the constraint in either loop. Wall time of evaluation and of evaluator upkeep is. This fits Perpetua's default criterion, shortest wall time.
- **v2 is still running.** These conclusions hold for the snapshot only. The first emulator series, the first offline phase, and replay = live are all still untested. Re-check [v2#34](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/34) and [v2#48](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/issues/48) before #15 closes, if timing allows.
