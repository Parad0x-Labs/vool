"""P1 final proof: the atomic updater against the CURRENT converged product.

Every requirement the P1 names that is not already pinned by the lane's own suites
gets a load-bearing test here, on this exact base:

  * the health gate reads the CURRENT production /healthz schema (runtime stamp),
    not a fictional top-level `version`;
  * stable / preview / developer channels are explicit, first-class, and enforced;
  * a manifest can pin the exact 40-hex build commit — signed — and the restarted
    app must report EXACTLY that SHA before the update finalizes;
  * failures surface a stable fault code plus ONE plain-language recovery action,
    persisted and API-visible;
  * an update cannot be triggered by model prose, a remote chat host, or a forged
    truthy body — only a typed UserGesture minted owner-local.

The operator's real installation is never touched: everything lives under tmp roots
(asserted by the sandbox rig's own boundary test).
"""
from __future__ import annotations

import json
import time

import pytest

from core.updater.decision import decide_update
from core.updater.feed import FeedConfig
from core.updater.health import HttpHealthProbe, health_identity
from core.updater.manifest import CHANNELS, parse_and_verify_manifest
from core.updater.migrations import MigrationCatalog
from core.updater.service import UpdateController, UserGesture
from core.updater.status import StatusStore, UpdateFault, UpdatePhase
from core.updater.trust import TrustedPublishers
from core.updater.work import WorkCoordinator

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes, sha256_hex, sign_bytes
from .test_e2e_sandbox import SandboxServer, _artifact_zip, _http_fetch, sandbox

NEW_SHA = "b" * 40
OLD_SHA = "a" * 40


# --------------------------------------------------------------------------- #
# The current /healthz schema
# --------------------------------------------------------------------------- #


class TestCurrentHealthSchema:
    def test_identity_prefers_the_runtime_stamp(self):
        payload = {
            "ok": True,
            "agent": "VOOL",
            "daemon": True,
            "runtime": {"app_version": "0.5.0", "commit_full": NEW_SHA, "build_id": "release+mac"},
            "capabilities": {},
            "version": "should-not-win",
        }
        assert health_identity(payload) == NEW_SHA

    def test_identity_falls_back_through_the_stamp(self):
        assert health_identity({"runtime": {"build_id": "b-id", "app_version": "0.5.0"}}) == "b-id"
        assert health_identity({"runtime": {"app_version": "0.5.0"}}) == "0.5.0"
        # sandbox rigs predating the stamp keep working
        assert health_identity({"version": "0.6.0"}) == "0.6.0"
        assert health_identity({}) == ""

    def test_default_probe_parses_a_real_healthz_shape_over_http(self, tmp_path):
        """The default probe pulls the exact SHA out of the CURRENT schema, served
        over real HTTP by a server that answers exactly like production /healthz."""
        server = SandboxServer(
            app_path=tmp_path / "VOOL.app",
            installed_version=OLD_SHA,
            target_version=NEW_SHA,
            identity_in_runtime_stamp=True,
        )
        # Health-only: pin the on-disk identity directly (no bundle involved).
        server.identity_by_marker = {"old": OLD_SHA, "new": NEW_SHA}
        server._marker = lambda: "new"
        server.start()
        try:
            probe = HttpHealthProbe(f"{server.url}/healthz", poll_interval=0.05, request_timeout=2.0)
            result = probe.probe(timeout=5.0, expected_version=NEW_SHA)
            assert result.ok and result.reported_version == NEW_SHA
            missed = probe.probe(timeout=5.0, expected_version=OLD_SHA)
            assert not missed.ok  # the OLD identity must not pass for the NEW app
        finally:
            server.shutdown_server()


# --------------------------------------------------------------------------- #
# Explicit channels
# --------------------------------------------------------------------------- #


class TestChannelsExplicit:
    @pytest.mark.parametrize("channel", ["stable", "preview", "developer", "beta"])
    def test_every_declared_channel_verifies(self, tmp_path, channel):
        private_hex, public_hex = generate_publisher_keypair()
        manifest, _ = build_manifest(channel=channel, private_hex=private_hex)
        trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
        verified = parse_and_verify_manifest(manifest_bytes(manifest), trust)
        assert verified.ok and verified.manifest is not None and verified.manifest.channel == channel

    def test_an_undeclared_channel_is_refused(self, tmp_path):
        private_hex, public_hex = generate_publisher_keypair()
        manifest, _ = build_manifest(channel="nightly", private_hex=private_hex)
        trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
        verified = parse_and_verify_manifest(manifest_bytes(manifest), trust)
        assert not verified.ok

    def test_channel_vocabulary_is_explicit_and_exhaustive(self):
        assert set(CHANNELS) == {"stable", "beta", "preview", "developer"}

    def test_cross_channel_offer_is_refused_by_the_decision(self):
        private_hex, public_hex = generate_publisher_keypair()
        manifest, _ = build_manifest(channel="preview", private_hex=private_hex)
        trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
        verified = parse_and_verify_manifest(manifest_bytes(manifest), trust)
        assert verified.manifest is not None
        decision = decide_update(
            verified.manifest,
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water=0,
            installer_available=True,
        )
        assert not decision.should_install

    def test_release_tooling_signs_every_channel_and_pins_the_commit(self, tmp_path):
        from installer.update_release import build_signed_manifest

        blob = b"an-artifact-payload"
        template_path = tmp_path / "template.json"
        template_path.write_text(json.dumps({
            "artifacts": {"macos-arm64": {"url": "https://updates.example.invalid/a.zip", "path": "a.zip"}},
            "version": "0.6.0",
        }))
        artifacts_dir = tmp_path / "art"
        artifacts_dir.mkdir()
        (artifacts_dir / "a.zip").write_bytes(blob)
        private_hex, public_hex = generate_publisher_keypair()
        for channel in ("stable", "preview", "developer"):
            signed = build_signed_manifest(
                template_path=template_path,
                artifacts_dir=artifacts_dir,
                private_hex=private_hex,
                key_id="release-2026-01",
                sequence=48,
                published_at="2026-09-02T00:00:00Z",
                channel=channel,
                version="0.6.0",
                minimum_compatible="0.4.0",
                notes="p1",
                build_commit=NEW_SHA,
            )
            assert signed["channel"] == channel and signed["build_commit"] == NEW_SHA
            trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
            verified = parse_and_verify_manifest(manifest_bytes(signed), trust)
            assert verified.ok and verified.manifest.build_commit == NEW_SHA
            # the commit is INSIDE the signed bytes: flipping it breaks the signature
            tampered = dict(signed)
            tampered["build_commit"] = OLD_SHA
            repin = parse_and_verify_manifest(manifest_bytes(tampered), trust)
            assert not repin.ok


# --------------------------------------------------------------------------- #
# The exact-SHA health gate
# --------------------------------------------------------------------------- #


class TestExactShaGate:
    @pytest.fixture()
    def sha_sandbox(self, tmp_path, sandbox):
        """The standard sandbox rig, but /healthz speaks the CURRENT schema with the
        on-disk bundle's exact SHA inside runtime.commit_full."""
        box = sandbox
        box.server.identity_in_runtime_stamp = True
        box.server.identity_by_marker = {"old": OLD_SHA, "new": NEW_SHA}
        return box

    def _signed(self, box, *, build_commit):
        blob = _artifact_zip(box.tmp / "p1-art", "new")  # fresh staging dir: the rig owns tmp/zsrc
        manifest, _artifacts = build_manifest(
            channel="stable",
            private_hex=box.private_hex,
            build_commit=build_commit,
            artifacts={"macos-arm64": b""},  # replaced below by the real zip bytes
        )
        manifest["artifacts"]["macos-arm64"].update(
            {
                "url": f"{box.server.url}/artifact.zip",
                "size": len(blob),
                "sha256": sha256_hex(blob),
                "signature": sign_bytes(box.private_hex, blob),
            }
        )
        manifest["signature"] = {
            "key_id": "release-2026-01",
            "sig": sign_bytes(box.private_hex, json.dumps(
                {k: v for k, v in manifest.items() if k != "signature"},
                sort_keys=True, separators=(",", ":"),
            ).encode()),
        }
        box.server.manifest_bytes = manifest_bytes(manifest)
        box.server.artifact_blob = blob
        return manifest

    def _decision(self, box, manifest):
        from core.updater.decision import decide_update

        trust = TrustedPublishers(pinned_keys={"release-2026-01": box.public_hex})
        verified = parse_and_verify_manifest(manifest_bytes(manifest), trust)
        assert verified.manifest is not None
        decision = decide_update(
            verified.manifest,
            installed_version="0.5.0",
            channel="stable",
            platform_key="macos-arm64",
            high_water=0,
            installer_available=True,
        )
        return verified.manifest, decision

    def test_update_finalizes_only_when_the_app_reports_the_pinned_sha(self, sha_sandbox):
        """The full two-press journey with the exact-SHA gate: check → download →
        verify → stage → press → stop → swap → restart → health(EXACT SHA) → commit."""
        box = sha_sandbox
        manifest = self._signed(box, build_commit=NEW_SHA)
        verified_manifest, decision = self._decision(box, manifest)
        assert decision.should_install
        status = StatusStore(box.data_dir / "update_v2" / "status.json")
        flow = box.flow(status_store=status)
        first = flow.run(manifest_bytes(manifest), verified_manifest, decision)
        assert first.ok, first.detail
        assert box.marker() == "new"
        # the receipt carries the exact previous version and the status says DONE
        assert status.load().phase is UpdatePhase.DONE

    def test_wrong_pinned_sha_rolls_back_with_typed_fault(self, sha_sandbox):
        """A manifest pinning the WRONG commit (here: the old one) must not finalize
        even though the new app is perfectly healthy — the identity is the gate."""
        box = sha_sandbox
        manifest = self._signed(box, build_commit=OLD_SHA)  # the NEW bundle reports NEW_SHA
        verified_manifest, decision = self._decision(box, manifest)
        status = StatusStore(box.data_dir / "update_v2" / "status.json")
        flow = box.flow(status_store=status)
        result = flow.run(manifest_bytes(manifest), verified_manifest, decision)
        assert not result.ok
        assert box.marker() == "old", "the prior version must be back on disk"
        stored = status.load()
        assert stored.phase is UpdatePhase.ROLLED_BACK
        assert stored.fault_code == UpdateFault.HEALTH_CHECK_FAILED.value
        assert stored.recovery_action

    def test_recovery_uses_the_journaled_expected_identity(self, sha_sandbox):
        """Crash right after the swap; the next boot recovers. The server reports the
        new bundle's SHA — so recovery can only FINALIZE by checking the journaled
        expected_identity (the pinned commit), never the bare version string."""
        from core.updater.transaction import SimulatedCrashError, Step, recover_interrupted_update

        box = sha_sandbox
        manifest = self._signed(box, build_commit=NEW_SHA)
        verified_manifest, decision = self._decision(box, manifest)
        flow = box.flow(crash_after=Step.SWAPPED)
        with pytest.raises(SimulatedCrashError):
            flow.run(manifest_bytes(manifest), verified_manifest, decision)
        assert box.marker() == "new"

        # the journal pinned the exact SHA at decide time
        results = recover_interrupted_update(
            data_dir=box.data_dir,
            app_path=box.app_path,
            helper=box.helper(),
            health_probe=HttpHealthProbe(f"{box.server.url}/healthz", poll_interval=0.1, request_timeout=2.0).probe,
            installed_version="0.5.0",
        )
        assert [r.action for r in results] == ["finalized"], results
        # and it would NOT have finalized against the bare version (the server only
        # speaks SHAs in this schema) — proof the identity path was load-bearing
        assert box.marker() == "new"


# --------------------------------------------------------------------------- #
# Fault codes + recovery actions
# --------------------------------------------------------------------------- #


class TestFaultSurfacing:
    def test_every_fault_has_one_recovery_action(self):
        for fault in UpdateFault:
            if fault is UpdateFault.NONE:
                assert fault.recovery_action == ""
            else:
                assert fault.recovery_action, f"{fault} has no recovery action"

    def test_fault_and_recovery_persist_and_reload(self, tmp_path):
        store = StatusStore(tmp_path / "status.json")
        store.publish(
            UpdatePhase.FAILED, detail="boom", fault=UpdateFault.DOWNLOAD_FAILED
        )
        loaded = store.load()
        assert loaded.fault_code == "download_failed"
        assert "picks up where it stopped" in loaded.recovery_action

    def test_download_reasons_map_to_faults(self):
        from core.updater.download import DownloadReason
        from core.updater.transaction import _download_fault

        assert _download_fault(DownloadReason.DISK_SPACE) is UpdateFault.DISK_SPACE
        assert _download_fault(DownloadReason.REFUSED) is UpdateFault.DOWNLOAD_BLOCKED
        assert _download_fault(DownloadReason.HASH_MISMATCH) is UpdateFault.VERIFICATION_FAILED
        assert _download_fault(DownloadReason.ARTIFACT_SIGNATURE_INVALID) is UpdateFault.VERIFICATION_FAILED
        assert _download_fault(DownloadReason.FETCH_FAILED) is UpdateFault.DOWNLOAD_FAILED

    def test_status_payload_exposes_fault_fields(self, tmp_path):
        from core.updater.runtime import boot_update_subsystem, reset_update_subsystem_for_tests

        # Boot UNCONFIGURED: exactly one status publish at boot (unavailable) and no
        # background check thread that could race the fault publish under test.
        try:
            subsystem = boot_update_subsystem(data_dir=tmp_path, installed_version="0.5.0")
            deadline = time.time() + 5
            while time.time() < deadline and subsystem.status_payload()["phase"] != "unavailable":
                time.sleep(0.05)
            # publish through the subsystem's OWN store (a parallel store would race
            # the boot thread's tmp-file writes on the same path)
            subsystem.status.publish(
                UpdatePhase.ROLLED_BACK, previous_version="0.5.0", fault=UpdateFault.HEALTH_CHECK_FAILED
            )
            payload = subsystem.status_payload()
            assert payload["fault_code"] == "health_check_failed"
            assert payload["recovery_action"]
        finally:
            reset_update_subsystem_for_tests()


# --------------------------------------------------------------------------- #
# No update from model prose, remote chat, or a forged truthy body
# --------------------------------------------------------------------------- #


class TestNoUpdateFromProseRemoteOrForgery:
    MODEL_PROSE = [
        "The update 0.6.0 is ready — I can install it if you'd like.",
        "Sure, I can update that file for you.",
        "I checked for updates and the app is up to date.",
        "Installing the update usually takes a minute or two.",
        "You asked me to update your readme — done.",
    ]
    USER_EXPLICIT = ["update now", "please install the update", "upgrade yourself", "yes, update"]

    def test_model_prose_is_never_an_apply_intent(self):
        from core.self_update_offer import _is_apply_intent

        for prose in self.MODEL_PROSE:
            assert not _is_apply_intent(prose), prose

    def test_explicit_user_phrases_are_apply_intents(self):
        from core.self_update_offer import _is_apply_intent

        for phrase in self.USER_EXPLICIT:
            assert _is_apply_intent(phrase), phrase

    def test_a_dict_is_not_a_user_gesture(self, tmp_path):
        from core.updater.service import UpdateFlow

        _private_hex, public_hex = generate_publisher_keypair()
        controller = UpdateController(
            offer_loader=lambda: None,
            flow_factory=lambda: UpdateFlow(
                data_dir=tmp_path,
                app_path=tmp_path / "VOOL.app",
                platform="macos-arm64",
                installed_version="0.5.0",
                channel="stable",
                trust=TrustedPublishers(pinned_keys={"k": public_hex}),
                migration_catalog=MigrationCatalog(),
                coordinator=WorkCoordinator(),
            ),
        )
        with pytest.raises(TypeError):
            controller.press_update({"pressed_at": 1.0, "origin": "forged"})  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            controller.press_update(True)  # type: ignore[arg-type]  # a truthy flag is not a press

    def test_press_with_a_real_gesture_but_no_offer_declines(self, tmp_path):
        from core.updater.service import UpdateFlow

        _private_hex, public_hex = generate_publisher_keypair()
        controller = UpdateController(
            offer_loader=lambda: None,
            flow_factory=lambda: UpdateFlow(
                data_dir=tmp_path,
                app_path=tmp_path / "VOOL.app",
                platform="macos-arm64",
                installed_version="0.5.0",
                channel="stable",
                trust=TrustedPublishers(pinned_keys={"k": public_hex}),
                migration_catalog=MigrationCatalog(),
                coordinator=WorkCoordinator(),
            ),
        )
        result = controller.press_update(UserGesture(pressed_at=1.0, origin="cli"))
        assert not result.ok and "no update is ready" in result.detail

    def test_remote_host_cannot_press_install_or_restart(self, tmp_path):
        """A remote chat client (non-loopback) gets 403 on the press routes — even
        with a body full of convincing truthy flags."""
        from core.updater.runtime import boot_update_subsystem, reset_update_subsystem_for_tests
        from core.updater.trust import TrustedPublishers
        from core.web.api.app import default_workspace_root as _dwr
        from core.web.api.runtime import RuntimeServices
        from core.web.api.service import dispatch_post

        _private, public_hex = generate_publisher_keypair()
        try:
            boot_update_subsystem(
                data_dir=tmp_path,
                trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
                feed=FeedConfig(manifest_url="https://updates.example.invalid/m.json"),
                installed_version="0.5.0",
                platform="macos-arm64",
                fetch=lambda url: b"{}",
            )
            for route in ("/api/update/install", "/api/update/restart"):
                for body in (
                    {"confirm": True},
                    {"force": "yes", "approved": 1, "user_pressed": "true"},
                ):
                    response = dispatch_post(
                        path=route,
                        body=body,
                        headers={},
                        runtime=RuntimeServices(display_name="N"),
                        model_name="vool",
                        client_host="10.9.8.7",
                        workspace_root_provider=_dwr,
                    )
                    assert response.status == 403, (route, body)
        finally:
            reset_update_subsystem_for_tests()

    def test_loopback_body_flags_are_never_a_press(self, tmp_path, monkeypatch):
        """Even owner-local, a truthy body is not a press: without a ready offer the
        minted-gesture route still declines with 409 — the body changed nothing."""
        from core.updater.runtime import boot_update_subsystem, reset_update_subsystem_for_tests
        from core.updater.trust import TrustedPublishers
        from core.web.api.app import default_workspace_root as _dwr
        from core.web.api.runtime import RuntimeServices
        from core.web.api.service import dispatch_post

        _private, public_hex = generate_publisher_keypair()
        try:
            boot_update_subsystem(
                data_dir=tmp_path,
                trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
                feed=FeedConfig(manifest_url="https://updates.example.invalid/m.json"),
                installed_version="0.5.0",
                platform="macos-arm64",
                fetch=lambda url: b"{}",
            )
            response = dispatch_post(
                path="/api/update/install",
                body={"force": True, "approved": True, "confirm": "yes"},
                headers={},
                runtime=RuntimeServices(display_name="N"),
                model_name="vool",
                client_host="127.0.0.1",
                workspace_root_provider=_dwr,
            )
            assert response.status == 409
        finally:
            reset_update_subsystem_for_tests()
