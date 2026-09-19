"""MediaEditProject — the ONE non-destructive edit engine for VOOL Media Studio.

Both controllers (the child editor GUI and AI chat tools) produce the SAME typed
operations against this graph; there is no separate "AI edit engine".

Laws encoded here:
- The source asset is immutable. Operations only extend the edit graph.
- Undo/redo are graph-state transitions, not file mutations.
- ActiveMediaSelection carries operational state so prompts like "crop this bit"
  resolve to a typed range instead of model guesswork.

Pure state module: no subprocess, no ffmpeg, no IO beyond hashing helpers used
by callers (kept out on purpose). JSON round-trips via to_dict/from_dict so any
store can persist it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Typed operation kinds. Kept as a closed set: an unknown kind must be rejected,
# never silently stored.
OPERATION_KINDS = {
    "trim",              # {start, end}          working range becomes [start, end)
    "delete_range",      # {start, end}          removes a slice of the working timeline
    "split",             # {at}                  marker op; segments derived from graph
    "crop",              # {x, y, width, height}
    "resize",            # {width, height}
    "set_aspect_ratio",  # {ratio}               16:9 | 9:16 | 1:1 | 4:5
    "rotate",            # {degrees}             90 | 180 | 270
    "set_volume",        # {factor}              0.0 = mute
    "normalize_audio",   # {}
}

ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:5"}

MAX_HISTORY = 200


class MediaEditError(ValueError):
    """Typed rejection of an invalid edit operation."""


@dataclass(frozen=True)
class EditOperation:
    """One typed, actor-agnostic edit operation."""

    kind: str
    params: dict[str, Any]
    actor: str = "gui"          # "gui" | "chat" — recorded for audit only; semantics identical
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": dict(self.params),
                "actor": self.actor, "revision": self.revision}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EditOperation:
        return cls(
            kind=str(raw.get("kind", "")),
            params=dict(raw.get("params") or {}),
            actor=str(raw.get("actor", "gui")),
            revision=int(raw.get("revision", 0)),
        )


def validate_operation(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate one op against its schema. Returns normalized params."""
    if kind not in OPERATION_KINDS:
        raise MediaEditError(f"unknown edit operation '{kind}'")

    def _num(key: str, *, required: bool = True) -> float | None:
        if key not in params:
            if required:
                raise MediaEditError(f"{kind} requires numeric '{key}'")
            return None
        try:
            return float(params[key])
        except (TypeError, ValueError):
            raise MediaEditError(f"{kind}.{key} must be numeric") from None

    if kind == "trim":
        start, end = _num("start"), _num("end")
        assert start is not None and end is not None
        if end <= start:
            raise MediaEditError("trim end must be greater than start")
        return {"start": start, "end": end}
    if kind == "delete_range":
        start, end = _num("start"), _num("end")
        assert start is not None and end is not None
        if end <= start:
            raise MediaEditError("delete_range end must be greater than start")
        return {"start": start, "end": end}
    if kind == "split":
        at = _num("at")
        assert at is not None
        if at < 0:
            raise MediaEditError("split point must be >= 0")
        return {"at": at}
    if kind == "crop":
        x, y = _num("x"), _num("y")
        w, h = _num("width"), _num("height")
        assert x is not None and y is not None and w is not None and h is not None
        if w <= 0 or h <= 0 or x < 0 or y < 0:
            raise MediaEditError("crop rect must be positive and non-negative origin")
        return {"x": int(x), "y": int(y), "width": int(w), "height": int(h)}
    if kind == "resize":
        w, h = _num("width"), _num("height")
        assert w is not None and h is not None
        if w <= 0 or h <= 0:
            raise MediaEditError("resize dimensions must be positive")
        return {"width": int(w), "height": int(h)}
    if kind == "set_aspect_ratio":
        ratio = str(params.get("ratio", ""))
        if ratio not in ASPECT_RATIOS:
            raise MediaEditError(f"aspect ratio must be one of {sorted(ASPECT_RATIOS)}")
        return {"ratio": ratio}
    if kind == "rotate":
        degrees = _num("degrees")
        assert degrees is not None
        if int(degrees) not in {90, 180, 270}:
            raise MediaEditError("rotation must be 90, 180 or 270 degrees")
        return {"degrees": int(degrees)}
    if kind == "set_volume":
        factor = _num("factor")
        assert factor is not None
        if factor < 0.0:
            raise MediaEditError("volume factor cannot be negative")
        return {"factor": min(factor, 10.0)}
    if kind == "normalize_audio":
        return {}
    raise MediaEditError(f"unknown edit operation '{kind}'")  # pragma: no cover


@dataclass
class ActiveMediaSelection:
    """Typed editor context: what "this bit" means right now.

    Chat ops that omit an explicit range resolve against selection_* here, so
    "remove this part" targets the exact user selection deterministically.
    """

    project_id: str = ""
    asset_id: str = ""
    range_start: float = 0.0
    range_end: float = 0.0
    playhead: float = 0.0
    crop_selection: dict[str, Any] | None = None
    track: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "asset_id": self.asset_id,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "playhead": self.playhead,
            "crop_selection": self.crop_selection,
            "track": self.track,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ActiveMediaSelection:
        raw = raw or {}
        return cls(
            project_id=str(raw.get("project_id", "")),
            asset_id=str(raw.get("asset_id", "")),
            range_start=float(raw.get("range_start", 0.0) or 0.0),
            range_end=float(raw.get("range_end", 0.0) or 0.0),
            playhead=float(raw.get("playhead", 0.0) or 0.0),
            crop_selection=raw.get("crop_selection"),
            track=str(raw.get("track", "")),
        )


@dataclass
class SourceAsset:
    """Immutable identity of the media being edited."""

    asset_id: str
    path: str
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)   # worker probe output

    def to_dict(self) -> dict[str, Any]:
        return {"asset_id": self.asset_id, "path": self.path,
                "sha256": self.sha256, "metadata": self.metadata}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SourceAsset:
        return cls(
            asset_id=str(raw["asset_id"]),
            path=str(raw["path"]),
            sha256=str(raw["sha256"]),
            metadata=dict(raw.get("metadata") or {}),
        )


@dataclass
class MediaEditProject:
    """Non-destructive edit state for one source asset."""

    project_id: str
    source_asset: SourceAsset
    edit_revision: int = 0
    operations: list[EditOperation] = field(default_factory=list)
    undo_stack: list[EditOperation] = field(default_factory=list)
    redo_stack: list[EditOperation] = field(default_factory=list)
    active_selection: ActiveMediaSelection = field(default_factory=ActiveMediaSelection)
    export_profiles: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "social": {"container": "mp4", "video_codec": "h264", "max_height": 1080},
        "small": {"container": "mp4", "video_codec": "h264", "max_height": 720},
    })

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, project_id: str, source_asset: SourceAsset) -> MediaEditProject:
        proj = cls(project_id=project_id, source_asset=source_asset)
        proj.active_selection = ActiveMediaSelection(
            project_id=project_id,
            asset_id=source_asset.asset_id,
            range_end=float(source_asset.metadata.get("duration", 0.0) or 0.0),
        )
        return proj

    # ------------------------------------------------------------ edits
    def apply(self, kind: str, params: dict[str, Any], *, actor: str = "gui",
              use_selection_range: bool = False) -> EditOperation:
        """Apply one typed operation. THE single mutation path for both actors.

        With ``use_selection_range`` a trim/delete_range without explicit bounds
        takes the current ActiveMediaSelection range verbatim.
        """
        params = dict(params or {})
        sel = self.active_selection
        if use_selection_range and kind in {"trim", "delete_range"} \
                and "start" not in params and "end" not in params \
                and sel.range_end > sel.range_start:
            params = {"start": sel.range_start, "end": sel.range_end}
        normalized = validate_operation(kind, params)
        op = EditOperation(kind=kind, params=normalized, actor=actor,
                           revision=self.edit_revision + 1)
        self.operations.append(op)
        self.undo_stack.append(op)
        if len(self.undo_stack) > MAX_HISTORY:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.edit_revision += 1
        return op

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        op = self.undo_stack.pop()
        self.redo_stack.append(op)
        self.edit_revision -= 1
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        op = self.redo_stack.pop()
        self.undo_stack.append(op)
        self.edit_revision += 1
        return True

    # ------------------------------------------------------------ views
    def effective_ops(self) -> list[EditOperation]:
        """The operations currently in force (undo/redo already applied)."""
        return list(self.undo_stack)

    def working_range(self) -> tuple[float, float]:
        """Timeline window after trims/deletes — used for preview/export bounds."""
        duration = float(self.source_asset.metadata.get("duration", 0.0) or 0.0)
        start, end = 0.0, duration
        for op in self.effective_ops():
            if op.kind == "trim":
                start = max(start, float(op.params["start"]))
                end = min(end, float(op.params["end"]))
        return (min(start, end), end)

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "source_asset": self.source_asset.to_dict(),
            "edit_revision": self.edit_revision,
            "operations": [op.to_dict() for op in self.operations],
            "undo_stack": [op.to_dict() for op in self.undo_stack],
            "redo_stack": [op.to_dict() for op in self.redo_stack],
            "active_selection": self.active_selection.to_dict(),
            "export_profiles": self.export_profiles,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MediaEditProject:
        proj = cls(
            project_id=str(raw["project_id"]),
            source_asset=SourceAsset.from_dict(raw["source_asset"]),
            edit_revision=int(raw.get("edit_revision", 0)),
            operations=[EditOperation.from_dict(o) for o in raw.get("operations", [])],
            undo_stack=[EditOperation.from_dict(o) for o in raw.get("undo_stack", [])],
            redo_stack=[EditOperation.from_dict(o) for o in raw.get("redo_stack", [])],
            active_selection=ActiveMediaSelection.from_dict(raw.get("active_selection")),
            export_profiles=dict(raw.get("export_profiles") or {}),
        )
        return proj
