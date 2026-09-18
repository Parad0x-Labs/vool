"""Read-only on-chain resolution of .null domain names.

A VOOL node can resolve a .null name to its on-chain record — owner, Arweave
content id, and (the part that matters for the compute market) the x402 payment
endpoint — straight from the deployed NULL registrar over the compliant
publicnode RPC. No write, no signing, no key.

The byte layout is the authoritative on-chain `NullDomain` struct
(programs/null_registrar/src/state.rs v1); offsets are kept in lockstep with the
web0-resolver browser extension's codec.js. Resolution derives the record's PDA
client-side (seeds ``[b"null-domain", sha256(pad_name64(name))]``) and reads it
with a single getAccountInfo — one cheap call the public RPCs serve, where an
unfiltered getProgramAccounts scan times out. A getProgramAccounts + memcmp scan
remains as a fallback for environments without the ed25519 curve check.
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Reuse the ONE compliant RPC path (publicnode endpoints only — never the
# Origin-403 api.mainnet-beta endpoint) and the base58 codec, so there is no
# second place an endpoint list could drift out of policy.
from core.vool_wallet import _rpc_call, b58decode, b58encode
from core.x402.client import NULL_REGISTRAR_MAINNET

# ed25519 curve check for find_program_address. nacl is a hard dependency (the
# wallet signs with it); the guard only keeps this module importable if it is
# ever stripped, in which case resolution falls back to the getProgramAccounts scan.
try:
    from nacl.bindings import crypto_core_ed25519_is_valid_point as _ed25519_is_valid_point
except Exception:  # pragma: no cover - nacl is always present in practice
    _ed25519_is_valid_point = None

_PDA_MARKER = b"ProgramDerivedAddress"

# --- authoritative NullDomain layout (314 bytes) ---------------------------
NULL_DOMAIN_SIZE = 314
NULL_DOMAIN_DISC = 0x4E  # 'N'
_OFF_NAME = 1
_OFF_OWNER = 65
_OFF_ARWEAVE_TXID = 97
_OFF_X402_ENDPOINT = 129
_OFF_PASSPORT_HASH = 257
_NAME_LEN = 64
_X402_ENDPOINT_LEN = 128
_PUBKEY_LEN = 32


@dataclass(frozen=True)
class NullDomainRecord:
    """A resolved .null domain. `x402_endpoint` is "" when unset; `arweave_txid`
    and `passport_hash` are None when the on-chain field is all-zero."""
    name: str
    owner: str  # base58 pubkey
    arweave_txid: str | None  # base64url (Arweave tx id) or None
    x402_endpoint: str  # "" when unset
    passport_hash: str | None  # hex or None


def pad_name64(name: str) -> bytes | None:
    """UTF-8 name as the on-chain 64-byte null-padded field; None if it overflows."""
    utf8 = name.encode("utf-8")
    if len(utf8) > _NAME_LEN:
        return None
    return utf8 + b"\x00" * (_NAME_LEN - len(utf8))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# --- x402 endpoint validation ----------------------------------------------
# An on-chain x402 endpoint is attacker-controllable (anyone can register a
# .null name and point its endpoint anywhere), and it flows straight into a
# payment path / the agent surface. Before it leaves this module we require a
# well-formed https:// URL (http:// only for an explicit localhost loopback,
# for local dev) with a sane charset and length. Anything else (javascript:,
# data:, file:, embedded control bytes, an over-long blob) is blanked.
_ENDPOINT_MAX_LEN = _X402_ENDPOINT_LEN  # the on-chain field is 128 bytes
# URL charset per RFC 3986 (no spaces / control chars / non-ASCII); printable
# ASCII minus space and the delimiters that have no place in a stored URL.
_ENDPOINT_CHARSET = re.compile(r"^[A-Za-z0-9._~:/?#@!$&'()*+,;=%\[\]-]+$")
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


def is_valid_x402_endpoint(endpoint: str) -> bool:
    """True only for a payment-safe endpoint URL.

    Accepts https:// with any host, and http:// only for a localhost loopback
    (local dev). Rejects every other scheme (javascript:, data:, file:, ftp:,
    bare http to a remote host), empty/over-long values, and any value carrying
    a non-URL character (space, control byte, non-ASCII).
    """
    if not isinstance(endpoint, str) or not endpoint:
        return False
    if len(endpoint) > _ENDPOINT_MAX_LEN:
        return False
    if not _ENDPOINT_CHARSET.match(endpoint):
        return False
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if scheme == "https":
        return bool(parsed.hostname)
    if scheme == "http":
        return (parsed.hostname or "").lower() in _LOCALHOST_HOSTS
    return False


def decode_null_domain(raw: bytes) -> NullDomainRecord | None:
    """Decode a raw NullDomain account blob. None if too short / wrong discriminator."""
    if len(raw) < NULL_DOMAIN_SIZE or raw[0] != NULL_DOMAIN_DISC:
        return None
    name = raw[_OFF_NAME : _OFF_NAME + _NAME_LEN].split(b"\x00", 1)[0].decode("utf-8", "replace")
    owner = b58encode(raw[_OFF_OWNER : _OFF_OWNER + _PUBKEY_LEN])
    txid_raw = raw[_OFF_ARWEAVE_TXID : _OFF_ARWEAVE_TXID + 32]
    arweave_txid = _b64url(txid_raw) if any(txid_raw) else None
    ep = raw[_OFF_X402_ENDPOINT : _OFF_X402_ENDPOINT + _X402_ENDPOINT_LEN]
    cut = ep.find(b"\x00")
    x402_endpoint = ep[: (cut if cut != -1 else len(ep))].decode("utf-8", "replace")
    # Only surface a payment-safe endpoint; blank anything malformed/unsafe so it
    # never reaches a payment path or the agent. "" already means "unset".
    if x402_endpoint and not is_valid_x402_endpoint(x402_endpoint):
        x402_endpoint = ""
    ph = raw[_OFF_PASSPORT_HASH : _OFF_PASSPORT_HASH + 32]
    passport_hash = ph.hex() if any(ph) else None
    return NullDomainRecord(
        name=name, owner=owner, arweave_txid=arweave_txid,
        x402_endpoint=x402_endpoint, passport_hash=passport_hash,
    )


def domain_filters(name: str) -> list[dict] | None:
    """getProgramAccounts memcmp filters that uniquely select a NullDomain by name.

    Filters ONLY on the discriminator (@0) and the full 64-byte padded name (@1),
    which is already unique. Deliberately no `dataSize` filter: NullDomain has a v1
    (314-byte) and a v2 (378-byte, adds a NullPay meta-address) layout, and pinning
    dataSize to one silently drops the other - matching the shipping web0-resolver
    extension, which omits dataSize for exactly this reason.
    """
    padded = pad_name64(name)
    if padded is None:
        return None
    return [
        {"memcmp": {"offset": 0, "bytes": b58encode(bytes([NULL_DOMAIN_DISC]))}},
        {"memcmp": {"offset": _OFF_NAME, "bytes": b58encode(padded)}},
    ]


def _is_on_curve(point: bytes) -> bool:
    """True if the 32 bytes are a valid ed25519 point — i.e. NOT a valid PDA."""
    if _ed25519_is_valid_point is None:
        return False
    try:
        return bool(_ed25519_is_valid_point(point))
    except Exception:
        return False


def find_program_address(seeds: list[bytes], program_id: str) -> tuple[str, int] | None:
    """Solana find_program_address: the canonical off-curve PDA + bump for `seeds`.

    Prefers solders' native implementation (byte-for-byte what web3.js / the deployed
    program use); the earlier hand-rolled version derived a WRONG address for real
    names (it pointed at an empty account while the canonical PDA held the record),
    so solders is the source of truth. Falls back to the hand-rolled walk only when
    solders is unavailable, and returns None if neither can derive.
    """
    try:
        from solders.pubkey import Pubkey

        pid = Pubkey.from_string(program_id)
        pda, bump = Pubkey.find_program_address(list(seeds), pid)
        return str(pda), bump
    except Exception:
        pass

    if _ed25519_is_valid_point is None:
        return None
    try:
        pid_bytes = b58decode(program_id)
    except Exception:
        return None
    prefix = b"".join(seeds)
    for bump in range(255, -1, -1):
        digest = hashlib.sha256(prefix + bytes([bump]) + pid_bytes + _PDA_MARKER).digest()
        if not _is_on_curve(digest):
            return b58encode(digest), bump
    return None


def derive_domain_pda(
    name: str, *, program_id: str = NULL_REGISTRAR_MAINNET
) -> str | None:
    """The NullDomain PDA for `name`: seeds [b"null-domain", sha256(pad_name64(name))]."""
    padded = pad_name64(name)
    if padded is None:
        return None
    seed_hash = hashlib.sha256(padded).digest()
    found = find_program_address([b"null-domain", seed_hash], program_id)
    return found[0] if found else None


def _resolve_via_account_info(
    pubkey: str, name: str, *, timeout: float = 5.0
) -> NullDomainRecord | None:
    """getAccountInfo on a derived PDA → decoded record, or None (empty / RPC miss)."""
    result = _rpc_call("getAccountInfo", [pubkey, {"encoding": "base64"}], timeout=timeout)
    if not isinstance(result, dict):
        return None
    value = result.get("value")
    if not value:  # account does not exist (unregistered) or RPC returned null
        return None
    try:
        data_field = value["data"]
        b64 = data_field[0] if isinstance(data_field, list) else data_field
        raw = base64.b64decode(b64)
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    rec = decode_null_domain(raw)
    if rec is not None and rec.name == name:
        return rec
    return None


def _resolve_via_program_accounts(
    name: str, *, program_id: str = NULL_REGISTRAR_MAINNET, timeout: float = 5.0
) -> NullDomainRecord | None:
    """Fallback scan (slow on public RPCs) — only used when PDA derivation is unavailable."""
    filters = domain_filters(name)
    if filters is None:
        return None
    result = _rpc_call(
        "getProgramAccounts",
        [program_id, {"encoding": "base64", "filters": filters}],
        timeout=timeout,
    )
    if not result:
        return None
    for entry in result:
        try:
            data_field = entry["account"]["data"]
            b64 = data_field[0] if isinstance(data_field, list) else data_field
            raw = base64.b64decode(b64)
        except (KeyError, TypeError, ValueError):
            continue
        rec = decode_null_domain(raw)
        if rec is not None and rec.name == name:
            return rec
    return None


def resolve_null_domain(
    name: str, *, program_id: str = NULL_REGISTRAR_MAINNET, timeout: float = 5.0
) -> NullDomainRecord | None:
    """Resolve a .null name on mainnet (read-only). None if unresolved or RPC unreachable.

    Primary path derives the NullDomain PDA and reads it with one getAccountInfo —
    cheap and public-RPC-friendly. Only when the PDA can't be derived (no ed25519
    curve check) does it fall back to the getProgramAccounts memcmp scan.
    """
    pda = derive_domain_pda(name, program_id=program_id)
    if pda is not None:
        record = _resolve_via_account_info(pda, name, timeout=timeout)
        if record is not None:
            return record
        # PDA read missed (unregistered, RPC hiccup, or a name whose live record lives
        # at a non-canonical account). Fall back to the name scan rather than giving up -
        # this is what the shipping extension does, and skipping it silently dropped
        # names that DO exist on-chain.
    return _resolve_via_program_accounts(name, program_id=program_id, timeout=timeout)


def resolve_x402_endpoint(name: str, **kwargs) -> str | None:
    """Convenience: a .null name -> its x402 payment endpoint URL, or None."""
    rec = resolve_null_domain(name, **kwargs)
    if rec is None or not rec.x402_endpoint:
        return None
    # Defensive re-validation: never hand a payment path an unsafe endpoint,
    # even if the record was built outside decode_null_domain.
    if not is_valid_x402_endpoint(rec.x402_endpoint):
        return None
    return rec.x402_endpoint


__all__ = [
    "NULL_DOMAIN_SIZE",
    "NullDomainRecord",
    "decode_null_domain",
    "derive_domain_pda",
    "domain_filters",
    "find_program_address",
    "is_valid_x402_endpoint",
    "pad_name64",
    "resolve_null_domain",
    "resolve_x402_endpoint",
]
