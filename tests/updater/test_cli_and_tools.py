"""CLI surface, external helper script, and release tools.

The helper-script test executes the REAL bash script against temp directories with a
spawned placeholder process — the installed VOOL app is never touched (a named
assertion documents that boundary). The release-signing tool is proven by round-trip:
its output verifies through the production verifier.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from core.updater.macos import MacOSBundleInstaller
from core.updater.manifest import parse_and_verify_manifest
from core.updater.trust import TrustedPublishers

from .helpers import generate_publisher_keypair, manifest_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "installer" / "update" / "mac_update_helper.sh"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "installer.update_cli", *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )


@pytest.fixture()
def pinned_keys(tmp_path, monkeypatch):
    private_hex, public_hex = generate_publisher_keypair()
    pin = tmp_path / "trusted_publisher_keys.json"
    pin.write_text(json.dumps({"release-2026-01": public_hex}))
    monkeypatch.setenv("VOOL_UPDATE_PUBLISHER_KEYS", f"release-2026-01:{public_hex}")
    return private_hex, public_hex


class TestCLI:
    def test_status_empty(self, tmp_path):
        result = run_cli("status", "--data-dir", str(tmp_path))
        assert result.returncode == 0
        assert "No update status yet" in result.stdout

    def test_status_prints_persisted_message(self, tmp_path):
        from core.updater.state import UpdaterPaths
        from core.updater.status import StatusStore, UpdatePhase

        StatusStore(UpdaterPaths.for_data_dir(tmp_path).status_file).publish(UpdatePhase.READY, version="0.6.0")
        result = run_cli("status", "--data-dir", str(tmp_path))
        assert "0.6.0" in result.stdout
        assert "ready" in result.stdout.lower()

    def test_check_without_trust_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VOOL_UPDATE_PUBLISHER_KEYS", raising=False)
        result = run_cli(
            "check", "--data-dir", str(tmp_path), "--manifest-url", "https://updates.example.invalid/m.json"
        )
        assert result.returncode != 0  # nothing is offered with no pinned publisher

    def test_check_without_manifest_url_reports_unavailable(self, tmp_path, monkeypatch):
        """The shipped feed is empty; `check` must still RUN and say so honestly.
        A required --manifest-url argparse-rejected the command before the honest
        "Updates are unavailable" state could ever be printed."""
        monkeypatch.delenv("VOOL_UPDATE_PUBLISHER_KEYS", raising=False)
        monkeypatch.delenv("VOOL_UPDATE_MANIFEST_URL", raising=False)
        result = run_cli("check", "--data-dir", str(tmp_path))
        assert result.returncode != 0  # unconfigured is a first-class failure, not success
        assert "unavailable" in result.stdout.lower()

    def test_apply_refuses_without_explicit_press(self, tmp_path):
        result = run_cli(
            "apply",
            "--data-dir",
            str(tmp_path),
            "--manifest-url",
            "https://updates.example.invalid/m.json",
            "--app-path",
            str(tmp_path / "VOOL.app"),
        )
        assert result.returncode == 2
        assert "no explicit update press" in result.stdout.lower()

    def test_apply_happy_path_with_local_server(self, tmp_path, pinned_keys, monkeypatch):
        private_hex, _public_hex = pinned_keys
        # a real app bundle (ad-hoc signed) to swap — proper structure so codesign accepts it
        app = tmp_path / "Apps" / "VOOL.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        (app / "Contents" / "Resources" / "marker.txt").write_text("old", encoding="utf-8")
        (app / "Contents" / "Info.plist").write_text(
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<plist version='1.0'><dict><key>CFBundleName</key><string>VOOL</string>"
            "<key>CFBundleIdentifier</key><string>ai.vool.sandbox</string>"
            "<key>CFBundleExecutable</key><string>VOOL</string>"
            "<key>CFBundlePackageType</key><string>APPL</string></dict></plist>\n",
            encoding="utf-8",
        )
        (app / "Contents" / "MacOS" / "VOOL").write_text("#!/bin/bash\nsleep 600\n", encoding="utf-8")
        (app / "Contents" / "MacOS" / "VOOL").chmod(0o755)
        subprocess.run(["codesign", "-s", "-", str(app)], capture_output=True, check=True)

        new_bundle = tmp_path / "new"
        (new_bundle / "VOOL.app" / "Contents" / "MacOS").mkdir(parents=True)
        (new_bundle / "VOOL.app" / "Contents" / "Resources").mkdir()
        (new_bundle / "VOOL.app" / "Contents" / "Resources" / "marker.txt").write_text("new", encoding="utf-8")
        (new_bundle / "VOOL.app" / "Contents" / "Info.plist").write_text(
            (app / "Contents" / "Info.plist").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (new_bundle / "VOOL.app" / "Contents" / "MacOS" / "VOOL").write_text(
            "#!/bin/bash\nsleep 600\n", encoding="utf-8"
        )
        (new_bundle / "VOOL.app" / "Contents" / "MacOS" / "VOOL").chmod(0o755)
        subprocess.run(["codesign", "-s", "-", str(new_bundle / "VOOL.app")], capture_output=True, check=True)
        import zipfile

        artifact = tmp_path / "artifact.zip"
        with zipfile.ZipFile(artifact, "w") as zf:
            for file in sorted((new_bundle / "VOOL.app").rglob("*")):
                if file.is_file():
                    zf.write(file, f"VOOL.app/{file.relative_to(new_bundle / 'VOOL.app')}")
        blob = artifact.read_bytes()

        from .test_e2e_sandbox import SandboxServer

        server = SandboxServer(app_path=app, installed_version="0.5.0", target_version="0.6.0")
        server.start()

        from .helpers import sha256_hex, sign_bytes

        manifest = {
            "schema": "vool.update.manifest.v1",
            "product": "vool",
            "channel": "stable",
            "version": "0.6.0",
            "sequence": 47,
            "published_at": "2026-09-01T12:00:00Z",
            "minimum_compatible": "0.4.0",
            "notes": "cli test",
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
                json.dumps({k: v for k, v in manifest.items() if k != "signature"}, sort_keys=True, separators=(",", ":")).encode(),
            ),
        }
        raw = manifest_bytes(manifest)
        server.manifest_bytes = raw
        server.artifact_blob = blob
        try:
            # Run the CLI IN-PROCESS so the injected seams apply; codesign stays REAL,
            # notarization is waived for the sandbox exactly as documented.
            import core.updater.download as download_module
            import core.updater.service as service_module
            import installer.update_cli as cli_module

            def local_fetch(url, headers=None, timeout=30.0):
                import urllib.request

                request = urllib.request.Request(url, headers=dict(headers or {}))
                return urllib.request.urlopen(request, timeout=timeout)

            monkeypatch.setattr(download_module, "door_fetch", local_fetch)
            monkeypatch.setattr(service_module, "fetch_manifest_bytes", lambda url: raw)
            monkeypatch.setattr(
                cli_module, "installer_for", lambda key: MacOSBundleInstaller(default_require_notarization=False)
            )
            monkeypatch.setattr(cli_module, "_installed_version", lambda: "0.5.0")
            rc = cli_module.main(
                [
                    "apply",
                    "--data-dir",
                    str(tmp_path / "data"),
                    "--manifest-url",
                    f"{server.url}/manifest.json",
                    "--app-path",
                    str(app),
                    "--i-pressed-update",
                    "--health-url",
                    f"{server.url}/healthz",
                    "--relaunch-cmd",
                    "/bin/echo relaunched",
                ]
            )
            assert rc == 0
            assert (app / "Contents" / "Resources" / "marker.txt").read_text(encoding="utf-8") == "new"
        finally:
            server.shutdown_server()


class TestHelperScript:
    def test_script_waits_swaps_and_records(self, tmp_path):
        apps = tmp_path / "Apps"
        app = apps / "VOOL.app"
        (app / "Contents" / "Resources").mkdir(parents=True)
        (app / "Contents" / "Resources" / "marker.txt").write_text("old", encoding="utf-8")
        staged_root = tmp_path / "stage"
        staged = staged_root / "VOOL.app"
        (staged / "Contents" / "Resources").mkdir(parents=True)
        (staged / "Contents" / "Resources" / "marker.txt").write_text("new", encoding="utf-8")

        placeholder = subprocess.Popen(["sleep", "60"])
        result_file = tmp_path / "result.json"
        relaunched = tmp_path / "relaunched.txt"
        began = time.monotonic()

        def stop_placeholder_later():
            time.sleep(1.0)
            placeholder.terminate()

        stopper = __import__("threading").Thread(target=stop_placeholder_later)
        stopper.start()
        completed = subprocess.run(
            [
                "bash",
                str(HELPER),
                "--app",
                str(app),
                "--staged",
                str(staged),
                "--txid",
                "e2e-1",
                "--wait-pid",
                str(placeholder.pid),
                "--wait-seconds",
                "20",
                "--relaunch-cmd",
                f"touch {relaunched}",
                "--result",
                str(result_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        waited = time.monotonic() - began
        placeholder.wait(timeout=10)
        stopper.join(timeout=5)

        assert completed.returncode == 0, completed.stderr
        assert (app / "Contents" / "Resources" / "marker.txt").read_text(encoding="utf-8") == "new"
        prior = apps / ".VOOL.app.prior-e2e-1"
        assert (prior / "Contents" / "Resources" / "marker.txt").read_text(encoding="utf-8") == "old"
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        assert payload["ok"] is True and payload["txid"] == "e2e-1"
        # the relaunch command is backgrounded by the helper; give it a moment
        for _ in range(50):
            if relaunched.exists():
                break
            time.sleep(0.1)
        assert relaunched.exists()
        assert waited >= 0.9  # it POLLED the pid rather than swapping under it

    def test_script_restores_when_staged_missing(self, tmp_path):
        apps = tmp_path / "Apps"
        app = apps / "VOOL.app"
        (app / "Contents").mkdir(parents=True)
        result_file = tmp_path / "result.json"
        completed = subprocess.run(
            [
                "bash",
                str(HELPER),
                "--app",
                str(app),
                "--staged",
                str(tmp_path / "no-such-bundle"),
                "--txid",
                "e2e-2",
                "--result",
                str(result_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        assert payload["ok"] is False
        assert app.exists()  # untouched


class TestReleaseTools:
    def test_gen_keypair_writes_outside_repo_only(self, tmp_path):
        from installer.gen_publisher_keypair import main as gen_main

        out_dir = tmp_path / "secrets"
        assert gen_main(["--key-id", "k1", "--out-dir", str(out_dir)]) == 0
        private_file = out_dir / "k1.private.hex"
        assert private_file.exists()
        assert private_file.stat().st_mode & 0o777 == 0o600
        assert (out_dir / "k1.public.hex").exists()

        repo_dir = REPO_ROOT / "workspace" / "should-not-exist"
        assert gen_main(["--key-id", "k2", "--out-dir", str(repo_dir)]) == 1

    def test_sign_release_round_trips_through_the_verifier(self, tmp_path, pinned_keys):
        private_hex, public_hex = pinned_keys
        artifact = tmp_path / "VOOL-0.6.0-macos-arm64.zip"
        blob = b"MACOS-BUNDLE-BYTES-" + os.urandom(16)
        artifact.write_bytes(blob)
        template = tmp_path / "template.json"
        template.write_text(
            json.dumps(
                {
                    "artifacts": {
                        "macos-arm64": {"url": "https://updates.example.invalid/VOOL-0.6.0-macos-arm64.zip", "path": str(artifact)}
                    },
                    "notes": "round trip",
                }
            )
        )
        key_file = tmp_path / "release.private.hex"
        key_file.write_text(private_hex + "\n")
        out = tmp_path / "manifest.json"

        from installer.update_release import main as sign_main

        rc = sign_main(
            [
                "--template",
                str(template),
                "--key-file",
                str(key_file),
                "--key-id",
                "release-2026-01",
                "--sequence",
                "51",
                "--version",
                "0.6.0",
                "--minimum-compatible",
                "0.4.0",
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
        # The signer stamps the REAL clock, so the verifier's now must be derived from the
        # signed manifest's own published_at. A hard-coded now expired on 2026-09-03T12:00Z
        # (48 h skew past the pin) and made this round trip fail by wall time, not by law.
        # The future-dated-manifest refusal keeps its own deterministic pin in
        # tests/updater/test_trust_manifest.py::test_future_dated_manifest_refused.
        signed_at = json.loads(out.read_bytes())["published_at"]
        verification = parse_and_verify_manifest(
            out.read_bytes(),
            trust,
            now=datetime.fromisoformat(signed_at.replace("Z", "+00:00")),
        )
        assert verification.ok, verification.reason
        assert verification.manifest.sequence == 51
        entry = verification.manifest.artifact_for("macos-arm64")
        assert entry.size == len(blob)
        from core.updater.manifest import verify_artifact_bytes

        assert verify_artifact_bytes(blob, entry, public_hex)
        assert not verify_artifact_bytes(blob + b"x", entry, public_hex)
