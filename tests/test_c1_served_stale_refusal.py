"""AUD-20260829-003, C1 — EUR->RUB uses CURRENT FX data.

The committed frontdoor test pins the good half: an opted-in public source, `grounded ==
"live_rate"`, the served date. It does not pin the half the criterion is actually about --
what happens when the only rate available is STALE. A currency lane that silently serves a
four-day-old rate satisfies "used a public source" and fails "uses current FX data".

`fx.py` has the whole contract already: a `STALE` status, an `available` property that requires
it not be stale, a `convert()` that raises on an unusable quote, a typed retrieval receipt, and
a named decline that says out loud it will not substitute training data. This drives it.

The injection seam is two `source_context` keys, `fx_fetch_json` and `fx_now`, which the
frontdoor hands to `FrankfurterFxProvider`. Note the trap they carry: a MISSPELLED key falls
through to the real fetcher and opens a socket, so these tests assert the stub was called --
a silent live call would otherwise look like a pass.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
#: `stale_after_seconds` is 4 days, so a quote observed 30 days ago is unusable by any reading.
STALE_DATE = (NOW - timedelta(days=30)).date().isoformat()
FRESH_DATE = (NOW - timedelta(days=1)).date().isoformat()


def _web_enabled():
    return mock.patch("core.policy_engine.allow_web_fallback", return_value=True)


def _reply(date: str):
    from core.agent_runtime.turn_frontdoor import _currency_reply

    fetcher = mock.Mock(return_value={"date": date, "rate": "99.8"})
    with _web_enabled():
        reply = _currency_reply(
            "1000 EUR to RUB",
            session_id="c1-stale-refusal",
            source_context={
                "conversation_history": [],
                "allow_remote_fetch": True,
                "fx_fetch_json": fetcher,
                "fx_now": lambda: NOW,
            },
        )
    return reply, fetcher


def test_a_fresh_quote_converts_and_says_it_is_live():
    """CONTROL. Without it a refusal test passes on any breakage at all."""
    reply, fetcher = _reply(FRESH_DATE)
    fetcher.assert_called_once()
    assert reply is not None
    assert reply["grounded"] == "live_rate"
    assert "99800" in reply["response"].replace(",", "").replace(" ", "")


def test_a_stale_quote_converts_nothing_and_says_why():
    """THE CRITERION. A 30-day-old rate is not current FX data, and the runtime must decline
    rather than convert with it. The rate is present in the payload -- declining is a choice
    the lane makes about freshness, not an absence of data."""
    reply, fetcher = _reply(STALE_DATE)
    fetcher.assert_called_once(), "the stub was bypassed; a live socket would fake this pass"
    assert reply is not None
    assert reply["grounded"] == "no_rate_declined", reply
    body = reply["response"]
    # Nothing was converted: the product of a stale rate never reaches the reader.
    assert "99800" not in body.replace(",", "").replace(" ", ""), body
    assert "99.8" not in body, body
    # And the decline says what it is declining to do.
    assert "stale" in body.lower() or "guess" in body.lower(), body


def test_the_stale_refusal_is_not_a_transport_failure():
    """The distinction the receipt exists to make: the fetch SUCCEEDED and the quote was
    rejected on age. A lane that reports 'unavailable' for both cannot be audited."""
    from core.fresh_data.fx import FrankfurterFxProvider, FxQuoteStatus

    fetcher = mock.Mock(return_value={"date": STALE_DATE, "rate": "99.8"})
    quote = FrankfurterFxProvider(fetch_json=fetcher, now=lambda: NOW).quote("EUR", "RUB")
    assert quote.status is FxQuoteStatus.STALE, quote
    assert quote.rate is not None, "a stale quote still carries the rate it declined to use"
    assert quote.available is False
    # The unusable quote raises rather than converting -- the honest 'cannot', in code.
    import pytest

    with pytest.raises(Exception):
        quote.convert(1000)
