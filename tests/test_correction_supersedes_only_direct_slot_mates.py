"""A correction replaces what it is about, not everything its slot chained to.

A marker statement ("actually", "scratch that", "recount") joins every earlier
chain it shares a word with, and slot building is transitive: one shared word
with a bridge record pulls in every record the bridge overlaps. The correction
winner then superseded every value-bearing record in that whole union, so a
value-less "I actually stayed at the cabin" withdrew an unrelated race time
two hops away (measured on an exposed holdout case: 19 records superseded,
the decisive operand among them).

The law: a correction winner supersedes only slot members DIRECTLY tied to it
by the join law (a shared subject token, unit word, content bigram, value or
morphological kin). Direct corrections and retractions keep superseding.

All sentences below are authored for this contract (a choir, a cabin, a
ferry, a potluck), not copied from any benchmark text.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.temporal_selection import (
    AsOfIntent,
    TemporalCandidate,
    apply_temporal_selection,
)

UTC = timezone.utc


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _cand(key: str, body: str, at: str, *, role: str = "user") -> TemporalCandidate:
    return TemporalCandidate(key=key, body=body, role=role, statement_at=_ts(at))


def _past(cands, question: str):
    return apply_temporal_selection(
        cands, intent=AsOfIntent(), past_only=True, question=question,
        now_utc=datetime(2026, 9, 1, tzinfo=UTC))


def _current(cands, question: str):
    return apply_temporal_selection(
        cands, intent=AsOfIntent(), question=question,
        now_utc=datetime(2026, 9, 1, tzinfo=UTC))


# A three-record chain: the race record shares nothing with the cabin
# correction; the bridge correction shares "choir concert" with the race
# record's neighbour and "library" with the cabin statement.
_CHAIN = [
    _cand("race", "My choir concert warm-up was a 10K run that took me 58 minutes.",
          "2026-05-02T09:00:00"),
    _cand("bridge", "Actually, the choir concert moved into the old library hall.",
          "2026-05-09T09:00:00"),
    _cand("cabin", "I actually stayed at the lakeside cabin after the library reading "
          "with my cousins.", "2026-05-16T09:00:00"),
]


def test_value_less_correction_does_not_withdraw_a_record_linked_only_through_the_chain() -> None:
    v = _past(_CHAIN, "How long did my 10K run take?")
    # the three records form one slot through the bridge (the precondition)
    assert v["race"].slot == v["cabin"].slot == v["bridge"].slot
    assert v["race"].eligible, v["race"]
    assert v["race"].reason == "historical-attributed"
    assert v["race"].superseded_by is None


def test_chain_linked_record_survives_a_current_ask_too() -> None:
    v = _current(_CHAIN, "How long does my 10K run take?")
    assert v["race"].eligible, v["race"]


def test_direct_correction_still_supersedes_on_past_ask() -> None:
    v = _past([
        _cand("old", "The ferry to the island leaves at 9:10.", "2026-04-01T08:00:00"),
        _cand("fix", "Actually the ferry to the island leaves at 9:40.", "2026-04-03T08:00:00"),
    ], "When did the ferry to the island leave?")
    assert v["fix"].eligible and v["fix"].reason == "slot-winner"
    assert not v["old"].eligible and v["old"].reason == "superseded-by-correction"


def test_single_word_retraction_still_withdraws_its_subject() -> None:
    v = _past([
        _cand("plan", "I'll bring 2 trays of lasagna to the potluck.", "2026-04-01T08:00:00"),
        _cand("undo", "Scratch that, forget the lasagna.", "2026-04-02T08:00:00"),
    ], "What did I say I would bring?")
    assert not v["plan"].eligible and v["plan"].reason == "superseded-by-correction"

