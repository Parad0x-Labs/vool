"""Media Studio service — ONE edit engine, two controllers.

Every mutation (GUI drag or chat command) flows through apply_edit(); every
export through export_project(). This module owns worker orchestration,
preview caching, and typed export receipts. It contains NO ffmpeg knowledge:
all media mechanism lives behind core/media_worker_client.py -> nebula-media.

Authorization is NOT decided here. Chat calls arrive already permission-gated
via execute_tool_intent; HTTP editor endpoints must enforce their own policy
checks (see core/web/api/media_editor_api.py). Capability availability never
implies permission.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.media_edit_project import (
    MediaEditProject,
    MediaEditError,
    SourceAsset,
    validate_operation,
)
from core.media_worker_client import MediaWorkerError, MediaWorkerUnavailable, get_media_worker
from storage import media_project_store

PREVIEW_CACHE_DIRNAME = "media_preview_cache"


@dataclass
class MediaExportReceipt:
    """Typed operational truth for one export.

    ``effect_state`` is intentionally limited to succeeded/failed here; A6
    reconciliation will later own ambiguous outcomes via ``outcome_source``.
    """

    ok: bool
    status: str                       # exported | failed | worker_unavailable
    project_id: str
    input_asset_id: str
    input_sha256: str
    edit_revision: int
    output_path: str = ""
    output_metadata: dict[str, Any] = field(default_factory=dict)
    effect_state: str = "unknown"     # succeeded | failed (A6 may extend)
    outcome_source: str = "nebula_media_worker"
    error: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "status": self.status, "project_id": self.project_id,
            "input_asset_id": self.input_asset_id, "input_sha256": self.input_sha256,
            "edit_revision": self.edit_revision, "output_path": self.output_path,
            "output_metadata": self.output_metadata, "effect_state": self.effect_state,
            "outcome_source": self.outcome_source, "error": self.error,
        }


class MediaStudioServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _worker_call(op: str, params: dict[str, Any], *, timeout: float | None = None):
    try:
        return get_media_worker().request(op, params, timeout=timeout)
    except MediaWorkerUnavailable as exc:
        raise MediaStudioServiceError(exc.code, exc.message) from exc
    except MediaWorkerError as exc:
        raise MediaStudioServiceError(exc.code, exc.message) from exc


# ---------------------------------------------------------------- projects

def open_project(source_path: str, *, now: float | None = None) -> MediaEditProject:
    """Open a source asset read-only and create a persisted edit project."""
    src = Path(source_path).expanduser()
    if not src.is_file():
        raise MediaStudioServiceError("io_error", f"source not found: {src}")
    metadata = _worker_call("probe", {"src": str(src)})
    asset = {
        "asset_id": f"asset-{_sha256(src)[:16]}",
        "path": str(src),
        "sha256": _sha256(src),
        "metadata": metadata,
    }
    project = MediaEditProject.create(
        project_id=f"mep-{uuid.uuid4().hex[:12]}",
        source_asset=SourceAsset.from_dict(asset),
    )
    persist(project, now=now if now is not None else time.time())
    return project


def get_project(project_id: str) -> MediaEditProject:
    doc = media_project_store.load_project(project_id)
    if doc is None:
        raise MediaStudioServiceError("project_not_found", f"no such project '{project_id}'")
    return MediaEditProject.from_dict(doc)


def persist(project: MediaEditProject, *, now: float | None = None) -> None:
    media_project_store.save_project(
        project.to_dict(), now=now if now is not None else time.time(),
    )


def list_projects(limit: int = 50) -> list[dict[str, Any]]:
    return media_project_store.list_projects(limit)


# ---------------------------------------------------------------- edits

def apply_edit(project_id: str, kind: str, params: dict[str, Any], *,
               actor: str, use_selection_range: bool = False) -> dict[str, Any]:
    """THE single mutation path for GUI and chat alike."""
    project = get_project(project_id)
    try:
        op = project.apply(kind, params, actor=actor,
                           use_selection_range=use_selection_range)
    except MediaEditError as exc:
        raise MediaStudioServiceError("invalid_edit", str(exc)) from exc
    persist(project)
    return {"operation": op.to_dict(), "edit_revision": project.edit_revision}


def undo(project_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    changed = project.undo()
    if changed:
        persist(project)
    return {"changed": changed, "edit_revision": project.edit_revision}


def redo(project_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    changed = project.redo()
    if changed:
        persist(project)
    return {"changed": changed, "edit_revision": project.edit_revision}


def update_selection(project_id: str, selection: dict[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    current = project.active_selection
    merged = current.to_dict() | dict(selection)
    project.active_selection = type(current).from_dict(merged)
    persist(project)
    return project.active_selection.to_dict()


def inspect(project_id: str) -> dict[str, Any]:
    """Bounded project view — never dumps codec catalogs to the model."""
    project = get_project(project_id)
    meta = project.source_asset.metadata
    return {
        "project_id": project.project_id,
        "asset_id": project.source_asset.asset_id,
        "source_path": project.source_asset.path,
        "duration": float(meta.get("duration", 0.0)),
        "width": int(meta.get("width", 0)),
        "height": int(meta.get("height", 0)),
        "has_audio": bool(meta.get("has_audio")),
        "edit_revision": project.edit_revision,
        "operations": [op.to_dict() for op in project.effective_ops()],
        "working_range": project.working_range(),
        "active_selection": project.active_selection.to_dict(),
    }


def verify_source_intact(project_id: str) -> bool:
    project = get_project(project_id)
    src = Path(project.source_asset.path)
    return src.is_file() and _sha256(src) == project.source_asset.sha256


# ---------------------------------------------------------------- preview

def render_preview_frame(project_id: str, at_time: float | None = None) -> dict[str, Any]:
    """Cheap preview frame for the editor (typed local-cache effect)."""
    project = get_project(project_id)
    t = float(at_time if at_time is not None else project.active_selection.playhead)
    cache_dir = Path.home() / ".vool" / PREVIEW_CACHE_DIRNAME / project.project_id
    dst = cache_dir / f"frame_{int(t * 1000)}.png"
    if not dst.exists():
        _worker_call("thumbnail", {"src": project.source_asset.path,
                                   "dst": str(dst), "time": max(0.0, t)}, timeout=120)
    return {"path": str(dst), "time": t}


def ensure_proxy(project_id: str) -> dict[str, Any]:
    """Low-cost proxy encode used by the editor <video> element."""
    project = get_project(project_id)
    cache_dir = Path.home() / ".vool" / PREVIEW_CACHE_DIRNAME / project.project_id
    proxy_path = cache_dir / f"proxy_r{project.edit_revision}.mp4"
    if not proxy_path.exists():
        _worker_call("proxy", {"src": project.source_asset.path,
                               "dst": str(proxy_path)}, timeout=600)
    return {"path": str(proxy_path)}


# ---------------------------------------------------------------- export

def export_project(project_id: str, *, out_path: str, profile: str = "social",
                   use_working_range: bool = True) -> MediaExportReceipt:
    """Render SOURCE + EDIT GRAPH to a NEW output file via the isolated worker.

    The source is never touched; output lands only at out_path (E12/S1).
    """
    try:
        project = get_project(project_id)
    except MediaStudioServiceError as exc:
        return MediaExportReceipt(ok=False, status="failed", project_id=project_id,
                                  input_asset_id="", input_sha256="", edit_revision=0,
                                  effect_state="failed", outcome_source="vool_platform",
                                  error={"code": exc.code, "message": str(exc)})
    receipt_base = {
        "project_id": project.project_id,
        "input_asset_id": project.source_asset.asset_id,
        "input_sha256": project.source_asset.sha256,
        "edit_revision": project.edit_revision,
    }

    ops = [op for op in project.effective_ops()
           if op.kind != "split"]  # split markers don't change rendered output
    out = Path(out_path).expanduser()

    def _fail(code: str, message: str, *, unavailable: bool = False) -> MediaExportReceipt:
        status = "worker_unavailable" if unavailable else "failed"
        return MediaExportReceipt(effect_state="failed", ok=False, status=status,
                                  error={"code": code, "message": message}, **receipt_base)

    scratch: list[Path] = []
    try:
        current = Path(project.source_asset.path)

        # Materialize the graph in order; each stage writes a new temp file.
        for i, op in enumerate(ops):
            nxt = out.parent / f".{out.stem}.stage{i}.{uuid.uuid4().hex[:8]}.mp4"
            nxt.parent.mkdir(parents=True, exist_ok=True)
            params = dict(op.params)
            if op.kind == "trim":
                _worker_call("trim", {"src": str(current), "dst": str(nxt),
                                      "start": float(params["start"]),
                                      "end": float(params["end"])})
            elif op.kind == "delete_range":
                _worker_call("remove_range", {"src": str(current), "dst": str(nxt),
                                              "start": float(params["start"]),
                                              "end": float(params["end"])})
            elif op.kind == "crop":
                _worker_call("crop", {"src": str(current), "dst": str(nxt),
                                      "x": params["x"], "y": params["y"],
                                      "width": params["width"], "height": params["height"]})
            elif op.kind == "resize":
                _worker_call("resize", {"src": str(current), "dst": str(nxt),
                                        "width": params["width"], "height": params["height"]})
            elif op.kind == "set_aspect_ratio":
                _worker_call("aspect", {"src": str(current), "dst": str(nxt),
                                        "ratio": params["ratio"]})
            elif op.kind == "rotate":
                _worker_call("rotate", {"src": str(current), "dst": str(nxt),
                                        "degrees": int(params["degrees"])})
            elif op.kind == "set_volume":
                factor = float(params["factor"])
                if factor == 0.0:
                    _worker_call("set_volume", {"src": str(current), "dst": str(nxt),
                                                "factor": 0.0})
                else:
                    _worker_call("set_volume", {"src": str(current), "dst": str(nxt),
                                                "factor": factor})
            elif op.kind == "normalize_audio":
                _worker_call("normalize_audio", {"src": str(current), "dst": str(nxt)})
            else:  # pragma: no cover — validate_operation rejects unknown kinds earlier
                return _fail("invalid_edit", f"unrenderable op '{op.kind}'")
            scratch.append(nxt)
            current = nxt

        # Final compression pass unless the last op was already a compress-grade encode.
        final_params: dict[str, Any] = {"src": str(current), "dst": str(out),
                                        "profile": profile}
        out.parent.mkdir(parents=True, exist_ok=True)
        result = _worker_call("compress", final_params)

        if not verify_source_intact(project_id):  # S1 guard — belt and braces
            return _fail("source_mutated", "source asset hash changed during export")
        return MediaExportReceipt(
            ok=True, status="exported", effect_state="succeeded",
            output_path=str(out), output_metadata=result, **receipt_base,
        )
    except MediaStudioServiceError as exc:
        return _fail(exc.code, str(exc),
                     unavailable=(exc.code == "worker_unavailable"))
    finally:
        for p in scratch:
            try:
                if p.is_file() and p != out:
                    p.unlink()
            except OSError:
                pass


__all__ = [
    "MediaExportReceipt", "MediaStudioServiceError", "apply_edit", "ensure_proxy",
    "export_project", "get_project", "inspect", "list_projects", "open_project",
    "persist", "redo", "render_preview_frame", "undo", "update_selection",
    "validate_operation", "verify_source_intact",
]
