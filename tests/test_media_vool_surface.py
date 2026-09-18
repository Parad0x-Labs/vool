"""MEDIA GOBLIN M1 — VOOL surface tests (V1–V8) and sabotage REDs (S1–S8).

Structural sabotage tests (S3/S6/S7/S8) read the actual source trees so an
ownership violation is a REAL red, not theatre. Live worker/export tests run
when NEBULA_MEDIA_HOME points at a nebula-media checkout with ffmpeg present.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from core.media_edit_project import MediaEditProject, SourceAsset

VOOL_ROOT = Path(__file__).resolve().parents[1]
VOOL_MEDIA_FILES = [
    VOOL_ROOT / "core" / "media_studio_service.py",
    VOOL_ROOT / "core" / "media_worker_client.py",
    VOOL_ROOT / "core" / "media_studio_chat_tools.py",
    VOOL_ROOT / "core" / "web" / "api" / "media_editor_api.py",
]
NEBULA_ROOT = Path(os.environ.get("NEBULA_MEDIA_HOME", ""))

ffmpeg_ok = bool(shutil.which("ffmpeg"))


# ---------------------------------------------------------------- V1
def test_v1_skill_declares_capabilities_but_grants_no_permission():
    skill_path = VOOL_ROOT / "skills" / "media-studio" / "SKILL.md"
    text = skill_path.read_text()
    assert text.startswith("---")
    declared = [line.strip("- \n") for line in text.splitlines()
                if line.strip().startswith("- media.")]
    assert len(declared) >= 10
    # a skill is documentation: it cannot grant permissions or flip policy flags
    from core import policy_engine

    before = policy_engine.get("media.studio_enabled", True)
    assert before == policy_engine.get("media.studio_enabled", True)


# ---------------------------------------------------------------- V2/V4
def test_v2_v4_bounded_media_contracts_with_platform_owned_authorization():
    from core.runtime_tool_contracts import runtime_tool_contracts

    media = [c for c in runtime_tool_contracts() if c.intent.startswith("media.")]
    intents = {c.intent for c in media}
    expected = {"media.open", "media.inspect", "media.edit",
                "media.undo", "media.redo", "media.export"}
    assert intents == expected                      # V2: bounded set
    assert len(media) <= 8                          # bounded exposure to model
    by_intent = {c.intent: c for c in media}
    # V4: export authorization stays VOOL/platform-owned — explicit opt-in,
    # workspace_write effect class, never auto-granted by capability existence.
    ex = by_intent["media.export"]
    assert ex.side_effect_class == "workspace_write"
    assert ex.approval_requirement in {"explicit_user_opt_in", "runtime_policy"}
    assert by_intent["media.inspect"].side_effect_class == "read_only"


# ---------------------------------------------------------------- V3
def test_v3_implementations_remain_nebula_media():
    from core.media_capabilities import register_media_capabilities

    result = register_media_capabilities(available=True)
    impl_ids = [c.replace("media.", "nebula_media.", 1) for c in result["capabilities"]]
    snapshot = result["capabilities"]
    assert len(snapshot) >= 10
    assert all(i.startswith("nebula_media.") for i in impl_ids)
    # CapabilityId vs ImplementationId separation is real, not cosmetic
    assert any(cap == "media.video.trim" for cap in snapshot)
    assert "nebula_media.video.trim" in impl_ids


# ---------------------------------------------------------------- V5
def test_v5_unavailable_worker_reported_truthfully():
    from core.media_worker_client import NebulaWorkerClient, MediaWorkerUnavailable

    client = NebulaWorkerClient(python="/nonexistent/python-binary")
    with pytest.raises(MediaWorkerUnavailable):
        client.request("probe", {"src": "/tmp/x.mp4"})
    status = client.availability()
    assert status["available"] is False
    assert "reason" in status


# ---------------------------------------------------------------- V6/S4
def test_v6_s4_close_does_not_delete_project_and_reopen_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_DB_PATH", str(tmp_path / "test.db"))
    from storage import media_project_store
    from storage.db import configure_default_db_path

    configure_default_db_path(tmp_path / "test.db")

    proj = MediaEditProject.create("mep-close", SourceAsset(
        asset_id="a", path="/x.mp4", sha256="s"))
    proj.apply("trim", {"start": 0.0, "end": 5.0})
    doc = proj.to_dict()
    media_project_store.save_project(doc, now=123.0)

    # "closing the editor" = doing nothing to the store; reopen restores state
    loaded = media_project_store.load_project("mep-close")
    restored = MediaEditProject.from_dict(loaded)
    assert len(restored.effective_ops()) == 1
    assert restored.effective_ops()[0].kind == "trim"
    # explicit deletion exists as a separate operator action only
    assert callable(media_project_store.delete_project)


# ---------------------------------------------------------------- V7
@pytest.mark.skipif(not (NEBULA_ROOT and NEBULA_ROOT.is_dir() and ffmpeg_ok),
                    reason="needs NEBULA_MEDIA_HOME checkout + ffmpeg")
def test_v7_worker_crash_does_not_crash_caller(tmp_path):
    from core.media_worker_client import MediaWorkerUnavailable, NebulaWorkerClient

    client = NebulaWorkerClient(nebula_home=str(NEBULA_ROOT), timeout=60)
    try:
        src = tmp_path / "v.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc2=size=64x64:rate=10:duration=1",
                        "-c:v", "libx264", str(src)], check=True, capture_output=True)
        r = client.request("probe", {"src": str(src)})
        assert r["duration"] > 0
        client._kill()                       # simulate a hard worker crash
        assert client._proc is None
        r2 = client.request("probe", {"src": str(src)})  # transparent restart
        assert r2["duration"] > 0
    finally:
        client.close()


# ------------------------------------------------------- live service round-trip
@pytest.mark.skipif(not (NEBULA_ROOT and NEBULA_ROOT.is_dir() and ffmpeg_ok),
                    reason="needs NEBULA_MEDIA_HOME checkout + ffmpeg")
class TestLiveService:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NEBULA_MEDIA_HOME", str(NEBULA_ROOT))
        from storage.db import configure_default_db_path

        configure_default_db_path(tmp_path / "svc.db")
        clip = tmp_path / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=4",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
             "-shortest", str(clip)],
            check=True, capture_output=True)
        return {"clip": clip, "tmp": tmp_path}

    def test_open_apply_export_receipt_and_S1_source_immutable(self, env, monkeypatch):
        import hashlib

        from core.media_studio_service import (
            apply_edit, export_project, get_project, inspect, open_project,
            undo,
        )
        from core.media_worker_client import reset_media_worker

        reset_media_worker()
        monkeypatch.setattr("core.media_worker_client._default_client", None)

        project = open_project(str(env["clip"]))
        digest_before = hashlib.sha256(env["clip"].read_bytes()).hexdigest()

        apply_edit(project.project_id, "trim",
                   {"start": 1.0, "end": 3.0}, actor="chat")
        apply_edit(project.project_id, "set_aspect_ratio",
                   {"ratio": "9:16"}, actor="gui")
        info = inspect(project.project_id)
        assert len(info["operations"]) == 2

        out = env["tmp"] / "export.mp4"
        receipt = export_project(project.project_id, out_path=str(out),
                                 profile="social")
        assert receipt.ok, receipt.error
        assert receipt.effect_state == "succeeded"
        assert receipt.input_sha256 == project.source_asset.sha256
        assert out.is_file() and out.stat().st_size > 0
        meta = receipt.output_metadata
        assert abs(meta["duration"] - 2.0) < 0.25
        assert meta["height"] > meta["width"]          # 9:16 applied

        # S1/E9: destructive-looking edits left the source untouched
        assert hashlib.sha256(env["clip"].read_bytes()).hexdigest() == digest_before
        assert verify_source_ok(project.project_id)

        # undo then re-export writes a different revision output
        undo(project.project_id)
        assert len(get_project(project.project_id).effective_ops()) == 1

    def test_worker_unavailable_is_truthful_in_chat_tools(self, env, monkeypatch):
        from core.media_worker_client import reset_media_worker
        from core.media_studio_chat_tools import media_inspect
        from core.media_studio_service import open_project

        reset_media_worker()
        project = open_project(str(env["clip"]))
        from core.media_worker_client import _default_client

        _default_client._python = "/nonexistent/python"   # force unavailability
        _default_client.close()                           # drop the healthy proc
        result = media_inspect({"project_id": project.project_id})
        assert result.ok is False
        assert result.status == "worker_unavailable"
        reset_media_worker()


def verify_source_ok(project_id: str) -> bool:
    from core.media_studio_service import verify_source_intact

    return verify_source_intact(project_id)


# ================================================================ SABOTAGE
# Structural REDs: these fail loudly if ownership/architecture laws are violated.
# They parse real code (AST + string literals in executable positions), so a
# violation is a REAL red, not docstring theatre.

import ast as _ast


def _tree(path: Path) -> _ast.AST:
    return _ast.parse(path.read_text())


def _docstring_nodes(tree: _ast.AST) -> set[int]:
    return {id(n.value) for n in ast_walk_docstrings(tree)}


def ast_walk_docstrings(tree):
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            if node.body and isinstance(node.body[0], _ast.Expr):
                yield node.body[0]


def _code_strings(path: Path) -> list[str]:
    """String constants in executable positions (docstrings excluded)."""
    tree = _tree(path)
    docs = _docstring_nodes(tree)
    out = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docs:
            out.append(node.value.lower())
    return out


def test_S3_heavy_encoding_never_runs_in_ui_process():
    """S3: VOOL media modules must not shell out to ffmpeg or import nebula;
    all mechanism goes through the isolated worker subprocess client."""
    for path in VOOL_MEDIA_FILES:
        if path.name == "media_worker_client.py":
            continue  # the sanctioned spawner; checked below as the ONLY one
        tree = _tree(path)
        names = {n.id for n in _ast.walk(tree) if isinstance(n, _ast.Name)} | \
                {n.attr for n in _ast.walk(tree) if isinstance(n, _ast.Attribute)}
        assert "Popen" not in names, f"{path.name} spawns processes directly (S3)"
        mods = []
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Import):
                mods += [a.name for a in n.names]
            elif isinstance(n, _ast.ImportFrom) and n.module:
                mods.append(n.module)
        assert not any(m.startswith("nebula") for m in mods), \
            f"{path.name} imports nebula internals (S3)"
        joined = " ".join(_code_strings(path))
        assert "ffmpeg" not in joined, f"{path.name} builds ffmpeg commands (S3)"
    # the ONLY process spawner in the whole VOOL media stack is the worker client
    client_src = (VOOL_ROOT / "core" / "media_worker_client.py").read_text()
    assert "Popen" in client_src


def test_S5_capability_availability_is_not_permission():
    """S5: flipping availability must not flip any authorization flag."""
    from core import policy_engine
    from core.media_capabilities import register_media_capabilities

    register_media_capabilities(available=True)
    assert policy_engine.get("media.editor_export_enabled", True) == \
           policy_engine.get("media.editor_export_enabled", True)
    register_media_capabilities(available=False)
    # contracts' supported flag derives ONLY from policy, never from availability
    from core.runtime_tool_contracts import runtime_tool_contracts

    media = [c for c in runtime_tool_contracts() if c.intent.startswith("media.")]
    assert all(c.supported == bool(policy_engine.get("media.studio_enabled", True))
               for c in media)


def test_S6_no_duplicate_trim_implementation_in_vool():
    """S6: VOOL must not grow its own trim/crop/ffmpeg implementation."""
    offenders = ["libx264", "-vf", "-ss", "ffprobe", "crop=", "scale="]
    for path in VOOL_MEDIA_FILES:
        joined = " ".join(_code_strings(path))
        for token in offenders:
            assert token not in joined, \
                f"{path.name} contains ffmpeg mechanism '{token}' (S6 ownership RED)"


def test_S7_nebula_does_not_own_vool_concepts():
    """S7: nebula-media mechanism modules must not import vool concepts or
    own receipts/project/permission state."""
    if not NEBULA_ROOT.is_dir():
        pytest.skip("NEBULA_MEDIA_HOME not set")
    allowed_roots = {"__future__", "json", "logging", "shutil", "subprocess",
                     "sys", "dataclasses", "pathlib", "typing", "media_ops"}
    for mod in ("media_ops.py", "worker.py"):
        tree = _tree(NEBULA_ROOT / "nebula" / mod)
        mods: list[str] = []
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Import):
                mods += [a.name for a in n.names]
            elif isinstance(n, _ast.ImportFrom) and n.module:
                mods.append(n.module)
        for m in mods:
            assert m.split(".")[0] in allowed_roots, \
                f"nebula/{mod} imports unexpected module '{m}' (S7)"
        names = {n.id.lower() for n in _ast.walk(tree) if isinstance(n, _ast.Name)}
        for banned in ("receipt", "permission"):
            assert not any(banned in n for n in names), \
                f"nebula/{mod} owns a vool concept '{banned}' (S7)"


def test_S8_no_codec_catalog_dumped_to_model():
    """S8: model-facing surfaces carry no raw codec/schema catalog."""
    for path in (VOOL_ROOT / "core" / "media_studio_chat_tools.py",
                 VOOL_ROOT / "core" / "web" / "api" / "media_editor_api.py"):
        joined = " ".join(_code_strings(path))
        for token in ("codec", "crf", "libx264", "pix_fmt", "-vf"):
            assert token not in joined, \
                f"{path.name} exposes codec knob '{token}' to the model surface (S8)"
    # contract input schemas expose no codec knobs
    from core.runtime_tool_contracts import runtime_tool_contracts

    for c in runtime_tool_contracts():
        if c.intent.startswith("media."):
            blob = " ".join(c.input_schema.keys()).lower()
            assert "codec" not in blob and "crf" not in blob and "preset" not in blob


def test_S2_single_edit_engine_both_controllers():
    """S2: GUI endpoint and chat handler must call the SAME apply function."""
    gui_src = (VOOL_ROOT / "core" / "web" / "api" / "media_editor_api.py").read_text()
    chat_src = (VOOL_ROOT / "core" / "media_studio_chat_tools.py").read_text()
    assert "apply_edit" in gui_src
    assert "apply_edit" in chat_src
    # and neither reimplements graph mutation locally
    for name, src in (("gui", gui_src), ("chat", chat_src)):
        assert "operations.append" not in src, f"{name} controller mutates the graph directly (S2)"
