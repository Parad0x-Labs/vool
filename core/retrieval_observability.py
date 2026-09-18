"""Typed runtime proof for generic web retrieval lanes.

FX has its own value-bearing receipt.  Adaptive research and the reasoning fallback previously
left only an audit-log entry, so the public turn trace could report a non-zero transport counter
while exposing no operation or receipt.  This module records the orchestration boundary without
claiming more than it knows: one requested query, its terminal status, and the sources returned.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from core.retrieval_provenance import (
    KEYED,
    STATUS_AVAILABLE,
    STATUS_FAILED,
    STATUS_REFUSED,
    STATUS_STARTED,
    STATUS_UNAVAILABLE,
    attribution_summary,
    lifecycle_for_status,
    provider_attribution,
)
from core.runtime_task_events import emit_runtime_event
from core.secret_redaction import redact_secrets

_FAILURE_REASON_LIMIT = 300


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failure_reason(failure: BaseException | None) -> str:
    """One redacted, bounded line naming why a retrieval failed. Empty when it did not.

    Never raises: an exception whose ``__str__`` itself blows up must not take down the receipt
    that is trying to report it -- losing the reason is bad, losing the whole terminal event is
    worse, because then nothing records that the retrieval ended at all.
    """

    if failure is None:
        return ""
    try:
        text = " ".join(str(failure).split()).strip()
    except Exception:
        return ""
    return redact_secrets(text)[:_FAILURE_REASON_LIMIT]


def _turn_identity(source_context: dict[str, Any] | None) -> dict[str, str]:
    """The request/turn this retrieval belongs to, when the caller has one.

    "When applicable" is literal: a settings probe or a background scope has no
    turn, and stamping an empty string is the honest record of that. Inventing
    an id would attach the effect to a turn that never made it.
    """
    context = source_context if isinstance(source_context, dict) else {}
    return {
        "request_id": str(context.get("request_id") or "")[:160],
        "turn_id": str(context.get("turn_id") or context.get("cancel_turn_id") or "")[:160],
    }


def begin_web_retrieval(
    source_context: dict[str, Any] | None,
    *,
    kind: str,
    query: str,
    task_id: str = "",
    action: str = "search",
    provider_id: str = "",
    keyed_or_keyless: str = "",
) -> dict[str, Any]:
    """Emit and return a secret-safe started receipt for one orchestrated search.

    `provider_id` is accepted here as well as at the terminal because a caller
    that already knows which provider it is about to use (the settings probe)
    should say so on the STARTED row too — otherwise an in-flight or crashed
    retrieval leaves a row that names no provider at all.
    """

    clean_query = " ".join(str(query or "").split()).strip()
    attribution = provider_attribution(
        provider_id,
        has_key=(keyed_or_keyless == KEYED) if keyed_or_keyless else None,
    )
    receipt = {
        "schema": "vool.web_retrieval_receipt.v1",
        "retrieval_id": f"web-retrieval-{uuid.uuid4().hex}",
        "kind": str(kind or "web_search").strip() or "web_search",
        "action": str(action or "search").strip() or "search",
        "task_id": str(task_id or "").strip()[:160],
        "query_hash": hashlib.sha256(clean_query.encode("utf-8")).hexdigest(),
        "status": STATUS_STARTED,
        # WHO answered, and whether a credential paid for it. Absent at base:
        # `search_provider` sat on every note and was never read, so a receipt
        # could report three sources and never say who returned them.
        "provider_id": attribution["provider_id"] if provider_id else "",
        "provider_label": attribution["provider_label"] if provider_id else "",
        "keyed_or_keyless": attribution["keyed_or_keyless"] if provider_id else "",
        "lifecycle": lifecycle_for_status(STATUS_STARTED),
        "source_count": 0,
        "source_domains": [],
        "started_at": _utc_now(),
        "completed_at": "",
        "failure_class": "",
        **_turn_identity(source_context),
    }
    emit_runtime_event(
        source_context,
        event_type="web_retrieval_started",
        message="Started an authorized web retrieval.",
        details=dict(receipt),
    )
    return receipt


def _provider_from_notes(notes: list[dict[str, Any]]) -> str:
    """The provider that actually answered, read off the notes it produced.

    `search_provider` is written onto every note by
    `retrieval/web_adapter.py::_notes_from_research`. It was already there and
    already correct; this writer simply never read it. First one wins because a
    single `web_research` call reports one provider — if a later note disagrees,
    the mixed case is recorded by the caller's own notes list, not smoothed over
    here into a provider nobody used.
    """
    for note in notes:
        provider = str(note.get("search_provider") or "").strip().lower()
        if provider and provider not in {"none", "disabled"}:
            return provider
    return ""


def finish_web_retrieval(
    source_context: dict[str, Any] | None,
    receipt: dict[str, Any],
    *,
    notes: list[dict[str, Any]] | None = None,
    failure: BaseException | None = None,
    refused: bool = False,
) -> dict[str, Any]:
    """Persist and emit exactly one terminal receipt for a started generic search.

    `refused` marks the case a bare failure cannot express: the turn was not
    PERMITTED to fetch. That is a policy fact, not a broken provider, and
    reporting it as `failed` sends the user to fix a key that works.
    """

    clean_notes = [dict(note) for note in list(notes or []) if isinstance(note, dict)]
    domains: list[str] = []
    for note in clean_notes:
        domain = redact_secrets(str(note.get("origin_domain") or "").strip())[:160]
        if domain and domain not in domains:
            domains.append(domain)
    if refused:
        status = STATUS_REFUSED
    elif failure is not None:
        status = STATUS_FAILED
    else:
        status = STATUS_AVAILABLE if clean_notes else STATUS_UNAVAILABLE
    provider_id = _provider_from_notes(clean_notes) or str(receipt.get("provider_id") or "")
    attribution = provider_attribution(provider_id) if provider_id else None
    terminal = dict(receipt)
    terminal.update(
        {
            "status": status,
            # The lifecycle rides BESIDE the status so a reader never has to
            # infer a terminal from a vocabulary it may not share. Both are
            # derived from the same call, so they cannot disagree.
            "lifecycle": lifecycle_for_status(status),
            "provider_id": attribution["provider_id"] if attribution else "",
            "provider_label": attribution["provider_label"] if attribution else "",
            "keyed_or_keyless": attribution["keyed_or_keyless"] if attribution else "",
            "source_count": len(clean_notes),
            "source_domains": domains[:8],
            "completed_at": _utc_now(),
            "failure_class": type(failure).__name__ if failure is not None else "",
            # The exception's MESSAGE, next to its class. A retrieval that died recorded only the
            # type -- "ConnectTimeout" with no host, no URL, no status code -- and the terminal
            # event's own message is the fixed sentence "Web retrieval failed.", so the whole
            # receipt named nothing a reader could act on. Redacted (a failing request URL can
            # carry a key) and bounded; the traceback is deliberately not carried, because this
            # rides to the Activity panel.
            "failure_reason": _failure_reason(failure),
        }
    )
    if isinstance(source_context, dict):
        receipts = source_context.get("web_retrieval_receipts")
        if not isinstance(receipts, list):
            receipts = []
            source_context["web_retrieval_receipts"] = receipts
        receipts.append(terminal)
    # The message is what the Activity row shows before anyone expands it, so
    # it carries the provenance line rather than a fixed sentence. At base every
    # completed retrieval rendered as "Completed web retrieval." with an empty
    # sub-line, and the count and domains the browser had already fetched were
    # dropped on the floor.
    summary = attribution_summary(terminal)
    if status == STATUS_AVAILABLE:
        message = f"Web retrieval: {summary}" if summary else "Completed web retrieval."
    elif status == STATUS_REFUSED:
        message = "Web retrieval refused by policy."
    elif status == STATUS_UNAVAILABLE:
        message = f"Web retrieval returned nothing ({summary})" if summary else "Web retrieval returned nothing."
    else:
        message = f"Web retrieval failed ({summary})." if summary else "Web retrieval failed."
    # The event TYPE keeps its existing meaning — "the call came back" vs "the
    # call raised" — because the Activity panel categorises on it. Whether the
    # retrieval actually delivered is the `lifecycle`/`status` pair's job, and
    # moving that judgement into the type would silently re-file every
    # returned-nothing row for every reader that already keys off it.
    emit_runtime_event(
        source_context,
        event_type=(
            "web_retrieval_failed"
            if (failure is not None or status == STATUS_REFUSED)
            else "web_retrieval_completed"
        ),
        message=message,
        details=dict(terminal),
    )
    return terminal


__all__ = ["begin_web_retrieval", "finish_web_retrieval"]
