# open-managed-agents — a model-agnostic managed-agent platform

## Problem

Managed-agent platforms (Claude Managed Agents and its peers) show the
shape of the product: you *declare* agents, submit a task, and the
platform plans, executes, governs and bills the work. But the managed
offerings tie you to one vendor's models and one vendor's runtime —
the workflow logic, the governance and the accounting live inside
someone else's black box.

What is missing is the open version of that shape: a managed-agent
runtime where agents are data, workflows are composed dynamically per
task, execution is an open framework (LangGraph), and *any* model
provider can be bound at run time — with the platform concerns
(budgets, approvals, recovery, traces, cost) implemented in the open,
where you can read and test them.

## Solution

open-managed-agents is that runtime, in miniature but complete:

- **Declared agents.** An `AgentSpec` is pure data: name, role,
  instructions, skills, allowed tools, provider preference, approval-
  gated skills, per-agent token cap. Adding an agent is adding a
  registry record — no runtime code changes.
- **Dynamic workflow composition.** A planner inspects each task's
  declared needs and composes a workflow DAG: one research step per
  sub-question (parallel branches), lookups feeding computations, a
  synthesis step, then write/publish. Tasks get the workflow they
  need, not a fixed pipeline.
- **Runtime compilation to LangGraph.** The DAG is compiled to a
  LangGraph `StateGraph` *at run time* — one node per step, edges from
  dependencies; independent steps execute concurrently as one
  superstep. There is no hand-written graph in the platform.
- **Model-agnostic providers.** Steps execute through a small provider
  interface. Two deterministic offline providers ship
  (`stub-basic`, `stub-pro` — scripted stand-ins whose capability
  tiers are explicit strategy differences), plus optional
  OpenAI/Anthropic REST adapters used only when an API key is in the
  environment. The *same plan* runs unchanged on any of them
  (experiment E3 verifies this by plan hash).
- **Managed-platform governance.**
  - *Budgets:* the governor reserves each step's estimated tokens
    against the run's token/step caps (and per-agent caps) *before*
    the step starts; an over-cap reservation blocks the step and
    escalates the run — spend cannot overshoot, even in parallel.
  - *Approval gates:* steps whose skill is approval-gated for their
    agent are held for a human approver; denial is recorded and
    blocks dependents.
  - *Failure handling:* bounded retries, then a **replan** — failed
    steps are re-issued to a different agent with the same skill,
    blocked dependents are re-run, and every recovery is recorded
    with its reason.
  - *Records:* every run yields a `RunRecord` — per-step traces
    (agent, provider, attempts, tokens, cost, latency, status),
    totals, and the final artifact — serialisable to JSON.
- **Safe deterministic tools.** Calculator (AST-whitelisted, never
  `eval`), document search and data lookup over a seeded corpus, and
  a file writer confined to a sandbox directory.

## Architecture

```
src/open_managed_agents/
  registry.py    AgentSpec + AgentRegistry (agents as data)
  corpus.py      seeded documents, metrics table, task suite, verifiers
  planner.py     DynamicPlanner, StaticPlanner (baseline), replan_step
  workflow.py    WorkflowPlan / PlanStep (JSON-able DAG, canonical hash)
  compiler.py    plan -> LangGraph StateGraph, at runtime
  providers.py   provider interface; scripted tiers; OpenAI/Anthropic adapters
  tools.py       calculator, doc search, data lookup, sandbox file writer
  runtime.py     ManagedRuntime: sessions, budget governor, gates,
                 retry/replan, traces, cost accounting
demo.py          full managed session end-to-end (approve / deny / flaky)
experiments/     E1–E5, writing results/results.md + charts from real runs
```

## How dynamic compilation works

1. `DynamicPlanner.plan(task, registry)` composes a `WorkflowPlan`
   from the task's sub-questions, lookups and computations and the
   registry's declared skills.
2. `compile_plan` builds a fresh `StateGraph`: a node per step,
   an edge per dependency, `START` to the roots, leaves to `END`.
3. The runtime injects the step executor, so the graph itself knows
   nothing about providers, budgets or gates — those live in the
   executor closure each node calls.
4. LangGraph's superstep scheduling runs every ready node
   concurrently; a node whose dependencies did not succeed marks
   itself `blocked` instead of executing.
5. On failure, the runtime asks the planner for a recovery plan over
   just the failed/blocked steps and compiles *that* — replanning is
   recompilation.

## How to run

```bash
pip install -e .
python3 demo.py                          # end-to-end managed session
python3 experiments/run_experiments.py   # E1–E5 -> results/
python3 -m pytest tests/test_smoke.py    # 21 smoke tests
```

No API keys are needed for any of the above: the scripted providers
are the reference execution path.

## Results (seeded run; see `results/results.md`)

- **E1 dynamic vs static:** dynamic plans verify **8/8** tasks;
  the fixed pipeline verifies 4/8 — it fails exactly the tasks that
  need more than one research question, and spends *more* steps than
  the dynamic plan on the quick tasks it does pass (5 vs 2).
- **E2 parallelism:** independent branches as one superstep run
  **1.86×** faster than the same plans forced sequential
  (1.80 s vs 3.35 s over three fan-out tasks), outputs identical.
- **E3 provider swap:** identical plans (hash-verified) on both
  tiers — stub-pro verifies 8/8, stub-basic 5/8, at ~1/60th the
  cost. Basic's misses are strategy failures (top-1 retrieval,
  rounded arithmetic, head-only synthesis), not randomness.
- **E4 failure recovery:** with the primary researcher down, runs
  complete **4/4 with retry+replan vs 0/4 without** — but only 2/4
  still *verify*, because the backup agent runs the basic tier:
  recovery of execution is not recovery of quality.
- **E5 budget governance:** a 32-step runaway fan-out under a
  6,000-token cap is stopped at 6 executed steps, status
  `budget_exceeded`, reason in the trace; with a sufficient cap the
  same plan completes all 32 steps.

## Honest limits

- The **planner is a deterministic capability-matching composer**,
  not an LLM planner. It demonstrates the platform contract
  (anything that returns a `WorkflowPlan` can plan) without
  pretending a scripted composer is a reasoning model.
- The **scripted providers are stand-ins**. Their tier differences
  are explicit strategies over the real tools, chosen so capability
  gaps are measurable; they do not measure any real model's ability.
  The hosted adapters run steps in direct-completion mode (no tool
  driving) and are exercised only when keys are present — the demo,
  tests and experiments never call them.
- Token counts are a deterministic `chars // 4` estimate and prices
  are stand-in list prices; the accounting *machinery* (per-step,
  per-agent, per-run) is real, the absolute dollars are illustrative.
- Simulated step latency is real sleep at demo scale (tenths of a
  second). The parallelism result shows the scheduling property of
  the compiled DAG, not a production throughput claim.
- Failure injection is by configuration (an agent marked down, a
  flaky counter), not chaos over real infrastructure; approvals are
  a callback, not an identity-checked workflow product.

## License

MIT — see [LICENSE](LICENSE).
