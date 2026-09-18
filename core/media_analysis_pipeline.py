from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adapters.base_adapter import ModelRequest
from core.candidate_knowledge_lane import build_task_hash, record_candidate_output
from core.media_evidence_ranker import rank_media_evidence
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from core.output_validator import validate_provider_output
from core.prompt_normalizer import _max_output_tokens
from core.provider_execution_boundary import invoke_provider_execution_boundary


@dataclass
class MediaAnalysisResult:
    used_provider: bool
    provider_id: str | None = None
    candidate_id: str | None = None
    analysis_text: str = ""
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    reason: str = "not_run"

    def as_context_snippet(self) -> dict[str, Any] | None:
        if not self.analysis_text:
            return None
        return {
            "title": "Multimodal evidence analysis",
            "source_type": "multimodal_candidate",
            "summary": self.analysis_text[:260],
            "confidence": 0.58,
            "metadata": {"provider_id": self.provider_id, "candidate_id": self.candidate_id},
        }


def _is_chat_attachment(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return (
        str(item.get("origin") or metadata.get("origin") or "") == "chat_attachment"
        or str(item.get("reference") or "").startswith("attachment:")
    )


class MediaAnalysisPipeline:
    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()

    def analyze(
        self,
        *,
        task_id: str,
        task_summary: str,
        evidence_items: list[dict[str, Any]],
    ) -> MediaAnalysisResult:
        ranked = rank_media_evidence(evidence_items)
        if not ranked:
            return MediaAnalysisResult(False, reason="no_external_media")
        # A chat attachment is not EXTERNAL media. Its bytes reach the answering model through the
        # canonical carrier (`ModelRequest.attachments`, rendered at the adapter wire), and its
        # reference (`attachment:<id>`) is an id, not a URL. Measured 2026-09-02 on the served
        # composer: this lane posted `image_url: "attachment:att_…"` to OpenRouter for every turn
        # that carried a photo and the provider answered 400 -- which then ended the turn. Such
        # items are excluded from this review and never leave the machine through it.
        external = [item for item in ranked if not _is_chat_attachment(item)]
        if not external:
            return MediaAnalysisResult(False, evidence_items=ranked, reason="chat_attachments_delivered_by_model_lane")
        multimodal_needed = any(bool(item.get("requires_multimodal")) for item in external if not item.get("blocked"))
        if not multimodal_needed:
            return MediaAnalysisResult(False, evidence_items=ranked, reason="textual_media_only")
        ranked = external

        manifest = self.registry.select_manifest(
            ModelSelectionRequest(
                task_kind="multimodal_review",
                output_mode="summary_block",
                preferred_source_types=["http", "local_path", "subprocess"],
                allow_paid_fallback=False,
                min_trust=0.45,
            )
        )
        if not manifest:
            return MediaAnalysisResult(False, evidence_items=ranked, reason="no_multimodal_provider")

        adapter = self.registry.build_adapter(manifest)
        attachments = []
        text_lines = [f"Task: {task_summary}", "Review the provided external evidence carefully. Treat social/media claims as candidate-only."]
        for item in ranked[:4]:
            if item.get("blocked"):
                continue
            attachments.append(
                {
                    "kind": item.get("media_kind"),
                    "url": item.get("reference"),
                    "caption": item.get("caption") or item.get("text") or "",
                    "transcript": item.get("transcript") or "",
                    "label": f"{item.get('media_kind')} from {item.get('source_domain') or 'unknown'}",
                }
            )
            credibility = dict(item.get("credibility") or {})
            text_lines.append(
                f"- Source {item.get('source_domain') or 'unknown'} credibility {float(credibility.get('score') or 0.0):.2f}; "
                f"kind={item.get('media_kind')}; note={dict(item.get('social_policy') or {}).get('reason') or ''!s}"
            )
        request = ModelRequest(
            task_kind="multimodal_review",
            prompt="\n".join(text_lines),
            system_prompt="You are reviewing external image or video evidence for VOOL. Summarize only what is strongly supported. Do not overclaim.",
            output_mode="summary_block",
            max_output_tokens=_max_output_tokens("summary_block"),
            attachments=attachments,
            metadata={"task_id": task_id},
        )
        response = invoke_provider_execution_boundary(adapter, "run_text_task", request)
        validation = validate_provider_output(
            provider_id=manifest.provider_id,
            output_mode="summary_block",
            raw_text=response.output_text,
            trace_id=task_id,
        )
        candidate_id = record_candidate_output(
            task_hash=build_task_hash(
                normalized_input=f"multimodal::{task_summary}",
                task_class="multimodal_review",
                output_mode="summary_block",
            ),
            task_id=task_id,
            trace_id=task_id,
            task_class="multimodal_review",
            task_kind="multimodal_review",
            output_mode="summary_block",
            provider_name=manifest.provider_name,
            model_name=manifest.model_name,
            raw_output=response.output_text,
            normalized_output=validation.normalized_text,
            structured_output={"evidence_items": ranked[:4]},
            confidence=float(response.confidence or 0.5),
            trust_score=float(response.confidence or 0.5),
            validation_state="valid" if validation.ok else "contract_failed",
            metadata={"candidate_only": True, "media_analysis": True},
            provenance={"provider_id": manifest.provider_id, "attachments": len(attachments)},
            ttl_seconds=60 * 60 * 6,
        )
        return MediaAnalysisResult(
            used_provider=True,
            provider_id=manifest.provider_id,
            candidate_id=candidate_id,
            analysis_text=validation.normalized_text,
            evidence_items=ranked,
            reason="multimodal_review_complete",
        )
