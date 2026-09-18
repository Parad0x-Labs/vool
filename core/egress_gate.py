"""F1-B canonical egress gate — ONE widening/exposure authority per boundary.

The four-field segment contract (segment_id, authority_class, principal_scope,
exposure_class) is metadata set by FIRST-PARTY assembly code only. Segment TEXT
never carries authority. Adapters are projections: representation may change;
exposure class may not widen.

Law:
- LOCAL_ONLY / SECRET segments are DROPPED (not redacted) at ineligible
  destinations.
- An UNCLASSIFIED or partially-classified segment is REFUSED (fail closed) —
  it never defaults to public.
- Every lane that moves payload out of the process calls THIS gate; no lane
  teaches itself its own privacy policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

AUTHORITY_CLASSES = frozenset({"system", "user", "assistant", "tool", "memory", "derived"})
EXPOSURE_CLASSES = frozenset({"PUBLIC", "LOCAL_ONLY", "SECRET"})
# Destination classes a lane may declare for its wire boundary.
DESTINATION_CLASSES = frozenset({"local_process", "cloud_provider", "channel_public"})

SEGMENT_META_KEY = "_egress_segment"


class EgressRefused(RuntimeError):
    """Fail-closed egress: unclassified/ineligible segment at the boundary."""


@dataclass(frozen=True)
class EgressSegment:
    segment_id: str
    authority_class: str
    principal_scope: str
    exposure_class: str

    def validated(self) -> "EgressSegment":
        if self.authority_class not in AUTHORITY_CLASSES:
            raise EgressRefused(f"unclassified authority_class: {self.authority_class!r}")
        if self.exposure_class not in EXPOSURE_CLASSES:
            raise EgressRefused(f"unclassified exposure_class: {self.exposure_class!r}")
        scope = str(self.principal_scope or "").strip()
        if not scope:
            raise EgressRefused("missing principal_scope")
        return self


def _destination_allows(exposure_class: str, destination_class: str) -> bool:
    # LOCAL_ONLY never leaves the process. SECRET is owner-local eyes only.
    if destination_class == "local_process":
        return True
    if exposure_class in ("LOCAL_ONLY", "SECRET"):
        return False
    return True


def project_for_destination(
    items: Sequence[tuple[Any, EgressSegment | dict[str, Any] | None]],
    *,
    destination_class: str,
) -> list[Any]:
    """Project (payload, segment) pairs onto a destination.

    - eligible segments pass through unchanged;
    - LOCAL_ONLY/SECRET segments at ineligible destinations are DROPPED;
    - missing or malformed segment metadata is REFUSED (fail closed).
    """
    if destination_class not in DESTINATION_CLASSES:
        raise EgressRefused(f"unknown destination_class: {destination_class!r}")
    out: list[Any] = []
    for payload, segment in items:
        if segment is None:
            raise EgressRefused(
                "unclassified segment refused at egress boundary "
                f"(destination={destination_class})"
            )
        if isinstance(segment, dict):
            try:
                segment = EgressSegment(
                    segment_id=str(segment.get("segment_id") or ""),
                    authority_class=str(segment.get("authority_class") or ""),
                    principal_scope=str(segment.get("principal_scope") or ""),
                    exposure_class=str(segment.get("exposure_class") or ""),
                )
            except Exception as exc:
                raise EgressRefused(f"malformed segment metadata: {exc}") from exc
        segment = segment.validated()
        if _destination_allows(segment.exposure_class, destination_class):
            out.append(payload)
        # else: dropped, not redacted.
    return out


def project_messages_for_destination(
    messages: list[dict[str, Any]], *, destination_class: str
) -> list[dict[str, Any]]:
    """Canonical projection for chat-message payloads leaving the process.

    A message is either first-party assembled WITHOUT segment metadata (plain
    conversation turn: user/assistant/system text — allowed by definition) or
    it CARRIES ``_egress_segment`` metadata, which must be fully classified.
    A PARTIAL/malformed segment stamp is refused, never defaulted.
    """
    if destination_class not in DESTINATION_CLASSES:
        raise EgressRefused(f"unknown destination_class: {destination_class!r}")
    out: list[dict[str, Any]] = []
    for message in messages:
        meta = message.get(SEGMENT_META_KEY)
        if meta is None:
            out.append(message)
            continue
        projected = project_for_destination([(message, meta)], destination_class=destination_class)
        out.extend(projected)
    return out
