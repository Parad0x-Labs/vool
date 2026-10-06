from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from core.internal_message_schema import is_turn_directives_message

# Per-message chat-template cost. Measured at 5 tokens/message for qwen2.5's template
# (<|im_start|>role\n ... <|im_end|>\n), taken as the slope between a 10- and a 50-message
# prompt; carried at 6 so the wrapper is not the thing that makes a long session read low.
_MESSAGE_OVERHEAD_TOKENS = 6
_RESPONSE_OVERHEAD_TOKENS = 4
_RETRIEVED_CONTEXT_MARKER = "<retrieved_context>"
_CONTEXT_SUMMARY_MARKER = "<context_summary>"

# transformers is already a dependency but is deliberately not used to tokenize here: the
# Qwen tokenizer is not vendored, so AutoTokenizer.from_pretrained() fetches it from
# huggingface.co on a runtime that must decide this offline; importing transformers costs
# ~46s cold against a guard that runs on every prompt; and one rate table must hold for
# every model served (qwen2.5, qwen3, deepseek-r1) rather than for a single tokenizer.
#
# Tokens per character by character class, calibrated against the runtime's own local
# tokenizer (qwen2.5:7b via Ollama prompt_eval_count, measured as the slope between a
# 3000- and a 6000-character sample so the chat template's fixed overhead cancels out).
# Measured tokens/char: ascii prose 0.19, ascii upper 0.30, ascii punctuation 0.58,
# digits 1.00, Han 0.73, kana 0.73, Hangul 0.89, Cyrillic 0.33, emoji 1.00, newline 0.03,
# space 0.008. Each rate carries a margin above its measurement: this estimate is what
# gates provider-side left truncation of the protected system prompt, so it must never
# read low. Validated against real counts for Chinese, Japanese, Korean, Cyrillic, English,
# shouty English, Python, Markdown, JSON, emoji and hex; see the pinned counts in
# tests/test_prompt_budget_guard.py.
#
# Scope of that never-reads-low claim (2026-09-30 qualification): the margins hold
# for the measured qwen2.5 tokenizer over the validated content classes, and the
# estimator's payload coverage is text content + tool-call fields + tool/format
# schemas (see estimate_message_tokens). It is NOT established for other served
# tokenizers beyond the margin carried in the table, NOT for chat templates other
# than the one measured, and NOT for non-text parts (images/audio are out of
# estimator scope entirely). Bytes are exact where bytes are measured; every token
# figure is an estimate on this calibration, never model tokenization.
_SPACE_TOKENS_PER_CHAR = 0.05
_NEWLINE_TOKENS_PER_CHAR = 0.12
_ASCII_LOWER_TOKENS_PER_CHAR = 0.26
_ASCII_UPPER_TOKENS_PER_CHAR = 0.42
_ASCII_SYMBOL_TOKENS_PER_CHAR = 0.80
_HAN_KANA_TOKENS_PER_CHAR = 0.82
_HANGUL_TOKENS_PER_CHAR = 0.98
_OTHER_LETTER_TOKENS_PER_CHAR = 0.48
_SYMBOL_TOKENS_PER_CHAR = 1.40
_ALNUM_RUN_TOKENS_PER_CHAR = 1.15

# Alphanumeric runs holding at least one digit: hashes, keys, ids, UUIDs, hex. The
# tokenizer emits about one token per character across the whole run -- the letters in
# "a3f9c2e1" do not merge into word tokens the way the same letters do in prose. A rate
# keyed on the character alone cannot see that, so these runs are priced as runs, before
# the per-character table is applied to what is left.
_ALNUM_RUN_RE = re.compile(r"[A-Za-z0-9]+")

# Han/kana/bopomofo/fullwidth blocks: ~1.4 chars per token against ~5 for Latin prose.
_HAN_KANA_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x303F),
    (0x3040, 0x30FF),
    (0x3100, 0x312F),
    (0x31F0, 0x31FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFE30, 0xFE4F),
    (0xFF00, 0xFFEF),
    (0x20000, 0x2FA1F),
)

# Hangul is priced apart from Han: a Korean syllable measures ~0.89 tokens/char against
# ~0.73 for Han, so folding them into one rate under-reads Korean.
_HANGUL_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7AF),
)

_char_cost_cache: dict[str, float] = {}


class PromptBudgetExceededError(RuntimeError):
    """Raised when protected instructions and the current turn cannot fit the model window."""

    def __init__(self, message: str, *, telemetry: dict[str, Any]) -> None:
        super().__init__(message)
        self.telemetry = telemetry


@dataclass(frozen=True)
class PromptBudgetResult:
    messages: list[dict[str, Any]]
    telemetry: dict[str, Any]


def _in_ranges(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code <= high for low, high in ranges)


def _char_cost(char: str) -> float:
    cached = _char_cost_cache.get(char)
    if cached is not None:
        return cached
    code = ord(char)
    if code < 128:
        if char in " \t":
            cost = _SPACE_TOKENS_PER_CHAR
        elif char in "\n\r":
            cost = _NEWLINE_TOKENS_PER_CHAR
        elif "a" <= char <= "z":
            cost = _ASCII_LOWER_TOKENS_PER_CHAR
        elif "A" <= char <= "Z":
            cost = _ASCII_UPPER_TOKENS_PER_CHAR
        else:
            cost = _ASCII_SYMBOL_TOKENS_PER_CHAR
    elif _in_ranges(code, _HANGUL_RANGES):
        cost = _HANGUL_TOKENS_PER_CHAR
    elif _in_ranges(code, _HAN_KANA_RANGES):
        cost = _HAN_KANA_TOKENS_PER_CHAR
    elif unicodedata.category(char)[0] in {"L", "M", "N"}:
        cost = _OTHER_LETTER_TOKENS_PER_CHAR
    else:
        cost = _SYMBOL_TOKENS_PER_CHAR
    _char_cost_cache[char] = cost
    return cost


def estimate_text_tokens(text: str) -> float:
    """Content-aware token estimate for one string.

    Digit-bearing alphanumeric runs are priced first and removed; Counter() then buckets
    what remains in C, so the per-character rate table is applied over the distinct
    characters present rather than over the full length -- the shed loop below re-estimates
    the prompt on every iteration and cannot afford a per-character Python pass over a full
    context window.
    """
    if not text:
        return 0.0
    remainder, run_chars = _price_alnum_runs(text)
    return math.fsum(
        [run_chars * _ALNUM_RUN_TOKENS_PER_CHAR]
        + [count * _char_cost(char) for char, count in Counter(remainder).items()]
    )


def _price_alnum_runs(text: str) -> tuple[str, int]:
    """Remove only digit-bearing runs without quadratic regex backtracking.

    The previous expression searched for a digit from every position in a
    long alphabetic run.  A large ordinary context summary therefore made a
    prompt-budget decision take tens of seconds.  Scan each alphanumeric run
    once, then retain runs that are ordinary prose.
    """
    fragments: list[str] = []
    cursor = 0
    priced_characters = 0
    for match in _ALNUM_RUN_RE.finditer(text):
        run = match.group(0)
        if not any("0" <= char <= "9" for char in run):
            continue
        fragments.append(text[cursor : match.start()])
        cursor = match.end()
        priced_characters += len(run)
    if not priced_characters:
        return text, 0
    fragments.append(text[cursor:])
    return "".join(fragments), priced_characters


def _message_tool_call_text(message: dict[str, Any]) -> str:
    """Every provider-bound character a message carries outside ``content``.

    Assistant tool-call turns serialize their ``tool_calls`` (call id, type,
    function name and the full arguments JSON) onto the wire, and tool-role
    replies carry ``name`` and ``tool_call_id`` — all of it tokenized by the
    provider even though none of it lives in ``content``. Arguments are the
    load-bearing case: a single large structured payload rides inside one
    message while ``content`` stays empty.
    """
    parts: list[str] = []
    name = message.get("name")
    if isinstance(name, str) and name:
        parts.append(name)
    tool_call_id = message.get("tool_call_id")
    if isinstance(tool_call_id, str) and tool_call_id:
        parts.append(tool_call_id)
    calls = message.get("tool_calls")
    if isinstance(calls, (list, tuple)):
        for call in calls:
            if not isinstance(call, dict):
                parts.append(str(call or ""))
                continue
            call_id = call.get("id")
            if isinstance(call_id, str) and call_id:
                parts.append(call_id)
            call_type = call.get("type")
            if isinstance(call_type, str) and call_type:
                parts.append(call_type)
            function = call.get("function")
            if isinstance(function, dict):
                function_name = function.get("name")
                if isinstance(function_name, str) and function_name:
                    parts.append(function_name)
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    parts.append(arguments)
                elif arguments is not None:
                    try:
                        parts.append(json.dumps(
                            arguments, sort_keys=True, ensure_ascii=False))
                    except (TypeError, ValueError):
                        parts.append(str(arguments))
            elif function is not None:
                parts.append(str(function))
    return "\n".join(part for part in parts if part)


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate prompt tokens from the character classes actually present.

    A flat chars/4 rule reads low for any content the tokenizer does not split like Latin
    prose -- measured against qwen2.5:7b, 8000 chars of Chinese is 5645 real tokens where
    chars/4 claimed 2006, and a 20980-char JSON blob is 11010 real tokens against a claimed
    5251. Under-reading here is not a cosmetic error: the guard passes the oversized prompt
    through and the provider left-truncates the protected system prompt anyway, which is the
    exact failure this module exists to prevent.

    Scope: text ``content`` plus the provider-bound tool-call fields
    (``tool_calls``/``name``/``tool_call_id``, see _message_tool_call_text).
    Non-text message parts (images, audio) are NOT estimated — this product
    does not pack them into provider messages, and inventing a byte-to-token
    rate for them would be a fabricated bound. Every figure stays an
    ESTIMATE against the calibrated rate table, not model tokenization.
    """
    content_tokens = math.fsum(
        estimate_text_tokens(_message_text(message.get("content"))) for message in messages
    )
    tool_call_tokens = math.fsum(
        estimate_text_tokens(_message_tool_call_text(message)) for message in messages
    )
    return (
        math.ceil(content_tokens + tool_call_tokens)
        + (_MESSAGE_OVERHEAD_TOKENS * len(messages))
        + _RESPONSE_OVERHEAD_TOKENS
    )


def fit_messages_to_context_window(
    messages: list[dict[str, Any]],
    *,
    num_ctx: int,
    output_reserve_tokens: int,
    protected_system_prompt: str,
    memory_prefix: str = "",
) -> PromptBudgetResult:
    """Fit a provider prompt without ever trimming its primary system instructions.

    On a retrieval-bearing turn the retrieved block is what answers the current question and
    stale verbatim history is not, so history is shed first and grounding is kept -- but only
    when keeping it is actually reachable. If the prompt still will not fit once every
    sheddable turn is gone, the retrieved block cannot survive under any order, so it is shed
    first instead and history is kept for as long as it fits.

    Remaining order is persistent memory, then as a last resort the compacted context summary.
    The summary is held back until after verbatim history because it is the compressed
    stand-in for the turns already dropped, so shedding it first would discard the most
    session context per token. If the protected system prompt plus current user turn still
    cannot fit, the call fails closed instead of relying on provider-side left truncation.
    """
    context_window = max(0, int(num_ctx))
    output_reserve = max(0, int(output_reserve_tokens))
    available = max(0, context_window - output_reserve)
    fitted = [dict(message) for message in list(messages or [])]
    estimated_before = estimate_message_tokens(fitted)
    dropped_retrieved = 0
    dropped_history = 0
    dropped_summary = 0
    dropped_memory = False

    current_user = _current_user_message(fitted)
    retrieval_present = _retrieval_index(fitted) is not None
    grounding_survives_history_shed = retrieval_present and estimate_message_tokens(
        _without_sheddable_history(fitted, current_user=current_user)
    ) <= available

    if not grounding_survives_history_shed:
        while fitted and estimate_message_tokens(fitted) > available:
            retrieval_index = _retrieval_index(fitted)
            if retrieval_index is None:
                break
            fitted.pop(retrieval_index)
            dropped_retrieved += 1

    while fitted and estimate_message_tokens(fitted) > available:
        history_indices = _oldest_history_indices(fitted, current_user=current_user)
        if not history_indices:
            break
        for history_index in reversed(history_indices):
            fitted.pop(history_index)
        dropped_history += len(history_indices)

    if fitted and estimate_message_tokens(fitted) > available and memory_prefix:
        primary_system_index = _primary_system_index(fitted)
        turn_index = next(
            (
                index
                for index, message in enumerate(fitted)
                if is_turn_directives_message(message) and memory_prefix in str(message["content"])
            ),
            None,
        )
        if turn_index is not None:
            fitted[turn_index] = {
                **fitted[turn_index],
                "content": str(fitted[turn_index]["content"]).replace(
                    f"{memory_prefix}\n\n---\n\n", "", 1
                ),
            }
            dropped_memory = True
        elif primary_system_index is not None:
            fitted[primary_system_index] = {
                **fitted[primary_system_index],
                "content": protected_system_prompt,
            }
            dropped_memory = True

    while fitted and estimate_message_tokens(fitted) > available:
        summary_index = next(
            (
                index
                for index, message in enumerate(fitted)
                if message is not current_user
                and _CONTEXT_SUMMARY_MARKER in _message_text(message.get("content"))
            ),
            None,
        )
        if summary_index is None:
            break
        fitted.pop(summary_index)
        dropped_summary += 1

    estimated_after = estimate_message_tokens(fitted)
    grounding_dropped = retrieval_present and _retrieval_index(fitted) is None
    telemetry = {
        "num_ctx": context_window,
        "output_reserve_tokens": output_reserve,
        "available_prompt_tokens": available,
        "estimated_prompt_tokens_before": estimated_before,
        "estimated_prompt_tokens_after": estimated_after,
        "dropped_retrieved_messages": dropped_retrieved,
        "dropped_history_messages": dropped_history,
        "dropped_context_summaries": dropped_summary,
        "dropped_memory": dropped_memory,
        "retrieval_present": retrieval_present,
        "grounding_dropped": grounding_dropped,
        "system_prompt_preserved": _system_prompt_preserved(fitted, protected_system_prompt),
        "status": (
            "trimmed"
            if (dropped_retrieved or dropped_history or dropped_summary or dropped_memory)
            else "fit"
        ),
    }
    if estimated_after > available or not telemetry["system_prompt_preserved"]:
        telemetry["status"] = "rejected"
        raise PromptBudgetExceededError(
            "prompt_budget_exceeded: protected system prompt and current turn require "
            f"{estimated_after} tokens, but only {available} are available "
            f"(num_ctx={context_window}, output_reserve={output_reserve})",
            telemetry=telemetry,
        )
    return PromptBudgetResult(messages=fitted, telemetry=telemetry)


#: What one image content part costs a vision model, in prompt tokens. Providers tile images into
#: a bounded number of patches (hundreds to a couple of thousand tokens) regardless of the byte
#: size; pricing the base64 payload as text would read a 400 KB PNG as ~500k tokens and make the
#: fitter shed every turn of history -- or fail the call closed -- for a picture the model reads
#: for ~1.5k. Deliberately on the high side of the published tilings so the guard never reads low.
IMAGE_PART_TOKENS = 1600
_IMAGE_PART_PLACEHOLDER = "\n" + ("█" * IMAGE_PART_TOKENS)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_message_text(item) for item in content)
    if isinstance(content, dict):
        if content.get("type") == "image_url":
            # An image part is a fixed-cost token block, never its data-URL bytes. The placeholder
            # is a run of one symbol priced at ~1 token/char by the table below, so the estimate
            # stays a pure function of the message text with no second code path in the fitter.
            return _IMAGE_PART_PLACEHOLDER
        if isinstance(content.get("text"), str):
            return str(content["text"])
        return " ".join(_message_text(value) for value in content.values())
    return str(content or "")


def _retrieval_index(messages: list[dict[str, Any]]) -> int | None:
    return next(
        (
            index
            for index, message in enumerate(messages)
            if _RETRIEVED_CONTEXT_MARKER in _message_text(message.get("content"))
        ),
        None,
    )


def _primary_system_index(messages: list[dict[str, Any]]) -> int | None:
    return next(
        (
            index
            for index, message in enumerate(messages)
            if str(message.get("role") or "").strip().lower() == "system"
            and _RETRIEVED_CONTEXT_MARKER not in _message_text(message.get("content"))
        ),
        None,
    )


def _current_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            message
            for message in reversed(messages)
            if str(message.get("role") or "").strip().lower() == "user"
        ),
        None,
    )


def _is_sheddable_history(
    messages: list[dict[str, Any]],
    index: int,
    *,
    current_user: dict[str, Any] | None,
    primary_system_index: int | None,
) -> bool:
    if index == primary_system_index or messages[index] is current_user:
        return False
    if is_turn_directives_message(messages[index]):
        # The per-turn facts (clock, corrections, recall) used to live inside the protected
        # primary system message; moving them after the history must not make them sheddable.
        return False
    text = _message_text(messages[index].get("content"))
    return _RETRIEVED_CONTEXT_MARKER not in text and _CONTEXT_SUMMARY_MARKER not in text


def _without_sheddable_history(
    messages: list[dict[str, Any]],
    *,
    current_user: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    primary_system_index = _primary_system_index(messages)
    return [
        message
        for index, message in enumerate(messages)
        if not _is_sheddable_history(
            messages,
            index,
            current_user=current_user,
            primary_system_index=primary_system_index,
        )
    ]


def _oldest_history_indices(
    messages: list[dict[str, Any]],
    *,
    current_user: dict[str, Any] | None,
) -> tuple[int, ...]:
    primary_system_index = _primary_system_index(messages)
    for index, message in enumerate(messages):
        if not _is_sheddable_history(
            messages,
            index,
            current_user=current_user,
            primary_system_index=primary_system_index,
        ):
            continue
        indices = [index]
        if str(message.get("role") or "").strip().lower() == "user" and index + 1 < len(messages):
            following = messages[index + 1]
            if (
                following is not current_user
                and str(following.get("role") or "").strip().lower() == "assistant"
            ):
                indices.append(index + 1)
        return tuple(indices)
    return ()


def _system_prompt_preserved(messages: list[dict[str, Any]], protected_system_prompt: str) -> bool:
    # Compare stripped: the memory prefix merge strips the prompt before embedding it, so an
    # exact match against surrounding whitespace would reject a prompt that is in fact intact.
    protected = protected_system_prompt.strip()
    if not protected:
        return True
    primary_system_index = _primary_system_index(messages)
    if primary_system_index is None:
        return False
    return protected in _message_text(messages[primary_system_index].get("content"))


__all__ = [
    "PromptBudgetExceededError",
    "PromptBudgetResult",
    "estimate_message_tokens",
    "estimate_text_tokens",
    "fit_messages_to_context_window",
]
