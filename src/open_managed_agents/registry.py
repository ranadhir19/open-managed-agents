"""The agent registry: agents are *declared*, not coded.

An ``AgentSpec`` is pure data — name, role, instructions, the skills it
can perform, the tools it is allowed to touch, which provider should run
it, and which of its skills require a human-approval gate. The planner
composes workflows out of whatever the registry declares; adding an
agent is adding a record, never editing the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SKILLS = ("research", "lookup", "compute", "synthesize", "write", "publish")


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    instructions: str
    skills: tuple[str, ...]
    tools: tuple[str, ...]
    provider: str = "stub-pro"               # provider preference
    approval_skills: tuple[str, ...] = ()    # skills gated on human approval
    max_tokens_per_run: int | None = None    # per-agent budget cap (tokens)

    def __post_init__(self):
        bad = [s for s in self.skills if s not in SKILLS]
        if bad:
            raise ValueError(f"agent {self.name}: unknown skills {bad}")
        bad = [s for s in self.approval_skills if s not in self.skills]
        if bad:
            raise ValueError(f"agent {self.name}: approval skills not in skills {bad}")


@dataclass
class AgentRegistry:
    _agents: dict = field(default_factory=dict)

    def register(self, spec: AgentSpec) -> AgentSpec:
        if spec.name in self._agents:
            raise ValueError(f"duplicate agent: {spec.name}")
        self._agents[spec.name] = spec
        return spec

    def get(self, name: str) -> AgentSpec:
        if name not in self._agents:
            raise KeyError(f"unknown agent: {name}")
        return self._agents[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._agents))

    def with_skill(self, skill: str, exclude: tuple[str, ...] = ()) -> list[AgentSpec]:
        """Agents that can perform ``skill``, in registration order."""
        return [a for a in self._agents.values()
                if skill in a.skills and a.name not in exclude]

    def __len__(self):
        return len(self._agents)


def default_registry() -> AgentRegistry:
    """The demo fleet: two researchers (primary + backup), an analyst,
    and a writer whose publish skill is human-gated."""
    reg = AgentRegistry()
    reg.register(AgentSpec(
        name="scout", role="researcher",
        instructions="Retrieve the document chunks that answer a question.",
        skills=("research", "lookup"), tools=("doc_search", "data_lookup"),
        provider="stub-pro"))
    reg.register(AgentSpec(
        name="scout-backup", role="researcher (backup)",
        instructions="Backup researcher; same tools, cheaper provider tier.",
        skills=("research", "lookup"), tools=("doc_search", "data_lookup"),
        provider="stub-basic"))
    reg.register(AgentSpec(
        name="analyst", role="data analyst",
        instructions="Look up metrics and compute exact figures from them.",
        skills=("lookup", "compute"), tools=("data_lookup", "calculator"),
        provider="stub-pro"))
    reg.register(AgentSpec(
        name="writer", role="report writer",
        instructions="Synthesize gathered material into a report and publish it.",
        skills=("synthesize", "write", "publish"),
        tools=("file_writer",), provider="stub-pro",
        approval_skills=("publish",)))
    return reg
