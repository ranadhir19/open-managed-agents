"""Smoke tests for open-managed-agents (seeded, deterministic).

Run with pytest, or directly:  python3 tests/test_smoke.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from open_managed_agents import (AgentRegistry, AgentSpec, DynamicPlanner,
                                 ManagedRuntime, ScriptedProvider, StaticPlanner,
                                 check_task, default_registry, replan_step,
                                 runaway_task, task_suite)
from open_managed_agents.corpus import CHUNKS, METRIC_ROWS
from open_managed_agents.planner import _agent_for
from open_managed_agents.providers import ProviderError, StepRequest
from open_managed_agents.tools import (CalculatorTool, DataLookupTool,
                                       DocSearchTool, FileWriterTool, ToolBox,
                                       ToolError)
from open_managed_agents.workflow import PlanStep, WorkflowPlan

TMP = tempfile.mkdtemp(prefix="oma-tests-")


def _runtime(**kw):
    return ManagedRuntime(default_registry(), CHUNKS, METRIC_ROWS, TMP, **kw)


def _task(task_id):
    return next(t for t in task_suite() if t.id == task_id)


# ---------------------------------------------------------------- registry
def test_default_registry_fleet():
    reg = default_registry()
    assert len(reg) == 4
    assert set(reg.names()) == {"scout", "scout-backup", "analyst", "writer"}
    assert reg.get("writer").approval_skills == ("publish",)


def test_registry_rejects_duplicates_and_bad_skills():
    reg = AgentRegistry()
    spec = AgentSpec("a", "r", "i", ("research",), ("doc_search",))
    reg.register(spec)
    for fn in (lambda: reg.register(spec),
               lambda: AgentSpec("b", "r", "i", ("fly",), ())):
        try:
            fn()
            raise AssertionError("should have raised")
        except ValueError:
            pass


# ---------------------------------------------------------------- workflow
def test_plan_layers_and_hash_stability():
    reg = default_registry()
    plan = DynamicPlanner().plan(_task("brief-launch"), reg)
    layers = plan.layers()
    assert len(layers[0]) == 3                      # 3 parallel research steps
    assert plan.plan_hash() == DynamicPlanner().plan(_task("brief-launch"),
                                                     reg).plan_hash()
    other = DynamicPlanner().plan(_task("brief-ops"), reg)
    assert other.plan_hash() != plan.plan_hash()


def test_plan_cycle_rejected():
    bad = WorkflowPlan("t", "g", [
        PlanStep("a", "scout", "research", "x", depends_on=("b",)),
        PlanStep("b", "scout", "research", "x", depends_on=("a",))])
    try:
        bad.validate()
        raise AssertionError("cycle not detected")
    except ValueError:
        pass


def test_dynamic_plan_shapes():
    reg = default_registry()
    quick = DynamicPlanner().plan(_task("quick-units"), reg)
    assert [s.skill for s in quick.steps] == ["lookup", "write"]  # no synthesis
    pub = DynamicPlanner().plan(_task("publish-brief"), reg)
    publish = pub.step("publish")
    assert publish.sensitive and publish.agent == "writer"


def test_static_plan_is_fixed_shape():
    reg = default_registry()
    plan = StaticPlanner().plan(_task("deepdive-launch"), reg)
    skills = [s.skill for s in plan.steps]
    assert skills.count("research") == 1            # only the first question
    assert skills.count("compute") == 1


def test_replan_moves_to_backup_agent():
    reg = default_registry()
    plan = DynamicPlanner().plan(_task("brief-ops"), reg)
    failed = plan.step("research_0")
    repl = replan_step(failed, reg, completed={})
    assert repl is not None and repl.agent == "scout-backup"
    optional = PlanStep("x", "scout", "research", "q", optional=True)
    assert replan_step(optional, reg, completed={}) is None


# ------------------------------------------------------------------- tools
def test_calculator_whitelist():
    calc = CalculatorTool()
    assert calc.run("2 + 3 * 4") == 14.0
    for bad in ("__import__('os')", "open('x')", "x + 1"):
        try:
            calc.run(bad)
            raise AssertionError(f"accepted {bad!r}")
        except ToolError:
            pass


def test_doc_search_indirect_fact_rank():
    search = DocSearchTool(CHUNKS)
    q = "helios launch date ship readiness"
    assert "14 March 2027" not in search.search(q, k=1)[0].text
    assert any("14 March 2027" in c.text for c in search.search(q, k=3))


def test_data_lookup_and_writer_sandbox():
    lookup = DataLookupTool(METRIC_ROWS)
    assert lookup.query("units") == 3544.0
    writer = FileWriterTool(TMP)
    path = writer.run("ok.txt", "hello")
    assert os.path.exists(path)
    try:
        writer.run("../escape.txt", "no")
        raise AssertionError("escape not rejected")
    except ToolError:
        pass


def test_toolbox_enforces_agent_tools():
    reg = default_registry()
    box = ToolBox.build(reg.get("writer").tools,
                        {"file_writer": FileWriterTool(TMP),
                         "calculator": CalculatorTool()})
    try:
        box.get("calculator")
        raise AssertionError("writer should not hold the calculator")
    except ToolError:
        pass


# --------------------------------------------------------------- providers
def _research_req():
    return StepRequest(agent="scout", skill="research",
                       instruction="helios launch date ship readiness")


def test_scripted_provider_determinism_and_tiers():
    reg = default_registry()
    tools = {"doc_search": DocSearchTool(CHUNKS),
             "data_lookup": DataLookupTool(METRIC_ROWS),
             "calculator": CalculatorTool(),
             "file_writer": FileWriterTool(TMP)}
    box = ToolBox.build(reg.get("scout").tools, tools)
    pro = ScriptedProvider("stub-pro")
    r1, r2 = pro.execute(_research_req(), box), pro.execute(_research_req(), box)
    assert r1.text == r2.text and "14 March 2027" in r1.text
    basic = ScriptedProvider("stub-basic")
    rb = basic.execute(_research_req(), box)
    assert "14 March 2027" not in rb.text


def test_basic_tier_rounding_breaks_decimal_compute():
    reg = default_registry()
    tools = {"data_lookup": DataLookupTool(METRIC_ROWS),
             "calculator": CalculatorTool()}
    box = ToolBox.build(reg.get("analyst").tools, tools)
    req = StepRequest(agent="analyst", skill="compute", instruction="net",
                      params={"label": "net", "expression": "100.50 * (1 - 0.175)"})
    assert ScriptedProvider("stub-pro").execute(req, box).text == "net = 82.91"
    assert ScriptedProvider("stub-basic").execute(req, box).text != "net = 82.91"


def test_provider_failure_injection():
    down = ScriptedProvider("stub-pro", fail_agents={"scout"})
    try:
        down.execute(_research_req(), ToolBox({}))
        raise AssertionError("down agent did not raise")
    except ProviderError:
        pass
    flaky = ScriptedProvider("stub-pro", flaky={"scout": 1})
    box = ToolBox.build(("doc_search",), {"doc_search": DocSearchTool(CHUNKS)})
    try:
        flaky.execute(_research_req(), box)
        raise AssertionError("flaky agent did not raise once")
    except ProviderError:
        pass
    assert "14 March 2027" in flaky.execute(_research_req(), box).text


# ----------------------------------------------------------------- runtime
def test_end_to_end_quick_task():
    task = _task("quick-units")
    rec = _runtime().run(DynamicPlanner().plan(task, default_registry()))
    ok, _missing = check_task(task, rec.final_output)
    assert rec.status == "completed" and ok
    assert rec.total_tokens == sum(t.tokens_in + t.tokens_out for t in rec.traces)
    assert abs(rec.total_cost_usd -
               round(sum(t.cost_usd for t in rec.traces), 6)) < 1e-9


def test_approval_gate_deny_and_approve():
    task = _task("publish-brief")
    plan = DynamicPlanner().plan(task, default_registry())
    denied = _runtime().run(plan, approver=lambda step: False)
    assert denied.status == "completed_with_denials"
    assert any(t.status == "denied" and t.step_id == "publish"
               for t in denied.traces)
    approved = _runtime().run(plan, approver=lambda step: True)
    assert approved.status == "completed"
    assert os.path.exists(os.path.join(TMP, "publish-brief.md"))


def test_budget_cap_blocks_runaway():
    task = runaway_task(30)
    plan = DynamicPlanner().plan(task, default_registry())
    rec = _runtime().run(plan, budget_tokens=6000)
    assert rec.status == "budget_exceeded"
    assert rec.steps_executed < len(plan.steps)
    assert any(t.status == "budget_blocked" for t in rec.traces)


def test_replan_recovers_when_primary_down():
    providers = {"stub-pro": ScriptedProvider("stub-pro", fail_agents={"scout"}),
                 "stub-basic": ScriptedProvider("stub-basic")}
    task = _task("brief-ops")
    plan = DynamicPlanner().plan(task, default_registry())
    rec = _runtime(providers=providers).run(plan)
    assert rec.status == "completed" and rec.replans
    ok, _ = check_task(task, rec.final_output)
    assert ok
    rec2 = _runtime(providers={"stub-pro": ScriptedProvider(
        "stub-pro", fail_agents={"scout"}),
        "stub-basic": ScriptedProvider("stub-basic")}).run(plan, allow_replan=False)
    assert rec2.status == "failed"


def test_run_record_json_roundtrip():
    task = _task("quick-north-revenue")
    rec = _runtime().run(DynamicPlanner().plan(task, default_registry()))
    path = os.path.join(TMP, "record.json")
    rec.save(path)
    loaded = json.load(open(path))
    assert loaded["task_id"] == "quick-north-revenue"
    assert loaded["plan_hash"] == rec.plan_hash


def test_provider_swap_same_plan():
    task = _task("analysis-net-revenue")
    plan = DynamicPlanner().plan(task, default_registry())
    basic = _runtime(provider_override=ScriptedProvider("stub-basic")).run(plan)
    pro = _runtime(provider_override=ScriptedProvider("stub-pro")).run(plan)
    assert basic.plan_hash == pro.plan_hash == plan.plan_hash()
    assert pro.success is None and basic.status == "completed"
    ok_pro, _ = check_task(task, pro.final_output)
    ok_basic, _ = check_task(task, basic.final_output)
    assert ok_pro and not ok_basic
    assert basic.total_cost_usd < pro.total_cost_usd


def test_agent_for_picks_first_declared():
    assert _agent_for(default_registry(), "compute") == "analyst"
    assert _agent_for(default_registry(), "research") == "scout"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    for name, fn in fns:
        fn()
        print(f"ok {name}")
    print(f"{len(fns)} smoke tests passed")
