"""Boot + API + gesture tests: the production wiring, in-process.

Covers the amendment's wiring claims:
  * boot is HONEST when unconfigured (no key / no feed) — status says unavailable,
    no background thread pretends to work;
  * boot is BOUNDED and non-blocking when configured (a slow feed cannot stall it);
  * the API endpoints sit behind the owner-local gate like every privileged route;
  * the REAL install/restart presses go through dispatch_post, create the typed
    UserGesture server-side, and run the full handoff journey (external helper script,
    out-of-process swap, health, finalize) against a sandbox app clone;
  * a destructive turn registered through the live coordinator blocks the restart
    press with the plain-language refusal (409), never a silent restart.
"""
from __future__ import annotations

import json
import subprocess
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.updater.feed import FeedConfig
from core.updater.runtime import (
    boot_update_subsystem,
    get_update_subsystem,
    reset_update_subsystem_for_tests,
)
from core.updater.status import UpdatePhase
from core.updater.trust import TrustedPublishers
from core.web.api.app import default_workspace_root as _default_workspace_root

from .helpers import generate_publisher_keypair, sha256_hex, sign_bytes
from .test_e2e_sandbox import SandboxServer, _make_app, _spawn_app

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "installer" / "update" / "mac_update_helper.sh"


@pytest.fixture(autouse=True)
def _reset_subsystem():
    yield
    reset_update_subsystem_for_tests()


class TestHonestDisable:
    def test_no_trusted_key_means_unavailable_not_silent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VOOL_UPDATE_PUBLISHER_KEYS", raising=False)
        monkeypatch.delenv("VOOL_UPDATE_TRUSTED_KEYS_JSON", raising=False)
        subsystem = boot_update_subsystem(data_dir=tmp_path, trust=TrustedPublishers(pinned_keys={}))
        assert not subsystem.configured
        payload = subsystem.status_payload()
        assert payload["phase"] == UpdatePhase.UNAVAILABLE.value
        assert "publisher key" in payload["unavailable_plain"]
        assert "unavailable" in payload["message"].lower()
        assert subsystem.service is None  # no background thread at all

    def test_no_feed_configured_is_unavailable(self, tmp_path):
        _private_hex, public_hex = generate_publisher_keypair()
        subsystem = boot_update_subsystem(
            data_dir=tmp_path,
            trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
            feed=FeedConfig(manifest_url=""),  # key pinned, feed absent
        )
        assert not subsystem.configured
        assert subsystem.status_payload()["unavailable_plain"].startswith("no release feed")

    def test_disabled_by_env_is_unavailable(self, tmp_path):
        _private_hex, public_hex = generate_publisher_keypair()
        subsystem = boot_update_subsystem(
            data_dir=tmp_path,
            trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
            feed=FeedConfig(manifest_url="https://x/y.json", source="disabled-by-env"),
        )
        assert not subsystem.configured

    def test_boot_is_idempotent_and_never_raises(self, tmp_path):
        first = boot_update_subsystem(data_dir=tmp_path)
        second = boot_update_subsystem(data_dir=tmp_path)
        assert first is second


class TestBoundedBoot:
    def test_start_returns_quickly_even_with_a_slow_feed(self, tmp_path):
        _private_hex, public_hex = generate_publisher_keypair()

        def slow_fetch(url):
            time.sleep(30)
            return b"{}"

        subsystem = boot_update_subsystem(
            data_dir=tmp_path,
            trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
            feed=FeedConfig(manifest_url="https://updates.example.invalid/m.json"),
            fetch=slow_fetch,
        )
        began = time.monotonic()
        subsystem.start()
        assert time.monotonic() - began < 2.0  # the check thread never blocks boot

    def test_configured_boot_produces_ready_status(self, tmp_path):
        private_hex, public_hex = generate_publisher_keypair()
        blob = b"SANDBOX-ARTIFACT"
        manifest = {
            "schema": "vool.update.manifest.v1",
            "product": "vool",
            "channel": "stable",
            "version": "0.6.0",
            "sequence": 47,
            "published_at": "2026-09-01T12:00:00Z",
            "minimum_compatible": "0.4.0",
            "notes": "boot test release",
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
        raw = json.dumps(manifest).encode()
        subsystem = boot_update_subsystem(
            data_dir=tmp_path,
            trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
            feed=FeedConfig(manifest_url="https://updates.example.invalid/m.json"),
            installed_version="0.5.0",
            platform="macos-arm64",
            fetch=lambda url: raw,
        )
        subsystem.start()
        payload = subsystem.trigger_check()
        assert payload["check_ok"], payload.get("detail")
        assert subsystem.status_payload()["phase"] == UpdatePhase.READY.value
        assert subsystem.status_payload()["notes"] == "boot test release"


class TestApiSurface:
    def _runtime(self):
        from core.web.api.runtime import RuntimeServices

        return RuntimeServices(display_name="N")

    def _get(self, path, client_host="127.0.0.1"):
        from core.web.api.service import dispatch_get

        return dispatch_get(
            path=path, query={}, runtime=self._runtime(), model_name="vool", client_host=client_host
        )

    def _post(self, path, body=None, client_host="127.0.0.1"):
        from core.web.api.service import dispatch_post

        return dispatch_post(
            path=path,
            body=body or {},
            headers={},
            runtime=self._runtime(),
            model_name="vool",
            client_host=client_host,
            workspace_root_provider=_default_workspace_root,
        )

    def test_status_unbooted_is_honest(self):
        assert get_update_subsystem() is None
        response = self._get("/api/update/status")
        assert response.status == 200
        payload = json.loads(response.body.decode())
        assert payload["configured"] is False and payload["booted"] is False
        assert "not running" in payload["message"].lower()

    def test_status_requires_owner_local(self, tmp_path):
        boot_update_subsystem(data_dir=tmp_path)
        response = self._get("/api/update/status", client_host="10.9.8.7")
        assert response.status == 403

    def test_check_requires_owner_local(self, tmp_path):
        boot_update_subsystem(data_dir=tmp_path)
        response = self._post("/api/update/check", client_host="10.9.8.7")
        assert response.status == 403

    def test_status_reports_unavailable_without_key(self, tmp_path):
        boot_update_subsystem(data_dir=tmp_path)
        response = self._get("/api/update/status")
        payload = json.loads(response.body.decode())
        assert payload["phase"] == "unavailable"
        assert "publisher key" in payload["unavailable_plain"]

    def test_install_without_target_or_offer_refuses_cleanly(self, tmp_path):
        _private_hex, public_hex = generate_publisher_keypair()
        subsystem = boot_update_subsystem(
            data_dir=tmp_path,
            trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
            feed=FeedConfig(manifest_url="https://updates.example.invalid/m.json"),
            installed_version="0.5.0",
            platform="macos-arm64",
            fetch=lambda url: b"{}",
        )
        subsystem.trigger_check()  # feed answers {} ⇒ nothing offered
        response = self._post("/api/update/install")
        assert response.status == 409
        payload = json.loads(response.body.decode())
        assert payload["accepted"] is False


class TestFullJourneyViaApi:
    """dispatch_post → typed gesture → download → READY_TO_RESTART → restart press →
    EXTERNAL helper script (real, out-of-process) → swap → health → finalize; and the
    same journey rolling back when the new version is unhealthy."""

    @pytest.fixture()
    def journey(self, tmp_path):
        private_hex, public_hex = generate_publisher_keypair()
        apps = tmp_path / "Apps"
        apps.mkdir()
        app_path = _make_app(apps, "VOOL.app", "old")
        heartbeat = tmp_path / "heartbeat.log"
        running = _spawn_app(app_path, heartbeat, "0.5.0")
        pidfile = tmp_path / "app.pid"
        pidfile.write_text(str(running.pid))

        server = SandboxServer(app_path=app_path, installed_version="0.5.0", target_version="0.6.0")
        server.start()

        new_bundle = _make_app(tmp_path / "zsrc", "VOOL.app", "new")
        artifact = tmp_path / "artifact.zip"
        with zipfile.ZipFile(artifact, "w") as archive:
            for file in sorted(new_bundle.rglob("*")):
                if file.is_file():
                    archive.write(file, f"VOOL.app/{file.relative_to(new_bundle)}")
        blob = artifact.read_bytes()

        manifest = {
            "schema": "vool.update.manifest.v1",
            "product": "vool",
            "channel": "stable",
            "version": "0.6.0",
            "sequence": 47,
            "published_at": "2026-09-01T12:00:00Z",
            "minimum_compatible": "0.4.0",
            "notes": "journey release notes line one\nline two",
            "artifacts": {
                "macos-arm64": {
                    "url": f"{server.url}/artifact.zip",
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
        raw = json.dumps(manifest).encode()
        server.manifest_bytes = raw
        server.artifact_blob = blob

        feed = FeedConfig(
            manifest_url=f"{server.url}/manifest.json",
            app_path=str(app_path),
            health_url=f"{server.url}/healthz",
            helper_script=str(HELPER),
            check_interval_seconds=3600,
        )
        import os

        old_pidfile_env = os.environ.get("VOOL_UPDATE_TARGET_PIDFILE")
        os.environ["VOOL_UPDATE_TARGET_PIDFILE"] = str(pidfile)
        subsystem = boot_update_subsystem(
            data_dir=tmp_path / "data",
            trust=TrustedPublishers(pinned_keys={"release-2026-01": public_hex}),
            feed=feed,
            installed_version="0.5.0",
            platform="macos-arm64",
            notarization_waiver_reason="pytest sandbox: no Developer ID; production default refuses un-notarized bundles",
            health_timeout=12.0,
        )
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        yield SimpleNamespace(
            subsystem=subsystem,
            server=server,
            app_path=app_path,
            heartbeat=heartbeat,
            marker=lambda: (app_path / "Contents" / "Resources" / "marker.txt").read_text().strip(),
            running=lambda: running,
            respawn=lambda: _spawn_app(app_path, heartbeat, "0.6.0" if (app_path / "Contents" / "Resources" / "marker.txt").read_text().strip() == "new" else "0.5.0"),
            restore_env=old_pidfile_env,
        )
        from .test_e2e_sandbox import sweep_sandbox_apps

        server.shutdown_server()
        sweep_sandbox_apps(tmp_path)  # helper-relaunched apps die with the test
        if running.poll() is None:
            running.terminate()
            try:
                running.wait(timeout=5)
            except subprocess.TimeoutExpired:
                running.kill()
        if old_pidfile_env is None:
            os.environ.pop("VOOL_UPDATE_TARGET_PIDFILE", None)
        else:
            os.environ["VOOL_UPDATE_TARGET_PIDFILE"] = old_pidfile_env

    def _wait_phase(self, subsystem, phases, timeout=60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            phase = subsystem.status_payload()["phase"]
            if phase in phases:
                return phase
            time.sleep(0.2)
        return subsystem.status_payload()["phase"]

    def test_two_press_journey_finalizes_via_external_helper(self, journey):
        from core.web.api.runtime import RuntimeServices
        from core.web.api.service import dispatch_post

        runtime = RuntimeServices(display_name="N")

        def post(path):
            return dispatch_post(
                path=path,
                body={},
                headers={},
                runtime=runtime,
                model_name="vool",
                client_host="127.0.0.1",
                workspace_root_provider=_default_workspace_root,
            )

        journey.subsystem.trigger_check()
        assert journey.subsystem.status_payload()["phase"] == "ready"

        install = post("/api/update/install")
        assert install.status == 200, install.body
        phase = self._wait_phase(journey.subsystem, {"ready_to_restart"})
        assert phase == "ready_to_restart"
        payload = journey.subsystem.status_payload()
        assert payload["target_version"] == "0.6.0"
        assert "journey release notes" in payload["notes"]

        restart = post("/api/update/restart")
        assert restart.status == 200, restart.body
        final = self._wait_phase(journey.subsystem, {"done", "rolled_back", "failed"})
        assert final == "done", journey.subsystem.status_payload()["message"]
        assert journey.marker() == "new"
        # the external helper ran OUTSIDE this process and relaunched the sandbox app
        result_file = journey.subsystem.paths.root / "helper-result.json"
        assert result_file.exists()
        result = json.loads(result_file.read_text(encoding="utf-8"))
        assert result["ok"] is True

    def test_restart_press_refuses_while_destructive_work_runs(self, journey):
        journey.subsystem.trigger_check()
        install = self._post_press("/api/update/install")
        assert install.status == 200
        self._wait_phase(journey.subsystem, {"ready_to_restart"})

        handle = journey.subsystem.register_turn("big-cleanup", "deleting old files", destructive=True)
        restart = self._post_press("/api/update/restart")
        assert restart.status == 409
        payload = json.loads(restart.body.decode())
        assert "deleting old files" in payload["detail"]
        assert journey.marker() == "old"  # nothing restarted over the work
        journey.subsystem.complete_turn(handle.id)

        restart = self._post_press("/api/update/restart")
        assert restart.status == 200
        final = self._wait_phase(journey.subsystem, {"done", "rolled_back", "failed"})
        assert final == "done", journey.subsystem.status_payload()["message"]
        assert journey.marker() == "new"

    def test_unhealthy_new_version_rolls_back_after_restart_press(self, journey):
        journey.server.health_sick = True
        journey.subsystem.trigger_check()
        assert self._post_press("/api/update/install").status == 200
        self._wait_phase(journey.subsystem, {"ready_to_restart"})
        assert self._post_press("/api/update/restart").status == 200
        final = self._wait_phase(journey.subsystem, {"done", "rolled_back", "failed"})
        assert final == "rolled_back", journey.subsystem.status_payload()["message"]
        assert journey.marker() == "old"
        message = journey.subsystem.status_payload()["message"]
        assert "put back" in message

    def _post_press(self, path):
        from core.web.api.runtime import RuntimeServices
        from core.web.api.service import dispatch_post

        return dispatch_post(
            path=path,
            body={},
            headers={},
            runtime=RuntimeServices(display_name="N"),
            model_name="vool",
            client_host="127.0.0.1",
            workspace_root_provider=_default_workspace_root,
        )
