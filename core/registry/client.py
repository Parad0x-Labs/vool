"""Explicit, anonymous registry reads. The website cannot select URLs or local paths."""
from __future__ import annotations

import os
import platform
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.registry.codec import COMMIT, RegistryError, decode, require
from core.registry.native import STATE_NAME, _write, home, install, preview
from core.registry.trust import TrustedCatalog

ORIGIN = "https://vool.dev"
PREFIX = "/registry/api"


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
                         "sha256": entry["artifact_sha256"]})
        return {"status": "ok", "entries": rows, "expires": value["expires"], "target": self.target}

    def prepare(self, package_id: str, version: str, *, approved: bool, now: int) -> dict:
        require(approved is True, "explicit package download consent required")
        entry = self.catalog.resolve(package_id, version, target=self.target, now=now)
        query = urllib.parse.urlencode({"id": package_id, "version": version})
        ticket = decode(self.request(self.origin + PREFIX + "/download?" + query, limit=65536), 65536)
        require(isinstance(ticket, dict) and ticket.get("sha256") == entry["artifact_sha256"] and
                ticket.get("length") == entry["artifact_length"], "download ticket differs from admission")
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
        stage = home() / "registry_staging" / (entry["artifact_sha256"] + ".json")
        try:
            raw = stage.read_bytes()
        except OSError:
            raise RegistryError("package has not been downloaded for local review") from None
        result = install(self.catalog, raw, package_id=package_id, version=version, target=self.target,
                         accepted=accepted, enable=enable, now=now)
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
        if action == "registry_prepare":
            return client.prepare(str(body.get("id", "")), str(body.get("version", "")),
                                  approved=body.get("approved") is True, now=now)
        return client.accept(str(body.get("id", "")), str(body.get("version", "")), str(body.get("review_id", "")),
                             accepted=body.get("accepted") is True, enable=body.get("enable") is True, now=now)
    finally:
        client.close()
