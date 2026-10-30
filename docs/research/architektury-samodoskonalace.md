# Architectures of self-improving loops over many LLM agent sessions

Research for [#11](https://github.com/Kucze3205/Perpetua/issues/11), child of map [#1](https://github.com/Kucze3205/Perpetua/issues/1). Feeds the architecture decision (#15).
Checked 2026-10-09. Primary sources: arXiv papers (HTML versions), official repos and their config files, official blog posts. Searches also run in Chinese, Japanese, Korean, Russian, German and French. Those turned up ML-Master, Gome, MLEvolve, SIFT and the LoongFlow/CORAL/AdaEvolve family; everything they cite traces back to English-language arXiv primaries, which are cited here.
**[unverified]** marks inference or second-hand numbers. **[v2]** marks facts read from the owner's v2 repo `Kucze3205/blockblast-Self-Improvement-loop`.

## TL;DR

| # | Question | Answer |
| - | - | - |
| 1 | Is there a width knob that reduces to a linear loop? | Yes, in several systems: Dream-RSI `W`, SimpleTES `(C, L, K)` with `C=K=1`, AIDE / AI Scientist v2 `num_drafts=1`, ShinkaEvolve power-law `α→∞` (= hill-climbing). DGM has no width knob, but its ablation "w/o open-ended exploration" *is* the linear loop. |
| 2 | Is "Dream-RSI at W=1 ≈ v1, needs no policy" accurate? | **Half right.** Structurally W=1 is one sequence of sessions, but three decisions remain each round: **continue a chain tip / open a new chain from the root (restart) / stop**, plus switching back to an older chain's tip. A fixed rule ("stop after M misses, never restart") is enough, and v2's own `policy.py` already reduces to exactly that at W=1. **What does not carry over:** Dream-RSI's distinctive part, offline policy "dreaming", has almost nothing to learn at W=1 (see §2.3). It also says nothing about v1's actual orchestrator decisions (which role or task runs next). Those would live inside the node. |
| 3 | Do trees/populations beat a linear loop? | **Yes against an "always build on the latest" loop** (DGM: 50.0% vs 23.0% on SWE-bench). **Often not against a "keep best-so-far" loop with good operators and strong models:** in AIRA (Meta), MCTS and evolutionary search gave no gain over greedy with AIDE's operators; Gome finds tree search loses to directed single-trace optimization as models get stronger. Single-lineage systems (AVO, SICA, autoresearch) report strong results. Width pays mainly when the evaluator is cheap and objective and the problem needs qualitatively different approaches (SimpleTES, ShinkaEvolve, AB-MCTS on ARC). |
| 4 | Who chooses the next step? | Deterministic code (FunSearch, AlphaEvolve/OpenEvolve sampling, AIDE, DGM), bandit/Thompson/UCT (AB-MCTS, HGM, ML-Master, ShinkaEvolve's model choice), an LLM (AI Scientist v2 best-first, AI co-scientist Supervisor, AVO, SICA), or code that an LLM rewrites offline (Dream-RSI). |
| 5 | Non-code / subjective goals | Most systems require a cheap, automatic numeric evaluator; FunSearch and SimpleTES state this as a limitation. The established pattern for subjective output is **pairwise LLM judging** (order-swapped, aggregated into a tournament, Elo or Bradley-Terry) plus human sign-off. Examples: AI co-scientist, SIFT, QDAIF. |

## 1. Comparison table

| System | Unit of work (node) | Next step chosen by | Evaluation | Width knob → linear? | Evidence vs linear | Cost (reported) |
| - | - | - | - | - | - | - |
| **Dream-RSI** (2026) | One generation-evaluation attempt by a coding agent that resumes its parent's workspace | Exploration policy = **code**; an LLM rewrites it offline, scored by replay ("dreaming") on recorded trees | Fixed task scorer | `W` (workers/batch); W=1 → sequential, but continue/restart/stop remain | Only vs. *Recursive Fixed Exploration* (same tree, fixed policy); no linear baseline, no W=1 run | 110 calls/round (Pro: 10×11), 640 (Flash: 32×20); Lasso 317 vs 550 calls for fixed exploration |
| **FunSearch** (2023) | One function body, written from k=2 best-shot programs | Deterministic: islands, Boltzmann cluster sampling; worst half of islands reset every 4 h | Fast programmatic evaluator | `num_islands`; no clean linear limit (population sampling stays) | No linear ablation found in main text | ~10⁶ samples; 15 samplers, 150 CPU evaluators |
| **AlphaEvolve** (2025) / **OpenEvolve** | One program diff | Deterministic: MAP-Elites + islands; LLM ensemble (Flash breadth, Pro depth) | Evaluation cascade; optional LLM grading of soft properties | OpenEvolve `num_islands`, `exploitation_ratio`; no clean linear limit | Ablation "no evolution" (re-prompt initial program) is worse, figure only | Up to ~100 compute-hours per evaluation (AlphaEvolve) |
| **ShinkaEvolve** (2025) | One program candidate | Deterministic parent sampling (power-law / weighted / hill-climb) + **UCB1 bandit** over LLMs; novelty rejection (embeddings, optional LLM judge) | Programmatic | Power-law `α→∞` = hill-climbing | Weighted sampling "consistently outperforms" random and hill-climbing; hill-climbing plateaus | Circle packing SOTA in <150 evaluations |
| **Darwin Gödel Machine** (2025) | One self-modified coding agent | Deterministic: p ∝ sigmoid(score)·1/(1+children) over archive | Staged benchmark subsets (10 → 60 → 200 tasks) | `k` parents/iter (2 or 4); the linear case is the ablation "always self-modify the latest" | **SWE-bench 50.0% vs 23.0%; Polyglot 30.7% vs 14.0%** (linear baseline) | ~$22k and ~2 weeks per SWE-bench run; baselines ~$10k |
| **Huxley-Gödel Machine** (2025/26) | One self-modified agent; evaluation decoupled per agent-task pair | **Thompson sampling** on clade metaproductivity (CMP) for both expand and evaluate | Benchmark tasks, early stopping | none | Beats DGM/SICA: 56.7% vs 53.3%/50.0% (SWE-Verified-60) with 517 vs 1231 CPU-h | 347–517 CPU-h |
| **SICA** (2025) | One self-edited agent | LLM: best agent so far acts as meta-agent | Benchmarks | Already ~linear (best-so-far) | 17% → 53% on SWE-bench Verified subset | — |
| **AIDE** (2025) | One script (draft/debug/improve) | Hard-coded greedy: draft until `num_drafts`, debug with `debug_prob`, else improve best | Validation metric | `num_drafts` (default 5); =1 → greedy best-so-far hill-climb | Beats OpenHands/MLAB on MLE-bench (confounded: different scaffolds) | Mostly <$1.50/task (GPT-4 Turbo) |
| **AIRA / MLE-bench search** (Meta, 2025) | Same as AIDE | Greedy vs MCTS vs evolutionary, same operators | Validation (overfits test by 9–13 pp) | — | **With AIDE operators, MCTS/evo "gain no advantage"; operators are the bottleneck**; greedy best on gold | 24 h/task, 1 H200 |
| **AI Scientist v1** (2024) | Fixed linear pipeline over a human template | Fixed sequence | LLM reviewer ("near-human") | — (linear) | — | <$15/paper |
| **AI Scientist v2** (2025) | Experiment node (plan, code, metrics, VLM feedback); 4 stages | Best-first by **LLM evaluator**; buggy nodes by probability | Metrics + VLM critique of plots | `num_workers` (4), `num_drafts` (3) | README: v2 "doesn't necessarily produce better papers than v1"; v1 has higher success with a good template | $15–20 experiments + ~$5 write-up; several hours |
| **AB-MCTS / TreeQuest** (Sakana, 2025) | One LLM answer | **Thompson sampling**: GEN (go wider) vs refine child (go deeper) | External feedback score | Adaptive; sequential refinement = depth-only special case | ARC-AGI (GPT-4o): sequential refinement 8.7 vs repeated sampling 15.0 vs AB-MCTS 12.3 | 128 calls budget |
| **SimpleTES** (2026) | One candidate; best of K appended to trajectory | Deterministic; graph-PUCT picks which history to show | Verifiable numeric scorer | `(C, L, K)`; `(1, L, 1)` = sequential refinement | "a single deep trajectory is not enough"; L saturates, C helps; best split is task-dependent | Default N = 32×100×16 = 51,200 evaluations |
| **ML-Master** (SJTU, 2025) | Solution node | **UCT** (MCTS) + scoped memory | Validation | — | 29.3% medals MLE-bench (12 h) | — |
| **Gome** (2026) | Multi-trace "gradient" steps | LLM diagnostic reasoning (as gradient), success memory (as momentum) | Validation | — | **Tree search wins with weak models; directed optimization wins as reasoning gets stronger** | 35.1% medals, 12 h, 1 V100 |
| **AVO** (2026) | Committed kernel version in **one lineage** | The agent itself (self-directed), with a stall detector | Correctness + benchmark | Single lineage by design | 7 days → beats cuDNN by up to 3.5%, FlashAttention-4 by up to 10.5% | 7 days, 40 commits, >500 directions tried |
| **autoresearch** (Karpathy) | One 5-minute training run | The agent; keep/discard on val_bpb | val_bpb | Linear | — | ~12 runs/h |
| **AI co-scientist** (Google, 2025) | Hypothesis | **LLM Supervisor** assigns specialized agents to a worker queue | **Elo from pairwise LLM tournaments**; experts; wet lab | — | Elo correlates with accuracy on GPQA; quality scales with test-time compute | — |
| **SIFT** (MIT + Sakana, 2026) | Self-modification patch | Rank-based parent sampling on **Bradley-Terry strength from LLM pairwise judge**; expensive eval only for top nodes | Pairwise judge → benchmark | — | Beats prior tree self-evolution on Polyglot with lower CPU-h, wall-clock and API cost (no numbers in abstract) | — |

Sources per row: §3.

## 2. Dream-RSI and the W=1 hypothesis

### 2.1 What the paper defines

- **Node:** one generation-evaluation attempt. The agent "resumes its parent's saved workspace" and the node records a filesystem snapshot, artifact, diagnostics and score `s_v`. Each non-root node has exactly one parent ([arXiv 2609.14858](https://arxiv.org/html/2609.14858v1), §3).
- **Action per round:** the policy picks a batch `C` of *eligible* nodes, `|C| ≤ W`. Eligible = **root + current leaves**. Picking the root opens a new branch; picking a leaf extends it. "The rollout ends when the policy selects an empty batch or completes" `K₁` rounds (§3).
- **W** = number of workers, "each of which can execute one generation–evaluation request at a time" (§3).
- **Policy form:** executable code (`OptimalPolicy.solve`). An LLM policy-development agent revises it between rounds, scored by replay on all recorded trees: `V = max score − β₁·N + β₂·N/max{1,k}` (N revealed nodes, k rounds). The replay prompt states: "A serial policy has penalty near 1" (App. B.2).
- **Initial policy:** "parallel refining": several independent workspaces, each refining its own candidate (§4).
- **Not in the paper:** no W=1 experiment, no linear or greedy baseline, no limitations section. The only controlled baseline is *Recursive Fixed Exploration* (same machinery, policy frozen). Wall-clock and tokens are not reported.

### 2.2 What remains at W=1

At W=1 the batch has at most one element, so each round is one of:

| Decision | Present at W=1? |
| - | - |
| Continue the current chain (extend its leaf) | yes |
| **Restart**: open a new chain from the root | yes |
| **Switch** to the leaf of an older, abandoned chain (backtracking, leaves only, not interior nodes) | yes |
| **Stop** (empty batch) | yes, plus the hard cap `K₁` |
| How many attempts run in parallel | **no**, this decision disappears |

So W=1 is not "no policy". It is a sequential best-first search over chains, with three or four choices per round. It becomes a plain linear loop only under a rule such as "never restart, stop after M non-improving steps".

**[v2]** The owner's `policy.py` already does exactly that. It opens W chains, continues every chain tip unless the last `MISSES=2` nodes had `delta ≤ 0`, and returns an empty list when nothing is left. At W=1: one chain from the root, continued until two consecutive non-improvements, then stop; no restart. v2 uses the objective `V = max s_v − β·T`, with `T` = wall-clock (a round costs its longest node). This matches Perpetua's "shortest wall-clock" criterion more closely than the paper's call-count objective ([v2 `.github/policy/policy.py`, `meta.md`, `config.json` W=4 K=7](https://github.com/Kucze3205/blockblast-Self-Improvement-loop/tree/main/.github/policy)).

### 2.3 Why the "RSI" part is nearly empty at W=1 [unverified, inference from the replay rules]

Replay can only reveal attempts that were actually recorded. Opening the root in replay returns "its earliest-created child not yet revealed" (paper §3). In v2, a chain with no recorded continuation simply ends without penalty (`meta.md`). A tree recorded at W=1 holds one chain. Any alternative policy ("restart earlier", "stop later") therefore hits unrecorded nodes, and replay cannot score the counterfactual. Policy improvement needs several *branchy* recorded trees and a scorer that stays fixed across them. A one-goal Perpetua loop with a subjective judge produces few trees, and a judge whose verdicts drift. **Conclusion: at W=1, Dream-RSI collapses to a linear loop plus a fixed stop rule, and its distinctive mechanism adds nothing.**

### 2.4 How W=1 differs from v1

v1's LLM orchestrator makes **content** decisions: which task, which role (researcher, implementer, verifier), blocking edges, verify on the original, goal reached. Dream-RSI's policy makes only **structural** decisions: which node to expand, how many in parallel, when to stop. The node's agent decides *what* to try. "W=1 ≈ v1" holds for the shape (one sequence of sessions), not for the decision content. An orchestrator-plus-roles design would sit *inside* a Dream-RSI node, or be replaced by one homogeneous "improve the candidate" session. **[inference]**

### 2.5 Owner's own data point [v2, anecdotal, one tree]

**Setup.** Tree 1 ran at W=4 with 6 rounds. The scores below are `s_sym`, the simulator score.

**Results.** All four chains climbed from 0.26–0.56 at depth 1 to a common plateau of **0.86–0.88** by depth 3–5:

| Chain | Best `s_sym` |
| - | - |
| 1 | 0.871 |
| 2 | 0.875 |
| 3 | **0.885** (tree best) |
| 4 | 0.860 |

**Cost.** Each node cost about $0.16–0.34 and 1–11 minutes of session time (`trees/1/*.json`).

**Reading.** The best chain beat the average chain-best by about 0.01 on this tree, for 4× the sessions (zero extra wall-clock). That fits "width = variance reduction, not a different solution" on this task. **[unverified generalization]**

## 3. Systems in more detail

### Evolutionary / population (program databases)
- **FunSearch.**
  - k=2 programs per prompt, ordered by score (best-shot prompting); islands; every 4 h the worst-scoring half of the islands is reset from survivors; cluster sampling by score signature, favouring shorter programs.
  - ~10⁶ samples per run; 15 samplers, 150 CPU evaluators. Cap-set n=8: only 4 of 140 runs found the size-512 set.
  - Works best with "an efficient evaluator", a rich score and a skeleton.
  - Sources: [Nature paper (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC10794145/); config defaults `functions_per_prompt=2`, `num_islands=10`, `reset_period=4h`, `num_samplers=15`, `num_evaluators=140`, `samples_per_prompt=4` from [funsearch config.py](https://raw.githubusercontent.com/google-deepmind/funsearch/main/implementation/config.py).
- **AlphaEvolve.**
  - Diffs on whole programs. MAP-Elites + islands database. Flash for breadth, Pro for depth. Evaluation cascade.
  - LLM-graded soft properties (e.g. simplicity) can be added to scores. Up to ~100 compute-hours per evaluation.
  - Ablations (no evolution, no context, no meta-prompt evolution, no full-file, small LLM only) are each worse; numbers are in a figure only.
  - Sources: [arXiv 2506.13131](https://arxiv.org/html/2506.13131).
  - [AVO](https://arxiv.org/html/2603.24517) notes that AlphaEvolve's sampling stays a fixed heuristic procedure.
- **OpenEvolve** (open re-implementation).
  - Defaults: `num_islands: 5`, `population_size: 1000`, `archive_size: 100`, `exploitation_ratio: 0.7`, `exploration_ratio: 0.2`, `parallel_evaluations: 4`.
  - `use_llm_feedback: false` (experimental, weight 0.1); `cascade_evaluation: true`.
  - Source: [default_config.yaml](https://raw.githubusercontent.com/algorithmicsuperintelligence/openevolve/main/configs/default_config.yaml).
- **ShinkaEvolve.**
  - Power-law parent selection (`α=0` uniform, `α→∞` hill-climb), weighted (sigmoid × 1/(1+children), the same form as DGM), islands with migration.
  - Novelty rejection by embedding cosine (e.g. η=0.95), then an LLM judge. UCB1 bandit over LLMs.
  - Ablation: weighted beats hill-climbing and random. The bandit beats a single or fixed ensemble. The embedding filter helps "substantially"; the LLM-judge filter adds only marginally.
  - Source: [arXiv 2509.19349](https://arxiv.org/html/2509.19349).
- **Newer, self-reported, not verified here:**
  - AdaEvolve: claims 2.1× over OpenEvolve.
  - LoongFlow: tree + MAP-Elites.
  - CORAL: multi-agent co-evolution.
  - CodeEvolve ([arXiv 2510.14150](https://arxiv.org/html/2510.14150v3)), DeepEvolve (adds deep research, [arXiv 2510.06056](https://arxiv.org/pdf/2510.06056)).
  - Surfaced via Chinese sources: [juejin](https://juejin.cn/post/7595603381663711238), [gitcode/CSDN](https://gitcode.csdn.net/69eb85a80a2f6a37c5a5e4c8.html). **[unverified]**

### Self-modifying agents (archive/tree of agents)
- **DGM.**
  - Parent probability `p_i ∝ s_i·h_i`, with `s_i = sigmoid(λ(α_i−α₀))` (λ=10, α₀=0.5) and `h_i = 1/(1+n_children)`. k=2 (SWE-bench) or 4 (Polyglot) parents per iteration; 80 iterations.
  - Baseline "w/o open-ended exploration" "always self-modifies the latest stored version": 23.0% / 14.0% vs DGM 50.0% / 30.7%.
  - Cost ~$22k per run vs ~$10k per baseline, so **not compute-matched**.
  - Objective hacking: the DGM "removed the markers we use in the reward function to detect hallucination".
  - Sources: [arXiv 2505.22954 (Table 1, App. C.2, E.1)](https://arxiv.org/html/2505.22954v3), [Sakana blog](https://sakana.ai/dgm/).
- **HGM.**
  - Scores an agent by its *clade* (the agent plus its descendants). Pearson correlation with true clade metaproductivity: HGM 0.778 vs DGM 0.285 (SWE, weighted).
  - Thompson sampling for expand vs evaluate.
  - Source: [arXiv 2510.21614](https://arxiv.org/html/2510.21614v2). ICLR 2026.
- **SICA.**
  - The best agent so far is the meta-agent. 17% → 53% on a SWE-bench Verified subset.
  - Source: [arXiv 2504.15228](https://arxiv.org/html/2504.15228v2).
- **SIFT.**
  - An LLM pairwise judge plus Bradley-Terry ranks candidate self-modifications cheaply; expensive benchmarks run only on the top nodes.
  - Source: [arXiv 2609.19526](https://arxiv.org/abs/2609.19526).

### Tree search over attempts (ML engineering, reasoning)
- **AIDE.**
  - `search_policy`:
    1. Draft while drafts < `num_drafts`.
    2. With `debug_prob`, debug a random buggy leaf within `max_debug_depth`.
    3. Otherwise improve `get_best_node()`.
  - Defaults: `num_drafts: 5`, `debug_prob: 0.5`, `max_debug_depth: 3`, `steps: 20`. At `num_drafts=1` this is greedy best-so-far hill-climbing with debug retries.
  - Sources: [aide/agent.py](https://raw.githubusercontent.com/WecoAI/aideml/main/aide/agent.py), [config.yaml](https://raw.githubusercontent.com/WecoAI/aideml/main/aide/utils/config.yaml), [arXiv 2502.13138](https://arxiv.org/html/2502.13138).
- **AIRA.**
  - Greedy vs MCTS vs evolutionary search; with AIDE operators, "more advanced search policies … gain no advantage".
  - The operator set "can act as a bottleneck". AIRA_greedy beats AIDE_greedy, 45.5% vs 39.8%.
  - Validation-to-test overfitting costs 9–13 pp of medal rate.
  - Source: [arXiv 2507.02554](https://arxiv.org/html/2507.02554).
- **AB-MCTS / TreeQuest.**
  - GEN node = go wider; existing child = go deeper; Thompson sampling decides.
  - Repeated sampling remains best on ARC-AGI (GPT-4o 15.0 vs 12.3). Multi-LLM AB-MCTS is reported on ARC-AGI-2 in the blog.
  - Sources: [arXiv 2503.04412](https://arxiv.org/html/2503.04412v5), [Sakana blog (JA/EN)](https://sakana.ai/ab-mcts/), library [SakanaAI/treequest](https://github.com/SakanaAI/treequest).
- **SimpleTES.**
  - N = C·L·K. Allocation is task-dependent: math constructions favour larger C, GPU kernels larger L.
  - "Increasing L initially improves performance but quickly saturates"; "increasing C provides a clear benefit". No numeric `(1, L, 1)` result is given.
  - Requires a verifiable evaluator with dense scores.
  - Source: [arXiv 2604.19341](https://arxiv.org/html/2604.19341).
- **ML-Master.**
  - UCT-based MCTS with memory; 29.3% average medal rate on MLE-bench in 12 h.
  - Sources: [SJTU news (ZH)](https://news.sjtu.edu.cn/jdzh/20250704/212431.html), [arXiv 2506.16499](https://arxiv.org/abs/2506.16499).
- **Gome.**
  - "LLM-based agents for MLE predominantly rely on tree search, a form of gradient-free optimization". Across 10 models, tree search is better for weaker models; "as reasoning capability strengthens, gradient-based optimization progressively outperforms".
  - Source: [arXiv 2603.01692](https://arxiv.org/abs/2603.01692), found via a Chinese search.
- **MLEvolve.**
  - Progressive Monte-Carlo graph search with cross-branch edges, scheduled from wide exploration to focused exploitation.
  - Source: [alphaxiv (RU)](https://www.alphaxiv.org/ru/abs/2606.06473). **[abstract only]**
- **SWE-Search.**
  - MCTS + value agent + discriminator debate; 23% relative improvement over the same agents without MCTS, across 5 models.
  - Source: [arXiv 2410.20285](https://arxiv.org/abs/2410.20285).
- **Test-time compute.**
  - Sequential revision vs parallel search: effectiveness "critically varies depending on the difficulty of the prompt"; the compute-optimal mix is >4× more efficient than best-of-N.
  - Source: [Snell et al., arXiv 2408.03314](https://arxiv.org/abs/2408.03314).
  - Detail that sequential suits easier prompts and parallel/search harder ones: **[unverified, from memory of the paper body]**.

### Research-agent pipelines
- **AI Scientist v1.**
  - Linear template pipeline, <$15 per paper, LLM reviewer.
  - Source: [arXiv 2408.06292](https://arxiv.org/abs/2408.06292).
- **AI Scientist v2.**
  - 4 stages × best-first tree. LLM evaluator picks nodes; VLM critiques figures.
  - Config: `num_workers: 4`, `num_drafts: 3`, `debug_prob: 0.5`, `max_debug_depth: 3`, stage iterations 20/12/12/18.
  - One of 3 papers scored 6.33 at an ICLR 2025 workshop.
  - README caveats: v2 "doesn't necessarily produce better papers than v1"; v1 "follows well-defined templates, leading to high success rates".
  - Sources: [arXiv 2504.08066](https://arxiv.org/html/2504.08066), [repo](https://github.com/SakanaAI/AI-Scientist-v2), [bfts_config.yaml](https://raw.githubusercontent.com/SakanaAI/AI-Scientist-v2/main/bfts_config.yaml).
- **AI co-scientist.**
  - Generation, Reflection, Ranking, Evolution, Proximity and Meta-review agents; a Supervisor assigns them to a worker queue.
  - "self-improvement relies on the Elo auto-evaluation metric derived from its tournaments". Elo correlates with GPQA accuracy.
  - Sources: [Google Research blog](https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/), [arXiv 2502.18864](https://arxiv.org/abs/2502.18864).

### Single-lineage loops (the "linear" reference class)
- **AVO.**
  - "single-lineage autonomous run". A version is committed only if it passes correctness and matches or beats the best score.
  - A self-supervision stall detector redirects the search (not a restart). 40 commits / >500 directions in 7 days.
  - Source: [arXiv 2603.24517](https://arxiv.org/html/2603.24517).
- **autoresearch.**
  - "modifies the code, trains for 5 minutes, checks if the result improved, keeps or discards, and repeats".
  - Source: [karpathy/autoresearch](https://github.com/karpathy/autoresearch).
- **Self-correction without external feedback** can degrade reasoning ("LLMs cannot self-correct reasoning yet", Huang et al. 2023, arXiv 2310.01798). **[from memory, not re-fetched]** All of the above rely on an external evaluator, not self-judgement.

## 4. Evidence: tree/population vs linear, summarized

**Three different "linear loops" get conflated.** Keep them apart:

| Linear variant | Example | Evidence |
| - | - | - |
| (a) Always build on the **latest** attempt | DGM ablation | Clearly bad: 23% vs 50% (DGM) |
| (b) Keep **best-so-far** (keep/discard, rollback) | SICA, autoresearch, AVO, AIDE greedy | Competitive: AIRA greedy ≈ MCTS ≈ evo with the same operators; AVO/SICA strong |
| (c) **LLM-orchestrated** heterogeneous sessions | v1, AI co-scientist Supervisor | No controlled comparison found |

**Width or population helps when:**
- the evaluator is cheap, objective and dense (FunSearch, SimpleTES, ShinkaEvolve);
- the problem needs qualitatively different approaches, e.g. math constructions ([SimpleTES](https://arxiv.org/html/2604.19341));
- models are weaker ([Gome](https://arxiv.org/abs/2603.01692));
- refinement without new information tends to stay in one basin (SimpleTES quote; AB-MCTS ARC result).

**Width helps little when:**
- operators are the bottleneck ([AIRA](https://arxiv.org/html/2507.02554));
- models are strong (Gome);
- a good template exists ([AI Scientist v2 README](https://github.com/SakanaAI/AI-Scientist-v2));
- chains converge to the same plateau (v2 tree 1).

**Cheapest form of width = parallel independent attempts + selection** (best-of-N per step):
- Repeated sampling was the strongest method on ARC-AGI in AB-MCTS.
- SimpleTES's K is this form.
- It needs no policy beyond "pick the best".

## 5. Fit for non-code / subjective goals

| Requirement of the system | Problem for a subjective goal |
| - | - |
| Cheap numeric evaluator, many samples (FunSearch ~10⁶, SimpleTES 51k, AlphaEvolve) | Each node = an expensive attempt + judge; noisy absolute scores |
| Fixed scorer across recorded trees (Dream-RSI replay) | The judge's rubric drifts as the owner gives feedback; the final gate is human |
| Benchmark subsets (DGM/HGM) | None exists for a one-off subjective goal |

**Patterns that do transfer:**
- **Pairwise LLM tournament with order swap**: Elo tournaments ([AI co-scientist](https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/)); Bradley-Terry over pairwise judgements ([SIFT](https://arxiv.org/abs/2609.19526)).
- **VLM critique of visual artifacts marks a node as buggy** ([AI Scientist v2](https://arxiv.org/html/2504.08066)).
- **LLM-judged quality *and* diversity** for creative text, with reasonable AI-human agreement ([QDAIF, arXiv 2310.13032](https://arxiv.org/abs/2310.13032)).

## 6. Bearing on the architecture decision (#15) [inference]

1. **Hypothesis "W=1 ≈ v1, no policy" holds for the shape, but needs two corrections.**
   - A fixed stop rule (M misses / budget / human gate) is still needed. "Restart from root" is an optional extra decision. Both fit in a few deterministic lines (v2's `policy.py` at W=1 already is this).
   - The orchestrator-plus-roles content of v1 is a separate axis. It lives inside a node, not in the tree policy.
2. **Dream-RSI's offline policy learning is not worth carrying into Perpetua.** At W=1 it has nothing to replay. For one-off subjective goals it lacks the fixed scorer and the many recorded trees it needs.
3. **The universal shape that the evidence supports:**
   - one lineage with **best-so-far + rollback** (never "always latest");
   - **width W as a parameter**, used as *W parallel candidates per step, then select*;
   - selection: numeric evaluator if one exists, else a pairwise LLM tournament.
   - W=1 is the linear loop; W>1 costs subscription quota, not wall-clock, on GitHub Actions. That fits "shortest wall-clock to goal". This is SimpleTES-K / best-of-N, not MCTS.
4. **Full tree policies (MCTS, Thompson, islands) are justified only for cheap objective evaluators.** If a goal has one, TreeQuest (AB-MCTS) is an off-the-shelf library ([SakanaAI/treequest](https://github.com/SakanaAI/treequest)) rather than something to build.
5. **The bigger levers are operators and evaluator, not search policy:**
   - operators = what a session does: AIRA, Gome;
   - evaluator quality and anti-hacking: DGM removed its hallucination markers; AIRA shows validation overfitting.
   - Keep the evaluator and gate outside the node's write access, as v2 already does with `tylko_do_odczytu`.
