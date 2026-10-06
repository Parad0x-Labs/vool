"""A later turn supersedes only what the SAME speaker said.

A relayed multi-party conversation is often stored as one user stream: every
turn is a "user" record and the participant is known only from the stored
speaker column or the reported source label heading the turn ("Marta: ...").
Temporal supersession treated the whole stream as one speaker, so one
participant's later statement withdrew another participant's fact about
their own life (measured: 25 cross-speaker withdrawals across five
question/query arms of a zero-spend capsule replay).

The law: slot identity is per speaker. A record supersedes another only when
both are attributed to the same speaker; unattributed records keep the
existing laws; one speaker's own update still supersedes their older value.

All sentences are authored for this contract (lockers, a ferry, a choir),
not copied from any benchmark text.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.temporal_selection import (
    AsOfIntent,
    TemporalCandidate,
    _build_slots,
    apply_temporal_selection,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _row(key: str, body: str, at: str, speaker: str = "", seq: int | None = None) -> dict:
    # Mapping form: the selection contract's documented input shape.
    return {"key": key, "body": body, "role": "user", "statement_at": _ts(at),
            "speaker": speaker, "seq": seq}


def _current(rows, question: str):
    return apply_temporal_selection(rows, intent=AsOfIntent(), question=question, now_utc=NOW)


def _past(rows, question: str):
    return apply_temporal_selection(rows, intent=AsOfIntent(), past_only=True,
                                    question=question, now_utc=NOW)


# ── two speakers' conflicting values both survive ─────────────────────────


def test_two_speakers_conflicting_values_both_survive_current_ask():
    rows = [
        _row("marta", "My locker code is 4417 at the rowing club.", "2026-05-02T09:00:00", "Marta"),
        _row("ivo", "My locker code is 9023 at the rowing club.", "2026-05-09T09:00:00", "Ivo"),
    ]
    verdicts = _current(rows, "What is the locker code at the rowing club?")
    assert verdicts["marta"].eligible, verdicts["marta"]
    assert verdicts["ivo"].eligible, verdicts["ivo"]
    assert verdicts["marta"].superseded_by is None


def test_another_speakers_correction_marker_withdraws_nothing_past_ask():
    rows = [
        _row("marta", "The ferry I took to the island left at 07:40.", "2026-05-02T09:00:00", "Marta"),
        _row("ivo", "Actually the ferry I took to the island left at 09:15.", "2026-05-09T09:00:00", "Ivo"),
    ]
    verdicts = _past(rows, "When did the ferry to the island leave?")
    assert verdicts["marta"].eligible, verdicts["marta"]
    assert verdicts["marta"].reason != "superseded-by-correction"
    assert verdicts["ivo"].eligible


def test_unattributed_bridge_cannot_join_two_speakers_into_one_slot():
    rows = [
        _row("marta", "Choir rehearsal for me is on Tuesday in the old hall.", "2026-05-02T09:00:00", "Marta"),
        _row("bridge", "Choir rehearsal notes: the old hall has a new piano.", "2026-05-05T09:00:00", ""),
        _row("ivo", "Choir rehearsal for me is on Thursday in the old hall.", "2026-05-09T09:00:00", "Ivo"),
    ]
    cands = [TemporalCandidate(key=r["key"], body=r["body"], statement_at=r["statement_at"],
                               speaker=r["speaker"]) for r in rows]
    for members in _build_slots(cands).values():
        speakers = {cands[i].speaker for i in members if cands[i].speaker}
        assert len(speakers) <= 1, [cands[i].key for i in members]
    verdicts = _current(rows, "When is choir rehearsal in the old hall?")
    assert verdicts["marta"].eligible and verdicts["ivo"].eligible


# ── one speaker's own update still supersedes their older value ───────────


def test_same_speaker_update_still_supersedes_older_value():
    rows = [
        _row("old", "My locker code is 4417 at the rowing club.", "2026-05-02T09:00:00", "Marta"),
        _row("other", "My locker code is 9023 at the rowing club.", "2026-05-05T09:00:00", "Ivo"),
        _row("new", "My locker code is 5520 at the rowing club now.", "2026-05-09T09:00:00", "Marta"),
    ]
    verdicts = _current(rows, "What is the locker code at the rowing club?")
    assert not verdicts["old"].eligible
    assert verdicts["old"].superseded_by == "new"
    assert verdicts["new"].eligible
    assert verdicts["other"].eligible


def test_same_speaker_correction_still_withdraws_past_ask():
    rows = [
        _row("old", "The ferry I took to the island left at 07:40.", "2026-05-02T09:00:00", "Marta"),
        _row("fix", "Actually the ferry I took to the island left at 09:15.", "2026-05-09T09:00:00", "Marta"),
    ]
    verdicts = _past(rows, "When did the ferry to the island leave?")
    assert not verdicts["old"].eligible
    assert verdicts["old"].reason == "superseded-by-correction"
    assert verdicts["fix"].eligible


def test_speaker_identity_ignores_case_spacing_and_label_colon():
    rows = [
        _row("old", "My locker code is 4417 at the rowing club.", "2026-05-02T09:00:00", "Marta  Lind"),
        _row("new", "My locker code is 5520 at the rowing club now.", "2026-05-09T09:00:00", "marta lind:"),
    ]
    verdicts = _current(rows, "What is the locker code at the rowing club?")
    assert not verdicts["old"].eligible
    assert verdicts["old"].superseded_by == "new"


def test_unattributed_records_keep_existing_supersession():
    rows = [
        _row("old", "My locker code is 4417 at the rowing club.", "2026-05-02T09:00:00"),
        _row("new", "My locker code is 5520 at the rowing club now.", "2026-05-09T09:00:00"),
    ]
    verdicts = _current(rows, "What is the locker code at the rowing club?")
    assert not verdicts["old"].eligible
    assert verdicts["old"].superseded_by == "new"


def test_unattributed_user_correction_still_reaches_an_attributed_relay():
    # The user's own unlabelled correction of a relayed line is the user's
    # statement about that subject: an unattributed record is compatible
    # with every speaker, so it still supersedes.
    rows = [
        _row("relay", "The ferry to the island leaves at 07:40.", "2026-05-02T09:00:00", "Marta"),
        _row("fix", "Actually the ferry to the island leaves at 09:15.", "2026-05-09T09:00:00", ""),
    ]
    verdicts = _past(rows, "When does the ferry to the island leave?")
    assert not verdicts["relay"].eligible
    assert verdicts["relay"].superseded_by == "fix"


# ── through real ingestion: the harness-shaped "Name:" store_turn path ─────

import pytest  # noqa: E402

from core import context_retrieval as cr  # noqa: E402
from core import temporal_selection as ts  # noqa: E402
from core.context_namespace import ensure_chat_namespace  # noqa: E402
from core.memory.entries import resolve_memory_access_policy  # noqa: E402
from tests.test_requested_source_authority_contract import _capsule, source_home  # noqa: E402,F401

CHAT = "relayed-two-speaker-transcript"


def _store(home, body: str, stated: str) -> str:
    receipt = cr.store_turn(
        CHAT, body, "", access_policy=resolve_memory_access_policy(chat_id=CHAT),
        source_context={"runtime_home": home, "statement_at": _ts(stated)})
    assert receipt["status"] in {"stored", "retained"}, receipt
    return receipt["occurrence_ids"][0]


def _capture(monkeypatch):
    owner = ts.apply_temporal_selection
    seen: dict = {}
    speakers: dict = {}

    def capture(candidates, **kwargs):
        for cand in candidates:
            speakers[cand.key] = cand.speaker
        result = owner(candidates, **kwargs)
        seen.update(result)
        return result

    monkeypatch.setattr(ts, "apply_temporal_selection", capture)
    return seen, speakers


def test_reported_label_scopes_supersession_from_real_ingestion(source_home, monkeypatch):
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    marta = _store(source_home, "Session date: 9:00 am on 2 May, 2026\n"
                   "Marta: My locker code at the rowing club is 4417.", "2026-05-02T09:00:00")
    ivo = _store(source_home, "Session date: 9:00 am on 9 May, 2026\n"
                 "Ivo: My locker code at the rowing club is 9023.", "2026-05-09T09:00:00")
    verdicts, speakers = _capture(monkeypatch)
    capsule = _capsule(source_home, "What is the locker code at the rowing club?", chat=CHAT)
    assert speakers.get(marta) == "Marta" and speakers.get(ivo) == "Ivo", speakers
    assert verdicts[marta].eligible, verdicts[marta]
    assert verdicts[ivo].eligible, verdicts[ivo]
    assert "4417" in capsule and "9023" in capsule, capsule


def test_same_label_update_still_supersedes_from_real_ingestion(source_home, monkeypatch):
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    old = _store(source_home, "Session date: 9:00 am on 2 May, 2026\n"
                 "Marta: My locker code at the rowing club is 4417.", "2026-05-02T09:00:00")
    new = _store(source_home, "Session date: 9:00 am on 9 May, 2026\n"
                 "Marta: My locker code at the rowing club is 5520 now.", "2026-05-09T09:00:00")
    verdicts, speakers = _capture(monkeypatch)
    capsule = _capsule(source_home, "What is the locker code at the rowing club?", chat=CHAT)
    assert speakers.get(old) == speakers.get(new) == "Marta", speakers
    assert not verdicts[old].eligible and verdicts[old].superseded_by == new, verdicts[old]
    assert "5520" in capsule and "4417" not in capsule, capsule


def test_register_header_label_is_not_a_speaker(source_home, monkeypatch):
    # "Update:" heads the user's own line; it attributes the record to
    # nobody, so the user's update supersedes their earlier note.
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    old = _store(source_home, "Note: my locker code at the rowing club is 4417.", "2026-05-02T09:00:00")
    new = _store(source_home, "Update: my locker code at the rowing club is 5520 now.", "2026-05-09T09:00:00")
    verdicts, speakers = _capture(monkeypatch)
    _capsule(source_home, "What is the locker code at the rowing club?", chat=CHAT)
    assert speakers.get(old) == "" and speakers.get(new) == "", speakers
    assert not verdicts[old].eligible and verdicts[old].superseded_by == new, verdicts[old]


def test_occurrence_only_records_carry_their_speaker(source_home, monkeypatch):
    # Records the importance gate keeps out of the node store reach temporal
    # selection only through the occurrence leg; their speaker must ride
    # that leg too (most relayed transcript turns have no node).
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    marta = _store(source_home, "Session date: 9:00 am on 2 May, 2026\n"
                   "Marta: The rowing club locker by the east window is the one I use.", "2026-05-02T09:00:00")
    ivo = _store(source_home, "Session date: 9:00 am on 9 May, 2026\n"
                 "Ivo: The rowing club locker by the west door is the one I use.", "2026-05-09T09:00:00")
    owner = ts.apply_temporal_selection
    seen: dict = {}
    nodes: dict = {}

    def capture(candidates, **kwargs):
        for cand in candidates:
            seen[cand.key] = cand.speaker
            nodes[cand.key] = cand.has_node
        return owner(candidates, **kwargs)

    monkeypatch.setattr(ts, "apply_temporal_selection", capture)
    _capsule(source_home, "Which rowing club locker do they have?", chat=CHAT)
    assert nodes.get(marta) is False and nodes.get(ivo) is False, nodes
    assert seen.get(marta) == "Marta" and seen.get(ivo) == "Ivo", seen
