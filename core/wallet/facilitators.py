"""Facilitator capability records: typed, validated, discovered or operator-configured,
and proven PER NETWORK. A facilitator is the non-custodial service that verifies
(``POST /verify``) and settles (``POST /settle``) x402 payments; support is never inferred
from a chain family or an asset row. The official discovery shape is
``GET {origin}/supported`` → ``{kinds: [{x402Version, scheme, network, extra?}], ...}``
(x402 v2 spec §7.3) — a malformed discovery answer refuses and records nothing.

Registration and discovery are operator-local doors reached from the loopback API; models,
plugins, skills, MCP and council seats have no surface here at all. The registry rows live
in the wallet's own SQLite store: one row per (facilitator, network, scheme) with its
verification evidence.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.wallet import custody, outbound
from core.wallet.security import wallet_fault
from core.wallet.store import connection, dumps, loads, utcnow

AUTHORITY = "core.wallet.facilitators"

X402_VERSION = 2
DISCOVERY_PATH = "/supported"


@dataclass(frozen=True)
class FacilitatorCapability:
    facilitator_id: str
    origin: str
    network: str
    scheme: str
    x402_version: int = X402_VERSION
    verified: bool = False
    verified_at: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"facilitator_id": self.facilitator_id, "origin": self.origin, "network": self.network, "scheme": self.scheme, "x402_version": self.x402_version, "verified": self.verified, "verified_at": self.verified_at, "evidence": dict(self.evidence)}


_COLS = "facilitator_id, origin, network, scheme, x402_version, verified, verified_at, evidence_json, created_at"


def _row(row: Any) -> FacilitatorCapability:
    return FacilitatorCapability(
        facilitator_id=row[0], origin=row[1], network=row[2], scheme=row[3], x402_version=int(row[4]),
        verified=bool(row[5]), verified_at=str(row[6] or ""), evidence=loads(row[7], {}),
    )


def _upsert(cap: FacilitatorCapability) -> FacilitatorCapability:
    with connection() as conn:
        conn.execute(
            f"INSERT INTO wallet_facilitators ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(facilitator_id, network, scheme) DO UPDATE SET origin = excluded.origin, x402_version = excluded.x402_version,"
            " verified = excluded.verified, verified_at = excluded.verified_at, evidence_json = excluded.evidence_json",
            (cap.facilitator_id, cap.origin, cap.network, cap.scheme, cap.x402_version, int(cap.verified), cap.verified_at, dumps(cap.evidence), utcnow()),
        )
    return cap


def list_capabilities(*, verified_only: bool = False) -> list[FacilitatorCapability]:
    with connection() as conn:
        sql = f"SELECT {_COLS} FROM wallet_facilitators"
        rows = conn.execute(sql + (" WHERE verified = 1" if verified_only else "") + " ORDER BY created_at ASC").fetchall()
    return [_row(r) for r in rows]


def capability_for(network: str, scheme: str) -> FacilitatorCapability | None:
    with connection() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM wallet_facilitators WHERE network = ? AND scheme = ? AND verified = 1 ORDER BY verified_at DESC LIMIT 1", (str(network), str(scheme))).fetchone()
    return _row(row) if row else None


def require_capability(network: str, scheme: str, *, source_context: dict[str, Any] | None = None) -> FacilitatorCapability:
    """The typed door: an x402 payment on this network needs a VERIFIED facilitator record.
    None exists → x402_scheme_unavailable, never a hopeful default."""
    from core.wallet import chains

    spec = chains.resolve_network(network)
    found = capability_for(spec.network, str(scheme))
    if found is None:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"network": spec.network, "scheme": str(scheme), "reason": "no_verified_facilitator"}, source_context=source_context)
    return found


def validate_origin(origin: str, *, source_context: dict[str, Any] | None = None) -> str:
    return outbound.validate_target(str(origin or "").strip(), spec=None, public_only=True)


def register_facilitator(*, facilitator_id: str, origin: str, networks: list[str], schemes: list[str], source_context: dict[str, Any] | None = None) -> list[FacilitatorCapability]:
    """Operator-local registration of an UNVERIFIED capability row set. Discovery or an
    explicit operator proof flips it to verified; registration alone does not."""
    custody.require_enabled(source_context=source_context)
    clean_id = str(facilitator_id or "").strip()
    if not clean_id or len(clean_id) > 120:
        raise wallet_fault("wallet_not_found", authority=AUTHORITY, context={"reason": "facilitator_id_required"}, source_context=source_context)
    clean_origin = validate_origin(origin, source_context=source_context)
    from core.wallet import chains

    caps: list[FacilitatorCapability] = []
    for network in networks:
        spec = chains.resolve_network(network)
        for scheme in schemes:
            caps.append(_upsert(FacilitatorCapability(facilitator_id=clean_id, origin=clean_origin, network=spec.network, scheme=str(scheme), verified=False)))
    return caps


def discover(origin: str, *, facilitator_id: str = "", source_context: dict[str, Any] | None = None) -> list[FacilitatorCapability]:
    """Ask the facilitator what it supports (GET /supported, official shape) and store one
    VERIFIED row per declared (network, scheme) that is ALSO a declared chain row. Networks
    outside the registry are recorded in the evidence, never as spendable rows."""
    custody.require_enabled(source_context=source_context)
    clean_origin = validate_origin(origin, source_context=source_context)
    from core.wallet import chains

    answer = outbound.fetch(clean_origin + DISCOVERY_PATH, timeout=15.0)
    if answer["status"] != 200:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": f"discovery_http_{answer['status']}", "origin": clean_origin}, source_context=source_context)
    try:
        payload = json.loads(answer["body"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "discovery_undecodable", "origin": clean_origin}, source_context=source_context) from None
    kinds = payload.get("kinds") if isinstance(payload, dict) else None
    if not isinstance(kinds, list):
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "discovery_shape_invalid", "origin": clean_origin}, source_context=source_context)
    clean_id = str(facilitator_id or "").strip() or "fac-" + uuid.uuid4().hex[:12]
    caps: list[FacilitatorCapability] = []
    for kind in kinds:
        if not isinstance(kind, dict) or int(kind.get("x402Version") or 0) != X402_VERSION:
            continue
        network = str(kind.get("network") or "")
        scheme = str(kind.get("scheme") or "")
        try:
            spec = chains.resolve_network(network)
        except Exception:
            continue  # an undeclared network is evidence, not a spendable row
        caps.append(_upsert(FacilitatorCapability(
            facilitator_id=clean_id, origin=clean_origin, network=spec.network, scheme=scheme,
            verified=True, verified_at=utcnow(), evidence={"kinds": kinds[:20], "source": "discovery"},
        )))
    if not caps:
        raise wallet_fault("x402_scheme_unavailable", authority=AUTHORITY, context={"reason": "discovery_no_declared_support", "origin": clean_origin}, source_context=source_context)
    return caps


__all__ = ["AUTHORITY", "DISCOVERY_PATH", "FacilitatorCapability", "capability_for", "discover", "list_capabilities", "register_facilitator", "require_capability"]
