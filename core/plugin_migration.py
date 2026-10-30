"""Retired service settings stay local; only explicit native-plugin enablement rebinds a key."""
from __future__ import annotations

from urllib.parse import urlsplit

SERVICES = (
    {"id": "vool-plugin-usepod", "name": "UsePod", "slot": "llm.cloud.usepod",
     "origin_slot": "llm.cloud.usepod_origin", "origin": "https://api.usepod.ai", "kind": "model_provider",
     "rebind": False,
     "digests": frozenset({"fe7fedb6881f6ba74df98b36ae25f818feab79a34c373ccf6acba87e9c820b54"})},
    {"id": "vool-plugin-eyebrow", "name": "Eyebrow", "slot": "security.eyebrow",
     "origin_slot": "", "origin": "https://api.eyebrow.cc", "kind": "scanner", "rebind": True,
     "digests": frozenset({"2e03c9975c55d7ccc2e41cef92385b65770343f981b0acc8279b7d20791e0540"})},
)

RESERVED_NAME = ("Give your plugin a different name to install it.")


def reserved_refusal(plugin_id: str, digest: str) -> str:
    """VOOL's own service plugins are known by their exact package bytes, never by a claimed name.

    Their ids unlock the retained payment lane and the saved scanner key, so a package that only
    calls itself by one of those names is refused.
    """
    service = next((service for service in SERVICES if service["id"] == plugin_id), None)
    return RESERVED_NAME if service and digest not in service["digests"] else ""


def is_first_party(record) -> bool:
    return bool(record) and not reserved_refusal(record.plugin_id, str(record.verified_digest or ""))


def retired_slots() -> tuple[str, ...]:
    return tuple(slot for service in SERVICES for slot in (service["slot"], service["origin_slot"]) if slot)


def notices() -> list[dict]:
    from core.credential_store import credential_is_indexed
    from core.plugin_lifecycle import STAGE_UNINSTALLED, record_for

    migrate_selections()
    fallback = selection_notice()
    result = [fallback] if fallback else []
    for service in SERVICES:
        if not any(credential_is_indexed(slot) for slot in (service["slot"], service["origin_slot"]) if slot):
            continue
        record = record_for(service["id"])
        installed = bool(record and record.stage != STAGE_UNINSTALLED)
        if installed and record.enabled and (not service["rebind"]
                                             or credential_is_indexed("plugin." + service["id"] + ".access")):
            continue
        if not installed:
            message = service["name"] + " now comes as a plugin. Install it to keep using it; your saved key will be reused."
        elif service["rebind"]:
            message = "Your saved " + service["name"] + " key is kept. Enable the plugin to reuse it, or connect your own service key."
        else:
            message = "Your saved " + service["name"] + " key is kept. Enable the plugin to keep using it through your wallet approvals."
        result.append({"plugin_id": service["id"], "message": message,
                       "message_key": "plugin_migration_" + service["id"].removeprefix("vool-plugin-") + ("_installed" if installed else "")})
    return result


def bind_on_enable(plugin_id: str) -> bool:
    from core.credential_store import credential_is_indexed, get_credential, store_credential, strict_reads
    from core.plugin_services import declaration
    from core.secret_redaction import register_exact_secret

    service = next((service for service in SERVICES if service["id"] == plugin_id), None)
    if not service or not service["rebind"]:
        # A UsePod token pays from a balance funded through VOOL's wallet. It stays in its
        # own slot, spent only on the retained lane with spend grants, caps and receipts.
        return False
    record, plugin = declaration(plugin_id, service["kind"])
    if not is_first_party(record):
        return False
    spec = plugin.vool[service["kind"]]
    own = f"plugin.{plugin_id}.{spec['credential_slot']}"
    if credential_is_indexed(own):
        return False
    if plugin.vool["kind"] != [service["kind"]] or plugin.vool.get("tools") or plugin.vool.get("mcpServers"):
        return False  # legacy service keys never reach downloaded executable code
    with strict_reads():
        origin = get_credential(service["origin_slot"]) if service["origin_slot"] and credential_is_indexed(service["origin_slot"]) else service["origin"]
        if not origin or spec["base_url"].rstrip("/") != origin.rstrip("/"):
            return False
        if plugin.vool["network"] != [urlsplit(origin).hostname]:
            return False
        value = get_credential(service["slot"]) if credential_is_indexed(service["slot"]) else None
    if not value:
        return False
    register_exact_secret(value)
    store_credential(own, value, label=service["name"] + " service key")
    return True


def migrate_selections() -> bool:
    """Move retired pins using actual v0.7 policy/chat rows, without probing any provider."""
    from core import cloud_escalation_policy as policy

    with policy._COUNTER_LOCK, policy._counter_file_lock():
        raw = policy._read_raw()
        settings = raw.get("policy", {})
        chats = raw.get("chat_models", {})
        if not isinstance(settings, dict) or not isinstance(chats, dict):
            return False
        retired = settings.get("provider") == "usepod" or any(
            isinstance(row, dict) and row.get("provider") == "usepod" for row in chats.values())
        if not retired:
            return False
        # Clear the retired pins. Never move them to another provider: a paid choice is
        # only made through the normal model picker and its spending confirmation.
        saved = raw.setdefault("plugin_migration", {})
        if settings.get("provider") == "usepod":
            saved.setdefault("previous_policy", dict(settings))
            settings.update(provider="", model="", mode="off")
        previous = saved.setdefault("previous_chats", {})
        for session, row in chats.items():
            if isinstance(row, dict) and row.get("provider") == "usepod":
                previous.setdefault(session, dict(row))
                row.update(provider="", model="", revision=int(row.get("revision", 0)) + 1)
        saved["notice_open"] = True
        return policy._write_raw(raw)


def selection_notice() -> dict | None:
    from core import cloud_escalation_policy as policy

    saved = policy._read_raw().get("plugin_migration", {})
    if not isinstance(saved, dict) or not saved.get("notice_open"):
        return None
    return {"plugin_id": "vool-plugin-usepod", "message_key": "plugin_migration_model_fallback", "dismissible": True,
            "message": "UsePod now comes as a plugin. Chats that used a UsePod model have no model selected now, "
                       "and Auto won't switch to a cloud model in those chats. Pick a model before you send another message there."}


def dismiss_selection_notice() -> bool:
    from core import cloud_escalation_policy as policy

    with policy._COUNTER_LOCK, policy._counter_file_lock():
        raw = policy._read_raw()
        saved = raw.get("plugin_migration")
        if not isinstance(saved, dict) or not saved.get("notice_open"):
            return False
        saved["notice_open"] = False
        return policy._write_raw(raw)
