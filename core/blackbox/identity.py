"""Who did this effect: turn / session / request / attempt / execution identity, read from the
runtime's own seams, never minted from prose.

Resolution order for the turn id mirrors ``EffectLedger._resolve_identity``: the typed
``TurnRequest`` in the source context first, then the door-stamped canonical user turn id, then a
plain ``turn_id``. A call that runs under no turn at all (a direct library call, a background
scope, a test scope) gets a ``direct:<ledger>-<uuid>`` id minted ONCE per effect ledger, so every
effect of one scope shares one rollback unit and two unrelated scopes never merge.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any

# Minted ids per (id(ledger), ledger_id). Bounded; a ledger is a per-turn object and turns end.
_MINTED: OrderedDict[tuple[int, str], str] = OrderedDict()
_MINTED_CAP = 512


@dataclass(frozen=True)
class EffectIdentity:
    turn_id: str
    session_id: str
    request_id: str
    attempt_id: str
    execution_id: str
    ledger_id: str
    scope_name: str
    route: str
    surface: str
    logical_effect_id: str
    effect_instance_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _minted_turn_id(ledger: Any) -> str:
    ledger_id = str(getattr(ledger, "ledger_id", "") or "")
    key = (id(ledger), ledger_id)
    minted = _MINTED.get(key)
    if minted is None:
        minted = f"direct:{ledger_id or 'noledger'}-{uuid.uuid4().hex}"
        _MINTED[key] = minted
        while len(_MINTED) > _MINTED_CAP:
            _MINTED.popitem(last=False)
    return minted


def identity_from_context(source_context: dict[str, Any] | None, *, task_id: str = "") -> EffectIdentity:
    context = dict(source_context or {})
    turn_id = ""
    request_id = ""
    try:
        from core.turn_contract import TURN_REQUEST_KEY

        request = context.get(TURN_REQUEST_KEY)
    except Exception:
        request = None
    if request is not None:
        turn_id = str(getattr(request, "turn_id", "") or "")
        request_id = str(getattr(request, "request_id", "") or "")
    turn_id = turn_id or str(context.get("_canonical_user_turn_id") or context.get("turn_id") or "")
    execution = context.get("_execution_identity") if isinstance(context.get("_execution_identity"), dict) else {}
    request_id = request_id or str(context.get("request_id") or execution.get("request_id") or "")
    attempt_id = str(task_id or context.get("_blackbox_task_id") or execution.get("attempt_id") or "")
    execution_id = str(execution.get("execution_id") or "")

    ledger = None
    try:
        from core.effect_gateway import current_effect_ledger

        ledger = current_effect_ledger()
    except Exception:
        ledger = None
    ledger_id = str(getattr(ledger, "ledger_id", "") or "")
    scope_name = str(getattr(ledger, "scope_name", "") or "")
    if ledger is not None and not turn_id:
        try:
            ledger_turn, ledger_request = ledger._resolve_identity()
        except Exception:
            ledger_turn, ledger_request = "", ""
        turn_id = str(ledger_turn or "")
        request_id = request_id or str(ledger_request or "")
    if not turn_id:
        turn_id = _minted_turn_id(ledger) if ledger is not None else f"direct:noledger-{uuid.uuid4().hex}"

    surface = str(context.get("surface") or context.get("platform") or "")
    route = scope_name or f"turn:{surface or 'unknown'}"

    logical_effect_id = ""
    effect_instance_id = ""
    try:
        from core.effect_reconciliation import peek_in_flight_effect

        claim = peek_in_flight_effect()
    except Exception:
        claim = None
    if isinstance(claim, dict):
        logical_effect_id = str(claim.get("logical_effect_id") or "")
        effect_instance_id = str(claim.get("effect_instance_id") or "")

    return EffectIdentity(
        turn_id=turn_id,
        session_id=str(context.get("session_id") or context.get("runtime_session_id") or ""),
        request_id=request_id,
        attempt_id=attempt_id,
        execution_id=execution_id,
        ledger_id=ledger_id,
        scope_name=scope_name,
        route=route,
        surface=surface,
        logical_effect_id=logical_effect_id,
        effect_instance_id=effect_instance_id,
    )


__all__ = ["EffectIdentity", "identity_from_context"]
