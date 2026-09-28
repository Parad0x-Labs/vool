"""Retirement contract: OpenClaw agent registration is a side-effect-free refusal.

The OpenClaw integration is retired from VOOL product paths. The historic
registration entrypoint stays importable (the CI wheel smoke imports it), but it
must refuse honestly: it never reads, creates, or writes any OpenClaw state, and
an unrelated existing OpenClaw installation stays byte-for-byte intact.
"""

from __future__ import annotations

import json
from pathlib import Path

from installer import register_openclaw_agent as roa


def test_register_refuses_without_side_effects(tmp_path: Path) -> None:
    openclaw_home = tmp_path / ".openclaw"
    openclaw_home.mkdir()
    config_path = openclaw_home / "openclaw.json"
    original_config = json.dumps(
        {
            "agents": {"defaults": {"workspace": "/existing/workspace"}},
            "gateway": {"auth": {"mode": "token", "token": "keep-me"}},
        }
    )
    config_path.write_text(original_config, encoding="utf-8")
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))

    ok = roa.register(
        project_root=str(tmp_path / "project"),
        vool_home=str(tmp_path / "vool-home"),
        model_tag="qwen2.5:7b",
        display_name="Cornholio",
        openclaw_home=str(openclaw_home),
    )

    assert ok is False
    assert config_path.read_text(encoding="utf-8") == original_config
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert after == before, f"registration wrote files: {set(after) - set(before)}"


def test_register_refuses_when_no_openclaw_exists_at_all(tmp_path: Path) -> None:
    marker_dir = tmp_path / "untouched"
    marker_dir.mkdir()

    ok = roa.register(
        project_root=str(tmp_path / "project"),
        vool_home=str(tmp_path / "vool-home"),
    )

    assert ok is False
    # No OpenClaw home, config, agent dir, or workspace was created anywhere.
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == ["untouched"]


def test_retirement_notice_points_at_native_startup_and_skills_repo(capsys) -> None:
    roa.register(project_root="/tmp/p", vool_home="/tmp/v")
    err = capsys.readouterr().err
    assert "retired" in err
    assert "Start_VOOL" in err
    assert "https://github.com/Parad0x-Labs/openclaw-skills" in err


def test_cli_main_exits_nonzero_with_notice(capsys) -> None:
    assert roa.main(["/tmp/project", "/tmp/runtime", "model", "name"]) == 1
    assert "retired" in capsys.readouterr().err
