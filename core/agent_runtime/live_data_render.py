"""Deterministic Markets/Weather tables + derived comparisons from a LiveDataPlan's outcomes.

Product code owns the verdict, not an LLM synthesis pass: every number in the two derived
comparison lines is read directly from a `SubtaskOutcome.result` dict, never asked of a model and
never reconstructed from prose. A missing or failed subtask is marked unavailable in its own table
row -- it is never silently dropped, and it never blocks the comparisons that CAN still be computed
from the subtasks that did succeed.
"""

from __future__ import annotations

import re

from core.agent_runtime.live_data_plan import LiveDataPlan, SubtaskLifecycle, SubtaskOutcome

# ---------------------------------------------------------------------------------------------
# Coverage note: a typed plan that cannot serve a requested live intent must SAY so.
#
# Found live (operator transcript, 2026-08-15 12:38): a five-part turn (weather x2 + which is
# warmer, flight prices Warsaw->London, BTC+BNB + which gained most THIS WEEK, hotel deals in
# London) was claimed whole by the typed plan, which planned only weather + market subtasks and
# rendered only the Markets/Weather tables. Flights and hotels were never mentioned -- no tool
# exists for them -- and "gained most this week" was silently answered with the 24-hour change
# field, a different question. The turn completed as if fully answered.
#
# The invariant: the rendered answer must name every requested-but-unservable live intent and
# why it was not served (no tool registered / the source provides 24h change only). Detection is
# deliberately conservative -- it requires the explicit noun families below, and for the bare
# nouns ("flight", "hotel") a commerce word within a short window, because the over-fire
# direction ("I could not do X" on a turn that never asked X) is the worse failure.
# ---------------------------------------------------------------------------------------------

# Self-sufficient flight-commerce phrases: the noun itself already names a purchase.
_FLIGHT_SELF_RE = re.compile(r"\b(?:airfares?|air\s+fares?|plane\s+tickets?|flight\s+tickets?)\b", re.IGNORECASE)
# Bare nouns that need commerce context nearby ("my flight lands at 5" asks nothing purchasable).
_FLIGHT_BARE_RE = re.compile(r"\bflights?\b", re.IGNORECASE)
_HOTEL_SELF_RE = re.compile(r"\b(?:room\s+nights?|accommodation\s+deals?|hotel\s+deals?)\b", re.IGNORECASE)
_HOTEL_BARE_RE = re.compile(r"\b(?:hotels?|accommodations?)\b", re.IGNORECASE)
_COMMERCE_RE = re.compile(
    r"\b(?:prices?|pricing|deals?|fares?|costs?|rates?|cheap(?:est|er)?|book(?:ing|ings)?|tickets?|offers?|discounts?)\b",
    re.IGNORECASE,
)
# A weekly timeframe the market source cannot serve (it provides 24h change only). Requires the
# week phrase AND change/gain vocabulary near it, so "next week's weather" alone never trips it.
_WEEK_RE = re.compile(r"\b(?:(?:this|past|last)\s+week|weekly|(?:7|seven)[-\s]days?|week)\b", re.IGNORECASE)
_CHANGE_RE = re.compile(
    r"\b(?:gain(?:ed|s|ers)?|chang(?:e|ed|es)|mov(?:ed|er|ers)?|perform(?:ed|ance|ing)?|ris(?:e|en)|rose|"
    r"drop(?:ped|s)?|fell|fall(?:en)?|winners?|losers?|grew|growth)\b",
    re.IGNORECASE,
)

_NEAR_WINDOW = 48


def _near(anchor: re.Pattern[str], context: re.Pattern[str], text: str, window: int = _NEAR_WINDOW) -> bool:
    """Whether any `anchor` match has a `context` match within `window` chars on either side."""
    for match in anchor.finditer(text):
        lo = max(0, match.start() - window)
        hi = min(len(text), match.end() + window)
        if context.search(text[lo:hi]):
            return True
    return False


def coverage_note(plan: LiveDataPlan | None) -> str:
    """The unserved-intent note for this plan's original request, or "" when everything asked
    for is within what the plan's operations can serve.

    Reads only `plan.original_request` and the plan's own subtask operations -- never outcomes --
    because the gap this closes is between what was ASKED and what was ever PLANNED, not between
    planned and fetched (failed fetches already render as unavailable rows).
    """
    if plan is None:
        return ""
    text = plan.original_request
    gaps: list[str] = []
    if _FLIGHT_SELF_RE.search(text) or _near(_FLIGHT_BARE_RE, _COMMERCE_RE, text):
        gaps.append("flight prices -- no flight-search tool is available in this runtime yet")
    if _HOTEL_SELF_RE.search(text) or _near(_HOTEL_BARE_RE, _COMMERCE_RE, text):
        gaps.append("hotel deals -- no hotel-search tool is available in this runtime yet")
    has_market_quote = any(task.operation == "market_quote" for task in plan.subtasks)
    if has_market_quote and _near(_WEEK_RE, _CHANGE_RE, text, window=64):
        gaps.append(
            "weekly (7-day) change -- the market source provides 24-hour change only, "
            "so the change figures above are 24h, not weekly"
        )
    if not gaps:
        return ""
    return "**Not covered in this answer**: " + "; ".join(gaps) + "."

_MARKET_COLUMNS = ("Asset", "Price", "24h Change", "Source", "Retrieved At")
# Condition, current temperature, today's high, today's low, source timestamp -- the exact weather
# schema a multi-city request asks for, never silently downgraded to a narrower shape. Each field
# is marked unavailable independently in `_weather_row` (e.g. a source that returns a current
# reading but no daily forecast still reports its condition/temperature; only High/Low go
# unavailable), not as a single all-or-nothing row failure.
_WEATHER_COLUMNS = ("City", "Condition", "Current Temp (C)", "Today's High (C)", "Today's Low (C)", "Source", "Source Timestamp")


def render_live_data_plan_unavailable(user_text: str, allowed_toolsets: tuple[str, ...]) -> str:
    """Checkpoint 6.5 fail-closed path: `requirements_for()` classified this turn LIVE_DATA but
    `build_live_data_plan()` returned no typed plan at all -- a discrepancy between the classifier
    and the plan's own entity extraction (found live: "Market data only for Bitcoin and gold."
    classified LIVE_DATA but built zero subtasks, and the turn fell through to the model, which
    fabricated prices). This is the deterministic substitute for that fallthrough: it never invents
    a number, states plainly that live data could not be retrieved, and names whatever entities a
    second, independent extraction pass can still find in the raw text -- best effort, not a
    requirement, since the fact that no plan could be built at all means that second pass may also
    come up empty.
    """
    parts: list[str] = []
    if "market_prices" in allowed_toolsets:
        from core.agent_runtime.fast_live_info_price import price_assets_named
        from tools.web.web_research import _extract_market_entity_candidates

        assets = price_assets_named(user_text) or _extract_market_entity_candidates(user_text)
        if assets:
            names = ", ".join(a.title() for a in assets)
            parts.append(f"Live market data for {names} could not be retrieved right now.")
        else:
            parts.append("The requested live market data could not be retrieved right now.")
    if "weather" in allowed_toolsets:
        from tools.web.web_research import _extract_weather_locations

        locations = _extract_weather_locations(user_text)
        if locations:
            names = ", ".join(loc.title() for loc in locations)
            parts.append(f"Live weather for {names} could not be retrieved right now.")
        else:
            parts.append("The requested live weather data could not be retrieved right now.")
    if not parts:
        parts.append("This request needs live data that could not be retrieved right now.")
    parts.append("No price or forecast figures were generated for this reply.")
    return " ".join(parts)


def _market_row(outcome: SubtaskOutcome) -> dict[str, str]:
    entity = outcome.subtask.entity
    if not outcome.ok or outcome.result is None:
        return {"Asset": entity, "Price": "unavailable", "24h Change": "-", "Source": outcome.failure_reason or "unavailable", "Retrieved At": "-"}
    result = outcome.result
    price = result.get("price")
    currency = str(result.get("currency") or "USD")
    unit = str(result.get("unit_label") or "")
    price_text = f"{currency} {price:,.2f}" if isinstance(price, (int, float)) else "unavailable"
    if unit:
        price_text += f" {unit}"
    change = result.get("change_24h_pct")
    change_text = f"{change:+.2f}%" if isinstance(change, (int, float)) else "-"
    source = str(result.get("source") or "")
    url = str(result.get("source_url") or "")
    source_text = f"[{source}]({url})" if source and url else source
    retrieved_at = str(result.get("retrieved_at") or "-")
    return {"Asset": entity, "Price": price_text, "24h Change": change_text, "Source": source_text, "Retrieved At": retrieved_at}


def _weather_row(outcome: SubtaskOutcome) -> dict[str, str]:
    entity = outcome.subtask.entity
    if not outcome.ok or outcome.result is None:
        # The whole lane failed (or was never fetched, e.g. no matching outcome recorded) --
        # every field in this row is unavailable, not just the ones a source sometimes omits.
        return {
            "City": entity,
            "Condition": "unavailable",
            "Current Temp (C)": "unavailable",
            "Today's High (C)": "unavailable",
            "Today's Low (C)": "unavailable",
            "Source": outcome.failure_reason or "unavailable",
            "Source Timestamp": "unavailable",
        }
    result = outcome.result
    condition = str(result.get("condition") or "") or "unavailable"
    temp = result.get("temperature_c")
    temp_text = f"{temp:.0f}" if isinstance(temp, (int, float)) else "unavailable"
    high = result.get("high_c")
    high_text = f"{high:.0f}" if isinstance(high, (int, float)) else "unavailable"
    low = result.get("low_c")
    low_text = f"{low:.0f}" if isinstance(low, (int, float)) else "unavailable"
    source = str(result.get("source") or "")
    url = str(result.get("source_url") or "")
    source_text = f"[{source}]({url})" if source and url else (source or "unavailable")
    observed_at = str(result.get("observed_at") or "") or "unavailable"
    return {
        "City": entity,
        "Condition": condition,
        "Current Temp (C)": temp_text,
        "Today's High (C)": high_text,
        "Today's Low (C)": low_text,
        "Source": source_text,
        "Source Timestamp": observed_at,
    }


def _reconcile_to_plan(plan: LiveDataPlan | None, outcomes: list[SubtaskOutcome]) -> list[SubtaskOutcome]:
    """`outcomes`, filtered and completed against `plan.subtasks` -- the explicit, defensive form
    of "rendered entity IDs are a subset of planned entity IDs, and every planned entity produces
    exactly one row" (rather than relying on callers upstream, e.g. `run_live_data_plan`, to have
    already gotten that 1:1 correspondence right on their own).

    Two directions, both enforced here rather than assumed: an outcome whose `subtask_id` is not
    one this PLAN produced is dropped (a corrupted or stale outcome list can never inject an
    unplanned row -- this is what would have caught "Warmest current city: - Source (27 C)" even
    if the extraction bug that created that subtask in the first place had slipped through), and a
    planned subtask with no matching outcome at all gets a synthesized "no outcome recorded" one
    instead of silently vanishing from its table.

    `plan=None` is a no-op (returns `outcomes` untouched): callers that hand-build outcomes with no
    real plan behind them (this module's own render-logic unit tests) have no ground truth to
    reconcile against, so there is nothing to defend here -- reconciliation only ever narrows what
    a REAL plan already vouches for, it is never the thing that decides content on its own.
    """
    if plan is None:
        return outcomes
    by_id = {outcome.subtask.subtask_id: outcome for outcome in outcomes}
    reconciled: list[SubtaskOutcome] = []
    for subtask in plan.subtasks:
        outcome = by_id.get(subtask.subtask_id)
        if outcome is None:
            outcome = SubtaskOutcome(
                subtask=subtask, state=SubtaskLifecycle.FAILED, failure_reason="no outcome recorded"
            )
        reconciled.append(outcome)
    return reconciled


def _render_table(columns: tuple[str, ...], rows: list[dict[str, str]]) -> str:
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(row.get(col, "") or "-" for col in columns) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def largest_absolute_mover(outcomes: list[SubtaskOutcome], plan: LiveDataPlan | None = None) -> tuple[str, float] | None:
    """The market subtask with the largest |24h change|, or None if no subtask has one.

    Computed directly from each outcome's own `change_24h_pct` -- never from rendered table text,
    and never guessed when the field is missing (a subtask that failed or never returned a change
    percentage is excluded, not treated as zero). When `plan` is given, a candidate must also
    belong to `plan.market_subtasks()` -- the explicit form of "the derived market entity MUST
    belong to validated planned market entity ids", not merely whatever happens to be in
    `outcomes`. `plan` is optional (rather than required) only so existing direct-outcome-list
    tests that predate the plan wiring keep working unchanged.
    """
    if plan is not None:
        planned_ids = {task.subtask_id for task in plan.market_subtasks()}
        outcomes = [o for o in outcomes if o.subtask.subtask_id in planned_ids]
    candidates = [
        (outcome.subtask.entity, outcome.result["change_24h_pct"],
         str(outcome.result.get("change_window") or "24h").strip() or "24h")
        for outcome in outcomes
        if outcome.subtask.operation == "market_quote"
        and outcome.ok
        and outcome.result is not None
        and isinstance(outcome.result.get("change_24h_pct"), (int, float))
    ]
    if not candidates:
        return None
    # Only like windows are comparable: a commodity's session change must not
    # compete against an exchange's trailing 24h under a "24-hour" label.
    # Mixed windows -> the comparison is not offered rather than mislabeled
    # (a derived line nobody asked for, built on unlike windows, is worse than
    # no line).
    windows = {window for _, _, window in candidates}
    if len(windows) > 1:
        return None
    entity, change = max(((e, c) for e, c, _ in candidates), key=lambda item: abs(item[1]))
    return entity, change


def _single_change_window(outcomes: list[SubtaskOutcome], plan: LiveDataPlan | None) -> str:
    """The one change window every quoted candidate shares, or "" when mixed/absent."""
    if plan is not None:
        planned_ids = {task.subtask_id for task in plan.market_subtasks()}
        outcomes = [o for o in outcomes if o.subtask.subtask_id in planned_ids]
    windows = {
        (str(o.result.get("change_window") or "24h").strip() or "24h")
        for o in outcomes
        if o.subtask.operation == "market_quote"
        and o.ok
        and o.result is not None
        and isinstance(o.result.get("change_24h_pct"), (int, float))
    }
    return windows.pop() if len(windows) == 1 else ""


def warmest_city(outcomes: list[SubtaskOutcome], plan: LiveDataPlan | None = None) -> tuple[str, float] | None:
    """The weather subtask with the highest temperature, or None if none has one.

    Same explicit planned-membership guard as `largest_absolute_mover` -- the incident this fixes
    ("Warmest current city: - Source (27 C)") was a bogus subtask that reached the OUTCOME list at
    all, not a bug in this function's own max-picking logic; this is the second, independent layer
    that would still have caught it even if a future extraction regression let one through again.
    """
    if plan is not None:
        planned_ids = {task.subtask_id for task in plan.weather_subtasks()}
        outcomes = [o for o in outcomes if o.subtask.subtask_id in planned_ids]
    candidates = [
        (outcome.subtask.entity, outcome.result["temperature_c"])
        for outcome in outcomes
        if outcome.subtask.operation == "weather_lookup"
        and outcome.ok
        and outcome.result is not None
        and isinstance(outcome.result.get("temperature_c"), (int, float))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])



def _water_blocks(blocks: list, water_outcomes: list) -> None:
    """One row per water ask, from structured data only, and NEVER silence.

    A failed water observation renders its honest unavailable row here, exactly as weather's
    single-line does -- an empty answer block falls through to the ordinary-chat guard, which
    convicts and serves a canned "try again".

    Extracted so the row law can be executed by a test. C4 -- "the Baltic water temperature is
    caveated, not one universal number" -- is graded on these bytes, and until this lived in its
    own function nothing could reach it without a whole plan and a full outcome set.
    """
    for water in water_outcomes:
        result = water.result or {}
        label = str(result.get("label") or water.subtask.arguments.get("place") or "").strip()
        if not water.ok or result.get("temperature_c") is None:
            reason = str(water.failure_reason or "no observation returned").strip()
            blocks.append(f"Water temperature at {label}: unavailable ({reason})")
            continue
        celsius = result.get("temperature_c")
        fahrenheit = result.get("temperature_f")
        source = str(result.get("source") or "").strip()
        line = f"Water temperature at {label}: {celsius}\u00b0C"
        if fahrenheit is not None:
            line += f" ({fahrenheit}\u00b0F)"
        if source:
            line += f" (source: {source})"
        blocks.append(line)

def render_live_data_answer(plan: LiveDataPlan | None, outcomes: list[SubtaskOutcome]) -> str:
    """The deterministic answer: Markets/Weather, shaped by how many results each side actually has.

    Renders from structured outcome data only -- this never asks a model to reconstruct results
    from prose, and it is the renderer used when model synthesis is empty or fails, not only as a
    fallback: for a LIVE_DATA turn, structured rendering IS the answer.

    Output shape follows the request rather than always forcing the benchmark's own tables: a
    SINGLE market or weather result renders as one sentence (matching how a single-asset price
    query already read before the multi-entity plan existed), with no derived-comparison line --
    comparing one thing to itself is not a comparison. A table, and its comparison line, appear
    only once there are two or more results to compare. Found live: "Weather in Vilnius,
    Lithuania." rendered a one-row table plus "Warmest current city: Vilnius (29 C)" -- technically
    correct, but exactly the "forcing the benchmark tables" shape onto a request that named only
    one place.
    """
    # Reconciled against the plan FIRST: an outcome whose subtask_id the plan never produced is
    # dropped (rendered entity IDs are always a subset of planned entity IDs), and a planned
    # subtask with no matching outcome still gets its row (every planned entity renders exactly
    # once) -- see `_reconcile_to_plan`.
    outcomes = _reconcile_to_plan(plan, outcomes)

    # Unsupported entities render alongside their resolved siblings, marked unavailable -- never a
    # silent gap between "what the plan has" and "what the answer shows".
    market_outcomes = [o for o in outcomes if o.subtask.operation in ("market_quote", "unsupported_market_entity")]
    weather_outcomes = [o for o in outcomes if o.subtask.operation == "weather_lookup"]

    blocks: list[str] = []
    if len(market_outcomes) > 1:
        blocks.append("**Markets**\n\n" + _render_table(_MARKET_COLUMNS, [_market_row(o) for o in market_outcomes]))
    elif len(market_outcomes) == 1:
        blocks.append(_single_market_line(market_outcomes[0]))

    if len(weather_outcomes) > 1:
        blocks.append("**Weather**\n\n" + _render_table(_WEATHER_COLUMNS, [_weather_row(o) for o in weather_outcomes]))
    elif len(weather_outcomes) == 1:
        blocks.append(_single_weather_line(weather_outcomes[0]))

    _water_blocks(blocks, [o for o in outcomes if o.subtask.operation == "water_temperature"])

    # A derived comparison answers a comparison the operator actually made. `market_outcomes`
    # deliberately includes `unsupported_market_entity` rows so nothing requested is silently
    # dropped -- but counting those toward the comparison meant ONE requested asset beside one
    # unresolvable mention produced "Largest absolute 24-hour mover: Gold", a ranking of one, for
    # a turn that never compared anything (measured live 2026-08-29 on "price of gold nwo", where
    # the typo "nwo" became a phantom second entity). The obligation comes from the count of
    # genuinely REQUESTED quotes; whether they resolved decides the value, not the line -- four
    # requested assets that all fail still owe the operator "unavailable", never silence.
    quoted_market_outcomes = [
        outcome for outcome in market_outcomes if outcome.subtask.operation == "market_quote"
    ]
    comparison_lines = []
    if len(quoted_market_outcomes) > 1:
        mover = largest_absolute_mover(outcomes, plan)
        # Label the window the comparison was actually measured over: a session
        # change (Yahoo commodities) presented as a "24-hour" mover was a false
        # label, and a mixed-window comparison is declined inside
        # `largest_absolute_mover` rather than mislabeled here.
        window = _single_change_window(outcomes, plan)
        label = (
            "Largest absolute 24-hour mover"
            if window in ("", "24h")
            else f"Largest absolute mover ({window} window)"
        )
        comparison_lines.append(
            f"{label}: {mover[0]} ({mover[1]:+.2f}%)" if mover else f"{label}: unavailable"
        )
    if len(weather_outcomes) > 1:
        warmest = warmest_city(outcomes, plan)
        comparison_lines.append(
            f"Warmest current city: {warmest[0]} ({warmest[1]:.0f} C)" if warmest else "Warmest current city: unavailable"
        )
    if comparison_lines:
        blocks.append("\n".join(comparison_lines))

    # A requested-but-unservable intent (flight prices, hotel deals, a weekly timeframe the
    # market source cannot serve) is named at the end of the answer, never silently dropped.
    note = coverage_note(plan)
    if note:
        blocks.append(note)

    return "\n\n".join(blocks)


def _single_market_line(outcome: SubtaskOutcome) -> str:
    row = _market_row(outcome)
    if not outcome.ok:
        return f"{row['Asset']}: unavailable ({row['Source']})"
    line = f"{row['Asset']}: {row['Price']}"
    if row.get("24h Change") and row["24h Change"] != "-":
        line += f" (24h change: {row['24h Change']})"
    if row.get("Source"):
        line += f". Source: {row['Source']}"
    if row.get("Retrieved At") and row["Retrieved At"] != "-":
        line += f", retrieved {row['Retrieved At']}"
    return line + "."


def _single_weather_line(outcome: SubtaskOutcome) -> str:
    row = _weather_row(outcome)
    if not outcome.ok:
        return f"{row['City']}: unavailable ({row['Source']})"
    line = f"{row['City']}: {row['Condition']}"
    if row.get("Current Temp (C)") and row["Current Temp (C)"] != "unavailable":
        line += f", {row['Current Temp (C)']} C"
    high = row.get("Today's High (C)")
    low = row.get("Today's Low (C)")
    if high and high != "unavailable" and low and low != "unavailable":
        line += f" (today's high {high} C / low {low} C)"
    if row.get("Source"):
        line += f". Source: {row['Source']}"
    if row.get("Source Timestamp") and row["Source Timestamp"] != "unavailable":
        line += f", observed {row['Source Timestamp']}"
    return line + "."
