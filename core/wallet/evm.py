"""The EVM family adapter: EIP-712 typed data, EIP-3009 authorization signing and the
EIP-1193 external-signer request door.

Nothing here holds a key. Production signing happens in the owner's wallet through an
EIP-1193 ``eth_signTypedData_v4`` request whose params are the EXACT typed data the
operator approved; the returned signature must recover to the registered account over that
exact data before anything downstream trusts it. The EIP-712 message hash is data encoding
(keccak/abi-pads over fixed known types) checked against the OFFICIAL x402/EIP-3009 vector;
secp256k1 signing and recovery are the maintained ``eth-account`` implementation.

EVERY third-party Ethereum dependency here is optional and is resolved at USE, never at
import. ``eth_abi``/``eth_utils`` used to be module-level imports, and because
``core.wallet.lifecycle`` imports this module unconditionally, their absence made the
Solana lane, custody, spend ceilings and the mainnet-impossible guard raise
``ModuleNotFoundError`` instead of returning a typed result -- a raw 500 on the served
door and a collapsed model turn, not a refusal. Now an EVM-only operation raises the
typed ``wallet_dependency_unavailable`` fault that every wallet caller already handles,
and nothing else in the wallet notices the dependency is missing.

Permit2 is NOT improvised: the canonical deployment and the x402 exact-scheme proxy are
pinned here, and a permit2 offer is a typed ``x402_scheme_unavailable`` until the official
implementation, proxy, nonce/deadline/domain and on-chain approval state can all be proven
per network. A typed unavailable is safer than partial signing support.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from core.faults.catalog import FAULT_WALLET_DEPENDENCY_UNAVAILABLE
from core.wallet import chains, custody, external_signing
from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.evm"

#: The EVM stack this module needs, named so a refusal can say which one is missing.
EVM_DEPENDENCIES = ("eth_abi", "eth_utils", "eth_account")


def evm_dependencies_available() -> bool:
    """Whether this machine can do EVM ABI encoding and secp256k1 recovery."""
    return not missing_evm_dependencies()


def missing_evm_dependencies() -> tuple[str, ...]:
    """The EVM modules this machine does not have. Resolved live, never cached:
    an install must not need a restart to become visible."""
    import importlib.util

    missing: list[str] = []
    for name in EVM_DEPENDENCIES:
        try:
            found = importlib.util.find_spec(name) is not None
        except Exception:
            # A resolver that cannot answer has not said "available" — a broken or
            # half-installed package counts as missing, so the refusal is typed
            # rather than an ImportError surfacing at the call site.
            found = False
        if not found:
            missing.append(name)
    return tuple(missing)


def require_evm_dependencies(operation: str, *, source_context: dict[str, Any] | None = None):
    """Raise the typed refusal BEFORE an EVM operation touches anything.

    Mirrors the reader lane's ``ReaderUnavailable`` law: an absent component is a
    typed answer naming what stayed undone and what would make it work -- never an
    ImportError escaping into a caller that only knows how to handle WalletFault.
    """
    missing = missing_evm_dependencies()
    if not missing:
        return
    raise wallet_fault(
        FAULT_WALLET_DEPENDENCY_UNAVAILABLE,
        authority=AUTHORITY,
        context={
            "operation": operation,
            "reason": "missing_dependency",
            "missing": ", ".join(missing),
            "required": ", ".join(EVM_DEPENDENCIES),
        },
        source_context=source_context,
    )


def _abi_encode(types, values) -> bytes:
    from eth_abi import encode as abi_encode

    return abi_encode(types, values)


def _keccak(data: bytes) -> bytes:
    from eth_utils import keccak

    return keccak(data)

#: Canonical Permit2 (CREATE2, same address on every chain) and the x402 exact-scheme
#: Permit2 proxy. Pinned from Uniswap's deployment docs and the x402 v2 spec.
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
X402_PERMIT2_PROXY = "0x402085c248EeA27D92E8b30b2C58ed07f9E20001"

#: The two EIP-712 typehashes. They are keccak over fixed literals, so they are
#: constants -- but computing them needs the EVM stack, and this module must import
#: on a machine without it. Derived on first use and memoized; never hard-coded,
#: because a pasted digest is a magic number nothing can re-derive.
_TYPEHASH_CACHE: dict[str, bytes] = {}

_EIP712_DOMAIN_SIGNATURE = (
    b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
_TRANSFER_WITH_AUTHORIZATION_SIGNATURE = (
    b"TransferWithAuthorization(address from,address to,uint256 value,"
    b"uint256 validAfter,uint256 validBefore,bytes32 nonce)"
)


def _typehash(name: str, signature: bytes) -> bytes:
    cached = _TYPEHASH_CACHE.get(name)
    if cached is None:
        cached = _keccak(signature)
        _TYPEHASH_CACHE[name] = cached
    return cached


def eip712_domain_typehash() -> bytes:
    return _typehash("domain", _EIP712_DOMAIN_SIGNATURE)


def transfer_with_authorization_typehash() -> bytes:
    return _typehash("transfer_with_authorization", _TRANSFER_WITH_AUTHORIZATION_SIGNATURE)

TRANSFER_WITH_AUTHORIZATION_TYPES = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]

TRANSFER_METHOD_EIP3009 = "eip3009"
TRANSFER_METHOD_PERMIT2 = "permit2"


def _address_bytes(address: str) -> bytes:
    return bytes.fromhex(str(address).replace("0x", "").zfill(40))


def _uint256(value: Any) -> bytes:
    return int(value).to_bytes(32, "big")


def domain_separator(*, chain_id: int, token: str, token_name: str, token_version: str) -> str:
    """keccak(EIP712Domain typehash ++ encodeData). EIP-712 encodeData hashes string members
    directly (keccak(content)), unlike raw ABI encoding with offset heads."""
    require_evm_dependencies("eip712_domain_separator")
    return "0x" + _keccak(
        eip712_domain_typehash()
        + _keccak(str(token_name).encode("utf-8"))
        + _keccak(str(token_version).encode("utf-8"))
        + int(chain_id).to_bytes(32, "big")
        + _address_bytes(token).rjust(32, b"\x00")
    ).hex()


def transfer_with_authorization_typed_data(
    *,
    chain_id: int,
    token: str,
    token_name: str,
    token_version: str,
    from_address: str,
    to: str,
    value: Any,
    valid_after: Any,
    valid_before: Any,
    nonce: str,
) -> dict[str, Any]:
    """The complete EIP-712 payload for one EIP-3009 transferWithAuthorization."""
    message = {
        "from": str(from_address),
        "to": str(to),
        "value": str(int(value)),
        "validAfter": str(int(valid_after)),
        "validBefore": str(int(valid_before)),
        "nonce": str(nonce),
    }
    return {
        "types": {"EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ], "TransferWithAuthorization": TRANSFER_WITH_AUTHORIZATION_TYPES},
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": str(token_name), "version": str(token_version), "chainId": int(chain_id), "verifyingContract": str(token)},
        "message": message,
    }


def _authorization_struct_hash(typed: dict[str, Any]) -> bytes:
    require_evm_dependencies("eip712_struct_hash")
    message = typed.get("message") or {}
    return _keccak(
        transfer_with_authorization_typehash()
        + _abi_encode(
            ["address", "address", "uint256", "uint256", "uint256", "bytes32"],
            [
                _address_bytes(message["from"]),
                _address_bytes(message["to"]),
                int(message["value"]),
                int(message["validAfter"]),
                int(message["validBefore"]),
                bytes.fromhex(str(message["nonce"]).replace("0x", "").zfill(64)),
            ],
        )
    )


def domain_separator_of(typed: dict[str, Any]) -> str:
    domain = typed.get("domain") or {}
    return domain_separator(
        chain_id=int(domain["chainId"]), token=str(domain["verifyingContract"]),
        token_name=str(domain["name"]), token_version=str(domain["version"]),
    )


def typed_data_digest(typed: dict[str, Any]) -> str:
    """keccak(0x1901 ++ domainSeparator ++ hashStruct) — the EIP-712 digest wallets sign."""
    require_evm_dependencies("eip712_typed_data_digest")
    return "0x" + _keccak(bytes.fromhex("1901") + bytes.fromhex(domain_separator_of(typed)[2:]) + _authorization_struct_hash(typed)).hex()


def _signable(typed: dict[str, Any]):
    require_evm_dependencies("eip712_encode_typed_data")
    from eth_account.messages import encode_typed_data

    return encode_typed_data(full_message=typed)


def recover_authorization_signer(typed: dict[str, Any], signature_hex: str) -> str:
    """The account that produced this signature over EXACTLY this typed data. Malformed
    input is a typed refusal, never a False."""
    require_evm_dependencies("eip712_recover_signer")
    from eth_account import Account

    clean = str(signature_hex or "").strip()
    if not clean.startswith("0x") or len(clean) != 132:
        raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"reason": "signature_shape"})
    try:
        return Account.recover_message(_signable(typed), signature=clean)
    except Exception as exc:
        raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"reason": f"signature_unrecoverable:{type(exc).__name__}"}) from None


def verify_authorization_signature(typed: dict[str, Any], signature_hex: str, expected_account: str) -> bool:
    try:
        recovered = recover_authorization_signer(typed, signature_hex)
    except WalletFault:
        return False
    return recovered.lower() == str(expected_account or "").lower()


def sign_authorization(private_key_hex: str, typed: dict[str, Any]) -> str:
    """TEST/STAND-IN ONLY: what the owner's external wallet does in its own process. The
    runtime's production path never calls this — the signing request leaves the process."""
    require_evm_dependencies("eip712_sign_authorization")
    from eth_account import Account

    signed = Account.sign_message(_signable(typed), private_key=private_key_hex)
    return "0x" + signed.signature.hex()


def address_for_private_key(private_key_hex: str) -> str:
    """TEST/STAND-IN ONLY: derive the account a stand-in key controls, for registration."""
    require_evm_dependencies("evm_address_for_private_key")
    from eth_account import Account

    return Account.from_key(private_key_hex).address


# --- the EIP-1193 external signer door ----------------------------------------------------------------


class _ProposalRef:
    """The minimal proposal shape the signing-request store needs."""

    def __init__(self, proposal_id: str, wallet_id: str, network: str):
        self.proposal_id = proposal_id
        self.wallet_id = wallet_id
        self.network = network


def open_evm_signing_request(*, proposal_id: str, profile: custody.WalletProfile, typed_data: dict[str, Any], amount_minor: int, asset: str) -> dict[str, Any]:
    """One EIP-1193 signing request bound to the exact typed data. Bytes leave; keys never do."""
    if chains.resolve_network(profile.network).family != chains.FAMILY_EVM:
        raise wallet_fault("wallet_signing_unavailable", authority=AUTHORITY, context={"reason": "not_an_evm_wallet"})
    if profile.mode == custody.MODE_WATCH_ONLY:
        raise wallet_fault("wallet_signing_unavailable", authority=AUTHORITY, context={"reason": "watch_only"})
    message_json = json.dumps(typed_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record = external_signing.open_signing_request(
        _ProposalRef(proposal_id, profile.wallet_id, profile.network),
        public_key=profile.public_key,
        message=message_json,
        unsigned_transaction=b"",
        family="evm",
        typed_data_b64=base64.b64encode(message_json).decode("ascii"),
    )
    return record


def verify_evm_submission(record: dict[str, Any], *, signature_hex: str) -> str:
    """Verify one external answer: the signature must recover to the registered account over
    the EXACT stored typed data. Returns the digest the approval binds to."""
    import base64

    typed = None
    if record.get("typed_data_b64"):
        typed = json.loads(base64.b64decode(record["typed_data_b64"]).decode("utf-8"))
    if typed is None:
        raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"reason": "no_typed_data", "proposal_id": record.get("proposal_id", "")})
    signature = str(signature_hex or "").strip()
    if not signature.startswith("0x") or len(signature) != 132:
        raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"reason": "signature_shape", "proposal_id": record.get("proposal_id", "")})
    recovered = recover_authorization_signer(typed, signature)
    if recovered.lower() != str(record.get("public_key", "")).lower():
        raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"reason": "external_signature_mismatch", "proposal_id": record.get("proposal_id", "")})
    return typed_data_digest(typed)


# --- transfer methods: what this build can actually prove ------------------------------------------------


def require_asset_transfer_method(network: Any, method: str) -> str:
    """The typed capability matrix. ``eip3009`` is supported for registered EIP-3009 assets;
    ``permit2`` is a typed unavailable on EVERY chain until its full preconditions (official
    implementation, proxy, domain, nonce/deadline and prior on-chain approval state) are
    provable per network. Anything else was never heard of."""
    spec = chains.resolve_network(network)
    clean = str(method or "").strip().lower()
    if clean == TRANSFER_METHOD_PERMIT2:
        raise wallet_fault(
            "x402_scheme_unavailable", authority=AUTHORITY,
            context={
                "network": spec.network, "reason": "permit2_not_provable_in_this_build",
                "permit2": PERMIT2_ADDRESS, "x402_permit2_proxy": X402_PERMIT2_PROXY,
            },
        )
    if clean == TRANSFER_METHOD_EIP3009:
        if not spec.is_evm:
            raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"network": spec.network, "reason": "eip3009_is_an_evm_method"})
        return TRANSFER_METHOD_EIP3009
    raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"network": spec.network, "reason": "unknown_transfer_method", "method": clean[:64]})


def authorization_for_proposal(proposal: Any, *, account: str, nonce: str, valid_after: int, valid_before: int, token_name: str, token_version: str) -> dict[str, Any]:
    """The typed data for one approved EVM proposal, with the domain the offer declared."""
    spec = chains.resolve_network(proposal.network)
    asset = chains.asset_for(proposal.network, proposal.asset)
    if not asset.address:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"network": spec.network, "reason": "native_asset_has_no_eip3009"})
    name = token_name or asset.eip712_name
    version = token_version or asset.eip712_version
    if not name or not version:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"network": spec.network, "reason": "eip712_domain_unpinned", "asset": asset.symbol})
    return transfer_with_authorization_typed_data(
        chain_id=int(spec.chain_id), token=asset.address, token_name=name, token_version=version,
        from_address=account, to=proposal.destination, value=int(proposal.amount_minor),
        valid_after=valid_after, valid_before=valid_before, nonce=nonce,
    )


__all__ = [
    "AUTHORITY",
    "PERMIT2_ADDRESS",
    "TRANSFER_METHOD_EIP3009",
    "TRANSFER_METHOD_PERMIT2",
    "TRANSFER_WITH_AUTHORIZATION_TYPES",
    "X402_PERMIT2_PROXY",
    "address_for_private_key",
    "authorization_for_proposal",
    "domain_separator_of",
    "open_evm_signing_request",
    "recover_authorization_signer",
    "require_asset_transfer_method",
    "sign_authorization",
    "transfer_with_authorization_typed_data",
    "typed_data_digest",
    "v1_payment_header",
    "verify_authorization_signature",
    "verify_evm_submission",
]


# --- independent settlement verification ----------------------------------------------------------

#: keccak("Transfer(address,address,uint256)") — the ERC-20/SPL-equivalent transfer event
TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

#: settlement states, from the reconciliation vocabulary
SETTLED = "settled"
SETTLED_WRONG_TERMS = "settled_wrong_terms"
SUBMITTED_UNKNOWN = "submitted_unknown"
NOT_SUBMITTED = "not_submitted"


def v1_payment_header(typed_data: dict[str, Any], *, signature_hex: str, network_name: str) -> str:
    """The OFFICIAL x402 v1 ``X-PAYMENT`` header for the `exact` scheme on EVM: base64(JSON
    ``{x402Version: 1, scheme, network, payload: {signature, authorization}}``) where the
    authorization carries the exact EIP-3009 values of the signed typed data."""
    import base64 as _base64
    import json as _json

    message = typed_data.get("message") or {}
    payload = {
        "x402Version": 1,
        "scheme": "exact",
        "network": str(network_name),
        "payload": {
            "signature": str(signature_hex),
            "authorization": {
                "from": str(message.get("from") or ""),
                "to": str(message.get("to") or ""),
                "value": str(int(message.get("value") or 0)),
                "validAfter": int(message.get("validAfter") or 0),
                "validBefore": int(message.get("validBefore") or 0),
                "nonce": str(message.get("nonce") or ""),
            },
        },
    }
    return _base64.b64encode(_json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).decode("ascii")


def verify_settlement(spec: chains.ChainIdentity, tx_hash: str, *, asset_address: str, pay_to: str, amount_minor: int, payer: str = "") -> tuple[str, bool]:
    """Read the settlement from the chain itself (never from the facilitator's word): the
    transaction receipt must exist, succeed, and carry a Transfer log of the approved token
    FROM the approved payer paying the approved payee at least the approved amount — a
    stranger's old deposit to the payee is wrong terms, never a proof. Returns (state, terms_ok)."""
    import urllib.error

    from core.wallet import outbound

    clean = str(tx_hash or "").strip()
    if not clean or not chains.EVM_TX_RE.match(clean):
        return NOT_SUBMITTED, False
    try:
        answer = outbound.rpc_call(
            chains.network_rpc_url(spec.network),
            payload={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [clean]},
            spec=spec, timeout=15.0,
        )
    except WalletFault:
        return SUBMITTED_UNKNOWN, False
    except urllib.error.HTTPError:
        return SUBMITTED_UNKNOWN, False
    receipt = answer.get("result") if isinstance(answer, dict) else None
    if not isinstance(receipt, dict):
        return SUBMITTED_UNKNOWN, False  # broadcast seen, settlement not proven yet
    if str(receipt.get("status") or "") not in {"0x1", "1"}:
        return "rejected", False
    want_topic = pad_address_topic(pay_to).lower()
    want_payer_topic = pad_address_topic(payer).lower() if str(payer or "").strip() else ""
    want_asset = str(asset_address or "").lower()
    amount = int(amount_minor)
    saw_transfer = False
    for log in receipt.get("logs") or []:
        if not isinstance(log, dict):
            continue
        topics = [str(t).lower() for t in (log.get("topics") or [])]
        if not topics or topics[0] != TRANSFER_TOPIC0:
            continue
        if str(log.get("address") or "").lower() != want_asset:
            continue
        saw_transfer = True
        payer_ok = not want_payer_topic or (len(topics) > 1 and topics[1] == want_payer_topic)
        # EXACT amount: the facilitator settles exactly the authorized EIP-3009 value, so
        # a receipt moving more (or less) than authorized is an anomalous instrument, not
        # our settlement — an inflated receipt must never confirm an exact payment.
        terms_ok = payer_ok and len(topics) > 2 and topics[2] == want_topic and _log_amount(log) == amount
        if terms_ok:
            return SETTLED, True
    if saw_transfer:
        return SETTLED_WRONG_TERMS, False
    return SETTLED_WRONG_TERMS, False


def _log_amount(log: dict[str, Any]) -> int:
    data = str(log.get("data") or "0x").replace("0x", "")
    if not data:
        topics = log.get("topics") or []
        if len(topics) > 3:
            data = str(topics[3]).replace("0x", "")
    try:
        return int(data, 16)
    except ValueError:
        return 0


def pad_address_topic(address: str) -> str:
    """A 32-byte left-padded topic for an address, as events encode indexed addresses."""
    return "0x" + str(address).replace("0x", "").lower().rjust(64, "0")


__all__.extend(["NOT_SUBMITTED", "SETTLED", "SETTLED_WRONG_TERMS", "SUBMITTED_UNKNOWN", "TRANSFER_TOPIC0", "pad_address_topic", "verify_settlement"])
