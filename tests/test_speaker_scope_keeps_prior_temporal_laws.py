"""Verifier controls for speaker-scoped supersession: the prior laws hold.

Speaker-scoped slots and the marker-sentence rule narrow when a later turn
withdraws an earlier one. These controls pin what must NOT move with them:

1. a speaker's own real update or correction still withdraws their older
   statement, including when the correction is one sentence of several;
2. per-speaker slots do not flood a question's capsule with another
   speaker's off-topic turns;
3. a question that names no date has no as-of frame: no record is judged
   against an as-of instant, whatever the speakers; a dated question keeps
   its frame;
4. layer-2 admission still refuses pure chit-chat, labelled or not.

All sentences are authored for this contract (lockers, a ferry, a choir,
a bakery), not copied from any benchmark text.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core import context_retrieval as cr
from core import temporal_selection as ts
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.temporal_selection import (
    AsOfIntent,
    apply_temporal_selection,
    resolve_question_as_of,
)
from tests.test_requested_source_authority_contract import _capsule, source_home  # noqa: F401

UTC = timezone.utc
NOW = datetime(2026, 9, 1, tzinfo=UTC)
CHAT = "verifier-speaker-scope-controls"


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _row(key: str, body: str, at: str, speaker: str = "") -> dict:
    return {"key": key, "body": body, "role": "user", "statement_at": _ts(at),
            "speaker": speaker}


def _select(rows, question: str, *, past: bool = False, intent: AsOfIntent | None = None):
    return apply_temporal_selection(
        rows, intent=intent or AsOfIntent(), past_only=past,
        question=question, now_utc=NOW)


# ── 1. a real same-speaker update still supersedes ─────────────────────────


def test_same_speaker_multi_sentence_correction_still_withdraws_their_older_value():
    # The marker sentence is one of three and shares the subject: a real
    # correction by its own speaker still withdraws their older value.
    rows = [
        _row("marta_old", "My locker code at the rowing club is 4417.", "2026-05-02T09:00:00", "Marta"),
        _row("marta_new",
             "Busy week at work. Actually my locker code at the rowing club is 5520 now. "
             "Dinner is at eight.",
             "2026-05-09T09:00:00", "Marta"),
    ]
    verdicts = _select(rows, "What is the locker code at the rowing club?")
    assert not verdicts["marta_old"].eligible, verdicts["marta_old"]
    assert verdicts["marta_old"].superseded_by == "marta_new"
    assert verdicts["marta_new"].eligible


def test_same_speaker_value_free_correction_about_the_subject_still_withdraws():
    # No value on either side: the marker alone decides, and its sentence
    # is about the slot-mate's subject, so the older statement goes.
    rows = [
        _row("old", "Choir rehearsal for me is on Tuesday in the old hall.", "2026-05-02T09:00:00", "Marta"),
        _row("new", "Long day. The choir rehearsal moved to Thursday for me.", "2026-05-09T09:00:00", "Marta"),
    ]
    verdicts = _select(rows, "When is choir rehearsal in the old hall?")
    assert not verdicts["old"].eligible, verdicts["old"]
    assert verdicts["old"].superseded_by == "new"
    assert verdicts["new"].eligible


# ── 2. no flood of another speaker's off-topic turns ───────────────────────


def _store(home, body: str, stated: str) -> str:
    receipt = cr.store_turn(
        CHAT, body, "", access_policy=resolve_memory_access_policy(chat_id=CHAT),
        source_context={"runtime_home": home, "statement_at": _ts(stated)})
    assert receipt["status"] in {"stored", "retained"}, receipt
    return receipt["occurrence_ids"][0]


OFF_TOPIC = [
    ("Ivo: The ferry to the island leaves at 07:40 on weekdays.", "2026-05-03T09:00:00"),
    ("Ivo: The bakery on the corner sells rye loaves on Saturdays.", "2026-05-04T09:00:00"),
    ("Marta: Choir rehearsal for me is on Tuesday in the old hall.", "2026-05-06T09:00:00"),
    ("Ivo: My bicycle needs a new rear tyre before the summer.", "2026-05-07T09:00:00"),
]


def test_other_speakers_off_topic_turns_do_not_flood_the_capsule(source_home):
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    _store(source_home, "Marta: My locker code at the rowing club is 4417.", "2026-05-02T09:00:00")
    for body, stated in OFF_TOPIC:
        _store(source_home, body, stated)
    _store(source_home, "Ivo: My locker code at the rowing club is 9023.", "2026-05-09T09:00:00")
    capsule = _capsule(source_home, "What is the locker code at the rowing club?", chat=CHAT)
    assert "9023" in capsule, capsule
    for marker in ("ferry", "07:40", "rye loaves", "rear tyre"):
        assert marker not in capsule, (marker, capsule)


# ── 3. no as-of frame on a question without a date ────────────────────────


AS_OF_REASONS = {"future-relative-to-as-of", "window-expired-before-as-of"}


def test_an_undated_question_has_no_as_of_frame_whatever_the_speakers():
    question = "What is the locker code at the rowing club?"
    intent = resolve_question_as_of(question, now_utc=NOW)
    assert not intent.present and intent.origin == ""
    rows = [
        _row("marta", "My locker code at the rowing club is 4417.", "2026-05-02T09:00:00", "Marta"),
        _row("ivo", "My locker code at the rowing club is 9023.", "2026-05-09T09:00:00", "Ivo"),
        _row("plain", "My locker code at the rowing club is 1180.", "2026-05-12T09:00:00"),
    ]
    verdicts = _select(rows, question, intent=intent)
    assert not {v.reason for v in verdicts.values()} & AS_OF_REASONS, verdicts
    assert all(v.reason != "future-relative-to-now" for v in verdicts.values())


def test_a_dated_question_keeps_its_as_of_frame_across_speakers():
    question = "As of May 5 2026, what was the locker code at the rowing club?"
    intent = resolve_question_as_of(question, now_utc=NOW)
    assert intent.present and intent.origin == "question-text"
    rows = [
        _row("marta", "My locker code at the rowing club is 4417.", "2026-05-02T09:00:00", "Marta"),
        _row("ivo_later", "My locker code at the rowing club is 9023.", "2026-05-09T09:00:00", "Ivo"),
    ]
    verdicts = _select(rows, question, intent=intent)
    assert verdicts["marta"].eligible, verdicts["marta"]
    assert not verdicts["ivo_later"].eligible
    assert verdicts["ivo_later"].reason == "future-relative-to-as-of", verdicts["ivo_later"]


# ── 4. admission still refuses pure chit-chat ──────────────────────────────


CHIT_CHAT = [
    "haha yes totally, same here honestly lol",
    "Marta: haha yes totally!! same here :)",
    "Ivo: ok cool, talk later then, have a good one",
]


def test_admission_scores_pure_chit_chat_below_the_gate():
    for body in CHIT_CHAT:
        assert cr._score_importance(body) < cr._IMPORTANCE_THRESHOLD, body
    assert cr._score_importance(
        "Marta: My locker code at the rowing club is 4417.") >= cr._IMPORTANCE_THRESHOLD


def _node_contents(home: str) -> list[str]:
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=home)
    try:
        return [str(row["content"]) for row in mem._conn.execute(
            "SELECT content FROM memory_nodes ORDER BY timestamp")]
    finally:
        mem.close()


def test_stored_chit_chat_is_retained_but_not_admitted_to_the_index(source_home):
    # Layer 1 keeps every turn byte-for-byte; layer-2 admission (the
    # semantic index) refuses pure chit-chat, labelled or not, while the
    # labelled fact is admitted.
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    _store(source_home, "Marta: My locker code at the rowing club is 4417.", "2026-05-02T09:00:00")
    for i, body in enumerate(CHIT_CHAT):
        _store(source_home, body, f"2026-05-0{3 + i}T09:00:00")
    nodes = _node_contents(source_home)
    assert any("4417" in n for n in nodes), nodes
    for marker in ("haha", "talk later", "lol"):
        assert not any(marker in n for n in nodes), (marker, nodes)
    capsule = _capsule(source_home, "What is the locker code at the rowing club?", chat=CHAT)
    assert "4417" in capsule, capsule
