"""Opt-in creative revise pass.

When VOOL_CREATIVE_REVISE is on, a generated media prompt (video/image) is run through the prompt
doctor (core.prompt_doctor) - scored, then rewritten up to two passes - before it is returned. Off by
default -> the text is returned unchanged, so behaviour is identical to today. Decision wrapper only:
the model call (scoring/rewriting) is injected as ``model_client``, so nothing here talks to a model
directly, and wiring this into the live turn path is a separate, supervised step (mirrors
core.agent_runtime.think_harder).
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from core.prompt_doctor import doctor_prompt

_MODE_FLAG = "VOOL_CREATIVE_REVISE"
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class ReviseResult:
    text: str
    revised: bool
    blocked: bool = False
    verdict: dict | None = None
    note: str = ""
    rounds: int = 1


def revise_enabled() -> bool:
    """True only when the opt-in env flag is set. Off by default."""
    return str(os.environ.get(_MODE_FLAG, "")).strip().lower() in _TRUTHY


def maybe_revise(
    prompt_text: str,
    *,
    model_client: Callable[[str], str],
    medium: str = "video",
    max_rewrites: int = 2,
) -> ReviseResult:
    """Return the doctored prompt when the revise flag is on, else the text unchanged.

    Off (default): the input text is returned as-is, revised=False, with no model call.
    On: run core.prompt_doctor.doctor_prompt (score -> rewrite up to max_rewrites -> re-score). An
    unsafe block returns empty text with blocked=True so the caller redirects rather than ships.
    """
    text = str(prompt_text or "")
    if not text.strip() or not revise_enabled():
        note = "revise_disabled" if text.strip() else "empty"
        return ReviseResult(text=text, revised=False, note=note)
    outcome = doctor_prompt(text, model_client=model_client, medium=medium, max_rewrites=max_rewrites)
    verdict = outcome.get("verdict")
    note = str(outcome.get("note") or "")
    rounds = int(outcome.get("rounds") or 1)
    if outcome.get("blocked"):
        return ReviseResult(text="", revised=True, blocked=True, verdict=verdict, note=note, rounds=rounds)
    final = str(outcome.get("final_prompt") or "")
    return ReviseResult(
        text=final or text,
        revised=bool(final) and final != text,
        verdict=verdict,
        note=note,
        rounds=rounds,
    )


__all__ = ["ReviseResult", "maybe_revise", "revise_enabled"]
