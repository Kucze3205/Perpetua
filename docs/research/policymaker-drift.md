# Does the Policymaker drift away from the goal?

Research for [#176](https://github.com/Kucze3205/Perpetua/issues/176). Question: before the policy phase gets
several passes per iteration with no hard pass limit, can we trust the Policymaker to work toward the goal? Two
suspected risks: (1) the "start by improving the environment" focus crowds out the goal; (2) orders bring no value
and the Policymaker's own reports are lenient.

## Data

Nine real-Actions runs of `e2e/live.py` (map [#65](https://github.com/Kucze3205/Perpetua/issues/65)), 2026-10-25 to
2026-10-27. All roles ran on Haiku 5.5, and the goal was always the same: two one-line files under `src/`. Sources are
the `collect` output of each run (local `perpetua-e2e-run{1..8-2}` directories: streams, staged `.session/`, iteration
records) and the session issues they link to.

- Runs 3 to 8 used the same Policymaker skill, identical to today's
  [engine/policy/roles/policymaker/SKILL.md](../../engine/policy/roles/policymaker/SKILL.md). That gives 7 comparable
  sessions: #99, #116, #127, #137, #147, #155, #160. Runs 1 and 2 used an older skill and appear only as context.
- Each run rebuilds the loop. Every Policymaker except #160 started with `iterations: []`, no audit report and
  `no_progress: 0`. Run 8 is the only one with two consecutive iterations (7 and 8).
- No verdict was ever `inconclusive`, and no run had a `no_progress` streak.

**Limit of the data:** with one goal, one model and at most two consecutive iterations, the data can show what the
Policymaker does at the start of a loop. It cannot show drift over many iterations, and it cannot show how the
Policymaker reacts to stalled progress. No Policymaker ever read an audit result.

## Risk 1: drift toward the environment. Not observed.

Tokens in runs 3 to 8 (input + output + cache, from iteration records and Policymaker streams):

| Work | Sessions | Tokens | Share |
|---|---|---|---|
| Goal (orchestrator, workers) | 19 | 2 268 k | 65 % |
| Policymaker | 7 | 778 k | 22 % |
| Environment (audit) | 3 | 434 k | 12 % |

Each loop ordered at most one audit, and it ran in parallel with the goal tasks of the same iteration, without
delaying them. The goal was accepted in runs 5, 7 and 8-1, both with and without an audit. Blocked workers in runs 3, 4
and 6 came from the engine sandbox (`bwrap: Can't mount proc`), not from Policymaker decisions.

The opposite problem showed up. Applying the environment focus is a coin flip. With identical inputs, three sessions
ordered the first audit (#116, #127, #137), three did not (#99, #147, #155), and #160 skipped it on purpose ("not yet
worth its cost" for a two-file goal). That last call is the one the skill asks for: expected benefit against cost.

## Risk 2: orders without value and lenient self-assessment. Real.

1. **Orders are justified by obedience, not by gain.** #116: "The startup rules say to order the first audit". #127:
   "the policy rules call for an initial environment audit". #137: "as the policy skill directs". Expected effects
   describe the next step of the process ("the audit report will trigger the next Policymaker call"), never what it
   gains for the goal.
2. **Ordered audits could not deliver.** All three audits reported almost nothing measurable: no streams, no records,
   startup budget `unmeasured` ([#117](https://github.com/Kucze3205/Perpetua/issues/117),
   [#128](https://github.com/Kucze3205/Perpetua/issues/128),
   [#138](https://github.com/Kucze3205/Perpetua/issues/138)). In a fresh loop there is nothing to audit yet, and the
   orders gave the audits no inputs (`exports: []` in #127 and #137). #127 set a verification check on
   `.session/orders.json`, a file that does not exist in the audit's session, so #128 ended `partial`. #137 gave a
   read-only audit 60 min and 80 turns, while the audit profile allows 10 min and 30 turns; #138 flagged the
   mismatch.
3. **Reports claim work that never landed.** #99, #127, #137 and #160 reported `done` and "working memory updated",
   but never ran `git commit`. The contract says "Commit before ending the session" and the epilogue publishes only
   committed changes, so each of those memory updates was lost. Neither the engine nor the next Policymaker noticed.
   4 out of 8 sessions on the current skill lost their memory this way. That memory is what a multi-pass policy
   phase relies on.
4. **The Policymaker watches loop health, not the goal.** In #160, the previous iteration had been accepted and
   `improved`. The Policymaker concluded "the loop is progressing … iteration 0008 proceeds on the current policy",
   even though the goal was already met. The shaper #161 noticed and asked the owner
   ([#162](https://github.com/Kucze3205/Perpetua/issues/162)). In no run was the gate accepted. Each Policymaker noted
   this, and none filed a request: "that needs an owner decision, not a request from me now" (#160, likewise #147 and
   #155). Without an accepted gate, the loop cannot decide on its own that it is done.

## Risk 3: same response to repeated `inconclusive` or no progress. No data.

No run produced either signal.

## Conclusions

- The environment focus in the skill needs no change: no drift was observed. The duration of the environment phase
  over many iterations remains unverified.
- Risk 2 is real, but its causes are narrower than "the Policymaker lacks commitment to the goal":
  - **Engine:** a session can report `done` with uncommitted changes in its writable paths (item 3). This is the most
    important finding: it silently breaks memory continuity, and a multi-pass phase would make that worse. The
    epilogue should detect it and downgrade the status, or reject the report.
  - **Skill:** "State expected effects in your report" yields descriptions of the process. Require instead that each
    order name the gain it expects toward the goal, and how the next pass will check that gain. Also have the
    Policymaker check whether the goal is already met, and file an owner request when the gate is not accepted
    (items 1 and 4).
  - **Evaluator:** none of the findings point at it.
- The data does not support removing the pass limit. Keep a limit until a run with a non-trivial goal spans several
  iterations, including at least one with no progress, after the two fixes above are in.
