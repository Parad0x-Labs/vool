"""The wallet-owned network environment: Mainnet or Test networks.

One preference, owned beneath ``core.wallet`` and never a generic user preference: which environment's
rows the product operates on. Crypto stays off by default; once it is on, a fresh installation operates
on Mainnet. An installation upgraded from a build whose wallet data lives only on test rows keeps
operating on Test networks until the owner chooses otherwise. Reading the environment never writes.

The environment gates NEW effects only (account registration, proposals, quotes, approvals, signing).
It never migrates, relabels or hides an account, a balance, a hold or a receipt: every row keeps its own
chain identity, and a transfer already dispatched is reconciled on its own row whatever the active
environment is. The first effect taken under a derived default pins that default, so later data can
never flip the environment without the owner's choice.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.environment"

ENVIRONMENT_MAINNET = "mainnet"
ENVIRONMENT_TESTNET = "testnet"
ENVIRONMENTS = (ENVIRONMENT_MAINNET, ENVIRONMENT_TESTNET)
#: Operator override for the safe direction only: ``testnet`` makes new effects run on Test networks
#: (development and test runs) and wins over the stored choice. A process environment can never move a
#: person onto Mainnet: any other value is ignored and the stored or derived environment applies.
ENVIRONMENT_ENV = "VOOL_WALLET_NETWORK_ENVIRONMENT"
_CONTROL_KEY = "network_environment"

SOURCE_OVERRIDE = "operator_override"
SOURCE_STORED = "stored"
SOURCE_UPGRADE = "upgrade_preserved_test_networks"
SOURCE_FRESH = "fresh_install_default"
_DERIVED_SOURCES = (SOURCE_UPGRADE, SOURCE_FRESH)

#: Tables whose rows carry the network an existing piece of wallet data lives on.
_DATA_TABLES = ("wallet_profiles", "wallet_proposals", "wallet_receipts")


@dataclass(frozen=True)
class EnvironmentState:
    environment: str
    source: str
    changed_at: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"environment": self.environment, "source": self.source, "changed_at": self.changed_at}


def _override() -> str:
    raw = str(os.environ.get(ENVIRONMENT_ENV) or "").strip().lower()
    return ENVIRONMENT_TESTNET if raw == ENVIRONMENT_TESTNET else ""


def _stored(conn: Any) -> tuple[str, str]:
    row = conn.execute("SELECT value, updated_at FROM wallet_controls WHERE key = ?", (_CONTROL_KEY,)).fetchone()
    if not row:
        return "", ""
    try:
        value = str((json.loads(row[0]) or {}).get("environment") or "")
    except (ValueError, AttributeError):
        value = ""
    return (value, str(row[1] or "")) if value in ENVIRONMENTS else ("", "")


def _environments_of_existing_data(conn: Any) -> set[str]:
    from core.wallet import chains

    names: set[str] = set()
    for table in _DATA_TABLES:
        names.update(str(row[0]) for row in conn.execute(f"SELECT DISTINCT network FROM {table}").fetchall())
    found: set[str] = set()
    for name in names:
        try:
            found.add(chains.resolve_network(name).environment)
        except Exception:
            continue
    return found


def _resolve(conn: Any) -> EnvironmentState:
    """The one derivation: the operator override, then the stored choice, then the default derived from existing data."""
    override = _override()
    if override:
        return EnvironmentState(override, SOURCE_OVERRIDE)
    stored, changed_at = _stored(conn)
    if stored:
        return EnvironmentState(stored, SOURCE_STORED, changed_at)
    if _environments_of_existing_data(conn) == {ENVIRONMENT_TESTNET}:
        return EnvironmentState(ENVIRONMENT_TESTNET, SOURCE_UPGRADE)
    return EnvironmentState(ENVIRONMENT_MAINNET, SOURCE_FRESH)


def active_environment() -> EnvironmentState:
    """The environment new effects run in, with where that answer came from. Never writes."""
    override = _override()
    if override:
        return EnvironmentState(override, SOURCE_OVERRIDE)
    with connection() as conn:
        return _resolve(conn)


def _control_value(environment: str, set_by: str) -> str:
    return json.dumps({"environment": environment, "set_by": set_by}, sort_keys=True)


def _active(conn: Any) -> EnvironmentState:
    """:func:`active_environment` on the caller's connection. A derived default is pinned there with the value
    :func:`require_active` pins, so a claim transaction reads and keeps one answer."""
    state = _resolve(conn)
    if state.source in _DERIVED_SOURCES:
        conn.execute("INSERT OR IGNORE INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?)", (_CONTROL_KEY, _control_value(state.environment, state.source), utcnow()))
    return state


def _write(environment: str, *, set_by: str, only_if_absent: bool) -> None:
    value = _control_value(environment, set_by)
    with connection() as conn:
        if only_if_absent:
            conn.execute("INSERT OR IGNORE INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?)", (_CONTROL_KEY, value, utcnow()))
        else:
            conn.execute(
                "INSERT INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (_CONTROL_KEY, value, utcnow()),
            )
            from core.wallet import controls

            # the owner's switch, in the same transaction: a signed transfer that has not been sent sees it
            controls.bump(conn, "environment")


def set_active_environment(environment: str, *, source_context: dict[str, Any] | None = None) -> EnvironmentState:
    """The owner's explicit choice (Settings -> Crypto -> Developer options). Touches nothing but the
    preference: accounts, holds, balances and receipts stay on their own rows."""
    wanted = str(environment or "").strip().lower()
    if wanted not in ENVIRONMENTS:
        raise wallet_fault(
            "wallet_environment_inactive", authority=AUTHORITY,
            context={"reason": "unknown_environment", "environment": wanted[:32], "environments": list(ENVIRONMENTS)},
            source_context=source_context,
        )
    before = active_environment()
    _write(wanted, set_by="owner", only_if_absent=False)
    from core.wallet import quotes

    # an open preview never crosses a switch: the next approval needs a fresh chain-qualified quote
    quotes.supersede_open_quotes(reason="environment_changed")
    from core.wallet import receipts

    receipts.journal_security_event("wallet_environment_changed", {"from": before.environment, "to": wanted, "previous_source": before.source}, source_context=source_context)
    return active_environment()


def require_active(network: Any, *, source_context: dict[str, Any] | None = None) -> Any:
    """The gate every new effect passes: the row must belong to the active environment. Returns the row.
    A derived default is pinned by the first effect that passes, so it cannot drift afterwards."""
    from core.wallet import chains

    spec = chains.resolve_network(network)
    state = active_environment()
    if spec.environment != state.environment:
        raise wallet_fault(
            "wallet_environment_inactive", authority=AUTHORITY,
            context={
                "reason": "row_outside_active_environment", "network": spec.network,
                "network_environment": spec.environment, "active_environment": state.environment,
            },
            source_context=source_context,
        )
    if state.source in _DERIVED_SOURCES:
        _write(state.environment, set_by=state.source, only_if_absent=True)
    return spec


def is_active(network: Any) -> bool:
    from core.wallet import chains

    try:
        return chains.resolve_network(network).environment == active_environment().environment
    except Exception:
        return False


def presentation(spec: Any) -> dict[str, str]:
    """The environment words every surface shows beside a row: one authority, never a JS literal."""
    if spec.environment == ENVIRONMENT_MAINNET:
        return {"badge": "MAINNET", "environment_label": "Mainnet", "value_note": "Real funds"}
    return {
        "badge": "DEVNET" if spec.is_svm else "TESTNET",
        "environment_label": "Devnet" if spec.is_svm else "Testnet",
        "value_note": "Test funds have no monetary value",
    }


__all__ = [
    "ENVIRONMENTS",
    "ENVIRONMENT_ENV",
    "ENVIRONMENT_MAINNET",
    "ENVIRONMENT_TESTNET",
    "SOURCE_FRESH",
    "SOURCE_OVERRIDE",
    "SOURCE_STORED",
    "SOURCE_UPGRADE",
    "EnvironmentState",
    "active_environment",
    "is_active",
    "presentation",
    "require_active",
    "set_active_environment",
]
