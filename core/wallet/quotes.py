"""Typed transfer quotes: what the approval sheet shows and what an approval binds.

A quote is minted from the chain for one pilot transfer, through the row's own endpoint after its identity proof. It
carries every popup field as typed data (network and environment, both full addresses, the observed balance with its
slot or block, the exact amount, the fee estimate and ceiling in the gas asset, the maximum debit and the estimated and
minimum balance after) and a digest over all of them. Unknown chain data is never zero: it is a typed
``wallet_quote_unavailable``. A refresh supersedes the open quote; an environment switch supersedes every open quote.

Fee rules per ``chains`` fee model (delivery/NETWORK-MATRIX.json, PLAN-v2 CF-2..CF-13):
* Solana: ``getFeeForMessage`` for the exact transfer message; rent rules for new recipients and dust remainders.
* EIP-1559: ``maxFee = ceil(baseFee x 1.125^GROWTH_BLOCKS) + priority``.
* BSC: base fee 0, priority floor of 0.05 gwei.
* OP stack: the L2 part as EIP-1559, plus the L1 fee from the GasPriceOracle over the UNSIGNED type-2 bytes (an estimate
  that can vary, buffered in the ceiling), plus the operator fee.
* Arbitrum Nitro: L1 cost is folded into gas, so the gas estimate is always buffered; tips are not used.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from core.wallet import amounts, chains, custody, environment, limits, proposals, transfers
from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault
from core.wallet.store import connection

AUTHORITY = "core.wallet.quotes"
QUOTE_DOMAIN = "wallet-transfer-quote-v1"
QUOTE_TTL_SECONDS = 60

#: base-fee growth bound: 12.5% per full block over the quote lifetime plus inclusion
GROWTH_BLOCKS = 6
#: BSC: BEP-226 base fee is 0 and validators accept no less than 0.05 gwei
BSC_MIN_PRIORITY_WEI = 50_000_000
#: Arbitrum Nitro folds the L1 cost into gas used: the estimate is always buffered
ARBITRUM_GAS_BUFFER_BPS = 2_000
CONTRACT_GAS_BUFFER_BPS = 2_000
#: the OP-stack L1 part is an estimate that moves with L1 prices: the ceiling buffers it
OP_L1_FEE_BUFFER_BPS = 2_500
EOA_GAS_LIMIT = 21_000

STATE_OPEN = "open"
STATE_CONSUMED = "consumed"
STATE_SUPERSEDED = "superseded"

GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
#: execution-layer system contracts (EIP-4788 beacon roots, EIP-2935 block hashes)
_SYSTEM_ADDRESSES = frozenset({"0x000f3df6d732807ef1319fb7b8bb8522d0beac02", "0x0000f90827f1c53a10cb7a02335b175320002935"})


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-int(numerator) // int(denominator))


def _grown(base_fee_wei: int) -> int:
    """ceil(base_fee x 1.125^GROWTH_BLOCKS) in exact integers (1.125 = 9/8): money never rides a float."""
    return _ceil_div(int(base_fee_wei) * 9 ** GROWTH_BLOCKS, 8 ** GROWTH_BLOCKS)


def _buffered(value: int, bps: int) -> int:
    return _ceil_div(int(value) * (10_000 + int(bps)), 10_000)


def _now() -> float:
    return time.time()


def _fault(code: str, *, source_context: dict[str, Any] | None = None, **context: Any) -> WalletFault:
    return wallet_fault(code, authority=AUTHORITY, context=context, source_context=source_context)


def digest_of(fields: dict[str, Any]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{QUOTE_DOMAIN}|{canonical}".encode()).hexdigest()


# --- chain identity before any read ----------------------------------------------------------------------

def _prove(spec: chains.ChainIdentity, url: str, probe: Any, *, source_context: dict[str, Any] | None) -> None:
    try:
        chains.verify_chain_identity(spec, probe, scope=chains.endpoint_scope("quote", url))
    except WalletFault as exc:
        if str(exc.context.get("reason") or "").startswith("identity_probe_failed"):
            raise _fault("wallet_quote_unavailable", network=spec.network, reason="endpoint_unreadable", source_context=source_context) from None
        raise


# --- Solana ------------------------------------------------------------------------------------------------

def _solana_quote(spec: chains.ChainIdentity, proposal: Any, account_address: str, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    from core.wallet import lifecycle

    rpc = lifecycle.RpcClient(chains.network_rpc_url(spec.network), network=spec.network)
    _prove(spec, rpc.url, lambda method: rpc._call(method, []), source_context=source_context)
    try:
        balance = rpc._call("getBalance", [account_address, {"commitment": "confirmed"}])
        latest = rpc._call("getLatestBlockhash", [{"commitment": "confirmed"}])
        rent_minimum = int(rpc._call("getMinimumBalanceForRentExemption", [0]))
        recipient = rpc._call("getAccountInfo", [proposal.destination, {"encoding": "base64", "commitment": "confirmed"}])
        blockhash = str(latest["value"]["blockhash"])
        message = lifecycle._build_message(proposal, account_address, blockhash)
        fee = rpc._call("getFeeForMessage", [base64.b64encode(bytes(message)).decode("ascii"), {"commitment": "confirmed"}])
    except WalletFault:
        raise
    except Exception as exc:
        raise _fault("wallet_quote_unavailable", network=spec.network, reason=f"rpc_read_failed:{type(exc).__name__}", source_context=source_context) from None
    fee_value = (fee or {}).get("value")
    if fee_value is None:
        raise _fault("wallet_quote_unavailable", network=spec.network, reason="fee_unknown_for_message", source_context=source_context)
    if (balance or {}).get("value") is None:
        raise _fault("wallet_quote_unavailable", network=spec.network, reason="balance_unknown", source_context=source_context)
    info = (recipient or {}).get("value")
    cues: list[str] = []
    if info is None or int(info.get("lamports") or 0) == 0:
        try:
            from solders.pubkey import Pubkey

            on_curve = bool(Pubkey.from_string(proposal.destination).is_on_curve())
        except Exception:
            on_curve = True
        if info is None and not on_curve:
            raise _fault("wallet_recipient_refused", network=spec.network, reason="off_curve_address_without_account", source_context=source_context)
        if int(proposal.amount_minor) < rent_minimum:
            raise _fault("wallet_recipient_refused", network=spec.network, reason="new_account_below_rent_minimum", rent_minimum_minor=rent_minimum, source_context=source_context)
        cues.append("new_recipient_account")
    else:
        if info.get("executable"):
            raise _fault("wallet_recipient_refused", network=spec.network, reason="executable_recipient", source_context=source_context)
        if str(info.get("owner") or "") != SYSTEM_PROGRAM:
            cues.append("non_system_owner_recipient")
    fee_minor = int(fee_value)
    return {
        "balance_minor": int(balance["value"]), "balance_ref": f"slot:{int((balance.get('context') or {}).get('slot') or 0)}",
        "fee_estimate_minor": fee_minor, "fee_max_minor": fee_minor, "fee_parts": {"base_minor": fee_minor},
        "rent_minimum_minor": rent_minimum, "recent_blockhash": blockhash,
        "last_valid_block_height": int(latest["value"].get("lastValidBlockHeight") or 0), "cues": cues,
    }


# --- EVM ---------------------------------------------------------------------------------------------------

def _evm_rpc(spec: chains.ChainIdentity, url: str, method: str, params: list[Any], *, source_context: dict[str, Any] | None) -> Any:
    from core.wallet import outbound

    try:
        answer = outbound.rpc_call(url, payload={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, spec=spec, timeout=15.0)
    except WalletFault:
        raise
    except Exception as exc:
        raise _fault("wallet_quote_unavailable", network=spec.network, reason=f"rpc_{method}_failed:{type(exc).__name__}", source_context=source_context) from None
    if not isinstance(answer, dict) or answer.get("error") is not None or "result" not in answer or answer.get("result") is None:
        raise _fault("wallet_quote_unavailable", network=spec.network, reason=f"rpc_{method}_no_result", source_context=source_context)
    return answer["result"]


def _check_evm_recipient(spec: chains.ChainIdentity, address: str, *, source_context: dict[str, Any] | None) -> list[str]:
    from eth_utils import is_checksum_address

    text = str(address or "").strip()
    body = text[2:]
    if body != body.lower() and body != body.upper() and not is_checksum_address(text):
        raise _fault("wallet_recipient_refused", network=spec.network, reason="bad_checksum", source_context=source_context)
    lowered = text.lower()
    value = int(lowered, 16)
    if value == 0:
        raise _fault("wallet_recipient_refused", network=spec.network, reason="zero_address", source_context=source_context)
    if value < 0x10000 or lowered.startswith("0x42000000000000000000000000000000000000") or lowered in _SYSTEM_ADDRESSES:
        raise _fault("wallet_recipient_refused", network=spec.network, reason="precompile_or_system_address", source_context=source_context)
    return ["unchecked_lowercase_address"] if body != body.upper() and body == body.lower() else []


def unsigned_type2_bytes(*, chain_id: int, nonce: int, max_priority_fee_per_gas: int, max_fee_per_gas: int, gas_limit: int, to: str, value: int) -> bytes:
    """The unsigned EIP-1559 transaction bytes (0x02 || rlp([...]) with empty data and access list)."""
    import rlp

    return b"\x02" + rlp.encode([int(chain_id), int(nonce), int(max_priority_fee_per_gas), int(max_fee_per_gas), int(gas_limit), bytes.fromhex(str(to)[2:]), int(value), b"", []])


def _abi_call(signature: str, types: list[str], values: list[Any]) -> str:
    from eth_abi import encode
    from eth_hash.auto import keccak

    return "0x" + (keccak(signature.encode())[:4] + encode(types, values)).hex()


def _evm_quote(spec: chains.ChainIdentity, proposal: Any, account_address: str, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    from core.wallet import evm

    evm.require_evm_dependencies("transfer_quote", source_context=source_context)
    cues = _check_evm_recipient(spec, proposal.destination, source_context=source_context)
    url = chains.network_rpc_url(spec.network)
    _prove(spec, url, lambda method: _evm_rpc(spec, url, method, [], source_context=source_context), source_context=source_context)
    balance = int(_evm_rpc(spec, url, "eth_getBalance", [account_address, "latest"], source_context=source_context), 16)
    block = _evm_rpc(spec, url, "eth_getBlockByNumber", ["latest", False], source_context=source_context)
    block_number = int(block["number"], 16)
    base_fee = int(block.get("baseFeePerGas") or "0x0", 16)
    nonce = int(_evm_rpc(spec, url, "eth_getTransactionCount", [account_address, "pending"], source_context=source_context), 16)
    code = str(_evm_rpc(spec, url, "eth_getCode", [proposal.destination, "latest"], source_context=source_context) or "0x").lower()
    has_code = code not in {"0x", "0x0", ""}
    if code.startswith("0xef0100"):
        cues.append("eip7702_delegated_recipient")
    elif has_code:
        cues.append("contract_recipient")
    amount = int(proposal.amount_minor)
    model = spec.fee_model
    if model == chains.FEE_BSC:
        gas_price = int(_evm_rpc(spec, url, "eth_gasPrice", [], source_context=source_context), 16)
        priority = max(gas_price, BSC_MIN_PRIORITY_WEI)
        max_fee = base_fee + priority
        estimate_price = base_fee + priority
    elif model == chains.FEE_ARBITRUM_NITRO:
        gas_price = int(_evm_rpc(spec, url, "eth_gasPrice", [], source_context=source_context), 16)
        priority = 0
        max_fee = _grown(gas_price)
        estimate_price = gas_price
    else:
        priority = int(_evm_rpc(spec, url, "eth_maxPriorityFeePerGas", [], source_context=source_context), 16)
        max_fee = _grown(base_fee) + priority
        estimate_price = base_fee + priority
    if model == chains.FEE_ARBITRUM_NITRO or has_code:
        estimate = int(_evm_rpc(spec, url, "eth_estimateGas", [{"from": account_address, "to": proposal.destination, "value": hex(amount)}], source_context=source_context), 16)
        buffer_bps = ARBITRUM_GAS_BUFFER_BPS if model == chains.FEE_ARBITRUM_NITRO else CONTRACT_GAS_BUFFER_BPS
        gas_limit = _buffered(estimate, buffer_bps)
    else:
        gas_limit = EOA_GAS_LIMIT
    tx_params = {
        "chain_id": int(spec.chain_id), "nonce": nonce, "max_priority_fee_per_gas": priority, "max_fee_per_gas": max_fee,
        "gas_limit": gas_limit, "to": proposal.destination, "value": amount,
    }
    fee_estimate = gas_limit * estimate_price
    fee_max = gas_limit * max_fee
    fee_parts: dict[str, Any] = {"execution_estimate_minor": fee_estimate, "execution_max_minor": fee_max}
    if model == chains.FEE_OP_STACK:
        unsigned = unsigned_type2_bytes(**tx_params)
        l1 = int(_evm_rpc(spec, url, "eth_call", [{"to": GAS_PRICE_ORACLE, "data": _abi_call("getL1Fee(bytes)", ["bytes"], [unsigned])}, "latest"], source_context=source_context), 16)
        operator = int(_evm_rpc(spec, url, "eth_call", [{"to": GAS_PRICE_ORACLE, "data": _abi_call("getOperatorFee(uint256)", ["uint256"], [gas_limit])}, "latest"], source_context=source_context), 16)
        l1_ceiling = _buffered(l1, OP_L1_FEE_BUFFER_BPS)
        fee_parts.update({"l1_estimate_minor": l1, "l1_ceiling_minor": l1_ceiling, "l1_is_estimate": True, "operator_minor": operator})
        fee_estimate += l1 + operator
        fee_max += l1_ceiling + operator
    return {
        "balance_minor": balance, "balance_ref": f"block:{block_number}", "fee_estimate_minor": fee_estimate, "fee_max_minor": fee_max,
        "fee_parts": fee_parts, "tx_params": tx_params, "cues": cues,
    }


# --- minting, reading, superseding -------------------------------------------------------------------------

def _held_elsewhere(wallet_id: str, network: str, proposal_id: str, *, asset: str) -> int:
    """What other in-flight transfers already hold of ``asset`` on this row (their amounts and reserved fees in that
    asset). Per asset: a token principal never reduces the native balance, and a token transfer's native fee
    companion does. The quoted proposal's own holds (principal and fee companion) are excluded."""
    with connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_minor + fee_minor), 0) FROM wallet_spend_ledger WHERE wallet_id = ? AND chain = ? AND asset = ? AND state = ? "
            "AND proposal_id NOT IN (?, ?)",
            (str(wallet_id), str(network), str(asset).upper(), limits.RESERVATION_RESERVED, str(proposal_id), limits._fee_hold_id(proposal_id)),
        ).fetchone()
    return int(row[0] or 0)


def mint_quote(proposal_id: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    custody.require_enabled(source_context=source_context)
    proposal = proposals.get_proposal(proposal_id)
    if proposal is None:
        raise _fault("wallet_not_found", proposal_id=str(proposal_id)[:64], source_context=source_context)
    if not transfers.is_pilot_transfer(proposal):
        raise _fault("wallet_quote_unavailable", proposal_id=proposal.proposal_id, reason="not_a_pilot_transfer", source_context=source_context)
    if proposal.state not in transfers.OPEN_PROPOSAL_STATES:
        raise _fault("wallet_quote_unavailable", proposal_id=proposal.proposal_id, reason=f"proposal_state_{proposal.state}", source_context=source_context)
    spec = environment.require_active(proposal.network, source_context=source_context)
    profile = custody.require_wallet(proposal.wallet_id, source_context=source_context)
    if custody.seal_policy(profile.wallet_id) == custody.PILOT_SEAL_POLICY and custody.setup_state(profile.wallet_id) not in {"", "ready"}:
        raise _fault("wallet_setup_state_invalid", wallet_id=profile.wallet_id, reason="setup_not_finished", source_context=source_context)
    native = chains.native_asset(spec.network)
    from core.wallet import svm_tokens

    token_payment = spec.is_svm and proposal.origin in (proposals.ORIGIN_USEPOD, proposals.ORIGIN_DNA_FEE) and svm_tokens.is_token_transfer(spec.network, proposal.asset)
    if not token_payment and str(proposal.asset).upper() not in {native.symbol.upper(), (spec.native_display_symbol or native.symbol).upper()}:
        raise _fault("wallet_quote_unavailable", proposal_id=proposal.proposal_id, reason="native_coin_transfers_only", source_context=source_context)
    if proposal.origin == proposals.ORIGIN_USEPOD:
        # a provider requirement is quoted only inside its window; a closed one ends the request here, typed
        from core.wallet import usepod

        usepod.require_binding(proposal, moment=_now(), expiry_code="wallet_quote_unavailable", source_context=source_context)
    recipient = dict(getattr(proposal, "recipient", {}) or {})
    if recipient.get("resolution") == "contact":
        # the destination came from a saved contact: quote it only while Contacts still holds exactly that destination
        from core.contacts import verify_snapshot

        check = verify_snapshot(recipient)
        if not check.ok:
            raise _fault("wallet_recipient_refused", proposal_id=proposal.proposal_id, reason=f"contact_{check.status}", detail=check.message, source_context=source_context)
    if proposal.origin == proposals.ORIGIN_DNA_FEE:
        # a fee collection has no sheet of its own: it is planned into, shown on and approved with its provider
        # payment's quote (nobody volunteers to pay fees through a separate card)
        raise _fault("wallet_quote_unavailable", proposal_id=proposal.proposal_id, reason="dna_fee_collection_rides_its_payment", source_context=source_context)
    if token_payment:
        return _mint_token_quote(spec, proposal, profile, source_context=source_context)
    presentation = environment.presentation(spec)
    from core.wallet import lifecycle

    specific = _solana_quote(spec, proposal, profile.public_key, source_context=source_context) if spec.is_svm else _evm_quote(spec, proposal, profile.public_key, source_context=source_context)
    now = _now()
    amount = int(proposal.amount_minor)
    held = _held_elsewhere(profile.wallet_id, spec.network, proposal.proposal_id, asset=native.symbol)
    balance = int(specific["balance_minor"])
    fee_estimate, fee_max = int(specific["fee_estimate_minor"]), int(specific["fee_max_minor"])
    fields: dict[str, Any] = {
        "proposal_id": proposal.proposal_id, "network": spec.network, "chain_key": spec.chain_key,
        "chain_label": chains.CHAIN_KEY_LABELS.get(spec.chain_key, spec.display_name), "display_name": spec.display_name,
        "environment": spec.environment, **presentation, "chain_identity": spec.genesis_hash if spec.is_svm else spec.chain_id,
        "from_label": profile.label or chains.CHAIN_KEY_LABELS.get(spec.chain_key, spec.display_name), "from_address": profile.public_key,
        "to_address": proposal.destination, "asset": native.symbol, "display_symbol": spec.native_display_symbol or native.symbol,
        "gas_asset": spec.native_display_symbol or native.symbol, "decimals": native.decimals, "amount_minor": amount,
        "amount_human": amounts.format_minor(amount, native.decimals), "fee_model": spec.fee_model,
        "balance_observed_at": now, "balance_stale": False, "held_elsewhere_minor": held, **specific,
        "max_total_minor": amount + fee_max,
        "estimated_after_minor": balance - amount - fee_estimate,
        "minimum_after_minor": balance - amount - fee_max,
        "quoted_at": now, "expires_at": now + QUOTE_TTL_SECONDS,
        "approval_method": str(getattr(profile, "approval_method", "") or custody.APPROVAL_PIN),
        # who the destination was resolved to (Contacts), bound by this digest beside the exact to_address
        "recipient_saved": recipient.get("resolution") == "contact",
        "recipient_label": (str(recipient.get("display_name") or "") + (f" · {recipient['label']}" if recipient.get("label") else ""))
        if recipient.get("resolution") == "contact" else "",
        "recipient_contact_id": str(recipient.get("contact_id") or ""), "recipient_endpoint_id": str(recipient.get("endpoint_id") or ""),
        "recipient_fingerprint": str(recipient.get("fingerprint") or ""), "recipient_verification": str(recipient.get("verification") or ""),
        "recipient_warning": str(recipient.get("warning") or ""),
    }
    # the sheet renders these strings verbatim: one formatting authority, no money arithmetic in the page
    for name in ("balance", "fee_estimate", "fee_max", "max_total", "estimated_after", "minimum_after"):
        fields[f"{name}_human"] = amounts.format_minor(int(fields[f"{name}_minor"]), native.decimals)
    # the shortened forms a person reads first: estimates marked as such, a maximum never understated, a minimum
    # never overstated, the requested amount always exact; the exact strings above stay for the Details section
    gas = fields["gas_asset"]
    fields["amount_display"] = amounts.display_amount(amount, native.decimals, fields["display_symbol"], exact=True)
    fields["balance_display"] = amounts.display_amount(balance, native.decimals, gas)
    fields["fee_estimate_display"] = amounts.display_amount(fee_estimate, native.decimals, gas)
    fields["fee_max_display"] = amounts.display_amount(fee_max, native.decimals, gas, mode=amounts.ROUND_UP)
    fields["max_total_display"] = amounts.display_amount(amount + fee_max, native.decimals, gas, mode=amounts.ROUND_UP)
    fields["estimated_after_display"] = amounts.display_amount(balance - amount - fee_estimate, native.decimals, gas)
    fields["minimum_after_display"] = amounts.display_amount(balance - amount - fee_max, native.decimals, gas, mode=amounts.ROUND_DOWN)
    fields["fee_asset_differs"] = fields["gas_asset"].upper() != fields["display_symbol"].upper()
    from core.wallet import purpose

    fields["purpose"] = purpose.purpose_for(proposal)
    _attach_fee_and_review(
        fields, proposal, rpc=lifecycle.RpcClient(chains.network_rpc_url(spec.network), network=spec.network) if spec.is_svm else None,
        blockhash=str(specific.get("recent_blockhash") or ""), principal_balance=balance, held_principal=held, fee_balance=balance, held_native=held,
        rent_minimum=int(specific.get("rent_minimum_minor") or 0), source_context=source_context,
    )
    spendable = balance - held
    shortfall = amount + fee_max - spendable
    if shortfall > 0:
        raise _fault(
            "wallet_insufficient_funds", proposal_id=proposal.proposal_id, reason="amount_plus_fee_ceiling_exceeds_spendable", shortfall_minor=shortfall,
            shortfall_human=amounts.format_minor(shortfall, native.decimals), gas_asset=fields["gas_asset"], source_context=source_context,
        )
    if spec.is_svm:
        remainder = spendable - amount - fee_max
        if remainder == 0:
            raise _fault("wallet_insufficient_funds", proposal_id=proposal.proposal_id, reason="full_drain_not_offered_in_the_pilot", source_context=source_context)
        if remainder < int(specific["rent_minimum_minor"]):
            raise _fault("wallet_insufficient_funds", proposal_id=proposal.proposal_id, reason="remainder_below_rent_minimum", remainder_minor=remainder, source_context=source_context)
    digest = digest_of(fields)
    quote_id = f"quote-{uuid.uuid4().hex[:20]}"
    with connection() as conn:
        limits._begin_immediate(conn)
        conn.execute("UPDATE wallet_quotes SET state = ? WHERE proposal_id = ? AND state = ?", (STATE_SUPERSEDED, proposal.proposal_id, STATE_OPEN))
        conn.execute(
            "INSERT INTO wallet_quotes (quote_id, proposal_id, wallet_id, network, environment, digest, fields_json, state, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (quote_id, proposal.proposal_id, profile.wallet_id, spec.network, spec.environment, digest, json.dumps(fields, sort_keys=True, default=str), STATE_OPEN, now, fields["expires_at"]),
        )
    return {"quote_id": quote_id, "proposal_id": proposal.proposal_id, "digest": digest, "state": STATE_OPEN, "fields": fields}


def _mint_token_quote(spec: chains.ChainIdentity, proposal: Any, profile: Any, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    """A registered token on a Solana row, paid for the UsePod x402 lane: the principal is quoted, held and shown in the
    token, the network fee in the native coin -- two assets, never one sum. Every chain fact comes through
    ``svm_tokens.require_payable`` (mint, recipient token account, spendable token balance, a native balance carrying
    the fee and the rent-exempt remainder), with what other in-flight transfers already hold subtracted per asset."""
    from core.wallet import lifecycle, svm_tokens

    native = chains.native_asset(spec.network)
    token = svm_tokens.token_asset(spec.network, proposal.asset, source_context=source_context)
    rpc = lifecycle.RpcClient(chains.network_rpc_url(spec.network), network=spec.network)
    _prove(spec, rpc.url, lambda method: rpc._call(method, []), source_context=source_context)
    amount = int(proposal.amount_minor)
    held_token = _held_elsewhere(profile.wallet_id, spec.network, proposal.proposal_id, asset=token.symbol)
    held_native = _held_elsewhere(profile.wallet_id, spec.network, proposal.proposal_id, asset=native.symbol)
    try:
        latest = rpc._call("getLatestBlockhash", [{"commitment": "confirmed"}])
        blockhash = str(latest["value"]["blockhash"])
        message = lifecycle._build_message(proposal, profile.public_key, blockhash)
        fee = rpc._call("getFeeForMessage", [base64.b64encode(bytes(message)).decode("ascii"), {"commitment": "confirmed"}])
    except WalletFault:
        raise
    except Exception as exc:
        raise _fault("wallet_quote_unavailable", network=spec.network, reason=f"rpc_read_failed:{type(exc).__name__}", source_context=source_context) from None
    fee_value = (fee or {}).get("value") if isinstance(fee, dict) else None
    if fee_value is None:
        raise _fault("wallet_quote_unavailable", network=spec.network, reason="fee_unknown_for_message", source_context=source_context)
    fee_minor = int(fee_value)
    facts = svm_tokens.require_payable(
        rpc, network=spec.network, asset=token.symbol, payer=profile.public_key, recipient_owner=proposal.destination, amount_minor=amount,
        held_token_minor=held_token, fee_max_minor=fee_minor, held_native_minor=held_native, source_context=source_context,
    )
    principal_balance, fee_balance = int(facts["principal_balance_minor"]), int(facts["fee_balance_minor"])
    now = _now()
    fields: dict[str, Any] = {
        "proposal_id": proposal.proposal_id, "network": spec.network, "chain_key": spec.chain_key,
        "chain_label": chains.CHAIN_KEY_LABELS.get(spec.chain_key, spec.display_name), "display_name": spec.display_name,
        "environment": spec.environment, **environment.presentation(spec), "chain_identity": spec.genesis_hash,
        "from_label": profile.label or chains.CHAIN_KEY_LABELS.get(spec.chain_key, spec.display_name), "from_address": profile.public_key,
        "to_address": proposal.destination, "asset": token.symbol, "display_symbol": token.symbol, "gas_asset": spec.native_display_symbol or native.symbol,
        "decimals": token.decimals, "fee_decimals": native.decimals, "amount_minor": amount, "amount_human": amounts.format_minor(amount, token.decimals),
        "fee_model": spec.fee_model, "token_transfer": True, "mint": token.address,
        "recipient_token_account": facts["recipient_token_account"], "payer_token_account": facts["payer_token_account"],
        "balance_observed_at": now, "balance_stale": False, "balance_ref": f"slot:{int((latest.get('context') or {}).get('slot') or 0)}",
        "balance_minor": principal_balance, "principal_balance_minor": principal_balance, "fee_balance_minor": fee_balance,
        "held_elsewhere_minor": held_token, "held_native_elsewhere_minor": held_native,
        "fee_estimate_minor": fee_minor, "fee_max_minor": fee_minor, "fee_parts": {"base_minor": fee_minor},
        "rent_minimum_minor": int(facts["rent_minimum_minor"]), "recent_blockhash": blockhash,
        "last_valid_block_height": int(latest["value"].get("lastValidBlockHeight") or 0), "cues": [],
        # the principal ceiling in the token; the fee ceiling is its own field, in the gas asset
        "max_total_minor": amount,
        "estimated_after_minor": principal_balance - amount, "minimum_after_minor": principal_balance - held_token - amount,
        "fee_balance_after_minimum_minor": fee_balance - held_native - fee_minor,
        "quoted_at": now, "expires_at": now + QUOTE_TTL_SECONDS,
        "approval_method": str(getattr(profile, "approval_method", "") or custody.APPROVAL_PIN),
    }
    # one formatting authority per asset: token figures in the token's decimals, fee figures in the native coin's
    for name in ("balance", "principal_balance", "max_total", "estimated_after", "minimum_after"):
        fields[f"{name}_human"] = amounts.format_minor(int(fields[f"{name}_minor"]), token.decimals)
    for name in ("fee_estimate", "fee_max", "fee_balance", "fee_balance_after_minimum"):
        fields[f"{name}_human"] = amounts.format_minor(int(fields[f"{name}_minor"]), native.decimals)
    # the shortened forms a person reads first (the readable sheet's display fields, composed for the
    # two-asset token lane per usepod-end-to-end INTEGRATION-HANDOFF-08d77f61 step 2): token figures in
    # the token, fee figures in the gas asset, never one sum across assets; the amount exact, maxima
    # rounded up, minima rounded down
    fields["amount_display"] = amounts.display_amount(amount, token.decimals, token.symbol, exact=True)
    fields["balance_display"] = amounts.display_amount(principal_balance, token.decimals, token.symbol)
    fields["fee_estimate_display"] = amounts.display_amount(fee_minor, native.decimals, native.symbol)
    fields["fee_max_display"] = amounts.display_amount(fee_minor, native.decimals, native.symbol, mode=amounts.ROUND_UP)
    fields["max_total_display"] = amounts.display_amount(amount, token.decimals, token.symbol, mode=amounts.ROUND_UP)
    fields["estimated_after_display"] = amounts.display_amount(principal_balance - amount, token.decimals, token.symbol)
    fields["minimum_after_display"] = amounts.display_amount(principal_balance - held_token - amount, token.decimals, token.symbol, mode=amounts.ROUND_DOWN)
    fields["fee_asset_differs"] = native.symbol.upper() != token.symbol.upper()
    from core.wallet import purpose

    fields["purpose"] = purpose.purpose_for(proposal)
    _attach_fee_and_review(
        fields, proposal, rpc=rpc, blockhash=blockhash, principal_balance=principal_balance, held_principal=held_token, fee_balance=fee_balance,
        held_native=held_native, rent_minimum=int(facts["rent_minimum_minor"]), source_context=source_context,
    )
    digest = digest_of(fields)
    quote_id = f"quote-{uuid.uuid4().hex[:20]}"
    with connection() as conn:
        limits._begin_immediate(conn)
        conn.execute("UPDATE wallet_quotes SET state = ? WHERE proposal_id = ? AND state = ?", (STATE_SUPERSEDED, proposal.proposal_id, STATE_OPEN))
        conn.execute(
            "INSERT INTO wallet_quotes (quote_id, proposal_id, wallet_id, network, environment, digest, fields_json, state, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (quote_id, proposal.proposal_id, profile.wallet_id, spec.network, spec.environment, digest, json.dumps(fields, sort_keys=True, default=str), STATE_OPEN, now, fields["expires_at"]),
        )
    return {"quote_id": quote_id, "proposal_id": proposal.proposal_id, "digest": digest, "state": STATE_OPEN, "fields": fields}


def _attach_fee_and_review(
    fields: dict[str, Any], proposal: Any, *, rpc: Any, blockhash: str, principal_balance: int, held_principal: int, fee_balance: int, held_native: int,
    rent_minimum: int, source_context: dict[str, Any] | None,
) -> None:
    """The DNA service-fee facts the sheet shows and the approval binds, from the fee owner (core.wallet.dna_fees):
    for a native x402 provider payment the exact fee on THIS payment, the accrued position, and the collection of
    previously accrued fees PLANNED to ride this payment when it is economical (``companion``: its own proposal, ledger
    identity and version, amount, treasury, network-fee ceiling). Then the finite list of actions the approval covers
    and the compact review (core.wallet.payment_review), all inside the digest."""
    from core.wallet import dna_fees, payment_review

    fields["companion"] = None
    if proposal.origin == proposals.ORIGIN_USEPOD:
        from core.wallet import usepod

        record = usepod.operation_for_proposal(proposal.proposal_id) or {}
        fields["provider_payment_kind"] = str(record.get("payment_kind") or usepod.KIND_UNKNOWN)
        plan = None
        if rpc is not None and blockhash:
            plan = dna_fees.plan_companion_collection(
                proposal, rpc=rpc, blockhash=blockhash, payment_fee_max_atomic=int(fields.get("fee_max_minor") or 0), principal_balance_atomic=int(principal_balance),
                held_principal_atomic=int(held_principal), fee_balance_atomic=int(fee_balance), held_native_atomic=int(held_native), rent_minimum_atomic=int(rent_minimum),
                source_context=source_context,
            )
        fee = dna_fees.payment_preview(proposal, plan=plan)
        if fee is not None:
            fields["dna_fee"] = fee
            fields["companion"] = plan.get("companion") if plan and plan.get("due") else None
            # total authorized exposure, per asset: the principal plus the fee's whole-unit ceiling in the paid asset;
            # the network fee stays in its own asset and its own row
            decimals = int(fields.get("decimals") or 0)
            fields["authorized_exposure_minor"] = int(fields["amount_minor"]) + int(fee["fee_reserved_ceiling_atomic"])
            fields["authorized_exposure_human"] = amounts.format_minor(fields["authorized_exposure_minor"], decimals)
    review = payment_review.compose(fields, proposal)
    fields["actions"] = review["actions"]
    fields["review"] = review


def get_quote(quote_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT quote_id, proposal_id, wallet_id, network, environment, digest, fields_json, state, created_at, expires_at FROM wallet_quotes WHERE quote_id = ?",
            (str(quote_id or ""),),
        ).fetchone()
    if not row:
        return None
    return {
        "quote_id": row[0], "proposal_id": row[1], "wallet_id": row[2], "network": row[3], "environment": row[4], "digest": row[5],
        "fields": json.loads(row[6]), "state": row[7], "created_at": row[8], "expires_at": row[9],
    }


def open_quote_for(proposal_id: str) -> dict[str, Any] | None:
    """The open quote of one proposal, as the status poll shows it: a sheet holding another id is stale."""
    with connection() as conn:
        row = conn.execute("SELECT quote_id, expires_at FROM wallet_quotes WHERE proposal_id = ? AND state = ?", (str(proposal_id or ""), STATE_OPEN)).fetchone()
    return {"quote_id": str(row[0]), "expires_at": float(row[1])} if row else None


class QuoteConsumeError(Exception):
    """The quote was not open, carried another digest, or had expired when it was consumed."""


def _consume(conn: Any, quote_id: str, digest: str, now: float) -> None:
    """Consume an open quote on the caller's connection: once, only with its digest, only before it expires."""
    cursor = conn.execute(
        "UPDATE wallet_quotes SET state = ? WHERE quote_id = ? AND state = ? AND digest = ? AND expires_at > ?",
        (STATE_CONSUMED, str(quote_id or ""), STATE_OPEN, str(digest or ""), float(now)),
    )
    if cursor.rowcount != 1:
        raise QuoteConsumeError(f"quote_not_consumable:{str(quote_id)[:40]}")


def supersede_quote(quote_id: str, *, reason: str = "") -> bool:
    """Close one open quote (a revalidation found the chain moved): the sheet must mint a fresh preview."""
    with connection() as conn:
        cursor = conn.execute("UPDATE wallet_quotes SET state = ? WHERE quote_id = ? AND state = ?", (STATE_SUPERSEDED, str(quote_id or ""), STATE_OPEN))
    return cursor.rowcount == 1


def supersede_open_quotes_for_wallet(wallet_id: str, *, reason: str = "") -> int:
    """Close every open quote of one wallet (a credential was recovered or changed): every sheet must mint again."""
    with connection() as conn:
        cursor = conn.execute("UPDATE wallet_quotes SET state = ? WHERE wallet_id = ? AND state = ?", (STATE_SUPERSEDED, str(wallet_id or ""), STATE_OPEN))
        return int(cursor.rowcount or 0)


def supersede_open_quotes(*, reason: str = "") -> int:
    with connection() as conn:
        cursor = conn.execute("UPDATE wallet_quotes SET state = ? WHERE state = ?", (STATE_SUPERSEDED, STATE_OPEN))
        return int(cursor.rowcount or 0)


def require_open_quote(quote_id: str, *, proposal: Any, quote_digest: str, account_address: str, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The quote an approval binds: open, this proposal's, the exact digest, unexpired, and still the same transfer."""
    record = get_quote(quote_id)
    if record is None or record["proposal_id"] != proposal.proposal_id:
        raise _fault("wallet_quote_mismatch", proposal_id=proposal.proposal_id, reason="quote_not_for_this_proposal", source_context=source_context)
    if record["state"] != STATE_OPEN:
        raise _fault("wallet_quote_expired", proposal_id=proposal.proposal_id, reason=f"quote_{record['state']}", source_context=source_context)
    if not hmac.compare_digest(str(quote_digest or ""), str(record["digest"])) or digest_of(record["fields"]) != record["digest"]:
        raise _fault("wallet_quote_mismatch", proposal_id=proposal.proposal_id, reason="quote_digest_mismatch", source_context=source_context)
    if _now() > float(record["fields"].get("expires_at") or 0):
        raise _fault("wallet_quote_expired", proposal_id=proposal.proposal_id, reason="quote_expired", source_context=source_context)
    fields = record["fields"]
    spec = chains.resolve_network(proposal.network)
    active = environment.active_environment().environment
    same = (
        fields.get("network") == spec.network and fields.get("environment") == spec.environment == active
        and fields.get("from_address") == account_address and fields.get("to_address") == proposal.destination
        and int(fields.get("amount_minor") or -1) == int(proposal.amount_minor)
    )
    if not same:
        raise _fault("wallet_quote_mismatch", proposal_id=proposal.proposal_id, reason="binding_changed_since_the_quote", source_context=source_context)
    return record


__all__ = [
    "ARBITRUM_GAS_BUFFER_BPS",
    "BSC_MIN_PRIORITY_WEI",
    "GROWTH_BLOCKS",
    "QUOTE_DOMAIN",
    "QUOTE_TTL_SECONDS",
    "digest_of",
    "get_quote",
    "mint_quote",
    "open_quote_for",
    "require_open_quote",
    "supersede_open_quotes",
    "supersede_open_quotes_for_wallet",
    "unsigned_type2_bytes",
]
