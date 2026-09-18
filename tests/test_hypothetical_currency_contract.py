from __future__ import annotations

import pytest

from core.hypothetical_currency_contract import hypothetical_currency_response


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        (
            'Assume it is the year 2085. Its currency is now "Dingo-Dollar" '
            "(pegged at 1 Dingo-Dollar = 5 Euro). If I buy water for 10 Euros, how many "
            "Dingo-Dollars did I spend? Explain why a live web search tool will fail to answer this.",
            "2 Dingo-Dollar",
        ),
        (
            'Assume it is 2099. The UK uses the "Cyber-Pound" (CP) pegged at 1 CP = 10 USD. '
            "If I buy coffee for 50 USD, how many CP did I spend? Explain why using a live web "
            "search tool will fail to answer this accurately.",
            "5 Cyber-Pound (CP)",
        ),
        (
            'Assume it is 3000. Its currency is the "Cred" (pegged at 1 Cred = 100 USD). '
            "If I buy a hoverboard for 500 USD, how many Creds did I spend? Explain why "
            "triggering a live web search for this will fail.",
            "5 Cred",
        ),
    ),
)
def test_user_stipulated_future_currency_math_is_closed_and_search_free(
    prompt: str, expected: str
) -> None:
    response = hypothetical_currency_response(prompt)
    assert response is not None
    assert expected in response
    assert "premise the user stipulated" in response


@pytest.mark.parametrize(
    "prompt",
    (
        "Convert 50 USD to EUR using today's rate.",
        'Assume a currency called "Cred" exists. What is its current market price?',
        'Assume the currency is "Cred" pegged at 1 Cred = 10 USD. Buy coffee for 20 EUR.',
        'The currency is "Cred" pegged at 1 Cred = 10 USD. I buy coffee for 20 USD.',
    ),
)
def test_live_incomplete_or_non_stipulated_near_misses_are_not_claimed(prompt: str) -> None:
    assert hypothetical_currency_response(prompt) is None
