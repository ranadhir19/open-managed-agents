# Writing agents (and tasks)

## Declare an agent

An agent is a record in the registry — no subclassing, no runtime
edits:

```python
from open_managed_agents import AgentRegistry, AgentSpec

reg = AgentRegistry()
reg.register(AgentSpec(
    name="scout",                    # unique id used in plans and traces
    role="researcher",               # human-readable
    instructions="Retrieve the document chunks that answer a question.",
    skills=("research", "lookup"),   # subset of the six skills
    tools=("doc_search", "data_lookup"),  # the only tools it may touch
    provider="stub-pro",             # provider preference (name)
    approval_skills=(),              # skills held for a human approver
    max_tokens_per_run=None,         # optional per-agent token cap
))
```

The six skills are `research`, `lookup`, `compute`, `synthesize`,
`write`, `publish`. A step can only be assigned to an agent that
declares its skill, and at execution time the agent's `ToolBox`
contains exactly its declared tools — asking for anything else
raises.

### Choosing the knobs

- **provider** — a *preference*. The runtime can override it for a
  whole run (that is how the provider-swap experiment works), so
  treat it as a default, not a guarantee.
- **approval_skills** — anything with side effects belongs here
  (the demo fleet gates `publish` on the writer). Denial is a
  normal, recorded outcome; design downstream steps to tolerate it.
- **max_tokens_per_run** — a hard per-agent ceiling enforced by the
  same reservation governor as the run budget. Useful for a costly
  specialist you never want dominating a run.
- **backups** — recovery quality is a registry property: if a skill
  has exactly one agent, its failure is unrecoverable by replan.
  Declare a backup agent for any skill a run cannot afford to lose,
  and know that the backup's provider tier becomes the quality
  ceiling of a recovered run (experiment E4 measures exactly this).

## Declare a task

```python
from open_managed_agents import TaskSpec

task = TaskSpec(
    id="q1-net", kind="analysis",
    goal="Compute Q1 net revenue after the vendor discount.",
    lookups=[{"metric": "revenue", "label": "gross_revenue"}],
    computations=[{"label": "net_revenue",
                   "template": "{gross_revenue} * (1 - 0.175)",
                   "inputs": {"gross_revenue": "lookup_0"}}],
    expected_values=["net_revenue = 173666.83"],   # verifier
)
```

- `sub_questions` become parallel `research` steps.
- `lookups` become `lookup` steps (`metric`, optional `region` /
  `month`, `agg`, and a `label` used in output text).
- `computations` become `compute` steps; `template` is an arithmetic
  expression whose `{placeholders}` are filled from the named lookup
  steps' values (`inputs` maps placeholder → step id, which also
  creates the dependency edges).
- `publish=True` appends the gated `publish` step.
- `expected_facts` / `expected_values` are the verifier: substrings
  that must appear in the final artifact. Keep them honest — they
  are the definition of "the task worked" in every experiment.

## Add a provider

Implement two methods and register the instance with the runtime:

```python
class MyProvider:
    name = "my-provider"
    price_in, price_out = 0.0, 0.0        # USD per 1K tokens
    def estimate_tokens(self, req): ...
    def execute(self, req, toolbox) -> StepResult: ...
```

`StepRequest` carries the agent, skill, instruction, upstream
context (dependency step id → text) and structured params;
`StepResult` returns text, token counts, latency and a `data` dict
(structured values — `compute`/`lookup` results travel there, and
computation templates read them). Raise `ProviderError` for
retryable failures; the runtime owns retry and replan policy.

Then either name it in agent records, pass it in the runtime's
`providers={...}` map, or bind a whole run with
`provider_override=...` — plans do not change in any of the three.

## Run it managed

```python
plan = DynamicPlanner().plan(task, reg)
rec = ManagedRuntime(reg, CHUNKS, METRIC_ROWS, sandbox_dir).run(
    plan, budget_tokens=20000, max_steps=40,
    approver=my_approval_callback)     # (PlanStep) -> bool
rec.save("run.json")                   # full trace, costs, replans
```

Read `rec.status`, `rec.traces`, `rec.replans` and `rec.final_output`
— that record is the unit of accountability for the run.
