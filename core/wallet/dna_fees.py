"""The native DNA x402 service fee, on the wallet side: the approved policy, the designated treasury, the collection
threshold, and the economical collection of accrued fees WITH the next native payment, under that payment's ONE
bound approval.

The policy (docs: DNA-NATIVE-FEE-POLICY, owner-approved 2026-09-15; AUTOMATIC-COLLECTION-ADDENDUM 2026-09-16): VOOL's
built-in accountless x402 route charges exactly 10 basis points on each ACCEPTED provider payment, shown apart from the
provider payment and apart from the network fee. Nothing else acquires it: ordinary wallet sends, wallet creation, local
inference and prepaid or BYOK token calls carry no service fee. The rate and the policy id are constants of
:mod:`core.service_fee_ledger`; no request, skill, model or setting changes them.

Who owns what:

* ACCRUAL is the money law's (:func:`core.effect_budget_money.settle_liability`): the exact fraction is written in the
  same transaction that proves the payment accepted, or admitted once a later, stronger proof arrives. This module
  only reads the position.
* The TREASURY is the owner-designated Solana wallet ``9vDnXsPonRJa7yAmvwRGMAdxt8W13Qbm7HZuvauM3Ya3``, bound here to
  the Solana Mainnet row as the treasury OWNER key (never a token-account address); the USDC destination is the
  owner's associated token account, derived and verified on the chain before any collection is planned or claimed.
  ``VOOL_DNA_FEE_TREASURY_OWNER`` is the operator's process-level override for disposable fixture recipients in
  proofs; it is validated as a 32-byte key and labelled as an override everywhere it is shown. A missing, malformed or
  mismatched treasury refuses collection, typed.
* COLLECTION moves whole atomic units of PRIOR accepted payments' debt, never a projected fee, and rides the NEXT
  native x402 payment's approval: the quote plans it (:func:`plan_companion_collection`) when the accrued amount
  reaches the Settings threshold (``/api/wallet/dna-fees/policy``; ``VOOL_DNA_FEE_COLLECT_MIN_ATOMIC`` is the
  operator's process-level override) AND the collection's maximum network cost, valued under the operator's
  conservative conversion bound, is within ``COLLECTION_COST_BOUND_BPS`` of the amount, AND the treasury's account
  is open, AND the wallet carries both transactions under its own caps. The plan is a ledger offer bound to a
  companion proposal of origin ``dna_fee`` naming the payment; the payment's sheet shows both actions and their
  per-asset maxima; the payment's ONE credential claims both in one transaction, signs both messages in one signing
  session, and sends the collection only after the payment left. Two separately journalled transactions, one
  correctly scoped approval, never an atomic on-chain pair. A deferred collection keeps every unit of the debt.
  Nobody volunteers to pay fees through a separate card; ``Decide later`` authorizes nothing.
* EXPIRY of a planned collection happens only under the ledger's compare-and-set (:func:`service_fee_ledger.expire_offer`):
  a claim that landed first keeps custody, its proposal and its quote. A submitted transfer keeps custody until the
  chain answers; a confirmed one retires exactly its amount, once; a failed or released one gives the debt back.

Conservative documented defaults: collect USDC fees at 100,000 µUSDC (0.10 USDC) and SOL fees at 1,000,000 lamports
(0.001 SOL) accrued, and never when the collection's maximum network cost exceeds 1% of the amount collected. No claim
is made about current network prices: the cost bound reads the operator's declared conversion bound and defers when
none is in force.
"""
from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from core import service_fee_ledger as ledger
from core.wallet import chains, custody, proposals
from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.dna_fees"
POLICY_ID = ledger.DNA_NATIVE_FEE_POLICY_ID
RATE_BPS = ledger.DNA_NATIVE_FEE_BPS
RATE_LABEL = "0.1%"
USER_FACING_EXPLANATION = (
    "DNA service fee: 0.1% of the provider payment. Network fees are shown separately. "
    "Tiny fees accumulate and are collected with a later native payment, under that payment's approval, "
    "only when the collection is economical (its network cost at most 1% of the amount collected)."
)

#: The owner-designated treasury OWNER public key on Solana Mainnet (decodes to 32 bytes; control is owner-attested).
DESIGNATED_TREASURY_OWNER = "9vDnXsPonRJa7yAmvwRGMAdxt8W13Qbm7HZuvauM3Ya3"
DESIGNATED_TREASURY_NETWORK = chains.SOLANA_MAINNET
TREASURY_SOURCE_DESIGNATED = "designated_production_owner"
TREASURY_SOURCE_OVERRIDE = "operator_override_disposable_recipient"
TREASURY_OVERRIDE_ENV = "VOOL_DNA_FEE_TREASURY_OWNER"

COLLECT_MIN_ENV = "VOOL_DNA_FEE_COLLECT_MIN_ATOMIC"
DEFAULT_COLLECT_MIN_ATOMIC: dict[str, int] = {"USDC": 100_000, "SOL": 1_000_000}
FALLBACK_COLLECT_MIN_ATOMIC = 100_000
MAX_COLLECT_MIN_ATOMIC = 10**15
_CONTROL_KEY_PREFIX = "dna_fee.collect_min_atomic."
#: A planned collection nobody approves with its payment lapses; the debt it held goes back and a fresh plan follows
#: with a later payment. (``OFFER_TTL_SECONDS`` keeps the older name for the same lifetime.)
COMPANION_OFFER_TTL_SECONDS = 900.0
OFFER_TTL_SECONDS = COMPANION_OFFER_TTL_SECONDS
#: The cost bound of a collection: its maximum network cost, valued in the collected asset under the operator's
#: conservative conversion bound, may be at most this many basis points of the amount collected (pilot default 1%).
COLLECTION_COST_BOUND_BPS = 100
MEMO_PREFIX = "DNA service fee collection"
FEE_STATE_RESERVED = "reserved_not_owed_until_accepted"


def _fault(code: str, *, source_context: dict[str, Any] | None = None, **context: Any) -> WalletFault:
    return wallet_fault(code, authority=AUTHORITY, context=context, source_context=source_context)


def _is_32_byte_key(value: str) -> bool:
    try:
        from core.vool_wallet import b58decode

        return len(b58decode(str(value or "").strip())) == 32
    except Exception:
        return False


# --- policy and treasury ---------------------------------------------------------------------------------


def treasury_binding(network: str) -> dict[str, Any]:
    """The treasury OWNER bound to this row and where the binding came from. ``state`` is ``configured``,
    ``unconfigured`` (no treasury is designated for this row) or ``invalid`` (a malformed operator override)."""
    try:
        spec = chains.resolve_network(network)
    except Exception:
        return {"owner": "", "source": "", "state": "unconfigured", "reason": "not_a_declared_network", "network": str(network or "")}
    override = str(os.environ.get(TREASURY_OVERRIDE_ENV) or "").strip()
    if override:
        if not spec.is_svm or not chains.destination_matches_family(spec.network, override) or not _is_32_byte_key(override):
            return {"owner": "", "source": TREASURY_SOURCE_OVERRIDE, "state": "invalid", "reason": "override_not_a_32_byte_solana_key", "network": spec.network}
        return {"owner": override, "source": TREASURY_SOURCE_OVERRIDE, "state": "configured", "reason": "", "network": spec.network}
    if spec.network == DESIGNATED_TREASURY_NETWORK:
        return {"owner": DESIGNATED_TREASURY_OWNER, "source": TREASURY_SOURCE_DESIGNATED, "state": "configured", "reason": "", "network": spec.network}
    return {"owner": "", "source": "", "state": "unconfigured", "reason": "no_treasury_designated_for_this_row", "network": spec.network}


def fee_terms(network: str, asset: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The fee terms that apply to a native x402 payment of ``asset`` on ``network``: None when no treasury is bound to
    the row (no fee line is reserved there); a typed refusal when the treasury configuration is invalid."""
    binding = treasury_binding(network)
    if binding["state"] == "invalid":
        raise _fault("wallet_recipient_refused", source_context=source_context, reason="dna_fee_treasury_misconfigured", detail=binding["reason"], network=binding["network"])
    if binding["state"] != "configured":
        return None
    spec = chains.resolve_network(network)
    registered = chains.asset_for(spec.network, asset)
    from core.effect_budget_money import AssetIdentity

    asset_key = AssetIdentity(network=spec.network, asset=registered.symbol, decimals=int(registered.decimals)).key
    return {
        "policy_id": POLICY_ID,
        "rate_bps": RATE_BPS,
        "rate_label": RATE_LABEL,
        "network": spec.network,
        "asset": registered.symbol,
        "decimals": int(registered.decimals),
        "asset_key": asset_key,
        "mint": str(registered.address or ""),
        "treasury_owner": binding["owner"],
        "treasury_source": binding["source"],
    }


def identity_for(*, payer: str, network: str, asset: str, source_context: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]] | None:
    terms = fee_terms(network, asset, source_context=source_context)
    if terms is None:
        return None
    key = ledger.identity_key(payer_account=str(payer), network=terms["network"], asset_key=terms["asset_key"], treasury_owner=terms["treasury_owner"], policy_id=terms["policy_id"])
    return key, terms


def _collect_min_override_atomic() -> int | None:
    raw = str(os.environ.get(COLLECT_MIN_ENV) or "").strip()
    if raw.isdigit() and 0 < int(raw) <= MAX_COLLECT_MIN_ATOMIC:
        return int(raw)
    return None


def collect_min_settings_atomic(asset: str) -> int | None:
    """The owner's stored Settings value for one asset, or None when Settings never set one. It is not always the
    effective threshold: an operator override outranks it while that override is in force."""
    symbol = str(asset or "").strip().upper()
    with connection() as conn:
        row = conn.execute("SELECT value FROM wallet_controls WHERE key = ?", (_CONTROL_KEY_PREFIX + symbol,)).fetchone()
    if row and str(row[0]).isdigit() and 0 < int(str(row[0])) <= MAX_COLLECT_MIN_ATOMIC:
        return int(str(row[0]))
    return None


def collect_min_atomic(asset: str) -> int:
    """The EFFECTIVE collection threshold for one asset: the operator's process override, else the owner's Settings
    value, else the documented conservative default."""
    symbol = str(asset or "").strip().upper()
    override = _collect_min_override_atomic()
    if override is not None:
        return override
    saved = collect_min_settings_atomic(symbol)
    if saved is not None:
        return saved
    return DEFAULT_COLLECT_MIN_ATOMIC.get(symbol, FALLBACK_COLLECT_MIN_ATOMIC)


def set_collect_min_atomic(asset: str, value: Any, *, source_context: dict[str, Any] | None = None) -> int:
    """The trusted Settings door's write: a positive integer of the asset's atomic units, bounded."""
    symbol = str(asset or "").strip().upper()
    if symbol not in DEFAULT_COLLECT_MIN_ATOMIC:
        raise _fault("wallet_amount_invalid", source_context=source_context, reason="dna_fee_threshold_asset_unknown", asset=symbol[:12])
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > MAX_COLLECT_MIN_ATOMIC:
        raise _fault("wallet_amount_invalid", source_context=source_context, reason="dna_fee_threshold_not_a_positive_integer", asset=symbol)
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (_CONTROL_KEY_PREFIX + symbol, str(int(value)), utcnow()),
        )
    return int(value)


def collect_min_source(asset: str) -> str:
    """Where the EFFECTIVE threshold comes from: ``operator_override``, ``settings`` or ``default``."""
    if _collect_min_override_atomic() is not None:
        return "operator_override"
    return "settings" if collect_min_settings_atomic(asset) is not None else "default"


def collect_min_view(asset: str) -> dict[str, Any]:
    """What Settings and its door show for one asset: the EFFECTIVE threshold with its source, the owner's saved value
    (None when never set) and the documented default, so a saved value an operator override outranks is never shown
    as if it were in force, and the override is never shown as the owner's choice."""
    symbol = str(asset or "").strip().upper()
    return {
        "asset": symbol,
        "atomic": collect_min_atomic(symbol),
        "source": collect_min_source(symbol),
        "settings_atomic": collect_min_settings_atomic(symbol),
        "default_atomic": DEFAULT_COLLECT_MIN_ATOMIC.get(symbol, FALLBACK_COLLECT_MIN_ATOMIC),
    }


# --- the ledger's store --------------------------------------------------------------------------------------


@contextmanager
def _ledger_txn() -> Iterator[Any]:
    """One BEGIN IMMEDIATE transaction on the money law's store, where the fee ledger lives (the law wrote the
    accruals there in its settlement transactions). Commits on clean exit; rolls back on any error."""
    from core import effect_budget as _eb

    conn = _eb._budget_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ledger.ensure_tables(conn)
        yield conn
        conn.commit()
    except BaseException:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        with contextlib.suppress(Exception):
            conn.close()


@contextmanager
def _ledger_read() -> Iterator[Any]:
    from core import effect_budget as _eb

    conn = _eb._budget_connection()
    try:
        ledger.ensure_tables(conn)
        conn.commit()
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def stores_aligned() -> bool:
    """Whether the wallet store and the money law's store are ONE SQLite file. In a served process they are (both
    resolve to the runtime home's database); a test rig that re-points one of them breaks the atomicity the
    collection's claim and settlement rely on, so those two hooks refuse when this is False."""
    try:
        from core import runtime_continuity
        from storage.db import _resolve_db_path, active_default_db_path

        wallet_path = str(runtime_continuity._runtime_db_path())
        return os.path.realpath(_resolve_db_path(wallet_path)) == os.path.realpath(active_default_db_path())
    except Exception:
        return False


# --- positions -----------------------------------------------------------------------------------------------


def _position(conn: Any, key: str) -> dict[str, Any]:
    ledger.ensure_tables(conn)
    return ledger.owed(conn, key)


def position_for(*, payer: str, network: str, asset: str) -> dict[str, Any] | None:
    found = identity_for(payer=payer, network=network, asset=asset)
    if found is None:
        return None
    key, terms = found
    with _ledger_read() as conn:
        position = _position(conn, key)
        open_row = ledger.open_collection(conn, key)
    return {**position, "terms": terms, "open_collection": _public_collection(open_row)}


def _public_collection(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "collection_id": str(row["collection_id"]),
        "state": str(row["state"]),
        "amount_atomic": int(row["amount_atomic"]),
        "treasury_owner": str(row["treasury_owner"]),
        "network": str(row["network"]),
        "asset_key": str(row["asset_key"]),
        "proposal_id": str(row.get("proposal_id") or ""),
        "wallet_id": str(row.get("wallet_id") or ""),
        "through_entry_id": int(row["through_entry_id"]),
        "network_fee_max_atomic": int(row.get("network_fee_max_atomic") or 0),
        "tx_signature": str(row.get("tx_signature") or ""),
        "payment_proposal_id": str(row.get("payment_proposal_id") or ""),
        "state_version": int(row.get("state_version") or 0),
        "offered_at": float(row.get("offered_epoch") or 0),
        "expires_at": float(row.get("expires_epoch") or 0),
        "close_reason": str(row.get("close_reason") or ""),
        "fingerprint": ledger.fingerprint(row),
    }


def _decimals(asset_key: str) -> int:
    from core.effect_budget_money import AssetIdentity

    return int(AssetIdentity.from_key(asset_key).decimals)


def _exact(numerator: int, decimals: int) -> str:
    return ledger.numerator_decimal(numerator, decimals)


def _atomic_exact(atomic: int, decimals: int) -> str:
    from core.wallet import amounts

    return amounts.format_minor(int(atomic), decimals)


# --- previews -------------------------------------------------------------------------------------------------

COLLECTION_MODE = "collected_with_the_next_native_payment_when_economical"
COLLECTION_DEFERRED_UNTIL = "the next native x402 payment whose collection passes the threshold and the cost bound"


def payment_preview(proposal: Any, *, plan: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """What the approval sheet says about the DNA service fee of ONE native x402 provider payment: the exact fee on
    this payment (never a fake zero), the whole-unit ceiling the money law holds for it, the identity's accrued fees
    before and after, what is collectible now, the threshold, and whether previously accrued fees are collected WITH
    this payment (``plan``, from :func:`plan_companion_collection`) or stay accrued and why. None for anything that
    is not a UsePod x402 payment."""
    if str(getattr(proposal, "origin", "") or "") != proposals.ORIGIN_USEPOD:
        return None
    profile = custody.get_wallet(str(proposal.wallet_id))
    if profile is None:
        return None
    found = identity_for(payer=profile.public_key, network=str(proposal.network), asset=str(proposal.asset))
    if found is None:
        return None
    key, terms = found
    amount = int(proposal.amount_minor)
    decimals = int(terms["decimals"])
    numerator = ledger.fee_numerator(amount, RATE_BPS)
    ceiling = ledger.fee_ceiling_atomic(amount, RATE_BPS)
    with _ledger_read() as conn:
        position = _position(conn, key)
        open_row = ledger.open_collection(conn, key)
    after = position["net_numerator"] + numerator
    threshold = collect_min_atomic(terms["asset"])
    plan = dict(plan or {})
    companion = plan.get("companion") if isinstance(plan.get("companion"), dict) else None
    return {
        "policy_id": POLICY_ID,
        "rate_bps": RATE_BPS,
        "rate_label": RATE_LABEL,
        "basis": "the provider payment (the quoted cap, paid exactly); network fees are not part of the basis",
        "basis_atomic": amount,
        "basis_exact": _atomic_exact(amount, decimals),
        "asset": terms["asset"],
        "decimals": decimals,
        "fee_numerator": numerator,
        "fee_exact": _exact(numerator, decimals),
        "fee_exact_atomic": ledger.numerator_atomic_text(numerator),
        "fee_reserved_ceiling_atomic": ceiling,
        "fee_reserved_ceiling_exact": _atomic_exact(ceiling, decimals),
        "state": FEE_STATE_RESERVED,
        "treasury_owner": terms["treasury_owner"],
        "treasury_source": terms["treasury_source"],
        "accrued_before_numerator": position["net_numerator"],
        "accrued_before_exact": _exact(position["net_numerator"], decimals),
        "accrued_after_exact": _exact(after, decimals),
        "owed_before_atomic": position["owed_atomic"],
        "carry_before_numerator": position["carry_numerator"],
        "collectible_now_atomic": position["collectible_atomic"],
        "collectible_now_exact": _atomic_exact(position["collectible_atomic"], decimals),
        "collect_min_atomic": threshold,
        "collect_min_exact": _atomic_exact(threshold, decimals),
        "collect_min_source": collect_min_source(terms["asset"]),
        "open_collection": _public_collection(open_row),
        "collection": {
            "mode": COLLECTION_MODE,
            "with_this_payment": bool(plan.get("due")),
            "reason": str(plan.get("reason") or "not_planned"),
            "amount_atomic": int(companion["amount_minor"]) if companion else 0,
            "amount_exact": str(companion["amount_human"]) if companion else _atomic_exact(0, decimals),
            "cost_bound_bps": COLLECTION_COST_BOUND_BPS,
            "economics": plan.get("economics"),
            "deferred_until": COLLECTION_DEFERRED_UNTIL,
        },
        "explanation": USER_FACING_EXPLANATION,
    }


# --- economics ------------------------------------------------------------------------------------------------


def collection_economics(*, amount_atomic: int, asset_key: str, network_fee_max_atomic: int, fee_asset_key: str) -> dict[str, Any]:
    """BOTH gates of a collection except the threshold: the collection's maximum network cost, valued in the collected
    asset under the operator's conservative conversion bound (rounded up), may be at most ``COLLECTION_COST_BOUND_BPS``
    of the amount collected. A missing or expired bound, or an unknown cost, DEFERS (the debt stays). Never a claim
    about live prices: the bound is the operator's declared ceiling, expiring, read from the money law."""
    from core.effect_budget_money import AssetIdentity, conversion_bound_view, convert_up_bound

    amount = int(amount_atomic)
    collected = AssetIdentity.from_key(str(asset_key))
    fee_asset = AssetIdentity.from_key(str(fee_asset_key))
    facts: dict[str, Any] = {
        "amount_atomic": amount, "collected_asset": collected.asset, "network_fee_max_atomic": int(network_fee_max_atomic), "fee_asset": fee_asset.asset,
        "cost_bound_bps": COLLECTION_COST_BOUND_BPS, "cost_in_collected_asset_atomic": None, "cost_bps_of_amount": None, "conversion": None, "ok": False, "reason": "",
    }
    if amount < 1:
        return {**facts, "reason": "nothing_collectible"}
    if int(network_fee_max_atomic) < 0:
        return {**facts, "reason": "collection_fee_unknown"}
    if fee_asset.key == collected.key:
        cost = int(network_fee_max_atomic)
        facts["conversion"] = {"kind": "same_asset"}
    else:
        bound = conversion_bound_view(fee_asset, collected)
        if bound is None:
            return {**facts, "reason": "conversion_bound_missing"}
        cost = convert_up_bound(int(network_fee_max_atomic), fee_asset, collected)
        if cost is None:
            return {**facts, "reason": "conversion_bound_missing"}
        facts["conversion"] = {"kind": "operator_bound_rounded_up", **{k: bound[k] for k in ("numerator", "denominator", "expires_epoch")}}
    cost_bps = -(-int(cost) * ledger.SCALE // amount)  # ceiling: the cost is never understated
    ok = cost_bps <= COLLECTION_COST_BOUND_BPS
    return {**facts, "cost_in_collected_asset_atomic": int(cost), "cost_bps_of_amount": int(cost_bps), "ok": ok, "reason": "" if ok else "collection_cost_exceeds_bound"}


# --- the companion collection: planned at the quote, bound by the ONE approval ----------------------------------


def _treasury_can_receive(spec: Any, terms: dict[str, Any]) -> tuple[bool, str]:
    """A bounded chain read before an offer exists: the treasury's associated token account must already exist for
    a token fee (this lane never creates accounts or spends rent); a native fee needs only the owner key."""
    if not terms.get("mint"):
        return True, ""
    from core.wallet import lifecycle, svm_tokens

    try:
        rpc = lifecycle.RpcClient(chains.network_rpc_url(spec.network), network=spec.network)
        facts = svm_tokens.read_token_account(rpc, owner=terms["treasury_owner"], mint=terms["mint"], refusal_code="wallet_recipient_refused")
    except WalletFault as exc:
        return False, str(exc.context.get("reason") or exc.code)
    except Exception as exc:
        return False, f"treasury_unreadable:{type(exc).__name__}"
    if facts is None:
        return False, "treasury_token_account_missing"
    if facts.frozen:
        return False, "treasury_token_account_frozen"
    return True, ""


def collection_message(*, spec: Any, terms: dict[str, Any], payer: str, amount_atomic: int, blockhash: str) -> Any:
    """The one-instruction message that moves the collected amount from the payer to the treasury: a TransferChecked
    between the two associated token accounts for a token fee, a system transfer for a native (SOL) fee."""
    if terms.get("mint"):
        from core.wallet import svm_tokens

        return svm_tokens.build_transfer_message(
            payer=payer, recipient_owner=terms["treasury_owner"], asset=svm_tokens.token_asset(spec.network, terms["asset"]), amount_minor=int(amount_atomic), blockhash=blockhash,
        )
    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    owner = Pubkey.from_string(str(payer))
    instruction = transfer(TransferParams(from_pubkey=owner, to_pubkey=Pubkey.from_string(terms["treasury_owner"]), lamports=int(amount_atomic)))
    return Message.new_with_blockhash([instruction], owner, Hash.from_string(str(blockhash)))


def _collection_fee_max(rpc: Any, spec: Any, terms: dict[str, Any], *, payer: str, amount_atomic: int, blockhash: str) -> int | None:
    """The network fee the chain quotes for the collection message; None when the node cannot say (never zero)."""
    import base64

    try:
        message = collection_message(spec=spec, terms=terms, payer=payer, amount_atomic=amount_atomic, blockhash=blockhash)
        answer = rpc._call("getFeeForMessage", [base64.b64encode(bytes(message)).decode("ascii"), {"commitment": "confirmed"}])
    except Exception:
        return None
    value = (answer or {}).get("value") if isinstance(answer, dict) else None
    return None if value is None else int(value)


def _companion_facts(row: dict[str, Any], companion: Any, spec: Any, terms: dict[str, Any], *, fee_max_atomic: int, economics: dict[str, Any]) -> dict[str, Any]:
    from core.wallet import svm_tokens

    native = chains.native_asset(spec.network)
    decimals = int(terms["decimals"])
    amount = int(row["amount_atomic"])
    return {
        "kind": "dna_service_fee_collection",
        "proposal_id": str(companion.proposal_id),
        "collection_id": str(row["collection_id"]),
        "state_version": int(row["state_version"]),
        "fingerprint": ledger.fingerprint(row),
        "amount_minor": amount,
        "amount_human": _atomic_exact(amount, decimals),
        "asset": terms["asset"],
        "decimals": decimals,
        "treasury_owner": terms["treasury_owner"],
        "treasury_source": terms["treasury_source"],
        "treasury_token_account": svm_tokens.associated_account(terms["treasury_owner"], terms["mint"]) if terms.get("mint") else "",
        "fee_estimate_minor": int(fee_max_atomic),
        "fee_max_minor": int(fee_max_atomic),
        "fee_decimals": int(native.decimals),
        "gas_asset": spec.native_display_symbol or native.symbol,
        "expires_at": float(row.get("expires_epoch") or 0),
        "through_entry_id": int(row["through_entry_id"]),
        "policy_id": str(row["policy_id"]),
        "rate_label": RATE_LABEL,
        "cost_bound_bps": COLLECTION_COST_BOUND_BPS,
        "economics": dict(economics),
        "purpose": "collection of DNA service fees already accrued on earlier provider payments; it buys no inference and delivers no service",
        "authorization": "collected with this payment under its one approval; the collection is its own transaction with its own network fee",
    }


def _companion_proposal_open(proposal_id: str) -> Any | None:
    companion = proposals.get_proposal(str(proposal_id or ""))
    if companion is None or companion.state != proposals.STATE_PENDING_APPROVAL:
        return None
    return companion


def plan_companion_collection(
    proposal: Any,
    *,
    rpc: Any,
    blockhash: str,
    payment_fee_max_atomic: int,
    principal_balance_atomic: int,
    held_principal_atomic: int,
    fee_balance_atomic: int,
    held_native_atomic: int,
    rent_minimum_atomic: int,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The economical collection of PRIOR accepted payments' debt that rides THIS native x402 payment's ONE approval.
    Called by the quote (so the sheet shows it and the digest binds it), never by a sweeper:

    * only what is already accrued and collectible (never this payment's projected fee),
    * only at or above the Settings threshold AND under the cost bound (:func:`collection_economics`),
    * only when the treasury is the configured owner with its token account already open,
    * only when the wallet's balances carry BOTH transactions and the wallet's own caps admit the collection,
    * exactly one open collection per identity; a plan is a ledger offer bound to a companion proposal of origin
      ``dna_fee`` that names this payment. Anything else DEFERS with a typed reason and the payment proceeds alone.

    Never signs, never sends. A deferred collection keeps every atomic unit of the debt."""
    outcome: dict[str, Any] = {"due": False, "reason": "", "companion": None, "position": None, "threshold": None, "economics": None}
    if str(getattr(proposal, "origin", "") or "") != proposals.ORIGIN_USEPOD:
        return {**outcome, "reason": "not_a_native_x402_payment"}
    profile = custody.get_wallet(str(proposal.wallet_id))
    if profile is None:
        return {**outcome, "reason": "wallet_unknown"}
    found = identity_for(payer=profile.public_key, network=str(proposal.network), asset=str(proposal.asset), source_context=source_context)
    if found is None:
        return {**outcome, "reason": "no_fee_policy_on_this_row"}
    key, terms = found
    if not stores_aligned():
        return {**outcome, "reason": "ledger_store_divergent"}
    spec = chains.resolve_network(str(proposal.network))
    native = chains.native_asset(spec.network)
    reconcile_offers()
    with _ledger_read() as conn:
        position = _position(conn, key)
        open_row = ledger.open_collection(conn, key)
    threshold = collect_min_atomic(terms["asset"])
    outcome.update(position=position, threshold=threshold)
    now = time.time()
    if open_row is not None:
        mine = str(open_row.get("payment_proposal_id") or "") == str(proposal.proposal_id) and str(open_row["state"]) == ledger.COLLECTION_OFFERED
        companion = _companion_proposal_open(str(open_row.get("proposal_id") or "")) if mine else None
        if mine and companion is not None and float(open_row.get("expires_epoch") or 0) > now:
            # this payment's own plan, still standing: re-price its network fee and re-check the economics
            fee_max = _collection_fee_max(rpc, spec, terms, payer=profile.public_key, amount_atomic=int(open_row["amount_atomic"]), blockhash=blockhash)
            economics = collection_economics(amount_atomic=int(open_row["amount_atomic"]), asset_key=terms["asset_key"], network_fee_max_atomic=-1 if fee_max is None else fee_max, fee_asset_key=_native_asset_key(spec))
            if fee_max is None or not economics["ok"] or fee_max > int(open_row.get("network_fee_max_atomic") or 0):
                _release_plan(open_row, reason=economics["reason"] or "collection_fee_rose_above_the_offer")
                return {**outcome, "reason": economics["reason"] or "collection_fee_rose_above_the_offer", "economics": economics}
            return {**outcome, "due": True, "reason": "planned", "economics": economics, "companion": _companion_facts(open_row, companion, spec, terms, fee_max_atomic=fee_max, economics=economics)}
        if mine:
            # our own plan lapsed or lost its proposal: retire it under the fence and plan afresh below
            _release_plan(open_row, reason="plan_stale")
            with _ledger_read() as conn:
                position = _position(conn, key)
                open_row = ledger.open_collection(conn, key)
            outcome["position"] = position
        if open_row is not None:
            return {**outcome, "reason": "collection_open_elsewhere"}
    amount = int(position["collectible_atomic"])
    if amount < 1:
        return {**outcome, "reason": "nothing_collectible"}
    if amount < threshold:
        return {**outcome, "reason": "below_threshold"}
    binding = treasury_binding(spec.network)
    if binding["state"] != "configured" or binding["owner"] != terms["treasury_owner"]:
        return {**outcome, "reason": "treasury_not_configured"}
    ok, why = _treasury_can_receive(spec, terms)
    if not ok:
        return {**outcome, "reason": why}
    fee_max = _collection_fee_max(rpc, spec, terms, payer=profile.public_key, amount_atomic=amount, blockhash=blockhash)
    if fee_max is None:
        return {**outcome, "reason": "collection_fee_unknown"}
    economics = collection_economics(amount_atomic=amount, asset_key=terms["asset_key"], network_fee_max_atomic=fee_max, fee_asset_key=_native_asset_key(spec))
    outcome["economics"] = economics
    if not economics["ok"]:
        return {**outcome, "reason": economics["reason"]}
    # the wallet must carry BOTH transactions, per asset, with the rent-exempt remainder kept
    payment_amount = int(proposal.amount_minor)
    if terms.get("mint"):
        if int(principal_balance_atomic) - int(held_principal_atomic) - payment_amount - amount < 0:
            return {**outcome, "reason": "insufficient_token_balance_for_collection"}
        native_need = int(payment_fee_max_atomic) + int(fee_max)
    else:
        native_need = payment_amount + int(payment_fee_max_atomic) + amount + int(fee_max)
    if int(fee_balance_atomic) - int(held_native_atomic) - native_need < int(rent_minimum_atomic):
        return {**outcome, "reason": "insufficient_native_balance_for_collection"}
    # the wallet's own caps admit the collection like any other transfer: the threshold never bypasses them
    from core.wallet import limits

    verdict = limits.check_limits(profile.wallet_id, terms["asset"], amount, terms["treasury_owner"], now=now, fee_minor=int(fee_max), chain=spec.network)
    if not verdict.ok:
        return {**outcome, "reason": f"limit_refused:{verdict.limit}"}
    try:
        with _ledger_txn() as conn:
            record = ledger.offer_collection(conn, key=key, amount_atomic=amount, expires_epoch=now + COMPANION_OFFER_TTL_SECONDS, network_fee_max_atomic=int(fee_max), network_fee_asset=native.symbol, now=now)
    except ledger.ServiceFeeLedgerError as exc:
        return {**outcome, "reason": exc.code}
    collection_id = str(record["collection_id"])
    companion = None
    try:
        from core.wallet import lifecycle

        companion = proposals.propose_transaction(
            wallet_id=profile.wallet_id, destination=terms["treasury_owner"], amount_minor=amount, asset=terms["asset"], origin=proposals.ORIGIN_DNA_FEE,
            memo=f"{MEMO_PREFIX} {collection_id} with {proposal.proposal_id}", idempotency_key=f"dna_fee:{collection_id}", source_context=source_context, network=spec.network,
        )
        if companion.state == proposals.STATE_PROPOSED:
            companion = lifecycle.default_lifecycle(source_context=source_context).prepare(companion.proposal_id)
        if companion.state != proposals.STATE_PENDING_APPROVAL:
            raise _fault("wallet_quote_unavailable", source_context=source_context, proposal_id=companion.proposal_id, reason=f"companion_{companion.state}")
        with _ledger_txn() as conn:
            bound = ledger.bind_collection(conn, collection_id, proposal_id=companion.proposal_id, wallet_id=profile.wallet_id, payment_proposal_id=str(proposal.proposal_id))
    except Exception as exc:
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        with contextlib.suppress(Exception):
            with _ledger_txn() as conn:
                ledger.release_collection(conn, collection_id=collection_id, reason=f"plan_failed:{code}"[:120], expected_state=ledger.COLLECTION_OFFERED)
        if companion is not None:
            _expire_proposal(companion.proposal_id, reason=f"dna_fee_plan_failed:{code}"[:120])
        return {**outcome, "reason": f"companion_failed:{code}"[:160]}
    return {**outcome, "due": True, "reason": "planned", "companion": _companion_facts(bound, companion, spec, terms, fee_max_atomic=int(fee_max), economics=economics)}


def _native_asset_key(spec: Any) -> str:
    from core.effect_budget_money import AssetIdentity

    native = chains.native_asset(spec.network)
    return AssetIdentity(network=spec.network, asset=native.symbol, decimals=int(native.decimals)).key


def _release_plan(row: dict[str, Any], *, reason: str) -> dict[str, Any] | None:
    """Retire an OFFERED plan under the fence (state and version as read); a claim that landed in between wins."""
    try:
        with _ledger_txn() as conn:
            released = ledger.release_collection(conn, collection_id=str(row["collection_id"]), reason=str(reason)[:120], expected_state=ledger.COLLECTION_OFFERED, expected_version=int(row["state_version"]))
    except ledger.ServiceFeeLedgerError:
        return None
    if released and row.get("proposal_id"):
        _expire_proposal(str(row["proposal_id"]), reason=f"dna_fee_{reason}"[:120])
    return released


def _expire_proposal(proposal_id: str, *, reason: str) -> None:
    """End a companion proposal nobody approved, ONLY while it is still open and unclaimed: the open quote is
    superseded, the proposal expires with a receipt. A claimed one (a transfer row exists, or the state moved) is
    the transfer lane's and is left exactly as it is."""
    from core.wallet import quotes, receipts, transfers

    proposal = proposals.get_proposal(proposal_id)
    if proposal is None or proposal.state not in transfers.OPEN_PROPOSAL_STATES or transfers.get_transfer_by_id(proposal_id) is not None:
        return
    open_quote = quotes.open_quote_for(proposal_id)
    if open_quote is not None:
        quotes.supersede_quote(open_quote["quote_id"], reason=reason)
    moved = proposals.transition(proposal_id, proposals.STATE_EXPIRED, detail={"reason": reason}, fault_code="wallet_quote_expired", expected_state=proposal.state)
    if moved is not None:
        receipts.record_receipt(moved, state=proposals.STATE_EXPIRED, fault_code="wallet_quote_expired", extra={"dna_fee_collection": reason})


def _expire_companion(row: dict[str, Any], *, now: float) -> dict[str, Any] | None:
    """Expiration under the authoritative transaction: the ledger's compare-and-set retires the offer only while it is
    still offered and lapsed; the proposal cleanup follows ONLY a won expiry. A claim that landed first keeps its
    custody, its proposal and its quote untouched (None)."""
    with _ledger_txn() as conn:
        expired = ledger.expire_offer(conn, collection_id=str(row["collection_id"]), now=float(now))
    if expired is None:
        return None
    if row.get("proposal_id"):
        _expire_proposal(str(row["proposal_id"]), reason="dna_fee_offer_expired")
    return expired


def reconcile_offers(*, now: float | None = None) -> list[dict[str, Any]]:
    """Give back the debt of offers that ended without a claim: a lapsed offer (the ledger's compare-and-set, so a
    concurrent claim is never released), a companion whose payment or own proposal ended, or a proposal that is gone.
    A claimed or submitted collection is the transfer lane's to end, never this."""
    moment = float(now if now is not None else time.time())
    released: list[dict[str, Any]] = []
    with _ledger_read() as conn:
        rows = [row for row in ledger.collections(conn, limit=200) if str(row["state"]) == ledger.COLLECTION_OFFERED]
    for row in rows:
        if float(row.get("expires_epoch") or 0) and float(row["expires_epoch"]) <= moment:
            expired = _expire_companion(row, now=moment)
            if expired:
                released.append(expired)
            continue
        reason = ""
        companion = proposals.get_proposal(str(row.get("proposal_id") or "")) if row.get("proposal_id") else None
        payment = proposals.get_proposal(str(row.get("payment_proposal_id") or "")) if row.get("payment_proposal_id") else None
        if row.get("proposal_id") and companion is None:
            reason = "proposal_missing"
        elif companion is not None and companion.state in proposals.TERMINAL_STATES:
            reason = f"proposal_{companion.state}"
        elif row.get("payment_proposal_id") and (payment is None or payment.state in proposals.TERMINAL_STATES):
            reason = f"payment_{payment.state if payment else 'missing'}"
        if reason:
            done = _release_plan(row, reason=reason)
            if done:
                released.append(done)
    return [_public_collection(row) for row in released if row]


def release_companion_for_payment(payment_proposal_id: str, *, reason: str) -> dict[str, Any] | None:
    """The provider payment ended before its claim (rejected on the card, its window closed, it failed): the
    collection planned to ride it gives its amount back and its companion proposal expires. A claimed or submitted
    companion is the transfer lane's (it was approved together with the payment) and is left untouched."""
    with _ledger_read() as conn:
        row = ledger.collection_for_payment(conn, str(payment_proposal_id))
    if row is None:
        return None
    if str(row["state"]) != ledger.COLLECTION_OFFERED:
        return _public_collection(row)
    released = _release_plan(row, reason=str(reason)[:120])
    return _public_collection(released or row)


# --- the approval's binding -----------------------------------------------------------------------------------


def require_companion_binding(proposal: Any, *, quote_fields: dict[str, Any] | None, moment: float, source_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The gate before any credential is asked for a payment whose quote carries a companion collection: the companion
    proposal is still pending, its collection record is offered, unexpired, bound to THIS payment, and every fact the
    sheet showed (identity, version, amount, treasury, asset) still holds; the treasury is still the configured one.
    A lapsed offer is expired under the ledger's fence and ends the approval typed (the sheet re-plans on refresh); a
    changed binding refuses typed. None when the quote carries no companion."""
    facts = quote_fields.get("companion") if isinstance(quote_fields, dict) else None
    if not isinstance(facts, dict) or not facts:
        return None
    companion = proposals.get_proposal(str(facts.get("proposal_id") or ""))
    if companion is None or companion.state != proposals.STATE_PENDING_APPROVAL:
        raise _fault("wallet_quote_mismatch", source_context=source_context, proposal_id=proposal.proposal_id, reason="dna_fee_companion_not_pending", companion_state=getattr(companion, "state", "missing"))
    with _ledger_read() as conn:
        row = ledger.collection_for_proposal(conn, companion.proposal_id)
    if row is None or str(row.get("payment_proposal_id") or "") != str(proposal.proposal_id):
        raise _fault("wallet_quote_mismatch", source_context=source_context, proposal_id=proposal.proposal_id, reason="dna_fee_companion_unbound")
    if str(row["state"]) != ledger.COLLECTION_OFFERED:
        raise _fault("wallet_duplicate_payment", source_context=source_context, proposal_id=proposal.proposal_id, reason=f"dna_fee_companion_{row['state']}")
    if float(row.get("expires_epoch") or 0) and float(row["expires_epoch"]) <= float(moment):
        _expire_companion(row, now=float(moment))
        raise _fault("wallet_quote_expired", source_context=source_context, proposal_id=proposal.proposal_id, reason="dna_fee_companion_expired", expires_at=float(row["expires_epoch"]))
    binding = treasury_binding(str(proposal.network))
    spec = chains.resolve_network(str(proposal.network))
    same = (
        str(row["collection_id"]) == str(facts.get("collection_id") or "") and int(row["state_version"]) == int(facts.get("state_version") or -1)
        and int(row["amount_atomic"]) == int(facts.get("amount_minor") or -1) == int(companion.amount_minor)
        and str(row["treasury_owner"]) == str(facts.get("treasury_owner") or "") == str(companion.destination)
        and binding["state"] == "configured" and binding["owner"] == str(row["treasury_owner"])
        and str(row["network"]) == spec.network == str(companion.network)
        and str(row["asset_key"]).split("|")[1] == chains.asset_for(spec.network, companion.asset).symbol == str(facts.get("asset") or "")
    )
    if not same:
        raise _fault(
            "wallet_quote_mismatch", source_context=source_context, proposal_id=proposal.proposal_id, reason="dna_fee_companion_binding_changed",
            treasury_state=binding["state"], configured_treasury=binding["owner"], bound_treasury=str(row["treasury_owner"]),
        )
    if not stores_aligned():
        raise _fault("wallet_quote_unavailable", source_context=source_context, proposal_id=proposal.proposal_id, reason="dna_fee_ledger_store_divergent")
    return {"proposal": companion, "fields": dict(facts), "record": row}


def check_companion_claim(conn: Any, companion: Any, *, quote_id: str, companion_fields: dict[str, Any], now: float) -> dict[str, Any]:
    """Inside the wallet's ONE claim transaction (the payment's): the offered collection bound to the companion moves
    to claimed under the payment's quote, at the version and the network-fee ceiling the sheet showed. Every bound
    fact is re-checked on this connection; a refusal rolls the whole claim (payment and collection) back."""
    if not stores_aligned():
        raise _fault("wallet_quote_unavailable", proposal_id=companion.proposal_id, reason="dna_fee_ledger_store_divergent")
    ledger.ensure_tables(conn)
    binding = treasury_binding(str(companion.network))
    spec = chains.resolve_network(str(companion.network))
    registered = chains.asset_for(spec.network, companion.asset)
    from core.effect_budget_money import AssetIdentity

    asset_key = AssetIdentity(network=spec.network, asset=registered.symbol, decimals=int(registered.decimals)).key
    if binding["state"] != "configured" or binding["owner"] != str(companion.destination):
        raise _fault("wallet_quote_mismatch", proposal_id=companion.proposal_id, reason="dna_fee_treasury_changed", treasury_state=binding["state"])
    try:
        return ledger.claim_collection(
            conn, proposal_id=str(companion.proposal_id), quote_id=str(quote_id), amount_atomic=int(companion.amount_minor), treasury_owner=binding["owner"],
            network=spec.network, asset_key=asset_key, network_fee_max_atomic=int(companion_fields.get("fee_max_minor") or 0), now=float(now),
            expected_version=int(companion_fields.get("state_version") or 0) or None,
        )
    except ledger.ServiceFeeLedgerError as exc:
        code = "wallet_quote_expired" if exc.code in {"service_fee_collection_expired", "service_fee_accrual_changed"} else "wallet_quote_mismatch"
        raise _fault(code, proposal_id=companion.proposal_id, reason=exc.code, detail=exc.detail[:160]) from None


def on_transfer_state(conn: Any, proposal_id: str, new_state: str, *, tx_id: str = "") -> dict[str, Any] | None:
    """The transfer lane's transition, mirrored onto the collection record on the SAME connection: sending keeps
    custody, confirmation retires the amount once, a failed execution or a never-sent ending gives the debt back.
    The transfer owner's release of its own claimed, proven-never-sent collection passes no fence: it owns the row."""
    from core.wallet import transfers

    if not stores_aligned():
        raise _fault("wallet_duplicate_payment", proposal_id=proposal_id, reason="dna_fee_ledger_store_divergent")
    ledger.ensure_tables(conn)
    if ledger.collection_for_proposal(conn, proposal_id) is None:
        return None
    try:
        if new_state == transfers.STATE_DISPATCHING:
            return ledger.mark_submitted(conn, proposal_id=proposal_id)
        if new_state == transfers.STATE_CONFIRMED:
            return ledger.confirm_collection(conn, proposal_id=proposal_id, tx_signature=str(tx_id or ""))
        if new_state == transfers.STATE_FAILED_ON_CHAIN:
            return ledger.fail_collection(conn, proposal_id=proposal_id, reason="failed_on_chain")
        if new_state in (transfers.STATE_RELEASED, transfers.STATE_DISCARDED, transfers.STATE_CANCELLED):
            return ledger.release_collection(conn, proposal_id=proposal_id, reason=f"transfer_{new_state}")
    except ledger.ServiceFeeLedgerError as exc:
        raise _fault("wallet_duplicate_payment", proposal_id=proposal_id, reason=exc.code, detail=exc.detail[:160]) from None
    return ledger.collection_for_proposal(conn, proposal_id)


# --- status ----------------------------------------------------------------------------------------------------


_STATE_LABELS = {
    ledger.COLLECTION_OFFERED: "planned with the payment, awaiting its approval",
    ledger.COLLECTION_CLAIMED: "approved with the payment, being sent",
    ledger.COLLECTION_SUBMITTED: "submitted, not yet confirmed (custody retained)",
    ledger.COLLECTION_CONFIRMED: "confirmed: collected by the treasury",
    ledger.COLLECTION_FAILED: "failed on chain: nothing collected, the fee stays owed",
    ledger.COLLECTION_RELEASED: "not sent: the fee stays owed",
}


def receipt_view(proposal_id: str) -> dict[str, Any] | None:
    """The fee-collection facts a transfer or receipt of origin ``dna_fee`` carries: never described as inference."""
    with _ledger_read() as conn:
        row = ledger.collection_for_proposal(conn, proposal_id)
    if row is None:
        return None
    decimals = _decimals(str(row["asset_key"]))
    state = str(row["state"])
    return {
        "kind": "dna_service_fee_collection",
        "collection_id": str(row["collection_id"]),
        "payment_proposal_id": str(row.get("payment_proposal_id") or ""),
        "state": state,
        "state_label": _STATE_LABELS.get(state, state),
        "amount_atomic": int(row["amount_atomic"]),
        "amount_exact": _atomic_exact(int(row["amount_atomic"]), decimals),
        "asset": str(row["asset_key"]).split("|")[1],
        "treasury_owner": str(row["treasury_owner"]),
        "tx_signature": str(row.get("tx_signature") or ""),
        "close_reason": str(row.get("close_reason") or ""),
        "policy_id": str(row["policy_id"]),
        "fingerprint": ledger.fingerprint(row),
    }


def companion_outcome(payment_proposal_id: str) -> dict[str, Any] | None:
    """What happened to the collection planned with ONE provider payment, for that payment's card, sheet and
    receipt: the newest collection bound to the payment, its state and transaction, and the transfer's own record.
    None when no collection was ever planned with it (the receipt then says the fee stays accrued)."""
    from core.wallet import transfers

    with _ledger_read() as conn:
        row = ledger.collection_for_payment(conn, str(payment_proposal_id), open_only=False)
    if row is None:
        return None
    view = receipt_view(str(row["proposal_id"])) or {}
    transfer = transfers.latest_receipt(str(row["proposal_id"])) if row.get("proposal_id") else None
    return {
        **view,
        "proposal_id": str(row.get("proposal_id") or ""),
        "transfer_state": str(transfer["state"]) if transfer else "",
        "transfer_state_label": str(transfer.get("state_label") or "") if transfer else "",
        "explorer_url": str(transfer.get("explorer_url") or "") if transfer else "",
        "collected": str(row["state"]) == ledger.COLLECTION_CONFIRMED,
    }


def status() -> dict[str, Any]:
    """The Settings and status read: the policy, the treasury binding of the designated row, the thresholds, the
    collection mode with its cost bound and the conversion bounds it can use, and every identity's exact position
    with its open collection. A read; the reconciliation of lapsed offers rides it."""
    from core.effect_budget_money import AssetIdentity, conversion_bound_view

    reconcile_offers()
    rows: list[dict[str, Any]] = []
    with _ledger_read() as conn:
        for key in ledger.identities(conn):
            try:
                parts = ledger.identity_parts(key)
            except ledger.ServiceFeeLedgerError:
                continue
            position = ledger.owed(conn, key)
            decimals = _decimals(parts[2])
            open_row = ledger.open_collection(conn, key)
            rows.append({
                "payer_account": parts[0], "network": parts[1], "asset_key": parts[2], "asset": parts[2].split("|")[1], "treasury_owner": parts[3], "policy_id": parts[4],
                "accrued_exact": _exact(position["accrued_numerator"], decimals), "retired_exact": _exact(position["retired_numerator"], decimals),
                "owed_exact": _exact(position["net_numerator"], decimals), "owed_atomic": position["owed_atomic"], "carry_exact": _exact(position["carry_numerator"], decimals),
                "held_by_open_collections_atomic": position["held_by_open_collections_atomic"], "collectible_atomic": position["collectible_atomic"],
                "collectible_exact": _atomic_exact(position["collectible_atomic"], decimals), "overcollected_exact": _exact(position["overcollected_numerator"], decimals),
                "collect_min_atomic": collect_min_atomic(parts[2].split("|")[1]), "open_collection": _public_collection(open_row),
            })
        recent = [_public_collection(row) for row in ledger.collections(conn, limit=20)]
    spec = chains.resolve_network(DESIGNATED_TREASURY_NETWORK)
    native = chains.native_asset(spec.network)
    native_identity = AssetIdentity(network=spec.network, asset=native.symbol, decimals=int(native.decimals))
    bounds: dict[str, Any] = {}
    for asset in sorted(DEFAULT_COLLECT_MIN_ATOMIC):
        if asset == native.symbol:
            bounds[asset] = {"kind": "same_asset"}
            continue
        try:
            registered = chains.asset_for(spec.network, asset)
            bounds[asset] = conversion_bound_view(native_identity, AssetIdentity(network=spec.network, asset=registered.symbol, decimals=int(registered.decimals)))
        except Exception:
            bounds[asset] = None
    return {
        "policy": {"policy_id": POLICY_ID, "rate_bps": RATE_BPS, "rate_label": RATE_LABEL, "explanation": USER_FACING_EXPLANATION, "scope": "native x402 provider payments only"},
        "treasury": treasury_binding(DESIGNATED_TREASURY_NETWORK),
        "collect_min": {asset: collect_min_view(asset) for asset in sorted(DEFAULT_COLLECT_MIN_ATOMIC)},
        "collection": {
            "mode": COLLECTION_MODE, "cost_bound_bps": COLLECTION_COST_BOUND_BPS, "companion_offer_ttl_seconds": COMPANION_OFFER_TTL_SECONDS,
            "conversion_bounds": bounds, "deferred_until": COLLECTION_DEFERRED_UNTIL,
        },
        "identities": rows,
        "collections": recent,
        "stores_aligned": stores_aligned(),
    }


__all__ = [
    "AUTHORITY", "COLLECTION_COST_BOUND_BPS", "COLLECTION_DEFERRED_UNTIL", "COLLECTION_MODE", "COLLECT_MIN_ENV", "COMPANION_OFFER_TTL_SECONDS", "DEFAULT_COLLECT_MIN_ATOMIC",
    "DESIGNATED_TREASURY_NETWORK", "DESIGNATED_TREASURY_OWNER", "FEE_STATE_RESERVED", "MEMO_PREFIX", "OFFER_TTL_SECONDS", "POLICY_ID", "RATE_BPS", "RATE_LABEL",
    "TREASURY_OVERRIDE_ENV", "TREASURY_SOURCE_DESIGNATED", "TREASURY_SOURCE_OVERRIDE", "USER_FACING_EXPLANATION",
    "check_companion_claim", "collect_min_atomic", "collect_min_settings_atomic", "collect_min_source", "collect_min_view", "collection_economics", "collection_message",
    "companion_outcome", "fee_terms", "identity_for", "on_transfer_state", "payment_preview", "plan_companion_collection", "position_for", "receipt_view",
    "reconcile_offers", "release_companion_for_payment", "require_companion_binding", "set_collect_min_atomic", "status", "stores_aligned", "treasury_binding",
]
