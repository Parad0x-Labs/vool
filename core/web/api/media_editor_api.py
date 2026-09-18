"""Media Studio child editor — HTTP surface.

VOOL is a web app; the "child editor window" is a dedicated route
(`/media-editor`) opened from the main chat window. It is owned by the same
server/session context: closing the tab never deletes project state, and
reopening restores it from the store (G4/V6).

These endpoints call core.media_studio_service — the SAME engine chat tools
use. Export authorization on this operator-local surface requires an explicit
policy flag (``media.editor_export_enabled``, default True for local operator)
AND is additionally gated in chat by the runtime permission lane; capability
availability alone never authorizes anything.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from core import policy_engine
from core.media_studio_service import (
    MediaStudioServiceError,
    apply_edit,
    ensure_proxy,
    export_project,
    get_project,
    inspect,
    list_projects,
    open_project,
    redo,
    render_preview_frame,
    undo,
    update_selection,
)

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>VOOL Media Studio</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 0; background: #141417; color: #eee;
         display: flex; flex-direction: column; height: 100vh; }}
  header {{ padding: 8px 14px; background: #1d1d22; display: flex; gap: 12px;
            align-items: center; font-size: 13px; }}
  header .spacer {{ flex: 1; }}
  button {{ background: #2c2c33; color: #eee; border: 1px solid #3a3a44;
            border-radius: 6px; padding: 5px 10px; cursor: pointer; }}
  button:hover {{ background: #3a3a44; }}
  main {{ flex: 1; display: flex; min-height: 0; }}
  #stage {{ flex: 1; display: flex; align-items: center; justify-content: center;
            background: #000; overflow: auto; }}
  video {{ max-width: 100%; max-height: 100%; }}
  aside {{ width: 260px; background: #1d1d22; padding: 12px; overflow-y: auto;
           font-size: 13px; }}
  aside h3 {{ margin: 14px 0 6px; font-size: 12px; text-transform: uppercase;
              color: #999; }}
  label {{ display: block; margin: 4px 0; }}
  input, select {{ width: 90px; background: #26262c; color: #eee;
                   border: 1px solid #3a3a44; border-radius: 4px; padding: 3px 6px; }}
  footer {{ background: #1d1d22; padding: 10px 14px; }}
  canvas {{ width: 100%; height: 72px; background: #26262c; border-radius: 6px;
            cursor: crosshair; }}
  #status {{ font-size: 11px; color: #888; margin-top: 6px; white-space: pre-wrap; }}
</style>
</head>
<body>
<header>
  <strong>VOOL Media Studio</strong>
  <span id="meta">no project</span>
  <span class="spacer"></span>
  <button onclick="doUndo()" title="Cmd+Z">Undo</button>
  <button onclick="doRedo()" title="Cmd+Shift+Z">Redo</button>
  <button onclick="openProject()">Open file…</button>
  <button style="background:#2d6a4f" onclick="doExport()">Export</button>
</header>
<main>
  <div id="stage"><video id="player" controls></video></div>
  <aside>
    <h3>Selection (s)</h3>
    <label>start <input id="selStart" type="number" step="0.01" value="0"></label>
    <label>end <input id="selEnd" type="number" step="0.01" value="0"></label>
    <button onclick="saveSelection()">Set selection</button>

    <h3>Trim to selection</h3>
    <button onclick="edit('trim', 'selection')">Trim start/end</button>

    <h3>Aspect</h3>
    <select id="ratio">
      <option>16:9</option><option>9:16</option><option>1:1</option><option>4:5</option>
    </select>
    <button onclick="edit('set_aspect_ratio', 'ratio')">Apply aspect</button>

    <h3>Crop rect</h3>
    <label>x <input id="cx" type="number" value="0"></label>
    <label>y <input id="cy" type="number" value="0"></label>
    <label>w <input id="cw" type="number" value="320"></label>
    <label>h <input id="ch" type="number" value="240"></label>
    <button onclick="cropSel()">Crop</button>

    <h3>Audio</h3>
    <label>volume <input id="vol" type="number" step="0.1" value="1.0"></label>
    <button onclick="edit('set_volume', 'vol')">Set volume</button>
    <button onclick="editRaw('set_volume', {{factor: 0}})">Mute</button>
    <button onclick="editRaw('normalize_audio', {{}})">Normalize</button>

    <h3>Delete / split</h3>
    <button onclick="edit('delete_range', 'selection')">Delete selection</button>

    <h3>Export profile</h3>
    <select id="profile">
      <option value="social">social 1080p</option>
      <option value="small">small 720p</option>
      <option value="high">high</option>
    </select>
    <div id="status">{status}</div>
  </aside>
</main>
<footer>
  <canvas id="timeline" width="1200" height="72"></canvas>
  <div style="display:flex; gap:16px; align-items:center;">
    <span>preview zoom</span>
    <input type="range" min="50" max="200" value="100" id="zoom"
           oninput="document.getElementById('player').style.width = this.value + '%'">
    <span>timeline zoom</span>
    <input type="range" min="1000" max="8000" value="1200" id="tlZoom"
           oninput="drawTimeline()">
  </div>
</footer>
<script>
const api = (path, body) => fetch('/media-editor' + path, {{
    method: body ? 'POST' : 'GET',
    headers: {{'Content-Type': 'application/json'}},
    body: body ? JSON.stringify(body) : undefined,
  }}).then(r => r.json());
let PROJECT = null;

function status(msg) {{ document.getElementById('status').textContent = msg; }}

function refresh() {{
  if (!PROJECT) return;
  api('/project?project_id=' + PROJECT).then(d => {{
    if (!d.ok) {{ status(d.error || 'error'); return; }}
    document.getElementById('meta').textContent =
      `${{d.filename}} · ${{d.duration.toFixed(2)}}s · rev ${{d.edit_revision}}`;
    if (!document.getElementById('player').src.includes('proxy'))
      document.getElementById('player').src = '/media-editor/proxy?project_id=' + PROJECT;
    drawTimeline();
  }});
}}

function openProject() {{
  const path = prompt('Path of media file:');
  if (!path) return;
  api('/open', {{source_path: path}}).then(d => {{
    if (!d.ok) {{ status(d.error || d.message || 'open failed'); return; }}
    PROJECT = d.project_id;
    sessionStorage.setItem('mediaProject', PROJECT);
    refresh();
  }});
}}

function saveSelection() {{
  if (!PROJECT) return;
  api('/selection', {{project_id: PROJECT,
      range_start: parseFloat(document.getElementById('selStart').value),
      range_end: parseFloat(document.getElementById('selEnd').value)}})
    .then(refresh);
}}

function edit(kind, inputId) {{
  if (!PROJECT) {{ status('no project'); return; }}
  let params = {{}};
  if (typeof inputId === 'object') params = inputId;
  else if (inputId === 'selection')
    params = {{start: parseFloat(document.getElementById('selStart').value),
               end: parseFloat(document.getElementById('selEnd').value)}};
  else if (kind === 'set_aspect_ratio') params = {{ratio: document.getElementById(inputId).value}};
  else if (kind === 'set_volume') params = {{factor: parseFloat(document.getElementById(inputId).value)}};
  api('/op', {{project_id: PROJECT, kind, params}}).then(d => {{
    status(d.error ? 'ERROR: ' + d.error : `applied ${{kind}} @rev ${{d.edit_revision}}`);
    refresh();
  }});
}}
function editRaw(kind, params) {{ edit(kind, params); }}

function cropSel() {{
  editRaw('crop', {{x: +cx.value, y: +cy.value, width: +cw.value, height: +ch.value}});
}}

function doUndo() {{ PROJECT && api('/undo', {{project_id: PROJECT}}).then(refresh); }}
function doRedo() {{ PROJECT && api('/redo', {{project_id: PROJECT}}).then(refresh); }}

function doExport() {{
  if (!PROJECT) {{ status('no project'); return; }}
  const out = prompt('Export output path:', '/tmp/vool_export.mp4');
  if (!out) return;
  status('exporting…');
  api('/export', {{project_id: PROJECT, output_path: out,
                   profile: document.getElementById('profile').value}})
    .then(d => status(d.receipt ? JSON.stringify(d.receipt, null, 1) : (d.error || 'failed')));
}}

// timeline: selection range + playhead
function drawTimeline() {{
  const c = document.getElementById('timeline');
  const ctx = c.getContext('2d');
  const w = parseInt(document.getElementById('tlZoom').value, 10);
  ctx.clearRect(0, 0, c.width, c.height);
  if (!PROJECT) return;
  api('/project?project_id=' + PROJECT).then(d => {{
    if (!d.ok) return;
    const dur = Math.max(d.duration, 0.01);
    const pxPerSec = w / dur;
    // working range
    ctx.fillStyle = '#33506b';
    ctx.fillRect(0, 18, c.width * Math.min(1, dur / dur), 36);
    // selection
    ctx.fillStyle = '#e76f51aa';
    ctx.fillRect(d.sel_start * pxPerSec, 18, (d.sel_end - d.sel_start) * pxPerSec, 36);
    ctx.fillStyle = '#fff';
    ctx.fillRect(d.playhead * pxPerSec, 8, 2, 56);
  }});
}}
document.getElementById('timeline').addEventListener('click', e => {{
  if (!PROJECT) return;
  const c = document.getElementById('timeline');
  const frac = e.offsetX / parseInt(document.getElementById('tlZoom').value, 10);
  api('/project?project_id=' + PROJECT).then(d => {{
    if (!d.ok) return;
    api('/selection', {{project_id: PROJECT,
        playhead: frac * d.duration,
        range_start: document.getElementById('selStart').value,
        range_end: document.getElementById('selEnd').value}});
  }});
}});
window.addEventListener('keydown', e => {{
  if (e.code === 'Space' && document.activeElement.tagName !== 'INPUT') {{
    e.preventDefault();
    const v = document.getElementById('player');
    v.paused ? v.play() : v.pause();
  }}
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {{
    e.preventDefault();
    e.shiftKey ? doRedo() : doUndo();
  }}
}});
PROJECT = sessionStorage.getItem('mediaProject');
const urlPid = new URLSearchParams(location.search).get('project_id');
if (urlPid) {{ PROJECT = urlPid; sessionStorage.setItem('mediaProject', urlPid); }}
if (PROJECT) refresh();
</script>
</body>
</html>"""


def _ok(payload: dict[str, Any]):
    from core.web.api.service import json_response

    return json_response(200, {"ok": True, **payload})


def _fail(code: str, message: str, status_code: int = 400):
    from core.web.api.service import json_response

    return json_response(status_code, {"ok": False, "error": code, "message": message})


def handle_media_editor_page() -> Any:
    from core.web.api.service import html_response

    try:
        projects = list_projects(limit=1)
        status = f"{len(list_projects(limit=50))} saved project(s)"
    except Exception:
        status = ""
    return html_response(200, _PAGE.format(status=html.escape(status)))


def handle_media_editor_post(path: str, body: dict[str, Any]) -> Any:
    action = path.rstrip("/").rsplit("/", 1)[-1]
    try:
        if action == "open":
            project = open_project(str(body.get("source_path") or ""))
            return _ok({"project_id": project.project_id})
        project_id = str(body.get("project_id") or "")
        if not project_id:
            return _fail("missing_project", "project_id required")
        if action == "op":
            result = apply_edit(project_id, str(body.get("kind") or ""),
                                dict(body.get("params") or {}), actor="gui",
                                use_selection_range=True)
            return _ok({"edit_revision": result["edit_revision"],
                        "operation": result["operation"]})
        if action == "undo":
            return _ok(**undo(project_id))
        if action == "redo":
            return _ok(**redo(project_id))
        if action == "selection":
            sel = update_selection(project_id, {
                k: v for k, v in body.items()
                if k in {"range_start", "range_end", "playhead"} and v is not None
            })
            return _ok({"active_selection": sel})
        if action == "export":
            from core.web.api.service import json_response

            if not policy_engine.get("media.editor_export_enabled", True):
                return _fail("export_disabled", "editor export disabled by policy", 403)
            receipt = export_project(
                project_id,
                out_path=str(body.get("output_path") or ""),
                profile=str(body.get("profile") or "social"),
            )
            return json_response(200, {"ok": receipt.ok, "receipt": receipt.to_dict()})
        return _fail("unknown_action", f"unknown editor action '{action}'")
    except MediaStudioServiceError as exc:
        return _fail(exc.code, str(exc))


def handle_media_editor_get(path: str, query: dict[str, list[str]]) -> Any:
    normalized = path.rstrip("/")
    if normalized == "/media-editor":
        return handle_media_editor_page()
    if normalized == "/media-editor/project":
        project_id = (query.get("project_id") or [""])[0]
        try:
            info = inspect(project_id)
            proxy = ensure_proxy(project_id)
        except MediaStudioServiceError as exc:
            return _fail(exc.code, str(exc), 404)
        sel = info["active_selection"]
        return _ok({
            "project_id": info["project_id"],
            "filename": Path(info["source_path"]).name,
            "duration": info["duration"],
            "width": info["width"],
            "height": info["height"],
            "edit_revision": info["edit_revision"],
            "operations": [op["kind"] for op in info["operations"]],
            "working_range": info["working_range"],
            "duration_total": info["duration"],
            "sel_start": sel["range_start"],
            "sel_end": sel["range_end"],
            "playhead": sel["playhead"],
            "preview_path": proxy["path"],
        })
    if normalized == "/media-editor/proxy":
        project_id = (query.get("project_id") or [""])[0]
        try:
            proxy = ensure_proxy(project_id)
        except MediaStudioServiceError as exc:
            return _fail(exc.code, str(exc), 404)
        return _serve_file(Path(proxy["path"]), content_type="video/mp4")
    if normalized == "/media-editor/preview":
        project_id = (query.get("project_id") or [""])[0]
        t = float((query.get("time") or ["0"])[0])
        try:
            frame = render_preview_frame(project_id, at_time=t)
        except MediaStudioServiceError as exc:
            return _fail(exc.code, str(exc))
        return _serve_file(Path(frame["path"]), content_type="image/png")
    return _fail("not_found", "unknown media-editor path", 404)


def _serve_file(file_path: Path, *, content_type: str) -> Any:
    from core.web.api.service import ApiResponse

    if not file_path.is_file():
        return _fail("io_error", f"not rendered yet: {file_path}", 404)
    data = file_path.read_bytes()
    return ApiResponse(
        status=200,
        content_type=content_type,
        body=data,
        headers={"Content-Length": str(len(data)),
                 "Accept-Ranges": "bytes",
                 "Cache-Control": "no-store"},
    )


__all__ = ["handle_media_editor_get", "handle_media_editor_page",
           "handle_media_editor_post"]
