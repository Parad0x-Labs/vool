"""The write gate VOOL's own file tools ask before writing on an agent's behalf.

Layer 1 of the overlap watch. A model agent's tool calls run inside the VOOL server, not in a
process the agent owns, so the process-tree watch cannot see them. Instead the tool asks here
first, with the chat session it is serving:

* the session is not an agent's → ``allowed`` (ordinary chat turns are untouched);
* the path is inside the agent's own claim, and not inside a live sub-agent's sub-claim → allowed;
* otherwise → refused, the file is not touched, and the attempt is recorded as evidence.

The same binding gives a tool command the agent's run token (:func:`env_for_session`), so a shell
command a model agent starts is in that agent's lineage and the process watch covers it.

``INSTALLED`` is flipped by the tool layer once it calls :func:`check_write`. Until then the team
refuses write-mode model agents: a write the gate never sees is a write the team cannot stop.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Protocol

INSTALLED = False
_LOCK = threading.RLock()
_BINDINGS: dict[str, tuple[Any, str]] = {}


class _GateOwner(Protocol):
    def gate_write(self, agent_id: str, abs_path: str) -> tuple[bool, str]: ...
    def run_token(self, agent_id: str) -> str: ...


def mark_installed() -> None:
    global INSTALLED
    INSTALLED = True


def bind_session(session_id: str, owner: _GateOwner, agent_id: str) -> None:
    with _LOCK:
        _BINDINGS[str(session_id)] = (owner, agent_id)


def unbind_session(session_id: str) -> None:
    with _LOCK:
        _BINDINGS.pop(str(session_id), None)


def binding(session_id: str) -> tuple[Any, str] | None:
    with _LOCK:
        return _BINDINGS.get(str(session_id or ""))


def check_write(path: str, *, session_id: str = "") -> tuple[bool, str]:
    """``(allowed, reason)`` for a write the tool layer is about to make."""
    bound = binding(session_id)
    if bound is None:
        return True, ""
    owner, agent_id = bound
    return owner.gate_write(agent_id, os.path.realpath(str(path)))


GATE_STATUS = "blocked_by_agent_team_gate"
#: Classes an agent's tool call may have. Anything else — a shell command, a class the tool
#: table does not declare — is refused for an agent: a write the gate cannot place inside the
#: agent's claim is a write the team cannot stop. Ordinary chats are never affected.
_READ_CLASSES = {"read_only"}
_PATH_WRITE_CLASSES = {"workspace_write"}


def check_tool(intent: str, arguments: Any, source_context: Any) -> str:
    """The refusal sentence for an agent's tool call, or ``""`` when it may run.

    Called by VOOL's one runtime tool door (``execute_runtime_tool``) before dispatch. Reuses the
    council fence's own readings — session binding, declared side-effect class, alias-bound path
    arguments, symlink-following resolution — so the two fences cannot disagree about what a call
    touches."""
    from core.council import containment

    session_id = containment._context_session_id(source_context) or containment._inherited_session_id()
    bound = binding(session_id)
    if bound is None:
        return ""
    owner, agent_id = bound
    effect = containment._declared_side_effect_class(intent)
    if effect in _READ_CLASSES:
        return ""
    if effect not in _PATH_WRITE_CLASSES:
        return (f"agents may not run `{intent}` (side effect {effect or 'undeclared'}); only reads and "
                "writes inside their own claimed paths are allowed")
    args = containment._bound_arguments(intent, arguments)
    targets = containment._path_arguments(args)
    if not targets:
        return f"`{intent}` names no path, so the team cannot place it inside the agent's claim"
    workspace = getattr(owner, "workspace", None)
    for raw in targets:
        resolved = containment._resolved(raw, jail=workspace) if workspace is not None else None
        if resolved is None:
            return f"`{raw}` could not be resolved"
        allowed, reason = owner.gate_write(agent_id, str(resolved))
        if not allowed:
            return reason
    return ""


def env_for_session(session_id: str) -> dict[str, str]:
    """Extra environment for a command a tool runs for ``session_id`` ({} for ordinary chats)."""
    bound = binding(session_id)
    if bound is None:
        return {}
    owner, agent_id = bound
    from core.agent_team.lineage import ENV_TOKEN

    return {ENV_TOKEN: owner.run_token(agent_id)}


__all__ = ["INSTALLED", "bind_session", "binding", "check_write", "env_for_session", "mark_installed",
           "unbind_session"]
