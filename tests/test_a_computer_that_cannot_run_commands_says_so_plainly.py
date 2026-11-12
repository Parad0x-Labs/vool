"""A computer where no command can run says so in one plain sentence, with the one thing to do.

App bugs 2026-11-12, real app on Linux without bubblewrap: every command turn answered with the
sandbox's engineering paragraph ("Sandbox isolation unavailable: OS-level filesystem confinement
and network isolation are required but unavailable (expected bwrap on Linux; sandbox-exec on
macOS). A backend may be installed and still unusable: containers and CI runners commonly ship
util-linux but deny the namespace syscall ... network_isolation_mode='heuristic_only' ...").

The refusal itself is unchanged and correct: no command runs unconfined. What changed is what the
person reads; the engineering text stays in the tool's error for the trace.
"""

from __future__ import annotations

import pytest

from core.runtime_execution_tools import _run_command
from sandbox import job_runner

ENGINEERING_WORDS = (
    "OS-level",
    "confinement",
    "sandbox-exec",
    "util-linux",
    "namespace",
    "syscall",
    "network_isolation_mode",
    "heuristic_only",
    "backend",
    "probe",
    "Sandbox isolation unavailable",
)


def _plain(text: str) -> None:
    for word in ENGINEERING_WORDS:
        assert word not in text, f"{word!r} in {text!r}"


@pytest.fixture
def no_usable_backend(monkeypatch):
    job_runner.reset_isolation_backend_probe_cache()
    monkeypatch.setattr(job_runner, "_backend_usable", lambda name, probe_argv: False)
    yield monkeypatch
    job_runner.reset_isolation_backend_probe_cache()


def test_linux_without_bubblewrap_names_it_and_how_to_install_it(monkeypatch) -> None:
    monkeypatch.setattr(job_runner, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(job_runner.sys, "platform", "linux")
    monkeypatch.setattr(job_runner.shutil, "which", lambda name: None)

    text = job_runner.isolation_unavailable_for_person()

    assert text == (
        "I can't run commands on this computer yet: VOOL runs each command inside a safety box, and that "
        "needs bubblewrap, a small free tool. Install it (on Ubuntu or Debian: sudo apt install "
        "bubblewrap), then ask again."
    )
    _plain(text)


def test_linux_with_bubblewrap_blocked_points_at_the_one_time_fix(monkeypatch) -> None:
    monkeypatch.setattr(job_runner, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(job_runner.sys, "platform", "linux")
    monkeypatch.setattr(job_runner.shutil, "which", lambda name: f"/usr/bin/{name}")

    text = job_runner.isolation_unavailable_for_person()

    assert "this system stops bubblewrap" in text and "Commands in chat on Linux" in text, text
    _plain(text)


def test_the_install_guide_has_the_section_the_sentence_names() -> None:
    from pathlib import Path

    guide = (Path(__file__).resolve().parents[1] / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    assert "### Commands in chat on Linux" in guide
    assert "sudo apt install bubblewrap" in guide


@pytest.mark.parametrize(("windows", "platform", "lead"), [
    (True, "win32", "I can't run commands on Windows yet:"),
    (False, "darwin", "I can't run commands on this Mac right now:"),
])
def test_other_systems_get_their_own_plain_sentence(monkeypatch, windows: bool, platform: str, lead: str) -> None:
    monkeypatch.setattr(job_runner, "_is_windows_platform", lambda: windows)
    monkeypatch.setattr(job_runner.sys, "platform", platform)

    text = job_runner.isolation_unavailable_for_person()

    assert text.startswith(lead), text
    _plain(text)


def test_a_command_turn_on_such_a_computer_answers_with_the_plain_sentence(no_usable_backend, tmp_path) -> None:
    """The real command tool, on a host where every backend is present and none may run."""
    no_usable_backend.setattr(job_runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    (tmp_path / "orders.csv").write_text("id,total\n1,125\n", encoding="utf-8")

    result = _run_command({"command": "sed -n 2p orders.csv"}, workspace_root=tmp_path)

    assert result.ok is False and result.status == "sandbox_unavailable"
    assert result.response_text == job_runner.isolation_unavailable_for_person()
    _plain(result.response_text)
    assert str(tmp_path) not in result.response_text
    # The trace keeps the engineering reason.
    assert str(result.details.get("error") or "").startswith("Sandbox isolation unavailable:")
