from __future__ import annotations

from unittest import mock

from installer import start_windows_detached
from installer.start_windows_detached import (
    CREATE_BREAKAWAY_FROM_JOB,
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    DETACHED_PROCESS,
    _creationflags,
    _fallback_log_path,
    _wrap_command_for_windows,
)


def test_wrap_command_leaves_executables_unchanged() -> None:
    assert _wrap_command_for_windows(["python.exe", "-m", "apps.vool_api_server"]) == [
        "python.exe",
        "-m",
        "apps.vool_api_server",
    ]


def test_wrap_command_wraps_batch_launchers_on_windows(monkeypatch) -> None:
    monkeypatch.setattr("installer.start_windows_detached.os.name", "nt")
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")

    assert _wrap_command_for_windows(["C:\\Users\\test\\.local\\bin\\openclaw.cmd", "gateway", "run"]) == [
        "C:\\Windows\\System32\\cmd.exe",
        "/c",
        "C:\\Users\\test\\.local\\bin\\openclaw.cmd",
        "gateway",
        "run",
    ]


def test_creationflags_hide_windows_on_windows(monkeypatch) -> None:
    monkeypatch.setattr("installer.start_windows_detached.os.name", "nt")

    assert _creationflags(include_breakaway=False) == (
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    )
    assert _creationflags(include_breakaway=True) == (
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
    )


def test_fallback_log_path_keeps_original_name_and_suffix(tmp_path) -> None:
    path = tmp_path / "vool_api_child.err.log"

    fallback = _fallback_log_path(path)

    assert fallback.parent == tmp_path
    assert fallback.name.startswith("vool_api_child.err.")
    assert fallback.name.endswith(".log")
    assert fallback != path


def test_main_splits_options_from_command() -> None:
    with mock.patch.object(start_windows_detached, "start_detached", return_value=1234) as start_mock:
        assert (
            start_windows_detached.main(
                [
                    "--cwd",
                    "C:\\repo",
                    "--stdout",
                    "C:\\temp\\out.log",
                    "--stderr",
                    "C:\\temp\\err.log",
                    "--",
                    "python.exe",
                    "-m",
                    "apps.vool_api_server",
                ]
            )
            == 0
        )

    start_mock.assert_called_once_with(
        command=["python.exe", "-m", "apps.vool_api_server"],
        cwd="C:\\repo",
        stdout_path="C:\\temp\\out.log",
        stderr_path="C:\\temp\\err.log",
    )


def test_main_accepts_command_without_separator() -> None:
    with mock.patch.object(start_windows_detached, "start_detached", return_value=1234) as start_mock:
        assert (
            start_windows_detached.main(
                [
                    "--cwd",
                    "C:\\repo",
                    "--stdout",
                    "C:\\temp\\out.log",
                    "--stderr",
                    "C:\\temp\\err.log",
                    "python.exe",
                    "-m",
                    "apps.vool_api_server",
                    "--port",
                    "11435",
                ]
            )
            == 0
        )

    start_mock.assert_called_once_with(
        command=["python.exe", "-m", "apps.vool_api_server", "--port", "11435"],
        cwd="C:\\repo",
        stdout_path="C:\\temp\\out.log",
        stderr_path="C:\\temp\\err.log",
    )
