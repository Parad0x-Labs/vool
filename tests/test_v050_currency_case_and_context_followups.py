"""QA-050-027: case is evidence, and the chat's own answers are evidence too.

Three live defects were reported against 03b04b39, in one session, in this order::

    "what is  ALL?"                 currency_definition_fast_path, no model.   CORRECT
    "WHAT IS all?"                  cloud Nemotron 550B, 1,512 tokens
    "what is meaning of try all?"   qwen3:14b, 4,925 tokens, pure-English answer

Measured on that base, at the seam that decides (`core.currency_intent`)::

    'what is  ALL?'                 def=HIT  fast=HIT  codes=['ALL']  class=chat_conversation
    'WHAT IS all?'                  def=—    fast=—    codes=[]       class=chat_conversation
    'what is meaning of try all?'   def=—    fast=—    codes=[]       class=research
    'what is meaning of TRY?'       def=—    fast=—    codes=['TRY']  class=research

One root cause with three faces. The homograph rule admitted a code on two grounds — typed in CAPS,
or the message says "money"/"currency" outright — and both are properties of the CURRENT message
only. So the runtime forgot its own answer the moment the user stopped shouting the code, and a
question whose subject was a two-word phrase ("try all") reached no lane at all. The fourth row is
the same miss with nothing to do with case: "meaning of" stayed glued to the term, so a three-word
definition never resolved either.

The wrong fix, stated so it cannot be quietly taken later
-----------------------------------------------------------
Uppercase the user's text and look everything up. That makes all four rows green and destroys the
only signal separating "what is ALL?" from "try all tests", "all of them" and "I will try all
options" — every one of which would then be a question about the Albanian lek.
`test_the_message_is_never_uppercased_wholesale` is the pin, and the ordinary-English family below
is what a global uppercase actually breaks.

What landed instead
--------------------
A candidate layer. The message is preserved; `code_candidates` derives candidates from it with the
typed case attached; `CurrencyEvidence.admits` uppercases ONE candidate for the lookup, and only
after a ground exists. The grounds are independent and each is measured separately below: typed
caps, explicit currency words, a conversion framing word paired with a quantity or a non-word code,
and — new — a code THIS CHAT already settled, read back out of the runtime's own answers by
`core/currency_chat_context.py`. That last one is per CODE, never a "currency mode" flag, which is
why a chat about TRY still refuses to read a later bare "all" as the lek.

Where both readings genuinely fit — "what is meaning of try all?" one turn after TRY and ALL were
defined — neither is picked. The turn is claimed and the choice handed back, locally.

What the assertions here are worth
-----------------------------------
`test_the_reported_session_runs_end_to_end_with_no_provider_call` drives the four reported messages
through the real front door in one session and counts provider calls at the two seams a call can be
entered at. That is the measurement that matters: the observed defect was a MODEL being bought, and
the lane/budget assertions further down are a second, weaker control — qwen3:14b is a
`daily_accelerated` role model on this operator's bundle, so "lane != deep" would not have caught
the 4,925-token turn on its own, and it is not offered as if it would.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from core.agent_runtime.fast_paths_currency import currency_fast_path
from core.currency_chat_context import chat_currency_codes
from core.currency_intent import (
    CodeCandidate,
    code_candidates,
    codes_from_currency_answer,
    codes_named,
    currency_definition_intent,
    currency_evidence,
    currency_semantics_present,
    currency_transaction_intent,
    fx_conversion_intent,
    homograph_phrase_intent,
    render_currency_definition,
    render_fx_conversion,
    render_homograph_phrase,
)
from tests.semantic_phase0._fixtures import (  # noqa: F401
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)
from tests.test_v050_fast_path_costs_no_speculative_inference import (  # noqa: F401
    ProviderInvocations,
    invocations,
)

# =================================================================================================
# The reported session, verbatim and in order.
# =================================================================================================

REPORTED_SESSION = (
    "what is TRY?",
    "what is  ALL?",
    "WHAT IS all?",
    "what is meaning of try all?",
)

#: What each of those must be claimed by. The fourth is an ambiguity, not a definition: "try all"
#: is a real English phrase and picking the currency reading would be the same class of guess as
#: picking the English one.
REPORTED_ROUTES = (
    "currency_definition_fast_path",
    "currency_definition_fast_path",
    "currency_definition_fast_path",
    "currency_ambiguity_fast_path",
)


def _drive(
    make_agent_module: Any,  # noqa: F811 - pytest's fixture request, not a redefinition
    workspace: Path,
    text: str,
    *,
    session: str,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "surface": "api",
        "session_id": session,
        "runtime_session_id": session,
        "request_id": f"req-{session}",
        "workspace": str(workspace),
        "workspace_root": str(workspace),
    }
    os.chdir(workspace)
    return make_agent_module().run_once(text, session_id_override=session, source_context=context)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "vool-currency-context"
    folder.mkdir()
    return folder


def test_the_reported_session_runs_end_to_end_with_no_provider_call(
    make_agent_module: Any,  # noqa: F811
    workspace: Path,
    invocations: ProviderInvocations,  # noqa: F811
) -> None:
    """The exact live sequence, one session, through the real front door.

    Every message is claimed locally and the provider-call count stays at zero for the whole
    session — counted where a call is ENTERED, so a call that then failed would still be counted.
    """

    session = "qa-050-027-reported"
    routes: list[str] = []
    answers: list[str] = []
    for text in REPORTED_SESSION:
        result = _drive(make_agent_module, workspace, text, session=session)
        routes.append(str(result.get("route_reason") or ""))
        answers.append(str(result.get("response") or ""))
        assert result.get("fast_path_hit") is True, f"{text!r} was not claimed by a fast path"
        assert int(result.get("model_calls") or 0) == 0, f"{text!r} reported a model call"
        assert dict(result.get("model_execution") or {}).get("used_model") is False

    assert routes == list(REPORTED_ROUTES), f"the reported session routed as {routes}"
    assert invocations.count == 0, (
        f"the reported session bought {invocations.count} provider call(s): {invocations.timeline}"
    )

    # Message 3 is the same question as message 2 and gets the same fact back.
    assert "Albanian lek" in answers[1] and "ALL" in answers[1]
    assert "Albanian lek" in answers[2] and "ALL" in answers[2]
    # Message 4 asks rather than guesses, and names BOTH readings in full.
    assert "try all" in answers[3]
    assert "Turkish lira" in answers[3] and "Albanian lek" in answers[3]
    assert "TRY" in answers[3] and "ALL" in answers[3]


def test_the_same_session_never_reaches_the_heavy_local_model(
    make_agent_module: Any,  # noqa: F811
    workspace: Path,
    invocations: ProviderInvocations,  # noqa: F811
) -> None:
    """No model was selected at all, so no model NAME can appear — qwen3:14b least of all."""

    session = "qa-050-027-no-heavy"
    for text in REPORTED_SESSION:
        result = _drive(make_agent_module, workspace, text, session=session)
        assert str(result.get("model_selected") or "") == "", (
            f"{text!r} selected {result.get('model_selected')!r}"
        )
        assert "qwen3" not in str(result.get("response") or "").lower()
    assert invocations.calls == []


def test_a_fresh_chat_gets_none_of_the_context_benefit(
    make_agent_module: Any,  # noqa: F811
    workspace: Path,
    invocations: ProviderInvocations,  # noqa: F811
) -> None:
    """The control for the test above: with no prior currency turn, neither follow-up is claimed.

    Same two messages, a session that never defined anything. If these were claimed anyway the
    context layer would be decoration over a lowercase lookup, which is the fix this file refuses.
    """

    for index, text in enumerate(("WHAT IS all?", "what is meaning of try all?")):
        result = _drive(make_agent_module, workspace, text, session=f"qa-050-027-fresh-{index}")
        assert "currency" not in str(result.get("route_reason") or ""), (
            f"{text!r} was claimed as currency in a chat that never mentioned currency"
        )


# =================================================================================================
# The rule, measured at the recognition seam: case, context, and neither.
# =================================================================================================

#: (code, the lowercase follow-up that must stay English until this chat settles that code).
CONTEXT_FOLLOWUPS = (
    ("ALL", "WHAT IS all?"),
    ("ALL", "what does all mean?"),
    ("TRY", "what is try?"),
    ("BOB", "what is bob?"),
    ("GEL", "what does gel mean?"),
    ("TOP", "define top"),
    ("CUP", "meaning of cup"),
    ("MAD", "what is mad?"),
)


@pytest.mark.parametrize(("code", "prompt"), CONTEXT_FOLLOWUPS)
def test_a_lowercase_homograph_is_english_in_a_fresh_chat(code: str, prompt: str) -> None:
    assert currency_definition_intent(prompt) is None, f"{prompt!r} was read as currency"
    assert codes_named(prompt) == []
    assert currency_semantics_present(prompt) is False
    assert currency_fast_path(prompt) is None


@pytest.mark.parametrize(("code", "prompt"), CONTEXT_FOLLOWUPS)
def test_the_same_question_resolves_once_this_chat_has_settled_that_code(
    code: str, prompt: str
) -> None:
    claimed = currency_fast_path(prompt, chat_codes=(code,))
    assert claimed is not None, f"{prompt!r} was not claimed after this chat settled {code}"
    assert claimed["kind"] == "definition"
    assert claimed["code"] == code
    from core.currency_intent import ISO_4217

    assert ISO_4217[code].name in str(claimed["response"])
    # The everyday reading is still disclosed. Context resolves which reading was asked for; it
    # does not entitle the answer to pretend the collision is not there.
    assert "Read as a word" in str(claimed["response"])


@pytest.mark.parametrize(("code", "prompt"), CONTEXT_FOLLOWUPS)
def test_context_is_per_code_and_never_a_currency_mode(code: str, prompt: str) -> None:
    """A chat that settled SEK does not license reading a bare "bob" as the boliviano.

    This is the difference between a breadcrumb and a mode flag, and it is the property that keeps
    a long currency conversation from turning every later English word into a code.
    """

    unrelated = ("SEK", "JPY", "EUR")
    assert code not in unrelated
    assert currency_fast_path(prompt, chat_codes=unrelated) is None, (
        f"{prompt!r} was claimed on context for {unrelated}, which does not contain {code}"
    )


def test_the_message_is_never_uppercased_wholesale() -> None:
    """Case survives into the candidate layer, and lowering it removes admission.

    A `text.upper()` anywhere on the message path makes the first assertion here fail, because
    "try all tests" would become "TRY ALL TESTS" and read as two currency codes.
    """

    assert codes_named("try all tests") == []
    assert codes_named("TRY ALL tests") == ["TRY", "ALL"]

    written = [candidate.written for candidate in code_candidates("Try all TRY usd")]
    assert written == ["Try", "all", "TRY", "usd"], (
        f"the candidate layer altered the user's capitalisation: {written}"
    )
    assert CodeCandidate(written="try").written_as_code is False
    assert CodeCandidate(written="TRY").written_as_code is True
    assert CodeCandidate(written="Try").written_as_code is False


def test_the_evidence_grounds_are_independent_and_each_one_is_enough() -> None:
    """Four grounds, measured one at a time on the same otherwise-bare token."""

    caps = currency_evidence("what is ALL?")
    assert caps.admits(CodeCandidate(written="ALL")) is True
    assert caps.explicit is False and not caps.chat_codes

    explicit = currency_evidence("what is all currency?")
    assert explicit.explicit is True
    assert explicit.admits(CodeCandidate(written="all")) is True

    chat = currency_evidence("what is all?", chat_codes=("ALL",))
    assert chat.admits(CodeCandidate(written="all")) is True
    assert chat.explicit is False and not chat.written_as_code

    framed = currency_evidence("convert 1000 all to usd")
    assert framed.exchange_framing is True and framed.quantity is True
    assert framed.admits(CodeCandidate(written="all")) is True

    # And the weak ground alone is not a ground.
    alone = currency_evidence("is it worth trying all of them")
    assert alone.exchange_framing is True
    assert alone.admits(CodeCandidate(written="all")) is False


# =================================================================================================
# The explicit-word family — the ground that needs neither caps nor history.
# =================================================================================================

EXPLICIT_WORD_FAMILY = (
    "what is all money?",
    "what is the all currency?",
    "i meant currency all",
    "i ment money try",
    "what is try in currency?",
    "all iso 4217?",
)


@pytest.mark.parametrize("prompt", EXPLICIT_WORD_FAMILY)
def test_an_explicit_currency_word_admits_a_lowercase_homograph(prompt: str) -> None:
    assert currency_semantics_present(prompt) is True, f"{prompt!r} lost its explicit context"


@pytest.mark.parametrize(
    "prompt",
    (
        "convert 1000 try to usd",
        "1000 all to usd",
        "500 try in usd",
        "what is 200 all worth in usd?",
    ),
)
def test_a_conversion_framing_plus_a_quantity_admits_a_lowercase_homograph(prompt: str) -> None:
    """The pairing rule. Framing alone proves nothing; framing over an amount does."""

    claimed = currency_fast_path(prompt)
    assert claimed is not None, f"{prompt!r} was not claimed"
    assert claimed["kind"] == "conversion"
    # And still no invented rate — the older guarantee is not weakened by the wider admission.
    assert claimed["grounded"] == "no_rate_declined"


@pytest.mark.parametrize(
    "prompt",
    (
        "move 3 try to top of the list",
        "bump 2 top to bob",
        "shift 5 all to gel",
    ),
)
def test_a_pair_with_no_settled_side_is_not_a_conversion(prompt: str) -> None:
    """The control for the pairing rule, and the reason it is a PAIR rule.

    Every one of these matches the conversion grammar over a quantity. What "1000 all to usd" has
    and these do not is one side that cannot be an English word. Without that requirement, moving
    an item to the top of a list would be a Tongan paʻanga conversion.
    """

    assert fx_conversion_intent(prompt) is None, f"{prompt!r} was read as a conversion"
    assert currency_fast_path(prompt, chat_codes=()) is None


# =================================================================================================
# ORDINARY ENGLISH — the negative controls, asserted with the breadcrumb LOADED.
# =================================================================================================

#: Sentences that are English and stay English. Every one is checked twice: in a fresh chat, and in
#: a chat that has already settled BOTH TRY and ALL as currencies — which is the exact state that
#: makes the reported follow-ups resolve. If context alone flipped these, the fix would have traded
#: one over-claim for a worse one.
ORDINARY_ENGLISH = (
    "try all tests",
    "all of them",
    "I will try all options",
    "try all apps",
    "try all the tests again please",
    "did you try all of the approaches?",
    "all done",
    "let me try all three",
    "run all tests",
    "that is all",
    "try again with all the flags",
    "all of the above",
)


@pytest.mark.parametrize("prompt", ORDINARY_ENGLISH)
@pytest.mark.parametrize("chat", [(), ("TRY", "ALL")], ids=["fresh_chat", "currency_chat"])
def test_ordinary_english_is_never_claimed_by_the_currency_lane(
    prompt: str, chat: tuple[str, ...]
) -> None:
    assert currency_fast_path(prompt, chat_codes=chat) is None, (
        f"{prompt!r} was claimed as currency with chat context {chat}"
    )
    assert currency_definition_intent(prompt, chat_codes=chat) is None
    assert homograph_phrase_intent(prompt, chat_codes=chat) is None


@pytest.mark.parametrize(
    "prompt",
    (
        "what does all mean?",
        "what is all?",
        "define all",
        "what does try mean?",
    ),
)
def test_a_bare_english_word_question_is_left_to_the_ordinary_lane(prompt: str) -> None:
    """"what does all mean?" in a fresh chat is a question about the English word.

    The currency lane says nothing rather than forcing the Albanian lek onto it.
    """

    assert currency_fast_path(prompt) is None


def test_a_currency_chat_does_not_reopen_a_transaction_request() -> None:
    """The oldest guard, re-checked under the new evidence. Context must not open it.

    A chat full of currency answers is exactly where a "send 1000 TRY to my wallet" would be most
    likely to be mistaken for another definition.
    """

    for prompt in (
        "send 1000 TRY to my wallet",
        "pay 500 all to that account",
        "transfer 200 try to my bank account",
    ):
        assert currency_transaction_intent(prompt) is True, prompt
        assert currency_fast_path(prompt, chat_codes=("TRY", "ALL", "USD")) is None, prompt


# =================================================================================================
# The ambiguity lane — what it claims, and everything it refuses to claim.
# =================================================================================================

AMBIGUOUS_PHRASE_FAMILY = (
    "what is meaning of try all?",
    "what does try all mean?",
    "meaning of try all",
    "define try all",
    "what is try all?",
    "what is the meaning of try all?",
)


@pytest.mark.parametrize("prompt", AMBIGUOUS_PHRASE_FAMILY)
def test_the_ambiguous_phrase_family_asks_instead_of_guessing(prompt: str) -> None:
    claimed = currency_fast_path(prompt, chat_codes=("TRY", "ALL"))
    assert claimed is not None, f"{prompt!r} was not claimed"
    assert claimed["kind"] == "ambiguity"
    answer = str(claimed["response"])
    assert "try all" in answer, "the English reading was not named"
    assert "TRY (Turkish lira)" in answer and "ALL (Albanian lek)" in answer
    assert answer.rstrip().endswith(".") or "?" in answer


@pytest.mark.parametrize("prompt", AMBIGUOUS_PHRASE_FAMILY)
def test_the_same_family_is_silent_in_a_fresh_chat(prompt: str) -> None:
    assert currency_fast_path(prompt) is None, (
        f"{prompt!r} was disambiguated in a chat with no currency in it"
    )


def test_a_phrase_of_non_word_codes_has_nothing_to_disambiguate() -> None:
    """"what does USD EUR mean" is not ambiguous, so no question is asked about it."""

    for prompt in ("what does USD EUR mean?", "meaning of usd eur", "what is sek nok?"):
        assert homograph_phrase_intent(prompt, chat_codes=("USD", "EUR", "SEK", "NOK")) is None


def test_a_shouted_phrase_of_codes_is_not_asked_about() -> None:
    """Typed in code case, the user already said which reading they meant."""

    assert homograph_phrase_intent("what does TRY ALL mean?", chat_codes=("TRY", "ALL")) is None


def test_the_ambiguity_answer_invents_no_number() -> None:
    """It is still a currency answer, and the no-rate guarantee covers every one of them."""

    import re

    answer = render_homograph_phrase(
        homograph_phrase_intent("what is meaning of try all?", chat_codes=("TRY", "ALL"))
    )
    assert not re.findall(r"\d", answer), f"the disambiguation printed a number: {answer!r}"


# =================================================================================================
# The breadcrumb reader — what counts as a currency turn, and what does not.
# =================================================================================================


def _history(*pairs: tuple[str, str]) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    for asked, answered in pairs:
        messages.append({"role": "user", "content": asked})
        messages.append({"role": "assistant", "content": answered})
    return {"conversation_history": messages}


def test_the_reader_enrols_a_code_from_this_runtimes_own_answer() -> None:
    answer = render_currency_definition(currency_definition_intent("what is ALL?"))
    context = _history(("what is ALL?", answer))
    assert "ALL" in chat_currency_codes(source_context=context)


def test_the_reader_enrols_nothing_from_an_ordinary_assistant_message() -> None:
    """A capitalised three-letter word in unrelated prose is not a currency turn.

    Without the marker gate in `codes_from_currency_answer`, every one of these would enrol a code
    and the next lowercase "all" would resolve to the Albanian lek off the back of a message about
    an API.
    """

    for prose in (
        "I checked the API and the CPU usage looks fine.",
        "ALL of the tests pass now.",
        "The TOP command shows the process is alive.",
        "Here is the SQL you asked for.",
        "I ran ALL the tests and TRY the build again if it fails.",
    ):
        assert chat_currency_codes(source_context=_history(("what happened?", prose))) == (), prose


def test_the_receipt_markers_survive_every_renderer() -> None:
    """Render the whole family and parse it back. This is what stops the markers drifting.

    The breadcrumb reads answers this module produced. Reword one without updating
    `CURRENCY_ANSWER_MARKERS` and the context layer goes quietly blind — so the round trip is
    asserted rather than assumed.
    """

    cases = (
        ("what is USD?", {"USD"}),
        ("what is ALL?", {"ALL"}),
        ("Turkish lira code?", {"TRY"}),
        ("what is kr?", {"SEK", "NOK", "DKK", "ISK"}),
    )
    for prompt, expected in cases:
        answer = render_currency_definition(currency_definition_intent(prompt))
        assert expected <= set(codes_from_currency_answer(answer)), (
            f"{prompt!r} rendered an answer the breadcrumb reader cannot read: {answer!r}"
        )

    conversion = render_fx_conversion(fx_conversion_intent("1000 TRY to USD?"))
    assert {"TRY", "USD"} <= set(codes_from_currency_answer(conversion))

    phrase = render_homograph_phrase(
        homograph_phrase_intent("what is meaning of try all?", chat_codes=("TRY", "ALL"))
    )
    assert {"TRY", "ALL"} <= set(codes_from_currency_answer(phrase))


def test_the_reader_survives_an_unreadable_event_log() -> None:
    """No breadcrumb is a smaller failure than a failed turn, and it must stay that way."""

    assert chat_currency_codes(session_id="no-such-session-qa-050-027") == ()
    assert chat_currency_codes() == ()
    assert chat_currency_codes(source_context={"conversation_history": "not a list"}) == ()


# =================================================================================================
# The footer, and the weight controls.
# =================================================================================================


def test_the_footer_on_every_new_route_says_no_model_ran(
    make_agent_module: Any,  # noqa: F811
    workspace: Path,
    invocations: ProviderInvocations,  # noqa: F811
) -> None:
    """A turn that ran no model must say so, including the two routes added here."""

    from core.response_provenance import format_provenance_footer

    session = "qa-050-027-footer"
    for text, expected_route in zip(REPORTED_SESSION, REPORTED_ROUTES, strict=True):
        result = _drive(make_agent_module, workspace, text, session=session)
        footer = format_provenance_footer(result, None)
        assert footer.startswith("`") and footer.endswith("`"), footer
        assert footer.count("\n") == 0, footer
        assert "no model" in footer, f"{text!r} footer claimed a model: {footer}"
        assert expected_route in footer or "tool" in footer, footer


@pytest.mark.parametrize(
    "prompt",
    ("WHAT IS all?", "what is meaning of try all?", "what is meaning of TRY?"),
)
def test_none_of_the_reported_turns_buys_an_unbounded_lane(prompt: str) -> None:
    """A second, weaker control than the call count above, and labelled as one.

    qwen3:14b is reachable from the `daily` lane on this operator's bundle, so this assertion would
    NOT have caught the reported 4,925-token turn by itself. It is here to keep an unbounded
    fallback loop from being reintroduced behind the fast path.
    """

    import core.agent_runtime  # noqa: F401  (package init is import-order sensitive on this base)
    from core.local_inference_autopilot import _resolve_lane
    from core.memory_first_router import resolve_fallback_budget_seconds
    from core.reasoning_engine import explicit_planner_style_requested
    from core.task_router import classify, model_execution_profile

    classification = classify(prompt, {"chat_surface": True})
    profile = model_execution_profile(
        classification["task_class"],
        chat_surface=True,
        planner_style_requested=explicit_planner_style_requested(prompt),
    )
    lane = _resolve_lane(
        user_text=prompt,
        task_kind=str(profile["task_kind"]),
        output_mode=str(profile["output_mode"]),
        source_context={},
        local_available=True,
        has_tiny_lane=True,
        has_deep_lane=True,
    )
    budget = resolve_fallback_budget_seconds(
        lane, forced_cpu=False, no_usable_gpu=False, output_mode=str(profile["output_mode"])
    )
    assert lane != "deep", f"{prompt!r} reached the heavyweight lane"
    assert budget is not None, f"{prompt!r} bought an unbounded fallback loop"


# =================================================================================================
# The fourth row: "meaning of" was never part of the term.
# =================================================================================================


@pytest.mark.parametrize(
    ("prompt", "code"),
    (
        ("what is meaning of TRY?", "TRY"),
        ("what is the meaning of USD?", "USD"),
        ("what is definition of SEK?", "SEK"),
        ("what is meaning of Kč?", "CZK"),
        ("what is the sense of JPY?", "JPY"),
    ),
)
def test_a_meaning_of_wrapper_no_longer_hides_the_term(prompt: str, code: str) -> None:
    """Measured miss on 03b04b39: "what is meaning of TRY?" classified `research` and resolved
    nothing, for a three-word definition this runtime already held."""

    claimed = currency_fast_path(prompt)
    assert claimed is not None, f"{prompt!r} still falls past the definition lane"
    assert claimed["code"] == code
