from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import audit_logger, policy_engine
from core.model_teacher_pipeline import ModelTeacherPipeline
from core.task_capsule import TaskCapsule
from network.assist_models import TaskResult
from network.signer import get_local_peer_id as local_peer_id


@dataclass
class WorkerOutcome:
    result: TaskResult
    accepted_scope: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    return " ".join((text or "").strip().split())


def _hash_result(summary: str, steps: list[str], evidence: list[str]) -> str:
    raw = json.dumps(
        {
            "summary": summary,
            "steps": steps,
            "evidence": evidence,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _truncate_to_budget(summary: str, evidence: list[str], steps: list[str], max_bytes: int) -> tuple[str, list[str], list[str]]:
    # simple budget-aware truncation
    if max_bytes <= 0:
        return "", [], []

    payload = {
        "summary": summary,
        "evidence": evidence,
        "steps": steps,
    }

    safety_limit = 512
    iterations = 0
    while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > max_bytes:
        iterations += 1
        if iterations > safety_limit:
            break
        before = (len(summary), len(evidence), len(steps))
        if len(evidence) > 1:
            evidence = evidence[:-1]
        elif len(steps) > 1:
            steps = steps[:-1]
        elif len(summary) > 128:
            summary = summary[: max(128, len(summary) - 64)].rstrip()
        else:
            break
        after = (len(summary), len(evidence), len(steps))
        if after == before:
            break
        payload = {
            "summary": summary,
            "evidence": evidence,
            "steps": steps,
        }

    return summary, evidence, steps


def _build_generic_steps(task_type: str, problem_class: str) -> list[str]:
    if task_type == "research":
        return [
            "identify_relevant_signal",
            "compare_safe_options",
            "summarize_best_tradeoff",
        ]
    if task_type == "classification":
        return [
            "inspect_abstract_inputs",
            "map_problem_to_class",
            "return_classification",
        ]
    if task_type == "ranking":
        return [
            "score_candidate_options",
            "rank_candidates",
            "return_ranked_recommendation",
        ]
    if task_type == "validation":
        return [
            "check_internal_consistency",
            "verify_constraint_fit",
            "return_validation_summary",
        ]
    if task_type == "planning":
        return [
            "decompose_subproblem",
            "sequence_safe_next_steps",
            "return_abstract_plan",
        ]
    if task_type == "code_reasoning":
        return [
            "identify_general_code_issue",
            "propose_safe_abstract_fix_path",
            "return_non_executable_guidance",
        ]
    if task_type == "documentation":
        return [
            "extract_key_points",
            "organize_clarified_summary",
            "return_draft_output",
        ]
    return [
        "review_task_capsule",
        "apply_safe_reasoning",
        "return_structured_result",
    ]


def _helper_model_profile(task_type: str) -> tuple[str, str, str]:
    # task_kind, output_mode, result_type
    mapping = {
        "research": ("summarization", "summary_block", "research_summary"),
        "classification": ("classification", "json_object", "classification"),
        "ranking": ("action_plan", "action_plan", "ranking"),
        "validation": ("action_plan", "action_plan", "validation"),
        "planning": ("action_plan", "action_plan", "plan_suggestion"),
        "code_reasoning": ("action_plan", "action_plan", "plan_suggestion"),
        "documentation": ("summarization", "summary_block", "draft_output"),
    }
    return mapping.get(task_type, ("normalization_assist", "summary_block", "plan_suggestion"))


def _extract_steps_from_text(text: str) -> list[str]:
    steps: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        else:
            match = re.match(r"^(\d+)[\.\)]\s*(.+)$", line)
            if match:
                line = match.group(2).strip()
        if not line:
            continue
        if len(steps) < 8:
            steps.append(line[:160])
    return steps


def _build_model_prompt(capsule: TaskCapsule, *, problem_class: str, abstract_inputs: list[str], constraints: list[str]) -> str:
    payload = {
        "task_id": capsule.task_id,
        "task_type": capsule.task_type,
        "subtask_type": capsule.subtask_type,
        "problem_class": problem_class,
        "summary": capsule.summary,
        "abstract_inputs": abstract_inputs[:8],
        "known_constraints": constraints[:8],
        "allowed_operations": list(capsule.allowed_operations),
        "forbidden_operations": list(capsule.forbidden_operations),
    }
    return (
        "You are a VOOL helper node. Produce concise, non-executable, policy-safe reasoning.\n"
        "Never output commands, shell snippets, credentials, or destructive instructions.\n"
        "Return only abstract analysis and safe guidance that fits the task capsule.\n"
        f"Capsule:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _run_model_reasoning(
    capsule: TaskCapsule,
    *,
    problem_class: str,
    abstract_inputs: list[str],
    constraints: list[str],
) -> dict[str, Any] | None:
    task_kind, output_mode, result_type = _helper_model_profile(capsule.task_type)
    pipeline = ModelTeacherPipeline()
    swarm_width = max(1, int(policy_engine.get("model_orchestration.drone_swarm_width", 2) or 2))
    candidate = pipeline.run(
        task_kind=task_kind,
        prompt=_build_model_prompt(
            capsule,
            problem_class=problem_class,
            abstract_inputs=abstract_inputs,
            constraints=constraints,
        ),
        context={
            "task_id": capsule.task_id,
            "problem_class": problem_class,
            "subtask_type": capsule.subtask_type,
        },
        trace_id=capsule.task_id,
        output_mode=output_mode,
        provider_role="drone",
        swarm_size=swarm_width,
    )
    if candidate is None:
        return None

    raw_text = (candidate.output_text or "").strip()
    if not raw_text:
        return None
    steps = _extract_steps_from_text(raw_text)
    summary = raw_text.splitlines()[0].strip() if raw_text.splitlines() else raw_text
    summary = summary[:640] if summary else f"Helper reasoning completed for {problem_class}."
    evidence = [f"model:{candidate.source_model_tag}", f"task_kind:{task_kind}", f"output_mode:{output_mode}"]
    if candidate.swarm_provider_ids:
        evidence.append(f"swarm:{len(candidate.swarm_provider_ids)}")
    evidence.extend(abstract_inputs[:2])
    evidence.extend(constraints[:2])
    return {
        "summary": summary,
        "steps": steps or _build_generic_steps(capsule.task_type, problem_class),
        "evidence": evidence[:12],
        "confidence": max(0.0, min(1.0, float(candidate.confidence))),
        "result_type": result_type,
        "risk_flags": [],
        "model_source": candidate.source_model_tag,
    }


def _model_unavailable_result(problem_class: str) -> dict[str, Any]:
    """The honest result when the real model pipeline returned nothing or raised.

    Replaces a removed function (`_run_template_reasoning`) that dressed up bare input echoes as
    findings -- "Top signal: {top[0]}. Best path is the safest option that satisfies the known
    constraints." on `research`, "Highest-fit option: {top}." on `ranking`, and similar per task
    type -- with a fixed confidence per type (0.55-0.78) and `model_source: None`. Nothing marked
    it as synthetic downstream: `helper_model_reasoning_used` (the audit event) only fires when
    `model_source` is truthy, so this path logged nothing, and the fabricated TaskResult fed the
    same consensus/reassembly/finalizer code as genuine model output, indistinguishable to anyone
    reading the finalized answer. Confirmed live and cut per project policy: an unverified
    component that turns out to be a scripted stand-in gets replaced, not patched around.

    confidence=0.0 and result_type="model_unavailable" (not one of the real result_type values
    used when a model actually answered) make this the opposite of a plausible answer: distinct on
    sight to any caller and to consensus scoring, which weights on confidence.
    """
    return {
        "summary": (
            f"No reasoning is available for this {problem_class} subtask: the local model "
            "pipeline returned no output."
        ),
        "steps": [],
        "evidence": [],
        "confidence": 0.0,
        "result_type": "model_unavailable",
        "risk_flags": ["model_unavailable"],
        "model_source": None,
    }


def run_task_capsule(capsule: TaskCapsule, *, helper_agent_id: str | None = None) -> WorkerOutcome:
    helper_agent_id = helper_agent_id or local_peer_id()

    # hard scope check: v1 helper only supports pure reasoning work
    allowed = set(capsule.allowed_operations)
    forbidden = set(capsule.forbidden_operations)
    if "execute" not in forbidden or "access_db" not in forbidden or "call_shell" not in forbidden:
        raise ValueError("Capsule scope too permissive for local helper worker.")

    if not allowed.issubset({"reason", "research", "compare", "rank", "summarize", "validate", "draft"}):
        raise ValueError("Capsule requested unsupported operation.")

    ctx = capsule.sanitized_context
    problem_class = _norm(ctx.problem_class)
    abstract_inputs = [_norm(x) for x in ctx.abstract_inputs]
    constraints = [_norm(x) for x in ctx.known_constraints]

    model_result: dict[str, Any] | None = None
    try:
        model_result = _run_model_reasoning(
            capsule,
            problem_class=problem_class,
            abstract_inputs=abstract_inputs,
            constraints=constraints,
        )
    except Exception as exc:
        audit_logger.log(
            "helper_model_reasoning_error",
            target_id=capsule.task_id,
            target_type="helper_task",
            trace_id=capsule.task_id,
            details={"error": str(exc), "task_type": capsule.task_type},
        )

    reasoning = model_result or _model_unavailable_result(problem_class)
    summary = str(reasoning["summary"])
    evidence = [str(item) for item in list(reasoning.get("evidence") or [])]
    steps = [str(item) for item in list(reasoning.get("steps") or [])]
    # `.get(key, default)`, not `.get(key) or default` -- a genuine 0.0 confidence (the
    # model-unavailable path) is falsy and the `or` form silently promoted it back to 0.55,
    # discarding the exact signal that path exists to send.
    confidence_value = reasoning.get("confidence")
    confidence = float(confidence_value) if confidence_value is not None else 0.55
    result_type = str(reasoning.get("result_type") or "plan_suggestion")
    risk_flags = [str(item) for item in list(reasoning.get("risk_flags") or [])]
    model_source = str(reasoning.get("model_source") or "")
    if model_source:
        audit_logger.log(
            "helper_model_reasoning_used",
            target_id=capsule.task_id,
            target_type="helper_task",
            trace_id=capsule.task_id,
            details={"task_type": capsule.task_type, "model_source": model_source, "confidence": confidence},
        )

    summary, evidence, steps = _truncate_to_budget(
        summary=summary,
        evidence=evidence,
        steps=steps,
        max_bytes=capsule.max_response_bytes,
    )

    result_hash = _hash_result(summary, steps, evidence)

    result = TaskResult(
        result_id=str(uuid.uuid4()),
        task_id=capsule.task_id,
        helper_agent_id=helper_agent_id,
        result_type=result_type,
        summary=summary,
        confidence=max(0.0, min(1.0, confidence)),
        evidence=evidence,
        abstract_steps=steps,
        risk_flags=risk_flags,
        result_hash=result_hash,
        timestamp=_utcnow(),
    )

    # Phase 25: Cooperative Swarm Learning
    if getattr(capsule, "learning_allowed", False) and bool(
        policy_engine.get("learning.enable_cooperative_swarm_memory", True)
    ):
        try:
            from storage.swarm_memory import save_sniffed_context
            save_sniffed_context(
                parent_peer_id=capsule.parent_agent_id,
                prompt_data=capsule.sanitized_context.model_dump(mode="json"),
                result_data={
                    "summary": summary,
                    "evidence": evidence,
                    "abstract_steps": steps
                }
            )
        except Exception as e:
            # We don't fail the task if local learning ingestion fails
            import logging
            logging.error(f"Failed to ingest learned context: {e}")
            audit_logger.log(
                "helper_learning_ingestion_failed",
                target_id=capsule.task_id,
                target_type="helper_task",
                trace_id=capsule.task_id,
                details={"error": str(e)},
            )

    return WorkerOutcome(
        result=result,
        accepted_scope=True,
    )
