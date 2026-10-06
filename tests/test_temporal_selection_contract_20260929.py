"""The temporal eligibility/selection contract — unit rows.

REGRESSION rows reproduce the saved failure shapes measured on the frozen
candidate 9174b42c (mission memory-quality90, 2026-09-29): corrections
skipped by the coverage law, as-of questions served every historical value,
retractions resurrecting after restart, decimal values severed at the point.

FRESH ACCEPTANCE rows were authored with different wording, entities, values,
dates and domains from every saved corpus case (a bicycle workshop, an apiary,
a climbing gym, a tea house — not the corpus's orchards, ferries and choirs)
and from each other. If the contract changes after a fresh-case failure, the
case stays as-is and a genuinely new confirmation case is added instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.temporal_selection import (
    AsOfIntent,
    TemporalCandidate,
    apply_temporal_selection,
    effective_time,
    resolve_question_as_of,
    value_tokens,
)

UTC = timezone.utc


def _cand(
    key: str,
    body: str,
    *,
    role: str = "user",
    statement_at: float | None = None,
    event_at: float | None = None,
    recorded_at: float | None = None,
    authority: str = "",
) -> TemporalCandidate:
    return TemporalCandidate(
        key=key, body=body, role=role, authority=authority,
        statement_at=statement_at, event_at=event_at, recorded_at=recorded_at,
    )


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _verdicts(cands, *, as_of=None, plumbed=None, question="", **kw):
    intent = AsOfIntent() if as_of is None and plumbed is None else (
        resolve_question_as_of(question, plumbed=plumbed)
    )
    if as_of is not None:
        intent = AsOfIntent(as_of_end=datetime.fromisoformat(as_of).replace(tzinfo=UTC))
    return apply_temporal_selection(cands, intent=intent, question=question, **kw)


def _eligible(v: dict, key: str) -> bool:
    verdict = v.get(key)
    return verdict is None or verdict.eligible


def _reason(v: dict, key: str) -> str:
    return v[key].reason if key in v else ""


# ---------------------------------------------------------------- regression
# Corpus shapes, different surface (kept minimal: the full end-to-end rows
# live in the seam file; here the LAWS are pinned on reduced inputs).


def test_regression_correction_supersedes_for_current_ask() -> None:
    v = _verdicts([
        _cand("old", "The workshop key safe code is 8841.", statement_at=_ts("2026-05-02T09:00:00")),
        _cand("new", "Correction: the safe code is 7730.", statement_at=_ts("2026-05-09T09:00:00")),
    ])
    assert _eligible(v, "new") and _reason(v, "new") == "slot-winner"
    assert not _eligible(v, "old")


def test_regression_retraction_wins_and_old_value_dies() -> None:
    v = _verdicts([
        _cand("commit", "Book the alpine hut for the solstice weekend.", statement_at=_ts("2026-06-01T10:00:00")),
        _cand("retract", "Scratch that — the solstice hut plan is off entirely.",
              statement_at=_ts("2026-06-03T10:00:00")),
    ])
    assert _eligible(v, "retract")
    assert not _eligible(v, "commit")


def test_regression_as_of_selects_the_value_that_applied() -> None:
    v = _verdicts([
        _cand("v1", "Apiary hive-rent is 15 kronor per season.", statement_at=_ts("2025-02-10T08:00:00")),
        _cand("v2", "Hive-rent goes to 22 kronor for the 2026 season.", statement_at=_ts("2026-02-08T08:00:00")),
    ], as_of="2025-11-30T00:00:00")
    assert _eligible(v, "v1") and _reason(v, "v1") == "slot-winner"
    assert not _eligible(v, "v2") and _reason(v, "v2") == "future-relative-to-as-of"


def test_regression_future_effective_record_excluded_before_date() -> None:
    v = _verdicts([
        _cand("now", "Day pass at the tea house is 4 zloty.", statement_at=_ts("2025-12-01T12:00:00")),
        _cand("later", "Notice: the day pass becomes 6 zloty from March 1.",
              statement_at=_ts("2026-01-20T12:00:00"), event_at=_ts("2026-03-01T00:00:00")),
    ], as_of="2026-02-10T00:00:00")
    assert _eligible(v, "now")
    assert not _eligible(v, "later")


def test_regression_window_expires_by_its_own_end_date() -> None:
    v = _verdicts([
        _cand("base", "A single gym session costs 9 euro.", statement_at=_ts("2026-01-05T18:00:00")),
        _cand("promo", "Open-wall week: sessions are free of charge from February 3 through February 9.",
              statement_at=_ts("2026-02-01T18:00:00")),
        _cand("back", "Open-wall week is over — the normal session price applies again.",
              statement_at=_ts("2026-02-10T18:00:00")),
    ], as_of="2026-02-15T00:00:00")
    assert _eligible(v, "base")
    assert not _eligible(v, "promo") and _reason(v, "promo") == "window-expired-before-as-of"
    assert _eligible(v, "back")


# ---------------------------------------------------------- fresh acceptance


def test_fresh_as_of_between_three_values_picks_middle() -> None:
    v = _verdicts([
        _cand("a", "Crane operator badge renewal costs 40 euro.", statement_at=_ts("2024-04-04T07:00:00")),
        _cand("b", "Badge renewal rises to 55 euro for the 2025 season.", statement_at=_ts("2025-04-02T07:00:00")),
        _cand("c", "Badge renewal rises again to 70 euro for the 2026 season.", statement_at=_ts("2026-04-01T07:00:00")),
    ], as_of="2025-08-18T00:00:00")
    assert _eligible(v, "b") and _reason(v, "b") == "slot-winner"
    assert not any(_eligible(v, k) for k in ("a", "c"))


def test_fresh_import_order_is_not_event_order() -> None:
    # The later-stated record describes the EARLIER event; the earlier-stated
    # record describes the later event. Effective order is event order.
    v = _verdicts([
        _cand("stated_late", "Per the harbor log: the mooring fee doubled to 30 euro on June 2.",
              statement_at=_ts("2026-06-20T09:00:00"), event_at=_ts("2026-06-02T00:00:00")),
        _cand("stated_early", "The mooring fee is 15 euro.",
              statement_at=_ts("2026-06-01T09:00:00"), event_at=_ts("2026-06-01T00:00:00")),
    ], as_of="2026-06-10T00:00:00")
    assert _eligible(v, "stated_late") and _reason(v, "stated_late") == "slot-winner"
    assert not _eligible(v, "stated_early")


def test_fresh_boundary_date_is_inclusive() -> None:
    v = _verdicts([
        _cand("old", "Filter coffee at the print-shop counter is 2.50 euro.",
              statement_at=_ts("2026-03-01T10:00:00")),
        _cand("new", "From October 7 the counter coffee is 3 euro.",
              statement_at=_ts("2026-10-01T10:00:00"), event_at=_ts("2026-10-07T00:00:00")),
    ], as_of="2026-10-07T00:00:00")
    assert _eligible(v, "new")
    assert not _eligible(v, "old")


def test_fresh_same_day_tie_resolved_by_statement_time() -> None:
    v = _verdicts([
        _cand("am", "Morning version: the loft door code is 1955.",
              statement_at=_ts("2026-08-11T08:15:00")),
        _cand("pm", "Evening correction from the landlord: the loft door code is 7241.",
              statement_at=_ts("2026-08-11T19:40:00")),
    ], as_of="2026-08-12T00:00:00")
    assert _eligible(v, "pm")
    assert not _eligible(v, "am")


def test_fresh_negation_flip_supersedes_for_current_ask() -> None:
    v = _verdicts([
        _cand("claim", "The tea house closes on Tuesdays.", statement_at=_ts("2026-07-02T16:00:00")),
        _cand("flip", "Scratch that — the tea house does not close on Tuesdays after all.",
              statement_at=_ts("2026-07-09T16:00:00")),
    ])
    assert _eligible(v, "flip") and _reason(v, "flip").startswith("slot-winner")
    assert not _eligible(v, "claim")


def test_fresh_terse_recount_supersedes_on_past_tense_ask_too() -> None:
    v = _verdicts([
        _cand("first", "The regatta entry list came to 84 boats.",
              statement_at=_ts("2026-05-05T11:00:00")),
        _cand("recount", "Recount says 91 boats.", statement_at=_ts("2026-05-06T08:30:00")),
    ], past_only=True, question="How many boats entered the regatta?")
    assert _eligible(v, "recount") and _reason(v, "recount") == "slot-winner"
    assert not _eligible(v, "first") and _reason(v, "first") == "superseded-by-correction"


def test_fresh_date_bound_update_survives_on_before_the_increase_ask() -> None:
    # A past-tense ask about the EARLIER value must keep it: only marker
    # corrections supersede globally, never date-bound updates.
    v = _verdicts([
        _cand("old", "The ropewalk tour was 6 euro.", statement_at=_ts("2026-03-03T10:00:00")),
        _cand("raise", "From June the ropewalk tour costs 9 euro.",
              statement_at=_ts("2026-05-28T10:00:00"), event_at=_ts("2026-06-01T00:00:00")),
    ], past_only=True, question="What did the ropewalk tour cost before the increase?")
    assert _eligible(v, "old") and _reason(v, "old") == "historical-attributed"
    assert _eligible(v, "raise")


def test_fresh_repeated_value_does_not_supersede() -> None:
    v = _verdicts([
        _cand("first", "The workshop closes at six.", statement_at=_ts("2026-04-01T09:00:00")),
        _cand("again", "Reminder for anyone asking: the workshop closes at six.",
              statement_at=_ts("2026-04-20T09:00:00")),
    ])
    assert _eligible(v, "first")
    assert _eligible(v, "again") and _reason(v, "again") == "slot-winner"


def test_fresh_unknown_dates_never_filter_and_never_supersede() -> None:
    v = _verdicts([
        _cand("known", "The observatory open evening is ticketed at 12 euro.",
              statement_at=_ts("2026-02-02T20:00:00")),
        _cand("unknown", "Correction — the open evening is ticketed at 15 euro.",
              statement_at=None, event_at=None, recorded_at=None),
    ], as_of="2026-03-01T00:00:00")
    # Unknown stays eligible alongside the dated winner; it never wins by
    # order and never drops the dated record.
    assert _eligible(v, "known")
    assert _eligible(v, "unknown")


def test_fresh_assistant_echo_of_dead_value_is_not_state() -> None:
    v = _verdicts([
        _cand("v1", "Dawn patrol sessions start at 06:15.", statement_at=_ts("2026-01-10T05:00:00")),
        _cand("v1_ack", "06:15 start — noted.", role="assistant", authority="assistant-output",
              statement_at=_ts("2026-01-10T05:00:20")),
        _cand("v2", "Correction — dawn patrol moves to 06:45 for the dark months.",
              statement_at=_ts("2026-11-02T05:00:00")),
        _cand("v2_ack", "06:45 it is.", role="assistant", authority="assistant-output",
              statement_at=_ts("2026-11-02T05:00:20")),
    ])
    assert _eligible(v, "v2") and _reason(v, "v2") == "slot-winner"
    assert not _eligible(v, "v1")
    assert not _eligible(v, "v1_ack") and _reason(v, "v1_ack") == "echo-of-superseded"
    # An echo of the WINNING value carries no dead value and stays harmless.
    assert _eligible(v, "v2_ack")


def test_fresh_question_never_wins_the_slot_from_its_answer() -> None:
    v = _verdicts([
        _cand("q", "How many kilns does the pottery guild run?", statement_at=_ts("2026-09-01T10:00:00")),
        _cand("a", "From the guild register: the pottery guild runs four kilns, and the new fifth kiln arrives in October.",
              role="assistant", authority="assistant-output",
              statement_at=_ts("2026-09-01T10:00:30")),
    ])
    assert _eligible(v, "a")
    assert "q" not in v  # questions do not participate in state resolution


def test_fresh_assistant_history_ask_keeps_assistant_statement() -> None:
    v = _verdicts([
        _cand("u", "What did the tide table say about the causeway window?",
              statement_at=_ts("2026-08-08T07:30:00")),
        _cand("a", "The causeway window that day was 14:10 to 16:40.",
              role="assistant", authority="assistant-output",
              statement_at=_ts("2026-08-08T07:30:20")),
    ], asks_assistant_history=True)
    assert _eligible(v, "a")


def test_fresh_double_correction_lands_on_the_final_value() -> None:
    v = _verdicts([
        _cand("orig", "The mail boat leaves the jetty at 07:40.",
              statement_at=_ts("2026-04-02T06:00:00")),
        _cand("corr", "Correction — it leaves at 08:10.", statement_at=_ts("2026-04-03T06:00:00")),
        _cand("recant", "Ignore that correction, 07:40 was right all along.",
              statement_at=_ts("2026-04-04T06:00:00")),
    ])
    assert _eligible(v, "recant") and _reason(v, "recant") == "slot-winner"
    assert not _eligible(v, "corr")
    # The original value agrees with the winner — it coexists.
    assert _eligible(v, "orig")


def test_fresh_distinct_aspects_of_one_subject_coexist() -> None:
    v = _verdicts([
        _cand("dur", "The causeway crossing takes 25 minutes at low water.",
              statement_at=_ts("2026-05-05T09:00:00")),
        _cand("price", "The causeway crossing costs 3 euro on foot.",
              statement_at=_ts("2026-05-06T09:00:00")),
    ])
    assert _eligible(v, "dur") and _eligible(v, "price")


# ───────────────────────────── as-of resolution ────────────────────────────


def test_plumbed_as_of_wins_over_question_text() -> None:
    intent = resolve_question_as_of(
        "What was the rate back in November 2025?", plumbed="2025-11-14"
    )
    assert intent.origin == "plumbed"
    assert intent.as_of_end.date().isoformat() == "2025-11-14"


def test_month_year_phrase_resolves() -> None:
    intent = resolve_question_as_of(
        "What was it costing back in November 2025?",
        now_utc=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert intent.origin == "question-text"
    assert intent.as_of_end.date().isoformat() == "2025-11-30"


def test_bare_month_day_takes_most_recent_past() -> None:
    intent = resolve_question_as_of(
        "As of March 7, what time did the shift start?",
        now_utc=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert intent.as_of_end.date().isoformat() == "2026-03-07"


def test_unresolvable_text_yields_no_as_of() -> None:
    intent = resolve_question_as_of("What time does the shift start?")
    assert intent.present is False


def test_effective_time_prefers_event_then_statement() -> None:
    assert effective_time(_cand("k", "x", statement_at=20.0, event_at=10.0)) == 10.0
    assert effective_time(_cand("k", "x", statement_at=20.0)) == 20.0
    assert effective_time(_cand("k", "x", recorded_at=5.0)) == 5.0
    assert effective_time(_cand("k", "x")) is None


def test_decimal_and_time_values_survive_tokenization() -> None:
    assert "1.75" in value_tokens("The proofing room holds 1.75 kilos of dough.")
    assert "06:45" in value_tokens("Dawn patrol moves to 06:45.")
    assert value_tokens("Recount says 4,900 euros.") == {"4900"}


def test_fresh_ordinal_earliest_ask_selects_the_first_record() -> None:
    v = _verdicts([
        _cand("later", "Kiln log, October 3rd: still 61 firings on the Elements set.",
              statement_at=_ts("2026-10-03T09:00:00")),
        _cand("first", "Started the Elements set for the kiln — 61 firings on it this morning, June 2nd.",
              statement_at=_ts("2026-06-02T09:00:00")),
    ], question="When did I first put the Elements set firings on record?")
    assert _eligible(v, "first") and _reason(v, "first") == "slot-winner-ordinal-earliest"
    assert not _eligible(v, "later") and _reason(v, "later") == "ordinal-not-selected"


def test_fresh_ordinal_latest_ask_selects_the_last_record() -> None:
    v = _verdicts([
        _cand("a", "Harbour gate log: seal count 12 on the March patrol.",
              statement_at=_ts("2026-03-05T08:00:00")),
        _cand("b", "Harbour gate log: seal count 19 on the September patrol.",
              statement_at=_ts("2026-09-05T08:00:00")),
    ], question="What was the seal count the last time I patrolled the gate?")
    assert _eligible(v, "b")
    assert not _eligible(v, "a")


# ─────────────── fresh controls for the D10 repair laws ────────────────────


def test_fresh_write_time_never_dates_a_record_for_as_of() -> None:
    # No seam times at all: the import moment (recorded_at) must not make a
    # record 'future' to an as-of question; ordering still applies.
    v = _verdicts([
        _cand("older", "Customs form: the keel anode was renewed 2026-02-10.",
              recorded_at=1000.0),
        _cand("newer", "Amended form: the keel anode was renewed 2026-03-15.",
              recorded_at=2000.0),
    ], as_of="2026-02-20T00:00:00")
    # both are undated STATE-wise for as-of... the text-declared dates rule:
    assert _eligible(v, "older") and _eligible(v, "newer")


def test_fresh_text_declared_effective_date_governs_as_of() -> None:
    v = _verdicts([
        _cand("stand", "The gantry inspection lane fee is 8 euro.",
              recorded_at=1000.0),
        _cand("rise", "Notice: the lane fee becomes 11 euro from March 3.",
              recorded_at=2000.0),
    ], as_of="2026-02-20T00:00:00")
    assert _eligible(v, "stand")
    assert not _eligible(v, "rise") and _reason(v, "rise") == "future-relative-to-as-of"


def test_fresh_state_dated_member_wins_over_write_dated_sibling() -> None:
    # A record whose own text declares its effective date states the slot's
    # value; a later-INGESTED undated sibling does not overturn it. (With
    # non-numeric values and no undo marker the sibling coexists as
    # attributed history — supersession needs a value conflict or a marker.)
    v = _verdicts([
        _cand("text_dated", "Effective February 7: the ash pit holds 6 lamps.",
              recorded_at=900.0),
        _cand("write_dated", "The ash pit holds 3 lamps.",
              recorded_at=5000.0),
    ])
    assert _eligible(v, "text_dated") and _reason(v, "text_dated") == "slot-winner"
    assert not _eligible(v, "write_dated")


def test_fresh_assistant_elaboration_coexists_with_user_fact() -> None:
    v = _verdicts([
        _cand("u", "I'm rigging the new jib with the standard sheet.",
              statement_at=_ts("2026-04-02T09:00:00")),
        _cand("a", "For that rig, 10 mm Dyneema is the usual sheet; lead it aft at 45 degrees.",
              role="assistant", authority="assistant-output",
              statement_at=_ts("2026-04-02T09:00:20")),
    ])
    assert _eligible(v, "u")
    assert _eligible(v, "a")  # adds values the user never stated


def test_fresh_year_grain_declaration_governs_as_of() -> None:
    v = _verdicts([
        _cand("old", "Dues bookkeeping: the guild's yearly levy is 12 crowns.",
              recorded_at=100.0),
        _cand("y25", "The levy rises to 18 crowns from the 2025 season.",
              recorded_at=200.0),
        _cand("y26", "The levy rises again to 24 crowns for the 2026 season.",
              recorded_at=300.0),
    ], as_of="2025-09-01T00:00:00")
    assert _eligible(v, "y25") and _reason(v, "y25") == "slot-winner"
    assert not _eligible(v, "y26") and _reason(v, "y26") == "future-relative-to-as-of"
    assert not _eligible(v, "old")


def test_fresh_from_now_dates_at_the_write_moment() -> None:
    v = _verdicts([
        _cand("then", "The boathouse code is 4-1-9.",
              recorded_at=1_700_000_000.0),
        _cand("now", "One more change: the boathouse code is 7-2-5 from now.",
              recorded_at=1_790_000_000.0),
    ], as_of="2026-04-20T00:00:00")
    # the 'from now' change postdates the asked day -> future; the standing
    # value is the answer
    assert not _eligible(v, "now") and _reason(v, "now") == "future-relative-to-as-of"
    assert _eligible(v, "then")
