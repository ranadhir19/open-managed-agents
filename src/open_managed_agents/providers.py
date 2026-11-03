"""Model providers: the model-agnostic seam of the platform.

A *provider* is the only thing that knows how a step gets executed.
Workflows, plans and the runtime never name a model vendor; they name a
provider. Two families ship here:

- ``ScriptedProvider`` — deterministic, offline, seeded stand-ins in two
  capability tiers (``stub-basic`` and ``stub-pro``). They execute each
  skill with explicit strategies over the real tools (document search,
  calculator, data lookup), so the whole platform runs — and every
  experiment reproduces — with no API keys. The tier differences are
  strategy differences (how many chunks get retrieved, whether
  intermediate arithmetic is rounded, how synthesis selects content),
  not coin flips.

- ``OpenAIProvider`` / ``AnthropicProvider`` — thin REST adapters used
  only when the matching API key is in the environment. They run steps
  in *direct-completion* mode (the model receives the step instruction
  plus gathered context and answers in one completion; they do not
  drive the tool routines). They are never constructed by the demo,
  the tests or the experiments unless a key is present.

Token counts are a deterministic function of text length
(``chars // 4``), so accounting is reproducible offline.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field

from .tools import ToolBox, ToolError


class ProviderError(Exception):
    """A provider failed to execute a step (retryable by the runtime)."""


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


# Simulated per-skill latency in seconds (what a hosted model round-trip
# plus tool time feels like at this scale). Deterministic per skill.
SKILL_LATENCY = {"research": 0.22, "lookup": 0.08, "compute": 0.12,
                 "synthesize": 0.18, "write": 0.10, "publish": 0.05}

# Deterministic per-skill token estimate used by the budget governor
# when reserving spend before a step starts.
SKILL_EST_TOKENS = {"research": 900, "lookup": 300, "compute": 500,
                    "synthesize": 1200, "write": 800, "publish": 200}


@dataclass
class StepRequest:
    agent: str
    skill: str
    instruction: str
    context: dict = field(default_factory=dict)   # key -> text from prior steps
    params: dict = field(default_factory=dict)    # structured step parameters


@dataclass
class StepResult:
    text: str
    tokens_in: int
    tokens_out: int
    latency_s: float
    data: dict = field(default_factory=dict)


class ScriptedProvider:
    """Deterministic offline provider with a named capability tier."""

    # price per 1K tokens (USD): stub-basic mimics a small hosted model
    # (~$0.20/$0.40 per 1M), stub-pro a frontier model (~$3/$15 per 1M)
    PRICES = {"stub-basic": (0.0002, 0.0004), "stub-pro": (0.003, 0.015)}

    def __init__(self, tier: str = "stub-pro", fail_agents=frozenset(),
                 flaky: dict | None = None):
        if tier not in self.PRICES:
            raise ValueError(f"unknown tier: {tier}")
        self.name = tier
        self.tier = tier
        self.price_in, self.price_out = self.PRICES[tier]
        self._fail_agents = set(fail_agents)       # agents that always error
        self._flaky = dict(flaky or {})            # agent -> failures before success
        self._attempts: dict[str, int] = {}

    # -- failure injection (used by the recovery experiment) --------------
    def _maybe_fail(self, req: StepRequest):
        if req.agent in self._fail_agents:
            raise ProviderError(f"agent {req.agent} is down (injected)")
        left = self._flaky.get(req.agent, 0)
        if left > 0:
            self._flaky[req.agent] = left - 1
            raise ProviderError(f"agent {req.agent} transient failure (injected)")

    # -- skill strategies ---------------------------------------------------
    def _research(self, req: StepRequest, box: ToolBox) -> StepResult:
        search = box.get("doc_search")
        k = 1 if self.tier == "stub-basic" else 3
        chunks = search.search(req.instruction, k=k)
        text = "\n".join(f"[{c.doc_id}] {c.text}" for c in chunks)
        return self._finish(req, text, {"chunks": [c.doc_id for c in chunks]})

    def _lookup(self, req: StepRequest, box: ToolBox) -> StepResult:
        lookup = box.get("data_lookup")
        p = req.params
        value = lookup.query(p["metric"], region=p.get("region"),
                             month=p.get("month"), agg=p.get("agg", "sum"))
        label = p.get("label", p["metric"])
        text = f"{label} = {value:.2f}"
        return self._finish(req, text, {"value": value, "label": label})

    def _compute(self, req: StepRequest, box: ToolBox) -> StepResult:
        calc = box.get("calculator")
        expr = req.params["expression"]
        if self.tier == "stub-basic":
            # basic tier rounds every literal operand to an integer first —
            # a real, deterministic precision loss on decimal arithmetic.
            expr = re.sub(r"\d+\.\d+", lambda m: str(round(float(m.group()))), expr)
        value = calc.run(expr)
        label = req.params.get("label", "result")
        text = f"{label} = {value:.2f}"
        return self._finish(req, text, {"value": value, "label": label})

    def _synthesize(self, req: StepRequest, box: ToolBox) -> StepResult:
        parts = [req.context[k] for k in sorted(req.context)]
        joined = "\n".join(parts)
        if self.tier == "stub-basic":
            # basic tier keeps only the head of the gathered material.
            text = joined[:400]
        else:
            # pro tier keeps every sentence that carries a fact: a number
            # or a bracketed source tag, in original order.
            sentences = re.split(r"(?<=[.!?])\s+", joined.replace("\n", " "))
            keep = [s for s in sentences
                    if re.search(r"\d", s) or s.strip().startswith("[")]
            text = " ".join(keep) if keep else joined[:400]
        return self._finish(req, text, {})

    def _write(self, req: StepRequest, box: ToolBox) -> StepResult:
        title = req.params.get("title", "Report")
        body = "\n\n".join(f"## {k}\n{req.context[k]}" for k in sorted(req.context))
        text = f"# {title}\n\n{body}\n"
        return self._finish(req, text, {})

    def _publish(self, req: StepRequest, box: ToolBox) -> StepResult:
        writer = box.get("file_writer")
        content = "\n\n".join(req.context[k] for k in sorted(req.context))
        path = writer.run(req.params["filename"], content)
        text = f"published to {path}"
        return self._finish(req, text, {"path": path})

    # -- plumbing -----------------------------------------------------------
    def _finish(self, req: StepRequest, text: str, data: dict) -> StepResult:
        ctx_text = "\n".join(req.context.values())
        return StepResult(text=text,
                          tokens_in=_tokens(req.instruction + ctx_text),
                          tokens_out=_tokens(text),
                          latency_s=SKILL_LATENCY[req.skill],
                          data=data)

    def execute(self, req: StepRequest, box: ToolBox) -> StepResult:
        self._attempts[req.agent] = self._attempts.get(req.agent, 0) + 1
        self._maybe_fail(req)
        handler = {"research": self._research, "lookup": self._lookup,
                   "compute": self._compute, "synthesize": self._synthesize,
                   "write": self._write, "publish": self._publish}.get(req.skill)
        if handler is None:
            raise ProviderError(f"scripted provider cannot do skill {req.skill!r}")
        try:
            return handler(req, box)
        except ToolError as exc:
            raise ProviderError(str(exc)) from exc

    def estimate_tokens(self, req: StepRequest) -> int:
        return SKILL_EST_TOKENS[req.skill]


# --------------------------------------------------------------------------
# Optional real-provider adapters (direct completion; key-gated)
# --------------------------------------------------------------------------

class _RESTProvider:
    """Base for hosted-model adapters. Never constructed without a key."""

    name = "rest"
    price_in = 0.0
    price_out = 0.0

    def __init__(self, model: str, api_key: str):
        self.model = model
        self._key = api_key

    def estimate_tokens(self, req: StepRequest) -> int:
        return SKILL_EST_TOKENS[req.skill]

    def _prompt(self, req: StepRequest) -> str:
        ctx = "\n\n".join(f"[{k}]\n{v}" for k, v in sorted(req.context.items()))
        return (f"You are a managed agent performing one workflow step.\n"
                f"Skill: {req.skill}\nInstruction: {req.instruction}\n\n"
                f"Context from previous steps:\n{ctx}\n\n"
                f"Return only the step output.")

    def execute(self, req: StepRequest, box: ToolBox) -> StepResult:  # pragma: no cover
        raise NotImplementedError

    def _post(self, url: str, payload: dict, headers: dict) -> dict:  # pragma: no cover
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())


class OpenAIProvider(_RESTProvider):  # pragma: no cover - key-gated
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None):
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderError("OPENAI_API_KEY is not set")
        super().__init__(model, key)
        self.price_in, self.price_out = 0.00015, 0.0006  # USD per 1K tokens

    def execute(self, req: StepRequest, box: ToolBox) -> StepResult:
        import time
        t0 = time.time()
        out = self._post("https://api.openai.com/v1/chat/completions",
                         {"model": self.model,
                          "messages": [{"role": "user", "content": self._prompt(req)}]},
                         {"Authorization": f"Bearer {self._key}"})
        text = out["choices"][0]["message"]["content"]
        usage = out.get("usage", {})
        return StepResult(text=text,
                          tokens_in=usage.get("prompt_tokens", _tokens(req.instruction)),
                          tokens_out=usage.get("completion_tokens", _tokens(text)),
                          latency_s=time.time() - t0, data={})


class AnthropicProvider(_RESTProvider):  # pragma: no cover - key-gated
    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-5", api_key: str | None = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        super().__init__(model, key)
        self.price_in, self.price_out = 0.003, 0.015  # USD per 1K tokens

    def execute(self, req: StepRequest, box: ToolBox) -> StepResult:
        import time
        t0 = time.time()
        out = self._post("https://api.anthropic.com/v1/messages",
                         {"model": self.model, "max_tokens": 1024,
                          "messages": [{"role": "user", "content": self._prompt(req)}]},
                         {"x-api-key": self._key, "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in out.get("content", []))
        usage = out.get("usage", {})
        return StepResult(text=text,
                          tokens_in=usage.get("input_tokens", _tokens(req.instruction)),
                          tokens_out=usage.get("output_tokens", _tokens(text)),
                          latency_s=time.time() - t0, data={})


def provider_from_env(name: str):  # pragma: no cover - key-gated convenience
    """Build a provider by name; scripted tiers need no key."""
    if name in ScriptedProvider.PRICES:
        return ScriptedProvider(name)
    if name == "openai":
        return OpenAIProvider()
    if name == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"unknown provider: {name}")
