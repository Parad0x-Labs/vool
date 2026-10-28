"""The step loop: one JSON action per model call, a hard step cap, nothing else.

Each step the brain returns text; `parse_action` pulls the first JSON object out of it (prose,
code fences and `<think>` blocks around it are tolerated). `{"tool": ...}` runs that tool through
the registry and feeds the result back; `{"answer": ...}` (or plain prose with no JSON) ends the
turn. On the last allowed step the model is told to answer with what it has, so the loop never
serves a raw tool request as the reply.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.clean_loop.prompt import STEP_LIMIT_NUDGE, build_system_prompt
from core.clean_loop.registry import Registry, ToolContext, ToolOutcome

_LOG = logging.getLogger(__name__)

Brain = Callable[[list[dict[str, str]]], str]

DEFAULT_MAX_STEPS = 6
_TOOL_RESULT_CHARS = 3000
_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_DECODER = json.JSONDecoder()

LOOK_FIRST_NUDGE = (
    "You have not looked yet. If the answer depends on the owner's files or computer, call a tool now; "
    "otherwise give the same answer again."
)
REPEAT_NOTICE = "You already tried exactly that and it failed. Try a different word or folder, or answer with what you found."

GAVE_UP_TEXT = "I couldn't finish looking that up. Could you tell me a bit more about where it might be?"
NO_REPLY_TEXT = "I couldn't put an answer together just now. Please try asking again."


def parse_action(text: str) -> dict[str, Any]:
    """The first JSON object carrying "tool" or "answer"; else the prose as the answer."""
    raw = _THINK_RE.sub("", str(text or "")).strip()
    for match in re.finditer(r"\{", raw):
        try:
            value, _end = _DECODER.raw_decode(raw, match.start())
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        if isinstance(value.get("args"), dict) and "tool" in value:
            value = {"tool": value["tool"], **value["args"]}
        if isinstance(value.get("tool"), str) and value["tool"].strip():
            return {**value, "tool": value["tool"].strip()}
        if "answer" in value:
            return {"answer": str(value.get("answer") or "").strip()}
    prose = re.sub(r"```\w*", "", raw).strip()
    return {"answer": prose}


@dataclass
class LoopResult:
    answer: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    stopped: str = "answered"  # answered | max_steps | brain_error
    #: Every tool and evidence text this turn showed the model, for answer gates that check the
    #: answer against what was actually seen (exact file names).
    seen: list[str] = field(default_factory=list)

    @property
    def tools_used(self) -> list[str]:
        return [str(step["tool"]) for step in self.steps if step.get("tool")]

    def trace(self) -> dict[str, Any]:
        return {
            "lane": "clean_local_loop",
            "stopped": self.stopped,
            "model_calls": self.model_calls,
            "tools_used": self.tools_used,
            "steps": list(self.steps),
            "proposals": list(self.proposals),
        }


def _run_tool(registry: Registry, action: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    name = str(action.get("tool") or "")
    spec = registry.tool(name)
    if spec is None:
        names = ", ".join(s.name for s in registry.tools())
        return ToolOutcome(text=f"unknown tool '{name}'. Use one of: {names}", ok=False, status="unknown_tool")
    args = {k: v for k, v in action.items() if k != "tool"}
    try:
        return spec.run(args, ctx)
    except Exception as exc:  # a plug-in fault is a failed step, never a failed turn
        from core.clean_loop.tools import error_text

        _LOG.exception("clean loop: tool %s failed", name)
        return ToolOutcome(text=f"that did not work ({error_text(exc, ctx.home)})", ok=False, status="error")


def _staged_evidence(ask: str, registry: Registry, ctx: ToolContext) -> str:
    parts = []
    for name, step in registry.pre_steps():
        try:
            staged = step(ask, ctx)
        except Exception:
            staged = None
        if staged:
            parts.append(f"[{name}]\n{str(staged)[:_TOOL_RESULT_CHARS]}")
    return "\n\n".join(parts)


def run_loop(
    ask: str,
    *,
    brain: Brain,
    registry: Registry,
    ctx: ToolContext,
    history: Sequence[dict[str, str]] = (),
    max_steps: int = DEFAULT_MAX_STEPS,
) -> LoopResult:
    max_steps = max(1, int(max_steps))
    messages: list[dict[str, str]] = [{"role": "system", "content": build_system_prompt(registry)}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    staged = _staged_evidence(ask, registry, ctx)
    user = ask if not staged else f"{ask}\n\n[Evidence gathered before you (read-only):]\n{staged}"
    messages.append({"role": "user", "content": user})
    result = LoopResult(answer="")
    if staged:
        result.seen.append(staged)
    nudged = False
    failed: set[str] = set()
    for step in range(max_steps):
        last = step == max_steps - 1
        if last and result.steps:
            messages.append({"role": "user", "content": STEP_LIMIT_NUDGE})
        started = time.monotonic()
        try:
            text = brain(messages)
        except Exception as exc:
            result.model_calls += 1
            result.stopped = "brain_error"
            result.steps.append({"step": step, "error": type(exc).__name__})
            break
        result.model_calls += 1
        think_ms = int((time.monotonic() - started) * 1000)
        action = parse_action(text)
        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        if "tool" not in action:
            if not result.steps and not nudged and not staged and not last and registry.tools():
                # Half the small model's misses on fresh asks ran no tool at all (router hold-out: 7 of
                # 14): one reminder to look, once; a general answer simply comes back the same.
                nudged = True
                result.steps.append({"step": step, "nudge": "look_first", "think_ms": think_ms})
                messages.append({"role": "user", "content": LOOK_FIRST_NUDGE})
                continue
            result.answer = str(action.get("answer") or "").strip()
            break
        if last:
            result.stopped = "max_steps"
            break
        args = {k: v for k, v in action.items() if k != "tool"}
        key = json.dumps([action["tool"], args], sort_keys=True, ensure_ascii=False, default=str)
        started_tool = time.monotonic()
        if key in failed:
            # The same failing call five times burned every step (Mac audit F25).
            outcome = ToolOutcome(text=REPEAT_NOTICE, ok=False, status="repeated_failure")
        else:
            outcome = _run_tool(registry, action, ctx)
            if not outcome.ok:
                failed.add(key)
        result.steps.append(
            {
                "step": step,
                "tool": action["tool"],
                "args": args,
                "ok": outcome.ok,
                "status": outcome.status,
                "intents": list(outcome.intents),
                "result_chars": len(outcome.text),
                "think_ms": think_ms,
                "tool_ms": int((time.monotonic() - started_tool) * 1000),
            }
        )
        result.seen.append(outcome.text[:_TOOL_RESULT_CHARS])
        messages.append({"role": "user", "content": "[tool result]\n" + outcome.text[:_TOOL_RESULT_CHARS]})
    result.proposals = list(ctx.proposals)
    if not result.answer:
        result.answer = GAVE_UP_TEXT if result.stopped == "max_steps" else NO_REPLY_TEXT
    for _name, gate in registry.answer_gates():
        try:
            result.answer = str(gate(result.answer, result) or result.answer)
        except Exception:
            continue
    return result


__all__ = ["DEFAULT_MAX_STEPS", "GAVE_UP_TEXT", "LOOK_FIRST_NUDGE", "NO_REPLY_TEXT", "REPEAT_NOTICE", "LoopResult", "parse_action", "run_loop"]
