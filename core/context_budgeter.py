from __future__ import annotations

from dataclasses import dataclass

from core.prompt_assembly_report import ContextItem, estimate_tokens

# No single item may take more than this share of a layer's token budget.
#
# must_keep items are never dropped and do not answer to the item-count gate, so without a
# ceiling one oversized item eats the layer by itself. Measured 2026-08-01 on an ordinary chat
# turn: `bootstrap-self-knowledge` is 586 tokens against a 180-token bootstrap layer -- 3.3x the
# whole budget -- and took all of it, so `bootstrap-task`, `bootstrap-session` and
# `bootstrap-continuity` all fell out at `over_budget` and the model ran with no task description.
DEFAULT_MAX_ITEM_SHARE = 0.5


@dataclass(frozen=True)
class ContextBudget:
    total_tokens: int = 900
    bootstrap_tokens: int = 180
    relevant_tokens: int = 520
    cold_tokens: int = 0
    max_bootstrap_items: int = 5
    max_relevant_items: int = 6
    max_cold_items: int = 2


@dataclass
class BudgetedLayer:
    included: list[ContextItem]
    excluded: list[tuple[ContextItem, str]]
    used_tokens: int
    used_chars: int


# What share of a model's usable prompt window the retrieved-context layers may occupy.
#
# The rest is for the system prompt, the conversation history, the user's own turn and the
# generation reserve, all of which the downstream fitter (core/prompt_budget) already bounds.
_CONTEXT_LAYER_SHARE = 0.25


def available_prompt_tokens(source_context: dict | None) -> int:
    """The prompt window the model answering this turn actually has, or 0 when unknown.

    Read from whatever the turn already carries rather than recomputed: several keys exist across
    the local and cloud lanes and any of them is authoritative. 0 means "do not scale", so a turn
    that cannot name its model keeps the declared budgets exactly.

    This is the reading half of the window contract; `scale_budget_to_window` is the spending
    half. Both the context loader (which consumes the window) and the router (which pre-resolves
    and stamps it before the loader runs) share this one key list, so neither can drift.
    """

    ctx = dict(source_context or {})
    for key in (
        "available_prompt_tokens",
        "model_context_window",
        "context_window",
        "num_ctx",
    ):
        try:
            value = int(ctx.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def scale_budget_to_window(budget: ContextBudget, *, available_prompt_tokens: int | None) -> ContextBudget:
    """Grow the layer budgets to fit the model actually answering this turn.

    The declared numbers are a FLOOR for a small local model, never a ceiling for a large one.
    Fixed constants were the bug: `bootstrap_budget` was 180 tokens whether the turn ran on a
    4k-context local model or a 200k-context paid one, so a caller who sent a long, considered
    request and expected the expensive model to do it justice got the same shredded context as the
    smallest model on the box -- and the safety rule was trimmed mid-sentence to make room.

    Nothing here ever shrinks a budget: an unknown or tiny window leaves the declared values
    exactly as they are, so this cannot make any existing turn worse.
    """

    available = int(available_prompt_tokens or 0)
    if available <= 0:
        return budget

    declared_total = max(1, int(budget.total_tokens))
    room = int(available * _CONTEXT_LAYER_SHARE)
    if room <= declared_total:
        return budget

    # Grow every layer in the proportion the strategy already declared, so a task class that wants
    # mostly-retrieved context still gets mostly-retrieved context.
    factor = room / declared_total
    return ContextBudget(
        total_tokens=room,
        bootstrap_tokens=int(budget.bootstrap_tokens * factor),
        relevant_tokens=int(budget.relevant_tokens * factor),
        cold_tokens=int(budget.cold_tokens * factor),
        max_bootstrap_items=budget.max_bootstrap_items,
        max_relevant_items=budget.max_relevant_items,
        max_cold_items=budget.max_cold_items,
    )


def normalize_budget(budget: ContextBudget) -> ContextBudget:
    bootstrap = max(0, int(budget.bootstrap_tokens))
    total = max(bootstrap, int(budget.total_tokens))
    remaining = max(0, total - bootstrap)
    relevant = min(max(0, int(budget.relevant_tokens)), remaining)
    cold = min(max(0, int(budget.cold_tokens)), max(0, remaining - relevant))
    return ContextBudget(
        total_tokens=total,
        bootstrap_tokens=bootstrap,
        relevant_tokens=relevant,
        cold_tokens=cold,
        max_bootstrap_items=max(1, int(budget.max_bootstrap_items)),
        max_relevant_items=max(1, int(budget.max_relevant_items)),
        max_cold_items=max(0, int(budget.max_cold_items)),
    )


def _with_content(item: ContextItem, content: str) -> ContextItem:
    return ContextItem(
        item_id=item.item_id,
        layer=item.layer,
        source_type=item.source_type,
        title=item.title,
        content=content,
        priority=item.priority,
        confidence=item.confidence,
        must_keep=item.must_keep,
        include_reason=item.include_reason,
        metadata=dict(item.metadata),
        provenance=dict(item.provenance),
    )


def _truncate_to_tokens(item: ContextItem, token_budget: int) -> ContextItem | None:
    if token_budget <= 0:
        return None
    approx_chars = max(32, token_budget * 4)
    content = (item.content or "").strip()
    if len(content) <= approx_chars:
        return item
    trimmed = content[: max(24, approx_chars - 3)].rstrip() + "..."
    return _with_content(item, trimmed)


def _fit_to_tokens(item: ContextItem, token_allowance: int) -> ContextItem:
    """Shrink `item` so it costs at most `token_allowance`, never dropping it.

    Unlike `_truncate_to_tokens` this has no lower floor on the trimmed length, so the result is
    guaranteed to fit. `estimate_tokens` is `ceil(len / 4)`, so `4 * allowance` characters is the
    exact budget in characters.
    """

    allowance = max(1, int(token_allowance))
    content = (item.content or "").strip()
    if estimate_tokens(content) <= allowance:
        return item if content == (item.content or "") else _with_content(item, content)

    char_limit = allowance * 4
    ellipsis = "..."
    head = content[: max(1, char_limit - len(ellipsis))].rstrip()
    return _with_content(item, (head + ellipsis)[:char_limit])


def _fair_shares(sizes: list[int], budget: int, ceiling: int) -> list[int]:
    """Max-min fair split of `budget` across `sizes`, no share above `ceiling`.

    Allocating smallest-first means an item that needs less than an even share takes only what it
    needs and rolls the surplus forward to the larger ones. Cheap items are therefore protected
    from expensive ones no matter how the caller ranked them: a 23-token safety block cannot be
    starved by a 586-token persona essay sitting above it.
    """

    shares = [0] * len(sizes)
    remaining = max(0, budget)
    claimants = len(sizes)
    for index in sorted(range(len(sizes)), key=lambda i: sizes[i]):
        even_split = remaining // claimants if claimants else 0
        grant = max(0, min(sizes[index], ceiling, even_split))
        shares[index] = grant
        remaining -= grant
        claimants -= 1
    return shares


def budget_layer(
    items: list[ContextItem],
    *,
    token_budget: int,
    max_items: int,
    max_item_share: float = DEFAULT_MAX_ITEM_SHARE,
) -> BudgetedLayer:
    """Resolve one context layer against its token and item budgets.

    `must_keep` is a guarantee, not a sort key. Items carrying it are admitted before anything
    else, are exempt from `max_items`, and are trimmed rather than dropped when the layer cannot
    hold them at full size. `max_items` therefore bounds the optional tail only -- the layer is
    sized by its token budget, and the mandatory content is what the token budget exists for.

    The one thing that still drops a `must_keep` item is a layer that is switched off entirely
    (`token_budget <= 0`, as the cold layer is by default). A disabled layer stays disabled.
    """

    token_budget = max(0, int(token_budget))
    ranked = sorted(items, key=lambda item: (item.must_keep, item.priority, item.confidence), reverse=True)
    included: list[ContextItem] = []
    excluded: list[tuple[ContextItem, str]] = []
    used_tokens = 0
    used_chars = 0

    if token_budget <= 0:
        return BudgetedLayer(
            included=[],
            excluded=[(item, "budget_exhausted") for item in ranked],
            used_tokens=0,
            used_chars=0,
        )

    required = [item for item in ranked if item.must_keep]
    optional = [item for item in ranked if not item.must_keep]

    ceiling = max(1, int(token_budget * max(0.0, min(1.0, float(max_item_share)))))
    allowances = _fair_shares([item.token_count for item in required], token_budget, ceiling)

    # strict=True on purpose: _fair_shares is fed one token_count per required item, so a length
    # mismatch is impossible unless that contract breaks -- in which case silently dropping the tail
    # would under-fund a mandatory item and be invisible. Fail loudly instead.
    for item, allowance in zip(required, allowances, strict=True):
        if allowance <= 0:
            # There are more mandatory items than the layer has tokens, so this one cannot be
            # funded at even a single token. Emitting it anyway would put a titled, empty section
            # into the prompt, so it is reported as a drop instead -- loud rather than silent.
            excluded.append((item, "budget_exhausted"))
            continue

        if item.token_count <= allowance:
            included.append(item)
            used_tokens += item.token_count
            used_chars += item.char_count
            continue
        # Reported as `trimmed_to_fit` so the shortening shows up as a trimming decision in the
        # prompt assembly report rather than reading as a silent drop.
        trimmed = _fit_to_tokens(item, allowance)
        included.append(trimmed)
        used_tokens += trimmed.token_count
        used_chars += trimmed.char_count
        excluded.append((item, "trimmed_to_fit"))

    optional_included = 0
    for item in optional:
        if optional_included >= max_items:
            excluded.append((item, "max_items_exceeded"))
            continue

        remaining = max(0, token_budget - used_tokens)
        item_tokens = item.token_count
        if item_tokens <= remaining:
            included.append(item)
            optional_included += 1
            used_tokens += item_tokens
            used_chars += item.char_count
            continue

        if remaining <= 0:
            excluded.append((item, "budget_exhausted"))
            continue

        trimmed = _truncate_to_tokens(item, remaining)
        if trimmed is None or trimmed.token_count > remaining:
            excluded.append((item, "over_budget"))
            continue

        included.append(trimmed)
        optional_included += 1
        used_tokens += trimmed.token_count
        used_chars += trimmed.char_count
        excluded.append((item, "trimmed_to_fit"))

    return BudgetedLayer(
        included=included,
        excluded=excluded,
        used_tokens=used_tokens,
        used_chars=used_chars,
    )
