"""What a turn actually cost, composed by the runtime instead of asked of the model.

A live audit turn on 2026-08-01 was asked: *"Report the cloud-input token count and which context
components used most of it."* The reply was **"Cloud-input token count: ~2,800 tokens"** followed by
a prose guess at the components. Nothing measured that number. The turn ran on
``openrouter-byok:nvidia/…``, whose completion response carries a real ``usage`` block, and the
runtime had already assembled a per-item context ledger — so a measured answer existed twice over
and neither copy was wired to the reply.

**A model on a bad turn cannot report its own token count.** It does not see the wire, it does not
see the assembler, and asking it to introspect produces a plausible number with nothing behind it —
the same failure shape as the build lane reporting a green suite it never ran. So this is composed
here, from measured values, exactly the way ``app_builder.render_app_build_response`` composes a
build receipt.

Two numbers, two different provenances, and the difference is never blurred:

* **Provider-reported** — ``usage.prompt_tokens`` / ``completion_tokens`` off the completion
  response. This is the real cloud input. Recorded per key by PRESENCE, not truthiness: a provider
  that genuinely reported ``0`` is a measurement, and a provider that reported nothing is a hole.
  A hole is printed as a hole (``not available``, naming the provider) and never filled with an
  estimate — a lane with no usage object says which figure is missing.
* **Assembler estimate** — ``PromptAssemblyReport.items_included``, at the loader's ~4-chars-per-
  token approximation. This is the only source of *which component cost what*, and it is labelled
  an estimate everywhere it appears.

Percentages are therefore taken against the itemised context total, never against the measured
input: the measured input also carries the system prompt, the conversation messages and any tool
schemas, none of which the assembler itemises. A percentage across that boundary would read as
precision the runtime does not have.
"""
from __future__ import annotations

import re
from typing import Any

# ``prompt_budget`` telemetry (core/prompt_budget.py) is the FITTER'S PLAN — estimated sizes and
# what it shed to make the prompt fit. These names are deliberately separate from it: mixing a
# measurement into a dict whose every other key is an estimate is how the two get confused, which
# is the confusion this module exists to remove.
SOURCE_PROVIDER_REPORTED = "provider_reported"
SOURCE_NOT_REPORTED = "not_reported"

TOKEN_USAGE_HEADING = "**Token usage — this turn**"

# How many components get their own row before the tail is summarised. The tail is always stated
# (count and tokens), never silently dropped: a truncated table reads as the whole context.
_MAX_COMPONENT_ROWS = 12


def _reported_int(block: dict[str, Any], *keys: str) -> int | None:
    """The first key this provider actually reported, or None when it reported none of them.

    Presence, not truthiness. ``core/memory_first_router.py::_record_response_usage`` uses
    ``a or b or 0``, which cannot tell a reported 0 from an absent field — fine for a spend meter
    where both bill nothing, wrong here, where the whole point is saying which figure is missing.
    """
    for key in keys:
        value = block.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None


def measured_call_usage(
    usage: Any,
    *,
    provider_id: str = "",
    model_id: str = "",
) -> dict[str, Any]:
    """The measured token counts for one model call, in the shape the receipt and the event carry.

    Spans the three provider dialects the runtime speaks: Ollama (``prompt_eval_count`` /
    ``eval_count``), OpenAI-compatible incl. OpenRouter (``prompt_tokens`` / ``completion_tokens``)
    and Anthropic (``input_tokens`` / ``output_tokens``).
    """
    try:
        block = dict(usage or {})
    except Exception:
        block = {}
    input_tokens = _reported_int(block, "prompt_eval_count", "prompt_tokens", "input_tokens")
    output_tokens = _reported_int(block, "eval_count", "completion_tokens", "output_tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "source": (
            SOURCE_PROVIDER_REPORTED
            if (input_tokens is not None or output_tokens is not None)
            else SOURCE_NOT_REPORTED
        ),
        "provider_id": str(provider_id or ""),
        "model_id": str(model_id or ""),
    }


def aggregate_call_usage(
    usages: list[Any] | tuple[Any, ...],
    *,
    provider_id: str = "",
    model_id: str = "",
    provider_calls: int | None = None,
) -> dict[str, Any]:
    """Totals across several calls, carrying whether the total is COMPLETE.

    A multi-call turn used to be summed with `_merge_provider_usage`, which adds numeric keys. That
    silently equates "this call reported nothing" with "this call reported 0", so a turn where one
    provider omitted its usage block reported a confident total that was short by an unknown amount
    — and the operator was given it as *the* cloud-input token count.

    A sum over an incomplete set is a LOWER BOUND, and this says so in the data rather than in a
    comment: `calls`, `calls_with_usage`, `calls_missing_usage`, `complete`, `lower_bound`. A caller
    that prints the number without reading those is printing a bound as a total, which the receipt
    line below refuses to do.

    **`provider_calls` — the calls this aggregate cannot see at all.** `usages` holds one entry per
    call that reached the usage-recording seam. A provider call that bypasses it contributes no
    entry, so it is not a call missing its usage block: it is absent from `calls` entirely, and the
    aggregate would then report `complete` over a strict subset of the turn. The runtime has exactly
    such a path — the intent arbiter posts straight to the provider — so a turn that spent an
    arbitration call and one answering call reported `1 model call, from provider receipts for every
    call` while two were made.

    Pass this turn's ledger count (`core.turn_model_call_ledger.turn_model_calls`) and the surplus
    is carried as `calls_unmetered`, which forces `complete` false. That is the "includes all calls
    or explicitly labels unmetered" rule: an unmetered call is never quietly dropped from the
    denominator. Omitting the argument keeps the previous behaviour for callers that have no ledger.
    """
    blocks: list[dict[str, Any]] = []
    for item in list(usages or ()):
        try:
            blocks.append(dict(item or {}))
        except Exception:
            blocks.append({})

    input_total = 0
    output_total = 0
    calls_with_usage = 0
    for block in blocks:
        reported_in = _reported_int(block, "prompt_eval_count", "prompt_tokens", "input_tokens")
        reported_out = _reported_int(block, "eval_count", "completion_tokens", "output_tokens")
        if reported_in is None and reported_out is None:
            continue
        calls_with_usage += 1
        input_total += reported_in or 0
        output_total += reported_out or 0

    metered = len(blocks)
    missing = metered - calls_with_usage
    # Never negative: a ledger that counted FEWER calls than produced usage blocks means the ledger
    # missed a seam, and inventing a negative surplus here would hide that instead of leaving the
    # two numbers visibly disagreeing.
    unmetered = max(0, int(provider_calls) - metered) if provider_calls is not None else 0
    calls = metered + unmetered
    complete = calls > 0 and missing == 0 and unmetered == 0
    return {
        "input_tokens": input_total if calls_with_usage else None,
        "output_tokens": output_total if calls_with_usage else None,
        "calls": calls,
        "calls_metered": metered,
        "calls_unmetered": unmetered,
        "calls_with_usage": calls_with_usage,
        "calls_missing_usage": missing,
        "complete": complete,
        "lower_bound": not complete,
        "source": SOURCE_PROVIDER_REPORTED if calls_with_usage else SOURCE_NOT_REPORTED,
        "provider_id": str(provider_id or ""),
        "model_id": str(model_id or ""),
    }


def usage_receipt_line(totals: dict[str, Any]) -> str:
    """One operator-visible sentence for an aggregate, never presenting a bound as a total."""
    block = dict(totals or {})
    calls = int(block.get("calls") or 0)
    missing = int(block.get("calls_missing_usage") or 0)
    unmetered = int(block.get("calls_unmetered") or 0)
    input_tokens = block.get("input_tokens")
    output_tokens = block.get("output_tokens")
    if not calls or block.get("source") == SOURCE_NOT_REPORTED:
        return (
            f"_Token usage: not available — none of the {calls} model call(s) this turn reported "
            "usage._"
        )
    figures = f"{input_tokens:,} input / {output_tokens:,} output tokens across {calls} model call(s)"
    if block.get("complete"):
        return f"_Token usage: {figures}, from provider receipts for every call._"
    # Two different reasons a total is short, named separately: a call that reported no usage block,
    # and a call that never reached the usage seam at all. Collapsing them would tell the operator
    # the provider was silent when in fact the runtime never asked.
    shortfalls = []
    if missing:
        shortfalls.append(f"{missing} reported no usage")
    if unmetered:
        shortfalls.append(f"{unmetered} unmetered (made outside the usage-recording path)")
    return (
        f"_Token usage: at least {figures} — this is a LOWER BOUND, not the total: "
        f"of {calls} call(s), {', and '.join(shortfalls)}, so their tokens are not counted here._"
    )


# --------------------------------------------------------------------------------------
# A multi-call turn: four different numbers that were being printed as one
# --------------------------------------------------------------------------------------

# Above either of these, a single-file audit is worth flagging to the operator rather than merely
# recording. Both are deliberately generous: the point is to catch a 9-call/28k-token audit of one
# file, not to editorialise about every turn that made three calls.
UNUSUAL_CALL_COUNT = 6
UNUSUAL_INPUT_TOKENS = 15000


def audit_token_breakdown(
    call_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    totals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep four numbers apart that a single "input tokens" figure was blending into one.

    The incident printed **28,741** as though it were the size of one prompt. It was the sum of nine
    prompts, most of which were the same file excerpt re-sent, and the operator had no way to see
    that from the number. Four distinct quantities, never added to each other:

    * **per-call provider input** — one row per call, measured;
    * **cumulative provider input** — the sum across the turn, and the only figure the provider bill
      corresponds to;
    * **assembled project context** — what the RUNTIME put into a prompt, estimated by the
      assembler's character approximation. Reported both as the largest single assembly (what one
      call was actually handed) and as the cumulative resend, because their ratio is the whole
      diagnosis of a call-explosion;
    * **system/tool/unclassified overhead** — cumulative measured input minus cumulative assembled
      context. Derived, therefore labelled derived, and never reported as a measurement.
    """
    rows = [dict(row or {}) for row in list(call_rows or ())]
    aggregate = dict(totals or {})

    per_call_input = [row.get("input_tokens") for row in rows]
    measured_inputs = [int(value) for value in per_call_input if isinstance(value, int)]
    assembled = [int(row.get("assembled_context_tokens_estimated") or 0) for row in rows]

    # The ROWS are the authority when there are any: one row exists per call that was actually
    # issued, so a call the budget refused cannot inflate the count. `totals` is the fallback for
    # callers that have usage blocks but no ledger.
    total_input: int | None = sum(measured_inputs) if measured_inputs else None
    if not rows:
        total_input = aggregate.get("input_tokens")
    cumulative_assembled = sum(assembled)
    overhead: int | None = None
    if isinstance(total_input, int) and cumulative_assembled:
        # Can go negative when a provider counts a shorter prompt than the assembler estimated, and
        # a negative "overhead" is nonsense to print. Reported as unknown rather than clamped to
        # zero, because zero would read as "there was none".
        difference = total_input - cumulative_assembled
        overhead = difference if difference >= 0 else None

    if rows:
        calls = len(rows)
        missing = sum(
            1
            for row in rows
            if not isinstance(row.get("input_tokens"), int)
            and not isinstance(row.get("output_tokens"), int)
        )
        complete = missing == 0
        output_total: Any = sum(
            int(row.get("output_tokens") or 0)
            for row in rows
            if isinstance(row.get("output_tokens"), int)
        )
        if not any(isinstance(row.get("output_tokens"), int) for row in rows):
            output_total = None
    else:
        calls = int(aggregate.get("calls") or 0)
        missing = int(aggregate.get("calls_missing_usage") or 0)
        complete = bool(aggregate.get("complete"))
        output_total = aggregate.get("output_tokens")
    return {
        "calls": calls,
        "cloud_calls": sum(1 for row in rows if row.get("cloud")),
        "largest_single_call_input": max(measured_inputs) if measured_inputs else None,
        "total_provider_input": total_input,
        "total_provider_output": output_total,
        "assembled_project_context_largest": max(assembled) if assembled else 0,
        "assembled_project_context_cumulative": cumulative_assembled,
        "assembled_context_source": "estimated by the prompt assembler at ~4 characters per token",
        "system_tool_overhead": overhead,
        "overhead_source": "derived: cumulative measured input minus cumulative assembled context",
        "calls_missing_usage": missing,
        "complete": complete,
        "lower_bound": not complete,
        "unusual": calls >= UNUSUAL_CALL_COUNT
        or (isinstance(total_input, int) and total_input >= UNUSUAL_INPUT_TOKENS),
    }


def audit_usage_sentence(breakdown: dict[str, Any]) -> str:
    """The one line a normal answer carries about what it cost.

    Everything else in the breakdown belongs in Activity. This sentence exists so a turn that cost
    nine calls cannot look, to the person reading it, exactly like a turn that cost two.
    """
    block = dict(breakdown or {})
    calls = int(block.get("calls") or 0)
    if not calls:
        return ""
    cloud = int(block.get("cloud_calls") or 0)
    label = "Cloud usage" if cloud else "Local model usage"
    total = block.get("total_provider_input")
    if not isinstance(total, int):
        return (
            f"{label}: {calls} model call(s); no provider reported token usage, so no input total "
            "is available."
        )
    if block.get("lower_bound"):
        # Rule 10: a sum over an incomplete set is a bound, and it says so in the words the operator
        # reads — with the missing-call count, because "at least" alone does not tell them how much
        # of the turn is unaccounted for.
        missing = int(block.get("calls_missing_usage") or 0)
        sentence = (
            f"{label}: {calls} model calls, at least {total:,} input tokens — a LOWER BOUND, not "
            f"the total: {missing} of {calls} call(s) reported no usage."
        )
    else:
        sentence = f"{label}: {calls} model calls, {total:,} input tokens."
    if block.get("unusual"):
        sentence += " This is unusually high for this task and is recorded for optimisation."
    return sentence


# --------------------------------------------------------------------------------------
# Is the operator asking?
# --------------------------------------------------------------------------------------

# `tokens?` on a word boundary so "tokenizer"/"tokenization" — a coding topic, not a usage
# question — does not match.
_TOKEN_SUBJECT = re.compile(r"\btokens?\b", re.IGNORECASE)
_CONTEXT_SUBJECT = re.compile(
    r"\bcontext[\s\-]+(?:window|components?|composition|breakdown|budget|usage|items?|size)\b",
    re.IGNORECASE,
)
_BARE_CONTEXT_SUBJECT = re.compile(r"\bcontext\b", re.IGNORECASE)
# "in the context of the Windows lane, how much work is left" is not a question about the prompt.
# The idiom is removed before the bare-`context` test rather than denying the whole message, so
# "in the context of that audit, how much context did you load" still resolves on its second use.
# The trailing `of` is what makes it the idiom: "in the context OF the Windows lane" is a framing
# device, while "what was in the context FOR that answer" is the literal prompt and must survive.
_CONTEXT_IDIOM = re.compile(
    r"\b(?:in|within|under|outside|given)\s+(?:the|this|that|its|our|their|such)\s+context\s+of\b"
    r"|\bin\s+(?:this|that)\s+context\b",
    re.IGNORECASE,
)

_USAGE_ASK = re.compile(
    r"\b(?:how\s+many|how\s+much|count|counts|usage|used|uses|use|spent|spend|consumed|"
    r"breakdown|broken\s+down|composition|report|budget|cost|costs|took|take|"
    r"what\s+went\s+into|what\s+was\s+in)\b",
    re.IGNORECASE,
)
# A bare "context" is only a usage question under one of these; otherwise "give me some context on
# X" and "in the context of Y" would both pull a token table onto an unrelated answer.
_STRONG_CONTEXT_ASK = re.compile(
    r"\b(?:how\s+many|how\s+much|breakdown|broken\s+down|composition|"
    r"what\s+went\s+into|what\s+was\s+in)\b",
    re.IGNORECASE,
)

# A request to BUILD something that counts tokens is not a request to be told this turn's count.
# Anchored to the opening imperative rather than searched anywhere, so "how many tokens did that
# build use?" still reads as a usage question.
_BUILD_OPENER = re.compile(
    r"^(?:(?:please|can|could|would)\s+you\s+|please\s+|let'?s\s+|i\s+want\s+(?:you\s+)?to\s+)?"
    r"(?:write|build|create|implement|generate|add|make|refactor|design|code|draft)\b",
    re.IGNORECASE,
)
# Asking what a token IS, or how tokenization works, is a topic question.
_DEFINITIONAL = re.compile(
    r"\b(?:what(?:'s|\s+is|\s+are)\s+(?:a\s+|an\s+|the\s+)?tokens?\b"
    r"|how\s+do(?:es)?\s+.{0,24}\btokeni[sz]"
    r"|explain\s+.{0,32}\btokens?\b)",
    re.IGNORECASE,
)


def token_usage_question(text: str) -> bool:
    """True when the operator is asking what this turn cost in tokens, or what was in its context.

    Deliberately a two-part test — a SUBJECT (tokens, or context-as-a-budget) and an ASK about
    consumption — because either half alone is ordinary English. "Tokens" appears in any wallet
    conversation on this product; "how much" appears everywhere.
    """
    body = str(text or "").strip()
    if not body:
        return False
    if _BUILD_OPENER.match(body) or _DEFINITIONAL.search(body):
        return False
    from core.turn_prohibitions import _strip_quoted_spans

    # The subject and consumption request must belong to the same clause. A numeric
    # pricing exercise elsewhere in a pasted document is not this turn's telemetry.
    for clause in re.split(r"[\n.!?;]+", _strip_quoted_spans(body)):
        if re.search(r"\b(?:estimated?|estimating|hypothetical|requiring|would)\b", clause, re.I):
            continue
        if not _USAGE_ASK.search(clause):
            continue
        if _TOKEN_SUBJECT.search(clause) or _CONTEXT_SUBJECT.search(clause):
            return True
        literal = _CONTEXT_IDIOM.sub(" ", clause)
        if _BARE_CONTEXT_SUBJECT.search(literal) and _STRONG_CONTEXT_ASK.search(clause):
            return True
    return False


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def _cell(value: Any) -> str:
    """A table cell that cannot break the table it sits in."""
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _lane_label(token_usage: dict[str, Any]) -> str:
    """Who reported the figure. `ModelProviderManifest.provider_id` is `name:model`, so naming the
    model again after it reads as two different things ("openrouter-byok:x:free (x:free)")."""
    provider = _cell(token_usage.get("provider_id")) or "the provider"
    model = _cell(token_usage.get("model_id"))
    if not model or model in provider:
        return provider
    return f"{provider} ({model})"


def _included_items(report: Any) -> list[dict[str, Any]]:
    items = list(getattr(report, "items_included", None) or [])
    return [item for item in items if isinstance(item, dict)]


def _component_rows(items: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for item in items:
        title = _cell(item.get("title")) or _cell(item.get("item_id")) or _cell(item.get("source_type")) or "(unnamed)"
        layer = _cell(item.get("layer")) or "—"
        try:
            tokens = max(0, int(item.get("tokens") or 0))
        except (TypeError, ValueError):
            tokens = 0
        rows.append((title, layer, tokens))
    rows.sort(key=lambda row: (-row[2], row[0]))
    return rows


def render_token_usage_receipt(*, token_usage: Any, report: Any) -> str:
    """The deterministic answer to "how many tokens, and what used them".

    Every figure carries its provenance in the row that prints it, and a figure the runtime does
    not have is printed as missing rather than estimated into existence.
    """
    usage = dict(token_usage or {}) if isinstance(token_usage, dict) else {}
    items = _included_items(report)
    rows = _component_rows(items)
    itemised_total = sum(tokens for _, _, tokens in rows)

    lines: list[str] = [TOKEN_USAGE_HEADING, ""]
    lines.append("| Figure | Tokens | Source |")
    lines.append("|---|---:|---|")

    lane = _lane_label(usage)
    for label, key in (("Model input (prompt)", "input_tokens"), ("Model output (completion)", "output_tokens")):
        value = usage.get(key)
        # `bool` is an `int` in Python, so a hand-built block carrying True would print "1" as a
        # measured count. `measured_call_usage` already refuses bools; this is the second gate,
        # because this function also renders dicts that did not come through it.
        if isinstance(value, int) and not isinstance(value, bool):
            lines.append(f"| {label} | {value:,} | measured — reported by {lane} |")
        else:
            lines.append(f"| {label} | not available | {lane} reported no usage for this call |")

    if rows:
        lines.append(
            f"| Assembled context | {itemised_total:,} | runtime estimate at assembly "
            "(~4 chars/token), not a provider figure |"
        )
    else:
        lines.append("| Assembled context | not available | the assembler itemised no components for this turn |")

    if not rows:
        lines.append("")
        lines.append(
            "No per-component breakdown exists for this turn — the context assembler recorded no "
            "included items, so there is nothing measured to attribute the input to."
        )
        return "\n".join(lines)

    lines.append("")
    lines.append(f"**Context components** — {len(rows)} item(s), {itemised_total:,} estimated tokens")
    lines.append("")
    lines.append("| Component | Layer | Tokens | % of context |")
    lines.append("|---|---|---:|---:|")

    def _share(tokens: int) -> str:
        return f"{(tokens / itemised_total * 100.0):.1f}%" if itemised_total > 0 else "—"

    for title, layer, tokens in rows[:_MAX_COMPONENT_ROWS]:
        lines.append(f"| {title} | {layer} | {tokens:,} | {_share(tokens)} |")

    tail = rows[_MAX_COMPONENT_ROWS:]
    if tail:
        tail_tokens = sum(tokens for _, _, tokens in tail)
        lines.append("")
        lines.append(
            f"…and {len(tail)} further component(s) totalling {tail_tokens:,} tokens "
            f"({_share(tail_tokens)}). Rows above are the largest by token count."
        )

    if isinstance(usage.get("input_tokens"), int):
        lines.append("")
        lines.append(
            "The component figures are the assembler's estimate; the measured input also carries "
            "the system prompt, the conversation messages and any tool schemas, which the "
            "assembler does not itemise. The two are not subtracted from each other here because "
            "they are not measured the same way."
        )
    return "\n".join(lines)


def token_usage_from_execution(model_execution: Any) -> dict[str, Any]:
    """The measured usage the router attached to this turn's decision, or an empty dict."""
    details = getattr(model_execution, "details", None)
    if not isinstance(details, dict):
        return {}
    usage = details.get("token_usage")
    return dict(usage) if isinstance(usage, dict) else {}


def append_token_usage_receipt(
    response_text: str,
    *,
    user_input: str,
    model_execution: Any,
    report: Any,
) -> str:
    """Append the measured receipt when — and only when — the operator asked for it.

    Idempotent, and a no-op for an empty reply. Never raises: a telemetry table is not allowed to
    be the reason an answer does not reach the operator.
    """
    text = str(response_text or "")
    if not text.strip():
        return response_text
    try:
        details = getattr(model_execution, "details", None)
        if isinstance(details, dict) and details.get("stepped_audit"):
            # Finding E point 9, 2026-08-04: this table was gated on `token_usage_question()`
            # alone, which fires on ordinary cost/context-brushing phrasing with no regard for
            # whether the SAME message also carried an audit finding -- so an audit turn whose
            # wording happened to brush "tokens"/"context" got the full multi-table breakdown
            # glued onto the same reply as the finding, degrading a clean report into
            # token-telemetry soup. An audit response already ends with its own compact one-line
            # `usage_line` footer (`audit_usage_sentence`, composed in `audit_verdict.py`) and a
            # pointer to Activity for full detail -- the multi-table breakdown is excluded from
            # this path entirely rather than made conditional on wording, since an audit turn's
            # own phrasing is not a reliable signal of what table the audit wants glued onto it.
            return response_text
        if not token_usage_question(user_input):
            return response_text
        if TOKEN_USAGE_HEADING in text:
            return response_text
        receipt = render_token_usage_receipt(
            token_usage=token_usage_from_execution(model_execution),
            report=report,
        )
    except Exception:  # pragma: no cover - defensive; the answer ships either way
        return response_text
    return f"{text.rstrip()}\n\n{receipt}"


__all__ = [
    "SOURCE_NOT_REPORTED",
    "SOURCE_PROVIDER_REPORTED",
    "TOKEN_USAGE_HEADING",
    "append_token_usage_receipt",
    "measured_call_usage",
    "render_token_usage_receipt",
    "token_usage_from_execution",
    "token_usage_question",
]
