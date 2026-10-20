"""Planners: turning a task into a workflow DAG.

The ``DynamicPlanner`` composes a plan from the task's declared needs
and the registry's declared skills: one research step per sub-question
(so independent questions become parallel branches), lookups feeding
computations, a synthesis step over everything gathered, then write
and (optionally) publish. It is a deterministic capability-matching
composer — a stand-in for an LLM planner, behind the same interface:
anything that returns a ``WorkflowPlan`` can plan here.

The ``StaticPlanner`` is the baseline: the same four stages in the
same order for every task (research -> compute -> synthesize ->
write), whether the task needs them or not, covering only the first
sub-question and the first computation.

``replan_step`` is the recovery move: re-issue a failed step against
a *different* agent with the same skill, or drop it if it was
optional.
"""

from __future__ import annotations

from .corpus import TaskSpec
from .registry import AgentRegistry
from .workflow import PlanStep, WorkflowPlan


def _agent_for(reg: AgentRegistry, skill: str, exclude=()) -> str:
    cands = reg.with_skill(skill, exclude=tuple(exclude))
    if not cands:
        raise ValueError(f"no agent in registry can perform skill {skill!r}")
    return cands[0].name


def _mk(reg: AgentRegistry, step_id: str, skill: str, instruction: str,
        depends_on=(), params=None, optional=False, agent=None) -> PlanStep:
    agent = agent or _agent_for(reg, skill)
    spec = reg.get(agent)
    return PlanStep(id=step_id, agent=agent, skill=skill,
                    instruction=instruction, depends_on=tuple(depends_on),
                    params=params or {}, optional=optional,
                    sensitive=skill in spec.approval_skills)


class DynamicPlanner:
    name = "dynamic"

    def plan(self, task: TaskSpec, reg: AgentRegistry) -> WorkflowPlan:
        steps: list[PlanStep] = []
        gather: list[str] = []
        for i, q in enumerate(task.sub_questions):
            sid = f"research_{i}"
            steps.append(_mk(reg, sid, "research", q))
            gather.append(sid)
        for i, lk in enumerate(task.lookups):
            sid = f"lookup_{i}"
            steps.append(_mk(reg, sid, "lookup",
                             f"Look up {lk.get('label', lk['metric'])}",
                             params=dict(lk)))
            gather.append(sid)
        for i, comp in enumerate(task.computations):
            sid = f"compute_{i}"
            deps = tuple(comp["inputs"].values())
            steps.append(_mk(reg, sid, "compute",
                             f"Compute {comp['label']}",
                             depends_on=deps,
                             params={"label": comp["label"],
                                     "template": comp["template"],
                                     "inputs": comp["inputs"]}))
            gather.append(sid)
        tail = gather
        if len(gather) >= 2:
            steps.append(_mk(reg, "synthesize", "synthesize",
                             f"Synthesize material for: {task.goal}",
                             depends_on=tuple(gather)))
            tail = ["synthesize"]
        steps.append(_mk(reg, "write", "write", f"Write the deliverable: {task.goal}",
                         depends_on=tuple(tail),
                         params={"title": task.goal}))
        if task.publish:
            steps.append(_mk(reg, "publish", "publish", "Publish the deliverable",
                             depends_on=("write",),
                             params={"filename": f"{task.id}.md"}))
        plan = WorkflowPlan(task_id=task.id, goal=task.goal, steps=steps,
                            origin="dynamic")
        plan.validate()
        return plan


class StaticPlanner:
    """Fixed pipeline for every task (the baseline): research only the
    first sub-question, run the lookups, compute only the first
    computation, then synthesize -> write. The stages never adapt to
    what the task actually needs."""

    name = "static"

    def plan(self, task: TaskSpec, reg: AgentRegistry) -> WorkflowPlan:
        steps: list[PlanStep] = []
        q = task.sub_questions[0] if task.sub_questions else task.goal
        steps.append(_mk(reg, "research_0", "research", q))
        lookup_ids = []
        for i, lk in enumerate(task.lookups):
            sid = f"lookup_{i}"
            steps.append(_mk(reg, sid, "lookup",
                             f"Look up {lk.get('label', lk['metric'])}",
                             params=dict(lk)))
            lookup_ids.append(sid)
        if task.computations:
            comp = task.computations[0]
            deps = tuple(comp["inputs"].values()) or ("research_0",)
            steps.append(_mk(reg, "compute_0", "compute", f"Compute {comp['label']}",
                             depends_on=deps,
                             params={"label": comp["label"],
                                     "template": comp["template"],
                                     "inputs": comp["inputs"]}))
        else:
            steps.append(_mk(reg, "compute_0", "compute", "Compute n/a",
                             depends_on=("research_0",),
                             params={"label": "n/a", "template": "0 + 0",
                                     "inputs": {}}))
        synth_deps = tuple(["research_0"] + lookup_ids + ["compute_0"])
        steps.append(_mk(reg, "synthesize", "synthesize",
                         f"Synthesize material for: {task.goal}",
                         depends_on=synth_deps))
        steps.append(_mk(reg, "write", "write", f"Write the deliverable: {task.goal}",
                         depends_on=("synthesize",),
                         params={"title": task.goal}))
        if task.publish:
            steps.append(_mk(reg, "publish", "publish", "Publish the deliverable",
                             depends_on=("write",),
                             params={"filename": f"{task.id}.md"}))
        plan = WorkflowPlan(task_id=task.id, goal=task.goal, steps=steps,
                            origin="static")
        plan.validate()
        return plan


def replan_step(failed: PlanStep, reg: AgentRegistry,
                completed: dict) -> PlanStep | None:
    """Recovery plan for one failed step: same step, different agent.

    Returns ``None`` when the step is optional (skip it) or no other
    agent can perform the skill (unrecoverable). Dependencies already
    satisfied by ``completed`` results are dropped.
    """
    if failed.optional:
        return None
    cands = reg.with_skill(failed.skill, exclude=(failed.agent,))
    if not cands:
        return None
    deps = tuple(d for d in failed.depends_on if d not in completed)
    return PlanStep(id=failed.id, agent=cands[0].name, skill=failed.skill,
                    instruction=failed.instruction, depends_on=deps,
                    params=dict(failed.params), optional=failed.optional,
                    sensitive=failed.skill in cands[0].approval_skills)
