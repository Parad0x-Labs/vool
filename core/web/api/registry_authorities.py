"""Registry authorities: legacy route bodies lifted VERBATIM so both the legacy
transport and the command registry call one implementation. Cut-paste only —
validation, gates and response bytes are unchanged; the signature carries the
closures the inline block used.
"""
from __future__ import annotations

import contextlib
from typing import Any

from core.web.api.service import (
    ApiResponse,
    _cloud_model_write,
    apply_runtime_headers,
    emit_runtime_event,
    is_loopback_host,
    json_response,
)


def set_prefs_authority(body: dict, headers: dict, runtime, client_host: str = '127.0.0.1') -> ApiResponse:
    """Lifted verbatim from the legacy /api/settings/prefs route; the registry command and the route both call it."""
    import json as _json

    from core.user_preferences import load_preferences, save_preferences
    from core.web.api.runtime import host_header_allowed

    def _ph(name: str) -> str:
        for key, value in (headers or {}).items():
            if str(key).lower() == name:
                return str(value or "")
        return ""

    ct = _ph("content-type").lower()
    if ct and "json" not in ct:
        return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
    origin = _ph("origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
    try:
        if len(_json.dumps(body)) > 65536:
            return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
    except (TypeError, ValueError):
        return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
    # Preferences that are STORED, READ and injected into every turn's context
    # (core/bootstrap_context.py:113-134) but that no surface could reach: the allowlist below is
    # the reason a user had to type a sentence at VOOL to change its roleplay character, its
    # profanity ceiling, or whether it takes hive tasks. Widened here, in the ONE authority both
    # the HTTP route and the registry command call, so the two can never diverge.
    #
    # Deliberately NOT widened:
    #   tone_hint  -- stored, but read by nothing in the product (0 non-test readers).
    #   timezone   -- UserPreferences.timezone is written and read only by load_user_timezone /
    #                 save_user_timezone, which have no production caller; the live timezone is an
    #                 Operator Profile item. Exposing this field would be a control over a value
    #                 nothing consults.
    _BOOL_PREFS = {
        "deep_reasoning", "show_workflow", "hive_followups",
        "idle_research_assist", "accept_hive_tasks", "social_commons",
        "wallet_enabled", "setup_dismissed", "speech_notice_dismissed",
    }
    _INT_PREFS = {"humor_percent", "ram_reserve_pct", "daily_token_budget", "profanity_level"}
    # Normalized by save_preferences (_normalize_* / _clamp_pct), so an out-of-vocabulary value
    # lands on the default rather than being refused -- the page reads the stored value back and
    # reports the difference instead of claiming the requested value was kept.
    _ENUM_PREFS = {"communication_style", "autonomy_mode", "boundaries_mode"}
    #   email_signature -- migrated OUT of user_preferences.json into the Operator Profile by
    #                 migrate_legacy_profile_fields(), which empties the JSON field at every daemon
    #                 boot. email_signature() reads the profile. A control writing the JSON field
    #                 would show empty after a restart while the real value stayed in effect.
    #   user_address -- the same migration; it stays in the allowlist below only because older
    #                 clients still post it, and the handler routes it to the profile authority.
    #   setup_skipped_steps -- the first-run flow's own record of which steps the user skipped
    #                 (comma-separated ids; core/setup_progress.py drops unknown ones on read).
    _TEXT_PREFS = {"character_mode", "style_notes", "setup_skipped_steps"}
    _TEXT_LIMITS = {"character_mode": 120, "style_notes": 600, "setup_skipped_steps": 200}

    allowed = {"user_address"} | _BOOL_PREFS | _INT_PREFS | _ENUM_PREFS | _TEXT_PREFS
    unknown = set(body.keys()) - allowed
    if unknown:
        return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)

    def _is_int(v) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)

    prefs = load_preferences()
    if "user_address" in body:
        # Older clients may still post the name here. It is routed to the ONE authority
        # (Operator Profile, origin settings) -- the JSON field is no longer written.
        v = body["user_address"]
        if not isinstance(v, str) or any(ord(ch) < 32 for ch in v):
            return apply_runtime_headers(json_response(400, {"error": "user_address must be a plain string"}), runtime)
        from core import operator_profile

        _principal = operator_profile.principal_for_request({"_owner_local": True, "surface": "web"})
        _name = v.strip()[:60]
        _current = operator_profile.resolve(_principal, "preferred_name")
        if _name:
            _change = operator_profile.remember(_principal, "preferred_name", _name, origin="settings", actor="settings", replace=True)
            if not _change.persisted and _change.kind != "unchanged":
                return apply_runtime_headers(json_response(400, {"error": _change.report}), runtime)
        elif _current is not None:
            operator_profile.forget_item(_current.item_id, actor="settings")
    for key in sorted(_ENUM_PREFS):
        if key in body and isinstance(body[key], str):
            setattr(prefs, key, body[key])                        # normalized on save
    if "autonomy_mode" in body and isinstance(body["autonomy_mode"], str):
        # Provenance: autonomy always has a value, so the field alone cannot show that the user
        # chose it. The stamp is what separates "the default" from "a decision" downstream
        # (core/setup_progress.py). Choosing the default explicitly is still a decision.
        from core.user_preferences import mark_autonomy_chosen

        mark_autonomy_chosen(prefs)
    _wallet_was_enabled = bool(prefs.wallet_enabled)
    for key in sorted(_BOOL_PREFS):
        if key in body:
            if not isinstance(body[key], bool):
                return apply_runtime_headers(json_response(400, {"error": f"{key} must be a boolean"}), runtime)
            setattr(prefs, key, body[key])
    _wallet_switched = bool(prefs.wallet_enabled) != _wallet_was_enabled
    if _wallet_switched:
        prefs.wallet_enabled_generation = int(prefs.wallet_enabled_generation or 0) + 1
    for key in sorted(_INT_PREFS):
        if key in body:
            if not _is_int(body[key]):
                return apply_runtime_headers(json_response(400, {"error": f"{key} must be an integer"}), runtime)
            setattr(prefs, key, body[key])                        # clamped on save
    for key in sorted(_TEXT_PREFS):
        if key in body:
            value = body[key]
            # The same shape check user_address already applies: a plain string, no control
            # characters, bounded -- these are concatenated into the model's context.
            if not isinstance(value, str) or any(ord(ch) < 32 for ch in value):
                return apply_runtime_headers(json_response(400, {"error": f"{key} must be a plain string"}), runtime)
            setattr(prefs, key, value.strip()[: _TEXT_LIMITS[key]])
    save_preferences(prefs)
    if _wallet_switched:
        from core.wallet import config as wallet_config

        # after the save: a signed wallet transfer that has not been sent sees Crypto turned off and back on
        wallet_config.record_enabled_change()
    return apply_runtime_headers(json_response(200, {"ok": True}), runtime)


def operator_json_guard(body: dict, headers: dict, runtime, client_host: str = '127.0.0.1') -> ApiResponse | None:
    """The guard suite every owner-only JSON door shares (core.web.api.contacts_api and its peers):
    owner-local (the TCP peer), JSON, same origin, bounded body. It proves a local caller, not a
    person — the owning authority (core.contacts.authority, the operator credential) still decides."""
    import json as _json

    from core.request_trust import is_loopback_host
    from core.web.api.runtime import host_header_allowed

    def _header(name: str) -> str:
        for key, value in (headers or {}).items():
            if str(key).lower() == name:
                return str(value or "")
        return ""

    if not is_loopback_host(client_host):
        return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
    content_type = _header("content-type").lower()
    if content_type and "json" not in content_type:
        return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
    origin = _header("origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
    try:
        if len(_json.dumps(body)) > 65536:
            return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
    except (TypeError, ValueError):
        return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
    return None


def _email_operator_guard(body: dict, headers: dict, runtime, client_host: str) -> ApiResponse | None:
    """The guard suite of the state-changing email operator routes: owner-local (the TCP peer), JSON,
    same origin, bounded body. The model has no route here: these are not tool contracts, and the
    registry executes them only for the operator principal."""
    import json as _json

    from core.request_trust import is_loopback_host
    from core.web.api.runtime import host_header_allowed

    def _header(name: str) -> str:
        for key, value in (headers or {}).items():
            if str(key).lower() == name:
                return str(value or "")
        return ""

    if not is_loopback_host(client_host):
        return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
    content_type = _header("content-type").lower()
    if content_type and "json" not in content_type:
        return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
    origin = _header("origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
    try:
        if len(_json.dumps(body)) > 65536:
            return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
    except (TypeError, ValueError):
        return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
    return None


_RECOVERY_STATUS_CODES = {
    "acknowledged": 200, "no_hold": 200, "found": 200, "absent": 200, "unverified": 200,
    "confirmation_required": 409, "generation_mismatch": 409, "quarantine_mismatch": 409,
    "unknown_message_id": 409, "no_recovery": 409,
    "acknowledgement_not_persisted": 500, "store_recovery_required": 500, "store_busy": 503,
}

def _recovery_payload(result, extra: dict | None = None) -> tuple[int, dict]:
    """The route's answer is the store's CURRENT state re-read after the action, never the action's
    intention: a write that failed reads back as still held."""
    from core.email_drafts import recovery_surface_view

    view = recovery_surface_view()
    payload = {"ok": bool(result.ok), "status": result.status, "message": result.message,
               "send_hold": bool(view.get("send_hold")), "recovery": view.get("recovery"),
               "generation": view.get("generation"), "summary": view.get("summary"),
               "acknowledge_effect": view.get("acknowledge_effect")}
    payload.update(extra or {})
    return _RECOVERY_STATUS_CODES.get(result.status, 400 if not result.ok else 200), payload


def email_recovery_acknowledge_authority(body: dict, headers: dict, runtime, client_host: str = '127.0.0.1') -> ApiResponse:
    """OPERATOR: acknowledge the exact current draft-store recovery (quarantine + generation + confirm)."""
    from core.email_drafts import acknowledge_store_recovery

    refused = _email_operator_guard(body, headers, runtime, client_host)
    if refused is not None:
        return refused
    result = acknowledge_store_recovery(
        str(body.get("quarantine") or ""), confirm=bool(body.get("confirm") is True),
        generation=str(body.get("generation") or ""), via="operator_surface",
    )
    status, payload = _recovery_payload(result)
    return apply_runtime_headers(json_response(status, payload), runtime)


def email_recovery_check_authority(body: dict, headers: dict, runtime, client_host: str = '127.0.0.1') -> ApiResponse:
    """OPERATOR: reconcile one Message-ID the hold names against its account's sent view; record it."""
    from core.email_drafts import check_recovery_message

    refused = _email_operator_guard(body, headers, runtime, client_host)
    if refused is not None:
        return refused
    result = check_recovery_message(str(body.get("message_id") or ""))
    status, payload = _recovery_payload(result, {"check": result.details.get("check"),
                                                 "check_persisted": result.details.get("check_persisted")})
    return apply_runtime_headers(json_response(status, payload), runtime)


def set_credentials_authority(body: dict, headers: dict, runtime, client_host: str = '127.0.0.1') -> ApiResponse:
    """Lifted verbatim from the legacy /api/settings/credentials route; the registry command and the route both call it."""
    import json as _json

    from core.cloud_providers import PROVIDERS, all_slots, provider_for_slot, slot_for
    from core.credential_store import delete_credential, has_credential, store_credential

    # VOOL School credential boundary (goal §12): the provider API key is a
    # SCHOOL_ADMIN action. Teachers and students — even on this loopback
    # machine — get a typed 403; the stored value is never shown to anyone
    # after save (the key form's own law), and students never see the form.
    from core.product_edition import is_school
    from core.web.api.runtime import host_header_allowed

    if is_school():
        from core.school.session import session_from_headers

        _school_session = session_from_headers(headers)
        if _school_session is None or _school_session.role != "SCHOOL_ADMIN":
            return apply_runtime_headers(
                json_response(403, {"error": "school_admin_only",
                                    "detail": "Provider credentials are managed by the school administrator."}),
                runtime,
            )

    _ALLOWED_CREDENTIAL_NAMES = all_slots()

    def _cred_header(name: str) -> str:
        for key, value in (headers or {}).items():
            if str(key).lower() == name:
                return str(value or "")
        return ""

    content_type = _cred_header("content-type").lower()
    if content_type and "json" not in content_type:
        return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
    origin = _cred_header("origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
    try:
        if len(_json.dumps(body)) > 65536:
            return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
    except (TypeError, ValueError):
        return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
    unknown = set(body.keys()) - {"name", "value", "label", "delete", "provider", "base_url"}
    if unknown:
        return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
    # "Auto-detect from the key" on the Settings form sent a value with no provider and no slot, and
    # this door -- a closed slot list that verifies nothing -- answered "unsupported credential name"
    # for every key, valid ones included (measured 2026-09-14). It still stores nothing on a guess:
    # the answer is the local recognition result from credential intelligence (which provider a
    # documented prefix names, if any, and why), so the client can select and verify through
    # /api/intake/*. Nothing is stored and nothing is sent anywhere.
    _auto_value = body.get("value")
    if (
        not str(body.get("provider") or "").strip()
        and not str(body.get("name") or "").strip()
        and body.get("delete") is not True
        and isinstance(_auto_value, str)
        and _auto_value.strip()
    ):
        from core.credential_intelligence.format_classifier import classify_format
        from core.credential_intelligence.provider_registry import default_registry
        from core.credential_intelligence.shortlist import build_shortlist

        _registry = default_registry()
        _shortlist = build_shortlist(
            classify_format(_auto_value, known_prefixes=_registry.known_key_prefixes()), _registry
        )
        _suggested = _registry.get(_shortlist.suggestion) if _shortlist.suggestion else None
        return apply_runtime_headers(
            json_response(
                400,
                {
                    "error": "Pick the service this key belongs to so VOOL can verify it before storing.",
                    "code": "provider_selection_required",
                    "suggestion": _suggested.provider_id.removeprefix("search.") if _suggested else "",
                    "suggestion_kind": _suggested.kind if _suggested else "",
                    "candidates": [entry.provider_id.removeprefix("search.") for entry in _shortlist.entries],
                    "reason": _shortlist.reason,
                },
            ),
            runtime,
        )
    # Web-search keys (Brave/Tavily/…) share this endpoint and its guard suite, but nothing
    # else: they must not touch the cloud escalation policy or activate an LLM lane, so they
    # are handled here and return before any of that. The cloud path below is left byte for
    # byte as it was. Routing them here also keeps ONE closed slot whitelist in force -- a
    # search slot is accepted because it is in the search table, never because the whitelist
    # was loosened to arbitrary names.
    from core.search_providers import SEARCH_PROVIDERS
    from core.search_providers import all_slots as _search_slots
    from core.search_providers import provider_for_slot as _search_provider_for_slot
    from core.search_providers import slot_for as _search_slot_for

    _raw_provider_field = str(body.get("provider") or "").strip().lower()
    _raw_name_field = str(body.get("name") or "").strip()
    if _raw_provider_field in SEARCH_PROVIDERS or _raw_name_field in set(_search_slots()):
        from core.credential_store import (
            delete_credential as _sdelete,
        )
        from core.credential_store import (
            has_credential as _shas,
        )
        from core.credential_store import (
            store_credential as _sstore,
        )
        from core.search_providers import normalize_key as _snormalize

        search_slot = _search_slot_for(_raw_provider_field) if _raw_provider_field else ""
        if _raw_name_field:
            if search_slot and _raw_name_field != search_slot:
                return apply_runtime_headers(json_response(400, {"error": "name and provider disagree"}), runtime)
            search_slot = _raw_name_field
        if search_slot not in set(_search_slots()):
            return apply_runtime_headers(json_response(400, {"error": "unsupported credential name"}), runtime)
        search_provider_id = _search_provider_for_slot(search_slot)
        if body.get("delete") is True:
            removed = _sdelete(search_slot)
            return apply_runtime_headers(
                json_response(
                    200,
                    {"name": search_slot, "provider": search_provider_id, "connected": False, "removed": removed},
                ),
                runtime,
            )
        raw_value = body.get("value")
        if not isinstance(raw_value, str) or not raw_value.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid value"}), runtime)
        if len(raw_value) > 8192:
            return apply_runtime_headers(json_response(413, {"error": "value too long"}), runtime)
        search_label = body.get("label", "")
        if not isinstance(search_label, str):
            return apply_runtime_headers(json_response(400, {"error": "label must be a string"}), runtime)
        # Normalize the paste (surrounding quotes, a leading "Bearer ") before storing, so the
        # key that gets probed is the key that gets sent, and a user who pasted a quoted value
        # is not told their good key is invalid.
        cleaned = _snormalize(raw_value)
        if not cleaned:
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid value"}), runtime)
        # C15: typed secure-storage failure — one bounded attempt, recovery shown, no retry.
        from core.unattended_preflight import SecureStorageError as _SSE  # noqa: N814 - short
        # local alias for a one-line except clause; not a constant.

        try:
            _sstore(search_slot, cleaned, label=search_label.strip()[:120])
        except _SSE as exc:
            return apply_runtime_headers(
                json_response(
                    503,
                    {"error": str(exc), "secure_storage_code": exc.code, "recovery": exc.recovery},
                ),
                runtime,
            )
        # No cloud-lane activation and no policy write: a stored key is itself what puts this
        # provider at the front of the search chain (tools/web/web_research._with_keyed_search_apis),
        # and that is read live per turn, so the change takes effect on the next search.
        return apply_runtime_headers(
            json_response(
                200,
                {"name": search_slot, "provider": search_provider_id, "connected": _shas(search_slot)},
            ),
            runtime,
        )
    # COUNCIL MODEL-PIN FENCE, second writer. Below this line the CLOUD path rewrites the
    # escalation policy's `model` field on a provider switch (`keep_model` is "" when the
    # provider changes) — the same composer pin a council holds, reached without ever
    # touching /api/cloud/model. Search-key saves returned above and are untouched: they
    # store a key and write no policy at all. A key save is trivially retried once the run
    # ends, so this refuses the whole cloud path rather than storing the key and silently
    # skipping the lane activation, which would leave the caller with a half-applied save
    # nothing reported.
    from core.council import pin_lock as _cred_pin_lock

    _cred_refusal = _cred_pin_lock.pin_write_refusal("", owner_local=True)
    if _cred_refusal is not None:
        return apply_runtime_headers(json_response(409, _cred_refusal), runtime)

    # The target slot: an explicit `name`, and/or a `provider` id that derives the slot. When
    # both are given they must agree. The slot must be one of the fixed provider slots — never
    # an arbitrary credential name (the whitelist stays closed).
    raw_provider = body.get("provider")
    name = ""
    if isinstance(raw_provider, str) and raw_provider.strip():
        prov = raw_provider.strip().lower()
        if prov not in PROVIDERS:
            return apply_runtime_headers(json_response(400, {"error": "unknown provider"}), runtime)
        name = slot_for(prov)
    raw_name = body.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        if name and raw_name.strip() != name:
            return apply_runtime_headers(json_response(400, {"error": "name and provider disagree"}), runtime)
        name = raw_name.strip()
    if name not in _ALLOWED_CREDENTIAL_NAMES:
        return apply_runtime_headers(json_response(400, {"error": "unsupported credential name"}), runtime)
    provider_id = provider_for_slot(name)

    if body.get("delete") is True:
        if provider_id == "usepod":
            # The token and its origin are ONE committed pair. The token and its binding row leave
            # through the store's journaled delete (removal proven by read-back), then the origin,
            # which was chosen together with the token and does not outlive it. A removal that
            # cannot be proven keeps the binding and says so.
            from core.cloud_providers import USEPOD_ORIGIN_SLOT
            from core.credential_intelligence.binding import MalformedBindingIndexError
            from core.credential_intelligence.provider_registry import default_registry
            from core.credential_intelligence.store import CredentialStore, StorageUnavailableError

            try:
                removed = bool(CredentialStore(default_registry()).delete(provider_id).removed)
            except (StorageUnavailableError, MalformedBindingIndexError) as exc:
                return apply_runtime_headers(
                    json_response(
                        503,
                        {"error": str(exc), "recovery": "the delete is journaled; reconcile (restart or on demand) decides it"},
                    ),
                    runtime,
                )
            delete_credential(USEPOD_ORIGIN_SLOT)
        else:
            removed = delete_credential(name)
        with contextlib.suppress(Exception):
            if removed:
                from core.cloud_runtime import bump_cloud_broker_epoch
                from core.runtime_provider_defaults import deactivate_provider_byok

                deactivate_provider_byok(provider_id)
                bump_cloud_broker_epoch()
        return apply_runtime_headers(
            json_response(200, {"name": name, "provider": provider_id, "connected": False, "removed": removed}), runtime
        )
    value = body.get("value")
    if not isinstance(value, str) or not value.strip():
        return apply_runtime_headers(json_response(400, {"error": "missing or invalid value"}), runtime)
    if len(value) > 8192:
        return apply_runtime_headers(json_response(413, {"error": "value too long"}), runtime)
    label = body.get("label", "")
    if not isinstance(label, str):
        return apply_runtime_headers(json_response(400, {"error": "label must be a string"}), runtime)
    # UsePod's credential is a token that lives in the URL path. A pasted proxy base URL is split
    # here -- token to the secret slot, origin validated and kept apart -- so the token is never
    # stored as a "base URL" and no API key UsePod ignores is demanded. Validated BEFORE anything is
    # stored: a refused paste writes nothing.
    usepod_binding = None
    if provider_id == "usepod":
        from core.usepod.descriptor import UsePodConfigError
        from core.usepod.discovery import plan_credential_binding

        try:
            usepod_binding = plan_credential_binding(value, explicit_origin=str(body.get("base_url") or ""))
        except UsePodConfigError as exc:
            return apply_runtime_headers(
                json_response(400, {"error": "not a usable UsePod credential", "code": exc.code}), runtime
            )
    # For the custom endpoint, a base_url must be provided and be HTTPS (or loopback) so a key
    # is never sent over plaintext; it is persisted so the lane survives a restart.
    # C15: the store is the explicit secure-storage door. One bounded attempt; a failure
    # comes back TYPED (code + recovery instruction) and is never retried here.
    from core.unattended_preflight import SecureStorageError

    try:
        if provider_id == "custom":
            base_url = str(body.get("base_url") or "").strip()
            from core.cloud_providers import is_safe_key_transport

            # Validate the PARSED host, not a URL-string prefix: a raw startswith("http://127.0.0.1")
            # would accept http://127.0.0.1.evil.com and leak the key in cleartext to that host.
            if not is_safe_key_transport(base_url):
                return apply_runtime_headers(json_response(400, {"error": "custom endpoint needs an https:// base url (or a loopback host)"}), runtime)
            # The endpoint and its key commit as ONE transaction (review F1): this door used to
            # write the base URL and the key as two unjournaled writes, so a failure between
            # them left a mixed pair with no recovery record. The store's operator-pair door
            # journals the operation, decides it by read-back and leaves a coherent binding row
            # (honestly `unverified` — this door proves nothing about the key itself).
            refused = _commit_operator_pair("custom", value.strip(), base_url, runtime)
            if refused is not None:
                return refused
        elif usepod_binding is not None:
            # The UsePod token and the origin it was split from commit as ONE pair through the same
            # operator-pair transaction: two separate writes could leave a new token beside an old
            # origin, and the token would travel to a destination nobody chose with it.
            refused = _commit_operator_pair(provider_id, usepod_binding.token, usepod_binding.origin, runtime)
            if refused is not None:
                return refused
        else:
            store_credential(name, value.strip(), label=label.strip()[:120])
    except SecureStorageError as exc:
        return apply_runtime_headers(
            json_response(
                503,
                {
                    "error": str(exc),
                    "secure_storage_code": exc.code,
                    "recovery": exc.recovery,
                },
            ),
            runtime,
        )
    # Make this provider the active cloud lane and register it now, so a Settings save goes live
    # immediately (resetting the model to the provider's default only when switching providers).
    bind_saved_cloud_key(provider_id)
    saved = {"name": name, "provider": provider_id, "connected": has_credential(name)}
    if usepod_binding is not None:
        # Non-secret facts about what was bound; the fingerprint identifies the binding, not the token.
        saved.update(
            {
                "origin": usepod_binding.origin,
                "origin_is_default": usepod_binding.origin_is_default,
                "credential_fingerprint": usepod_binding.fingerprint,
                "token_shape": usepod_binding.token_shape,
            }
        )
    return apply_runtime_headers(json_response(200, saved), runtime)


def cloud_key_save_refusal() -> dict | None:
    """The council model-pin fence for ANY door that stores a cloud key and then rebinds the cloud
    lane (the reasoning is at its first use in set_credentials_authority): the refusal payload while
    a council run holds the pin, else None."""
    from core.council import pin_lock as _cred_pin_lock

    return _cred_pin_lock.pin_write_refusal("", owner_local=True)


def bind_saved_cloud_key(provider_id: str) -> None:
    """Make ``provider_id`` the active cloud lane and register its BYOK lane now, so a stored key goes
    live immediately (resetting the model to the provider's default only when switching providers).

    Moved out of set_credentials_authority unchanged, so the Settings credentials door and the
    credential intake flow bind a saved key identically. Best-effort by contract, as it was inline."""
    with contextlib.suppress(Exception):
        from dataclasses import replace

        from core import cloud_escalation_policy as cep
        from core.cloud_providers import active_provider
        from core.cloud_runtime import bump_cloud_broker_epoch
        from core.runtime_provider_defaults import activate_provider_byok, retire_nonactive_provider_lanes

        prev = active_provider(str(cep.load_policy().provider or "")) or "openrouter"
        keep_model = cep.load_policy().model if provider_id == prev else ""
        current_policy = cep.load_policy()
        cep.save_policy(
            replace(
                current_policy,
                provider=provider_id,
                model=keep_model,
                free_cloud_enabled=(
                    True if provider_id == "openrouter" else current_policy.free_cloud_enabled
                ),
            )
        )
        activate_provider_byok(provider_id)
        # One active provider (v1): retire any other provider's still-enabled BYOK lane so a
        # burst can never route to the provider the user just switched away from.
        retire_nonactive_provider_lanes(provider_id)
        bump_cloud_broker_epoch()
    with contextlib.suppress(Exception):
        from core.cloud_providers import uses_url_path_token

        if uses_url_path_token(provider_id):
            # Lanes already registered carry the origin they were registered for; a re-pointed origin
            # must reach them. Their approved routes stay bound to the previous origin and are refused
            # until the owner approves again.
            from core.runtime_provider_defaults import refresh_path_token_lanes

            refresh_path_token_lanes(provider_id)


def _commit_operator_pair(provider_id: str, secret: str, endpoint: str, runtime) -> ApiResponse | None:
    """Commit a key and its endpoint as ONE operator-asserted pair through the store's transaction
    (journaled, decided by read-back, a coherent ``unverified`` binding row), or return the typed
    refusal response. ``None`` means the pair committed."""
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import (
        CredentialStore,
        IntakeRefusedError,
        StorageConflictError,
        StorageUnavailableError,
        StoreWriteTimeoutError,
    )

    registry = default_registry()
    try:
        CredentialStore(registry).save_operator_pair(registry.get(provider_id), secret, endpoint=endpoint)
    except IntakeRefusedError as exc:
        return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)
    except StorageConflictError as exc:
        return apply_runtime_headers(json_response(
            409, {"error": str(exc), "recovery": "run reconcile (restart the app or re-save the pair)"}),
            runtime)
    except (StoreWriteTimeoutError, StorageUnavailableError) as exc:
        return apply_runtime_headers(json_response(
            503, {"error": str(exc), "recovery": "the pair is journaled as a pending intent; reconcile (restart or on demand) decides it"}),
            runtime)
    return None


def set_cloud_model_authority(body: dict, headers: dict, runtime, client_host: str = '127.0.0.1') -> ApiResponse:
    """Lifted verbatim from the legacy /api/cloud/model route; the registry command and the route both call it."""
    import json as _json

    from core.cloud_model_control import (
        CLOUD_MODEL_ID_RE,
        MODEL_COST_UNKNOWN,
        PAID_STATUS_UNKNOWN,
        classify_cloud_model_cost,
        split_provider_prefix,
    )
    from core.cloud_providers import PROVIDERS
    from core.web.api.runtime import host_header_allowed

    def _model_header(name: str) -> str:
        for key, value in (headers or {}).items():
            if str(key).lower() == name:
                return str(value or "")
        return ""

    content_type = _model_header("content-type").lower()
    if content_type and "json" not in content_type:
        return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
    origin = _model_header("origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
    try:
        if len(_json.dumps(body)) > 65536:
            return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
    except (TypeError, ValueError):
        return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
    unknown = set(body.keys()) - {"model", "provider", "confirm_paid", "session_id", "selection_revision"}
    if unknown:
        return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)

    # Owner decision FIRST — derived from the real TCP peer (never a body field) and placed
    # ahead of any classification work so a non-owner learns nothing about pricing state
    # from this endpoint before being told they are not the owner.
    if not is_loopback_host(client_host):
        return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)

    # C3c COUNCIL MODEL-PIN FENCE, WRITE SIDE. This endpoint sets the SAME global pin a
    # council holds, so during a run it is a competing writer to the state the fence
    # exists to protect. Refused here — after the owner decision, before any
    # classification or pricing work — because a refusal that first classifies has
    # already done the work it refused and told the caller the pricing state of an id
    # it will not let them pin. The council's own seat pin and final restoration carry
    # the run's capability and pass; nothing else does.
    from core.council import pin_lock as _pin_lock

    _council_capability = _model_header("x-vool-council-dispatch")
    _pin_refusal = _pin_lock.pin_write_refusal(_council_capability, owner_local=True)
    if _pin_refusal is not None:
        return apply_runtime_headers(json_response(409, _pin_refusal), runtime)

    session_id = body.get("session_id", "")
    selection_revision = body.get("selection_revision", 0)
    if type(selection_revision) is not int or not 0 <= selection_revision <= 9007199254740991:
        return apply_runtime_headers(json_response(400, {"error": "invalid selection_revision"}), runtime)
    if "session_id" in body:
        import re

        if not isinstance(session_id, str) or not re.fullmatch(r"openclaw:[0-9a-f]{20}", session_id):
            return apply_runtime_headers(json_response(400, {"error": "invalid session_id"}), runtime)

    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        return apply_runtime_headers(json_response(400, {"error": "missing or invalid model"}), runtime)
    model = model.strip()
    # Strip an optional "provider:" prefix before the regex check (one shared parser; the
    # switch re-parses it identically).
    _prefix_provider, _probe_model = split_provider_prefix(model)
    if model.lower() not in {"auto", "default", "reset"} and not CLOUD_MODEL_ID_RE.match(_probe_model):
        return apply_runtime_headers(json_response(400, {"error": "invalid model id"}), runtime)
    _mp = body.get("provider")
    model_provider = str(_mp).strip().lower() if isinstance(_mp, str) and _mp.strip() else None
    if model_provider is not None and model_provider not in PROVIDERS:
        return apply_runtime_headers(json_response(400, {"error": "unknown provider"}), runtime)

    # A11 SERVER-OWNED PAID AUTHORITY: classification runs through the ONE server-owned
    # classifier every surface converges on (this endpoint, the chat command, NL intents,
    # direct/internal set_cloud_model callers) — the same identity canonicalization, the
    # same cold-cache fail-closed rule, no endpoint-private second opinion that could drift.
    # A refusal is machine-readable (stable `code`) and the confirmation stays REQUEST-
    # SCOPED: `confirm_paid` rides THIS POST only, nothing sticky is stored, and the client
    # may express consent but never DECIDES free-vs-paid — the classifier does.
    confirm_paid = body.get("confirm_paid") is True
    # `auto`/`default`/`reset` name no concrete model, so nothing below classifies them
    # and neither name is ever bound in that branch. Inline, the acceptance-record guard
    # short-circuited on the same keyword test before it read `_cost`; lifting the write
    # into a helper made both arguments eager, so they are bound here for every path.
    # The values are inert for a keyword switch — no price is recorded for one.
    _cost: dict[str, Any] = {"cost_state": "free", "reason_code": ""}
    _resolved_provider = ""
    if model.lower() not in {"auto", "default", "reset"}:
        # Resolve the effective provider EXACTLY like the switch itself resolves it (explicit
        # body arg -> already-parsed "provider:" prefix -> active/default), so this
        # classification can never inspect a different lane than the one about to be persisted.
        from core import cloud_escalation_policy as _cep_probe
        from core.cloud_providers import active_provider as _active_provider

        try:
            _resolved_provider = str(model_provider or _prefix_provider or "").strip().lower() or (
                _active_provider(str(_cep_probe.load_policy().provider or "")) or "openrouter"
            )
        except Exception:
            _resolved_provider = str(model_provider or _prefix_provider or "").strip().lower()
        _cost = classify_cloud_model_cost(
            provider_id=_resolved_provider, model_id=_probe_model, allow_network=True
        )
        if _cost["cost_state"] != "free" and not confirm_paid:
            # ACCEPTED-CEILING LAW (design authority: catalog price != user-authorized
            # price). A model the operator already confirmed carries a recorded ceiling;
            # discovery moving ABOVE it re-gates with the exact before/after, so the
            # operator decides against numbers, not a generic paid prompt. Decreases and
            # unchanged prices fall through to the ordinary confirm gate. Fail-open here
            # is impossible: this branch only ever ADDS a more specific refusal.
            from core.model_price_acceptance import price_above_acceptance

            try:
                _rise = price_above_acceptance(_resolved_provider, _probe_model)
            except Exception:
                _rise = None
            if _rise is not None:
                return apply_runtime_headers(
                    json_response(
                        409,
                        {
                            "ok": False,
                            "error": (
                                "The catalog now lists this model ABOVE the price you "
                                "accepted earlier. Nothing was pinned; re-send with "
                                "confirm_paid=true only if you accept the new price — "
                                "that rewrites your ceiling."
                            ),
                            "code": "price_above_accepted",
                            "model": _probe_model,
                            "cost_state": _cost["cost_state"],
                            "accepted": _rise["accepted"],
                            "current": _rise["current"],
                        },
                    ),
                    runtime,
                )
            code = _cost["reason_code"] or "paid_model_confirm_required"
            if code == MODEL_COST_UNKNOWN:
                message = (
                    "The catalog could not classify what this model costs, so it was not "
                    "pinned. Re-send with confirm_paid=true only after deciding you accept "
                    "unclassified per-token cost — the confirmation covers this switch only."
                )
            elif code == PAID_STATUS_UNKNOWN:
                message = (
                    "This model's published pricing is indeterminate in the catalog, so it "
                    "was not pinned. Re-send with confirm_paid=true only after deciding you "
                    "accept unverified per-token cost — the confirmation covers this switch only."
                )
            else:
                code = "paid_model_confirm_required"
                message = (
                    "That model is PAID per the cached OpenRouter catalog. Confirm the pin "
                    "to spend provider credits."
                )
            return apply_runtime_headers(
                json_response(
                    409,
                    {
                        "ok": False,
                        "error": message,
                        "code": code,
                        "model": _probe_model,
                        "cost_state": _cost["cost_state"],
                    },
                ),
                runtime,
            )

    def _chat_selection_write():
        # Chat selection travels on the requested-model rail. Do not mutate or replace
        # the global fallback lane: other chats and in-flight requests still own their choices.
        from core import cloud_escalation_policy as cep

        chosen = "" if model.lower() in {"auto", "default", "reset"} else _probe_model
        provider = _resolved_provider if chosen else ""
        if chosen:
            from core.runtime_provider_defaults import register_chat_cloud_model

            try:
                if not register_chat_cloud_model(provider, chosen):
                    raise ValueError("Provider endpoint unavailable")
            except Exception:
                return apply_runtime_headers(json_response(503, {"ok": False, "error": "model registration unavailable"}), runtime)
        if confirm_paid and chosen and _cost["cost_state"] != "free":
            from core.model_price_acceptance import record_acceptance

            try:
                record_acceptance(provider, chosen, cost_state=_cost["cost_state"])
            except Exception:
                return apply_runtime_headers(json_response(503, {"ok": False, "error": "price acceptance unavailable"}), runtime)
        if not cep.save_chat_model_selection(session_id, model=chosen, provider=provider, revision=selection_revision):
            return apply_runtime_headers(json_response(503, {"ok": False, "error": "selection not persisted"}), runtime)
        return apply_runtime_headers(json_response(200, {
            "ok": True, "model": chosen, "provider": provider, "session_id": session_id,
            "cost_state": _cost["cost_state"], "selection_source": "server",
        }), runtime)

    # Held ACROSS the mutation, not checked before it: a council acquiring the pin now
    # waits for this write to land rather than pinning through it. Re-checked here too,
    # because classification above can take long enough for a council to arrive in the
    # meantime — that turn is refused before it mutates, never after.
    _pin_admission = _pin_lock.admit_model_write(
        capability=_council_capability, owner_local=True
    )
    if _pin_admission.refusal is not None:
        return apply_runtime_headers(json_response(409, _pin_admission.refusal), runtime)
    try:
        if session_id:
            return _chat_selection_write()
        return _cloud_model_write(
            model=model,
            model_provider=model_provider,
            confirm_paid=confirm_paid,
            cost=_cost,
            resolved_provider=_resolved_provider,
            probe_model=_probe_model,
            council_capability=_council_capability,
            runtime=runtime,
        )
    finally:
        _pin_admission.release()


def task_recovery_authority(body: dict, runtime, client_host: str = '127.0.0.1') -> ApiResponse:
    """Lifted verbatim from the legacy /api/task/recovery route; the registry command and the route both call it."""
    if not is_loopback_host(client_host):
        return apply_runtime_headers(json_response(403, {"error": "task recovery is local-session only"}), runtime)
    unknown = set(body.keys()) - {"session_id", "checkpoint_id", "action"}
    if unknown:
        return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
    recovery_session = str(body.get("session_id") or "").strip()
    checkpoint_id = str(body.get("checkpoint_id") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    if not recovery_session or not checkpoint_id or action != "cancel":
        return apply_runtime_headers(json_response(400, {"error": "session_id, checkpoint_id, and action=cancel are required"}), runtime)
    from core.runtime_continuity import (
        CheckpointTransitionRefused,
        get_runtime_checkpoint,
        update_runtime_checkpoint,
    )

    checkpoint = get_runtime_checkpoint(checkpoint_id)
    if not checkpoint or str(checkpoint.get("session_id") or "") != recovery_session:
        return apply_runtime_headers(json_response(404, {"error": "recoverable task not found in this session"}), runtime)
    if str(checkpoint.get("status") or "") not in {"running", "interrupted", "pending_approval"}:
        return apply_runtime_headers(json_response(409, {"error": "task is no longer recoverable"}), runtime)
    # Recovery acts on a task whose PROCESS is gone: it rewrites the durable row and stops
    # nothing. Applying it to a checkpoint whose worker is still executing would mark the row
    # cancelled while the work carried on, and the operator would be told it stopped when it had
    # not. Stopping a live turn is /api/chat/cancel, which sets the turn's actual cancel Event.
    from core.live_turns import is_checkpoint_live

    if is_checkpoint_live(checkpoint_id):
        return apply_runtime_headers(
            json_response(
                409,
                {
                    "error": "this task is still running; stop it from the chat instead of recovery",
                    "worker_live": True,
                },
            ),
            runtime,
        )
    try:
        updated = update_runtime_checkpoint(checkpoint_id, status="cancelled", pending_intent={})
    except CheckpointTransitionRefused as exc:
        # A7 W1: terminal checkpoints cannot silently reopen or be re-mutated.
        return apply_runtime_headers(
            json_response(409, {"error": str(exc), "refused": "terminal_transition"}),
            runtime,
        )
    # A8 availability gate: a checkpoint's final_response bytes are only
    # served when their governing payload is still AVAILABLE (the stored
    # final_response_hash binds the bytes to the payload identity; the
    # erasure digest tombstone makes post-erase verdicts deterministic).
    try:
        from core.finalization import (
            AVAILABILITY_ERASED,
            AVAILABILITY_WITHHELD,
            payload_availability_for_hash,
        )

        _final_hash = str((updated or {}).get("final_response_hash") or "")
        _verdict = (
            payload_availability_for_hash(_final_hash) if _final_hash else None
        )
        if _verdict in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):
            updated = dict(updated or {})
            updated["final_response"] = ""
            updated["final_response_hash"] = ""
    except Exception:
        # A8 PASS003 fail-closed law (pass-002 latent-fail-open caveat):
        # an unreadable privacy verdict means governed bytes must NOT be
        # served — the payload is suppressed, never disclosed.
        updated = dict(updated or {})
        updated["final_response"] = ""
        updated["final_response_hash"] = ""
    emit_runtime_event(
        {"runtime_session_id": recovery_session, "session_id": recovery_session},
        event_type="task_cancelled",
        message="Interrupted task was cancelled from recovery controls.",
        details={"checkpoint_id": checkpoint_id, "task_id": str(checkpoint.get("task_id") or "")},
    )
    return apply_runtime_headers(json_response(200, {"ok": True, "checkpoint": updated}), runtime)
