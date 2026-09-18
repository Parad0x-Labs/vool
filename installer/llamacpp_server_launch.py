"""Builds the llama-server.exe launch command and starts it detached on Windows.

Reuses installer/start_windows_detached.py's process-launch primitive (no console
window, survives the launching shell exiting) rather than reinventing it.
"""

from __future__ import annotations

from pathlib import Path

from core.local_model_bundles import llamacpp_offload_layers
from installer.start_windows_detached import start_detached


def build_server_argv(
    *,
    server_exe_path: str | Path,
    model_path: str | Path,
    port: int,
    model_name: str,
    vram_gb: float,
    context_window: int = 4096,
    host: str = "127.0.0.1",
) -> list[str]:
    n_gpu_layers = llamacpp_offload_layers(model_name=model_name, vram_gb=vram_gb)
    return [
        str(server_exe_path),
        "-m", str(model_path),
        "--host", host,
        "--port", str(port),
        "-ngl", str(n_gpu_layers),
        "-c", str(context_window),
    ]


def launch_server_detached(
    *,
    server_exe_path: str | Path,
    model_path: str | Path,
    port: int,
    model_name: str,
    vram_gb: float,
    log_dir: str | Path,
    context_window: int = 4096,
    host: str = "127.0.0.1",
) -> int:
    argv = build_server_argv(
        server_exe_path=server_exe_path,
        model_path=model_path,
        port=port,
        model_name=model_name,
        vram_gb=vram_gb,
        context_window=context_window,
        host=host,
    )
    log_root = Path(log_dir).expanduser().resolve()
    log_root.mkdir(parents=True, exist_ok=True)
    return start_detached(
        command=argv,
        cwd=str(Path(server_exe_path).expanduser().resolve().parent),
        stdout_path=str(log_root / f"llamacpp-{port}.log"),
        stderr_path=str(log_root / f"llamacpp-{port}.err.log"),
    )


__all__ = ["build_server_argv", "launch_server_detached"]
