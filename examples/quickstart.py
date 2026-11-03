"""Quickstart: declare a custom agent, compose a plan, run it managed."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from open_managed_agents import (AgentSpec, DynamicPlanner, ManagedRuntime,
                                 check_task, default_registry)
from open_managed_agents.corpus import CHUNKS, METRIC_ROWS, TaskSpec

reg = default_registry()
# a new agent is a registry record, not code:
reg.register(AgentSpec(
    name="auditor", role="compliance auditor",
    instructions="Retrieve policy facts for audits.",
    skills=("research",), tools=("doc_search",), provider="stub-basic"))

task = TaskSpec(id="audit-q", kind="brief", goal="Audit fact check.",
                sub_questions=["penetration test findings reporting service"],
                expected_facts=["two medium findings"])
plan = DynamicPlanner().plan(task, reg)
sandbox = os.path.join(os.path.dirname(__file__), "sandbox")
rec = ManagedRuntime(reg, CHUNKS, METRIC_ROWS, sandbox).run(plan)
ok, _ = check_task(task, rec.final_output)
print(f"status={rec.status} verified={ok} steps={rec.steps_executed} "
      f"cost=${rec.total_cost_usd:.4f}")
print(rec.final_output)
