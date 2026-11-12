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


#: The binaries the worker's operations shell out to (nebula/media_ops.py resolves them on PATH).
_MEDIA_BINARIES = ("ffmpeg", "ffprobe")


def media_tools_present() -> tuple[bool, str]:
    """Whether this machine has the media tools: the nebula-media worker module
    (``nebula/worker.py`` under NEBULA_MEDIA_HOME, else under the vendored
    ``vendor/nebula-media``, else an importable ``nebula`` package) AND the ffmpeg and ffprobe
    binaries on PATH. (present, plain reason). Reads the disk only; starts nothing."""
    import importlib.util
    import shutil
    from pathlib import Path

    from core.media_worker_client import default_nebula_home

    home = default_nebula_home()
    if home:
        worker = (Path(home) / "nebula" / "worker.py").is_file()
        where = f"{home}/nebula/worker.py"
    else:
        worker = importlib.util.find_spec("nebula") is not None
        where = "an importable nebula package"
    if not worker:
        return False, f"no nebula-media worker ({where} not found)"
    missing = [name for name in _MEDIA_BINARIES if not shutil.which(name)]
    if missing:
        return False, f"not on PATH: {', '.join(missing)}"
    return True, f"worker at {where}; {', '.join(_MEDIA_BINARIES)} on PATH"


def register_media_capabilities_if_present() -> dict[str, Any]:
    """Boot hook: register the media family only on a machine that has the media tools.

    Without them nothing is registered: the media ids stay unknown to the graph and the
    media-studio skill is refused by name, as before. With them the family is registered and
    each implementation's availability is the worker's live answer (``register_media_capabilities``
    probes it), so a worker that will not start is recorded as unavailable, never as ready.
    """
    present, reason = media_tools_present()
    if not present:
        return {"family": "media", "registered": False, "reason": reason}
    result = register_media_capabilities()
    return {**result, "registered": True, "tools": reason}


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


__all__ = [
    "media_capability_snapshot",
    "media_tools_present",
    "register_media_capabilities",
    "register_media_capabilities_if_present",
]
