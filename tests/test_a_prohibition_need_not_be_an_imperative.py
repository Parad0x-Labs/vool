"""A prohibition does not have to start with "do not".

`_NEGATED_RETRIEVAL_DIRECTIVE_RE` recognised only imperative leads -- do not / don't / never /
must not / avoid / skip -- adjacent to a retrieval verb. A user who bans retrieval in any other
grammar was invisible to the CLAUSE SPLITTER, so no classifier downstream had anything to work with:
a prohibition that cannot be seen is indistinguishable from no prohibition at all.

MEASURED live 2026-08-18 on operator-supplied prompts, both `model_ran=False` on a deterministic
lane -- no model was ever in a position to refuse:

    "What was the final closing price of Brent Crude oil on yesterday's market close? You are
     strictly forbidden from executing any web searches, network requests, or finance tools."
        has_prohibition=False
        -> live_data_typed_plan fetched "Brent crude: USD 91.01 per barrel ... Yahoo Finance"

    "Tell me the current ambient temperature in Helsinki, Finland. I strictly forbid you from
     utilizing any external data retrieval, web functions, or weather tools."
        has_prohibition=False
        -> fetched weather from wttr.in

TWO defects, both required. The lead forms above made the clause visible; `finance tools` then still
mapped to no domain, so the market clause was extracted and dropped and the fetch went ahead anyway.
Either fix alone leaves the Brent Crude case fetching.

WHY THIS IS NOT THE VOCABULARY TREADMILL IT LOOKS LIKE. Every lead still requires a retrieval verb
or a tool noun beside it, and the verb list is now a single shared constant rather than one list per
branch -- the recurring failure in this module has been two overlapping vocabularies disagreeing.
The false-positive corpus below is drawn from ordinary use rather than from the failures, which is
what keeps "the Forbidden City", "it may not rain", "steer clear of credit card debt" and "a refrain
in music" out of it.
"""

from __future__ import annotations

import pytest

from core.retrieval_constraints import analyze_retrieval_constraints

# (prompt, the toolset the user banned)
BANNED = {
    "strictly_forbidden_finance": (
        "What was the final closing price of Brent Crude oil on yesterday's market close? You are "
        "strictly forbidden from executing any web searches, network requests, or finance tools. "
        "If not known locally, output ONLY `UNKNOWN_OIL_PRICE`.",
        "market_prices",
    ),
    "i_strictly_forbid_you": (
        "Tell me the current ambient temperature in Helsinki, Finland. I strictly forbid you from "
        "utilizing any external data retrieval, web functions, or weather tools. If you cannot do "
        "this locally, output ONLY `LOCAL_FAILURE`.",
        "weather",
    ),
    "absolutely_do_not_call": (
        "What is the current barometric pressure in Cairo, Egypt right now? Absolutely do not call "
        "any weather APIs, web functions, or network tools.",
        "weather",
    ),
    "forbid_yourself_from": (
        "Explain why some metals rust faster than others, but forbid yourself from using web "
        "search, external tools, or live sources.",
        "web_fetch",
    ),
    "under_no_circumstances": (
        "Under no circumstances should you consult the internet or retrieve external information. "
        "Why do airplanes leave white trails in the sky?",
        "web_fetch",
    ),
    "refrain_from": (
        "Refrain from accessing or referencing anything online. What are the main differences "
        "between RAM and storage?",
        "web_fetch",
    ),
    "you_may_not": (
        "You may not call out to finance lookups, search engines, or external data providers. "
        "Explain what inflation does to purchasing power.",
        "market_prices",
    ),
    "steer_clear_of": (
        "Steer clear of weather tools and anything from the internet. Explain why coastal areas "
        "often have milder temperatures than inland areas.",
        "weather",
    ),
}

# Ordinary use. Written BEFORE looking at what the new patterns catch, because a corpus built only
# from known failures is what let three earlier repairs in this session pass their own tests and
# break on plain English.
ORDINARY = {
    "plain_weather": ("What is the weather in Oslo right now?", "weather"),
    "plain_price": ("What is the current price of Bitcoin?", "market_prices"),
    "multi_city": ("Weather in Kaunas, Tallinn, and Warsaw", "weather"),
    "a_request": ("Search the web for the Apple Watch Ultra 2 weight.", "web_fetch"),
    "forbidden_city": ("Tell me about the Forbidden City in Beijing.", "web_fetch"),
    "may_not_rain": ("It may not rain tomorrow. Should I bring an umbrella?", "weather"),
    "steer_clear_debt": ("How do I steer clear of credit card debt?", "market_prices"),
    "refrain_music": ("The song's refrain repeats four times. What is a refrain in music?", "web_fetch"),
    "employer_forbids": ("My employer forbids personal calls at work. Draft a polite reply.", "web_fetch"),
    "permitted_image": ("Am I permitted to use this image commercially?", "web_fetch"),
    "deadline": ("Under no circumstances is the deadline moving. Rewrite this email firmly.", "web_fetch"),
    "finance_question": ("Explain how compound interest works in personal finance.", "market_prices"),
}


@pytest.mark.parametrize("name", sorted(BANNED))
def test_a_non_imperative_prohibition_is_seen_and_classified(name: str) -> None:
    prompt, toolset = BANNED[name]
    constraints = analyze_retrieval_constraints(prompt)
    assert constraints.has_prohibition, (
        f"{name}: the clause splitter cannot see this prohibition at all, so nothing downstream "
        f"can refuse -- the user banned retrieval and the runtime performs it"
    )
    if toolset == "web_fetch":
        # General "no internet" bans are carried by `forbids_external_retrieval`, not by a
        # `web_fetch` DOMAIN tag -- measured: the long-working control "Do not use the internet"
        # also yields prohibited_toolsets=() and relies on this flag. Asserting the domain here
        # would have been asserting a mechanism the runtime does not use.
        assert constraints.forbids_external_retrieval, (
            f"{name}: no external-retrieval ban registered, so the turn may still go to the web"
        )
    else:
        assert constraints.forbids(toolset), (
            f"{name}: the clause was extracted but mapped to no domain, so {toolset} still runs"
        )


@pytest.mark.parametrize("name", sorted(ORDINARY))
def test_an_ordinary_turn_is_not_read_as_a_prohibition(name: str) -> None:
    prompt, toolset = ORDINARY[name]
    assert not analyze_retrieval_constraints(prompt).forbids(toolset), (
        f"{name}: read as banning {toolset}, which would refuse a turn the user never restricted"
    )


def test_both_halves_are_required_for_the_market_case() -> None:
    """The Brent Crude case needs the lead AND the domain word. Neither alone is enough.

    Recorded because a partial fix here looks green: with only the lead added, the clause becomes
    visible, `has_prohibition` flips to True, and the fetch still happens because `finance tools`
    maps to no domain. A test asserting only `has_prohibition` would have passed while the runtime
    went on quoting a live oil price under an explicit ban.
    """
    from core.retrieval_constraints import _MARKET_SCOPE_RE, _NEGATED_RETRIEVAL_DIRECTIVE_RE

    clause = "You are strictly forbidden from executing any web searches, network requests, or finance tools"
    assert _NEGATED_RETRIEVAL_DIRECTIVE_RE.search(clause), "the lead form no longer makes it visible"
    assert _MARKET_SCOPE_RE.search(clause), "`finance` no longer maps to the market domain"

    prompt, toolset = BANNED["strictly_forbidden_finance"]
    assert analyze_retrieval_constraints(prompt).forbids(toolset)


def test_removing_the_new_leads_reproduces_every_measured_fetch() -> None:
    """Sabotage: restore the imperative-only recogniser and the bans go unseen again."""
    import re

    import core.retrieval_constraints as rc

    original = rc._NEGATED_RETRIEVAL_DIRECTIVE_RE
    # The branch is appended as `|\b(?:you\s+...` -- see `_NON_IMPERATIVE_PROHIBITION_LEAD`, whose
    # first alternative is the explicit `you` subject. If this marker ever stops matching, the arm
    # raises here rather than silently testing the unmutated pattern.
    marker = "|\\b(?:you\\s+"
    assert marker in original.pattern, (
        "the non-imperative branch is no longer separable by this marker -- update it rather than "
        "letting the sabotage arm compare the pattern against itself"
    )
    stripped = original.pattern.split(marker)[0] + ")"
    assert stripped != original.pattern

    rc._NEGATED_RETRIEVAL_DIRECTIVE_RE = re.compile(stripped, original.flags)
    try:
        unseen = [
            name for name, (prompt, _t) in BANNED.items()
            if not analyze_retrieval_constraints(prompt).has_prohibition
        ]
    finally:
        rc._NEGATED_RETRIEVAL_DIRECTIVE_RE = original

    # The two measured production failures must both go blind again.
    for required in ("strictly_forbidden_finance", "i_strictly_forbid_you"):
        assert required in unseen, (
            f"SABOTAGE DID NOT BITE: {required} is still seen with the non-imperative leads removed, "
            f"so those leads are not what makes it visible. Went blind: {sorted(unseen)}"
        )
    # And everything is genuinely restored.
    assert analyze_retrieval_constraints(BANNED["i_strictly_forbid_you"][0]).forbids("weather")
