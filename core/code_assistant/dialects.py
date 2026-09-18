"""Model tool dialects for the coding assistant: many wire shapes, one canonical call.

A cloud model answers with native tool calls (the provider-neutral ``CloudToolCall`` envelope the
runtime already speaks); a local model answers with text in which a tool call rides as a strict
JSON object. Both are TRANSLATIONS of the same intent — the dialect a model speaks may not change
what executes. Every parser here mints a validated ``CodeAssistantProposal`` whose
``(intent, arguments)`` pair IS the canonical call, and refuses anything the contract would
refuse, so the execution path downstream of the dialect layer is byte-identical.

``canonical_call`` is the seam the served lane's twin-journey proof reads: the SAME model intent
spoken in either dialect normalizes to the SAME ``(intent, arguments)`` pair, and from there to
the same ``code.task.*`` calls, the same journal states, and the same bytes on disk. (The served
transport has its own broader recovery layer — ``core.tool_call_recovery`` — for the wire dialects
local providers drift into; this module is the coding lane's canonical normalization of the two
first-class proposal dialects.)
"""
from __future__ import annotations

import json
import re
from typing import Any

from core.code_assistant.contract import (
    CodeAssistantProposal,
    ContractRefused,
    REASON_INVALID_ARGUMENTS,
    REASON_UNKNOWN_INTENT,
    validate_proposal,
)

DIALECT_CLOUD = "cloud_native_tool_call"
DIALECT_LOCAL = "local_text_tool_call"

_TOOL_BLOCK = re.compile(r"\{[^{}]*\"tool\"[^{}]*\}", re.DOTALL)


def _first_tool_object(raw: str) -> str:
    """The first brace-balanced substring that parses as an object naming ``tool``.

    A regex cannot do this: ``arguments`` is a nested object, so the tool object's own braces
    appear inside it. A bounded brace-matching scan over the message is exact and cheap enough
    for a model answer; strings containing braces are handled by the JSON parser's own verdict,
    so a candidate that does not parse is skipped, never mis-split.
    """
    start = 0
    while True:
        begin = raw.find("{", start)
        if begin < 0:
            return ""
        depth = 0
        in_string = False
        escape = False
        for index in range(begin, len(raw)):
            char = raw[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[begin : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict) and "tool" in parsed:
                        return candidate
                    break
        start = begin + 1


def _mint(intent: Any, arguments: Any, *, stage: str, rationale: str, dialect: str, origin: str) -> CodeAssistantProposal:
    if not isinstance(arguments, dict):
        raise ContractRefused(
            REASON_INVALID_ARGUMENTS,
            f"Tool arguments for `{intent}` must be a JSON object, got {type(arguments).__name__}.",
        )
    proposal = CodeAssistantProposal(
        intent=str(intent or "").strip(),
        arguments=dict(arguments),
        stage=str(stage or "reproduce"),
        rationale=str(rationale or ""),
        dialect=dialect,
        origin=str(origin or ""),
    )
    # Validate AT MINT: a dialect may not emit a proposal the contract would refuse, because the
    # refusal reason is part of the model-facing feedback loop, not a post-execution surprise.
    validate_proposal(proposal)
    return proposal


def proposal_from_cloud_call(
    call: Any,
    *,
    stage: str = "reproduce",
    rationale: str = "",
) -> CodeAssistantProposal:
    """A cloud native tool call (``CloudToolCall`` or the same shape as a mapping)."""
    if isinstance(call, dict):
        intent = call.get("intent") or call.get("name") or (call.get("function") or {}).get("name", "")
        raw_arguments = call.get("arguments")
        if raw_arguments is None:
            raw_arguments = (call.get("function") or {}).get("arguments", {})
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ContractRefused(
                    REASON_INVALID_ARGUMENTS,
                    f"Cloud tool call `{intent}` carried arguments that are not valid JSON: {exc}.",
                ) from exc
        else:
            arguments = raw_arguments
        origin = str(call.get("call_id") or call.get("id") or "")
        rationale = str(rationale or call.get("rationale") or "")
    else:
        intent = getattr(call, "intent", "") or getattr(call, "name", "")
        arguments = getattr(call, "arguments", None)
        origin = str(getattr(call, "call_id", "") or "")
        rationale = str(rationale or getattr(call, "rationale", "") or "")
    return _mint(intent, arguments, stage=stage, rationale=rationale, dialect=DIALECT_CLOUD, origin=origin)


def proposal_from_local_text(
    text: str,
    *,
    stage: str = "reproduce",
    rationale: str = "",
) -> CodeAssistantProposal:
    """A local-model tool call: one strict JSON object naming ``tool`` and ``arguments``.

    The object may ride bare or inside a fenced block; the FIRST well-formed object wins and
    anything else in the message is model prose, exactly as a provider's native channel treats
    surrounding text. A message with no tool object raises ``ContractRefused`` — the runtime would
    rather ask again than guess an intent from prose.
    """
    raw = str(text or "")
    candidate = ""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        candidate = fence.group(1)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None
        if not (isinstance(payload, dict) and "tool" in payload):
            candidate = ""
    if not candidate:
        candidate = _first_tool_object(raw)
    if not candidate:
        raise ContractRefused(
            REASON_UNKNOWN_INTENT,
            "No tool call found in the model message; expected a JSON object with `tool` and `arguments`.",
        )
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ContractRefused(
            REASON_INVALID_ARGUMENTS,
            f"The local-model tool object is not valid JSON: {exc}.",
        ) from exc
    if not isinstance(payload, dict) or "tool" not in payload:
        raise ContractRefused(
            REASON_UNKNOWN_INTENT,
            "The local-model tool object must name a `tool`.",
        )
    return _mint(
        payload.get("tool"),
        payload.get("arguments") or {},
        stage=stage,
        rationale=str(payload.get("rationale") or rationale or ""),
        dialect=DIALECT_LOCAL,
        origin=str(payload.get("call_id") or ""),
    )


def canonical_call(payload: Any, *, stage: str = "reproduce", rationale: str = "") -> dict[str, Any]:
    """One model answer -> the ONE canonical ``(intent, arguments)`` call, whichever dialect spoke.

    A cloud envelope (``{"tool_calls": [{"name": ..., "arguments": ...}]}``), a bare mapping naming
    ``tool``/``intent``, or local-model text carrying that JSON object all normalize to the same
    pair — which is exactly the payload a ``code.task.propose`` / ``code.task.step`` call carries.
    Raises ``ContractRefused`` for anything the contract would refuse, so normalization never
    invents a call the model did not make.
    """
    return parse_model_output(payload, stage=stage, rationale=rationale).canonical_call()


def parse_model_output(
    payload: Any,
    *,
    stage: str = "reproduce",
    rationale: str = "",
) -> CodeAssistantProposal:
    """Route one model answer to its dialect parser. Cloud envelopes carry ``tool_calls``; a bare
    string is the local text dialect. This is the ONLY entry the runtime loop uses, so adding a
    dialect means adding a branch here and nothing downstream changes."""
    if isinstance(payload, dict):
        calls = payload.get("tool_calls")
        if isinstance(calls, list) and calls:
            if len(calls) > 1:
                raise ContractRefused(
                    REASON_INVALID_ARGUMENTS,
                    "One proposal at a time: the coding assistant executes tool calls sequentially.",
                )
            return proposal_from_cloud_call(calls[0], stage=stage, rationale=rationale)
        if "tool" in payload:
            return _mint(
                payload.get("tool"),
                payload.get("arguments") or {},
                stage=stage,
                rationale=str(payload.get("rationale") or rationale or ""),
                dialect=DIALECT_CLOUD,
                origin=str(payload.get("call_id") or ""),
            )
        raise ContractRefused(REASON_UNKNOWN_INTENT, "The model answer carries no tool call.")
    return proposal_from_local_text(payload, stage=stage, rationale=rationale)


__all__ = [
    "DIALECT_CLOUD",
    "DIALECT_LOCAL",
    "canonical_call",
    "parse_model_output",
    "proposal_from_cloud_call",
    "proposal_from_local_text",
]
