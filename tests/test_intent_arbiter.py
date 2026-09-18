"""Constrained intent arbiter: menu building, strict parsing, and fail-open on every failure."""
from __future__ import annotations

from unittest import mock

import pytest

from core import intent_arbiter as ia
from core.agent_runtime.intent_claims import (
    FAMILY_FIND_FOLDER,
    FAMILY_MACHINE_SPECS,
    IntentClaim,
)

AMBIGUOUS_CLAIMS = [IntentClaim(FAMILY_FIND_FOLDER, "token hunter"), IntentClaim(FAMILY_MACHINE_SPECS)]


@pytest.fixture(autouse=True)
def _arbiter_env(monkeypatch):
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")  # skip live model discovery
    yield


def _model_reply(content: str):
    reply = mock.Mock()
    reply.raise_for_status = mock.Mock()
    reply.json.return_value = {"message": {"content": content}}
    return reply


def test_flag_off_never_touches_the_network(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "0")
    with mock.patch("requests.post", side_effect=AssertionError("must not be called")):
        assert ia.arbitrate("check token hunter folder on this machine", AMBIGUOUS_CLAIMS) is None


def test_picks_the_claiming_family_with_model_argument() -> None:
    with mock.patch("requests.post", return_value=_model_reply('{"choice": "find_folder", "argument": "token hunter"}')):
        decision = ia.arbitrate("check Token hunter folder on this machine", AMBIGUOUS_CLAIMS)
    assert decision is not None
    assert decision.family == FAMILY_FIND_FOLDER and decision.argument == "token hunter"


def test_empty_model_argument_falls_back_to_the_claims_extraction() -> None:
    with mock.patch("requests.post", return_value=_model_reply('{"choice": "find_folder", "argument": ""}')):
        decision = ia.arbitrate("check Token hunter folder on this machine", AMBIGUOUS_CLAIMS)
    assert decision is not None and decision.argument == "token hunter"  # from the claim probe


def test_chat_choice_is_a_valid_decline() -> None:
    with mock.patch("requests.post", return_value=_model_reply('{"choice": "chat", "argument": ""}')):
        decision = ia.arbitrate("why can't we improve our PnL?", AMBIGUOUS_CLAIMS)
    assert decision is not None and decision.family == ia.CHOICE_CHAT


def test_off_menu_choice_fails_open() -> None:
    # A family that was never offered must not be routable — the menu is the whole contract.
    with mock.patch("requests.post", return_value=_model_reply('{"choice": "image_generation", "argument": "x"}')):
        assert ia.arbitrate("check token hunter folder on this machine", AMBIGUOUS_CLAIMS) is None


def test_garbage_and_prose_fail_open() -> None:
    for content in ("sure! I think it's the folder one", "{not json", "", "42", '{"choice": 7}'):
        with mock.patch("requests.post", return_value=_model_reply(content)):
            assert ia.arbitrate("check token hunter folder", AMBIGUOUS_CLAIMS) is None, content


def test_network_failure_fails_open() -> None:
    with mock.patch("requests.post", side_effect=RuntimeError("ollama down")):
        assert ia.arbitrate("check token hunter folder", AMBIGUOUS_CLAIMS) is None


def test_json_embedded_in_prose_is_recovered() -> None:
    content = 'Here you go: {"choice": "find_folder", "argument": "token hunter"} hope that helps'
    with mock.patch("requests.post", return_value=_model_reply(content)):
        decision = ia.arbitrate("check token hunter folder on this machine", AMBIGUOUS_CLAIMS)
    assert decision is not None and decision.family == FAMILY_FIND_FOLDER


def test_near_miss_offers_the_full_readonly_menu() -> None:
    options = ia._candidate_options([])
    # 8 -> 10: list_processes and host_state joined the menu when the specs family stopped
    # absorbing "what is eating my CPU" and "what is the uptime".
    assert ia.CHOICE_CHAT in options and FAMILY_FIND_FOLDER in options and len(options) == 10


def test_ambiguous_offers_only_the_claiming_families_plus_chat() -> None:
    options = ia._candidate_options(AMBIGUOUS_CLAIMS)
    assert options == [FAMILY_FIND_FOLDER, FAMILY_MACHINE_SPECS, ia.CHOICE_CHAT]


def test_argument_is_sanitized_and_capped() -> None:
    long_arg = "x" * 500
    with mock.patch("requests.post", return_value=_model_reply(f'{{"choice": "find_folder", "argument": "{long_arg}"}}')):
        decision = ia.arbitrate("check folder", AMBIGUOUS_CLAIMS)
    assert decision is not None and len(decision.argument) <= 80


def test_default_is_on_and_zero_disables(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_INTENT_ARBITER", raising=False)
    assert ia.arbiter_enabled() is True          # proven default
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "0")
    assert ia.arbiter_enabled() is False         # explicit off wins


def test_near_miss_uses_the_model_argument_when_no_claim_carries_one() -> None:
    with mock.patch("requests.post", return_value=_model_reply('{"choice": "find_folder", "argument": "oken hunter"}')):
        decision = ia.arbitrate("please fint the oken hunter folder", [])  # near-miss: no claims
    assert decision is not None and decision.argument == "oken hunter"


def test_claim_extraction_beats_an_overcopied_model_argument() -> None:
    whole_message = "check my token hunter folder on this machine pls"
    with mock.patch("requests.post", return_value=_model_reply(f'{{"choice": "find_folder", "argument": "{whole_message}"}}')):
        decision = ia.arbitrate(whole_message, AMBIGUOUS_CLAIMS)
    assert decision is not None and decision.argument == "token hunter"  # regex extraction wins


def test_timeout_trips_the_breaker_and_next_call_fails_open_instantly() -> None:
    import requests as _requests

    ia.reset_breaker()
    with mock.patch("requests.post", side_effect=_requests.exceptions.ReadTimeout("slow")):
        assert ia.arbitrate("check token hunter folder on this machine", AMBIGUOUS_CLAIMS) is None
    assert ia.last_failure() == "request:ReadTimeout"
    # breaker now open: the next call must not even attempt the network
    with mock.patch("requests.post", side_effect=AssertionError("must not be called")):
        assert ia.arbitrate("check token hunter folder on this machine", AMBIGUOUS_CLAIMS) is None
    assert ia.last_failure() == "breaker_open"
    ia.reset_breaker()


def test_success_resets_the_breaker() -> None:
    ia.reset_breaker()
    with mock.patch("requests.post", return_value=_model_reply('{"choice": "find_folder", "argument": "token hunter"}')):
        assert ia.arbitrate("check token hunter folder on this machine", AMBIGUOUS_CLAIMS) is not None
    assert ia.last_failure() == ""
    assert ia._breaker_open() is False


def test_both_arbiter_payloads_carry_a_sized_context_window(monkeypatch) -> None:
    """This lane posts straight to Ollama, so the adapter's num_ctx stamping never reaches it.

    Unsized, Ollama loads the model at its NATIVE context and `keep_alive: 30m` pins that instance
    for half an hour at boot. Measured on a 24 GiB host with the arbiter's own payload shape
    (evidence-session4-20260909/arbiter-sizing-measurement.json):

        unsized  -> ctx=40960  4.99 GiB     <- a 0.6B model costing as much as a 7B
        num_ctx=16384          2.30 GiB     <- the adaptive default for role=classifier
        num_ctx=8192           1.40 GiB

    2.69 GiB of a 24 GiB machine, held by the smallest model in the bundle, for a classification
    prompt whose own output budget is 96 tokens. That is the cumulative-residency exhaustion
    FINDINGS F12 measured from the other side, and it is why acceptance packs died mid-run.
    """
    from core.runtime_provider_defaults import _ollama_context_window_for_bundle_role

    expected = _ollama_context_window_for_bundle_role("classifier", model_tag="qwen3:0.6b")
    assert expected > 0

    seen: list[dict] = []

    def _capture(*args, **kwargs):
        seen.append(dict(kwargs.get("json") or {}))
        return _model_reply('{"choice": "find_folder", "argument": "token hunter"}')

    with mock.patch("requests.post", side_effect=_capture):
        ia.arbitrate("check token hunter folder on this machine", AMBIGUOUS_CLAIMS)
    assert seen, "the arbitration call was never made"
    options = seen[0].get("options") or {}
    assert options.get("num_ctx") == expected, (
        f"the arbitration payload loads the model unsized: options={options}"
    )
    # keep_alive is what makes an unsized load EXPENSIVE rather than momentary.
    assert seen[0].get("keep_alive"), "this lane pins residency, so its sizing is not optional"

    seen.clear()
    with mock.patch("requests.post", side_effect=_capture):
        thread = ia.prewarm_async()
        if thread is not None and hasattr(thread, "join"):
            thread.join(timeout=10)
        else:
            import time as _time

            for _ in range(50):
                if seen:
                    break
                _time.sleep(0.1)
    assert seen, "the boot prewarm was never issued"
    warm_options = seen[0].get("options") or {}
    assert warm_options.get("num_ctx") == expected, (
        f"the boot prewarm pins an UNSIZED instance for 30 minutes: options={warm_options}"
    )


def test_every_direct_to_ollama_lane_sizes_the_model_it_loads() -> None:
    """A lane that posts straight to Ollama must stamp num_ctx, because nothing else will.

    These lanes bypass the provider adapter, which is where num_ctx is normally stamped
    (adapters/openai_compatible_adapter.py:906,978). Unsized, Ollama loads the model at its NATIVE
    context. Measured on a 24 GiB host with the arbiter's own payload shape
    (validation-logs/consolidation-continuation-20260909/evidence-session4-20260909/
    arbiter-sizing-measurement.json):

        qwen3:0.6b unsized -> ctx=40960  4.99 GiB      <- as much as a 7B model
        qwen3:0.6b @16384  ->            2.30 GiB
        qwen3:0.6b @8192   ->            1.40 GiB

    core/gpu_inference_probe.py already stamps num_ctx (and keep_alive 0), so this is the
    codebase's own rule, not a new one. This test exists because THREE more lanes were found
    breaking it after the first was fixed -- the next one added should fail here, not in a
    residency measurement three sessions later.
    """
    import ast
    import pathlib

    lanes = {
        "core/intent_arbiter.py",
        "core/fact_extractor.py",
        "core/conversation_summarizer.py",
        "core/craft_upgrade.py",
    }
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for relative in sorted(lanes):
        source = (root / relative).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # Every payload dict that carries an "options" key for a local generate/chat call.
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "options" not in keys or "model" not in keys:
                continue
            options = node.values[keys.index("options")]
            if not isinstance(options, ast.Dict):
                continue
            option_keys = [
                k.value for k in options.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
            ]
            if "num_ctx" not in option_keys:
                offenders.append(f"{relative}:{node.lineno} options={option_keys}")
    assert not offenders, (
        "these lanes post straight to Ollama and load the model UNSIZED, at its native context:\n  "
        + "\n  ".join(offenders)
    )

