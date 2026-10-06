"""Explicit-date questions: the as-of year, the date window, and the
occurrence time leg.

A question that names a calendar day ("Which port was I moored in on
April 11, 2024?") asks about what was said around that day. The record that
answers it is often stated the NEXT day in words that share nothing with the
question ("Yesterday we tied up at Cais Brandao"), so neither the lexical
nor the semantic leg ranks it. Every occurrence carries a statement time;
the time leg reads the records stated inside the named period.

Rows:
* resolve_question_as_of honors a written 4-digit year (comma or not) and
  no longer drops the month+year phrase to an ungrouped month alternation;
* question_date_window — the closed date grammar, with paraphrase, sloppy,
  negative and adversarial members (quoted dates, two different dates, the
  modal "may", a bare year);
* relative_reference_day — the closed relative-day class;
* VoolMemory.occurrence_stated_between — scope, status and null-time laws;
* end to end through inject_retrieved on a real profile (hash backend, so
  no embedding service): the next-day record reaches the capsule, the
  as-of law still keeps a later record out, a hedged next-day record does
  not ride, and a question with no date (or only a quoted one) runs no leg.

Domains are fresh (a sailing log, a pottery studio, a bee yard); nothing here
is drawn from any benchmark conversation.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from core.temporal_selection import resolve_question_as_of

UTC = timezone.utc
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def question_date_window(question, *, now_utc=None):
    # imported per call so the behavioural rows of this file still run (and
    # fail on their own assertions) against a tree without the time leg
    from core.temporal_selection import question_date_window as fn

    return fn(question, now_utc=now_utc)


def relative_reference_day(body, statement_at):
    from core.temporal_selection import relative_reference_day as fn

    return fn(body, statement_at)


def _margin_days() -> int:
    from core.temporal_selection import DAY_WINDOW_MARGIN_DAYS

    return DAY_WINDOW_MARGIN_DAYS


# ───────────────────────── as-of resolution: written year ──────────────────


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # month-day, comma, year: the year was dropped (resolved to the most
        # recent past reading of the month-day instead)
        ("Which port was I moored in on April 11, 2024?", "2024-04-11"),
        ("where was the kiln firing on june 2, 2021", "2021-06-02"),
        ("Who visited the bee yard on December 5, 2021?", "2021-12-05"),
        ("The March 14, 2022 rehearsal: who sang?", "2022-03-14"),
        # month-day year without comma: the range resolver's half-open end
        # read as the NEXT day
        ("On August 9 2020 who called the studio?", "2020-08-09"),
        # month + year: the ungrouped month alternation captured only the
        # month word and the phrase never resolved
        ("what happened with the hives in February 2024", "2024-02-29"),
        ("What was the slip fee in November, 2019?", "2019-11-30"),
    ],
)
def test_as_of_honors_written_year(question: str, expected: str) -> None:
    intent = resolve_question_as_of(question, now_utc=NOW)
    assert intent.origin == "question-text"
    assert intent.as_of_end.date().isoformat() == expected


def test_as_of_year_less_day_keeps_most_recent_past() -> None:
    intent = resolve_question_as_of("What did I glaze on March 14?", now_utc=NOW)
    assert intent.as_of_end.date().isoformat() == "2026-03-14"


@pytest.mark.parametrize(
    "question",
    [
        "What time does the studio open?",
        "Which port do I prefer for winter?",
        "How many frames did the October swarm fill?",
    ],
)
def test_as_of_negative_controls_stay_unresolved(question: str) -> None:
    assert resolve_question_as_of(question, now_utc=NOW).as_of_end is None


# ───────────────────────── question_date_window grammar ────────────────────


@pytest.mark.parametrize(
    ("question", "day"),
    [
        ("Which port was I moored in on April 11, 2024?", "2024-04-11"),
        ("Where had I tied up by April 11th, 2024?", "2024-04-11"),
        ("what harbour was the boat at 11 April 2024", "2024-04-11"),
        ("on the 11th of April, 2024 where was i moored", "2024-04-11"),
        ("Moored where on 2024-04-11?", "2024-04-11"),
        ("wher was i moord on april 11 2024??", "2024-04-11"),
        ("port april 11, 2024", "2024-04-11"),
        ("Who came to the pottery class on June 2, 2021?", "2021-06-02"),
        # year-less: the as-of resolver's deictic most-recent-past rule
        ("What did I glaze on March 14?", "2026-03-14"),
    ],
)
def test_window_day_grain_family(question: str, day: str) -> None:
    window = question_date_window(question, now_utc=NOW)
    assert window is not None and window.grain == "day"
    assert window.first_day.isoformat() == day == window.last_day.isoformat()
    named = date.fromisoformat(day)
    assert (named - window.start.date()).days == _margin_days()
    assert (window.end.date() - named).days == _margin_days()
    assert window.day_distance(named) == 0
    assert window.day_distance(date.fromordinal(named.toordinal() + 1)) == 1


@pytest.mark.parametrize(
    ("question", "first", "last"),
    [
        ("What did the hives do in February 2024?", "2024-02-01", "2024-02-29"),
        ("slip fee in november, 2019", "2019-11-01", "2019-11-30"),
    ],
)
def test_window_month_grain(question: str, first: str, last: str) -> None:
    window = question_date_window(question, now_utc=NOW)
    assert window is not None and window.grain == "month"
    assert (window.first_day.isoformat(), window.last_day.isoformat()) == (first, last)
    assert window.start.date().isoformat() == first
    assert window.end.date().isoformat() == last


@pytest.mark.parametrize(
    "question",
    [
        # no date at all
        "Which port do I like best?",
        "What did I glaze last week?",
        # a bare year is too wide to be a day or month anchor
        "Where did I sail in 2024?",
        # two different named days: a range/comparison has no single window
        "Did I move from April 3, 2024 to April 9, 2024?",
        # quoted dates are someone else's text, not the question's frame
        'The almanac says "April 11, 2024 high tide 3.2 m" - which port do I use?',
        # the modal verb is not a month
        "How many jars may 3 people take home?",
        # an impossible day never resolves
        "What happened on February 30, 2024?",
    ],
)
def test_window_negative_and_adversarial(question: str) -> None:
    assert question_date_window(question, now_utc=NOW) is None


def test_window_same_day_named_twice_is_one_window() -> None:
    window = question_date_window(
        "On April 11, 2024 (that is 2024-04-11) where was I moored?", now_utc=NOW)
    assert window is not None and window.first_day.isoformat() == "2024-04-11"


# ───────────────────────── relative_reference_day ──────────────────────────


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Yesterday we tied up at Cais Brandao.", "2024-04-11"),
        ("we left the slip LAST NIGHT after dinner", "2024-04-11"),
        ("The day before yesterday the queen was laying again.", "2024-04-10"),
        ("Three days ago the kiln cracked a shelf.", "2024-04-09"),
        ("I glazed six bowls 2 days ago.", "2024-04-10"),
    ],
)
def test_relative_reference_day_family(body: str, expected: str) -> None:
    stated = _ts("2024-04-12T09:00:00")
    assert relative_reference_day(body, stated).isoformat() == expected


@pytest.mark.parametrize(
    "body",
    [
        "Today the swarm settled in the hedge.",
        "Next week I sail to Sines.",
        # two different referenced days: ambiguous, never guessed
        "Yesterday I fired the kiln; three days ago I loaded it.",
        "No time words in this one at all.",
    ],
)
def test_relative_reference_day_negative(body: str) -> None:
    assert relative_reference_day(body, _ts("2024-04-12T09:00:00")) is None


def test_relative_reference_day_needs_statement_time() -> None:
    assert relative_reference_day("Yesterday we tied up.", None) is None


# ───────────────────────── store read ──────────────────────────────────────


def test_occurrence_stated_between_scope_status_and_null_time(tmp_path) -> None:
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=str(tmp_path / "home"))
    try:
        def put(scope, body, stated):
            return mem.occurrence_store(
                chat_scope=scope, role="user", body=body,
                authority="observed-user-statement",
                statement_at=_ts(stated) if stated else None,
            )

        inside = put("sail", "Inside the window.", "2024-04-11T10:00:00")
        edge = put("sail", "On the window edge.", "2024-04-13T23:00:00")
        put("sail", "Before the window.", "2024-04-01T10:00:00")
        put("sail", "No statement time.", None)
        put("other-chat", "Other chat, same day.", "2024-04-11T10:00:00")
        gone = put("sail", "Deleted inside the window.", "2024-04-11T11:00:00")
        mem.occurrence_delete(occurrence_id=gone.occurrence_id)
        rows = mem.occurrence_stated_between(
            chat_scope="sail",
            start=_ts("2024-04-09T00:00:00"),
            end=_ts("2024-04-13T23:59:59"),
        )
        assert [r.occurrence_id for r in rows] == [
            inside.occurrence_id, edge.occurrence_id]
        assert mem.occurrence_stated_between(
            chat_scope="sail", start=_ts("2024-04-13T00:00:00"),
            end=_ts("2024-04-09T00:00:00")) == []
        assert mem.occurrence_stated_between(
            chat_scope="", start=0, end=_ts("2030-01-01T00:00:00")) == []
    finally:
        mem.close()


# ───────────────────────── end to end through the capsule ──────────────────


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "home"
    profile.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        VOOL_HOME=str(profile),
        VOOL_WORKSPACE_ROOT=str(profile / "workspace"),
    )
    return profile


@pytest.fixture()
def _hash_backend():
    from core import embedding_service

    original = embedding_service._best_embed_model
    embedding_service._best_embed_model = lambda: None
    yield
    embedding_service._best_embed_model = original


def _ingest(profile: Path, chat: str, turns: list[tuple[str, str]]) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for stated, text in turns:
        append_conversation_event(
            session_id=chat,
            user_input=text,
            assistant_output="",
            source_context={
                "surface": "api", "platform": "api", "chat_id": chat,
                "runtime_home": str(profile), "statement_at": stated,
            },
            access_policy=policy,
        )


def _capsule(profile: Path, chat: str, question: str):
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import get_last_retrieval_telemetry, inject_retrieved
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    messages = inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(profile)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    capsule = next(
        (str(m.get("content") or "") for m in messages
         if "<retrieved_context>" in str(m.get("content") or "")),
        "",
    )
    return capsule, get_last_retrieval_telemetry()


_SAIL_LOG = [
    # distractors: carry the question's own words on other dates, so the
    # lexical leg fills with them and term coverage is already satisfied
    ("2024-01-15T18:00:00", "I moored the sloop at the port of Sines for the winter."),
    ("2024-02-02T18:00:00", "The port office at Sines wants the mooring papers renewed."),
    ("2024-03-02T18:00:00", "The port authority raised the mooring fee to 40 euros a night."),
    ("2024-03-20T18:00:00", "I moored beside the fuel dock while the port crane was busy."),
    ("2024-06-03T18:00:00", "I moored at the port of Lagos for the June regatta."),
    ("2024-07-19T18:00:00", "Port of Faro was full so I moored outside the breakwater."),
    ("2024-08-08T18:00:00", "Which port has the cheapest diesel? I moored twice to compare."),
    # the named day's neighborhood: stated the day AFTER, no shared words
    ("2024-04-12T08:30:00", "Yesterday we tied up at Cais Brandao after the squall passed."),
    ("2024-04-12T08:40:00", "The squall bent the forestay a little."),
    ("2024-04-10T19:00:00", "Bought new fenders at the chandlery."),
    # far from the named day, also no shared words
    ("2024-05-01T10:00:00", "Varnished the tiller and replaced two cleats."),
    ("2024-09-01T10:00:00", "Hauled out for bottom paint."),
]


@pytest.mark.usefixtures("_hash_backend")
def test_named_day_reaches_next_day_record(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "sail-log", _SAIL_LOG)
    capsule, telemetry = _capsule(
        profile, "sail-log", "Which port was I moored in on April 11, 2024?")
    assert "Cais Brandao" in capsule, capsule
    leg = telemetry.get("evidence_time_leg")
    assert leg and leg["grain"] == "day" and leg["first_day"] == "2024-04-11"
    assert leg["anchored_records"] >= 1
    receipts = [r for r in telemetry.get("evidence_refs") or []
                if "Cais Brandao" in str(r.get("line") or r.get("span") or "")]
    assert receipts, telemetry.get("evidence_refs")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize(
    "question",
    [
        "where was i moored on april 11 2024",
        "On 11 April 2024, which port held my boat?",
        "port I was moored in, 11th of April 2024?",
    ],
)
def test_named_day_variants_reach_next_day_record(tmp_path, question) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "sail-log", _SAIL_LOG)
    capsule, _telemetry = _capsule(profile, "sail-log", question)
    assert "Cais Brandao" in capsule, capsule


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize(
    "question",
    [
        # no date: the leg does not run
        "Which port was I moored in?",
        # the only date is quoted text, not the question's frame
        'The tide table says "April 11, 2024" - which port do I usually moor in?',
    ],
)
def test_no_explicit_date_runs_no_leg(tmp_path, question) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "sail-log", _SAIL_LOG)
    capsule, telemetry = _capsule(profile, "sail-log", question)
    assert telemetry.get("evidence_time_leg") is None
    assert "Cais Brandao" not in capsule


@pytest.mark.usefixtures("_hash_backend")
def test_hedged_next_day_record_does_not_ride(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest(profile, "bee-yard", [
        ("2025-05-02T09:00:00", "The bee yard hive count is twelve colonies."),
        ("2025-05-20T09:00:00", "Moved two colonies in the bee yard to the clover field."),
        ("2025-06-11T09:00:00", "Maybe yesterday the swarm went to the old chestnut, I am not sure."),
        ("2025-06-11T09:30:00", "Smoker fuel is running low."),
    ])
    capsule, telemetry = _capsule(
        profile, "bee-yard", "Where did the swarm from the bee yard go on June 10, 2025?")
    assert telemetry.get("evidence_time_leg") is not None
    assert "chestnut" not in capsule


@pytest.mark.usefixtures("_hash_backend")
def test_window_record_from_after_the_day_without_back_reference_stays_out(tmp_path) -> None:
    """The as-of law still rules: a record stated after the named day that
    does not date itself back onto it is not evidence for that day, even
    though the time leg read it from the window."""
    profile = _profile(tmp_path)
    _ingest(profile, "studio", [
        ("2023-02-01T10:00:00", "The studio glaze for the mugs is celadon."),
        ("2023-03-09T10:00:00", "Correction: the studio glaze for the mugs is now tenmoku."),
    ])
    capsule, telemetry = _capsule(
        profile, "studio", "What was the studio glaze for the mugs on March 8, 2023?")
    assert "celadon" in capsule
    assert "tenmoku" not in capsule
    assert telemetry.get("evidence_time_leg") is not None


@pytest.mark.usefixtures("_hash_backend")
def test_next_day_record_dated_back_onto_the_day_counts_for_that_day(tmp_path) -> None:
    """A record stated the day after the asked day whose own words date the
    change onto it ("Yesterday I switched ...") is evidence FOR that day:
    the as-of law must not drop it as a future statement."""
    profile = _profile(tmp_path)
    _ingest(profile, "studio", [
        ("2023-02-01T10:00:00", "The studio glaze for the mugs is celadon."),
        ("2023-03-09T10:00:00", "Yesterday I switched the studio glaze for the mugs to tenmoku."),
    ])
    capsule, telemetry = _capsule(
        profile, "studio", "What was the studio glaze for the mugs on March 8, 2023?")
    assert "tenmoku" in capsule, capsule
    assert telemetry.get("evidence_time_leg") is not None
