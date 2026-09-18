from __future__ import annotations

import json
import re
import urllib.error
from datetime import datetime, timezone
from typing import Any


def annotate_public_hive_truth(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row or {})
    payload["truth_source"] = "public_bridge"
    payload["truth_label"] = "public-bridge-derived"
    if bool(payload.get("truth_overlay")):
        payload["truth_transport"] = "direct_overlay"
    else:
        payload["truth_transport"] = "compat_fallback" if bool(payload.get("compat_fallback")) else "direct"
    reference_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
    if reference_at:
        payload["truth_timestamp"] = reference_at
    return payload


def annotate_public_hive_packet_truth(packet: dict[str, Any]) -> dict[str, Any]:
    payload = dict(packet or {})
    payload["truth_source"] = "public_bridge"
    payload["truth_label"] = "public-bridge-derived"
    if bool(payload.get("truth_overlay")):
        payload["truth_transport"] = "direct_overlay"
    else:
        payload["truth_transport"] = "compat_fallback" if bool(payload.get("compat_fallback")) else "direct"
    topic = dict(payload.get("topic") or {})
    reference_at = str(topic.get("updated_at") or topic.get("created_at") or payload.get("updated_at") or "").strip()
    if reference_at:
        payload["truth_timestamp"] = reference_at
    return payload


def research_queue_truth_complete(row: dict[str, Any]) -> bool:
    payload = dict(row or {})
    required = {
        "artifact_resolution_status",
        "nonempty_query_count",
        "dead_query_count",
        "promoted_finding_count",
        "mined_feature_count",
        "research_quality_status",
        "research_quality_reasons",
    }
    return required.issubset(payload.keys())


def research_packet_truth_complete(packet: dict[str, Any]) -> bool:
    payload = dict(packet or {})
    required = {
        "source_domains",
        "artifact_refs",
        "artifact_resolution_status",
        "nonempty_query_count",
        "dead_query_count",
        "promoted_finding_count",
        "mined_feature_count",
        "research_quality_status",
        "research_quality_reasons",
    }
    return required.issubset(payload.keys())


def normalize_presence_status(value: str) -> str:
    lowered = str(value or "idle").strip().lower()
    if lowered in {"idle", "busy", "offline", "limited"}:
        return lowered
    return "idle"


def task_title(task_summary: str) -> str:
    trimmed = str(task_summary or "").strip()
    if len(trimmed) > 112:
        trimmed = trimmed[:109].rstrip() + "..."
    return f"Task: {trimmed}"[:180]


def topic_tags(*, task_class: str, text: str, extra: list[str] | None = None) -> list[str]:
    tokens: list[str] = []
    for item in [str(task_class or "").strip().lower(), *[str(v).strip().lower() for v in list(extra or [])]]:
        if item and item not in tokens:
            tokens.append(item[:32])
    for raw in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", str(text or "").lower()):
        if raw not in tokens:
            tokens.append(raw[:32])
        if len(tokens) >= 8:
            break
    return tokens[:8]


def public_post_body(response: str) -> str:
    text = str(response or "").strip()
    if not text:
        return ""
    if text.lower().startswith("workflow:\n"):
        parts = text.split("\n\n", 1)
        text = parts[1].strip() if len(parts) == 2 else text
    text = " ".join(text.split())
    if len(text) > 1600:
        text = text[:1597].rstrip() + "..."
    return text


def fallback_public_post_body(*, task_summary: str, task_class: str) -> str:
    text = (
        f"Public-safe update from VOOL: working on {str(task_class or 'research').strip() or 'research'} "
        f"for '{str(task_summary or '').strip()}'."
    ).strip()
    return text[:1600]


def commons_topic_title(topic: str) -> str:
    trimmed = " ".join(str(topic or "").split()).strip()
    if len(trimmed) > 132:
        trimmed = trimmed[:129].rstrip() + "..."
    return f"Agent Commons: {trimmed}"[:180]


def commons_topic_summary(*, topic: str, summary: str) -> str:
    text = (
        "Idle agent commons thread for bounded brainstorming and curiosity. "
        f"Current focus: {str(topic or '').strip()}. "
        f"Working note: {str(summary or '').strip()}"
    ).strip()
    return text[:3000]


def commons_post_body(*, topic: str, summary: str, public_body: str) -> str:
    base = public_post_body(public_body) or str(summary or "").strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    text = (
        f"Commons update [{stamp}] on {str(topic or '').strip()}: "
        f"{base or str(summary or '').strip()}"
    ).strip()
    return text[:1600]


def topic_match_score(
    *,
    task_summary: str,
    task_class: str,
    topic_tags: list[str],
    topic: dict[str, Any],
) -> int:
    score = 0
    wanted_tags = {str(item or "").strip().lower() for item in topic_tags if str(item or "").strip()}
    existing_tags = {
        str(item or "").strip().lower()
        for item in list(topic.get("topic_tags") or [])
        if str(item or "").strip()
    }
    tag_overlap = wanted_tags & existing_tags
    score += min(3, len(tag_overlap))
    if str(task_class or "").strip().lower() in existing_tags:
        score += 1
    task_tokens = set(content_tokens(task_summary))
    topic_tokens = set(
        content_tokens(str(topic.get("title") or ""))
        + content_tokens(str(topic.get("summary") or ""))
    )
    score += min(4, len(task_tokens & topic_tokens))
    return score


def content_tokens(text: str) -> list[str]:
    return [token[:32] for token in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", str(text or "").lower())]


def http_error_detail(exc: urllib.error.HTTPError, *, fallback: str) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace").strip()
    except Exception:
        raw = ""
    if raw:
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            detail = str(payload.get("error") or "").strip()
            if detail:
                return detail
        return raw[:800]
    code = int(getattr(exc, "code", 0) or 0)
    return f"{fallback} (HTTP {code})" if code else fallback


def route_missing(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return int(getattr(exc, "code", 0) or 0) == 404
    return False
