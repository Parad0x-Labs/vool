"""The production UsePod monetary authority: one liability, on VOOL's money law.

This connects the UsePod lane's monetary boundary (:mod:`core.usepod.monetary`) to the reviewed
atomic money authority (:mod:`core.effect_budget_money`, task 03). It creates no ledger and no
second reservation: every reservation, claim, dispatch record, settlement and release below is a
call into the ONE money law, keyed by the UsePod operation id.

Boundary ownership (one liability, one owner per transition):

* ``reserve``   -- :func:`effect_budget_money.reserve_liability`, called by the UsePod adapter
                   BEFORE any byte leaves. Requires an operator-minted grant and a fresh verified
                   provider liquidity observation; refusals carry the money law's codes.
* ``claim``     -- :func:`effect_budget_money.claim_dispatch`, persisted BEFORE the transport
                   writes the request and, for x402, BEFORE ``obtain_proof`` can sign or spend.
                   ``mark_dispatched`` is never a substitute for this claim.
* ``mark_dispatched`` -- :func:`effect_budget_money.record_dispatched`, AFTER the request left.
* ``settle``    -- :func:`effect_budget_money.settle_liability` with provider usage evidence:
                   reported usage priced at the approved ceiling settles both prepaid flows as an
                   exact actual (deterministic arithmetic over provider-reported tokens); usage
                   the provider did not report stays BOUNDED at the liability maximum, never zero.
                   An x402 surplus is recorded as provider credit, never a wallet refund.
* ``retain_unknown`` -- :func:`effect_budget_money.record_unknown`; only external evidence or a
                   settlement may later close it.
* ``release_unsent`` -- :func:`effect_budget_money.record_unsent` with a CLAIMANT proof, and only
                   from the adapter's proven-before-send refusal sites; never from an exception
                   name alone. A reservation never claimed is released unclaimed.

Money is integer atomic units (USDC microunits; SOL lamports for an x402 fee rail). No floats.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from core.usepod.monetary import (
    MonetaryAuthorityRefusedError,
    MonetaryAuthorityUnavailableError,
    MonetaryReservation,
    ProviderLiability,
    SettlementEvidence,
)

AUTHORITY_LABEL = "effect_budget_money:v1"

#: The prepaid account's credit asset: USDC on UsePod's internal account ledger, 1e6 atomic per
#: unit (microunits), matching the marketplace feed's integer prices and the balance header.
USEPOD_ACCOUNT_NETWORK = "usepod:account"
USDC_DECIMALS = 6
#: Documented atomic units an x402 option may name, mapped to their asset decimals. An unknown
#: unit is refused, never guessed. ``lamport`` is the unit the transport names for a SOL option
#: (core.usepod.transport.ATOMIC_UNIT_BY_ASSET); ``sol_lamport`` stays for liabilities recorded under it.
X402_UNIT_DECIMALS = {"usdc_microunit": 6, "lamport": 9, "sol_lamport": 9}

OPERATION_PREPAID = "inference_prepaid"
OPERATION_X402 = "inference_x402"

#: Prepaid-proxy ADMISSION refusals: the proxy's own typed answer, to these exact bytes, that the
#: request was refused before any upstream service. Documented basis (core.usepod.descriptor):
#: an unknown token answers 401, and a token with no balance "is rejected before any upstream
#: call"; billing meters actual token usage from a served response stream, so a request refused
#: at admission metered nothing against the prepaid account. These are the ONLY codes whose
#: no-charge reading the runtime asserts -- an HTTP status alone is not evidence (a 402 can be an
#: x402 challenge, an upstream relay, or a proxy gate), and every other refusal (throttle,
#: upstream failure, interrupted stream, timeout after send) keeps its hold until real evidence
#: resolves it.
ADMISSION_REFUSAL_NO_CHARGE_CODES = frozenset({"token_rejected", "payment_or_balance_required"})
ADMISSION_REFUSAL_STATUS_BY_CODE = {"token_rejected": "401", "payment_or_balance_required": "402"}

#: How long a reserved price bound stays authorizable: the marketplace snapshot TTL plus slack.
PRICE_WINDOW_SECONDS = 180.0
#: The money law accepts provider liquidity observations up to 900 s old; older is unverified.
#: The adapter obtains a fresh observation at the dispatch boundary (core.usepod.discovery.observe_balance,
#: reusing one at most BALANCE_REUSE_SECONDS old), so this window is judged, never renewed by stamping.
LIQUIDITY_FRESH_SECONDS = 900.0
_LIQUIDITY_MAX_AGE = LIQUIDITY_FRESH_SECONDS

_REFUSAL_PREFIX = "MONEY_"


def _usdc_asset() -> Any:
    from core.effect_budget_money import AssetIdentity

    return AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=USDC_DECIMALS)


def _option_asset(liability: ProviderLiability) -> Any:
    """The AssetIdentity for an x402 option's asset, from its own network and atomic unit."""
    from core.effect_budget_money import AssetIdentity

    decimals = X402_UNIT_DECIMALS.get(str(liability.unit).strip().lower())
    if decimals is None:
        raise MonetaryAuthorityRefusedError(
            f"{_REFUSAL_PREFIX}INVALID_REQUEST",
            f"the x402 option's unit {liability.unit!r} has no known decimal scale; refusing to guess",
        )
    return AssetIdentity(network=str(liability.network), asset=str(liability.asset), decimals=decimals)


def _wrap_refusal(exc: BaseException) -> BaseException:
    """Translate a money-law refusal into the UsePod monetary boundary's typed error."""
    code = str(getattr(exc, "code", "") or "")
    detail = str(exc)
    if code.startswith(_REFUSAL_PREFIX):
        return MonetaryAuthorityRefusedError(code, detail)
    return MonetaryAuthorityUnavailableError(detail or type(exc).__name__)


def active_grants(operation_kind: str = OPERATION_PREPAID) -> list[dict[str, Any]]:
    """Active grants that may authorize a UsePod liability of this operation kind. A read, never
    a widening. x402 grants are listed exactly like prepaid ones -- the empty x402 candidate set
    was the integration gap: installing a wallet alone could never activate the lane."""
    try:
        from core.effect_budget_money import money_grants

        rows = []
        for grant in money_grants(active_only=True):
            spec = dict(grant.spec or {})
            if str(spec.get("provider_id") or "") != "usepod":
                continue
            if operation_kind not in tuple(spec.get("operation_kinds") or ()):
                continue
            rows.append({"grant_id": grant.grant_id, "spec": spec, "state": grant.state})
        return rows
    except Exception:
        return []


def active_prepaid_grants() -> list[dict[str, Any]]:
    """Kept name for the prepaid listing the pick gate and Settings read."""
    return active_grants(OPERATION_PREPAID)


def liability_route(liability: ProviderLiability) -> str:
    """The route identity a grant binds: the approval's permitted classes, sorted and
    '+'-joined (a grant for an auto policy names both classes explicitly)."""
    basis = liability.basis or {}
    classes = basis.get("route_classes")
    if isinstance(classes, (list, tuple)) and classes:
        return "+".join(sorted(str(item) for item in classes))
    return str(basis.get("route_class") or "")


def _x402_asset_key(liability: ProviderLiability) -> str:
    """The asset key an x402 liability is denominated in, or "" when its unit has no known scale."""
    try:
        return str(_option_asset(liability).key)
    except MonetaryAuthorityRefusedError:
        return ""


def _grant_shape_matches(spec: dict[str, Any], liability: ProviderLiability, per_operation: int) -> bool:
    models = tuple(spec.get("models") or ())
    routes = tuple(spec.get("routes") or ())
    if models and liability.model_id not in models:
        return False
    if routes and liability_route(liability) not in routes:
        return False
    if liability.transport_mode == "x402" and str(spec.get("asset_key") or "") != _x402_asset_key(liability):
        # A per-operation maximum is a number in the grant's own asset. It is compared only with an
        # amount in that same asset: a consent in µUSDC never covers a payment in lamports.
        return False
    return per_operation >= liability.max_amount_atomic


def _grant_headroom(grant_id: str) -> dict[str, Any] | None:
    try:
        from core.effect_budget_money import grant_headroom

        return grant_headroom(grant_id)
    except Exception:
        return None


def x402_payment_bounds(*, model_id: str, route: str, usdc_route_bound_atomic: int) -> tuple[tuple[str, ...], dict[str, int]]:
    """The assets an accountless x402 call may pay in, and the local bound for each, from the operator's own
    x402 consents for this model and route.

    USDC is bounded by the approved route's price ceiling and by the consent. SOL is bounded only by a
    consent denominated in lamports -- a µUSDC price ceiling is never converted into a lamport bound. A consent
    that already authorized its operations, or spent its principal or fee authority (the law's own usage
    arithmetic), offers nothing while another consent can still pay: a used USDC consent never shadows a fresh
    SOL one. When every matching consent is used, they are still named, so the reservation refuses with the
    law's exhaustion code. Without any x402 consent the lane offers USDC under the route bound, and the
    reservation refuses with the law's own code. A read, never a widening: the reservation still decides.
    """
    bounds: dict[str, int] = {}
    spent: dict[str, int] = {}
    for row in active_grants(OPERATION_X402):
        spec = row["spec"]
        models = tuple(spec.get("models") or ())
        routes = tuple(spec.get("routes") or ())
        if (models and model_id not in models) or (routes and route not in routes):
            continue
        parts = str(spec.get("asset_key") or "").split("|")
        if len(parts) != 3:
            continue
        try:
            per_operation = int(str(spec.get("per_operation_max_atomic") or "0"))
        except ValueError:
            continue
        asset, decimals = parts[1], parts[2]
        if asset == "USDC" and decimals == str(USDC_DECIMALS):
            bound = min(int(usdc_route_bound_atomic), per_operation)
        elif asset == "SOL" and decimals == str(X402_UNIT_DECIMALS["lamport"]):
            bound = per_operation
        else:
            continue
        if bound <= 0:
            continue
        headroom = _grant_headroom(str(row["grant_id"]))
        if headroom is not None and (
            headroom["operations_left"] == 0 or headroom["fees_left_atomic"] == 0 or int(headroom["principal_left_atomic"]) <= 0
        ):
            spent[asset] = max(spent.get(asset, 0), bound)
            continue
        bounds[asset] = max(bounds.get(asset, 0), bound)
    chosen = bounds or spent
    if not chosen:
        return ("USDC",), {"USDC": int(usdc_route_bound_atomic)}
    order = tuple(asset for asset in ("USDC", "SOL") if asset in chosen)
    return order, {asset: chosen[asset] for asset in order}


def candidate_grants(liability: ProviderLiability, *, operation_kind: str) -> list[dict[str, Any]]:
    """Active grants that could cover this liability, best first.

    The authoritative check runs inside ``reserve_liability``'s transaction; this only orders the
    candidates. :func:`grant_refusal_code` names WHY none applied, using the grant's own state
    rather than a generic refusal.
    """
    try:
        from core.effect_budget_money import _atomic_value
    except ImportError:  # pragma: no cover - the merged tree always has it
        return []
    candidates = []
    for row in active_grants(operation_kind):
        spec = row["spec"]
        try:
            per_operation = _atomic_value(spec.get("per_operation_max_atomic"), what="grant per-operation maximum")
        except Exception:
            continue
        if _grant_shape_matches(spec, liability, per_operation):
            candidates.append((row, per_operation, str(spec.get("credit_liquidity") or "required") == "not_required"))
    # Largest per-operation authority first; among equals, a grant whose operator explicitly took
    # the liquidity duty wins, so a required-liquidity grant never shadows it.
    candidates.sort(key=lambda item: (item[2], item[1]), reverse=True)
    return [item[0] for item in candidates]


def find_grant(liability: ProviderLiability, *, operation_kind: str) -> dict[str, Any] | None:
    """The first candidate grant, kept for callers that only need the pick."""
    grants = candidate_grants(liability, operation_kind=operation_kind)
    return grants[0] if grants else None


def grant_refusal_code(liability: ProviderLiability, *, operation_kind: str) -> str:
    """The most precise refusal code for a liability no ACTIVE grant covers: a matching REVOKED
    grant names revocation, an EXPIRED one expiry, a too-small one exhaustion; only a grant that
    never existed reads as AUTHORITY_INVALID."""
    from core.effect_budget_money import _atomic_value, money_grants

    saw_revoked = saw_expired = saw_small = False
    for grant in money_grants(active_only=False):
        spec = dict(grant.spec or {})
        if str(spec.get("provider_id") or "") != "usepod":
            continue
        if operation_kind not in tuple(spec.get("operation_kinds") or ()):
            continue
        try:
            per_operation = _atomic_value(spec.get("per_operation_max_atomic"), what="grant per-operation maximum")
        except Exception:
            continue
        # NAMING only, never authorization: provider, kind, model and size decide whether a
        # grant was FOR this shape. Routes are deliberately not compared here -- the router-level
        # probe cannot know the turn's route classes, and a naming heuristic must not read a
        # route difference as "no grant ever existed". The authoritative route check runs inside
        # reserve_liability's transaction.
        if liability.transport_mode == "x402" and str(spec.get("asset_key") or "") != _x402_asset_key(liability):
            continue  # a consent in another asset is not a grant for this payment at all
        names_this = not (spec.get("models") or ()) or liability.model_id in tuple(spec.get("models") or ())
        if str(grant.state) == "revoked" and names_this:
            saw_revoked = True
        elif float(spec.get("expires_epoch") or 0.0) and float(spec.get("expires_epoch") or 0.0) < time.time() and names_this:
            saw_expired = True
        elif not names_this or per_operation < liability.max_amount_atomic:
            saw_small = True
    if saw_revoked:
        return "MONEY_AUTHORITY_REVOKED"
    if saw_expired:
        return "MONEY_AUTHORITY_EXPIRED"
    if saw_small:
        return "MONEY_AUTHORITY_EXHAUSTED"
    return "MONEY_AUTHORITY_INVALID"


def _record_provider_liquidity(liability: ProviderLiability) -> tuple[str, str]:
    """Record the credential's observed provider balance as the money law's liquidity evidence.

    The observation is what the ONE balance door last recorded for this credential
    (``core.usepod.discovery.observe_balance`` / ``balance_observation``: a read of
    ``/proxy/<token>/balance``); its true observation time rides along, so a stale balance stays
    stale here and the money law refuses rather than authorizing against old money. Returns
    ``("", "")`` when the observation was recorded, else the refusal code and a detail naming
    exactly which verification is missing -- the actionable blocker, never a guess at funds.
    """
    from core.usepod import discovery

    credential = discovery.resolve_credential()
    fingerprint = liability.account_ref or (credential.fingerprint if credential else "")
    code = f"{_REFUSAL_PREFIX}LIQUIDITY_UNVERIFIED"
    if not fingerprint:
        return code, "no UsePod credential is bound, so no account balance can be observed"
    observation = discovery.balance_observation(fingerprint)
    state = str(observation.get("state") or "")
    if state == "not_observed":
        return code, "the UsePod account balance has never been observed for this credential"
    if state == "unavailable":
        status = observation.get("http_status")
        where = f"HTTP {status}" if isinstance(status, int) else str(observation.get("error_code") or "no response")
        return code, f"the last UsePod balance read failed ({where})"
    if state != "reported":
        return code, f"the last UsePod balance read was not a readable balance ({state})"
    observed_atomic = observation.get("usdc_balance_microunits")
    observed_at = observation.get("observed_at")
    if isinstance(observed_atomic, bool) or not isinstance(observed_atomic, int) or not isinstance(observed_at, (int, float)):
        return code, "the recorded UsePod balance observation is malformed"
    age = max(0.0, time.time() - float(observed_at))
    if age > _LIQUIDITY_MAX_AGE:
        return code, f"the last UsePod balance observation is {int(age)} s old; the money law accepts at most {int(_LIQUIDITY_MAX_AGE)} s"
    from core.effect_budget_money import record_liquidity_observation

    record_liquidity_observation(
        account=fingerprint,
        asset=_usdc_asset(),
        balance_atomic=int(observed_atomic),
        source="provider:usepod_balance",
        verified=True,
        observed_epoch=float(observed_at),
    )
    return "", ""


def _record_wallet_liquidity(liability: ProviderLiability) -> str:
    """Record the wallet authority's own principal and fee observations (from the wallet facts
    the transport captured) as the money law's wallet liquidity evidence. Each asset is checked
    on its own; a missing or insufficient one refuses before any reservation or proof."""
    from core.effect_budget_money import AssetIdentity, record_liquidity_observation

    basis = dict(liability.basis or {})
    principal = basis.get("principal_balance_atomic")
    if not isinstance(principal, int) or principal < liability.max_amount_atomic:
        return f"{_REFUSAL_PREFIX}LIQUIDITY_INSUFFICIENT"
    label = str(basis.get("wallet_authority_label") or "wallet")
    record_liquidity_observation(
        account=liability.account_ref,
        asset=_option_asset(liability),
        balance_atomic=principal,
        source=f"wallet:{label}",
        verified=True,
    )
    fee_max = basis.get("fee_max_atomic")
    if isinstance(fee_max, int) and fee_max > 0 and basis.get("fee_asset"):
        fee_balance = basis.get("fee_balance_atomic")
        if not isinstance(fee_balance, int) or fee_balance < fee_max:
            return f"{_REFUSAL_PREFIX}LIQUIDITY_INSUFFICIENT"
        record_liquidity_observation(
            account=liability.account_ref,
            asset=AssetIdentity(
                network=str(basis.get("fee_network") or liability.network),
                asset=str(basis["fee_asset"]),
                decimals=int(basis.get("fee_decimals") or 0),
            ),
            balance_atomic=fee_balance,
            source=f"wallet:{label}",
            verified=True,
        )
    return ""


def _turn_identity(liability: ProviderLiability) -> Any:
    """The owning turn's identity when the dispatch runs inside one, from the same authority the
    provider seal uses (the effect ledger). Outside a turn (a unit test, a background caller) the
    liability names only its provider -- and a grant bound to a task or session then honestly
    refuses it."""
    from core.effect_budget_money import MoneyIdentity

    identity = MoneyIdentity(provider_id=liability.provider_id)
    try:
        from core.effect_gateway import current_effect_ledger

        ledger = current_effect_ledger()
        if ledger is None:
            return identity
        owned = dict(ledger._budget_identity())
    except Exception:
        return identity
    from dataclasses import replace as _replace

    return _replace(
        identity,
        request_id=str(owned.get("request_id") or ""),
        turn_id=str(owned.get("turn_id") or ""),
        task_id=str(owned.get("task_id") or ""),
        agent_id=str(owned.get("agent_id") or ""),
        session_id=str(owned.get("session_id") or ""),
        project_key=str(owned.get("project_key") or ""),
    )


def _liability_request(liability: ProviderLiability, *, grant_id: str) -> Any:
    """Map the UsePod liability onto the money law's request. Public correlation ids only."""
    from core.effect_budget_money import LiabilityRequest, MoneyLine

    if liability.transport_mode == "x402":
        asset = _option_asset(liability)
        payer = str(liability.account_ref or "")
        provider_account = str((liability.basis or {}).get("pay_to") or payer or "usepod:x402")
        lines = [MoneyLine("wallet_outflow", asset, liability.max_amount_atomic, payer or "x402:payer")]
        lines.append(MoneyLine("inference_expense", asset, liability.max_amount_atomic, provider_account))
        basis = dict(liability.basis or {})
        fee_max = basis.get("fee_max_atomic")
        if isinstance(fee_max, int) and fee_max > 0 and basis.get("fee_asset"):
            from core.effect_budget_money import AssetIdentity

            fee_asset = AssetIdentity(
                network=str(basis.get("fee_network") or liability.network),
                asset=str(basis["fee_asset"]),
                decimals=int(basis.get("fee_decimals") or 0),
            )
            # The fee line carries its OWN asset and account: principal liquidity never covers it.
            lines.append(MoneyLine("network_fee", fee_asset, fee_max, payer))
        fee_terms = _service_fee_terms(liability)
        if fee_terms is not None:
            # The native DNA service fee: its whole-unit CEILING is held in the paid asset against the payer so the
            # consent and the liquidity check include it; the exact fraction accrues at settlement in the money law.
            from core.service_fee_ledger import fee_ceiling_atomic

            lines.append(MoneyLine("service_fee", asset, fee_ceiling_atomic(liability.max_amount_atomic, int(fee_terms["rate_bps"])), payer or "x402:payer"))
        lines = tuple(lines)
        operation_kind = OPERATION_X402
    else:
        asset = _usdc_asset()
        account = liability.account_ref or "usepod:prepaid"
        # The equal-maximum pair the money law requires for prepaid: the expense and the debit of
        # the same provider credit are one economic fact stated on both flows.
        lines = (
            MoneyLine("inference_expense", asset, liability.max_amount_atomic, account),
            MoneyLine("provider_credit_debit", asset, liability.max_amount_atomic, account),
        )
        operation_kind = OPERATION_PREPAID
    correlation = {
        "operation_id": liability.operation_id,
        "route_approval_id": liability.route_approval_id,
        "envelope_binding_sha256": liability.envelope_binding_sha256,
    }
    quote_id = str((liability.basis or {}).get("quote_id") or "")
    if quote_id:
        correlation["quote_id"] = quote_id
    if liability.transport_mode == "x402" and fee_terms is not None:
        correlation["fee_policy"] = str(fee_terms["policy_id"])
        correlation["fee_rate_bps"] = str(int(fee_terms["rate_bps"]))
        correlation["fee_treasury"] = str(fee_terms["treasury_owner"])
    from core.effect_budget_money import MoneyIdentity

    return LiabilityRequest(
        operation_id=liability.operation_id,
        operation_kind=operation_kind,
        grant_id=grant_id,
        lines=lines,
        identity=_turn_identity(liability),
        provider_account=account if operation_kind == OPERATION_PREPAID else provider_account,
        model_id=liability.model_id,
        route=liability_route(liability),
        network=liability.network if operation_kind == OPERATION_X402 else "",
        payer_account=payer if operation_kind == OPERATION_X402 else "",
        expires_epoch=time.time() + PRICE_WINDOW_SECONDS,
        correlation=correlation,
        owner_ref="usepod.money_law",
    )


def _service_fee_terms(liability: ProviderLiability) -> dict[str, Any] | None:
    """The DNA service-fee terms of one x402 payment, from the wallet's fee owner: None where no treasury is bound to
    the row; a typed money refusal when the treasury configuration is invalid (nothing is reserved on a bad binding)."""
    if liability.transport_mode != "x402":
        return None
    from core.wallet import dna_fees

    try:
        return dna_fees.fee_terms(str(liability.network), str(liability.asset))
    except Exception as exc:
        raise MonetaryAuthorityRefusedError(
            f"{_REFUSAL_PREFIX}INVALID_REQUEST",
            f"the DNA service fee treasury for {liability.network} is misconfigured ({getattr(exc, 'code', '') or type(exc).__name__}); nothing is reserved",
        ) from None


def service_fee_summary(operation_id: str) -> dict[str, Any] | None:
    """The DNA service-fee facts of one UsePod operation for its receipt: the reserved ceiling, the exact accrual once
    the payment was accepted, and the identity's position. None when the operation carries no fee line."""
    from core.effect_budget_money import liability_for_operation, service_fee_for_liability

    row = liability_for_operation(str(operation_id)) or {}
    liability_id = str(row.get("liability_id") or "")
    if not liability_id:
        return None
    return service_fee_for_liability(liability_id)


@dataclass(frozen=True)
class LawReservation(MonetaryReservation):
    """A reservation ISSUED to one invocation. The claim handle lives on this instance, in a
    one-slot box: the money law's durable row holds the authoritative token, and only the caller
    that presented THIS reservation to ``claim`` can present it back for dispatch records,
    settlement, retention or an unsent proof. A caller that re-reserves the same operation
    idempotently receives its own instance with an EMPTY box -- a liability id is not a claim
    handle, and borrowing another invocation's handle is what the fencing exists to prevent."""

    _claim_box: tuple = ()  # ([token],) after a successful claim; frozen dataclass, mutable slot

    @property
    def claim_token(self) -> str:
        return str(self._claim_box[0]) if self._claim_box else ""


class EffectBudgetMonetaryAuthority:
    """The production authority installed by default once the money law is merged.

    Test doubles keep using :func:`core.usepod.monetary.install_monetary_authority`; their labels
    continue to say what they are. The authority holds NO shared claim state: every transition
    below re-reads the money law's durable rows, and the claim token travels only on the
    reservation instance the claiming invocation holds.
    """

    label = AUTHORITY_LABEL

    # --- the UsePod monetary protocol -----------------------------------------------------------------

    def reserve(self, liability: ProviderLiability) -> MonetaryReservation:
        from core.effect_budget_money import reserve_liability

        operation_kind = OPERATION_PREPAID if liability.transport_mode != "x402" else OPERATION_X402
        if operation_kind == OPERATION_X402:
            refusal = _record_wallet_liquidity(liability)
            if refusal:
                raise MonetaryAuthorityRefusedError(
                    refusal,
                    "the wallet authority's own balance evidence does not cover this operation's "
                    "principal and fee; nothing is reserved or signed",
                )
        grants = candidate_grants(liability, operation_kind=operation_kind)
        if not grants:
            raise MonetaryAuthorityRefusedError(
                grant_refusal_code(liability, operation_kind=operation_kind),
                "no active UsePod spend grant covers this model, route and per-call maximum; "
                "an operator grant (core.effect_budget_money.grant_money_authority) is required "
                "before any paid dispatch",
            )
        # Try each candidate until one reserves: a grant that has spent its own envelope must not
        # shadow a fresh one behind it, and the refusal that survives is the LAST candidate's.
        last_refusal: MonetaryAuthorityRefusedError | None = None
        for grant in grants:
            liquidity_refusal = ""
            liquidity_detail = ""
            if operation_kind == OPERATION_PREPAID:
                credit_liquidity = str((grant["spec"] or {}).get("credit_liquidity") or "required")
                if credit_liquidity != "not_required":
                    liquidity_refusal, liquidity_detail = _record_provider_liquidity(liability)
            if liquidity_refusal:
                last_refusal = MonetaryAuthorityRefusedError(
                    liquidity_refusal,
                    f"{liquidity_detail}; nothing was sent or charged (a debit is never authorized "
                    "against an unverified balance). Check the UsePod token and balance under Settings, then send again",
                )
                continue
            request = _liability_request(liability, grant_id=str(grant["grant_id"]))
            try:
                receipt = reserve_liability(request)
            except MonetaryAuthorityRefusedError:
                raise
            except Exception as exc:  # the money law's typed refusal
                last_refusal = _wrap_refusal(exc)  # type: ignore[assignment]
                continue
            return LawReservation(
                reservation_id=str(receipt.liability_id),
                authority_label=self.label,
                liability=liability,
                granted_at=time.time(),
            )
        # The CODE is the law's own last refusal (what actually blocked this dispatch); when a
        # shape-matching grant is revoked or expired, that fact rides the DETAIL -- it explains
        # the blocker without relabeling an exhaustion as a revocation.
        precise = grant_refusal_code(liability, operation_kind=operation_kind)
        detail = ""
        if last_refusal is not None:
            # The refusal's own text, once: its message already carries the code, and the raise
            # below carries it again as the code -- re-prefixing here read "CODE: CODE: detail".
            detail = str(last_refusal)
            prefix = f"{last_refusal.code}: "
            if detail.startswith(prefix):
                detail = detail[len(prefix):]
            if precise in {"MONEY_AUTHORITY_REVOKED", "MONEY_AUTHORITY_EXPIRED"}:
                detail += f"; a matching UsePod spend grant is also {precise.split('_')[-1].lower()}"
        else:
            detail = "no active UsePod spend grant could authorize this dispatch"
            if precise in {"MONEY_AUTHORITY_REVOKED", "MONEY_AUTHORITY_EXPIRED"}:
                detail = f"the matching UsePod spend grant is {precise.split('_')[-1].lower()}"
        raise MonetaryAuthorityRefusedError(
            last_refusal.code if last_refusal is not None else precise,
            detail,
        )

    def claim(self, reservation: MonetaryReservation, *, executor: str = "usepod_transport") -> Any:
        """Persist the pre-effect dispatch claim. BEFORE the request is written and before any
        payment proof is obtained; ``mark_dispatched`` is not this claim.

        The fencing token is written onto THIS reservation instance only. A refused contender
        (``MONEY_CLAIM_CONFLICT``) receives nothing and must not touch the winner's state; a
        claim refused for revocation, expiry or the freeze has already released the never-sent
        reservation inside the law's own transaction, so no cleanup belongs here either.
        """
        from core.effect_budget_money import claim_dispatch

        liability_id = str(reservation.reservation_id)
        try:
            claim = claim_dispatch(liability_id, executor=executor)
        except Exception as exc:
            raise _wrap_refusal(exc) from None
        if claim.claim_token:
            # The handle is written onto the instance THIS caller presented to the successful
            # claim -- a LawReservation's box, or any reservation with a settable slot. Only a
            # successful claim on this call puts a token anywhere; refused contenders get none.
            import contextlib

            if isinstance(reservation, LawReservation):
                object.__setattr__(reservation, "_claim_box", (claim.claim_token,))
            else:
                with contextlib.suppress(Exception):
                    object.__setattr__(reservation, "claim_token", claim.claim_token)
        return claim

    @staticmethod
    def _claim_token(reservation: MonetaryReservation) -> str:
        """The handle the PRESENTING invocation owns. An instance that never claimed (or a plain
        double reservation) answers empty, and the money law's own fencing then refuses to let it
        touch a claimed liability."""
        return str(getattr(reservation, "claim_token", "") or "")

    @staticmethod
    def _guard_claim_owned(reservation: MonetaryReservation, *, action: str) -> None:
        """The money law lets EVIDENCE close a liability regardless of caller; this seam serves
        the adapter's dispatch lifecycle, where only the claiming invocation ever holds the
        response. While the liability is claim-owned (dispatching/pending), a caller without THIS
        invocation's handle -- a refused contender, an idempotent re-reservation -- must not mark,
        settle, retain or release it, whatever evidence object it constructs. Unknown liabilities
        stay closable by evidence, which is the law's own reconciliation path."""
        from core.effect_budget_money import LIABILITY_DISPATCHING, LIABILITY_PENDING, liability_for_operation

        operation_id = str(reservation.liability.operation_id)
        if getattr(reservation, "claim_token", ""):
            return
        row = liability_for_operation(operation_id)
        state = str((row or {}).get("state") or "")
        if state in (LIABILITY_DISPATCHING, LIABILITY_PENDING):
            raise MonetaryAuthorityRefusedError(
                "MONEY_CLAIM_CONFLICT",
                f"operation {operation_id} is {state} under another invocation's dispatch claim; "
                f"{action} belongs to the claimant",
            )

    def mark_dispatched(self, reservation: MonetaryReservation) -> None:
        from core.effect_budget_money import record_dispatched

        self._guard_claim_owned(reservation, action="the dispatch record")
        try:
            record_dispatched(
                str(reservation.reservation_id),
                self._claim_token(reservation),
                evidence_id=str(reservation.liability.operation_id),
            )
        except Exception as exc:
            raise _wrap_refusal(exc) from None

    def settle(self, reservation: MonetaryReservation, evidence: SettlementEvidence) -> None:
        from core.effect_budget_money import CreditLine, SettlementEvidence as LawEvidence, settle_liability

        liability_id = str(reservation.reservation_id)
        self._guard_claim_owned(reservation, action="settlement")
        # SETTLEMENT TRUTH: only the provider's own exact, operation-bound charge is an ACTUAL.
        # Usage priced at the APPROVED CEILING is an upper bound -- deterministic arithmetic over
        # attested tokens, but a ceiling input, not a bill -- so it NEVER becomes an exact
        # actual: the durable lines stay BOUNDED at their maximum and the narrower estimate
        # rides public_detail for the projection, preserving the cap until exact evidence (or
        # reconciliation) closes it. Absent usage reports the same, with no estimate.
        actuals: dict[str, int] = {}
        public_detail = {
            "outcome": str(evidence.outcome),
            "usage_exceeds_liability_bound": str(bool(evidence.usage_exceeds_liability_bound)),
            "balance_reported": "yes" if evidence.balance_remaining_raw is not None else "no",
        }
        exact = evidence.exact_cost_atomic
        if exact is not None:
            amount = int(exact)
            actuals["inference_expense"] = amount
            if reservation.liability.transport_mode != "x402":
                actuals["provider_credit_debit"] = amount
            public_detail["exact_charge_source"] = str(evidence.exact_cost_state)
        elif evidence.upper_bound_cost_atomic is not None:
            public_detail["usage_priced_at_ceiling_bound_atomic"] = str(int(evidence.upper_bound_cost_atomic))
            public_detail["estimate_basis"] = "usage_priced_at_approved_ceiling_not_a_bill"
        credits = []
        credit_atomic = getattr(evidence, "provider_credit_atomic", None)
        if isinstance(credit_atomic, int) and credit_atomic > 0:
            account = str(getattr(evidence, "provider_credit_account", "") or "usepod:x402")
            credit_asset = _option_asset(reservation.liability) if reservation.liability.transport_mode == "x402" else _usdc_asset()
            credits.append(CreditLine(asset=credit_asset, account=account, amount_atomic=credit_atomic, verified=False))
        law_evidence = LawEvidence(
            evidence_kind="provider_usage_receipt",
            evidence_id=str(evidence.operation_id),
            source="provider",
            actuals=actuals,
            credits=tuple(credits),
            public_detail=public_detail,
        )
        try:
            settle_liability(liability_id, law_evidence, claim_token=self._claim_token(reservation))
        except Exception as exc:
            raise _wrap_refusal(exc) from None

    def retain_unknown(self, reservation: MonetaryReservation, evidence: SettlementEvidence) -> None:
        from core.effect_budget_money import record_unknown

        self._guard_claim_owned(reservation, action="retention")
        try:
            record_unknown(
                str(reservation.reservation_id),
                self._claim_token(reservation),
                reason=f"usepod:{evidence.outcome}:{evidence.detail or 'outcome_unknown'}"[:200],
            )
        except Exception as exc:
            raise _wrap_refusal(exc) from None

    def settle_admission_refusal(
        self,
        reservation: MonetaryReservation,
        *,
        refusal_code: str,
        http_status: int,
    ) -> None:
        """Settle a refused-before-service prepaid operation at its proven actual: zero.

        The evidence is the provider's operation-bound admission refusal -- the typed answer to
        these exact bytes -- carried as a ``provider_refusal_record``, never the HTTP status
        alone and never a balance delta. Only the codes whose pre-service semantics the
        descriptor documents qualify (ADMISSION_REFUSAL_NO_CHARGE_CODES); the wallet-paid x402
        lane has no admission refusal (its money moves at payment and owns the paid-retry path).
        """
        from core.effect_budget_money import (
            EVIDENCE_PROVIDER_REFUSAL_RECORD,
            FLOW_INFERENCE_EXPENSE,
            FLOW_PROVIDER_CREDIT_DEBIT,
            SettlementEvidence as LawEvidence,
            settle_liability,
        )

        code = str(refusal_code or "")
        if code not in ADMISSION_REFUSAL_NO_CHARGE_CODES:
            raise MonetaryAuthorityRefusedError(
                "MONEY_EVIDENCE_INSUFFICIENT",
                f"{code!r} is not an admission refusal whose no-charge basis is documented; the hold stays",
            )
        if str(reservation.liability.transport_mode or "") == "x402":
            raise MonetaryAuthorityRefusedError(
                "MONEY_EVIDENCE_INSUFFICIENT",
                "the x402 lane pays before dispatch; a refusal after payment is the paid-retry path, not an admission refusal",
            )
        self._guard_claim_owned(reservation, action="the admission-refusal settlement")
        evidence = LawEvidence(
            evidence_kind=EVIDENCE_PROVIDER_REFUSAL_RECORD,
            evidence_id=str(reservation.liability.operation_id),
            source="provider",
            actuals={FLOW_INFERENCE_EXPENSE: 0, FLOW_PROVIDER_CREDIT_DEBIT: 0},
            public_detail={
                "refusal_code": code,
                "refusal_status": str(int(http_status)),
                "transport_mode": str(reservation.liability.transport_mode or "prepaid_token"),
            },
        )
        try:
            settle_liability(str(reservation.reservation_id), evidence, claim_token=self._claim_token(reservation))
        except Exception as exc:
            raise _wrap_refusal(exc) from None

    def release_unsent(self, reservation: MonetaryReservation, *, reason: str) -> None:
        """Only proven-unsent sites may call this; the proof kind states WHO proved it."""
        from core.effect_budget_money import UnsentEvidence, record_unsent, release_unclaimed

        liability_id = str(reservation.reservation_id)
        self._guard_claim_owned(reservation, action="the unsent proof")
        clean = str(reason or "").strip()
        proof_kind = (
            "local_refusal_before_send"
            if clean in {"provider_invocation_seal_failed", "provider_invocation_permit_refused"}
            else "connection_not_established"
        )
        claim_token = self._claim_token(reservation)
        try:
            if not claim_token:
                release_unclaimed(liability_id, reason=f"usepod:{clean or 'unclaimed'}"[:200])
            else:
                record_unsent(
                    liability_id,
                    claim_token,
                    evidence=UnsentEvidence(
                        proof_kind=proof_kind,
                        evidence_id=str(reservation.liability.operation_id),
                        source="mechanical",
                    ),
                )
        except Exception as exc:
            raise _wrap_refusal(exc) from None


    def reservation_for_resume(self, liability: ProviderLiability) -> tuple[MonetaryReservation | None, str]:
        """The durable reservation of an operation another invocation left unresolved, and its state now. The instance
        carries NO claim handle: a live claim (dispatching or pending) is never handed to a second invocation, and an
        unknown liability closes only on evidence -- a provider receipt, a chain read -- or the law's reconciliation."""
        from core.effect_budget_money import liability_for_operation, reconcile_money_liabilities

        # Judge dead claimants first: a process that ended while its claim was open leaves that liability unknown here.
        reconcile_money_liabilities(force=True)
        row = liability_for_operation(str(liability.operation_id)) or {}
        liability_id = str(row.get("liability_id") or "")
        if not liability_id:
            return None, ""
        reservation = LawReservation(reservation_id=liability_id, authority_label=self.label, liability=liability, granted_at=time.time())
        return reservation, str(row.get("state") or "")

    def record_chain_confirmation(self, reservation: MonetaryReservation, confirmation: Any) -> dict[str, Any]:
        """Chain evidence for a paid x402 operation: the wallet outflow and the network fee the transaction charged, read
        from the chain by the wallet authority and recorded under the transaction signature.

        Only AFTER the provider's settlement: any settlement evidence settles a held liability, so chain evidence on a
        pending or unknown liability would close an operation whose provider outcome is not proven. The provider receipt
        and the chain receipt stay two evidence items on one liability; a replay is idempotent, and a different amount
        under the same signature is refused."""
        from core.effect_budget_money import LIABILITY_SETTLED, MONEY_STATE_ERROR, liability_for_operation, settle_liability
        from core.effect_budget_money import SettlementEvidence as LawEvidence

        liability = reservation.liability
        if liability.transport_mode != "x402":
            raise MonetaryAuthorityRefusedError(f"{_REFUSAL_PREFIX}INVALID_REQUEST", "chain confirmation belongs to an x402 payment")
        row = liability_for_operation(str(liability.operation_id)) or {}
        if str(row.get("state") or "") != LIABILITY_SETTLED:
            raise MonetaryAuthorityRefusedError(MONEY_STATE_ERROR, "chain evidence is recorded only after the provider's settlement")
        actuals = {"wallet_outflow": int(confirmation.wallet_outflow_atomic)}
        basis = dict(liability.basis or {})
        fee_max = basis.get("fee_max_atomic")
        has_fee_line = isinstance(fee_max, int) and not isinstance(fee_max, bool) and fee_max > 0 and bool(basis.get("fee_asset"))
        fee = getattr(confirmation, "network_fee_atomic", None)
        if has_fee_line and isinstance(fee, int) and not isinstance(fee, bool):
            actuals["network_fee"] = int(fee)
        law_evidence = LawEvidence(
            evidence_kind="chain_confirmation",
            evidence_id=str(confirmation.signature),
            source="mechanical",
            actuals=actuals,
            public_detail={
                "reader": "wallet_payment_authority",
                "fee_state": "charged" if "network_fee" in actuals else ("not_recorded_by_wallet" if has_fee_line else "no_fee_line"),
                "fee_asset": str(getattr(confirmation, "fee_asset", "") or ""),
            },
        )
        try:
            return settle_liability(str(reservation.reservation_id), law_evidence, claim_token=self._claim_token(reservation))
        except Exception as exc:
            raise _wrap_refusal(exc) from None

def unresolved_x402_operations() -> list[dict[str, Any]]:
    """Paid accountless operations whose answer never arrived, each with its liability state now: public facts only,
    never the request bytes. A row is resumable when its bytes are recorded and the law holds its liability as unknown
    (dead claimants are judged first)."""
    from core.effect_budget_money import liability_for_operation, reconcile_money_liabilities
    from core.usepod.transport import UsePodHttpTransport, UsePodX402Client

    reconcile_money_liabilities(force=True)
    rows = UsePodX402Client(transport=UsePodHttpTransport()).journal.unresolved_operations()
    for row in rows:
        state = str((liability_for_operation(str(row["operation_id"])) or {}).get("state") or "")
        row["liability_state"] = state
        row["resumable"] = bool(row.get("resumable")) and state == "unknown"
    return rows


def admission_refusal_code_of_unknown_reason(unknown_reason: str) -> str:
    """The admission-refusal code a recorded unknown reason carries, or "".

    The reason string is the authority's own typed record, written at refusal time by
    ``retain_unknown`` (``usepod:<outcome>:<code>``). Only ``failed_after_send`` with a code in
    the documented admission set qualifies; ``unknown`` outcomes (timeouts, lost responses,
    interrupted streams) and dead-claimant reconciliations carry no admission fact and stay held.
    """
    import re

    match = re.fullmatch(r"usepod:failed_after_send:([a-z0-9_]+)", str(unknown_reason or "").strip())
    code = str(match.group(1)) if match else ""
    return code if code in ADMISSION_REFUSAL_NO_CHARGE_CODES else ""


def recover_admission_refusal_holds(*, limit: int = 500) -> list[dict[str, Any]]:
    """Settle unknown prepaid holds whose OWN recorded refusal is a documented admission refusal.

    The recovery reads only facts the authority already durably recorded (the typed
    ``unknown_reason`` written when the adapter retained the hold); it makes no new claim about
    the provider and no balance inference. Each settlement carries a ``provider_refusal_record``
    bound to the operation id, settles both prepaid flows at their proven zero, and is
    idempotent -- replaying it after a later conflicting receipt is refused by the law. Every
    other unknown hold (ambiguous outcome, dead claimant, undocumented code) stays exactly as
    held as it was.
    """
    from core.effect_budget_money import (
        EVIDENCE_PROVIDER_REFUSAL_RECORD,
        FLOW_INFERENCE_EXPENSE,
        FLOW_PROVIDER_CREDIT_DEBIT,
        LIABILITY_UNKNOWN,
        SettlementEvidence as LawEvidence,
        liabilities,
        settle_liability,
    )

    changes: list[dict[str, Any]] = []
    for row in liabilities(state=LIABILITY_UNKNOWN, limit=limit):
        if str(row.get("operation_kind") or "") != OPERATION_PREPAID:
            continue
        if str(row.get("provider_id") or "usepod") != "usepod":
            continue
        code = admission_refusal_code_of_unknown_reason(str(row.get("unknown_reason") or ""))
        if not code:
            continue
        status = ADMISSION_REFUSAL_STATUS_BY_CODE.get(code, "")
        evidence = LawEvidence(
            evidence_kind=EVIDENCE_PROVIDER_REFUSAL_RECORD,
            evidence_id=str(row.get("operation_id") or ""),
            source="provider",
            actuals={FLOW_INFERENCE_EXPENSE: 0, FLOW_PROVIDER_CREDIT_DEBIT: 0},
            public_detail={
                "refusal_code": code,
                "refusal_status": status,
                "transport_mode": "prepaid_token",
                "recovered_from": "recorded_unknown_reason",
            },
        )
        try:
            result = settle_liability(str(row["liability_id"]), evidence)
        except Exception as exc:  # a refused settlement is a real conflict: report it, keep the hold
            changes.append(
                {
                    "liability_id": str(row["liability_id"]),
                    "from": LIABILITY_UNKNOWN,
                    "to": LIABILITY_UNKNOWN,
                    "refused": str(getattr(exc, "code", "") or type(exc).__name__),
                    "refusal_code": code,
                }
            )
            continue
        changes.append(
            {
                "liability_id": str(row["liability_id"]),
                "from": LIABILITY_UNKNOWN,
                "to": str(result.get("state") or ""),
                "idempotent": bool(result.get("idempotent")),
                "evidence_kind": EVIDENCE_PROVIDER_REFUSAL_RECORD,
                "refusal_code": code,
                "refusal_status": status,
            }
        )
    return changes


def money_law_status() -> dict[str, Any]:
    """Configuration facts for Settings: the installed law and the UsePod grants that exist."""
    grants = active_prepaid_grants()
    return {
        "installed": True,
        "label": AUTHORITY_LABEL,
        "grants": [
            {
                "grant_id": row["grant_id"],
                "kind": str((row["spec"] or {}).get("kind") or ""),
                "models": list((row["spec"] or {}).get("models") or ()),
                "routes": list((row["spec"] or {}).get("routes") or ()),
                "per_operation_max_atomic": (row["spec"] or {}).get("per_operation_max_atomic"),
                "max_total_atomic": (row["spec"] or {}).get("max_total_atomic"),
                "expires_epoch": (row["spec"] or {}).get("expires_epoch"),
            }
            for row in grants
        ],
    }


__all__ = [
    "AUTHORITY_LABEL",
    "ADMISSION_REFUSAL_NO_CHARGE_CODES",
    "ADMISSION_REFUSAL_STATUS_BY_CODE",
    "LIQUIDITY_FRESH_SECONDS",
    "EffectBudgetMonetaryAuthority",
    "active_prepaid_grants",
    "admission_refusal_code_of_unknown_reason",
    "find_grant",
    "money_law_status",
    "recover_admission_refusal_holds",
    "service_fee_summary",
    "x402_payment_bounds",
    "unresolved_x402_operations",
]
