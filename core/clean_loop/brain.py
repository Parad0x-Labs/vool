"""Which local model answers a clean-loop turn, and how it is called.

No new client. The call goes through the router's own provider seam,
`MemoryFirstRouter._invoke_manifest` -- the one every provider call crosses (local-only backstop,
routing-plan fence, authorship fence, sealed invocation, call ledger, deadlines) -- with the
registered adapter for the model's manifest. Temperature 0 and reasoning disabled (`think: false`
on Ollama's thinking models).

The brain is local when:

* the owner pinned a model for this turn and that manifest runs on this machine, or
* the turn is Auto, no saved cloud default takes it, local models are enabled and running, and a
  registered local model has passed its final-answer check (the model the person made ready).

`VOOL_CLEAN_LOOP_MODEL` pins one registered local model by name, for measurement runs.

With `VOOL_CLEAN_LOOP_CLOUD` on (default OFF), a turn the owner's own cloud model would answer runs the
same loop on that model: the model the owner pinned for the turn, or the cloud model they saved for
Auto. Only the owner's own local turn gets this (the route checks that first), so a remote or phone
turn never spends the owner's key here.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MODEL_ENV = "VOOL_CLEAN_LOOP_MODEL"
CLOUD_ENV = "VOOL_CLEAN_LOOP_CLOUD"
_TRUTHY = {"1", "true", "yes", "on"}
_CALL_SECONDS = float(os.environ.get("VOOL_CLEAN_LOOP_CALL_SECONDS", "120") or 120)
_MAX_OUTPUT_TOKENS = 700


def _is_local(manifest: Any) -> bool:
    try:
        from core.local_model_policy import manifest_is_local

        return bool(manifest_is_local(manifest))
    except Exception:
        return False


def _ready(manifest: Any) -> bool:
    try:
        from core.final_answer_authorship import local_manifest_authorship_certified

        return bool(local_manifest_authorship_certified(manifest))
    except Exception:
        return False


def resolve_local_manifest(router: Any, source_context: dict[str, Any] | None) -> Any | None:
    """The local manifest that would answer this turn, or None when the brain is not local."""
    context = dict(source_context or {})
    try:
        manifests = list(router.registry.list_manifests(enabled_only=True))
    except Exception:
        return None
    local = [m for m in manifests if _is_local(m)]
    pinned = str(os.environ.get(MODEL_ENV) or "").strip()
    if pinned:
        return next((m for m in local if str(getattr(m, "model_name", "")) == pinned), None)
    requested = str(context.get("requested_model") or "").strip()
    if requested:
        try:
            from core.auto_local_only_mode import is_auto_selection

            auto = bool(is_auto_selection(requested))
        except Exception:
            auto = requested.lower() in {"auto", ""}
        if not auto:
            try:
                manifest = router._requested_model_manifest(context)
            except Exception:
                manifest = None
            return manifest if manifest is not None and _is_local(manifest) else None
    try:
        from core.cloud_only_default import default_for_auto_turn
        from core.local_model_policy import local_models_enabled
        from core.local_model_presence import local_model_running
        from core.request_trust import request_is_owner_local

        if not local_models_enabled() or default_for_auto_turn(owner_local=request_is_owner_local(context)):
            return None
        if not local_model_running():
            return None
    except Exception:
        return None
    return next((m for m in local if _ready(m)), None)


def cloud_enabled(env: Any = None) -> bool:
    return str((env if env is not None else os.environ).get(CLOUD_ENV) or "").strip().lower() in _TRUTHY


def resolve_cloud_manifest(router: Any, source_context: dict[str, Any] | None) -> Any | None:
    """The owner's own cloud manifest for this turn (pinned, or saved for Auto), or None."""
    if not cloud_enabled():
        return None
    context = dict(source_context or {})
    requested = str(context.get("requested_model") or "").strip()
    try:
        from core.auto_local_only_mode import is_auto_selection

        auto = not requested or bool(is_auto_selection(requested))
    except Exception:
        auto = requested.lower() in {"auto", ""}
    if auto:
        try:
            from core.cloud_only_default import default_for_auto_turn
            from core.request_trust import request_is_owner_local

            saved = default_for_auto_turn(owner_local=request_is_owner_local(context))
        except Exception:
            saved = ""
        if not saved:
            return None
        context["requested_model"] = saved
    try:
        manifest = router._requested_model_manifest(context)
    except Exception:
        return None
    return manifest if manifest is not None and not _is_local(manifest) else None


@dataclass(frozen=True)
class _StepTask:
    """The identity and size one paid loop step's reservation binds to (the turn's own task id)."""

    task_id: str
    task_summary: str
    prompt_tokens: int
    max_output_tokens: int


def _prompt_token_ceiling(text: str) -> int:
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return max(1, (ascii_chars + 3) // 4 + (len(text) - ascii_chars))


def _reserve_step(
    manifest: Any, context: dict[str, Any], messages: list[dict[str, str]], *, fallback_task_id: str = ""
) -> tuple[Any, Any, dict[str, Any]]:
    """A paid cloud model's step reserves its own hold first, exactly as the router's paid gate needs.

    Each loop step is one call and gets one reservation sized for that call, under the owner's own
    caps; a free or local model needs none. Returns (authorization, task, call context); raises when
    the reservation is refused, so the step fails like any provider error and nothing is sent.
    """
    from core.agent_runtime.builder.pinned_generation import _manifest_needs_paid_reservation

    # One id for the whole loop, so the per-task cap adds every step of this turn together.
    turn_task_id = (
        str(context.get("task_id") or context.get("turn_id") or "").strip()
        or fallback_task_id
        or f"clean-loop-{uuid.uuid4().hex[:12]}"
    )
    task = _StepTask(
        task_id=turn_task_id,
        task_summary="clean loop step on the owner's own cloud model",
        prompt_tokens=_prompt_token_ceiling("".join(str(m.get("content") or "") for m in messages)),
        max_output_tokens=_MAX_OUTPUT_TOKENS,
    )
    if not _manifest_needs_paid_reservation(manifest):
        return None, task, context
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    subtask = f"clean_loop_step_{uuid.uuid4().hex[:12]}"
    call_context = {**context, "task_id": turn_task_id, "subtask_id": subtask}
    denial: dict[str, str] = {}
    authorization = reserve_owner_pick_paid_call(
        manifest=manifest,
        task=task,
        source_context=call_context,
        task_kind="clean_local_loop",
        call_role="clean_loop_step",
        denial=denial,
    )
    if authorization is None:
        raise RuntimeError(f"paid_call_refused:{denial.get('reason') or 'not_authorized_or_reserved'}")
    return authorization, task, {**call_context, "authorized_paid_call": authorization, "model_call_role": "clean_loop_step"}


def make_brain(router: Any, manifest: Any, source_context: dict[str, Any] | None) -> Callable[[list[dict[str, str]]], str]:
    """A `messages -> text` callable over the router's provider seam for one manifest."""
    from adapters.base_adapter import ModelRequest

    loop_task_id = f"clean-loop-{uuid.uuid4().hex[:12]}"

    def call(messages: list[dict[str, str]]) -> str:
        from core import runtime_active_clock
        from core.memory_first_router import _release_undispatched_pin_reservation
        from core.paid_call_reservation import release_owner_pick_paid_call
        from core.provider_call_deadline import bind_provider_deadline

        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        request = ModelRequest(
            task_kind="clean_local_loop",
            prompt=last_user,
            system_prompt=system,
            messages=[dict(m) for m in messages],
            temperature=0.0,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            output_mode="plain_text",
            reasoning_mode="disabled",
            metadata={"clean_local_loop": True},
            allow_response_control_retry=False,
            allow_provider_retry=False,
        )
        context = bind_provider_deadline(
            {k: v for k, v in dict(source_context or {}).items() if k != "runtime_event_stream_id"},
            turn_deadline_monotonic=runtime_active_clock.monotonic() + _CALL_SECONDS,
            cleanup_margin_seconds=0.0,
            reason="clean local loop step",
        )
        authorization, task, context = _reserve_step(manifest, context, messages, fallback_task_id=loop_task_id)
        try:
            _adapter, response, error = router._invoke_manifest(
                manifest=manifest,
                request=request,
                output_mode="plain_text",
                task=task if authorization is not None else None,
                source_context=context,
            )
        except BaseException:
            # The invoke loop settles or releases what it handled; one that escaped it has an unknown
            # dispatch state, so it is held as possibly billed rather than handed back.
            if authorization is not None:
                release_owner_pick_paid_call(
                    authorization, reason="call_failed", source_context=context, call_role="clean_loop_step"
                )
            raise
        if response is None or error:
            if authorization is not None:
                # A refusal before any request was built leaves the hold standing: hand it back
                # (a no-op on one the invoke loop already settled or released).
                _release_undispatched_pin_reservation(authorization, source_context=context)
            raise RuntimeError(str(error or "no_response"))
        return str(getattr(response, "output_text", "") or "")

    return call


def model_label(manifest: Any) -> tuple[str, str]:
    return str(getattr(manifest, "provider_id", "") or ""), str(getattr(manifest, "model_name", "") or "")


__all__ = [
    "CLOUD_ENV",
    "MODEL_ENV",
    "cloud_enabled",
    "make_brain",
    "model_label",
    "resolve_cloud_manifest",
    "resolve_local_manifest",
]
