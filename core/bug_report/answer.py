"""Wrong-answer evidence: the one place a report may quote the conversation, and how little.

A wrong-answer report quotes ONE answer -- the part the user selected, or the whole answer
when they reported the message -- and, only when the user ticks the box, their question for
that same turn. Both are read from the persisted transcript on the server, bound by the
canonical (session_id, request_id) pair, through the same availability gate the transcript
reader and the chat export use: an answer that was forgotten or withheld cannot be reported,
because its bytes are not served to anyone.

The receipt is the turn's own records (record state, model, provider, actions, tokens, time,
typed fault codes) projected read-only by ``core.proof_projection``. A record that is not
there is ``unknown``; nothing is inferred from the answer's prose.
"""
from __future__ import annotations

import re
from typing import Any

from core.bug_report.allowlist import sanitize_identifier, sanitize_model_identifier
from core.bug_report.schema import AnswerAction, AnswerReceipt, CaptureRefusedError

MAX_EXCERPT_CHARS = 2000
MAX_QUESTION_CHARS = 1000
MAX_ACTIONS = 20

_SESSION_RE = re.compile(r"^[A-Za-z0-9:_-]{1,80}$")
_REQUEST_RE = re.compile(r"^[A-Za-z0-9:_.-]{1,128}$")
_REASON_CODE_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")


class AnswerNotFoundError(CaptureRefusedError):
    """No served answer binds this (session, request) pair -- or it is no longer available."""


def valid_turn_ref(session_id: str, request_id: str) -> bool:
    return bool(_SESSION_RE.match(str(session_id or ""))) and bool(_REQUEST_RE.match(str(request_id or "")))


def _alnum(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def excerpt_belongs_to(excerpt: str, answer: str) -> bool:
    """True when the selected text is a piece of this answer.

    The page selects RENDERED text (bullets, links and emphasis already turned into layout)
    while the store holds the Markdown source, so the comparison is over letters and digits
    only: decoration differs between the two, the words do not."""
    needle = _alnum(excerpt)
    return bool(needle) and needle in _alnum(answer)


def load_turn(session_id: str, request_id: str) -> tuple[str, str]:
    """(question, answer) for one served turn, through the transcript's availability gate."""
    if not valid_turn_ref(session_id, request_id):
        raise AnswerNotFoundError("missing or invalid chat or answer id")
    from core.web.api.chat_export_api import ExportRefused, scan_snapshot

    try:
        turns, _counts = scan_snapshot(session_id)
    except ExportRefused as exc:
        raise AnswerNotFoundError("this chat has no saved answers to report") from exc
    matches = [turn for turn in turns if turn.request_id == request_id]
    if len(matches) != 1:
        raise AnswerNotFoundError("this answer is not saved yet, or is not part of this chat")
    turn = matches[0]
    if turn.unavailable:
        raise AnswerNotFoundError("this answer was forgotten or withheld, so nothing from it can be reported")
    if not turn.assistant.strip():
        raise AnswerNotFoundError("this answer has no text to report")
    return turn.user, turn.assistant


def _as_text(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _fault_codes(session_id: str, turn_keys: list[str]) -> tuple[str, ...]:
    if not turn_keys:
        return ()
    try:
        from core.faults.bug_export import fault_evidence_export

        export = fault_evidence_export(turn_key=str(turn_keys[0]), session_id=session_id)
    except Exception:
        return ()
    codes: list[str] = []
    for fault in export.get("faults") or []:
        code = sanitize_identifier(str(fault.get("code") or "").lower(), field="fault")
        if code and code not in codes:
            codes.append(code)
    return tuple(codes[:20])


def build_receipt(session_id: str, request_id: str) -> AnswerReceipt | None:
    """The turn's receipt from its own records, or None when no record binds this turn."""
    try:
        from core.proof_projection import ProofNotBound, build_turn_proof
    except Exception:
        return None
    try:
        proof = build_turn_proof(session_id=session_id, request_id=request_id, principal="owner_local")
    except ProofNotBound:
        return None
    except Exception:
        return None
    compact = proof.get("compact") or {}
    expanded = proof.get("expanded") or {}
    actions: list[AnswerAction] = []
    for row in (expanded.get("actions") or [])[:MAX_ACTIONS]:
        if not isinstance(row, dict):
            continue
        name = sanitize_identifier(str(row.get("name") or "").lower(), field="tool") or "unnamed"
        kind = sanitize_identifier(str(row.get("kind") or "").lower(), field="tool") or "unknown"
        status = sanitize_identifier(str(row.get("status") or "").lower(), field="tool") or "unknown"
        actions.append(AnswerAction(name=name, kind=kind, status=status, ok=bool(row.get("ok"))))
    reasons = tuple(
        reason for reason in (str(r) for r in (proof.get("state_reasons") or [])) if _REASON_CODE_RE.match(reason)
    )
    cost = compact.get("cost") or {}
    finalization = expanded.get("finalization") or {}
    witness = expanded.get("witness") or {}
    provider = sanitize_identifier(str(expanded.get("provider") or "").lower(), field="provider")
    return AnswerReceipt(
        record_state=sanitize_identifier(str(proof.get("state") or "").lower(), field="state") or "unknown",
        state_reasons=reasons[:12],
        model=sanitize_model_identifier(str(expanded.get("model") or "")) or "unknown",
        provider=provider or "unknown",
        actions=tuple(actions),
        sources_count=_as_text(compact.get("sources")),
        tokens=_as_text(cost.get("tokens")),
        elapsed_ms=_as_text(expanded.get("elapsed_ms")),
        integrity_ok=_as_text(finalization.get("content_hash_ok")),
        witness_consistent=_as_text(witness.get("consistent")),
        fault_codes=_fault_codes(session_id, list(expanded.get("turn_keys") or [])),
    )


__all__ = [
    "MAX_EXCERPT_CHARS",
    "MAX_QUESTION_CHARS",
    "AnswerNotFoundError",
    "build_receipt",
    "excerpt_belongs_to",
    "load_turn",
    "valid_turn_ref",
]
