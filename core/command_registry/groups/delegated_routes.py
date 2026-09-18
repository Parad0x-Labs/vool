"""The route families the census could not see, given owners.

Every command here adapts a route that a prefix dispatcher hands to a delegated
handler module. Those routes were invisible to the operator-action census —
``_discover_http`` harvested string literals from ``ast.Compare`` comparators, and
``normalized_path.startswith("/api/wallet/")`` is an ``ast.Call`` — so the census
reported ``LEGACY_UNMIGRATED: 0`` while 36 real operator actions sat behind
prefixes, including every wallet route that moves value, the mobile
device-authority surface and the OAuth code intake.

The scanner is repaired separately (``census._discover_delegated_families``). This
file is the other half: the surfaces it revealed get declared owners, so the zero
the census reports is a zero about the product rather than about the scanner.

DECLARATIVE ON PURPOSE. Each row states method, path, the delegated handler, the
effects class and the permission gate; the handler bodies are generated from that
one table. A route added to a delegated module and not added here appears in the
census as unowned, which is exactly the outcome that was previously impossible.

Nothing here re-implements an authority. `wallet_api` and `mobile_companion_api`
already own their gates, their typed faults and their owner-local checks; these
commands put a declared name, a typed effects class and the effect-budget door in
front of the same code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from typing import Any

from core.command_registry.registry import CommandRegistry
from core.command_registry.spec import (
    AuthorityDecision,
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    OpenRead,
    OperatorAuthority,
)


@_dataclass(frozen=True)
class RouteInput:
    """The transport shape a delegated route already accepts."""

    body: dict[str, Any] = _field(default_factory=dict)
    query: dict[str, Any] = _field(default_factory=dict)
    path: str = ""


def _peer(ctx: Any) -> str:
    return str((getattr(ctx, "extra", None) or {}).get("client_host") or "127.0.0.1")


def _gate_owner_local(_inp: Any, ctx: Any) -> AuthorityDecision:
    """Operator-only and loopback-only, enforced by the AUTHORITY.

    The delegated handlers already refuse a foreign peer. This adds the half they
    cannot do: a principal check, so the model lane and any delegated principal are
    refused before dispatch, on every projection rather than only over HTTP.
    """
    from core.web.api.service import is_loopback_host

    principal = str(getattr(ctx, "principal", "") or "")
    if principal != "operator":
        return AuthorityDecision(
            granted=False, reason=f"operator-only surface; principal was {principal!r}"
        )
    if not is_loopback_host(_peer(ctx)):
        return AuthorityDecision(granted=False, reason="owner_local_required")
    return AuthorityDecision(granted=True)


def _probe_wallet_surface(_context: dict) -> tuple[bool, str]:
    try:
        from core.wallet import config

        config.wallet_enabled()
        return True, ""
    except Exception as exc:  # pragma: no cover - probe failure is the evidence
        return False, f"wallet surface unreachable: {exc}"


def _probe_mobile_surface(_context: dict) -> tuple[bool, str]:
    try:
        import core.web.api.mobile_companion_api as _m

        return bool(_m), ""
    except Exception as exc:  # pragma: no cover
        return False, f"mobile companion surface unreachable: {exc}"


def _response_to_result(resp: Any, *, summary: str) -> Any:
    """Turn the delegated handler's ApiResponse into a typed envelope.

    ``legacy_status`` rides in the fault detail so the HTTP adapter can return the
    handler's own status and body byte-for-byte.
    """
    status = int(getattr(resp, "status", 200) or 200)
    raw = getattr(resp, "body", b"") or b"{}"
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"ok": status == 200}
    if status == 200:
        return HandlerOk(data=payload, summary=summary)
    code = {
        400: "usage",
        403: "permission_denied",
        404: "fault_validation",
        409: "conflict",
        422: "fault_validation",
    }.get(status, "fault_tool")
    detail = {"legacy_status": status}
    if isinstance(payload, dict):
        detail.update(payload)
    return HandlerFault(fault_code=code, summary=summary, detail=detail)


def _wallet_get(path: str):
    def _run(inp: RouteInput, ctx: Any) -> Any:
        from core.web.api.wallet_api import handle_wallet_get

        return _response_to_result(
            handle_wallet_get(path, dict(inp.query or {}), client_host=_peer(ctx)),
            summary=f"wallet read {path}",
        )

    return _run


def _wallet_post(path: str):
    def _run(inp: RouteInput, ctx: Any) -> Any:
        from core.web.api.wallet_api import handle_wallet_post

        return _response_to_result(
            handle_wallet_post(path, dict(inp.body or {}), client_host=_peer(ctx)),
            summary=f"wallet action {path}",
        )

    return _run


def _mobile_post(path: str):
    def _run(inp: RouteInput, ctx: Any) -> Any:
        from core.web.api.mobile_companion_api import handle_mobile_companion_post
        from core.web.api.runtime import RuntimeServices

        return _response_to_result(
            handle_mobile_companion_post(
                path,
                dict(inp.body or {}),
                runtime=RuntimeServices(display_name="VOOL"),
                model_name="",
                client_host=_peer(ctx),
            ),
            summary=f"mobile action {path}",
        )

    return _run


#: (command_id, method, path, effects, description)
#:
#: `spend` and `external_send` are used where the route can move value or reach a
#: third party, so the effect-budget door meters them under the class that names
#: what they actually do.
WALLET_READS: tuple[tuple[str, str, str], ...] = (
    ("wallet.status", "/api/wallet/status", "Read the wallet's public status (never a key)"),
    ("wallet.receipts", "/api/wallet/receipts", "List recent wallet receipts, redacted"),
    ("wallet.transfers", "/api/wallet/transfers", "List Crypto Pilot transfers: state, evidence and explorer link, never bytes"),
    ("wallet.balance", "/api/wallet/balance", "Read one account's native-coin balance from its row's endpoint; unavailable is a typed reason, never zero"),
    ("wallet.usepod.status", "/api/wallet/usepod/status", "Read the UsePod top-up record for a correlation id: state, transaction id, explorer link"),
    ("wallet.dna_fees.status", "/api/wallet/dna-fees", "Read the DNA service-fee ledger: policy, treasury binding, thresholds, exact accrued positions and collections"),
    ("wallet.pocket.terms", "/api/wallet/pocket/terms", "Read the pocket-wallet custody terms"),
    ("wallet.get", "/api/wallet/wallets/{id}", "Read one registered wallet's public profile"),
    ("wallet.proposal.get", "/api/wallet/proposals/{id}",
     "Read one parked proposal and its approval-challenge binding"),
)

WALLET_ACTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("wallet.watch_only.create", "/api/wallet/watch-only", "idempotent_write",
     "Register a watch-only wallet (no key, no spend)"),
    ("wallet.external.register", "/api/wallet/external", "idempotent_write",
     "Register an external-signer wallet (the key stays in the operator's wallet)"),
    ("wallet.pocket.create", "/api/wallet/pocket/create", "destructive",
     "Create a VOOL Wallet on this device (typed confirmation and PIN required)"),
    ("wallet.pocket.restore", "/api/wallet/pocket/restore", "destructive",
     "Restore a VOOL Wallet from a recovery phrase (PIN required)"),
    ("wallet.limits.set", "/api/wallet/limits", "mutating",
     "Set the wallet's spend ceilings (per-transaction, daily, destination)"),
    ("wallet.environment.set", "/api/wallet/environment", "mutating",
     "Choose Mainnet or Test networks for new wallet effects; accounts, balances and receipts never move"),
    ("wallet.setup.create", "/api/wallet/setup/create", "destructive",
     "Create a Crypto Pilot wallet on this device: credential first, then one backup reveal"),
    ("wallet.setup.reveal", "/api/wallet/setup/reveal", "destructive",
     "Reveal a Crypto Pilot wallet's backup key exactly once (credential required)"),
    ("wallet.setup.acknowledge", "/api/wallet/setup/acknowledge", "mutating",
     "Confirm the backup was saved and finish Crypto Pilot setup"),
    ("wallet.setup.cancel", "/api/wallet/setup/cancel", "mutating",
     "Cancel a Crypto Pilot setup; the wallet is kept, never deleted"),
    ("wallet.setup.resume", "/api/wallet/setup/resume", "mutating",
     "Resume a cancelled Crypto Pilot setup without revealing the backup again"),
    ("wallet.recovery.options", "/api/wallet/recovery/options", "mutating",
     "Read what recovery a Crypto Pilot wallet offers for a forgotten PIN or password; reading changes nothing"),
    ("wallet.recovery.backup", "/api/wallet/recovery/backup", "destructive",
     "Restore access to a Crypto Pilot wallet from the backup key VOOL issued and set a new PIN or password (same key, same address)"),
    ("wallet.recovery.device", "/api/wallet/recovery/device", "destructive",
     "Restore access to a Crypto Pilot wallet from its enrolled device-protected copy after fresh user presence and set a new PIN or password"),
    ("wallet.quote.mint", "/api/wallet/quote", "external_send",
     "Mint a typed transfer quote from the chain: balance, fee ceiling and totals for the approval sheet"),
    ("wallet.propose", "/api/wallet/propose", "mutating",
     "Park a transaction proposal for operator approval; nothing is signed or sent"),
    ("wallet.x402.propose", "/api/wallet/x402/propose", "mutating",
     "Park an x402 payment proposal from an offer; nothing is paid"),
    ("wallet.approve", "/api/wallet/approve", "spend",
     "Approve and execute a parked transaction — this moves real value off this machine"),
    ("wallet.external.submit", "/api/wallet/external/submit", "spend",
     "Submit an externally-signed transaction for broadcast — this moves real value"),
    ("wallet.reject", "/api/wallet/reject", "mutating",
     "Reject a parked proposal; nothing is signed or sent"),
    ("wallet.usepod.topup", "/api/wallet/usepod/topup", "mutating",
     "Turn a UsePod payment requirement into one Crypto Pilot proposal under its correlation id; nothing is signed or sent"),
    ("wallet.dna_fees.policy", "/api/wallet/dna-fees/policy", "mutating",
     "Read or set the DNA service-fee collection threshold (the rate, the treasury and the cost bound are not settable; collection rides the next native payment's approval)"),
    ("wallet.transfers.refresh", "/api/wallet/transfers/refresh", "mutating",
     "Wake the transfer observer; it reads the chain and never transmits"),
    ("wallet.transfers.stop_waiting", "/api/wallet/transfers/stop-waiting", "mutating",
     "Stop waiting on an uncertain transfer: it keeps counting, the account is freed, nothing is sent"),
    ("wallet.transfers.resend", "/api/wallet/transfers/resend", "spend",
     "Send the same signed transaction again — the same amount, recipient and fee limit; it cannot pay twice"),
    ("wallet.transfers.discard", "/api/wallet/transfers/discard", "mutating",
     "Discard a signed transfer that never left this device; releases its hold"),
    ("wallet.facilitator.discover", "/api/wallet/facilitator/discover", "external_send",
     "Ask a facilitator for its capabilities — leaves this machine"),
    ("wallet.facilitators.list", "/api/wallet/facilitators", "read_only",
     "List known x402 facilitators and their declared capabilities"),
    ("wallet.x402.fetch", "/api/wallet/x402/fetch", "spend",
     "Fetch an x402-gated resource, paying for it — this moves real value"),
    ("wallet.x402.retry", "/api/wallet/x402/retry", "spend",
     "Retry a paid x402 fetch — never pays twice, but can move real value"),
)

MOBILE_ACTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("mobile.pairing.start", "/api/mobile/pairing/start", "mutating",
     "Start a device-pairing session"),
    ("mobile.pairing.claim", "/api/mobile/pairing/claim", "destructive",
     "Claim a pairing session — this MINTS a device grant"),
    ("mobile.companion.dispatch", "/api/mobile/companion", "mutating",
     "The companion RPC door: a paired device's typed actions"),
    ("mobile.devices.revoke", "/api/mobile/devices/revoke", "destructive",
     "Revoke a paired device's authority"),
)

_AUTHORITY = OperatorAuthority(
    kind="delegated.route",
    verifier="core.command_registry.groups.delegated_routes:_gate_owner_local",
)
_WALLET_AVAILABILITY = Availability(
    probe="core.command_registry.groups.delegated_routes:_probe_wallet_surface"
)
_MOBILE_AVAILABILITY = Availability(
    probe="core.command_registry.groups.delegated_routes:_probe_mobile_surface"
)

def route_command_map() -> dict[str, str]:
    """census_id -> command_id, derived from the declaration tables above.

    Derived, not accumulated during registration: `census_overrides` imports this at
    MODULE import time, long before `registry()` first calls `register()`, so a dict
    filled in during registration reads as empty and every one of these routes stays
    LEGACY_UNMIGRATED while looking declared. One source of truth, readable without
    side effects.
    """
    mapping: dict[str, str] = {}
    for command_id, path, _description in WALLET_READS:
        mapping[f"http:GET:{path}"] = command_id
    for command_id, path, _effects, _description in WALLET_ACTIONS:
        mapping[f"http:POST:{path}"] = command_id
    for command_id, path, _effects, _description in MOBILE_ACTIONS:
        mapping[f"http:POST:{path}"] = command_id
    return mapping


#: census_id -> command_id, consumed by census_overrides so the pin binds.
ROUTE_COMMANDS: dict[str, str] = {}


def _register_handler(name: str, fn) -> str:
    """Bind a generated handler to a module-level dotted path the registry can resolve."""
    globals()[name] = fn
    return f"core.command_registry.groups.delegated_routes:{name}"


def register(reg: CommandRegistry) -> None:
    reg.add_group(
        GroupSpec(
            group_id="wallet",
            description="wallet custody, proposals and settlement (disabled by default, testnet only)",
        )
    )
    reg.add_group(
        GroupSpec(group_id="mobile", description="mobile companion pairing and device authority")
    )

    for command_id, path, description in WALLET_READS:
        fn_name = "_h_" + command_id.replace(".", "_")
        dotted = _register_handler(fn_name, _wallet_get(path))
        reg.add(
            CommandSpec(
                command_id=command_id,
                group="wallet",
                description=description,
                input_schema=RouteInput,
                effects="read_only",
                permission=_AUTHORITY,
                handler=Handler(dotted),
                exit_codes=(0, 2, 21, 42),
            )
        )
        ROUTE_COMMANDS[f"http:GET:{path}"] = command_id

    for command_id, path, effects, description in WALLET_ACTIONS:
        fn_name = "_h_" + command_id.replace(".", "_")
        dotted = _register_handler(fn_name, _wallet_post(path))
        read_only = effects == "read_only"
        reg.add(
            CommandSpec(
                command_id=command_id,
                group="wallet",
                description=description,
                input_schema=RouteInput,
                effects=effects,
                capabilities=frozenset() if read_only else frozenset({"change_settings"}),
                permission=OpenRead() if read_only else _AUTHORITY,
                availability=None if read_only else _WALLET_AVAILABILITY,
                handler=Handler(dotted),
                exit_codes=(0, 2, 21, 22, 30, 42) if not read_only else (0, 2, 42),
            )
        )
        ROUTE_COMMANDS[f"http:POST:{path}"] = command_id

    for command_id, path, effects, description in MOBILE_ACTIONS:
        fn_name = "_h_" + command_id.replace(".", "_")
        dotted = _register_handler(fn_name, _mobile_post(path))
        reg.add(
            CommandSpec(
                command_id=command_id,
                group="mobile",
                description=description,
                input_schema=RouteInput,
                effects=effects,
                capabilities=frozenset({"change_settings"}),
                permission=_AUTHORITY,
                availability=_MOBILE_AVAILABILITY,
                handler=Handler(dotted),
                exit_codes=(0, 2, 21, 22, 30, 42),
            )
        )
        ROUTE_COMMANDS[f"http:POST:{path}"] = command_id
    register_inline(reg)
    register_chat_actions(reg)


# ---------------------------------------------------------------------------
# The inline families: profile writes, the OAuth code intake, the media editor.
#
# These are not delegated to a handler module -- their bodies live inline in
# service.py -- so the commands call the SAME authority functions those bodies
# call, exactly as settings.prefs.* does. No logic is re-implemented.
# ---------------------------------------------------------------------------


def _profile_action(action: str):
    def _run(inp: RouteInput, _ctx: Any) -> Any:
        from core import operator_profile as op

        body = dict(inp.body or {})

        def _s(key: str, default: str = "") -> str:
            return str(body.get(key) or default).strip()

        principal = op.principal_for_request({"_owner_local": True, "surface": "command"})
        revision = body.get("expected_revision")
        expected = int(revision) if isinstance(revision, int) else None
        try:
            if action == "remember":
                data = op.remember(
                    principal, _s("category"), body.get("value"),
                    scope=_s("scope", "global") or "global", actor="operator",
                )
            elif action == "item":
                data = op.edit_item(_s("item_id"), body.get("value"),
                                    expected_revision=expected, actor="operator")
            elif action == "forget":
                data = op.forget_item(_s("item_id"), expected_revision=expected, actor="operator")
            elif action == "scope":
                data = op.move_scope(_s("item_id"), _s("scope"), scope_key=_s("scope_key"),
                                     expected_revision=expected, actor="operator")
            elif action == "candidate":
                data = op.decide_candidate(_s("candidate_id"), _s("action"),
                                           value=body.get("value"), actor="operator")
            elif action == "resolve":
                data = op.resolve_conflict(_s("candidate_id"), _s("action"), scope=_s("scope"),
                                           scope_key=_s("scope_key"), actor="operator")
            elif action == "restore":
                data = op.restore_previous(_s("item_id"), actor="operator")
            else:  # pause
                op.set_paused(principal, bool(body.get("paused")))
                data = {"ok": True, "paused": op.is_paused(principal)}
        except Exception as exc:
            return HandlerFault(
                fault_code="fault_validation",
                summary=f"operator profile {action} refused",
                detail={"legacy_status": 400, "ok": False, "error": str(exc)},
            )
        payload = data if isinstance(data, dict) else {"ok": True, "result": data}
        return HandlerOk(data=payload, summary=f"operator profile {action}")

    return _run


def _auth_callback(inp: RouteInput, _ctx: Any) -> Any:
    from core.oauth_callback import record_callback

    body = dict(inp.body or {})
    provider = str(body.get("provider") or "").strip().lower()
    code = str(body.get("code") or "").strip()
    state = str(body.get("state") or "").strip()
    if not provider or not code:
        return HandlerFault(
            fault_code="usage",
            summary="auth callback requires provider and code",
            detail={"legacy_status": 400, "ok": False, "error": "provider and code are required"},
        )
    try:
        record_callback(provider, code, state)
    except Exception as exc:
        return HandlerFault(
            fault_code="fault_validation",
            summary="auth callback refused",
            detail={"legacy_status": 400, "ok": False, "error": str(exc)},
        )
    # The authorization CODE is never echoed back: it is exchanged for a key and
    # held in memory only.
    return HandlerOk(data={"ok": True, "provider": provider}, summary="auth callback recorded")


def _media_editor_action(action: str):
    def _run(inp: RouteInput, ctx: Any) -> Any:
        from core.web.api.media_editor_api import handle_media_editor_post

        return _response_to_result(
            handle_media_editor_post(
                f"/media-editor/{action}", dict(inp.body or {}), client_host=_peer(ctx)
            ),
            summary=f"media editor {action}",
        )

    return _run


def _probe_profile_store(_context: dict) -> tuple[bool, str]:
    try:
        from core.operator_profile import is_paused, principal_for_request

        is_paused(principal_for_request({"_owner_local": True, "surface": "probe"}))
        return True, ""
    except Exception as exc:  # pragma: no cover
        return False, f"operator profile store unreachable: {exc}"


def _probe_media_editor(_context: dict) -> tuple[bool, str]:
    try:
        import core.web.api.media_editor_api as _m

        return bool(_m), ""
    except Exception as exc:  # pragma: no cover
        return False, f"media editor unreachable: {exc}"


PROFILE_ACTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("profile.remember", "remember", "mutating", "Remember a typed fact in the operator profile"),
    ("profile.item.edit", "item", "mutating", "Edit one operator-profile item"),
    ("profile.item.forget", "forget", "destructive", "Forget one operator-profile item"),
    ("profile.item.scope", "scope", "mutating", "Move one profile item between scopes"),
    ("profile.candidate.decide", "candidate", "mutating", "Accept or reject a profile candidate"),
    ("profile.conflict.resolve", "resolve", "mutating", "Resolve a profile conflict"),
    ("profile.item.restore", "restore", "mutating", "Restore an item's previous value"),
    ("profile.pause", "pause", "mutating", "Pause or resume operator-profile learning"),
)

MEDIA_EDITOR_ACTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("media.editor.open", "open", "read_only", "Open a media project for editing"),
    ("media.editor.op", "op", "mutating", "Apply one edit operation to a media project"),
    ("media.editor.undo", "undo", "mutating", "Undo the last media edit"),
    ("media.editor.redo", "redo", "mutating", "Redo the last undone media edit"),
    ("media.editor.selection", "selection", "mutating", "Update the media editor selection"),
    ("media.editor.export", "export", "mutating", "Export a media project to an operator-named file"),
)


def inline_route_command_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for command_id, action, _effects, _description in PROFILE_ACTIONS:
        mapping[f"http:POST:/api/profile/{action}"] = command_id
    mapping["http:POST:/api/profile/*"] = PROFILE_ACTIONS[0][0]
    mapping["http:POST:/api/auth/*"] = "auth.callback.record"
    for command_id, action, _effects, _description in MEDIA_EDITOR_ACTIONS:
        mapping[f"http:POST:/media-editor/{action}"] = command_id
    mapping["http:POST:/media-editor/*"] = MEDIA_EDITOR_ACTIONS[0][0]
    mapping["http:POST:/api/learning/procedures/*"] = "learning.invalidate"
    mapping["http:POST:/api/mobile/devices"] = "mobile.devices.revoke"
    return mapping


def register_inline(reg: CommandRegistry) -> None:
    reg.add_group(GroupSpec(group_id="profile", description="the operator's typed profile"))
    reg.add_group(GroupSpec(group_id="auth", description="OAuth authorization-code intake"))
    reg.add_group(GroupSpec(group_id="media", description="media editor projects and exports"))

    _profile_availability = Availability(
        probe="core.command_registry.groups.delegated_routes:_probe_profile_store"
    )
    _media_availability = Availability(
        probe="core.command_registry.groups.delegated_routes:_probe_media_editor"
    )

    for command_id, action, effects, description in PROFILE_ACTIONS:
        dotted = _register_handler("_h_" + command_id.replace(".", "_"), _profile_action(action))
        reg.add(
            CommandSpec(
                command_id=command_id, group="profile", description=description,
                input_schema=RouteInput, effects=effects,
                capabilities=frozenset({"change_settings"}),
                permission=_AUTHORITY, availability=_profile_availability,
                handler=Handler(dotted), exit_codes=(0, 2, 21, 22, 30, 42),
            )
        )

    dotted = _register_handler("_h_auth_callback_record", _auth_callback)
    reg.add(
        CommandSpec(
            command_id="auth.callback.record", group="auth",
            description=(
                "Record an OAuth authorization code delivered to the local callback. "
                "The code is exchanged for a key and never echoed or logged."
            ),
            input_schema=RouteInput, effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=_AUTHORITY,
            availability=Availability(
                probe="core.command_registry.groups.delegated_routes:_probe_oauth_callback"
            ),
            handler=Handler(dotted), exit_codes=(0, 2, 21, 22, 42),
        )
    )

    for command_id, action, effects, description in MEDIA_EDITOR_ACTIONS:
        dotted = _register_handler(
            "_h_" + command_id.replace(".", "_"), _media_editor_action(action)
        )
        read_only = effects == "read_only"
        reg.add(
            CommandSpec(
                command_id=command_id, group="media", description=description,
                input_schema=RouteInput, effects=effects,
                capabilities=frozenset() if read_only else frozenset({"change_settings"}),
                permission=OpenRead() if read_only else _AUTHORITY,
                availability=None if read_only else _media_availability,
                handler=Handler(dotted),
                exit_codes=(0, 2, 42) if read_only else (0, 2, 21, 22, 30, 42),
            )
        )


def _probe_oauth_callback(_context: dict) -> tuple[bool, str]:
    try:
        import core.oauth_callback as _o

        return bool(_o), ""
    except Exception as exc:  # pragma: no cover
        return False, f"oauth callback store unreachable: {exc}"


# ---------------------------------------------------------------------------
# Chat-session actions.
#
# The census classified all of `/api/chat/...` as machine-lane transport, because
# `_MACHINE_TRANSPORT_PATTERNS` carried a bare `^/api/chat`. A wildcard that
# absorbs surfaces nobody reviewed is how a false zero survives, so the pattern is
# anchored and every sub-path is now judged on its own. The READS are read feeds
# and classified as such by name; these six are operator ACTIONS and get owners.
#
# Each calls the same authority the inline route body calls. The route keeps its
# own transport guards (content-type, Origin, body size) because those are
# transport concerns; the command adds the declared owner, the typed effects class
# and the effect-budget door, on every projection rather than only over HTTP.
# ---------------------------------------------------------------------------


def _chat_pin(inp: RouteInput, _ctx: Any) -> Any:
    from core import message_pins

    body = dict(inp.body or {})
    session = str(body.get("session_id") or "").strip()
    if not session:
        return HandlerFault(
            fault_code="usage", summary="pin requires session_id",
            detail={"legacy_status": 400, "ok": False, "error": "session_id is required"},
        )
    try:
        if body.get("delete"):
            result = message_pins.unpin(session, str(body.get("pin_id") or "").strip())
        else:
            result = message_pins.pin(
                session, role=str(body.get("role") or ""), text=str(body.get("text") or ""),
                ts=body.get("ts"),
            )
    except Exception as exc:
        return HandlerFault(
            fault_code="fault_validation", summary="pin refused",
            detail={"legacy_status": 400, "ok": False, "error": str(exc)},
        )
    payload = result if isinstance(result, dict) else {"ok": True, "result": result}
    return HandlerOk(data=payload, summary="chat pin updated")


def _chat_cancel(inp: RouteInput, _ctx: Any) -> Any:
    from core.web.api.turn_cancel import request_cancel

    body = dict(inp.body or {})
    session = str(body.get("session_id") or "").strip()
    turn = str(body.get("turn_id") or "").strip()
    if not session or not turn:
        return HandlerFault(
            fault_code="usage", summary="cancel requires session_id and turn_id",
            detail={"legacy_status": 400, "ok": False, "error": "session_id and turn_id are required"},
        )
    state = request_cancel(session, turn)
    return HandlerOk(data={"ok": True, "state": state}, summary=f"turn cancel: {state}")


def _chat_attachment_remove(inp: RouteInput, _ctx: Any) -> Any:
    from core import chat_attachments

    body = dict(inp.body or {})
    session = str(body.get("session_id") or "").strip()
    attachment = str(body.get("attachment_id") or "").strip()
    if not session or not attachment:
        return HandlerFault(
            fault_code="usage", summary="remove requires session_id and attachment_id",
            detail={"legacy_status": 400, "ok": False, "error": "session_id and attachment_id are required"},
        )
    removed = chat_attachments.remove_staged(session_id=session, attachment_id=attachment)
    return HandlerOk(data={"ok": True, "removed": bool(removed)}, summary="staged attachment removed")


def _chat_session_meta(inp: RouteInput, _ctx: Any) -> Any:
    from core import project_store

    body = dict(inp.body or {})
    session = str(body.get("session_id") or "").strip()
    if not session:
        return HandlerFault(
            fault_code="usage", summary="session update requires session_id",
            detail={"legacy_status": 400, "ok": False, "error": "session_id is required"},
        )
    try:
        if "title" in body:
            project_store.set_session_title(session, str(body.get("title") or ""))
        if "archived" in body:
            project_store.set_session_archived(session, bool(body.get("archived")))
    except Exception as exc:
        return HandlerFault(
            fault_code="fault_validation", summary="session update refused",
            detail={"legacy_status": 400, "ok": False, "error": str(exc)},
        )
    return HandlerOk(data={"ok": True, "session_id": session}, summary="chat session metadata updated")


def _chat_queue(inp: RouteInput, _ctx: Any) -> Any:
    from core.runtime_continuity import cancel_queue_item, enqueue_message

    body = dict(inp.body or {})
    session = str(body.get("session_id") or "").strip()
    if not session:
        return HandlerFault(
            fault_code="usage", summary="queue requires session_id",
            detail={"legacy_status": 400, "ok": False, "error": "session_id is required"},
        )
    try:
        item = str(body.get("queue_id") or "").strip()
        if item:
            result = {"ok": True, "cancelled": bool(cancel_queue_item(session, item))}
        else:
            result = {"ok": True, "queued": enqueue_message(session, str(body.get("text") or ""))}
    except Exception as exc:
        return HandlerFault(
            fault_code="fault_validation", summary="queue action refused",
            detail={"legacy_status": 400, "ok": False, "error": str(exc)},
        )
    return HandlerOk(data=result, summary="chat queue updated")


def _chat_dictation(inp: RouteInput, _ctx: Any) -> Any:
    from core import dictation as dictation_authority

    body = dict(inp.body or {})
    try:
        availability = dictation_authority.availability()
    except Exception as exc:
        return HandlerFault(
            fault_code="unavailable", summary="dictation unavailable",
            detail={"legacy_status": 503, "ok": False, "error": str(exc)},
        )
    return HandlerOk(
        data={"ok": True, "availability": availability, "session_id": str(body.get("session_id") or "")},
        summary="dictation availability",
    )


def _probe_chat_actions(_context: dict) -> tuple[bool, str]:
    try:
        from core import message_pins

        return bool(message_pins), ""
    except Exception as exc:  # pragma: no cover
        return False, f"chat action authorities unreachable: {exc}"


CHAT_ACTIONS: tuple[tuple[str, str, str, str, Any], ...] = (
    ("chat.pin", "/api/chat/pin", "mutating", "Pin or unpin one message in a chat", _chat_pin),
    ("chat.cancel", "/api/chat/cancel", "mutating", "Cancel one in-flight turn", _chat_cancel),
    ("chat.attachment.remove", "/api/chat/attachments/remove", "destructive",
     "Drop one staged-but-unsent attachment from this chat", _chat_attachment_remove),
    ("chat.session.update", "/api/chat/session", "mutating",
     "Set a chat's title or archived flag (metadata only; never the transcript)",
     _chat_session_meta),
    ("chat.queue.update", "/api/chat/queue", "mutating",
     "Queue a message or cancel a queued one", _chat_queue),
    ("chat.dictation", "/api/chat/dictation", "mutating",
     "On-device dictation for this chat; writes an Activity event", _chat_dictation),
)


def chat_action_command_map() -> dict[str, str]:
    return {f"http:POST:{path}": cid for cid, path, _e, _d, _fn in CHAT_ACTIONS}


def register_chat_actions(reg: CommandRegistry) -> None:
    reg.add_group(GroupSpec(group_id="chat", description="chat session actions (owner-local)"))
    availability = Availability(
        probe="core.command_registry.groups.delegated_routes:_probe_chat_actions"
    )
    for command_id, _path, effects, description, fn in CHAT_ACTIONS:
        dotted = _register_handler("_h_" + command_id.replace(".", "_"), fn)
        reg.add(
            CommandSpec(
                command_id=command_id, group="chat", description=description,
                input_schema=RouteInput, effects=effects,
                capabilities=frozenset({"change_settings"}),
                permission=_AUTHORITY, availability=availability,
                handler=Handler(dotted), exit_codes=(0, 2, 21, 22, 30, 42),
            )
        )
