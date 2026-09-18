from __future__ import annotations

from unittest import mock

from installer.llamacpp_server_launch import build_server_argv, launch_server_detached


def test_build_server_argv_full_offload_when_model_fits_vram() -> None:
    argv = build_server_argv(
        server_exe_path="C:/llama/llama-server.exe",
        model_path="C:/models/qwen2.5-7b-instruct-q4_k_m.gguf",
        port=8090,
        model_name="qwen2.5:7b-instruct-q4_k_m",
        vram_gb=8.0,
        context_window=4096,
    )

    assert argv[0] == "C:/llama/llama-server.exe"
    assert "-ngl" in argv
    assert argv[argv.index("-ngl") + 1] == "999"
    assert argv[argv.index("--port") + 1] == "8090"
    assert argv[argv.index("-c") + 1] == "4096"


def test_build_server_argv_partial_offload_when_model_exceeds_vram() -> None:
    argv = build_server_argv(
        server_exe_path="C:/llama/llama-server.exe",
        model_path="C:/models/qwen2.5-14b-instruct-q4_k_m.gguf",
        port=8091,
        model_name="qwen2.5:14b-instruct-q4_k_m",
        vram_gb=8.0,
    )

    ngl_value = int(argv[argv.index("-ngl") + 1])
    assert 0 < ngl_value < 999


def test_launch_server_detached_delegates_to_start_detached(tmp_path) -> None:
    with mock.patch("installer.llamacpp_server_launch.start_detached", return_value=4242) as start_mock:
        pid = launch_server_detached(
            server_exe_path=tmp_path / "llama-server.exe",
            model_path=tmp_path / "model.gguf",
            port=8090,
            model_name="qwen2.5:7b-instruct-q4_k_m",
            vram_gb=8.0,
            log_dir=tmp_path / "logs",
        )

    assert pid == 4242
    start_mock.assert_called_once()
    _, kwargs = start_mock.call_args
    assert "8090" in kwargs["command"]
    assert (tmp_path / "logs").exists()
