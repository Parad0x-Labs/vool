"""VOOL -> vool-local-render skill bridge: intent cues, subprocess parsing, fail-soft."""

from __future__ import annotations

from core import local_media_render as lmr


class _Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_local_cue_detection() -> None:
    assert lmr.wants_local_render("draw me a cat locally") is True
    assert lmr.wants_local_render("make an image on my machine") is True
    assert lmr.wants_local_render("render this privately, no cloud") is True
    assert lmr.wants_local_render("generate a picture of a cat") is False


def test_run_local_render_parses_ok_path() -> None:
    ok, ref = lmr.run_local_image_render(
        "a cat", runner=lambda *a, **k: _Proc(0, "OK: /Users/x/Pictures/VOOL-Renders/cat_ab12.png\n")
    )
    assert ok is True
    assert ref.endswith("cat_ab12.png")


def test_run_local_render_surfaces_error_line() -> None:
    ok, msg = lmr.run_local_image_render(
        "a cat", runner=lambda *a, **k: _Proc(3, "", "ERROR: ComfyUI is not reachable at 127.0.0.1:8188.")
    )
    assert ok is False
    assert "ComfyUI is not reachable" in msg


def test_run_local_render_requires_prompt() -> None:
    ok, msg = lmr.run_local_image_render("   ", runner=lambda *a, **k: _Proc(0, "OK: x"))
    assert ok is False
    assert "prompt" in msg.lower()


def test_missing_script_fails_soft(monkeypatch) -> None:
    monkeypatch.setattr(lmr, "local_render_script", lambda: None)
    assert lmr.local_render_available() is False
    ok, msg = lmr.run_local_image_render("a cat")
    assert ok is False
    assert "isn't installed" in msg


def test_render_passes_timeout_to_client_with_subprocess_margin(monkeypatch) -> None:
    # The cold first render can take minutes; the client gets an explicit --timeout and the subprocess
    # a margin on top, so a slow-but-fine render never reports a false timeout (the real Elon/alien bug).
    from pathlib import Path

    monkeypatch.setattr(lmr, "local_render_script", lambda: Path("/tmp/render_sdxl.py"))
    captured: dict = {}

    def _runner(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        return _Proc(0, "OK: /tmp/out.png")

    ok, ref = lmr.run_local_image_render("a cat", timeout=120.0, runner=_runner)
    assert ok is True and ref.endswith("out.png")
    assert "--timeout" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--timeout") + 1] == "120.0"
    assert captured["timeout"] == 180.0  # client render wait + 60s margin


def test_render_timeout_defaults_from_env(monkeypatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(lmr, "_RENDER_TIMEOUT", 600.0)
    monkeypatch.setattr(lmr, "local_render_script", lambda: Path("/tmp/render_sdxl.py"))
    captured: dict = {}

    def _runner(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        return _Proc(0, "OK: /tmp/out.png")

    lmr.run_local_image_render("a cat", runner=_runner)
    assert captured["cmd"][captured["cmd"].index("--timeout") + 1] == "600.0"
    assert captured["timeout"] == 660.0


def test_comfyui_autostart_gating(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_AUTOSTART_COMFYUI", raising=False)
    assert lmr.comfyui_autostart_enabled() is True  # default on
    monkeypatch.setenv("VOOL_AUTOSTART_COMFYUI", "0")
    assert lmr.comfyui_autostart_enabled() is False
    monkeypatch.setenv("VOOL_AUTOSTART_COMFYUI", "off")
    assert lmr.comfyui_autostart_enabled() is False


def test_ensure_comfyui_fails_soft_when_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(lmr, "_COMFY_PYTHON", "/nope/bin/python")
    monkeypatch.setattr(lmr, "_COMFY_HOME", "/nope/ComfyUI")
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda *a, **k: False)
    ok, msg = lmr.ensure_comfyui()
    assert ok is False and "isn't installed" in msg


def test_ensure_comfyui_builds_a_lean_hardcoded_command(monkeypatch, tmp_path) -> None:
    (tmp_path / "main.py").write_text("")
    py = tmp_path / "python"
    py.write_text("")
    monkeypatch.setattr(lmr, "_COMFY_HOME", str(tmp_path))
    monkeypatch.setattr(lmr, "_COMFY_PYTHON", str(py))
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda *a, **k: False)

    import core.local_service_autostart as sa
    captured: dict = {}

    def _fake_ensure(service, **kwargs):
        captured["argv"] = service.argv
        captured["cwd"] = service.cwd
        captured["env"] = dict(service.env)
        return True, "ComfyUI is up"

    monkeypatch.setattr(sa, "ensure_service", _fake_ensure)
    ok, _ = lmr.ensure_comfyui()
    assert ok is True
    assert captured["argv"][0] == str(py) and captured["argv"][1] == "main.py"
    assert "--disable-all-custom-nodes" in captured["argv"]  # lean: skips the slow Manager startup
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"]["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"
