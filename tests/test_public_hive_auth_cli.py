from __future__ import annotations

import json

import ops.ensure_public_hive_auth as cli


def test_main_emits_json_and_returns_zero_when_bootstrap_is_ready(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "ensure_public_hive_auth",
        lambda **kwargs: {"ok": True, "status": "hydrated_from_bundle", "target_path": "/tmp/agent-bootstrap.json"},
    )

    exit_code = cli.main(["--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["status"] == "hydrated_from_bundle"


def test_main_returns_nonzero_for_incomplete_bootstrap(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "ensure_public_hive_auth",
        lambda **kwargs: {
            "ok": False,
            "status": "missing_ssh_key",
            "target_path": "/tmp/agent-bootstrap.json",
            "watch_host": "hive.example.test",
            "suggested_remote_config_path": "/etc/vool-hive-mind/watch-config.json",
            "suggested_command": "python -m ops.ensure_public_hive_auth --watch-host hive.example.test --remote-config-path /etc/vool-hive-mind/watch-config.json",
        },
    )

    exit_code = cli.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "status=missing_ssh_key" in captured.out
    assert "watch_host=hive.example.test" in captured.out
    assert "suggested_remote_config_path=/etc/vool-hive-mind/watch-config.json" in captured.out
    assert "next_step=python -m ops.ensure_public_hive_auth --watch-host hive.example.test --remote-config-path /etc/vool-hive-mind/watch-config.json" in captured.out
