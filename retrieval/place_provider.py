"""Place discovery through VOOL's configured, policy-bounded web adapter.

This adapter intentionally returns *candidates*, not authoritative business
records.  The existing web stack labels its notes non-authoritative, so search
snippets may be shown as source evidence but may not be promoted into invented
addresses, coordinates, opening hours, or availability claims.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from core.fresh_data.places import (
    PlaceResult,
    PlaceSearchReport,
    PlaceSearchRequest,
    PlaceSearchStatus,
)

PlaceSearchNotes = Callable[..., Sequence[dict[str, Any]]]


def _configured_web_search(
    query_text: str,
    *,
    limit: int,
    source_label: str,
    total_budget_s: float | None = None,
) -> Sequence[dict[str, Any]]:
    # Lazy import keeps the evidence contract independent of the retrieval
    # implementation and avoids making provider injection pay import-time cost.
    from retrieval.web_adapter import WebAdapter

    return WebAdapter.search_query(
        query_text,
        limit=limit,
        source_label=source_label,
        total_budget_s=total_budget_s,
    )


class ConfiguredWebPlaceProvider:
    """Discover sourced place candidates with the configured web search chain."""

    name = "configured web search"

    def __init__(
        self,
        *,
        search_notes: PlaceSearchNotes | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._search_notes = search_notes or _configured_web_search
        self._now = now or (lambda: datetime.now(timezone.utc))

    def search(
        self,
        request: PlaceSearchRequest,
        *,
        timeout_s: float = 8.0,
    ) -> PlaceSearchReport:
        # The shared web adapter owns provider selection, policy enforcement,
        # fetch accounting, filtering, and the shared wall-clock budget.
        query = f"{request.service} near {request.location}"
        notes = tuple(
            self._search_notes(
                query,
                limit=5,
                source_label="place.search",
                total_budget_s=max(0.5, float(timeout_s)),
            )
            or ()
        )
        results: list[PlaceResult] = []
        seen_urls: set[str] = set()
        providers: list[str] = []
        for note in notes:
            if not isinstance(note, dict):
                continue
            name = " ".join(str(note.get("result_title") or "").split()).strip()
            source_url = str(note.get("result_url") or "").strip()
            if not name or not source_url or source_url in seen_urls:
                continue
            seen_urls.add(source_url)
            source = str(
                note.get("search_provider")
                or note.get("source_label")
                or self.name
            ).strip()
            if source and source not in providers:
                providers.append(source)
            results.append(
                PlaceResult(
                    name=name,
                    service=request.service,
                    location=request.location,
                    source=source,
                    source_url=source_url,
                    evidence_excerpt=" ".join(
                        str(note.get("summary") or "").split()
                    )[:280],
                )
            )

        observed = self._now().astimezone(timezone.utc).isoformat()
        source_summary = ", ".join(providers) or self.name
        if not results:
            return PlaceSearchReport(
                service=request.service,
                location=request.location,
                status=PlaceSearchStatus.NO_RESULTS,
                source=source_summary,
                retrieved_at=observed,
                failure_reason=(
                    "the configured web search returned no sourced place candidates"
                ),
            )
        return PlaceSearchReport(
            service=request.service,
            location=request.location,
            status=PlaceSearchStatus.AVAILABLE,
            results=tuple(results),
            source=source_summary,
            retrieved_at=observed,
        )


__all__ = ["ConfiguredWebPlaceProvider", "PlaceSearchNotes"]
