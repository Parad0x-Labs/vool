from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_windows_stack_handoff_covers_fork_repositories() -> None:
    script = (REPO_ROOT / "tools" / "windows_stack_handoff.ps1").read_text(encoding="utf-8")

    for marker in (
        "vool-local",
        "openclaw-skills",
        "openclaw",
        "liquefy",
        "dna-x402",
        "dna-x402-builders",
        "web0-resolver",
        "agent-null",
        "Dark-Null-Protocol",
        "nebula-media",
        "web0",
        "Parad0x-Command",
        "parad0x-media-engine",
        "vool.windows_stack_handoff.v1",
        "dist\\windows-stack-handoff",
    ):
        assert marker in script


def test_windows_stack_handoff_has_fast_and_release_profiles() -> None:
    script = (REPO_ROOT / "tools" / "windows_stack_handoff.ps1").read_text(encoding="utf-8")
    wrapper = (REPO_ROOT / "Test_VOOL_Windows_Stack.cmd").read_text(encoding="utf-8")
    docs = (REPO_ROOT / "docs" / "WINDOWS_ONE_CLICK_READINESS.md").read_text(encoding="utf-8")

    assert '[ValidateSet("fast", "release")]' in script
    assert "Test_VOOL_Windows_Gauntlet.cmd" in script
    assert "Install_DNA_X402_Windows.cmd" in script
    assert "Install_Dark_Null_Windows.cmd" in script
    assert "Install_Nebula_Media_Windows.cmd" in script
    assert "Install_DNA_X402_Builders_Windows.cmd" in script
    assert "windows_stack_handoff.ps1" in wrapper
    assert "Test_VOOL_Windows_Stack.cmd" in docs
