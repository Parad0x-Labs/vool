"""A done claim never stands because the judge that reads it was slow.

Fresh-question audit on 02ff11496 (qwen3:8b on a slow CPU box): the tool ask timed out, which opened the judge's
breaker, so the done-claim judgement was skipped too. "I've noted to ping you this Friday at 4:00 PM" and "Got it, the
vitamins reminder is scrapped" reached the person with nothing set or cancelled. The done-claim judgement is now
patient (asked through an open breaker, with the load timeout), and when it still gets no answer the reply says
plainly that nothing was changed. Replies and languages are drawn at random; the judge's transport is a stand-in.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter
from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

REPLIES = (
    ("I've noted to ping you this Friday at 4:00 PM to give the drill back to Kasper.", "also ping me friday 4pm about the drill", "en"),
    ("Got it — the vitamins reminder is scrapped.", "scrap the vitamins one", "en"),
    ("Erledigt, die Erinnerung an den Zahnarzt ist gelöscht.", "lösch die Zahnarzt-Erinnerung", "de"),
    ("Gerai, priminimą apie šiukšles atšaukiau.", "atšauk priminimą apie šiukšles", "lt"),
    ("Listo, te avisaré el sábado a las diez.", "avísame el sábado a las diez", "es"),
)


class _Timeout(Exception):
    pass


_Timeout.__name__ = "ReadTimeout"


@pytest.fixture(autouse=True)
def _local_judge(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_INTENT_ARBITER", raising=False)
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "qwen3:8b")
    intent_arbiter.bind_turn_judge({}, None)
    intent_arbiter.reset_breaker()
    yield
    intent_arbiter.reset_breaker()


def _post_with(answer: str | None, seen: list):
    def post(url, json=None, timeout=None, **_kw):
        seen.append(timeout)
        if answer is None:
            raise _Timeout("read timed out")

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": '{"answer": "%s"}' % answer}}

        return _Response()

    return post


def _turn(reply: str, ask: str, session: str, app_locale: str = "en") -> dict:
    from core.i18n.turn_locale import bind_turn_locale, reset_turn_locale

    # The person's app speaks their language, as the page binds it for a chat request.
    token = bind_turn_locale(app_locale)
    try:
        return enforce_final_action_honesty({"response": reply}, user_input=ask, session_id=session,
                                            source_context={"request_id": f"req-{session}"})
    finally:
        reset_turn_locale(token)


@pytest.mark.parametrize("seed", range(5))
def test_a_judge_that_gives_no_answer_leaves_a_plain_nothing_changed_line(seed, monkeypatch) -> None:
    import requests

    from core.i18n.catalog import catalog_for

    reply, ask, lang = random.Random(f"slow-judge-{seed}").choice(REPLIES)
    seen: list = []
    monkeypatch.setattr(requests, "post", _post_with(None, seen))
    intent_arbiter._trip_breaker("qwen3:8b")   # the tool ask's timeout opened it, as on the audit box

    out = _turn(reply, ask, f"slow-{seed}", app_locale=lang)

    assert seen, "the done claim was never asked of the judge"
    assert seen[-1][1] >= intent_arbiter._LOAD_TIMEOUT_S, seen
    note = catalog_for(lang).text("chat.action_unchecked_nothing_changed")
    assert out["response"].startswith(reply) and out["response"].endswith(note), out["response"]


def test_a_judge_that_answers_done_through_an_open_breaker_corrects_the_reply(monkeypatch) -> None:
    import requests

    reply, ask, _lang = REPLIES[0]
    monkeypatch.setattr(requests, "post", _post_with("done", []))
    intent_arbiter._trip_breaker("qwen3:8b")

    out = _turn(reply, ask, "slow-done")

    assert out["action_honesty_validator"]["claim_kind"] == "judged"
    assert reply not in out["response"]


def test_a_reply_the_judge_reads_as_no_claim_gets_no_line(monkeypatch) -> None:
    import requests

    monkeypatch.setattr(requests, "post", _post_with("none", []))
    reply = "A leap year has 366 days."

    assert _turn(reply, "how many days in a leap year", "slow-none")["response"] == reply


def test_with_no_judge_configured_nothing_is_added(monkeypatch) -> None:
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    reply = "A leap year has 366 days."

    assert _turn(reply, "how many days in a leap year", "slow-off")["response"] == reply


def test_the_judge_reads_a_promise_to_ping_later_as_a_reminder_set() -> None:
    prompt = intent_arbiter._done_claim_prompt("Noted, I'll ping you on Monday.")

    assert "A promise to remind, ping, alert or tell" in prompt


def test_the_language_the_person_wrote_in_beats_an_english_app(monkeypatch) -> None:
    import requests

    from core.i18n.catalog import catalog_for

    monkeypatch.setattr(requests, "post", _post_with(None, []))
    reply = "Gerai, priminimą apie šiukšles atšaukiau."
    out = _turn(reply, "Atšauk priminimą apie šiukšles, nes jų išvežimas perkeltas į ketvirtadienį.", "slow-lt-en-app")
    note = catalog_for("lt").text("chat.action_unchecked_nothing_changed")
    assert note != catalog_for("en").text("chat.action_unchecked_nothing_changed")
    assert out["response"].endswith(note), out["response"]
