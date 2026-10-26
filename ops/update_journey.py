"""Prove the in-app update the way a person meets it: notice, Download and update, restart, same data.

One run installs an OLD released package in a disposable home, starts it, and drives only the app's own
HTTP surface (the same calls the header chip makes):

  1. the Go online switch is turned on through Settings' own route;
  2. the update notice must appear on its own (``--expect-notice auto``), or after one Check press for
     releases that predate automatic checks (``--expect-notice press``);
  3. Update is pressed once;
  4. the restarted app must report the new build commit on /healthz, the status must end in ``done``,
     and data written before the update must still be there.

Feeds:
  ``--feed live``  the shipped feed and pinned publisher key, exactly as users get them;
  ``--feed DIR``   a test feed: DIR holds a manifest signed with a throwaway key plus the payload ZIPs. It is
                   served on loopback (the updater only fetches from GitHub's release hosts or this computer),
                   the installed package's feed address is pointed at it, and the throwaway public key is
                   pinned for this run only. A loopback feed counts as local, so it is not gated by the
                   online switch; the switch gate itself is proved on the live feed (``--feed live``).

``--broken-update`` expects the opposite ending: the new version never comes up healthy, the app restores
the previous version by itself, and the old commit is serving again.

Linux packages run here directly. Mac DMGs run on a Mac (``--platform macos``); the app relaunch after a
swap uses macOS ``open``, so a Mac run uses the real login HOME of a disposable machine (a CI runner).
Writes ``journey.json`` (every step, pass/fail, timings, the feed request log) into ``--scratch``.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 11435
ORIGIN = f"http://127.0.0.1:{PORT}"


class JourneyFailure(RuntimeError):
    pass


def call(path: str, body: dict | None = None, timeout: float = 30) -> dict:
    request = urllib.request.Request(ORIGIN + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"},
                                     method="POST" if body is not None else "GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def health() -> dict:
    try:
        return call("/healthz", timeout=5)
    except (OSError, ValueError, urllib.error.URLError):
        return {}


class Log:
    def __init__(self, scratch: Path):
        self.scratch = scratch
        self.steps: list[dict] = []
        self.started = time.monotonic()
        self.requests: list[dict] = []

    def step(self, name: str, ok: bool, detail: object = "") -> None:
        entry = {"step": name, "ok": bool(ok), "at_s": round(time.monotonic() - self.started, 1), "detail": detail}
        self.steps.append(entry)
        print(("PASS " if ok else "FAIL ") + name + (f" :: {json.dumps(detail)[:400]}" if detail else ""), flush=True)
        self.save()
        if not ok:
            raise JourneyFailure(name)

    def save(self) -> None:
        (self.scratch / "journey.json").write_text(json.dumps(
            {"steps": self.steps, "feed_requests": self.requests}, indent=2) + "\n")


def wait_for(what: str, probe, timeout: float, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(interval)
    raise JourneyFailure(f"timed out after {timeout:.0f}s waiting for {what}; last: {last!r}")


def serve_feed(directory: Path, log: Log) -> tuple[str, http.server.ThreadingHTTPServer]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, fmt, *args):  # the journey keeps its own request log
            log.requests.append({"at_s": round(time.monotonic() - log.started, 1), "path": self.path})
            log.save()

    host = "127.0.0.1"
    server = http.server.ThreadingHTTPServer((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://{host}:{server.server_address[1]}", server


def write_test_feed(feed_dir: Path, base_url: str) -> None:
    """Point the throwaway manifest's artifact URLs at the served copies and re-sign it with the throwaway key."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core.updater.manifest import canonical_manifest_bytes
    from installer.update_release import sign

    manifest = json.loads((feed_dir / "manifest.unsigned.json").read_text())
    private_hex = (feed_dir / "test-key.private.hex").read_text().strip()
    for entry in manifest["artifacts"].values():
        entry["url"] = f"{base_url}/{entry['file']}"
        entry.pop("file")
    manifest["signature"] = {"key_id": manifest.pop("key_id"), "sig": sign(private_hex, canonical_manifest_bytes(manifest))}
    (feed_dir / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")


# -- platforms ---------------------------------------------------------------------------------------


class LinuxPackage:
    def __init__(self, archive: Path, scratch: Path):
        self.home = scratch / "home"
        self.home.mkdir()
        extracted = scratch / "extracted"
        extracted.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(extracted, filter="data")
        (package,) = list(extracted.iterdir())
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "CI": "true",
                    "VOOL_UNATTENDED": "1", "VOOL_LOCAL_MODELS_ENABLED": "0", "TMPDIR": str(scratch),
                    "XDG_CACHE_HOME": str(self.home / ".cache"), "BROWSER": "true"}
        subprocess.run(["bash", str(package / "install.sh")], env=self.env, check=True)
        shutil.rmtree(extracted)
        self.root = self.home / ".local/share/vool"
        self.log = (scratch / "app.log").open("ab")

    def feed_config(self) -> Path:
        return self.root / "current/app/config/release/update_feed.json"

    def data_dir(self) -> Path:
        return self.home / ".vool_runtime/data"

    def start(self) -> None:
        subprocess.Popen([str(self.home / ".local/bin/vool"), "--headless"], cwd=self.home, env=self.env,
                         stdout=self.log, stderr=self.log, start_new_session=True)

    def current_commit(self) -> str:
        return os.readlink(self.root / "current").rsplit("/", 1)[-1]

    def stop(self) -> None:
        pid = (health().get("runtime") or {}).get("pid")
        if pid:
            os.kill(int(pid), 15)


class MacApp:
    def __init__(self, dmg: Path, scratch: Path):
        attached = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-plist", str(dmg)],
                                  check=True, capture_output=True)
        mount = next(Path(e["mount-point"]) for e in plistlib.loads(attached.stdout)["system-entities"]
                     if e.get("mount-point"))
        try:
            self.app = scratch / "Applications" / "VOOL.app"
            self.app.parent.mkdir()
            subprocess.run(["ditto", str(mount / "VOOL.app"), str(self.app)], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True)
        # A downloaded DMG carries quarantine; the user's first open clears it through Gatekeeper.
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(self.app)], check=False)
        self.env = dict(os.environ, VOOL_UNATTENDED="1", VOOL_LOCAL_MODELS_ENABLED="0")
        self.log = (scratch / "app.log").open("ab")

    def feed_config(self) -> Path:
        return self.app / "Contents/Resources/app/config/release/update_feed.json"

    def data_dir(self) -> Path:
        return Path.home() / "Library/Application Support/VOOL/runtime/data"

    def start(self) -> None:
        subprocess.Popen([str(self.app / "Contents/MacOS/VOOL")], env=self.env, stdout=self.log, stderr=self.log,
                         start_new_session=True)

    def current_commit(self) -> str:
        manifest = json.loads((self.app / "Contents/Resources/BUILD_MANIFEST.json").read_text())
        return str(manifest.get("exact_sha") or "")

    def stop(self) -> None:
        subprocess.run(["osascript", "-e", 'quit app "VOOL"'], check=False, timeout=30)


# -- the journey -------------------------------------------------------------------------------------


def journey(args: argparse.Namespace) -> None:
    scratch = args.scratch
    scratch.mkdir(parents=True, exist_ok=False)
    log = Log(scratch)
    target = LinuxPackage(args.old, scratch) if args.platform == "linux" else MacApp(args.old, scratch)
    old_commit = target.current_commit()
    log.step("old package installed", bool(old_commit), {"commit": old_commit})

    server = None
    if args.feed != "live":
        feed_dir = Path(args.feed)
        base_url, server = serve_feed(feed_dir, log)
        write_test_feed(feed_dir, base_url)
        config = json.loads(target.feed_config().read_text())
        config["manifest_url"] = f"{base_url}/manifest.json"
        target.feed_config().write_text(json.dumps(config, indent=2) + "\n")
        target.env["VOOL_UPDATE_TRUSTED_KEYS_JSON"] = str(feed_dir / "test-pin.json")
        log.step("test feed served", True, {"manifest_url": config["manifest_url"]})

    target.start()
    first = wait_for("the old version to answer", lambda: health() if health().get("ok") else None, 300)
    runtime = first.get("runtime") or {}
    log.step("old version running", runtime.get("commit_full") == old_commit,
             {k: runtime.get(k) for k in ("app_version", "commit_full")})
    marker = target.data_dir() / "update-journey-marker.txt"
    marker.write_text("written before the update\n")

    status = call("/api/update/status")
    log.step("updater configured", status.get("configured") is True,
             {k: status.get(k) for k in ("configured", "unavailable_plain", "phase", "online_access")})
    if args.feed == "live":
        time.sleep(10)
        quiet = call("/api/update/status")
        log.step("no check while Go online is off", quiet.get("phase") == "idle" and quiet.get("online_access") is False,
                 {k: quiet.get(k) for k in ("phase", "online_access", "message")})

    switched = call("/api/onboarding/pact/boundary", {"key": "automatic_checks", "value": True})
    log.step("Go online on its own turned on", switched.get("ok") is not False, switched)

    if args.expect_notice == "press":
        pressed = call("/api/update/check", {}, timeout=120)
        log.step("Check for updates pressed", pressed.get("check_ok") is True,
                 {k: pressed.get(k) for k in ("check_ok", "decision", "detail")})

    def notice():
        payload = call("/api/update/status")  # the chip's own poll
        return payload if payload.get("phase") == "ready" else None

    offered = wait_for("the update notice", notice, args.notice_timeout, 2)
    log.step("update notice shown" + (" without a Check press" if args.expect_notice == "auto" else ""),
             offered.get("target_version") == args.new_version,
             {k: offered.get(k) for k in ("phase", "target_version", "message")})

    started = call("/api/update/install", {}, timeout=60)
    log.step("Update pressed", started.get("ok") is not False and started.get("accepted", True) is not False, started)

    expect = old_commit if args.broken_update else args.new_commit
    ending = "rolled_back" if args.broken_update else "finalized"
    transactions = target.data_dir() / "update_v2" / "transactions"

    def journal_end():
        # The transaction journal is the updater's own record of how the update ended. (The status line
        # can already show the restarted app's next check: with a throwaway pin that check of the live
        # feed fails, which says nothing about the update itself.)
        for directory in sorted(transactions.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
            lines = (directory / "journal.jsonl").read_text().splitlines() if (directory / "journal.jsonl").exists() else []
            return [json.loads(line).get("step") for line in lines if line.strip()]
        return []

    def serving():
        runtime = health().get("runtime") or {}
        steps = journal_end()
        if runtime.get("commit_full") != expect or not steps or steps[-1] not in ("finalized", "rolled_back", "aborted"):
            return None
        return {"steps": steps, "status": call("/api/update/status")}

    finished = wait_for("the update to finish", serving, args.install_timeout, 2)
    status = finished["status"]
    log.step(f"update journal ended {ending}", finished["steps"][-1] == ending,
             {"steps": finished["steps"], "status": {k: status.get(k) for k in ("phase", "message", "installed_version")}})
    log.step("serving the expected build", target.current_commit() == expect,
             {"expected": expect, "current": target.current_commit()})
    log.step("data from before the update kept", marker.read_text() == "written before the update\n")

    if args.platform == "linux":
        relaunch = subprocess.run([str(target.home / ".local/bin/vool")], env=target.env, capture_output=True,
                                  text=True, timeout=60)
        log.step("opening VOOL again reaches the running app", relaunch.returncode == 0,
                 relaunch.stderr.strip()[-300:])
    if server is not None:
        log.step("feed requests", True, log.requests)
    target.stop()


def make_feed(args: argparse.Namespace) -> None:
    """A throwaway-key feed for the exact payload bytes: the unsigned manifest, the key pair and the pin."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from installer.update_release import sha256_of, sign

    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    key = Ed25519PrivateKey.generate()
    private_hex = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption()).hex()
    public_hex = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    (out / "test-key.private.hex").write_text(private_hex + "\n")
    (out / "test-pin.json").write_text(json.dumps({"update-journey-test": public_hex}) + "\n")
    artifacts = {}
    for item in args.artifact:
        platform, _, path = item.partition("=")
        source = Path(path)
        shutil.copy2(source, out / source.name)
        artifacts[platform] = {"file": source.name, "size": source.stat().st_size, "sha256": sha256_of(source),
                               "signature": sign(private_hex, source.read_bytes())}
    manifest = {"schema": "vool.update.manifest.v1", "product": "vool", "channel": args.channel,
                "version": args.version, "sequence": args.sequence, "minimum_compatible": "0.7.0",
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "notes": f"VOOL {args.version} test",
                "build_commit": args.commit, "artifacts": artifacts, "key_id": "update-journey-test"}
    (out / "manifest.unsigned.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "make-feed":
        feed = argparse.ArgumentParser(prog="update_journey.py make-feed")
        feed.add_argument("--out", type=Path, required=True)
        feed.add_argument("--version", required=True)
        feed.add_argument("--commit", required=True)
        feed.add_argument("--channel", default="beta")
        feed.add_argument("--sequence", type=int, default=1000)
        feed.add_argument("--artifact", action="append", required=True, help="PLATFORM_KEY=path/to/update.zip")
        make_feed(feed.parse_args(sys.argv[2:]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--platform", choices=("linux", "macos"), required=True)
    parser.add_argument("--old", type=Path, required=True, help="the released package: Linux .tar.gz or Mac .dmg")
    parser.add_argument("--feed", default="live", help="'live' or a test feed directory")
    parser.add_argument("--new-version", required=True, help="the version the notice must offer, e.g. 0.7.5")
    parser.add_argument("--new-commit", required=True, help="the build commit the updated app must report")
    parser.add_argument("--expect-notice", choices=("auto", "press"), default="auto")
    parser.add_argument("--broken-update", action="store_true")
    parser.add_argument("--notice-timeout", type=float, default=180)
    parser.add_argument("--install-timeout", type=float, default=900)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    try:
        journey(args)
    except JourneyFailure as exc:
        print(f"JOURNEY FAILED: {exc}", file=sys.stderr)
        return 1
    print("JOURNEY PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
