"""Curated model lists for the direct providers (non-OpenRouter) that feed the model dropdown.

OpenRouter has a public live ``/models`` catalog (``core.openrouter_catalog``); the direct
providers' ``/models`` endpoints are inconsistent and some need auth, so the dropdown shows a
small curated list per provider by default. A live ``/models`` discovery can augment this later,
but the curated list is what makes the picker usable offline and without a key.

Each row: ``{id, name, context_length, free, coding, prompt_usd_per_m, completion_usd_per_m}`` —
the same shape ``GET /api/cloud/models`` already returns for OpenRouter, so the UI needs no new
row handling. Prices are indicative USD per 1M tokens (the exact bill is the provider's).
"""
from __future__ import annotations

from typing import Any


def _row(model_id: str, name: str, ctx: int, pin: float, pout: float, *, coding: bool = False) -> dict[str, Any]:
    return {
        "id": model_id,
        "name": name,
        "context_length": ctx,
        "free": False,  # direct-provider models are paid; the free-first grouping is OpenRouter-only
        "coding": coding,
        "prompt_usd_per_m": round(pin, 4),
        "completion_usd_per_m": round(pout, 4),
    }


# Indicative, current-generation models per provider. Kept short on purpose — the user can type any
# id via `cloud model <id>`; this is the convenience picker, not an exhaustive mirror.
_CURATED: dict[str, list[dict[str, Any]]] = {
    "openai": [
        _row("gpt-4.1-mini", "GPT-4.1 mini", 1_047_576, 0.40, 1.60),
        _row("gpt-4.1", "GPT-4.1", 1_047_576, 2.00, 8.00, coding=True),
        _row("gpt-4o-mini", "GPT-4o mini", 128_000, 0.15, 0.60),
        _row("o4-mini", "o4-mini (reasoning)", 200_000, 1.10, 4.40, coding=True),
    ],
    "anthropic": [
        _row("claude-sonnet-4-5", "Claude Sonnet 4.5", 200_000, 3.00, 15.00, coding=True),
        _row("claude-haiku-4-5", "Claude Haiku 4.5", 200_000, 1.00, 5.00),
        _row("claude-opus-4-1", "Claude Opus 4.1", 200_000, 15.00, 75.00, coding=True),
    ],
    "groq": [
        _row("llama-3.3-70b-versatile", "Llama 3.3 70B", 131_072, 0.59, 0.79),
        _row("llama-3.1-8b-instant", "Llama 3.1 8B (instant)", 131_072, 0.05, 0.08),
        _row("moonshotai/kimi-k2-instruct", "Kimi K2 (on Groq)", 131_072, 1.00, 3.00, coding=True),
    ],
    "google": [
        _row("gemini-2.5-flash", "Gemini 2.5 Flash", 1_048_576, 0.30, 2.50),
        _row("gemini-2.5-pro", "Gemini 2.5 Pro", 1_048_576, 1.25, 10.00, coding=True),
        _row("gemini-2.0-flash", "Gemini 2.0 Flash", 1_048_576, 0.10, 0.40),
    ],
    "deepseek": [
        _row("deepseek-chat", "DeepSeek V3 (chat)", 65_536, 0.27, 1.10),
        _row("deepseek-reasoner", "DeepSeek R1 (reasoner)", 65_536, 0.55, 2.19, coding=True),
    ],
    "moonshot": [
        _row("kimi-k2-0711-preview", "Kimi K2", 131_072, 0.60, 2.50, coding=True),
        _row("moonshot-v1-128k", "Moonshot v1 128k", 131_072, 0.60, 2.50),
    ],
    "custom": [],  # unknown until the user configures a base URL; discovery could fill this later
}


def curated_models(provider_id: str) -> list[dict[str, Any]]:
    """The curated model rows for a direct provider (empty for openrouter/custom/unknown)."""
    return [dict(r) for r in _CURATED.get(str(provider_id or "").strip().lower(), [])]


__all__ = ["curated_models"]
