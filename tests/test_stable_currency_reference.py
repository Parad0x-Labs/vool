from __future__ import annotations

from unittest import mock

import pytest

from core.stable_currency_reference import stable_currency_reference_response


@pytest.mark.parametrize(
    ("prompt", "codes"),
    [
        (
            '"Try to mop up all the spilled gel from the top." What do Try, mop, all, gel, and top have in common financially?',
            ("TRY", "MOP", "ALL", "GEL", "TOP"),
        ),
        (
            '"I saw a COP chasing a MAD dog near the BAM." Identify the three words that share 3-letter ISO 4217 currency codes.',
            ("COP", "MAD", "BAM"),
        ),
        (
            '"My friends BOB and RON took a CAB." Identify the two names that are actual 3-letter ISO 4217 currency codes.',
            ("BOB", "RON"),
        ),
        (
            'The words "GEL", "PEN", and "CUP" are household items. Which three countries use them as official currency codes?',
            ("GEL", "PEN", "CUP"),
        ),
        (
            '"Please MOP the floor and grab the hair GEL." What do MOP and GEL stand for in global finance?',
            ("MOP", "GEL"),
        ),
    ],
)
def test_explicit_currency_word_identification_uses_stable_local_reference(
    prompt: str, codes: tuple[str, ...]
) -> None:
    response = stable_currency_reference_response(prompt)

    assert response is not None
    assert tuple(line.split(" ", 1)[0] for line in response.splitlines()[1:]) == codes
    assert response.startswith("ISO 4217 currency codes:\n")


@pytest.mark.parametrize(
    "prompt",
    [
        "Please mop the floor and grab the hair gel.",
        "What does HTTP stand for?",
        "The PHP server in BSD is throwing an SOS.",
        '"I need to CAD a design for a new RUB." What do CAD and RUB mean in engineering software and global finance?',
        "Is my portfolio doing well financially?",
    ],
)
def test_ordinary_words_and_non_currency_acronyms_stay_model_owned(prompt: str) -> None:
    assert stable_currency_reference_response(prompt) is None


def test_explicit_plain_english_homograph_phrase_uses_ordinary_senses() -> None:
    prompt = (
        'If the word "TRY" means "Turkish Lira" and "ALL" means "Albanian Lek", '
        'what does "TRY ALL" mean in plain English? Do NOT trigger a currency lookup tool.'
    )

    assert stable_currency_reference_response(prompt) == (
        'TRY ALL means "attempt everything" in plain English.'
    )


def test_cancelled_forex_arithmetic_yields_only_requested_code_issuers() -> None:
    prompt = (
        '"I will TRY to MOP the floor, but I am MAD." How much is 100 TRY + 100 MOP in MAD? '
        "Just kidding, do NOT calculate forex rates. Just list the three countries that use "
        "those official currency codes."
    )

    assert stable_currency_reference_response(prompt) == (
        "ISO 4217 currency codes:\n"
        "TRY — Türkiye (Turkish lira)\n"
        "MOP — Macau (Macanese pataca)\n"
        "MAD — Morocco (Moroccan dirham)"
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Convert 100 TRY plus 100 MOP into MAD.",
        "Do not calculate 100 TRY in MAD.",
        "List files called TRY, MOP, and MAD from this directory.",
        "List countries in Europe, then calculate 100 TRY in MAD.",
        "Which countries use lira?",
    ],
)
def test_country_list_authority_does_not_claim_unbounded_or_effectful_requests(prompt: str) -> None:
    assert stable_currency_reference_response(prompt) is None


def test_cancelled_forex_country_list_preempts_model_tool_and_live_rate(
    tmp_path, monkeypatch
) -> None:
    from apps.vool_agent import VoolAgent

    prompt = (
        '"I will TRY to MOP the floor, but I am MAD." How much is 100 TRY + 100 MOP in MAD? '
        "Just kidding, do NOT calculate forex rates. Just list the three countries that use "
        "those official currency codes."
    )
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="currency-reference", persona_id="default")
    conductor = mock.Mock(side_effect=AssertionError("reference turn reached conductor"))
    model = mock.Mock(side_effect=AssertionError("reference turn reached model"))
    tool = mock.Mock(side_effect=AssertionError("reference turn reached tool"))
    fetch = mock.Mock(side_effect=AssertionError("reference turn reached live FX"))
    monkeypatch.setattr(agent, "_maybe_answer_conductor_turn", conductor)
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        prompt,
        session_id_override="openclaw:set5currencyreference",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
            "fx_fetch_json": fetch,
        },
    )

    assert result["route_reason"] == "stable_currency_reference_contract"
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert "TRY — Türkiye" in result["response"]
    assert "MOP — Macau" in result["response"]
    assert "MAD — Morocco" in result["response"]
    assert "×" not in result["response"]
    conductor.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()
    fetch.assert_not_called()
