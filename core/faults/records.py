"""The typed fault record: one failure, mapped once, carrying everything a consumer needs.

A fault record is the DURABLE contract between a failing boundary and everyone
downstream -- the served surface that owes the user a safe sentence, the operator view
that owes an action, the bug-report pipeline that owes a reproducible code, and the
security plane that owes an observation. The minimum field set is the contract:

* stable ``code`` + the ``schema_version`` of the catalog it came from;
* category, lifecycle, severity, retryability -- read from the catalog, never per-site
  guesses (one meaning per code, everywhere);
* the safe user message and operator action -- canned by the vocabulary, so no
  boundary can interpolate exception text into user-facing text again;
* the owning authority, and every identity the runtime carries for the failure:
  turn key, attempt id, effect id, session id;
* evidence references, a redacted internal context, and a redacted cause chain.

Two laws shape construction. IDENTITY: a record with no turn, attempt, effect, session
or stable dedupe key is REFUSED -- an unjoinable fault re-creates the fragmentation
this plane exists to remove (a credential-vault failure with no turn in flight still
identifies itself by credential name, which is a dedupe key). REDACTION: the context
is allowlisted-and-masked here, at construction, so no producer can forget to do it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from core.faults.catalog import (
    FAULT_SCHEMA,
    FaultVocabularyError,
    InvalidFaultTransitionError,
    allowed_fault_transition,
    get_spec,
)
from core.faults.redaction import redact_context


class MissingFaultIdentityError(FaultVocabularyError):
    """A fault nobody can join to a turn, attempt, effect, session or dedupe key."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class FaultCause:
    """One frame of the cause chain: the honest class name, the redacted message."""

    exception_type: str
    message: str

    @classmethod
    def from_exception(cls, exc: BaseException | None) -> FaultCause:
        kind = type(exc).__name__ if exc is not None else ""
        try:
            raw = str(exc or "")
        except Exception:
            raw = ""
        # Local import to keep the module import graph flat (redaction pulls regexes).
        from core.faults.redaction import redact_text

        return cls(exception_type=kind, message=redact_text(raw))

    def to_dict(self) -> dict[str, str]:
        return {"exception_type": self.exception_type, "message": self.message}


def _cause_chain(exc: BaseException | None, *, depth: int = 6) -> tuple[FaultCause, ...]:
    """The exception's own chain, redacted -- context the operator needs, secrets stripped."""
    chain: list[FaultCause] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and len(chain) < depth and id(current) not in seen:
        seen.add(id(current))
        chain.append(FaultCause.from_exception(current))
        current = current.__cause__ or current.__context__
    return tuple(chain)


@dataclass(frozen=True)
class FaultRecord:
    """One failure, as the owning boundary mapped it. Frozen: facts do not mutate."""

    fault_id: str
    code: str
    schema_version: str
    category: str
    severity: str
    retry: str
    lifecycle: str
    user_message: str
    operator_action: str
    authority: str
    turn_key: str
    attempt_id: str
    effect_id: str
    session_id: str
    evidence_refs: tuple[str, ...] = ()
    #: Redacted internal context (allowlisted keys, masked values) -- the what, never
    #: the payload. See core.faults.redaction.
    context: dict[str, str] = field(default_factory=dict)
    cause_chain: tuple[FaultCause, ...] = ()
    created_at: str = ""

    @classmethod
    def for_code(
        cls,
        code: str,
        *,
        authority: str,
        turn_key: str = "",
        attempt_id: str = "",
        effect_id: str = "",
        session_id: str = "",
        evidence_refs: tuple[str, ...] = (),
        context: dict[str, Any] | None = None,
        cause_chain: tuple[FaultCause, ...] = (),
        dedupe: str = "",
        lifecycle: str = "",
        created_at: str = "",
    ) -> FaultRecord:
        """Build one record from the catalog contract. The only constructor there is.

        ``dedupe`` is the producer's stable per-failure discriminator (a tool name, a
        call id, a credential name) and counts as identity: a boundary with no turn in
        flight still names WHAT failed.
        """
        from core.faults.catalog import LIFECYCLE_RAISED

        owner = _text(authority)
        if not owner:
            raise MissingFaultIdentityError("a fault record needs its owning authority")
        if not any(
            _text(value)
            for value in (turn_key, attempt_id, effect_id, session_id, dedupe)
        ):
            raise MissingFaultIdentityError(
                "a fault with no turn, attempt, effect, session or dedupe identity cannot be filed"
            )

        spec = get_spec(code)
        identity_material = "\x1f".join(
            (
                FAULT_SCHEMA,
                spec.code,
                owner,
                _text(turn_key),
                _text(attempt_id),
                _text(effect_id),
                _text(session_id),
                _text(dedupe),
            )
        )
        # 48 bits of identity hash, the same shape house report ids use (``br_`` + 12 hex):
        # the material is already the failure's identity, so the hash only has to tell
        # apart DIFFERENT identity tuples -- and a short id also survives the bug-report
        # pipeline's entropy scanner, which flags long random-looking runs as secret-like.
        fault_id = "fault-" + hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:12]
        return cls(
            fault_id=fault_id,
            code=spec.code,
            schema_version=FAULT_SCHEMA,
            category=spec.category,
            severity=spec.severity,
            retry=spec.retry,
            lifecycle=_text(lifecycle) or LIFECYCLE_RAISED,
            user_message=spec.user_message,
            operator_action=spec.operator_action,
            authority=owner,
            turn_key=_text(turn_key),
            attempt_id=_text(attempt_id),
            effect_id=_text(effect_id),
            session_id=_text(session_id),
            evidence_refs=tuple(_text(ref) for ref in evidence_refs if _text(ref)),
            context=redact_context(context),
            cause_chain=tuple(cause_chain),
            created_at=_text(created_at) or _utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        """The JSON-safe projection every consumer (store, export, bug report) reads."""
        return {
            "fault_id": self.fault_id,
            "code": self.code,
            "schema_version": self.schema_version,
            "category": self.category,
            "severity": self.severity,
            "retry": self.retry,
            "lifecycle": self.lifecycle,
            "user_message": self.user_message,
            "operator_action": self.operator_action,
            "authority": self.authority,
            "turn_key": self.turn_key,
            "attempt_id": self.attempt_id,
            "effect_id": self.effect_id,
            "session_id": self.session_id,
            "evidence_refs": list(self.evidence_refs),
            "context": dict(self.context),
            "cause_chain": [cause.to_dict() for cause in self.cause_chain],
            "created_at": self.created_at,
        }

    def with_lifecycle(self, new_lifecycle: str) -> FaultRecord:
        """The same fault, advanced through the typed lifecycle state machine."""
        current = self.lifecycle
        target = _text(new_lifecycle)
        if not allowed_fault_transition(current, target):
            raise InvalidFaultTransitionError(
                f"the fault lifecycle cannot move from {current!r} to {target!r}"
            )
        return replace(self, lifecycle=target)


def fault_record_for_code(code: str, **kwargs: Any) -> FaultRecord:
    """Module-level alias of :meth:`FaultRecord.for_code` for call sites that prefer a function."""
    return FaultRecord.for_code(code, **kwargs)


def cause_chain_for(exc: BaseException | None) -> tuple[FaultCause, ...]:
    """The redacted cause chain of an exception, for mappers that build records themselves."""
    return _cause_chain(exc)


__all__ = [
    "FaultCause",
    "FaultRecord",
    "InvalidFaultTransitionError",
    "MissingFaultIdentityError",
    "cause_chain_for",
    "fault_record_for_code",
]
