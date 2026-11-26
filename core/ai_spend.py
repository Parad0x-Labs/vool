"""AI spending: what paid models have cost, against the limits the person set, and a warning before
a limit is reached.

The limits here are the ones the spend ledger enforces (``core.paid_call_reservation.spend_limits``
reads them): a paid call is refused before it is sent when it could take the day, the month or a
single answer past its limit. Nothing here enforces on its own; it is the person's place to set the
numbers and to see where they stand.

Amounts come from the same ledger the caps are checked on (``core.model_spend_ledger``), so the
page can never show less than what the next call will be checked against. Each amount says how it is
known: ``billed`` is the provider's own bill, ``estimated`` is tokens priced by VOOL, ``held`` is a
call still open or whose bill is unknown, counted at its ceiling until it settles.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import tempfile
from typing import Any

#: The limits the person can set, in USD: one answer, one UTC day, one UTC month.
LIMIT_KEYS = ("per_call", "daily", "monthly")
#: The share of a limit at which VOOL warns, before the limit itself refuses a call.
WARN_AT = 0.8
MIN_USD = 0.01
MAX_USD = 10_000.0
_FILE = "ai_spend_limits.json"


def _path():
    from core.runtime_paths import data_path

    return data_path(_FILE)


def _valid(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < MIN_USD or number > MAX_USD:
        return None
    return round(number, 2)


def person_limits() -> dict[str, float]:
    """The limits the person set. A missing, damaged or out-of-range value is simply not set, and
    the built-in default applies: a limit is never lifted by a bad file."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key in LIMIT_KEYS:
        value = _valid(raw.get(key))
        if value is not None:
            out[key] = value
    return out


def set_limits(values: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Save the person's limits. ``None`` for a key clears it back to the default. Every value is
    checked before anything is written; one bad value writes nothing."""
    if not isinstance(values, dict) or not values or set(values) - set(LIMIT_KEYS):
        return False, {"error": "unknown_limit", "allowed": list(LIMIT_KEYS)}
    current = person_limits()
    for key, value in values.items():
        if value is None or value == "":
            current.pop(key, None)
            continue
        number = _valid(value)
        if number is None:
            return False, {"error": "out_of_range", "limit": key, "min": MIN_USD, "max": MAX_USD}
        current[key] = number
    if "per_call" in current and current["per_call"] > min(current.get("daily", math.inf), current.get("monthly", math.inf)):
        return False, {"error": "answer_above_period", "limit": "per_call"}
    if current.get("daily") and current.get("monthly") and current["daily"] > current["monthly"]:
        return False, {"error": "day_above_month", "limit": "daily"}
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-spend-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return True, {"limits": current}


def _window(totals: dict[str, Any], cap: float) -> dict[str, Any]:
    counted = float(totals.get("counted_usd") or 0.0)
    share = counted / cap if cap > 0 else 1.0
    state = "reached" if counted >= cap - 1e-9 else "near" if share >= WARN_AT else "ok"
    return {**totals, "limit_usd": round(cap, 2), "share": round(share, 4), "state": state,
            "left_usd": round(max(0.0, cap - counted), 6)}


def overview(*, session_id: str = "", now: float | None = None) -> dict[str, Any]:
    """Where paid model spend stands against each enforced limit, for this day, month and chat."""
    from core.model_spend_ledger import spend_totals
    from core.paid_call_reservation import spend_limits

    limits = spend_limits()
    totals = spend_totals(now=now, session_id=session_id)
    mine = person_limits()
    out: dict[str, Any] = {
        "ok": True,
        "limits": {"per_call": round(float(limits.per_call_usd), 2), "daily": round(float(limits.daily_usd), 2),
                   "monthly": round(float(limits.monthly_usd), 2)},
        "set_by_you": sorted(mine),
        "today": _window(totals["today"], float(limits.daily_usd)),
        "month": _window(totals["month"], float(limits.monthly_usd)),
        "day_resets_at": totals["day_resets_at"],
        "warn_at": WARN_AT,
        "range": {"min": MIN_USD, "max": MAX_USD},
        "paid_use_possible": paid_use_possible(),
        "limits_prompt_due": limits_prompt_due(),
    }
    if "session" in totals:
        out["session"] = totals["session"]
    out["alerts"] = [{"window": name, "state": out[name]["state"], "counted_usd": out[name]["counted_usd"],
                      "limit_usd": out[name]["limit_usd"]}
                     for name in ("today", "month") if out[name]["state"] != "ok"]
    return out


def answer_spend(turn_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What this answer's paid calls cost, from their own settlement receipts: billed, estimated, or
    held because the provider has not said. None when the answer used no paid model."""
    billed = estimated = held = 0.0
    calls = 0
    seen: set[str] = set()
    for event in turn_events:
        kind = str(event.get("event_type") or "")
        if kind not in {"paid_call.settled", "paid_call.billing_ambiguous"}:
            continue
        call = str(event.get("model_call_id") or "")
        if call and call in seen:
            continue
        seen.add(call)
        calls += 1
        if kind == "paid_call.billing_ambiguous" or event.get("actual_usd") is None:
            held += float(event.get("held_usd") or event.get("reserved_usd") or 0.0)
            continue
        amount = float(event.get("actual_usd") or 0.0)
        if str(event.get("cost_basis") or "") == "provider_reported":
            billed += amount
        else:
            estimated += amount
    if not calls:
        return None
    return {"calls": calls, "billed_usd": round(billed, 6), "estimated_usd": round(estimated, 6),
            "held_usd": round(held, 6)}


# ---- shown only where money can be spent ------------------------------------------------------------------------------
#
# A computer that only runs local models spends nothing, and the spending limits read as clutter there (sls, Nov 26, from
# a local-only user's report). The limits themselves are never switched off: ``core.paid_call_reservation.spend_limits``
# enforces the built-in defaults whether or not they are shown, so nothing paid can run past a limit. What changes is
# only where they are shown and when the person is asked to set their own.
_PROMPT_FILE = "ai_spend_prompt.json"


def _a_cloud_key_is_kept() -> bool:
    from core import credential_store
    from core.cloud_providers import all_slots

    # The disk index only: no Keychain read, so a settings page or a new chat never waits on a Keychain prompt.
    return any(credential_store.credential_is_indexed(slot) for slot in all_slots())


def _the_wallet_is_on() -> bool:
    from core.wallet import config as wallet_config

    return bool(wallet_config.wallet_enabled())


def _anything_was_spent() -> bool:
    from core.model_spend_ledger import spend_totals

    month = spend_totals().get("month") or {}
    return float(month.get("counted_usd") or 0.0) > 0.0


def paid_use_possible() -> bool:
    """Whether anything on this computer can spend money on a model: a cloud key is kept, the wallet is on, the person
    set limits of their own, or a paid call was counted this month. A read that fails counts as yes: a money surface is
    never hidden because something could not be read."""
    if person_limits():
        return True
    for check in (_a_cloud_key_is_kept, _the_wallet_is_on, _anything_was_spent):
        try:
            if check():
                return True
        except Exception:
            return True
    return False


def _prompt_path():
    from core.runtime_paths import data_path

    return data_path(_PROMPT_FILE)


def limits_prompt_dismissed() -> bool:
    try:
        raw = json.loads(_prompt_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(raw, dict) and raw.get("kept_defaults") is True


def keep_default_limits() -> None:
    """The person chose the built-in limits: the card asking for their own is not shown again."""
    path = _prompt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ai_spend_prompt.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"kept_defaults": True}, handle)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def limits_prompt_due() -> bool:
    """Whether the new chat screen offers to set spending limits: money can be spent here, the person has set no limits
    of their own, and they have not chosen to keep the built-in ones."""
    return paid_use_possible() and not person_limits() and not limits_prompt_dismissed()


__all__ = [
    "LIMIT_KEYS",
    "WARN_AT",
    "answer_spend",
    "keep_default_limits",
    "limits_prompt_dismissed",
    "limits_prompt_due",
    "overview",
    "paid_use_possible",
    "person_limits",
    "set_limits",
]
