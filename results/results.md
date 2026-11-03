# open-managed-agents: results (seeded run)

All numbers below are measured from actual runs of this suite against the bundled corpus (scripted providers, no API keys).

## E1 — Dynamic vs static planning

| task | dynamic success | dynamic steps | static success | static steps |
|---|---|---|---|---|
| brief-launch | True | 5 | False | 4 |
| brief-ops | True | 5 | False | 4 |
| analysis-net-revenue | True | 4 | True | 5 |
| analysis-north | True | 5 | True | 6 |
| quick-units | True | 2 | True | 5 |
| quick-north-revenue | True | 2 | True | 5 |
| deepdive-launch | True | 9 | False | 5 |
| publish-brief | True | 5 | False | 5 |

- dynamic: **8/8 tasks verified**, avg steps 4.6, avg wall 0.48s
- static: **4/8 tasks verified**, avg steps 4.9, avg wall 0.68s
- the static pipeline fails exactly the tasks whose needs it cannot see: more than one sub-question. It succeeds on single-thread analysis/quick tasks — and spends more steps there than the dynamic plan (fixed stages it does not need).

## E2 — Parallel branches vs forced-sequential

| task | parallel wall | sequential wall | speedup |
|---|---|---|---|
| brief-launch | 0.54s | 1.05s | 1.95x |
| brief-ops | 0.54s | 1.01s | 1.87x |
| deepdive-launch | 0.68s | 1.24s | 1.82x |

- total: parallel 1.76s vs sequential 3.31s (**1.88x**) — independent research branches run as one LangGraph superstep; step outputs are identical, only the schedule changes.

## E3 — Provider swap (identical plans, zero workflow changes)

- plan composition is deterministic: re-planning each task reproduced the identical plan hash in 8/8 cases, and the same plan objects were executed on both providers — the swap changes only the runtime's provider binding.
- stub-basic: **5/8 verified**, total cost $0.0009
- stub-pro: **8/8 verified**, total cost $0.0549
| task | basic | pro | what basic missed |
|---|---|---|---|
| brief-launch | False | True | 14 March 2027; 17.5 percent |
| brief-ops | True | True | — |
| analysis-net-revenue | False | True | net_revenue = 173666.83 |
| analysis-north | True | True | — |
| quick-units | True | True | — |
| quick-north-revenue | True | True | — |
| deepdive-launch | False | True | 14 March 2027; 17.5 percent; 12 engineers; two medium findings; net_revenue = 173666.83 |
| publish-brief | True | True | — |
- basic's failures are strategy failures, documented in the provider: top-1 retrieval misses the indirectly-phrased fact, literal rounding breaks decimal arithmetic, head-only synthesis drops late facts. Cheaper per token, more expensive per *successful* task on this suite.

## E4 — Failure recovery (primary researcher `scout` down)

| task | with replan: status | verified | no replan: status | verified |
|---|---|---|---|---|
| brief-launch | completed | False | failed | False |
| brief-ops | completed | True | failed | False |
| deepdive-launch | completed | False | failed | False |
| publish-brief | completed | True | failed | False |

- workflow recovery (run completes): **4/4 with retry+replan vs 0/4 without**.
- verified recovery: 2/4 — replan moves research to the backup agent, whose provider is the basic tier: execution recovers fully, but on tasks whose facts need deep retrieval the *answer quality* drops with the tier. Recovery of execution is not recovery of quality.

## E5 — Budget governance (runaway fan-out)

- task fans out to 32 planned steps (~29000 estimated tokens).
- cap 6000 tokens: status **budget_exceeded**, 6 steps executed, actual spend 511 tokens — the governor blocked the step whose reservation would have crossed the cap and escalated the run with the reason in the trace.
- cap 100000 tokens: status completed, 32 steps executed, 11637 actual tokens.
- the cap is enforced on *reservations* before steps start, so parallel branches cannot overshoot it concurrently.
