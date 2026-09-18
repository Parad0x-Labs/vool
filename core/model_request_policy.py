"""Reasoning control as a typed request-level policy, owned by the caller and read by adapters.

Reasoning used to be switched by TASK NAME: the adapter read `metadata["workspace_audit_turn"]`
and turned thinking off for that one task. It worked, and it does not scale — the same 8,192-token
reasoning burn then happened on the pinned builder's bounded artifact call, which carries
`metadata={"builder_generation": True}` and so matched nothing. The next bounded call type would
have needed a third name, and every adapter would need to learn all of them.

The property that actually matters is not "is this an audit". It is: **this call must return an
artifact, and chain-of-thought spent inside its output budget is the artifact not arriving.** That
is a property of the REQUEST, so it belongs on the request:

    auto      — the adapter's own judgement (unchanged behaviour; the default)
    disabled  — this call must not spend its output budget reasoning
    required  — this call needs reasoning; a provider that cannot carry it must say so

Adapters translate the policy only where the knob is real (Ollama `think`, OpenRouter/OpenAI
`reasoning`). Where it is not, `unsupported_reasoning_policy_error` returns a sentence naming the
model — because silently dropping a policy is how a caller comes to believe a guarantee it never
got.
"""
from __future__ import annotations

from typing import Any

REASONING_AUTO = "auto"
REASONING_DISABLED = "disabled"
REASONING_REQUIRED = "required"

REASONING_MODES: tuple[str, ...] = (REASONING_AUTO, REASONING_DISABLED, REASONING_REQUIRED)


def normalize_reasoning_mode(value: Any) -> str:
    """One of ``REASONING_MODES``; anything unrecognised is ``auto``.

    Unknown input falls back to `auto` rather than raising: a policy is a hint about how to spend a
    budget, and an unparseable one must never take down a turn that would otherwise answer.
    """
    candidate = str(value or "").strip().lower()
    return candidate if candidate in REASONING_MODES else REASONING_AUTO


def request_reasoning_mode(request: Any) -> str:
    """The policy on ``request``, defaulting to ``auto`` for any object that has no field."""
    return normalize_reasoning_mode(getattr(request, "reasoning_mode", REASONING_AUTO))


def unsupported_reasoning_policy_error(adapter: Any, request: Any) -> str:
    """A truthful sentence when this adapter cannot honour the request's policy, else ``""``.

    Only `required` can be unsatisfiable in a way the caller must hear about: asking a model with
    no reasoning capability to reason is a contract the adapter cannot keep. `disabled` on a
    non-reasoning model is already satisfied — there is nothing to switch off — so it is not an
    error, and reporting one would break every ordinary local call.
    """
    mode = request_reasoning_mode(request)
    if mode != REASONING_REQUIRED:
        return ""
    try:
        supported = bool(adapter._is_thinking_capable_model())
    except Exception:
        supported = False
    if supported:
        return ""
    model = ""
    with_manifest = getattr(adapter, "manifest", None)
    if with_manifest is not None:
        model = str(getattr(with_manifest, "model_name", "") or "")
    return (
        f"reasoning_mode=required was requested, but `{model or 'this model'}` does not support "
        "reasoning on this provider, so the policy cannot be honoured."
    )
