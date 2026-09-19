"""Chat-side handlers for Media Studio intents (bounded model surface).

The model sees a small set of media intents; each one maps onto the SAME
service functions the editor GUI uses. No codec catalogs, no ffmpeg flags,
no raw worker protocol ever reaches the model.
"""

from __future__ import annotations

from typing import Any

from core.media_studio_service import (
    MediaExportReceipt,
    MediaStudioServiceError,
    apply_edit,
    ensure_proxy,
    export_project,
    inspect,
    open_project,
    redo,
    undo,
)


def _err(exc: MediaStudioServiceError, intent: str):
    from core.runtime_execution_tools import RuntimeExecutionResult, _tool_observation

    unavailable = exc.code == "worker_unavailable"
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status="worker_unavailable" if unavailable else "failed",
        response_text=(
            "Media editing is currently unavailable: the nebula-media worker could "
            f"not be reached ({exc.message})."
            if unavailable else f"`{intent}` failed: {exc.message}"
        ),
        details={
            "error_code": exc.code,
            "observation": _tool_observation(
                intent=intent, tool_surface="media", ok=False,
                status="worker_unavailable" if unavailable else "failed",
                error_code=exc.code,
            ),
        },
    )


def _receipt_to_result(receipt: MediaExportReceipt, intent: str):
    from core.runtime_execution_tools import RuntimeExecutionResult, _tool_observation

    text = (
        f"Export complete: {receipt.output_path} "
        f"(revision {receipt.edit_revision}, {receipt.output_metadata.get('duration', '?')}s, "
        f"{receipt.output_metadata.get('width', '?')}x{receipt.output_metadata.get('height', '?')})."
        if receipt.ok else f"Export failed: {receipt.error.get('message', receipt.status)}."
    )
    return RuntimeExecutionResult(
        handled=True,
        ok=receipt.ok,
        status=receipt.status,
        response_text=text,
        details={
            "export_receipt": receipt.to_dict(),
            "observation": _tool_observation(
                intent=intent, tool_surface="media", ok=receipt.ok, status=receipt.status,
                output_path=receipt.output_path, effect_state=receipt.effect_state,
            ),
        },
    )


def media_inspect(arguments: dict[str, Any]):
    from core.runtime_execution_tools import RuntimeExecutionResult, _tool_observation

    try:
        info = inspect(str(arguments.get("project_id") or ""))
        proxy = ensure_proxy(info["project_id"])
    except MediaStudioServiceError as exc:
        return _err(exc, "media.inspect")
    lines = [
        f"Project {info['project_id']}: {info['duration']:.2f}s "
        f"{info['width']}x{info['height']}, audio={'yes' if info['has_audio'] else 'no'}.",
        f"Edit revision {info['edit_revision']} with {len(info['operations'])} operation(s).",
        f"Working range {info['working_range'][0]:.2f}s–{info['working_range'][1]:.2f}s.",
    ]
    return RuntimeExecutionResult(
        handled=True, ok=True, status="ok", response_text="\n".join(lines),
        details={"project": info, "preview_proxy": proxy,
                 "observation": _tool_observation(
                     intent="media.inspect", tool_surface="media", ok=True, status="ok")},
    )


def media_open(arguments: dict[str, Any]):
    from core.runtime_execution_tools import RuntimeExecutionResult, _tool_observation

    try:
        project = open_project(str(arguments.get("source_path") or ""))
    except MediaStudioServiceError as exc:
        return _err(exc, "media.open")
    return RuntimeExecutionResult(
        handled=True, ok=True, status="opened",
        response_text=(f"Media project {project.project_id} opened for "
                       f"{project.source_asset.path}. Open it in the Media Studio editor: "
                       f"/media-editor?project_id={project.project_id}"),
        details={"project_id": project.project_id,
                 "editor_url": f"/media-editor?project_id={project.project_id}",
                 "observation": _tool_observation(
                     intent="media.open", tool_surface="media", ok=True, status="opened",
                     project_id=project.project_id)},
    )


def media_edit(arguments: dict[str, Any]):
    from core.runtime_execution_tools import RuntimeExecutionResult, _tool_observation

    kind = str(arguments.get("kind") or "").strip()
    params = dict(arguments.get("params") or {})
    use_selection = bool(arguments.get("use_selection_range", True))
    try:
        result = apply_edit(str(arguments.get("project_id") or ""), kind, params,
                            actor="chat", use_selection_range=use_selection)
    except MediaStudioServiceError as exc:
        return _err(exc, "media.edit")
    op = result["operation"]
    return RuntimeExecutionResult(
        handled=True, ok=True, status="applied",
        response_text=(f"Applied {op['kind']} at revision {result['edit_revision']} "
                       f"(params: {op['params']})."),
        details={"edit_revision": result["edit_revision"], "operation": op,
                 "observation": _tool_observation(
                     intent="media.edit", tool_surface="media", ok=True, status="applied",
                     edit_kind=op["kind"])},
    )


def media_undo_redo(arguments: dict[str, Any], *, direction: str):
    from core.runtime_execution_tools import RuntimeExecutionResult, _tool_observation

    fn = undo if direction == "undo" else redo
    try:
        result = fn(str(arguments.get("project_id") or ""))
    except MediaStudioServiceError as exc:
        return _err(exc, f"media.{direction}")
    return RuntimeExecutionResult(
        handled=True, ok=True, status="ok" if result["changed"] else "noop",
        response_text=(
            f"{direction.capitalize()} done; revision now {result['edit_revision']}."
            if result["changed"] else f"Nothing to {direction}."
        ),
        details={"edit_revision": result["edit_revision"],
                 "observation": _tool_observation(
                     intent=f"media.{direction}", tool_surface="media",
                     ok=True, status=result.get("status", "ok"))},
    )


def media_export(arguments: dict[str, Any]):
    receipt = export_project(
        str(arguments.get("project_id") or ""),
        out_path=str(arguments.get("output_path") or ""),
        profile=str(arguments.get("profile") or "social"),
    )
    return _receipt_to_result(receipt, "media.export")


__all__ = ["media_edit", "media_export", "media_inspect", "media_open",
           "media_undo_redo"]
