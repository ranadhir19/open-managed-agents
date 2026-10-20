"""open-managed-agents: a model-agnostic managed-agent platform.

Agents are declared in a registry; a planner composes a workflow DAG
for each task; the DAG is compiled to a LangGraph StateGraph at
runtime and executed under budget governance, human-approval gates,
and retry/replan failure handling — with any model provider behind a
small interface, including deterministic offline scripted providers.
"""

from .compiler import compile_plan
from .corpus import TaskSpec, check_task, runaway_task, task_suite
from .planner import DynamicPlanner, StaticPlanner, replan_step
from .providers import (AnthropicProvider, OpenAIProvider, ProviderError,
                        ScriptedProvider, StepRequest, StepResult,
                        provider_from_env)
from .registry import AgentRegistry, AgentSpec, default_registry
from .runtime import ManagedRuntime, RunRecord, StepTrace
from .workflow import PlanStep, WorkflowPlan

__version__ = "0.1.0"

__all__ = [
    "AgentRegistry", "AgentSpec", "default_registry",
    "WorkflowPlan", "PlanStep", "compile_plan",
    "DynamicPlanner", "StaticPlanner", "replan_step",
    "ManagedRuntime", "RunRecord", "StepTrace",
    "ScriptedProvider", "OpenAIProvider", "AnthropicProvider",
    "ProviderError", "StepRequest", "StepResult", "provider_from_env",
    "TaskSpec", "task_suite", "runaway_task", "check_task",
]
