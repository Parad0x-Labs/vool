"""Retired service settings stay local; no saved key of a retired service is ever handed to a plugin."""
from __future__ import annotations

SERVICES = (
    {"id": "vool-plugin-usepod", "name": "UsePod", "slot": "llm.cloud.usepod",
     "origin_slot": "llm.cloud.usepod_origin", "origin": "https://api.usepod.ai", "kind": "model_provider",
     # UsePod is retired (owner decision 2026-11-12): VOOL offers no UsePod plugin and says so
     # once. The saved key stays where it is and nothing uses it. Its payment lane left core in 0.8.
     "retired": True,
     "digests": frozenset({"fe7fedb6881f6ba74df98b36ae25f818feab79a34c373ccf6acba87e9c820b54"})},
    {"id": "vool-plugin-eyebrow", "name": "Eyebrow", "slot": "security.eyebrow",
     "origin_slot": "", "origin": "https://api.eyebrow.cc", "kind": "scanner",
     # Owner decision 2026-11-12, as for UsePod: Eyebrow is out of core and no Eyebrow plugin is
     # offered. Its saved key stays in its own slot and is never copied into any plugin.
     "retired": True,
     "digests": frozenset({"2e03c9975c55d7ccc2e41cef92385b65770343f981b0acc8279b7d20791e0540"})},
)

RESERVED_NAME = ("This name belongs to a plugin made by VOOL. Give your plugin a different name to install it.")


def _service(plugin_id: str):
    return next((service for service in SERVICES if service["id"] == plugin_id), None)


def own_package(plugin_id: str, digest: str) -> bool:
    """Whether these exact package bytes are one of VOOL's own service plugins."""
    service = _service(plugin_id)
    return bool(service) and bool(digest) and digest in service["digests"]


def reserved_refusal(plugin_id: str, digest: str) -> str:
    """VOOL's own service plugins are known by their exact package bytes, never by a claimed name.

    Their ids stay reserved so a package that only calls itself by one of those names can never
    pose as VOOL's own or reach a saved key; such a package is refused.

    So is every other id that reads as VOOL's: the id of a pack VOOL ships, an id that is or
    starts with VOOL's or its maker's name, and the names VOOL's own tools start with
    (``core.plugin_identity.reserved_id``). Only two retired ids were kept back before, and a
    folder calling itself by the shipped database pack's id installed like any other.
    """
    service = _service(plugin_id)
    if service:
        return "" if own_package(plugin_id, digest) else RESERVED_NAME
    from core.plugin_identity import reserved_id

    return RESERVED_NAME if reserved_id(plugin_id) else ""


def is_first_party(record) -> bool:
    # Unchanged by the wider reservation above: this asks only about the retired service ids.
    return bool(record) and not (_service(record.plugin_id) and not own_package(
        record.plugin_id, str(record.verified_digest or "")))


def retired_slots() -> tuple[str, ...]:
    return tuple(slot for service in SERVICES for slot in (service["slot"], service["origin_slot"]) if slot)


def notices() -> list[dict]:
    from core.credential_store import credential_is_indexed
    from core.plugin_lifecycle import STAGE_UNINSTALLED, record_for

    migrate_selections()
    fallback = selection_notice()
    result = []
    for service in SERVICES:
        if not any(credential_is_indexed(slot) for slot in (service["slot"], service["origin_slot"]) if slot):
            continue
        record = record_for(service["id"])
        installed = bool(record and record.stage != STAGE_UNINSTALLED)
        if service.get("retired") and not installed:
            # Said once, plainly, until the owner closes it: nothing to install, nothing to do.
            if not _retired_notice_closed(service["id"]):
                result.append({"plugin_id": service["id"], "dismissible": True,
                               "message": RETIRED_KEY.format(name=service["name"]),
                               "message_key": "plugin_migration_" + service["id"].removeprefix("vool-plugin-")})
            continue
        if installed and record.enabled:
            continue
        # An old package left installed and off: the service is gone from VOOL and its saved key is
        # never handed to the package, so enabling it would not bring the old service back.
        result.append({"plugin_id": service["id"], "message": RETIRED_KEY.format(name=service["name"]),
                       "message_key": "plugin_migration_" + service["id"].removeprefix("vool-plugin-")})
    if fallback:
        result.append(fallback)
    return result


RETIRED_KEY = "{name} is no longer part of VOOL. Your saved {name} key stays on this computer and VOOL does not use it."
SELECTION_CLEARED = ("Chats that used a UsePod model have no model selected now, and Auto won't switch to a cloud model "
                     "in those chats. Pick a model before you send another message there.")


def _retired_notice_closed(plugin_id: str) -> bool:
    from core import cloud_escalation_policy as policy

    saved = policy._read_raw().get("plugin_migration", {})
    return isinstance(saved, dict) and plugin_id in (saved.get("retired_closed") or [])


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
            "message": SELECTION_CLEARED}


def dismiss_selection_notice() -> bool:
    """"Got it": closes the cleared-model notice and the retired-service notice together."""
    from core import cloud_escalation_policy as policy

    with policy._COUNTER_LOCK, policy._counter_file_lock():
        raw = policy._read_raw()
        saved = raw.get("plugin_migration")
        if not isinstance(saved, dict):
            saved = raw["plugin_migration"] = {}
        retired = sorted(service["id"] for service in SERVICES if service.get("retired"))
        if not saved.get("notice_open") and saved.get("retired_closed") == retired:
            return False
        saved["notice_open"] = False
        saved["retired_closed"] = retired
        return policy._write_raw(raw)
