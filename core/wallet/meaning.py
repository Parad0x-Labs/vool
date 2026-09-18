"""What approving this actually does -- one plain sentence and a tier, derived from the bytes, never from a model.

A wallet never asks a human to approve bytes whose consequences it cannot explain. Users sign "airdrop claims"
and "migrations" that are token approvals, and "support repairs" that are ownership transfers, because the
wallet showed them hex. This module reads the exact thing that will be signed -- a compiled Solana message, an
EIP-712 typed-data object, EVM calldata, or the runtime's own proposal -- and says, in words a person can act
on, what happens if they approve:

* GREEN  "Exactly X goes to Y, once. No continuing permission."
* AMBER  "Up to X / until date Y / limited to scope Z." -- a bounded standing permission.
* RED    standing authority, ownership or upgrade authority, blanket access, or anything this module cannot
         completely decode. Red names the address and the capability, adds "This is standing authority, not a
         one-time payment", and its acknowledgement repeats the capability ("I understand this gives 0x1a2b…9f
         ongoing permission to spend my USDC") -- never a generic "I understand the risks".

Unknown is red. A guess would be worse than silence, so an unrecognised instruction, selector or typed-data
shape is described as exactly that, and the approval surface must not enable signing without the
acknowledgement that says so.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

TIER_GREEN = "green"
TIER_AMBER = "amber"
TIER_RED = "red"
_SEVERITY = {TIER_GREEN: 0, TIER_AMBER: 1, TIER_RED: 2}

PRINCIPLE = "A wallet never asks a human to approve bytes whose consequences it cannot explain."
STANDING = "This is standing authority, not a one-time payment."
BOUNDED_STANDING = "This is standing authority within a limit, not a one-time payment."
NO_CONTINUING = "No continuing permission."
CANNOT_ACK = "I understand this wallet cannot tell me what this does, and I am signing it anyway."

SYSTEM_PROGRAM = "11111111111111111111111111111111"
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SPL_TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
_U64_MAX = 2**64 - 1
_UNLIMITED_EVM = 2**255  # any allowance at or above this is "everything", whatever the token supply

# ERC-20 / ERC-721 / ownership / proxy selectors, keccak-256 first four bytes of the canonical signature
_SEL_TRANSFER = "a9059cbb"
_SEL_TRANSFER_FROM = "23b872dd"
_SEL_APPROVE = "095ea7b3"
_SEL_INCREASE_ALLOWANCE = "39509351"
_SEL_SET_APPROVAL_FOR_ALL = "a22cb465"
_SEL_TRANSFER_OWNERSHIP = "f2fde38b"
_SEL_UPGRADE_TO = "3659cfe6"
_SEL_UPGRADE_TO_AND_CALL = "4f1ef286"
_SEL_PERMIT = "d505accf"


@dataclass(frozen=True)
class Meaning:
    tier: str
    headline: str
    lines: tuple[str, ...] = ()
    ack_text: str = ""
    decoded: bool = True
    kind: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "headline": self.headline, "lines": list(self.lines), "ack_text": self.ack_text,
                "decoded": self.decoded, "kind": self.kind, "details": dict(self.details), "principle": PRINCIPLE}


def short_address(value: str) -> str:
    text = str(value or "")
    if not text:
        return "(no address)"
    if text.startswith("0x"):
        return text[:6] + "…" + text[-4:] if len(text) > 12 else text
    return text[:8] + "…" + text[-4:] if len(text) > 14 else text


def format_amount(minor: int, decimals: int | None, symbol: str) -> str:
    """Exact human units ("0.0012 SOL", "2.5 USDC"); minor units when the decimals are not known."""
    try:
        amount = int(minor)
    except (TypeError, ValueError):
        return f"{minor} {symbol}".strip()
    if decimals is None or decimals < 0:
        return f"{amount} minor units" + (f" of {symbol}" if symbol else "")
    if decimals == 0:
        return f"{amount} {symbol}".strip()
    quant = Decimal(amount) / (Decimal(10) ** decimals)
    text = format(quant.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {symbol}".strip()


def _when(unix_seconds: Any) -> str:
    try:
        stamp = int(str(unix_seconds), 0)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0 or stamp > 4_102_444_800 * 1000:
        return ""
    if stamp > 4_102_444_800:  # milliseconds
        stamp //= 1000
    return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        text = str(value).strip()
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return None


# --- the sentences ---------------------------------------------------------------------------------------

def exact_transfer(amount_text: str, to: str, *, for_origin: str = "", kind: str = "transfer", extra: tuple[str, ...] = ()) -> Meaning:
    target = short_address(to)
    where = f" for {for_origin}" if for_origin else ""
    return Meaning(TIER_GREEN, f"Exactly {amount_text} goes to {target}{where}, once. {NO_CONTINUING}", tuple(extra), "", True, kind, {"to": to})


def bounded_allowance(amount_text: str, spender: str, *, until: str = "", kind: str = "allowance") -> Meaning:
    target = short_address(spender)
    when = f" until {until}" if until else ""
    return Meaning(TIER_AMBER, f"Approving this lets {target} take up to {amount_text} from your wallet{when}.", (BOUNDED_STANDING,), "", True, kind, {"spender": spender})


def unlimited_allowance(symbol: str, spender: str, *, kind: str = "allowance_unlimited", scope: str = "token in this wallet") -> Meaning:
    target = short_address(spender)
    what = f"every {symbol} {scope}" if symbol else f"every {scope}"
    return Meaning(
        TIER_RED,
        f"Approving this gives {target} permission to transfer {what}, from your wallet, until the permission is revoked.",
        (STANDING,),
        f"I understand this gives {target} ongoing permission to spend my {symbol or 'tokens'}.",
        True, kind, {"spender": spender},
    )


def approval_for_all(collection: str, operator: str) -> Meaning:
    target = short_address(operator)
    name = collection or "this collection's"
    return Meaning(
        TIER_RED,
        f"Approving this gives {target} permission to move every {name} token you own, now and in the future, until revoked.",
        (STANDING,),
        f"I understand this gives {target} ongoing permission to move every {name} token I own.",
        True, "approval_for_all", {"operator": operator},
    )


def hands_control(what: str, new_authority: str) -> Meaning:
    target = short_address(new_authority)
    return Meaning(
        TIER_RED,
        f"Approving this hands control of {what} to {target}. After this, {target} decides, not you.",
        (STANDING,),
        f"I understand this hands control of {what} to {target}.",
        True, "authority_transfer", {"new_authority": new_authority},
    )


def code_upgrade(target: str) -> Meaning:
    return Meaning(
        TIER_RED,
        f"Approving this lets {short_address(target)} replace this contract's code. Whatever it can do to your assets can change after this.",
        (STANDING,),
        "I understand this lets the contract's code be replaced.",
        True, "upgrade", {"target": target},
    )


def cannot_explain(what: str, *, kind: str = "unknown") -> Meaning:
    return Meaning(
        TIER_RED,
        f"This request cannot be completely explained: {what}. Approving means signing consequences this wallet cannot show you.",
        ("Unknown is red: this wallet will not guess.",),
        CANNOT_ACK,
        False, kind, {},
    )


def _most_severe(items: list[Meaning], *, lines: bool = True) -> Meaning:
    if not items:
        return cannot_explain("the request contains no instruction")
    top = max(items, key=lambda m: _SEVERITY[m.tier])
    if len(items) == 1 or not lines:
        return top
    all_lines = tuple(f"{i + 1}. {m.headline}" for i, m in enumerate(items))
    return Meaning(top.tier, top.headline, all_lines + tuple(l for l in top.lines if l not in all_lines), top.ack_text, all(m.decoded for m in items), top.kind, dict(top.details))


# --- EVM typed data (EIP-712) ---------------------------------------------------------------------------

def describe_evm_typed_data(typed: Mapping[str, Any], *, decimals: int | None = None) -> Meaning:
    primary = str((typed or {}).get("primaryType") or "")
    domain = dict((typed or {}).get("domain") or {})
    message = dict((typed or {}).get("message") or {})
    symbol = str(domain.get("name") or "").strip()
    if primary in ("TransferWithAuthorization", "ReceiveWithAuthorization"):
        value = _int(message.get("value"))
        to = str(message.get("to") or "")
        if value is None or not to:
            return cannot_explain(f"the {primary} message has no readable value or recipient")
        until = _when(message.get("validBefore"))
        extra = (f"This authorization can be used one time only{', and expires ' + until if until else ''}.",)
        return exact_transfer(format_amount(value, decimals, symbol), to, kind="eip3009_exact", extra=extra)
    if primary == "Permit":
        spender = str(message.get("spender") or "")
        if "allowed" in message:  # DAI-style permit: a boolean, unlimited by construction
            if bool(message.get("allowed")):
                return unlimited_allowance(symbol, spender)
            return Meaning(TIER_GREEN, f"Approving this revokes {short_address(spender)}'s permission to spend your {symbol}. {NO_CONTINUING}", (), "", True, "revoke")
        value = _int(message.get("value"))
        if value is None or not spender:
            return cannot_explain("the Permit message has no readable value or spender")
        if value >= _UNLIMITED_EVM:
            return unlimited_allowance(symbol, spender)
        return bounded_allowance(format_amount(value, decimals, symbol), spender, until=_when(message.get("deadline")))
    if primary in ("PermitSingle", "PermitBatch"):
        spender = str(message.get("spender") or "")
        details = message.get("details")
        rows = details if isinstance(details, list) else [details or {}]
        parts: list[Meaning] = []
        for row in rows:
            row = dict(row or {})
            amount = _int(row.get("amount"))
            if amount is None:
                return cannot_explain("the Permit2 details have no readable amount")
            if amount >= 2**159:
                parts.append(unlimited_allowance(short_address(str(row.get("token") or "")) + " tokens", spender))
            else:
                parts.append(bounded_allowance(f"{amount} minor units of {short_address(str(row.get('token') or ''))}", spender, until=_when(row.get("expiration"))))
        return _most_severe(parts)
    if primary in ("PermitForAll", "ApprovalForAll", "SetApprovalForAll"):
        operator = str(message.get("operator") or message.get("spender") or "")
        if bool(message.get("approved", True)):
            return approval_for_all(symbol, operator)
        return Meaning(TIER_GREEN, f"Approving this revokes {short_address(operator)}'s permission over your {symbol} tokens. {NO_CONTINUING}", (), "", True, "revoke")
    return cannot_explain(f"typed data of kind {primary or '(unnamed)'} is not recognised")


# --- EVM calldata ------------------------------------------------------------------------------------------

def _words(data_hex: str) -> list[int]:
    body = data_hex[8:]
    return [int(body[i : i + 64] or "0", 16) for i in range(0, len(body) - len(body) % 64, 64)]


def _addr(word: int) -> str:
    return "0x" + (word & (2**160 - 1)).to_bytes(20, "big").hex()


def describe_evm_calldata(data: str, *, to: str, symbol: str, decimals: int | None, value_wei: int = 0) -> Meaning:
    hexdata = str(data or "").strip().lower()
    if hexdata.startswith("0x"):
        hexdata = hexdata[2:]
    if not hexdata:
        if value_wei and value_wei > 0:
            return exact_transfer(format_amount(value_wei, decimals if decimals is not None else 18, symbol or "ETH"), to, kind="native_transfer")
        return Meaning(TIER_GREEN, f"Approving this sends nothing and calls nothing: an empty transaction to {short_address(to)}. {NO_CONTINUING}", (), "", True, "empty")
    if len(hexdata) < 8:
        return cannot_explain(f"calldata to {short_address(to)} is too short to read")
    selector, words = hexdata[:8], _words(hexdata)
    if selector == _SEL_TRANSFER and len(words) >= 2:
        return exact_transfer(format_amount(words[1], decimals, symbol), _addr(words[0]), kind="erc20_transfer")
    if selector == _SEL_TRANSFER_FROM and len(words) >= 3:
        return exact_transfer(format_amount(words[2], decimals, symbol), _addr(words[1]), kind="erc20_transfer_from")
    if selector in (_SEL_APPROVE, _SEL_INCREASE_ALLOWANCE) and len(words) >= 2:
        spender, amount = _addr(words[0]), words[1]
        if amount >= _UNLIMITED_EVM:
            return unlimited_allowance(symbol, spender)
        return bounded_allowance(format_amount(amount, decimals, symbol), spender)
    if selector == _SEL_PERMIT and len(words) >= 4:
        spender, amount, deadline = _addr(words[1]), words[2], words[3]
        if amount >= _UNLIMITED_EVM:
            return unlimited_allowance(symbol, spender)
        return bounded_allowance(format_amount(amount, decimals, symbol), spender, until=_when(deadline))
    if selector == _SEL_SET_APPROVAL_FOR_ALL and len(words) >= 2:
        operator = _addr(words[0])
        if words[1]:
            return approval_for_all(symbol, operator)
        return Meaning(TIER_GREEN, f"Approving this revokes {short_address(operator)}'s permission over your {symbol or 'collection'} tokens. {NO_CONTINUING}", (), "", True, "revoke")
    if selector == _SEL_TRANSFER_OWNERSHIP and len(words) >= 1:
        return hands_control("this contract", _addr(words[0]))
    if selector in (_SEL_UPGRADE_TO, _SEL_UPGRADE_TO_AND_CALL) and len(words) >= 1:
        return code_upgrade(_addr(words[0]))
    return cannot_explain(f"call 0x{selector} to {short_address(to)} is not recognised")


# --- Solana compiled messages ----------------------------------------------------------------------------------

def _parse_message(raw: bytes):
    from solders.message import Message, MessageV0

    try:
        return Message.from_bytes(bytes(raw))
    except Exception:
        pass
    try:
        return MessageV0.from_bytes(bytes(raw))
    except Exception:
        return None


def _system_instruction(data: bytes, keys: list[str]) -> Meaning:
    if len(data) < 4:
        return cannot_explain("a System program instruction is too short to read")
    index = int.from_bytes(data[:4], "little")
    if index == 2 and len(data) >= 12 and len(keys) >= 2:
        lamports = int.from_bytes(data[4:12], "little")
        return exact_transfer(format_amount(lamports, 9, "SOL"), keys[1], kind="sol_transfer")
    if index == 0 and len(data) >= 12 and len(keys) >= 2:
        lamports = int.from_bytes(data[4:12], "little")
        return Meaning(TIER_AMBER, f"Approving this moves {format_amount(lamports, 9, 'SOL')} into a new account {short_address(keys[1])} that this transaction creates.", (BOUNDED_STANDING,), "", True, "create_account")
    if index in (1, 10) and len(keys) >= 1:
        owner = data[4:36].hex() if len(data) >= 36 else ""
        return hands_control(f"account {short_address(keys[0])}", owner or "another program")
    return cannot_explain(f"System program instruction {index} is not recognised")


def _spl_instruction(data: bytes, keys: list[str]) -> Meaning:
    if not data:
        return cannot_explain("an SPL Token instruction is empty")
    op = data[0]
    amount = int.from_bytes(data[1:9], "little") if len(data) >= 9 else None
    if op == 3 and amount is not None and len(keys) >= 2:
        return exact_transfer(format_amount(amount, None, ""), keys[1], kind="spl_transfer")
    if op == 12 and amount is not None and len(data) >= 10 and len(keys) >= 3:
        return exact_transfer(format_amount(amount, data[9], f"tokens of mint {short_address(keys[1])}"), keys[2], kind="spl_transfer_checked")
    if op in (4, 13) and amount is not None and len(keys) >= 2:
        delegate = keys[1]
        if amount == _U64_MAX:
            return unlimited_allowance("", delegate, scope="token in this account")
        decimals = data[9] if op == 13 and len(data) >= 10 else None
        return bounded_allowance(format_amount(amount, decimals, ""), delegate)
    if op == 5 and len(keys) >= 1:
        return Meaning(TIER_GREEN, f"Approving this revokes every delegate's permission over token account {short_address(keys[0])}. {NO_CONTINUING}", (), "", True, "revoke")
    if op == 6 and len(keys) >= 1:
        new_authority = str(_pubkey_from(data[3:35])) if len(data) >= 35 and data[2] == 1 else ""
        return hands_control(f"token account {short_address(keys[0])}", new_authority or "nobody (the authority is removed)")
    if op == 9 and len(keys) >= 2:
        return Meaning(TIER_AMBER, f"Approving this closes token account {short_address(keys[0])} and sends its SOL to {short_address(keys[1])}.", (BOUNDED_STANDING,), "", True, "close_account")
    if op == 8 and amount is not None and len(keys) >= 1:
        return Meaning(TIER_AMBER, f"Approving this destroys {format_amount(amount, None, '')} from token account {short_address(keys[0])}. They cannot be recovered.", (), "", True, "burn")
    return cannot_explain(f"SPL Token instruction {op} is not recognised")


def _pubkey_from(raw: bytes) -> str:
    from solders.pubkey import Pubkey

    try:
        return str(Pubkey(bytes(raw)))
    except Exception:
        return bytes(raw).hex()


def describe_solana_message(raw: bytes, *, network: str = "") -> Meaning:
    message = _parse_message(raw)
    if message is None:
        return cannot_explain("the bytes are not a Solana message this wallet can read")
    try:
        account_keys = [str(k) for k in message.account_keys]
        instructions = list(message.instructions)
    except Exception:
        return cannot_explain("the Solana message has no readable instruction table")
    parts: list[Meaning] = []
    for ix in instructions:
        try:
            program = account_keys[int(ix.program_id_index)]
            keys = [account_keys[int(i)] for i in bytes(ix.accounts)]
            data = bytes(ix.data)
        except Exception:
            parts.append(cannot_explain("an instruction points outside the account table"))
            continue
        if program == SYSTEM_PROGRAM:
            parts.append(_system_instruction(data, keys))
        elif program in (SPL_TOKEN_PROGRAM, SPL_TOKEN_2022_PROGRAM):
            parts.append(_spl_instruction(data, keys))
        else:
            parts.append(cannot_explain(f"instruction to program {short_address(program)} is not recognised"))
    return _most_severe(parts)


# --- the runtime's own proposals and signing requests ------------------------------------------------------

def _decimals_for(network: str, asset: str) -> int | None:
    try:
        from core.wallet import chains

        return int(chains.asset_for(network, asset).decimals)
    except Exception:
        return None


def describe_proposal(proposal: Any, *, binding: Mapping[str, Any] | None = None) -> Meaning:
    network, asset = str(getattr(proposal, "network", "") or ""), str(getattr(proposal, "asset", "") or "")
    amount = format_amount(int(getattr(proposal, "amount_minor", 0) or 0), _decimals_for(network, asset), asset)
    destination = str(getattr(proposal, "destination", "") or "")
    if binding:
        url = str(binding.get("url") or binding.get("resource") or "")
        origin = urlsplit(url).netloc if url else ""
        pay_to = str(binding.get("pay_to") or destination)
        extra = (f"Payment for: {url}" if url else "Payment for a paid resource", "One-time, exact amount: nobody can take more or take again.")
        return exact_transfer(amount, pay_to, for_origin=origin, kind="x402_exact_payment", extra=extra)
    if str(getattr(proposal, "origin", "") or "") == "dna_fee":
        extra = (
            "DNA service fee collection: pays fees already owed (0.1% of earlier provider payments) to the DNA treasury.",
            "A fee payment, not an inference and not a service delivery. One-time, exact amount, approved with the provider payment it rides.",
        )
        return exact_transfer(amount, destination, for_origin="the DNA treasury (service fees)", kind="dna_service_fee_collection", extra=extra)
    return exact_transfer(amount, destination, kind="transfer")


def describe_signing_request(record: Mapping[str, Any], proposal: Any, *, binding: Mapping[str, Any] | None = None) -> Meaning:
    """The bytes the user will actually sign, checked against the proposal they were shown."""
    described = describe_proposal(proposal, binding=binding)
    family = str(record.get("family") or "svm")
    if family == "evm":
        try:
            typed = json.loads(base64.b64decode(str(record.get("typed_data_b64") or "")).decode("utf-8"))
        except Exception:
            return cannot_explain("the EVM typed data of this request cannot be read")
        decimals = _decimals_for(str(getattr(proposal, "network", "")), str(getattr(proposal, "asset", "")))
        bytes_meaning = describe_evm_typed_data(typed, decimals=decimals)
        message = dict(typed.get("message") or {})
        to, value = str(message.get("to") or "").lower(), _int(message.get("value"))
        if bytes_meaning.tier == TIER_GREEN and (to != str(getattr(proposal, "destination", "")).lower() or value != int(getattr(proposal, "amount_minor", 0) or 0)):
            return _mismatch(described, bytes_meaning)
        return bytes_meaning if bytes_meaning.tier != TIER_GREEN else Meaning(described.tier, described.headline, described.lines + bytes_meaning.lines, "", True, described.kind, described.details)
    try:
        raw = base64.b64decode(str(record.get("message_b64") or ""))
    except Exception:
        return cannot_explain("the message bytes of this request cannot be read")
    bytes_meaning = describe_solana_message(raw, network=str(getattr(proposal, "network", "")))
    if bytes_meaning.tier != TIER_GREEN:
        return bytes_meaning
    to = str(bytes_meaning.details.get("to") or "")
    expected_amount = format_amount(int(getattr(proposal, "amount_minor", 0) or 0), 9, "SOL")
    if to != str(getattr(proposal, "destination", "")) or expected_amount not in bytes_meaning.headline:
        return _mismatch(described, bytes_meaning)
    return Meaning(described.tier, described.headline, (*described.lines, "The bytes to sign match this description."), "", True, described.kind, described.details)


def _mismatch(described: Meaning, actual: Meaning) -> Meaning:
    return Meaning(
        TIER_RED,
        f"The bytes you would sign do not match the proposal you were shown. They do this: {actual.headline} The proposal said: {described.headline}",
        (STANDING, "Do not sign bytes that say something different from the request."),
        "I understand the bytes do not match what was proposed, and I am signing them anyway.",
        actual.decoded, "mismatch", dict(actual.details),
    )


__all__ = [
    "PRINCIPLE",
    "SPL_TOKEN_PROGRAM",
    "SYSTEM_PROGRAM",
    "TIER_AMBER",
    "TIER_GREEN",
    "TIER_RED",
    "Meaning",
    "describe_evm_calldata",
    "describe_evm_typed_data",
    "describe_proposal",
    "describe_signing_request",
    "describe_solana_message",
    "format_amount",
    "short_address",
]
