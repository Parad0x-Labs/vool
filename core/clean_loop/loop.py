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

# Nine looks and a forced answer. Six ran out on asks that needed a find, a list and two reads (R4-CL
# review 2026-10-31: R22 had the right files and stopped before reading them). Every step is read-only.
DEFAULT_MAX_STEPS = 10
_TOOL_RESULT_CHARS = 3000
_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_DECODER = json.JSONDecoder()

LOOK_FIRST_NUDGE = (
    "You have not looked yet. If the answer depends on the owner's files or computer, call a tool now; "
    "otherwise give the same answer again."
)
CONTENT_SEARCH_NUDGE = (
    "You have not finished searching document contents yet. Search inside the owner's files before "
    "saying the information was not found."
)
REPEAT_NOTICE = "You already tried exactly that and it failed. Try a different word or folder, or answer with what you found."

SEARCH_FIRST_NUDGE = (
    "You asked a question without looking. Call find now with two or three words from the owner's ask, "
    "then answer from what it returns."
)
NOTHING_FOUND_NUDGE = (
    "Your search found nothing. Tell the owner plainly that you found nothing for what they asked. "
    "Do not ask which file or folder they mean in place of saying so; one short question may follow the statement."
)

GAVE_UP_TEXT = "I couldn't finish looking that up. Could you tell me a bit more about where it might be?"
NO_REPLY_TEXT = "I couldn't put an answer together just now. Please try asking again."

#: The headings under which this loop hands its brain what it read on the owner's computer.
TOOL_RESULT_HEADING = "[tool result]"
STAGED_EVIDENCE_HEADING = "[Evidence gathered before you (read-only):]"
PRIOR_EVIDENCE_HEADING = "[Evidence from your previous clean-loop turn in this session (read-only):]"


def carries_owner_evidence(messages: Sequence[dict[str, str]]) -> bool:
    """Whether these messages hand the brain anything this loop read on the owner's computer.

    A cloud brain's step that does is a request carrying the owner's files, and the cloud
    disclosure boundary asks before it leaves (core.disclosure.wire), as for every other request.
    """
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if (content.startswith(TOOL_RESULT_HEADING) or STAGED_EVIDENCE_HEADING in content
                or PRIOR_EVIDENCE_HEADING in content):
            return True
    return False


class BoundaryStop(RuntimeError):
    """A step stopped at a boundary only the owner opens (the cloud disclosure preview): nothing was
    sent. Not a model failure, so the loop ends the turn with it instead of counting a brain error."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        #: Model calls this turn's loop completed before the stop (filled in by run_loop).
        self.model_calls = 0


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


def is_bare_question(text: str) -> bool:
    """Whether the text is only questions: every sentence ends in a question mark, none states anything."""
    parts = [p.strip() for p in re.findall(r"[^.!?]+[.!?]*", str(text or "")) if p.strip(" .!?")]
    return bool(parts) and all(p.endswith("?") for p in parts)


def _read_something(steps: Sequence[dict[str, Any]]) -> bool:
    """A tool step that returned content: ok, not an empty search, not an unfinished one."""
    return any(
        step.get("tool") and step.get("tool") != "propose_change" and step.get("ok") and not step.get("empty")
        and step.get("status") != "content_search_incomplete" and int(step.get("result_chars") or 0) > 0
        for step in steps
    )


@dataclass
class LoopResult:
    answer: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    stopped: str = "answered"  # answered | max_steps | brain_error
    #: Every model call's time, the final answer's included (per-step think_ms misses that one).
    think_ms: int = 0
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
    prior_evidence: str = "",
    max_steps: int = DEFAULT_MAX_STEPS,
) -> LoopResult:
    max_steps = max(1, int(max_steps))
    messages: list[dict[str, str]] = [{"role": "system", "content": build_system_prompt(registry)}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    staged = _staged_evidence(ask, registry, ctx)
    user = ask if not staged else f"{ask}\n\n{STAGED_EVIDENCE_HEADING}\n{staged}"
    if prior_evidence.strip():
        user += f"\n\n{PRIOR_EVIDENCE_HEADING}\n"
        user += prior_evidence[:1500]
    messages.append({"role": "user", "content": user})
    result = LoopResult(answer="")
    if staged:
        result.seen.append(staged)
    nudged = False
    question_nudged = False
    own_search_done = False
    absence_search_nudged = False
    failed: set[str] = set()
    for step in range(max_steps):
        last = step == max_steps - 1
        if last and result.steps:
            messages.append({"role": "user", "content": STEP_LIMIT_NUDGE})
        started = time.monotonic()
        try:
            text = brain(messages)
        except BoundaryStop as stop:
            stop.model_calls = result.model_calls
            raise
        except Exception as exc:
            result.model_calls += 1
            result.think_ms += int((time.monotonic() - started) * 1000)
            result.stopped = "brain_error"
            result.steps.append({"step": step, "error": type(exc).__name__})
            break
        result.model_calls += 1
        think_ms = int((time.monotonic() - started) * 1000)
        result.think_ms += think_ms
        action = parse_action(text)
        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        if "tool" not in action:
            from core.clean_loop.tools import content_absence_claim

            content_search_complete = any(
                item.get("tool") == "find" and item.get("status") == "content_search_complete"
                for item in result.steps
            )
            if (
                content_absence_claim(str(action.get("answer") or ""))
                and not content_search_complete
                and not absence_search_nudged
                and not last
            ):
                absence_search_nudged = True
                result.steps.append({"step": step, "nudge": "content_search_incomplete", "think_ms": think_ms})
                messages.append({"role": "user", "content": CONTENT_SEARCH_NUDGE})
                continue
            if not result.steps and not nudged and not staged and not last and registry.tools():
                # Half the small model's misses on fresh asks ran no tool at all (router hold-out: 7 of
                # 14): one reminder to look, once; a general answer simply comes back the same.
                nudged = True
                result.steps.append({"step": step, "nudge": "look_first", "think_ms": think_ms})
                messages.append({"role": "user", "content": LOOK_FIRST_NUDGE})
                continue
            candidate = str(action.get("answer") or "").strip()
            if is_bare_question(candidate) and not _read_something(result.steps) and not last and registry.tools():
                # A question is not an answer to an ask about the owner's files when nothing was read for it: either
                # nothing was searched, or the search found nothing and the model asked instead of saying so.
                looked = any(item.get("tool") for item in result.steps)
                unfinished = any(i.get("tool") == "find" and i.get("status") == "content_search_incomplete" for i in result.steps[-1:])
                if not question_nudged and not (nudged and not looked):
                    question_nudged = True
                    result.steps.append({"step": step, "nudge": "question_without_evidence", "think_ms": think_ms})
                    messages.append({
                        "role": "user",
                        "content": CONTENT_SEARCH_NUDGE if unfinished else (NOTHING_FOUND_NUDGE if looked else SEARCH_FIRST_NUDGE),
                    })
                    continue
                if not looked and not own_search_done and registry.tool("find") is not None:
                    # Told to look, still only a question: the loop runs the search itself, once.
                    own_search_done = True
                    outcome = _run_tool(registry, {"tool": "find", "query": ask}, ctx)
                    result.steps.append({
                        "step": step, "tool": "find", "args": {"query": ask}, "ok": outcome.ok, "status": outcome.status,
                        "empty": bool(getattr(outcome, "empty", False)), "intents": list(outcome.intents),
                        "result_chars": len(outcome.text), "think_ms": think_ms, "tool_ms": 0, "by": "loop",
                    })
                    result.seen.append(outcome.text[:_TOOL_RESULT_CHARS])
                    messages.append({"role": "user", "content": f"{TOOL_RESULT_HEADING}\n" + outcome.text[:_TOOL_RESULT_CHARS]})
                    continue
                from core.clean_loop.tools import NOT_FOUND_TEXT, UNSEARCHED_TEXT

                searched = any(i.get("tool") == "find" and i.get("status") == "content_search_complete" for i in result.steps)
                candidate = NOT_FOUND_TEXT if searched else UNSEARCHED_TEXT
            result.answer = candidate
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
                "empty": bool(getattr(outcome, "empty", False)),
                "intents": list(outcome.intents),
                "result_chars": len(outcome.text),
                "think_ms": think_ms,
                "tool_ms": int((time.monotonic() - started_tool) * 1000),
            }
        )
        result.seen.append(outcome.text[:_TOOL_RESULT_CHARS])
        messages.append({"role": "user", "content": f"{TOOL_RESULT_HEADING}\n" + outcome.text[:_TOOL_RESULT_CHARS]})
    result.proposals = list(ctx.proposals)
    if not result.answer:
        result.answer = GAVE_UP_TEXT if result.stopped == "max_steps" else NO_REPLY_TEXT
    for _name, gate in registry.answer_gates():
        try:
            result.answer = str(gate(result.answer, result) or result.answer)
        except Exception:
            continue
    return result


__all__ = [
    "DEFAULT_MAX_STEPS", "GAVE_UP_TEXT", "LOOK_FIRST_NUDGE", "NO_REPLY_TEXT", "PRIOR_EVIDENCE_HEADING", "REPEAT_NOTICE",
    "STAGED_EVIDENCE_HEADING", "TOOL_RESULT_HEADING", "BoundaryStop", "LoopResult", "carries_owner_evidence",
    "parse_action", "run_loop",
]
