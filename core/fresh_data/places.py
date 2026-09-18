"""Explicit local-service discovery contract.

Place discovery is external observation.  It is not workspace evidence and there is intentionally
no fallback to ``workspace.search_text``.  A provider is injected or configured explicitly; absent
configuration produces a structured unavailable report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


class PlaceSearchStatus(str, Enum):
    AVAILABLE = "available"
    NO_RESULTS = "no_results"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PlaceResult:
    name: str
    service: str
    location: str
    address: str = ""
    latitude: str = ""
    longitude: str = ""
    source: str = ""
    source_url: str = ""
    evidence_excerpt: str = ""

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class PlaceSearchReport:
    service: str
    location: str
    status: PlaceSearchStatus
    results: tuple[PlaceResult, ...] = ()
    source: str = ""
    retrieved_at: str = ""
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "location": self.location,
            "status": self.status.value,
            "results": [item.to_dict() for item in self.results],
            "source": self.source,
            "retrieved_at": self.retrieved_at,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class PlaceSearchRequest:
    service: str
    location: str


class PlaceProvider(Protocol):
    name: str

    def search(
        self,
        request: PlaceSearchRequest,
        *,
        timeout_s: float = 8.0,
    ) -> PlaceSearchReport: ...


_PLACE_FRAMES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:find|search(?:\s+for)?|look\s+for|locate|show\s+me)\s+"
        r"(?P<service>.+?)\s+(?:in|near|around|close\s+to)\s+(?P<location>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:where\s+can\s+i\s+find|where\s+is|what\s+.+?\s+is\s+near)\s+"
        r"(?P<service>.+?)\s+(?:in|near|around|close\s+to)\s+(?P<location>.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^where\s+can\s+i\s+"
        r"(?P<service>(?:change|repair|replace|service|fit|buy|get|have)\s+.+?)\s+"
        r"(?:in|near|around|close\s+to)\s+(?P<location>.+?)\s*$",
        re.IGNORECASE,
    ),
)


def parse_place_search_request(text: str) -> PlaceSearchRequest | None:
    clean = " ".join(str(text or "").strip().strip(".?!").split())
    for frame in _PLACE_FRAMES:
        match = frame.match(clean)
        if not match:
            continue
        service = match.group("service").strip(" ,;:-")
        location = match.group("location").strip(" ,;:-")
        if service and location and service.casefold() != location.casefold():
            return PlaceSearchRequest(service=service, location=location)
    return None


def unavailable_place_search(
    request: PlaceSearchRequest,
    reason: str = "no place-search provider is configured",
    *,
    source: str = "unconfigured",
) -> PlaceSearchReport:
    return PlaceSearchReport(
        service=request.service,
        location=request.location,
        status=PlaceSearchStatus.UNAVAILABLE,
        source=source,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        failure_reason=reason,
    )


__all__ = [
    "PlaceProvider",
    "PlaceResult",
    "PlaceSearchReport",
    "PlaceSearchRequest",
    "PlaceSearchStatus",
    "parse_place_search_request",
    "unavailable_place_search",
]
