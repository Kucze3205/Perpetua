## 🟢 Iteration 17 · closed · goal not reached

Started 2026-11-02 19:10 UTC · took 15m 06s · engine 0.7.0

### Active roles
| Role | Model | Effort | Sessions | In | Out | Cache read | Cache write |
|---|---|---|---|---|---|---|---|
| worker | haiku-5-5 | low | 1 | 10 | 1.4k | 56.4k (86%) | 9.4k |
| orchestrator | haiku-5-5 | low | 1 | 8 | 2.8k | 52.5k (76%) | 16.1k |
| policymaker | haiku-5-5 | low | – | – | – | – | – |

### Task graph
```mermaid
flowchart LR
  write_greeting["✅ Write src/greeting.txt<br/>worker · merged"]:::ok
  classDef ok fill:#dafbe1,stroke:#1a7f37,color:#1f2328
  classDef warn fill:#fff8c5,stroke:#9a6700,color:#1f2328
  classDef bad fill:#ffebe9,stroke:#cf222e,color:#1f2328
  classDef run fill:#ddf4ff,stroke:#0969da,color:#1f2328
  classDef wait fill:#f6f8fa,stroke:#8c959f,color:#1f2328
```

### Policy changes
_none this iteration_

### Tokens and cache
| | in | out | cache read | cache write |
|---|---|---|---|---|
| This iteration | 18 | 4.2k | 108.9k | 25.6k |
| Last 9 iterations | 152 | 33.0k | 1024.1k | 158.9k |

### Session summaries
- [worker #319](https://github.com/Kucze3205/Perpetua/issues/319) → [Session summary](https://github.com/Kucze3205/Perpetua/issues/319)
- [orchestrator #318](https://github.com/Kucze3205/Perpetua/issues/318) → [Session summary](https://github.com/Kucze3205/Perpetua/issues/318)

<sub>Updated by the engine at 2026-11-02 19:25 UTC</sub>
