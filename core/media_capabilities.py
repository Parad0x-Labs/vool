"""Media Studio capability declarations (A5 graph).

Registers the bounded media.* editing capability family. CapabilityIds name
WHAT the platform can do; ImplementationIds name WHERE it is implemented
(nebula_media.*). Availability reflects the worker's truthful state — it is
NOT a permission grant: authorization stays in the contract/permission lane
(runtime_tool_contracts + mode_permission_policy).
"""

from __future__ import annotations

from typing import Any

_MEDIA_CAPABILITIES: list[dict[str, str]] = [
    # (capability_id, family, label)
    ("media.inspect", "media", "Inspect media files"),
    ("media.video.trim", "media", "Trim video start/end"),
    ("media.video.split", "media", "Split video at time points"),
    ("media.video.crop", "media", "Crop video region"),
    ("media.video.resize", "media", "Resize video dimensions"),
    ("media.video.compress", "media", "Compress video"),
    ("media.audio.extract", "media", "Extract audio track"),
    ("media.audio.trim", "media", "Trim audio with video"),
    ("media.audio.volume", "media", "Set volume / mute"),
    ("media.audio.normalize", "media", "Normalize audio loudness"),
    ("media.preview", "media", "Render editor previews"),
    ("media.export", "media", "Export edited media"),
]

_registered = False


def register_media_capabilities(*, available: bool | None = None) -> dict[str, Any]:
    """Idempotently register the media edit family in the capability graph."""
    global _registered
    from core.capability_graph import (
        Capability,
        CapabilityFamily,
        CapabilityId,
        Implementation,
        ImplementationId,
        register_capability,
        register_family,
        register_implementation,
    )

    if available is None:
        try:
            from core.media_worker_client import get_media_worker

            available = bool(get_media_worker().availability().get("available"))
        except Exception:
            available = False
    reason = "nebula-media worker reachable" if available \
        else "nebula-media worker unavailable"

    register_family(CapabilityFamily(
        id="media",
        label="Media production",
        description="Local media inspection and non-destructive editing via nebula-media.",
    ))
    registered: list[str] = []
    for cap_id, _family, label in _MEDIA_CAPABILITIES:
        register_capability(Capability(
            id=CapabilityId(cap_id), family="media", label=label,
            description=label + " (non-destructive; source asset immutable).",
        ))
        impl_id = cap_id.replace("media.", "nebula_media.", 1)
        register_implementation(Implementation(
            id=ImplementationId(impl_id),
            capability_id=CapabilityId(cap_id),
            tool_intent=_tool_intent_for(cap_id),
            label=f"{label} — nebula-media",
            provider="builtin",
            source="builtin",
            available=bool(available),
            availability_reason=reason,
        ))
        registered.append(cap_id)
    _registered = True
    return {"family": "media", "capabilities": registered,
            "available": bool(available), "reason": reason}


def _tool_intent_for(cap_id: str) -> str:
    mapping = {
        "media.inspect": "media.inspect",
        "media.video.trim": "media.edit",
        "media.video.split": "media.edit",
        "media.video.crop": "media.edit",
        "media.video.resize": "media.edit",
        "media.video.compress": "media.export",
        "media.audio.extract": "media.export",
        "media.audio.trim": "media.edit",
        "media.audio.volume": "media.edit",
        "media.audio.normalize": "media.edit",
        "media.preview": "media.preview",
        "media.export": "media.export",
    }
    return mapping.get(cap_id, "media.edit")


def media_capability_snapshot() -> dict[str, Any]:
    """Bounded view for tests/UI: capability ids, implementation ids, availability.

    Deliberately contains NO codec catalogs and NO permission grants.
    """
    from core.capability_graph import all_implementations

    out = []
    for impl in all_implementations():
        if not str(getattr(impl, "id", "")).startswith("nebula_media."):
            continue
        out.append({
            "capability_id": str(impl.capability_id),
            "implementation_id": str(impl.id),
            "available": bool(impl.available),
            "availability_reason": str(impl.availability_reason),
        })
    return {"implementations": out}


__all__ = ["media_capability_snapshot", "register_media_capabilities"]
