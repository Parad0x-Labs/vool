"""Curated external skills: inert download, scan, explicit review, existing lifecycle.

Only reviewed standalone SKILL.md packs are supported here. No arbitrary URLs, archive
extraction, install scripts, automatic scans or private workspace uploads.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from core.addon_catalog import CATALOG
from core.eyebrow_client import (
    KEY_NAME,
    AddonError,
    fetch_bytes,
    request_api,
    scan_skill,
    valid_version_report,
    validate_report,
)

_LOCK = threading.RLock()
PREFIX = "discover-"
_LOCAL_REPORT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,99}\Z")
# Every path component this lane builds from a request- or receipt-supplied string goes through
# _safe_component: basename first (a separator or '..' can never survive it), then a strict
# allow-list fullmatch. The saved receipts are additionally MAC-verified on read, so a forged
# entry cannot smuggle a component past this barrier either.
_COMPONENT_ID = r"[a-z0-9][a-z0-9-]{0,63}"


def _safe_component(value: object, pattern: str, code: str, message: str, status: int | None = None) -> str:
    name = os.path.basename(str(value))
    if not re.fullmatch(pattern, name):
        if status is None:
            raise AddonError(code, message)
        raise AddonError(code, message, status)
    return name


def _lookup(folder: str, name: str) -> Path | None:
    """Resolve an identifier to a stored file by enumerating the folder and matching its
    OWN entry names. The caller's string is only ever COMPARED -- it is never joined into
    a path -- so no request-supplied spelling can steer where the path points."""
    directory = _root() / folder
    if not directory.is_dir():
        return None
    target = name + ".json"
    for path in directory.iterdir():
        if path.name == target:
            return path
    return None


def _root() -> Path:
    from core.runtime_paths import data_path
    return Path(data_path("addon-reviews"))


def _key() -> bytes:
    from network.signer import derive_local_secret
    return derive_local_secret("vool-addon-review-v1", length=32)


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = _canonical({"value": value, "mac": hmac.new(_key(), _canonical(value), hashlib.sha256).hexdigest()})
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 3 * 1024 * 1024:
            raise ValueError()
        envelope = json.loads(path.read_bytes())
        value = envelope["value"]
        if not hmac.compare_digest(envelope["mac"], hmac.new(_key(), _canonical(value), hashlib.sha256).hexdigest()):
            raise ValueError()
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise AddonError("review_untrusted", "The saved review is missing or changed. Scan the add-on again.") from None


def _entry(identifier: str) -> dict:
    identifier = _safe_component(identifier, r"github-[a-f0-9]{24}|" + _COMPONENT_ID,
                                 "addon_unknown", "This add-on is not in the curated catalogue.", 404)
    if re.fullmatch(r"github-[a-f0-9]{24}", identifier):
        path = _lookup("discovered", identifier)
        if path is not None:
            value = _read(path)
            if value.get("id") != identifier:
                raise AddonError("review_untrusted", "The saved source identity changed. Inspect it again.")
            return value
    for entry in CATALOG:
        if entry["id"] == identifier:
            return dict(entry)
    raise AddonError("addon_unknown", "This add-on is not in the curated catalogue.", 404)


def _entries() -> list[dict]:
    rows = {item["id"]:dict(item) for item in CATALOG}
    folder = _root() / "discovered"
    if folder.is_dir():
        for path in sorted(folder.glob('github-*.json'))[:500]:
            try:
                item = _entry(path.stem)
                rows[item['id']] = item
            except AddonError:
                continue
    return list(rows.values())


def is_managed(plugin_id: str) -> bool:
    return plugin_id.startswith(PREFIX)


def _installed_receipt(plugin_id: str, *, create: bool = False) -> Path:
    plugin_id = _safe_component(plugin_id, r"discover-[a-z0-9][a-z0-9-]{0,99}",
                                "addon_unknown", "Invalid add-on identity.")
    found = _lookup("installed", plugin_id)
    if found is not None:
        return found
    if not create:
        raise AddonError("review_untrusted", "The saved review is missing or changed. Scan the add-on again.")
    return _root() / "installed" / (plugin_id + ".json")


def assert_reviewed(plugin_id: str, pack: Path) -> None:
    """Extra evidence at the existing verification owner; never an execution permission."""
    from core.plugin_lifecycle import manifest_digest
    record = _read(_installed_receipt(plugin_id))
    if (record.get("plugin_id") != plugin_id or not _accepted_review(record)
            or record.get("pack_digest") != manifest_digest(pack)):
        raise AddonError("addon_changed", "The add-on changed since its security review. A new scan is required.")
    # The initial format has no executable/resource files. Refuse additions outside its contract.
    allowed = {".codex-plugin/plugin.json", "skills/" + record["entry"]["skill_name"] + "/SKILL.md", "LICENSE.txt"}
    if any(p.is_symlink() or (p.is_file() and p.relative_to(pack).as_posix() not in allowed) for p in pack.rglob("*")):
        raise AddonError("addon_changed", "Files were added outside the reviewed standalone skill. A new review is required.")
    if hashlib.sha256((pack / "LICENSE.txt").read_bytes()).hexdigest() != record["entry"]["license_sha256"]:
        raise AddonError("addon_changed", "The add-on’s licence file changed after review.")


def _accepted_review(receipt: dict) -> bool:
    """A scan verdict and the owner's version-specific decision remain separate truths."""
    report = validate_report(receipt['entry']['skill_name'], receipt['report'])
    if report['eligible']:
        return True  # Includes existing, authenticated clean-scan installation receipts.
    decision = receipt.get('acceptance', {})
    return (decision.get('mode') == 'risks_accepted'
            and decision.get('acknowledged') is True
            and decision.get('review_id') == receipt.get('review_id')
            and decision.get('source_sha256') == receipt['entry']['sha256']
            and decision.get('report_sha256') == hashlib.sha256(_canonical(receipt['report'])).hexdigest()
            and isinstance(decision.get('accepted_at'), (int, float)) and decision['accepted_at'] > 0)


def available(plugin_id: str, pack: Path) -> bool:
    from core.plugin_lifecycle import is_available
    try:
        assert_reviewed(plugin_id, pack)
        return is_available(plugin_id, root=pack)
    except Exception:
        return False


def reviewed_skill(plugin_id: str, pack: Path):
    """Load the exact scanned snapshot, including when a file changes during a read."""
    from core.plugin_skills import parse_skill_content
    try:
        plugin_id = _safe_component(plugin_id, r"discover-[a-z0-9][a-z0-9-]{0,99}",
                                    "addon_unknown", "Invalid add-on identity.")
        if not available(plugin_id, pack):
            return None
        receipt = _read(_installed_receipt(plugin_id))
        skill_name = _safe_component(receipt["entry"]["skill_name"], _COMPONENT_ID,
                                     "review_untrusted", "The saved review is missing or changed. Scan the add-on again.")
        path = pack / "skills" / skill_name / "SKILL.md"
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != receipt["entry"]["sha256"]:
            return None
        # Provenance sent to a model identifies the add-on, not the user's home directory.
        shown = Path("addons") / plugin_id / skill_name / "SKILL.md"
        return parse_skill_content(content.decode("utf-8"), path=shown, plugin_id=plugin_id)
    except Exception:
        return None


def _local_report_path(identifier: str, *, create: bool = False) -> Path:
    identifier = _safe_component(identifier, _LOCAL_REPORT_ID.pattern,
                                 "addon_unknown", "This add-on is not in the curated catalogue.", 404)
    found = _lookup("local-reports", identifier)
    if found is not None:
        return found
    if not create:
        raise AddonError("addon_unknown", "No local check was saved for this add-on.", 404)
    return _root() / "local-reports" / (identifier + ".json")


def local_check(identifier: str) -> dict:
    """One VOOL-local static check of the exact pinned bytes; advisory evidence only.

    No Eyebrow key or call, no installation effect and no permission change. The
    bytes come from the same integrity-checked source owner as the Eyebrow scan;
    bundled sources make this check entirely offline.
    """
    entry = _entry(identifier)
    if entry.get('discovery_only') or entry.get('import_ready') is False:
        raise AddonError('addon_needs_compatibility',
                         'Inspect this skill’s licence and compatibility before checking it. ' + entry.get('compatibility', ''))
    from core.local_scan import scan_bytes
    content = _download(entry, "SKILL.md", entry["sha256"])
    report = scan_bytes(entry["skill_name"], content)
    record = {"id": entry["id"], "source_sha256": entry["sha256"], "checked_at": time.time(), "report": report}
    with _LOCK:
        _write(_local_report_path(entry["id"], create=True), record)
    return {"ok": True, "state": "current", "local_check": {"checked_at": record["checked_at"], **report}}


def local_report_saved(identifier: str) -> dict:
    """Show a saved local report only while it still describes the current source
    bytes AND was produced by the current scanner/ruleset; otherwise ask for a
    recheck. Eyebrow reports, installation receipts and permissions live in
    separate records and are never altered by this staleness."""
    from core import local_scan
    entry = _entry(identifier)
    try:
        record = _read(_local_report_path(identifier))
    except AddonError:
        return {"ok": True, "state": "none"}
    if record.get("id") != identifier:
        return {"ok": True, "state": "none"}
    if record.get("source_sha256") != entry.get("sha256"):
        return {"ok": True, "state": "stale", "reason": "source_changed",
                "message": "The add-on source changed since this local check. Run it again before relying on it."}
    saved = record.get("report") or {}
    if (saved.get("scanner_version") != local_scan.SCANNER_VERSION
            or saved.get("ruleset_version") != local_scan.RULESET_VERSION):
        return {"ok": True, "state": "stale", "reason": "scanner_updated",
                "message": "Check again — scanner updated."}
    return {"ok": True, "state": "current", "local_check": {"checked_at": record["checked_at"], **saved}}


def catalog() -> dict:
    from core.credential_store import credential_is_indexed
    from core.plugin_catalog import _disabled_ids
    from core.plugin_lifecycle import record_for
    rows = []
    for item in _entries():
        row = dict(item)
        plugin_id = PREFIX + item["id"]
        record = record_for(plugin_id)
        row.update(plugin_id=plugin_id, installed=bool(record and record.stage != "uninstalled"), enabled=False, scan="not_checked")
        if row["installed"]:
            row["enabled"] = plugin_id not in _disabled_ids() and available(plugin_id, Path(record.root))
            row["stage"] = record.stage
            try:
                assert_reviewed(plugin_id, Path(record.root))
                receipt = _read(_installed_receipt(plugin_id))
                mode = receipt.get('acceptance', {}).get('mode', 'scan_passed')
                row.update(scan="risks_accepted" if mode == 'risks_accepted' else "checked",
                           checked_at=receipt["checked_at"], engine=receipt["report"]["engine"],
                           scan_verdict=receipt['report']['verdict'], acceptance=receipt.get('acceptance', {}))
            except Exception:
                row["scan"] = "changed_or_unverifiable"
        rows.append(row)
    pending = []
    # A scan belongs to the runtime, not a browser tab. Reopening never rescans.
    folder = _root() / "pending"
    if folder.is_dir():
        for path in sorted(folder.iterdir(), key=lambda p: p.name):
            if len(pending) >= 50:
                break
            if not re.fullmatch(r"[a-f0-9]{32}", path.name):
                continue
            try:
                saved = review_saved(path.name)
                pending.append({k: saved[k] for k in ("review_id", "entry", "checked_at", "can_install", "reason")})
            except AddonError:
                continue
    pending.sort(key=lambda item: item["checked_at"], reverse=True)
    return {"entries": rows, "reviews": pending, "eyebrow": {"configured": credential_is_indexed(KEY_NAME)}, "supported": "Standalone instruction skills. Executable plugin and MCP imports are not offered yet."}


def security_action(action: str, value: str = "") -> dict:
    if action == "save_key":
        raise AddonError('shared_key_setup_required','Add or replace this key in Settings → API Keys.',409)
    if action == "remove_key":
        from core.credential_intelligence.provider_registry import default_registry
        from core.credential_intelligence.store import CredentialStore
        CredentialStore(default_registry()).delete('eyebrow')
        return {"ok": True, "message": "Eyebrow key removed."}
    if action == "test_key":
        report, quota = request_api("/v1/version")
        if not valid_version_report(report):
            raise AddonError("eyebrow_invalid_report", "Eyebrow did not return version information.")
        return {"ok": True, "message": "Eyebrow accepted the key.", "engine": str(report["engine"].get("version", ""))[:80], "quota_remaining": quota}
    raise AddonError("unsupported_action", "Unknown security action.")


def _download(entry: dict, filename: str, expected: str) -> bytes:
    # All URL components are shipped catalogue constants, never caller-supplied paths.
    try:
        shipped = next((item for item in CATALOG if item['id'] == entry['id']
                        and item.get('bundled_source') and all(item.get(key) == entry.get(key)
                        for key in ('repository','ref','sha256','license_sha256'))), None)
        if entry.get('bundled_source') or shipped:
            from core.addon_packages import source
            data = source(expected)
        elif entry.get('skill_path'):
            from core.addon_discovery import source_bytes
            path = entry['skill_path'] if filename == 'SKILL.md' else entry['license_path']
            data = source_bytes(entry['repository'], entry['ref'], path)
        else:
            url = "https://raw.githubusercontent.com/anthropics/skills/" + entry["ref"] + "/" + entry["path"] + "/" + filename
            data, _ = fetch_bytes(url, limit=128 * 1024)
    except AddonError:
        raise
    except Exception:
        raise AddonError("addon_download_failed", "Could not download the pinned public add-on. Nothing was installed.", 502) from None
    if hashlib.sha256(data).hexdigest() != expected:
        raise AddonError("addon_source_changed", "Downloaded contents do not match the reviewed catalogue version. Nothing was installed.")
    return data


def prepare(identifier: str, *, approved: bool) -> dict:
    if approved is not True:
        raise AddonError("scan_consent_required", "Approve downloading this public skill and sending it to Eyebrow for one scan first.")
    entry = _entry(identifier)
    if entry.get('discovery_only') or entry.get('import_ready') is False:
        raise AddonError('addon_needs_compatibility', 'Inspect this skill’s licence and compatibility before scanning. ' + entry.get('compatibility', ''))
    from core.credential_store import has_credential
    if not has_credential(KEY_NAME):
        raise AddonError("eyebrow_key_missing", "Add your Eyebrow key in Settings → API Keys first.")
    content = _download(entry, "SKILL.md", entry["sha256"])
    licence = _download(entry, "LICENSE.txt", entry["license_sha256"])
    with _LOCK:
        stage = _root() / "pending" / uuid.uuid4().hex
        stage.mkdir(parents=True, mode=0o700)
        try:
            (stage / "SKILL.md").write_bytes(content)
            (stage / "LICENSE.txt").write_bytes(licence)
            from core.plugin_skills import _FRONTMATTER_RE, _MAX_BODY_CHARS
            from core.skill_tools import validate_skill
            text = content.decode("utf-8")
            match = _FRONTMATTER_RE.match(text)
            if not match or len(match.group(2).strip()) > _MAX_BODY_CHARS:
                raise AddonError("addon_incompatible", "This skill cannot be loaded in full by this VOOL version.")
            result = validate_skill(str(stage / "SKILL.md"))
            if result.get("status") != "ok":
                raise AddonError("addon_incompatible", "This skill requires a format or tools unavailable in this VOOL version.")
            report = scan_skill(entry["skill_name"], text)
            receipt = {"entry": entry, "report": report, "checked_at": time.time(), "review_id": stage.name}
            _write(stage / "review.json", receipt)
            return review_saved(stage.name)
        except Exception:
            shutil.rmtree(stage)
            raise


def _stage(review_id: str) -> Path:
    review_id = _safe_component(review_id, r"[a-f0-9]{32}", "invalid_review", "Invalid review identity.")
    pending = _root() / "pending"
    if pending.is_dir():
        for path in pending.iterdir():
            if path.name == review_id:   # compare only; the path itself comes from the listing
                if path.is_symlink():
                    raise AddonError("invalid_review", "Invalid review location.")
                return path
    raise AddonError("invalid_review", "Invalid review identity.")


def reject(review_id: str) -> dict:
    with _LOCK:
        stage = _stage(review_id)
        if stage.exists():
            shutil.rmtree(stage)
    return {"ok": True, "message": "Review discarded. Nothing was installed."}


def _review_identity(entry: dict) -> dict:
    # Shipping an identical public file locally changes its transport, not the
    # version the owner reviewed. Older ready entries omitted these default flags.
    identity = dict(entry, import_ready=entry.get('import_ready', True),
                    discovery_only=entry.get('discovery_only', False))
    identity.pop('bundled_source', None)
    return identity


def _review_material(stage: Path, receipt: dict) -> tuple[bytes, bytes, dict]:
    """One evidence/compatibility gate for review display and both installation decisions."""
    if time.time() - receipt["checked_at"] > 86400:
        raise AddonError("review_expired", "This review is over 24 hours old. Scan again before installing.")
    entry = receipt["entry"]
    try:
        current = _entry(entry["id"])
    except AddonError:
        raise AddonError("addon_source_unavailable", "VOOL can’t verify where this add-on came from. Find it again in Browse, then scan before installing.") from None
    if _review_identity(entry) != _review_identity(current):
        raise AddonError("addon_source_changed", "The selected version changed. Inspect and scan that version before installing.")
    if entry.get('discovery_only') or entry.get('import_ready') is False:
        raise AddonError("addon_incompatible", "This package needs compatibility or licence work before it can be installed.")
    report = validate_report(entry['skill_name'], receipt['report'])
    files = []
    try:
        for name, digest in (("SKILL.md", "sha256"), ("LICENSE.txt", "license_sha256")):
            path = stage / name
            if path.is_symlink():
                raise AddonError("addon_changed", "A downloaded file was replaced with a link. Scan the original files again.")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != entry[digest]:
                raise AddonError("addon_changed", "The downloaded files changed after the scan. Scan again.")
            files.append(content)
    except OSError:
        raise AddonError("addon_files_missing", "The downloaded files are missing or unreadable. Download and scan again.") from None
    return files[0], files[1], report


def review_saved(review_id: str) -> dict:
    """Eyebrow findings are evidence; VOOL reports its separate installation checks."""
    with _LOCK:
        stage = _stage(review_id)
        receipt = _read(stage / "review.json")
        try:
            _, _, report = _review_material(stage, receipt)
        except AddonError as exc:
            return {"ok": True, **receipt, "can_install": False, "can_override": False,
                    "reason": exc.message,
                    "vool_check": {"status": "blocked", "code": exc.code, "message": exc.message}}
        clear = report['eligible']
        message = ("The scanned files are unchanged and this standalone skill can be installed."
                   if clear else "The scanned files are unchanged and the format is supported. VOOL requires an explicit risk acceptance because the Eyebrow report is not clear.")
        return {"ok": True, **receipt, "can_install": clear, "can_override": not clear,
                "reason": "" if clear else "Eyebrow reported findings or a failed scan policy. Installation requires your separate risk acceptance.",
                "vool_check": {"status": "passed", "code": "ready" if clear else "risk_acceptance_required", "message": message}}


def install_review(review_id: str, *, accepted: bool, risk_override: bool = False,
                   risk_acknowledged: bool = False) -> dict:
    from core import plugin_lifecycle as lifecycle
    from core.plugin_catalog import configured_plugins_root, invalidate_storage_listing
    with _LOCK:
        if accepted is not True:
            raise AddonError("install_consent_required", "Review the findings and accept installation first.")
        stage = _stage(review_id)
        receipt = _read(stage / "review.json")
        content, licence, report = _review_material(stage, receipt)
        if not report['eligible'] and not (risk_override is True and risk_acknowledged is True):
            raise AddonError("risk_acceptance_required", "Review Eyebrow's findings and explicitly acknowledge the risks for this version before installing.")
        entry = receipt["entry"]
        receipt['acceptance'] = {
            'mode': 'scan_passed' if report['eligible'] else 'risks_accepted',
            'acknowledged': True, 'accepted_at': time.time(), 'review_id': review_id,
            'source_sha256': entry['sha256'],
            'report_sha256': hashlib.sha256(_canonical(receipt['report'])).hexdigest(),
        }
        # Receipt fields are MAC-verified, but the components still cross this barrier so a
        # forged-or-corrupt receipt cannot steer a path even if its MAC somehow matched.
        safe_entry_id = _safe_component(entry["id"], r"github-[a-f0-9]{24}|" + _COMPONENT_ID,
                                        "addon_changed", "The reviewed add-on identity is not usable.")
        safe_skill_name = _safe_component(entry["skill_name"], _COMPONENT_ID,
                                          "addon_changed", "The reviewed skill name is not usable.")
        plugin_id = PREFIX + safe_entry_id
        target = configured_plugins_root() / "plugins" / plugin_id
        if target.exists():
            raise AddonError("addon_already_installed", "This add-on is already present. Existing files were preserved.")
        # Assemble without executing anything; the reserved namespace is unavailable until
        # the lifecycle and authenticated review both permit it, including partial failures.
        skill = target / "skills" / safe_skill_name / "SKILL.md"
        try:
            skill.parent.mkdir(parents=True)
            skill.write_bytes(content)
            (target / "LICENSE.txt").write_bytes(licence)
            manifest = {"name": plugin_id, "version": entry["ref"][:12], "skills": "./skills/", "tools": [], "permissions": [],
                        "author": {"name": entry["publisher"]}, "interface": {"displayName": entry["name"], "shortDescription": entry["description"], "category": entry["category"]}}
            (target / ".codex-plugin").mkdir()
            (target / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest, sort_keys=True))
            receipt.update(plugin_id=plugin_id, pack_digest=lifecycle.manifest_digest(target))
            _write(_installed_receipt(plugin_id, create=True), receipt)
            lifecycle.install(plugin_id, root=target, source="eyebrow-catalog:" + safe_entry_id)
            lifecycle.verify(plugin_id, root=target, expected_digest=receipt["pack_digest"])
            lifecycle.enable(plugin_id)
            invalidate_storage_listing()
            from core.tool_offer_assembly import reset_skill_cache
            reset_skill_cache()
        except Exception:
            if lifecycle.record_for(plugin_id):
                lifecycle.disable(plugin_id)
            raise
        shutil.rmtree(stage)
        return {"ok": True, "plugin_id": plugin_id, "acceptance": receipt["acceptance"], "message": "Installed and enabled. Normal VOOL permissions still apply."}
