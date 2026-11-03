"""The compiler: a ``WorkflowPlan`` becomes a LangGraph ``StateGraph``.

Compilation happens at runtime, per plan — there is no hand-written
graph anywhere in the platform. Each plan step becomes one node;
dependencies become edges; independent steps land in the same
superstep and execute concurrently (async nodes). With
``sequential=True`` the compiler adds chain edges layer by layer,
forcing one step at a time — the forced-sequential baseline the
parallelism experiment measures against.

Step execution itself is injected by the runtime (``step_fn``), so the
compiled graph knows nothing about providers, budgets or gates.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .workflow import WorkflowPlan


class _State(TypedDict):
    results: Annotated[dict, lambda a, b: {**a, **b}]
    traces: Annotated[list, operator.add]


def compile_plan(plan: WorkflowPlan, step_fn, sequential: bool = False):
    """Compile ``plan`` to an executable LangGraph graph.

    ``step_fn(step, state)`` is an async callable returning
    ``{"results": {step_id: result}, "traces": [trace, ...]}``.
    """
    plan.validate()
    builder = StateGraph(_State)
    for step in plan.steps:
        builder.add_node(step.id, _node(step_fn, step))
    depended_upon = set()
    for step in plan.steps:
        for dep in step.depends_on:
            builder.add_edge(dep, step.id)
            depended_upon.add(step.id)
    roots = [s.id for s in plan.steps if not s.depends_on]
    for r in roots:
        builder.add_edge(START, r)
    leaves = [s.id for s in plan.steps if s.id not in
              {d for s in plan.steps for d in s.depends_on}]
    for leaf in leaves:
        builder.add_edge(leaf, END)
    if sequential:
        prev = None
        for layer in plan.layers():
            for step in layer:
                if prev is not None:
                    builder.add_edge(prev, step.id)
                prev = step.id
    return builder.compile()


def _node(step_fn, step):
    async def run(state: _State):
        return await step_fn(step, state)
    run.__name__ = f"node_{step.id}"
    return run
