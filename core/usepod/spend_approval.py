"""The UsePod spend-approval bridge: user consent, through the trusted approval authority.

A spend grant is ECONOMIC authority, so its consent path is the runtime's ONE approval
authority (``core.mode_permission_policy``'s pending-approval store, resolved by the operator
through the chat/native approval surface or the CLI) -- not a new mechanism, and not an open
HTTP mint. ``/api/money`` widening stays refused exactly as it was.

The flow, and who owns each step:

1. ``propose`` (owner-local, Settings): the SERVER derives every economic fact -- the prepaid
   account (the credential fingerprint), the asset, and the model/route allowlists from the
   operator's already-approved route bounds -- and the operator chooses only the ceilings:
   per-call maximum, aggregate maximum, expiry. Prepaid provider budgets explicitly cover all
   models/chats on that account, either until spent/disabled or renewing every 24 hours while
   enabled. Route price approval still applies. The exact fact sheet is registered as a
   PENDING approval in the trusted store and shown to the operator.
   For accountless x402 no UsePod token is read: the wallet payment authority names the payer
   wallet, the network it can pay on now and the SOL fee ceiling, and the operator also picks
   which documented asset pays (USDC or SOL), with ceilings in that asset's own unit.
2. The OPERATOR resolves the approval (allow/deny) through the existing approval door. Nothing
   here can resolve it for them; denial changes no authority.
3. ``confirm`` (owner-local): mints ONE grant from the approval's recorded facts through the
   real operator authority (``grant_operator_budget_authority`` + ``grant_money_authority``,
   both outside every effect scope, both journaled). The approval's id is the grant's
   ``approval_ref``, which also makes replay idempotent: a second confirm returns the same
   grant, never a second one. Revocation is the existing ``POST /api/money/grants/revoke``.

Route approval remains a separate fact: it bounds what a dispatch may COST; this consent
bounds what may be SPENT. Neither implies the other.
"""
from __future__ import annotations

import time
from typing import Any

APPROVAL_INTENT = "usepod.spend_grant"
#: The money law's own grant expiry ceiling.
MAX_GRANT_LIFETIME_SECONDS = 30 * 24 * 3600
_DEFAULT_LIFETIME_SECONDS = 24 * 3600
#: UsePod's documented x402 assets and the unit each is counted in (docs.usepod.ai/api/x402-payments). A consent names
#: ONE of them and its ceilings are integers of that unit: µUSDC never bounds lamports.
X402_ASSET_UNITS: dict[str, tuple[str, int]] = {"USDC": ("usdc_microunit", 6), "SOL": ("lamport", 9)}
_UNIT_LABELS = {"usdc_microunit": "µUSDC", "lamport": "lamports"}
_NO_ROUTES = "no approved route bounds exist; approve a route for a model first (route approval and spend consent are separate facts)"


def _routing_facts() -> tuple[list[str], list[str]]:
    """The models and route-class sets the operator has already approved routes for. The spend
    grant's allowlists come from HERE, server-side: the operator consents to spending on what
    they route-approved, and a request body cannot name other models."""
    from core.usepod import routing

    state = routing.load_route_state()
    models: list[str] = []
    routes: list[str] = []
    for model_id, bound in sorted((state.bounds or {}).items()):
        models.append(str(model_id))
        classes = "+".join(sorted(str(item) for item in (bound.allowed_route_classes or ())))
        if classes and classes not in routes:
            routes.append(classes)
    return models, routes


def _unit_label(asset: str) -> str:
    return _UNIT_LABELS[X402_ASSET_UNITS.get(str(asset or "").upper(), ("usdc_microunit", 6))[0]]


def _x402_proposal_facts(*, per_call_atomic: int, max_total_atomic: int, expiry_epoch: float, asset: str) -> dict[str, Any]:
    """Accountless x402: no UsePod token is read. The wallet payment authority names the network it can pay on now, the
    payer account and the fee asset and ceiling; the owner picks the asset and the ceilings in that asset's unit."""
    from core.usepod.discovery import configured_origin
    from core.usepod.transport import payment_authority

    if asset not in X402_ASSET_UNITS:
        raise ValueError(f"UsePod x402 pays in USDC or SOL; {asset or 'no asset'} is not an x402 asset")
    unit, decimals = X402_ASSET_UNITS[asset]
    models, routes = _routing_facts()
    if not models:
        raise ValueError(_NO_ROUTES)
    authority = payment_authority()
    facts_provider = getattr(authority, "wallet_facts", None)
    networks = tuple(str(item) for item in (getattr(authority, "networks", ()) or ()))
    if facts_provider is None or not networks:
        raise ValueError(
            "the x402 lane needs a wallet that can pay now: turn Crypto on and finish a Crypto Pilot wallet on a network "
            "UsePod documents for x402 (Solana Mainnet) before spending can be consented to"
        )
    network = networks[0]
    try:
        wallet = facts_provider(network=network, asset=asset, atomic_unit=unit)
    except Exception as exc:
        # the wallet's own typed refusal (unit, payer, balance read, mint), named by its code only
        raise ValueError(f"the wallet could not describe this payment ({getattr(exc, 'code', '') or type(exc).__name__})") from None
    return {
        "account": str(wallet.payer_account),
        "account_kind": "x402_payer_wallet",
        "asset": asset,
        "unit": unit,
        "decimals": decimals,
        "network": network,
        "origin": configured_origin(),
        "models": models,
        "routes": routes,
        "per_call_atomic": int(per_call_atomic),
        "max_total_atomic": int(max_total_atomic),
        "expiry_epoch": float(expiry_epoch),
        "x402": {
            "network": network,
            "asset": asset,
            "unit": unit,
            "decimals": decimals,
            "payer_account": str(wallet.payer_account),
            "fee_network": str(wallet.fee_network),
            "fee_asset": str(wallet.fee_asset),
            "fee_decimals": int(wallet.fee_decimals),
            "fee_max_atomic": int(wallet.fee_max_atomic),
            "authority": str(getattr(authority, "label", "") or ""),
            # observed when proposed, shown to the owner; never authority (liquidity is checked at reservation)
            "observed_principal_balance_atomic": wallet.principal_balance_atomic,
            "observed_fee_balance_atomic": wallet.fee_balance_atomic,
        },
    }


def _proposal_facts(*, per_call_atomic: int, max_total_atomic: int, expiry_epoch: float, asset: str = "USDC", all_models: bool = False) -> dict[str, Any]:
    from core.usepod import discovery
    from core.usepod.lane import load_lane_preference
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    preference, _error = load_lane_preference()
    if str(preference.transport_mode) == "x402":
        return _x402_proposal_facts(per_call_atomic=per_call_atomic, max_total_atomic=max_total_atomic, expiry_epoch=expiry_epoch, asset=asset)
    if asset != "USDC":
        raise ValueError("the prepaid lane spends the UsePod account's USDC balance; SOL is paid only through accountless x402")
    credential = discovery.resolve_credential()
    if credential is None:
        raise ValueError("no UsePod credential is configured; save one under API Keys first")
    models, routes = _routing_facts()
    if not models and not all_models:
        raise ValueError(_NO_ROUTES)
    return {
        "account": credential.fingerprint,
        "account_kind": "usepod_prepaid_token_balance",
        "asset": "USDC",
        "network": USEPOD_ACCOUNT_NETWORK,
        "origin": credential.origin,
        "models": models,
        "routes": routes,
        "per_call_atomic": int(per_call_atomic),
        "max_total_atomic": int(max_total_atomic),
        "expiry_epoch": float(expiry_epoch),
    }


def propose_spend_grant(
    *,
    per_call_atomic: int,
    max_total_atomic: int,
    expiry_epoch: float = 0.0,
    asset: str = "USDC",
    budget_mode: str = "once",
) -> dict[str, Any]:
    """Register the PENDING approval and return its fact sheet + approval id.

    The caller chooses only the ceilings, the expiry and, for accountless x402, which documented asset pays; every
    identity fact is derived here. The caps are validated the way the money law will validate them, so a refusal names
    itself at proposal time rather than after consent."""
    from core.mode_permission_policy import register_external_approval

    if budget_mode not in {"once", "total", "daily"}:
        raise ValueError("budget_mode must be once, total or daily")
    clean_asset = str(asset or "USDC").strip().upper()
    unit = _unit_label(clean_asset)
    per_call = int(per_call_atomic or 0)
    total = int(max_total_atomic or 0)
    if per_call < 1 or total < 1:
        raise ValueError(f"per-call and aggregate ceilings must be positive integer {unit}")
    if per_call > total:
        raise ValueError("the per-call ceiling cannot exceed the aggregate ceiling")
    now = time.time()
    expires = float(expiry_epoch or (now + _DEFAULT_LIFETIME_SECONDS)) if budget_mode == "once" else 0.0
    if expires and expires <= now:
        raise ValueError("expiry must be in the future")
    if expires > now + MAX_GRANT_LIFETIME_SECONDS:
        raise ValueError("a grant may run at most 30 days ahead")
    facts = _proposal_facts(per_call_atomic=per_call, max_total_atomic=total, expiry_epoch=expires, asset=clean_asset, **({"all_models": True} if budget_mode != "once" else {}))
    if budget_mode != "once":
        if facts.get("x402"):
            raise ValueError("provider-wide budgets apply to prepaid UsePod credit; wallet payments keep their approval")
        facts.update(budget_mode=budget_mode, models=[], routes=[])
    x402 = dict(facts.get("x402") or {})
    if x402:
        account_line = f"payer wallet {facts['account']}"
        fee_unit = "lamports" if str(x402["fee_asset"]) == "SOL" else f"{x402['fee_asset']} atomic units"
        fee = f", plus at most {x402['fee_max_atomic']} {fee_unit} network fee"
    else:
        account_line, fee = f"prepaid account {facts['account']}", ""
    effect = (
        f"Authorizes UsePod on this prepaid account across ALL models and chats: up to {total / 1000000:g} USDC "
        + ("every 24 hours, renewing automatically while enabled" if budget_mode == "daily" else "in total, until used or disabled")
        + f", at most {per_call / 1000000:g} USDC per request. Replaces previous budgets for this account. Price limits still apply."
    ) if budget_mode != "once" else (
        f"Authorizes ONE paid UsePod inference call up to {per_call} {unit} "
        f"(aggregate ceiling {total} {unit}){fee}, until "
        f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(expires))}. Repeat spending needs repeat consent."
    )
    approval_id = register_external_approval(
        {
            "task_id": "settings:usepod",
            "intent": APPROVAL_INTENT,
            "action": "Approve UsePod spending",
            "affected_resources": [
                account_line,
                f"asset {facts['asset']} on {facts['network']}",
                *(f"model {model}" for model in facts["models"]),
            ],
            "expected_side_effects": effect,
            "reversible": True,
            "scope_options": ["once"],
            "spend_grant_spec": facts,
            "expires_at": now + 86400,
        }
    )
    return {"approval_id": approval_id, "facts": facts}


def _approval_entry(approval_id: str) -> dict[str, Any] | None:
    from core.mode_permission_policy import _APPROVALS, _LOCK, _ensure_approvals_restored

    with _LOCK:
        _ensure_approvals_restored()
        entry = _APPROVALS.get(str(approval_id or ""))
        return dict(entry) if isinstance(entry, dict) else None


def _latest_spend_entry() -> tuple[str, dict[str, Any]] | None:
    """The most recent spend-consent approval entry (any status), newest first."""
    from core.mode_permission_policy import _APPROVALS, _LOCK, _ensure_approvals_restored

    with _LOCK:
        _ensure_approvals_restored()
        best: tuple[float, tuple[str, dict[str, Any]]] | None = None
        for entry in _APPROVALS.values():
            if not isinstance(entry, dict) or str(entry.get("intent") or "") != APPROVAL_INTENT:
                continue
            raised = float(entry.get("expires_at") or 0)
            if best is None or raised >= best[0]:
                best = (raised, (str(entry.get("approval_id") or ""), entry))
        return best[1] if best else None


def pending_spend_approval() -> dict[str, Any] | None:
    """The ACTIONABLE consent: a proposal awaiting the operator's decision, or one the operator
    already ALLOWED whose grant has not been minted yet (the confirm step survives a refresh or
    restart between Allow and mint -- the consent is not lost at the state boundary). Denied,
    expired and minted consents are not actionable and answer None here; spend_consent_state()
    carries their truthful states for the surface."""
    state = spend_consent_state()
    if state and state["state"] in {"pending", "approved"}:
        return {"approval_id": state["approval_id"], "facts": state["facts"], "status": state["state"]}
    return None


def spend_consent_state() -> dict[str, Any] | None:
    """The truthful state model for Settings/discovery:

    pending  -- awaiting the operator's Allow/Deny
    approved -- the operator allowed it; the grant is not minted yet (confirm continues it)
    denied   -- the operator refused it; nothing minted. Denial stays denial, expired or not
    expired  -- EITHER deadline passed before the mint: the approval's own lifetime or the
                recorded economic expiry. The continuation is dead; fresh spending needs a NEW
                proposal and a NEW explicit operator decision. Never renewed or extended here
    minted   -- consumed: exactly one grant exists (its own state tells whether it is still
                active or revoked); a replay of confirm returns that same grant. An expired or
                revoked EXISTING grant stays a minted record with its grant state -- it is
                never turned back into a mint opportunity
    invalid  -- the entry's recorded facts are missing: not usable consent, not actionable

    Both deadlines are judged against ONE clock read, and expiry takes precedence over
    "approved": an allowed-but-past-deadline consent is dead, not actionable.
    """
    found = _latest_spend_entry()
    if found is None:
        return None
    approval_id, entry = found
    status = str(entry.get("status") or "pending")
    facts = dict(entry.get("spend_grant_spec") or {})
    now = time.time()
    minted = _existing_grant_for_approval(approval_id)
    approval_expired = float(entry.get("expires_at") or 0) <= now
    economic_expired = bool(facts) and facts.get("budget_mode", "once") == "once" and float(facts.get("expiry_epoch") or 0) <= now
    if minted is not None:
        state = "minted"
    elif status == "denied":
        state = "denied"
    elif not facts:
        state = "invalid"
    elif approval_expired or economic_expired:
        state = "expired"
    elif status == "approved":
        state = "approved"
    else:
        state = "pending"
    return {
        "state": state,
        "approval_id": approval_id,
        "facts": facts,
        "grant": minted,
        "expected_side_effects": str(entry.get("expected_side_effects") or ""),
    }


def _existing_grant_for_approval(approval_id: str) -> dict[str, Any] | None:
    from core.effect_budget_money import grant_headroom, money_grants

    for grant in money_grants(active_only=False):
        if str((grant.spec or {}).get("approval_ref") or "") == str(approval_id):
            return {"grant_id": grant.grant_id, "state": grant.state, "headroom": grant_headroom(grant.grant_id), "kind": grant.spec["kind"], "renews_at": (float(grant.spec.get("period_anchor") or 0) + (int((time.time()-float(grant.spec.get("period_anchor") or 0))//86400)+1)*86400) if grant.spec.get("renewal_seconds") else None, "expired": bool((grant.spec or {}).get("expires_epoch")) and float(grant.spec["expires_epoch"]) <= time.time()}
    return None


def prepaid_spend_readiness(model_id: str) -> dict[str, Any]:
    """Read-only composer preflight, never authorization or a reservation.

    Checks the real account, model, expiry and remaining grant envelope. Dispatch still checks
    the actual price, route, scope, liquidity and races inside the money-law transaction.
    Wallet payments keep their separate wallet approval flow.
    """
    from core.effect_budget_money import grant_headroom, money_grants
    from core.usepod import discovery
    from core.usepod.lane import load_lane_preference

    preference, error = load_lane_preference()
    if error:
        return {"available": False, "state": "unavailable"}
    if str(preference.transport_mode) != "prepaid_token":
        return {"available": True, "state": "wallet_approval_required"}
    credential = discovery.resolve_credential()
    if credential is None:
        return {"available": False, "state": "no_account"}
    now = time.time()
    states = set()
    for grant in money_grants(active_only=False):
        spec = grant.spec or {}
        if spec.get("provider_id") != "usepod" or "inference_prepaid" not in spec.get("operation_kinds", ()):
            continue
        if spec.get("provider_account") != credential.fingerprint:
            continue
        if spec.get("models") and model_id not in spec["models"]:
            continue
        if grant.state == "revoked":
            states.add("revoked")
        elif spec.get("expires_epoch") and float(spec["expires_epoch"]) <= now:
            states.add("expired")
        elif grant.state != "active":
            states.add("unavailable")
        else:
            headroom = grant_headroom(grant.grant_id)
            if headroom is None:
                states.add("unavailable")
            elif headroom["operations_left"] == 0 or headroom["principal_left_atomic"] <= 0:
                states.add("spent")
            else:
                return {"available": True, "state": "available", **({"budget_scope": "provider"} if spec.get("kind") == "provider_budget" else {})}
    state = next((x for x in ("revoked", "expired", "spent", "unavailable") if x in states), "missing")
    return {"available": False, "state": state}


def confirm_spend_grant(approval_id: str) -> dict[str, Any]:
    """Mint the grant the operator approved. Only after an explicit ALLOW through the trusted
    approval door, only from the recorded facts, and idempotently per approval."""
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    clean = str(approval_id or "").strip()
    existing = _existing_grant_for_approval(clean)
    if existing is not None:
        return {"ok": True, "idempotent": True, **existing}
    entry = _approval_entry(clean)
    if entry is None or str(entry.get("intent") or "") != APPROVAL_INTENT:
        raise PermissionError("no such spend approval")
    status = str(entry.get("status") or "")
    if status == "pending":
        raise PermissionError("the spend approval is still awaiting the operator's decision")
    if status != "approved":
        raise PermissionError(f"the spend approval was {status}; nothing was minted")
    facts = dict(entry.get("spend_grant_spec") or {})
    if not facts:
        raise PermissionError("the approval carries no grant facts")
    if float(entry.get("expires_at") or 0) <= time.time():
        raise PermissionError("the approval expired; propose it again")
    operator = grant_operator_budget_authority(note=f"usepod spend approval {clean}")
    # Legacy and wallet consents remain single-payment. Only an explicitly reviewed prepaid
    # provider budget can span models/chats and renew. The consent_id consumes either kind
    # exactly once inside the mint transaction, including concurrent confirms and restarts.
    consent_id = f"{APPROVAL_INTENT}:{clean}"
    x402_facts = dict(facts.get("x402") or {})
    if x402_facts:
        # The x402 lane: the wallet authority's own verified network, payer account and fee
        # asset are facts the operator consents TO, captured at proposal time.
        grant = grant_money_authority(
            operator,
            MoneyGrantSpec(
                kind="single_payment",
                operation_kinds=("inference_x402",),
                provider_id="usepod",
                asset=AssetIdentity(network=str(x402_facts["network"]), asset=str(x402_facts.get("asset") or "USDC"), decimals=int(x402_facts.get("decimals") or 6)),
                max_total_atomic=int(facts["max_total_atomic"]),
                per_operation_max_atomic=int(facts["per_call_atomic"]),
                models=tuple(facts.get("models") or ()),
                routes=tuple(facts.get("routes") or ()),
                network=str(x402_facts["network"]),
                payer_account=str(x402_facts["payer_account"]),
                fee_asset=AssetIdentity(
                    network=str(x402_facts["fee_network"]),
                    asset=str(x402_facts["fee_asset"]),
                    decimals=int(x402_facts["fee_decimals"]),
                ),
                max_fee_total_atomic=int(x402_facts["fee_max_atomic"]),
                per_operation_max_fee_atomic=int(x402_facts["fee_max_atomic"]),
                expires_epoch=float(facts["expiry_epoch"]),
                credit_liquidity="not_required",
                approval_ref=clean,
                note=f"Settings x402 spend approval {clean} (synthetic funds in tests)",
            ),
            consent_id=consent_id,
        )
        return {"ok": True, "idempotent": False, "grant_id": grant.grant_id, "state": grant.state}
    grant = grant_money_authority(
        operator,
        MoneyGrantSpec(
            kind="provider_budget" if facts.get("budget_mode") in {"total", "daily"} else "single_payment",
            renewal_seconds=86400 if facts.get("budget_mode") == "daily" else 0,
            operation_kinds=("inference_prepaid",),
            provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=int(facts["max_total_atomic"]),
            per_operation_max_atomic=int(facts["per_call_atomic"]),
            models=tuple(facts.get("models") or ()),
            routes=tuple(facts.get("routes") or ()),
            expires_epoch=float(facts["expiry_epoch"]),
            provider_account=str(facts["account"]),
            credit_liquidity="required",
            approval_ref=clean,
            note=f"Settings spend approval {clean} (synthetic funds in tests)",
        ),
        consent_id=consent_id,
    )
    return {"ok": True, "idempotent": False, "grant_id": grant.grant_id, "state": grant.state}


__all__ = [
    "APPROVAL_INTENT",
    "X402_ASSET_UNITS",
    "confirm_spend_grant",
    "pending_spend_approval",
    "propose_spend_grant",
    "spend_consent_state",
]
