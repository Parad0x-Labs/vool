from __future__ import annotations

import json
from pathlib import Path

import core.proof_manifest as pm
from core.proof_manifest import repo_source_snapshot


def test_repo_source_snapshot_uses_archive_build_metadata_when_git_is_absent(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "build-source.json").write_text(
        json.dumps(
            {
                "branch": "codex/honest-ollama-prewarm-bootstrap",
                "commit": "0123456789abcdef0123456789abcdef01234567",
                "dirty_state": True,
                "source_kind": "archive",
            }
        ),
        encoding="utf-8",
    )

    truth = repo_source_snapshot(tmp_path)

    assert truth["source_kind"] == "archive"
    assert truth["branch"] == "codex/honest-ollama-prewarm-bootstrap"
    assert truth["commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert truth["dirty_state"] is True


def test_repo_source_snapshot_coerces_string_dirty_state_values(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "build-source.json").write_text(
        json.dumps(
            {
                "branch": "main",
                "commit": "archive",
                "dirty_state": "false",
            }
        ),
        encoding="utf-8",
    )

    truth = repo_source_snapshot(tmp_path)

    assert truth["dirty_state"] is False


def test_zero_checks_surfaces_the_evaluated_count(tmp_path: Path, monkeypatch) -> None:
    # An archive build where every value is unknown evaluates no checks. overall_consistent
    # stays True (no inconsistency was found), but consistency_checks_evaluated == 0 lets a
    # caller distinguish "verified consistent" from "nothing to verify".
    monkeypatch.setattr(
        pm,
        "repo_source_snapshot",
        lambda root: {"branch": "archive", "commit": "archive", "dirty_state": True, "source_kind": "archive"},
    )
    manifest = pm.build_proof_manifest(
        repo_root=tmp_path, generated_by="test", install_receipt={}, runtime_health={}
    )
    assert manifest["consistency_checks_evaluated"] == 0
    assert manifest["overall_consistent"] is True


def test_trivial_commit_prefix_is_not_consistent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pm,
        "repo_source_snapshot",
        lambda root: {
            "branch": "main",
            "commit": "abcdef1234567890abcdef1234567890abcdef12",
            "dirty_state": False,
            "source_kind": "git",
        },
    )
    manifest = pm.build_proof_manifest(
        repo_root=tmp_path,
        generated_by="test",
        install_receipt={"commit": "a"},              # 1-char "prefix" must NOT pass
        runtime_health={"runtime": {"commit": "abcdef1"}},  # 7-char short SHA is consistent
    )
    checks = {c["name"]: c for c in manifest["consistency_checks"]}
    assert checks["repo_vs_runtime_commit"]["pass"] is True
    assert checks["repo_vs_receipt_commit"]["pass"] is False
    assert manifest["overall_consistent"] is False
