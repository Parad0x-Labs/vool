from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from itertools import islice
from typing import Any
from urllib.parse import urlparse

from core import policy_engine
from core.agent_runtime.request_authority import bounded_evidence
from core.social_source_policy import evaluate_social_source
from core.source_credibility import evaluate_source_domain
from storage.media_evidence_log import record_media_evidence
from tools.browser.browser_render import browser_render
from tools.web.http_fetch import http_fetch_text

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


@dataclass
class MediaEvidence:
    reference: str
    media_kind: str
    source_kind: str
    source_domain: str
    credibility: dict[str, Any]
    social_policy: dict[str, Any]
    text: str = ""
    caption: str = ""
    transcript: str = ""
    blocked: bool = False
    requires_multimodal: bool = False
    metadata: dict[str, Any] = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = dict(self.metadata or {})
        return payload


def ingest_media_evidence(
    *,
    task_id: str,
    trace_id: str,
    user_input: str,
    source_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    source_context = dict(source_context or {})
    fetch_text_references = bool(source_context.get("fetch_text_references")) and policy_engine.allow_web_fallback()
    # ONE budget for the whole turn, shared by every origin, and enforced HERE so a library caller
    # invoking this function directly is bounded without needing `VoolAgent` or the channel gateway
    # in front of it. The loop below normalizes, fetches and PERSISTS each surviving item, so an
    # unbounded stream is fifty thousand `media_evidence_log` rows and, with `fetch_text_references`
    # on, fifty thousand outbound requests.
    #
    # Attachments are taken first and contextual URLs spend WHAT IS LEFT -- 64 attachments leaves
    # no URL slots, 63 leaves one. Production order is preserved and nothing is prioritised by what
    # it contains; the budget is spent in arrival order and runs out where it runs out. `finditer`
    # rather than `findall` so a message full of URLs is scanned only as far as the budget reaches.
    evidence = bounded_evidence(source_context.get("external_evidence"))
    raw_items = list(evidence.items)
    for match in islice(_URL_RE.finditer(user_input or ""), evidence.remaining):
        url = match.group(0)
        raw_items.append({"kind": _infer_kind_from_url(url), "url": url})

    ingested: list[dict[str, Any]] = []
    for item in raw_items:
        evidence = _normalize_item(item, fetch_text_reference=fetch_text_references)
        if not evidence:
            continue
        record_media_evidence(
            task_id=task_id,
            trace_id=trace_id,
            source_kind=evidence.source_kind,
            source_domain=evidence.source_domain,
            media_kind=evidence.media_kind,
            reference=evidence.reference,
            credibility_score=float(evidence.credibility.get("score") or 0.0),
            blocked=bool(evidence.blocked),
            metadata={
                "caption": evidence.caption,
                "has_text": bool(evidence.text),
                "has_transcript": bool(evidence.transcript),
                "requires_multimodal": bool(evidence.requires_multimodal),
                **dict(evidence.metadata or {}),
            },
        )
        ingested.append(evidence.to_dict())
    return ingested


def build_media_context_snippets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for item in items:
        if item.get("blocked"):
            continue
        domain = item.get("source_domain") or "unknown"
        confidence = float(dict(item.get("credibility") or {}).get("score") or 0.0)
        social_reason = str(dict(item.get("social_policy") or {}).get("reason") or "").strip()
        summary = item.get("text") or item.get("caption") or item.get("transcript") or f"{item.get('media_kind', 'media')} reference"
        details = f"Source {domain}. {social_reason}".strip()
        snippets.append(
            {
                "title": f"External {item.get('media_kind', 'media')} evidence",
                "source_type": f"external_{item.get('media_kind', 'media')}",
                "summary": f"{summary[:240]} {details}".strip(),
                "confidence": confidence,
                "metadata": {
                    "reference": item.get("reference"),
                    "source_domain": domain,
                    "requires_multimodal": bool(item.get("requires_multimodal")),
                    "blocked": bool(item.get("blocked")),
                },
            }
        )
    return snippets


def build_multimodal_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for item in items:
        if item.get("blocked"):
            continue
        media_kind = str(item.get("media_kind") or "")
        if media_kind not in {"image", "video"}:
            continue
        attachments.append(
            {
                "kind": media_kind,
                "url": item.get("reference"),
                "caption": item.get("caption") or item.get("text") or "",
                "transcript": item.get("transcript") or "",
                "label": f"{media_kind} evidence from {item.get('source_domain') or 'unknown'}",
            }
        )
    return attachments


def _evidence_blocked(credibility: dict[str, Any], social_policy: dict[str, Any]) -> bool:
    """Whether policy refuses to carry this source's content to a model.

    Stamped onto `MediaEvidence.blocked` by `_normalize_item`, and honoured downstream:
    `build_media_context_snippets` and `build_multimodal_attachments` both skip a blocked item, so
    its content reaches no model at all.

    Nothing in this module answers "may this become the user's request" any more -- that question
    belongs to `core/agent_runtime/request_authority.py`, and its answer for evidence is no.  An
    earlier version read `caption`/`transcript`/`text`/`post_text` off an item and handed them to
    the task envelope as a goal, which granted command authority on the strength of a dictionary
    key.  Evidence informs a request; it is never the request.
    """
    return bool(credibility.get("blocked")) or (
        social_policy.get("platform") != "unknown"
        and not bool(social_policy.get("allowed_for_orientation", True))
    )


def media_kind_for_reference(reference: str) -> str:
    """The media kind a bare reference implies -- the same inference `_normalize_item` uses."""
    return _infer_kind_from_url(reference)


def _external_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (bool, int, float)):
        return str(value).strip()
    return ""


_INVALID_METADATA = object()


def _neutral_metadata(value: Any, *, depth: int = 0) -> Any:
    """A bounded JSON-safe view of untrusted extra fields, or a sentinel to omit the value."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if depth >= 4:
        return _INVALID_METADATA
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        try:
            pairs = islice(value.items(), 64)
            for key, nested in pairs:
                if not isinstance(key, str):
                    continue
                normalized = _neutral_metadata(nested, depth=depth + 1)
                if normalized is not _INVALID_METADATA:
                    result[key] = normalized
        except Exception:
            return _INVALID_METADATA
        return result
    if isinstance(value, (list, tuple)):
        result = []
        try:
            for nested in islice(iter(value), 64):
                normalized = _neutral_metadata(nested, depth=depth + 1)
                if normalized is not _INVALID_METADATA:
                    result.append(normalized)
        except Exception:
            return _INVALID_METADATA
        return result
    return _INVALID_METADATA


def _normalize_item(item: Any, *, fetch_text_reference: bool = False) -> MediaEvidence | None:
    if not isinstance(item, Mapping):
        return None
    try:
        item_values = dict(item)
    except Exception:
        return None
    reference = _external_text(
        item_values.get("url") or item_values.get("path") or item_values.get("reference")
    )
    text = _external_text(item_values.get("text") or item_values.get("post_text"))
    caption = _external_text(item_values.get("caption"))
    transcript = _external_text(item_values.get("transcript"))
    media_kind = _external_text(item_values.get("kind") or _infer_kind_from_url(reference)).lower() or "text"
    domain = _domain_from_reference(reference)
    credibility = evaluate_source_domain(domain).to_dict()
    social_policy = evaluate_social_source(domain).to_dict()
    blocked = _evidence_blocked(credibility, social_policy)
    requires_multimodal = media_kind in {"image", "video"} and not transcript and not text
    source_kind = "social" if social_policy.get("platform") not in {"unknown", "social"} else "web"
    if media_kind == "social_post":
        source_kind = "social"
    if not reference and not text and not caption and not transcript:
        return None
    metadata: dict[str, Any] = {}
    for key, value in item_values.items():
        if key in {"url", "path", "reference", "text", "post_text", "caption", "transcript", "kind"}:
            continue
        if not isinstance(key, str):
            continue
        normalized_metadata = _neutral_metadata(value)
        if normalized_metadata is not _INVALID_METADATA:
            metadata[key] = normalized_metadata
    if fetch_text_reference and reference and media_kind == "text" and not text and not caption and not transcript and not blocked:
        fetched = _fetch_reference_text(reference)
        text = fetched.get("text", text)
        metadata.update(
            {
                "fetch_status": fetched.get("status", "fetch_error"),
                "used_browser": bool(fetched.get("used_browser")),
                "final_url": fetched.get("final_url"),
            }
        )
    return MediaEvidence(
        reference=reference or f"inline:{media_kind}",
        media_kind=media_kind,
        source_kind=source_kind,
        source_domain=domain,
        credibility=credibility,
        social_policy=social_policy,
        text=text,
        caption=caption,
        transcript=transcript,
        blocked=blocked,
        requires_multimodal=requires_multimodal,
        metadata=metadata,
    )


def _infer_kind_from_url(url: str) -> str:
    lower = (url or "").lower()
    if any(token in lower for token in ("x.com/", "twitter.com/", "facebook.com/", "instagram.com/", "reddit.com/")):
        return "social_post"
    if any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "image"
    if any(lower.endswith(ext) for ext in (".mp4", ".mov", ".webm", ".mkv")) or "youtube.com/" in lower or "youtu.be/" in lower:
        return "video"
    return "text"


def _domain_from_reference(reference: str) -> str:
    if not reference or reference.startswith("inline:"):
        return ""
    parsed = urlparse(reference)
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _fetch_reference_text(reference: str) -> dict[str, Any]:
    # R2b2b: the page transport goes through the ONE door, which refuses a
    # fetch with no active ledger. Media ingestion runs both inside turns
    # (the scope defers to the turn's ledger) and on its own background
    # passes — this entry point owns those by name.
    from core.effect_gateway import named_background_effect_scope

    try:
        with named_background_effect_scope("media.ingest.fetch"):
            fetched = http_fetch_text(reference)
    except Exception as exc:
        return {"status": f"fetch_error:{type(exc).__name__}", "text": "", "used_browser": False, "final_url": reference}

    status = str(fetched.get("status") or "fetch_error")
    text = str(fetched.get("text") or "")
    if status == "ok" and len(text.strip()) >= 600:
        return {"status": status, "text": text[:200000], "used_browser": False, "final_url": reference}
    if not policy_engine.allow_browser_fallback():
        return {"status": status, "text": text[:200000], "used_browser": False, "final_url": reference}

    rendered = browser_render(reference, engine=policy_engine.browser_engine())
    rendered_status = str(rendered.get("status") or "fetch_error")
    if rendered_status == "ok":
        return {
            "status": rendered_status,
            "text": str(rendered.get("text") or "")[:200000],
            "used_browser": True,
            "final_url": rendered.get("final_url") or reference,
        }
    return {"status": rendered_status, "text": text[:200000], "used_browser": rendered_status != "disabled", "final_url": rendered.get("final_url") or reference}
