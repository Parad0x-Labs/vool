"""LLM-backed reasoning engine for helper workers.

Replaces the template-based responses in helper_worker.run_task_capsule()
with actual model invocations via the ModelRegistry adapter system.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from core.provider_execution_boundary import invoke_provider_execution_boundary
from core.task_capsule import TaskCapsule

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You are a VOOL swarm helper agent. You answer task capsules from other agents in the network.

Rules:
1. You ONLY have access to the sanitized context provided. Do NOT invent facts.
2. Never produce executable code, file paths, URLs, or API keys.
3. Your output must be safe, abstract, and non-executable.
4. Structure your response as JSON with these fields:
   - "summary": A concise summary of your reasoning and conclusion
   - "evidence": A list of key supporting points (max 5)
   - "abstract_steps": A list of reasoning steps you took (max 5)
   - "confidence": A float 0.0-1.0 indicating your confidence
   - "risk_flags": A list of any concerns or caveats (empty list if none)

Respond ONLY with valid JSON. No markdown, no preamble.
"""


def _build_prompt(capsule: TaskCapsule) -> str:
    """Build a reasoning prompt from the capsule's sanitized context."""
    ctx = capsule.sanitized_context
    parts = [
        f"Task Type: {capsule.task_type}",
        f"Subtask: {capsule.subtask_type}",
        f"Summary: {capsule.summary}",
        f"Problem Class: {ctx.problem_class}",
    ]
    if ctx.abstract_inputs:
        parts.append(f"Abstract Inputs: {json.dumps(ctx.abstract_inputs[:8])}")
    if ctx.known_constraints:
        parts.append(f"Known Constraints: {json.dumps(ctx.known_constraints[:8])}")
    if ctx.environment_tags:
        parts.append(f"Environment Tags: {json.dumps(ctx.environment_tags)}")

    parts.append(f"Allowed Operations: {json.dumps(capsule.allowed_operations)}")
    return "\n".join(parts)


def _parse_llm_response(resp: ModelResponse) -> dict[str, Any]:
    """Parse the LLM output JSON into structured fields."""
    text = (resp.output_text or "").strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
        return {
            "summary": str(parsed.get("summary", ""))[:1024],
            "evidence": [str(e)[:256] for e in (parsed.get("evidence") or [])[:5]],
            "abstract_steps": [str(s)[:256] for s in (parsed.get("abstract_steps") or [])[:5]],
            "confidence": max(0.0, min(1.0, float(parsed.get("confidence", resp.confidence)))),
            "risk_flags": [str(f)[:128] for f in (parsed.get("risk_flags") or [])[:5]],
        }
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Failed to parse LLM JSON output, falling back to raw text: %s", e)
        return {
            "summary": text[:1024] if text else "Model returned unparseable output.",
            "evidence": [],
            "abstract_steps": ["llm_invocation", "parse_failed_fallback"],
            "confidence": max(0.1, resp.confidence - 0.15),
            "risk_flags": ["unparseable_output"],
        }


def _select_adapter(capsule: TaskCapsule) -> ModelAdapter | None:
    """Select the best model adapter for this capsule's task type."""
    registry = ModelRegistry()
    request = ModelSelectionRequest(
        task_kind=capsule.task_type,
        output_mode="json" if capsule.task_type in {"classification", "ranking", "validation"} else "plain_text",
        allow_paid_fallback=True,
    )
    manifest = registry.select_manifest(request)
    if manifest is None:
        return None
    return registry.build_adapter(manifest)


def invoke_llm_reasoning(capsule: TaskCapsule) -> dict[str, Any] | None:
    """Run a real LLM call for the given capsule.

    Returns structured output dict with summary/evidence/steps/confidence/risk_flags,
    or None if no model is available.
    """
    adapter = _select_adapter(capsule)
    if adapter is None:
        logger.info("No model adapter available for task_type=%s, falling back to template", capsule.task_type)
        return None

    prompt = _build_prompt(capsule)
    request = ModelRequest(
        task_kind=capsule.task_type,
        prompt=prompt,
        system_prompt=_SYSTEM_PROMPT,
        temperature=0.3,
        max_output_tokens=min(2048, capsule.max_response_bytes // 4),
        output_mode="json",
        trace_id=capsule.task_id,
        metadata={"capsule_id": capsule.capsule_id},
    )

    try:
        resp = invoke_provider_execution_boundary(adapter, "invoke", request)
        if resp.error:
            logger.error("Model returned error for capsule=%s: %s", capsule.capsule_id, resp.error)
            return None
        return _parse_llm_response(resp)
    except Exception as e:
        logger.error("LLM invocation failed for capsule=%s: %s", capsule.capsule_id, e, exc_info=True)
        return None


def grade_output(capsule: TaskCapsule, output: dict[str, Any]) -> float:
    """Grade the quality of helper output using a separate evaluator call.

    Returns a quality score 0.0-1.0. Falls back to the output's self-reported
    confidence if no grading model is available.
    """
    adapter = _select_adapter(capsule)
    if adapter is None:
        return float(output.get("confidence", 0.5))

    grading_prompt = json.dumps({
        "task_summary": capsule.summary,
        "task_type": capsule.task_type,
        "problem_class": capsule.sanitized_context.problem_class,
        "helper_summary": output.get("summary", ""),
        "helper_evidence": output.get("evidence", []),
        "helper_confidence": output.get("confidence", 0.5),
    }, indent=2)

    request = ModelRequest(
        task_kind="validation",
        prompt=grading_prompt,
        system_prompt=(
            "You are a quality evaluator. Rate the helper's answer on a scale of 0.0 to 1.0. "
            "Consider: relevance, completeness, accuracy, and safety. "
            "Respond with ONLY a JSON object: {\"quality_score\": <float>, \"reason\": \"<brief reason>\"}"
        ),
        temperature=0.1,
        max_output_tokens=256,
        output_mode="json",
        trace_id=capsule.task_id,
    )

    try:
        resp = invoke_provider_execution_boundary(adapter, "invoke", request)
        text = (resp.output_text or "").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        parsed = json.loads(text)
        return max(0.0, min(1.0, float(parsed.get("quality_score", output.get("confidence", 0.5)))))
    except Exception as e:
        logger.warning("Output grading failed, using self-reported confidence: %s", e)
        return float(output.get("confidence", 0.5))
