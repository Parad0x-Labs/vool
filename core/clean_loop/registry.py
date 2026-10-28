"""Plug-in seams for the clean local loop.

Research wins plug in here by registering, never by editing the loop:

* a **tool** is a short name the model may call, a one-line doc shown in the prompt, and a handler
  that returns a `ToolOutcome`. Every built-in handler crosses VOOL's authorized execution boundary;
  a registered tool must too (or, like `propose_change`, execute nothing at all).
* a **pre-step** runs before the first model call and may stage read-only evidence for the model
  (a future "tray"). It returns text or None.
* an **answer gate** sees the final answer and the loop trace and may rewrite the answer (a future
  receipt gate). It returns the answer to serve.

A `Registry` is a plain object, so a test or an experiment can build its own and hand it to the
loop; `default_registry()` is the one the runtime uses.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolOutcome:
    """What one tool call gave the model, and what it ran underneath."""

    text: str
    ok: bool = True
    status: str = "executed"
    intents: tuple[str, ...] = ()
    proposal: dict[str, Any] | None = None


@dataclass
class ToolContext:
    """Per-turn state the handlers share. `source_context` is the runtime's own turn context."""

    home: Path
    source_context: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    proposals: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    # The authorized boundary; replaceable only so tests can observe calls without a runtime.
    execute: Callable[..., Any] | None = None


ToolHandler = Callable[[dict[str, Any], ToolContext], ToolOutcome]
PreStep = Callable[[str, ToolContext], "str | None"]
AnswerGate = Callable[[str, Any], str]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    doc: str
    run: ToolHandler


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._pre_steps: list[tuple[str, PreStep]] = []
        self._answer_gates: list[tuple[str, AnswerGate]] = []
        self._lock = threading.Lock()

    def register_tool(self, name: str, doc: str, run: ToolHandler) -> None:
        clean = str(name or "").strip()
        if not clean or clean == "answer":
            raise ValueError("a tool needs a name, and 'answer' is reserved")
        with self._lock:
            self._tools[clean] = ToolSpec(clean, str(doc or "").strip(), run)

    def register_pre_step(self, name: str, step: PreStep) -> None:
        with self._lock:
            self._pre_steps = [item for item in self._pre_steps if item[0] != name] + [(name, step)]

    def register_answer_gate(self, name: str, gate: AnswerGate) -> None:
        with self._lock:
            self._answer_gates = [item for item in self._answer_gates if item[0] != name] + [(name, gate)]

    def tool(self, name: str) -> ToolSpec | None:
        return self._tools.get(str(name or "").strip())

    def tools(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools.values())

    def pre_steps(self) -> tuple[tuple[str, PreStep], ...]:
        return tuple(self._pre_steps)

    def answer_gates(self) -> tuple[tuple[str, AnswerGate], ...]:
        return tuple(self._answer_gates)


_DEFAULT: Registry | None = None
_DEFAULT_LOCK = threading.Lock()


def default_registry() -> Registry:
    """The runtime's registry, with the built-in tools installed once."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            from core.clean_loop.tools import install_builtin_tools

            registry = Registry()
            install_builtin_tools(registry)
            _DEFAULT = registry
        return _DEFAULT


__all__ = [
    "AnswerGate",
    "PreStep",
    "Registry",
    "ToolContext",
    "ToolHandler",
    "ToolOutcome",
    "ToolSpec",
    "default_registry",
]
