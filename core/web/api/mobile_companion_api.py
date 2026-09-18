"""VOOL mobile companion API — the phone's ONLY door into the desktop.

Landed 2026-09-02 (P1, build/mobile-companion-p1-20260902) on the Device Link
foundation (core/device_link — the operator-gated bridge this module finally
wires for the COMPANION shape: chats, approvals, status; never files, never a
shell, never credentials).

WHAT THE PHONE CAN DO (the entire surface — a closed action catalog):
    chats.list / chats.prepare / chats.history / chats.send
    attachments.limits / attachments.upload
    approvals.pending / approvals.resolve
    status.snapshot / proof.get / devices.me / notifications.poll
Every catalog action is mediated by a Device Link grant; there is no raw verb,
no path parameter, and no filesystem/ signer / credential reach of any kind.
The desktop's fs/terminal verbs from core.device_link.protocol exist in the
envelope ladder but this module NEVER offers them: COMPANION_ACTIONS is the
whole offer, checked after the grant authorizes and before any handler runs.

IDENTITY (both directions, proven cryptographically):
    desktop -> phone: the pairing QR carries the desktop's Ed25519
    fingerprint; every claim response is SIGNED by that authority key and the
    phone verifies the signature against the pinned fingerprint (the claim
    also returns the public key; sha256(key) must equal the pin) before
    storing credentials.
    phone -> desktop: PROOF OF POSSESSION. The phone's Ed25519 keypair is
    generated on-device from securely random seed material; only the private
    seed ever leaves volatile memory, into the platform keystore. The pairing
    claim carries a signature by that key over the server-held challenge,
    pairing id, code, phone public key and expiry — the desktop verifies it
    BEFORE issuing any grant, so a forged or replayed public key cannot
    register. device_id = "phone:" + sha256(pubkey)[:20]. EVERY subsequent
    request additionally carries an Ed25519 signature by the registered key
    over the same canonical bytes the grant HMAC protects (device id, action,
    parameters digest, nonce, timestamp, grant identity) — a stolen grant
    secret without the phone's private key is useless, and a stolen private
    key without a live grant is equally useless. Reinstalls/lost keys cannot
    resume: a new key is a new device_id and must re-pair.

AUTHENTICATION: the exact Device Link request surface — HMAC-SHA256 over the
canonical {grant_id, verb:"vool.action", params:{action,...}, nonce,
timestamp} bytes with the grant's bearer secret, a per-process nonce replay
shield, a +/-120 s clock window, and instant revocation.

PAIRING: short-lived (default 5 min, clamp 60..900), SINGLE-USE device codes
with per-session attempt caps, per-host token buckets, constant-time compares,
and a hash-chained receipt journal (mobile_companion_receipts.jsonl) recording
every pairing outcome, revocation, approval decision and security refusal.

REVOCATION: durable (state.json), checked on EVERY authenticated request via
both the store's device status and the live GrantRegistry. A lost/revoked
device loses authority on its next packet — and restarts don't resurrect it,
because the service rehydrates the registry from the store at boot.

LOCAL-Network FIRST: the companion is served by the same always-on daemon on
the machine's own interface; binding beyond loopback stays the operator's
explicit act (VOOL_ALLOWED_HOSTS + --bind), exactly like the desktop UI.
A remote relay is DISABLED unless explicitly configured
(VOOL_MOBILE_RELAY_ENABLED=1); while disabled, any request that arrives
declaring relay provenance is refused and receipted, fail-closed.

NO SECRETS ON THE PHONE: the phone holds exactly one secret — the grant bearer
secret minted for it at pairing. API/provider/model keys, wallet keys and the
desktop signer never leave this machine; no endpoint in this module returns
them, and none could (the action catalog cannot name them).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.device_link import identity as device_identity
from core.device_link import protocol as device_protocol
from core.request_trust import is_loopback_host

# ---------------------------------------------------------------------------
# Tunables (env-overridable, all clamped)
# ---------------------------------------------------------------------------

_PAIRING_TTL_DEFAULT = 300
_PAIRING_TTL_MIN = 60
_PAIRING_TTL_MAX = 900
_MAX_DEVICES_DEFAULT = 8
_PAIRING_MAX_ATTEMPTS = 6
_DEVICE_RATE_CAP = 300          # tokens
_DEVICE_RATE_REFILL = 1.0       # tokens/second
_PAIR_HOST_RATE_CAP = 12        # failed claims per host per window
_PAIR_START_RATE_CAP = 12       # pairing starts per host per window
_RATE_WINDOW = 300.0            # seconds the buckets cover
_MOBILE_UPLOAD_CEILING = 3 * 1024 * 1024  # keeps base64 bodies under the shell's 4 MiB JSON cap


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, "") or default)))
    except (TypeError, ValueError):
        return default


def pairing_ttl_seconds() -> int:
    return _env_int("VOOL_MOBILE_PAIRING_TTL_SECONDS", _PAIRING_TTL_DEFAULT, _PAIRING_TTL_MIN, _PAIRING_TTL_MAX)


def max_devices() -> int:
    return _env_int("VOOL_MOBILE_MAX_DEVICES", _MAX_DEVICES_DEFAULT, 1, 32)


def relay_enabled() -> bool:
    """Remote relay is OFF unless explicitly configured. No relay client ships
    in this drop — this flag exists so one can never appear silently."""
    return str(os.environ.get("VOOL_MOBILE_RELAY_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def _utcnow() -> datetime:
    # Module-level clock so expiry tests can travel honestly instead of sleeping.
    return datetime.now(timezone.utc)


def _now() -> float:
    return float(time.time())


# ---------------------------------------------------------------------------
# The closed action catalog — the phone's entire capability surface
# ---------------------------------------------------------------------------

COMPANION_ACTIONS: frozenset[str] = frozenset({
    "chats.list",
    "chats.prepare",
    "chats.history",
    "chats.send",
    "attachments.limits",
    "attachments.upload",
    "approvals.pending",
    "approvals.resolve",
    "status.snapshot",
    "proof.get",
    "devices.me",
    "notifications.poll",
})

#: Anything a grant could theoretically reach that this surface refuses to
#: offer. Named for the receipt, so a future regression reads as a security
#: event instead of a silent capability leak.
_REFUSED_ACTION_FAMILIES = ("fs", "term", "script", "input", "screen", "clip", "app", "download", "signer", "credential")


# ---------------------------------------------------------------------------
# Persistent companion state (durable device registry + receipts)
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    from core.runtime_paths import active_data_dir

    # Daemon-owned data area — the phone has no filesystem reach, so this file
    # is unreachable from the companion surface by construction.
    path = active_data_dir() / "mobile_companion"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


class CompanionStore:
    """Durable device registry + pairing sessions + rate buckets + receipt chain head.

    One JSON document, atomically replaced on every mutation; the receipt
    journal is a separate append-only JSONL with a sha256 chain (each row
    commits to the previous row's hash, and the head lives in this store).
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._state_path = self._root / "state.json"
        self._receipts_path = self._root / "mobile_companion_receipts.jsonl"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"version": 1, "devices": {}, "pairings": {}, "rate": {}, "chain_head": ""}
        self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> None:
        if self._state_path.exists():
            try:
                raw = json.loads(self._state_path.read_text())
            except (json.JSONDecodeError, OSError):
                raw = None
            if isinstance(raw, dict) and raw.get("version") == 1:
                self._data = raw
        else:
            self._save()

    def _save(self) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._state_path)

    # -- devices ----------------------------------------------------------

    def devices(self) -> dict[str, dict[str, Any]]:
        return dict(self._data["devices"])

    def device(self, device_id: str) -> Optional[dict[str, Any]]:
        rec = self._data["devices"].get(str(device_id or ""))
        return dict(rec) if rec else None

    def active_device_count(self) -> int:
        return sum(1 for rec in self._data["devices"].values() if rec.get("status") == "active")

    def put_device(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._data["devices"][str(record["device_id"])] = record
            self._save()

    def mark_device(self, device_id: str, **fields: Any) -> Optional[dict[str, Any]]:
        with self._lock:
            rec = self._data["devices"].get(str(device_id or ""))
            if not rec:
                return None
            rec.update(fields)
            self._save()
            return dict(rec)

    # -- pairing sessions ---------------------------------------------------

    def pairings(self) -> dict[str, dict[str, Any]]:
        return dict(self._data["pairings"])

    def pairing(self, pairing_id: str) -> Optional[dict[str, Any]]:
        rec = self._data["pairings"].get(str(pairing_id or ""))
        return dict(rec) if rec else None

    def put_pairing(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._data["pairings"][str(record["pairing_id"])] = record
            self._save()

    def update_pairing(self, pairing_id: str, **fields: Any) -> Optional[dict[str, Any]]:
        with self._lock:
            rec = self._data["pairings"].get(str(pairing_id or ""))
            if not rec:
                return None
            rec.update(fields)
            self._save()
            return dict(rec)

    # -- rate buckets -------------------------------------------------------

    def allow_rate(self, key: str, *, cap: int, window: float = _RATE_WINDOW, now: Optional[float] = None) -> bool:
        """Fixed-window counter persisted in the store (survives restarts)."""
        ts = now if now is not None else _now()
        with self._lock:
            buckets = self._data.setdefault("rate", {})
            start, count = buckets.get(key, (ts, 0))
            if ts - float(start) >= window:
                start, count = ts, 0
            if int(count) >= int(cap):
                buckets[key] = (start, count)
                self._save()
                return False
            buckets[key] = (start, int(count) + 1)
            self._save()
            return True

    # -- receipts (append-only, hash-chained) --------------------------------

    def append_receipt(self, kind: str, *, device_id: str = "", detail: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        with self._lock:
            seq = int(self._data.get("receipt_seq") or 0) + 1
            payload = {
                "seq": seq,
                "receipt_id": uuid.uuid4().hex,
                "at": _utcnow().isoformat(),
                "kind": str(kind),
                "device_id": str(device_id or ""),
                "detail": detail or {},
            }
            prev = str(self._data.get("chain_head") or "")
            row = dict(payload)
            row["prev"] = prev
            row["hash"] = hashlib.sha256(_canonical_bytes(row)).hexdigest()
            with self._receipts_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            self._data["receipt_seq"] = seq
            self._data["chain_head"] = row["hash"]
            self._save()
            return row

    def receipts(self, *, after_seq: int = 0, limit: int = 200) -> tuple[list[dict[str, Any]], str, Optional[bool]]:
        rows: list[dict[str, Any]] = []
        if self._receipts_path.exists():
            try:
                for line in self._receipts_path.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if int(row.get("seq") or 0) > int(after_seq):
                        rows.append(row)
            except (json.JSONDecodeError, OSError):
                return rows, str(self._data.get("chain_head") or ""), False
        rows.sort(key=lambda r: int(r.get("seq") or 0))
        return rows[-int(limit):], str(self._data.get("chain_head") or ""), self.chain_verified()

    def chain_verified(self) -> Optional[bool]:
        """Recompute the full chain. True/False; None when no journal exists yet."""
        if not self._receipts_path.exists():
            return None
        prev = ""
        try:
            for line in self._receipts_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if str(row.get("prev") or "") != prev:
                    return False
                digest = hashlib.sha256(_canonical_bytes({k: row[k] for k in row if k != "hash"})).hexdigest()
                if not hmac.compare_digest(digest, str(row.get("hash") or "")):
                    return False
                prev = str(row.get("hash") or "")
        except (json.JSONDecodeError, OSError, KeyError):
            return False
        return hmac.compare_digest(prev, str(self._data.get("chain_head") or ""))


# ---------------------------------------------------------------------------
# The service singleton (one per daemon process)
# ---------------------------------------------------------------------------

_SERVICE: Optional[MobileCompanionService] = None
_SERVICE_LOCK = threading.Lock()


class MobileCompanionService:
    """Owns the authority key, the grant registry and the durable store."""

    def __init__(self, *, store: Optional[CompanionStore] = None, signing_key=None, clock: Optional[Callable[[], datetime]] = None) -> None:
        self._store = store or CompanionStore(_state_dir())
        self._authority_key = signing_key  # injectable for tests; lazily loaded otherwise
        self._clock = clock or _utcnow
        self.registry = device_protocol.GrantRegistry()
        self.dispatch_lock = threading.Lock()
        # Rehydrate: grants and revocations survive a daemon restart because
        # the store — not the registry — is the durable authority.
        for rec in self._store.devices().values():
            grant = rec.get("grant") or {}
            if grant.get("grant_id"):
                self.registry.register(grant)
                if rec.get("status") != "active":
                    self.registry.revoke(str(grant["grant_id"]), reason=str(rec.get("revoke_reason") or "device not active"))

    # -- accessors ----------------------------------------------------------

    @property
    def store(self) -> CompanionStore:
        return self._store

    def authority_key(self):
        if self._authority_key is None:
            # Explicit operator-chosen path, never <runtime_home>/data/keys
            # (that sibling belongs to the canonical signer — see identity.py).
            self._authority_key = device_identity.load_or_create(
                _state_dir() / "device_authority_key.json"
            )
        return self._authority_key

    def desktop_fingerprint(self) -> str:
        return device_identity.fingerprint(self.authority_key())

    def desktop_short_fingerprint(self) -> str:
        return device_identity.short_fingerprint(self.authority_key())

    def utcnow(self) -> datetime:
        return (self._clock or _utcnow)()

    def set_clock(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock


def mobile_companion_service() -> MobileCompanionService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = MobileCompanionService()
        return _SERVICE


def reset_mobile_companion_service() -> None:
    """Test/daemon-shutdown hook: drop the singleton so a fresh VOOL_HOME binds a fresh store."""
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = None


# ---------------------------------------------------------------------------
# Response helpers (lazy import — .service imports this module lazily too)
# ---------------------------------------------------------------------------

def _json(status: int, payload: dict[str, Any]):
    from core.web.api.service import json_response

    return json_response(status, payload)


def _err(status: int, code: str, message: str = ""):
    return _json(status, {"ok": False, "error": message or code, "code": code})


def _relay_declared(headers: dict[str, Any], body: dict[str, Any]) -> bool:
    flagged = str((headers or {}).get("x-vool-mobile-relay") or (headers or {}).get("X-VOOL-Mobile-Relay") or "").strip()
    if flagged:
        return True
    return bool((body or {}).get("relay"))


def _check_relay(headers: dict[str, Any], body: dict[str, Any], store: CompanionStore):
    """Fail-closed relay gate: disabled (the default) refuses declared-relay traffic."""
    if _relay_declared(headers, body) and not relay_enabled():
        store.append_receipt("security.relay_refused", detail={"relay": "declared_while_disabled"})
        return _err(403, "relay_disabled", "remote relay is not enabled on this desktop")
    return None


def _verify_ed25519(public_key_hex: str, payload: bytes, signature_hex: str) -> bool:
    """Constant-failure Ed25519 verification over raw bytes; never raises."""
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey

        VerifyKey(bytes.fromhex(str(public_key_hex or ""))).verify(
            payload, bytes.fromhex(str(signature_hex or "")))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def _public_device(rec: dict[str, Any]) -> dict[str, Any]:
    """Never leak the grant (its bearer secret least of all) to a listing."""
    return {
        "device_id": rec.get("device_id"),
        "name": rec.get("name"),
        "platform": rec.get("platform"),
        "public_key": rec.get("public_key"),
        "status": rec.get("status"),
        "paired_at": rec.get("paired_at"),
        "last_seen_at": rec.get("last_seen_at"),
        "revoked_at": rec.get("revoked_at"),
        "revoke_reason": rec.get("revoke_reason"),
        "grant_id": (rec.get("grant") or {}).get("grant_id"),
        "envelope": (rec.get("grant") or {}).get("envelope"),
    }


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

def _new_pairing_code() -> str:
    # 8 chars from an unambiguous alphabet — typeable, scannable, single-use.
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def start_pairing(body: dict[str, Any], headers: dict[str, Any], service: MobileCompanionService, client_host: str):
    from urllib.parse import urlsplit

    store = service.store
    if not is_loopback_host(client_host):
        return _err(403, "owner_local_required", "pairing is started from the desktop")
    refusal = _check_relay(headers, body, store)
    if refusal is not None:
        return refusal
    if not store.allow_rate(f"pair_start:{client_host}", cap=_PAIR_START_RATE_CAP):
        store.append_receipt("pairing.rate_limited", detail={"client_host": client_host, "phase": "start"})
        return _err(429, "pairing_rate_limited", "too many pairing attempts; wait and retry")

    ttl = pairing_ttl_seconds()
    pairing_id = "pair_" + secrets.token_hex(8)
    code = _new_pairing_code()
    expires_at_epoch = _now() + ttl
    # The challenge forces the phone to prove possession of the Ed25519
    # private key it is registering: the claim signature must cover
    # server-held values (challenge, code, expiry) it can only obtain by
    # being shown this pairing — and can only sign with the key it owns.
    challenge = secrets.token_hex(32)
    record = {
        "pairing_id": pairing_id,
        "code_hash": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        # The plaintext code is needed to reconstruct the signed claim
        # payload; owner-only file (0600), short-lived by design.
        "code": code,
        "challenge": challenge,
        "created_at": service.utcnow().isoformat(),
        "expires_at_epoch": expires_at_epoch,
        "ttl_seconds": ttl,
        "attempts": 0,
        "status": "waiting",
        "client_hint": str(body.get("device_hint") or "")[:64],
    }
    store.put_pairing(record)
    store.append_receipt("pairing.started", detail={"pairing_id": pairing_id, "ttl_seconds": ttl})

    advertise_host = str(os.environ.get("VOOL_MOBILE_ADVERTISE_HOST") or "").strip()
    if not advertise_host:
        # Advertise what the desktop itself is addressed by — host AND port,
        # so the QR the phone scans dials the same surface.
        host_header = str((headers or {}).get("host") or (headers or {}).get("Host") or "").strip()
        advertise_host = urlsplit("//" + host_header).netloc if host_header else ""
    qr_uri = (
        "vool-pair://" + advertise_host
        + "?fp=" + service.desktop_fingerprint()
        + "&pid=" + pairing_id
        + "&code=" + code
        + "&exp=" + str(int(expires_at_epoch))
    ) if advertise_host else ""
    return _json(200, {
        "ok": True,
        "pairing_id": pairing_id,
        "code": code,
        "expires_in_seconds": ttl,
        "expires_at_epoch": int(expires_at_epoch),
        "challenge": challenge,
        "desktop_fingerprint": service.desktop_fingerprint(),
        "desktop_short_fingerprint": service.desktop_short_fingerprint(),
        "qr_uri": qr_uri,
        "pairing_page": "/api/mobile/pairing/page?pairing_id=" + pairing_id,
        "manual_entry": {"pairing_id": pairing_id, "code": code},
    })


def claim_pairing(body: dict[str, Any], headers: dict[str, Any], service: MobileCompanionService, client_host: str):
    store = service.store
    refusal = _check_relay(headers, body, store)
    if refusal is not None:
        return refusal
    pairing_id = str(body.get("pairing_id") or "").strip()
    code = str(body.get("code") or "").strip()
    device_name = str(body.get("device_name") or "").strip()[:64]
    platform = str(body.get("platform") or "").strip()[:32]
    public_key = str(body.get("device_public_key") or "").strip().lower()
    proof_signature = str(((body.get("device_proof") or {}) if isinstance(body.get("device_proof"), dict) else {}).get("signature") or "").strip().lower()

    if not pairing_id or not code or not device_name or not public_key or len(public_key) != 64 or not proof_signature:
        return _err(400, "invalid_request",
                    "pairing_id, code, device_name, a 64-hex device_public_key and "
                    "device_proof.signature are required")
    try:
        bytes.fromhex(public_key)
        bytes.fromhex(proof_signature)
    except ValueError:
        return _err(400, "invalid_request", "device_public_key and device_proof.signature must be hex")

    session = store.pairing(pairing_id)
    if session is None:
        store.append_receipt("pairing.failed", detail={"pairing_id": pairing_id, "reason": "unknown_pairing", "client_host": client_host})
        return _err(404, "pairing_unknown", "no such pairing session")
    if session.get("status") == "claimed":
        store.append_receipt("pairing.failed", detail={"pairing_id": pairing_id, "reason": "code_replayed", "client_host": client_host})
        return _err(409, "pairing_used", "this pairing code was already used")
    if float(session.get("expires_at_epoch") or 0) <= _now():
        store.update_pairing(pairing_id, status="expired")
        store.append_receipt("pairing.failed", detail={"pairing_id": pairing_id, "reason": "expired", "client_host": client_host})
        return _err(410, "pairing_expired", "pairing code expired; start a new pairing on the desktop")
    if int(session.get("attempts") or 0) >= _PAIRING_MAX_ATTEMPTS:
        store.update_pairing(pairing_id, status="locked")
        store.append_receipt("pairing.failed", detail={"pairing_id": pairing_id, "reason": "attempt_cap", "client_host": client_host})
        return _err(429, "pairing_locked", "too many wrong codes; start a new pairing")
    if not store.allow_rate(f"pair_claim_host:{client_host}", cap=_PAIR_HOST_RATE_CAP):
        store.append_receipt("pairing.failed", detail={"pairing_id": pairing_id, "reason": "host_rate", "client_host": client_host})
        return _err(429, "pairing_rate_limited", "too many claims from this address; wait and retry")

    if not hmac.compare_digest(hashlib.sha256(code.encode("utf-8")).hexdigest(), str(session.get("code_hash") or "")):
        store.update_pairing(pairing_id, attempts=int(session.get("attempts") or 0) + 1)
        store.append_receipt("pairing.failed", detail={"pairing_id": pairing_id, "reason": "bad_code", "client_host": client_host})
        return _err(401, "bad_code", "pairing code did not match")

    if store.active_device_count() >= max_devices():
        store.append_receipt("pairing.failed", detail={"pairing_id": pairing_id, "reason": "device_limit"})
        return _err(409, "device_limit", "device limit reached; revoke a device first")

    # ---- PROOF OF POSSESSION ---------------------------------------------
    # The claim signature is verified against the public key the claim
    # REGISTERS, over a payload of server-held values (challenge, code,
    # expiry, pairing id). A claimant without the matching private key —
    # a forged or replayed public key — cannot produce it, and every input
    # to the payload comes from this session, never from the request body.
    proof_payload = {
        "kind": "vool.mobile.pairing.proof.v1",
        "pairing_id": pairing_id,
        "code": str(session.get("code") or ""),
        "challenge": str(session.get("challenge") or ""),
        "device_public_key": public_key,
        "expires_at": int(float(session.get("expires_at_epoch") or 0)),
    }
    if not _verify_ed25519(public_key, _canonical_bytes(proof_payload), proof_signature):
        store.update_pairing(pairing_id, attempts=int(session.get("attempts") or 0) + 1)
        store.append_receipt("pairing.failed", detail={
            "pairing_id": pairing_id, "reason": "proof_of_possession", "client_host": client_host})
        return _err(401, "proof_of_possession_failed",
                    "device signature over the pairing challenge did not verify")

    device_id = "phone:" + hashlib.sha256(bytes.fromhex(public_key)).hexdigest()[:20]
    grant = device_protocol.issue_grant(
        signing_key=service.authority_key(),
        device_name=device_name,
        envelope=device_protocol.ENVELOPE_VIEW_ONLY,
        scope_kind=device_protocol.SCOPE_UNTIL_REVOKED,
        granted_to_peer_id=device_id,
        max_uses=10**9,
    )
    service.registry.register(grant)

    proof_payload = {
        "kind": "vool.mobile.pairing.claim.v1",
        "pairing_id": pairing_id,
        "device_id": device_id,
        "grant_id": grant["grant_id"],
        "desktop_fingerprint": service.desktop_fingerprint(),
    }
    signature = device_protocol.sign_with_authority_key(
        service.authority_key(), _canonical_bytes(proof_payload))

    record = {
        "device_id": device_id,
        "name": device_name,
        "platform": platform,
        "public_key": public_key,
        "status": "active",
        "paired_at": service.utcnow().isoformat(),
        "last_seen_at": service.utcnow().isoformat(),
        "pairing_id": pairing_id,
        "grant": grant,
    }
    store.put_device(record)
    store.update_pairing(pairing_id, status="claimed", device_id=device_id)
    store.append_receipt("pairing.claimed", device_id=device_id, detail={
        "pairing_id": pairing_id, "name": device_name, "platform": platform, "client_host": client_host})

    # The bearer secret crosses the wire exactly once, right here.
    return _json(200, {
        "ok": True,
        "device_id": device_id,
        "grant_id": grant["grant_id"],
        "grant_secret": grant["grant_secret"],
        "envelope": grant["envelope"],
        "scope_kind": grant["scope_kind"],
        "desktop_fingerprint": service.desktop_fingerprint(),
        "desktop_short_fingerprint": service.desktop_short_fingerprint(),
        "desktop_public_key": device_identity.public_hex(service.authority_key()),
        "desktop_proof": {"payload": proof_payload, "signature": signature},
    })


# ---------------------------------------------------------------------------
# Authenticated companion dispatch
# ---------------------------------------------------------------------------

def _companion_error_for(reason: str) -> tuple[int, str]:
    lowered = reason.lower()
    # The clock-window refusal's text ends "(replay shield)" — classify by the
    # specific phrase before the generic replay family.
    if "timestamp" in lowered:
        return 401, "timestamp_window"
    if "replay" in lowered:
        return 409, "replay_detected"
    if "revoked" in lowered:
        return 401, "device_revoked"
    if "hmac" in lowered or "signature" in lowered:
        return 401, "signature_invalid"
    if "expired" in lowered:
        return 401, "grant_expired"
    if "budget" in lowered:
        return 401, "grant_exhausted"
    return 401, "unauthorized"


def dispatch_companion(
    body: dict[str, Any],
    headers: dict[str, Any],
    *,
    runtime,
    model_name: str,
    client_host: str,
    workspace_root_provider,
    service: MobileCompanionService,
):
    store = service.store
    refusal = _check_relay(headers, body, store)
    if refusal is not None:
        return refusal

    device_id = str(body.get("device_id") or "").strip()
    grant_id = str(body.get("grant_id") or "").strip()
    action = str(body.get("action") or "").strip()
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    nonce = str(body.get("nonce") or "").strip()
    timestamp = str(body.get("timestamp") or "").strip()
    signature = str(body.get("signature") or "").strip()
    device_signature = str(body.get("device_signature") or "").strip().lower()
    if not device_id or not grant_id or not action or not nonce or not timestamp or not signature or not device_signature:
        return _err(400, "invalid_request",
                    "device_id, grant_id, action, nonce, timestamp, signature and "
                    "device_signature are required")

    device = store.device(device_id)
    if device is None:
        return _err(401, "device_unknown", "no such device")
    if device.get("status") != "active":
        store.append_receipt("security.revoked_device_attempt", device_id=device_id,
                             detail={"action": action, "status": device.get("status")})
        return _err(401, "device_revoked", "this device's authority was revoked on the desktop")

    grant = dict(device.get("grant") or {})
    if str(grant.get("grant_id") or "") != grant_id:
        return _err(401, "signature_invalid", "grant does not belong to this device")

    if not store.allow_rate(f"device:{device_id}", cap=_DEVICE_RATE_CAP, window=_RATE_WINDOW):
        store.append_receipt("security.device_rate_limited", device_id=device_id, detail={"action": action})
        return _err(429, "device_rate_limited", "too many requests; slow down")

    # The exact Device Link decision pipeline: revocation, self-signature,
    # expiry, budget, +/-120 s window, nonce replay shield, request HMAC,
    # envelope coverage. verb is pinned to vool.action (VIEW_ONLY's operator
    # verb); the companion catalog below is the narrower product truth.
    with service.dispatch_lock:
        ok, reason, _detail = service.registry.authorize(
            grant=grant,
            verb="vool.action",
            params={"action": action, **params},
            nonce=nonce,
            timestamp=timestamp,
            signature=signature,
        )
    if not ok:
        status, code = _companion_error_for(reason)
        if code == "replay_detected":
            store.append_receipt("security.replay_detected", device_id=device_id, detail={"action": action, "nonce": nonce[:16]})
        return _err(status, code, reason)

    # ---- DEVICE SIGNATURE --------------------------------------------------
    # Second factor on EVERY request: an Ed25519 signature by the phone's
    # REGISTERED key (from the device record — a public key in the request
    # body is never trusted) over the same canonical bytes the grant HMAC
    # protects: device id, action, parameters digest, nonce, timestamp and
    # grant identity. A stolen grant secret without the phone's private key
    # stops here; a stolen private key without a live grant stops at the
    # HMAC/revocation checks above.
    request_proof = {
        "kind": "vool.mobile.request.v1",
        "device_id": device_id,
        "action": action,
        "params_sha256": hashlib.sha256(_canonical_bytes(params)).hexdigest(),
        "nonce": nonce,
        "timestamp": timestamp,
        "grant_id": grant_id,
    }
    if not _verify_ed25519(str(device.get("public_key") or ""), _canonical_bytes(request_proof), device_signature):
        store.append_receipt("security.device_signature_invalid", device_id=device_id, detail={"action": action})
        return _err(401, "device_signature_invalid",
                    "request was not signed by this device's registered key")

    if action not in COMPANION_ACTIONS:
        family = action.split(".", 1)[0]
        if family in _REFUSED_ACTION_FAMILIES:
            store.append_receipt("security.action_refused", device_id=device_id,
                                 detail={"action": action, "reason": "family_not_offered_to_companion"})
        return _err(403, "action_not_offered", f"the mobile companion does not offer {action!r}")

    store.mark_device(device_id, last_seen_at=service.utcnow().isoformat())
    handler = _ACTION_HANDLERS.get(action)
    if handler is None:
        return _err(403, "action_not_offered", f"the mobile companion does not offer {action!r}")
    try:
        status, result = handler(
            params,
            runtime=runtime,
            model_name=model_name,
            client_host=client_host,
            workspace_root_provider=workspace_root_provider,
            service=service,
            device=device,
        )
    except Exception as exc:
        from core.error_surface import safe_error_text

        return _err(500, "companion_handler_error", safe_error_text(exc))
    return _json(status, {"ok": status < 400, "action": action, "device_id": device_id, "result": result})


# ---------------------------------------------------------------------------
# Action handlers — each returns (status, result_dict)
# ---------------------------------------------------------------------------

def _action_devices_me(params: dict[str, Any], *, service: MobileCompanionService, device: dict[str, Any], **_kw):
    rec = service.store.device(str(device.get("device_id") or ""))
    if rec is None:
        return 401, {"error": "device_no_longer_registered"}
    return 200, {
        "device_id": rec.get("device_id"),
        "name": rec.get("name"),
        "platform": rec.get("platform"),
        "status": rec.get("status"),
        "paired_at": rec.get("paired_at"),
        "last_seen_at": rec.get("last_seen_at"),
        "desktop_fingerprint": service.desktop_fingerprint(),
        "desktop_short_fingerprint": service.desktop_short_fingerprint(),
    }


def _action_status_snapshot(params: dict[str, Any], *, runtime, model_name: str, service: MobileCompanionService, **_kw):
    cloud_state: dict[str, Any] = {}
    try:
        from core.cloud_connection_state import connection_status

        cloud_state = dict(connection_status() or {})
    except Exception:
        cloud_state = {"state": "unavailable"}
    escalation: dict[str, Any] = {}
    try:
        from core import cloud_escalation_policy as _cep

        policy = _cep.load_policy()
        escalation = {"model": getattr(policy, "model", "") or "", "mode": getattr(policy, "mode", "") or ""}
    except Exception:
        escalation = {"model": "", "mode": ""}
    stamp = dict(getattr(runtime, "runtime_version_stamp", None) or {})
    return 200, {
        "desktop": {
            "display_name": getattr(runtime, "display_name", "VOOL"),
            "runtime_started_at": getattr(runtime, "runtime_started_at", None),
            "version": stamp,
            "fingerprint": service.desktop_fingerprint(),
            "short_fingerprint": service.desktop_short_fingerprint(),
        },
        "model": {
            "current": model_name,
            "cloud_model": escalation.get("model") or cloud_state.get("model") or "",
            "cloud_state": cloud_state.get("state"),
            "cloud_detail": cloud_state.get("detail") or "",
            "checked_at": cloud_state.get("checked_at") or "",
        },
        "relay": {"enabled": relay_enabled(), "mode": "remote-relay" if relay_enabled() else "local-network"},
        "proof_chip_note": "per-turn proof via the proof.get action with the turn's chat_id and request_id",
    }


def _action_chats_list(params: dict[str, Any], **_kw):
    from core.memory.entries import list_conversation_sessions

    rows = list_conversation_sessions(limit=100)
    return 200, {
        "chats": [
            {
                "chat_id": row.get("session_id"),
                "title": row.get("title"),
                "updated_at": row.get("updated_at"),
                "turn_count": row.get("turn_count"),
                "archived": bool(row.get("archived")),
            }
            for row in rows
        ],
        "total": len(rows),
    }


def _read_session_id(handle: str) -> str:
    """Session id for READ actions: a canonical id passes verbatim (the same
    unguessable-id bar the desktop's own read routes apply); a phone handle
    derives to its canonical session."""
    cleaned = str(handle or "").strip()
    if cleaned.startswith("openclaw:") and len(cleaned) == len("openclaw:") + 20:
        return cleaned
    return _effective_session_id(cleaned)


def _effective_session_id(handle: str) -> str:
    """Map a phone-held chat handle onto the canonical session id.

    The /api/chat pipeline resumes canonical desktop ids verbatim ONLY for
    owner-local clients (anti-spoofing: a remote peer must not graft onto an
    existing desktop namespace by guessing its id). Non-owner handles are
    sha256-derived — DETERMINISTICALLY, so the same phone handle always maps
    to the same canonical session: that is the phone's conversation
    continuity, and it never needs to present a desktop id at all.
    """
    from core.web.api.runtime import stable_openclaw_session_id

    return stable_openclaw_session_id(
        body={"session_id": str(handle or "").strip()},
        history=[],
        headers={},
        allow_canonical_resume=False,
    )


def _action_chats_prepare(params: dict[str, Any], **_kw):
    # A PHONE-side handle (never a desktop canonical id): continuity comes
    # from the deterministic derivation in _effective_session_id.
    handle = "mob-" + secrets.token_hex(10)
    return 200, {"chat_id": handle, "canonical_chat_id": _effective_session_id(handle)}


def _action_chats_history(params: dict[str, Any], *, runtime, model_name: str, **_kw):
    from core.web.api.service import dispatch_get

    chat_id = str(params.get("chat_id") or "").strip()
    if not chat_id:
        return 400, {"error": "chat_id is required"}
    limit = _clamp_int(params.get("limit"), default=200, lo=1, hi=500)
    # The REAL desktop handler — same truth the desktop UI reads, no parallel
    # reconstruction that could drift from it. The phone's handle resolves to
    # the same canonical session every time (see _effective_session_id).
    response = dispatch_get(
        path="/api/chat/history",
        query={"session": [_read_session_id(chat_id)], "limit": [str(limit)]},
        runtime=runtime,
        model_name=model_name,
    )
    try:
        payload = json.loads(response.body or b"{}")
    except (json.JSONDecodeError, TypeError):
        return 500, {"error": "history_unreadable"}
    if response.status != 200:
        return response.status, payload
    messages = []
    for row in payload.get("messages") or []:
        item = {"role": row.get("role"), "content": row.get("content")}
        if row.get("attachments"):
            item["attachments"] = row.get("attachments")
        if row.get("ts"):
            item["ts"] = row.get("ts")
        if isinstance(row.get("a7"), dict):
            item["answer_state"] = row.get("a7", {}).get("status")
        if row.get("request_id"):
            item["request_id"] = row.get("request_id")
        messages.append(item)
    return 200, {"chat_id": chat_id, "messages": messages}


def _action_chats_send(
    params: dict[str, Any],
    *,
    runtime,
    model_name: str,
    client_host: str,
    workspace_root_provider,
    **_kw,
):
    from core.web.api.service import dispatch_post

    message = str(params.get("message") or "").strip()
    if not message:
        return 400, {"error": "message is required"}
    if len(message) > 32768:
        return 400, {"error": "message too long (32768 char ceiling)"}
    chat_id = str(params.get("chat_id") or "").strip()
    attachments = [str(a) for a in params.get("attachments") or [] if str(a or "").strip()][:8]
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": message}],
        "stream": False,
        "source_context": {"surface": "mobile_companion"},
        "platform": "mobile_companion",
    }
    if chat_id:
        body["session_id"] = chat_id
    if attachments:
        body["attachments"] = attachments
    # The REAL /api/chat pipeline — session derivation, mode resolution, the
    # turn gate, finalization and the receipt envelope all belong to it.
    response = dispatch_post(
        path="/api/chat",
        body=body,
        headers={"content-type": "application/json"},
        runtime=runtime,
        model_name=model_name,
        workspace_root_provider=workspace_root_provider,
        client_host=client_host,
        request_id="mob-" + uuid.uuid4().hex,
    )
    try:
        payload = json.loads(response.body or b"{}")
    except (json.JSONDecodeError, TypeError):
        return 500, {"error": "turn_unreadable"}
    if response.status != 200:
        return response.status, payload
    envelope_msg = payload.get("message") or {}
    commit = payload.get("vool_response_commit") or {}
    result: dict[str, Any] = {
        # Phone-facing continuity: the phone keeps ITS handle; the canonical
        # desktop id is reported alongside, never required for the next turn.
        "chat_id": chat_id or payload.get("vool_session_id"),
        "canonical_chat_id": payload.get("vool_session_id"),
        "reply": envelope_msg.get("content"),
        "done_reason": payload.get("done_reason"),
        "commit": {"type": commit.get("type"), "request_id": commit.get("request_id")} if commit else {},
        "model": payload.get("model"),
    }
    return 200, result


def _action_attachments_limits(params: dict[str, Any], **_kw):
    from core import chat_attachments

    limits = dict(chat_attachments.limits_payload() or {})
    limits["mobile_max_bytes_per_file"] = _MOBILE_UPLOAD_CEILING
    limits["note"] = "photos are downscaled on the phone before upload; the desktop limits still apply"
    return 200, limits


def _action_attachments_upload(params: dict[str, Any], *, service: MobileCompanionService, device: dict[str, Any], **_kw):
    import binascii

    from core import chat_attachments

    chat_id = str(params.get("chat_id") or "").strip()
    filename = str(params.get("filename") or "").strip()
    content_b64 = str(params.get("content_b64") or "").strip()
    media_type = str(params.get("media_type") or "").strip()[:128]
    kind_hint = str(params.get("kind") or "").strip().lower()
    if not chat_id or not filename or not content_b64:
        return 400, {"error": "chat_id, filename and content_b64 are required"}
    chat_id = _effective_session_id(chat_id)  # stage under the canonical session the send will use
    try:
        data = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError):
        return 400, {"error": "content_b64 is not valid base64"}
    if not data:
        return 400, {"error": "attachment is empty"}
    if len(data) > _MOBILE_UPLOAD_CEILING:
        return 413, {"error": "attachment exceeds the mobile upload ceiling", "max_bytes": _MOBILE_UPLOAD_CEILING}
    if kind_hint not in ("", "photo", "file"):
        return 400, {"error": "kind must be photo or file"}
    try:
        record = chat_attachments.stage_attachment(
            session_id=chat_id,
            declared_name=filename,
            declared_type=media_type,
            data=data,
        )
    except chat_attachments.AttachmentRefused as exc:
        return exc.http_status, exc.to_dict()
    service.store.append_receipt("attachment.uploaded", device_id=str(device.get("device_id") or ""),
                                 detail={"attachment_id": record.get("id"), "chat_id": chat_id,
                                         "size_bytes": record.get("size_bytes"), "kind": record.get("kind")})
    return 201, {"attachment": record}


def _action_approvals_pending(params: dict[str, Any], **_kw):
    # mode_permission_policy has no public list API (prompts normally ride the
    # turn stream). Reading the module's own state under its own lock — no
    # writes, no edits to that module — is the narrowest honest reader.
    import core.mode_permission_policy as mpp

    mpp._ensure_approvals_restored()
    now = _now()
    rows: list[dict[str, Any]] = []
    with mpp._LOCK:
        for token, approval in dict(mpp._APPROVALS).items():
            if str(approval.get("status") or "") != "pending":
                continue
            if float(approval.get("expires_at") or 0) <= now:
                continue
            diff_preview = str(approval.get("diff_preview") or "")
            rows.append({
                "approval_id": token,
                "task_id": approval.get("task_id"),
                "intent": approval.get("intent"),
                "action": approval.get("action"),
                "affected_resources": approval.get("affected_resources"),
                "expected_side_effects": approval.get("expected_side_effects"),
                "reversible": approval.get("reversible"),
                "scope_options": approval.get("scope_options"),
                "planned_actions": approval.get("planned_actions"),
                "planned_action_count": approval.get("planned_action_count"),
                "diff_preview": diff_preview[:1024],
                "raised_at": approval.get("raised_at"),
                "expires_at": approval.get("expires_at"),
            })
    rows.sort(key=lambda r: float(r.get("expires_at") or 0))
    return 200, {"approvals": rows, "needs_approval": bool(rows)}


def _action_approvals_resolve(
    params: dict[str, Any],
    *,
    service: MobileCompanionService,
    device: dict[str, Any],
    **_kw,
):
    from core.mode_permission_policy import resolve_approval
    from core.runtime_task_events import emit_runtime_event

    approval_id = str(params.get("approval_id") or "").strip()
    decision = str(params.get("decision") or "").strip().lower()
    scope = str(params.get("scope") or "once").strip().lower()
    if not approval_id or decision not in {"allow", "deny"}:
        return 400, {"error": "approval_id and decision (allow|deny) are required"}
    if scope not in {"once", "task", "request", "project"}:
        return 400, {"error": "scope must be one of once|task|request|project"}
    approval = resolve_approval(approval_id, decision=decision, scope=scope)
    if approval is None:
        return 409, {"error": "approval is missing, stale, expired, or already resolved"}
    allowed = str(approval.get("status") or "") == "approved"
    emit_runtime_event(
        {"runtime_session_id": approval.get("session_id") or ""},
        event_type="permission_approved" if allowed else "permission_denied",
        message=("Approved" if allowed else "Denied") + f" {approval.get('intent') or 'action'} (mobile).",
        details={
            "approval_id": str(approval.get("approval_id") or ""),
            "task_id": str(approval.get("task_id") or ""),
            "tool_name": str(approval.get("intent") or ""),
            "approval_scope": str(approval.get("scope") or "once"),
            "via": "mobile_companion",
            "device_id": str(device.get("device_id") or ""),
            "device_name": str(device.get("name") or ""),
        },
    )
    service.store.append_receipt("approval.decided", device_id=str(device.get("device_id") or ""), detail={
        "approval_id": approval_id, "decision": decision, "scope": str(approval.get("scope") or scope),
        "intent": str(approval.get("intent") or "")})
    return 200, {"approval": approval, "decision": decision}


def _action_proof_get(params: dict[str, Any], *, client_host: str = "", **_kw):
    from core.proof_projection import build_turn_proof

    chat_id = str(params.get("chat_id") or "").strip()
    request_id = str(params.get("request_id") or "").strip()
    if not chat_id or not request_id:
        return 400, {"error": "chat_id and request_id are required"}
    # Principal isolation is the product's own law: the invocation ledger
    # records each served turn under the principal derived from its TCP peer
    # ("owner_local" / "channel:http:<host>"). The phone reads with the SAME
    # server-side derivation, so it sees the turns ITS source address served —
    # never the desktop's owner-local shelf, and no client-supplied claim.
    principal = (
        "owner_local"
        if is_loopback_host(client_host)
        else f"channel:http:{client_host or 'unknown'}"
    )
    try:
        proof = build_turn_proof(session_id=_read_session_id(chat_id), request_id=request_id, principal=principal)
    except Exception:
        return 404, {"error": "proof_not_bound"}
    if not proof or not proof.get("bound"):
        return 404, {"error": "proof_not_bound"}
    return 200, {"proof": proof}


def _action_notifications_poll(params: dict[str, Any], **_kw):
    from core.runtime_task_events import list_runtime_session_events

    after = _clamp_int(params.get("after"), default=0, lo=0, hi=2**62)
    limit = _clamp_int(params.get("limit"), default=60, lo=1, hi=200)
    session = str(params.get("session") or "").strip()
    events = list_runtime_session_events(
        _read_session_id(session) if session else "", after_seq=after, limit=limit)
    next_after = after
    if events:
        next_after = max(int(item.get("seq") or 0) for item in events)
    # Completion/failure are terminal turn events; keep the rows whole (they
    # are already the product's own typed truth) and add a coarse kind so the
    # phone can badge without parsing every event family.
    classified: list[dict[str, Any]] = []
    for item in events:
        row = dict(item)
        etype = str(item.get("event_type") or "")
        row["mobile_kind"] = (
            "failure" if any(k in etype for k in ("fail", "error", "denied", "refused"))
            else "completion" if any(k in etype for k in ("finaliz", "complete", "approved", "done"))
            else "info"
        )
        classified.append(row)
    return 200, {"events": classified, "next_after": next_after, "session": session}


def _clamp_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


_ACTION_HANDLERS: dict[str, Callable[..., tuple[int, dict[str, Any]]]] = {
    "devices.me": _action_devices_me,
    "status.snapshot": _action_status_snapshot,
    "chats.list": _action_chats_list,
    "chats.prepare": _action_chats_prepare,
    "chats.history": _action_chats_history,
    "chats.send": _action_chats_send,
    "attachments.limits": _action_attachments_limits,
    "attachments.upload": _action_attachments_upload,
    "approvals.pending": _action_approvals_pending,
    "approvals.resolve": _action_approvals_resolve,
    "proof.get": _action_proof_get,
    "notifications.poll": _action_notifications_poll,
}


# ---------------------------------------------------------------------------
# Desktop management endpoints (owner-local)
# ---------------------------------------------------------------------------

def _devices_payload(service: MobileCompanionService) -> dict[str, Any]:
    devices = [_public_device(rec) for rec in service.store.devices().values()]
    devices.sort(key=lambda d: str(d.get("paired_at") or ""))
    return {
        "ok": True,
        "devices": devices,
        "active_count": service.store.active_device_count(),
        "max_devices": max_devices(),
        "relay_enabled": relay_enabled(),
    }


def handle_desktop_revoke(body: dict[str, Any], service: MobileCompanionService):
    store = service.store
    if bool(body.get("all")):
        revoked = []
        for rec in store.devices().values():
            if rec.get("status") == "active":
                revoked.append(revoke_device(service, str(rec["device_id"]), reason=str(body.get("reason") or "panic")))
        return _json(200, {"ok": True, "revoked": [r["device_id"] for r in revoked if r], "devices": _devices_payload(service)["devices"]})
    device_id = str(body.get("device_id") or "").strip()
    if not device_id:
        return _err(400, "invalid_request", "device_id (or all: true) is required")
    rec = revoke_device(service, device_id, reason=str(body.get("reason") or ""))
    if rec is None:
        return _err(404, "device_unknown", "no such device")
    return _json(200, {"ok": True, "device": _public_device(rec), "devices": _devices_payload(service)["devices"]})


def revoke_device(service: MobileCompanionService, device_id: str, *, reason: str = "") -> Optional[dict[str, Any]]:
    store = service.store
    grant = (store.device(device_id) or {}).get("grant") or {}
    if grant.get("grant_id"):
        service.registry.revoke(str(grant["grant_id"]), reason=reason or "device revoked")
    rec = store.mark_device(
        device_id,
        status="revoked",
        revoked_at=service.utcnow().isoformat(),
        revoke_reason=reason or "revoked by operator",
    )
    if rec is not None:
        store.append_receipt("device.revoked", device_id=device_id, detail={"reason": reason or "revoked by operator"})
    return rec


# ---------------------------------------------------------------------------
# Route entrypoints (the seam service.py delegates to)
# ---------------------------------------------------------------------------

_PAIRING_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VOOL — pair a device</title>
<style>
  body {{ background: #0b0d10; color: #e8ecf1; font-family: -apple-system, system-ui, sans-serif;
         display: flex; justify-content: center; padding-top: 48px; }}
  .card {{ background: #12161b; border-radius: 14px; padding: 28px; max-width: 420px; width: 90%; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .fp {{ color: #7d8794; font-size: 12px; font-family: menlo, monospace; margin-bottom: 18px; }}
  #qr {{ background: #fff; padding: 16px; border-radius: 10px; width: fit-content; margin: 0 auto 14px; }}
  #qr table {{ border-collapse: collapse; }}
  #qr td {{ width: 4px; height: 4px; }}
  .code {{ font-size: 30px; letter-spacing: 8px; font-family: menlo, monospace;
           text-align: center; margin: 10px 0 4px; }}
  .meta {{ color: #7d8794; font-size: 12px; text-align: center; }}
  .warn {{ color: #f59e0b; font-size: 12px; margin-top: 14px; line-height: 1.5; }}
</style>
</head>
<body>
<div class="card">
  <h1>Pair a device</h1>
  <div class="fp">desktop authority __SHORT_FP__</div>
  <div id="qr"></div>
  <div class="code">__CODE__</div>
  <div class="meta">pairing id <span id="pid"></span> · expires <span id="exp"></span></div>
  <div class="warn">Scan with the VOOL companion app, or type the code into it.
  The code is single-use and expires in minutes. Every paired device can be
  revoked from this desktop at any time.</div>
</div>
<script>
(function () {{
  var payload = __PAYLOAD__;
  document.getElementById("pid").textContent = payload.pid;
  var left = Math.max(0, Math.round(payload.exp - Date.now() / 1000));
  document.getElementById("exp").textContent = left > 0 ? ("in " + left + "s") : "EXPIRED";
  var uri = "vool-pair://" + window.location.host
    + "?fp=" + payload.fp + "&pid=" + payload.pid
    + "&code=" + payload.code + "&exp=" + payload.exp;
  var qr = qrcode(0, "M");
  qr.addData(uri);
  qr.make();
  document.getElementById("qr").innerHTML = qr.createTableTag(4, 8);
}})();
</script>
</body>
</html>
"""


def _html_pairing_page(payload: dict[str, Any]):
    from core.web.api.service import ApiResponse

    html = _PAIRING_PAGE_TEMPLATE.replace("__SHORT_FP__", str(payload.get("short_fp") or ""))
    html = html.replace("__CODE__", str(payload.get("code") or ""))
    html = html.replace("__PAYLOAD__", json.dumps(payload))
    vendor = (Path(__file__).resolve().parent / "mobile_vendor/qrcode.js").read_text(encoding="utf-8")
    # Inline the vendored renderer before the page logic (one file, no CDN).
    marker = "<script>"
    html = html.replace(marker, marker + chr(10) + vendor, 1)
    return ApiResponse(200, content_type="text/html; charset=utf-8", body=html.encode("utf-8"))


MOBILE_PATH_PREFIX = "/api/mobile/"


def handle_mobile_companion_get(path: str, query: dict[str, list[str]], *, runtime, model_name: str, client_host: str = ""):
    """GET routes under /api/mobile/. Returns None when the path is not ours."""
    if not path.startswith(MOBILE_PATH_PREFIX):
        return None
    service = mobile_companion_service()
    store = service.store

    def _q(name: str) -> str:
        return str((query.get(name) or [""])[0] or "").strip()

    if path == "/api/mobile/devices":
        if not is_loopback_host(client_host):
            return _err(403, "owner_local_required", "device management is desktop-only")
        return _json(200, _devices_payload(service))

    if path == "/api/mobile/pairing/status":
        if not is_loopback_host(client_host):
            return _err(403, "owner_local_required", "pairing is observed from the desktop")
        pairing_id = _q("pairing_id")
        session = store.pairing(pairing_id) if pairing_id else None
        if session is None:
            return _err(404, "pairing_unknown", "no such pairing session")
        status = str(session.get("status") or "waiting")
        if status == "waiting" and float(session.get("expires_at_epoch") or 0) <= _now():
            status = "expired"
        return _json(200, {"ok": True, "pairing_id": pairing_id, "status": status,
                           "device_id": session.get("device_id") or "",
                           "device_name": (store.device(str(session.get("device_id") or "")) or {}).get("name", "")})

    if path == "/api/mobile/pairing/challenge":
        # Pre-claim leaf for the phone: everything needed to construct the
        # proof-of-possession payload (server-held values, never client-
        # supplied) plus the desktop identity to pin. The pairing id itself
        # is the unguessable gate.
        pairing_id = _q("pairing_id")
        session = store.pairing(pairing_id) if pairing_id else None
        if session is None:
            return _err(404, "pairing_unknown", "no such pairing session")
        if session.get("status") == "claimed":
            return _err(409, "pairing_used", "this pairing code was already used")
        if float(session.get("expires_at_epoch") or 0) <= _now():
            return _err(410, "pairing_expired", "pairing code expired; start a new pairing on the desktop")
        return _json(200, {
            "ok": True,
            "pairing_id": pairing_id,
            "challenge": str(session.get("challenge") or ""),
            "expires_at_epoch": int(float(session.get("expires_at_epoch") or 0)),
            "desktop_fingerprint": service.desktop_fingerprint(),
            "desktop_public_key": device_identity.public_hex(service.authority_key()),
        })

    if path == "/api/mobile/pairing/page":
        # The DESKTOP pairing surface: renders the actual QR image for this
        # pairing (vendored qrcode-generator, client-side), plus the manual
        # fallback fields. Owner-local, like every pairing surface.
        if not is_loopback_host(client_host):
            return _err(403, "owner_local_required", "the pairing page is desktop-only")
        pairing_id = _q("pairing_id")
        session = store.pairing(pairing_id) if pairing_id else None
        if session is None:
            return _err(404, "pairing_unknown", "no such pairing session")
        payload = {
            "pid": pairing_id,
            "code": str(session.get("code") or ""),
            "fp": service.desktop_fingerprint(),
            "short_fp": service.desktop_short_fingerprint(),
            "exp": int(float(session.get("expires_at_epoch") or 0)),
            "status": str(session.get("status") or "waiting"),
        }
        return _html_pairing_page(payload)

    if path == "/api/mobile/receipts":
        if not is_loopback_host(client_host):
            return _err(403, "owner_local_required", "receipts are desktop-only")
        after = _clamp_int(_q("after"), default=0, lo=0, hi=2**62)
        limit = _clamp_int(_q("limit"), default=200, lo=1, hi=1000)
        rows, head, verified = store.receipts(after_seq=after, limit=limit)
        return _json(200, {"ok": True, "receipts": rows, "chain_head": head, "chain_verified": verified})

    if path == "/api/mobile/info":
        # Unauthenticated capability leaf: what the companion offers and which
        # desktop it is talking to. No chat, device or model data.
        return _json(200, {
            "ok": True,
            "product": "vool-mobile-companion",
            "actions": sorted(COMPANION_ACTIONS),
            "desktop_fingerprint": service.desktop_fingerprint(),
            "desktop_short_fingerprint": service.desktop_short_fingerprint(),
            "relay_enabled": relay_enabled(),
            "pairing_ttl_seconds": pairing_ttl_seconds(),
        })

    return _err(404, "not_found", "unknown mobile companion route")


def handle_mobile_companion_post(
    path: str,
    body: dict[str, Any],
    headers: dict[str, Any],
    *,
    runtime,
    model_name: str,
    client_host: str = "",
    workspace_root_provider=None,
):
    """POST routes under /api/mobile/. Returns None when the path is not ours."""
    if not path.startswith(MOBILE_PATH_PREFIX):
        return None
    service = mobile_companion_service()

    if path == "/api/mobile/pairing/start":
        return start_pairing(body, headers, service, client_host)
    if path == "/api/mobile/pairing/claim":
        return claim_pairing(body, headers, service, client_host)
    if path == "/api/mobile/companion":
        if workspace_root_provider is None:
            from core.web.api.runtime import default_workspace_root

            workspace_root_provider = default_workspace_root
        return dispatch_companion(
            body, headers,
            runtime=runtime,
            model_name=model_name,
            client_host=client_host,
            workspace_root_provider=workspace_root_provider,
            service=service,
        )
    if path in {"/api/mobile/devices/revoke", "/api/mobile/devices"}:
        if not is_loopback_host(client_host):
            return _err(403, "owner_local_required", "device management is desktop-only")
        return handle_desktop_revoke(body, service)
    return _err(404, "not_found", "unknown mobile companion route")
