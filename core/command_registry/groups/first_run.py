"""First-Run Pact + provider-choice commands — the ONE mutation authority for first run.

Every state-changing pact/onboarding HTTP endpoint is a thin adapter over THESE
registered commands (provider-spec L11 / correction #1): the HTTP handler only
translates JSON ↔ typed command input and never mutates state itself. Pact tour
truth is owned by ``core.first_run_pact``, provider choice by ``core.first_run``;
facts, boundaries, names, receipts and proofs stay with their existing authorities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.command_registry.spec import (
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    OpenRead,
    OperatorAuthority,
)
from core.first_run import FirstRunError
from core.first_run_pact import PactFault

GROUP_ID = "first_run"

_FAULT_REMEDIATIONS = {
    "invalid_transition": ("check the tour step; every step can be skipped",),
    "stale_revision": ("the tour state changed elsewhere; it has been refreshed",),
    "evidence_missing": ("run the real action first; the pact never fakes a step",),
    "evidence_unverifiable": ("the evidence did not verify; retry the real action or skip",),
    "evidence_session_unknown": ("that chat has no receipt ledger here",),
    "not_applicable_readonly": ("replay the tour from Settings → Show me around",),
    "boundary_key_unknown": ("use local_only_composite, memory_paused or web_lookups",),
    "pact_absent": ("the tour seed has not run yet; re-poll in a moment",),
    "lock_unavailable": ("another window is publishing; retry in a moment",),
    "boundary_partial": ("part of the boundary applied; the outcome names what did and what failed — live truth is projected",),
    "authority_write_failed": ("the boundary write was refused by its authority; nothing was changed",),
}


def _err(exc: Exception) -> HandlerFault:
    """Map typed pact/provider faults onto the registry's typed conflict path."""
    code = getattr(exc, "code", "") or "fault_validation"
    status = int(getattr(exc, "http_status", 409) or 409)
    if status == 404:
        code = "pact_absent"
        status = 409
    return HandlerFault(
        fault_code="conflict" if status == 409 else "fault_validation",
        summary=str(exc),
        detail={"error": code, "detail": str(exc), "legacy_status": status},
    )


# --- typed inputs ---------------------------------------------------------------------------


@dataclass
class EmptyInput:
    pass


@dataclass
class AdvanceInput:
    to: str
    expect_revision: int | None = None
    evidence: dict = field(default_factory=dict)


@dataclass
class HideInput:
    hidden: bool = True
    expect_revision: int | None = None


@dataclass
class RevisionInput:
    expect_revision: int | None = None


@dataclass
class NameInput:
    agent_name: str = ""
    keep_default: bool = False
    preferred_address: str = ""
    expect_revision: int | None = None


@dataclass
class FactsSetInput:
    items: list = field(default_factory=list)
    expect_revision: int | None = None


@dataclass
class FactsForgetInput:
    category: str
    expect_revision: int | None = None


@dataclass
class BoundaryInput:
    key: str
    value: bool
    expect_revision: int | None = None


@dataclass
class ClaimInput:
    session_id: str
    request_id: str
    receipt_id: str = ""
    expect_revision: int | None = None


@dataclass
class ChoiceInput:
    choice: str
    expect_revision: int | None = None


@dataclass
class IntakeClassifyInput:
    session_id: str
    value: str


@dataclass
class IntakeProviderInput:
    session_id: str
    provider_id: str
    #: The operator-entered base URL of a user endpoint (the custom OpenAI-compatible provider).
    base_url: str = ""


@dataclass
class IntakeCompleteInput:
    session_id: str
    #: "store" (default: verified only) or "later" (explicit save-for-later: quarantined,
    #: encrypted, invisible to every execution consumer until a verification promotes it).
    persist: str = "store"


@dataclass
class IntakeQuarantineInput:
    provider_id: str = ""


@dataclass
class ProviderAdvanceInput:
    state: str
    provider_id: str = ""
    expect_revision: int | None = None


@dataclass
class ProviderPickInput:
    provider_id: str
    expect_revision: int | None = None


# --- pact handlers ---------------------------------------------------------------------------


def _pact_read(inp, ctx):
    from core import first_run_pact

    try:
        return HandlerOk(data=first_run_pact.snapshot(), summary="first-run pact tour state")
    except PactFault as exc:
        return _err(exc)


def _pact_seed(inp, ctx):
    from core import first_run_pact

    data = first_run_pact.seed()
    return HandlerOk(data={"state": data.get("state"), "revision": data.get("revision")}, summary="pact seed ensured")


def _pact_begin(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.begin(expect_revision=inp.expect_revision)
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data={"state": data["state"], "revision": data["revision"]}, summary="tour started")


def _pact_advance(inp, ctx):
    from core import first_run_pact

    try:
        evidence = dict(getattr(inp, "evidence", {}) or {})
        data = first_run_pact.advance(
            inp.to,
            evidence=evidence,
            expect_revision=inp.expect_revision,
        )
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data={"state": data["state"], "revision": data["revision"]}, summary=f"tour at {data['state']}")


def _pact_hide(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.hide(inp.hidden, expect_revision=inp.expect_revision)
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data={"state": data["state"], "revision": data["revision"], "welcome_hidden": data.get("welcome_hidden")}, summary="welcome card hidden")


def _pact_skip(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.skip(expect_revision=getattr(inp, "expect_revision", None))
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data={"state": data["state"], "revision": data["revision"]}, summary="tour skipped")


def _pact_reset(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.reset(expect_revision=getattr(inp, "expect_revision", None))
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data={"state": data["state"], "revision": data["revision"]}, summary="tour replaying; your choices are kept")


def _pact_name(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.set_name(
            agent_name=inp.agent_name,
            keep_default=inp.keep_default,
            preferred_address=inp.preferred_address,
            expect_revision=inp.expect_revision,
        )
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data=data, summary="name saved")


def _pact_facts_set(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.set_facts(list(inp.items or []), expect_revision=inp.expect_revision)
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data=data, summary="facts processed")


def _pact_facts_forget(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.forget_fact(inp.category, expect_revision=getattr(inp, "expect_revision", None))
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data=data, summary="fact forgotten")


def _pact_boundary_set(inp, ctx):
    from core import first_run_pact
    from core.cross_process_lock import LockUnavailable

    try:
        data = first_run_pact.set_boundary(inp.key, inp.value, expect_revision=inp.expect_revision)
    except PactFault as exc:
        return _err(exc)
    except LockUnavailable as exc:
        # honest mapping for the owning-policy exception: typed conflict, never a 500
        return _err(PactFault("lock_unavailable", str(exc), http_status=409))
    return HandlerOk(data=data, summary=f"boundary {inp.key} -> {inp.value}")


def _pact_task_claim(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.claim_task(
            session_id=inp.session_id,
            request_id=inp.request_id,
            expect_revision=inp.expect_revision,
            receipt_id=inp.receipt_id,
        )
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data=data, summary="real task verified from its signed receipt")


def _pact_denial_claim(inp, ctx):
    from core import first_run_pact

    try:
        data = first_run_pact.claim_denial(
            session_id=inp.session_id,
            request_id=inp.request_id,
            expect_revision=inp.expect_revision,
            receipt_id=inp.receipt_id,
        )
    except PactFault as exc:
        return _err(exc)
    return HandlerOk(data=data, summary="real denial verified from its refusal record")


def _choice_local_only(inp, ctx):
    """Thin alias: one card button → one command; the provider command stays the authority.

    The two machines keep SEPARATE revision counters (two narrow authorities, one
    delegation edge), so the pact revision is deliberately not forwarded as the
    provider's CAS expectation.
    """
    return _onboarding_choice(ChoiceInput(choice="local_only", expect_revision=None), ctx)


# --- provider-choice handlers (the delegation edge) -------------------------------------------


def _onboarding_state(inp, ctx):
    from core import first_run as provider

    return HandlerOk(data=provider.snapshot(), summary="provider first-run state")


def _onboarding_choice(inp, ctx):
    from core import first_run as provider

    try:
        data = provider.choose(inp.choice, expect_revision=getattr(inp, "expect_revision", None))
    except FirstRunError as exc:
        return _err(exc)
    return HandlerOk(data={"state": data["state"], "revision": data["revision"]}, summary=f"provider choice: {inp.choice}")


def _onboarding_reset(inp, ctx):
    from core import first_run as provider

    try:
        data = provider.reset(expect_revision=getattr(inp, "expect_revision", None))
    except FirstRunError as exc:
        return _err(exc)
    return HandlerOk(data={"state": data["state"], "revision": data["revision"]}, summary="provider setup review")


# --- intake handlers (zero network until the explicit verify) ----------------------------------


@dataclass
class _IntakeSession:
    """Process-memory intake session (provider-spec L7: the raw value lives nowhere else).

    ``outcome`` keeps the ONE verification's non-secret result and ``endpoint`` the exact URL it was
    obtained from, so completing a verified intake stores the key without sending it a second time,
    and a changed provider or base URL can never reuse an outcome that belongs to another endpoint."""

    value: str = ""
    provider_id: str = ""
    base_url: str = ""
    outcome: Any = None
    endpoint: str = ""


_INTAKE_SESSIONS: dict[str, _IntakeSession] = {}
_INTAKE_LOCK = None  # created lazily to avoid import-time locks in spawned processes


def _intake_lock():
    global _INTAKE_LOCK
    import threading

    if _INTAKE_LOCK is None:
        _INTAKE_LOCK = threading.Lock()
    return _INTAKE_LOCK


def _intake_begin(inp, ctx):
    import uuid

    with _intake_lock():
        active = [sid for sid, s in _INTAKE_SESSIONS.items() if s.value]
        session_id = f"intake-{uuid.uuid4().hex[:16]}"
        _INTAKE_SESSIONS[session_id] = _IntakeSession()
    return HandlerOk(data={"session_id": session_id}, summary="intake session opened")


def _intake_fault(error: str, detail: str, status: int) -> HandlerFault:
    return HandlerFault(
        fault_code="fault_validation",
        summary=detail,
        detail={"error": error, "detail": detail, "legacy_status": status},
    )


def _intake_descriptor(provider_id: str, base_url: str):
    """(descriptor, fault) for the ONE selected provider. A user endpoint (the custom
    OpenAI-compatible provider) is verified at the operator's base URL, which must be safe to carry a
    key -- https to any host, plain http only to this machine -- before anything is sent. A key that
    commits together with a documented endpoint (UsePod's origin) is verified at the origin the
    operator explicitly chose, or at that documented origin when none was named."""
    from core.credential_intelligence.provider_registry import default_registry

    descriptor = default_registry().get(str(provider_id or ""))
    if descriptor is None:
        return None, _intake_fault("unknown_provider", f"unknown provider {provider_id!r}", 400)
    if descriptor.user_endpoint:
        base = str(base_url or "").strip()
        if not base:
            return None, _intake_fault("base_url_required", "enter the base URL of the endpoint, for example https://host/v1", 400)
        from core.cloud_providers import is_safe_key_transport

        if not is_safe_key_transport(base):
            return None, _intake_fault(
                "insecure_base_url",
                "the base URL must start with https:// (plain http:// is allowed only to a server on this machine), so the key never travels in clear",
                400,
            )
        descriptor = descriptor.with_base_url(base)
    elif descriptor.endpoint_slot:
        chosen = str(base_url or "").strip()
        origin = _chosen_origin(chosen) if chosen else str(descriptor.default_endpoint or "")
        if not origin:
            return None, _intake_fault(
                "insecure_base_url",
                "the origin must be scheme://host[:port] with no path -- https:// to any host, plain http:// only to a server on this machine -- so the key never travels in clear or to a path you did not choose",
                400,
            )
        descriptor = descriptor.with_base_url(origin)
    return descriptor, None


def _chosen_origin(value: str) -> str:
    """``scheme://host[:port]`` for an origin the operator chose, or "": the canonical key-transport gate
    (https to any host, plain http only to loopback) with no userinfo, path, query or fragment."""
    from core.usepod.descriptor import UsePodConfigError, normalize_origin

    try:
        return normalize_origin(value)
    except UsePodConfigError:
        return ""


def _path_token_descriptor(session: _IntakeSession, provider_id: str, base_url: str):
    """(descriptor, fault) for the selected provider, splitting a URL-path credential first.

    A credential that rides the URL path (UsePod) may be pasted as the bare token or as the whole
    proxy URL containing it. It is split with the provider's own parser BEFORE anything is sent:
    the session keeps only the token (registered with the redactor) and the origin it binds to, and
    the pasted URL itself is dropped. A URL that names another origin than the one the operator
    chose, or a non-default origin nobody chose, is refused. Every other placement is resolved by
    ``_intake_descriptor`` unchanged."""
    descriptor, fault = _intake_descriptor(provider_id, base_url)
    if fault is not None or str(getattr(descriptor, "auth_style", "") or "") != "url_path_token":
        return descriptor, fault
    from core.usepod.descriptor import UsePodConfigError, remember_token_for_redaction
    from core.usepod.discovery import plan_credential_binding

    try:
        plan = plan_credential_binding(session.value, explicit_origin=str(base_url or ""))
    except UsePodConfigError as exc:
        return None, _intake_fault(
            "credential_not_usable",
            f"not a usable {descriptor.label} credential ({exc.code}); nothing was sent anywhere",
            400,
        )
    remember_token_for_redaction(plan.token)
    with _intake_lock():
        session.value = plan.token
        session.base_url = plan.origin
    return _intake_descriptor(provider_id, plan.origin)


def _intake_classify(inp, ctx):
    """Local-only, shape-only classification — ZERO network, ZERO storage (L2).

    Recognition is a hint, never a verdict. The owner's classifier and shortlist name the provider a
    documented prefix identifies (``suggestion``), every candidate an ambiguous format could belong
    to, and one sentence of ``reason``. An unfamiliar key is not refused: the operator picks the
    service (or a custom endpoint) and exactly that one is asked."""
    from core.credential_intelligence.format_classifier import classify_format
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.shortlist import build_shortlist
    from core.secret_redaction import register_exact_secret

    value = str(inp.value or "")
    registry = default_registry()
    key_format = classify_format(value, known_prefixes=registry.known_key_prefixes())
    shortlist = build_shortlist(key_format, registry)
    register_exact_secret(value)  # every downstream persistence surface scrubs this exact value
    with _intake_lock():
        session = _INTAKE_SESSIONS.get(str(inp.session_id or ""))
        if session is not None:
            session.value = value
            session.outcome = None  # a new paste invalidates any earlier verification
            session.endpoint = ""
    suggestion = shortlist.suggestion
    payload = {
        "family": key_format.family,
        "prefix": key_format.prefix or "",
        "length": key_format.length,
        "plausible": bool(key_format.plausible_key),
        "shortlist": [] if suggestion else sorted(entry.provider_id for entry in shortlist.entries),
        "suggestion": suggestion,
        "candidates": [
            {"provider_id": entry.provider_id, "label": entry.label, "kind": getattr(registry.get(entry.provider_id), "kind", "")}
            for entry in shortlist.entries
        ],
        "unrecognized": bool(shortlist.unrecognized),
        "recognized_family": shortlist.recognized_family,
        "reason": shortlist.reason,
    }
    return HandlerOk(data=payload, summary="shape classified locally; nothing was sent anywhere")


def _intake_preview(inp, ctx):
    """The origin preview: pure descriptor data. Makes ZERO network requests (S-P-PREV)."""
    from urllib.parse import urlparse

    base_url = str(getattr(inp, "base_url", "") or "").strip()
    with _intake_lock():
        session = _INTAKE_SESSIONS.get(str(inp.session_id or ""))
    if session is not None and session.value:
        # A URL-path credential is split here as well, so a key saved for later keeps only its token
        # and the origin it binds to -- never the pasted URL.
        descriptor, fault = _path_token_descriptor(session, str(inp.provider_id or ""), base_url)
    else:
        descriptor, fault = _intake_descriptor(str(inp.provider_id or ""), base_url)
    if fault is not None:
        return fault
    with _intake_lock():
        if session is not None:
            session.provider_id = descriptor.provider_id
            if str(descriptor.auth_style or "") != "url_path_token":
                session.base_url = base_url
    endpoint = descriptor.verify_endpoint
    origin = f"{urlparse(endpoint).scheme}://{urlparse(endpoint).netloc}" if endpoint else ""
    return HandlerOk(
        data={
            "origin": origin,
            "method": descriptor.verify_method,
            "endpoint": endpoint,
            "paid": bool(descriptor.paid),
            "label": descriptor.label,
            "kind": descriptor.kind,
        },
        summary="this is exactly where the key would be sent — only on your explicit verify",
    )


def _intake_verify(inp, ctx):
    """The ONE explicit, bounded request against the selected provider's endpoint (operator action).

    The non-secret outcome is kept in the session, so ``intake.complete`` stores exactly what was
    verified without asking the provider again."""
    from core.credential_intelligence.verification import verify_provider_credential

    with _intake_lock():
        session = _INTAKE_SESSIONS.get(str(inp.session_id or ""))
    if session is None or not session.value:
        return _intake_fault("intake_session_expired", "re-paste the key; sessions are memory-only", 410)
    base_url = str(getattr(inp, "base_url", "") or "").strip()
    descriptor, fault = _path_token_descriptor(session, str(inp.provider_id or ""), base_url)
    if fault is not None:
        return fault
    try:
        outcome = verify_provider_credential(session.value, descriptor)
    except Exception as exc:
        return HandlerOk(
            data={"outcome": "network_unavailable", "provider_id": descriptor.provider_id, "detail": f"verification could not run: {type(exc).__name__}"},
            summary="the key was NOT stored",
        )
    with _intake_lock():
        session.provider_id = descriptor.provider_id
        if str(descriptor.auth_style or "") != "url_path_token":
            session.base_url = base_url  # a path token's session already holds the origin it was split to
        session.outcome = outcome
        session.endpoint = descriptor.verify_endpoint
    data = {key: value for key, value in outcome.to_dict().items() if key != "status"}
    return HandlerOk(
        data={"outcome": str(outcome.status), **data, "label": descriptor.label, "kind": descriptor.kind},
        summary="one check with the selected provider ran; nothing stored until you complete",
    )


def _intake_complete(inp, ctx):
    """Store a VERIFIED key and bind it where the runtime reads it; refuse anything unverified.

    The key is stored on the ONE verification this session already ran against the same provider and
    endpoint -- no second request; an intake that never verified runs exactly one verification here.
    A model provider then becomes the active cloud lane through the same binding the Settings
    credentials door performs (cloud policy, BYOK lane, other lanes retired, broker epoch), with a
    custom endpoint's base URL stored beside its key; a web-search key lands in its search slot, which
    the search chain reads on its next lookup."""
    from core import credential_store
    from core.credential_intelligence.binding import MalformedBindingIndexError
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import (
        CredentialStore,
        StorageConflictError,
        StorageUnavailableError,
        StoreWriteTimeoutError,
    )
    from core.credential_intelligence.verification import verify_provider_credential
    from core.unattended_preflight import SecureStorageError

    with _intake_lock():
        session = _INTAKE_SESSIONS.get(str(inp.session_id or ""))
        if session is not None:
            _INTAKE_SESSIONS.pop(str(inp.session_id), None)
    if session is None or not session.value:
        return _intake_fault("intake_session_expired", "re-paste the key; sessions are memory-only", 410)
    if not session.provider_id:
        return _intake_fault("provider_mismatch", "no provider was selected and verified in this session — nothing saved", 409)
    descriptor, fault = _path_token_descriptor(session, session.provider_id, session.base_url)
    if fault is not None:
        return fault
    # EXPLICIT save-for-later: the operator chose to keep an unverified key. It is stored
    # encrypted in a QUARANTINE slot no execution consumer reads, with an honest row that says
    # unverified — never into the provider's real slot, never bound, never used. Promotion
    # happens only through a later verified outcome (intake.quarantine.retry).
    if str(getattr(inp, "persist", "store") or "store") == "later":
        from core.credential_intelligence.store import CredentialStore as _Store
        from core.credential_intelligence.store import IntakeRefusedError as _Refused
        from core.credential_intelligence.store import (
            StorageConflictError as _Conflict,
        )
        from core.credential_intelligence.store import (
            StorageUnavailableError as _Unavailable,
        )
        from core.credential_intelligence.store import (
            StoreWriteTimeoutError as _Timeout,
        )

        try:
            # The quarantined key keeps its OWN destination: the base URL is recorded in the
            # quarantine row and installed only at promotion. The LIVE custom endpoint
            # (CUSTOM_BASE_URL_SLOT) is deliberately NOT written here — a refused or
            # quarantined save must never repoint the endpoint the active key executes
            # against (review 2026-09-15: it did, before anything was refused).
            binding = _Store(default_registry()).save_quarantined(
                descriptor, session.value, session.outcome, endpoint=session.base_url)
        except (_Refused, _Timeout, _Unavailable, _Conflict, SecureStorageError, MalformedBindingIndexError) as exc:
            return _intake_fault("not_stored", f"{exc}", 409)
        finally:
            session.value = ""
        return HandlerOk(
            data={
                "slot": binding.slot if hasattr(binding, "slot") else "",
                "label": descriptor.label,
                "kind": descriptor.kind,
                "provider_id": descriptor.provider_id,
                "quarantined": True,
                "binding": binding.to_dict(),
            },
            summary="stored sealed and QUARANTINED — nothing uses this key until a verification promotes it",
        )
    outcome = session.outcome
    if outcome is None or outcome.provider_id != descriptor.provider_id or session.endpoint != descriptor.verify_endpoint:
        outcome = verify_provider_credential(session.value, descriptor)
    if str(outcome.status) != "verified":
        return HandlerFault(
            fault_code="fault_validation",
            summary="the key did not verify; nothing was stored",
            detail={"error": "not_verified", "detail": f"outcome {outcome.status}", "outcome": str(outcome.status), "legacy_status": 409},
        )
    if descriptor.kind == "llm_cloud":
        from core.web.api.registry_authorities import cloud_key_save_refusal

        refusal = cloud_key_save_refusal()
        if refusal is not None:
            return HandlerFault(
                fault_code="fault_validation",
                summary="a council run holds the cloud model pin; nothing was stored",
                detail={**refusal, "legacy_status": 409},
            )
    path_facts: dict[str, Any] = {}
    if str(descriptor.auth_style or "") == "url_path_token":
        # Non-secret facts about what is being bound: the origin the token travels to and the one-way
        # fingerprint its receipts carry. Never the token itself.
        from core.usepod.descriptor import credential_fingerprint, token_shape

        path_facts = {
            "origin": session.base_url,
            "credential_fingerprint": credential_fingerprint(session.base_url, session.value),
            "token_shape": token_shape(session.value),
        }
    try:
        # The custom endpoint (and a path token's origin) commits TOGETHER with its key inside
        # save_verified's one transaction -- never before it (review R3: a pre-lock write here let
        # two interleaved verified completions leave key A live against endpoint B).
        binding = CredentialStore(default_registry()).save_verified(
            descriptor, session.value, outcome, endpoint=session.base_url)
    except (StoreWriteTimeoutError, StorageUnavailableError, StorageConflictError, SecureStorageError, MalformedBindingIndexError) as exc:
        return _intake_fault("secure_storage_unavailable", f"secure storage did not complete ({type(exc).__name__}); the key was not bound", 503)
    finally:
        session.value = ""  # the raw value's last stop: drop it (provider intake law)
    if descriptor.kind == "llm_cloud":
        from core.web.api.registry_authorities import bind_saved_cloud_key

        bind_saved_cloud_key(descriptor.provider_id)
    return HandlerOk(
        data={
            "slot": descriptor.credential_slot,
            "label": descriptor.label,
            "kind": descriptor.kind,
            "provider_id": descriptor.provider_id,
            "storage": str(credential_store.active_backend()),
            "connected": bool(credential_store.has_credential(descriptor.credential_slot)),
            "binding": binding.to_dict(),
            "account_state": str(outcome.account_state),
            **path_facts,
        },
        summary="verified key stored in secure local storage and bound",
    )


# --- quarantined keys: list, retry (verify → promote), delete ----------------------------------


def _intake_quarantine_list(inp, ctx):
    """Every quarantined key's honest row — status, last outcome, timestamps. No secrets."""
    from core.credential_intelligence.binding import STATUS_QUARANTINED
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    rows = []
    for binding in CredentialStore(default_registry()).bindings():
        if binding.status != STATUS_QUARANTINED:
            continue
        rows.append(binding.to_dict())
    return HandlerOk(data={"quarantined": rows}, summary="unverified keys held in quarantine")


def _intake_quarantine_retry(inp, ctx):
    """Re-verify a quarantined key against its provider's own descriptor. A verified outcome
    PROMOTES it into the real slot (one live copy, bound like any verified save); any other
    outcome keeps it quarantined and reports the provider's own word for what happened."""
    from core.credential_intelligence.binding import MalformedBindingIndexError
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore
    from core.credential_intelligence.store import IntakeRefusedError as _QuarantineChanged
    from core.credential_intelligence.verification import verify_provider_credential
    from core.unattended_preflight import SecureStorageError

    provider_id = str(getattr(inp, "provider_id", "") or "").strip()
    store = CredentialStore(default_registry())
    # ONE atomic snapshot: secret + provider + saved endpoint + operation identity, read
    # together under the transaction. The verification below goes to EXACTLY this snapshot's
    # destination — a later paste's endpoint can never be combined with this secret (the
    # review's F1) — and promotion compares the whole snapshot at the commit boundary.
    from core.credential_intelligence.store import StorageConflictError as _Incoherent
    from core.credential_intelligence.store import StorageUnavailableError as _Unreadable

    try:
        snapshot = store.quarantine_snapshot(provider_id)
    except _Incoherent as exc:
        return _intake_fault(
            "quarantine_incoherent",
            f"{exc}. Retry after reconcile; nothing was sent anywhere.", 409)
    except (_Unreadable, MalformedBindingIndexError) as exc:
        # An unreadable quarantine slot is an unknown state, not "nothing quarantined" (R4).
        return _intake_fault(
            "secure_storage_unavailable",
            f"the quarantined key could not be read ({type(exc).__name__}); nothing was sent anywhere", 503)
    if snapshot is None or not snapshot.secret:
        return _intake_fault("not_quarantined", f"no quarantined key for {provider_id!r}", 404)
    descriptor = default_registry().get(provider_id)
    if descriptor is None:
        return _intake_fault("unknown_provider", f"unknown provider {provider_id!r}", 400)
    if descriptor.user_endpoint:
        if not snapshot.endpoint:
            return _intake_fault("base_url_required", "the quarantined key has no stored endpoint; delete it and re-save with the base URL", 400)
        descriptor = descriptor.with_base_url(snapshot.endpoint)
    elif descriptor.endpoint_slot:
        # A key saved for later keeps the origin it was split to; it is verified THERE, never at an
        # origin a later save chose.
        descriptor = descriptor.with_base_url(snapshot.endpoint or descriptor.default_endpoint)
    outcome = verify_provider_credential(snapshot.secret, descriptor)
    if outcome.status == "verified":
        from core.credential_intelligence.store import (
            StorageConflictError,
            StorageUnavailableError,
            StoreWriteTimeoutError,
        )

        try:
            binding = store.promote_quarantined(
                descriptor, snapshot.secret, outcome, snapshot=snapshot,
            )
        except _QuarantineChanged as exc:
            return HandlerOk(
                data={"outcome": "quarantine_changed", "promoted": False,
                      "detail": "the saved-for-later key changed while its retry ran; nothing was promoted"},
                summary=str(exc),
            )
        except (StoreWriteTimeoutError, StorageUnavailableError, StorageConflictError, SecureStorageError, MalformedBindingIndexError) as exc:
            return _intake_fault("secure_storage_unavailable", f"promotion did not complete ({type(exc).__name__}); the key stays quarantined", 503)
        if descriptor.kind == "llm_cloud":
            from core.web.api.registry_authorities import bind_saved_cloud_key

            bind_saved_cloud_key(descriptor.provider_id)
        return HandlerOk(
            data={"outcome": outcome.status, "promoted": True, "binding": binding.to_dict(),
                  "account_state": outcome.account_state},
            summary="the key verified and was promoted out of quarantine",
        )
    # keep the row honest about the newest attempt
    from core.credential_intelligence.binding import load_index, save_index

    rows = load_index()
    row = rows.get(descriptor.provider_id)
    if isinstance(row, dict):
        row["last_outcome"] = outcome.status
        save_index(rows)
    data = {k: v for k, v in outcome.to_dict().items() if k != "status"}
    return HandlerOk(
        data={"outcome": outcome.status, "promoted": False, **data},
        summary="still unverified — the key stays quarantined and unused",
    )


def _intake_quarantine_delete(inp, ctx):
    from core.credential_intelligence.binding import MalformedBindingIndexError
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore as _QStore
    from core.credential_intelligence.store import IntakeRefusedError as _QRefused
    from core.credential_intelligence.store import (
        StorageUnavailableError,
    )
    from core.unattended_preflight import SecureStorageError

    provider_id = str(getattr(inp, "provider_id", "") or "").strip()
    from core.credential_intelligence.store import StorageConflictError as _QIncoherent

    store = _QStore(default_registry())
    try:
        # Read inside the handled block: an unreadable or incoherent quarantine is a typed refusal,
        # never an unhandled error and never "nothing quarantined" (R4).
        snapshot = store.quarantine_snapshot(provider_id)
        result = store.delete_quarantined(
            provider_id, generation=snapshot.generation if snapshot else "", snapshot=snapshot)
    except _QRefused as exc:
        return HandlerOk(
            data={"provider_id": provider_id, "deleted": False, "outcome": "quarantine_changed",
                  "detail": "the saved-for-later key changed while its deletion ran; nothing was deleted"},
            summary=str(exc),
        )
    except _QIncoherent as exc:
        return _intake_fault("quarantine_incoherent", f"{exc}. Retry after reconcile; nothing was deleted.", 409)
    except (StorageUnavailableError, SecureStorageError, MalformedBindingIndexError) as exc:
        return _intake_fault("not_deleted", f"{exc}", 503)
    if not result.removed:
        return _intake_fault("not_quarantined", result.note, 404)
    return HandlerOk(data={"provider_id": provider_id, "deleted": True}, summary="quarantined key deleted")


# --- registration ------------------------------------------------------------------------------


def register(reg) -> None:
    reg.add_group(
        GroupSpec(
            group_id=GROUP_ID,
            description="First-run pact tour and provider choice — every mutation rides one authority",
            aliases=("first-run", "pact"),
        )
    )
    pact_perm = OperatorAuthority(kind="first_run.pact", verifier="core.command_registry.groups.first_run:_gate_operator")
    provider_perm = OperatorAuthority(kind="onboarding.choice", verifier="core.command_registry.groups.first_run:_gate_operator")
    read = OpenRead()

    specs = [
        CommandSpec(
            command_id="first_run.pact.read",
            group=GROUP_ID,
            description="Project the first-run pact tour state with live authority truth",
            input_schema=EmptyInput,
            effects="read_only",
            permission=read,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_read"),
            exit_codes=(0, 2, 42),
        ),
        CommandSpec(
            command_id="first_run.pact.seed",
            group=GROUP_ID,
            description="Idempotent boot seed of the pact tour state",
            input_schema=EmptyInput,
            effects="idempotent_write",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_seed"),
            fault_bindings=(FaultBinding(when="state_write_refused", fault_code="conflict", remediation=("check the data dir",)),),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="first_run.pact.begin",
            group=GROUP_ID,
            description="Start or replay the tour (Settings → Show me around)",
            input_schema=RevisionInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_begin"),
            fault_bindings=_bindings("invalid_transition", "stale_revision", "not_applicable_readonly"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="first_run.pact.advance",
            group=GROUP_ID,
            description="Advance one operator-driven tour step (evidence-locked steps refuse)",
            input_schema=AdvanceInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_advance"),
            fault_bindings=_bindings("invalid_transition", "stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="first_run.pact.hide",
            group=GROUP_ID,
            description="Hide the welcome card (chat keeps working; the footer link brings it back)",
            input_schema=HideInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_hide"),
            fault_bindings=_bindings("invalid_transition", "stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="first_run.pact.skip",
            group=GROUP_ID,
            description="Skip the tour from any non-terminal step; Local Only keeps working",
            input_schema=RevisionInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_skip"),
            fault_bindings=_bindings("invalid_transition", "stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="first_run.pact.reset",
            group=GROUP_ID,
            description="Replay the tour; facts, boundaries and provider state are kept",
            input_schema=RevisionInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_reset"),
            fault_bindings=_bindings("stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="first_run.pact.name",
            group=GROUP_ID,
            description="Name VOOL and optionally record how to address the operator",
            input_schema=NameInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_name"),
            fault_bindings=_bindings("fact_refused_secret", "stale_revision"),
            exit_codes=(0, 2, 30, 42),
        ),
        CommandSpec(
            command_id="first_run.pact.facts.set",
            group=GROUP_ID,
            description="Save optional operator facts as typed profile items (references, never secrets)",
            input_schema=FactsSetInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_facts_set"),
            fault_bindings=_bindings("fact_refused_secret", "stale_revision"),
            exit_codes=(0, 2, 30, 42),
        ),
        CommandSpec(
            command_id="first_run.pact.facts.forget",
            group=GROUP_ID,
            description="Tombstone one pact-captured fact",
            input_schema=FactsForgetInput,
            effects="destructive",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_facts_forget"),
            fault_bindings=_bindings("fact_validation_failed", "stale_revision"),
            exit_codes=(0, 2, 30, 42),
        ),
        CommandSpec(
            command_id="first_run.pact.boundary.set",
            group=GROUP_ID,
            description="Flip one privacy boundary through its owning authority (Local Only composite, memory pause, web lookups)",
            input_schema=BoundaryInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_boundary_set"),
            fault_bindings=_bindings("boundary_key_unknown", "invalid_transition", "stale_revision",
                                     "lock_unavailable", "boundary_partial", "authority_write_failed"),
            exit_codes=(0, 2, 30, 42),
        ),
        CommandSpec(
            command_id="first_run.pact.task.claim",
            group=GROUP_ID,
            description="Complete the task step ONLY from cryptographically verified receipt evidence",
            input_schema=ClaimInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_task_claim"),
            fault_bindings=_bindings("evidence_missing", "evidence_unverifiable", "evidence_session_unknown", "invalid_transition", "stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="first_run.pact.denial.claim",
            group=GROUP_ID,
            description="Complete the denial step ONLY from a real pre-dispatch refusal record",
            input_schema=ClaimInput,
            effects="mutating",
            permission=pact_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_pact_denial_claim"),
            fault_bindings=_bindings("evidence_missing", "evidence_unverifiable", "evidence_session_unknown", "invalid_transition", "stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="first_run.choice.local_only",
            group=GROUP_ID,
            description="Card button: choose Local Only (forwards to the provider choice authority)",
            input_schema=RevisionInput,
            effects="mutating",
            permission=provider_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_choice_local_only"),
            fault_bindings=_bindings("invalid_transition", "stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="onboarding.state",
            group=GROUP_ID,
            description="Project the provider first-run state machine",
            input_schema=EmptyInput,
            effects="read_only",
            permission=read,
            handler=Handler("core.command_registry.groups.first_run:_onboarding_state"),
            exit_codes=(0, 2),
        ),
        CommandSpec(
            command_id="onboarding.choice",
            group=GROUP_ID,
            description="Provider first-run choice: local_only | connect | skip",
            input_schema=ChoiceInput,
            effects="mutating",
            permission=provider_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_onboarding_choice"),
            fault_bindings=_bindings("invalid_transition", "stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="onboarding.reset",
            group=GROUP_ID,
            description="Review provider setup from the card again",
            input_schema=RevisionInput,
            effects="mutating",
            permission=provider_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_onboarding_reset"),
            fault_bindings=_bindings("stale_revision"),
            exit_codes=(0, 2, 30),
        ),
        CommandSpec(
            command_id="intake.begin",
            group=GROUP_ID,
            description="Open a memory-only key-intake session",
            input_schema=EmptyInput,
            effects="read_only",
            permission=provider_perm,
            handler=Handler("core.command_registry.groups.first_run:_intake_begin"),
            exit_codes=(0,),
        ),
        CommandSpec(
            command_id="intake.classify",
            group=GROUP_ID,
            description="Local-only, shape-only key classification — zero network, zero storage",
            input_schema=IntakeClassifyInput,
            effects="read_only",
            permission=provider_perm,
            handler=Handler("core.command_registry.groups.first_run:_intake_classify"),
            exit_codes=(0,),
        ),
        CommandSpec(
            command_id="intake.preview",
            group=GROUP_ID,
            description="Show exactly which origin would receive the key — makes ZERO requests",
            input_schema=IntakeProviderInput,
            effects="read_only",
            permission=provider_perm,
            handler=Handler("core.command_registry.groups.first_run:_intake_preview"),
            exit_codes=(0, 42),
        ),
        CommandSpec(
            command_id="intake.verify",
            group=GROUP_ID,
            description="THE one explicit, bounded check of the pasted key against the pinned endpoint",
            input_schema=IntakeProviderInput,
            effects="external_send",
            permission=provider_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_intake_verify"),
            exit_codes=(0,),
        ),
        CommandSpec(
            command_id="intake.complete",
            group=GROUP_ID,
            description="Store a verified key in the encrypted local vault (refuses anything unverified)",
            input_schema=IntakeCompleteInput,
            effects="mutating",
            permission=provider_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_intake_complete"),
            exit_codes=(0, 30),
        ),
        CommandSpec(
            command_id="intake.quarantine.list",
            group=GROUP_ID,
            description="List keys saved for later: unverified, quarantined, unused by anything",
            input_schema=EmptyInput,
            effects="read_only",
            permission=read,
            handler=Handler("core.command_registry.groups.first_run:_intake_quarantine_list"),
            exit_codes=(0,),
        ),
        CommandSpec(
            command_id="intake.quarantine.retry",
            group=GROUP_ID,
            description="Re-verify a quarantined key once; a verified answer promotes it into use",
            input_schema=IntakeQuarantineInput,
            effects="external_send",
            permission=provider_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_intake_quarantine_retry"),
            exit_codes=(0,),
        ),
        CommandSpec(
            command_id="intake.quarantine.delete",
            group=GROUP_ID,
            description="Delete a quarantined key (never touches a verified binding)",
            input_schema=IntakeQuarantineInput,
            effects="mutating",
            permission=provider_perm,
            availability=Availability("core.command_registry.groups.first_run:_probe_first_run_state"),
            handler=Handler("core.command_registry.groups.first_run:_intake_quarantine_delete"),
            exit_codes=(0,),
        ),
    ]
    for spec in specs:
        reg.add(spec)


def _bindings(*codes: str) -> tuple[FaultBinding, ...]:
    return tuple(
        FaultBinding(when=code, fault_code="conflict", remediation=_FAULT_REMEDIATIONS.get(code, ("retry or skip the step",)))
        for code in codes
    )


def _probe_first_run_state(context: dict) -> tuple[bool, str]:
    """Real availability evidence for every first-run command: the publication seam is
    usable only when the active data directory exists and accepts writes."""
    from core.runtime_paths import active_data_dir

    try:
        data_dir = active_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".first_run_availability_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, ""
    except Exception as exc:
        return False, f"first-run state directory not writable: {exc}"


def _gate_operator(inp, ctx):
    from core.command_registry.spec import AuthorityDecision

    if str(getattr(ctx, "principal", "") or "") != "operator":
        return AuthorityDecision(granted=False, reason="only the local operator may change first-run state")
    return AuthorityDecision(granted=True)
