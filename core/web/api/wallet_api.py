"""The wallet's one HTTP seam. Mounted from ``service.py`` with two deferred-import blocks.

Reads are public-safe (they serve :func:`core.wallet.status.wallet_status`); every state
change is owner-local (loopback) and every refusal is the wallet's typed fault, serialized.
The recovery phrase crosses this seam exactly once, in the pocket-creation response.
"""
from __future__ import annotations

from typing import Any

from core.request_trust import is_loopback_host
from core.wallet import caller_binding, config, custody, proposals, receipts
from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault

PREFIX = "/api/wallet/"
#: The old info routes are read-only compatibility adapters over the canonical wallet (never a key minted on GET).
LEGACY_INFO_PATHS = frozenset({"/api/wallet/info", "/v1/wallet/info"})

_STATUS_FOR_CODE = {
    "wallet_disabled": 403, "wallet_network_disabled": 403, "wallet_signing_unavailable": 403, "wallet_signature_invalid": 403,
    "wallet_confirmation_required": 400, "wallet_pin_invalid": 400, "wallet_limit_exceeded": 403, "wallet_duplicate_payment": 409,
    "wallet_approval_rejected": 403, "wallet_x402_cap_exceeded": 403, "wallet_card_data_refused": 400, "wallet_not_found": 404,
    "wallet_simulation_failed": 502, "wallet_broadcast_failed": 502, "wallet_acknowledgement_required": 400,
    "wallet_device_auth_unavailable": 403, "wallet_device_auth_denied": 403, "wallet_export_unavailable": 400, "wallet_export_pin_not_accepted": 400, "wallet_dependency_unavailable": 403,
    "wallet_password_invalid": 400,
    "wallet_chain_identity_mismatch": 403, "wallet_outbound_refused": 403, "evm_pocket_custody_unavailable": 403, "x402_scheme_unavailable": 403,
    "wallet_caller_refused": 403, "wallet_environment_inactive": 409,
    "wallet_unlock_throttled": 429, "wallet_backup_unavailable": 409, "wallet_storage_class_refused": 403,
    "wallet_setup_state_invalid": 409, "wallet_credential_mismatch": 400,
    "wallet_quote_unavailable": 502, "wallet_quote_expired": 409, "wallet_quote_mismatch": 409, "wallet_insufficient_funds": 400,
    "wallet_recipient_refused": 400, "wallet_request_ambiguous": 409, "wallet_recovery_refused": 400,
}

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

#: Tests configure VOOL_WALLET_UI_CAPABILITY_SHA256 and then reload the binding through this seam.
reset_caller_binding_for_tests = caller_binding.reset_for_tests


def owns(path: str) -> bool:
    return path.startswith(PREFIX) or path in LEGACY_INFO_PATHS


def _json(status: int, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> Any:
    from core.web.api.service import json_response

    return json_response(status, payload, headers=headers)


def _proposal_meaning_for(proposal_id: str) -> dict[str, Any]:
    from core.wallet import meaning, proposals, status

    proposal = proposals.get_proposal(proposal_id)
    if proposal is None:
        return meaning.cannot_explain("the proposal is unknown").to_dict()
    return status.proposal_meaning(proposal)


def _require_acknowledged(described: dict[str, Any], body: dict[str, Any], *, source_context: dict[str, Any] | None) -> None:
    """A RED meaning fails closed: approval needs the acknowledgement that repeats the capability, byte for byte.

    A wallet never asks a human to approve bytes whose consequences it cannot explain -- and when the
    consequence is standing authority, ownership, an upgrade, a mismatch or something undecodable, the
    person must say back what they are granting before signing is even possible."""
    from core.wallet.security import wallet_fault

    if str(described.get("tier") or "") != "red":
        return
    expected = str(described.get("ack_text") or "")
    given = str(body.get("acknowledged_capability") or "")
    if expected and given == expected:
        return
    raise wallet_fault(
        "wallet_acknowledgement_required", authority="core.wallet",
        context={"reason": "red_tier_needs_the_capability_acknowledgement", "meaning": described, "acknowledged_capability": expected},
        source_context=source_context,
    )


def _ok(payload: dict[str, Any]) -> Any:
    return _json(200, {"ok": True, **payload})


def _fault(exc: WalletFault) -> Any:
    payload: dict[str, Any] = {"ok": False, "error": exc.code, "message": exc.user_message, "fault": exc.to_dict()}
    if exc.code == "wallet_confirmation_required":
        payload["warning"] = custody.POCKET_WARNING_TEXT
        payload["confirmation_phrase"] = custody.POCKET_CONFIRMATION_PHRASE
    if exc.code == "wallet_acknowledgement_required":
        # the surface shows the meaning and asks for its exact acknowledgement text; nothing generic passes
        payload["meaning"] = exc.context.get("meaning")
        payload["acknowledged_capability"] = exc.context.get("acknowledged_capability")
    return _json(_STATUS_FOR_CODE.get(exc.code, 400), payload)


def _source_context(body: dict[str, Any], client_host: str) -> dict[str, Any]:
    return {"session_id": str(body.get("session_id") or ""), "surface": "wallet_api", "client_host": str(client_host or "")}


def _wallet_id(body: dict[str, Any]) -> str:
    explicit = str(body.get("wallet_id") or "").strip()
    if explicit:
        return explicit
    profile = custody.default_wallet()
    return profile.wallet_id if profile else ""


def _prepared(proposal: proposals.TransactionProposal, *, source_context: dict[str, Any], duplicate: bool) -> Any:
    from core.wallet import lifecycle

    prepared = lifecycle.default_lifecycle(source_context=source_context).prepare(proposal.proposal_id)
    return _ok({"proposal": prepared.to_dict(), "duplicate": duplicate})


def handle_wallet_get(path: str, query: dict[str, list[str]], *, client_host: str = "") -> Any:
    if not is_loopback_host(client_host):
        return _json(403, {"ok": False, "error": "owner_local_required"})
    from core.wallet.status import wallet_status

    if path == "/api/wallet/status":
        return _ok({"status": wallet_status()})
    if path == "/api/wallet/safety":
        # knowledge, not custody: served whether or not the wallet is switched on
        from core.wallet.scam_school import panel

        return _ok(panel())
    if path in LEGACY_INFO_PATHS:
        from core.vool_wallet import legacy_wallet_view

        return _ok(legacy_wallet_view(include_balances=True))
    if path == "/api/wallet/pocket/terms":
        return _ok({"warning": custody.POCKET_WARNING_TEXT, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE, "signing_preference": custody.preferred_signing_mode()})
    if path == "/api/wallet/receipts":
        return _ok({"receipts": receipts.list_receipts(limit=50)})
    if path == "/api/wallet/dna-fees":
        # the DNA service-fee ledger as the owner reads it: policy, treasury binding, thresholds, exact positions, collections
        from core.wallet import dna_fees

        return _ok({"dna_fees": dna_fees.status()})
    if path == "/api/wallet/balance":
        # the Crypto surface's balance line: a typed read of the row's native coin, or `unavailable` with its reason (never zero)
        from core.wallet import balances

        wallet_id = str((query.get("wallet_id") or [""])[0] or "").strip()
        return _ok({"balance": balances.read_balance(wallet_id)})
    if path == "/api/wallet/usepod/status":
        from core.wallet import usepod

        record = usepod.status_for(str((query.get("correlation_id") or [""])[0] or ""), provider=str((query.get("provider") or [""])[0] or ""))
        return _ok({"topup": record}) if record else _json(404, {"ok": False, "error": "wallet_not_found"})
    if path == "/api/wallet/transfers":
        # the Crypto Pilot activity read model: one row per transfer, its state label, evidence and explorer link
        from core.wallet import transfers

        wanted = str((query.get("proposal_id") or [""])[0] or "").strip()
        if wanted:
            view = transfers.latest_receipt(wanted)
            return _ok({"transfer": view}) if view else _json(404, {"ok": False, "error": "wallet_not_found"})
        try:
            limit = int((query.get("limit") or ["50"])[0])
        except (TypeError, ValueError):
            limit = 50
        return _ok({"transfers": transfers.list_transfers(limit=limit, wallet_id=str((query.get("wallet_id") or [""])[0] or ""), network=str((query.get("network") or [""])[0] or ""))})
    if path.startswith("/api/wallet/wallets/"):
        profile = custody.get_wallet(path.rsplit("/", 1)[-1])
        return _ok({"wallet": profile.to_dict()}) if profile else _json(404, {"ok": False, "error": "wallet_not_found"})
    if path.startswith("/api/wallet/proposals/"):
        proposal_id = path.rsplit("/", 1)[-1]
        proposal = proposals.get_proposal(proposal_id)
        if proposal is None:
            return _json(404, {"ok": False, "error": "wallet_not_found"})
        challenge_view = None
        if proposal.state == proposals.STATE_PENDING_APPROVAL:
            try:
                from core.wallet import lifecycle

                profile = custody.require_wallet(proposal.wallet_id)
                challenge_view = lifecycle.default_lifecycle()._challenge_for(proposal, profile).binding_view()
            except Exception:
                challenge_view = None
        return _ok({"proposal": proposal.to_dict(), "events": proposals.proposal_events(proposal_id), "challenge": challenge_view})
    return _json(404, {"ok": False, "error": "not found"})


def _setup_door(path: str, body: dict[str, Any], *, source_context: dict[str, Any]) -> Any:
    """The Crypto Pilot setup doors. Every answer is no-store; only the reveal ever carries key material."""
    from core.wallet import pilot_custody

    wallet_id = str(body.get("wallet_id") or "")
    credential = str(body.get("credential") or "")
    if path == "/api/wallet/setup/create":
        view = pilot_custody.create_pilot_wallet(
            network=str(body.get("network") or ""), method=str(body.get("method") or "pin"), credential=credential,
            credential_confirmation=str(body.get("credential_confirmation") or ""), creation_key=str(body.get("creation_key") or ""),
            label=str(body.get("label") or ""), source_context=source_context,
        )
        return _json(200, {"ok": True, "setup": view}, headers=_NO_STORE)
    if path == "/api/wallet/setup/reveal":
        revealed = pilot_custody.reveal_pilot_backup(wallet_id, credential=credential, source_context=source_context)
        return _json(200, {"ok": True, "backup": revealed}, headers=_NO_STORE)
    if path == "/api/wallet/setup/acknowledge":
        view = pilot_custody.acknowledge_pilot_backup(wallet_id, credential=credential, ack_token=str(body.get("ack_token") or ""), source_context=source_context)
        return _json(200, {"ok": True, "setup": view}, headers=_NO_STORE)
    if path == "/api/wallet/setup/cancel":
        return _json(200, {"ok": True, "setup": pilot_custody.cancel_pilot_setup(wallet_id, credential=credential, source_context=source_context)}, headers=_NO_STORE)
    if path == "/api/wallet/setup/resume":
        return _json(200, {"ok": True, "setup": pilot_custody.resume_pilot_setup(wallet_id, credential=credential, source_context=source_context)}, headers=_NO_STORE)
    return _json(404, {"ok": False, "error": "not found"})


def _recovery_door(path: str, body: dict[str, Any], *, source_context: dict[str, Any]) -> Any:
    """Forgotten credential. `options` reads what this wallet offers (no secret crosses); `backup` takes the one-time
    backup VOOL issued and a new credential; `device` takes the wallet-bound challenge and a new credential. Every answer
    is no-store; the backup value is registered with the exact-value scrubber the moment it arrives and never echoed."""
    from core.wallet import pilot_custody

    wallet_id = str(body.get("wallet_id") or "")
    if path == "/api/wallet/recovery/options":
        return _json(200, {"ok": True, "recovery": pilot_custody.recovery_options(wallet_id, source_context=source_context)}, headers=_NO_STORE)
    method = str(body.get("method") or "pin")
    credential = str(body.get("credential") or "")
    confirmation = str(body.get("credential_confirmation") or "")
    if path == "/api/wallet/recovery/backup":
        view = pilot_custody.recover_with_backup(wallet_id, backup_value=str(body.get("backup_value") or body.get("private_key") or ""), method=method,
                                                 credential=credential, credential_confirmation=confirmation, source_context=source_context)
        return _json(200, {"ok": True, "setup": view}, headers=_NO_STORE)
    if path == "/api/wallet/recovery/device":
        view = pilot_custody.recover_with_device(wallet_id, challenge=str(body.get("challenge") or ""), method=method, credential=credential,
                                                 credential_confirmation=confirmation, source_context=source_context)
        return _json(200, {"ok": True, "setup": view}, headers=_NO_STORE)
    return _json(404, {"ok": False, "error": "not found"})


def handle_wallet_post(path: str, body: dict[str, Any], *, client_host: str = "", headers: dict[str, Any] | None = None) -> Any:
    """``headers`` are the real request's headers. A caller without a request behind it (the command-dispatch
    projection) passes none, and a trusted door refuses it: loopback alone never authorizes a trusted door."""
    if not is_loopback_host(client_host):
        return _json(403, {"ok": False, "error": "owner_local_required"})
    body = body if isinstance(body, dict) else {}
    source_context = _source_context(body, client_host)
    if caller_binding.is_trusted_door(path):
        reason = caller_binding.refusal_reason(headers)
        if reason:
            return _fault(wallet_fault("wallet_caller_refused", authority=caller_binding.AUTHORITY, context={"reason": reason, "door": path}, source_context=source_context))
    try:
        if path == "/api/wallet/transfers/refresh":
            # wakes the observer only; it runs whether Crypto is enabled or not, so unresolved transfers keep moving
            from core.wallet import settlement

            return _ok({"observer": {"woken": settlement.wake_observer(), **settlement.observer_state()}})
        if not config.wallet_enabled():
            custody.require_enabled(source_context=source_context)
        if path == "/api/wallet/watch-only":
            profile = custody.create_watch_only_wallet(str(body.get("public_key") or ""), network=str(body.get("network") or config.NETWORK_SOLANA_DEVNET), label=str(body.get("label") or ""), source_context=source_context)
            return _ok({"wallet": profile.to_dict()})
        if path == "/api/wallet/external":
            profile = custody.register_external_signer_wallet(str(body.get("public_key") or ""), network=str(body.get("network") or config.NETWORK_SOLANA_DEVNET), label=str(body.get("label") or ""), source_context=source_context)
            return _ok({"wallet": profile.to_dict()})
        if path == "/api/wallet/pocket/create":
            created = custody.create_pocket_wallet(
                acknowledged_warning=bool(body.get("acknowledged_warning")), confirmation_phrase=str(body.get("confirmation_phrase") or ""),
                pin=str(body.get("pin") or ""), password=str(body.get("password") or ""), approval_method=str(body.get("approval_method") or "pin"), network=str(body.get("network") or config.NETWORK_SOLANA_DEVNET), label=str(body.get("label") or ""), source_context=source_context,
            )
            # The one and only place the phrase is ever serialized. Not logged, not journaled, not retained,
            # and never cacheable by the browser.
            return _json(200, {"ok": True, "wallet": created.profile.to_dict(), "recovery_phrase": created.recovery_phrase, "shown_once": True, "warning": custody.POCKET_WARNING_TEXT}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        if path == "/api/wallet/pocket/restore":
            profile = custody.restore_pocket_wallet(str(body.get("recovery_phrase") or ""), pin=str(body.get("pin") or ""), password=str(body.get("password") or ""), approval_method=str(body.get("approval_method") or "pin"), label=str(body.get("label") or ""), source_context=source_context)
            return _ok({"wallet": profile.to_dict()})
        if path in (
            "/api/wallet/setup/create", "/api/wallet/setup/reveal", "/api/wallet/setup/acknowledge", "/api/wallet/setup/cancel",
            "/api/wallet/setup/resume",
        ):
            # listed door by door: each is its own registered command, and a prefix here is catalogued as an unregistered family
            return _setup_door(path, body, source_context=source_context)
        if path in ("/api/wallet/recovery/options", "/api/wallet/recovery/backup", "/api/wallet/recovery/device"):
            return _recovery_door(path, body, source_context=source_context)
        if path == "/api/wallet/quote":
            from core.wallet import quotes

            return _json(200, {"ok": True, "quote": quotes.mint_quote(str(body.get("proposal_id") or ""), source_context=source_context)}, headers=_NO_STORE)
        if path == "/api/wallet/environment":
            from core.wallet import environment
            from core.wallet.status import crypto_pilot_status

            environment.set_active_environment(str(body.get("environment") or ""), source_context=source_context)
            return _ok({"crypto_pilot": crypto_pilot_status()})
        if path == "/api/wallet/limits":
            from core.wallet import limits

            wallet_id = _wallet_id(body)
            custody.require_wallet(wallet_id, source_context=source_context)
            current = limits.load_limits(wallet_id, str(body.get("asset") or "SOL"))
            updated = limits.set_limits(wallet_id, str(body.get("asset") or "SOL"), limits.SpendLimits(
                per_tx_minor=int(body.get("per_tx_minor", current.per_tx_minor)), daily_minor=int(body.get("daily_minor", current.daily_minor)),
                per_destination_daily_minor=int(body.get("per_destination_daily_minor", current.per_destination_daily_minor)),
            ))
            return _ok({"limits": updated.to_dict()})
        if path == "/api/wallet/dna-fees/policy":
            # the trusted Settings door for the collection threshold (the rate and the treasury are not settable here or
            # anywhere). The reply separates what was SAVED from what is EFFECTIVE: an operator override outranks the
            # saved value while it is in force, and the page must never call the override "saved".
            from core.wallet import dna_fees

            asset = str(body.get("asset") or "USDC")
            saved = None
            if "collect_min_atomic" in body:
                value = dna_fees.set_collect_min_atomic(asset, body.get("collect_min_atomic"), source_context=source_context)
                saved = {"asset": asset.upper(), "atomic": value, "source": "settings"}
            view = dna_fees.collect_min_view(asset)
            return _ok({"collect_min": view, "saved": saved, "override_in_force": view["source"] == "operator_override"})
        if path == "/api/wallet/usepod/topup":
            # the UsePod owner's exact payment requirement becomes ONE pilot proposal under its correlation id
            from core.wallet import usepod

            try:
                requirement = usepod.parse_requirement(body)
            except usepod.RequirementError as exc:
                return _json(400, {"ok": False, "error": "usepod_requirement_invalid", "reason": exc.reason})
            return _ok({"topup": usepod.validate_topup(requirement, source_context=source_context)})
        if path == "/api/wallet/propose":
            key = str(body.get("idempotency_key") or "")
            wallet_id = _wallet_id(body)
            from core.wallet import idempotency

            existed = idempotency.existing_for_key(wallet_id, key) is not None if key else False
            proposal = proposals.propose_transaction(
                wallet_id=wallet_id, destination=str(body.get("destination") or ""), amount_minor=int(body.get("amount_minor") or 0),
                asset=str(body.get("asset") or "SOL"), origin=str(body.get("origin") or proposals.ORIGIN_USER), memo=str(body.get("memo") or ""),
                idempotency_key=key, source_context=source_context, network=str(body.get("network") or ""),
            )
            return _prepared(proposal, source_context=source_context, duplicate=existed)
        if path == "/api/wallet/x402/propose":
            from core.wallet import x402, x402_v2

            headers_body = body.get("headers") if isinstance(body.get("headers"), dict) else {}
            v2_offer = x402_v2.parse_payment_required(headers_body, body.get("body"), status=int(body.get("status") or 402))
            if v2_offer is not None:
                entry, reason = x402_v2.select_offer(v2_offer, wallet_id=_wallet_id(body), source_context=source_context)
                if entry is None:
                    raise WalletFault("x402_scheme_unavailable", context={"reason": reason})
                proposal = x402_v2.propose_from_v2_offer(v2_offer, entry, wallet_id=_wallet_id(body), source_context=source_context)
                existed = proposal.state != proposals.STATE_PROPOSED
                return _prepared(proposal, source_context=source_context, duplicate=existed)
            request = x402.detect_x402(int(body.get("status") or 0), headers_body, body.get("body"))
            if request is None:
                return _json(400, {"ok": False, "error": "x402_not_detected"})
            wallet_id = _wallet_id(body)
            from core.wallet import idempotency

            proposal = x402.propose_from_x402(request, wallet_id=wallet_id, source_context=source_context)
            existed = proposal.state != proposals.STATE_PROPOSED
            return _prepared(proposal, source_context=source_context, duplicate=existed) if not existed else _ok({"proposal": proposal.to_dict(), "duplicate": True, "x402": request.to_dict()})
        if path == "/api/wallet/export":
            # export is device-authenticated ONLY: a PIN in the request is refused outright, never consulted
            if any(key in body for key in ("pin", "password", "passphrase")):
                raise wallet_fault("wallet_export_pin_not_accepted", authority="core.wallet", context={"reason": "export_unlocks_only_by_device_authentication"}, source_context=source_context)
            return _ok({"export": custody.export_private_key(_wallet_id(body), target=str(body.get("target") or ""), source_context=source_context)})
        if path == "/api/wallet/approve":
            from core.wallet import approval, lifecycle

            engine = lifecycle.default_lifecycle(source_context=source_context)
            proposal_id = str(body.get("proposal_id") or "")
            if str(body.get("quote_id") or "").strip():
                # a Crypto Pilot transfer: approval v3 bound to the open quote
                if str(body.get("method") or "").strip().lower() == "device":
                    pilot_approver: Any = approval.DeviceApprover()
                elif body.get("password"):
                    pilot_approver = approval.PasswordApprover(str(body.get("password") or ""))
                else:
                    pilot_approver = approval.PinApprover(str(body.get("pin") or ""))
                result = engine.approve_pilot_transfer(proposal_id, quote_id=str(body.get("quote_id") or ""), quote_digest=str(body.get("quote_digest") or ""), approver=pilot_approver)
                # ``companion``: what happened to the collection of previously accrued DNA fees that rode this approval (None when none did)
                return _ok({"transfer": result["transfer"], "duplicate": bool(result.get("duplicate")), "receipt": result["transfer"], "companion": result.get("companion")})
            _require_acknowledged(_proposal_meaning_for(proposal_id), body, source_context=source_context)
            if str(body.get("method") or "").strip().lower() == "external":
                # The owner approves by signing in their own wallet: hand out the exact bytes, nothing more.
                return _ok({"signing_request": engine.request_external_signature(proposal_id)})
            if str(body.get("method") or "").strip().lower() == "device":
                approver: Any = approval.DeviceApprover()
            elif body.get("password"):
                approver = approval.PasswordApprover(str(body.get("password") or ""))
            else:
                approver = approval.PinApprover(str(body.get("pin") or ""))
            receipt = engine.approve_and_execute(proposal_id, approver=approver)
            return _ok({"receipt": receipt.to_dict()})
        if path == "/api/wallet/approval-method":
            profile = custody.change_approval_method(
                _wallet_id(body), new_method=str(body.get("method") or ""), new_secret=str(body.get("new_pin") or body.get("new_password") or ""),
                current_secret=str(body.get("pin") or body.get("password") or ""), source_context=source_context,
            )
            return _ok({"wallet": profile.to_dict()})
        if path == "/api/wallet/external/submit":
            from core.wallet import external_signing, lifecycle

            _record = external_signing.get_signing_request(str(body.get("request_id") or ""))
            if _record is not None:
                _require_acknowledged(external_signing.request_meaning(_record), body, source_context=source_context)
            receipt = lifecycle.default_lifecycle(source_context=source_context).submit_external_signature(
                str(body.get("request_id") or ""), signature_b58=str(body.get("signature_b58") or ""), signed_transaction_b64=str(body.get("signed_transaction_b64") or body.get("transaction") or ""),
                signature_hex=str(body.get("signature_hex") or (str(body.get("signature") or "") if str(body.get("signature") or "").startswith("0x") else "")),
            )
            return _ok({"receipt": receipt.to_dict()})
        if path in ("/api/wallet/transfers/stop-waiting", "/api/wallet/transfers/resend", "/api/wallet/transfers/discard"):
            from core.wallet import lifecycle, pilot_custody, settlement, transfers

            proposal_id = str(body.get("proposal_id") or "")
            proposal = proposals.get_proposal(proposal_id)
            row = transfers.get_transfer_by_id(proposal_id)
            if proposal is None or row is None:
                return _json(404, {"ok": False, "error": "wallet_not_found"})
            baseline = settlement.control_baseline()  # read before the credential: the resend compares against it
            # the exit re-proves the owner's credential through the wallet's own throttle; nothing else authorizes it
            pilot_custody.prove_credential(str(row["wallet_id"]), str(body.get("password") or body.get("pin") or ""), source_context=source_context)
            try:
                if path.endswith("/stop-waiting"):
                    view = settlement.owner_stop_waiting(proposal_id, decision_note=str(body.get("note") or ""))
                elif path.endswith("/discard"):
                    view = settlement.owner_discard(proposal_id)
                else:
                    view = lifecycle.default_lifecycle(source_context=source_context).send_signed(proposal_id, origin="owner_resend", baseline=baseline)
            except settlement.ExitUnavailableError as exc:
                return _fault(wallet_fault("wallet_broadcast_failed", authority=settlement.AUTHORITY, context={"proposal_id": proposal_id, "reason": f"exit_unavailable:{exc}"}, source_context=source_context))
            return _ok({"transfer": view})
        if path == "/api/wallet/reject":
            proposal_id = str(body.get("proposal_id") or "")
            proposal = proposals.get_proposal(proposal_id)
            if proposal is None:
                return _json(404, {"ok": False, "error": "wallet_not_found"})
            from core.wallet import transfers

            if transfers.is_pilot_transfer(proposal) and proposal.state != proposals.STATE_AWAITING_SIGNATURE:
                # a Crypto Pilot request: one transaction decides between rejecting the proposal and flagging its claim;
                # the answer says exactly what happened, and a request that already reached the send is not cancellable
                from core.wallet import settlement

                answer = settlement.request_cancel(proposal_id)
                current = proposals.get_proposal(proposal_id) or proposal
                return _ok({**answer, "proposal": answer.get("proposal") or current.to_dict(), "rejected": bool(answer.get("cancelled"))})
            if proposal.state == proposals.STATE_AWAITING_SIGNATURE:
                # the owner's cancellation AFTER claim: valid only while the request is
                # still OPEN (never consumed = the signature never returned to this app =
                # positive no-dispatch through the app's only submission door). A request
                # another submit already CONSUMED is a submission in flight: rejection
                # would release a hold for a payment that may still settle, so it is
                # refused honestly instead.
                from core.wallet import external_signing, limits, reconciliation

                record = external_signing.open_request_for_proposal(proposal_id)
                won = bool(record) and external_signing.expire_signing_request(record["request_id"])
                if not won:
                    return _ok({"proposal": proposal.to_dict(), "rejected": False, "reason": "signing_request_in_flight"})
                limits.release_spend(proposal_id)
                reconciliation.resolve_payment_effect(proposal_id, applied=False, evidence="owner rejected before signature submission", source="mechanical")
                rejected = proposals.transition(proposal_id, proposals.STATE_REJECTED, detail={"reason": "owner_rejected_after_claim"}, expected_state=proposals.STATE_AWAITING_SIGNATURE)
                if rejected is not None:
                    receipts.record_receipt(rejected, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected")
                return _ok({"proposal": (rejected or proposal).to_dict(), "rejected": rejected is not None})
            rejected = proposals.transition(proposal_id, proposals.STATE_REJECTED, detail={"reason": "owner_rejected"}, expected_state=proposals.STATE_PENDING_APPROVAL)
            return _ok({"proposal": (rejected or proposal).to_dict(), "rejected": rejected is not None})
        if path == "/api/wallet/facilitator/discover":
            from core.wallet import facilitators

            caps = facilitators.discover(str(body.get("origin") or ""), facilitator_id=str(body.get("facilitator_id") or ""), source_context=source_context)
            return _ok({"capabilities": [c.to_dict() for c in caps]})
        if path == "/api/wallet/facilitators":
            from core.wallet import facilitators

            return _ok({"capabilities": [c.to_dict() for c in facilitators.list_capabilities()]})
        if path == "/api/wallet/x402/fetch":
            from core.wallet import x402

            outcome = x402.fetch_paid_resource(str(body.get("url") or ""), wallet_id=_wallet_id(body), source_context=source_context)
            return _ok({"outcome": outcome.to_dict(), "body_text": outcome.body[:4096].decode("utf-8", "replace")})
        if path == "/api/wallet/x402/retry":
            from core.wallet import x402

            outcome = x402.retry_paid_resource(str(body.get("proposal_id") or ""), source_context=source_context)
            return _ok({"outcome": outcome.to_dict(), "body_text": outcome.body[:4096].decode("utf-8", "replace")})
        return _json(404, {"ok": False, "error": "not found"})
    except WalletFault as exc:
        return _fault(exc)
