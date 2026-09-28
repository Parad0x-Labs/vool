"""Retirement contract: VOOL never patches third-party OpenClaw JavaScript."""

from __future__ import annotations

from pathlib import Path

from installer import patch_openclaw_session_retry as retry


def test_apply_refuses_without_touching_third_party_files(tmp_path: Path) -> None:
    dist = tmp_path / "openclaw-dist"
    dist.mkdir()
    bundle = dist / "get-reply-abc123.js"
    original = "async function initSessionState(params){return await attempt(params,false);}\n"
    bundle.write_text(original, encoding="utf-8")

    status = retry.apply(bundle)

    assert "refused" in status
    assert bundle.read_text(encoding="utf-8") == original
    assert list(dist.iterdir()) == [bundle]


def test_apply_remove_also_refuses_without_side_effects(tmp_path: Path) -> None:
    bundle = tmp_path / "get-reply-abc123.js"
    original = "async function initSessionState(){}\n"
    bundle.write_text(original, encoding="utf-8")

    status = retry.apply(bundle, remove=True)

    assert "refused" in status
    assert bundle.read_text(encoding="utf-8") == original


def test_retirement_notice_names_native_startup_and_skills_repo(capsys) -> None:
    retry.apply(Path("/nonexistent/get-reply.js"))
    err = capsys.readouterr().err
    assert "retired" in err
    assert "Talk_To_VOOL" in err
    assert "https://github.com/Parad0x-Labs/openclaw-skills" in err


def test_cli_main_exits_nonzero() -> None:
    assert retry.main([]) == 1
