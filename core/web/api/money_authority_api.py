"""The owner-local HTTP seam of the monetary law (`core.effect_budget_money`).

What it serves, and what it deliberately does not:

* READS — the contract, the money projection, grants, liabilities, money rules
  and the funding decision. Owner-local (loopback) only: amounts, accounts and
  grant terms are the operator's business.
* NARROWING — revoke a grant, run dead-instance reconciliation. Owner-local
  POSTs behind the guard suite the other state-changing owner routes use: a
  loopback TCP peer, a JSON content type, a same-origin (or absent) Origin, a
  bounded body and a JSON object.
* NO WIDENING. Minting a grant, raising a rule, setting a conversion bound or a
  funding policy is not reachable over HTTP. Those take an operator token and
  belong to trusted approval surfaces (the wallet's PIN or device approval, the
  owner's CLI). A loopback POST is something any local process can send —
  including a command a model was allowed to run — and a money envelope must
  never be mintable that way.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote

from core.request_trust import is_loopback_host

PREFIX = "/api/money/"
MAX_BODY_BYTES = 65_536
_WIDENING_PATHS = frozenset(
    {"/api/money/grants", "/api/money/rules", "/api/money/conversion-bounds", "/api/money/funding-policy"}
)
_STATUS_FOR_CODE = {
    "MONEY_INVALID_REQUEST": 400,
    "MONEY_STATE_ERROR": 409,
    "MONEY_CLAIM_CONFLICT": 409,
    "MONEY_SETTLEMENT_CONFLICT": 409,
    "MONEY_IDEMPOTENCY_CONFLICT": 409,
    "MONEY_STORE_UNAVAILABLE": 503,
    "MONEY_STORE_CORRUPT": 500,
    "EFFECT_BUDGET_AUTHORITY_REFUSED": 403,
    "EFFECT_BUDGET_STORE_UNAVAILABLE": 503,
}


def owns(path: str) -> bool:
    return str(path or "").startswith(PREFIX)


def _json(status: int, payload: dict[str, Any]) -> Any:
    from core.web.api.service import json_response

    return json_response(status, payload, headers={"Cache-Control": "no-store"})


def _refused(exc: Any) -> Any:
    code = str(getattr(exc, "code", "") or "MONEY_STORE_UNAVAILABLE")
    status = _STATUS_FOR_CODE.get(code, 403 if code.startswith("MONEY_AUTHORITY") else 400)
    return _json(
        status,
        {"ok": False, "error": code, "message": str(getattr(exc, "detail", "") or "")[:500], "rule": str(getattr(exc, "rule", "") or "")},
    )


def _first(query: dict[str, list[str]] | None, name: str, default: str = "") -> str:
    values = (query or {}).get(name) or []
    return str(values[0]).strip() if values else default


def _header(headers: dict[str, Any] | None, name: str) -> str:
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return str(value or "")
    return ""


def handle_money_get(path: str, query: dict[str, list[str]] | None, *, client_host: str = "") -> Any:
    if not is_loopback_host(client_host):
        return _json(403, {"ok": False, "error": "owner_local_required"})
    from core import effect_budget_money as ebm
    from core.effect_budget import EffectBudgetRefusedError

    try:
        if path == "/api/money/contract":
            return _json(200, {"ok": True, "contract": ebm.money_contract()})
        if path == "/api/money/projection":
            projection = ebm.money_projection(
                provider_id=_first(query, "provider_id"),
                task_id=_first(query, "task_id"),
                session_id=_first(query, "session_id"),
                grant_id=_first(query, "grant_id"),
            )
            return _json(200, {"ok": True, "projection": projection})
        if path == "/api/money/grants":
            active_only = _first(query, "active").lower() in {"1", "true", "yes"}
            return _json(200, {"ok": True, "grants": [grant.to_dict() for grant in ebm.money_grants(active_only=active_only)]})
        if path == "/api/money/liabilities":
            try:
                limit = max(1, min(int(_first(query, "limit", "200")), 1000))
            except ValueError:
                limit = 200
            found = ebm.liabilities(
                state=_first(query, "state"), grant_id=_first(query, "grant_id"), effect_id=_first(query, "effect_id"), limit=limit
            )
            return _json(200, {"ok": True, "liabilities": found})
        if path.startswith("/api/money/liabilities/"):
            liability_id = unquote(path.rsplit("/", 1)[-1])
            found = ebm.liability(liability_id)
            if found is None:
                return _json(404, {"ok": False, "error": "liability_not_found"})
            return _json(200, {"ok": True, "liability": found})
        if path == "/api/money/rules":
            return _json(200, {"ok": True, "rules": [rule.to_dict() for rule in ebm.money_rules()]})
        if path == "/api/money/funding":
            decision = ebm.evaluate_funding_policy(
                provider_id=_first(query, "provider_id"),
                provider_account=_first(query, "provider_account"),
                asset=ebm.AssetIdentity.from_key(_first(query, "asset_key")),
            )
            return _json(200, {"ok": True, "decision": decision.to_dict()})
    except EffectBudgetRefusedError as exc:
        return _refused(exc)
    return _json(404, {"ok": False, "error": "not_found"})


def handle_money_post(path: str, body: Any, headers: dict[str, Any] | None = None, *, client_host: str = "") -> Any:
    # owner decision FIRST, from the real TCP peer, before any shape work
    if not is_loopback_host(client_host):
        return _json(403, {"ok": False, "error": "owner_local_required"})
    content_type = _header(headers, "content-type").lower()
    if content_type and "json" not in content_type:
        return _json(415, {"ok": False, "error": "content-type must be application/json"})
    origin = _header(headers, "origin").strip()
    if origin:
        from core.web.api.runtime import host_header_allowed

        if not host_header_allowed(origin.split("://", 1)[-1]):
            return _json(403, {"ok": False, "error": "cross-origin request not allowed"})
    if not isinstance(body, dict):
        return _json(400, {"ok": False, "error": "body must be a JSON object"})
    try:
        if len(json.dumps(body)) > MAX_BODY_BYTES:
            return _json(413, {"ok": False, "error": "request body too large"})
    except (TypeError, ValueError):
        return _json(400, {"ok": False, "error": "unserializable body"})
    if path in _WIDENING_PATHS:
        return _json(
            403,
            {
                "ok": False,
                "error": "widening_not_served",
                "message": "money authority is minted or widened only through a trusted approval surface, never a loopback POST",
            },
        )
    from core import effect_budget as eb
    from core import effect_budget_money as ebm

    try:
        if path == "/api/money/grants/revoke":
            grant_id = str(body.get("grant_id") or "").strip()
            if not grant_id:
                return _json(400, {"ok": False, "error": "grant_id is required"})
            token = eb.grant_operator_budget_authority("owner-local money revocation")
            grant = ebm.revoke_money_authority(
                token, grant_id, reason=str(body.get("reason") or "revoked from the owner surface")[:200]
            )
            return _json(200, {"ok": True, "grant": grant.to_dict()})
        if path == "/api/money/reconcile":
            money_changes = ebm.reconcile_money_liabilities(force=True)
            released_units = eb.reconcile_stale_reservations(force=True)
            return _json(200, {"ok": True, "money_changes": money_changes, "unit_reservations_released": released_units})
    except eb.EffectBudgetRefusedError as exc:
        return _refused(exc)
    return _json(404, {"ok": False, "error": "not_found"})


__all__ = ["PREFIX", "handle_money_get", "handle_money_post", "owns"]
