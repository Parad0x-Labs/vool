"""The typed security event: what was observed, joinable, accusation-free.

An event is minted FROM a fault record (:meth:`SecurityEvent.for_fault`) so every
identity field it carries -- turn, session, fault id -- is the fault's own truth and
the two rows join exactly. The event's summary, severity and resource class come from
the SEC vocabulary, not from the producing seam, so the observation reads the same no
matter which boundary produced the underlying fault.

The state machine is part of the type: observed -> acknowledged -> resolved, with
``with_state`` refusing anything else. (The durable store enforces the same machine on
transitions it persists; a frozen dataclass cannot enforce it on its own copies.)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from core.security_events.catalog import (
    SEC_SCHEMA,
    STATE_ACKNOWLEDGED,
    STATE_OBSERVED,
    STATE_RESOLVED,
    SecurityVocabularyError,
    sec_spec_for_fault_code,
)


class InvalidSecurityStateError(SecurityVocabularyError):
    """A state transition the machine does not allow (including out of a terminal state)."""


_ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATE_OBSERVED: (STATE_ACKNOWLEDGED,),
    STATE_ACKNOWLEDGED: (STATE_RESOLVED,),
    STATE_RESOLVED: (),  # terminal
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class SecurityEvent:
    """One observed security-relevant fact, and nothing inferred about anyone."""

    event_id: str
    sec_code: str
    schema_version: str
    severity: str
    state: str
    source: str
    resource_class: str
    summary: str
    fault_id: str
    evidence_refs: tuple[str, ...] = ()
    turn_key: str = ""
    session_id: str = ""
    observed_at: str = ""
    acknowledged_at: str = ""
    acknowledged_by: str = ""
    resolved_at: str = ""
    resolution_note: str = ""

    @classmethod
    def for_fault(
        cls,
        record: Any,
        *,
        sec_code: str = "",
        source: str = "",
        evidence_refs: tuple[str, ...] = (),
    ) -> SecurityEvent:
        """Mint the observation a fault record's family declares, with the fault as evidence.

        ``record`` is a ``core.faults.records.FaultRecord``; it is duck-typed here so the
        security plane imports no fault machinery beyond the vocabulary.
        """

        spec = sec_spec_for_fault_code(getattr(record, "code", ""))
        code = _text(sec_code) or spec.sec_code
        if code != spec.sec_code:
            raise SecurityVocabularyError(
                f"{code!r} does not observe fault {spec.fault_code!r} ({spec.sec_code} does)"
            )
        fault_id = _text(getattr(record, "fault_id", ""))
        turn = _text(getattr(record, "turn_key", ""))
        session = _text(getattr(record, "session_id", ""))
        material = "\x1f".join((SEC_SCHEMA, code, _text(source) or spec.source, turn, session, fault_id))
        refs = tuple(_text(ref) for ref in evidence_refs if _text(ref))
        if fault_id and fault_id not in refs:
            refs = (fault_id, *refs)
        # Short hex for the same two reasons as the fault id: the material is already the
        # identity, and long random-looking runs read as secret material to the outbound
        # scanner the bug-report pipeline runs on everything that leaves the machine.
        return cls(
            event_id="sec-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12],
            sec_code=code,
            schema_version=SEC_SCHEMA,
            severity=spec.severity,
            state=STATE_OBSERVED,
            source=_text(source) or spec.source,
            resource_class=spec.resource_class,
            summary=spec.summary,
            fault_id=fault_id,
            evidence_refs=refs,
            turn_key=turn,
            session_id=session,
            observed_at=_utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        """The JSON-safe projection; also the exact shape of the privacy-safe export row."""
        return {
            "event_id": self.event_id,
            "sec_code": self.sec_code,
            "schema_version": self.schema_version,
            "severity": self.severity,
            "state": self.state,
            "source": self.source,
            "resource_class": self.resource_class,
            "summary": self.summary,
            "fault_id": self.fault_id,
            "evidence_refs": list(self.evidence_refs),
            "turn_key": self.turn_key,
            "session_id": self.session_id,
            "observed_at": self.observed_at,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
            "resolved_at": self.resolved_at,
            "resolution_note": self.resolution_note,
        }

    def with_state(
        self,
        new_state: str,
        *,
        actor: str = "",
        note: str = "",
        at: str = "",
    ) -> SecurityEvent:
        """The same observation advanced through the typed state machine."""
        target = _text(new_state)
        current = self.state
        if target not in _ALLOWED_TRANSITIONS.get(current, ()):
            raise InvalidSecurityStateError(
                f"a security event cannot move from {current!r} to {target!r}"
            )
        stamp = _text(at) or _utcnow()
        if target == STATE_ACKNOWLEDGED:
            return replace(self, state=target, acknowledged_at=stamp, acknowledged_by=_text(actor))
        return replace(self, state=target, resolved_at=stamp, resolution_note=_text(note))


__all__ = [
    "STATE_ACKNOWLEDGED",
    "STATE_OBSERVED",
    "STATE_RESOLVED",
    "InvalidSecurityStateError",
    "SecurityEvent",
]
