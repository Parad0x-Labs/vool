"""PC-owned, per-phone rights. Phone input can narrow these, never widen them."""

from __future__ import annotations

import copy
import math
import time

RIGHTS = (
    "chat",
    "history",
    "attachments",
    "tool_approval",
    "alerts",
    "wallet_view",
    "payment_propose",
    "payment_approve",
    "camera",
    "photos",
    "location",
    "microphone",
    # The owner's own VOOL from the phone (core.request_trust.remote_turn_tool_refusal): files, apps,
    # commands and accounts on this computer; and the owner's paid models and paid search. Money
    # tools stay out of chat either way; payments go through payment_propose and the wallet.
    "computer_tools",
    "paid_services",
)
STRONG = frozenset({"payment_propose", "payment_approve", "tool_approval", "computer_tools", "paid_services"})
#: Strong rights the computer grants for 30 days when switched on without a chosen expiry.
_RENEWED_ON_ENABLE = ("computer_tools", "paid_services")
ACTIONS = {
    "chats.list": "history",
    "chats.history": "history",
    "chats.prepare": "chat",
    "chats.send": "chat",
    "chats.progress": "chat",
    "chats.cancel": "chat",
    "proof.get": "history",
    "attachments.limits": "attachments",
    "attachments.upload": "attachments",
    # Files the computer sent this phone: the send itself was the owner's (or an allowed turn's).
    "files.list": "chat",
    "files.fetch": "chat",
    "approvals.pending": "history",
    # A phone answers only the approvals its own turns raised, for tools it was allowed to use.
    "approvals.resolve": "computer_tools",
    "wallet.status": "wallet_view",
    "wallet.balances": "wallet_view",
    "wallet.receipts": "wallet_view",
    "wallet.contacts": "wallet_view",
    "wallet.pending": "wallet_view",
    "wallet.propose": "payment_propose",
    "wallet.preview": "payment_propose",
    "wallet.decline": "payment_propose",
    "wallet.approve": "payment_approve",
    "abilities.dictate": "microphone",
    "abilities.location": "location",
}


class PhoneHistoryRequired(ValueError):
    pass


def scoped_chat_handle(device, handle):
    import hashlib
    import re

    if not device or not device.get("sealed_protocol"):
        return handle
    if re.fullmatch(r"mob-[a-f0-9]{20,64}", handle):
        return "mob-" + hashlib.sha256((device["device_id"] + ":" + handle).encode()).hexdigest()[:40]
    if not enabled(device, "history"):
        raise PhoneHistoryRequired("Enable chat history on your computer to continue this desktop chat.")
    return handle


def defaults():
    return {name: False for name in RIGHTS}


def enabled(device, right, now=None):
    rules = device.get("rules") or {}
    expiry = (device.get("right_expiry") or {}).get(right, 0)
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry < 0:
        return False
    moment = time.time() if now is None else now
    if right in STRONG and (not expiry or expiry > moment + 30 * 86400):
        return False
    return rules.get(right) is True and (not expiry or expiry > moment)


def refusal(device, action):
    # v1 grants are a loopback compatibility surface only; the separate listener
    # cannot decrypt them. Newly paired network devices always carry explicit rights.
    if device.get("sealed_protocol") or action.startswith("wallet."):
        right = ACTIONS.get(action)
        if right and not enabled(device, right):
            return "phone_right_off_or_expired"
        if action == "wallet.approve":
            return "approval_key_unproven_use_computer"
    return ""


def cap_display(cap):
    from core.wallet import amounts, chains

    if not cap:
        return {}
    asset = chains.native_asset(str(cap["network"]))
    return {
        "asset": asset.symbol,
        "per_payment": amounts.format_minor(cap["per_payment_minor"], asset.decimals),
        "daily": amounts.format_minor(cap["daily_minor"], asset.decimals),
    }


def update(service, body):
    from core.wallet import amounts, caller_binding, chains, custody, limits, pilot_custody

    if caller_binding.refusal_reason(body.get("_headers")):
        raise ValueError("Change phone permissions from Settings on your computer.")
    device_id = str(body.get("device_id") or "")
    current = service.store.device(device_id)
    if not current or current.get("status") != "active":
        raise ValueError("This phone is no longer paired.")
    expected = body.get("expected_permission_revision")
    if expected is not None and (type(expected) is not int or expected != int(current.get("permission_revision") or 0)):
        raise ValueError("These permissions changed. Reopen this section before saving again.")
    changes = body.get("rules")
    if (
        not isinstance(changes, dict)
        or set(changes) - set(RIGHTS)
        or any(type(v) is not bool for v in changes.values())
    ):
        raise ValueError("Choose a known phone permission.")
    if changes.get("payment_approve") or changes.get("tool_approval"):
        raise ValueError(
            "Approval on this phone is unavailable until its hardware key is proven. Approve on your computer."
        )
    if current.get("kind") == "bot":
        from core.mobile_companion.bot_runtime import BOT_RIGHTS

        if any(value and name not in BOT_RIGHTS for name, value in changes.items()):
            raise ValueError("This permission is not available to chat apps. Approve on your computer.")
    rules = {**defaults(), **(current.get("rules") or {}), **changes}
    expiries = copy.deepcopy(current.get("right_expiry") or {})
    given = body.get("right_expiry", {})
    if not isinstance(given, dict) or set(given) - set(RIGHTS):
        raise ValueError("Unknown permission expiry.")
    for name, expiry in given.items():
        if (
            type(expiry) not in (int, float)
            or not math.isfinite(expiry)
            or expiry < 0
            or (expiry and expiry <= time.time())
        ):
            raise ValueError("Choose a future permission expiry.")
        if name in STRONG and (not expiry or expiry > time.time() + 30 * 86400):
            raise ValueError("Payment and approval rights must expire within 30 days. Renew them on your computer.")
        expiries[name] = expiry
    cap = copy.deepcopy(current.get("payment_cap") or {})
    if "payment_cap_human" in body:
        text = body["payment_cap_human"]
        if not isinstance(text, dict) or set(text) != {"wallet_id", "network", "per_payment", "daily"}:
            raise ValueError("Choose an account and both phone limits.")
        asset = chains.native_asset(str(text["network"]))
        body = {
            **body,
            "payment_cap": {
                "wallet_id": str(text["wallet_id"]),
                "network": str(text["network"]),
                "asset": asset.symbol,
                "per_payment_minor": amounts.parse_human_amount(
                    text["per_payment"], decimals=asset.decimals, symbol=asset.symbol
                ),
                "daily_minor": amounts.parse_human_amount(text["daily"], decimals=asset.decimals, symbol=asset.symbol),
            },
        }
    if (
        changes.get("payment_propose")
        or "payment_cap" in body
        or ("payment_propose" in given and rules["payment_propose"])
    ):
        reason = caller_binding.refusal_reason(body.get("_headers"))
        if reason:
            raise ValueError("Change payment rights from Settings on your computer.")
        cap = body.get("payment_cap") or cap
        if not isinstance(cap, dict) or set(cap) != {
            "wallet_id",
            "network",
            "asset",
            "per_payment_minor",
            "daily_minor",
        }:
            raise ValueError("Choose an account, network, asset and both phone limits.")
        profile = custody.require_wallet(str(cap["wallet_id"]))
        if profile.approval_method == custody.APPROVAL_DEVICE:
            raise ValueError("This wallet uses approval on the Mac. Payment rights stay on the computer.")
        pilot_custody.prove_credential(
            profile.wallet_id, str(body.get("credential") or ""), source_context={"surface": "mobile_settings"}
        )
        spec = chains.resolve_network(str(cap["network"]))
        if spec.network != chains.resolve_network(profile.network).network:
            raise ValueError("The account belongs to a different network.")
        # This first payment proposal door offers native assets only. Token fee
        # caps need a separately configured fee-asset allowance before enabling.
        if cap["asset"] != chains.native_asset(spec.network).symbol:
            raise ValueError("Phone proposals currently support the network's native asset only.")
        wallet_cap = limits.load_limits(profile.wallet_id, str(cap["asset"]))
        for name, ceiling in (("per_payment_minor", wallet_cap.per_tx_minor), ("daily_minor", wallet_cap.daily_minor)):
            value = cap[name]
            if type(value) is not int or value <= 0 or (ceiling > 0 and value >= ceiling):
                raise ValueError("Phone limits must be positive and lower than the wallet limits.")
        cap = {**cap, "network": spec.network}
        expiries.setdefault("payment_propose", time.time() + 30 * 86400)
        if changes.get("payment_propose") and "payment_propose" not in given:
            expiries["payment_propose"] = time.time() + 30 * 86400
    extra = {}
    if "file_scope" in body:
        extra["file_scope"] = _file_scope(body["file_scope"])
    if "cloud_monthly_usd" in body:
        extra["cloud_monthly_usd"] = _cloud_monthly(body["cloud_monthly_usd"])
    previous_rules = current.get("rules") or {}
    for name in _RENEWED_ON_ENABLE:
        # Switched on now, or on with no live expiry: 30 days. A save that only re-sends an
        # already-on right keeps the expiry the owner chose.
        stale = not (type(expiries.get(name)) in (int, float) and expiries[name] > time.time())
        was_on = previous_rules.get(name) is True and not stale
        if changes.get(name) and name not in given and not was_on:
            expiries[name] = time.time() + 30 * 86400
    service.store.mark_device(
        device_id,
        rules=rules,
        right_expiry=expiries,
        payment_cap=cap,
        permission_revision=int(current.get("permission_revision") or 0) + 1,
        **extra,
    )
    if not current.get("notification_state"):
        service.store.edit_notifications(device_id, lambda _state, _device: None)
    service.store.append_receipt(
        "device.rules_changed", device_id=device_id, detail={"rules": rules, "right_expiry": expiries, **extra}
    )
    return {"rules": rules, "right_expiry": expiries, "payment_cap": cap, **extra}


def system_area(path):
    """Whether ``path`` is outside the owner's home folder or inside its Library (keychains, mail,
    browser data, app settings). A phone never reaches those, whatever it was allowed."""
    from pathlib import Path

    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return True
    target = Path(path)
    if target != home and home not in target.parents:
        return True
    library = home / "Library"
    return target == library or library in target.parents


#: How many folders one phone may be limited to.
MAX_PHONE_FOLDERS = 20


def _file_scope(value):
    """``{"mode": "all"}`` or ``{"mode": "folders", "folders": [...]}`` with each folder an existing
    folder on this computer, stored fully resolved so a later symlink swap cannot widen it."""
    from pathlib import Path

    from core.runtime_state_protection import refusal as protected_refusal

    if not isinstance(value, dict) or value.get("mode") not in {"all", "folders"}:
        raise ValueError("Choose all files or chosen folders for this phone.")
    if value["mode"] == "all":
        if set(value) != {"mode"}:
            raise ValueError("Choose all files or chosen folders for this phone.")
        return {"mode": "all"}
    folders = value.get("folders")
    if set(value) != {"mode", "folders"} or not isinstance(folders, list) or not folders:
        raise ValueError("Choose at least one folder for this phone.")
    if len(folders) > MAX_PHONE_FOLDERS:
        raise ValueError(f"Choose up to {MAX_PHONE_FOLDERS} folders for this phone.")
    chosen = []
    for raw in folders:
        candidate = Path(str(raw if isinstance(raw, str) else "").strip()).expanduser()
        if not str(raw or "").strip() or not candidate.is_absolute():
            raise ValueError("Choose each folder by its full location on this computer.")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError(f"VOOL could not find the folder {candidate}.") from None
        if not resolved.is_dir():
            raise ValueError(f"{candidate} is not a folder.")
        if protected_refusal(resolved, write=False):
            raise ValueError(f"{candidate} holds VOOL's own data. Choose another folder.")
        if system_area(resolved):
            raise ValueError(f"{candidate} belongs to the system. Choose a folder in your home folder.")
        if str(resolved) not in chosen:
            chosen.append(str(resolved))
    return {"mode": "folders", "folders": chosen}


#: The highest monthly cloud limit a phone can be given; the computer's own cloud limits still apply.
MAX_PHONE_CLOUD_MONTHLY_USD = 10000.0


def _cloud_monthly(value):
    """A positive monthly cloud limit in US dollars, or ``None`` for the computer's own limits only."""
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0.01 or value > MAX_PHONE_CLOUD_MONTHLY_USD:
        raise ValueError("Enter a monthly cloud limit of at least 0.01 dollars.")
    return round(float(value), 2)


def switch_off(service, device, params):
    names = params.get("rights")
    if not isinstance(names, list) or not names or set(names) - set(RIGHTS):
        return 400, {"error": "Choose the permissions to pause."}
    with service.dispatch_lock:
        current = service.store.device(device["device_id"]) or {}
        rules = {**defaults(), **(current.get("rules") or {})}
        rules.update({name: False for name in names})
        service.store.mark_device(
            device["device_id"], rules=rules, permission_revision=int(current.get("permission_revision") or 0) + 1
        )
        service.store.append_receipt("device.rights_paused", device_id=device["device_id"], detail={"rights": names})
    return 200, {"rules": rules}
