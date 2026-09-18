from __future__ import annotations

from typing import Any


def build_hive_create_pending_payload(
    agent: Any,
    pending: dict[str, Any],
    *,
    fallback_task_id: str = "",
) -> dict[str, Any]:
    variants = {
        key: agent._normalize_hive_create_variant(
            title=str(dict(value).get("title") or ""),
            summary=str(dict(value).get("summary") or ""),
            topic_tags=[
                str(item).strip()
                for item in list(dict(value).get("topic_tags") or [])
                if str(item).strip()
            ][:8],
            auto_start_research=bool(dict(value).get("auto_start_research")),
            preview_note=str(dict(value).get("preview_note") or ""),
        )
        for key, value in dict(pending.get("variants") or {}).items()
        if isinstance(value, dict)
    }
    if not variants and str(pending.get("title") or "").strip():
        variants["improved"] = agent._normalize_hive_create_variant(
            title=str(pending.get("title") or "").strip(),
            summary=str(pending.get("summary") or "").strip()
            or str(pending.get("title") or "").strip(),
            topic_tags=[
                str(item).strip()
                for item in list(pending.get("topic_tags") or [])
                if str(item).strip()
            ][:8],
            auto_start_research=bool(pending.get("auto_start_research")),
            preview_note=str(pending.get("preview_note") or ""),
        )
    improved = dict(variants.get("improved") or {})
    return {
        "title": str(improved.get("title") or pending.get("title") or "").strip(),
        "summary": str(improved.get("summary") or pending.get("summary") or "").strip()
        or str(pending.get("title") or "").strip(),
        "topic_tags": list(improved.get("topic_tags") or [])[:8],
        "task_id": str(pending.get("task_id") or "").strip() or fallback_task_id,
        "auto_start_research": bool(improved.get("auto_start_research") or pending.get("auto_start_research")),
        "default_variant": str(pending.get("default_variant") or "improved"),
        "variants": variants,
        "original_blocked_reason": str(pending.get("original_blocked_reason") or "").strip(),
    }
