"""A turn whose model gave no answer says so in plain words, and later prompts still know it failed.

App bugs 2026-11-12 (real app, 247124ed):

  C1  "`qwen3.5:4b` failed before producing a usable answer. No cached or remembered text was
      substituted. Retry the turn." -- a log line, with the model name as a code span.
  C3  a 401 from a model on this computer: "ollama-local rejected the stored API key
      (authentication failed), so no cloud model could run this turn ... Check the key in
      Settings → API Keys". A local model has no key to check.

The notices are composed by core.agent_runtime.memory_runtime.chat_surface_honest_degraded_response
(the one surface every chat path uses) from the router's own decision. The served journey on the
real page is tests/test_a_failed_turn_reads_plainly_and_offers_try_again_served.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime import memory_runtime
from core.context_history_authority import assistant_text_is_runtime_failure_notice

#: Words that made the old notices read like a log line, or that leak how VOOL is built.
LOG_WORDING = (
    "`",
    "Retry the turn",
    "cached or remembered",
    "before producing a usable answer",
    "usable model response",
    "admission gate",
    "local runtime",
    "HTTP",
    "fallback",
)


def _agent() -> SimpleNamespace:
    # The model-failure branches never consult the agent; a live-info classifier answering "not a
    # live lookup" keeps the call on the model-failure path.
    return SimpleNamespace(_live_info_mode=lambda *_a, **_k: "")


def _no_answer(details: dict, **extra) -> str:
    execution = SimpleNamespace(source=extra.pop("source", "no_provider_available"), details=details, **extra)
    return memory_runtime.chat_surface_honest_degraded_response(_agent(), execution, user_input="Summarize a heat pump.")


def _assert_plain(text: str) -> None:
    for wording in LOG_WORDING:
        assert wording not in text, f"{wording!r} in {text!r}"


# ------------------------------------------------------------------------------------- C1 and kin


@pytest.mark.parametrize(
    ("error", "error_class", "says"),
    [
        ("503 Server Error: Service Unavailable for url: http://127.0.0.1:11434/api/chat", "", "stopped with an error before it replied"),
        ("", "EMPTY_PROVIDER_RESPONSE", "sent back an empty reply"),
        ("Read timed out. (read timeout=59.99)", "", "took too long to reply"),
        ("HTTPConnectionPool(host='127.0.0.1', port=11434): Max retries exceeded (Connection refused)", "", "could not be reached"),
        ("model_load_gated_low_memory -- 4.3 GB free", "", "could not start because this computer was short on free memory"),
        ("", "", "did not reply"),
    ],
)
def test_one_failed_model_is_named_with_what_it_did_in_plain_words(error: str, error_class: str, says: str) -> None:
    timing = {"provider_id": "ollama-local:qwen3.5:4b", "model_id": "qwen3.5:4b", "outcome": "failed", "local": True}
    if error:
        timing["error"] = error
    if error_class:
        timing["error_class"] = error_class
    text = _no_answer({"attempted": ["ollama-local:qwen3.5:4b"], "attempted_error_reasons": [error], "attempt_timings": [timing]})

    assert text == (
        f"I couldn't get an answer this time: qwen3.5:4b {says}. Nothing was made up in its place. "
        "You can try again."
    )
    _assert_plain(text)
    assert "127.0.0.1" not in text and "11434" not in text and "GB" not in text
    # A later prompt still recognizes it as the runtime's own no-answer notice, never an answer.
    assert assistant_text_is_runtime_failure_notice(text)


def test_each_model_the_turn_tried_is_named_in_order() -> None:
    text = _no_answer({
        "attempted": ["ollama-local:qwen3:4b", "ollama-local:qwen2.5:7b"],
        "attempted_error_reasons": ["Read timed out", "model_load_gated_low_memory -- 4.3 GB free"],
        "attempt_timings": [
            {"provider_id": "ollama-local:qwen3:4b", "model_id": "qwen3:4b", "error": "Read timed out", "local": True},
            {"provider_id": "ollama-local:qwen2.5:7b", "model_id": "qwen2.5:7b", "error": "model_load_gated_low_memory", "local": True},
        ],
    })

    assert text.startswith(
        "I couldn't get an answer this time: qwen3:4b took too long to reply, then qwen2.5:7b could not "
        "start because this computer was short on free memory."
    ), text
    _assert_plain(text)
    assert assistant_text_is_runtime_failure_notice(text)


def test_an_answer_cut_at_the_length_limit_says_so_plainly() -> None:
    execution = SimpleNamespace(
        source="provider_execution",
        model_name="qwen3:4b",
        details={"response_constraint": {"response_control": {
            "retry_attempted": True,
            "provider_completion": {"final": {"at_output_limit": True, "reasons": ["provider_finish_reason:length"]}},
        }}},
    )
    text = memory_runtime.chat_surface_honest_degraded_response(_agent(), execution, user_input="Explain tides.")

    assert text == (
        "I couldn't finish this answer: qwen3:4b reached its length limit before it was done, and one more "
        "try stopped at the same limit. Nothing was made up in its place. You can try again, or ask for a "
        "shorter answer."
    )
    assert assistant_text_is_runtime_failure_notice(text)


def test_a_selected_model_that_sent_back_nothing_says_so_plainly() -> None:
    text = _no_answer(
        {"requested_model": "qwen3.5:4b", "model_was_attempted": True,
         "provider_failure": {"error_class": "EMPTY_PROVIDER_RESPONSE"}, "attempted": ["ollama-local:qwen3.5:4b"]},
        source="selected_model_blocked",
    )

    assert text == "I couldn't get an answer this time: qwen3.5:4b sent back an empty reply. Nothing was made up in its place. You can try again."
    assert assistant_text_is_runtime_failure_notice(text)


@pytest.mark.parametrize(
    ("requested", "attempted", "named"),
    [
        ("usepod-byok:meridian-synth-chat", "usepod-byok:meridian-synth-chat", "meridian-synth-chat"),
        ("openrouter:meta-llama/llama-3.3-70b-instruct", "openrouter:meta-llama/llama-3.3-70b-instruct", "meta-llama/llama-3.3-70b-instruct"),
        ("ollama-local:qwen3.5:4b", "ollama-local:qwen3.5:4b", "qwen3.5:4b"),
        # A bare model name keeps its own colon: only a lane id the router dispatched loses its lane.
        ("qwen3.5:4b", "ollama-local:qwen3.5:4b", "qwen3.5:4b"),
    ],
    ids=["cloud-pin-lane", "openrouter-pin", "local-pin-lane", "bare-name"],
)
def test_a_pinned_model_is_named_by_its_own_name_not_its_lane(requested: str, attempted: str, named: str) -> None:
    """CI #681 (served pin lane, 2026-11-13): a pin arrives as its lane id, and the notice read
    "usepod-byok:meridian-synth-chat sent back an empty reply"."""
    text = _no_answer(
        {"requested_model": requested, "model_was_attempted": True,
         "provider_failure": {"error_class": "EMPTY_PROVIDER_RESPONSE"}, "attempted": [attempted]},
        source="selected_model_blocked",
    )

    assert text == f"I couldn't get an answer this time: {named} sent back an empty reply. Nothing was made up in its place. You can try again."
    _assert_plain(text)
    assert assistant_text_is_runtime_failure_notice(text)


@pytest.mark.parametrize(
    "stored",
    [
        "`qwen3.5:4b` failed before producing a usable answer. No cached or remembered text was substituted. Retry the turn.",
        "`claude-3-haiku` hit its output limit before it completed the answer. No cached or remembered text was substituted. Retry the turn.",
        "I couldn't get a usable model response: `x` did not return a usable reply. No cached or remembered text was substituted. Retry the turn.",
    ],
)
def test_notices_already_stored_in_chats_are_still_recognized(stored: str) -> None:
    """Chats keep the old wording forever; a later prompt must still close those exchanges."""
    assert assistant_text_is_runtime_failure_notice(stored)


@pytest.mark.parametrize(
    "answer",
    [
        "I couldn't get an answer this time from the library, so I checked the archive instead and found the 1907 map.",
        "Nothing was made up in its place: the replacement part is the original model from 1962.",
        "You can try again later if the shop is closed.",
    ],
)
def test_a_real_answer_with_similar_words_is_not_taken_for_a_notice(answer: str) -> None:
    assert not assistant_text_is_runtime_failure_notice(answer)


# ------------------------------------------------------------------------------------------- C3


def _auth_failure(status_error: str, *, local: bool, pinned: bool = False) -> str:
    provider = "ollama-local:qwen3.5:4b" if local else "openrouter:meta-llama/llama-3.3-70b-instruct"
    model = provider.split(":", 1)[-1]
    if pinned:
        details = {
            "attempted": [provider], "reason": status_error, "block_reason": status_error,
            "model_was_attempted": True, "requested_model": provider, "ranked_candidates": [],
            **({"selected_model_local": True} if local else {}),
        }
        return _no_answer(details, source="selected_model_blocked", provider_id=provider)
    timing = {"provider_id": provider, "model_id": model, "outcome": "failed", "error": status_error}
    if local:
        timing["local"] = True
    return _no_answer(
        {"attempted": [provider], "attempted_error_reasons": [status_error], "attempt_timings": [timing]},
        provider_id=provider,
    )


@pytest.mark.parametrize("pinned", [False, True], ids=["auto", "pinned"])
def test_a_401_from_a_model_on_this_computer_is_not_a_cloud_key_problem(pinned: bool) -> None:
    text = _auth_failure("401 Client Error: Unauthorized for url: http://127.0.0.1:11434/api/chat", local=True, pinned=pinned)

    assert text == (
        "I couldn't get an answer this time: the model app on this computer would not run qwen3.5:4b "
        "because it asked for a sign-in. Nothing was made up in its place. If this model needs an "
        "account in your model app (such as Ollama), sign in there, then try again."
    ), text
    for cloud_wording in ("API key", "API Keys", "cloud", "ollama-local", "authentication failed", "401"):
        assert cloud_wording not in text, f"{cloud_wording!r} in {text!r}"
    assert assistant_text_is_runtime_failure_notice(text)


def test_a_403_and_a_429_from_a_model_on_this_computer_are_said_plainly_too() -> None:
    refused = _auth_failure("403 Client Error: Forbidden", local=True)
    busy = _auth_failure("429 Client Error: Too Many Requests", local=True)

    assert "the model app on this computer refused to run qwen3.5:4b" in refused, refused
    assert "the model app on this computer was too busy to run qwen3.5:4b" in busy, busy
    for text in (refused, busy):
        assert "API Keys" not in text and "quota" not in text and "HTTP" not in text, text


@pytest.mark.parametrize("pinned", [False, True], ids=["auto", "pinned"])
def test_a_429_from_a_model_on_this_computer_names_the_wait_it_asked_for(pinned: bool) -> None:
    """The 429 law the cloud wording keeps holds here too (CI #681, served fault journey): the model
    app's own Retry-After is the wait to name, and "Wait a moment" never stands in for it. Without
    one, no short wait is promised: an account-run model in Ollama has a usage limit of its own."""
    timed = _auth_failure("429 Client Error: Too Many Requests [retry_after=27s]", local=True, pinned=pinned)
    bare = _auth_failure("429 Client Error: Too Many Requests", local=True, pinned=pinned)

    assert "the model app on this computer was too busy to run qwen3.5:4b just now and asked to wait about 27 seconds" in timed, timed
    assert "Try again after that wait." in timed, timed
    assert "may limit how much this model can be used" in bare and "seconds" not in bare, bare
    for text in (timed, bare):
        assert "wait a moment" not in text.lower(), text
        assert "retry_after" not in text and "Retry-After" not in text, text
        _assert_plain(text)
        assert assistant_text_is_runtime_failure_notice(text)


def test_a_401_from_a_cloud_provider_keeps_its_key_advice() -> None:
    text = _auth_failure("401 Client Error: Unauthorized", local=False)

    assert "rejected the stored API key" in text and "Settings → API Keys" in text, text


def test_the_router_marks_an_attempt_on_this_computer_as_local() -> None:
    from core.memory_first_router import _record_attempt_timing
    from storage.model_provider_manifest import ModelProviderManifest

    def manifest(name: str, base_url: str) -> ModelProviderManifest:
        return ModelProviderManifest(
            provider_name=name, model_name="m", source_type="http", adapter_type="openai_compatible",
            license_name="x", license_reference="x", weight_location="external", runtime_dependency="x",
            capabilities=["summarize"], runtime_config={"base_url": base_url}, metadata={},
        )

    timings: list[dict] = []
    _record_attempt_timing(timings, manifest=manifest("ollama-local", "http://127.0.0.1:11434"), seconds=1.0, outcome="failed", error="401")
    _record_attempt_timing(timings, manifest=manifest("openrouter", "https://openrouter.ai/api/v1"), seconds=1.0, outcome="failed", error="401")

    assert timings[0].get("local") is True
    assert "local" not in timings[1]
