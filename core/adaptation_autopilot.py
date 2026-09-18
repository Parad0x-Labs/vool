from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import audit_logger, policy_engine
from core.adaptation_dataset import build_adaptation_corpus, load_adaptation_examples, score_adaptation_rows
from core.lora_training_pipeline import dependency_status, promote_adaptation_job, run_adaptation_job
from core.model_registry import ModelRegistry
from core.provider_invocation_gateway import seal_direct_provider_invocation
from core.public_hive_bridge import PublicHiveBridge
from core.semantic_judge import evaluate_semantic_agreement
from storage.adaptation_store import (
    append_adaptation_job_event,
    create_adaptation_corpus,
    create_adaptation_eval_run,
    create_adaptation_job,
    get_adaptation_corpus,
    get_adaptation_job,
    get_adaptation_loop_state,
    list_adaptation_corpora,
    list_adaptation_eval_runs,
    list_adaptation_jobs,
    update_adaptation_eval_run,
    update_adaptation_job,
    update_corpus_analysis,
    update_corpus_spec,
    upsert_adaptation_loop_state,
)
from storage.migrations import run_migrations
from storage.model_provider_manifest import set_provider_manifest_enabled
from storage.useful_output_store import summarize_useful_outputs

_AUTOPILOT_LOCK = threading.Lock()
_AUTOPILOT_THREAD: threading.Thread | None = None


@dataclass
class CorpusScore:
    corpus_id: str
    example_count: int
    content_hash: str
    quality_score: float
    quality_details: dict[str, Any]


@dataclass
class EvalSummary:
    eval_id: str
    eval_kind: str
    decision: str
    sample_count: int
    baseline_score: float
    candidate_score: float
    score_delta: float
    metrics: dict[str, Any]


@dataclass
class BaseModelResolution:
    base_model_ref: str
    base_provider_name: str
    base_model_name: str
    license_name: str
    license_reference: str


def get_adaptation_policy() -> dict[str, Any]:
    raw = dict(policy_engine.get("adaptation", {}) or {})
    defaults = {
        "enabled": True,
        "tick_interval_seconds": 1800,
        "max_running_jobs": 1,
        "default_corpus_label": "autopilot-default",
        "limit_per_source": 256,
        "base_model_ref": "",
        "base_provider_name": "",
        "base_model_name": "",
        "license_name": "",
        "license_reference": "",
        "adapter_provider_name": "vool-adapted",
        "adapter_model_prefix": "vool-loop",
        "capabilities": ["summarize", "classify", "format"],
        "min_examples_to_train": 64,
        "min_structured_examples": 16,
        "min_high_signal_examples": 10,
        "min_new_examples_since_last_job": 16,
        "min_quality_score": 0.68,
        "max_conversation_ratio": 0.45,
        "max_eval_samples": 12,
        "max_canary_samples": 8,
        "eval_holdout_examples": 12,
        "canary_holdout_examples": 8,
        "min_train_examples_after_holdout": 24,
        "epochs": 1,
        "max_steps": 32,
        "batch_size": 1,
        "gradient_accumulation_steps": 4,
        "learning_rate": 0.0002,
        "cutoff_len": 768,
        "lora_r": 8,
        "lora_alpha": 16,
        "lora_dropout": 0.05,
        "logging_steps": 4,
        "promotion_margin": 0.03,
        "rollback_margin": 0.04,
        "min_candidate_eval_score": 0.55,
        "min_candidate_canary_score": 0.52,
        "post_promotion_canary_min_new_examples": 8,
        "publish_metadata_to_hive": True,
        "hive_topic": "VOOL Model Adaptation",
    }
    defaults.update(raw)
    defaults["capabilities"] = [str(item).strip() for item in list(defaults.get("capabilities") or []) if str(item).strip()]
    return defaults


def get_adaptation_autopilot_status(loop_name: str = "default") -> dict[str, Any]:
    run_migrations()
    return {
        "dependency_status": dependency_status().to_dict(),
        "policy": get_adaptation_policy(),
        "loop_state": get_adaptation_loop_state(loop_name) or {},
        "signal_summary": summarize_useful_outputs(),
        "recent_corpora": list_adaptation_corpora(limit=12),
        "recent_jobs": list_adaptation_jobs(limit=12),
        "recent_evals": list_adaptation_eval_runs(limit=24),
        "worker_running": bool(_AUTOPILOT_THREAD and _AUTOPILOT_THREAD.is_alive()),
    }


def schedule_adaptation_autopilot_tick(*, loop_name: str = "default", force: bool = False, wait: bool = False) -> dict[str, Any]:
    global _AUTOPILOT_THREAD
    run_migrations()
    with _AUTOPILOT_LOCK:
        if _AUTOPILOT_THREAD and _AUTOPILOT_THREAD.is_alive():
            return {"ok": True, "status": "already_running", "loop_name": loop_name}
        if wait:
            return run_adaptation_autopilot_tick(loop_name=loop_name, force=force)

        def _runner() -> None:
            try:
                run_adaptation_autopilot_tick(loop_name=loop_name, force=force)
            finally:
                global _AUTOPILOT_THREAD
                with _AUTOPILOT_LOCK:
                    _AUTOPILOT_THREAD = None

        _AUTOPILOT_THREAD = threading.Thread(target=_runner, name=f"vool-adaptation-{loop_name}", daemon=True)
        _AUTOPILOT_THREAD.start()
        return {"ok": True, "status": "scheduled", "loop_name": loop_name}


def run_adaptation_autopilot_tick(*, loop_name: str = "default", force: bool = False) -> dict[str, Any]:
    run_migrations()
    cfg = get_adaptation_policy()
    now = _utcnow()
    state = upsert_adaptation_loop_state(loop_name, status="running", last_tick_at=now, last_error_text="")
    try:
        deps = dependency_status().to_dict()
        if not bool(cfg.get("enabled", True)):
            return _finish_loop(loop_name, status="disabled", decision="skipped", reason="adaptation_disabled", metrics={"dependency_status": deps})
        if not deps.get("ok"):
            return _finish_loop(loop_name, status="blocked", decision="skipped", reason="missing_runtime_dependencies", metrics={"dependency_status": deps})
        if not force and _tick_within_interval(state=state, tick_interval_seconds=int(cfg.get("tick_interval_seconds", 1800) or 1800)):
            return _finish_loop(loop_name, status="idle", decision="skipped", reason="tick_interval_not_elapsed", touch_completed=False)
        running_jobs = list_adaptation_jobs(limit=max(1, int(cfg.get("max_running_jobs", 1) or 1)), statuses=("queued", "running"))
        if running_jobs:
            return _finish_loop(loop_name, status="busy", decision="skipped", reason="adaptation_job_already_running", metrics={"running_job_ids": [job["job_id"] for job in running_jobs]})

        base = _resolve_base_model(cfg)
        if not base.base_model_ref:
            return _finish_loop(loop_name, status="blocked", decision="skipped", reason="trainable_base_model_not_configured")
        if not base.license_name or not base.license_reference:
            return _finish_loop(loop_name, status="blocked", decision="skipped", reason="missing_license_metadata_for_base_model", metrics={"base_model_ref": base.base_model_ref})

        state = upsert_adaptation_loop_state(
            loop_name,
            base_model_ref=base.base_model_ref,
            base_provider_name=base.base_provider_name,
            base_model_name=base.base_model_name,
        )
        previous_hash = str((state or {}).get("last_corpus_hash") or "")
        previous_examples = int((state or {}).get("last_example_count") or 0)
        corpus = _ensure_loop_corpus(cfg)
        built = build_adaptation_corpus(corpus["corpus_id"])
        score = score_adaptation_corpus(corpus["corpus_id"], built.output_path)

        state = upsert_adaptation_loop_state(
            loop_name,
            last_corpus_id=score.corpus_id,
            last_corpus_hash=score.content_hash,
            last_example_count=score.example_count,
            last_quality_score=score.quality_score,
            metrics={"corpus_quality": score.quality_details},
        )

        rollback_summary = _maybe_run_post_promotion_canary(loop_name=loop_name, state=state, score=score, cfg=cfg)
        if rollback_summary:
            state = get_adaptation_loop_state(loop_name) or state

        if score.example_count < int(cfg.get("min_examples_to_train", 64) or 64):
            return _finish_loop(loop_name, status="idle", decision="skipped", reason="insufficient_examples", metrics={"example_count": score.example_count, "quality_score": score.quality_score})
        structured_examples = int((score.quality_details or {}).get("structured_examples") or score.example_count)
        min_structured_examples = int(cfg.get("min_structured_examples", 0) or 0)
        if min_structured_examples > 0 and structured_examples < min_structured_examples:
            return _finish_loop(
                loop_name,
                status="idle",
                decision="skipped",
                reason="insufficient_structured_examples",
                metrics={"example_count": score.example_count, "structured_examples": structured_examples, "quality_score": score.quality_score},
            )
        high_signal_examples = int((score.quality_details or {}).get("high_signal_examples") or score.example_count)
        min_high_signal_examples = int(cfg.get("min_high_signal_examples", 0) or 0)
        if min_high_signal_examples > 0 and high_signal_examples < min_high_signal_examples:
            return _finish_loop(
                loop_name,
                status="idle",
                decision="skipped",
                reason="insufficient_high_signal_examples",
                metrics={"example_count": score.example_count, "high_signal_examples": high_signal_examples, "quality_score": score.quality_score},
            )
        conversation_ratio = float((score.quality_details or {}).get("conversation_ratio") or 0.0)
        max_conversation_ratio = float(cfg.get("max_conversation_ratio", 1.0) or 1.0)
        if max_conversation_ratio < 1.0 and conversation_ratio > max_conversation_ratio:
            return _finish_loop(
                loop_name,
                status="idle",
                decision="skipped",
                reason="conversation_ratio_too_high",
                metrics={"example_count": score.example_count, "conversation_ratio": conversation_ratio, "quality_score": score.quality_score},
            )
        if score.quality_score < float(cfg.get("min_quality_score", 0.68) or 0.68):
            return _finish_loop(loop_name, status="idle", decision="skipped", reason="quality_threshold_not_met", metrics={"example_count": score.example_count, "quality_score": score.quality_score})

        if not force and previous_hash and previous_hash == score.content_hash:
            return _finish_loop(loop_name, status="idle", decision="skipped", reason="no_new_data_since_last_tick", metrics={"example_count": score.example_count, "quality_score": score.quality_score})
        if not force and previous_examples and (score.example_count - previous_examples) < int(cfg.get("min_new_examples_since_last_job", 16) or 16):
            return _finish_loop(
                loop_name,
                status="idle",
                decision="skipped",
                reason="insufficient_new_examples_since_last_job",
                metrics={"example_count": score.example_count, "new_examples": score.example_count - previous_examples, "quality_score": score.quality_score},
            )

        job = create_adaptation_job(
            corpus_id=score.corpus_id,
            base_model_ref=base.base_model_ref,
            base_provider_name=base.base_provider_name,
            base_model_name=base.base_model_name,
            adapter_provider_name=str(cfg.get("adapter_provider_name") or "vool-adapted"),
            adapter_model_name=_autopilot_model_name(prefix=str(cfg.get("adapter_model_prefix") or "vool-loop"), corpus_hash=score.content_hash),
            training_config={
                "license_name": base.license_name,
                "license_reference": base.license_reference,
                "capabilities": list(cfg.get("capabilities") or []),
                "epochs": max(1, int(cfg.get("epochs", 1) or 1)),
                "max_steps": max(1, int(cfg.get("max_steps", 32) or 32)),
                "batch_size": max(1, int(cfg.get("batch_size", 1) or 1)),
                "gradient_accumulation_steps": max(1, int(cfg.get("gradient_accumulation_steps", 4) or 4)),
                "learning_rate": float(cfg.get("learning_rate", 0.0002) or 0.0002),
                "cutoff_len": max(128, int(cfg.get("cutoff_len", 768) or 768)),
                "lora_r": max(1, int(cfg.get("lora_r", 8) or 8)),
                "lora_alpha": max(1, int(cfg.get("lora_alpha", 16) or 16)),
                "lora_dropout": max(0.0, float(cfg.get("lora_dropout", 0.05) or 0.05)),
                "logging_steps": max(1, int(cfg.get("logging_steps", 4) or 4)),
                "eval_holdout_examples": max(0, int(cfg.get("eval_holdout_examples", 12) or 12)),
                "canary_holdout_examples": max(0, int(cfg.get("canary_holdout_examples", 8) or 8)),
                "min_train_examples_after_holdout": max(1, int(cfg.get("min_train_examples_after_holdout", 24) or 24)),
            },
            metadata={
                "autopilot_loop": loop_name,
                "corpus_hash": score.content_hash,
                "quality_score": score.quality_score,
                "quality_details": score.quality_details,
            },
        )
        upsert_adaptation_loop_state(loop_name, active_job_id=job["job_id"], status="training")
        append_adaptation_job_event(job["job_id"], "autopilot_triggered", "Automatic adaptation loop triggered a new training job.", {"loop_name": loop_name, "quality_score": score.quality_score, "content_hash": score.content_hash})

        job = run_adaptation_job(job["job_id"])
        if str(job.get("status") or "") != "completed":
            return _finish_loop(loop_name, status="failed", decision="failed", reason="adaptation_training_failed", metrics={"job_id": job["job_id"], "error_text": job.get("error_text") or ""})

        promotion_eval = evaluate_adaptation_job(job["job_id"], eval_kind="promotion_gate", max_samples=int(cfg.get("max_eval_samples", 12) or 12))
        upsert_adaptation_loop_state(loop_name, last_eval_id=promotion_eval.eval_id)
        if promotion_eval.decision != "promote_candidate":
            return _finish_loop(
                loop_name,
                status="idle",
                decision="rejected",
                reason="promotion_eval_rejected_candidate",
                metrics={"job_id": job["job_id"], "eval": promotion_eval.metrics},
            )

        canary_eval = evaluate_adaptation_job(job["job_id"], eval_kind="pre_promotion_canary", max_samples=int(cfg.get("max_canary_samples", 8) or 8))
        upsert_adaptation_loop_state(loop_name, last_canary_eval_id=canary_eval.eval_id)
        if canary_eval.decision != "canary_pass":
            return _finish_loop(
                loop_name,
                status="idle",
                decision="rejected",
                reason="pre_promotion_canary_failed",
                metrics={"job_id": job["job_id"], "canary": canary_eval.metrics},
            )

        previous_active = _active_promoted_adaptation_manifest()
        promoted = promote_adaptation_job(job["job_id"])
        _disable_other_promoted_adaptations(promoted_job_id=job["job_id"])
        if bool(cfg.get("publish_metadata_to_hive", True)):
            _publish_adapter_metadata(job=promoted, eval_summary=promotion_eval, canary_summary=canary_eval, action="promoted", cfg=cfg)
        return _finish_loop(
            loop_name,
            status="promoted",
            decision="promoted",
            reason="candidate_promoted_after_eval_and_canary",
            active_job_id=promoted["job_id"],
            active_provider_name=str((promoted.get("registered_manifest") or {}).get("provider_name") or ""),
            active_model_name=str((promoted.get("registered_manifest") or {}).get("model_name") or ""),
            previous_job_id=str(previous_active.get("job_id") or "") if previous_active else "",
            previous_provider_name=str(previous_active.get("provider_name") or "") if previous_active else "",
            previous_model_name=str(previous_active.get("model_name") or "") if previous_active else "",
            last_eval_id=promotion_eval.eval_id,
            last_canary_eval_id=canary_eval.eval_id,
            metrics={
                "job_id": promoted["job_id"],
                "promotion_eval": promotion_eval.metrics,
                "pre_promotion_canary": canary_eval.metrics,
                "quality_score": score.quality_score,
            },
        )
    except Exception as exc:
        audit_logger.log(
            "adaptation_autopilot_error",
            target_id=loop_name,
            target_type="adaptation",
            details={"error": str(exc)},
        )
        return _finish_loop(loop_name, status="failed", decision="failed", reason="autopilot_exception", last_error_text=str(exc))


def score_adaptation_corpus(corpus_id: str, output_path: str) -> CorpusScore:
    corpus = get_adaptation_corpus(corpus_id)
    if not corpus:
        raise ValueError(f"Unknown adaptation corpus: {corpus_id}")
    rows = load_adaptation_examples(output_path)
    scored = score_adaptation_rows(rows)
    quality_score = float(scored.get("quality_score") or 0.0)
    content_hash = hashlib.sha256(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows).encode("utf-8")
    ).hexdigest()
    details = dict(scored.get("details") or {})
    update_corpus_analysis(
        corpus_id,
        quality_score=quality_score,
        quality_details=details,
        content_hash=content_hash,
        last_scored_at=_utcnow(),
    )
    return CorpusScore(
        corpus_id=corpus_id,
        example_count=len(rows),
        content_hash=content_hash,
        quality_score=quality_score,
        quality_details=details,
    )


def evaluate_adaptation_job(job_id: str, *, eval_kind: str, max_samples: int = 12) -> EvalSummary:
    job = get_adaptation_job(job_id)
    if not job:
        raise ValueError(f"Unknown adaptation job: {job_id}")
    split_name, examples = _resolve_eval_examples(job, eval_kind=eval_kind, max_samples=max_samples)
    manifest = dict(job.get("registered_manifest") or {})
    adapter_path = str((manifest.get("runtime_config") or {}).get("adapter_path") or "").strip()
    eval_run = create_adaptation_eval_run(
        job_id=job_id,
        corpus_id=str(job.get("corpus_id") or ""),
        eval_kind=eval_kind,
        split_name=split_name,
        sample_count=len(examples),
        baseline_provider_ref=str(job.get("base_model_ref") or ""),
        candidate_provider_ref=f"{manifest.get('provider_name', '')}:{manifest.get('model_name', '')}",
        metrics={"max_samples": max_samples},
    )
    try:
        if not examples:
            raise RuntimeError(f"No evaluation examples available for {eval_kind}.")
        baseline_outputs = _generate_eval_outputs(
            base_model_ref=str(job.get("base_model_ref") or ""),
            adapter_path=None,
            examples=examples,
            max_new_tokens=int((job.get("training_config") or {}).get("inference_max_new_tokens") or 192),
        )
        candidate_outputs = _generate_eval_outputs(
            base_model_ref=str(job.get("base_model_ref") or ""),
            adapter_path=adapter_path,
            examples=examples,
            max_new_tokens=int((job.get("training_config") or {}).get("inference_max_new_tokens") or 192),
        )
        sample_metrics = _score_outputs(examples=examples, baseline_outputs=baseline_outputs, candidate_outputs=candidate_outputs)
        baseline_score = sample_metrics["baseline_score"]
        candidate_score = sample_metrics["candidate_score"]
        score_delta = sample_metrics["score_delta"]
        decision = _eval_decision(eval_kind=eval_kind, baseline_score=baseline_score, candidate_score=candidate_score, cfg=get_adaptation_policy())
        metrics = dict(sample_metrics)
        metrics["split_name"] = split_name
        update_adaptation_eval_run(
            eval_run["eval_id"],
            status="completed",
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            score_delta=score_delta,
            metrics=metrics,
            decision=decision,
            completed_at=_utcnow(),
        )
        append_adaptation_job_event(
            job_id,
            "adaptation_eval_completed",
            f"{eval_kind} completed with decision {decision}.",
            {"eval_id": eval_run["eval_id"], "baseline_score": baseline_score, "candidate_score": candidate_score, "score_delta": score_delta, "split_name": split_name},
        )
        update_adaptation_job(job_id, metadata={f"last_{eval_kind}_eval_id": eval_run["eval_id"], f"last_{eval_kind}_decision": decision})
        return EvalSummary(
            eval_id=eval_run["eval_id"],
            eval_kind=eval_kind,
            decision=decision,
            sample_count=len(examples),
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            score_delta=score_delta,
            metrics=metrics,
        )
    except Exception as exc:
        update_adaptation_eval_run(
            eval_run["eval_id"],
            status="failed",
            decision="failed",
            error_text=str(exc),
            completed_at=_utcnow(),
        )
        append_adaptation_job_event(job_id, "adaptation_eval_failed", f"{eval_kind} failed.", {"eval_id": eval_run["eval_id"], "error": str(exc)})
        raise


def rollback_adaptation_job(job_id: str, *, reason: str, loop_name: str = "default") -> dict[str, Any]:
    job = get_adaptation_job(job_id)
    if not job:
        raise ValueError(f"Unknown adaptation job: {job_id}")
    manifest = dict(job.get("registered_manifest") or {})
    provider_name = str(manifest.get("provider_name") or "").strip()
    model_name = str(manifest.get("model_name") or "").strip()
    state = get_adaptation_loop_state(loop_name) or {}
    if provider_name and model_name:
        set_provider_manifest_enabled(
            provider_name,
            model_name,
            enabled=False,
            metadata_updates={"adaptation_promoted": False, "adaptation_rolled_back": True, "rollback_ts": _utcnow(), "rollback_reason": reason},
        )
    previous_provider = str(state.get("previous_provider_name") or "").strip()
    previous_model = str(state.get("previous_model_name") or "").strip()
    if previous_provider and previous_model:
        set_provider_manifest_enabled(previous_provider, previous_model, enabled=True, metadata_updates={"restored_by_adaptation_rollback": True})
    update_adaptation_job(
        job_id,
        status="rolled_back",
        metadata={"rollback_reason": reason, "rollback_ts": _utcnow()},
        rolled_back_at=_utcnow(),
    )
    append_adaptation_job_event(job_id, "job_rolled_back", "Adapted provider rolled back after canary regression.", {"reason": reason})
    result = upsert_adaptation_loop_state(
        loop_name,
        status="rolled_back",
        active_job_id=str(state.get("previous_job_id") or ""),
        active_provider_name=previous_provider,
        active_model_name=previous_model,
        last_reason=reason,
        last_decision="rolled_back",
    )
    return {"ok": True, "status": "rolled_back", "loop_state": result, "job_id": job_id}


def _ensure_loop_corpus(cfg: dict[str, Any]) -> dict[str, Any]:
    label = str(cfg.get("default_corpus_label") or "autopilot-default").strip() or "autopilot-default"
    existing = next((row for row in list_adaptation_corpora(limit=128) if str(row.get("label") or "") == label), None)
    if existing:
        refreshed = update_corpus_spec(
            existing["corpus_id"],
            source_config={
                "include_useful_outputs": True,
                "include_conversations": True,
                "include_final_responses": True,
                "include_hive_posts": True,
                "include_task_results": True,
                "limit_per_source": max(1, int(cfg.get("limit_per_source", 256) or 256)),
            },
            filters={
                "min_instruction_chars": 12,
                "min_output_chars": 24,
                "max_instruction_chars": 6000,
                "max_output_chars": 12000,
                "min_signal_score": 0.34,
                "conversation_min_signal_score": 0.38,
                "max_duplicate_output_fingerprint": 2,
                "max_duplicate_output_fingerprint_conversation": 1,
                "max_conversation_share_when_structured_present": 0.45,
                "max_conversation_ratio": float(cfg.get("max_conversation_ratio", 0.45) or 0.45),
                "min_structured_examples": int(cfg.get("min_structured_examples", 16) or 16),
                "min_high_signal_examples": int(cfg.get("min_high_signal_examples", 10) or 10),
            },
        )
        return refreshed or existing
    return create_adaptation_corpus(
        label=label,
        source_config={
            "include_useful_outputs": True,
            "include_conversations": True,
            "include_final_responses": True,
            "include_hive_posts": True,
            "include_task_results": True,
            "limit_per_source": max(1, int(cfg.get("limit_per_source", 256) or 256)),
        },
        filters={
            "min_instruction_chars": 12,
            "min_output_chars": 24,
            "max_instruction_chars": 6000,
            "max_output_chars": 12000,
            "min_signal_score": 0.34,
            "conversation_min_signal_score": 0.38,
            "max_duplicate_output_fingerprint": 2,
            "max_duplicate_output_fingerprint_conversation": 1,
            "max_conversation_share_when_structured_present": 0.45,
            "max_conversation_ratio": float(cfg.get("max_conversation_ratio", 0.45) or 0.45),
            "min_structured_examples": int(cfg.get("min_structured_examples", 16) or 16),
            "min_high_signal_examples": int(cfg.get("min_high_signal_examples", 10) or 10),
        },
    )


def _resolve_base_model(cfg: dict[str, Any]) -> BaseModelResolution:
    explicit_ref = str(cfg.get("base_model_ref") or "").strip()
    explicit_provider = str(cfg.get("base_provider_name") or "").strip()
    explicit_model = str(cfg.get("base_model_name") or "").strip()
    explicit_license = str(cfg.get("license_name") or "").strip()
    explicit_license_ref = str(cfg.get("license_reference") or "").strip()
    if explicit_ref:
        explicit_ref = _normalize_model_ref(explicit_ref)
        return BaseModelResolution(
            base_model_ref=explicit_ref,
            base_provider_name=explicit_provider,
            base_model_name=explicit_model,
            license_name=explicit_license,
            license_reference=explicit_license_ref,
        )
    project_root = Path(_project_root())
    runtime_project_root = Path(__file__).resolve().parents[1]

    # When the effective project root is overridden (for tests or isolated runs),
    # prefer that root's local fallback over unrelated staged models under the
    # caller's persistent runtime home.
    if project_root.resolve() != runtime_project_root.resolve():
        project_scoped_fallback = _local_fallback_base_model(project_root=project_root, prefer_project_fallback=True)
        if project_scoped_fallback is not None:
            return project_scoped_fallback

    fallback_base = _local_fallback_base_model(project_root=project_root)
    if fallback_base is not None:
        return fallback_base
    registry = ModelRegistry()
    for manifest in registry.list_manifests(enabled_only=True, limit=128):
        runtime_family = str(manifest.metadata.get("runtime_family") or "").strip().lower()
        model_path = _normalize_model_ref(str(manifest.runtime_config.get("model_path") or "").strip())
        if manifest.source_type == "local_path" and (runtime_family == "transformers" or manifest.adapter_type == "optional_transformers") and model_path:
            return BaseModelResolution(
                base_model_ref=model_path,
                base_provider_name=manifest.provider_name,
                base_model_name=manifest.model_name,
                license_name=str(manifest.license_name or ""),
                license_reference=str(manifest.resolved_license_reference or ""),
            )
    return BaseModelResolution(base_model_ref="", base_provider_name="", base_model_name="", license_name="", license_reference="")


def _normalize_model_ref(model_ref: str) -> str:
    candidate = str(model_ref or "").strip()
    if not candidate:
        return ""
    path = Path(candidate)
    if path.is_absolute():
        return str(path)
    if candidate.startswith("./") or candidate.startswith("../") or "/" in candidate:
        return str((_project_root() / candidate).resolve())
    return candidate


def _local_fallback_base_model(
    *,
    project_root: Path | None = None,
    prefer_project_fallback: bool = False,
) -> BaseModelResolution | None:
    effective_project_root = Path(project_root or _project_root())
    fallback_candidates = (
        effective_project_root / "data" / "trainable_models" / "sshleifer-tiny-gpt2",
        Path(tempfile.gettempdir()) / "vool_tiny_gpt2",
    )
    if prefer_project_fallback:
        for candidate in fallback_candidates:
            if not (candidate / "config.json").exists():
                continue
            return BaseModelResolution(
                base_model_ref=str(candidate),
                base_provider_name="vool-test-base",
                base_model_name="sshleifer-tiny-gpt2",
                license_name="unknown-test-only",
                license_reference="https://huggingface.co/sshleifer/tiny-gpt2",
            )
    try:
        from core.trainable_base_manager import best_staged_trainable_base

        staged = best_staged_trainable_base()
    except Exception:
        staged = None
    if staged is not None:
        return BaseModelResolution(
            base_model_ref=str(staged.get("local_path") or ""),
            base_provider_name=str(staged.get("provider_name") or "vool-trainable-base"),
            base_model_name=str(staged.get("model_name") or ""),
            license_name=str(staged.get("license_name") or ""),
            license_reference=str(staged.get("license_reference") or ""),
        )
    for candidate in fallback_candidates:
        if not (candidate / "config.json").exists():
            continue
        return BaseModelResolution(
            base_model_ref=str(candidate),
            base_provider_name="vool-test-base",
            base_model_name="sshleifer-tiny-gpt2",
            license_name="unknown-test-only",
            license_reference="https://huggingface.co/sshleifer/tiny-gpt2",
        )
    return None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tick_within_interval(*, state: dict[str, Any], tick_interval_seconds: int) -> bool:
    last_completed = _parse_ts(state.get("last_completed_tick_at"))
    if last_completed is None:
        return False
    return (datetime.now(timezone.utc) - last_completed).total_seconds() < max(1, int(tick_interval_seconds))


def _finish_loop(
    loop_name: str,
    *,
    status: str,
    decision: str,
    reason: str,
    last_error_text: str = "",
    metrics: dict[str, Any] | None = None,
    active_job_id: str | None = None,
    active_provider_name: str | None = None,
    active_model_name: str | None = None,
    previous_job_id: str | None = None,
    previous_provider_name: str | None = None,
    previous_model_name: str | None = None,
    last_eval_id: str | None = None,
    last_canary_eval_id: str | None = None,
    touch_completed: bool = True,
) -> dict[str, Any]:
    payload = upsert_adaptation_loop_state(
        loop_name,
        status=status,
        active_job_id=active_job_id,
        active_provider_name=active_provider_name,
        active_model_name=active_model_name,
        previous_job_id=previous_job_id,
        previous_provider_name=previous_provider_name,
        previous_model_name=previous_model_name,
        last_eval_id=last_eval_id,
        last_canary_eval_id=last_canary_eval_id,
        last_completed_tick_at=_utcnow() if touch_completed else None,
        last_decision=decision,
        last_reason=reason,
        last_error_text=last_error_text,
        metrics=metrics,
    )
    audit_logger.log(
        "adaptation_autopilot_tick",
        target_id=loop_name,
        target_type="adaptation",
        details={"status": status, "decision": decision, "reason": reason, "error": last_error_text, **dict(metrics or {})},
    )
    return payload


def _resolve_eval_examples(job: dict[str, Any], *, eval_kind: str, max_samples: int) -> tuple[str, list[dict[str, Any]]]:
    metadata = dict(job.get("metadata") or {})
    if eval_kind == "promotion_gate":
        path = str(metadata.get("eval_output_path") or "").strip()
        return "eval", load_adaptation_examples(path)[: max(1, int(max_samples))] if path else []
    if eval_kind == "pre_promotion_canary":
        path = str(metadata.get("canary_output_path") or "").strip()
        return "canary", load_adaptation_examples(path)[: max(1, int(max_samples))] if path else []
    if eval_kind == "post_promotion_canary":
        corpus = get_adaptation_corpus(str(job.get("corpus_id") or "")) or {}
        path = str(corpus.get("output_path") or "").strip()
        rows = load_adaptation_examples(path)
        promoted_at = _parse_ts(job.get("promoted_at"))
        fresh = _fresh_examples_since(rows, since=promoted_at, limit=max_samples)
        return "fresh_canary", fresh
    raise ValueError(f"Unsupported eval kind: {eval_kind}")


def _generate_eval_outputs(*, base_model_ref: str, adapter_path: str | None, examples: list[dict[str, Any]], max_new_tokens: int) -> list[str]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    device = dependency_status().device
    base_model_ref = _normalize_model_ref(base_model_ref)
    tokenizer_ref = _normalize_model_ref(str(adapter_path or base_model_ref).strip())
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_ref)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token
    model = AutoModelForCausalLM.from_pretrained(base_model_ref)
    if adapter_path:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter_path)
    model.to(device)
    model.eval()
    outputs: list[str] = []
    with torch.no_grad():
        for row in examples:
            prompt = _build_eval_prompt(tokenizer, str(row.get("instruction") or ""))
            encoded = tokenizer(prompt, return_tensors="pt")
            encoded = {name: tensor.to(device) for name, tensor in encoded.items()}
            generation = {
                "max_new_tokens": max(8, int(max_new_tokens)),
                "do_sample": False,
                "temperature": 0.0,
                "pad_token_id": int(
                    tokenizer.pad_token_id or tokenizer.eos_token_id or 0
                ),
                "eos_token_id": int(
                    tokenizer.eos_token_id or tokenizer.pad_token_id or 0
                ),
            }
            permit = seal_direct_provider_invocation(
                provider_id="transformers:adaptation-eval",
                model_id=base_model_ref,
                operation="adaptation_evaluation",
                payload={
                    "prompt": prompt,
                    "generation": generation,
                    "adapter_path": str(adapter_path or ""),
                },
                request_id=(
                    "adaptation-eval-"
                    + hashlib.sha256(
                        (
                            f"{base_model_ref}\0{adapter_path or ''}\0{prompt}"
                        ).encode()
                    ).hexdigest()
                ),
                max_output_tokens=generation["max_new_tokens"],
            )
            authorized = permit.consume()
            generated_ids = model.generate(
                **encoded,
                **dict(authorized["generation"]),
            )
            answer_ids = generated_ids[0][encoded["input_ids"].shape[1]:]
            outputs.append(tokenizer.decode(answer_ids, skip_special_tokens=True).strip())
    return outputs


def _build_eval_prompt(tokenizer: Any, instruction: str) -> str:
    if hasattr(tokenizer, "apply_chat_template"):
        try:
            return str(tokenizer.apply_chat_template([{"role": "user", "content": instruction}], tokenize=False, add_generation_prompt=True))
        except Exception:
            pass
    return f"### Instruction:\n{instruction}\n\n### Response:\n"


def _score_outputs(*, examples: list[dict[str, Any]], baseline_outputs: list[str], candidate_outputs: list[str]) -> dict[str, Any]:
    baseline_scores: list[float] = []
    candidate_scores: list[float] = []
    breakdown: list[dict[str, Any]] = []
    for index, row in enumerate(examples):
        reference = str(row.get("output") or "")
        baseline_text = baseline_outputs[index] if index < len(baseline_outputs) else ""
        candidate_text = candidate_outputs[index] if index < len(candidate_outputs) else ""
        baseline_score = _response_score(prediction=baseline_text, reference=reference)
        candidate_score = _response_score(prediction=candidate_text, reference=reference)
        baseline_scores.append(baseline_score)
        candidate_scores.append(candidate_score)
        breakdown.append(
            {
                "index": index,
                "source": str(row.get("source") or ""),
                "baseline_score": round(baseline_score, 4),
                "candidate_score": round(candidate_score, 4),
                "baseline_preview": baseline_text[:160],
                "candidate_preview": candidate_text[:160],
            }
        )
    baseline_mean = round(sum(baseline_scores) / max(1, len(baseline_scores)), 4)
    candidate_mean = round(sum(candidate_scores) / max(1, len(candidate_scores)), 4)
    return {
        "baseline_score": baseline_mean,
        "candidate_score": candidate_mean,
        "score_delta": round(candidate_mean - baseline_mean, 4),
        "sample_breakdown": breakdown,
    }


def _response_score(*, prediction: str, reference: str) -> float:
    pred = str(prediction or "").strip()
    ref = str(reference or "").strip()
    if not ref:
        return 0.0
    lexical = _token_f1(pred, ref)
    semantic = evaluate_semantic_agreement(pred, ref)
    exact_bonus = 0.08 if pred == ref and pred else 0.0
    return max(0.0, min(1.0, (0.45 * lexical) + (0.47 * semantic) + exact_bonus))


def _token_f1(a: str, b: str) -> float:
    a_tokens = _norm_tokens(a)
    b_tokens = _norm_tokens(b)
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = len(a_tokens & b_tokens)
    if overlap <= 0:
        return 0.0
    precision = overlap / len(a_tokens)
    recall = overlap / len(b_tokens)
    return (2 * precision * recall) / max(1e-9, precision + recall)


def _norm_tokens(text: str) -> set[str]:
    return {token for token in "".join(ch if ch.isalnum() else " " for ch in str(text or "").lower()).split() if len(token) > 2}


def _eval_decision(*, eval_kind: str, baseline_score: float, candidate_score: float, cfg: dict[str, Any]) -> str:
    delta = candidate_score - baseline_score
    if eval_kind == "promotion_gate":
        if candidate_score >= float(cfg.get("min_candidate_eval_score", 0.55) or 0.55) and delta >= float(cfg.get("promotion_margin", 0.03) or 0.03):
            return "promote_candidate"
        return "reject_candidate"
    if eval_kind == "pre_promotion_canary":
        if candidate_score >= float(cfg.get("min_candidate_canary_score", 0.52) or 0.52) and delta >= 0.0:
            return "canary_pass"
        return "canary_fail"
    if eval_kind == "post_promotion_canary":
        if candidate_score >= float(cfg.get("min_candidate_canary_score", 0.52) or 0.52) and delta >= (0.0 - float(cfg.get("rollback_margin", 0.04) or 0.04)):
            return "keep_live"
        return "rollback"
    return "unknown"


def _fresh_examples_since(rows: list[dict[str, Any]], *, since: datetime | None, limit: int) -> list[dict[str, Any]]:
    if since is None:
        return list(rows[-max(1, int(limit)):])
    fresh = [row for row in rows if (_example_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc)) > since]
    return fresh[-max(1, int(limit)) :]


def _example_timestamp(row: dict[str, Any]) -> datetime | None:
    metadata = dict(row.get("metadata") or {})
    for key in ("ts", "created_at"):
        candidate = _parse_ts(metadata.get(key))
        if candidate is not None:
            return candidate
    return None


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _maybe_run_post_promotion_canary(*, loop_name: str, state: dict[str, Any], score: CorpusScore, cfg: dict[str, Any]) -> dict[str, Any] | None:
    active_job_id = str(state.get("active_job_id") or "").strip()
    if not active_job_id:
        return None
    active_job = get_adaptation_job(active_job_id)
    if not active_job or str(active_job.get("status") or "") != "promoted":
        return None
    trained_corpus_hash = str((active_job.get("metadata") or {}).get("corpus_hash") or "").strip()
    if trained_corpus_hash == score.content_hash:
        return None
    trained_examples = int((active_job.get("metadata") or {}).get("corpus_total_examples") or 0)
    if (score.example_count - trained_examples) < int(cfg.get("post_promotion_canary_min_new_examples", 8) or 8):
        return None
    summary = evaluate_adaptation_job(active_job_id, eval_kind="post_promotion_canary", max_samples=int(cfg.get("max_canary_samples", 8) or 8))
    upsert_adaptation_loop_state(loop_name, last_canary_eval_id=summary.eval_id)
    if summary.decision == "rollback":
        rollback = rollback_adaptation_job(active_job_id, reason="post_promotion_canary_regression", loop_name=loop_name)
        if bool(cfg.get("publish_metadata_to_hive", True)):
            _publish_adapter_metadata(job=active_job, eval_summary=summary, canary_summary=summary, action="rolled_back", cfg=cfg)
        return rollback
    return {"ok": True, "status": "keep_live", "eval_id": summary.eval_id}


def _active_promoted_adaptation_manifest() -> dict[str, Any] | None:
    for manifest in ModelRegistry().list_manifests(enabled_only=True, limit=256):
        if bool(manifest.metadata.get("adaptation_promoted")):
            return {
                "provider_name": manifest.provider_name,
                "model_name": manifest.model_name,
                "job_id": str(manifest.metadata.get("adaptation_job_id") or ""),
            }
    return None


def _disable_other_promoted_adaptations(*, promoted_job_id: str) -> None:
    registry = ModelRegistry()
    current = get_adaptation_job(promoted_job_id) or {}
    current_manifest = dict(current.get("registered_manifest") or {})
    current_provider = str(current_manifest.get("provider_name") or "").strip()
    current_model = str(current_manifest.get("model_name") or "").strip()
    for manifest in registry.list_manifests(enabled_only=True, limit=256):
        if not bool(manifest.metadata.get("adaptation_promoted")):
            continue
        if manifest.provider_name == current_provider and manifest.model_name == current_model:
            continue
        set_provider_manifest_enabled(
            manifest.provider_name,
            manifest.model_name,
            enabled=False,
            metadata_updates={"adaptation_promoted": False, "superseded_by_job_id": promoted_job_id, "superseded_ts": _utcnow()},
        )


def _publish_adapter_metadata(*, job: dict[str, Any], eval_summary: EvalSummary, canary_summary: EvalSummary, action: str, cfg: dict[str, Any]) -> None:
    bridge = PublicHiveBridge()
    if not bridge.write_enabled():
        return
    manifest = dict(job.get("registered_manifest") or {})
    provider = str(manifest.get("provider_name") or "").strip()
    model = str(manifest.get("model_name") or "").strip()
    if not provider or not model:
        return
    metadata = dict(job.get("metadata") or {})
    summary = (
        f"{action.title()} adapter {provider}:{model}. "
        f"Quality {float(metadata.get('quality_score') or 0.0):.2f}. "
        f"Eval {eval_summary.candidate_score:.2f} vs baseline {eval_summary.baseline_score:.2f}. "
        f"Canary {canary_summary.candidate_score:.2f} vs baseline {canary_summary.baseline_score:.2f}."
    )
    public_body = (
        f"Adapter action: {action}.\n"
        f"Provider: {provider}:{model}.\n"
        f"Base model: {_safe_base_model_label(str(job.get('base_model_ref') or ''))}.\n"
        f"Examples: {int(metadata.get('train_example_count') or 0)} train / {int(metadata.get('eval_example_count') or 0)} eval / {int(metadata.get('canary_example_count') or 0)} canary.\n"
        f"Corpus quality: {float(metadata.get('quality_score') or 0.0):.4f}.\n"
        f"Promotion eval delta: {eval_summary.score_delta:.4f}.\n"
        f"Canary delta: {canary_summary.score_delta:.4f}."
    )
    result = bridge.publish_agent_commons_update(
        topic=str(cfg.get("hive_topic") or "VOOL Model Adaptation"),
        topic_kind="model_adaptation",
        summary=summary,
        public_body=public_body,
        topic_tags=["model_adaptation", "lora", action],
    )
    if result.get("ok"):
        upsert_adaptation_loop_state("default", last_metadata_publish_at=_utcnow())


def _autopilot_model_name(*, prefix: str, corpus_hash: str) -> str:
    clean_prefix = str(prefix or "vool-loop").strip() or "vool-loop"
    return f"{clean_prefix}-{str(corpus_hash or '')[:8]}"


def _safe_base_model_label(base_model_ref: str) -> str:
    candidate = str(base_model_ref or "").strip()
    if not candidate:
        return "unknown"
    return Path(candidate).name or candidate


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
