"""The currency fast path: stable definitions answered locally, everything priced answered from evidence.

QA-050-026 built this door for definitions and conversions. QA-050-027 widened it, because the
turn that produced the reported defect was neither:

    "Compare 500 kr, $500, and ¥500 in terms of value and purchasing power. Which is worth most
     today, and which would go furthest locally? If inflation is 5%, which is best to hold?"

`core/currency_intent.py` decides WHAT a message asks for, `core/currency_comparison.py` and
`core/entity_consistency.py` decide what a comparison establishes and what it breaks, and this
module is the only place that turns any of those readings into a reply. `turn_frontdoor` is the
only place that calls it. Keeping recognition, rendering and dispatch apart is what lets the ISO
table grow, a city be added, or a real FX feed arrive without any of them reaching into another.

Why a fast path at all
-----------------------
A currency code's meaning is a fact this runtime holds, in the same class as the clock: TRY is the
Turkish lira today, tomorrow, and in a year. Sending "what is TRY?" to a model bought a provider
round trip to be told the model wasn't sure what TRY meant, and sending "i ment money TRY" bought
one to be told VOOL does not handle money transactions. Both are answered here from reference data,
locally, with no model call and no paid arm.

Why the PRICED turns go through here too, rather than being left to the model
------------------------------------------------------------------------------
This is the part that matters, and both measured defects say the same thing. "1000 TRY to USD?"
reached the model with no grounding requirement attached and came back with ~$590-610 — a rate the
model remembered from training. "Compare 500 kr, $500, and ¥500" reached it the same way and came
back with `kr = NOK`, `¥ = JPY`, and a ranking built on both. Leaving the turn to the model is
precisely what produced the invented numbers, so these turns are claimed here and answered with
what is actually known: the pair or the table, the ambiguity where there is one, and the fact that
the rate — or the price level — is missing. Declining to state a number is the ANSWER.

`live_rate`, `live_rates` and `live_ppp` thread through as arguments all the way down. When a feed
is wired up, it is passed in here and the same renderers do the arithmetic with attribution — no
branch in this file changes, and nothing in the recognition modules changes at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from core.currency_comparison import (
    CurrencyComparisonRequest,
    comparison_followup,
    currency_comparison_intent,
    currency_grounding_facts,
    invalidations,
    render_currency_comparison,
    render_currency_grounding_note,
    uncovered_residue,
)
from core.currency_intent import (
    currency_definition_intent,
    currency_transaction_intent,
    fx_conversion_intent,
    fx_rate_lookup_intent,
    homograph_phrase_intent,
    render_currency_definition,
    render_fx_conversion,
    render_fx_rate_lookup,
    render_homograph_phrase,
)
from core.currency_travel_spend import render_travel_spend, travel_spend_intent
from core.entity_consistency import (
    contradiction_observation,
    entity_consistency_report,
    render_entity_contradictions,
    uncovered_by_contradictions,
)


def _user_messages(conversation_history: Sequence[Any] | None) -> list[str]:
    """The user's own turns, oldest first. A model's reply is not evidence about what was asked."""

    out: list[str] = []
    for message in list(conversation_history or []):
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role and role not in {"user", "human"}:
            continue
        content = str(message.get("content") or "").strip()
        if content:
            out.append(content)
    return out


def prior_comparison(
    conversation_history: Sequence[Any] | None,
) -> CurrencyComparisonRequest | None:
    """The most recent comparison this chat asked for, rebuilt from the user's own turns.

    Rebuilt rather than remembered on purpose. `currency_comparison_intent` is a pure function of
    the text, so replaying it gives exactly the reading the earlier answer was rendered from —
    there is no second copy of the state to drift out of step with the first.
    """

    for text in reversed(_user_messages(conversation_history)):
        request = currency_comparison_intent(text)
        if request is not None:
            return request
    return None


def currency_fast_path(
    user_input: str,
    *,
    chat_codes: Sequence[str] = (),
    live_rate: Decimal | None = None,
    rate_asof: str = "",
    conversation_history: Sequence[Any] | None = None,
    live_rates: Mapping[tuple[str, str], Decimal] | None = None,
    live_ppp: Mapping[tuple[str, str], Decimal] | None = None,
    rates_asof: str = "",
    ppp_asof: str = "",
) -> dict[str, Any] | None:
    """The reply for a currency turn, or None when this turn is not one.

    Returns a small envelope rather than a bare string so the caller can log WHICH reading claimed
    the turn without re-deriving it — `kind` is `definition`, `ambiguity`, `conversion`,
    `rate_lookup`, `comparison`, `comparison_followup` or `entity_contradiction`, and `grounded`
    says whether a number was produced and on what basis. `codes` is what the turn resolved, so the
    caller never has to re-parse the reply to know.

    `chat_codes` threads in the codes this chat has already settled (`core.currency_chat_context`).
    It is an argument rather than a lookup for the same reason `live_rate` is: this lane must stay
    answerable with no session at all, and every default here is "no context", so an empty chat
    behaves exactly as it did before any of this existed.
    """

    text = str(user_input or "")
    if not text.strip():
        return None

    # A hypothetical purchase is normally (and correctly) caught by the transaction guard below.
    # The narrow exception is a fully resolved calculation whose places, amounts and exchange
    # equations are all present in the user's own turn.  It performs no transaction and retrieves
    # no rate; it merely evaluates the supplied arithmetic with Decimal.
    travel_spend = travel_spend_intent(text)
    if travel_spend is not None:
        travel_grounding = (
            "no_rate_declined"
            if bool(getattr(travel_spend, "missing_rate", False))
            else "user_supplied_rate_or_local_identity"
        )
        return {
            "kind": "travel_spend",
            "response": render_travel_spend(travel_spend),
            "code": "",
            "codes": tuple(
                dict.fromkeys(
                    code
                    for code in (
                        getattr(travel_spend, "source_code", ""),
                        getattr(travel_spend, "target_code", ""),
                        *(holding.code for holding in getattr(travel_spend, "holdings", ())),
                    )
                    if code
                )
            ),
            "ambiguous": False,
            "grounded": travel_grounding,
            "retrieval_allowed": False,
        }

    # A request to MOVE money is not this lane's turn, whatever currencies it names. "send 1000 TRY
    # to someone" must reach the ordinary path and its own safeguards, not be answered with a
    # definition of the lira. This check runs FIRST for that reason.
    if currency_transaction_intent(text):
        return None

    definition = currency_definition_intent(text, chat_codes=chat_codes)
    if definition is not None:
        response = render_currency_definition(definition)
        if response:
            return {
                "kind": "definition",
                "response": response,
                "code": definition.reference.code,
                "codes": tuple(definition.reference.candidates),
                "ambiguous": definition.reference.ambiguous,
                "grounded": "local_reference_data",
            }

    # A phrase that is BOTH a run of codes and ordinary English ("try all") is settled by neither
    # reading, so it is asked about rather than answered. Below the definition lane because a
    # single code that resolves is not ambiguous, and above the conversion lane because a phrase
    # carries no amount for a conversion to find.
    phrase = homograph_phrase_intent(text, chat_codes=chat_codes)
    if phrase is not None:
        response = render_homograph_phrase(phrase)
        if response:
            return {
                "kind": "ambiguity",
                "response": response,
                "code": "",
                "codes": tuple(phrase.codes),
                "ambiguous": True,
                "grounded": "local_reference_data",
            }

    conversion = fx_conversion_intent(text, chat_codes=chat_codes)
    if conversion is not None:
        response = render_fx_conversion(conversion, live_rate=live_rate, rate_asof=rate_asof)
        if response:
            if conversion.supplied_rate is not None:
                grounded = "user_supplied_rate"
            elif live_rate is not None:
                grounded = "live_rate"
            else:
                grounded = "no_rate_declined"
            return {
                "kind": "conversion",
                "response": response,
                "code": conversion.target.code,
                "codes": tuple(
                    dict.fromkeys(conversion.source.candidates + conversion.target.candidates)
                ),
                "ambiguous": conversion.source.ambiguous or conversion.target.ambiguous,
                "grounded": grounded,
            }

    # A comparison of several currencies, and — one turn later — the correction that pins them.
    # A turn that is a full comparison in its own right is read as one; anything else is offered to
    # the correction reader, because "Actually the kr is in Copenhagen and the ¥ is in Shanghai"
    # names no amount and would be claimed by nothing on its own. That gap is exactly how the
    # reported answer got to repeat NOK and JPY after being told otherwise.
    prior = prior_comparison(conversation_history)
    comparison = currency_comparison_intent(text)
    followup = None if comparison is not None else (
        comparison_followup(prior, text) if prior is not None else None
    )

    # A gate that claims a turn ENDS it. So the claim needs a whole-turn coverage proof, not a
    # recognition: "compare 500 kr and $500, then list the files in /tmp" is a mixed turn, and
    # answering only the currency half while silently dropping the other is the swallow this
    # check exists to prevent. Residue means hand the turn on — with the findings attached, via
    # `currency_grounding_observation` below, so nothing downstream has to guess at the symbols.
    # (The mixed-DOMAIN half of this contract — "weather in rome rn, 100 usd to rub, 18^2, …" —
    # is enforced one layer up, in `answer_coverage.coverage_for`: a slice co-claimed across
    # domain groups can never read as whole-turn coverage. Extending THIS gate to conversions
    # was tried and reverted: it is sentence-granular and broke same-family multi-conversion
    # turns, "100 EUR in USD at 1.10? and 500 CHF in JPY at 170?", which the coverage layer
    # handles correctly.)
    if (comparison is not None or followup is not None) and uncovered_residue(text):
        return None

    if followup is not None:
        response = render_currency_comparison(
            followup,
            prior=prior,
            live_rates=live_rates,
            rates_asof=rates_asof,
            live_ppp=live_ppp,
            ppp_asof=ppp_asof,
        )
        if response:
            return {
                "kind": "comparison_followup",
                "response": response,
                "codes": list(followup.codes()),
                "ambiguous": bool(followup.unpinned),
                "invalidated": [item.unit for item in invalidations(prior, followup)],
                "grounded": _comparison_grounding(followup, live_rates, live_ppp),
            }
    if comparison is not None:
        response = render_currency_comparison(
            comparison,
            live_rates=live_rates,
            rates_asof=rates_asof,
            live_ppp=live_ppp,
            ppp_asof=ppp_asof,
        )
        if response:
            return {
                "kind": "comparison",
                "response": response,
                "codes": list(comparison.codes()),
                "ambiguous": bool(comparison.unpinned),
                "grounded": _comparison_grounding(comparison, live_rates, live_ppp),
            }

    lookup = fx_rate_lookup_intent(text)
    if lookup is not None:
        return {
            "kind": "rate_lookup",
            "response": render_fx_rate_lookup(lookup, live_rate=live_rate, rate_asof=rate_asof),
            "code": lookup.quote.code,
            "ambiguous": False,
            "grounded": "live_rate" if live_rate is not None else "no_rate_declined",
        }

    # Last: a turn whose entity pairings cannot hold at all ("compare Copenhagen NOK and Oslo
    # DKK", "Toyota Passat vs Renault Golf"). No table says which side the user meant, so the
    # answer asks instead of picking — and computes nothing in the meantime.
    report = entity_consistency_report(text)
    if not report.consistent and not uncovered_by_contradictions(text, report):
        return {
            "kind": "entity_contradiction",
            "response": render_entity_contradictions(report),
            "domains": list(report.domains()),
            "ambiguous": True,
            "grounded": "authority_table_contradiction",
        }
    return None


def currency_grounding_observation(
    user_input: str,
    *,
    conversation_history: Sequence[Any] | None = None,
    chat_codes: Sequence[str] = (),
) -> dict[str, Any] | None:
    """What this lane established, for a turn it declined to answer. None when it established nothing.

    A deterministic decline must not also be a discard. When the turn is mixed and goes on to
    whatever composes several intents, the ISO ambiguity is still a fact the runtime resolved —
    and a model that has to rediscover it is a model that will guess, which is the whole of the
    reported defect. It travels on the observation channel the workspace tools already use rather
    than through a new prompt field: that path reaches the model on every surface, and it keeps
    the reading visibly labelled as runtime state instead of blending into the conversation.
    """

    text = str(user_input or "")
    if not text.strip() or currency_transaction_intent(text):
        return None
    request = currency_comparison_intent(text, chat_codes=chat_codes)
    if request is None:
        prior = prior_comparison(conversation_history)
        request = (
            comparison_followup(prior, text, chat_codes=chat_codes) if prior is not None else None
        )
    if request is None:
        report = entity_consistency_report(text)
        return contradiction_observation(
            report, unanswered=uncovered_by_contradictions(text, report)
        )
    note = render_currency_grounding_note(request)
    if not note:
        return None
    return {
        "schema": "tool_observation_v1",
        "intent": "currency.grounding",
        "tool_surface": "runtime",
        "ok": True,
        "status": "resolved",
        "read_only": True,
        "final_answer": False,
        "readings": list(currency_grounding_facts(request)),
        "unanswered_here": list(uncovered_residue(text)),
        "instruction": note,
    }


#: How many observations the shared channel keeps, matching what `turn_frontdoor` already trims to.
_OBSERVATION_LIMIT = 12


def attach_currency_grounding(
    source_context: dict[str, Any] | None,
    user_input: str,
    *,
    conversation_history: Sequence[Any] | None = None,
    chat_codes: Sequence[str] = (),
) -> bool:
    """Put this lane's findings on the shared observation channel. True when something was added.

    Called on the DECLINE path, so a turn this lane hands on still carries what it worked out.
    Lives here rather than in `turn_frontdoor` so the front door stays a dispatcher and the shape
    of the observation is owned by the lane that produces it.
    """

    if source_context is None:
        return False
    observation = currency_grounding_observation(
        user_input, conversation_history=conversation_history, chat_codes=chat_codes
    )
    if observation is None:
        return False
    existing = [
        dict(item)
        for item in list(source_context.get("runtime_tool_observations") or [])
        if isinstance(item, dict)
    ]
    existing.append(observation)
    source_context["runtime_tool_observations"] = existing[-_OBSERVATION_LIMIT:]
    return True


def _comparison_grounding(
    request: CurrencyComparisonRequest,
    live_rates: Mapping[tuple[str, str], Decimal] | None,
    live_ppp: Mapping[tuple[str, str], Decimal] | None,
) -> str:
    if request.poisoned:
        return "contradiction_declined"
    if live_rates or live_ppp:
        return "live_rate"
    if request.market_rates or request.ppp_rates or request.per_code_inflation:
        return "user_supplied_rate"
    return "no_rate_declined"


__all__ = [
    "attach_currency_grounding",
    "currency_fast_path",
    "currency_grounding_observation",
    "prior_comparison",
]
