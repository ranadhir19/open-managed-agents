"""Deterministic, offline tools available to managed agents.

Every tool is safe and reproducible: the calculator evaluates arithmetic
through an AST whitelist (never ``eval``), the document search and data
lookup run over a seeded corpus bundled with the package, and the file
writer is confined to a sandbox directory it cannot escape.
"""

from __future__ import annotations

import ast
import math
import os
import re
from dataclasses import dataclass, field


class ToolError(Exception):
    """Raised when a tool is misused or asked to do something unsafe."""


# --------------------------------------------------------------------------
# Calculator
# --------------------------------------------------------------------------

_BIN_OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
            ast.Mod: lambda a, b: a % b, ast.Pow: lambda a, b: a ** b}
_UNARY_OPS = {ast.UAdd: lambda a: +a, ast.USub: lambda a: -a}
_FUNCS = {"round": round, "abs": abs, "min": min, "max": max,
          "sqrt": math.sqrt, "log": math.log, "exp": math.exp}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ToolError(f"non-numeric constant: {node.value!r}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS:
        return float(_FUNCS[node.func.id](*[_eval_node(a) for a in node.args]))
    raise ToolError(f"disallowed expression element: {type(node).__name__}")


class CalculatorTool:
    """Safe arithmetic over an AST whitelist."""

    name = "calculator"

    def run(self, expression: str) -> float:
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ToolError(f"unparseable expression: {expression!r}") from exc
        return _eval_node(tree)


# --------------------------------------------------------------------------
# Document search over the seeded corpus
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class Chunk:
    doc_id: str
    title: str
    text: str


class DocSearchTool:
    """Keyword-overlap search over corpus chunks (deterministic ranking)."""

    name = "doc_search"

    def __init__(self, chunks: list[Chunk]):
        self._chunks = chunks
        self._chunk_tokens = [set(_tokens(c.text)) for c in chunks]

    def search(self, query: str, k: int = 3) -> list[Chunk]:
        q = set(_tokens(query))
        scored = []
        for idx, ctoks in enumerate(self._chunk_tokens):
            overlap = len(q & ctoks)
            if overlap:
                scored.append((-overlap, idx))
        scored.sort()
        return [self._chunks[i] for _neg, i in scored[:k]]


# --------------------------------------------------------------------------
# Structured data lookup over the seeded metrics table
# --------------------------------------------------------------------------

class DataLookupTool:
    """Aggregates over a seeded metrics table (list of dict rows)."""

    name = "data_lookup"

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def query(self, metric: str, region: str | None = None,
              month: str | None = None, agg: str = "sum") -> float:
        vals = [float(r[metric]) for r in self._rows
                if (region is None or r["region"] == region)
                and (month is None or r["month"] == month)]
        if not vals:
            raise ToolError(f"no rows for metric={metric} region={region} month={month}")
        if agg == "sum":
            return float(sum(vals))
        if agg == "avg":
            return float(sum(vals) / len(vals))
        if agg == "max":
            return float(max(vals))
        if agg == "min":
            return float(min(vals))
        raise ToolError(f"unknown aggregation: {agg}")


# --------------------------------------------------------------------------
# Sandbox file writer
# --------------------------------------------------------------------------

class FileWriterTool:
    """Writes text files strictly inside a sandbox directory."""

    name = "file_writer"

    def __init__(self, sandbox_dir: str):
        self.sandbox_dir = os.path.abspath(sandbox_dir)
        os.makedirs(self.sandbox_dir, exist_ok=True)

    def run(self, filename: str, content: str) -> str:
        target = os.path.abspath(os.path.join(self.sandbox_dir, filename))
        if os.path.commonpath([target, self.sandbox_dir]) != self.sandbox_dir:
            raise ToolError(f"path escapes sandbox: {filename!r}")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content)
        return target


# --------------------------------------------------------------------------
# Tool box: the per-agent filtered view the runtime hands to a provider
# --------------------------------------------------------------------------

@dataclass
class ToolBox:
    """Only the tools an agent is declared to be allowed to use."""

    _tools: dict = field(default_factory=dict)

    @classmethod
    def build(cls, allowed: tuple[str, ...], available: dict) -> "ToolBox":
        missing = [t for t in allowed if t not in available]
        if missing:
            raise ToolError(f"tools not available: {missing}")
        return cls({name: available[name] for name in allowed})

    def get(self, name: str):
        if name not in self._tools:
            raise ToolError(f"tool not allowed for this agent: {name}")
        return self._tools[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))
