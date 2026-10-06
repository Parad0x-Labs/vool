"""An advice request keeps the details from the user's own context.

Measured on the archived holdout reader calls (12 preference drafts, both VOOL arms): every
first draft was rejected by the 64-word ordinary-chat ceiling, and the one rewrite was told
"Do not introduce an analogy, comparison, or identifier from an earlier turn". The rewrite read
that as "drop what the user told you earlier": user-evidence words kept fell 82 -> 28 -> 20 from
draft to delivered answer, so the delivered advice was generic.

The contract after the repair:
  * an open advice / recommendation / suggestion request is selected by request KIND (its
    grammar), never by topic, and gets a bounded detail budget: 140 words, up to 5 items;
  * the rewrite asks the model to keep the user's own details that make the answer fit them;
  * the earlier-turn ban is sent only when the chat really holds an earlier opaque code
    (prior_turn_literal_hashes non-empty), worded as "do not repeat earlier opaque codes";
  * the hash-based literal-leak rejection itself is unchanged.

Every sentence here is synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import core.ordinary_chat_response_guard as guard
from adapters.base_adapter import ModelRequest, ModelResponse
from core.memory_first_router import MemoryFirstRouter
from storage.model_provider_manifest import ModelProviderManifest

FACT = "Sony A7R IV"
ADVICE_TURN = "Can you recommend a few upgrades for my camera kit?"


def _policy(text: str, **kwargs) -> dict:
    return guard.ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode="plain_text", user_text=text, **kwargs
    )


def _words(text: str) -> int:
    return len(guard._WORD_RE.findall(text))


# A 150-word first draft that names the user's own camera and runs past every budget.
DRAFT = (
    f"Since you shoot on a {FACT} with the standard zoom, a few upgrades stand out for the kind "
    "of landscape and portrait work you described. "
    + "A sturdy carbon tripod steadies long exposures and keeps the high resolution sensor sharp. " * 6
    + "A spare battery set matters because the electronic viewfinder drains power quickly. " * 3
    + "Pick what suits your trips best."
)

# The rewrite a model returns: five short items, about 110 words, and the user's camera is named
# only AFTER word 64 -- exactly the detail a 64-word cut removes.
REWRITE = (
    "Here are five upgrades that fit how you shoot:\n\n"
    "1. A carbon tripod with a ball head, so long landscape exposures stay sharp at dusk and dawn.\n"
    "2. A fast portrait prime, around 85mm, for soft backgrounds when you photograph friends outdoors.\n"
    "3. A circular polarizer and a six stop neutral density filter for water, skies and midday glare.\n"
    "4. Two spare batteries, because the "
    + FACT
    + " drains power quickly when the electronic viewfinder runs all day.\n"
    "5. A weatherproof sling bag that holds the body, two lenses and the filters on long walks.\n\n"
    "Start with the tripod and filters; they change your landscape results the most."
)


def _manifest() -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen2.5:7b",
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="provider terms",
        license_reference="https://example.invalid/terms",
        weight_location="external",
        runtime_dependency="openai-compatible",
        capabilities=["summarize"],
        runtime_config={
            "base_url": "http://127.0.0.1:11434/v1",
            "api_path": "/chat/completions",
            "timeout_seconds": 5.0,
        },
        metadata={
            "deployment_class": "local",
            "cost_class": "free_local",
            "runtime_family": "openai-compatible",
        },
    )


def _invoke(policy: dict, prompt: str, replies: list[str]):
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(text, usage={"completion_tokens": 200}, finish_reason="stop")
        for text in replies
    ]
    request = ModelRequest(
        task_kind="conversation",
        prompt=prompt,
        messages=[{"role": "user", "content": prompt}],
        max_output_tokens=440,
        output_mode="plain_text",
        metadata={
            "ordinary_chat_output_policy": policy,
            "defer_stream_until_verified": True,
        },
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        mock.patch("core.memory_first_router.provider_cost_class", return_value="free_local"),
        mock.patch("core.memory_first_router.reported_cost_class", return_value="free_local"),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="advice-keeps-user-details"),
            source_context={},
        )
    assert error is None and response is not None
    return response, adapter


# ---------------------------------------------------------------------------------------------
# Fake-provider round trip: the falsifying case
# ---------------------------------------------------------------------------------------------


def test_the_draft_and_rewrite_have_the_measured_shape() -> None:
    assert 145 <= _words(DRAFT) <= 155
    assert 100 <= _words(REWRITE) <= 140
    prefix_64 = guard._WORD_RE.findall(REWRITE)[:64]
    assert "Sony" not in prefix_64


def test_an_advice_rewrite_keeps_the_users_own_detail() -> None:
    policy = _policy(ADVICE_TURN)
    assert policy["prior_turn_literal_hashes"] == []

    response, adapter = _invoke(policy, ADVICE_TURN, [DRAFT, REWRITE])

    assert adapter.run_text_task.call_count == 2
    instruction = adapter.run_text_task.call_args_list[1].args[0].prompt
    assert "earlier turn" not in instruction
    assert "earlier opaque codes" not in instruction
    assert "Keep the details from the user's own context that make this answer fit them." in instruction
    assert "at most 140 words" in instruction
    assert FACT in response.output_text
    assert response.output_text == REWRITE


def test_the_earlier_code_sentence_is_sent_only_when_the_chat_holds_one() -> None:
    plain = guard.ordinary_chat_retry_instruction(_policy(ADVICE_TURN))
    with_code = guard.ordinary_chat_retry_instruction(
        _policy(
            ADVICE_TURN,
            prior_turn_literal_hashes=tuple(
                guard.prior_turn_literal_hashes(
                    [{"role": "user", "content": "Output exactly ERR_AWS_DENIED and nothing else."}],
                    current_user_text=ADVICE_TURN,
                )
            ),
        )
    )
    assert "opaque code" not in plain
    assert "earlier turn" not in plain
    assert "Do not repeat earlier opaque codes" in with_code
    assert "Keep the details from the user's own context" in with_code


def test_an_earlier_literal_is_still_stripped_from_an_advice_answer() -> None:
    """The literal-leak guard is the hash check; the advice budget does not touch it."""

    prior = guard.prior_turn_literal_hashes(
        [{"role": "user", "content": "Output exactly ERR_AWS_DENIED and nothing else."}],
        current_user_text=ADVICE_TURN,
    )
    policy = _policy(ADVICE_TURN, prior_turn_literal_hashes=prior)
    leaked = "A carbon tripod helps most. ERR_AWS_DENIED. A polarizer comes next."
    verdict = guard.inspect_ordinary_chat_output(leaked, policy, current_user_text=ADVICE_TURN)
    assert verdict.allowed is False
    assert verdict.reasons == ("unrequested_prior_turn_literal",)
    stripped = guard.remove_unrequested_prior_turn_literals(
        leaked, policy, current_user_text=ADVICE_TURN
    )
    assert "ERR_AWS_DENIED" not in stripped
    assert "carbon tripod" in stripped


# ---------------------------------------------------------------------------------------------
# The selector is request kind (grammar), never topic
# ---------------------------------------------------------------------------------------------


ADVICE_REQUESTS = (
    "Any suggestions?",
    "I have a free Saturday coming up. Any ideas?",
    "Can you suggest a few board games for a rainy evening?",
    "Could you recommend some podcasts for a long drive?",
    "Please recommend a sturdy backpack for day hikes.",
    "Do you have any helpful pointers for learning the violin as an adult?",
    "I'm repainting the hallway. Any tips on choosing a colour?",
    "My balcony garden keeps wilting. Any advice?",
    "Got any podcast recommendations for the gym?",
    "I need some advice on negotiating a raise.",
    "What should I cook for friends who don't eat meat?",
    "Which laptop should I buy for video editing?",
    "Should I learn Rust or Go first?",
    "Do you think it would be a good idea to switch teams at work?",
    "Would it be a good idea to repot my fern in spring?",
    # sloppy, user-typed variants of the same request kind
    "any sugestions for a cheap weekend trip",
    "can u recomend a good thriller",
    "pls recommend me a pizza place near the station",
    "need a gift for my sister, any ideas??",
    "got any good tips 4 sleeping on planes",
    "what shoes should i get for trail running",
    "Is it worth it to buy a used e-bike?",
)


@pytest.mark.parametrize("text", ADVICE_REQUESTS)
def test_an_open_advice_request_gets_the_bounded_detail_budget(text: str) -> None:
    policy = _policy(text)
    assert policy["max_words"] == "140", text
    assert policy["max_items"] == 5, text
    assert policy["detail_requested"] == "false", text


NOT_ADVICE = (
    "Why do file names matter?",
    "What time does the library close?",
    "What did you recommend for my sore back last week?",
    "My doctor gave me some advice about sleep yesterday.",
    "Explain how a heat pump works.",
    "Who wrote the tips section of the manual?",
    "Could there be a reason my tea tastes bitter?",
    # adversarial near-misses: advice words that are not a request for advice
    "Suggestions from the committee were ignored.",
    "Remind me what tips you gave me about my sourdough starter.",
    "I recommended that café to my brother.",
)


@pytest.mark.parametrize("text", NOT_ADVICE)
def test_other_request_kinds_keep_the_ordinary_budget(text: str) -> None:
    policy = _policy(text)
    assert policy["max_words"] == "64", text
    assert policy.get("max_items") is None, text


@pytest.mark.parametrize(
    "text,words",
    (
        ("Any suggestions for a quick lunch? Keep it brief.", 64),
        ("Can you suggest a snack in under 20 words?", 20),
        ("Any tips on sleeping better? Answer in one sentence.", 64),
    ),
)
def test_an_explicit_short_contract_outranks_the_advice_budget(text: str, words: int) -> None:
    policy = _policy(text)
    assert policy["max_words"] == str(words)
    assert policy.get("max_items") is None


@pytest.mark.parametrize(
    "text",
    (
        "Give me a detailed list of suggestions for a garden party.",
        "Suggest 8 items for a picnic.",
    ),
)
def test_a_stated_detail_or_count_contract_still_lifts_the_ceiling(text: str) -> None:
    policy = _policy(text)
    assert policy["detail_requested"] == "true"
    assert policy["max_words"] == ""
    assert policy.get("max_items") is None


def test_a_stated_count_is_never_cut_to_the_advice_item_bound() -> None:
    """"recommend 8 novels" names its own count; five items would drop three of them."""

    assert guard._is_open_advice_request("Could you recommend some novels for a holiday?")
    assert not guard._is_open_advice_request("Can you recommend 8 novels for a long holiday?")
    assert _policy("Can you recommend 8 novels for a long holiday?").get("max_items") is None


# ---------------------------------------------------------------------------------------------
# The bounded detail budget in the inspector
# ---------------------------------------------------------------------------------------------


def test_a_five_item_advice_answer_within_budget_passes() -> None:
    policy = _policy(ADVICE_TURN)
    assert guard.inspect_ordinary_chat_output(
        REWRITE, policy, current_user_text=ADVICE_TURN
    ).allowed


def test_a_long_five_item_advice_answer_is_not_called_an_overanswer() -> None:
    """120-140 words in five items is inside the bounded detail budget; the ordinary rule (three
    list items at 120+ words) would reject it and send the answer back for a rewrite."""

    policy = _policy(ADVICE_TURN)
    items = "\n".join(
        f"{index}. Option {index} suits the way you shoot on weekend walks, because it is light, "
        "simple to carry, and quick to set up at dusk."
        for index in range(1, 6)
    )
    text = "These five fit your kit and your habits.\n\n" + items
    assert 120 <= _words(text) <= 140
    verdict = guard.inspect_ordinary_chat_output(text, policy, current_user_text=ADVICE_TURN)
    assert verdict.allowed, verdict
    ordinary = _policy("Why do file names matter?")
    assert ordinary["max_words"] == "64"
    assert not guard.inspect_ordinary_chat_output(
        text, {**ordinary, "max_words": "140"}, current_user_text="Why do file names matter?"
    ).allowed


def test_a_long_advice_list_over_the_item_bound_is_still_an_overanswer() -> None:
    policy = _policy(ADVICE_TURN)
    items = "\n".join(
        f"{index}. Option {index} is a reasonable pick for most camera bags and trips."
        for index in range(1, 10)
    )
    text = "Several options would work for you.\n\n" + items
    assert _words(text) >= 120
    verdict = guard.inspect_ordinary_chat_output(text, policy, current_user_text=ADVICE_TURN)
    assert verdict.allowed is False
    assert verdict.reasons == ("boilerplate_overanswer",)


def test_an_advice_answer_over_the_word_budget_is_trimmed_at_140() -> None:
    policy = _policy(ADVICE_TURN)
    verdict = guard.inspect_ordinary_chat_output(DRAFT, policy, current_user_text=ADVICE_TURN)
    assert verdict.reasons == ("ordinary_response_too_long",)
    trimmed = guard.constrain_ordinary_chat_output(DRAFT, policy)
    assert _words(trimmed) <= 140
    assert FACT in trimmed


def test_the_advice_rewrite_names_the_item_bound() -> None:
    instruction = guard.ordinary_chat_retry_instruction(_policy(ADVICE_TURN))
    assert "at most 5 items" in instruction
    ordinary = guard.ordinary_chat_retry_instruction(_policy("Why do file names matter?"))
    assert "at most 5 items" not in ordinary
    assert "at most 64 words" in ordinary
