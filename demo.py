"""open-managed-agents end-to-end demo (seeded, deterministic).

A full managed session:
  1. the fleet is *declared* in a registry (no agent code),
  2. a task arrives; the dynamic planner composes a workflow DAG,
  3. the DAG is compiled to a LangGraph StateGraph at runtime,
  4. the runtime executes it under a token budget, with the writer's
     publish step held for a human-approval gate,
  5. the same task is then re-run with the approver *denying* publish,
  6. and once more with the primary researcher transiently failing,
     so the retry path fires.

Writes results/demo_run.json (the full run record) and prints traces.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from open_managed_agents import (DynamicPlanner, ManagedRuntime, ScriptedProvider,
                                 check_task, default_registry, task_suite)
from open_managed_agents.corpus import CHUNKS, METRIC_ROWS

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
SANDBOX = os.path.join(HERE, "results", "sandbox")


def show(record, task):
    ok, missing = check_task(task, record.final_output)
    print(f"  status={record.status}  steps ok={record.steps_executed}  "
          f"tokens={record.total_tokens}  cost=${record.total_cost_usd:.4f}  "
          f"wall={record.wall_time_s:.2f}s  verified={ok}")
    for t in record.traces:
        print(f"    [{t.status:14s}] {t.step_id:12s} agent={t.agent:12s} "
              f"provider={t.provider:10s} attempts={t.attempts} "
              f"tokens={t.tokens_in + t.tokens_out:5d} ${t.cost_usd:.4f}"
              + (f"  ({t.detail})" if t.detail else ""))
    for r in record.replans:
        print(f"    replan: {r}")
    if missing:
        print(f"    verifier missing: {missing}")
    return ok


def main():
    os.makedirs(RESULTS, exist_ok=True)
    reg = default_registry()
    print(f"registry: {len(reg)} declared agents: {', '.join(reg.names())}")
    planner = DynamicPlanner()
    task = next(t for t in task_suite() if t.id == "publish-brief")
    plan = planner.plan(task, reg)
    print(f"\ntask {task.id}: {task.goal}")
    print(f"plan hash {plan.plan_hash()} — {len(plan.steps)} steps, "
          f"layers: {[len(layer) for layer in plan.layers()]}")

    rt = ManagedRuntime(reg, CHUNKS, METRIC_ROWS, SANDBOX)
    print("\n[1] managed run, approver approves publish:")
    rec = rt.run(plan, budget_tokens=20000)
    show(rec, task)
    rec.save(os.path.join(RESULTS, "demo_run.json"))

    print("\n[2] same plan, approver DENIES publish:")
    rec2 = rt.run(plan, budget_tokens=20000, approver=lambda step: False)
    show(rec2, task)

    print("\n[3] same plan, primary researcher transiently flaky (2 failures):")
    flaky_rt = ManagedRuntime(
        reg, CHUNKS, METRIC_ROWS, SANDBOX,
        providers={"stub-pro": ScriptedProvider("stub-pro", flaky={"scout": 2}),
                   "stub-basic": ScriptedProvider("stub-basic")})
    rec3 = flaky_rt.run(plan, budget_tokens=20000)
    show(rec3, task)
    print("\ndemo done — full record in results/demo_run.json")


if __name__ == "__main__":
    main()
