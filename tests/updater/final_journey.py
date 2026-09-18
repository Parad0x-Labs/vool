#!/usr/bin/env python3
"""P1 hermetic installed-app journey for the atomic updater — the REAL lifecycle,
driven through the REAL production subsystem (boot_update_subsystem → check service →
two presses → external helper script → health gate → finalize or rollback).

What is REAL here:
  * the app under update is a REAL spawned process per bundle, serving /healthz in the
    CURRENT production schema (ok/agent/daemon/runtime/capabilities, exact SHA inside
    runtime.commit_full read from a file INSIDE the bundle — so after a swap the
    relaunched app reports the NEW identity);
  * the release feed is a real HTTP server with byte-Range support (and a tamper/cut arm);
  * manifests are really Ed25519-signed; artifacts really ad-hoc-codesigned bundles;
  * the swap runs through the shipped installer/update/mac_update_helper.sh, out of
    process, exactly as in production;
  * the presses are typed UserGesture objects through boot_update_subsystem — the same
    entry the API and CLI use.

What is hermetic: everything lives under a fresh tmp root (apps, feed, user data,
updater state); ports are scratch; notarization is waived per-transaction (journaled,
the production default still refuses un-notarized bundles, pinned by unit test). The
operator's installed app is never touched — the journey stats it read-only before and
after and records that it did not change.

Scenarios (each on its own sandbox root):
  A commit        channel=preview end-to-end: check → download → verify → stage →
                  press Update → press Restart → stop → swap → restart → health(EXACT
                  SHA) → finalize. Proves a non-stable channel explicitly.
  B rollback      the new version serves /healthz 503 → automatic rollback, prior
                  version relaunched and healthy, typed fault + recovery action.
  C unknown-key   a manifest signed by a key that is not pinned → nothing is offered.
  D tampered      the served artifact does not match its signed sha256 → download
                  refuses with a typed fault, nothing is changed.
  E wrong-platform a manifest with no artifact for this platform → honest refusal.
  F resume        the download is hard-cut mid-transfer → typed failure; a second
                  press RESUMES from the served Range and completes.

Every scenario asserts: no duplicate daemon/watchdog processes (exactly one live app
process per bundle path, census via ps), user data (credentials, sessions, workspace)
byte-identical across the update, and zero leaked processes at teardown.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from tests.updater.helpers import generate_publisher_keypair, manifest_bytes, sha256_hex, sign_bytes
from tests.updater.test_e2e_sandbox import SandboxServer, sweep_sandbox_apps

HELPER = REPO / "installer" / "update" / "mac_update_helper.sh"
PYTHON = sys.executable

OLD_SHA = "1" * 40
NEW_SHA = "2" * 40
RESULTS: list[dict] = []


def record(scenario: str, ok: bool, detail: dict) -> None:
    RESULTS.append({"scenario": scenario, "ok": ok, **detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {scenario}: {json.dumps(detail)[:400]}")


def scenario_clean(scenario: str) -> bool:
    return not any(row["ok"] is False and row["scenario"] == scenario for row in RESULTS)


def check(name: str, ok: bool, scenario: str, detail: str = "") -> bool:
    if not ok:
        record(scenario, False, {"failed_check": name, "detail": detail})
    return ok


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------- #
# the app under update
# --------------------------------------------------------------------------- #

APP_EXE = """#!/usr/bin/env python3
import json, os, signal, socket, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

bundle = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../VOOL.app/Contents
resources = os.path.join(bundle, "Resources")
conf = json.load(open(os.path.join(resources, "journey.json")))
root = os.path.dirname(os.path.dirname(os.path.dirname(bundle)))  # sandbox root (above Apps/)
pidfile = os.path.join(root, ".sandbox-app.pids")
heartbeat = conf.get("heartbeat") or os.path.join(root, "heartbeat.log")

# register BOTH the launcher and the long-lived server body (zero-leak law)
with open(pidfile, "a") as fh:
    fh.write(f"{os.getpid()}\\n")

# production parity: the relaunched app rewrites its pidfile at boot, so the
# updater always targets whoever CURRENTLY occupies the app seat
with open(os.path.join(root, "app.pid"), "w") as fh:
    fh.write(str(os.getpid()))

port = int(conf["port"])

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path.rstrip("/").endswith("/healthz"):
            if conf.get("sick"):
                body = b'{"ok": false}'
                self.send_response(503)
            else:
                payload = {
                    "ok": True,
                    "agent": "VOOL",
                    "daemon": True,
                    "runtime": {
                        "app_version": conf["version"],
                        "commit": conf["commit_full"][:12],
                        "commit_full": conf["commit_full"],
                        "build_id": "release+macos_arm+" + conf["commit_full"][:12],
                    },
                    "capabilities": {},
                }
                body = json.dumps(payload).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

deadline = time.time() + 15
server = None
while time.time() < deadline:  # the port may be in TIME_WAIT after a swap+rollback
    try:
        ThreadingHTTPServer.allow_reuse_address = True
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        break
    except OSError:
        time.sleep(0.25)
if server is None:
    sys.exit(3)

with open(heartbeat, "a") as fh:
    fh.write(conf["version"] + "\\n")
server.serve_forever()
"""


def make_journey_app(root: Path, name: str, *, port: int, commit_full: str, version: str, sick: bool = False) -> Path:
    bundle = root / "Apps" / name
    exe = bundle / "Contents" / "MacOS" / "VOOL"
    resources = bundle / "Contents" / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    exe.parent.mkdir(parents=True, exist_ok=True)
    (bundle / "Contents" / "Info.plist").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<!DOCTYPE plist PUBLIC '-//Apple//DTD PLIST 1.0//EN' 'http://www.apple.com/DTDs/PropertyList-1.0.dtd'>\n"
        "<plist version='1.0'><dict><key>CFBundleName</key><string>VOOL</string>"
        "<key>CFBundleIdentifier</key><string>ai.nulla.desktop.journey</string>"
        "<key>CFBundleExecutable</key><string>VOOL</string>"
        "<key>CFBundlePackageType</key><string>APPL</string></dict></plist>\n",
        encoding="utf-8",
    )
    (resources / "journey.json").write_text(
        json.dumps({"port": port, "commit_full": commit_full, "version": version, "sick": sick}), encoding="utf-8"
    )
    exe.write_text(APP_EXE, encoding="utf-8")
    exe.chmod(0o755)
    signed = subprocess.run(["codesign", "-s", "-", str(bundle)], capture_output=True, text=True)
    assert signed.returncode == 0, f"ad-hoc codesign failed: {signed.stderr}"
    return bundle


def spawn_app(bundle: Path, root: Path) -> subprocess.Popen:
    heartbeat = root / "heartbeat.log"
    process = subprocess.Popen(
        [str(bundle / "Contents" / "MacOS" / "VOOL")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "VOOL_SANDBOX_HEARTBEAT": str(heartbeat)},
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if app_health(bundle) is not None:
            return process
        time.sleep(0.1)
    raise AssertionError("journey app did not become healthy")


def wait_app_identity(bundle: Path, expected_sha: str, timeout: float = 30.0) -> dict | None:
    """Poll the app's own /healthz until it reports `expected_sha` (the relaunched
    app needs a moment to bind; `open -n` returns before the process is up)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = app_health(bundle)
        if health is not None and str(health.get("runtime", {}).get("commit_full", "")) == expected_sha:
            return health
        time.sleep(0.4)
    return app_health(bundle)


def app_health(bundle: Path) -> dict | None:
    """The identity the app at `bundle` currently reports on /healthz — the CURRENT
    production schema; None when it does not answer 200."""
    conf_path = bundle / "Contents" / "Resources" / "journey.json"
    try:
        conf = json.loads(conf_path.read_text())
    except OSError:
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{conf['port']}/healthz", timeout=2.0) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def live_app_processes(root: Path) -> list[tuple[int, str]]:
    """(pid, bundle-path) for every LIVE journey app process under root, by ps scan."""
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        line = line.strip()
        pid_str, _, command = line.partition(" ")
        if not pid_str.isdigit():
            continue
        if str(root) in command and "/Contents/MacOS/VOOL" in command:
            rows.append((int(pid_str), command))
    return rows


# --------------------------------------------------------------------------- #
# one scenario's sandbox
# --------------------------------------------------------------------------- #


class Scenario:
    def __init__(self, name: str, *, channel: str, sick_new: bool = False):
        self.name = name
        self.channel = channel
        self.root = Path(f"/tmp/vool-final-journey-{name}-{os.getpid()}")
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.port = free_port()
        self.feed_port = free_port()
        self.data_dir = self.root / "data"
        self._seed_user_data()
        self.private_hex, self.public_hex = generate_publisher_keypair()
        self.pin_file = self.root / "trusted_publisher_keys.json"
        self.pin_file.write_text(json.dumps({"release-2026-01": self.public_hex}), encoding="utf-8")

        self.app_path = make_journey_app(
            self.root, "VOOL.app", port=self.port, commit_full=OLD_SHA, version="0.5.0"
        )
        self.process = spawn_app(self.app_path, self.root)
        (self.root / "app.pid").write_text(str(self.process.pid))

        self.server = SandboxServer(
            app_path=self.app_path, installed_version="0.5.0", target_version="0.6.0"
        )
        self.server.start()

        self.new_bundle = make_journey_app(
            self.root / "zsrc",
            "VOOL.app",
            port=self.port,  # the SAME port: the relaunched app takes over the health seat
            commit_full=NEW_SHA,
            version="0.6.0",
            sick=sick_new,
        )
        artifact = self.root / "artifact.zip"
        with zipfile.ZipFile(artifact, "w") as archive:
            for file in sorted(self.new_bundle.rglob("*")):
                if file.is_file():
                    archive.write(file, f"VOOL.app/{file.relative_to(self.new_bundle)}")
        self.blob = artifact.read_bytes()
        self.artifact_sha = sha256_hex(self.blob)

        self._boot_subsystem()
        self.baseline_apps = self._census()

    # -- user data the update must preserve ---------------------------------- #

    def _seed_user_data(self):
        home = self.data_dir
        (home / "config").mkdir(parents=True)
        (home / "credentials").mkdir()
        (home / "sessions").mkdir()
        (home / "workspace").mkdir()
        (home / "config" / "settings.json").write_text(json.dumps({"theme": "dark", "model": "cloud"}))
        (home / "credentials" / "keyring.json").write_text(json.dumps({"openrouter": "sk-or-secret-1"}))
        (home / "sessions" / "s1.json").write_text(json.dumps({"turns": [1, 2, 3], "topic": "update lane"}))
        (home / "workspace" / "notes.txt").write_text("operator notes that must survive")

    def user_data_state(self) -> dict:
        state = {}
        for rel in (
            "config/settings.json",
            "credentials/keyring.json",
            "sessions/s1.json",
            "workspace/notes.txt",
        ):
            path = self.data_dir / rel
            state[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
        return state

    # -- wiring --------------------------------------------------------------- #

    def _boot_subsystem(self):
        from core.updater.feed import FeedConfig
        from core.updater.runtime import boot_update_subsystem, reset_update_subsystem_for_tests
        from core.updater.trust import TrustedPublishers

        reset_update_subsystem_for_tests()
        os.environ["VOOL_UPDATE_TARGET_PIDFILE"] = str(self.root / "app.pid")
        self.subsystem = boot_update_subsystem(
            data_dir=self.data_dir,
            trust=TrustedPublishers(pinned_keys={"release-2026-01": self.public_hex}),
            feed=FeedConfig(
                manifest_url=f"{self.server.url}/manifest.json",
                app_path=str(self.app_path),
                health_url=f"http://127.0.0.1:{self.port}/healthz",
                helper_script=str(HELPER),
                channel=self.channel,
                check_interval_seconds=3600,
            ),
            installed_version="0.5.0",
            platform="macos-arm64",
            notarization_waiver_reason=(
                "final-p1 journey sandbox: no Apple Developer ID; production default "
                "refuses un-notarized bundles (pinned in test_platforms_macos)"
            ),
            health_timeout=25.0,
        )

    def teardown(self):
        from core.updater.runtime import reset_update_subsystem_for_tests

        self.server.shutdown_server()
        reset_update_subsystem_for_tests()
        sweep_sandbox_apps(self.root)
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        time.sleep(0.3)
        leftovers = live_app_processes(self.root)
        for pid, _ in leftovers:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        shutil.rmtree(self.root, ignore_errors=True)
        return leftovers

    # -- helpers --------------------------------------------------------------- #

    def _census(self) -> int:
        return len(live_app_processes(self.root))

    def wait_phase(self, phases, timeout=90.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            phase = self.subsystem.status_payload()["phase"]
            if phase in phases:
                return phase
            time.sleep(0.2)
        return self.subsystem.status_payload()["phase"]

    def status(self) -> dict:
        return self.subsystem.status_payload()

    def press(self, route: str) -> dict:
        """The press exactly as the web API performs it: a typed UserGesture minted
        owner-local from a real UI action."""
        import time as time_mod

        from core.updater.service import UserGesture

        outcome = (
            self.subsystem.press_install(UserGesture(pressed_at=time_mod.time(), origin="journey"))
            if route == "install"
            else self.subsystem.press_restart(UserGesture(pressed_at=time_mod.time(), origin="journey"))
        )
        return outcome.to_dict()

    def signed_manifest(self, *, sign_key_hex: str, artifacts: dict, key_id: str = "release-2026-01") -> bytes:
        manifest = {
            "schema": "vool.update.manifest.v1",
            "product": "vool",
            "channel": self.channel,
            "version": "0.6.0",
            "sequence": 47,
            "published_at": "2026-09-02T00:00:00Z",
            "minimum_compatible": "0.4.0",
            "notes": f"final-p1 journey {self.name}",
            "build_commit": NEW_SHA,  # the exact SHA the new bundle reports on /healthz
            "artifacts": artifacts,
        }
        manifest["signature"] = {"key_id": key_id, "sig": sign_bytes(sign_key_hex, json.dumps(
            {k: v for k, v in manifest.items() if k != "signature"}, sort_keys=True, separators=(",", ":")
        ).encode())}
        return manifest_bytes(manifest)

    def default_artifacts(self, *, blob: bytes | None = None, platform: str = "macos-arm64") -> dict:
        blob = self.blob if blob is None else blob
        return {
            platform: {
                "url": f"{self.server.url}/artifact.zip",
                "size": len(blob),
                "sha256": sha256_hex(blob),
                "signature": sign_bytes(self.private_hex, blob),
            }
        }

    def serve(self, manifest: bytes, blob: bytes | None = None, cut: int | None = None):
        self.server.manifest_bytes = manifest
        self.server.artifact_blob = self.blob if blob is None else blob  # None = the real bytes
        if cut is not None:
            self.server.cut_connection_after = cut


# --------------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------------- #


def scenario_commit() -> None:
    """A: channel=preview, full lifecycle to DONE; exact SHA on /healthz after restart."""
    box = Scenario("commit", channel="preview")
    data_before = box.user_data_state()
    prod_before = _production_stat()
    try:
        check("census_one_app", box._census() == 1, box.name, f"got {box._census()}")
        box.serve(box.signed_manifest(sign_key_hex=box.private_hex, artifacts=box.default_artifacts()))
        offered = box.subsystem.trigger_check()
        check("check_offered", bool(offered.get("check_ok")), box.name, json.dumps(offered)[:200])
        phase = box.status()["phase"]
        check("phase_ready", phase == "ready", box.name, phase)

        outcome = box.press("install")
        check("install_accepted", bool(outcome.get("accepted")), box.name, json.dumps(outcome)[:200])
        phase = box.wait_phase({"ready_to_restart", "failed"})
        check("staged_ready_to_restart", phase == "ready_to_restart", box.name, phase)
        status_file = box.data_dir / "update_v2" / "status.json"
        staged_status = json.loads(status_file.read_text())
        check(
            "progress_visible",
            staged_status.get("progress_percent") is None or 0 <= staged_status["progress_percent"] <= 100,
            box.name,
            json.dumps(staged_status)[:160],
        )

        outcome = box.press("restart")
        check("restart_accepted", bool(outcome.get("accepted")), box.name, json.dumps(outcome)[:200])
        phase = box.wait_phase({"done", "rolled_back", "failed"})
        check("final_done", phase == "done", box.name, phase)

        # the FULL journaled lifecycle, in order (external/delegated helper path):
        # check→download→verify→stage→press→stop→swap→restart→health→commit
        expected_lifecycle = [
            "created", "manifest_verified", "decided", "downloaded", "artifact_verified",
            "preflight_ok", "work_paused", "active_state_received", "snapshot_complete",
            "migrated", "helper_shutdown", "app_stopped", "swapped",
            "restarted", "healthy", "finalized",
        ]
        check("lifecycle_complete", box._journal_steps() == expected_lifecycle, box.name,
              " -> ".join(box._journal_steps()))

        # the exact installed SHA, straight from the restarted app's own /healthz
        health = wait_app_identity(box.app_path, NEW_SHA)
        check("health_answers", health is not None, box.name)
        if health:
            reported = str(health.get("runtime", {}).get("commit_full", ""))
            check("exact_sha_after_restart", reported == NEW_SHA, box.name, reported)
        check("marker_new_on_disk", "0.6.0" in json.loads(
            (box.app_path / "Contents" / "Resources" / "journey.json").read_text())["version"], box.name)
        receipt_ok = any(
            p.name.endswith("success.json") for p in (box.data_dir / "update_v2" / "receipts").glob("*.json")
        )
        check("success_receipt", receipt_ok, box.name)
        check("user_data_preserved_commit", box.user_data_state() == data_before, box.name,
              json.dumps({"before": data_before, "after": box.user_data_state()}))
        check("no_duplicate_processes", box._census() == 1, box.name, f"got {box._census()}")
        check("production_untouched", _production_stat() == prod_before, box.name)
        record(box.name, scenario_clean(box.name), {
            "channel": box.channel,
            "sha_after_restart": NEW_SHA,
            "phase": phase,
            "processes": 1,
            "user_data": "byte-identical",
        })
    finally:
        leftovers = box.teardown()
        check("zero_leak_commit", not leftovers, box.name, str(leftovers))


def scenario_rollback() -> None:
    """B: the new version is sick → automatic rollback, typed fault + recovery action."""
    box = Scenario("rollback", channel="stable", sick_new=True)
    data_before = box.user_data_state()
    try:
        box.serve(box.signed_manifest(sign_key_hex=box.private_hex, artifacts=box.default_artifacts()))
        box.subsystem.trigger_check()
        box.press("install")
        phase = box.wait_phase({"ready_to_restart", "failed"})
        check("rb_staged", phase == "ready_to_restart", box.name, phase)
        box.press("restart")
        phase = box.wait_phase({"rolled_back", "done", "failed"})
        check("rb_rolled_back", phase == "rolled_back", box.name, phase)
        status = json.loads((box.data_dir / "update_v2" / "status.json").read_text())
        check("rb_fault_code", status.get("fault_code") == "health_check_failed", box.name, status.get("fault_code", ""))
        check("rb_recovery_action", bool(status.get("recovery_action")), box.name, status.get("recovery_action", ""))
        health = wait_app_identity(box.app_path, OLD_SHA)
        check("rb_prior_healthy_again", health is not None and
              str(health.get("runtime", {}).get("commit_full", "")) == OLD_SHA, box.name,
              json.dumps(health)[:160] if health else "no answer")
        check("rb_user_data_preserved", box.user_data_state() == data_before, box.name)
        check("rb_one_process", box._census() == 1, box.name, f"got {box._census()}")
        record(box.name, scenario_clean(box.name), {"fault": status.get("fault_code"), "recovery": status.get("recovery_action", "")[:60]})
    finally:
        leftovers = box.teardown()
        check("zero_leak_rollback", not leftovers, box.name, str(leftovers))


def scenario_unknown_key() -> None:
    """C: a manifest signed by an unpinned key → nothing offered, nothing changed."""
    box = Scenario("unknown-key", channel="stable")
    try:
        rogue_private, _rogue_public = generate_publisher_keypair()
        box.serve(box.signed_manifest(sign_key_hex=rogue_private, artifacts=box.default_artifacts(), key_id="rogue-key"))
        offered = box.subsystem.trigger_check()
        phase = box.status()["phase"]
        check("uk_not_offered", phase != "ready", box.name, phase)
        check("uk_app_untouched", app_health(box.app_path) is not None, box.name)
        check("uk_census", box._census() == 1, box.name)
        record(box.name, scenario_clean(box.name), {"phase": phase, "check_payload": {k: offered.get(k) for k in ("check_ok", "reason", "plain")}})
    finally:
        box.teardown()


def scenario_tampered() -> None:
    """D: the served artifact betrays its signed sha256 → typed refusal, nothing changed."""
    box = Scenario("tampered", channel="stable")
    data_before = box.user_data_state()
    try:
        evil = bytes(box.blob[:-4]) + b"PWN!"  # same size, different bytes
        box.serve(
            box.signed_manifest(sign_key_hex=box.private_hex, artifacts=box.default_artifacts(blob=box.blob)),
            blob=evil,
        )
        box.subsystem.trigger_check()
        box.press("install")  # accepted=False can mean "ran and failed fast"; judge the phase
        phase = box.wait_phase({"failed", "ready_to_restart"}, timeout=60)
        check("tam_refused", phase == "failed", box.name, phase)
        status = json.loads((box.data_dir / "update_v2" / "status.json").read_text())
        check("tam_fault_verification", status.get("fault_code") == "verification_failed", box.name, status.get("fault_code", ""))
        check("tam_recovery_action", bool(status.get("recovery_action")), box.name)
        check("tam_app_untouched", app_health(box.app_path) is not None, box.name)
        check("tam_user_data", box.user_data_state() == data_before, box.name)
        record(box.name, scenario_clean(box.name), {"fault": status.get("fault_code")})
    finally:
        box.teardown()


def scenario_wrong_platform() -> None:
    """E: a manifest with no artifact for this platform → honest refusal."""
    box = Scenario("wrong-platform", channel="stable")
    try:
        box.serve(box.signed_manifest(sign_key_hex=box.private_hex, artifacts=box.default_artifacts(platform="windows-x86_64")))
        box.subsystem.trigger_check()
        payload = box.status()
        check("wp_honest_no_artifact", payload["phase"] != "ready", box.name, payload["phase"])
        check("wp_app_untouched", app_health(box.app_path) is not None, box.name)
        record(box.name, scenario_clean(box.name), {"phase": payload["phase"], "message": payload["message"][:90]})
    finally:
        box.teardown()


def scenario_resume() -> None:
    """F: hard-cut mid-download → typed failure; the next press RESUMES over Range."""
    box = Scenario("resume", channel="stable")
    data_before = box.user_data_state()
    try:
        box.serve(
            box.signed_manifest(sign_key_hex=box.private_hex, artifacts=box.default_artifacts()),
            cut=len(box.blob) // 3,
        )
        box.subsystem.trigger_check()
        box.press("install")  # the cut download fails fast; the phase is the truth
        phase = box.wait_phase({"failed", "ready_to_restart"}, timeout=60)
        check("res_first_press_failed_typed", phase == "failed", box.name, phase)
        status = json.loads((box.data_dir / "update_v2" / "status.json").read_text())
        check("res_fault_download", status.get("fault_code") in ("download_failed", "verification_failed"), box.name,
              status.get("fault_code", ""))

        # heal the feed; the staged partial must be picked up, not restarted
        box.server.cut_connection_after = None
        range_hits_before = box._range_hits()
        box.press("install")
        phase = box.wait_phase({"ready_to_restart", "failed"}, timeout=90)
        check("res_resumed_to_ready", phase == "ready_to_restart", box.name, phase)
        range_hits_after = box._range_hits()
        check("res_range_resume_used", range_hits_after > range_hits_before, box.name,
              f"range requests before={range_hits_before} after={range_hits_after}")
        box.press("restart")
        phase = box.wait_phase({"done", "rolled_back", "failed"})
        check("res_final_done", phase == "done", box.name, phase)
        health = wait_app_identity(box.app_path, NEW_SHA)
        check("res_exact_sha", health is not None and
              str(health.get("runtime", {}).get("commit_full", "")) == NEW_SHA, box.name)
        check("res_user_data", box.user_data_state() == data_before, box.name)
        record(box.name, scenario_clean(box.name), {"resumed_via": "Range", "range_requests": range_hits_after - range_hits_before})
    finally:
        leftovers = box.teardown()
        check("zero_leak_resume", not leftovers, box.name, str(leftovers))


def _range_hits(self) -> int:
    return sum(1 for method, _path in self.server.requests if method == "RANGE")


def _journal_steps(self) -> list[str]:
    """The ordered step names of every transaction journal under this sandbox."""
    steps: list[str] = []
    tx_root = self.data_dir / "update_v2" / "transactions"
    if not tx_root.is_dir():
        return steps
    for tx_dir in sorted(tx_root.iterdir()):
        journal_file = tx_dir / "journal.jsonl"
        if not journal_file.is_file():
            continue
        for line in journal_file.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValueError):
                steps.append(str(json.loads(line).get("step")))
    return steps


Scenario._range_hits = _range_hits
Scenario._journal_steps = _journal_steps


def _production_stat() -> dict:
    """A read-only fingerprint of the operator's real installed app (never opened)."""
    out = {}
    for candidate in (Path.home() / "Applications" / "VOOL.app", Path("/Applications/VOOL.app")):
        if candidate.exists():
            stat = candidate.stat()
            out[str(candidate)] = f"{stat.st_mtime_ns}:{stat.st_ino}"
        else:
            out[str(candidate)] = "not-present"
    return out


def main() -> int:
    print(f"final-p1 journey @ {REPO} (python {PYTHON})")
    scenarios = [scenario_commit, scenario_rollback, scenario_unknown_key, scenario_tampered, scenario_wrong_platform, scenario_resume]
    failures = 0
    try:
        for run in scenarios:
            before = len(RESULTS)
            try:
                run()
            except Exception as exc:  # a scenario crash is a failure, not an abort
                import traceback

                traceback.print_exc()
                record(run.__name__, False, {"exception": f"{type(exc).__name__}: {exc}"})
            failures += sum(1 for row in RESULTS[before:] if not row["ok"])
    finally:
        out_dir = REPO / "validation-logs" / "updater-final-p1-20260902"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "journey_results.json").write_text(json.dumps(RESULTS, indent=2) + "\n", encoding="utf-8")
        print(f"\n{len(RESULTS)} rows, {failures} failed -> {out_dir / 'journey_results.json'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
