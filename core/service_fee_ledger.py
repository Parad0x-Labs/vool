"""The service-fee ledger of the money law: exact, durable, sub-atomic fee accounting and bounded collection.

The native DNA x402 route charges a fee of ``rate_bps`` basis points on each ACCEPTED provider payment. At 10 bps a
112-atomic-unit payment owes 0.112 atomic units -- a fraction no integer line can carry. This ledger keeps every
obligation as an exact integer NUMERATOR in units of ``1/SCALE`` of one atomic unit (``basis_atomic * rate_bps``,
``SCALE == 10_000``), so a thousand such payments account for exactly 112 whole units and nothing is ever floored
away per call. No float touches money here.

Ownership and boundaries:

* ACCRUAL is written by :func:`core.effect_budget_money.settle_liability` inside the money law's own settlement
  transaction, once per liability (a replayed settlement is idempotent there and finds the accrual here). Nothing
  accrues for a reserved, dispatching, pending or unknown liability: reservation is not revenue.
* REVERSAL takes provider or operator evidence naming the refunded payment and can never exceed the operation's own
  accrual: one operation's refund cannot erase another operation's debt, and a reversal creates no spendable credit.
* COLLECTION is a separate whole-unit transfer to the treasury through the Crypto Pilot lane (see
  :mod:`core.wallet.dna_fees`). A collection record is bound to one identity, one amount, one treasury, the accrual
  generation it covers and the network-fee ceiling the owner saw; it holds its amount from the moment it is offered,
  retires it exactly once on chain confirmation, keeps custody while a transfer is submitted or unknown, and gives the
  debt back when the transfer fails or is released. Exactly one open collection exists per identity.

Every function takes the caller's connection: the money law calls in from its BEGIN IMMEDIATE transaction, the wallet
lane from its claim and settlement transactions. Both stores are one SQLite file (``storage.db``), so each stage is
atomic with the record it belongs to.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any

SCALE = 10_000
#: The approved rate of the native DNA x402 service fee: exactly 10 basis points. Not configurable by a skill, a
#: model, a request or a setting; a different rate is a different fee policy version.
DNA_NATIVE_FEE_BPS = 10
DNA_NATIVE_FEE_POLICY_ID = "dna_native_x402_fee:v1"

ENTRY_ACCRUAL = "accrual"
ENTRY_REVERSAL = "reversal"

COLLECTION_OFFERED = "offered"
COLLECTION_CLAIMED = "claimed"
COLLECTION_SUBMITTED = "submitted"
COLLECTION_CONFIRMED = "confirmed"
COLLECTION_FAILED = "failed"
COLLECTION_RELEASED = "released"
OPEN_COLLECTION_STATES = (COLLECTION_OFFERED, COLLECTION_CLAIMED, COLLECTION_SUBMITTED)
#: A submitted transfer may still land: its collection keeps custody of the amount until the chain answers.
CUSTODY_STATES = (COLLECTION_CLAIMED, COLLECTION_SUBMITTED)

ENTRIES_TABLE = "effect_budget_money_service_fee_entries"
COLLECTIONS_TABLE = "effect_budget_money_service_fee_collections"

_SIGNED_ATOMIC_RE = re.compile(r"-?(0|[1-9][0-9]{0,77})")


class ServiceFeeLedgerError(RuntimeError):
    """A typed refusal of the fee ledger; ``code`` names the rule."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail or "")
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)


# --- exact arithmetic ---------------------------------------------------------------------------------------


def fee_numerator(basis_atomic: int, rate_bps: int) -> int:
    """The exact fee on ``basis_atomic`` at ``rate_bps``, in 1/SCALE atomic units. Integer only, never floored."""
    basis = _require_int(basis_atomic, what="fee basis", allow_zero=True)
    rate = _require_int(rate_bps, what="fee rate", allow_zero=True)
    return basis * rate


def fee_ceiling_atomic(basis_atomic: int, rate_bps: int) -> int:
    """The smallest whole number of atomic units that covers the exact fee: the line a reservation holds."""
    numerator = fee_numerator(basis_atomic, rate_bps)
    return -(-numerator // SCALE)


def whole_atomic(numerator: int) -> int:
    """The representable (whole) atomic units of a non-negative numerator; a negative numerator has none."""
    return int(numerator) // SCALE if int(numerator) >= 0 else 0


def carry_of(numerator: int) -> int:
    """The sub-atomic remainder of a non-negative numerator, retained for later calls."""
    return int(numerator) % SCALE if int(numerator) >= 0 else 0


def numerator_decimal(numerator: int, decimals: int) -> str:
    """The exact decimal of a numerator in the asset's own unit: 1120 at 6 decimals is ``0.000000112``.

    Exact: the value has ``decimals + 4`` places before trailing zeros are removed; nothing is rounded."""
    value = int(numerator)
    places = int(decimals) + 4
    sign = "-" if value < 0 else ""
    digits = str(abs(value)).rjust(places + 1, "0")
    whole, fraction = digits[:-places], digits[-places:]
    fraction = fraction.rstrip("0")
    return f"{sign}{whole}.{fraction}" if fraction else f"{sign}{whole}"


def numerator_atomic_text(numerator: int) -> str:
    """The exact value in ATOMIC units with its fraction: 1120 is ``0.112`` (of one atomic unit)."""
    return numerator_decimal(numerator, 0)


#: The identity key's separator. None of the five parts may contain whitespace (account, network, asset-key and
#: policy identifiers are whitespace-free by the money law's own regexes; a treasury owner is base58), so a spaced
#: separator can never occur inside a part -- the asset key's own ``|`` can.
IDENTITY_SEPARATOR = " // "


def identity_key(*, payer_account: str, network: str, asset_key: str, treasury_owner: str, policy_id: str) -> str:
    """The aggregation identity: fees add up only within one payer, one network, one asset, one treasury owner and one
    fee policy. A changed treasury or policy starts a new identity; old accrual never migrates."""
    parts = (str(payer_account or ""), str(network or ""), str(asset_key or ""), str(treasury_owner or ""), str(policy_id or ""))
    if not all(parts) or any(any(ch.isspace() for ch in part) for part in parts):
        raise ServiceFeeLedgerError("service_fee_identity_incomplete", "payer, network, asset, treasury and policy are all required and whitespace-free")
    return IDENTITY_SEPARATOR.join(parts)


def identity_parts(key: str) -> tuple[str, str, str, str, str]:
    """(payer_account, network, asset_key, treasury_owner, policy_id) of an identity key, or a typed refusal."""
    parts = str(key or "").split(IDENTITY_SEPARATOR)
    if len(parts) != 5 or not all(parts):
        raise ServiceFeeLedgerError("service_fee_identity_incomplete", "identity key must carry five parts")
    return parts[0], parts[1], parts[2], parts[3], parts[4]


def _require_int(value: Any, *, what: str, allow_zero: bool = False, allow_negative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ServiceFeeLedgerError("service_fee_invalid_amount", f"{what} must be an integer, not {type(value).__name__}")
    if value < 0 and not allow_negative:
        raise ServiceFeeLedgerError("service_fee_invalid_amount", f"{what} must not be negative")
    if value == 0 and not allow_zero:
        raise ServiceFeeLedgerError("service_fee_invalid_amount", f"{what} must be positive")
    return value


def _text(value: int) -> str:
    return str(int(value))


def _value(text: Any, *, what: str) -> int:
    raw = "" if text is None else str(text)
    if not _SIGNED_ATOMIC_RE.fullmatch(raw):
        raise ServiceFeeLedgerError("service_fee_store_corrupt", f"{what} is not a canonical amount")
    return int(raw)


def _now() -> float:
    return time.time()


# --- schema ---------------------------------------------------------------------------------------------


def ensure_tables(conn: Any) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ENTRIES_TABLE} (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_key TEXT NOT NULL,
            liability_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            basis_atomic TEXT NOT NULL,
            rate_bps INTEGER NOT NULL,
            numerator TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_kind TEXT NOT NULL,
            source TEXT NOT NULL,
            payer_account TEXT NOT NULL,
            network TEXT NOT NULL,
            asset_key TEXT NOT NULL,
            treasury_owner TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            created_epoch REAL NOT NULL
        )
        """
    )
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{ENTRIES_TABLE}_once ON {ENTRIES_TABLE}(liability_id, kind, evidence_id)")
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{ENTRIES_TABLE}_accrual ON {ENTRIES_TABLE}(liability_id) WHERE kind = 'accrual'")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{ENTRIES_TABLE}_identity ON {ENTRIES_TABLE}(identity_key)")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {COLLECTIONS_TABLE} (
            collection_id TEXT PRIMARY KEY,
            identity_key TEXT NOT NULL,
            payer_account TEXT NOT NULL,
            network TEXT NOT NULL,
            asset_key TEXT NOT NULL,
            treasury_owner TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            amount_atomic TEXT NOT NULL,
            through_entry_id INTEGER NOT NULL,
            owed_numerator_at_offer TEXT NOT NULL,
            state TEXT NOT NULL,
            state_version INTEGER NOT NULL DEFAULT 1,
            proposal_id TEXT NOT NULL DEFAULT '',
            wallet_id TEXT NOT NULL DEFAULT '',
            quote_id TEXT NOT NULL DEFAULT '',
            network_fee_max_atomic TEXT NOT NULL DEFAULT '0',
            network_fee_asset TEXT NOT NULL DEFAULT '',
            tx_signature TEXT NOT NULL DEFAULT '',
            offered_epoch REAL NOT NULL,
            claimed_epoch REAL,
            resolved_epoch REAL,
            expires_epoch REAL NOT NULL DEFAULT 0,
            close_reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{COLLECTIONS_TABLE}_open ON {COLLECTIONS_TABLE}(identity_key) "
        "WHERE state IN ('offered', 'claimed', 'submitted')"
    )
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{COLLECTIONS_TABLE}_proposal ON {COLLECTIONS_TABLE}(proposal_id) WHERE proposal_id <> ''")
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({COLLECTIONS_TABLE})").fetchall()}
    if "payment_proposal_id" not in columns:
        # a collection collected WITH a provider payment names that payment: one bound approval covers both
        conn.execute(f"ALTER TABLE {COLLECTIONS_TABLE} ADD COLUMN payment_proposal_id TEXT NOT NULL DEFAULT ''")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{COLLECTIONS_TABLE}_payment ON {COLLECTIONS_TABLE}(payment_proposal_id)")


# --- reads ----------------------------------------------------------------------------------------------


def _row(cursor: Any, row: Any) -> dict[str, Any]:
    return {description[0]: row[index] for index, description in enumerate(cursor.description)}


def owed(conn: Any, key: str) -> dict[str, Any]:
    """The identity's position now: exact accrued numerator, what confirmed collections retired, what open collections
    hold, the representable whole units and the retained carry. Nothing here is rounded or estimated."""
    accrued = 0
    last_entry = 0
    for row in conn.execute(f"SELECT entry_id, numerator FROM {ENTRIES_TABLE} WHERE identity_key = ?", (str(key),)).fetchall():
        accrued += _value(row[1], what="entry numerator")
        last_entry = max(last_entry, int(row[0]))
    retired = 0
    held = 0
    open_states = ", ".join("?" for _ in OPEN_COLLECTION_STATES)
    for row in conn.execute(f"SELECT state, amount_atomic FROM {COLLECTIONS_TABLE} WHERE identity_key = ?", (str(key),)).fetchall():
        amount = _value(row[1], what="collection amount")
        if str(row[0]) == COLLECTION_CONFIRMED:
            retired += amount * SCALE
        elif str(row[0]) in OPEN_COLLECTION_STATES:
            held += amount
    del open_states
    net = accrued - retired
    representable = whole_atomic(net)
    return {
        "identity_key": str(key),
        "accrued_numerator": accrued,
        "retired_numerator": retired,
        "net_numerator": net,
        "owed_atomic": representable,
        "carry_numerator": carry_of(net),
        "overcollected_numerator": max(-net, 0),
        "held_by_open_collections_atomic": held,
        "collectible_atomic": max(representable - held, 0),
        "last_entry_id": last_entry,
    }


def entries_for_liability(conn: Any, liability_id: str) -> list[dict[str, Any]]:
    cursor = conn.execute(f"SELECT * FROM {ENTRIES_TABLE} WHERE liability_id = ? ORDER BY entry_id", (str(liability_id),))
    return [_row(cursor, row) for row in cursor.fetchall()]


def accrual_for_liability(conn: Any, liability_id: str) -> dict[str, Any] | None:
    cursor = conn.execute(f"SELECT * FROM {ENTRIES_TABLE} WHERE liability_id = ? AND kind = ?", (str(liability_id), ENTRY_ACCRUAL))
    row = cursor.fetchone()
    return _row(cursor, row) if row is not None else None


def open_collection(conn: Any, key: str) -> dict[str, Any] | None:
    placeholders = ", ".join("?" for _ in OPEN_COLLECTION_STATES)
    cursor = conn.execute(f"SELECT * FROM {COLLECTIONS_TABLE} WHERE identity_key = ? AND state IN ({placeholders})", (str(key), *OPEN_COLLECTION_STATES))
    row = cursor.fetchone()
    return _row(cursor, row) if row is not None else None


def collection(conn: Any, collection_id: str) -> dict[str, Any] | None:
    cursor = conn.execute(f"SELECT * FROM {COLLECTIONS_TABLE} WHERE collection_id = ?", (str(collection_id),))
    row = cursor.fetchone()
    return _row(cursor, row) if row is not None else None


def collection_for_proposal(conn: Any, proposal_id: str) -> dict[str, Any] | None:
    if not str(proposal_id or ""):
        return None
    cursor = conn.execute(f"SELECT * FROM {COLLECTIONS_TABLE} WHERE proposal_id = ?", (str(proposal_id),))
    row = cursor.fetchone()
    return _row(cursor, row) if row is not None else None


def collection_for_payment(conn: Any, payment_proposal_id: str, *, open_only: bool = True) -> dict[str, Any] | None:
    """The collection riding WITH one provider payment's approval: the open one (offered, claimed or submitted) by
    default, else the newest record bound to that payment whatever its state."""
    if not str(payment_proposal_id or ""):
        return None
    if open_only:
        placeholders = ", ".join("?" for _ in OPEN_COLLECTION_STATES)
        cursor = conn.execute(
            f"SELECT * FROM {COLLECTIONS_TABLE} WHERE payment_proposal_id = ? AND state IN ({placeholders}) ORDER BY offered_epoch DESC LIMIT 1",
            (str(payment_proposal_id), *OPEN_COLLECTION_STATES),
        )
    else:
        cursor = conn.execute(f"SELECT * FROM {COLLECTIONS_TABLE} WHERE payment_proposal_id = ? ORDER BY offered_epoch DESC LIMIT 1", (str(payment_proposal_id),))
    row = cursor.fetchone()
    return _row(cursor, row) if row is not None else None


def identities(conn: Any) -> list[str]:
    keys = {str(row[0]) for row in conn.execute(f"SELECT DISTINCT identity_key FROM {ENTRIES_TABLE}").fetchall()}
    keys |= {str(row[0]) for row in conn.execute(f"SELECT DISTINCT identity_key FROM {COLLECTIONS_TABLE}").fetchall()}
    return sorted(keys)


def collections(conn: Any, *, key: str = "", limit: int = 50) -> list[dict[str, Any]]:
    if key:
        cursor = conn.execute(f"SELECT * FROM {COLLECTIONS_TABLE} WHERE identity_key = ? ORDER BY offered_epoch DESC LIMIT ?", (str(key), max(1, int(limit))))
    else:
        cursor = conn.execute(f"SELECT * FROM {COLLECTIONS_TABLE} ORDER BY offered_epoch DESC LIMIT ?", (max(1, int(limit)),))
    return [_row(cursor, row) for row in cursor.fetchall()]


# --- accrual and reversal ---------------------------------------------------------------------------------


def accrue(
    conn: Any,
    *,
    liability_id: str,
    operation_id: str,
    payer_account: str,
    network: str,
    asset_key: str,
    treasury_owner: str,
    policy_id: str,
    basis_atomic: int,
    rate_bps: int,
    evidence_id: str,
    evidence_kind: str,
    source: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Record the exact fee one ACCEPTED provider payment owes. Idempotent per liability: a second call for the same
    liability returns the existing entry and adds nothing (a retried settlement never accrues twice)."""
    key = identity_key(payer_account=payer_account, network=network, asset_key=asset_key, treasury_owner=treasury_owner, policy_id=policy_id)
    existing = accrual_for_liability(conn, liability_id)
    if existing is not None:
        if str(existing["identity_key"]) != key or _value(existing["basis_atomic"], what="basis") != int(basis_atomic):
            raise ServiceFeeLedgerError("service_fee_accrual_conflict", f"liability {liability_id} already accrued under different terms")
        position = owed(conn, key)
        return {**existing, "idempotent": True, "newly_representable_atomic": 0, "owed_after": position}
    _require_int(basis_atomic, what="accrual basis")
    numerator = fee_numerator(basis_atomic, rate_bps)
    before = owed(conn, key)
    stamp = float(now if now is not None else _now())
    conn.execute(
        f"INSERT INTO {ENTRIES_TABLE} (identity_key, liability_id, operation_id, kind, basis_atomic, rate_bps, numerator, evidence_id, evidence_kind, source, "
        "payer_account, network, asset_key, treasury_owner, policy_id, created_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, str(liability_id), str(operation_id), ENTRY_ACCRUAL, _text(basis_atomic), int(rate_bps), _text(numerator), str(evidence_id), str(evidence_kind), str(source),
         str(payer_account), str(network), str(asset_key), str(treasury_owner), str(policy_id), stamp),
    )
    after = owed(conn, key)
    entry = accrual_for_liability(conn, liability_id) or {}
    return {**entry, "idempotent": False, "newly_representable_atomic": max(after["owed_atomic"] - before["owed_atomic"], 0), "owed_before": before, "owed_after": after}


def reverse(
    conn: Any,
    *,
    liability_id: str,
    refunded_basis_atomic: int,
    evidence_id: str,
    evidence_kind: str,
    source: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Reverse the fee on a PROVEN refund of (part of) one operation's payment. Bounded by that operation's own
    accrual net of earlier reversals: it can neither touch another operation's debt nor create spendable credit.
    Idempotent per evidence id."""
    accrual = accrual_for_liability(conn, liability_id)
    if accrual is None:
        raise ServiceFeeLedgerError("service_fee_nothing_to_reverse", f"liability {liability_id} never accrued a service fee")
    prior = conn.execute(
        f"SELECT numerator, basis_atomic FROM {ENTRIES_TABLE} WHERE liability_id = ? AND kind = ? AND evidence_id = ?",
        (str(liability_id), ENTRY_REVERSAL, str(evidence_id)),
    ).fetchone()
    refunded = _require_int(refunded_basis_atomic, what="refunded amount")
    if prior is not None:
        if _value(prior[1], what="reversal basis") != refunded:
            raise ServiceFeeLedgerError("service_fee_reversal_conflict", f"evidence {evidence_id} already reversed a different amount")
        return {"liability_id": str(liability_id), "idempotent": True, "numerator": _value(prior[0], what="reversal numerator")}
    already = 0
    for row in conn.execute(f"SELECT basis_atomic FROM {ENTRIES_TABLE} WHERE liability_id = ? AND kind = ?", (str(liability_id), ENTRY_REVERSAL)).fetchall():
        already += _value(row[0], what="reversal basis")
    basis = _value(accrual["basis_atomic"], what="accrual basis")
    if already + refunded > basis:
        raise ServiceFeeLedgerError(
            "service_fee_reversal_exceeds_accrual",
            f"a refund of {refunded} on top of {already} already reversed exceeds the {basis} this operation paid",
        )
    numerator = -fee_numerator(refunded, int(accrual["rate_bps"]))
    stamp = float(now if now is not None else _now())
    conn.execute(
        f"INSERT INTO {ENTRIES_TABLE} (identity_key, liability_id, operation_id, kind, basis_atomic, rate_bps, numerator, evidence_id, evidence_kind, source, "
        "payer_account, network, asset_key, treasury_owner, policy_id, created_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(accrual["identity_key"]), str(liability_id), str(accrual["operation_id"]), ENTRY_REVERSAL, _text(refunded), int(accrual["rate_bps"]), _text(numerator),
         str(evidence_id), str(evidence_kind), str(source), str(accrual["payer_account"]), str(accrual["network"]), str(accrual["asset_key"]),
         str(accrual["treasury_owner"]), str(accrual["policy_id"]), stamp),
    )
    return {"liability_id": str(liability_id), "idempotent": False, "numerator": numerator, "owed_after": owed(conn, str(accrual["identity_key"]))}


# --- collections --------------------------------------------------------------------------------------------


def offer_collection(
    conn: Any,
    *,
    key: str,
    amount_atomic: int,
    expires_epoch: float,
    network_fee_max_atomic: int = 0,
    network_fee_asset: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Hold ``amount_atomic`` of the identity's collectible whole units for one collection. Refuses when an open
    collection already exists (exactly one per identity) or when the amount is not collectible now. The amount is
    bound to the accrual generation (``through_entry_id``) it was computed from."""
    amount = _require_int(amount_atomic, what="collection amount")
    existing = open_collection(conn, key)
    if existing is not None:
        raise ServiceFeeLedgerError("service_fee_collection_open", f"collection {existing['collection_id']} is {existing['state']}")
    position = owed(conn, key)
    if amount > position["collectible_atomic"]:
        raise ServiceFeeLedgerError("service_fee_not_collectible", f"{amount} requested, {position['collectible_atomic']} collectible")
    parts = identity_parts(str(key))
    stamp = float(now if now is not None else _now())
    collection_id = "sfc:" + secrets.token_hex(8)
    conn.execute(
        f"INSERT INTO {COLLECTIONS_TABLE} (collection_id, identity_key, payer_account, network, asset_key, treasury_owner, policy_id, amount_atomic, through_entry_id, "
        "owed_numerator_at_offer, state, network_fee_max_atomic, network_fee_asset, offered_epoch, expires_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (collection_id, str(key), parts[0], parts[1], parts[2], parts[3], parts[4], _text(amount), int(position["last_entry_id"]), _text(position["net_numerator"]),
         COLLECTION_OFFERED, _text(max(0, int(network_fee_max_atomic))), str(network_fee_asset or ""), stamp, float(expires_epoch)),
    )
    return collection(conn, collection_id) or {}


def _cas(conn: Any, row: dict[str, Any], new_state: str, **sets: Any) -> dict[str, Any]:
    columns = {"state": new_state, **sets}
    assignments = ", ".join(f"{name} = ?" for name in columns)
    cursor = conn.execute(
        f"UPDATE {COLLECTIONS_TABLE} SET {assignments}, state_version = state_version + 1 WHERE collection_id = ? AND state = ? AND state_version = ?",
        (*columns.values(), str(row["collection_id"]), str(row["state"]), int(row["state_version"])),
    )
    if cursor.rowcount != 1:
        raise ServiceFeeLedgerError("service_fee_collection_conflict", f"collection {row['collection_id']} changed under this transition")
    return collection(conn, str(row["collection_id"])) or {}


def bind_collection(conn: Any, collection_id: str, *, proposal_id: str, wallet_id: str, payment_proposal_id: str = "") -> dict[str, Any]:
    """Bind an offered collection to the proposal that will pay it (once), and to the provider payment whose ONE
    approval covers it when it is collected with that payment."""
    row = collection(conn, collection_id)
    if row is None or str(row["state"]) != COLLECTION_OFFERED:
        raise ServiceFeeLedgerError("service_fee_collection_not_offered", str(collection_id))
    if str(row["proposal_id"] or ""):
        raise ServiceFeeLedgerError("service_fee_collection_already_bound", str(row["proposal_id"]))
    return _cas(conn, row, COLLECTION_OFFERED, proposal_id=str(proposal_id), wallet_id=str(wallet_id), payment_proposal_id=str(payment_proposal_id or ""))


def claim_collection(
    conn: Any,
    *,
    proposal_id: str,
    quote_id: str,
    amount_atomic: int,
    treasury_owner: str,
    network: str,
    asset_key: str,
    network_fee_max_atomic: int,
    now: float | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """The approval's binding, inside the wallet's claim transaction: the offered collection bound to THIS proposal
    moves to claimed only when every bound fact still holds -- the amount, the treasury, the network, the asset, an
    unexpired offer, a fee ceiling no higher than the one the offer named, the state version the approval saw (when
    it names one), and an accrual position that still owes at least the amount. A competing proposal can neither
    claim nor release it."""
    row = collection_for_proposal(conn, proposal_id)
    if row is None:
        raise ServiceFeeLedgerError("service_fee_collection_unbound", f"no collection is bound to proposal {proposal_id}")
    if str(row["state"]) != COLLECTION_OFFERED:
        raise ServiceFeeLedgerError("service_fee_collection_not_offered", f"collection {row['collection_id']} is {row['state']}")
    if expected_version is not None and int(row["state_version"]) != int(expected_version):
        raise ServiceFeeLedgerError("service_fee_collection_version_changed", f"collection {row['collection_id']} is at version {row['state_version']}, the approval saw {int(expected_version)}")
    stamp = float(now if now is not None else _now())
    if float(row["expires_epoch"] or 0) and float(row["expires_epoch"]) <= stamp:
        raise ServiceFeeLedgerError("service_fee_collection_expired", str(row["collection_id"]))
    if _value(row["amount_atomic"], what="collection amount") != _require_int(amount_atomic, what="claimed amount"):
        raise ServiceFeeLedgerError("service_fee_collection_amount_changed", f"{amount_atomic} claimed, {row['amount_atomic']} offered")
    if str(row["treasury_owner"]) != str(treasury_owner) or str(row["network"]) != str(network) or str(row["asset_key"]) != str(asset_key):
        raise ServiceFeeLedgerError("service_fee_collection_binding_changed", "treasury, network or asset differ from the offer")
    ceiling = _value(row["network_fee_max_atomic"], what="fee ceiling")
    if ceiling and int(network_fee_max_atomic) > ceiling:
        raise ServiceFeeLedgerError("service_fee_collection_fee_ceiling_exceeded", f"{network_fee_max_atomic} > {ceiling}")
    position = owed(conn, str(row["identity_key"]))
    others_held = position["held_by_open_collections_atomic"] - _value(row["amount_atomic"], what="collection amount")
    if position["owed_atomic"] - max(others_held, 0) < _value(row["amount_atomic"], what="collection amount"):
        raise ServiceFeeLedgerError("service_fee_accrual_changed", f"only {position['owed_atomic']} owed now; the offer of {row['amount_atomic']} is stale")
    # the claim binds the network-fee ceiling the owner approved on the sheet (an offer names none until then)
    return _cas(conn, row, COLLECTION_CLAIMED, quote_id=str(quote_id), claimed_epoch=stamp, network_fee_max_atomic=_text(max(0, int(network_fee_max_atomic))))


def mark_submitted(conn: Any, *, proposal_id: str) -> dict[str, Any] | None:
    """claimed -> submitted: the bytes left. Custody stays with the collection until the chain answers."""
    row = collection_for_proposal(conn, proposal_id)
    if row is None:
        return None
    if str(row["state"]) == COLLECTION_SUBMITTED:
        return row
    if str(row["state"]) != COLLECTION_CLAIMED:
        raise ServiceFeeLedgerError("service_fee_collection_not_claimed", f"collection {row['collection_id']} is {row['state']}")
    return _cas(conn, row, COLLECTION_SUBMITTED)


def confirm_collection(conn: Any, *, proposal_id: str, tx_signature: str, now: float | None = None) -> dict[str, Any] | None:
    """The chain confirmed the transfer: retire exactly this collection's amount, once. Idempotent per signature."""
    row = collection_for_proposal(conn, proposal_id)
    if row is None:
        return None
    if str(row["state"]) == COLLECTION_CONFIRMED:
        if str(row["tx_signature"] or "") != str(tx_signature or ""):
            raise ServiceFeeLedgerError("service_fee_collection_conflict", "confirmed under another signature")
        return row
    if str(row["state"]) not in CUSTODY_STATES:
        raise ServiceFeeLedgerError("service_fee_collection_not_in_custody", f"collection {row['collection_id']} is {row['state']}")
    stamp = float(now if now is not None else _now())
    return _cas(conn, row, COLLECTION_CONFIRMED, tx_signature=str(tx_signature or ""), resolved_epoch=stamp, close_reason="confirmed_on_chain")


def fail_collection(conn: Any, *, proposal_id: str, reason: str, now: float | None = None) -> dict[str, Any] | None:
    """The chain executed the transfer and it failed: nothing was collected; the debt is given back."""
    row = collection_for_proposal(conn, proposal_id)
    if row is None or str(row["state"]) == COLLECTION_FAILED:
        return row
    if str(row["state"]) not in CUSTODY_STATES:
        raise ServiceFeeLedgerError("service_fee_collection_not_in_custody", f"collection {row['collection_id']} is {row['state']}")
    stamp = float(now if now is not None else _now())
    return _cas(conn, row, COLLECTION_FAILED, resolved_epoch=stamp, close_reason=str(reason or "failed_on_chain")[:120])


def release_collection(
    conn: Any, *, proposal_id: str = "", collection_id: str = "", reason: str, now: float | None = None,
    expected_state: str | None = None, expected_version: int | None = None,
) -> dict[str, Any] | None:
    """An offer or a claim that ends before anything was sent gives its amount back. A SUBMITTED collection is never
    released here: its transfer may still land, so custody stays until the chain answers.

    ``expected_state`` / ``expected_version`` are the fence for a caller that decided on a SNAPSHOT (an expiry read
    on another connection): the release happens only if the row is still what that caller saw; a claim that landed in
    between wins and this raises ``service_fee_collection_conflict`` instead of releasing the winner's custody. The
    transfer owner's release of its own claimed, proven-never-sent collection passes no fence."""
    row = collection_for_proposal(conn, proposal_id) if proposal_id else collection(conn, collection_id)
    if row is None or str(row["state"]) == COLLECTION_RELEASED:
        return row
    if expected_state is not None and str(row["state"]) != str(expected_state):
        raise ServiceFeeLedgerError("service_fee_collection_conflict", f"collection {row['collection_id']} is {row['state']}, not {expected_state}: another request owns it now")
    if expected_version is not None and int(row["state_version"]) != int(expected_version):
        raise ServiceFeeLedgerError("service_fee_collection_conflict", f"collection {row['collection_id']} moved to version {row['state_version']} since the snapshot at {int(expected_version)}")
    if str(row["state"]) not in (COLLECTION_OFFERED, COLLECTION_CLAIMED):
        raise ServiceFeeLedgerError("service_fee_collection_in_custody", f"collection {row['collection_id']} is {row['state']}; it is reconciled, never released")
    stamp = float(now if now is not None else _now())
    return _cas(conn, row, COLLECTION_RELEASED, resolved_epoch=stamp, close_reason=str(reason or "released")[:120])


def expire_offer(conn: Any, *, collection_id: str, now: float | None = None) -> dict[str, Any] | None:
    """Expiration under the authoritative transaction: ONE compare-and-set that retires the offer only while it is
    still OFFERED and its expiry has passed. A claim that landed first keeps custody untouched (this returns None),
    and a caller that read a lapsed offer on another connection can never release what became someone's claim."""
    stamp = float(now if now is not None else _now())
    cursor = conn.execute(
        f"UPDATE {COLLECTIONS_TABLE} SET state = ?, state_version = state_version + 1, resolved_epoch = ?, close_reason = ? "
        "WHERE collection_id = ? AND state = ? AND expires_epoch > 0 AND expires_epoch <= ?",
        (COLLECTION_RELEASED, stamp, "offer_expired", str(collection_id), COLLECTION_OFFERED, stamp),
    )
    if cursor.rowcount != 1:
        return None
    return collection(conn, str(collection_id))


def fingerprint(row: dict[str, Any]) -> str:
    """A short public fingerprint of a collection's binding, for receipts and logs."""
    canonical = "|".join(str(row.get(name) or "") for name in ("collection_id", "identity_key", "amount_atomic", "through_entry_id", "treasury_owner"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "COLLECTIONS_TABLE",
    "COLLECTION_CLAIMED",
    "COLLECTION_CONFIRMED",
    "COLLECTION_FAILED",
    "COLLECTION_OFFERED",
    "collection_for_payment",
    "expire_offer",
    "COLLECTION_RELEASED",
    "COLLECTION_SUBMITTED",
    "CUSTODY_STATES",
    "DNA_NATIVE_FEE_BPS",
    "DNA_NATIVE_FEE_POLICY_ID",
    "ENTRIES_TABLE",
    "ENTRY_ACCRUAL",
    "ENTRY_REVERSAL",
    "OPEN_COLLECTION_STATES",
    "SCALE",
    "ServiceFeeLedgerError",
    "accrual_for_liability",
    "accrue",
    "bind_collection",
    "carry_of",
    "claim_collection",
    "collection",
    "collection_for_proposal",
    "collections",
    "confirm_collection",
    "ensure_tables",
    "entries_for_liability",
    "fail_collection",
    "fee_ceiling_atomic",
    "fee_numerator",
    "IDENTITY_SEPARATOR",
    "fingerprint",
    "identities",
    "identity_key",
    "identity_parts",
    "mark_submitted",
    "numerator_atomic_text",
    "numerator_decimal",
    "offer_collection",
    "open_collection",
    "owed",
    "release_collection",
    "reverse",
    "whole_atomic",
]
