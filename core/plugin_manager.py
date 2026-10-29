"""Owner-local installation and consent over the existing plugin lifecycle authority."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from core import plugin_lifecycle as lifecycle
from core.plugin_migration import reserved_refusal
from core.plugin_package import PackageError, capture_package
from core.plugin_tools import PluginManifestError, confined_plugin_id, load_manifest, parse_manifest

_LOCK = threading.RLock()


def _state() -> Path:
    from core.runtime_paths import data_path

    result = Path(data_path("plugin-imports"))
    result.mkdir(parents=True, exist_ok=True)
    return result


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex)
    try:
        with temp.open("x", encoding="utf-8") as handle:
            os.chmod(temp, 0o600)
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _remove(path: Path) -> None:
    if path.is_symlink():
        raise lifecycle.LifecycleError("A linked plugin folder was preserved. Choose its actual folder.")
    if not path.exists():
        return
    # Captured packages are read-only, including their directories. Only owned copies enter here.
    for folder, directories, _files in os.walk(path, followlinks=False):
        Path(folder).chmod(0o700)
        if any((Path(folder) / name).is_symlink() for name in directories):
            raise lifecycle.LifecycleError("A changed plugin folder was preserved for review.")
    shutil.rmtree(path)


def _destination(plugin_id: str) -> Path:
    from core.plugin_catalog import configured_plugins_root

    if not confined_plugin_id(plugin_id):
        raise lifecycle.LifecycleError("Choose a valid plugin.")
    parent = configured_plugins_root() / "plugins"
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / plugin_id
    if target.is_symlink() or target.resolve().parent != parent.resolve():
        raise lifecycle.LifecycleError("A linked plugin folder was preserved.")
    return target


def _loaded(plugin_id: str, *, available=False):
    record = lifecycle.record_for(plugin_id)
    if record is None or record.stage == lifecycle.STAGE_UNINSTALLED:
        raise lifecycle.LifecycleError("This plugin is not installed.")
    plugin = load_manifest(Path(record.root) / ".codex-plugin" / "plugin.json")
    if not plugin.vool:
        raise lifecycle.LifecycleError("This package uses the older skill and tool settings.")
    if available and not lifecycle.is_available(plugin_id):
        raise lifecycle.LifecycleError("Enable this verified plugin before connecting to its service.")
    return record, plugin


def summary(plugin) -> dict:
    from core.plugin_executor import plugin_isolation_refusal
    from core.plugin_network import network_available

    block = plugin.vool
    return {"id": plugin.plugin_id, "version": plugin.version,
            **{key: block[key] for key in ("name", "publisher", "description", "homepage", "license", "kind", "network", "credentials")},
            "mcp_servers": list(block.get("mcpServers", {})),
            "process_network_available": network_available(private_environment=any(
                isinstance(spec, dict) and spec.get("env") for spec in block.get("mcpServers", {}).values())),
            "executable_runtime_available": not bool(refusal := plugin_isolation_refusal(plugin)),
            "mac_process_note": sys.platform == "darwin",
            "executable_runtime_refusal": refusal, "executable_runtime_refusal_key": message_key(refusal) if refusal else "",
            "tools": [{"intent": tool.intent, "description": tool.description,
                       "side_effect_class": tool.side_effect_class,
                       "permission_actions": list(tool.permission_actions)} for tool in plugin.contracts]}


def message_key(message: str) -> str:
    return "plugin_message_" + hashlib.sha256(message.encode()).hexdigest()[:12]


GENERIC_FAILURE = "This plugin action could not be completed. Nothing was changed. Review the plugin in Add-ons and try again."


def user_message(message: str) -> dict:
    """Only reviewed, catalogued sentences reach the page; internal detail never does."""
    from core.i18n.catalog import catalog_for

    key = "addons." + message_key(message)
    if key in catalog_for("en").keys:
        return {"message": message, "message_key": key}
    return {"message": GENERIC_FAILURE, "message_key": "addons." + message_key(GENERIC_FAILURE)}


def prepare_local(folder: str, *, update=False, source="local") -> dict:
    """Capture inert bytes once; the owner reviews the resulting complete digest."""
    if not isinstance(folder, str) or not folder or len(folder) > 4096:
        raise lifecycle.LifecycleError("Choose the plugin folder you want to review.")
    review_id = uuid.uuid4().hex
    pending = _state() / "pending"
    pending.mkdir(exist_ok=True)
    stage = pending / review_id
    try:
        captured = capture_package(Path(folder).expanduser(), destination=stage)
        plugin = parse_manifest(captured.manifest, path=stage / ".codex-plugin" / "plugin.json")
        if not plugin.vool:
            raise lifecycle.LifecycleError("Choose a plugin with the current manifest and access declarations.")
        if refusal := reserved_refusal(plugin.plugin_id, captured.digest):
            raise lifecycle.LifecycleError(refusal)
        value = {"review_id": review_id, "digest": captured.digest, "plugin_id": plugin.plugin_id,
                 "created_at": time.time(), "update": update is True, "source": source}
        _write(_state() / "reviews" / (review_id + ".json"), value)
        return {"ok": True, "review_id": review_id, "digest": captured.digest,
                "reviewed_by_store": False, "plugin": summary(plugin),
                "message": "Not reviewed by the VOOL store. Review the entire package before installing it."}
    except Exception:
        _remove(stage)
        raise


def prepare_github(repository: str, revision: str, folder: str, *, approved: bool, update=False) -> dict:
    from core import addon_discovery
    from core.local_scan import scan_bytes

    if approved is not True:
        raise lifecycle.LifecycleError("Approve downloading the public plugin before reviewing it.")
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise lifecycle.LifecycleError("Choose an exact repository version to review.")
    folder = "" if folder in ("", ".") else addon_discovery.safe_path(folder).rstrip("/")
    listing = addon_discovery.repository(repository, revision)
    prefix = folder + "/" if folder else ""
    files = [entry for entry in listing["tree"] if entry.get("type") != "tree" and entry.get("path", "").startswith(prefix)]
    if not files or len(files) > 4096:
        raise lifecycle.LifecycleError("Choose a complete plugin folder within the package size limits.")
    stage = _state() / "downloads" / uuid.uuid4().hex
    stage.mkdir(parents=True)
    reports = []
    total = 0
    try:
        for entry in files:
            if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
                raise lifecycle.LifecycleError("Linked files and nested repositories cannot be imported.")
            name = addon_discovery.safe_path(entry["path"][len(prefix):])
            data = addon_discovery.source_bytes(repository, revision, entry["path"])
            digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data).hexdigest()
            if digest != entry.get("sha"):
                raise lifecycle.LifecycleError("The repository file differs from the selected version.")
            total += len(data)
            if total > 128 * 1024 * 1024:
                raise lifecycle.LifecycleError("The plugin exceeds the package size limit.")
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if entry["mode"] == "100755" else 0o644)
            if target.suffix.lower() in {".md", ".txt", ".json", ".py", ".js", ".sh"}:
                reports.append({"file": name, "report": scan_bytes(name, data)})
        result = prepare_local(str(stage), update=update, source="github:" + listing["repository"] + "@" + revision)
        result["local_checks"] = reports
        result["scan_notice"] = "Checked on this device. No remote scan service was contacted."
        return result
    finally:
        _remove(stage)


def install(review_id: str, *, accepted: bool, digest: str) -> dict:
    if accepted is not True or not re.fullmatch(r"[a-f0-9]{32}", str(review_id)):
        raise lifecycle.LifecycleError("Review and accept this exact package before installing it.")
    with _LOCK:
        try:
            saved = json.loads((_state() / "reviews" / (review_id + ".json")).read_text())
        except (OSError, ValueError):
            raise lifecycle.LifecycleError("The package review is missing. Review the folder again.") from None
        stage = _state() / "pending" / review_id
        captured = capture_package(stage)
        if captured.digest != digest or saved["digest"] != digest or time.time() - saved["created_at"] > 86400:
            raise lifecycle.LifecycleError("The reviewed package changed or its review expired. Review it again.")
        plugin = parse_manifest(captured.manifest, path=stage / ".codex-plugin" / "plugin.json")
        if refusal := reserved_refusal(plugin.plugin_id, captured.digest):
            raise lifecycle.LifecycleError(refusal)
        target = _destination(plugin.plugin_id)
        previous = lifecycle.record_for(plugin.plugin_id)
        active = previous and previous.stage != lifecycle.STAGE_UNINSTALLED
        if active or target.exists():
            if not saved["update"] or not active or Path(previous.root).resolve() != target.resolve() or not previous.source.startswith("plugin:"):
                raise lifecycle.LifecycleError("A plugin with this identity is already installed. Use Update to review a replacement.")
        elif saved["update"]:
            raise lifecycle.LifecycleError("Install the plugin before updating it.")
        backup = _state() / "pending" / (review_id + "-previous")
        moved = False
        try:
            if active:
                lifecycle.disable(plugin.plugin_id)
            if target.exists():
                target.chmod(0o700)
                target.rename(backup)
                backup.chmod(0o555)
            # macOS requires write permission on a directory being moved. Sealing
            # stays in force for all package files, and is restored before verify.
            stage.chmod(0o700)
            stage.rename(target)
            target.chmod(0o555)
            moved = True
            lifecycle.install(plugin.plugin_id, root=target, source="plugin:" + saved["source"])
            record = lifecycle.verify(plugin.plugin_id, expected_digest=digest)
            record.credential_bindings = [f"plugin.{plugin.plugin_id}.{slot}" for slot in plugin.vool["credentials"]]
            lifecycle._save(record)
            _remove(backup)
        except Exception:
            if moved:
                _remove(target)
            if backup.exists():
                backup.chmod(0o700)
                backup.rename(target)
                target.chmod(0o555)
            if previous:
                # Failed replacement preserves bytes, but never silently re-enables them.
                previous.enabled = False
                previous.consented_approval = ""
                previous.network_consent = False
                if previous.stage == lifecycle.STAGE_ENABLED:
                    previous.stage = lifecycle.STAGE_VERIFIED
                lifecycle._save(previous)
            else:
                with contextlib.suppress(lifecycle.LifecycleError):
                    lifecycle.uninstall(plugin.plugin_id)
            raise
        _refresh(plugin.plugin_id)
        return {"ok": True, "plugin": inspect(plugin.plugin_id), "enabled": False}


def inspect(plugin_id: str) -> dict:
    from core.credential_store import credential_is_indexed

    record, plugin = _loaded(plugin_id)
    from core.plugin_providers import cached_models
    available = lifecycle.is_available(plugin_id)
    reason = ""
    if record.verified_digest and lifecycle.manifest_digest(Path(record.root)) != record.verified_digest:
        reason = "The installed plugin changed or its approval is no longer valid. Review it again."
    if record.enabled and not available:
        lifecycle.disable(plugin_id)
        reason = "The installed plugin changed or its approval is no longer valid. Review it again."
        record = lifecycle.record_for(plugin_id)
    return {**summary(plugin), "stage": record.stage, "enabled": available,
            "approval_digest": record.approval_digest, "package_digest": record.verified_digest,
            "reviewed_by_store": record.source.startswith("registry:"), "reason": reason,
            "reason_key": message_key(reason),
            "model_count": len(cached_models(plugin_id)) if "model_provider" in plugin.vool["kind"] else 0,
            "configured_slots": [slot for slot in plugin.vool["credentials"]
                                 if credential_is_indexed(credential_name(plugin_id, slot))]}


def inventory() -> list[dict]:
    result = []
    for record in lifecycle.records():
        if record.stage == lifecycle.STAGE_UNINSTALLED:
            continue
        try:
            result.append(inspect(record.plugin_id))
        except (lifecycle.LifecycleError, PluginManifestError, PackageError, OSError):
            # Legacy skills remain in their existing inventory; malformed service plugins show a reason.
            if not record.source.startswith("plugin:"):
                continue
            if record.enabled:
                lifecycle.disable(record.plugin_id)
            reason = "The plugin manifest is missing or invalid. Review the package again."
            result.append({"id": record.plugin_id, "name": record.plugin_id, "enabled": False,
                           "stage": "invalid", "reason": reason, "reason_key": message_key(reason)})
    return result


def _refresh(plugin_id: str) -> None:
    from core import plugin_catalog, plugin_tools

    lifecycle._invalidate_registration(plugin_id)
    record = lifecycle.record_for(plugin_id)
    if record and record.stage != lifecycle.STAGE_UNINSTALLED:
        plugin_tools.register_plugin(load_manifest(Path(record.root) / ".codex-plugin" / "plugin.json"))
    plugin_catalog.invalidate_storage_listing()


def enable(plugin_id: str, *, accepted: bool, approval_digest: str) -> dict:
    if accepted is not True:
        lifecycle.disable(plugin_id)
        return {"ok": True, "enabled": False, "plugin": inspect(plugin_id)}
    _record, plugin = _loaded(plugin_id)
    if plugin.contracts or plugin.vool.get("mcpServers"):
        from core.plugin_executor import kernel_confinement_available, plugin_isolation_refusal

        if refusal := plugin_isolation_refusal(plugin):
            raise lifecycle.LifecycleError(refusal)
        if not kernel_confinement_available():
            raise lifecycle.LifecycleError("This device cannot safely confine the plugin. It remains disabled.")
    lifecycle.accept_consent(plugin_id, expected_approval=approval_digest, network=bool(plugin.vool["network"]))
    lifecycle.enable(plugin_id)
    try:
        from core.plugin_migration import bind_on_enable

        bind_on_enable(plugin_id)
    except Exception:
        lifecycle.disable(plugin_id)
        raise lifecycle.LifecycleError("The saved service key could not be opened. It is kept unchanged; connect your key in Add-ons and review access again.") from None
    _refresh(plugin_id)
    return {"ok": True, "enabled": True, "plugin": inspect(plugin_id)}


def disable(plugin_id: str) -> dict:
    lifecycle.disable(plugin_id)
    _refresh(plugin_id)
    return {"ok": True, "enabled": False, "plugin": inspect(plugin_id)}


def credential_name(plugin_id: str, slot: str) -> str:
    _record, plugin = _loaded(plugin_id)
    if slot not in plugin.vool["credentials"]:
        raise lifecycle.LifecycleError("Choose one of this plugin's declared key slots.")
    return f"plugin.{plugin_id}.{slot}"


def connect(plugin_id: str, slot: str, value: str) -> dict:
    from core.credential_store import store_credential
    from core.secret_redaction import register_exact_secret

    name = credential_name(plugin_id, slot)
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 8192:
        raise lifecycle.LifecycleError("Paste the service key into its declared slot.")
    register_exact_secret(value.strip())
    store_credential(name, value.strip(), label=plugin_id + " · " + slot)
    return {"ok": True, "plugin": inspect(plugin_id), "message": "Key saved. No connection was made."}


def test_connection(plugin_id: str) -> dict:
    from core.plugin_services import ServiceRefused, native_request

    _record, plugin = _loaded(plugin_id, available=True)
    kind = next((kind for kind in ("scanner", "model_provider") if kind in plugin.vool["kind"]), "")
    if not kind:
        raise ServiceRefused("This plugin does not declare a service connection test.")
    spec = plugin.vool[kind]
    path = spec["test_path"] if kind == "scanner" else spec["model_list_path"]
    response = native_request(plugin_id, kind, "GET", spec["base_url"].rstrip("/") + path)
    try:
        data = response.json()
        if not isinstance(data, dict) or not data or "error" in data:
            raise ServiceRefused("The service did not return readable connection information.")
    except ValueError:
        raise ServiceRefused("The service did not return readable connection information.") from None
    finally:
        response.close()
    return {"ok": True, "message": "Connection checked. The service returned a response."}


def uninstall(plugin_id: str, *, remove_key=True) -> dict:
    from core.credential_store import delete_credential, list_credentials
    from core.plugin_tools import plugin_scratch_dir

    record = lifecycle.record_for(plugin_id)
    if record is None or record.stage == lifecycle.STAGE_UNINSTALLED:
        raise lifecycle.LifecycleError("This plugin is not installed.")
    if type(remove_key) is not bool:
        raise lifecycle.LifecycleError("Choose whether to remove saved keys.")
    target = _destination(plugin_id)
    registry_owned = record.source.startswith("registry:") and bool(_loaded(plugin_id)[1].vool)
    if Path(record.root).resolve() != target.resolve() or not (record.source.startswith("plugin:") or registry_owned):
        raise lifecycle.LifecycleError("This package is managed by its original installation settings.")
    lifecycle.disable(plugin_id)
    if remove_key:
        # Every slot this plugin ever owned, including ones an update dropped from its manifest.
        owned = {name for name in record.credential_bindings if name.startswith(f"plugin.{plugin_id}.")}
        owned |= {str(row.get("name", "")) for row in list_credentials()
                  if str(row.get("name", "")).startswith(f"plugin.{plugin_id}.")}
        for name in sorted(owned):
            delete_credential(name)
    _remove(plugin_scratch_dir(plugin_id))
    _remove(target)
    lifecycle.uninstall(plugin_id)
    _refresh(plugin_id)
    return {"ok": True, "enabled": False, "uninstalled": True}


def handle(action: str, body: dict) -> dict:
    from core.plugin_services import ServiceRefused, ServiceTimeout

    try:
        if action == "plugin_prepare":
            return prepare_local(body.get("path"), update=body.get("update") is True)
        if action == "plugin_github_prepare":
            return prepare_github(body.get("repository"), body.get("revision"), body.get("path", ""),
                                  approved=body.get("approved") is True, update=body.get("update") is True)
        if action == "plugin_install":
            return install(body.get("review_id"), accepted=body.get("accepted") is True, digest=body.get("digest"))
        if action == "plugin_notice_dismiss":
            from core.plugin_migration import dismiss_selection_notice

            return {"ok": True, "dismissed": dismiss_selection_notice()}
        plugin_id = body.get("id")
        if not isinstance(plugin_id, str) or not confined_plugin_id(plugin_id):
            raise lifecycle.LifecycleError("Choose a valid plugin.")
        if action == "plugin_enable":
            return enable(plugin_id, accepted=body.get("accepted") is True, approval_digest=body.get("approval_digest"))
        if action == "plugin_disable":
            return disable(plugin_id)
        if action == "plugin_connect":
            return connect(plugin_id, body.get("slot"), body.get("value"))
        if action == "plugin_test":
            return test_connection(plugin_id)
        if action == "plugin_models":
            from core.plugin_providers import refresh_models

            return refresh_models(plugin_id)
        if action == "plugin_uninstall":
            return uninstall(plugin_id, remove_key=body.get("remove_key", True))
        raise lifecycle.LifecycleError("Choose a supported plugin action.")
    except ServiceTimeout:
        raise lifecycle.LifecycleError("The plugin service timed out. No automatic retry was made.") from None
    except ServiceRefused as exc:
        raise lifecycle.LifecycleError(str(exc)) from None
    except (PackageError, PluginManifestError):
        raise lifecycle.LifecycleError("The plugin manifest or package is invalid. Review it before installing.") from None
