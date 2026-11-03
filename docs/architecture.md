# Architecture deep-dive

## The one-sentence design

A task goes in; a **plan** (data) is composed from **declared agents**
(data); the plan is **compiled to a LangGraph graph at runtime**; the
runtime executes the graph under **governance** (budgets, gates,
recovery) with a **provider** bound per agent — and every step leaves
a trace.

```
TaskSpec ──► DynamicPlanner ──► WorkflowPlan (DAG, hashable)
                                    │
Registry (AgentSpec[]) ─────────────┤ compile_plan()
                                    ▼
                          LangGraph StateGraph ──► ManagedRuntime
                                    │                  │  ├─ budget governor
                    provider ◄──────┘  step executor    │  ├─ approval gates
                    (per agent)                        │  ├─ retry / replan
                                                       │  └─ traces → RunRecord
```

## Components

### Registry (`registry.py`)
Agents are frozen dataclasses. The registry answers one question for
the planner — `with_skill(skill, exclude=...)`, in registration
order — and one for the runtime: the `AgentSpec` behind a name.
Nothing in the runtime switches on agent identity; behaviour comes
from the record (skills, tools, provider preference, approval
skills, token cap).

### Plans (`workflow.py`)
`WorkflowPlan`/`PlanStep` are JSON-serialisable dataclasses.
`validate()` rejects duplicate ids, unknown dependencies and cycles
(Kahn's algorithm). `layers()` exposes the topological layering the
sequential baseline chains against. `plan_hash()` is a SHA-256 over
the canonical JSON — the identity the provider-swap experiment uses
to prove the workflow did not change when the provider did.

### Planner (`planner.py`)
`DynamicPlanner` maps task structure to steps:

| task need | steps composed |
|---|---|
| each sub-question | one `research` step (all in layer 0 → parallel) |
| each lookup | one `lookup` step |
| each computation | one `compute` step depending on its input lookups |
| ≥2 gathered steps | one `synthesize` step over all of them |
| always | one `write` step |
| `publish=True` | one `publish` step (sensitive if the agent gates it) |

Agent assignment is first-declared-with-skill. `StaticPlanner`
exists as the measured baseline: fixed stages, first sub-question
only, first computation only. `replan_step()` is the recovery
primitive: same step, next agent with the skill, dependencies
already satisfied by completed results dropped; optional steps and
steps with no alternative agent return `None` (skip / unrecoverable).

### Compiler (`compiler.py`)
`compile_plan(plan, step_fn, sequential=False)` builds a fresh
`StateGraph` per plan:

- state is `{results: dict (merge reducer), traces: list (concat)}`;
- one node per step, one edge per dependency, `START`→roots,
  leaves→`END`;
- because LangGraph schedules by superstep, every step whose
  dependencies have completed runs in the same superstep — fan-out
  is concurrency without any concurrency code in the platform;
- `sequential=True` adds chain edges layer by layer, forcing a total
  order — used only as the E2 baseline.

The graph is deliberately dumb: `step_fn` is injected by the runtime,
so providers, budgets and gates are *runtime* policy, not graph
structure. Replanning is recompilation of a smaller plan.

### Runtime (`runtime.py`)
`ManagedRuntime.run()` is a loop of *execute → inspect → maybe
replan* (bounded by `max_replans`):

1. Compile the current plan and `ainvoke` it with the accumulated
   outcomes seeded into state (so recovery runs see prior results).
2. Per step, the injected executor:
   - marks the step `blocked` without executing if any dependency
     is not `ok` (failure propagates structurally, never silently);
   - for sensitive steps, calls the approver; a denial is a
     recorded terminal state, not an exception;
   - asks the **budget governor** to reserve the step's estimated
     tokens (`provider.estimate_tokens`) against the run's token
     cap, step cap, and the agent's registry cap — a refusal is a
     recorded `budget_blocked` state with the reason;
   - executes through the step's provider with bounded retries on
     `ProviderError`, sleeping the provider's reported latency
     (simulated at demo scale);
   - records a `StepTrace` and reconciles actual tokens/cost.
3. If steps failed and replans remain, builds a recovery plan
   (failed steps → alternative agents; blocked steps re-issued)
   and loops. Run status is derived from final outcomes:
   `completed`, `completed_with_denials`, `completed_partial`,
   `failed`, `budget_exceeded`.

**Why reservations, not post-hoc accounting?** In a parallel
superstep, several steps start before any finishes. Post-hoc checks
would let concurrent branches jointly overshoot a cap. Reserving
estimated spend before execution — and reporting estimates alongside
actuals — makes the cap a hard invariant: E5 stops at 6 of 32 steps
with the reservation total under the cap.

### Providers (`providers.py`)
The contract is one method: `execute(StepRequest, ToolBox) ->
StepResult`, plus `estimate_tokens` and per-1K prices. The runtime
never imports a vendor SDK.

`ScriptedProvider` implements each skill as an explicit strategy over
the real tools, parameterised by tier:

| skill | stub-basic | stub-pro |
|---|---|---|
| research | top-1 chunk | top-3 chunks |
| compute | rounds literal operands to ints | exact |
| synthesize | first 400 chars of gathered text | fact-bearing sentences |
| lookup / write / publish | same | same |

Failure injection is provider configuration (`fail_agents`,
`flaky={agent: n}`), which is how E4 takes the primary researcher
down without touching platform code.

Hosted adapters (`OpenAIProvider`, `AnthropicProvider`) speak REST
via `urllib`, are constructed only with an env key, and run steps in
direct-completion mode. They exist to prove the seam is real, not
to be exercised offline.

### Tools (`tools.py`)
Tools are capability-checked twice: the registry declares which an
agent may hold, and `ToolBox` is built per agent from exactly that
list — a step that asks for an undeclared tool raises `ToolError`.
The calculator parses with `ast` and walks a whitelist (no `eval`,
no names, no attributes). The file writer resolves the target inside
the sandbox and rejects escapes.

### Corpus & verifiers (`corpus.py`)
Experiments measure against a fixed fictional world: 8 document
chunks, a 12-row metrics table, 8 tasks. Each task carries a
deterministic verifier (expected facts / values in the final
artifact). The corpus is built so capability differences are
*structural*: one fact chunk is outranked by a keyword-heavy summary
chunk for indirect phrasing, so retrieval depth is measurable;
decimal arithmetic makes rounding measurable; fact position in
gathered text makes synthesis strategy measurable.

## What a production version changes

Real LLM planner behind the same `plan()` contract; hosted
providers driving tools via function calling; reservations replaced
by provider-reported usage with the same governor interface;
approvals as an authenticated, auditable workflow; persistence for
run records; per-tenant registries. The graph, governance and
accounting code paths above are the parts this repo exists to make
readable and testable.
