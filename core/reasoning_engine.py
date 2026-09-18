from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.identity_manager import render_with_persona

_CHAT_SURFACES = {"channel", "openclaw", "api"}
_PLANNER_LEAKAGE_MARKERS = (
    "workflow:",
    "real steps completed:",
    "here's what i'd suggest",
    '"summary":',
    '"steps":',
)
_TEMPLATE_FALLBACK_MARKERS = (
    "here's what i'd suggest",
    "no strong match found",
    "using a safe local fallback",
    "using best known pattern for",
    "using candidate output from",
    "using relevant retained context for",
    "using external notes as a temporary fallback",
)
_EXPLICIT_PLAN_REQUEST_PATTERNS = (
    r"\bgive me (?:a|the) plan\b",
    r"\bmake (?:me )?(?:a|the) plan\b",
    r"\baction plan\b",
    r"\broadmap\b",
    r"\bchecklist\b",
    r"\bexecution checklist\b",
    r"\bstep[\s-]?by[\s-]?step\b",
    r"\brollout steps\b",
    r"\brollout plan\b",
    r"\bsteps to\b",
    r"\bshow (?:me )?(?:the )?steps\b",
    r"\bwhat steps did you take\b",
    r"\breal steps\b",
    r"\bworkflow\b",
    r"\bplan this\b",
    r"\bplan out\b",
    r"\boutline (?:a|the) plan\b",
)


@dataclass
class Plan:
    summary: str
    abstract_steps: list[str]
    confidence: float
    risk_flags: list[str] = field(default_factory=list)
    simulation_steps: list[str] = field(default_factory=list)
    safe_actions: list[dict] = field(default_factory=list)
    reads_workspace: bool = False
    writes_workspace: bool = False
    requests_network: bool = False
    requests_subprocess: bool = False
    evidence_sources: list[str] = field(default_factory=list)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_best(evidence: dict[str, Any]) -> tuple[dict | None, str | None]:
    candidates = []

    for label in ["candidates", "local_candidates", "swarm_candidates"]:
        items = evidence.get(label) or []
        if items:
            candidates.append((items[0], label))

    if not candidates:
        return None, None

    # choose highest score if available
    best = max(candidates, key=lambda x: float(x[0].get("score", 0.0)))
    return best


def _fallback_steps(task_class: str) -> list[str]:
    mapping = {
        "dependency_resolution": [
            "inspect_runtime_version",
            "inspect_dependency_manifest",
            "clear_safe_local_cache",
            "reinstall_dependencies_in_workspace",
            "retest_build",
        ],
        "debugging": [
            "read_error_carefully",
            "identify_failing_component",
            "test_small_safe_fix",
            "re_run_validation",
        ],
        "config": [
            "inspect_config_keys",
            "compare_expected_vs_actual",
            "apply_safe_defaults",
            "retest_configuration",
        ],
        "security_hardening": [
            "identify_sensitive_surfaces",
            "remove_secret_exposure_paths",
            "prefer_reputable_hardening_guidance",
            "apply_safe_local_protections_first",
        ],
        "system_design": [
            "identify_required_runtime_layers",
            "separate_proven_vs_unproven_assumptions",
            "define_safe_minimal_integration_path",
            "list_live_proof_requirements",
        ],
        "research": [
            "define_question",
            "search_trusted_sources",
            "compare_findings",
            "summarize_result",
        ],
        "file_inspection": [
            "open_target_file_safely",
            "inspect_structure",
            "note_anomalies",
            "propose_next_step",
        ],
        "shell_guidance": [
            "explain_safe_command_intent",
            "simulate_command",
            "review_expected_effect",
        ],
        "risky_system_action": [
            "explain_why_action_is_risky",
            "refuse_unsafe_execution",
            "offer_safer_alternative",
        ],
    }
    return mapping.get(task_class, ["review_problem", "choose_safe_next_step", "validate_result"])


def _looks_like_ungrounded_live_lookup(summary: str) -> bool:
    lowered = str(summary or "").strip().lower()
    if not lowered:
        return False
    suspicious_markers = (
        "check online",
        "checked online",
        "checked the web",
        "search online",
        "searched online",
        "searched the web",
        "looked online",
        "fetching task",
        "fetching live",
        "fetched live",
        "real ai hive",
        "real hive",
        "real task",
        "found some real",
        "from an online source",
    )
    return any(marker in lowered for marker in suspicious_markers)


def _ungrounded_live_lookup_summary(task_class: str) -> str:
    if task_class in {"research", "integration_orchestration", "system_design"}:
        return (
            "No verified live lookup result was captured in this run. "
            "Be explicit about missing web or Hive evidence instead of inventing results."
        )
    return "No verified live lookup result was captured in this run."


def build_plan(task: Any, classification: dict[str, Any], evidence: dict[str, Any], persona: Any) -> Plan:
    task_class = classification.get("task_class", "unknown")
    base_risks = list(classification.get("risk_flags", []) or [])

    best_candidate, source_label = _extract_best(evidence)
    model_candidates = evidence.get("model_candidates") or []
    web_notes = evidence.get("web_notes") or []
    context_snippets = evidence.get("context_snippets") or []

    if best_candidate:
        steps = list(best_candidate.get("resolution_pattern") or [])[:8]
        confidence = float(best_candidate.get("score", 0.70))
        summary = str(best_candidate.get("summary") or f"Using best known pattern for {task_class}.")
        evidence_sources = [source_label or "candidate"]
        risk_flags = list(dict.fromkeys(base_risks + list(best_candidate.get("risk_flags", []) or [])))
    elif model_candidates:
        top = dict(model_candidates[0])
        raw_score = float(top.get("score", top.get("trust_score", classification.get("confidence_hint", 0.35))) or 0.35)
        validation_state = str(top.get("validation_state") or "").strip().lower()
        confidence_cap = 0.84 if validation_state == "valid" else 0.78 if raw_score >= 0.72 else 0.74
        confidence_floor = 0.36 if validation_state == "valid" else 0.28
        confidence = max(confidence_floor, min(confidence_cap, raw_score))
        steps = list(top.get("resolution_pattern") or [])[:8] or _fallback_steps(task_class)
        provider_name = top.get("provider_name") or "model"
        summary = str(top.get("summary") or f"Using candidate output from {provider_name} for {task_class}.")
        evidence_sources = [f"model:{provider_name}"]
        risk_flags = base_risks
        if not web_notes and _looks_like_ungrounded_live_lookup(summary):
            summary = _ungrounded_live_lookup_summary(task_class)
            confidence = min(confidence, 0.38)
            risk_flags = list(dict.fromkeys([*risk_flags, "ungrounded_live_claim"]))
    elif context_snippets:
        top = context_snippets[0]
        confidence = max(0.30, min(0.68, float(top.get("confidence", classification.get("confidence_hint", 0.35)) or 0.35)))
        steps = _fallback_steps(task_class)
        summary = str(top.get("summary") or f"Using relevant retained context for {task_class}.")
        evidence_sources = [f"context:{top.get('source_type', 'memory')}"]
        risk_flags = base_risks
    elif web_notes:
        note = web_notes[0]
        confidence = max(0.40, min(0.70, float(note.get("confidence", 0.55))))
        steps = _fallback_steps(task_class)
        summary = str(note.get("summary") or f"Using external notes as a temporary fallback for {task_class}.")
        evidence_sources = ["web"]
        risk_flags = base_risks
    else:
        confidence = max(0.25, min(0.60, float(classification.get("confidence_hint", 0.35))))
        steps = _fallback_steps(task_class)
        summary = f"No strong match found. Deferring to model synthesis for {task_class}."
        evidence_sources = ["model_deferred"]
        risk_flags = base_risks

    if task_class == "risky_system_action":
        confidence = min(confidence, 0.30)

    requests_network = bool(web_notes)
    requests_subprocess = False
    reads_workspace = task_class in {"file_inspection", "debugging", "config"}
    writes_workspace = False

    safe_actions: list[dict] = []
    simulation_steps = list(steps)

    return Plan(
        summary=summary,
        abstract_steps=steps,
        confidence=max(0.0, min(1.0, confidence)),
        risk_flags=risk_flags,
        simulation_steps=simulation_steps,
        safe_actions=safe_actions,
        reads_workspace=reads_workspace,
        writes_workspace=writes_workspace,
        requests_network=requests_network,
        requests_subprocess=requests_subprocess,
        evidence_sources=evidence_sources,
    )


def render_response(
    plan: Plan,
    gate_decision: Any,
    persona: Any,
    input_interpretation: Any | None = None,
    prompt_assembly_report: Any | None = None,
    *,
    surface: str = "cli",
    allow_planner_style: bool = False,
) -> str:
    """Render agent response. surface='channel'|'openclaw'|'api' gives clean
    conversational output; surface='cli' (default) gives full diagnostics."""
    if surface in {"channel", "openclaw", "api"}:
        return _render_conversational(
            plan,
            gate_decision,
            persona,
            input_interpretation,
            allow_planner_style=allow_planner_style,
        )

    return _render_diagnostic(plan, gate_decision, persona, input_interpretation, prompt_assembly_report)


def explicit_planner_style_requested(user_input: str) -> bool:
    """Whether the user asked for VOOL's plan document rather than an ordinary answer.

    A turn that states the exact shape of the answer it wants has already answered this question.
    Measured on the v0.5.0 smoke run (QA-050-027): "Answer in one sentence: give me the numbered
    steps to boil an egg." matched `\\bsteps to\\b`, which forces `output_mode=action_plan`
    (`core/task_router.model_execution_profile`), and the reader got a plan scaffold instead of the
    one sentence they specified. The two instructions are contradictory and the explicit shape is
    the more specific one, so it wins.

    Narrow on purpose: this only declines when the turn carries a parsed response constraint. "Give
    me a step-by-step rollout plan" still gets the planner, because nothing in it says otherwise.
    """
    lowered_input = str(user_input or "").strip().lower()
    if not lowered_input:
        return False
    if not any(re.search(pattern, lowered_input) for pattern in _EXPLICIT_PLAN_REQUEST_PATTERNS):
        return False
    from core.response_constraints import parse_response_constraint

    return parse_response_constraint(user_input) is None


def should_use_planner_renderer(
    *,
    surface: str = "cli",
    output_mode: str = "plain_text",
    user_input: str = "",
) -> bool:
    normalized_surface = str(surface or "cli").strip().lower()
    normalized_output_mode = str(output_mode or "plain_text").strip().lower()

    if normalized_surface not in _CHAT_SURFACES:
        return True
    if normalized_output_mode != "action_plan":
        return False
    return explicit_planner_style_requested(user_input)


def inspect_user_response_shape(
    response_text: str,
    *,
    surface: str = "cli",
    rendered_via: str = "unknown",
) -> dict[str, bool | str]:
    lowered = str(response_text or "").strip().lower()
    is_chat_surface = surface in _CHAT_SURFACES
    planner_leakage = any(marker in lowered for marker in _PLANNER_LEAKAGE_MARKERS)
    template_fallback_hit = any(marker in lowered for marker in _TEMPLATE_FALLBACK_MARKERS)
    return {
        "surface": surface,
        "rendered_via": rendered_via,
        "chat_surface": is_chat_surface,
        "planner_leakage": planner_leakage,
        "template_renderer_hit": is_chat_surface and rendered_via == "reasoning_engine",
        "template_fallback_hit": is_chat_surface and rendered_via == "reasoning_engine" and template_fallback_hit,
    }


def _render_conversational(
    plan: Plan,
    gate_decision: Any,
    persona: Any,
    input_interpretation: Any | None = None,
    *,
    allow_planner_style: bool = False,
) -> str:
    """Clean conversational response for chat surfaces (OpenClaw, channels)."""
    mode = _get(gate_decision, "mode", "advice_only")

    if mode == "blocked":
        reason = _get(gate_decision, "reason", "safety policy")
        return render_with_persona(
            f"I can't help with that \u2014 {reason}",
            persona,
        )

    summary = plan.summary.strip()

    # Detect when the summary is useless (fallback boilerplate or echoed input)
    _FALLBACK_PHRASES = [
        "no strong match found",
        "using a safe local fallback",
        "using best known pattern for",
        "using candidate output from",
        "using relevant retained context for",
        "using external notes as a temporary fallback",
    ]
    is_fallback = any(phrase in summary.lower() for phrase in _FALLBACK_PHRASES)

    # Also detect echoed user input masquerading as a summary
    user_text = ""
    if input_interpretation is not None:
        user_text = str(
            _get(input_interpretation, "reconstructed_text", "")
            or _get(input_interpretation, "normalized_text", "")
            or ""
        ).strip().lower()
    if user_text and summary.lower().strip("[] \t\n").endswith(user_text):
        is_fallback = True

    # If the summary looks like a user question rather than an agent answer,
    # treat it as echoed input (stale context from memory).
    if not is_fallback and summary.strip().endswith("?"):
        is_fallback = True
    summary_lower = summary.lower().strip()
    if not is_fallback and len(summary_lower.split()) <= 8:
        _trivial_patterns = {
            "hi", "hello", "hey", "good", "ok", "yes", "no", "thanks",
            "thank you", "bye", "goodbye", "sup", "yo", "hm", "hmm",
        }
        if summary_lower.rstrip("?!., ") in _trivial_patterns:
            is_fallback = True

    if is_fallback or not summary or len(summary) < 5:
        body = _build_natural_fallback(plan, allow_planner_style=allow_planner_style)
    else:
        body = summary
        if plan.abstract_steps:
            readable_steps = [s.replace("_", " ") for s in plan.abstract_steps]
            step_text = "\n".join(f"- {s}" for s in readable_steps)
            body += f"\n\n{step_text}"

    return render_with_persona(body, persona)


def _build_natural_fallback(plan: Plan, *, allow_planner_style: bool = False) -> str:
    """Generate a helpful natural-language response when no real content exists."""
    if not plan.abstract_steps:
        return "I'm here and listening. What would you like to work on?"

    readable_steps = [s.replace("_", " ") for s in plan.abstract_steps]

    generic_steps = {"review problem", "choose safe next step", "validate result"}
    if set(readable_steps) == generic_steps:
        return "I'm here and ready to help. What would you like to work on?"

    step_text = "\n".join(f"- {s}" for s in readable_steps)
    if not allow_planner_style:
        return step_text
    return f"Here's what I'd suggest:\n\n{step_text}"


def _render_diagnostic(
    plan: Plan,
    gate_decision: Any,
    persona: Any,
    input_interpretation: Any | None = None,
    prompt_assembly_report: Any | None = None,
) -> str:
    """Full diagnostic response for CLI / developer surfaces."""
    mode = _get(gate_decision, "mode", "advice_only")
    reason = _get(gate_decision, "reason", "No reason provided.")

    lines: list[str] = []
    if input_interpretation is not None:
        understanding = float(_get(input_interpretation, "understanding_confidence", 1.0) or 1.0)
        ref_targets = list(_get(input_interpretation, "reference_targets", []) or [])
        if ref_targets or understanding < 0.58:
            summary = _get(input_interpretation, "interpretation_summary", None)
            if summary:
                lines.extend([f"I'm reading your ask as: {summary}.", ""])
            if understanding < 0.45:
                lines.extend(["This input was still somewhat ambiguous, so the plan follows the most likely meaning.", ""])
    if prompt_assembly_report is not None:
        retrieval_confidence = _get(prompt_assembly_report, "retrieval_confidence", None)
        total_tokens = _get(prompt_assembly_report, "total_tokens_used", None)
        if callable(total_tokens):
            total_tokens = total_tokens()
        if retrieval_confidence:
            lines.extend([f"Context load: {retrieval_confidence} confidence.", ""])
        if total_tokens is not None:
            lines.extend([f"Context budget used: {int(total_tokens)} tokens.", ""])

    lines.extend(
        [
            f"{plan.summary}",
            "",
            f"Confidence: {plan.confidence:.2f}",
            f"Mode: {mode}",
            f"Why: {reason}",
            "",
            "Recommended steps:",
        ]
    )

    for step in plan.abstract_steps:
        lines.append(f"- {step}")

    if plan.risk_flags:
        lines.extend(["", f"Risk flags: {', '.join(plan.risk_flags)}"])

    body = "\n".join(lines)
    return render_with_persona(body, persona)
