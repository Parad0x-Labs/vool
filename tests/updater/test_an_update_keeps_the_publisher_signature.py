"""An update from a build signed with VOOL's publisher certificate keeps working, and keeps the Mac's permissions.

Owner's iMac, macOS 26.5, 2026-11-27: every update lost Calendar, Contacts, Files and the
microphone, because each build was signed ad hoc and macOS ties a permission to the signature.
From 0.8 the Mac builds are signed with one publisher certificate, the same for every build.
The updater running inside such a build must still accept the next build: it keeps the
publisher-verified tier only for a replacement that meets the running app's own designated
requirement, which is the check macOS itself uses to keep a permission.
"""
from __future__ import annotations

import pytest

from core.updater.macos import MacOSBundleInstaller
from tests.updater.test_transaction_flow import Completed, harness

_REQUIREMENT = 'identifier "ai.nulla.desktop" and certificate leaf = H"8f2c41aa0b"'


def _runner(*, same_publisher: bool, commands: list, signed: str = "publisher", requirement: str = _REQUIREMENT):
    def run(command):
        commands.append(command)
        if command[0] == "codesign" and "-dv" in command:
            return Completed(0, err={
                "publisher": "Identifier=ai.nulla.desktop\nAuthority=VOOL Publisher (Parad0x Labs LLC)\n",
                "developer_id": "Authority=Developer ID Application: Publisher\nAuthority=Developer ID Certification Authority\n"
                                "Authority=Apple Root CA\n",
                "adhoc": "Signature=adhoc\n",
            }[signed])
        if command[:3] == ["codesign", "-d", "-r-"]:
            return Completed(0, out="Executable=/Applications/VOOL.app/Contents/MacOS/VOOL\ndesignated => " + requirement + "\n")
        if command[0] == "codesign" and "-R" in command:
            assert command[command.index("-R") + 1] == "=" + requirement
            return Completed(0 if same_publisher else 3, err="" if same_publisher else "test-requirement: code failed to satisfy")
        if command[0] == "spctl":
            return Completed(1, err="not notarized")
        return Completed(0)

    return run


@pytest.mark.parametrize("same_publisher", [True, False])
def test_a_publisher_signed_app_updates_only_to_the_same_publisher(harness, same_publisher):
    import io
    import json
    import zipfile

    from core.updater.decision import decide_update
    from core.updater.manifest import parse_and_verify_manifest
    from core.updater.transaction import Step, UpdateJournal
    from core.updater.trust import TrustedPublishers
    from tests.updater.helpers import build_manifest, manifest_bytes

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("VOOL.app/Contents/marker.txt", "new")
        zipped.writestr("VOOL.app/Contents/Resources/BUILD_MANIFEST.json", json.dumps({"arch": "arm64", "version": "0.6.0-beta"}))
    blob = archive.getvalue()
    document, _ = build_manifest(private_hex=harness._keys[0], artifacts={"macos-arm64": blob})
    raw = manifest_bytes(document)
    manifest = parse_and_verify_manifest(raw, TrustedPublishers({"release-2026-01": harness._keys[1]})).manifest
    decision = decide_update(manifest, installed_version="0.5.0", channel="stable",
                             platform_key="macos-arm64", high_water={}, installer_available=True)
    harness.server.blob = blob
    commands: list = []
    harness.installer._run = _runner(same_publisher=same_publisher, commands=commands)
    result = harness.make_flow(allow_publisher_verified_adhoc=True, notarization_waiver_reason="").run(raw, manifest, decision)

    assert not any(command[0] == "spctl" for command in commands), "a self-signed publisher is never sent to Gatekeeper"
    if same_publisher:
        assert result.ok and harness.marker() == "new", result
        journal = UpdateJournal(harness.data_dir / "update_v2/transactions" / result.txid)
        assert journal.payload_of(Step.PREFLIGHT_OK)["trust_policy"] == "publisher-verified-adhoc"
        assert any(command[:3] == ["xattr", "-dr", "com.apple.quarantine"] for command in commands)
    else:
        assert not result.ok and result.terminal is Step.ABORTED
        assert harness.marker() == "old"
        assert not any(command[0] == "xattr" for command in commands)


@pytest.mark.parametrize(("signed", "requirement", "needs_gatekeeper"), [
    ("adhoc", _REQUIREMENT, False),
    ("publisher", _REQUIREMENT, False),
    ("publisher", 'cdhash H"a1b2"', True),
    ("developer_id", _REQUIREMENT, True),
])
def test_which_running_apps_keep_the_publisher_tier(signed, requirement, needs_gatekeeper, tmp_path) -> None:
    installer = MacOSBundleInstaller(run=_runner(same_publisher=True, commands=[], signed=signed, requirement=requirement))
    assert installer.running_requires_notarization(tmp_path) is needs_gatekeeper


def test_a_stale_publisher_check_never_skips_a_gatekeeper_check_that_was_asked_for(tmp_path) -> None:
    commands: list = []
    installer = MacOSBundleInstaller(run=_runner(same_publisher=True, commands=commands))
    assert installer.running_requires_notarization(tmp_path) is False
    (tmp_path / "New.app").mkdir()
    verdict = installer.verify_bundle(tmp_path / "New.app", require_notarization=True)
    assert not verdict.ok and any(command[0] == "spctl" for command in commands)
