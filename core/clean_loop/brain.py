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
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

MODEL_ENV = "VOOL_CLEAN_LOOP_MODEL"
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


def make_brain(router: Any, manifest: Any, source_context: dict[str, Any] | None) -> Callable[[list[dict[str, str]]], str]:
    """A `messages -> text` callable over the router's provider seam for one manifest."""
    from adapters.base_adapter import ModelRequest

    def call(messages: list[dict[str, str]]) -> str:
        from core import runtime_active_clock
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
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=request,
            output_mode="plain_text",
            task=None,
            source_context=context,
        )
        if response is None or error:
            raise RuntimeError(str(error or "no_response"))
        return str(getattr(response, "output_text", "") or "")

    return call


def model_label(manifest: Any) -> tuple[str, str]:
    return str(getattr(manifest, "provider_id", "") or ""), str(getattr(manifest, "model_name", "") or "")


__all__ = ["MODEL_ENV", "make_brain", "model_label", "resolve_local_manifest"]
