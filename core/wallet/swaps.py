"""Convert one coin into another inside the owner's own Solana wallet ("we only have SOL, convert some to USDC?").

A swap is an ordinary Crypto Pilot proposal with origin ``swap``: the amount held is the most the wallet can pay in
the input coin, the destination is the wallet's own address, and it is approved on the ordinary sheet with the
owner's PIN, under the same caps, freeze and receipts as a send. Nothing here can approve, sign or send.

Provider: Jupiter's public swap API (``API_BASE``, no key). Jupiter quotes a route and builds the transaction; VOOL
never signs that transaction as given. Before the key opens (:func:`build_verified_message`):

* the transaction needs exactly ONE signature, this wallet's, and this wallet pays the fee;
* every program it calls at the top level is on ``ALLOWED_PROGRAMS``; System, Token and Associated Token Account
  instructions may only wrap SOL into, sync, and close the wallet's OWN wrapped-SOL account, and open the wallet's own
  token accounts; the compute price is capped (``PRIORITY_FEE_CAP_LAMPORTS``);
* the node simulates it against the wallet's own accounts and the outcome must be inside what the sheet showed: the
  input coin falls by at most the shown maximum (plus the network cost ceiling for SOL), the output coin rises by at
  least the shown minimum, and no token account of the wallet gains a delegate or a close authority or changes owner.

Any mismatch refuses typed with nothing signed. After landing, :func:`prove_landed` reads the chain's own balances
before the swap counts as done. Solana Mainnet only: Jupiter has no Devnet liquidity.
"""
from __future__ import annotations

import base64
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from core.wallet import amounts, chains, custody, proposals
from core.wallet.security import wallet_fault
from core.wallet.store import connection, dumps, loads, utcnow

AUTHORITY = "core.wallet.swaps"
PROVIDER = "Jupiter"
#: Jupiter's public swap API. Tests point this at a loopback stand-in; any other non-HTTPS base is refused.
API_BASE = "https://lite-api.jup.ag/swap/v1"
HTTP_TIMEOUT_SECONDS = 12.0

WSOL_MINT = "So11111111111111111111111111111111111111112"
JUPITER_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
#: The programs a swap transaction may call at the top level. Jupiter's own instruction routes through the venues;
#: its effect on the wallet is checked by the simulation, not trusted.
ALLOWED_PROGRAMS = frozenset({JUPITER_PROGRAM, SYSTEM_PROGRAM, TOKEN_PROGRAM, ASSOCIATED_TOKEN_PROGRAM, COMPUTE_BUDGET_PROGRAM})

#: The coins a swap may take or give on Solana Mainnet (the registry's rows; SOL rides as wrapped SOL).
SWAPPABLE = ("SOL", "USDC", "USDT")
DEFAULT_SLIPPAGE_BPS = 50
MAX_SLIPPAGE_BPS = 100
#: A route that moves the price by more than this (percent) is refused: the owner would lose too much to the trade.
MAX_PRICE_IMPACT_PCT = 2.0
#: An exact-output swap holds this much above the quoted maximum, so a refreshed quote can still fit the proposal.
EXACT_OUT_HEADROOM_BPS = 100
BASE_FEE_LAMPORTS = 5_000
PRIORITY_FEE_CAP_LAMPORTS = 200_000
TOKEN_ACCOUNT_RENT_LAMPORTS = 2_039_280
RENT_EXEMPT_MINIMUM_LAMPORTS = 890_880

SIDE_IN = "in"    # the owner names what they give: "convert 1 SOL to USDC" (ExactIn)
SIDE_OUT = "out"  # the owner names what they need: "get 10 USDC" (ExactOut)
BINDING_VERSION = 1

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS wallet_swap_bindings (
        proposal_id TEXT PRIMARY KEY, version INTEGER NOT NULL, wallet_id TEXT NOT NULL, network TEXT NOT NULL,
        binding_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
)


def _fault(code: str, *, source_context: dict[str, Any] | None = None, message: str = "", **context: Any) -> Any:
    fault = wallet_fault(code, authority=AUTHORITY, context=context, source_context=source_context)
    if message:
        fault.user_message = message
    return fault


def _ensure_schema(conn: Any) -> None:
    for statement in SCHEMA:
        conn.execute(statement)


# --- coins -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Coin:
    symbol: str
    mint: str
    decimals: int
    native: bool


def coin(network: str, symbol: str, *, source_context: dict[str, Any] | None = None) -> Coin:
    """A coin a swap may take or give on this row, or a typed refusal naming what can be swapped."""
    spec = chains.resolve_network(network)
    wanted = str(symbol or "").strip().upper()
    if not (spec.is_svm and spec.is_mainnet):
        raise _fault("wallet_network_disabled", reason="swaps_need_solana_mainnet", network=spec.network, source_context=source_context,
                     message="Converting coins works on Solana Mainnet only. Nothing was converted.")
    if wanted not in SWAPPABLE:
        raise _fault("wallet_network_disabled", reason="coin_not_swappable", asset=wanted[:16], source_context=source_context,
                     message=f"I can convert between {', '.join(SWAPPABLE)} on Solana. {wanted or 'That coin'} is not one of them. Nothing was converted.")
    asset = chains.asset_for(spec.network, wanted)
    return Coin(symbol=asset.symbol, mint=WSOL_MINT if asset.native else str(asset.address), decimals=int(asset.decimals), native=bool(asset.native))


# --- Jupiter ------------------------------------------------------------------------------------------------------

def _http_json(method: str, path: str, *, query: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    base = str(API_BASE).rstrip("/")
    parts = urllib.parse.urlsplit(base)
    if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("swap_api_not_https")
    url = f"{base}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "VOOL-wallet"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310 (pinned base above)
            raw = response.read(2_000_000)
    except urllib.error.HTTPError as exc:
        raw = exc.read(20_000)
        try:
            answer = json.loads(raw.decode() or "{}")
        except ValueError:
            answer = {}
        raise _ProviderRefused(int(exc.code), str((answer or {}).get("errorCode") or (answer or {}).get("error") or "")[:80]) from None
    answer = json.loads(raw.decode())
    if not isinstance(answer, dict):
        raise ValueError("swap_api_answer_not_an_object")
    return answer


class _ProviderRefused(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"{status}:{code}")
        self.status, self.code = status, code


def _int_field(answer: dict[str, Any], key: str) -> int:
    value = answer.get(key)
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError(f"missing_{key}")
    number = int(str(value))
    if number <= 0:
        raise ValueError(f"non_positive_{key}")
    return number


def fetch_quote(*, source: Coin, target: Coin, amount_minor: int, side: str, slippage_bps: int, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Jupiter's quote, checked field by field against what was asked. Returns the typed terms plus the provider's raw
    quote (needed again, unchanged, to build the transaction)."""
    mode = "ExactIn" if side == SIDE_IN else "ExactOut"
    query = {"inputMint": source.mint, "outputMint": target.mint, "amount": str(int(amount_minor)), "slippageBps": str(int(slippage_bps)),
             "swapMode": mode, "restrictIntermediateTokens": "true"}
    try:
        raw = _http_json("GET", "/quote", query=query)
    except _ProviderRefused as refused:
        raise _fault("wallet_quote_unavailable", reason=f"swap_provider_refused:{refused.code or refused.status}", source_context=source_context,
                     message=f"{PROVIDER} has no price for this conversion right now ({refused.code or refused.status}). Nothing was converted.") from None
    except Exception as exc:
        raise _fault("wallet_quote_unavailable", reason=f"swap_provider_unreachable:{type(exc).__name__}", source_context=source_context,
                     message=f"I could not reach {PROVIDER} for a price. Nothing was converted.") from None
    try:
        if str(raw.get("inputMint")) != source.mint or str(raw.get("outputMint")) != target.mint:
            raise ValueError("mints_differ")
        if str(raw.get("swapMode")) != mode or int(str(raw.get("slippageBps"))) != int(slippage_bps):
            raise ValueError("terms_differ")
        in_amount, out_amount, threshold = _int_field(raw, "inAmount"), _int_field(raw, "outAmount"), _int_field(raw, "otherAmountThreshold")
        impact = abs(float(str(raw.get("priceImpactPct") or "0")))
        if not math.isfinite(impact):
            raise ValueError("price_impact_not_a_number")
        if side == SIDE_IN and in_amount != int(amount_minor):
            raise ValueError("input_differs")
        if side == SIDE_OUT and out_amount != int(amount_minor):
            raise ValueError("output_differs")
        if raw.get("platformFee") not in (None, {}) and int(str((raw.get("platformFee") or {}).get("amount") or "0")) != 0:
            raise ValueError("platform_fee_charged")
        route = [str(((step or {}).get("swapInfo") or {}).get("label") or "?")[:40] for step in (raw.get("routePlan") or [])]
        if not route:
            raise ValueError("no_route")
    except ValueError as exc:
        raise _fault("wallet_quote_unavailable", reason=f"swap_quote_malformed:{exc}", source_context=source_context,
                     message=f"{PROVIDER}'s price did not match what was asked, so I did not use it. Nothing was converted.") from None
    impact_pct = impact * 100.0  # Jupiter reports a fraction ("0.0012" = 0.12 %)
    if impact_pct > MAX_PRICE_IMPACT_PCT:
        raise _fault("wallet_quote_unavailable", reason="swap_price_impact_too_high", price_impact_pct=round(impact_pct, 3), source_context=source_context,
                     message=f"This conversion would move the price by {impact_pct:.2f}%, more than the {MAX_PRICE_IMPACT_PCT:g}% I allow. Try a smaller amount. Nothing was converted.")
    max_in = in_amount if side == SIDE_IN else threshold
    min_out = threshold if side == SIDE_IN else out_amount
    return {
        "side": side, "mode": mode, "slippage_bps": int(slippage_bps), "in_minor": in_amount, "out_minor": out_amount,
        "max_in_minor": max_in, "min_out_minor": min_out, "price_impact_pct": round(impact_pct, 4), "route": route,
        "context_slot": int(raw.get("contextSlot") or 0), "quoted_at": time.time(), "raw": raw,
    }


# --- the binding ------------------------------------------------------------------------------------------------

def binding_for(proposal_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        _ensure_schema(conn)
        row = conn.execute("SELECT binding_json FROM wallet_swap_bindings WHERE proposal_id = ?", (str(proposal_id or ""),)).fetchone()
    found = loads(row[0], {}) if row else {}
    return found or None


def _save_binding(proposal_id: str, binding: dict[str, Any], *, conn: Any = None) -> None:
    now = utcnow()

    def write(c: Any) -> None:
        _ensure_schema(c)
        c.execute(
            "INSERT INTO wallet_swap_bindings (proposal_id, version, wallet_id, network, binding_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(proposal_id) DO UPDATE SET binding_json = excluded.binding_json, updated_at = excluded.updated_at",
            (str(proposal_id), BINDING_VERSION, str(binding["wallet_id"]), str(binding["network"]), dumps(binding), now, now),
        )

    if conn is not None:
        write(conn)
        return
    with connection() as own:
        write(own)


def is_swap(proposal: Any) -> bool:
    return str(getattr(proposal, "origin", "") or "") == proposals.ORIGIN_SWAP


def _choose_wallet(network: str, wallet_id: str, *, source_context: dict[str, Any] | None) -> custody.WalletProfile:
    from core.wallet import wallet_roles

    chosen = str(wallet_id or "").strip() or wallet_roles.main_wallet_id(network)
    if not chosen:
        default = custody.default_wallet()
        if default is not None and chains.resolve_network(default.network).network == network:
            chosen = default.wallet_id
    if not chosen:
        raise _fault("wallet_not_found", reason="no_solana_wallet_for_swap", source_context=source_context,
                     message="You have no Solana wallet on Mainnet yet, so there is nothing to convert. Nothing was converted.")
    profile = custody.require_wallet(chosen, source_context=source_context)
    if chains.resolve_network(profile.network).network != network:
        raise _fault("wallet_not_found", reason="wallet_not_on_solana_mainnet", source_context=source_context)
    if custody.seal_policy(profile.wallet_id) != custody.PILOT_SEAL_POLICY:
        raise _fault("wallet_send_needs_vool_wallet", reason="swap_needs_pilot_wallet", source_context=source_context)
    return profile


def propose_swap(*, from_asset: str, to_asset: str, amount: Any, side: str, wallet_id: str = "", slippage_bps: Any = None, origin: str = proposals.ORIGIN_SWAP,
                 then_send: dict[str, Any] | None = None, idempotency_key: str = "", source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Park one conversion for the owner's approval: a fresh quote, then ONE proposal holding at most the shown maximum
    of the input coin. ``then_send`` (a resolved Contacts snapshot or address, the amount and coin) is remembered so the
    send card can be offered once the swap has landed; it is never sent by this approval."""
    from core.wallet import environment

    custody.require_enabled(source_context=source_context)
    network = chains.resolve_network("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp").network
    environment.require_active(network, source_context=source_context)
    source, target = coin(network, from_asset, source_context=source_context), coin(network, to_asset, source_context=source_context)
    if source.symbol == target.symbol:
        raise _fault("wallet_quote_unavailable", reason="swap_same_coin", source_context=source_context, message=f"That is {source.symbol} to {source.symbol}: nothing to convert.")
    side = SIDE_OUT if str(side or "").strip().lower() == SIDE_OUT else SIDE_IN
    named = target if side == SIDE_OUT else source
    amount_minor = amounts.parse_human_amount(amount, decimals=named.decimals, symbol=named.symbol, source_context=source_context)
    slippage = DEFAULT_SLIPPAGE_BPS if slippage_bps in (None, "") else int(slippage_bps)
    if not 1 <= slippage <= MAX_SLIPPAGE_BPS:
        raise _fault("wallet_quote_unavailable", reason="swap_slippage_out_of_range", slippage_bps=slippage, source_context=source_context,
                     message=f"Slippage can be 0.01% to {MAX_SLIPPAGE_BPS / 100:g}%. Nothing was converted.")
    profile = _choose_wallet(network, wallet_id, source_context=source_context)
    terms = fetch_quote(source=source, target=target, amount_minor=amount_minor, side=side, slippage_bps=slippage, source_context=source_context)
    hold = terms["max_in_minor"] if side == SIDE_IN else -(-terms["max_in_minor"] * (10_000 + EXACT_OUT_HEADROOM_BPS) // 10_000)
    memo = f"Convert to {target.symbol}"
    proposal = proposals.propose_transaction(
        wallet_id=profile.wallet_id, destination=profile.public_key, amount_minor=int(hold), asset=source.symbol, origin=origin, memo=memo,
        idempotency_key=idempotency_key, source_context=source_context, network=network,
    )
    binding = {
        "version": BINDING_VERSION, "provider": PROVIDER, "wallet_id": profile.wallet_id, "network": network, "owner": profile.public_key,
        "side": side, "asked_minor": int(amount_minor), "from": source.__dict__, "to": target.__dict__, "slippage_bps": slippage,
        "terms": {k: v for k, v in terms.items() if k != "raw"}, "raw_quote": terms["raw"], "hold_minor": int(hold),
        "then_send": dict(then_send) if then_send else None, "follow_up_proposal_id": "",
    }
    _save_binding(proposal.proposal_id, binding)
    return {"proposal": proposal, "binding": binding}


def refresh_terms(proposal: Any, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """A fresh quote for an open swap (the sheet opened or refreshed). The new maximum must still fit what the proposal
    holds; otherwise the price moved too far and the owner asks again."""
    binding = binding_for(proposal.proposal_id)
    if binding is None:
        raise _fault("wallet_quote_unavailable", reason="swap_binding_missing", proposal_id=proposal.proposal_id, source_context=source_context)
    source, target = Coin(**binding["from"]), Coin(**binding["to"])
    terms = fetch_quote(source=source, target=target, amount_minor=int(binding["asked_minor"]), side=str(binding["side"]),
                       slippage_bps=int(binding["slippage_bps"]), source_context=source_context)
    if int(terms["max_in_minor"]) > int(proposal.amount_minor):
        raise _fault("wallet_quote_expired", reason="swap_price_moved_beyond_hold", proposal_id=proposal.proposal_id, source_context=source_context,
                     message=f"The price moved: it would now cost more {source.symbol} than this request holds. Ask me again for a new price. Nothing was converted.")
    binding = {**binding, "terms": {k: v for k, v in terms.items() if k != "raw"}, "raw_quote": terms["raw"]}
    _save_binding(proposal.proposal_id, binding)
    return binding


def output_account_missing(rpc: Any, binding: dict[str, Any]) -> bool:
    """Whether the wallet has no token account for the output coin yet (the swap then opens one: one-time rent)."""
    target = Coin(**binding["to"])
    if target.native:
        return False
    from core.wallet import svm_tokens

    return svm_tokens.read_token_account(rpc, owner=str(binding["owner"]), mint=target.mint, refusal_code="wallet_quote_unavailable") is None


def fee_max_minor(binding: dict[str, Any], *, opens_output_account: bool) -> int:
    return BASE_FEE_LAMPORTS + PRIORITY_FEE_CAP_LAMPORTS + (TOKEN_ACCOUNT_RENT_LAMPORTS if opens_output_account else 0)


def quote_block(binding: dict[str, Any]) -> dict[str, Any]:
    """What the sheet shows and the quote's digest binds: the exact maxima and minima, the rate, impact and route."""
    source, target, terms = Coin(**binding["from"]), Coin(**binding["to"]), binding["terms"]
    max_in, min_out, out_expected, in_expected = int(terms["max_in_minor"]), int(terms["min_out_minor"]), int(terms["out_minor"]), int(terms["in_minor"])
    rate = (out_expected / 10 ** target.decimals) / (in_expected / 10 ** source.decimals) if in_expected else 0.0
    return {
        "provider": PROVIDER, "side": binding["side"], "from_symbol": source.symbol, "to_symbol": target.symbol, "from_mint": source.mint, "to_mint": target.mint,
        "from_decimals": source.decimals, "to_decimals": target.decimals,
        "max_in_minor": max_in, "max_in_human": amounts.format_minor(max_in, source.decimals),
        "expected_in_human": amounts.format_minor(in_expected, source.decimals),
        "min_out_minor": min_out, "min_out_human": amounts.format_minor(min_out, target.decimals),
        "expected_out_human": amounts.format_minor(out_expected, target.decimals),
        "rate_text": f"1 {source.symbol} ≈ {rate:,.6g} {target.symbol}" if rate else "",
        "slippage_bps": int(binding["slippage_bps"]), "slippage_text": f"{int(binding['slippage_bps']) / 100:g}%",
        "price_impact_pct": float(terms["price_impact_pct"]), "route": list(terms["route"]),
        "provider_fee_text": "none (Jupiter's quote carries no platform fee)",
        "privacy_note": f"{PROVIDER} sees your wallet address and this trade.",
        "then_send": _then_send_view(binding.get("then_send")),
    }


def _then_send_view(then_send: Any) -> dict[str, Any] | None:
    if not isinstance(then_send, dict):
        return None
    recipient = then_send.get("recipient") if isinstance(then_send.get("recipient"), dict) else {}
    return {"amount": str(then_send.get("amount") or ""), "asset": str(then_send.get("asset") or ""),
            "to": str(recipient.get("display_name") or recipient.get("value") or then_send.get("destination") or "")}


# --- checking Jupiter's transaction before the key opens ---------------------------------------------------------

class SwapMessage:
    """A v0 (or legacy) message Jupiter built, checked. ``bytes()`` gives exactly the bytes the wallet signs."""

    def __init__(self, message: Any) -> None:
        from solders.message import MessageV0, to_bytes_versioned

        self.message = message
        self.versioned = isinstance(message, MessageV0)
        self._bytes = to_bytes_versioned(message) if self.versioned else bytes(message)

    def __bytes__(self) -> bytes:
        return self._bytes

    def serialize(self, signature: bytes) -> bytes:
        from solders.signature import Signature
        from solders.transaction import Transaction, VersionedTransaction

        if self.versioned:
            return bytes(VersionedTransaction.populate(self.message, [Signature.from_bytes(bytes(signature))]))
        return bytes(Transaction.populate(self.message, [Signature.from_bytes(bytes(signature))]))


def _refuse(reason: str, *, source_context: dict[str, Any] | None, **context: Any) -> Any:
    return _fault("wallet_simulation_failed", reason=f"swap_check:{reason}", source_context=source_context,
                  message=f"The conversion {PROVIDER} built did not match what you approved ({reason.replace('_', ' ')}), so I did not sign it. Nothing was converted.", **context)


def _ata(owner: str, mint: str) -> str:
    from core.wallet import svm_tokens

    return svm_tokens.associated_account(owner, mint)


def _lookup_addresses(rpc: Any, table: str) -> list[str]:
    from solders.pubkey import Pubkey

    answer = rpc._call("getAccountInfo", [table, {"encoding": "base64", "commitment": "confirmed"}])
    value = (answer or {}).get("value") or {}
    data = base64.b64decode(((value.get("data") or ["", ""])[0]) or "")
    body = data[56:]
    return [str(Pubkey.from_bytes(body[i:i + 32])) for i in range(0, len(body) - len(body) % 32, 32)]


def _all_keys(rpc: Any, message: Any) -> list[str]:
    keys = [str(key) for key in message.account_keys]
    writable: list[str] = []
    readonly: list[str] = []
    for lookup in list(getattr(message, "address_table_lookups", []) or []):
        table = _lookup_addresses(rpc, str(lookup.account_key))
        for index in bytes(lookup.writable_indexes):
            writable.append(table[index])
        for index in bytes(lookup.readonly_indexes):
            readonly.append(table[index])
    return keys + writable + readonly


def _check_instructions(message: Any, keys: list[str], binding: dict[str, Any], *, source_context: dict[str, Any] | None) -> dict[str, int]:
    owner = str(binding["owner"])
    wsol = _ata(owner, WSOL_MINT)
    static = len(message.account_keys)
    unit_limit, unit_price = 0, 0
    for index, instruction in enumerate(message.instructions):
        program_index = int(instruction.program_id_index)
        if program_index >= static:
            raise _refuse("program_from_lookup_table", source_context=source_context, instruction=index)
        program = keys[program_index]
        accounts = [keys[int(position)] for position in bytes(instruction.accounts)]
        data = bytes(instruction.data)
        if program not in ALLOWED_PROGRAMS:
            raise _refuse("program_not_allowed", source_context=source_context, program=program, instruction=index)
        if program == COMPUTE_BUDGET_PROGRAM:
            if data[:1] == b"\x02" and len(data) >= 5:
                unit_limit = int.from_bytes(data[1:5], "little")
            elif data[:1] == b"\x03" and len(data) >= 9:
                unit_price = int.from_bytes(data[1:9], "little")
            elif data[:1] not in (b"\x01",):  # RequestHeapFrame is harmless; anything else is not expected
                raise _refuse("compute_budget_instruction", source_context=source_context, instruction=index)
        elif program == SYSTEM_PROGRAM:
            if not (len(data) == 12 and int.from_bytes(data[:4], "little") == 2 and accounts[:2] == [owner, wsol]):
                raise _refuse("system_instruction", source_context=source_context, instruction=index)
        elif program == TOKEN_PROGRAM:
            if data == b"\x11" and accounts[:1] == [wsol]:
                continue  # SyncNative on the wallet's own wrapped-SOL account
            if data == b"\x09" and accounts[:3] == [wsol, owner, owner]:
                continue  # CloseAccount of the wallet's own wrapped-SOL account, back to the wallet
            raise _refuse("token_instruction", source_context=source_context, instruction=index)
        elif program == ASSOCIATED_TOKEN_PROGRAM:
            if data not in (b"", b"\x00", b"\x01") or len(accounts) < 6 or accounts[0] != owner or accounts[2] != owner:
                raise _refuse("account_opening_instruction", source_context=source_context, instruction=index)
    priority = -(-unit_limit * unit_price // 1_000_000) if unit_price else 0
    if unit_price and not unit_limit:
        priority = -(-1_400_000 * unit_price // 1_000_000)
    if priority > PRIORITY_FEE_CAP_LAMPORTS:
        raise _refuse("priority_fee_above_cap", source_context=source_context, priority_lamports=priority)
    return {"unit_limit": unit_limit, "unit_price": unit_price, "priority_lamports": priority}


def _token_amount(data: bytes) -> int:
    return int.from_bytes(data[64:72], "little")


def _token_account_safe(data: bytes, owner: str) -> bool:
    from solders.pubkey import Pubkey

    if len(data) < 165:
        return False
    return str(Pubkey.from_bytes(data[32:64])) == owner and data[72:76] == b"\x00\x00\x00\x00" and data[108] == 1 and data[129:133] == b"\x00\x00\x00\x00"


def _account_reads(rpc: Any, addresses: list[str]) -> list[dict[str, Any] | None]:
    answer = rpc._call("getMultipleAccounts", [addresses, {"encoding": "base64", "commitment": "confirmed"}])
    return list((answer or {}).get("value") or [None] * len(addresses))


def _data(value: dict[str, Any] | None) -> bytes:
    if not value:
        return b""
    return base64.b64decode(((value.get("data") or ["", ""])[0]) or "")


def build_verified_message(binding: dict[str, Any], *, rpc: Any, fee_max: int, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask Jupiter for the transaction of the bound quote, then prove it before anything is signed (see the module
    docstring). Network reads only; no key exists while this runs. Returns the checked message and what it proved."""
    from solders.transaction import VersionedTransaction

    owner = str(binding["owner"])
    source, target = Coin(**binding["from"]), Coin(**binding["to"])
    body = {
        "quoteResponse": binding["raw_quote"], "userPublicKey": owner, "wrapAndUnwrapSol": True, "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {"maxLamports": PRIORITY_FEE_CAP_LAMPORTS, "priorityLevel": "high"}},
    }
    try:
        built = _http_json("POST", "/swap", body=body)
        raw = base64.b64decode(str(built["swapTransaction"]))
        transaction = VersionedTransaction.from_bytes(raw)
        last_valid = int(built.get("lastValidBlockHeight") or 0)
    except _ProviderRefused as refused:
        raise _fault("wallet_quote_unavailable", reason=f"swap_build_refused:{refused.code or refused.status}", source_context=source_context,
                     message=f"{PROVIDER} could not build this conversion now ({refused.code or refused.status}). Nothing was converted.") from None
    except Exception as exc:
        raise _fault("wallet_quote_unavailable", reason=f"swap_build_failed:{type(exc).__name__}", source_context=source_context,
                     message=f"{PROVIDER} could not build this conversion now. Nothing was converted.") from None
    message = transaction.message
    header = message.header
    keys_static = [str(key) for key in message.account_keys]
    if int(header.num_required_signatures) != 1 or not keys_static or keys_static[0] != owner:
        raise _refuse("signers_or_fee_payer", source_context=source_context)
    keys = _all_keys(rpc, message)
    compute = _check_instructions(message, keys, binding, source_context=source_context)
    # the wallet's own accounts, before and as the simulation leaves them
    watched = [owner]
    if not source.native:
        watched.append(_ata(owner, source.mint))
    if not target.native:
        watched.append(_ata(owner, target.mint))
    before = _account_reads(rpc, watched)
    unsigned = bytes(VersionedTransaction.populate(message, [transaction.signatures[0]]))
    try:
        answer = rpc._call("simulateTransaction", [base64.b64encode(unsigned).decode("ascii"), {
            "encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": False, "commitment": "confirmed",
            "accounts": {"encoding": "base64", "addresses": watched}}])
    except Exception as exc:
        raise _fault("wallet_simulation_failed", reason=f"swap_simulation_unreadable:{type(exc).__name__}", source_context=source_context,
                     message="The network could not test this conversion before signing. Nothing was converted.") from None
    value = (answer or {}).get("value") or {}
    if value.get("err") is not None:
        raise _fault("wallet_simulation_failed", reason="swap_simulation_failed", err=json.dumps(value.get("err"), default=str)[:160], source_context=source_context,
                     message="The network's test run of this conversion failed (the price may have moved). Nothing was converted. Ask me again for a fresh price.")
    after = list(value.get("accounts") or [])
    if len(after) != len(watched) or after[0] is None:
        raise _refuse("simulation_did_not_report_accounts", source_context=source_context)
    sol_before, sol_after = int((before[0] or {}).get("lamports") or 0), int(after[0].get("lamports") or 0)
    if 0 < sol_after < RENT_EXEMPT_MINIMUM_LAMPORTS:
        raise _refuse("wallet_left_below_rent", source_context=source_context)
    token_before: dict[str, int] = {}
    token_after: dict[str, int] = {}
    for address, old, new in zip(watched[1:], before[1:], after[1:]):
        old_data, new_data = _data(old), _data(new)
        if new_data and not _token_account_safe(new_data, owner):
            raise _refuse("token_account_changed_hands", source_context=source_context, account=address)
        token_before[address] = _token_amount(old_data) if old_data else 0
        token_after[address] = _token_amount(new_data) if new_data else 0
    terms = binding["terms"]
    max_in, min_out = int(terms["max_in_minor"]), int(terms["min_out_minor"])
    sol_spent = sol_before - sol_after
    if source.native:
        paid = sol_spent
        if paid > max_in + fee_max:
            raise _refuse("pays_more_than_shown", source_context=source_context, paid=paid, max=max_in + fee_max)
    else:
        account = _ata(owner, source.mint)
        paid = token_before[account] - token_after[account]
        if paid > max_in:
            raise _refuse("pays_more_than_shown", source_context=source_context, paid=paid, max=max_in)
        if not target.native and sol_spent > fee_max:
            raise _refuse("network_cost_above_ceiling", source_context=source_context, spent=sol_spent, ceiling=fee_max)
    if target.native:
        received = sol_after - sol_before
        if received < min_out - fee_max:
            raise _refuse("receives_less_than_shown", source_context=source_context, received=received, min=min_out - fee_max)
    else:
        account = _ata(owner, target.mint)
        received = token_after[account] - token_before[account]
        if received < min_out:
            raise _refuse("receives_less_than_shown", source_context=source_context, received=received, min=min_out)
    return {
        "message": SwapMessage(message), "blockhash": str(message.recent_blockhash), "last_valid": last_valid,
        "simulated": {"paid_minor": paid, "received_minor": received, "sol_change_minor": -sol_spent, **compute},
    }


# --- after landing -----------------------------------------------------------------------------------------------

def outcome_of(answer: Any, binding: dict[str, Any]) -> dict[str, Any] | None:
    """What a landed swap paid and received, from the chain's own pre/post balances of the wallet (None when the node
    served none)."""
    if not isinstance(answer, dict):
        return None
    meta = answer.get("meta") or {}
    pre, post = meta.get("preBalances"), meta.get("postBalances")
    if not isinstance(pre, list) or not isinstance(post, list) or not pre or not post:
        return None
    owner = str(binding["owner"])
    source, target = Coin(**binding["from"]), Coin(**binding["to"])

    def held(rows: Any, mint: str) -> int:
        total = 0
        for row in rows or []:
            if isinstance(row, dict) and str(row.get("owner") or "") == owner and str(row.get("mint") or "") == mint:
                total += int(str(((row.get("uiTokenAmount") or {}).get("amount")) or "0"))
        return total

    fee = int(meta.get("fee") or 0)
    sol_change = int(post[0]) - int(pre[0])
    opened = (not target.native) and not any(
        isinstance(row, dict) and str(row.get("owner") or "") == owner and str(row.get("mint") or "") == target.mint for row in meta.get("preTokenBalances") or [])
    rent = TOKEN_ACCOUNT_RENT_LAMPORTS if opened else 0
    # the network fee and an opened account's rent are what left besides the price
    paid = (-sol_change - fee - rent) if source.native else held(meta.get("preTokenBalances"), source.mint) - held(meta.get("postTokenBalances"), source.mint)
    received = (sol_change + fee + rent) if target.native else held(meta.get("postTokenBalances"), target.mint) - held(meta.get("preTokenBalances"), target.mint)
    return {"paid_minor": paid, "received_minor": received, "fee_minor": fee, "opened_output_account": opened,
            "account_rent_minor": TOKEN_ACCOUNT_RENT_LAMPORTS if opened else 0}


def prove_landed(client: Any, row: dict[str, Any], binding: dict[str, Any]) -> bool | None:
    """True when the confirmed transaction's own balances show the wallet paid no more than the maximum and received at
    least the minimum; False when they disagree; None when the node served no balances."""
    try:
        answer = client._call("getTransaction", [str(row.get("tx_id") or ""), {"commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
    except Exception:
        return None
    outcome = outcome_of(answer, binding)
    if outcome is None:
        return None
    terms = binding["terms"]
    ok = outcome["paid_minor"] <= int(terms["max_in_minor"]) and outcome["received_minor"] >= int(terms["min_out_minor"])
    if ok:
        _save_binding(str(row["proposal_id"]), {**binding, "outcome": outcome})
    return ok


def charged_fee(client: Any, tx_id: str) -> int | None:
    """The network fee plus the rent of a token account the swap opened (what left the wallet besides the price)."""
    try:
        answer = client._call("getTransaction", [str(tx_id), {"commitment": "confirmed", "maxSupportedTransactionVersion": 0}])
    except Exception:
        return None
    meta = (answer or {}).get("meta") if isinstance(answer, dict) else None
    if not isinstance(meta, dict) or meta.get("fee") is None:
        return None
    return int(meta["fee"])


def receipt_facts(proposal_id: str) -> dict[str, Any] | None:
    """The swap's own facts for its receipt and for chat: provider, coins, shown bounds, route and (once proven) what
    the chain says it paid and received."""
    binding = binding_for(proposal_id)
    if binding is None:
        return None
    block = quote_block(binding)
    facts = {k: block[k] for k in ("provider", "from_symbol", "to_symbol", "max_in_human", "min_out_human", "rate_text", "slippage_text", "price_impact_pct", "route")}
    outcome = binding.get("outcome")
    if isinstance(outcome, dict):
        facts["paid_human"] = amounts.format_minor(int(outcome["paid_minor"]), int(binding["from"]["decimals"]))
        facts["received_human"] = amounts.format_minor(int(outcome["received_minor"]), int(binding["to"]["decimals"]))
    facts["follow_up_proposal_id"] = str(binding.get("follow_up_proposal_id") or "")
    return facts


def follow_up(proposal_id: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """After a swap is proven on chain: the send the owner asked for when they asked to convert ("send 10 USDC to Maya"),
    parked as its OWN proposal for its own card and PIN. Nothing is sent here. Returns the new proposal, or None."""
    binding = binding_for(proposal_id)
    if binding is None or not isinstance(binding.get("then_send"), dict) or not isinstance(binding.get("outcome"), dict):
        return None
    if binding.get("follow_up_proposal_id"):
        found = proposals.get_proposal(str(binding["follow_up_proposal_id"]))
        return found.to_dict() if found is not None else None
    plan = binding["then_send"]
    recipient = plan.get("recipient") if isinstance(plan.get("recipient"), dict) else None
    from core.wallet import lifecycle

    target = Coin(**binding["to"])
    amount_minor = amounts.parse_human_amount(plan.get("amount"), decimals=target.decimals, symbol=target.symbol, source_context=source_context)
    proposal = proposals.propose_transaction(
        wallet_id=str(binding["wallet_id"]), destination=str((recipient or {}).get("value") or plan.get("destination") or ""), amount_minor=amount_minor,
        asset=target.symbol, origin=str(plan.get("origin") or proposals.ORIGIN_MODEL), memo=str(plan.get("memo") or ""),
        idempotency_key=f"after-swap:{proposal_id}", source_context=source_context, network=str(binding["network"]), recipient=recipient,
    )
    from core.wallet import payment_context

    payment_context.copy(proposal_id, proposal.proposal_id, note=f"sent after converting {binding['from']['symbol']} to {binding['to']['symbol']} for it")
    prepared = lifecycle.default_lifecycle(source_context=source_context).prepare(proposal.proposal_id)
    _save_binding(proposal_id, {**binding, "follow_up_proposal_id": prepared.proposal_id})
    return prepared.to_dict()


__all__ = [
    "ALLOWED_PROGRAMS", "API_BASE", "DEFAULT_SLIPPAGE_BPS", "MAX_PRICE_IMPACT_PCT", "MAX_SLIPPAGE_BPS", "PROVIDER", "SIDE_IN", "SIDE_OUT", "SWAPPABLE",
    "SwapMessage", "binding_for", "build_verified_message", "charged_fee", "coin", "fee_max_minor", "follow_up", "is_swap", "outcome_of",
    "output_account_missing", "propose_swap", "prove_landed", "quote_block", "receipt_facts", "refresh_terms",
]
