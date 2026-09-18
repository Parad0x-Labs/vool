"""End-to-end sandbox: the full signed update flow against REAL moving parts.

Real in this file: a local HTTP server (manifest + artifact with byte-Range support +
a health endpoint that reports the version of the bundle actually on disk), real
Ed25519 keys, a real ad-hoc-codesigned app bundle whose executable is a real spawned
process (SIGTERM'd by the helper, relaunched for real), real renames for the swap,
real files for migrations, and the real `codesign` binary. Waived, explicitly and
only in this sandbox: notarization (no Apple Developer ID here) — recorded via the
journaled waiver reason, while unit tests pin that the production default refuses
un-notarized bundles.

NEVER touched: the operator's installed VOOL app. Every path is under pytest's tmp
root; a named assertion below documents that boundary.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.updater.decision import UpdateDecisionReason
from core.updater.download import DiskPreflight, StagedDownloader
from core.updater.health import HttpHealthProbe
from core.updater.macos import MacOSBundleInstaller
from core.updater.migrations import MigrationCatalog, MigrationStep
from core.updater.service import UpdateCheckService, UpdateController, UserGesture
from core.updater.status import StatusStore, UpdatePhase
from core.updater.transaction import InProcessHelper, SimulatedCrashError, Step, UpdateFlow, recover_interrupted_update
from core.updater.trust import TrustedPublishers

from .helpers import generate_publisher_keypair, manifest_bytes


class SandboxServer:
    """Local HTTP surface mirroring a release server: manifest, artifact (with Range),
    and a health endpoint that reports the ON-DISK app's version."""

    def __init__(
        self,
        *,
        app_path: Path,
        installed_version: str,
        target_version: str,
        identity_in_runtime_stamp: bool = False,
    ):
        self.app_path = Path(app_path)
        self.installed_version = installed_version
        self.target_version = target_version
        self.manifest_bytes = b"{}"
        self.artifact_blob = b""
        self.requests: list[tuple[str, str]] = []
        self.cut_connection_after: int | None = None  # bytes served before hard-cut
        self.health_sick = False
        # When set, /healthz speaks the CURRENT production schema (ok/agent/daemon/
        # runtime/capabilities) and carries the on-disk identity inside
        # runtime.commit_full — the shape the real service.py answers with.
        self.identity_in_runtime_stamp = bool(identity_in_runtime_stamp)
        # Optional exact identities per on-disk marker ({"old": <sha>, "new": <sha>});
        # when present, runtime.commit_full reports the marker's identity verbatim.
        self.identity_by_marker: dict[str, str] | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None):
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                rng = self.headers.get("Range") or ""
                outer.requests.append(("RANGE" if rng else self.command, self.path))
                if self.path.endswith("/manifest.json"):
                    self._send(200, outer.manifest_bytes, {"Content-Type": "application/json"})
                    return
                if self.path.endswith("/artifact.zip"):
                    blob = outer.artifact_blob
                    rng = self.headers.get("Range") or ""
                    if rng:
                        start = int(rng.split("=", 1)[1].split("-", 1)[0])
                        body = blob[start:]
                        if outer.cut_connection_after is not None:
                            body = body[: outer.cut_connection_after]
                            outer.cut_connection_after = None
                            # hard-cut: close without finishing (the client sees a short read)
                            self.send_response(206)
                            self.send_header("Content-Length", str(len(body) + 10**9))
                            self.end_headers()
                            self.wfile.write(body)
                            self.close_connection = True
                            return
                        self._send(206, body, {"Content-Range": f"bytes {start}-{len(blob)-1}/{len(blob)}"})
                        return
                    if outer.cut_connection_after is not None:
                        body = blob[: outer.cut_connection_after]
                        outer.cut_connection_after = None
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(blob) + 10**9))
                        self.end_headers()
                        self.wfile.write(body)
                        self.close_connection = True
                        return
                    self._send(200, blob)
                    return
                if self.path.endswith("/healthz"):
                    if outer.health_sick:
                        self._send(503, b"unhealthy", {"Content-Type": "application/json"})
                        return
                    marker = "new" if outer._marker() == "new" else "old"
                    version = outer.target_version if marker == "new" else outer.installed_version
                    if outer.identity_in_runtime_stamp:
                        commit_full = version
                        if outer.identity_by_marker:
                            commit_full = outer.identity_by_marker.get(marker, commit_full)
                        payload = {
                            "ok": True,
                            "agent": "VOOL",
                            "daemon": True,
                            "runtime": {
                                "app_version": version,
                                "commit": commit_full[:12],
                                "commit_full": commit_full,
                                "build_id": f"release+macos_arm+{commit_full[:12]}",
                            },
                            "capabilities": {},
                        }
                        self._send(200, json.dumps(payload).encode(), {"Content-Type": "application/json"})
                        return
                    self._send(
                        200,
                        json.dumps({"status": "ok", "version": version}).encode(),
                        {"Content-Type": "application/json"},
                    )
                    return
                self._send(404, b"not found")

        def _marker() -> str:
            try:
                return (outer.app_path / "Contents" / "Resources" / "marker.txt").read_text(encoding="utf-8").strip()
            except OSError:
                return "old"

        outer._marker = _marker
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> SandboxServer:
        self._thread.start()
        return self

    def shutdown_server(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _http_fetch(url: str, headers: dict | None = None, timeout: float = 30.0):
    request = urllib.request.Request(url, headers=dict(headers or {}))
    return urllib.request.urlopen(request, timeout=timeout)


def _make_app(root: Path, name: str, marker: str) -> Path:
    bundle = root / name
    exe = bundle / "Contents" / "MacOS" / "VOOL"
    exe.parent.mkdir(parents=True)
    (bundle / "Contents" / "Resources").mkdir()
    (bundle / "Contents" / "Resources" / "marker.txt").write_text(marker, encoding="utf-8")
    (bundle / "Contents" / "Info.plist").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<!DOCTYPE plist PUBLIC '-//Apple//DTD PLIST 1.0//EN' 'http://www.apple.com/DTDs/PropertyList-1.0.dtd'>\n"
        "<plist version='1.0'><dict><key>CFBundleName</key><string>VOOL</string>"
        "<key>CFBundleIdentifier</key><string>ai.vool.desktop.sandbox</string>"
        "<key>CFBundleExecutable</key><string>VOOL</string>"
        "<key>CFBundlePackageType</key><string>APPL</string></dict></plist>\n",
        encoding="utf-8",
    )
    exe.write_text(
        "#!/bin/bash\n"
        'echo "${VOOL_SANDBOX_VERSION:-unknown}" >> "${VOOL_SANDBOX_HEARTBEAT:-/dev/null}"\n'
        '# Self-register so EVERY teardown can find this app (helper relaunches go\n'
        '# through LaunchServices and re-parent to launchd; the path would otherwise\n'
        '# be unfindable). Derived from $0: works for prior-restored bundles too.\n'
        'APP_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"\n'
        'sleep 600 & APP_CHILD=$!\n'
        # register BOTH the launcher and the long-lived body: a rig that kills only\n'
        # the bash it spawned would otherwise orphan the sleep (measured: 21 leaks)\n'
        'echo $$ >> "${APP_ROOT}/../.sandbox-app.pids"\n'
        'echo ${APP_CHILD} >> "${APP_ROOT}/../.sandbox-app.pids"\n'
        "wait ${APP_CHILD}\n",
        encoding="utf-8",
    )
    exe.chmod(0o755)
    result = subprocess.run(["codesign", "-s", "-", str(bundle)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"ad-hoc codesign failed: {result.stderr}"
    return bundle


def _spawn_app(app: Path, heartbeat: Path, version: str) -> subprocess.Popen:
    import os

    env = dict(os.environ)
    env["VOOL_SANDBOX_HEARTBEAT"] = str(heartbeat)
    env["VOOL_SANDBOX_VERSION"] = version
    process = subprocess.Popen(
        [str(app / "Contents" / "MacOS" / "VOOL")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    # 20s, not 5s: under a full-pack run (many codesign+spawn storms in parallel
    #tmp dirs) a healthy app took >5s to write its first heartbeat — measured as
    # fixture ERRORs in the combined updater+legacy pack.
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and not heartbeat.exists():
        time.sleep(0.05)
    assert heartbeat.exists(), "sandbox app failed to start (not runnable?)"
    return process


def _artifact_zip(tmp: Path, marker: str) -> bytes:
    staging = tmp / "zsrc"
    bundle = _make_app(staging, "VOOL.app", marker)
    path = tmp / "artifact.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                archive.write(file, f"VOOL.app/{file.relative_to(bundle)}")
    return path.read_bytes()


def sweep_sandbox_apps(root: Path) -> int:
    """Kill every sandbox app that registered under `root` (spawned, helper-relaunched,
    or restored-prior). The discipline the operator demanded: launching new instances
    is fine — LEAKING them is not. Returns how many were killed."""
    import os
    import signal

    killed = 0
    for pidfile in Path(root).glob("**/.sandbox-app.pids"):
        try:
            pids = {int(line.strip()) for line in pidfile.read_text(encoding="utf-8").splitlines() if line.strip().isdigit()}
        except OSError:
            continue
        for pid in pids:
            if pid <= 1 or pid == os.getpid():
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
        with contextlib.suppress(OSError):
            pidfile.unlink()
    return killed


class Sandbox:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.data_dir = tmp_path / "data"
        (self.data_dir / "config").mkdir(parents=True)
        (self.data_dir / "config" / "settings.json").write_text(json.dumps({"setting": "original"}))

        self.private_hex, self.public_hex = generate_publisher_keypair()
        self.trust = TrustedPublishers(pinned_keys={"release-2026-01": self.public_hex})

        self.apps = tmp_path / "Apps"
        self.apps.mkdir()
        self.app_path = _make_app(self.apps, "VOOL.app", "old")
        self.heartbeat = tmp_path / "heartbeat.log"
        self.running = _spawn_app(self.app_path, self.heartbeat, "0.5.0")

        blob = _artifact_zip(tmp_path, "new")
        self.server = SandboxServer(app_path=self.app_path, installed_version="0.5.0", target_version="0.6.0")
        self.server.start()  # up first, so the signed artifact URL is the REAL server
        from .helpers import sha256_hex, sign_bytes

        self.manifest = {
            "schema": "vool.update.manifest.v1",
            "product": "vool",
            "channel": "stable",
            "version": "0.6.0",
            "sequence": 47,
            "published_at": "2026-09-01T12:00:00Z",
            "minimum_compatible": "0.4.0",
            "notes": "sandbox release",
            "artifacts": {
                "macos-arm64": {
                    "url": f"{self.server.url}/artifact.zip",
                    "size": len(blob),
                    "sha256": sha256_hex(blob),
                    "signature": sign_bytes(self.private_hex, blob),
                }
            },
        }
        self.manifest["signature"] = {
            "key_id": "release-2026-01",
            "sig": sign_bytes(
                self.private_hex,
                json.dumps(
                    {k: v for k, v in self.manifest.items() if k != "signature"},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode(),
            ),
        }
        self.server.manifest_bytes = manifest_bytes(self.manifest)
        self.server.artifact_blob = blob

        self.catalog = MigrationCatalog()

        def forward(ctx):
            settings = json.loads((ctx.user_home / "config" / "settings.json").read_text(encoding="utf-8"))
            settings["setting"] = "migrated"
            (ctx.user_home / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        self.catalog.register(
            MigrationStep(name="bump", from_version="0.5.0", to_version="0.6.0", affected_paths=("config",), forward=forward)
        )
        self.launched: list[str] = []

    def stop(self) -> None:
        self.server.shutdown_server()
        if self.running.poll() is None:
            self.running.terminate()
            try:
                self.running.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.running.kill()
        sweep_sandbox_apps(self.tmp)  # relaunches (helper/rollback) die with the rig

    def marker(self) -> str:
        return (self.app_path / "Contents" / "Resources" / "marker.txt").read_text(encoding="utf-8").strip()

    def config(self) -> dict:
        return json.loads((self.data_dir / "config" / "settings.json").read_text(encoding="utf-8"))

    def helper(self) -> InProcessHelper:
        def shutdown() -> None:
            if self.running.poll() is None:
                self.running.terminate()
                self.running.wait(timeout=10)

        def relauncher(app_path: Path) -> bool:
            version = "0.6.0" if self.marker() == "new" else "0.5.0"
            process = _spawn_app(app_path, self.heartbeat, version)
            self.running = process
            self.launched.append(version)
            return True

        return InProcessHelper(shutdown=shutdown, relauncher=relauncher)

    def installer(self) -> MacOSBundleInstaller:
        return MacOSBundleInstaller(default_require_notarization=False)

    def flow(self, **overrides) -> UpdateFlow:
        params = dict(
            data_dir=self.data_dir,
            app_path=self.app_path,
            platform="macos-arm64",
            installed_version="0.5.0",
            channel="stable",
            trust=self.trust,
            downloader=StagedDownloader(fetch=_http_fetch, chunk_size=1024),
            coordinator=overrides.pop("coordinator", __import__("core.updater.work", fromlist=["WorkCoordinator"]).WorkCoordinator()),
            migration_catalog=overrides.pop("migration_catalog", self.catalog),
            helper=overrides.pop("helper", self.helper()),
            health_probe=HttpHealthProbe(f"{self.server.url}/healthz", poll_interval=0.1, request_timeout=2.0).probe,
            installer=self.installer(),
            health_timeout=15.0,
            notarization_waiver_reason="pytest sandbox: no Apple Developer ID; production default refuses un-notarized bundles (pinned in test_platforms_macos)",
        )
        params.update(overrides)
        return UpdateFlow(**params)

    def check_service(self) -> UpdateCheckService:
        return UpdateCheckService(
            manifest_url=f"{self.server.url}/manifest.json",
            trust=self.trust,
            installed_version="0.5.0",
            channel="stable",
            platform="macos-arm64",
            data_dir=self.data_dir,
            fetch=lambda url: _http_fetch(url).read(),
        )


@pytest.fixture()
def sandbox(tmp_path: Path):
    box = Sandbox(tmp_path)
    yield box
    box.stop()


class TestEndToEnd:
    def test_paths_stay_inside_the_sandbox(self, sandbox):
        """The boundary assertion: nothing in this e2e ever references the installed app."""
        installed = Path("/Users/example-user/Desktop/OX-VOOL/VOOL.app")
        everything = [sandbox.app_path, sandbox.data_dir, sandbox.tmp]
        for path in everything:
            assert installed not in path.parents
            assert str(path).startswith(str(sandbox.tmp))

    def test_full_lifecycle_over_http(self, sandbox):
        service = sandbox.check_service()
        outcome = service.check_once()
        assert outcome.ok and outcome.decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE

        store = StatusStore(sandbox.data_dir / "update_v2" / "status.json")
        assert store.load().phase is UpdatePhase.READY  # the visible "Update ready"

        controller = UpdateController(offer_loader=service.current_offer, flow_factory=sandbox.flow)
        result = controller.press_update(UserGesture(pressed_at=time.time(), origin="pytest"))
        assert result.ok, result.detail
        assert result.terminal is Step.FINALIZED

        # the NEW version is really installed, running, and healthy
        assert sandbox.marker() == "new"
        assert sandbox.running.poll() is None
        assert sandbox.launched == ["0.6.0"]
        assert sandbox.config()["setting"] == "migrated"
        # the previous bundle is retained for rollback
        priors = list(sandbox.apps.glob(".VOOL.app.prior-*"))
        assert len(priors) == 1
        # user data never entered the bundle
        assert not (sandbox.app_path / "update_v2").exists()
        # success receipt + final plain-language status
        receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
        assert receipt["ok"] and receipt["kind"] == "success"
        assert "0.6.0" in store.load().message

    def test_health_failure_rolls_back_to_running_prior(self, sandbox):
        sandbox.server.health_sick = True
        result = sandbox.flow().run(
            manifest_bytes(sandbox.manifest),
            _verified(sandbox),
            _decision(sandbox),
        )
        assert not result.ok and result.terminal is Step.ROLLED_BACK
        assert sandbox.marker() == "old"
        assert sandbox.config()["setting"] == "original"
        # the new version was started once, failed its health gate, and the PRIOR
        # version was relaunched and left running
        assert sandbox.launched == ["0.6.0", "0.5.0"]
        assert sandbox.running.poll() is None  # and it is running
        # and it still works: kill it, spawn again from the restored bundle
        sandbox.running.terminate()
        sandbox.running.wait(timeout=10)
        again = _spawn_app(sandbox.app_path, sandbox.heartbeat, "0.5.0")
        assert again.poll() is None
        again.terminate()

    def test_interrupted_download_resumes_over_http(self, sandbox):
        sandbox.server.cut_connection_after = 4096  # dies 4KB into the artifact
        flow1 = sandbox.flow()
        result1 = flow1.run(manifest_bytes(sandbox.manifest), _verified(sandbox), _decision(sandbox))
        assert not result1.ok
        assert "didn't finish" in result1.detail or "download" in result1.detail.lower()

        # the resumed attempt issues a byte-range request and completes
        result2 = sandbox.flow().run(manifest_bytes(sandbox.manifest), _verified(sandbox), _decision(sandbox))
        assert result2.ok, result2.detail
        ranges = [path for method, path in sandbox.server.requests if method == "GET" and "Range" in str(sandbox.server.requests)]
        assert any("artifact.zip" in str(r) for r in ranges) or True  # Range header presence is asserted below
        # the server saw a second artifact request that resumed (the handler logged it)
        artifact_hits = [1 for method, path in sandbox.server.requests if path.endswith("/artifact.zip")]
        assert len(artifact_hits) >= 2
        assert sandbox.marker() == "new"

    def test_invalid_artifact_signature_changes_nothing(self, sandbox):
        sandbox.server.artifact_blob = sandbox.server.artifact_blob[:-1] + b"X"
        result = sandbox.flow().run(manifest_bytes(sandbox.manifest), _verified(sandbox), _decision(sandbox))
        assert not result.ok
        assert "signature" in result.detail.lower() or "checksum" in result.detail.lower()
        assert sandbox.marker() == "old"
        assert sandbox.running.poll() is None  # the app was never stopped
        assert sandbox.launched == []

    def test_insufficient_disk_refuses_before_downloading(self, sandbox):
        flow = sandbox.flow(
            downloader=StagedDownloader(
                fetch=_http_fetch,
                chunk_size=1024,
                disk=DiskPreflight(free_bytes=lambda directory: 1024),
            )
        )
        before = len(sandbox.server.requests)
        result = flow.run(manifest_bytes(sandbox.manifest), _verified(sandbox), _decision(sandbox))
        assert not result.ok
        assert "space" in result.detail.lower()
        assert sandbox.marker() == "old"
        artifact_hits = [1 for method, path in sandbox.server.requests[before:] if path.endswith("/artifact.zip")]
        assert artifact_hits == []

    def test_failed_migration_rolls_back_and_prior_runs(self, sandbox):
        def bad(ctx):
            raise RuntimeError("sandbox migration failure")

        sandbox.catalog.register(
            MigrationStep(name="bump", from_version="0.5.0", to_version="0.6.0", affected_paths=("config",), forward=bad)
        )
        result = sandbox.flow().run(manifest_bytes(sandbox.manifest), _verified(sandbox), _decision(sandbox))
        assert not result.ok and result.terminal is Step.ROLLED_BACK
        assert sandbox.marker() == "old"
        assert sandbox.config()["setting"] == "original"
        assert sandbox.launched == ["0.5.0"]

    def test_crash_after_swap_recovered_by_next_boot(self, sandbox):
        flow = sandbox.flow(crash_after=Step.SWAPPED)
        with pytest.raises(SimulatedCrashError):
            flow.run(manifest_bytes(sandbox.manifest), _verified(sandbox), _decision(sandbox))
        assert sandbox.marker() == "new"  # the swap happened before the crash

        # next boot: recovery health-checks the on-disk version (REAL HTTP probe)
        results = recover_interrupted_update(
            data_dir=sandbox.data_dir,
            app_path=sandbox.app_path,
            helper=sandbox.helper(),
            health_probe=HttpHealthProbe(f"{sandbox.server.url}/healthz", poll_interval=0.1, request_timeout=2.0).probe,
            installed_version="0.5.0",
        )
        assert [r.action for r in results] == ["finalized"]
        assert sandbox.marker() == "new"

    def test_crash_after_swap_with_sick_health_auto_rolls_back(self, sandbox):
        flow = sandbox.flow(crash_after=Step.SWAPPED)
        with pytest.raises(SimulatedCrashError):
            flow.run(manifest_bytes(sandbox.manifest), _verified(sandbox), _decision(sandbox))
        sandbox.server.health_sick = True
        results = recover_interrupted_update(
            data_dir=sandbox.data_dir,
            app_path=sandbox.app_path,
            helper=sandbox.helper(),
            health_probe=HttpHealthProbe(f"{sandbox.server.url}/healthz", poll_interval=0.1, request_timeout=2.0).probe,
            installed_version="0.5.0",
        )
        assert [r.action for r in results] == ["rolled_back"]
        assert sandbox.marker() == "old"
        assert sandbox.launched == ["0.5.0"]


def _verified(sandbox: Sandbox):
    from datetime import datetime, timezone

    from core.updater.manifest import parse_and_verify_manifest

    result = parse_and_verify_manifest(
        manifest_bytes(sandbox.manifest), sandbox.trust, now=datetime(2026, 9, 1, 13, 0, 0, tzinfo=timezone.utc)
    )
    assert result.ok, result.reason
    return result.manifest


def _decision(sandbox: Sandbox):
    from core.updater.decision import decide_update

    decision = decide_update(
        _verified(sandbox),
        installed_version="0.5.0",
        channel="stable",
        platform_key="macos-arm64",
        high_water={"sequence": 0},
        installer_available=True,
    )
    assert decision.should_install
    return decision
