"""A late probe must neither replace wire evidence nor fake session isolation."""
import threading

import pytest

from tests.test_c18_language_parity_served import CountingProvider


def _provider(calls):
    provider = CountingProvider.__new__(CountingProvider)
    provider._lock = threading.Lock()
    provider.answer_marker = "You are Atlas"
    provider.calls = calls
    return provider


def _call(prompt="question", system="You are Atlas", path="/api/chat", model="local"):
    return dict(prompt=prompt, system=system, path=path, model=model)


def test_late_metadata_and_auxiliary_calls_cannot_replace_answer_evidence():
    answer = _call(system="You are Atlas. Respond in German.")
    provider = _provider([answer, _call(prompt="", system="", path="/api/show"),
                          _call(prompt="judge this", system="judge"), _call(model="other")])
    assert provider.wire_text("local") == "You are Atlas. Respond in German.\nquestion"
    assert provider.answer_calls_for("local") == [answer]


def test_missing_policy_stays_missing_instead_of_selecting_an_older_good_answer():
    provider = _provider([_call(system="You are Atlas. Respond in Japanese."), _call()])
    assert "Respond in Japanese" not in provider.wire_text("local")
    assert "Respond in Japanese" in provider.wire_text("local", 0)


def test_probes_alone_cannot_satisfy_an_answer_assertion():
    provider = _provider([_call(prompt="", system="", path="/api/show")])
    with pytest.raises(AssertionError, match="No answer request"):
        provider.wire_text("local")


def test_background_probe_cannot_fake_a_session_without_the_leaked_preference():
    provider = _provider([_call(system="You are Atlas. Respond in Lithuanian."),
                          _call(system="You are Atlas. Respond in Lithuanian."),
                          _call(prompt="", system="", path="/api/show")])
    calls = provider.answer_calls_for("local")
    assert len(calls) == 2
    assert all("Respond in Lithuanian" in call["system"] for call in calls)
