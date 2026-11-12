"""Prove the in-app update the way a person meets it: notice, Download and update, restart, same data.

One run installs an OLD released package in a disposable home, starts it, and drives only the app's own
HTTP surface (the same calls the header chip makes):

  1. the Go online switch is turned on through Settings' own route;
  2. the update notice must appear on its own (``--expect-notice auto``), or after one Check press for
     releases that predate automatic checks (``--expect-notice press``);
  3. Update is pressed once;
  4. the restarted app must report the new build commit on /healthz, the status must end in ``done``,
     and data written before the update must still be there.

Before Update is pressed, the person's state is put in through the old version's own doors (chats brought
in with Settings' chat import, a pinned chat, a project, preferences and facts; ``ops/update_preservation.py``).
After the update, or after a broken update restored itself, the running version must still show all of it,
and no profile file, key or database row may be gone.

Feeds:
  ``--feed live``  the shipped feed and pinned publisher key, exactly as users get them;
  ``--feed DIR``   a test feed: DIR holds a manifest signed with a throwaway key plus the payload ZIPs. It is
                   served on loopback (the updater only fetches from GitHub's release hosts or this computer),
                   the installed package's feed address is pointed at it, and the throwaway public key is
                   pinned for this run only. A loopback feed counts as local, so it is not gated by the
                   online switch; the switch gate itself is proved on the live feed (``--feed live``).

``--broken-update`` expects the opposite ending: the new version never comes up healthy, the app restores
the previous version by itself, and the old commit is serving again.

On Linux the person then closes VOOL and opens it again: the version now serving must start cold and still
show everything. ``--legacy-profile`` starts the person from the pre-rename profile (``~/.nulla_runtime``;
``~/Library/Application Support/NULLA/runtime`` on a Mac), which every version reuses in place: the update
(and on Linux the cold start) must keep using it, and no second, empty VOOL profile may appear beside it.

``--platform linux-deb`` is a person who installed the .deb with apt: ``--old`` is the old .deb, the first open
is the menu entry's own command (``/usr/bin/vool``, no ``--headless``), which sets up their own copy, and every
later open goes the same way, so a cold open after the update must keep the in-app update and never put back the
older copy apt installed. ``--apt-upgrade NEW.deb`` replaces the in-app update with ``apt-get install NEW.deb``:
the next open from the menu must set up that newer copy for the person, with all their data. Both end with
``apt-get remove vool``: only the .deb's own files go, and the person's own copy still opens with everything.
apt runs through ``sudo -n`` unless this runs as root.

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

import update_preservation  # ops/, the script's own folder

PORT = 11435
ORIGIN = f"http://127.0.0.1:{PORT}"
SYSTEM_PACKAGE = Path("/opt/vool/package")  # where the .deb keeps its copy


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
    def __init__(self, archive: Path, scratch: Path, *, legacy_profile: bool = False):
        self._person(scratch, legacy_profile)
        extracted = scratch / "extracted"
        extracted.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(extracted, filter="data")
        (package,) = list(extracted.iterdir())
        subprocess.run(["bash", str(package / "install.sh")], env=self.env, check=True)
        shutil.rmtree(extracted)

    def _person(self, scratch: Path, legacy_profile: bool) -> None:
        self.home = scratch / "home"
        self.home.mkdir()
        # The pre-rename profile folder: a person who used VOOL before its rename has only this one.
        self.profile = self.home / (".nulla_runtime" if legacy_profile else ".vool_runtime")
        self.second_profile = self.home / ".vool_runtime"
        if legacy_profile:
            self.profile.mkdir()
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "CI": "true",
                    "VOOL_UNATTENDED": "1", "VOOL_LOCAL_MODELS_ENABLED": "0", "TMPDIR": str(scratch),
                    "XDG_CACHE_HOME": str(self.home / ".cache"), "BROWSER": "true"}
        self.root = self.home / ".local/share/vool"
        self.log = (scratch / "app.log").open("ab")

    def feed_config(self) -> Path:
        return self.root / "current/app/config/release/update_feed.json"

    def point_at_feed(self, manifest_url: str) -> None:
        # The package's shipped feed setting is plain data: pointing it at the test feed keeps every other
        # shipped choice (auto_check) in force, so the notice proves what users get.
        config = json.loads(self.feed_config().read_text())
        config["manifest_url"] = manifest_url
        self.feed_config().write_text(json.dumps(config, indent=2) + "\n")

    def open_command(self) -> list[str]:
        return [str(self.home / ".local/bin/vool")]

    def data_dir(self) -> Path:
        return self.profile / "data"

    def person_home(self) -> Path:
        return self.home

    def start(self) -> None:
        subprocess.Popen([str(self.home / ".local/bin/vool"), "--headless"], cwd=self.home, env=self.env,
                         stdout=self.log, stderr=self.log, start_new_session=True)

    def current_commit(self) -> str:
        return os.readlink(self.root / "current").rsplit("/", 1)[-1]

    def stop(self) -> None:
        pid = (health().get("runtime") or {}).get("pid")
        if pid:
            os.kill(int(pid), 15)


def apt(*arguments: str) -> str:
    sudo = [] if os.geteuid() == 0 else ["sudo", "-n"]
    result = subprocess.run([*sudo, "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-y", *arguments],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise JourneyFailure(f"apt-get {' '.join(arguments)} failed: {(result.stderr or result.stdout)[-1500:]}")
    return result.stdout


class DebPackage(LinuxPackage):
    """A person who installed the .deb with apt. Every open is the menu entry's own command."""

    def __init__(self, deb: Path, scratch: Path, *, legacy_profile: bool = False):
        self._person(scratch, legacy_profile)
        if Path("/usr/bin/vool").exists() or SYSTEM_PACKAGE.exists():
            raise JourneyFailure("VOOL is already installed on this computer: the .deb journey needs one without it")
        apt("install", "--no-install-recommends", str(deb.resolve()))

    def point_at_feed(self, manifest_url: str) -> None:
        # The person's own copy only appears on the first open; the feed override reaches it unchanged.
        self.env["VOOL_UPDATE_MANIFEST_URL"] = manifest_url

    def open_command(self) -> list[str]:
        return ["/usr/bin/vool"]

    def start(self) -> None:
        subprocess.Popen(self.open_command(), cwd=self.home, env=self.env, stdout=self.log, stderr=self.log,
                         start_new_session=True)

    def current_commit(self) -> str:
        if (self.root / "current").is_symlink():
            return super().current_commit()
        return (SYSTEM_PACKAGE / "SOURCE_COMMIT").read_text().strip()  # not opened yet: the copy apt installed


def removing_the_deb_keeps_everything(target: DebPackage, log: Log, seen_before: dict, expect: str) -> None:
    target.stop()
    wait_for("VOOL to close", lambda: not health(), 120)
    said = apt("remove", "vool")
    log.step("apt-get remove vool took only the .deb's own files",
             not Path("/usr/bin/vool").exists() and not SYSTEM_PACKAGE.exists() and (target.root / "current").is_symlink(),
             [line for line in said.splitlines() if line.startswith("VOOL:")])
    subprocess.Popen([str(target.home / ".local/bin/vool"), "--headless"], cwd=target.home, env=target.env,
                     stdout=target.log, stderr=target.log, start_new_session=True)
    wait_for("the person's own copy to open", lambda: (health().get("runtime") or {}).get("commit_full") == expect, 300)
    try:
        seen = update_preservation.person_view(ORIGIN)
    except update_preservation.PreservationFailure as exc:
        log.step("after apt-get remove, the person's own copy opens with everything they had", False, str(exc))
    lost = update_preservation.view_losses(seen_before, seen)
    log.step("after apt-get remove, the person's own copy opens with everything they had", not lost,
             lost or {"chats": len(seen["chats"]), "commit": expect})


class MacApp:
    def __init__(self, dmg: Path, scratch: Path, *, legacy_profile: bool = False):
        support = Path.home() / "Library/Application Support"
        self.profile = support / ("NULLA/runtime" if legacy_profile else "VOOL/runtime")
        self.second_profile = support / "VOOL/runtime"
        if legacy_profile:
            if self.second_profile.exists():
                raise JourneyFailure(f"{self.second_profile} already exists: the pre-rename journey needs a Mac "
                                     "that never ran VOOL (a fresh CI runner)")
            self.profile.mkdir(parents=True)
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
        return self.profile / "data"

    def person_home(self) -> Path:
        return Path.home()

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
    kind = {"linux": LinuxPackage, "linux-deb": DebPackage, "macos": MacApp}[args.platform]
    target = kind(args.old, scratch, legacy_profile=args.legacy_profile)
    old_commit = target.current_commit()
    log.step("old package installed", bool(old_commit), {"commit": old_commit})

    server = None
    if args.feed != "live":
        feed_dir = Path(args.feed)
        base_url, server = serve_feed(feed_dir, log)
        write_test_feed(feed_dir, base_url)
        manifest_url = f"{base_url}/manifest.json"
        if args.platform == "macos":
            # Editing a Mac bundle would break its code signature; the launcher's feed override is used instead.
            target.env["VOOL_UPDATE_MANIFEST_URL"] = manifest_url
        else:
            target.point_at_feed(manifest_url)
        target.env["VOOL_UPDATE_TRUSTED_KEYS_JSON"] = str(feed_dir / "test-pin.json")
        log.step("test feed served", True, {"manifest_url": manifest_url})

    target.start()
    first = wait_for("the old version to answer", lambda: health() if health().get("ok") else None, 300)
    runtime = first.get("runtime") or {}
    log.step("old version running", runtime.get("commit_full") == old_commit,
             {k: runtime.get(k) for k in ("app_version", "commit_full")})
    marker = target.data_dir() / "update-journey-marker.txt"
    marker.write_text("written before the update\n")
    try:
        seen_before = update_preservation.seed(ORIGIN, target.person_home())
    except update_preservation.PreservationFailure as exc:
        log.step("the person's chats, pin, project, preferences and facts saved in the old version", False, str(exc))
    log.step("the person's chats, pin, project, preferences and facts saved in the old version", True,
             {"chats": {title: len(messages) for title, messages in seen_before["chats"].items()},
              "pinned": seen_before["pinned"], "projects": seen_before["projects"],
              "prefs": seen_before["prefs"], "facts": seen_before["facts"]})
    if args.apt_upgrade:
        apt_upgrade(args, target, log, seen_before)
        return

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

    profile_before = update_preservation.inventory(target.data_dir().parent)
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
        if steps and steps[-1] == "aborted":
            # An aborted update leaves the old version serving: report it now, not after the whole timeout.
            return {"steps": steps, "status": call("/api/update/status")}
        if runtime.get("commit_full") != expect or not steps or steps[-1] not in ("finalized", "rolled_back"):
            return None
        return {"steps": steps, "status": call("/api/update/status")}

    finished = wait_for("the update to finish", serving, args.install_timeout, 2)
    status = finished["status"]
    log.step(f"update journal ended {ending}", finished["steps"][-1] == ending,
             {"steps": finished["steps"], "status": {k: status.get(k) for k in ("phase", "message", "installed_version")}})
    log.step("serving the expected build", target.current_commit() == expect,
             {"expected": expect, "current": target.current_commit()})
    log.step("data from before the update kept", marker.read_text() == "written before the update\n")
    try:
        seen_after = update_preservation.person_view(ORIGIN)
    except update_preservation.PreservationFailure as exc:
        log.step("the person still sees their chats, pin, project, preferences and facts", False, str(exc))
    lost = update_preservation.view_losses(seen_before, seen_after)
    log.step("the person still sees their chats, pin, project, preferences and facts", not lost,
             lost or {"chats": len(seen_after["chats"]), "pinned": seen_after["pinned"]})
    gone, changed = update_preservation.inventory_losses(profile_before, update_preservation.inventory(
        target.data_dir().parent))
    log.step("no profile file, key or database row lost", not gone,
             {"lost": gone, "changed_in_place": changed, "files_before": len(profile_before)})

    if args.platform in ("linux", "linux-deb"):
        relaunch = subprocess.run(target.open_command(), env=target.env, capture_output=True, text=True, timeout=60)
        log.step("opening VOOL again reaches the running app", relaunch.returncode == 0,
                 relaunch.stderr.strip()[-300:])
        # Closed and opened again: the version now serving starts cold and must find the same profile.
        target.stop()
        wait_for("VOOL to close", lambda: not health(), 120)
        target.start()
        wait_for("VOOL to open again", lambda: (health().get("runtime") or {}).get("commit_full") == expect, 300)
        try:
            seen_again = update_preservation.person_view(ORIGIN)
        except update_preservation.PreservationFailure as exc:
            log.step("closed and opened again, the person still sees everything", False, str(exc))
        lost_again = update_preservation.view_losses(seen_before, seen_again)
        log.step("closed and opened again, the person still sees everything", not lost_again,
                 lost_again or {"chats": len(seen_again["chats"]), "commit": expect})
        if args.platform == "linux-deb":
            system_commit = (SYSTEM_PACKAGE / "SOURCE_COMMIT").read_text().strip()
            log.step("opened again from the menu, the in-app update stays: the older copy apt installed is not "
                     "put back", target.current_commit() == expect, {"serving": expect, "apt_installed": system_commit})
    if args.legacy_profile:
        log.step("the pre-rename profile is still the one in use, with no second profile beside it",
                 not target.second_profile.exists() and (target.data_dir() / "update_v2").is_dir(),
                 {"profile": str(target.profile), "second_profile_made": target.second_profile.exists()})
    if server is not None:
        log.step("feed requests", True, log.requests)
    if args.platform == "linux-deb":
        removing_the_deb_keeps_everything(target, log, seen_before, expect)
    target.stop()


def apt_upgrade(args: argparse.Namespace, target: DebPackage, log: Log, seen_before: dict) -> None:
    """apt-get installs a newer .deb; the person's next open from the menu must set that copy up for them."""
    target.stop()
    wait_for("VOOL to close", lambda: not health(), 120)
    profile_before = update_preservation.inventory(target.data_dir().parent)
    own_before = target.current_commit()
    apt("install", "--no-install-recommends", str(args.apt_upgrade.resolve()))
    system_commit = (SYSTEM_PACKAGE / "SOURCE_COMMIT").read_text().strip()
    log.step("apt-get upgraded the copy apt keeps, and left the person's own copy alone",
             system_commit == args.new_commit and target.current_commit() == own_before,
             {"apt_installed": system_commit, "person": target.current_commit()})
    target.start()
    wait_for("VOOL to open from the menu", lambda: (health().get("runtime") or {}).get("commit_full") == args.new_commit,
             300)
    log.step("opened from the menu, the newer copy apt brought is set up for this person",
             target.current_commit() == args.new_commit, {"person": target.current_commit()})
    try:
        seen_after = update_preservation.person_view(ORIGIN)
    except update_preservation.PreservationFailure as exc:
        log.step("the person still sees their chats, pin, project, preferences and facts", False, str(exc))
    lost = update_preservation.view_losses(seen_before, seen_after)
    log.step("the person still sees their chats, pin, project, preferences and facts", not lost,
             lost or {"chats": len(seen_after["chats"]), "pinned": seen_after["pinned"]})
    gone, changed = update_preservation.inventory_losses(profile_before, update_preservation.inventory(
        target.data_dir().parent))
    log.step("no profile file, key or database row lost", not gone,
             {"lost": gone, "changed_in_place": changed, "files_before": len(profile_before)})
    if args.legacy_profile:
        log.step("the pre-rename profile is still the one in use, with no second profile beside it",
                 not target.second_profile.exists() and target.data_dir().is_dir(),
                 {"profile": str(target.profile), "second_profile_made": target.second_profile.exists()})
    removing_the_deb_keeps_everything(target, log, seen_before, args.new_commit)
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
    parser.add_argument("--platform", choices=("linux", "linux-deb", "macos"), required=True)
    parser.add_argument("--old", type=Path, required=True,
                        help="the released package: Linux .tar.gz, Linux .deb (linux-deb) or Mac .dmg")
    parser.add_argument("--apt-upgrade", type=Path, default=None,
                        help="linux-deb: instead of the in-app update, apt-get install this newer .deb")
    parser.add_argument("--feed", default="live", help="'live' or a test feed directory")
    parser.add_argument("--new-version", required=True, help="the version the notice must offer, e.g. 0.7.5")
    parser.add_argument("--new-commit", required=True, help="the build commit the updated app must report")
    parser.add_argument("--expect-notice", choices=("auto", "press"), default="auto")
    parser.add_argument("--broken-update", action="store_true")
    parser.add_argument("--legacy-profile", action="store_true",
                        help="the person's profile is the pre-rename one (~/.nulla_runtime, or NULLA/runtime on a Mac)")
    parser.add_argument("--notice-timeout", type=float, default=180)
    parser.add_argument("--install-timeout", type=float, default=900)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    if args.apt_upgrade is not None and args.platform != "linux-deb":
        parser.error("--apt-upgrade is for --platform linux-deb")
    try:
        journey(args)
    except JourneyFailure as exc:
        print(f"JOURNEY FAILED: {exc}", file=sys.stderr)
        return 1
    print("JOURNEY PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
