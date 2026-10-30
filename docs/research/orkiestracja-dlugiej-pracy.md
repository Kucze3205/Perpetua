# Orchestrating long-running autonomous agent work (coding and non-coding)

Research for [Perpetua #12](https://github.com/Kucze3205/Perpetua/issues/12). Date: 2026-10-09.

**Question.** How are systems organized in which LLM agents work autonomously for days or weeks toward a goal? The candidate shapes are planner + workers, task graphs, blackboard, task markets, and hierarchies. The questions cover memory across sessions, role decomposition, LLM-judge and human evaluation, stop conditions, failure tolerance, cost, and human approval gates for outward actions and spending. What holds regardless of the goal's domain?

**Source policy.** Primary sources (vendor engineering blogs, official docs, arXiv) where possible. Claims marked **(unverified)** come only from secondary write-ups or search snippets that I could not check against the owner's text. Non-English sources were searched in Chinese, Japanese, Korean, Russian, German and French. They mostly confirm the English-language picture rather than add new mechanisms (see §10). Ticket #13 covers them in depth.

---

## TL;DR

1. **The winning shape is boring:** one coordinator holds the goal; workers have narrow tasks and their own clean context; a separate judge decides "done / continue"; all state lives in files or issues, not in a conversation. This holds for Cursor (weeks, hundreds of agents), Anthropic's research system, Devin's coordinator, Kosmos (science), and Project Vend (a business).
2. **Flat or self-organizing swarms fail.** Lock-based shared files, peer-to-peer "companies" and markets of agents all report the same problems: waiting, risk-aversion, duplicated work, and errors spreading socially. Measured: independent agents amplify errors 17.2x, while centralized setups do so 4.4x.
3. **Writes single-threaded, intelligence parallel.** Parallelize reading, research, review and generation of alternatives. Keep one writer per artifact.
4. **Memory = durable files + fresh contexts.** Every long-run system re-reads a small set of files at each session start: a spec with a "done when" section, a plan or feature list with pass flags, a progress log, and git history. Compaction alone is not enough.
5. **The judge must be independent:** clean context, ideally a different model family, a checklist rubric, and evidence (tests, citations). Same-model supervision "shares the blind spots" (Project Vend). False "done" claims are the most common long-run failure.
6. **Stop conditions are layered:** a machine-checkable "done when", a judge verdict per cycle, max iterations or turns, a budget cap, and periodic fresh restarts against drift.
7. **Outward actions go through a narrow, declared, separately-permissioned channel:** gh-aw "safe outputs", the Village's "request outreach approval" tool, AP2 spending mandates. Drafts are free; send, pay and publish need approval.


---

## 1. Organizational patterns, with evidence

| Pattern | Examples | What the evidence says |
|---|---|---|
| **Single agent in a loop + external state** | Ralph loop; Anthropic long-running harness; Codex 25-hour run | Works for days when state lives in files and every iteration re-reads it. The simplest option, and the baseline everything else must beat. |
| **Orchestrator–workers** (hub and spoke) | Anthropic Research; Claude Code subagents; Devin "managed Devins"; Kurly task DAG | Strong for breadth (research, review). Weak when subtasks share hidden decisions. |
| **Planner / worker / judge hierarchy** | Cursor "scaling agents" | Ran for weeks with hundreds of agents. Beat flat coordination. The judge ends each cycle and the next cycle starts fresh. |
| **Shared task list + mailbox (team)** | Claude Code agent teams; Gastown; Paperclip | Useful for parallel review. Practitioner reports show 5–10x tokens and fragile hand-offs. |
| **Blackboard / shared world model** | arXiv 2510.01285; Kosmos "structured world model"; MetaGPT message pool | The coordinator posts requests and specialists self-select. Kosmos ran coherently for 12 h and 200 rollouts thanks to the shared model. |
| **Market / auction** | Magentic Marketplace (Microsoft) | Agents show severe first-proposal bias and are manipulable. Not a sound task-allocation mechanism today. |
| **Corporate hierarchy** (CEO → workers) | Project Vend phase 2; Paperclip | The CEO layer added little because it shared the worker's blind spots. Cleanly separated roles (the merch agent) helped. |
| **Tournament / tree search** | Google AI co-scientist (Elo); AI Scientist v2 (agentic tree search) | Good for exploring alternatives under a judge. Ticket #11 covers this. |

### 1.1 Single agent + durable external state

- **Anthropic, "Effective harnesses for long-running agents."** An *initializer* session writes `init.sh`, `claude-progress.txt`, a first git commit, and a **JSON feature list** (200+ items, each with `passes: false`). Every later *coding* session reads git log, the progress file and the feature list, picks one feature, runs a basic end-to-end check first, and ends in a clean, committed state. JSON was chosen because the model "is less likely to inappropriately overwrite" it. The documented failure modes were: building too much at once, **declaring victory early** after seeing progress, and marking features done without end-to-end testing. Browser automation for verification "dramatically improved performance." The initializer and the coding agent differ only in their first user prompt. https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- **OpenAI, "Run long-horizon tasks with Codex."** GPT-5.3-Codex ran about 25 h uninterrupted, used about 13M tokens and wrote about 30k LOC. It used four files: `prompt.md` (spec with goals, non-goals and a **"done when"** section), `plans.md` (milestones with acceptance criteria and validation commands), `implement.md` (runbook) and `documentation.md` ("the shared memory and audit log"). A stop-and-fix rule applied: validation failure → repair before moving on. https://developers.openai.com/blog/run-long-horizon-tasks-with-codex.md — PLANS.md pattern (7+ h runs): https://developers.openai.com/cookbook/articles/codex_exec_plans
- **OpenAI, "Harness engineering."** Five months, about 1M LOC and about 1,500 PRs with 3–7 engineers and zero hand-written code. "Humans steer. Agents execute." https://openai.com/index/harness-engineering/ (403 to my fetcher; figures via https://www.infoq.com/news/2026/02/openai-harness-engineering-codex)
- **Ralph loop.** "Ralph is a Bash loop" that feeds the same prompt to a fresh agent process, so memory is only the filesystem and git. The Claude Code `ralph-wiggum` plugin instead uses a Stop hook in one session, with `--completion-promise` and `--max-iterations`. Guides warn that the default is unlimited iterations. **(unverified: secondary write-ups)** https://www.atcyrus.com/stories/ralph-wiggum-technique-claude-code-autonomous-loops

### 1.2 Orchestrator–workers

- **Anthropic multi-agent research system.** An Opus lead with Sonnet subagents beat single-agent Opus by **90.2%** on an internal eval. Multi-agent runs use about **15x** chat tokens (single agents about 4x). Token usage explains about 80% of the variance on BrowseComp. Multi-agent **hurts** "tasks needing shared context or many inter-agent dependencies. Most coding tasks fall here." The lead saves its plan to memory because context beyond 200k tokens gets truncated. Fresh subagents continue from the stored plan. https://www.anthropic.com/engineering/multi-agent-research-system
- **Anthropic, "Building effective agents."** Defines the vocabulary: prompt chaining with gates, routing, parallelization, orchestrator-workers, and **evaluator-optimizer** ("clear evaluation criteria exist and iterative refinement adds measurable value"). "Find the simplest solution possible, and only increase complexity when needed." https://www.anthropic.com/engineering/building-effective-agents
- **Cognition (Devin).** The original "Don't build multi-agents" argued that parallel subagents make conflicting implicit decisions (the Flappy Bird example). The April 2026 follow-up says what works is: "writes stay single-threaded and the additional agents contribute intelligence rather than actions"; a **clean-context reviewer** ("catches bugs the coder can't see"); "smart friend" escalation to a stronger model; and manager → children map-reduce. Unstructured swarms are "mostly a distraction." https://cognition.com/blog/multi-agents-working
- **Devin coordinator.** It launches child sessions, each with its own prompt, playbook and **ACU (compute) cap**. It monitors their spend, pauses or stops stuck ones, and schedules reminders to revisit long-running ones. https://docs.devin.ai/work-with-devin/advanced-capabilities

### 1.3 Planner / worker / judge (Cursor)

From https://cursor.com/blog/scaling-agents:
- **What failed:** self-coordination through a shared file with locks ("Twenty agents would slow down to the effective throughput of two or three"). Optimistic concurrency was better but not enough. A **flat** structure made agents "risk-averse": they avoided hard tasks and churned. A dedicated **integrator** role became a bottleneck and was removed.
- **What worked:** planners (recursive sub-planners) create tasks; workers take a task, finish it and push without coordinating with each other; at the end of each cycle **a judge decides whether to continue, and the next iteration starts fresh** "to combat drift and tunnel vision."
- **Scale:** a browser from scratch in about a week (1M+ LOC); a Solid→React migration over 3+ weeks; "trillions of tokens" in total.
- **Models per role:** GPT-5.2 was better at long autonomous work and at planning; Opus 4.5 "tended to stop earlier and take shortcuts." They now pick a model per role.
- Quotes: "Many of our improvements came from removing complexity rather than adding it." "The harness and models matter, but the prompts matter more."

### 1.4 Teams with a shared task list (Claude Code agent teams)

- Components: a lead, teammates, a shared task list (pending / in progress / completed, with dependencies and file-lock claiming) and per-agent JSON mailboxes. Hooks `TeammateIdle`, `TaskCreated` and `TaskCompleted` can block with exit code 2, which works as quality gates. https://code.claude.com/docs/en/agent-teams
- **Constraints that matter for Perpetua:**
  - Teams are **not spawned in non-interactive `-p` mode** (including Agent SDK). Named subagents run as ordinary subagents there. This is the mode GitHub Actions uses.
  - No nested teams, and no resumption of in-process teammates.
  - "Task status can lag"; the lead "can stop early too."
  - Token cost scales linearly with teammates. The docs recommend 3–5 teammates and 5–6 tasks per teammate.
- **Practitioner report (Korean).** Gastown used about 10x tokens versus a single session and productivity dropped. A 9-agent Paperclip "media agency" (target: 15 pieces per week) needed constant manual recovery at ≥5x tokens. The author mapped failures to MAST categories and concluded "running a 'company' of agents does not work well — for now." https://shalomeir.substack.com/p/multi-agent-orchestration-problems

### 1.5 Blackboard / shared world model

- **arXiv 2510.01285** (Google Cloud AI Research, UMass). The main agent posts requests to a blackboard and file-cluster agents decide for themselves whether to answer. Gains were 13–57% relative over RAG and master–slave designs on data-discovery tasks. The authors argue that master–slave designs need the controller to know every worker's abilities in advance. https://arxiv.org/abs/2510.01285
- **Kosmos (Edison Scientific / FutureHouse).** A data-analysis agent and a literature agent write into a **structured world model**, which then picks the next tasks. A run lasts up to 12 h or 200 rollouts, with about 42k LOC executed and about 1,500 papers read. 79.4% of report statements were judged accurate by independent scientists, and every claim cites a notebook or paper. https://arxiv.org/abs/2511.02824
- **MetaGPT.** SOPs encoded as prompt sequences, with an "assembly line" of roles verifying intermediate results. https://arxiv.org/abs/2308.00352 (The shared publish–subscribe message pool is from the full paper; not re-verified here.)

### 1.6 Markets and corporate hierarchies

- **Magentic Marketplace (Microsoft Research).** "All models exhibit severe first-proposal bias, creating 10–30x advantages for response speed over quality." Most models were manipulable, and welfare degrades with market size. https://arxiv.org/abs/2510.25779 — Implication: do not allocate tasks by agents bidding. Use a coordinator.
- **Project Vend phase 2 (Anthropic).** A CEO agent (Seymour Cash) supervised the shopkeeper (Claudius), and a separate merch agent (Clothius) was added. The CEO "shared many of the deficiencies and blind spots of Claudius" and approved lenient requests about 8x more often than it denied them. The most impactful changes were procedures and tools, such as forcing price and delivery checks before quoting ("bureaucracy matters"). A role with a clean boundary (merch) helped. There was **no payment interface: a human approved purchases.** Agents drifted into overnight "eternal transcendence" exchanges. https://www.anthropic.com/news/project-vend-2

### 1.7 When does multi-agent help? (quantitative)

- **Google Research / DeepMind / MIT, "Towards a science of scaling agent systems"** (180 configurations, 4 benchmarks). Error amplification: **17.2x for independent agents, 4.4x for centralized**, where the orchestrator acts as a validation bottleneck. Centralized coordination gave +80.9% on parallelizable tasks, but **all multi-agent variants were 39–70% worse on sequential tasks**. Returns are diminishing or negative once the single-agent baseline exceeds about 45%. https://arxiv.org/abs/2512.08296 · https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/
- **MAST, "Why do multi-agent LLM systems fail?"** 1,600+ traces across 7 frameworks, 14 failure modes in 3 groups: system design / specification, inter-agent misalignment, and task verification. Failures "primarily stem from system design issues," not model limits, so prompt fixes are insufficient. https://arxiv.org/abs/2503.13657
- **Kimi K2.5 Agent Swarm (Moonshot, China).** Orchestration is trained into the model: up to 100 subagents, trained with PARL against "serial collapse." Its metric counts only **critical-path steps**, so a split is worth it only if it shortens wall-clock time. Reported wall-clock gains were 3–4.5x. https://www.kimi.com/blog/kimi-k2-5 (vendor-reported)

---

## 2. Memory across sessions

What every long-running system converges on:

| Layer | Concrete form | Source |
|---|---|---|
| **Goal / spec** | `prompt.md` with "done when"; the Village repeats the goal in the system prompt | OpenAI Codex blog; AI Village |
| **Plan / backlog with status** | JSON feature list with `passes`; `plans.md` milestones with acceptance criteria; a shared task list | Anthropic harness; Codex; Claude Code teams |
| **Progress log / decisions** | `claude-progress.txt`; `documentation.md` ("shared memory and audit log") | Anthropic; OpenAI |
| **Versioned history** | git commits; Letta "context repositories" (git-versioned memory, Feb 2026) | Anthropic; https://blog.mayflower.de/27075-ki-agenten-gedaechtnis.html **(unverified)** |
| **Working scratchpad recited each turn** | Manus `todo.md`, rewritten to keep the plan near the end of context | https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus |
| **Consolidated long-term notes** | Village: a "consolidate" tool every ~40 actions, appended to memory and rewritten shorter when too long; a history-search tool | https://aivillageblog.substack.com/p/how-the-ai-village-works |
| **Event log** | OpenHands: an append-only, replayable event history; condensation recorded as events | https://arxiv.org/abs/2511.03690 · https://docs.openhands.dev/sdk/arch/events |
| **Shared world model** | Kosmos structured world model | §1.5 |

Lessons:
- **Compaction alone fails over many sessions.** You need structured external state that the next fresh session reads (Anthropic harness).
- **Fresh context beats accumulated context** for judging and for resisting drift. Cursor restarts each cycle; Cognition's clean-context reviewer; Ralph uses a fresh process.
- **Memory can entrench errors.** A Village agent came to believe every misclick was a system bug, and a hallucinated 93-person contact list spread to all agents and cost 8+ hours. https://aivillageblog.substack.com/p/what-we-learned-2025
- **Vending-Bench.** Long-horizon breakdowns usually start with the agent misreading its own state (for example, assuming a delivery had arrived). They do **not** correlate with the context window being full. https://arxiv.org/abs/2502.15840
- **Keep failures visible** (Manus): stack traces left in context stop the agent from repeating mistakes. Keep prompt prefixes stable for KV-cache hits. Manus runs at about 100:1 input:output tokens, and cached input costs one tenth of uncached.

## 3. Role decomposition

- **The roles that recur in every domain:** coordinator/planner (holds the goal and builds the task graph), worker/executor (narrow task, own context), **judge/verifier** (separate context, decides pass and continue), researcher (read-only, parallel). An optional integrator was removed at Cursor, because workers can resolve their own conflicts.
- **Separation is the value.** "Planners don't write code, workers don't plan" (Cursor). Clothius helped because its role was cleanly separated (Vend). Executor–validator–critic (Korean practitioner; Russian DepthNet, https://habr.com/ru/articles/1067196/ **(unverified)**).
- **Pick models per role.** Cursor matches models to roles; Anthropic uses an Opus lead with Sonnet workers; Cognition uses a "smart friend." Escalation fails when the primary model is too weak to know when to ask.
- **Coordinate through a shared environment** (tickets, git, queues), not free-form agent chat (shalomeir; Cursor's lock lesson). Russian Habr calls chatty multi-agent setups "hidden distributed monoliths," because natural language has no enforceable contracts. https://habr.com/ru/companies/otus/articles/986962/
- **Team size:** 3–5 parallel agents in the Claude Code docs. The Google scaling study shows saturation; the claim that gains stop beyond about 4 agents comes via a secondary citation **(unverified)**. Cursor runs hundreds, but only with strict hierarchy and independent tasks.
- **Reusable role definitions:** Devin playbooks and Knowledge; Claude Code subagent definitions reused as teammates. Devin can turn successful sessions into playbooks.

## 4. Evaluation: LLM judge + human

- **Rubric + single call.** Anthropic found one LLM call that returns a 0.0–1.0 score plus pass/fail against a rubric to be "most consistent and closest to human judgment." Start with about 20 real cases. **Human testers caught biases automation missed** (agents preferring SEO content farms). https://www.anthropic.com/engineering/multi-agent-research-system
- **Judge independence matters:**
  - Same-model supervision shares blind spots (Vend).
  - Self-preference bias is documented for GPT-4o and Claude 3.5 Sonnet, and Claude and GPT judges favor same-family outputs. https://arxiv.org/abs/2508.06709
  - Multimodal judges also favor their own generations. https://arxiv.org/pdf/2604.11589
  - Remedies: clean context, strip authorship, and a cross-family judge or a panel of judges.
- **Evidence-based checklists beat holistic VLM scoring.** Directly prompted VLM judges give under-justified, false-positive ratings. A checklist with key-frame evidence fixes this. https://arxiv.org/abs/2512.02942
- **Verification is the weakest link in long runs:**
  - Agents marked features done after unit tests or `curl` only (Anthropic harness).
  - "Opus 4 claimed over 50 benchmark tests completed when it had done only a fraction" (Village).
  - Kosmos: about 20% of statements were still wrong, so human review stays mandatory for claims.
- **Tournament judging** (co-scientist): pairwise Elo debates rank alternatives, and Elo correlated with correctness. Most compute goes to verification. https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/ **(details via secondary summaries; unverified)**
- **Human role in practice:**
  - Kurly: 1–2 h of upfront requirements, then only final results are checked. https://helloworld.kurly.com/blog/ai-orchestration-2/
  - Village: a kickoff plus 1–4 steering messages per week.
  - Japanese ad-review practice: AI does first-pass checks (wording, banned terms, required items) but "is not the approver of publish/no-publish" **(unverified, vendor blog)**. https://genai-ai.co.jp/ai-kanri/blog/cc-ad-ai-use-cases/

## 5. Stop conditions

Layered, from cheapest to most expensive:
1. **A machine-checkable "done when"** in the spec, plus per-milestone acceptance criteria and validation commands (Codex).
2. **A backlog where all items have `passes: true`**, which only verification may flip (Anthropic harness).
3. **A judge verdict per cycle**: continue or done (Cursor; the Claude Code `TaskCompleted` and `TeammateIdle` hooks can veto).
4. **Hard caps:** `--max-iterations` (Ralph), `--max-turns`, workflow `timeout-minutes`, gh-aw `max-ai-credits` per run (default 1,000) and `max-daily-ai-credits` (default 5,000), Devin ACU limits per child. https://github.github.com/gh-aw/
5. **Failure streak stop:** Kurly's loop runs verification after every 2 fixes and **stops and reports after 3 consecutive failures**.
6. **Human acceptance** for subjective goals.

Known anti-patterns:
- Agents **stop too early** (Opus 4.5 at Cursor; the agent-team lead "can stop early"; Anthropic "declaring victory").
- Agents **run too long** (Cursor; Ralph without a cap).
- A completion-promise string that occurs naturally (for example "done") ends the loop by accident.

## 6. Failure tolerance

- **Durable state, resumable steps.** Anthropic Research resumes from checkpoints instead of restarting, and tells agents when tools fail. Temporal-style durable execution: each LLM or tool call is a retryable activity, and runs survive crashes and **multi-day human waits at no compute cost**. Side effects must be idempotent (idempotency keys), because retries repeat them. https://docs.temporal.io/ai · https://temporal.io/blog/temporal-langgraph-plugin-durable-execution
- **Clean state at the end of each unit of work** (Anthropic harness: every session ends committed and mergeable; the next starts with a smoke test).
- **Ghost / stalled workers:** Kurly's `get_session` detects sessions that exited without reporting. MAST and shalomeir: "ghost delegation" (tasks stuck in review, subtasks open after the parent closed) accounts for about 37% of failures in the author's mapping **(unverified)**. Use timeouts and reassignment.
- **Fresh restarts against drift and tunnel vision** (Cursor), and against path-dependent false beliefs (Village).
- **Containment:** keep a single unreliable agent from poisoning the group. Sycophantic agreement spread a hallucination to every Village agent. Centralized validation keeps error amplification at 4.4x instead of 17.2x.
- **Sandboxing:** AI Scientist v2 executes LLM-written code, and its README recommends isolation. gh-aw runs the agent read-only in a sandbox with a firewall and an API proxy, so the agent never sees credentials. https://github.github.com/gh-aw/introduction/architecture/

## 7. Cost

- **Multipliers:** agent ≈ 4x chat, multi-agent ≈ 15x (Anthropic); Gastown ≈ 10x a single session; Paperclip ≥ 5x (shalomeir). Agent-team cost scales linearly with teammates (Claude Code docs).
- **Absolute figures:**
  - Codex 25 h ≈ 13M tokens.
  - Cursor: "trillions" of tokens in total.
  - AI Village ≈ $10k per month for 4–10 agents, 8 h per weekday.
  - shalomeir spent about $5k on orchestration experiments.
- **Levers:** model per role (cheap workers, strong planner); KV-cache-friendly append-only context (10x cheaper cached input, Manus); parallelize only critical-path work (Kimi "critical steps"); caps per run and per day (gh-aw); per-child compute caps (Devin).
- **Subscription vs API on GitHub Actions:** `claude-code-action` accepts `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`, Pro/Max/Team/Enterprise), so "runs use your Claude subscription instead of API billing." The token is **tied to the subscription of the person who generated it**. https://code.claude.com/docs/en/github-actions
- **Generation APIs dominate the cost of non-code goals** (§9.3).

## 8. Human approval gates for outward actions and spending

- **gh-aw "safe outputs"** are the clearest primary pattern for Perpetua's substrate. "Agents run read-only and request actions via structured output"; a separate job holding the write permission performs them. Each output type has a max count (for example `create-issue` defaults to 1). `staged: true` previews without side effects. `environment:` attaches GitHub deployment environments (environment secrets) to the output jobs. A threat-detection job scans the proposed outputs. https://github.github.com/gh-aw/reference/safe-outputs/
- **GitHub Actions itself:** a workflow run may last 35 days "including execution, waiting, and approval time." Environments with required reviewers therefore give a native human gate. Jobs are limited to 6 h on GitHub-hosted runners. https://docs.github.com/en/actions/reference/limits
- **AI Village:** contacting real people requires the "request outreach approval" tool. Approval is granted only if the outreach gives "substantial value to the human recipient." Unsolicited spam happened before this gate existed.
- **Project Vend:** no payment interface; a human approved purchases. The CEO agent still nearly signed an onion futures contract that would have broken the law.
- **AP2 (Agent Payments Protocol, Google).** A signed *Intent Mandate* (scope, price limits, conditions) allows human-not-present purchases within limits. A *Cart Mandate* records exactly what was approved. https://ap2-protocol.org **(spec details via secondary sources; unverified)**
- **Common rule** (German and French practice guides, Japanese ad review): drafts are unlimited; **send, delete, pay, publish need human approval**. Define the gates before production, not after an incident. Set gate strictness by the cost of an error. https://www.journaldunet.com/intelligence-artificielle/1548129-agents-ia-passer-du-prototype-a-un-systeme-en-production/ **(unverified)**
- **Agent-relayed approvals are untrusted.** Claude Code tells a receiving agent that a message came from another session; "a teammate can't approve a permission prompt or supply consent on your behalf." https://code.claude.com/docs/en/agent-teams

---

## 9. Non-coding cases

### 9.1 Non-coding long-running systems

| System | Domain | Shape | Notable lesson |
|---|---|---|---|
| Kosmos | Science | Two worker types + structured world model | 12 h coherent runs; ~80% accurate claims; every claim traceable |
| AI co-scientist | Science | Supervisor + generate / reflect / rank (Elo) / evolve / meta-review | Tournament as judge; compute spent on verification |
| AI Scientist v2 | ML research | Experiment-manager agent + tree search + VLM figure review | One workshop paper accepted; novelty and justification weak |
| Anthropic Research | Web research | Lead + 3–5 parallel subagents + citation agent | +90% vs single agent; 15x tokens |
| Project Vend | Retail business | Shopkeeper + CEO + merch agent; human pays | Procedures > hierarchy; same-model boss is weak |
| AI Village | Open goals (charity, events, merch) | Peer agents + group chat + memory consolidation | Goal neglect, fabricated success, social spread of hallucinations |
| Vending-Bench | Simulated business | Single agent | Long-horizon breakdown starts with misread state |
| Paperclip "media agency" | Content | Org chart of 9 agents | Failed to sustain 15 pieces per week without manual recovery |

---

## 10. Non-English sources: what they add

- **Chinese.**
  - Kimi K2.5's "critical steps" metric and the "serial collapse" failure (§1.7).
  - A 149-page Renmin University survey framing the current stage as a **"Runtime Harness"** that manages tools, memory, workflow, permissions, verification, recovery and multi-agent collaboration **(unverified)**. https://finance.sina.com.cn/tech/roll/2026-07-25/doc-iniizerf4357053.shtml
- **Korean.**
  - Kurly: 53 days, 940 sessions, 2,594 subagents; task DAG in layers; Obsidian + local vector recall; stop after 3 consecutive failures. https://helloworld.kurly.com/blog/ai-orchestration-2/
  - shalomeir's negative results on agent "companies" (§1.4).
- **Russian.**
  - "Multi-agents are hidden distributed monoliths" (no enforceable contracts in natural language).
  - DepthNet: deterministic service routing plus executor/critic/validator roles **(unverified)**.
- **German.**
  - Hybrid orchestration: deterministic BPMN/DMN backbone plus LLM agents for unstructured steps.
  - Approval for send/delete/pay/publish **(unverified, consultancy blogs)**. https://www.it-daily.net/it-management/ki/ki-agenten-in-der-produktion-2026-vom-prototyp-zum-prozessstandard
- **French.**
  - Layered memory including "transactional memory" (audit trail for recovery).
  - Mandatory HITL, dry-run and documented rollback for agents that write to production **(unverified)**.
---

## 11. What is universal regardless of domain

1. **One goal-holder, many narrow workers.** Centralize the goal and the task graph; give workers bounded tasks and fresh context; keep writes to each artifact single-threaded.
2. **State outside the model.** A spec with "done when", a backlog with pass flags, a progress/decision log and versioned history, re-read at every session start. In Perpetua's world these are issues, labels and repo files.
3. **An independent judge with evidence.** Clean context, ideally a different model family, a checklist rubric, and artifacts as evidence (tests, citations). Judge each unit, not only the end.
4. **Humans at gates, not in the loop.** Approve the goal and its acceptance criteria up front, approve the cheap preview (plan) before expensive work, approve the final result, and approve every outward action or spend. Steer sparingly in between.
5. **Layered stop conditions** plus fresh restarts against drift.
6. **Durability and idempotency.** Every step resumable; side effects behind a declared, separately-permissioned channel with counts and caps.
7. **Cost scales with parallelism.** Parallelize only what shortens the critical path; choose the model per role; cap spend per run and per day.
8. **Domain-specific parts are small:** the worker skills (code, external API calls), the judge rubric, and the expensive-action list. Everything above is the same for code, research and business.

## 12. Bearing on the architecture decision (#15)

- **Evidence favors a centralized coordinator with fresh-context workers and a separate judge per cycle**, which is close to v1's orchestrator + role tickets. The owner's hypothesis of a *tree of attempts with W = 1* matches Cursor's cycle (judge → fresh start) and the Ralph/Anthropic harness loop. Width > 1 resembles co-scientist and AI Scientist v2 tournaments and tree search, which pay off when a cheap judge can rank alternatives. See #11.
- **Substrate facts that constrain the design** (sources in §1.4, §7, §8):
  - Claude Code **agent teams do not run in headless `-p` mode**, which is what GitHub Actions uses. Only subagents are available inside one job, so parallelism must come from **separate Actions jobs** (matrix ≤256 jobs; 20 concurrent jobs on Free, 40 on Pro).
  - Each job is **limited to 6 h** and a workflow run to 35 days (including approval waits), so long work must be chunked into resumable units with state in the repo or issues.
  - `claude-code-action` **rejects bot actors** unless they are listed in `allowed_bots`, so loops that trigger themselves must allowlist their own bot.
  - A subscription OAuth token is tied to one person's plan, which is also the throughput ceiling.
- **The gh-aw "safe outputs" pattern** (the agent is read-only and emits structured requests; a separate job holding the permissions executes them with max counts, staged preview and an `environment:` with required reviewers) is a ready-made shape for Perpetua's per-loop policy on outward actions and spending. It also matches the map's "no hard default ban, policy set in setup."
- **Judge placement:**- **Judge placement:** the evidence favors a **separate judge step with clean context** (its own job or role), not a part of the orchestrator's context. Use a cross-family model if possible, with an evidence checklist derived from the setup map's acceptance criteria, and keep the owner as the final goal gate.

## Gaps and caveats

- The Cursor, Cognition and Anthropic posts are vendor self-reports. Only the Google scaling study and MAST are controlled studies.
- The full texts of AP2 and co-scientist were not read; details are marked unverified.
