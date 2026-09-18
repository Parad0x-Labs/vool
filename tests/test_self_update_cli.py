"""Tests for the `vool update` CLI — the ONE signed-manifest authority (2026-09-01).

Rewritten in the consolidation amendment: `cmd_update` now delegates to core/updater
(the same authority the server, the chat UI and the wrapper chip share). The five
scenarios the legacy GitHub-releases lane pinned — up to date, available-without-apply,
apply starts the install, no target refuses, unreachable/deconfigured returns 1 — are
pinned against the new path with the subsystem's seams injected.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from apps.vool_cli import cmd_update
from core.app_version import VOOL_VERSION
from core.updater.runtime import reset_update_subsystem_for_tests

from .updater.helpers import generate_publisher_keypair, sha256_hex, sign_bytes


def _signed_manifest(version: str, notes: str, private_hex: str) -> bytes:
    blob = f"ARTIFACT-{version}".encode()
    manifest = {
        "schema": "vool.update.manifest.v1",
        "product": "vool",
        "channel": "stable",
        "version": version,
        "sequence": 91,
        "published_at": "2026-09-01T12:00:00Z",
        "minimum_compatible": "0.4.0",
        "notes": notes,
        "artifacts": {
            "macos-arm64": {
                "url": "https://updates.example.invalid/a.zip",
                "size": len(blob),
                "sha256": sha256_hex(blob),
                "signature": sign_bytes(private_hex, blob),
            }
        },
    }
    manifest["signature"] = {
        "key_id": "release-2026-01",
        "sig": sign_bytes(
            private_hex,
            json.dumps(
                {k: v for k, v in manifest.items() if k != "signature"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        ),
    }
    return json.dumps(manifest).encode()


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_update_subsystem_for_tests()


def _bump(version: str) -> str:
    major, minor, patch = [*version.split("."), "0", "0"][:3]
    return f"{major}.{minor}.{int(patch) + 1}"


def _booted_subsystem(tmp_path: Path, version: str, notes: str):
    """A booted-in-place subsystem whose feed serves a manifest signed by ITS OWN
    pinned key (no network, no key leakage across fixtures)."""
    private_hex, public_hex = generate_publisher_keypair()
    raw_manifest = _signed_manifest(version, notes, private_hex)
    from core.updater.feed import FeedConfig
    from core.updater.runtime import UpdateSubsystem
    from core.updater.trust import TrustedPublishers

    return UpdateSubsystem(
        feed=FeedConfig(manifest_url="https://updates.example.invalid/m.json", source="env"),
        trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
        data_dir=tmp_path,
        installed_version=VOOL_VERSION,
        platform="macos-arm64",
        fetch=lambda url: raw_manifest,
    )


def test_update_check_up_to_date(tmp_path, capsys) -> None:
    with mock.patch("core.updater.runtime.get_update_subsystem", return_value=_booted_subsystem(tmp_path, VOOL_VERSION, "same version")):
        assert cmd_update() == 0
    assert "up to date" in capsys.readouterr().out.lower()


def test_update_check_available_does_not_apply(tmp_path, capsys) -> None:
    subsystem = _booted_subsystem(tmp_path, _bump(VOOL_VERSION), "- Fixed a thing\n- Added another")
    press = mock.Mock(return_value=None)
    with mock.patch("core.updater.runtime.get_update_subsystem", return_value=subsystem), mock.patch.object(
        subsystem, "press_install", press
    ):
        assert cmd_update() == 0
    out = capsys.readouterr().out
    assert "Update available" in out
    assert "Fixed a thing" in out
    press.assert_not_called()  # check-only must never launch the updater


def test_update_apply_starts_the_install(tmp_path, capsys) -> None:
    subsystem = _booted_subsystem(tmp_path, _bump(VOOL_VERSION), "- Fixed a thing")
    from core.updater.runtime import PressOutcome

    press = mock.Mock(return_value=PressOutcome(True, detail="download started"))
    with mock.patch("core.updater.runtime.get_update_subsystem", return_value=subsystem), mock.patch.object(
        subsystem, "press_install", press
    ):
        assert cmd_update(apply=True) == 0
    press.assert_called_once()
    assert press.call_args.args[0].origin == "cli"  # the explicit typed gesture
    assert "update" in capsys.readouterr().out.lower()


def test_update_apply_without_target_refuses(tmp_path, capsys) -> None:
    subsystem = _booted_subsystem(tmp_path, _bump(VOOL_VERSION), "- Fixed a thing")
    # offer exists but no app target configured: the press must refuse, not guess a path
    subsystem.feed = type(subsystem.feed)(
        manifest_url=subsystem.feed.manifest_url, app_path="", channel="stable"
    )
    with mock.patch("core.updater.runtime.get_update_subsystem", return_value=subsystem):
        assert cmd_update(apply=True) == 1
    out = capsys.readouterr().out.lower()
    assert "no app path is configured" in out


def test_update_unconfigured_returns_one(tmp_path, capsys) -> None:
    """The honest-disable posture: no pinned key ⇒ exit 1 with the plain reason."""
    from core.updater.feed import FeedConfig
    from core.updater.runtime import UpdateSubsystem
    from core.updater.trust import TrustedPublishers

    subsystem = UpdateSubsystem(
        feed=FeedConfig(manifest_url="https://updates.example.invalid/m.json", source="env"),
        trust=TrustedPublishers(pinned_keys={}),
        data_dir=tmp_path,
    )
    with mock.patch("core.updater.runtime.get_update_subsystem", return_value=subsystem):
        assert cmd_update() == 1
    assert "unavailable" in capsys.readouterr().out.lower()
