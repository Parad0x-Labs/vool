"""First-run pact + provider-onboarding HTTP surface — a thin adapter, never an authority.

Every GET here projects authority truth (``core.first_run_pact`` / ``core.first_run``);
every POST translates JSON → one registered Command Registry command and translates the
typed envelope back. No handler in this module mutates state directly (S-P6 pins this).
Guards mirror the operator-profile write surface: JSON content-type, same-origin,
loopback-owner only.
"""
from __future__ import annotations

from typing import Any

from core.request_trust import is_loopback_host


def _json_response(status: int, payload: Any):
    # Imported lazily: service.py is this module's caller, so module-level import cycles.
    from core.web.api.service import json_response

    return json_response(status, payload)


def _with_runtime_headers(response, runtime):
    from core.web.api.service import apply_runtime_headers

    return apply_runtime_headers(response, runtime)

_PACT_GET_PATHS = {"/api/onboarding/pact"}
_QUARANTINE_GET_PATHS = {"/api/intake/quarantine/list"}
_PROVIDER_GET_PATHS = {"/api/onboarding/state"}

_PACT_POST_PATHS = {
    "/api/onboarding/pact/advance": "first_run.pact.advance",
    "/api/onboarding/pact/begin": "first_run.pact.begin",
    "/api/onboarding/pact/hide": "first_run.pact.hide",
    "/api/onboarding/pact/skip": "first_run.pact.skip",
    "/api/onboarding/pact/reset": "first_run.pact.reset",
    "/api/onboarding/pact/name": "first_run.pact.name",
    "/api/onboarding/pact/facts": "first_run.pact.facts.set",
    "/api/onboarding/pact/facts/forget": "first_run.pact.facts.forget",
    "/api/onboarding/pact/boundary": "first_run.pact.boundary.set",
    "/api/onboarding/pact/task/claim": "first_run.pact.task.claim",
    "/api/onboarding/pact/denial/claim": "first_run.pact.denial.claim",
    "/api/onboarding/pact/choice/local-only": "first_run.choice.local_only",
    "/api/onboarding/choice": "onboarding.choice",
    "/api/onboarding/reset": "onboarding.reset",
    "/api/intake/begin": "intake.begin",
    "/api/intake/classify": "intake.classify",
    "/api/intake/preview": "intake.preview",
    "/api/intake/verify": "intake.verify",
    "/api/intake/complete": "intake.complete",
    "/api/intake/quarantine/list": "intake.quarantine.list",
    "/api/intake/quarantine/retry": "intake.quarantine.retry",
    "/api/intake/quarantine/delete": "intake.quarantine.delete",
}


def _header(headers: dict | None, name: str) -> str:
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return str(value or "")
    return ""


def _guards(runtime, headers: dict | None, client_host: str, body: Any):
    """The standard operator-write guard suite; returns an error response or None."""
    from core.web.api.runtime import host_header_allowed

    content_type = _header(headers, "content-type").lower()
    if content_type and "json" not in content_type:
        return _with_runtime_headers(_json_response(415, {"error": "content-type must be application/json"}), runtime)
    origin = _header(headers, "origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return _with_runtime_headers(_json_response(403, {"error": "cross-origin request not allowed"}), runtime)
    if not is_loopback_host(client_host):
        return _with_runtime_headers(_json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
    if not isinstance(body, dict):
        return _with_runtime_headers(_json_response(400, {"error": "body must be a JSON object"}), runtime)
    return None


def _opt_int(body: dict, key: str):
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return int(value)


def _opt_str(body: dict, key: str) -> str:
    value = body.get(key)
    return str(value if value is not None else "").strip()


def handle_pact_get(path: str, query: dict, runtime, client_host: str = "127.0.0.1"):
    """GET /api/onboarding/pact — the aggregated §6.1 projection (seed races resolve as 404)."""
    if path not in _PACT_GET_PATHS:
        if path in _QUARANTINE_GET_PATHS and is_loopback_host(client_host):
            from core.command_registry.legacy import forward as _cr_forward

            _q_status, _q_payload = _cr_forward("intake.quarantine.list", {})
            from core.web.api.service import json_response as _jr

            return _with_runtime_headers(_jr(_q_status, _q_payload), runtime)
        return None
    if not is_loopback_host(client_host):
        return _with_runtime_headers(_json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
    from core import first_run_pact

    model_pull = None
    try:
        model_pull = dict((runtime.runtime_version_stamp or {}).get("model_pull") or {}) or None
    except Exception:
        model_pull = None
    try:
        payload = first_run_pact.seed_and_snapshot(model_pull=model_pull)
    except first_run_pact.PactFault as exc:
        if exc.code == "pact_absent":
            return _with_runtime_headers(_json_response(404, {"error": "pact_absent"}), runtime)
        return _with_runtime_headers(_json_response(exc.http_status or 409, {"error": exc.code, "detail": exc.detail}), runtime)
    return _with_runtime_headers(_json_response(200, payload), runtime)


def handle_provider_get(path: str, query: dict, runtime):
    """GET /api/onboarding/state — the provider-choice machine (provider spec §4.3)."""
    if path not in _PROVIDER_GET_PATHS:
        return None
    from core.command_registry.legacy import forward as _cr_forward

    status, payload = _cr_forward("onboarding.state", {})
    return _with_runtime_headers(_json_response(status if status != 200 else 200, payload), runtime)


def handle_pact_post(path: str, body: Any, headers: dict | None, client_host: str, runtime):
    """POST /api/onboarding/* and /api/intake/* — one POST = one registered command."""
    command_id = _PACT_POST_PATHS.get(path)
    if command_id is None:
        return None
    guarded = _guards(runtime, headers, client_host, body)
    if guarded is not None:
        return guarded
    if path.startswith("/api/intake/"):
        refused = _school_credential_refusal(headers, runtime)
        if refused is not None:
            return refused

    try:
        input_data = _command_input(command_id, body)
    except ValueError as exc:
        return _with_runtime_headers(_json_response(400, {"error": str(exc)}), runtime)

    from core.command_registry.legacy import forward as _cr_forward

    status, payload = _cr_forward(command_id, input_data)
    if status == 200:
        payload = {"ok": True, **(payload or {})}
    else:
        payload = {"ok": False, **(payload or {})}
    return _with_runtime_headers(_json_response(status, payload), runtime)


def _command_input(command_id: str, body: dict) -> dict:
    expect_revision = _opt_int(body, "expect_revision")
    if command_id == "first_run.pact.advance":
        return {
            "to": _opt_str(body, "to"),
            "expect_revision": expect_revision,
            "evidence": dict(body.get("evidence") or {}),
        }
    if command_id == "first_run.pact.hide":
        hidden = body.get("hidden")
        return {"hidden": bool(hidden) if hidden is not None else True, "expect_revision": expect_revision}
    if command_id == "first_run.pact.name":
        return {
            "agent_name": _opt_str(body, "agent_name"),
            "keep_default": bool(body.get("keep_default", False)),
            "preferred_address": str(body.get("preferred_address") or "").strip(),
            "expect_revision": expect_revision,
        }
    if command_id == "first_run.pact.facts.set":
        items = body.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be a list of {category, value}")
        return {"items": items[:16], "expect_revision": expect_revision}
    if command_id == "first_run.pact.facts.forget":
        return {"category": _opt_str(body, "category"), "expect_revision": expect_revision}
    if command_id == "first_run.pact.boundary.set":
        value = body.get("value")
        if not isinstance(value, bool):
            raise ValueError("value must be a boolean")
        return {"key": _opt_str(body, "key"), "value": value, "expect_revision": expect_revision}
    if command_id in {"first_run.pact.task.claim", "first_run.pact.denial.claim"}:
        return {
            "session_id": _opt_str(body, "session_id"),
            "request_id": _opt_str(body, "request_id"),
            "receipt_id": _opt_str(body, "receipt_id"),
            "expect_revision": expect_revision,
        }
    if command_id in {"onboarding.choice", "intake.classify", "intake.preview", "intake.verify", "intake.complete",
                      "intake.quarantine.retry", "intake.quarantine.delete"}:
        data: dict[str, Any] = {}
        if command_id == "onboarding.choice":
            data["choice"] = _opt_str(body, "choice")
            data["expect_revision"] = expect_revision
        elif command_id == "intake.classify":
            data["session_id"] = _opt_str(body, "session_id")
            data["value"] = str(body.get("value") or "")
        elif command_id in {"intake.preview", "intake.verify"}:
            data["session_id"] = _opt_str(body, "session_id")
            data["provider_id"] = _opt_str(body, "provider_id")
            data["base_url"] = _opt_str(body, "base_url")
        elif command_id == "intake.quarantine.retry" or command_id == "intake.quarantine.delete":
            data = {"provider_id": _opt_str(body, "provider_id")}
            return data
        else:
            data["session_id"] = _opt_str(body, "session_id")
            data["persist"] = _opt_str(body, "persist") or "store"
        return data
    if command_id == "first_run.choice.local_only":
        return {"expect_revision": expect_revision}
    if command_id == "intake.begin":
        return {}
    # first_run.pact.{begin,skip,reset} / onboarding.reset
    return {"expect_revision": expect_revision}


def _school_credential_refusal(headers: dict | None, runtime):
    """VOOL School: provider credentials are a SCHOOL_ADMIN action on every door that can verify
    or store one. The intake flow now carries the Settings key form, so it answers exactly as
    /api/settings/credentials does (core/web/api/registry_authorities.py) -- same rule, same
    typed 403 -- instead of becoming a way around it."""
    from core.product_edition import is_school

    if not is_school():
        return None
    from core.school.session import session_from_headers

    session = session_from_headers(headers)
    if session is not None and session.role == "SCHOOL_ADMIN":
        return None
    return _with_runtime_headers(
        _json_response(
            403,
            {"ok": False, "error": "school_admin_only", "detail": "Provider credentials are managed by the school administrator."},
        ),
        runtime,
    )
