"""Session identity for no-handle test turns, acquired WITHOUT re-feeding the guarded
result object back into the runtime.

An in-process turn that passes no ``session_id_override`` and carries no door-stamped
session runs under the interior mint: the pure ``device:persona`` derivation the ingress
makes when it builds the canonical TurnRequest (``core.agent_runtime.agent.run_once``).
Tests used to read that identity off the returned result dict (``result["session_id"]``)
and pass it back into a later call's session parameter. The result dict is the output of
the final honesty guards, so feeding any of its fields into a session parameter welded
the guards' output to the telemetry persistence path — the static data-flow behind
CodeQL alert #155 (``py/clear-text-storage-sensitive-data``), whose name-based source
model treats the guard calls as secret sources and ships no sanitizer that could
re-validate them at the sink.

Deriving the identity from the same ingress derivation instead — and asserting the
runtime echoes exactly it — keeps the resume contract under test (the echoed session id
IS the identity the turn ran under, so resuming with it lands on the same chat) while
the value passed onward never flows from the guarded result.
"""

from __future__ import annotations

from typing import Any

from core.human_input_adapter import runtime_session_id


def no_handle_turn_session_identity(agent) -> str:
    """The session identity ``agent.run_once`` derives for a turn with no presented handle.

    Mirrors the ingress fallback chain's last branch exactly (override, then the
    door-stamped context session, then this mint), so it stays valid only for turns
    called with neither an override nor a ``session_id`` in their source context.
    """
    return runtime_session_id(device=agent.device, persona_id=agent.persona_id)


def echoed_session_identity(agent, result: dict[str, Any]) -> str:
    """Assert the turn result echoes the no-handle identity; return that identity.

    Use where a test used to read ``result["session_id"]`` to address the same session
    in a later call. The equality assertion is the echo law the resume contract rests
    on: the runtime must hand the caller back exactly the identity the turn ran under.
    """
    sid = no_handle_turn_session_identity(agent)
    echoed = str((result or {}).get("session_id") or "")
    assert echoed == sid, (
        "the runtime must echo the identity the turn ran under: "
        f"expected {sid!r}, got {echoed!r}"
    )
    return sid
