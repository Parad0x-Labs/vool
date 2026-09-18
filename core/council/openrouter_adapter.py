"""Minimal OpenRouter adapter for live council trials.

Reads the user's existing OpenRouter credential through VOOL's own store
(``core.credential_store.get_credential("llm.cloud.openrouter")``) at call
time. The key is held in a local variable for the duration of one HTTP request
and NEVER logged, printed, returned, or written anywhere by this module.

Implements the council ``SeatModel`` protocol. On any transport/provider
failure it raises :class:`LiveSeatError` with the honest status — the runtime,
not this adapter, decides how a dead seat degrades. No synthetic output is
ever produced here.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from core.council.capsule import ContextCapsule
from core.council.runtime import LiveSeatError

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_CREDENTIAL_SLOT = "llm.cloud.openrouter"


@dataclass(frozen=True)
class SeatOutcome:
    """Response plus the provider-reported metadata bound into the receipt."""

    text: str
    model_attested: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float | None = None
    latency_ms: int = 0


class OpenRouterSeat:
    """One real model behind one council seat."""

    def __init__(self, model: str, *, timeout_s: float = 120.0, max_tokens: int = 3000) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.calls: list[dict] = []  # non-secret call log (model ids, tokens, ms)

    def respond(self, capsule: ContextCapsule) -> SeatOutcome:
        try:
            return self._respond_once(capsule, self.max_tokens)
        except _TruncatedBeforeContent as exc:
            # Reasoning-style models can burn the whole token budget thinking and
            # get truncated before any content. One honest retry of the SAME real
            # call at double budget — not a synthetic substitute.
            try:
                return self._respond_once(capsule, self.max_tokens * 2, prior_error=str(exc))
            except LiveSeatError:
                raise
            except _TruncatedBeforeContent as exc2:
                raise LiveSeatError(str(exc2)) from exc2

    def _respond_once(self, capsule: ContextCapsule, max_tokens: int, prior_error: str = "") -> SeatOutcome:
        key = _load_key()
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": capsule.render_prompt()}],
            "max_tokens": max_tokens,
            "temperature": 0.4,
            # Upstream flipped several slugs (deepseek-v4-flash, ling) to
            # reasoning-by-default: the hidden chain of thought consumes the
            # whole max_tokens budget and content comes back EMPTY
            # (finish_reason=length). Deterministically disable thinking —
            # measured: v4 judge 16k reasoning tokens/empty body -> clean
            # answer in 37s. Ignored by non-reasoning providers.
            "reasoning": {"enabled": False},
        }
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            _OPENROUTER_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            # THE SHARED DOOR, not a raw socket: veto first, then the ledger, then the wire.
            # This module predates the outbound-door work (2026-08-26) and shipped a bare
            # `urllib.request.urlopen`, so a council seat's provider call would have opened a
            # socket the per-turn ledger never saw and the network veto could not stop.
            from core.remote_fetch_policy import open_remote

            with open_remote(request, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # Honest single backoff-retry on rate limiting: same call, same
            # payload, just delayed — never a substitute output.
            if exc.code == 429 and not getattr(capsule, "_retried_429", False):
                time.sleep(20)
                capsule = ContextCapsule(
                    capsule.council_name, capsule.seat_id, capsule.role,
                    capsule.phase, capsule.task_text, capsule.kernel_facts,
                    capsule.materials,
                )
                object.__setattr__(capsule, "_retried_429", True)
                return self.respond(capsule)
            detail = ""
            with contextlib_suppress():
                detail = exc.read().decode()[:300]
            raise LiveSeatError(
                f"{self.model} HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LiveSeatError(f"{self.model} transport failure: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        # key is out of scope from here on
        del key
        choices = data.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        if not text.strip():
            finish = (choices[0].get("finish_reason") if choices else "") or ""
            raise _TruncatedBeforeContent(
                f"{self.model} empty completion (finish_reason={finish!r}"
                + (f"; prior: {prior_error}" if prior_error else "") + ")"
            )
        usage = data.get("usage") or {}
        attested = str(data.get("model") or "")
        cost = data.get("usage", {}).get("cost") if isinstance(data.get("usage"), dict) else None
        record = {
            "seat_phase": capsule.phase,
            "requested_model": self.model,
            "attested_model": attested,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_cost": cost,
            "latency_ms": latency_ms,
        }
        self.calls.append(record)
        return SeatOutcome(
            text=text,
            model_attested=attested,
            prompt_tokens=record["prompt_tokens"],
            completion_tokens=record["completion_tokens"],
            total_cost=cost,
            latency_ms=latency_ms,
        )


def _load_key() -> str:
    """Fetch the existing credential through VOOL's own mechanism."""
    from core.credential_store import get_credential

    key = get_credential(_CREDENTIAL_SLOT)
    if not key:
        raise LiveSeatError("no OpenRouter credential in VOOL credential store")
    return key


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


class _TruncatedBeforeContent(Exception):
    """finish_reason=length with no content — retried once at double budget."""


__all__ = ["OpenRouterSeat", "SeatOutcome"]
