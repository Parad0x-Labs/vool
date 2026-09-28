"""Retirement contract: VOOL never mutates the third-party OpenClaw Control UI."""

from __future__ import annotations

from pathlib import Path

from installer import inject_openclaw_web0_pill as pill


def test_apply_refuses_without_touching_third_party_files(tmp_path: Path) -> None:
    dist = tmp_path / "openclaw-dist"
    dist.mkdir()
    index_html = dist / "index.html"
    original = "<!doctype html><html><body><openclaw-app></openclaw-app></body></html>\n"
    index_html.write_text(original, encoding="utf-8")

    status = pill.apply(index_html)

    assert "refused" in status
    assert index_html.read_text(encoding="utf-8") == original
    assert list(dist.iterdir()) == [index_html], "no pill js may be dropped next to third-party files"


def test_apply_remove_also_refuses_without_side_effects(tmp_path: Path) -> None:
    dist = tmp_path / "openclaw-dist"
    dist.mkdir()
    index_html = dist / "index.html"
    original = "<!doctype html><html></html>\n"
    index_html.write_text(original, encoding="utf-8")

    status = pill.apply(index_html, remove=True)

    assert "refused" in status
    assert index_html.read_text(encoding="utf-8") == original


def test_retirement_notice_names_native_web_ui_and_skills_repo(capsys) -> None:
    pill.apply(Path("/nonexistent/index.html"))
    err = capsys.readouterr().err
    assert "retired" in err
    assert "/web0" in err
    assert "https://github.com/Parad0x-Labs/openclaw-skills" in err


def test_cli_main_exits_nonzero() -> None:
    assert pill.main([]) == 1
