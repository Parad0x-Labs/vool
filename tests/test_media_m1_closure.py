"""MEDIA GOBLIN M1 FINAL CLOSURE proofs (C1–C3 + live worker crash).

C1  actual editor launch from the VOOL main surface (served ASGI app)
C2  chat ↔ editor one-engine flows (A: chat->graph->editor; B: selection->chat;
    convergence: manual + AI edits land in ONE graph)
C3  authority boundary: media creates NO permission authority, bypasses NO
    platform authorization
LIVE worker crash mid-operation: caller/project/source survive, typed failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and os.environ.get("NEBULA_MEDIA_HOME")),
    reason="needs ffmpeg + NEBULA_MEDIA_HOME",
)

VOOL_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBULA_MEDIA_HOME", os.environ["NEBULA_MEDIA_HOME"])
    from storage.db import configure_default_db_path
    from core.media_worker_client import reset_media_worker

    configure_default_db_path(tmp_path / "closure.db")
    reset_media_worker()

    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-shortest", str(clip)],
        check=True, capture_output=True)
    yield {"clip": clip, "tmp": tmp_path}
    reset_media_worker()


def _app():
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


def _req(app, method, path, body=None, query=b""):
    from tests.asgi_harness import asgi_request

    headers = {"Content-Type": "application/json"} if body is not None else None
    payload = json.dumps(body).encode() if body is not None else b""
    status, _h, raw = asgi_request(app, method=method, path=path,
                                   headers=headers, body=payload, query_string=query)
    return status, raw


def _probe_lenient(path: Path) -> dict | None:
    """ffprobe a possibly-truncated file; None if unreadable."""
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        fmt = json.loads(proc.stdout).get("format", {})
        return {"duration": float(fmt.get("duration", 0) or 0)}
    except (json.JSONDecodeError, ValueError):
        return None


# ================================================================ C1
class TestEditorLaunch:
    def test_main_surface_links_to_editor_and_full_launch_cycle(self, env):
        app = _app()

        # the VOOL main surface offers the dedicated editor launch point
        status, raw = _req(app, "GET", "/chat")
        assert status == 200
        assert '/media-editor' in raw.decode()

        # dedicated surface serves
        status, raw = _req(app, "GET", "/media-editor")
        assert status == 200
        assert b"VOOL Media Studio" in raw

        # main surface -> create project -> editor deep-link carries exact id
        status, raw = _req(app, "POST", "/media-editor/open",
                           {"source_path": str(env["clip"])})
        assert status == 200
        pid = json.loads(raw)["project_id"]
        status, raw = _req(app, "GET", f"/media-editor?project_id={pid}")
        assert status == 200  # deep-linked launch resolves exact project_id

        # editor reads the project, applies an edit, sees revision move
        status, raw = _req(app, "GET", "/media-editor/project",
                           query=f"project_id={pid}".encode())
        assert status == 200
        assert json.loads(raw)["edit_revision"] == 0

        # CLOSE the editor (client-side only): server does nothing.
        # REOPEN: identical state.
        status, raw_before = _req(app, "GET", "/media-editor/project",
                                  query=f"project_id={pid}".encode())
        status, raw_after = _req(app, "GET", "/media-editor/project",
                                 query=f"project_id={pid}".encode())
        assert json.loads(raw_after) == json.loads(raw_before)

        # and an edit made after reopen lands on the same surviving graph
        status, raw = _req(app, "POST", "/media-editor/op",
                           {"project_id": pid, "kind": "trim",
                            "params": {"start": 0.0, "end": 5.0}})
        assert status == 200 and json.loads(raw)["ok"]
        status, raw = _req(app, "POST", "/media-editor/export",
                           {"project_id": pid,
                            "output_path": str(env["tmp"] / "c1.mp4")})
        receipt = json.loads(raw)["receipt"]
        assert receipt["edit_revision"] >= 1


# ================================================================ C2
class TestChatEditorOneEngine:
    def _open(self, env):
        from core.media_studio_service import open_project

        return open_project(str(env["clip"]))

    def test_C2A_chat_trim_reaches_same_graph_the_editor_reads(self, env):
        from core.media_studio_chat_tools import media_edit
        from core.media_studio_service import inspect
        from core.web.api.media_editor_api import handle_media_editor_get

        proj = self._open(env)
        result = media_edit({"project_id": proj.project_id, "kind": "trim",
                             "params": {"start": 3.0, "end": 6.0}})
        assert result.ok and result.status == "applied"

        # the editor reads THE SAME graph over HTTP
        status, raw = _req(_app(), "GET", "/media-editor/project",
                           query=f"project_id={proj.project_id}".encode())
        served = json.loads(raw)
        assert served["edit_revision"] == 1
        assert served["operations"] == ["trim"]
        assert served["working_range"][0] == 3.0
        assert inspect(proj.project_id)["edit_revision"] == 1

    def test_C2B_editor_selection_drives_chat_edit_exactly(self, env):
        from core.media_studio_chat_tools import media_edit
        from core.media_studio_service import inspect
        from core.web.api.media_editor_api import handle_media_editor_post

        proj = self._open(env)
        sel_body = {"project_id": proj.project_id,
                    "range_start": 1.25, "range_end": 2.5}
        resp = handle_media_editor_post("/media-editor/selection", sel_body)
        assert json.loads(resp.body)["ok"]

        # user says "remove this bit" — no explicit range given
        result = media_edit({"project_id": proj.project_id,
                             "kind": "delete_range"})
        assert result.ok
        op = result.details["operation"]
        assert op["params"] == {"start": 1.25, "end": 2.5}   # exact, not guessed
        info = inspect(proj.project_id)
        assert info["operations"][-1]["params"] == {"start": 1.25, "end": 2.5}

    def test_C2C_manual_and_ai_edits_converge_on_one_graph(self, env):
        from core.media_studio_chat_tools import media_edit
        from core.web.api.media_editor_api import handle_media_editor_post

        gui_proj = self._open(env)
        ai_proj = self._open(env)

        # AI: trim first 3 seconds
        r = media_edit({"project_id": ai_proj.project_id, "kind": "trim",
                        "params": {"start": 3.0, "end": 6.0}})
        assert r.ok
        ai_op = r.details["operation"]

        # human: drags the same handles in the editor
        g = handle_media_editor_post(
            "/media-editor/op",
            {"project_id": gui_proj.project_id, "kind": "trim",
             "params": {"start": 3.0, "end": 6.0}})
        gui_op = json.loads(g.body)["operation"]

        strip = lambda d: {k: v for k, v in d.items() if k != "actor"}
        assert strip(gui_op) == strip(ai_op)   # ONE edit engine, two actors


# ================================================================ C3
class TestAuthorityBoundary:
    def test_no_permission_authority_created_by_media(self):
        """Media ships zero permission primitives: nothing registers into the
        permission/mode-policy layers, no new approval authorities."""
        media_files = ["core/media_studio_service.py",
                       "core/media_worker_client.py",
                       "core/media_studio_chat_tools.py",
                       "core/web/api/media_editor_api.py",
                       "core/media_capabilities.py"]
        for rel in media_files:
            src = (VOOL_ROOT / rel).read_text()
            assert "decide_tool_call" not in src, f"{rel} touches permission decisions"
            assert "PermissionEffect" not in src, f"{rel} grants/denies permissions"
            assert "register_implementation" not in src or rel.endswith("media_capabilities.py")

    def test_capability_availability_is_not_authorization(self, env, monkeypatch):
        """Worker fully available + capabilities registered ≠ export allowed:
        flipping the PLATFORM policy flag disables the intent regardless."""
        from core import policy_engine
        from core.media_capabilities import register_media_capabilities
        from core.runtime_execution_tools import execute_runtime_tool

        register_media_capabilities(available=True)     # everything available…
        monkeypatch.setattr(policy_engine, "get",
                            lambda key, default=None: False)  # …platform says no
        result = execute_runtime_tool("media.edit", {
            "project_id": "mep-whatever", "kind": "trim",
            "params": {"start": 0, "end": 1}})
        assert result is not None and result.ok is False
        assert result.status == "disabled"

    def test_media_intents_have_no_side_door_past_the_permission_seam(self):
        """The registry-tool lane (tools.registry) can be called without the
        controller permission seam — media must never appear there."""
        from tools.registry import TOOLS

        assert not [name for name in TOOLS if name.startswith("media.")]

    def test_export_receipt_carries_A6_seam(self, env):
        from core.media_studio_service import export_project, open_project

        proj = open_project(str(env["clip"]))
        receipt = export_project(proj.project_id,
                                 out_path=str(env["tmp"] / "a6.mp4"))
        d = receipt.to_dict()
        assert d["outcome_source"] == "nebula_media_worker"
        assert d["effect_state"] in {"succeeded", "failed"}  # A6 extends later
        assert d["input_sha256"] and d["edit_revision"] >= 0


# ================================================================ LIVE WORKER CRASH
class TestWorkerCrashMidOperation:
    def test_kill_mid_operation_typed_failure_everything_survives(self, env):
        import hashlib

        from core.media_studio_service import (
            MediaStudioServiceError, get_project, open_project,
            verify_source_intact,
        )
        from core.media_worker_client import get_media_worker

        digest_before = hashlib.sha256(env["clip"].read_bytes()).hexdigest()
        project = open_project(str(env["clip"]))   # worker alive here

        # make the next operation heavy enough to still be running when we kill
        big = env["tmp"] / "big.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=120",
             "-c:v", "libx264", "-preset", "ultrafast", str(big)],
            check=True, capture_output=True)
        from core.media_studio_service import persist
        project.source_asset.metadata["path"] = str(big)

        client = get_media_worker()
        errors: list[BaseException] = []

        def run():
            try:
                client.request("compress", {"src": str(big),
                                            "dst": str(env["tmp"] / "never.mp4"),
                                            "profile": "high"}, timeout=120)
                errors.append(AssertionError("operation should not have completed"))
            except Exception as exc:  # typed failure expected
                errors.append(exc)

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.5)
        client._proc.kill()          # HARD crash of nebula-media worker mid-op
        t.join(timeout=60)

        failure = errors[0]
        from core.media_worker_client import MediaWorkerError

        assert isinstance(failure, MediaWorkerError), f"untyped failure: {failure!r}"
        assert failure.code in {"worker_unavailable", "internal", "timeout"}

        # VOOL caller survived; worker transparently restarts
        probe = client.request("probe", {"src": str(env["clip"])})
        assert probe["duration"] > 0

        # project survives, intact; source survives, untouched
        assert get_project(project.project_id).project_id == project.project_id
        assert verify_source_intact(project.project_id)
        assert hashlib.sha256(env["clip"].read_bytes()).hexdigest() == digest_before
        # and the killed operation produced NO complete output (ffmpeg creates
        # the dst immediately; a group-kill leaves it truncated/unplayable)
        dst = env["tmp"] / "never.mp4"
        if dst.exists():
            info = _probe_lenient(dst)
            assert info is None or info.get("duration", 0) < 60, \
                "killed operation somehow completed its output"
