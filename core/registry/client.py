"""Explicit, anonymous registry reads. The website cannot select URLs or local paths."""
from __future__ import annotations

import base64
import hashlib
import http.client
import os
import platform
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.registry.codec import (
    COMMIT,
    DOWNLOADS_PAUSED,
    FAILED_SAFETY_CHECKS,
    HEX,
    RegistryError,
    decode,
    require,
    version_key,
)
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
# The team's own publisher name. Only team members can publish under it, so its skills are listed first.
TEAM_PUBLISHER = "vool-team"
# The install pass: the registry sends one with each package download. VOOL keeps it with the installed copy
# and hands it to the website only when the person presses Rate, for that one add-on.
PASS_HEADER = "X-Vool-Review-Pass"
RATE_PATH = "/codex/skills/review"
_PASS = re.compile(r"[A-Za-z0-9_-]{135}")


def _team(package_id: str) -> bool:
    return package_id.split("/")[0] == TEAM_PUBLISHER


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RegistryError("registry redirect refused")


def fetch(url: str, *, limit: int, passes: list | None = None) -> bytes:
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
                urllib.request.Request(url, headers={"User-Agent": "VOOL-registry", "Accept": "application/json"}), timeout=20) as response:
            raw = response.read(limit + 1)
            require(len(raw) <= limit, "registry response exceeds byte limit")
            if passes is not None:
                passes.append(response.headers.get(PASS_HEADER, ""))  # the install pass that comes with a package
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
    def __init__(self, trusted_root: dict, target: dict, *, origin=ORIGIN, request=fetch, now=None, download=None):
        parsed = urllib.parse.urlsplit(origin)
        require(parsed.scheme == "https" and parsed.netloc and not parsed.path and not parsed.query and
                not parsed.fragment and not parsed.username, "registry origin must be a fixed HTTPS origin")
        self.origin, self.target, self.request = origin, target, request
        # Fetches a package and the install pass sent with it: (url, limit=) -> (bytes, pass text).
        self.download = download or (_download if request is fetch else None)
        self.catalog = TrustedCatalog(trusted_root, home() / "config" / STATE_NAME, now=int(time.time()) if now is None else now)

    def close(self):
        self.catalog.close()

    def _presentation(self, rows: list[dict], *, now: int) -> None:
        """Optional display facts, fetched only by Browse; they never authorize installation."""
        from core.registry.codec import digest
        from core.registry.trust import verify

        try:
            value = decode(self.request(self.origin + PREFIX + "/presentation", limit=1024 * 1024), 1024 * 1024)
            require(isinstance(value, dict) and set(value) == {"marks", "metrics"} and
                    isinstance(value["metrics"], list) and len(value["metrics"]) <= 4096, "invalid presentation")
            metrics = {}
            for item in value["metrics"]:
                require(isinstance(item, dict) and set(item) == {"id", "stars", "download_requests"} and
                        isinstance(item["id"], str) and all(type(item[field]) is int and 0 <= item[field] <= 10**12
                        for field in ("stars", "download_requests")), "invalid popularity metric")
                metrics[item["id"]] = {key: item[key] for key in ("stars", "download_requests")}
            curated = {}
            if value["marks"] is not None:
                marks = verify(value["marks"], self.catalog.root, "admission")
                require(marks.get("schema") == "vool.registry.marks.v1" and type(marks.get("sequence")) is int and
                        marks["sequence"] > 0 and type(marks.get("issued_at")) is int and marks["issued_at"] <= now + 300,
                        "invalid editorial marks")
                # Add-ons the team took off the list leave it here too; installed copies are not touched.
                hidden = {item.get("id") for item in marks.get("delisted", []) if isinstance(item, dict)} \
                    if isinstance(marks.get("delisted", []), list) else set()
                rows[:] = [row for row in rows if row["id"] not in hidden]
                curated = marks.get("curation", {})
                require(isinstance(curated, dict) and all(isinstance(curated.get(field, []), list) and
                        len(curated.get(field, [])) <= 24 and all(isinstance(item, str) for item in curated.get(field, []))
                        for field in ("editors_picks", "featured")), "invalid editorial curation")
                with self.catalog.lock, self.catalog.db:
                    self.catalog.db.execute("CREATE TABLE IF NOT EXISTS presentation_state (root INTEGER PRIMARY KEY, sequence INTEGER, digest TEXT)")
                    previous = self.catalog.db.execute("SELECT sequence,digest FROM presentation_state WHERE root=?",
                                                       (self.catalog.root["version"],)).fetchone()
                    require(not previous or marks["sequence"] > previous[0] or
                            (marks["sequence"] == previous[0] and digest(marks) == previous[1]), "editorial marks rollback")
                    self.catalog.db.execute("INSERT OR REPLACE INTO presentation_state VALUES (?,?,?)",
                                            (self.catalog.root["version"], marks["sequence"], digest(marks)))
            for row in rows:
                row.update(metrics.get(row["id"], {}))
                row.update(editors_pick=row["id"] in curated.get("editors_picks", []),
                           featured=row["id"] in curated.get("featured", []))
            rows.sort(key=lambda item: (not _team(item["id"]), not item.get("editors_pick", False),
                                       not item.get("featured", False), -item.get("stars", 0), item["id"]))
        except (RegistryError, OSError, ValueError, TypeError, KeyError):
            return  # An unavailable display feed does not grant a label or change the verified catalog.

    def _ratings(self, rows: list[dict]) -> None:
        """Stars for the cards, fetched only by Browse: the whole list, so the request says nothing about this device."""
        try:
            value = decode(self.request(self.origin + PREFIX + "/ratings/summary", limit=1024 * 1024), 1024 * 1024)
            require(isinstance(value, dict) and isinstance(value.get("ratings"), list) and len(value["ratings"]) <= 4096,
                    "invalid ratings")
            ratings = {}
            for item in value["ratings"]:
                # Fields this build does not know are left alone, so the list can grow later.
                require(isinstance(item, dict) and isinstance(item.get("id"), str) and type(item.get("shown")) is bool and
                        type(item.get("count")) is int and 0 <= item["count"] <= 10**9 and
                        type(item.get("removed", 0)) is int and 0 <= item.get("removed", 0) <= 10**9, "invalid rating")
                average = item.get("average")
                # No average under five counted ratings, whatever the feed says.
                require((item["shown"] and item["count"] >= 5 and type(average) in (int, float) and 1 <= average <= 5) or
                        (not item["shown"] and average is None), "invalid rating")
                ratings[item["id"]] = {"shown": item["shown"], "average": average, "count": item["count"],
                                       "removed": item.get("removed", 0)}
            for row in rows:
                row["rating"] = ratings.get(row["id"], {"shown": False, "average": None, "count": 0, "removed": 0})
        except (RegistryError, OSError, ValueError, TypeError, KeyError):
            for row in rows:
                row.pop("rating", None)  # no feed, no stars: a card never shows a number it could not read

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
        latest = {}
        for row in rows:
            prior = latest.get(row["id"])
            if prior is None or version_key(row["version"]) > version_key(prior["version"]):
                latest[row["id"]] = row
        rows = [latest[package_id] for package_id in sorted(latest, key=lambda package_id: (not _team(package_id), package_id))]
        self._presentation(rows, now=now)
        self._ratings(rows)
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

    def recall(self, *, now: int) -> dict:
        """Read the signed list only to learn what was removed. Shows nothing and installs nothing.

        The request is the one Browse makes. Its consent is the owner's saved daily-check choice, which
        core.registry.recall reads before it calls this.
        """
        snapshot = decode(self.request(self.origin + PREFIX + "/snapshot", limit=4 * 1024 * 1024), 4 * 1024 * 1024)
        value = self.catalog.accept(snapshot, now=now)
        return {"status": "ok", "removed_listed": len(value["revoked"])}

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
        if self.download is not None:
            raw, handed = self.download(self.origin + ticket["url"], limit=entry["artifact_length"])
        else:
            raw, handed = self.request(self.origin + ticket["url"], limit=entry["artifact_length"]), ""
        result = preview(self.catalog, raw, package_id=package_id, version=version, target=self.target, now=now)
        stage = home() / "registry_staging" / (entry["artifact_sha256"] + ".json")
        _write(stage, decode(raw))
        # The pass waits beside the staged copy. It reaches the install receipt only if the install succeeds.
        waiting = stage.with_suffix(".pass")
        if isinstance(handed, str) and _PASS.fullmatch(handed):
            _write(waiting, {"review_pass": handed})
        else:
            waiting.unlink(missing_ok=True)
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
            stage.with_suffix(".pass").unlink(missing_ok=True)  # no install, no pass
            raise
        stage.unlink()
        _keep_pass(result["plugin_id"], stage.with_suffix(".pass"))
        return result


def _download(url: str, *, limit: int) -> tuple[bytes, str]:
    passes: list = []
    raw = fetch(url, limit=limit, passes=passes)
    return raw, passes[0] if passes else ""


def _keep_pass(plugin_id: str, waiting: Path) -> None:
    """After a successful install the pass joins the install receipt, the only place it is kept."""
    from core.registry.native import _receipt_path

    try:
        handed = decode(waiting.read_bytes(), 4096).get("review_pass")
        receipt = decode(_receipt_path(plugin_id).read_bytes(), 65536)
        if isinstance(handed, str) and _PASS.fullmatch(handed) and isinstance(receipt, dict):
            _write(_receipt_path(plugin_id), {**receipt, "review_pass": handed})
    except (OSError, RegistryError, AttributeError):
        pass  # the add-on is installed either way; without a pass there is no Rate button
    finally:
        waiting.unlink(missing_ok=True)


def _rate_pass(package_id: str) -> str:
    """The pass kept for an installed Skill Codex add-on, or nothing. Reads this device only."""
    from core.registry.artifact import native_identity
    from core.registry.native import _receipt_path

    try:
        receipt = decode(_receipt_path(native_identity(package_id)).read_bytes(), 65536)
        handed = receipt.get("review_pass")
        if receipt["admission"]["signed"]["metadata"]["id"] == package_id and isinstance(handed, str) and _PASS.fullmatch(handed):
            return handed
    except (OSError, RegistryError, KeyError, TypeError, AttributeError, ValueError):
        pass
    return ""


def rate(package_id: str) -> dict:
    """The website page where the person rates this add-on, with its install pass after the #.

    Nothing is sent from here. What follows a # never leaves the browser by itself: the page reads it and
    sends the pass only with the rating, after the person signs in. One add-on, one pass, on this click only;
    no other receipt is read.
    """
    handed = _rate_pass(package_id)
    require(bool(handed), "this add-on has no install pass; install it from the Skill Codex to rate it")
    return {"status": "ok", "id": package_id,
            "url": ORIGIN + RATE_PATH + "#" + urllib.parse.urlencode({"id": package_id, "pass": handed})}


CLOSED = "Skill Codex is not open yet in this version of VOOL"


def _anchor() -> Path:
    from core.runtime_paths import PROJECT_ROOT

    return Path(os.environ.get("VOOL_REGISTRY_ROOT_FILE") or Path(PROJECT_ROOT) / "core" / "registry" / "trusted-root.json")


def _read_anchor() -> dict:
    from core.registry.trust import persisted_root, root_document, verify

    try:
        root = decode(_anchor().read_bytes(), 65536)
        require(isinstance(root, dict), CLOSED)
        now = int(time.time())
        document = root_document(root.get("signed"), now, allow_expired=True)
        verify(root, document, "root")
        if document["expires"] <= now:
            return persisted_root(root, home() / "config" / STATE_NAME, now=now)
        return root
    except (OSError, RegistryError, sqlite3.Error, KeyError, TypeError, ValueError):
        raise RegistryError(CLOSED) from None


def is_open() -> bool:
    """Local capability only: no network and no trust-state writes during startup or listing."""
    try:
        _read_anchor()
        return True
    except RegistryError:
        return False


def configured_client() -> Client:
    # Only local configuration selects the trust anchor and the actual release identity.
    root = _read_anchor()
    return Client(root, runtime_target())


def handle(action: str, body: dict) -> dict:
    if action == "registry_recall":
        # The owner's daily-check switch: read it, or save a clear yes or no. No request is made here.
        from core.registry import recall

        if "enabled" in body:
            require(type(body["enabled"]) is bool, "the daily check is either on or off")
            return recall.set_enabled(body["enabled"])
        return recall.status()
    if action == "registry_rate":
        return rate(str(body.get("id", "")))  # a link for the person to open; no request is made here
    client = configured_client()
    try:
        now = int(time.time())
        if action == "registry_refresh":
            result = client.refresh(approved=body.get("approved") is True, now=now)
            for row in result["entries"]:
                row["rate_ready"] = bool(_rate_pass(row["id"]))  # installed from the Skill Codex, with a pass kept
            return result
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
