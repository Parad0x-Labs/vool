"""In-process provenance for deterministic memory command replies."""
from __future__ import annotations


class ForgetNoMatch(str):
    """A command-produced no-match result; plain or serialized text has no authority."""

    __slots__ = ("request", "session", "turn_key")

    def __new__(cls, target: str, *, request: str, session: str, turn_key: str = ""):
        value = super().__new__(
            cls, f'Nothing was removed. Forget found no memory in this chat that matched "{target}".'
        )
        object.__setattr__(value, "request", str(request or "").strip())
        object.__setattr__(value, "session", str(session or "").strip())
        object.__setattr__(value, "turn_key", str(turn_key or ""))
        return value

    def __setattr__(self, name, value):
        raise AttributeError("memory command results are immutable")

    def __delattr__(self, name):
        raise AttributeError("memory command results are immutable")


def command_turn_key(source_context: dict | None) -> str:
    from core.execution_truth import resolve_turn_key

    return str(resolve_turn_key(dict(source_context or {}), None) or "")


def is_bound_forget_no_match(
    result: dict, *, user_input: str, effective_input: str | None,
    session_id: str | None, source_context: dict | None,
) -> bool:
    response = result.get("response")
    outcome = result.get("_memory_command_outcome", response)
    return (
        type(outcome) is ForgetNoMatch
        and str(response or "") == str(outcome)
        and outcome.request in {str(user_input or "").strip(), str(effective_input or "").strip()}
        and outcome.session == str(session_id or "").strip()
        and outcome.turn_key == command_turn_key(source_context)
    )
