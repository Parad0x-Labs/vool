"""Convergence commands: release-critical legacy actions migrated into the
registry so the census can reach zero LEGACY_UNMIGRATED.

Every handler binds the SAME authority the legacy route/CLI called — one
authority traversal, no re-declaration. Non-200 authority answers file
``legacy_status`` in fault detail so the legacy adapter can preserve the
transport shape byte-for-byte.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from core.command_registry.spec import (
    ApprovalDecision,
    ApprovalGate,
    AuthorityDecision,
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
    OperatorAuthority,
)


def _legacy_fault(code: str, summary: str, status: int, payload: dict | None = None) -> HandlerFault:
    detail: dict[str, Any] = {"legacy_status": int(status)}
    if isinstance(payload, dict):
        detail.update(payload)
    return HandlerFault(fault_code=code, summary=summary, detail=detail)


def _from_api_response(resp, *, ok_summary: str, fault_summary: str, receipts: tuple = ()) -> Any:
    import json as _json

    payload = _json.loads(resp.body) if resp.body else {}
    if resp.status == 200:
        return HandlerOk(data=payload, summary=ok_summary, receipts=receipts)
    code = {400: "usage", 403: "permission_denied", 404: "fault_validation", 409: "conflict"}.get(resp.status, "fault_tool")
    return _legacy_fault(code, fault_summary, resp.status, payload if isinstance(payload, dict) else {"error": str(payload)})


def _gate_operator(inp, ctx) -> AuthorityDecision:
    if str(ctx.principal or "") != "operator":
        return AuthorityDecision(granted=False, reason="operator-only command")
    return AuthorityDecision(granted=True)


def _gate_two_press(inp, ctx) -> ApprovalDecision:
    approval = ctx.approval_context or {}
    if not str(approval.get("gesture") or approval.get("authority_token") or "").strip():
        return ApprovalDecision(required=True, reason="two-press consent required (the press is created server-side from the real UI gesture)")
    return ApprovalDecision(required=False)


def _probe_update_subsystem(context: dict) -> tuple[bool, str]:
    try:
        from core.updater.runtime import boot_update_subsystem

        boot_update_subsystem()
        return True, ""
    except Exception as exc:
        return False, f"update subsystem unavailable: {exc}"


# ---------------------------------------------------------------------------
# update group additions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpdateStatusInput:
    pass


@dataclass(frozen=True)
class UpdateApplyInput:
    resolve_destructive: str = ""


def _handle_update_status(inp, ctx):
    from core.updater.runtime import get_update_subsystem

    subsystem = get_update_subsystem()
    if subsystem is None:
        return HandlerOk(
            data={
                "ok": True,
                "configured": False,
                "booted": False,
                "unavailable_plain": "the update service is not running in this process",
                "phase": "unavailable",
                "message": "Updates are unavailable — the update service is not running in this process.",
            },
            summary="Update service not running in this process",
        )
    payload = subsystem.status_payload()
    payload["ok"] = True
    payload["booted"] = True
    return HandlerOk(data=payload, summary="Update subsystem status")


def _handle_update_apply(inp, ctx) -> Any:
    import time as _time

    from core.updater.runtime import boot_update_subsystem
    from core.updater.service import UserGesture

    subsystem = boot_update_subsystem()
    gesture = UserGesture(pressed_at=_time.time(), origin=str((ctx.approval_context or {}).get("origin") or "web-ui"))
    resolution = _destructive_resolution(inp.resolve_destructive)
    outcome = subsystem.press_install(gesture, resolution=resolution)
    payload = dict(outcome.to_dict())
    payload["ok"] = outcome.accepted
    payload.update({k: v for k, v in subsystem.status_payload().items() if k in ("phase", "message", "target_version")})
    if not outcome.accepted:
        return _legacy_fault("conflict", f"update apply refused: {outcome.detail or outcome.phase}", 409, payload)
    return HandlerOk(
        data=payload,
        summary="Update applied (snapshot + rollback point per the update transaction)",
        receipts=({"kind": "update_applied", "txid": outcome.txid, "origin": gesture.origin},),
    )


def _destructive_resolution(override: str):
    override = str(override or "").strip()
    if not override:
        return None
    from core.updater.work import DestructiveWorkResolution

    return DestructiveWorkResolution(
        operator_ack="acknowledged via the command registry",
        work_ids=tuple(part.strip() for part in override.split(",") if part.strip()),
    )


def _handle_update_restart(inp, ctx) -> Any:
    import time as _time

    from core.updater.runtime import boot_update_subsystem
    from core.updater.service import UserGesture

    subsystem = boot_update_subsystem()
    gesture = UserGesture(pressed_at=_time.time(), origin=str((ctx.approval_context or {}).get("origin") or "web-ui"))
    outcome = subsystem.press_restart(gesture, resolution=_destructive_resolution(inp.resolve_destructive))
    payload = dict(outcome.to_dict())
    payload["ok"] = outcome.accepted
    payload.update({k: v for k, v in subsystem.status_payload().items() if k in ("phase", "message", "target_version")})
    if not outcome.accepted:
        return _legacy_fault("conflict", f"update restart refused: {outcome.detail or outcome.phase}", 409, payload)
    return HandlerOk(
        data=payload,
        summary="Restart flow executed",
        receipts=({"kind": "update_restart", "txid": outcome.txid, "origin": gesture.origin},),
    )


# ---------------------------------------------------------------------------
# settings group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrefsSetInput:
    prefs: dict[str, Any] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class CredentialsSetInput:
    payload: dict = dataclasses.field(default_factory=dict)


def _handle_prefs_list(inp, ctx):
    from core.user_preferences import load_preferences, user_address

    p = load_preferences()
    # Every field settings.prefs.set accepts is read back here. A surface that can WRITE a value it
    # cannot READ cannot tell a saved write from a refused or renormalised one, so the two lists
    # are kept in step deliberately. tone_hint and timezone are absent from both: nothing in the
    # product reads them.
    return HandlerOk(
        data={
            "user_address": user_address(),
            "humor_percent": p.humor_percent,
            "deep_reasoning": p.deep_reasoning,
            "communication_style": p.communication_style,
            "autonomy_mode": p.autonomy_mode,
            "ram_reserve_pct": p.ram_reserve_pct,
            "daily_token_budget": p.daily_token_budget,
            "boundaries_mode": p.boundaries_mode,
            "profanity_level": p.profanity_level,
            "character_mode": p.character_mode,
            "style_notes": p.style_notes,
            "show_workflow": p.show_workflow,
            "hive_followups": p.hive_followups,
            "idle_research_assist": p.idle_research_assist,
            "accept_hive_tasks": p.accept_hive_tasks,
            "social_commons": p.social_commons,
            "wallet_enabled": p.wallet_enabled,
            "setup_skipped_steps": p.setup_skipped_steps,
            "setup_dismissed": p.setup_dismissed,
            "speech_notice_dismissed": p.speech_notice_dismissed,
        },
        summary="User preferences",
    )


def _handle_prefs_set(inp, ctx):
    from core.web.api.registry_authorities import set_prefs_authority
    from core.web.api.runtime import RuntimeServices

    resp = set_prefs_authority(dict(inp.prefs), _headers(ctx), RuntimeServices(display_name="VOOL"), _peer(ctx))
    return _from_api_response(
        resp,
        ok_summary="Preferences saved",
        fault_summary="Preference write refused",
        receipts=({"kind": "settings_write", "surface": "prefs"},),
    )


def _handle_credentials_list(inp, ctx):
    from core.credential_store import list_credentials

    return HandlerOk(data={"credentials": list_credentials()}, summary="Credential slots")


def _handle_credentials_set(inp, ctx):
    from core.web.api.registry_authorities import set_credentials_authority
    from core.web.api.runtime import RuntimeServices

    resp = set_credentials_authority(dict(inp.payload or {}), _headers(ctx), RuntimeServices(display_name="VOOL"), _peer(ctx))
    return _from_api_response(
        resp,
        ok_summary="Credential slot written",
        fault_summary="Credential write refused",
        receipts=({"kind": "settings_write", "surface": "credentials"},),
    )


# ---------------------------------------------------------------------------
# email: the operator's account and draft-store recovery surfaces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmailRecoveryInput:
    payload: dict = dataclasses.field(default_factory=dict)


def _handle_email_accounts_list(inp, ctx):
    from core.email_accounts import accounts_snapshot

    return HandlerOk(data=accounts_snapshot(), summary="Email accounts")


def _handle_email_recovery_status(inp, ctx):
    from core.email_drafts import recovery_surface_view

    return HandlerOk(data=recovery_surface_view(), summary="Email draft store recovery")


def _handle_email_recovery_check(inp, ctx):
    from core.web.api.registry_authorities import email_recovery_check_authority
    from core.web.api.runtime import RuntimeServices

    resp = email_recovery_check_authority(dict(inp.payload or {}), _headers(ctx), RuntimeServices(display_name="VOOL"), _peer(ctx))
    return _from_api_response(
        resp,
        ok_summary="Recovery check recorded",
        fault_summary="Recovery check refused",
        receipts=({"kind": "email_recovery", "surface": "check"},),
    )


def _handle_email_recovery_acknowledge(inp, ctx):
    from core.web.api.registry_authorities import email_recovery_acknowledge_authority
    from core.web.api.runtime import RuntimeServices

    resp = email_recovery_acknowledge_authority(dict(inp.payload or {}), _headers(ctx), RuntimeServices(display_name="VOOL"), _peer(ctx))
    return _from_api_response(
        resp,
        ok_summary="Store recovery acknowledged",
        fault_summary="Acknowledgement refused",
        receipts=({"kind": "email_recovery", "surface": "acknowledge"},),
    )


def _probe_email_drafts(context: dict) -> tuple[bool, str]:
    try:
        from core.email_drafts import _drafts_path

        _drafts_path()
        return True, ""
    except Exception as exc:
        return False, f"email draft store unreachable: {exc}"


# ---------------------------------------------------------------------------
# models group additions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPinInput:
    payload: dict = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class ModelCurrentInput:
    pass


@dataclass(frozen=True)
class ModelAutoInput:
    model: str


def _handle_models_current(inp, ctx):
    from core import cloud_escalation_policy as _cep

    try:
        policy = _cep.load_policy()
        payload = {
            "ok": True,
            "model": str(policy.model or ""),
            "provider": str(policy.provider or ""),
            "free_cloud_enabled": bool(policy.free_cloud_enabled),
            "auto_free_model": str(getattr(policy, "auto_free_model", "") or ""),
            "source": "server",
        }
        pinned = str(payload["model"] or "").strip()
        if pinned:
            try:
                from core.cloud_model_control import classify_cloud_model_cost

                payload["cost_state"] = classify_cloud_model_cost(
                    provider_id=str(payload["provider"] or "") or "openrouter",
                    model_id=pinned,
                )["cost_state"]
            except Exception:
                payload["cost_state"] = "unknown"
    except Exception:
        payload = {"ok": False, "model": "", "provider": "", "source": "server", "error": "unavailable"}
    return HandlerOk(data=payload, summary=f"Pinned model: {payload.get('model') or 'none'}")


def _handle_models_auto(inp, ctx):
    from core.cloud_model_control import set_auto_free_model
    from core.request_trust import is_loopback_host

    ok, message, chosen = set_auto_free_model(inp.model.strip(), owner_local=is_loopback_host(_peer(ctx)))
    status_code = 200 if ok else 403 if "local session" in message else 400
    if not ok:
        return _legacy_fault("fault_validation" if status_code == 400 else "permission_denied", message, status_code, {"ok": ok, "message": message, "model": chosen})
    return HandlerOk(
        data={"ok": ok, "message": message, "model": chosen},
        summary=f"Auto free-model set: {chosen}",
        receipts=({"kind": "model_auto", "model": chosen},),
    )


def _peer(ctx) -> str:
    return str((ctx.extra or {}).get("client_host") or "127.0.0.1")


def _headers(ctx) -> dict:
    return dict((ctx.extra or {}).get("headers") or {})


def _handle_models_pin(inp, ctx):
    from core.web.api.registry_authorities import set_cloud_model_authority
    from core.web.api.runtime import RuntimeServices

    resp = set_cloud_model_authority(dict(inp.payload or {}), _headers(ctx), RuntimeServices(display_name="VOOL"), _peer(ctx))
    pinned = str((inp.payload or {}).get("model") or "")
    return _from_api_response(
        resp,
        ok_summary=f"Cloud model pinned: {pinned}",
        fault_summary="Model pin refused",
        receipts=({"kind": "model_pin", "model": pinned},),
    )


# ---------------------------------------------------------------------------
# search group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchProvidersInput:
    pass


@dataclass(frozen=True)
class SearchTestInput:
    provider: str


def _handle_search_providers(inp, ctx):
    from core.search_connection_state import connection_rows

    return HandlerOk(data={"providers": connection_rows()}, summary="Search providers")


def _handle_search_test(inp, ctx):
    from core.search_connection_state import probe_search_provider

    payload = probe_search_provider(inp.provider.strip().lower())
    return HandlerOk(data=payload, summary=f"Search probe: {inp.provider}")


# ---------------------------------------------------------------------------
# memory group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryForgetText:
    record_id: str


def _handle_memory_forget(inp, ctx):
    from core.memory.entries import MemoryErasureError, forget_memory_record

    try:
        removed = forget_memory_record(inp.record_id)
    except MemoryErasureError:
        # P0 success-after-verification law at the served edge: a forget that
        # could not be verified absent from every model-visible memory path is
        # never acknowledged. The durable tombstone has landed; the reply and
        # the receipt both say NOT applied so the operator retries the forget
        # instead of trusting a removal that did not verify.
        return HandlerOk(
            data={"ok": False, "removed": False, "error": "erasure_unverified"},
            summary=(
                f"Memory record {inp.record_id} NOT forgotten: erasure could not be "
                "verified on every memory path — retry the forget"
            ),
            receipts=(
                {"kind": "memory_forget", "record_id": inp.record_id, "removed": False},
            ),
        )
    except Exception:
        removed = False
    return HandlerOk(
        data={"ok": True, "removed": bool(removed)},
        summary=f"Memory record {inp.record_id} forgotten (removed={bool(removed)})",
        receipts=({"kind": "memory_forget", "record_id": inp.record_id, "removed": bool(removed)},),
    )


# ---------------------------------------------------------------------------
# tasks group addition: coding-task recovery control
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskRecoveryInput:
    session_id: str
    checkpoint_id: str
    action: str


def _probe_runtime_continuity(context: dict) -> tuple[bool, str]:
    try:
        from core.runtime_continuity import _conn as _rc

        _rc()
        return True, ""
    except Exception as exc:
        return False, f"runtime continuity store unreachable: {exc}"


def _handle_task_recovery(inp, ctx):
    from core.web.api.registry_authorities import task_recovery_authority
    from core.web.api.runtime import RuntimeServices

    resp = task_recovery_authority(
        {"session_id": inp.session_id, "checkpoint_id": inp.checkpoint_id, "action": inp.action},
        RuntimeServices(display_name="VOOL"),
        _peer(ctx),
    )
    return _from_api_response(
        resp,
        ok_summary=f"Task checkpoint {inp.checkpoint_id} recovered",
        fault_summary="Task recovery refused",
        receipts=({"kind": "task_recovery", "checkpoint_id": inp.checkpoint_id, "action": inp.action},),
    )


# ---------------------------------------------------------------------------
# status + capabilities groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusShowInput:
    pass


def _handle_status_show(inp, ctx):
    from core.vool_user_summary import build_user_summary

    summary = build_user_summary()
    return HandlerOk(data=summary, summary="Vool status: what it learned, stored, indexed, and exchanged")


def _handle_capabilities_list(inp, ctx):
    from core.runtime_capabilities import runtime_capability_snapshot

    snap = runtime_capability_snapshot()
    contracts = snap.get("tools") or snap.get("contracts") or []
    return HandlerOk(
        data={"snapshot_keys": sorted(snap.keys()), "contracts": contracts},
        summary=f"{len(contracts) if isinstance(contracts, list) else 'runtime'} capability entr(ies) — model-facing truth from the capability graph",
    )


# ---------------------------------------------------------------------------
# approvals group additions: bypass + mode
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BypassActivateInput:
    session_id: str
    scope: str = "task"
    duration_seconds: int = 900
    project_id: str = ""
    task_id: str = ""
    confirmation_id: str = ""
    until_off: bool = False
    workspace_root: str = ""


@dataclass(frozen=True)
class BypassRevokeInput:
    token: str


@dataclass(frozen=True)
class ModeSetInput:
    session_id: str
    mode: str


def _handle_bypass_activate(inp, ctx):
    from core.mode_permission_policy import activate_bypass_grant

    grant = activate_bypass_grant(
        session_id=inp.session_id,
        project_id=inp.project_id,
        task_id=inp.task_id,
        scope=inp.scope,
        duration_seconds=int(inp.duration_seconds),
        confirmation_id=str(inp.confirmation_id or ""),
        until_off=bool(inp.until_off),
        workspace_root=str(inp.workspace_root or ""),
    )
    lifetime = "until turned off" if grant.get("until_off") else "bounded, expiring"
    return HandlerOk(
        data={"ok": True, "grant": grant},
        summary=f"Approval-bypass scope granted ({lifetime})",
        receipts=({"kind": "bypass_grant", "session_id": inp.session_id, "scope": inp.scope},),
    )


def _handle_bypass_revoke(inp, ctx):
    from core.mode_permission_policy import revoke_bypass_grant

    revoked = revoke_bypass_grant(inp.token)
    if not revoked:
        return _legacy_fault("conflict", "no such bypass grant", 404, {"ok": False, "error": "no such grant"})
    return HandlerOk(data={"ok": True, "revoked": True}, summary="Bypass grant revoked")


def _handle_mode_set(inp, ctx):
    from core.mode_permission_policy import normalize_mode, set_active_mode

    normalized = normalize_mode(inp.mode)
    if normalized is None:
        return _legacy_fault("usage", f"unknown mode {inp.mode!r}", 400, {"ok": False, "error": f"unknown mode {inp.mode!r}"})
    set_active_mode(inp.session_id, normalized)
    return HandlerOk(
        data={"ok": True, "mode": normalized},
        summary=f"Active mode set to {normalized}",
        receipts=({"kind": "mode_set", "session_id": inp.session_id, "mode": normalized},),
    )


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="settings", description="operator settings: preferences and credential slots"))
    reg.add_group(GroupSpec(group_id="search", description="search provider configuration and probes"))
    reg.add_group(GroupSpec(group_id="memory", description="persistent memory records"))
    reg.add_group(GroupSpec(group_id="status", description="the one health+defaults read"))
    reg.add_group(GroupSpec(group_id="capabilities", description="model-facing capability truth (the capability graph is the model authority; this is its operator read)"))

    # -- update additions ----------------------------------------------------
    reg.add(
        CommandSpec(
            command_id="update.status",
            group="update",
            description="Update subsystem status (channel, feed, pending migrations)",
            input_schema=UpdateStatusInput,
            effects="read_only",
            capabilities=frozenset({"self_update.check"}),
            handler=Handler("core.command_registry.groups.convergence:_handle_update_status"),
            exit_codes=(0, 2, 10),
        )
    )
    reg.add(
        CommandSpec(
            command_id="update.apply",
            group="update",
            description="Apply the staged update (two-press consent; snapshot + rollback)",
            input_schema=UpdateApplyInput,
            effects="mutating",
            capabilities=frozenset({"self_update.apply"}),
            permission=ApprovalGate(
                kind="self_update.apply",
                verifier="core.command_registry.groups.convergence:_gate_two_press",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_update_apply"),
            availability=Availability("core.command_registry.groups.convergence:_probe_update_subsystem"),
            fault_bindings=(FaultBinding(when="refused", fault_code="conflict", remediation=("vool update status",)),),
            exit_codes=(0, 2, 10, 20, 30),
            lifecycle="preview",
            next_actions=(NextAction(command_id="update.status", label="Check update health"),),
        )
    )
    reg.add(
        CommandSpec(
            command_id="update.restart",
            group="update",
            description="Run the update restart flow (two-press consent)",
            input_schema=UpdateApplyInput,
            effects="mutating",
            capabilities=frozenset({"self_update.apply"}),
            permission=ApprovalGate(
                kind="self_update.restart",
                verifier="core.command_registry.groups.convergence:_gate_two_press",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_update_restart"),
            availability=Availability("core.command_registry.groups.convergence:_probe_update_subsystem"),
            exit_codes=(0, 2, 10, 20, 30),
            lifecycle="preview",
        )
    )

    # -- settings ------------------------------------------------------------
    reg.add(
        CommandSpec(
            command_id="settings.prefs.list",
            group="settings",
            description="List user preferences",
            effects="read_only",
            handler=Handler("core.command_registry.groups.convergence:_handle_prefs_list"),
            exit_codes=(0,),
        )
    )
    reg.add(
        CommandSpec(
            command_id="settings.prefs.set",
            group="settings",
            description="Set user preferences (typed knobs; normalized on write)",
            input_schema=PrefsSetInput,
            effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=OperatorAuthority(
                kind="settings.write",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_prefs_set"),
            availability=Availability("core.command_registry.groups.convergence:_probe_prefs_store"),
            exit_codes=(0, 2, 21, 30),
        )
    )
    reg.add(
        CommandSpec(
            command_id="settings.credentials.list",
            group="settings",
            description="List credential slots (names only; secrets never returned)",
            effects="read_only",
            handler=Handler("core.command_registry.groups.convergence:_handle_credentials_list"),
            exit_codes=(0,),
        )
    )
    reg.add(
        CommandSpec(
            command_id="settings.credentials.set",
            group="settings",
            description="Set or remove one credential slot (sealed at rest)",
            input_schema=CredentialsSetInput,
            effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=OperatorAuthority(
                kind="settings.write",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_credentials_set"),
            availability=Availability("core.command_registry.groups.convergence:_probe_prefs_store"),
            exit_codes=(0, 2, 21),
        )
    )

    # -- email: accounts and the draft-store recovery hold (operator surfaces) ----
    reg.add_group(GroupSpec(group_id="email", description="email accounts and the draft store's sending hold"))
    reg.add(
        CommandSpec(
            command_id="email.accounts.list",
            group="email",
            description="List configured email accounts: identity, capabilities, state and the chosen default (secrets never returned)",
            effects="read_only",
            handler=Handler("core.command_registry.groups.convergence:_handle_email_accounts_list"),
            exit_codes=(0,),
        )
    )
    reg.add(
        CommandSpec(
            command_id="email.recovery.status",
            group="email",
            description="The email draft store's recovery hold as the operator sees it (evidence, named Message-IDs, checks)",
            effects="read_only",
            handler=Handler("core.command_registry.groups.convergence:_handle_email_recovery_status"),
            exit_codes=(0,),
        )
    )
    reg.add(
        CommandSpec(
            command_id="email.recovery.check",
            group="email",
            description="Look one held Message-ID up in its account's sent view and record the outcome (never resends)",
            input_schema=EmailRecoveryInput,
            effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=OperatorAuthority(
                kind="settings.write",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_email_recovery_check"),
            availability=Availability("core.command_registry.groups.convergence:_probe_email_drafts"),
            exit_codes=(0, 2, 21, 30),
        )
    )
    reg.add(
        CommandSpec(
            command_id="email.recovery.acknowledge",
            group="email",
            description="Acknowledge the exact current store recovery and release NEW approved sends (operator only; nothing is resent)",
            input_schema=EmailRecoveryInput,
            effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=OperatorAuthority(
                kind="settings.write",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_email_recovery_acknowledge"),
            availability=Availability("core.command_registry.groups.convergence:_probe_email_drafts"),
            exit_codes=(0, 2, 21, 30),
        )
    )

    # -- models additions ------------------------------------------------------
    reg.add(
        CommandSpec(
            command_id="models.current",
            group="models",
            description="The one authoritative cloud-model pin",
            effects="read_only",
            handler=Handler("core.command_registry.groups.convergence:_handle_models_current"),
            exit_codes=(0,),
        )
    )
    reg.add(
        CommandSpec(
            command_id="models.pin",
            group="models",
            description="Pin the cloud model (paid models keep their gate)",
            input_schema=ModelPinInput,
            effects="mutating",
            capabilities=frozenset({"change_provider_configuration"}),
            permission=OperatorAuthority(
                kind="models.pin",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_models_pin"),
            availability=Availability("core.command_registry.groups.convergence:_probe_cloud_policy"),
            exit_codes=(0, 2, 21, 42),
        )
    )

    reg.add(
        CommandSpec(
            command_id="models.auto",
            group="models",
            description="Set the Auto cloud free-model fallback (catalog-verified :free or 'auto')",
            input_schema=ModelAutoInput,
            effects="mutating",
            capabilities=frozenset({"change_provider_configuration"}),
            permission=OperatorAuthority(
                kind="models.auto",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_models_auto"),
            availability=Availability("core.command_registry.groups.convergence:_probe_cloud_policy"),
            exit_codes=(0, 2, 21, 42),
        )
    )

    # -- search ----------------------------------------------------------------
    reg.add(
        CommandSpec(
            command_id="search.providers",
            group="search",
            description="List search providers and their connection state",
            effects="read_only",
            handler=Handler("core.command_registry.groups.convergence:_handle_search_providers"),
            exit_codes=(0,),
        )
    )
    reg.add(
        CommandSpec(
            command_id="search.test",
            group="search",
            description="Probe one search provider connection",
            input_schema=SearchTestInput,
            effects="idempotent_write",
            capabilities=frozenset({"settings.search_provider_test"}),
            permission=ApprovalGate(
                kind="search.test",
                verifier="core.command_registry.groups.convergence:_gate_operator_approval",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_search_test"),
            availability=Availability("core.command_registry.groups.convergence:_probe_search_state"),
            exit_codes=(0, 2, 20),
        )
    )

    # -- memory ------------------------------------------------------------------
    reg.add(
        CommandSpec(
            command_id="memory.forget",
            group="memory",
            description="Remove exactly one remembered entry by record id",
            input_schema=MemoryForgetText,
            effects="destructive",
            permission=OperatorAuthority(
                kind="memory.forget",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_memory_forget"),
            availability=Availability("core.command_registry.groups.convergence:_probe_prefs_store"),
            exit_codes=(0, 2, 21, 42),
        )
    )

    # -- tasks addition ------------------------------------------------------------
    reg.add(
        CommandSpec(
            command_id="tasks.recovery",
            group="tasks",
            description="Cancel one stuck coding-task checkpoint (local session only)",
            input_schema=TaskRecoveryInput,
            effects="mutating",
            permission=OperatorAuthority(
                kind="tasks.recovery",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_task_recovery"),
            availability=Availability("core.command_registry.groups.convergence:_probe_runtime_continuity"),
            exit_codes=(0, 2, 21, 30, 42),
        )
    )

    # -- status + capabilities --------------------------------------------------------
    reg.add(
        CommandSpec(
            command_id="status.show",
            group="status",
            description="Health+defaults read: what Vool learned, stored, indexed, exchanged",
            aliases=("status",),
            effects="read_only",
            handler=Handler("core.command_registry.groups.convergence:_handle_status_show"),
            exit_codes=(0,),
            model_offerable=True
        )
    )
    reg.add(
        CommandSpec(
            command_id="capabilities.list",
            group="capabilities",
            description="Model-facing capability truth from the capability graph",
            aliases=("capabilities",),
            effects="read_only",
            handler=Handler("core.command_registry.groups.convergence:_handle_capabilities_list"),
            exit_codes=(0,),
            model_offerable=True
        )
    )

    # -- approvals additions ------------------------------------------------------------
    reg.add(
        CommandSpec(
            command_id="approvals.bypass.activate",
            group="approvals",
            description="Grant a bounded approval-bypass scope (expiring)",
            input_schema=BypassActivateInput,
            effects="mutating",
            permission=OperatorAuthority(
                kind="approvals.bypass",
                verifier="core.command_registry.groups.convergence:_gate_bypass_confirmation",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_bypass_activate"),
            availability=Availability("core.command_registry.groups.approvals:_probe_approval_store"),
            exit_codes=(0, 2, 21),
        )
    )
    reg.add(
        CommandSpec(
            command_id="approvals.bypass.revoke",
            group="approvals",
            description="Revoke an approval-bypass grant",
            input_schema=BypassRevokeInput,
            effects="mutating",
            permission=OperatorAuthority(
                kind="approvals.bypass",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_bypass_revoke"),
            availability=Availability("core.command_registry.groups.approvals:_probe_approval_store"),
            exit_codes=(0, 2, 21, 30),
        )
    )
    reg.add(
        CommandSpec(
            command_id="approvals.mode.set",
            group="approvals",
            description="Set the active permission mode for a session",
            input_schema=ModeSetInput,
            effects="mutating",
            permission=OperatorAuthority(
                kind="approvals.mode",
                verifier="core.command_registry.groups.convergence:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.convergence:_handle_mode_set"),
            availability=Availability("core.command_registry.groups.approvals:_probe_approval_store"),
            exit_codes=(0, 2, 21, 42),
        )
    )


def _probe_prefs_store(context: dict) -> tuple[bool, str]:
    try:
        from core.user_preferences import load_preferences

        load_preferences()
        return True, ""
    except Exception as exc:
        return False, f"preferences store unreachable: {exc}"


def _gate_operator_approval(inp, ctx) -> ApprovalDecision:
    if str(ctx.principal or "") != "operator":
        return ApprovalDecision(required=True, reason="operator-only command")
    return ApprovalDecision(required=False)


def _gate_bypass_confirmation(inp, ctx) -> AuthorityDecision:
    if str(ctx.principal or "") != "operator":
        return AuthorityDecision(granted=False, reason="operator-only command")
    # A caller-asserted boolean is not user presence. Activation requires a
    # server-minted, single-use confirmation_id bound to this exact action; the
    # handler consumes it atomically (core.mode_permission_policy).
    if not str(getattr(inp, "confirmation_id", "") or "").strip():
        return AuthorityDecision(
            granted=False,
            reason="bypass activation requires a single-use confirmation_id minted by the local VOOL UI",
        )
    return AuthorityDecision(granted=True)


def _probe_cloud_policy(context: dict) -> tuple[bool, str]:
    try:
        from core import cloud_escalation_policy as _cep

        _cep.load_policy()
        return True, ""
    except Exception as exc:
        return False, f"cloud policy store unreachable: {exc}"


def _probe_search_state(context: dict) -> tuple[bool, str]:
    try:
        from core.search_connection_state import connection_rows

        connection_rows()
        return True, ""
    except Exception as exc:
        return False, f"search connection state unavailable: {exc}"
