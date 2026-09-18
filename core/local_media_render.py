"""Bridge from VOOL to the local `vool-local-render` skill (SDXL via ComfyUI).

VOOL discovers the plugin's deterministic render script and runs it as a subprocess — the generation
logic, ComfyUI graph, and models stay inside the plugin (the "skill" boundary the operator chose);
VOOL only detects the intent and routes to it. Nothing here bundles ComfyUI or a model. If the skill
is not installed or ComfyUI is not running, this fails soft and the caller can fall back to the cloud
(fal) path or prompt the user.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.error

from core.runtime_paths import user_runtime_default
import urllib.request
from pathlib import Path

# Where the local-render skill's runtime script lives. Override with VOOL_LOCAL_RENDER_SCRIPT.
_DEFAULT_PLUGIN_SCRIPT = (
    Path.home() / "Desktop" / "Vool-skills-plugins" / "plugins" / "vool-local-render" / "runtime" / "render_sdxl.py"
)
_COMFY_SERVER = os.environ.get("VOOL_COMFY_SERVER", "127.0.0.1:8188")
# A cold first render (after ComfyUI starts) loads a ~6.6GB SDXL checkpoint and can take minutes on
# Apple Silicon; warm renders are ~1 min. Keep this generous so the first image never reports a false
# timeout. Override with VOOL_RENDER_TIMEOUT (shared with the render skill).
_RENDER_TIMEOUT = float(os.environ.get("VOOL_RENDER_TIMEOUT", "600"))
# Where the on-device image engine (ComfyUI) is installed, so VOOL can start it herself when an image
# is asked for and it isn't running. Overridable for a non-standard install.
_COMFY_HOME = os.environ.get("VOOL_COMFY_HOME", str(Path.home() / ".vool_video_lab" / "repos" / "ComfyUI"))
_COMFY_PYTHON = os.environ.get("VOOL_COMFY_PYTHON", str(Path.home() / ".vool_video_lab" / "envs" / "comfyui" / "bin" / "python"))
# How long a CHAT TURN waits for the engine to bind its port; separate from the per-render wait above.
#
# This was 180s, which is longer than a turn survives -- the turn was cut off before the honest
# "couldn't bring up the engine" message could be delivered, so the user waited three minutes and
# then got a generic non-answer. Measured on this Mac 2026-07-31, spawning the exact argv below:
# ComfyUI binds :8188 in 10.0s. The server binds BEFORE any checkpoint is loaded (the ~6.6GB SDXL
# load is the separate _RENDER_TIMEOUT above), so this only has to cover a torch import.
#
# 45s leaves a wide margin over the measured 10s while still fitting inside a turn. A start that
# crashes no longer waits at all -- ensure_service watches the child process and returns as soon as
# it exits. If the engine is merely slow, it is detached and keeps coming up, so the next request
# finds it ready; the message says exactly that rather than claiming it failed.
_COMFY_START_TIMEOUT = float(os.environ.get("VOOL_COMFY_START_TIMEOUT", "45"))


def comfyui_autostart_enabled() -> bool:
    """True when VOOL may start ComfyUI on demand (default on). Set VOOL_AUTOSTART_COMFYUI=0 to opt out."""
    return str(os.environ.get("VOOL_AUTOSTART_COMFYUI", "1")).strip().lower() not in ("0", "false", "no", "off")


_comfyui_mode: str | None = None   # the memory flag ComfyUI was last started with by us: "" | "lowvram" | "novram"


def ensure_comfyui(*, ready_timeout: float | None = None, low_memory: str = "") -> tuple[bool, str]:
    """Start the on-device image engine (ComfyUI) if it isn't already up, in the requested memory mode.

    ``low_memory`` picks the ComfyUI memory strategy so a render fits a constrained Mac instead of
    freezing it: "" = full speed (keeps the model resident), "lowvram" = offload weights to CPU RAM and
    stream them (roughly halves peak memory, a bit slower), "novram" = maximal offload (slowest, smallest
    footprint). If ComfyUI is already up in a DIFFERENT mode, it is restarted into the requested one.

    Lean start: core nodes only (our SDXL graph needs no custom nodes). Fail-soft when not installed.
    """
    global _comfyui_mode
    from core.local_service_autostart import LocalService, ensure_service

    port = (_COMFY_SERVER.rsplit(":", 1)[-1] or "8188").strip()
    log_home = Path(os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME") or user_runtime_default())
    desired = low_memory if low_memory in {"lowvram", "novram"} else ""
    extra = {"lowvram": ["--lowvram"], "novram": ["--novram"]}.get(desired, [])

    def _preflight() -> str | None:
        if not Path(_COMFY_PYTHON).exists() or not (Path(_COMFY_HOME) / "main.py").exists():
            return "The on-device image engine (ComfyUI) isn't installed on this machine."
        return None

    # Already up but in the wrong memory mode -> stop it so it restarts in the requested one.
    if comfyui_reachable() and _comfyui_mode != desired:
        from core.resource_governor import stop_comfyui
        if stop_comfyui():
            for _ in range(20):
                if not comfyui_reachable(timeout=1.0):
                    break
                time.sleep(0.5)

    service = LocalService(
        name="ComfyUI",
        reachable=lambda: comfyui_reachable(),
        argv=[_COMFY_PYTHON, "main.py", "--port", str(port), "--disable-all-custom-nodes", *extra],
        cwd=_COMFY_HOME,
        env={"PYTORCH_ENABLE_MPS_FALLBACK": "1"},
        ready_timeout=float(ready_timeout if ready_timeout is not None else _COMFY_START_TIMEOUT),
        log_path=str(log_home / "logs" / "comfyui.log"),
        preflight=_preflight,
    )
    ok, msg = ensure_service(service)
    if ok:
        _comfyui_mode = desired
    return ok, msg
_OK_RE = re.compile(r"^OK:\s*(.+)$", re.MULTILINE)
_LOCAL_CUE_RE = re.compile(r"\b(local(?:ly)?|on\s+my\s+machine|on\s+device|offline|private(?:ly)?|no\s+cloud|comfyui|sdxl)\b", re.IGNORECASE)


def local_render_script() -> Path | None:
    override = str(os.environ.get("VOOL_LOCAL_RENDER_SCRIPT") or "").strip()
    candidate = Path(override) if override else _DEFAULT_PLUGIN_SCRIPT
    return candidate if candidate.is_file() else None


def local_render_available() -> bool:
    """True when the vool-local-render skill is installed (its script is present)."""
    return local_render_script() is not None


def wants_local_render(text: str) -> bool:
    """True when the message explicitly asks for a local/private render."""
    return bool(_LOCAL_CUE_RE.search(str(text or "")))


def comfyui_reachable(server: str = _COMFY_SERVER, timeout: float = 2.0) -> bool:
    url = "http://" + str(server or _COMFY_SERVER).replace("http://", "").rstrip("/") + "/system_stats"
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except (OSError, urllib.error.URLError, ValueError):
        return False


def run_local_image_render(
    prompt: str, *, style: str = "photoreal", timeout: float | None = None,
    runner: object | None = None, width: int = 1024, height: int = 1024, steps: int = 30,
) -> tuple[bool, str]:
    """Run the plugin's render script as a subprocess. Returns (ok, image_path) or (False, message).

    ``width``/``height``/``steps`` size the render — the governor turns these down (768/512, fewer steps)
    on a memory-constrained Mac so the image still renders instead of being refused. ``runner`` (test
    seam) defaults to subprocess.run. ``timeout`` is the render wait (default VOOL_RENDER_TIMEOUT); the
    subprocess gets a small margin on top so the client's clean "timed out" message surfaces first.
    """
    if not str(prompt or "").strip():
        return False, "An image prompt is required."
    script = local_render_script()
    if script is None and runner is None:
        return False, "The local render skill (vool-local-render) isn't installed on this machine."
    # The injected runner is the subprocess seam used by tests and diagnostics. It must be
    # able to exercise parsing and error handling even when the optional skill is absent.
    if script is None:
        script = Path("vool-local-render")
    render_timeout = float(timeout if timeout is not None else _RENDER_TIMEOUT)
    run = runner or subprocess.run
    cmd = [
        sys.executable, str(script), "--prompt", str(prompt),
        "--style", str(style or "photoreal"), "--timeout", str(render_timeout),
        "--width", str(int(width)), "--height", str(int(height)), "--steps", str(int(steps)),
    ]
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=render_timeout + 60)
    except subprocess.TimeoutExpired:
        return False, "The local render timed out."
    except Exception as exc:  # never surface a raw traceback to the chat
        return False, f"Local render could not start ({type(exc).__name__})."
    stdout = getattr(proc, "stdout", "") or ""
    match = _OK_RE.search(stdout)
    if getattr(proc, "returncode", 1) == 0 and match:
        return True, match.group(1).strip()
    combined = stdout + (getattr(proc, "stderr", "") or "")
    err = next((line for line in combined.splitlines() if line.startswith("ERROR:")), "")
    return False, (err or combined.strip()[:300] or "Local render failed.")


__all__ = [
    "comfyui_autostart_enabled",
    "comfyui_reachable",
    "ensure_comfyui",
    "local_render_available",
    "local_render_script",
    "run_local_image_render",
    "wants_local_render",
]
