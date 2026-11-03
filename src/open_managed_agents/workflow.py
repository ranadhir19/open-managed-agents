"""Workflow plans: the DAG the planner composes and the compiler runs.

A plan is pure data (JSON-serialisable), which is what makes the
provider-swap experiment meaningful: the plan's canonical hash is
identical no matter which provider executes it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict


@dataclass
class PlanStep:
    id: str
    agent: str
    skill: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    params: dict = field(default_factory=dict)
    optional: bool = False        # a failed optional step may be skipped on replan
    sensitive: bool = False       # resolved at plan time from the agent's gate list

    def to_dict(self) -> dict:
        d = asdict(self)
        d["depends_on"] = list(self.depends_on)
        return d


@dataclass
class WorkflowPlan:
    task_id: str
    goal: str
    steps: list[PlanStep]
    origin: str = "dynamic"       # "dynamic" | "static" | "replan"

    def step(self, step_id: str) -> PlanStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def validate(self) -> None:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step ids")
        known = set(ids)
        for s in self.steps:
            missing = [d for d in s.depends_on if d not in known]
            if missing:
                raise ValueError(f"step {s.id}: unknown dependencies {missing}")
        # cycle check via Kahn
        remaining = {s.id: set(s.depends_on) for s in self.steps}
        done = set()
        while remaining:
            ready = [i for i, deps in remaining.items() if deps <= done]
            if not ready:
                raise ValueError("plan has a dependency cycle")
            for i in ready:
                done.add(i)
                del remaining[i]

    def layers(self) -> list[list[PlanStep]]:
        """Topological layers; steps inside a layer are independent."""
        done, out, pending = set(), [], list(self.steps)
        while pending:
            layer = [s for s in pending if set(s.depends_on) <= done]
            if not layer:
                raise ValueError("plan has a dependency cycle")
            out.append(layer)
            done |= {s.id for s in layer}
            pending = [s for s in pending if s.id not in done]
        return out

    def canonical(self) -> str:
        payload = {"task_id": self.task_id, "goal": self.goal,
                   "steps": [s.to_dict() for s in self.steps]}
        return json.dumps(payload, sort_keys=True)

    def plan_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "goal": self.goal,
                "origin": self.origin,
                "steps": [s.to_dict() for s in self.steps]}
