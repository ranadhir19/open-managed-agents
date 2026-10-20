"""The managed runtime: sessions, budgets, gates, recovery, records.

The runtime is what makes this a *managed* platform rather than a
graph demo:

- **Sessions & records.** Every run produces a ``RunRecord`` with a
  full per-step trace (agent, provider, tokens, cost, latency,
  attempts, status) that serialises to JSON.
- **Budget governance.** Before a step starts, the governor reserves
  its estimated tokens against the run's token budget and step cap;
  a reservation that would exceed the cap blocks the step (and its
  dependents) and escalates the run — spend never exceeds the cap.
  Per-agent caps from the registry are enforced the same way.
- **Human-approval gates.** Steps whose skill is in the agent's
  ``approval_skills`` are held for the approver callback; a denial is
  recorded and blocks dependents, it never silently proceeds.
- **Failure handling.** Provider errors are retried (bounded); a step
  that still fails triggers a *replan*: the planner re-issues the
  failed and blocked steps — failed steps move to a different agent
  with the same skill — and the recovery plan is compiled and run.
  Every recovery is recorded with its reason.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field

from .compiler import compile_plan
from .planner import replan_step
from .providers import ProviderError, ScriptedProvider, StepRequest
from .registry import AgentRegistry
from .tools import (CalculatorTool, DataLookupTool, DocSearchTool,
                    FileWriterTool, ToolBox)
from .workflow import WorkflowPlan, PlanStep


@dataclass
class StepTrace:
    step_id: str
    agent: str
    provider: str
    skill: str
    status: str            # ok | failed | blocked | denied | budget_blocked
    attempts: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class RunRecord:
    run_id: str
    task_id: str
    plan_hash: str
    origin: str
    status: str            # completed | completed_partial | failed |
                           # budget_exceeded | completed_with_denials
    traces: list = field(default_factory=list)        # StepTrace
    replans: list = field(default_factory=list)       # human-readable reasons
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    wall_time_s: float = 0.0
    final_output: str = ""
    success: bool | None = None                       # set by a verifier

    @property
    def steps_executed(self) -> int:
        return sum(1 for t in self.traces if t.status == "ok")

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["traces"] = [t.to_dict() for t in self.traces]
        d["steps_executed"] = self.steps_executed
        return d

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)


class _Budget:
    def __init__(self, token_cap: int, step_cap: int):
        self.token_cap = token_cap
        self.step_cap = step_cap
        self.reserved_tokens = 0
        self.reserved_steps = 0
        self.agent_spend: dict[str, int] = {}

    def reserve(self, agent: str, est_tokens: int,
                agent_cap: int | None) -> str | None:
        """Returns a refusal reason, or None when the reservation holds."""
        if self.reserved_steps + 1 > self.step_cap:
            return f"step cap {self.step_cap} reached"
        if self.reserved_tokens + est_tokens > self.token_cap:
            return (f"token budget {self.token_cap} would be exceeded "
                    f"({self.reserved_tokens}+{est_tokens})")
        if agent_cap is not None and \
                self.agent_spend.get(agent, 0) + est_tokens > agent_cap:
            return f"agent {agent} token cap {agent_cap} would be exceeded"
        self.reserved_tokens += est_tokens
        self.reserved_steps += 1
        self.agent_spend[agent] = self.agent_spend.get(agent, 0) + est_tokens
        return None


def approve_all(step: PlanStep) -> bool:
    return True


class ManagedRuntime:
    def __init__(self, registry: AgentRegistry, chunks, metric_rows,
                 sandbox_dir: str, providers: dict | None = None,
                 provider_override=None, max_retries: int = 2,
                 max_replans: int = 2):
        self.registry = registry
        self._tools = {
            "calculator": CalculatorTool(),
            "doc_search": DocSearchTool(chunks),
            "data_lookup": DataLookupTool(metric_rows),
            "file_writer": FileWriterTool(sandbox_dir),
        }
        self.providers = providers or {
            "stub-pro": ScriptedProvider("stub-pro"),
            "stub-basic": ScriptedProvider("stub-basic"),
        }
        self.provider_override = provider_override
        self.max_retries = max_retries
        self.max_replans = max_replans
        self._run_counter = 0

    # -- provider / toolbox binding ----------------------------------------
    def _provider_for(self, agent_name: str):
        if self.provider_override is not None:
            return self.provider_override
        return self.providers[self.registry.get(agent_name).provider]

    def _toolbox_for(self, agent_name: str) -> ToolBox:
        return ToolBox.build(self.registry.get(agent_name).tools, self._tools)

    # -- public API ----------------------------------------------------------
    def run(self, plan: WorkflowPlan, budget_tokens: int = 20000,
            max_steps: int = 40, allow_replan: bool = True,
            sequential: bool = False, approver=None) -> RunRecord:
        return asyncio.run(self._run(plan, budget_tokens, max_steps,
                                     allow_replan, sequential,
                                     approver or approve_all))

    async def _run(self, plan, budget_tokens, max_steps, allow_replan,
                   sequential, approver) -> RunRecord:
        self._run_counter += 1
        record = RunRecord(run_id=f"run-{self._run_counter:03d}",
                           task_id=plan.task_id, plan_hash=plan.plan_hash(),
                           origin=plan.origin, status="completed")
        budget = _Budget(budget_tokens, max_steps)
        t0 = time.time()
        outcomes: dict[str, dict] = {}
        current, rounds = plan, 0
        while True:
            outcomes = await self._execute(current, outcomes, budget,
                                           record, approver, sequential)
            failed = [s for s in current.steps
                      if outcomes.get(s.id, {}).get("status") == "failed"]
            blocked = [s for s in current.steps
                       if outcomes.get(s.id, {}).get("status") == "blocked"]
            if not failed:
                break
            if not allow_replan or rounds >= self.max_replans:
                break
            recovery = self._recovery_plan(current, failed, blocked, outcomes)
            if recovery is None:
                break
            rounds += 1
            record.replans.append(
                f"round {rounds}: re-issued {[s.id for s in recovery.steps]} "
                f"after failure of {[s.id for s in failed]}")
            current = recovery
        record.wall_time_s = round(time.time() - t0, 3)
        record.total_tokens = sum(t.tokens_in + t.tokens_out for t in record.traces)
        record.total_cost_usd = round(sum(t.cost_usd for t in record.traces), 6)
        record.final_output = self._final_output(plan, outcomes)
        statuses = {o.get("status") for o in outcomes.values()}
        if "budget_blocked" in statuses:
            record.status = "budget_exceeded"
        elif "failed" in statuses or "blocked" in statuses:
            record.status = "failed" if "write" not in outcomes or \
                outcomes.get("write", {}).get("status") != "ok" else "completed_partial"
        elif "denied" in statuses:
            record.status = "completed_with_denials"
        return record

    def _final_output(self, plan, outcomes) -> str:
        for sid in ("write", "synthesize"):
            if outcomes.get(sid, {}).get("status") == "ok":
                return outcomes[sid]["text"]
        parts = [o["text"] for o in outcomes.values() if o.get("status") == "ok"]
        return "\n\n".join(parts)

    def _recovery_plan(self, plan, failed, blocked, outcomes):
        completed = {sid for sid, o in outcomes.items() if o.get("status") == "ok"}
        steps = []
        for s in failed:
            repl = replan_step(s, self.registry, completed)
            if repl is None and not s.optional:
                return None
            if repl is not None:
                steps.append(repl)
        for s in blocked:
            deps = tuple(d for d in s.depends_on if d not in completed)
            steps.append(PlanStep(id=s.id, agent=s.agent, skill=s.skill,
                                  instruction=s.instruction, depends_on=deps,
                                  params=dict(s.params), optional=s.optional,
                                  sensitive=s.sensitive))
        if not steps:
            return None
        rec = WorkflowPlan(task_id=plan.task_id, goal=plan.goal, steps=steps,
                           origin="replan")
        rec.validate()
        return rec

    # -- graph execution ------------------------------------------------------
    async def _execute(self, plan, seed_outcomes, budget, record, approver,
                       sequential):
        step_fn = self._make_step_fn(budget, record, approver)
        graph = compile_plan(plan, step_fn, sequential=sequential)
        state = await graph.ainvoke({"results": dict(seed_outcomes), "traces": []})
        return state["results"]

    def _make_step_fn(self, budget: _Budget, record: RunRecord, approver):
        async def step_fn(step: PlanStep, state):
            results = state["results"]
            trace = StepTrace(step_id=step.id, agent=step.agent, provider="",
                              skill=step.skill, status="ok")
            bad_deps = [d for d in step.depends_on
                        if results.get(d, {}).get("status") != "ok"]
            if bad_deps:
                trace.status = "blocked"
                trace.detail = f"dependencies not ok: {bad_deps}"
                record.traces.append(trace)
                return {"results": {step.id: {"status": "blocked", "text": ""}},
                        "traces": [trace]}
            spec = self.registry.get(step.agent)
            provider = self._provider_for(step.agent)
            trace.provider = provider.name
            context = {d: results[d]["text"] for d in step.depends_on}
            params = self._resolve_params(step, results)
            req = StepRequest(agent=step.agent, skill=step.skill,
                              instruction=step.instruction,
                              context=context, params=params)
            if step.sensitive:
                if not approver(step):
                    trace.status = "denied"
                    trace.detail = "human approver denied this step"
                    record.traces.append(trace)
                    return {"results": {step.id: {"status": "denied", "text": ""}},
                            "traces": [trace]}
                trace.detail = "approved by human approver"
            est = provider.estimate_tokens(req)
            refusal = budget.reserve(step.agent, est, spec.max_tokens_per_run)
            if refusal:
                trace.status = "budget_blocked"
                trace.detail = refusal
                record.traces.append(trace)
                return {"results": {step.id: {"status": "budget_blocked",
                                              "text": ""}},
                        "traces": [trace]}
            box = self._toolbox_for(step.agent)
            t0 = time.time()
            result, err = None, None
            for attempt in range(1, self.max_retries + 2):
                trace.attempts = attempt
                try:
                    result = provider.execute(req, box)
                    break
                except ProviderError as exc:
                    err = exc
            if result is None:
                trace.status = "failed"
                trace.detail = f"provider error after {trace.attempts} attempts: {err}"
                record.traces.append(trace)
                return {"results": {step.id: {"status": "failed", "text": ""}},
                        "traces": [trace]}
            await asyncio.sleep(result.latency_s)
            trace.latency_s = round(time.time() - t0, 3)
            trace.tokens_in, trace.tokens_out = result.tokens_in, result.tokens_out
            trace.cost_usd = round(
                result.tokens_in / 1000 * provider.price_in
                + result.tokens_out / 1000 * provider.price_out, 6)
            record.traces.append(trace)
            outcome = {"status": "ok", "text": result.text, "data": result.data}
            return {"results": {step.id: outcome}, "traces": [trace]}
        return step_fn

    @staticmethod
    def _resolve_params(step: PlanStep, results: dict) -> dict:
        """Fill compute-step templates from upstream steps' data values."""
        params = dict(step.params)
        if step.skill == "compute" and "template" in params:
            values = {}
            for name, sid in params.get("inputs", {}).items():
                values[name] = results[sid]["data"]["value"]
            params["expression"] = params["template"].format(**values)
        return params
