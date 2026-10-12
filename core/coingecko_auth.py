"""CoinGecko API keys: the one table that says which host a request goes to and where its key rides.

CoinGecko documents two keyed plans (docs.coingecko.com/reference/authentication, read 2026-10-05):

* **Demo** -- the free plan: root ``https://api.coingecko.com/api/v3``, key in ``x-cg-demo-api-key``;
* **Pro** -- the paid plans: root ``https://pro-api.coingecko.com/api/v3``, key in ``x-cg-pro-api-key``.

A key is never placed in the query string (the URL would carry it into logs and receipts). A Pro key
sent to the Demo root is answered 400 (error 10010) and a bad key on ``/ping`` 401 (measured
2026-10-05), so the plan is the operator's choice in Settings, verified once at save, never guessed.

Without a stored key VOOL keeps the keyless public API it always used. A stored key only RAISES
the limits: when CoinGecko refuses it (revoked, expired), the request is asked again with
the next stored key, then none, and the refusal is remembered, so a dead key never takes price
answers down.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

KEYLESS_BASE = "https://api.coingecko.com/api/v3"


@dataclass(frozen=True)
class CoinGeckoPlan:
    provider_id: str
    label: str
    credential_slot: str
    base_url: str
    key_header: str
    signup_url: str


PLANS: dict[str, CoinGeckoPlan] = {
    "coingecko": CoinGeckoPlan(
        provider_id="coingecko",
        label="CoinGecko (Demo key)",
        credential_slot="market.data.coingecko",
        base_url="https://api.coingecko.com/api/v3",
        key_header="x-cg-demo-api-key",
        signup_url="https://www.coingecko.com/en/developers/dashboard",
    ),
    "coingecko_pro": CoinGeckoPlan(
        provider_id="coingecko_pro",
        label="CoinGecko Pro",
        credential_slot="market.data.coingecko_pro",
        base_url="https://pro-api.coingecko.com/api/v3",
        key_header="x-cg-pro-api-key",
        signup_url="https://www.coingecko.com/en/api/pricing",
    ),
}
#: A Pro key wins when both are stored: it is the plan the operator pays for.
_PREFERENCE = ("coingecko_pro", "coingecko")
#: Documented CoinGecko key prefix; a recognition hint only, shared by both plans.
KEY_PREFIX = "CG-"
#: The statuses with which CoinGecko refuses a key (bad, revoked, wrong plan for this root).
REFUSED_KEY_STATUSES = frozenset({400, 401, 403})

_refused_lock = threading.Lock()
_refused: dict[str, str] = {}  # plan id -> digest of the key CoinGecko refused at request time


def all_slots() -> frozenset[str]:
    return frozenset(plan.credential_slot for plan in PLANS.values())


def plan_for_slot(slot: str) -> CoinGeckoPlan | None:
    return next((plan for plan in PLANS.values() if plan.credential_slot == str(slot or "")), None)


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stored(plan: CoinGeckoPlan) -> str:
    try:
        from core import credential_store

        return str(credential_store.get_credential(plan.credential_slot) or "").strip()
    except Exception:
        return ""


def keyed_target() -> tuple[CoinGeckoPlan, str] | None:
    """(plan, key) for the stored key requests should carry, or None to stay keyless. A key
    CoinGecko refused at request time is skipped until it changes. Never raises."""
    for plan_id in _PREFERENCE:
        plan = PLANS[plan_id]
        key = _stored(plan)
        if not key:
            continue
        with _refused_lock:
            if _refused.get(plan_id) == _digest(key):
                continue
        return plan, key
    return None


def request_for(path_and_query: str) -> tuple[str, dict[str, str], str]:
    """(url, auth headers, plan id or "keyless") for one CoinGecko API call.

    ``path_and_query`` starts with ``/`` and is relative to the API root (``/simple/price?ids=…``).
    The key, when there is one, rides only its plan's documented header."""
    target = keyed_target()
    if target is None:
        return f"{KEYLESS_BASE}{path_and_query}", {}, "keyless"
    plan, key = target
    return f"{plan.base_url}{path_and_query}", {plan.key_header: key}, plan.provider_id


def note_refused(plan_id: str, sent_key: str) -> None:
    """Remember that CoinGecko refused ``sent_key`` -- the key that request actually carried, not
    whatever the store holds by the time the answer arrives (a key saved meanwhile was never tried and
    must not inherit the refusal). Requests skip that exact key until it changes."""
    if plan_id in PLANS and str(sent_key or "").strip():
        with _refused_lock:
            _refused[plan_id] = _digest(str(sent_key).strip())


def refused_plans() -> tuple[str, ...]:
    """Plans whose stored key CoinGecko refused at request time (for Settings and diagnostics)."""
    out = []
    for plan_id, plan in PLANS.items():
        key = _stored(plan)
        with _refused_lock:
            if key and _refused.get(plan_id) == _digest(key):
                out.append(plan_id)
    return tuple(out)


def reset_refusals_for_tests() -> None:
    with _refused_lock:
        _refused.clear()


__all__ = [
    "KEYLESS_BASE",
    "KEY_PREFIX",
    "PLANS",
    "REFUSED_KEY_STATUSES",
    "CoinGeckoPlan",
    "all_slots",
    "keyed_target",
    "note_refused",
    "plan_for_slot",
    "refused_plans",
    "request_for",
    "reset_refusals_for_tests",
]
