from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from apps.vool_cli import cmd_install_profile
from core.runtime_backbone import ProviderRegistrySnapshot
from core.runtime_install_profiles import InstallProfileTruth


def _profile(*, profile_id: str, ready: bool = True, degraded: bool = False, reasons: tuple[str, ...] = ()) -> InstallProfileTruth:
    return InstallProfileTruth(
        profile_id=profile_id,
        label=profile_id.title(),
        summary=f"{profile_id} summary",
        selection_source="auto",
        selected_model="qwen2.5:7b",
        provider_mix=tuple(),
        estimated_download_gb=8.0,
        estimated_disk_footprint_gb=12.0,
        minimum_free_space_gb=11.0,
        ram_expectation_gb=12.0,
        vram_expectation_gb=4.0,
        ready=ready,
        degraded=degraded,
        single_volume_ready=ready,
        reasons=reasons,
        volume_checks=tuple(),
    )


def test_cmd_install_profile_shows_current_profile(capsys) -> None:
    fake_context = SimpleNamespace(paths=SimpleNamespace(runtime_home="/tmp/vool-runtime"))
    snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=tuple(), capability_truth=tuple())

    with mock.patch("apps.vool_cli._bootstrap_cli_storage"), mock.patch(
        "apps.vool_cli.build_runtime_context",
        return_value=fake_context,
    ), mock.patch(
        "apps.vool_cli.build_provider_registry_snapshot",
        return_value=snapshot,
    ) as build_snapshot, mock.patch(
        "apps.vool_cli.installed_profile_id",
        return_value="local-only",
    ), mock.patch(
        "apps.vool_cli.active_install_profile_id",
        return_value="local-only",
    ), mock.patch(
        "apps.vool_cli.build_install_profile_truth",
        return_value=_profile(profile_id="local-only"),
    ):
        assert cmd_install_profile(json_mode=False) == 0

    build_snapshot.assert_called_once_with(
        runtime_home="/tmp/vool-runtime",
        requested_profile=None,
        honor_install_profile=False,
    )
    out = capsys.readouterr().out
    assert "VOOL install profile" in out
    assert "Stored profile:  ollama-only (local-only)" in out
    assert "Resolved profile: ollama-only (local-only)" in out


def test_cmd_install_profile_persists_ready_profile_and_requests_restart(capsys) -> None:
    fake_context = SimpleNamespace(paths=SimpleNamespace(runtime_home="/tmp/vool-runtime"))
    snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=tuple(), capability_truth=tuple())

    with mock.patch("apps.vool_cli._bootstrap_cli_storage"), mock.patch(
        "apps.vool_cli.build_runtime_context",
        return_value=fake_context,
    ), mock.patch(
        "apps.vool_cli.build_provider_registry_snapshot",
        return_value=snapshot,
    ), mock.patch(
        "apps.vool_cli.build_install_profile_truth",
        return_value=_profile(profile_id="local-only"),
    ), mock.patch(
        "apps.vool_cli.persist_install_profile_record",
        return_value=Path("/tmp/vool-runtime/config/install-profile.json"),
    ) as persist_record:
        assert cmd_install_profile(set_profile="local-only", json_mode=False) == 0

    persist_record.assert_called_once_with(
        "/tmp/vool-runtime",
        "local-only",
        selected_model="qwen2.5:7b",
    )
    out = capsys.readouterr().out
    assert "Install profile saved: ollama-only (local-only)" in out
    assert "Restart VOOL to apply the new provider mix." in out


def test_cmd_install_profile_accepts_ollama_only_alias(capsys) -> None:
    fake_context = SimpleNamespace(paths=SimpleNamespace(runtime_home="/tmp/vool-runtime"))
    snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=tuple(), capability_truth=tuple())

    with mock.patch("apps.vool_cli._bootstrap_cli_storage"), mock.patch(
        "apps.vool_cli.build_runtime_context",
        return_value=fake_context,
    ), mock.patch(
        "apps.vool_cli.build_provider_registry_snapshot",
        return_value=snapshot,
    ), mock.patch(
        "apps.vool_cli.build_install_profile_truth",
        return_value=_profile(profile_id="local-only"),
    ), mock.patch(
        "apps.vool_cli.persist_install_profile_record",
        return_value=Path("/tmp/vool-runtime/config/install-profile.json"),
    ) as persist_record:
        assert cmd_install_profile(set_profile="ollama-only", json_mode=False) == 0

    persist_record.assert_called_once_with(
        "/tmp/vool-runtime",
        "local-only",
        selected_model="qwen2.5:7b",
    )
    out = capsys.readouterr().out
    assert "Install profile saved: ollama-only (local-only)" in out


def test_cmd_install_profile_persists_resolved_profile_when_auto_recommended_is_requested(capsys) -> None:
    fake_context = SimpleNamespace(paths=SimpleNamespace(runtime_home="/tmp/vool-runtime"))
    snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=tuple(), capability_truth=tuple())

    with mock.patch("apps.vool_cli._bootstrap_cli_storage"), mock.patch(
        "apps.vool_cli.build_runtime_context",
        return_value=fake_context,
    ), mock.patch(
        "apps.vool_cli.installed_profile_id",
        return_value="local-only",
    ), mock.patch(
        "apps.vool_cli.build_provider_registry_snapshot",
        return_value=snapshot,
    ), mock.patch(
        "apps.vool_cli.build_install_profile_truth",
        return_value=_profile(profile_id="local-only"),
    ), mock.patch(
        "apps.vool_cli.persist_install_profile_record",
        return_value=Path("/tmp/vool-runtime/config/install-profile.json"),
    ) as persist_record:
        assert cmd_install_profile(set_profile="auto-recommended", json_mode=False) == 0

    persist_record.assert_called_once_with(
        "/tmp/vool-runtime",
        "local-only",
        selected_model="qwen2.5:7b",
    )
    out = capsys.readouterr().out
    assert "Install profile saved: ollama-only (local-only)" in out
    assert "Resolved profile:     ollama-only (local-only)" in out


def test_cmd_install_profile_blocks_unready_profile_switch(capsys) -> None:
    fake_context = SimpleNamespace(paths=SimpleNamespace(runtime_home="/tmp/vool-runtime"))
    snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=tuple(), capability_truth=tuple())

    with mock.patch("apps.vool_cli._bootstrap_cli_storage"), mock.patch(
        "apps.vool_cli.build_runtime_context",
        return_value=fake_context,
    ), mock.patch(
        "apps.vool_cli.build_provider_registry_snapshot",
        return_value=snapshot,
    ), mock.patch(
        "apps.vool_cli.build_install_profile_truth",
        return_value=_profile(
            profile_id="hybrid-kimi",
            ready=False,
            reasons=("hybrid-kimi needs KIMI_API_KEY before the remote queen lane is usable.",),
        ),
    ), mock.patch("apps.vool_cli.persist_install_profile_record") as persist_record:
        assert cmd_install_profile(set_profile="hybrid-kimi", json_mode=False) == 2

    persist_record.assert_not_called()
    out = capsys.readouterr().out
    assert "Install profile switch blocked: hybrid-kimi" in out
    assert "KIMI_API_KEY" in out
