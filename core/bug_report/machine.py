"""Machine specs and a local health snapshot for bug reports: typed facts, never identities.

What an investigation needs to reproduce a problem is the SHAPE of the machine (OS release,
chip, memory, accelerator, local model server version, free-disk bucket), not who owns it.
Everything here is a short typed string read locally; nothing makes a network call except a
loopback read of the local model inventory the app already performs, and every probe that
fails reports ``unknown`` instead of a guess.

NEVER collected: hostname, user or account names, serial numbers, MAC/IP addresses, paths,
installed-application lists, wallet addresses or balances, API keys or which keys exist. Each
value still passes the outbound scanner with the rest of the report.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from typing import Any

UNKNOWN = "unknown"

_VERSION_RE = re.compile(r"(\d+(?:\.\d+){1,3})")
_SAFE_VALUE_RE = re.compile(r"[^A-Za-z0-9 ._:/()+,@-]")


def _clean(value: Any, *, max_chars: int = 80) -> str:
    text = _SAFE_VALUE_RE.sub("", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_chars] if text else UNKNOWN


def _run(argv: list[str], *, timeout: float = 3.0) -> str:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    if completed.returncode != 0:
        return ""
    return str(completed.stdout or "").strip()


def os_version() -> str:
    system = platform.system()
    if system == "Darwin":
        release = platform.mac_ver()[0]
        return _clean(f"macOS {release}") if release else "macOS unknown"
    if system == "Linux":
        try:
            with open("/etc/os-release", encoding="utf-8") as handle:
                fields = dict(
                    line.rstrip("\n").split("=", 1) for line in handle if "=" in line
                )
        except OSError:
            fields = {}
        name = fields.get("NAME", "").strip('"') or "Linux"
        version = fields.get("VERSION_ID", "").strip('"')
        return _clean(f"{name} {version}".strip())
    if system == "Windows":
        return _clean(f"Windows {platform.release()} build {platform.version()}")
    return _clean(system) if system else UNKNOWN


def chip() -> str:
    system = platform.system()
    if system == "Darwin":
        brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if brand:
            return _clean(brand)
    if system == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.lower().startswith("model name") and ":" in line:
                        return _clean(line.split(":", 1)[1])
        except OSError:
            pass
    return _clean(platform.processor()) if platform.processor() else UNKNOWN


def ram_gb() -> str:
    try:
        import psutil  # type: ignore

        return str(round(float(psutil.virtual_memory().total) / (1024.0 ** 3)))
    except Exception:
        return UNKNOWN


def accelerator() -> tuple[str, str]:
    """(gpu description, gpu memory in whole GB). Apple silicon shares system memory."""
    try:
        from core.hardware_tier import detect_gpu_devices

        devices = detect_gpu_devices()
    except Exception:
        return UNKNOWN, UNKNOWN
    if not devices:
        return "none", "0"
    device = devices[0]
    memory = str(round(device.vram_gb)) if device.vram_gb is not None else UNKNOWN
    if device.backend == "mps":
        memory = "shared"
    return _clean(f"{device.name} ({device.backend})"), memory


def ollama_version() -> str:
    executable = shutil.which("ollama")
    if not executable:
        return "not installed"
    match = _VERSION_RE.search(_run([executable, "--version"]))
    return match.group(1) if match else UNKNOWN


def install_kind() -> str:
    if os.environ.get("VOOL_APP_BUNDLE") or ".app/Contents/" in os.path.abspath(__file__):
        return "macOS app"
    if os.environ.get("APPIMAGE"):
        return "AppImage"
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if os.path.isdir(os.path.join(root, ".git")):
        return "source checkout"
    return "installed package"


def capture_machine() -> dict[str, str]:
    """The machine shape for the Environment section. Every value is a short typed string."""
    gpu, gpu_memory = accelerator()
    return {
        "os_version": os_version(),
        "chip": chip(),
        "cpu_cores": str(os.cpu_count() or UNKNOWN),
        "ram_gb": ram_gb(),
        "gpu": gpu,
        "gpu_memory_gb": gpu_memory,
        "ollama_version": ollama_version(),
        "install_kind": install_kind(),
    }


def _disk_free_bucket() -> str:
    try:
        from core.runtime_paths import data_path

        target = data_path(".")
        while not os.path.exists(target) and os.path.dirname(str(target)) != str(target):
            target = os.path.dirname(str(target))
        free_gb = shutil.disk_usage(target).free / (1024.0 ** 3)
    except Exception:
        return UNKNOWN
    if free_gb < 5:
        return "under 5 GB"
    if free_gb < 20:
        return "5-20 GB"
    if free_gb < 100:
        return "20-100 GB"
    return "over 100 GB"


def _local_models() -> tuple[str, list[str]]:
    """(local model server answered, installed text-model names) via a loopback read."""
    try:
        from core.local_model_policy import resolve_local_model_policy

        if resolve_local_model_policy().local_models_disabled:
            return "turned off", []
    except Exception:
        pass
    try:
        from core.local_ollama_inventory import _ollama_models_payload, is_text_generation_ollama_model

        # The raw payload, not the inventory helper: the helper folds "server down" into an
        # empty list, and "no models" is a different finding from "nothing answered".
        payload = _ollama_models_payload(env=os.environ, base_url=None, path="/api/tags", timeout_seconds=3.0)
    except Exception:
        return UNKNOWN, []
    if not isinstance(payload, list):
        return "no", []
    names = [
        str(item.get("name") or item.get("model") or "")
        for item in payload
        if isinstance(item, dict) and is_text_generation_ollama_model(str(item.get("name") or item.get("model") or ""))
    ]
    return "yes", [name for name in names if name]


def _open_circuits() -> str:
    try:
        from core.model_health import _HEALTH, _HEALTH_LOCK

        with _HEALTH_LOCK:
            return str(sum(1 for health in _HEALTH.values() if health.circuit_open))
    except Exception:
        return UNKNOWN


def capture_health(*, models: tuple[str, ...] = ()) -> dict[str, str]:
    """A local health snapshot: is the model server up, is the model in the report installed,
    how much disk is left, are any providers cut off. Counts and yes/no only."""
    from core.bug_report.allowlist import sanitize_model_identifier

    reachable, installed = _local_models()
    snapshot = {
        "local_model_server": reachable,
        "local_models_installed": str(len(installed)) if reachable == "yes" else UNKNOWN,
        "disk_free": _disk_free_bucket(),
        "providers_cut_off": _open_circuits(),
    }
    wanted = [m for m in (sanitize_model_identifier(x) for x in models) if m and ":" in m]
    if wanted and reachable == "yes":
        snapshot["report_model_installed"] = "yes" if all(m in installed for m in wanted) else "no"
    return snapshot


__all__ = ["UNKNOWN", "capture_health", "capture_machine"]
