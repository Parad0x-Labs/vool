"""
LLM-based conversation summarizer.

When a conversation exceeds SUMMARY_THRESHOLD messages:
  - Keep the last KEEP_RECENT messages verbatim
  - Compress all older messages into a structured <summary> assistant turn
  - Replace those messages with the summary turn

Structured output format preserves key facts verbatim, decisions, open
questions, and a narrative summary — so facts are never lost to compression.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from collections import OrderedDict
from contextvars import ContextVar

from core.ollama_endpoint import ollama_base_url
from core.provider_invocation_gateway import seal_direct_provider_invocation
from core.secret_redaction import redact_secrets

SUMMARY_THRESHOLD = 20   # compress when messages exceed this count
KEEP_RECENT = 8           # always keep this many recent messages verbatim

# The summarized range must stay put long enough for a cache key over it to repeat.
# The compressed window advances one turn at a time, so the summarized prefix is
# anchored to a multiple of this many messages; the rest rides along verbatim.
SUMMARY_STRIDE = 8

# The carried remainder rides inside the summary turn, and the prompt budget holds that
# turn back until every sheddable message is gone -- so an unbounded remainder is an
# unbounded un-sheddable block. Measured against a 4096 window (3856 available) with
# 6000-character turns: the summary turn reached 5347 tokens, the budget shed the
# grounding and then the summary itself, and the model received a 32-token prompt. The
# cap keeps the block small enough that shedding the rest of the prompt is never
# preferable to shedding it. Nothing is lost for good: what is trimmed here is
# summarized from the full history once the anchor advances past it.
_MAX_CARRIED_CHARS = 1500

# A summarizer that invents a value is worse than one that drops it: the invention
# re-enters the next turn as session history and cannot be told apart from a real
# fact. Measured on a 22-message compression, qwen3:0.6b (0.75B params) invented
# message ranges and filed filler turn numbers under "Decisions Made" on 6 of 8
# runs; qwen2.5:7b (7.6B) was clean on 8 of 8 for the same input. Only those two
# sizes were measured — this floor is the conservative line drawn between them,
# not a per-model fidelity guarantee.
_MIN_SUMMARY_PARAMS_B = 3.0

# Per-message cap for the extractive fallback.
_FALLBACK_MSG_CHARS = 240

# Marks a summary the model did not write. Callers that report on compaction need to tell a
# real summary from an excerpt taken because no model answered, and the two are otherwise
# indistinguishable from the text alone.
EXTRACTIVE_FALLBACK_MARKER = "extractive fallback"

_SUMMARY_CACHE_MAX = 32
_SUMMARY_CACHE: OrderedDict[str, str] = OrderedDict()

_UNSET = object()
_PICKED_MODEL: object = _UNSET
_SUMMARY_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "summary_provider_context",
    default=None,
)

_SUMMARY_SYSTEM = """\
You are a conversation compressor. Produce a structured summary preserving ALL information.

Output EXACTLY these four sections:

## Key Facts
(bullet every specific NON-SECRET value: ports, dates, names, numbers — VERBATIM, character-for-character \
exact. NEVER copy a secret: for any password, API key, token, seed, or private key write "[redacted]" \
in its place — do not reproduce the value.)

## Decisions Made
(bullet every decision, preference, commitment, or constraint the user stated)

## Open Questions
(bullet unresolved items or pending work — write "None" if none)

## Context Summary
(2-3 sentences: what was discussed and any important outcomes)

Rules:
- EXACT values for ports, dates, and plain numbers — word-for-word
- NEVER reproduce a secret: for any password, API key, token, seed, private key, or verification code,
  write "[redacted]" instead of the value
- Do NOT drop anything that might be asked about later
- Every value you write must appear in the transcript. Never invent, extrapolate, or fill a section
  with a placeholder — if a section has nothing in the transcript, write "None"
- No preamble, no commentary — output ONLY the four sections above"""


def _summarizer_num_ctx(model_tag: str) -> int:
    """Sized context for this direct-to-Ollama lane, from the existing sizing authority."""

    from core.runtime_provider_defaults import _ollama_context_window_for_bundle_role

    return _ollama_context_window_for_bundle_role("general", model_tag=str(model_tag or ""))


def _call_ollama(model: str, messages: list[dict], timeout: int = 60) -> str:
    # The canonical LocalModelPolicy: disabled means this summarizer must not run a local
    # model (or even probe for one) — the caller falls back to the extractive summary,
    # exactly as when Ollama is unreachable.
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return ""

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        # Sized for the same reason as every other direct-to-Ollama lane here: unsized, the model
        # loads at its NATIVE context. `general` is the same share the serving adapter would give
        # this model, so compression input can never be tighter than it already is.
        "options": {
            "temperature": 0.1,
            "num_predict": 1024,
            "num_ctx": _summarizer_num_ctx(model),
        },
    }
    invocation_context = dict(_SUMMARY_CONTEXT.get() or {})
    permit = seal_direct_provider_invocation(
        provider_id="ollama:conversation-summarizer",
        model_id=model,
        operation="summary",
        payload=payload,
        request_id=str(
            invocation_context.get("request_id")
            or (
                "summary-"
                + hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
            )
        ),
        context_manifest={
            "chat_id": str(invocation_context.get("chat_id") or ""),
            "project_id": str(
                invocation_context.get("project_id") or ""
            ),
            "capsule_version": "none",
            "items_included": [],
            "items_excluded": [],
        },
        max_output_tokens=1024,
        header_names=("Content-Type",),
    )
    req = urllib.request.Request(
        f"{ollama_base_url()}/api/chat",
        data=json.dumps(permit.consume()).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data["message"]["content"]


def _parse_param_count_b(raw: object) -> float:
    """Ollama reports details.parameter_size as e.g. '7.6B' or '751.63M'."""
    text = str(raw or "").strip().upper()
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([BMK]?)", text)
    if not match:
        return 0.0
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "M":
        return value / 1_000.0
    if unit == "K":
        return value / 1_000_000.0
    return value


def _default_box_model() -> str:
    try:
        from core.runtime_provider_defaults import default_runtime_model_tag

        return str(default_runtime_model_tag() or "").strip()
    except Exception:
        return ""


def _pick_model_uncached() -> str:
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return ""

    try:
        with urllib.request.urlopen(f"{ollama_base_url()}/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return ""

    eligible: dict[str, float] = {}
    for entry in data.get("models", []) or []:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        params = _parse_param_count_b((entry.get("details") or {}).get("parameter_size"))
        if params >= _MIN_SUMMARY_PARAMS_B:
            eligible[name] = params
    if not eligible:
        return ""

    # The tag this box is provisioned to run is already trusted to answer the user,
    # so it is the first choice for summarizing that same conversation.
    box_default = _default_box_model()
    for candidate in (box_default, f"{box_default}:latest"):
        if candidate and candidate in eligible:
            return candidate
    # Otherwise the smallest eligible model: fastest of those above the fidelity floor.
    return min(eligible, key=lambda name: (eligible[name], name))


def _pick_model() -> str:
    """Pick an installed model big enough to be trusted with verbatim facts.

    Returns "" when none qualifies, which routes summarize_messages to its
    deterministic extractive fallback instead of a model that fabricates.
    """
    global _PICKED_MODEL
    if _PICKED_MODEL is _UNSET:
        picked = _pick_model_uncached()
        # Only a successful pick is memoized. Ollama is commonly still starting when the
        # first compaction lands, and remembering that failure would pin the process to the
        # extractive fallback for its whole lifetime -- one transient timeout would silently
        # degrade every later turn of the session.
        if not picked:
            return ""
        _PICKED_MODEL = picked
    return str(_PICKED_MODEL or "")


def _extractive_summary(messages: list[dict], *, reason: str) -> str:
    """Deterministic, non-inventive compression: excerpt the source, infer nothing."""
    lines = ["## Key Facts"]
    kept = 0
    for message in messages:
        content = " ".join(str(message.get("content", "")).split())
        if not content:
            continue
        # Verbatim prefix rather than a split on ".": splitting there corrupts decimals
        # and version numbers, which turns a dropped fact into a wrong one.
        if len(content) > _FALLBACK_MSG_CHARS:
            content = content[:_FALLBACK_MSG_CHARS].rstrip() + " [...]"
        role = message.get("role", "user")
        lines.append(f"- [{role}] {content}")
        kept += 1
    lines += [
        "## Decisions Made",
        f"- ({EXTRACTIVE_FALLBACK_MARKER} — {reason}; no decisions inferred)",
        "## Open Questions",
        "- None",
        "## Context Summary",
        f"Verbatim excerpt of {kept} earlier message(s) ({EXTRACTIVE_FALLBACK_MARKER} — {reason}).",
    ]
    return "\n".join(lines)


def summary_is_extractive_fallback(summary: str) -> bool:
    """True when this text is an excerpt taken because no model answered."""
    return EXTRACTIVE_FALLBACK_MARKER in str(summary or "")


def extractive_fallback_reason(summary: str) -> str:
    """Why this summary was excerpted rather than written, or "" if a model wrote it."""
    match = re.search(rf"{EXTRACTIVE_FALLBACK_MARKER} — ([^;)\n]+)", str(summary or ""))
    return match.group(1).strip() if match else ""


def summarize_messages(
    messages: list[dict],
    *,
    model: str | None = None,
    session_id: str = "",
    project_id: str = "",
) -> str:
    """
    Ask the LLM to compress *messages* into a structured summary.
    Returns the summary text with Key Facts / Decisions / Open Questions / Context sections.
    """
    if not messages:
        return ""

    llm_model = str(model or "").strip() or _pick_model()
    if not llm_model:
        return redact_secrets(
            _extractive_summary(messages, reason="no local model large enough to summarize faithfully")
        )

    lines: list[str] = []
    for m in messages:
        role = m.get("role", "user").upper()
        content = str(m.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    transcript = "\n".join(lines)

    prompt = (
        "Summarize this conversation transcript into the four required sections. "
        "Preserve every non-secret fact, number, date, and decision verbatim; for any password, key, "
        "token, seed, or verification code, write [redacted] instead of the value:\n\n"
        f"{transcript}"
    )

    context_token = _SUMMARY_CONTEXT.set(
        {
            "request_id": f"summary-{_cache_key(messages, llm_model)}",
            "chat_id": str(session_id or ""),
            "project_id": str(project_id or ""),
        }
    )
    try:
        summary = _call_ollama(
            llm_model,
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:
        # Carry what actually failed: "LLM unavailable" alone cannot be told apart from a
        # timeout, a refused connection, or a missing model when reading a degraded report.
        summary = _extractive_summary(messages, reason=f"LLM unavailable — {type(exc).__name__}: {exc}")
    finally:
        _SUMMARY_CONTEXT.reset(context_token)
    # Defense in depth: strip any secret the model reproduced despite the instruction to redact it.
    return redact_secrets(summary)


def _cache_key(messages: list[dict], model: str | None) -> str:
    digest = hashlib.sha256()
    digest.update(f"{model or ''}\x00".encode())
    for message in messages:
        digest.update(
            f"{message.get('role', '')}\x01{message.get('content', '')}\x02".encode("utf-8", "replace")
        )
    return digest.hexdigest()


def _cached_summary(
    messages: list[dict],
    *,
    model: str | None,
    session_id: str = "",
    project_id: str = "",
) -> str:
    key = _cache_key(messages, model)
    cached = _SUMMARY_CACHE.get(key)
    if cached is not None:
        _SUMMARY_CACHE.move_to_end(key)
        return cached
    summary = summarize_messages(
        messages,
        model=model,
        session_id=session_id,
        project_id=project_id,
    )
    if summary_is_extractive_fallback(summary):
        # Caching an excerpt taken while the model was unreachable would outlive the outage
        # and keep serving the degraded summary after the model came back.
        return summary
    _SUMMARY_CACHE[key] = summary
    _SUMMARY_CACHE.move_to_end(key)
    while len(_SUMMARY_CACHE) > _SUMMARY_CACHE_MAX:
        _SUMMARY_CACHE.popitem(last=False)
    return summary


def reset_summary_cache() -> None:
    """Drop the memoized summaries and the picked model (test isolation, model changes)."""
    global _PICKED_MODEL
    _SUMMARY_CACHE.clear()
    _PICKED_MODEL = _UNSET


def compress_if_needed(
    messages: list[dict],
    *,
    threshold: int = SUMMARY_THRESHOLD,
    keep_recent: int = KEEP_RECENT,
    model: str | None = None,
    stride: int = SUMMARY_STRIDE,
    session_id: str = "",
    project_id: str = "",
) -> tuple[list[dict], bool]:
    """
    Compress *messages* if they exceed *threshold*.

    Returns (new_message_list, was_compressed).

    The returned list has at most (keep_recent + 1) messages:
      - [0] is a system or assistant <context_summary> turn
      - [1..] are the most recent *keep_recent* turns verbatim
    """
    if len(messages) <= threshold:
        return messages, False

    split = len(messages) - keep_recent
    old_messages = messages[:split]
    recent_messages = messages[split:]

    system_prefix: list[dict] = []
    if old_messages and old_messages[0].get("role") == "system":
        system_prefix = [old_messages[0]]
        old_messages = old_messages[1:]

    if not old_messages:
        return messages, False

    # The compressed range slides forward every turn, so a key over the exact range
    # never repeats and the model is re-invoked each turn. Anchoring the summarized
    # prefix to a stride boundary holds the range — and its cache key — still for
    # `stride` messages. The unanchored remainder is carried verbatim rather than
    # summarized, which keeps the returned list at keep_recent + 1.
    anchor = (len(old_messages) // stride) * stride if stride > 0 else len(old_messages)
    summarized = old_messages[:anchor]
    carried = old_messages[anchor:]

    blocks: list[str] = []
    if summarized:
        blocks.append(
            _cached_summary(
                summarized,
                model=model,
                session_id=session_id,
                project_id=project_id,
            )
        )
    if carried:
        verbatim = _carried_verbatim(carried)
        if verbatim:
            blocks.append(f"## Recent Context (verbatim)\n{redact_secrets(verbatim)}")

    summary_text = "\n\n".join(block for block in blocks if block)
    summary_turn: dict = {
        "role": "assistant",
        "content": f"<context_summary>\n{summary_text}\n</context_summary>",
    }

    return [*system_prefix, summary_turn, *recent_messages], True


def _carried_verbatim(carried: list[dict]) -> str:
    """Render the unanchored remainder, newest first, within a fixed character budget.

    The newest turns are kept because they are the ones the next question is most likely
    to be about; anything that does not fit is dropped rather than truncated mid-message,
    so no line is left saying half of something.
    """
    lines: list[str] = []
    used = 0
    for message in reversed(carried):
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        line = f"{str(message.get('role', 'user')).upper()}: {content}"
        if used + len(line) > _MAX_CARRIED_CHARS:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(reversed(lines))


def token_estimate(messages: list[dict]) -> int:
    """Approximate token count for a message list (chars / 4)."""
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return total_chars // 4


__all__ = [
    "KEEP_RECENT",
    "SUMMARY_STRIDE",
    "SUMMARY_THRESHOLD",
    "compress_if_needed",
    "reset_summary_cache",
    "summarize_messages",
    "token_estimate",
]
