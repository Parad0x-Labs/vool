"""Deterministic fast-path for image generation.

"generate an image of X" is rendered by the local engine or the credential-backed image.generate
tool directly, rather than relying on the local model to emit a tool call (a small local model is
unreliable at that). If no image service is configured yet, it points the user at
`image key <fal-key>` instead of failing opaquely. Every render is decided by the ONE permission
authority before either engine runs -- see the gate note on `maybe_handle_image_generation`.
"""
from __future__ import annotations

import contextlib
from typing import Any

from core.execution.constants import image_generation_intent

_ENGINE_STARTING_MSG = (
    "Starting the on-device image engine (ComfyUI); the first image after a fresh start "
    "takes a minute or two while the model loads."
)


def maybe_handle_image_generation(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Run an image render when the message asks for one. None when it is not an image request.

    Routes local-first: the on-device vool-local-render skill (SDXL via ComfyUI) is free and private,
    so it wins when it is installed and either reachable or explicitly asked for ("...locally"). Falls
    back to the cloud fal key, and points the user at setup when neither is ready.

    AUTHORITY GATE PARITY: this lane used to render with no permission decision at all, while the
    model route's `image.generate` call crossed `decide_tool_call` (which PROMPTS in Manual mode for
    media generation and DENIES it in Plan). The lane now takes its decision from the ONE authority
    via `authorize_runtime_tool` before rendering by either engine, and the cloud branch crosses the
    canonical `execute_authorized_runtime_tool` boundary with the held decision, so both routes
    decide identically and the generation runs exactly once.
    """
    prompt = image_generation_intent(user_input)
    if prompt is None:
        return None

    from core import local_media_render as lmr
    from core import media_tools
    from core.authorized_tool_execution import (
        REFUSAL_STATUSES,
        authorize_runtime_tool,
        execute_authorized_runtime_tool,
        permission_payload,
    )
    from core.mode_permission_policy import PermissionEffect
    from core.tool_intent_executor import _turn_was_cancelled

    def reply(
        body: str,
        *,
        ok: bool = True,
        reason: str = "image_generate",
        execution: Any = None,
        decision: Any = None,
    ) -> dict[str, Any]:
        result = agent._fast_path_result(
            session_id=session_id, user_input=user_input, response=body,
            confidence=0.95 if ok else 0.85, source_context=source_context, reason=reason,
        )
        details: dict[str, Any] = {}
        if execution is not None:
            details = dict(getattr(execution, "details", {}) or {})
            status = str(getattr(execution, "status", "") or "")
            if status in REFUSAL_STATUSES:
                result["status"] = status
                result["success"] = False
            result["details"] = details
        if decision is not None:
            # Every outcome on this lane carries the authority verdict that stood behind it --
            # an allow on a render just as much as a refusal (invariant 5).
            details.setdefault("permission", permission_payload(decision))
            result["details"] = details
            if decision.effect is not PermissionEffect.ALLOW:
                result["status"] = (
                    "pending_approval"
                    if decision.effect is PermissionEffect.REQUIRE_APPROVAL
                    else "blocked_by_mode"
                )
                result["success"] = False
        approval_request = dict(details.get("approval_request") or {})
        if approval_request:
            result["approval_request"] = approval_request
            result["task_outcome"] = "pending_approval"
            agent._emit_runtime_event(
                source_context,
                event_type="task_pending_approval",
                message=body,
                tool_name="image.generate",
                approval_request=approval_request,
            )
        return result

    # Cancellation precedes everything, as on the model route.
    if _turn_was_cancelled(source_context):
        cancelled = agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response="The turn was cancelled before this image ran.",
            confidence=0.85,
            source_context=source_context,
            reason="image_generate_cancelled",
        )
        cancelled["status"] = "cancelled"
        cancelled["success"] = False
        return cancelled

    # ONE permission decision for the generation, whichever engine would run it.
    decision = authorize_runtime_tool(
        "image.generate", {"prompt": prompt}, source_context=source_context
    )
    if decision is None or decision.effect is PermissionEffect.DENY:
        reason_text = (
            decision.reason
            if decision is not None
            else "no permission decision could be taken for media generation, and an undecided "
            "generation does not run"
        )
        return reply(
            f"I did not generate an image: {reason_text}",
            ok=False,
            reason="image_generate_blocked_by_mode",
            decision=decision,
        )
    if decision.effect is PermissionEffect.REQUIRE_APPROVAL:
        request = dict(decision.approval_request or {})
        from dataclasses import replace as _dc_replace

        decision_with_request = _dc_replace(
            decision, approval_request=request or decision.approval_request
        )
        return reply(
            f"Generating an image needs your approval first. {decision.reason} "
            "Review the details, then allow or deny this request.",
            ok=False,
            reason="image_generate_pending_approval",
            decision=decision_with_request,
        )

    has_local = lmr.local_render_available()
    has_fal = media_tools.has_image_service()
    wants_local = lmr.wants_local_render(user_input)

    # 1) On-device render — the free, private default when the skill is installed, UNLESS the user has
    #    only a cloud key and didn't ask for local. If ComfyUI isn't up yet, VOOL starts it herself, so
    #    "generate an image" just works with no manual step.
    prefer_local = has_local and (wants_local or lmr.comfyui_reachable() or not has_fal)
    if prefer_local:
        # Resource governor: pick the best render that FITS this Mac right now instead of freezing OR
        # refusing. plan_render() frees idle memory, then returns a tier — full 1024px when there's room,
        # or a smaller ComfyUI low-memory render when tight (slower, but it still renders). Only a
        # genuinely starved machine (too little even for a 512px low-memory render) falls back to cloud.
        from core import resource_governor as gov
        tier, room = gov.plan_render()
        if tier is None:
            freed = (room.note() + " ") if room.actions else ""
            offer = (" You have a fal.ai cloud key — ask me to render it on the cloud and I will."
                     if has_fal else " Or add a fal.ai key with `image key <key>` to render off-device.")
            return reply(
                f"{freed}Your Mac is extremely low on memory right now (~{room.usable_after_gb:.1f} GB free) — "
                f"too little even for a small local render without risking a freeze. Close a few apps and try "
                f"again.{offer}",
                ok=False, reason="image_generate_low_memory", decision=decision,
            )
        # Bring the engine up in the tier's memory mode (restarts it if it is up in a different mode, so a
        # tight Mac never runs a full-memory render).
        start_note = ""
        if not lmr.comfyui_reachable():
            if lmr.comfyui_autostart_enabled():
                with _ToolEvent(agent, source_context, prompt, starting=_ENGINE_STARTING_MSG):
                    _started, start_note = lmr.ensure_comfyui(low_memory=tier["low_memory"])
        elif tier["low_memory"]:
            with _ToolEvent(agent, source_context, prompt, starting=_ENGINE_STARTING_MSG):
                _started, start_note = lmr.ensure_comfyui(low_memory=tier["low_memory"])
        if lmr.comfyui_reachable():
            with _ToolEvent(agent, source_context, prompt):
                ok, ref = lmr.run_local_image_render(
                    prompt, width=tier["width"], height=tier["height"], steps=tier["steps"],
                )
            with contextlib.suppress(Exception):  # idle hygiene: hand memory back if the Mac is now tight
                gov.reclaim_if_pressured()
            if ok:
                with contextlib.suppress(Exception):  # tag the render with its chat so Files can group by chat
                    from core import generated_files
                    generated_files.record_generated_file(ref, session_id=session_id, prompt=prompt)
                lead = (room.note() + "\n\n") if room.actions else ""
                fit = "" if tier["low_memory"] == "" else f" _(rendered at {tier['label']} to fit your Mac)_"
                return reply(
                    f'{lead}Here\'s your local render for "{prompt}"{fit}:\n\n![{prompt}](file://{ref})\n\n{ref}',
                    decision=decision,
                )
            return reply(f"Local render failed: {ref}", ok=False, reason="image_generate_local_failed", decision=decision)
        # The on-device engine couldn't be brought up. Honor an explicit "locally" (don't silently use
        # the cloud); otherwise fall through to a cloud key when one exists.
        if wants_local or not has_fal:
            # Say WHY, in the engine's own words (it exited with a code and a log tail, or it is
            # still binding). A bare "couldn't bring it up" sent the user to check an install that
            # is present and fine.
            detail = f" {start_note}." if start_note else ""
            offer = (
                " You do have a fal.ai cloud key — ask me to render it on the cloud and I will."
                if has_fal
                else " You can add a fal.ai key with `image key <your-key>` to render off-device."
            )
            return reply(
                f"On-device rendering isn't available this turn.{detail}{offer}",
                ok=False, reason="image_generate_comfyui_unavailable", decision=decision,
            )

    # 2) Cloud fal key — through the ONE authorized execution boundary, carrying the decision this
    #    lane already holds so the generation is decided once and executed once.
    if has_fal:
        with _ToolEvent(agent, source_context, prompt):
            execution = execute_authorized_runtime_tool(
                "image.generate",
                {"prompt": prompt, "account": "default"},
                source_context=source_context,
                permission_decision=decision,
            )
        if execution is not None and getattr(execution, "ok", False):
            image_ref = str((getattr(execution, "details", {}) or {}).get("image_ref") or "")
            if image_ref:
                return reply(
                    f'Here\'s your image for "{prompt}":\n\n![{prompt}]({image_ref})\n\n{image_ref}',
                    execution=execution,
                )
            return reply(
                'Generated the image, but the provider returned no URL to show.',
                execution=execution,
            )
        message = str(getattr(execution, "response_text", "") or "") if execution is not None else ""
        return reply(
            message or "Image generation failed.", ok=False, execution=execution
        )

    # 3) Nothing configured.
    return reply(
        "Image generation isn't set up yet. Install the local ComfyUI render skill, or paste a fal.ai key "
        "with `image key <your-key>` — the key stays sealed on this machine.",
        ok=False, reason="image_generate_needs_setup",
    )


class _ToolEvent:
    """Emit tool_selected/tool_executed around the generation so the activity panel shows a step.
    Best-effort: a missing emitter never breaks the generation."""

    def __init__(self, agent: Any, source_context: dict[str, Any] | None, prompt: str,
                 *, starting: str | None = None) -> None:
        self._agent = agent
        self._ctx = source_context
        self._prompt = prompt
        self._starting = starting  # a phase message (e.g. "Starting the image engine") vs the render itself

    def _emit(self, event_type: str, message: str) -> None:
        emit = getattr(self._agent, "_emit_runtime_event", None)
        if emit is None:
            return
        with contextlib.suppress(Exception):
            emit(self._ctx, event_type=event_type, message=message, tool_name="image.generate", summary=message)

    def __enter__(self) -> _ToolEvent:
        self._emit("tool_selected", self._starting or f'Generating an image: "{self._prompt}"')
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        done = "Image engine ready." if self._starting else "Image generation finished."
        self._emit("tool_executed" if exc_type is None else "tool_failed", done)


__all__ = ["maybe_handle_image_generation"]
