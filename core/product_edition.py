"""core/product_edition.py — the product edition boundary (PERSONAL | SCHOOL).

VOOL ships one runtime with more than one product face. The edition is resolved once
per process from the environment or the install config, and every capability-bearing
seam consults THIS module — never a scattered ``if school:`` — so an absent capability
is mechanically absent: not offered to a model, not routable by an intent, not
invokable at dispatch, not mounted as an HTTP door.

SCHOOL floor law (goal §4): a floored capability cannot be re-enabled by any
preference, policy, environment variable, or administrator action in this process.
This mirrors the wallet's mainnet law (core/wallet/config.py): no flag adds it back.

Resolution order:
    1. ``VOOL_EDITION`` environment variable (installer/service override)
    2. ``<config home>/edition.json`` ``{"edition": "school"}`` (packaged installs)
    3. PERSONAL (default — an existing install with no edition marker is unchanged)

The resolved edition is cached per process; tests use ``reset_edition_cache()``.
"""

from __future__ import annotations

import json
import os
from enum import Enum
from functools import lru_cache


class ProductEdition(str, Enum):
    PERSONAL = "personal"
    SCHOOL = "school"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


_EDITION_ENV = "VOOL_EDITION"
_EDITION_CONFIG_RELPATH = "edition.json"

# Tool surfaces (the registry's own namespace vocabulary, see
# core/runtime_tool_contracts.py ``tool_surface=``) that the SCHOOL edition
# mechanically lacks. Crypto, payments, marketplaces and the web0 paid-gate
# economy are absent — "educational discussion" remains possible through the
# ordinary chat lane; the *capabilities* are gone.
SCHOOL_FLOOR_SURFACES = frozenset(
    {
        "wallet",      # wallet.*, pay.*, x402.* — value movement, balances, proposals
        "marketplace", # marketplace.*, sell.* — paid knowledge purchase / quotes
        "web0",        # web0.* — paid gated-section site economy
    }
)

# Intent prefixes that are floored in SCHOOL even where a contract's surface is
# shared with an unfloored lane. Kept explicit so a future surface rename cannot
# accidentally re-expose a money intent.
SCHOOL_FLOOR_INTENT_PREFIXES = (
    "wallet.",
    "pay.",
    "x402.",
    "marketplace.",
    "sell.",
    "web0.",
)

# Non-tool surfaces (HTTP doors, daemons, background lanes) that SCHOOL does not
# run. The mesh/hive lane accepts tasks from unenrolled peers by design in
# PERSONAL; in SCHOOL "local network != authority" is enforced by not running it.
SCHOOL_FLOOR_SERVICES = frozenset(
    {
        "mesh_daemon",       # UDP 0.0.0.0 hive transport — no classroom enrollment exists
        "hive_task_intake",  # accept_hive_tasks lane — remote peers' capsules
        "meet_server",       # public write/meet surface (separate process)
        "web0_announce",     # mesh capability/price announcements
        "earnings_page",     # mesh economics UI
    }
)

_EDITION_REASONS = {
    "wallet": "Money and wallet capabilities are not part of VOOL School.",
    "marketplace": "Paid marketplaces are not part of VOOL School.",
    "web0": "The web0 paid-site economy is not part of VOOL School.",
    "mesh_daemon": "The hive mesh is not part of VOOL School (local network is never authority).",
    "hive_task_intake": "The hive mesh is not part of VOOL School (local network is never authority).",
    "meet_server": "The public meet surface is not part of VOOL School.",
    "web0_announce": "Mesh announcements are not part of VOOL School.",
    "earnings_page": "Mesh earnings are not part of VOOL School.",
}

_DEFAULT_REASON = "This capability is not part of VOOL School."


def _edition_from_config() -> ProductEdition | None:
    try:
        from core.runtime_paths import config_path

        path = config_path(_EDITION_CONFIG_RELPATH)
    except Exception:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        value = str(raw.get("edition", "")).strip().lower()
    except Exception:
        return None
    return _coerce(value)


def _coerce(value: str) -> ProductEdition | None:
    value = str(value or "").strip().lower()
    if value == ProductEdition.SCHOOL.value:
        return ProductEdition.SCHOOL
    if value == ProductEdition.PERSONAL.value:
        return ProductEdition.PERSONAL
    return None


@lru_cache(maxsize=1)
def _active_edition_cached() -> ProductEdition:
    env_value = os.environ.get(_EDITION_ENV, "")
    if env_value:
        coerced = _coerce(env_value)
        if coerced is not None:
            return coerced
    from_config = _edition_from_config()
    if from_config is not None:
        return from_config
    return ProductEdition.PERSONAL


def reset_edition_cache() -> None:
    """Test hook: re-resolve the edition (after env/config changes)."""
    _active_edition_cached.cache_clear()


def active_edition() -> ProductEdition:
    """The resolved product edition for this process."""
    return _active_edition_cached()


def is_school() -> bool:
    return active_edition() is ProductEdition.SCHOOL


def edition_allows(surface: str) -> tuple[bool, str]:
    """May this capability surface exist in the active edition?

    Returns ``(allowed, canonical_reason)``. In PERSONAL everything is allowed and
    the reason is empty. In SCHOOL a floored surface/tool gets ``(False, reason)``
    — the one truth consulted by the contract builder, the demand-signal seat
    filter, the service mounts and the daemon boot.
    """
    if active_edition() is not ProductEdition.PERSONAL:
        surface = str(surface or "").strip().lower()
        if surface in SCHOOL_FLOOR_SURFACES or surface in SCHOOL_FLOOR_SERVICES:
            return False, _EDITION_REASONS.get(surface, _DEFAULT_REASON)
    return True, ""


def edition_intent_allowed(intent: str) -> tuple[bool, str]:
    """Edition verdict for a concrete tool intent (e.g. ``pay.x402``)."""
    if active_edition() is ProductEdition.PERSONAL:
        return True, ""
    intent = str(intent or "").strip().lower()
    for prefix in SCHOOL_FLOOR_INTENT_PREFIXES:
        if intent.startswith(prefix):
            return False, _EDITION_REASONS.get(prefix.rstrip("."), _DEFAULT_REASON)
    return True, ""


def edition_profanity_ceiling() -> int:
    """SCHOOL pins the profanity preference to 0 and refuses raises (operator law)."""
    return 0 if is_school() else 100


__all__ = [
    "SCHOOL_FLOOR_INTENT_PREFIXES",
    "SCHOOL_FLOOR_SERVICES",
    "SCHOOL_FLOOR_SURFACES",
    "ProductEdition",
    "active_edition",
    "edition_allows",
    "edition_intent_allowed",
    "edition_profanity_ceiling",
    "is_school",
    "reset_edition_cache",
]
