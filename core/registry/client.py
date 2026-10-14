"""Explicit, anonymous registry reads. The website cannot select URLs or local paths."""
from __future__ import annotations

import base64
import hashlib
import http.client
import os
import platform
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.registry.codec import COMMIT, DOWNLOADS_PAUSED, FAILED_SAFETY_CHECKS, HEX, RegistryError, decode, require
from core.registry.media import LIMITS, describe
from core.registry.native import STATE_NAME, _write, home, install, preview
from core.registry.trust import TrustedCatalog

ORIGIN = "https://vool.dev"
PREFIX = "/registry/api"
# Skill icons the verified listing named at the last Browse in this run of VOOL: (id, sha256) -> (path, expires).
# Nothing here survives a restart, so pictures are fetched only after the person pressed Browse.
_LISTED: dict[tuple[str, str], tuple[str, int]] = {}
# Icons already checked since that Browse, kept in memory only (never written to disk).
_PICTURES: dict[tuple[str, str], bytes] = {}
_PICTURE_STATE = {"browsed": False}
_PICTURE_LOCK = threading.Lock()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RegistryError("registry redirect refused")


def fetch(url: str, *, limit: int) -> bytes:
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
                urllib.request.Request(url, headers={"User-Agent": "VOOL-registry", "Accept": "application/json"}), timeout=20) as response:
            raw = response.read(limit + 1)
            require(len(raw) <= limit, "registry response exceeds byte limit")
            return raw
    except urllib.error.HTTPError as error:
        # One refusal is shown as it is, because a person can act on it; any other answer stays generic.
        try:
            answer = decode(error.read(4097), 4096) if error.code == 400 else None
        except (RegistryError, OSError, ValueError, http.client.HTTPException):
            answer = None
        finally:
            error.close()
        if isinstance(answer, dict) and answer.get("error") == DOWNLOADS_PAUSED:
            raise RegistryError(DOWNLOADS_PAUSED) from None
        raise RegistryError("registry unavailable; existing discovery and local skills remain available") from None
    except (urllib.error.URLError, OSError):
        raise RegistryError("registry unavailable; existing discovery and local skills remain available") from None


def runtime_target() -> dict:
    from core.runtime_paths import PROJECT_ROOT

    # The existing release builder already emits this immutable provenance file.
    # A dirty dev checkout cannot pretend to be a compatibility-tested release.
    try:
        manifest = decode((Path(PROJECT_ROOT).parent / "BUILD_MANIFEST.json").read_bytes(), 65536)
    except OSError:
        raise RegistryError("this installation has no verified release build identity") from None
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64" if machine in {"x86_64", "amd64"} else "unsupported"
    require(manifest.get("schema") == "vool-build-manifest/1" and manifest.get("release") is True and
            manifest.get("source_tree_clean") is True and manifest.get("natively_verified") is True and
            bool(COMMIT.fullmatch(str(manifest.get("exact_sha")))) and
            manifest.get("platform") == "macos" and platform.system() == "Darwin" and
            manifest.get("arch") == arch, "registry compatibility requires the actual released platform build")
    return {"version": manifest["version"], "commit": manifest["exact_sha"], "platform": "macos-" + arch}


class Client:
    def __init__(self, trusted_root: dict, target: dict, *, origin=ORIGIN, request=fetch, now=None):
        parsed = urllib.parse.urlsplit(origin)
        require(parsed.scheme == "https" and parsed.netloc and not parsed.path and not parsed.query and
                not parsed.fragment and not parsed.username, "registry origin must be a fixed HTTPS origin")
        self.origin, self.target, self.request = origin, target, request
        self.catalog = TrustedCatalog(trusted_root, home() / "config" / STATE_NAME, now=int(time.time()) if now is None else now)

    def close(self):
        self.catalog.close()

    def refresh(self, *, approved: bool, now: int) -> dict:
        require(approved is True, "explicit registry network consent required")
        snapshot = decode(self.request(self.origin + PREFIX + "/snapshot", limit=4 * 1024 * 1024), 4 * 1024 * 1024)
        value = self.catalog.accept(snapshot, now=now)
        rows = []
        for envelope in value["entries"]:
            info = envelope["signed"]["metadata"]
            try:
                entry = self.catalog.resolve(info["id"], info["version"], target=self.target, now=now)
            except RegistryError:
                continue
            rows.append({**info, "reviewed": True, "import_ready": True, "discovery_only": False,
                         "sha256": entry["artifact_sha256"], "targets": entry["targets"]})
        with _PICTURE_LOCK:
            # A new Browse forgets earlier pictures, so a picture whose approval was taken away stops showing.
            _PICTURES.clear()
            _LISTED.clear()
            _LISTED.update({(row["id"], row["icon"]["sha256"]): (row["icon"]["path"], value["expires"])
                            for row in rows if "icon" in row})
            _PICTURE_STATE["browsed"] = True
        from core.registry.requirements import public_requirements

        # Reporting happens on the website, after the person picks a reason; VOOL only opens the page.
        return {"status": "ok", "entries": rows, "expires": value["expires"], "target": self.target,
                "requirements": public_requirements(), "report_page": self.origin + "/codex/skills/report"}

    def picture(self, package_id: str, sha256: str, *, approved: bool, now: int) -> dict:
        """A listed skill's approved icon, checked against the signed listing from this run's Browse."""
        require(approved is True, "explicit registry network consent required")
        require(bool(HEX.fullmatch(sha256)), "invalid picture")
        with _PICTURE_LOCK:
            browsed, listed, cached = _PICTURE_STATE["browsed"], _LISTED.get((package_id, sha256)), _PICTURES.get((package_id, sha256))
        require(browsed, "browse Skill Codex first")
        require(listed is not None, "picture is not part of a listed skill")
        path, expires = listed
        require(now < expires, "the Skill Codex listing is out of date; browse again")
        if cached is None:
            # The server checks that a person approved this picture for this skill.
            data = self.request(self.origin + PREFIX + "/pictures/" + package_id + "/" + sha256, limit=LIMITS["icon"][0])
            require(hashlib.sha256(data).hexdigest() == sha256, "picture differs from the signed listing")
            describe("icon", path, data)
            cached = data
            with _PICTURE_LOCK:
                _PICTURES[(package_id, sha256)] = data
                while len(_PICTURES) > 64:
                    _PICTURES.pop(next(iter(_PICTURES)))
        return {"status": "ok", "id": package_id, "sha256": sha256, "type": "image/png",
                "data": base64.b64encode(cached).decode("ascii")}

    def _ticket(self, entry: dict, package_id: str, version: str) -> dict:
        # The registry refuses a ticket while downloads of the skill are paused; asking does not count as a download.
        query = urllib.parse.urlencode({"id": package_id, "version": version})
        ticket = decode(self.request(self.origin + PREFIX + "/download?" + query, limit=65536), 65536)
        require(isinstance(ticket, dict) and ticket.get("sha256") == entry["artifact_sha256"] and
                ticket.get("length") == entry["artifact_length"], "download ticket differs from admission")
        return ticket

    def prepare(self, package_id: str, version: str, *, approved: bool, now: int) -> dict:
        require(approved is True, "explicit package download consent required")
        entry = self.catalog.resolve(package_id, version, target=self.target, now=now)
        ticket = self._ticket(entry, package_id, version)
        parsed = urllib.parse.urlsplit(str(ticket.get("url", "")))
        params = urllib.parse.parse_qs(parsed.query, max_num_fields=2)
        require(not parsed.scheme and not parsed.netloc and not parsed.fragment and
                parsed.path == PREFIX + "/artifacts/" + entry["artifact_sha256"] and
                set(params) == {"ticket"} and len(params["ticket"]) == 1 and
                0 < len(params["ticket"][0]) <= 128, "unexpected artifact URL")
        raw = self.request(self.origin + ticket["url"], limit=entry["artifact_length"])
        result = preview(self.catalog, raw, package_id=package_id, version=version, target=self.target, now=now)
        stage = home() / "registry_staging" / (entry["artifact_sha256"] + ".json")
        _write(stage, decode(raw))
        return {"status": "review", "review_id": entry["artifact_sha256"], **result}

    def accept(self, package_id: str, version: str, review_id: str, *, accepted: bool, enable: bool, now: int) -> dict:
        entry = self.catalog.resolve(package_id, version, target=self.target, now=now)
        require(review_id == entry["artifact_sha256"], "review does not name this exact package version")
        if accepted:
            # A skill paused after it was downloaded for inspection is not installed from the local copy either.
            self._ticket(entry, package_id, version)
        stage = home() / "registry_staging" / (entry["artifact_sha256"] + ".json")
        try:
            raw = stage.read_bytes()
        except OSError:
            raise RegistryError("package has not been downloaded for local review") from None
        try:
            result = install(self.catalog, raw, package_id=package_id, version=version, target=self.target,
                             accepted=accepted, enable=enable, now=now)
        except RegistryError as exc:
            if str(exc) == FAILED_SAFETY_CHECKS:
                stage.unlink(missing_ok=True)  # a copy VOOL's own checks refused is not kept either
            raise
        stage.unlink()
        return result


def configured_client() -> Client:
    from core.runtime_paths import PROJECT_ROOT

    # An optional owner-installed anchor is local configuration. The HTTP API
    # cannot pass a root, URL, path, target build or a trust override.
    anchor = Path(os.environ.get("VOOL_REGISTRY_ROOT_FILE") or Path(PROJECT_ROOT) / "core" / "registry" / "trusted-root.json")
    try:
        root = decode(anchor.read_bytes(), 65536)
    except OSError:
        raise RegistryError("Skill Codex is not open yet in this version of VOOL") from None
    return Client(root, runtime_target())


def handle(action: str, body: dict) -> dict:
    client = configured_client()
    try:
        now = int(time.time())
        if action == "registry_refresh":
            return client.refresh(approved=body.get("approved") is True, now=now)
        if action == "registry_picture":
            return client.picture(str(body.get("id", "")), str(body.get("sha256", "")),
                                  approved=body.get("approved") is True, now=now)
        if action == "registry_prepare":
            return client.prepare(str(body.get("id", "")), str(body.get("version", "")),
                                  approved=body.get("approved") is True, now=now)
        return client.accept(str(body.get("id", "")), str(body.get("version", "")), str(body.get("review_id", "")),
                             accepted=body.get("accepted") is True, enable=body.get("enable") is True, now=now)
    finally:
        client.close()
