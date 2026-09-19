"""Device Link capability-grant protocol (desktop core).

Ported 2026-08-30 from the CONVERGED pass (round 2) tether/protocol.py — the
newest of the three preserved passes — verbatim except: the identity import
(core.device_link.identity, the bridge authority key convention), de-tethered
names ("tether" already names a model provider in this repo), and the operator
gate below. This module is deliberately READ-ONLY-registered: it mints and
verifies grants and authorizes requests, but nothing here opens a socket or
executes a verb. The bridge server, TLS, pairing and verb handlers are the
operator-gated integration (see core/device_link/__init__.py).

Direct descendant of tether-control pass-001 `tether_protocol.py` with ONE
deliberate change: grants are signed by the BRIDGE's Ed25519 identity key
(nacl) instead of canonical network.signer. The bridge key already anchors
device-mesh pairing, TLS pinning and LAN beacons — reusing it keeps exactly
one desktop authority in the converged product.

Everything else is preserved: envelope ladder, scope kinds, per-grant bearer
secret, HMAC-SHA256 over canonical request bytes, nonce replay shield,
+/-120 s clock window, use budget, expiry and instant revocation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from core.device_link import identity

# --------------------------------------------------------------------------
# Envelopes (unchanged from tether control)
# --------------------------------------------------------------------------

ENVELOPE_VIEW_ONLY = "VIEW_ONLY"
ENVELOPE_FILES = "FILES"
ENVELOPE_FILES_TERMINAL = "FILES+TERMINAL"
ENVELOPE_DEV_MACHINE = "DEV_MACHINE"
ENVELOPE_FULL_REMOTE_CONTROL = "FULL_REMOTE_CONTROL"

ENVELOPE_ORDER = [
    ENVELOPE_VIEW_ONLY,
    ENVELOPE_FILES,
    ENVELOPE_FILES_TERMINAL,
    ENVELOPE_DEV_MACHINE,
    ENVELOPE_FULL_REMOTE_CONTROL,
]

VERB_CATALOG: dict[str, str] = {
    "fs.list": "list a directory",
    "fs.read": "read a file",
    "fs.write": "write/create a file",
    "fs.delete": "delete a file or directory",
    "upload": "push a file from phone to desktop",
    "download": "fetch a file from desktop to phone",
    "term.run": "run a whitelisted terminal command",
    "script.run": "run a script inside a scoped project",
    "git.status": "show repo status",
    "git.log": "show commit history",
    "git.diff": "show uncommitted changes",
    "git.commit": "create a git commit",
    "dev.test": "run the project test command",
    "browser.open": "open a URL in the desktop browser",
    "clip.read": "read the desktop clipboard",
    "clip.write": "write to the desktop clipboard",
    "screen.snapshot": "capture a screenshot",
    "screen.view": "view a live screen frame",
    "app.list": "list running applications",
    "app.launch": "launch an application",
    "input.mouse": "move/click the mouse",
    "input.keyboard": "send keystrokes",
    "vool.action": "invoke a local VOOL operator action",
}

# Cumulative ladder: each rung ADDS its verbs to everything below it.
ENVELOPE_VERBS: dict[str, set] = {
    ENVELOPE_VIEW_ONLY: {
        "fs.list", "fs.read", "git.status", "git.log", "git.diff",
        "app.list", "screen.snapshot", "screen.view", "clip.read", "vool.action",
    },
    ENVELOPE_FILES: {"fs.write", "fs.delete", "upload", "download"},
    ENVELOPE_FILES_TERMINAL: {"term.run", "script.run"},
    ENVELOPE_DEV_MACHINE: {"git.commit", "dev.test", "browser.open", "clip.write"},
    ENVELOPE_FULL_REMOTE_CONTROL: {"app.launch", "input.mouse", "input.keyboard"},
}

CONSENT_GATED_VERBS = frozenset({
    "fs.delete", "input.mouse", "input.keyboard", "app.launch", "script.run",
})

SCOPE_ONE_ACTION = "one_action"
SCOPE_ONE_SESSION = "one_session"
SCOPE_ONE_PROJECT = "one_project"
SCOPE_ONE_PATHS = "paths"
SCOPE_UNTIL_REVOKED = "until_revoked"


class GrantError(Exception):
    """Grant issuance or verification refused."""


#: Lineage alias — the converged pass called this TetherGrantError.
TetherGrantError = GrantError


def verbs_for_envelope(envelope: str) -> set:
    if envelope not in ENVELOPE_ORDER:
        raise ValueError(f"unknown envelope: {envelope!r}")
    granted = set()
    for step in ENVELOPE_ORDER[: ENVELOPE_ORDER.index(envelope) + 1]:
        granted |= ENVELOPE_VERBS[step]
    return granted


def envelope_for_verb(verb: str) -> Optional[str]:
    for env in ENVELOPE_ORDER:
        if verb in verbs_for_envelope(env):
            return env
    return None


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# Grant lifecycle — signed by the bridge identity key (the ONE authority)
# --------------------------------------------------------------------------

def sign_with_authority_key(signing_key, payload_bytes: bytes) -> str:
    return signing_key.sign(payload_bytes).signature.hex()


def verify_with_authority_key(public_hex: str, payload_bytes: bytes, sig_hex: str) -> bool:
    from nacl.signing import VerifyKey
    try:
        VerifyKey(bytes.fromhex(public_hex)).verify(payload_bytes, bytes.fromhex(sig_hex))
        return True
    except Exception:
        return False


def issue_grant(
    *,
    signing_key,
    device_name: str,
    envelope: str,
    scope_kind: str,
    allowed_paths: Optional[list] = None,
    allowed_executables: Optional[list] = None,
    ttl_seconds: int = 3600,
    max_uses: int = 1,
    granted_to_peer_id: Optional[str] = None,
) -> dict:
    """Mint a grant signed by the desktop authority's Ed25519 identity key."""
    if envelope not in ENVELOPE_ORDER:
        raise GrantError(f"unknown envelope: {envelope!r}")
    if scope_kind not in (
        SCOPE_ONE_ACTION, SCOPE_ONE_SESSION, SCOPE_ONE_PROJECT,
        SCOPE_ONE_PATHS, SCOPE_UNTIL_REVOKED,
    ):
        raise GrantError(f"unknown scope kind: {scope_kind!r}")
    if scope_kind == SCOPE_ONE_ACTION:
        max_uses = 1
    if scope_kind == SCOPE_ONE_PROJECT and not allowed_paths:
        raise GrantError("one_project scope requires a project root path")
    if scope_kind == SCOPE_UNTIL_REVOKED:
        expires_at = ""
    else:
        ttl = max(30, int(ttl_seconds))
        if scope_kind == SCOPE_ONE_ACTION:
            ttl = min(ttl, 900)
        expires_at = _iso(_utcnow() + timedelta(seconds=ttl))

    body = {
        "grant_id": str(uuid.uuid4()),
        "device_name": str(device_name)[:64],
        "granted_by": identity.public_hex(signing_key),
        "granted_to": str(granted_to_peer_id or "phone:" + secrets.token_hex(8)),
        "envelope": envelope,
        "scope_kind": scope_kind,
        "allowed_paths": [str(p) for p in (allowed_paths or [])],
        "allowed_executables": sorted({str(e) for e in (allowed_executables or [])}),
        "max_uses": int(max(1, int(max_uses))),
        "issued_at": _iso(_utcnow()),
        "expires_at": expires_at,
    }
    body["signature"] = sign_with_authority_key(signing_key, _canonical_bytes(body))
    # The bearer secret is NOT covered by the signature: it never travels back
    # to the desktop from the phone; it only authenticates requests.
    body["grant_secret"] = secrets.token_urlsafe(24)
    return body


def grant_signature_payload(grant: dict) -> dict:
    return {k: v for k, v in grant.items() if k not in ("signature", "grant_secret")}


def verify_grant_self_signature(grant: dict) -> bool:
    payload = grant_signature_payload(grant)
    sig = str(grant.get("signature") or "")
    return verify_with_authority_key(
        str(grant.get("granted_by") or ""), _canonical_bytes(payload), sig)


# --------------------------------------------------------------------------
# Request authentication (phone proves possession of the grant secret)
# --------------------------------------------------------------------------

def request_signature_payload(*, grant_id: str, verb: str, params: dict,
                              nonce: str, timestamp: str) -> dict:
    return {
        "grant_id": grant_id,
        "verb": str(verb),
        "params": params or {},
        "nonce": nonce,
        "timestamp": timestamp,
    }


def sign_request(grant_secret: str, *, grant_id: str, verb: str, params: dict,
                 nonce: str, timestamp: str) -> str:
    payload = request_signature_payload(
        grant_id=grant_id, verb=verb, params=params, nonce=nonce, timestamp=timestamp)
    return hmac.new(
        grant_secret.encode("utf-8"), _canonical_bytes(payload), hashlib.sha256
    ).hexdigest()


# --------------------------------------------------------------------------
# Authorization decision (desktop enforcement core)
# --------------------------------------------------------------------------

class GrantRegistry:
    """Active-grant bookkeeping with use counting and instant revocation."""

    def __init__(self):
        self._grants: dict[str, dict] = {}
        self._used_counts: dict[str, int] = {}
        self._nonces: set = set()
        self._revoked: dict[str, dict] = {}

    def register(self, grant: dict) -> None:
        self._grants[grant["grant_id"]] = grant
        self._used_counts[grant["grant_id"]] = 0

    def get(self, grant_id: str) -> Optional[dict]:
        return self._grants.get(str(grant_id or "").strip())

    def revoke(self, grant_id: str, reason: str = "") -> bool:
        gid = str(grant_id or "").strip()
        if gid not in self._grants:
            return False
        self._revoked[gid] = {"reason": reason, "at": _iso(_utcnow())}
        return True

    def is_revoked(self, grant_id: str) -> bool:
        return grant_id in self._revoked

    def revoke_all(self, reason: str = "panic") -> int:
        n = 0
        for gid in list(self._grants):
            if gid not in self._revoked:
                self.revoke(gid, reason)
                n += 1
        return n

    def active_summary(self) -> list:
        out = []
        now = _utcnow()
        for gid, g in self._grants.items():
            exp = parse_iso(g.get("expires_at") or "")
            alive = (
                gid not in self._revoked
                and (exp is None or exp > now)
                and self._used_counts[gid] < int(g["max_uses"])
            )
            out.append({
                "grant_id": gid,
                "device_name": g.get("device_name"),
                "envelope": g.get("envelope"),
                "scope_kind": g.get("scope_kind"),
                "alive": alive,
                "uses": "%d/%d" % (self._used_counts[gid], g["max_uses"]),
                "expires_at": g.get("expires_at") or "until revoked",
                "revoked": self._revoked.get(gid),
            })
        return out

    def envelope_covers(self, peer_id: str, verb: str) -> bool:
        """True when peer_id holds a live grant whose envelope includes verb."""
        now = _utcnow()
        for gid, g in self._grants.items():
            if g.get("granted_to") != peer_id or gid in self._revoked:
                continue
            exp = parse_iso(str(g.get("expires_at") or ""))
            if exp is not None and exp <= now:
                continue
            if self._used_counts.get(gid, 0) >= int(g["max_uses"]):
                continue
            if verb in verbs_for_envelope(str(g.get("envelope") or "")):
                return True
        return False

    def has_live_grant(self, peer_id: str) -> bool:
        """True when peer_id holds at least one live (unrevoked, unexpired,
        under-budget) grant of ANY envelope."""
        now = _utcnow()
        for gid, g in self._grants.items():
            if g.get("granted_to") != peer_id or gid in self._revoked:
                continue
            exp = parse_iso(str(g.get("expires_at") or ""))
            if exp is not None and exp <= now:
                continue
            if self._used_counts.get(gid, 0) < int(g["max_uses"]):
                return True
        return False

    def authorize(self, *, grant: dict, verb: str, params: dict, nonce: str,
                  timestamp: str, signature: str) -> tuple[bool, str, dict]:
        """Full decision pipeline. Returns (ok, reason, decision_detail)."""
        gid = str(grant.get("grant_id") or "")
        detail = {"envelope": grant.get("envelope"), "scope_kind": grant.get("scope_kind")}

        if self.is_revoked(gid):
            return False, "grant revoked: {}".format(self._revoked[gid]["reason"]), detail

        if not verify_grant_self_signature(grant):
            return False, "grant signature invalid (not minted by this desktop)", detail

        expires_at = parse_iso(str(grant.get("expires_at") or ""))
        if expires_at is not None and expires_at <= _utcnow():
            return False, "grant expired at {}".format(grant["expires_at"]), detail

        used = self._used_counts.get(gid, 0)
        if used >= int(grant["max_uses"]):
            return False, "grant use budget exhausted (%d/%d)" % (used, grant["max_uses"]), detail

        ts = parse_iso(timestamp)
        if ts is None or abs((_utcnow() - ts).total_seconds()) > 120:
            return False, "request timestamp outside +/-120s window (replay shield)", detail

        if nonce in self._nonces:
            return False, "nonce already seen (replay shield)", detail

        expected_payload = request_signature_payload(
            grant_id=gid, verb=verb, params=params, nonce=nonce, timestamp=timestamp)
        expected = hmac.new(
            str(grant.get("grant_secret") or "").encode("utf-8"),
            _canonical_bytes(expected_payload), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(signature)):
            return False, "request HMAC invalid (wrong grant secret or tampered body)", detail

        if verb not in VERB_CATALOG:
            return False, f"unknown verb: {verb!r}", detail

        envelope = str(grant.get("envelope") or "")
        if verb not in verbs_for_envelope(envelope):
            return False, (
                f"verb {verb!r} requires envelope {envelope_for_verb(verb)}; grant carries {envelope}"), detail

        err = check_scope_paths(grant, params)
        if err:
            return False, err, detail

        self._nonces.add(nonce)
        self._used_counts[gid] = used + 1
        detail["use"] = "%d/%d" % (self._used_counts[gid], grant["max_uses"])
        detail["consent_required"] = verb in CONSENT_GATED_VERBS
        return True, "authorized", detail


def check_scope_paths(grant: dict, params: dict) -> Optional[str]:
    """Scope containment: every path-ish param must resolve under a grant root."""
    import os
    scope_kind = grant.get("scope_kind")
    roots = [str(p) for p in (grant.get("allowed_paths") or [])]
    if scope_kind == SCOPE_ONE_PROJECT and len(roots) != 1:
        return "one_project grant must carry exactly one root"
    if scope_kind == SCOPE_ONE_PATHS and not roots:
        return "paths grant must carry at least one root"
    if not roots:
        return None

    candidates = []
    for key in ("path", "source", "dest", "project_root"):
        val = str((params or {}).get(key) or "").strip()
        if val:
            candidates.append(val)

    norm_roots = [os.path.realpath(os.path.expanduser(r)) for r in roots]
    for cand in candidates:
        real = os.path.realpath(os.path.expanduser(cand))
        if not any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in norm_roots):
            return f"path {cand!r} escapes grant scope roots"
    return None
