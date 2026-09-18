from __future__ import annotations

from typing import Any


def build_hive_create_pending_variants(
    agent: Any,
    *,
    raw_input: str,
    draft: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    improved_title = agent._clean_hive_title(str(draft.get("title") or "").strip())
    improved_summary = str(draft.get("summary") or "").strip() or improved_title
    improved_copy = agent._prepare_public_hive_topic_copy(
        raw_input=raw_input,
        title=improved_title,
        summary=improved_summary,
        mode="improved",
    )
    if not bool(improved_copy.get("ok")):
        return improved_copy

    improved_variant = agent._normalize_hive_create_variant(
        title=str(improved_copy.get("title") or improved_title).strip() or improved_title,
        summary=str(improved_copy.get("summary") or improved_summary).strip() or improved_summary,
        topic_tags=[
            str(item).strip()
            for item in list(draft.get("topic_tags") or [])
            if str(item).strip()
        ][:8],
        auto_start_research=bool(draft.get("auto_start_research")),
        preview_note=str(improved_copy.get("preview_note") or ""),
    )

    original_variant: dict[str, Any] | None = None
    original_blocked_reason = ""
    original_draft = agent._extract_original_hive_topic_create_draft(raw_input)
    if original_draft is not None:
        same_title = str(original_draft.get("title") or "").strip() == str(improved_variant.get("title") or "").strip()
        same_summary = str(original_draft.get("summary") or "").strip() == str(improved_variant.get("summary") or "").strip()
        if not (same_title and same_summary):
            original_copy = agent._prepare_public_hive_topic_copy(
                raw_input=raw_input,
                title=str(original_draft.get("title") or "").strip(),
                summary=str(original_draft.get("summary") or "").strip()
                or str(original_draft.get("title") or "").strip(),
                mode="original",
            )
            if bool(original_copy.get("ok")):
                original_variant = agent._normalize_hive_create_variant(
                    title=str(original_copy.get("title") or "").strip(),
                    summary=str(original_copy.get("summary") or "").strip(),
                    topic_tags=[
                        str(item).strip()
                        for item in list(original_draft.get("topic_tags") or [])
                        if str(item).strip()
                    ][:8],
                    auto_start_research=bool(original_draft.get("auto_start_research")),
                    preview_note=str(original_copy.get("preview_note") or ""),
                )
            else:
                original_blocked_reason = str(original_copy.get("response") or "").strip()

    pending = {
        "title": str(improved_variant.get("title") or "").strip(),
        "summary": str(improved_variant.get("summary") or "").strip(),
        "topic_tags": list(improved_variant.get("topic_tags") or []),
        "task_id": str(task_id or "").strip(),
        "auto_start_research": bool(improved_variant.get("auto_start_research")),
        "default_variant": "improved",
        "variants": {"improved": improved_variant},
        "original_blocked_reason": original_blocked_reason,
    }
    if original_variant is not None:
        pending["variants"]["original"] = original_variant
    return {"ok": True, "pending": pending}


def normalize_hive_create_variant(
    agent: Any,
    *,
    title: str,
    summary: str,
    topic_tags: list[str],
    auto_start_research: bool,
    preview_note: str = "",
) -> dict[str, Any]:
    resolved_title = str(title or "").strip()[:180]
    resolved_summary = str(summary or "").strip()[:4000] or resolved_title
    resolved_tags = [
        str(item).strip()
        for item in list(topic_tags or [])[:8]
        if str(item).strip()
    ]
    if not resolved_tags and resolved_title:
        resolved_tags = agent._infer_hive_topic_tags(resolved_title)
    return {
        "title": resolved_title,
        "summary": resolved_summary,
        "topic_tags": resolved_tags[:8],
        "auto_start_research": bool(auto_start_research),
        "preview_note": str(preview_note or "").strip(),
    }
