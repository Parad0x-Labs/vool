"""C13 — the ``context.pages.*`` command family: one owner for the turn-context
privacy surface.

Why a family and not one command with an ``action`` string: effects class and
permission weight are per-operation facts, not per-surface facts. ``recall`` is a
gated READ, ``erase`` is ``destructive``, ``pin``/``release_pin`` are
``idempotent_write``, the rest are ``mutating``. A single command would have to
declare one effects class for all eight, which would either reserve a mutation
budget for a read or under-declare an irreversible erasure. Nine typed commands
is therefore the SMALLEST declaration that is still coherent.

The authority is ``core.turn_context`` — nothing here re-implements scoping,
admission gating or erasure. These specs add what the raw HTTP surface never
had: a declared owner, a dispatch-time permission gate that every projection
(CLI, chat, API) traverses, a typed effects class, and — through
``execute_command`` — the effect-budget door.

Privacy law preserved verbatim: the whole family is OWNER-LOCAL. The gate below
refuses any principal that is not the operator and any peer that is not
loopback, so a foreign caller is refused by the AUTHORITY, not merely by the
transport that happened to receive it.
"""
from __future__ import annotations

from dataclasses import dataclass
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
    OperatorAuthority,
)

#: the one principal these controls are declared for
OWNER_PRINCIPAL = "owner_local"


# ---------------------------------------------------------------------------
# Typed inputs — one per operation, carrying exactly that operation's fields
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListPagesInput:
    session_id: str = ""
    include_content: bool = False
    limit: int = 200


@dataclass(frozen=True)
class AdmissionInput:
    """withhold / erase / pin — an admission plus an operator reason."""

    admission_id: str
    reason: str = ""


@dataclass(frozen=True)
class RecallInput:
    admission_id: str


@dataclass(frozen=True)
class SupersedeInput:
    admission_id: str
    content: str
    reason: str = ""
    title: str = ""


@dataclass(frozen=True)
class ReleasePinInput:
    admission_id: str
    pin_id: str


@dataclass(frozen=True)
class ScopeInput:
    session_id: str


@dataclass(frozen=True)
class BumpGenerationInput:
    session_id: str
    reason: str = ""


# ---------------------------------------------------------------------------
# Permission gate + availability evidence
# ---------------------------------------------------------------------------


def _peer(ctx: Any) -> str:
    return str((getattr(ctx, "extra", None) or {}).get("client_host") or "127.0.0.1")


def _gate_owner_local(_inp: Any, ctx: Any) -> AuthorityDecision:
    """Owner-local authority for every context-pages control.

    Two independent facts must both hold: the principal is the operator (so a
    model-lane or delegated principal can never reach these controls), and the
    transport peer is loopback (so a foreign channel is refused by the authority
    itself). Neither fact is inferred from the other.
    """
    from core.web.api.service import is_loopback_host

    principal = str(getattr(ctx, "principal", "") or "")
    if principal != "operator":
        return AuthorityDecision(
            granted=False,
            reason=f"context-pages controls are operator-only; principal was {principal!r}",
        )
    peer = _peer(ctx)
    if not is_loopback_host(peer):
        return AuthorityDecision(granted=False, reason="owner_local_required")
    return AuthorityDecision(granted=True)


def _probe_turn_context(_context: dict) -> tuple[bool, str]:
    """Machine evidence that the turn-context authority answers, never a constant."""
    try:
        from core.turn_context import inspect_admissions

        inspect_admissions(OWNER_PRINCIPAL, session_id="__availability_probe__", limit=1)
        return True, ""
    except Exception as exc:  # pragma: no cover - probe failure is the evidence
        return False, f"turn-context store unreachable: {exc}"


# ---------------------------------------------------------------------------
# Typed error mapping — the authority's exceptions become typed faults that
# still carry the legacy HTTP status, so the served bytes do not move.
# ---------------------------------------------------------------------------


def _fault_for(exc: Exception) -> HandlerFault | None:
    from core.context_layout import LayoutLawViolation
    from core.context_pages import PinActiveError
    from core.turn_context import (
        AdmissionClosedError,
        AdmissionNotFoundError,
        AdmissionRefusedError,
        AdmissionScopeError,
    )

    if isinstance(exc, AdmissionNotFoundError):
        return HandlerFault(
            fault_code="fault_validation",
            summary="admission not found",
            detail={"legacy_status": 404, "ok": False, "error": "admission_not_found"},
        )
    if isinstance(exc, AdmissionScopeError):
        return HandlerFault(
            fault_code="permission_denied",
            summary="admission out of scope",
            detail={"legacy_status": 403, "ok": False, "error": "admission_out_of_scope"},
        )
    if isinstance(exc, AdmissionClosedError):
        return HandlerFault(
            fault_code="conflict",
            summary="admission closed",
            detail={
                "legacy_status": 409,
                "ok": False,
                "error": "admission_closed",
                "message": str(exc),
            },
        )
    if isinstance(exc, AdmissionRefusedError):
        return HandlerFault(
            fault_code="fault_validation",
            summary="refused by privacy policy",
            detail={
                "legacy_status": 422,
                "ok": False,
                "error": "admission_refused_by_privacy_policy",
            },
        )
    if isinstance(exc, PinActiveError):
        return HandlerFault(
            fault_code="conflict",
            summary="page pinned by open work",
            detail={"legacy_status": 409, "ok": False, "error": "page_pinned_by_open_work"},
        )
    if isinstance(exc, LayoutLawViolation):
        return HandlerFault(
            fault_code="conflict",
            summary="context layout law violation",
            detail={
                "legacy_status": 409,
                "ok": False,
                "error": "context_layout_law_violation",
                "message": str(exc),
            },
        )
    if isinstance(exc, ValueError):
        return HandlerFault(
            fault_code="usage",
            summary=str(exc),
            detail={"legacy_status": 400, "ok": False, "error": str(exc)},
        )
    return None


def _run(action: str, call, *, receipt_kind: str) -> Any:
    """Execute one authority call, mapping its typed exceptions onto typed faults."""
    try:
        payload = call()
    except Exception as exc:
        fault = _fault_for(exc)
        if fault is None:
            raise
        return fault
    data = {"ok": True, "action": action, **(payload if isinstance(payload, dict) else {})}
    return HandlerOk(
        data=data,
        summary=f"context page {action}",
        receipts=({"kind": receipt_kind, "surface": "context.pages", "action": action},),
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_list(inp: ListPagesInput, _ctx: Any) -> Any:
    from core.turn_context import inspect_admissions

    try:
        records = inspect_admissions(
            OWNER_PRINCIPAL,
            session_id=str(inp.session_id or ""),
            include_content=bool(inp.include_content),
            limit=int(inp.limit),
        )
    except Exception as exc:
        fault = _fault_for(exc)
        if fault is None:
            raise
        return fault
    return HandlerOk(
        data={
            "ok": True,
            "principal": OWNER_PRINCIPAL,
            "session_filter": str(inp.session_id or ""),
            "count": len(records),
            "admissions": records,
        },
        summary=f"{len(records)} context admissions",
    )


def _handle_withhold(inp: AdmissionInput, _ctx: Any) -> Any:
    from core.turn_context import withhold_admission

    return _run(
        "withhold",
        lambda: withhold_admission(OWNER_PRINCIPAL, inp.admission_id, reason=inp.reason),
        receipt_kind="context_withhold",
    )


def _handle_erase(inp: AdmissionInput, _ctx: Any) -> Any:
    from core.turn_context import erase_admission

    return _run(
        "erase",
        lambda: erase_admission(OWNER_PRINCIPAL, inp.admission_id, reason=inp.reason),
        receipt_kind="context_erase",
    )


def _handle_supersede(inp: SupersedeInput, _ctx: Any) -> Any:
    from core.turn_context import supersede_admission

    if not str(inp.content or "").strip():
        return HandlerFault(
            fault_code="usage",
            summary="supersede requires non-empty content",
            detail={
                "legacy_status": 400,
                "ok": False,
                "error": "supersede requires non-empty content",
            },
        )
    return _run(
        "supersede",
        lambda: supersede_admission(
            OWNER_PRINCIPAL,
            inp.admission_id,
            content=inp.content,
            reason=inp.reason,
            title=inp.title,
        ),
        receipt_kind="context_supersede",
    )


def _handle_pin(inp: AdmissionInput, _ctx: Any) -> Any:
    from core.turn_context import pin_admission

    return _run(
        "pin",
        lambda: pin_admission(OWNER_PRINCIPAL, inp.admission_id, reason=inp.reason),
        receipt_kind="context_pin",
    )


def _handle_release_pin(inp: ReleasePinInput, _ctx: Any) -> Any:
    from core.turn_context import release_admission_pin

    return _run(
        "release_pin",
        lambda: release_admission_pin(
            OWNER_PRINCIPAL, inp.admission_id, pin_id=inp.pin_id
        ),
        receipt_kind="context_release_pin",
    )


def _handle_archive(inp: ScopeInput, _ctx: Any) -> Any:
    from core.turn_context import archive_scope_pages

    return _run(
        "archive",
        lambda: {
            "session_id": inp.session_id,
            "archived": archive_scope_pages(OWNER_PRINCIPAL, inp.session_id),
        },
        receipt_kind="context_archive",
    )


def _handle_recall(inp: RecallInput, _ctx: Any) -> Any:
    from core.turn_context import recall_page

    return _run(
        "recall",
        lambda: recall_page(OWNER_PRINCIPAL, inp.admission_id),
        receipt_kind="context_recall",
    )


def _handle_bump_generation(inp: BumpGenerationInput, _ctx: Any) -> Any:
    from core.turn_context import bump_context_generation

    return _run(
        "bump_generation",
        lambda: {
            "session_id": inp.session_id,
            "generation": bump_context_generation(
                OWNER_PRINCIPAL, inp.session_id, reason=inp.reason
            ),
        },
        receipt_kind="context_bump_generation",
    )


#: action name -> command id, the one mapping the HTTP adapter routes through.
ACTION_COMMANDS: dict[str, str] = {
    "withhold": "context.pages.withhold",
    "erase": "context.pages.erase",
    "supersede": "context.pages.supersede",
    "pin": "context.pages.pin",
    "release_pin": "context.pages.release_pin",
    "archive": "context.pages.archive",
    "recall": "context.pages.recall",
    "bump_generation": "context.pages.bump_generation",
}

#: every command id this family declares, listing command first.
FAMILY_COMMAND_IDS: tuple[str, ...] = (
    "context.pages.list",
    *sorted(ACTION_COMMANDS.values()),
)

_AUTHORITY = OperatorAuthority(
    kind="context.pages.control",
    verifier="core.command_registry.groups.context_pages_group:_gate_owner_local",
)
_AVAILABILITY = Availability(
    probe="core.command_registry.groups.context_pages_group:_probe_turn_context"
)


def register(reg: CommandRegistry) -> None:
    reg.add_group(
        GroupSpec(
            group_id="context",
            description="turn-context privacy controls: see, correct and take back the pages a turn admitted",
        )
    )

    reg.add(
        CommandSpec(
            command_id="context.pages.list",
            group="context",
            description="Inspect the scoped turn-context admissions (provenance always; bytes only on request)",
            input_schema=ListPagesInput,
            effects="read_only",
            permission=_AUTHORITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_list"
            ),
            exit_codes=(0, 2, 21),
        )
    )
    reg.add(
        CommandSpec(
            command_id="context.pages.recall",
            group="context",
            description="Read one admitted context page verbatim (owner-local, admission-gated)",
            input_schema=RecallInput,
            effects="read_only",
            permission=_AUTHORITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_recall"
            ),
            exit_codes=(0, 2, 21, 30, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="context.pages.withhold",
            group="context",
            description="Stop serving one admitted context page from the next turn onward (reversible)",
            input_schema=AdmissionInput,
            effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=_AUTHORITY,
            availability=_AVAILABILITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_withhold"
            ),
            exit_codes=(0, 2, 21, 22, 30, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="context.pages.erase",
            group="context",
            description="Forget one admitted context page and its bytes for good (irreversible)",
            input_schema=AdmissionInput,
            effects="destructive",
            capabilities=frozenset({"change_settings"}),
            permission=_AUTHORITY,
            availability=_AVAILABILITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_erase"
            ),
            exit_codes=(0, 2, 21, 22, 30, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="context.pages.supersede",
            group="context",
            description="Replace one admitted context page with corrected content (append-only)",
            input_schema=SupersedeInput,
            effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=_AUTHORITY,
            availability=_AVAILABILITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_supersede"
            ),
            exit_codes=(0, 2, 21, 22, 30, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="context.pages.pin",
            group="context",
            description="Pin one admitted context page so open work keeps it",
            input_schema=AdmissionInput,
            effects="idempotent_write",
            capabilities=frozenset({"change_settings"}),
            permission=_AUTHORITY,
            availability=_AVAILABILITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_pin"
            ),
            exit_codes=(0, 2, 21, 22, 30, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="context.pages.release_pin",
            group="context",
            description="Release one pin previously taken on an admitted context page",
            input_schema=ReleasePinInput,
            effects="idempotent_write",
            capabilities=frozenset({"change_settings"}),
            permission=_AUTHORITY,
            availability=_AVAILABILITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_release_pin"
            ),
            exit_codes=(0, 2, 21, 22, 30, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="context.pages.archive",
            group="context",
            description="Archive every unpinned context page in one session scope",
            input_schema=ScopeInput,
            effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=_AUTHORITY,
            availability=_AVAILABILITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_archive"
            ),
            exit_codes=(0, 2, 21, 22, 30, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="context.pages.bump_generation",
            group="context",
            description="Start a new context generation for one session; the frozen guard re-arms",
            input_schema=BumpGenerationInput,
            effects="mutating",
            capabilities=frozenset({"change_settings"}),
            permission=_AUTHORITY,
            availability=_AVAILABILITY,
            handler=Handler(
                "core.command_registry.groups.context_pages_group:_handle_bump_generation"
            ),
            exit_codes=(0, 2, 21, 22, 30, 42),
        )
    )
