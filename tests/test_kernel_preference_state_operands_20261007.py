"""v14.2 kernel: preference and state facts in the receipts, state chains, and the operands the compiler builds from
them, on the served store and retrieval seams. Families from a held-out run's miss classes, reworded; no held-out
wording is reused here."""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.memory_receipts import extract_facts, find_changes
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d, hh=9):
    return float(calendar.timegm(datetime(y, m, d, hh, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as es

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1"); monkeypatch.setenv("VOOL_MEMORY_RECEIPTS", "1"); monkeypatch.setenv("VOOL_EVIDENCE_COMPILER", "1")
    configure_runtime_home(profile); es._best_embed_model = lambda: None
    from storage.migrations import run_migrations
    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def _store(home, chat, user_text, stated_at):
    ensure_chat_namespace(chat, grant_current_receipts=False); policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(chat, user_text, "Noted.", access_policy=policy, source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated_at})


def _ask(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat); cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}], access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    return "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content"))), dict(cr.get_last_retrieval_telemetry())


PREFERENCES = [  # (statement, question with no shared content word, the word that must reach the packet)
    ("I keep a gluten-free diet, bread is off the table for me.", "Any ideas for what to serve at brunch on Sunday?", "gluten"),
    ("im lactose intolerant so no dairy for me pls", "what dessert should i bring to the office party", "lactose"),
    ("I prefer taking the night train over flying whenever a trip is under ten hours.", "How should I get to Lyon for the long weekend?", "train"),
    ("My wardrobe is all earth tones, loose cuts, nothing shiny.", "What should I wear to a cousin's wedding reception?", "earth tones"),
    ("I dont do caffeine after lunch, it wrecks my sleep", "Suggest something to drink while I study tonight.", "caffeine"),
    ("I'm allergic to shellfish, even a trace sets me off.", "Recommend a restaurant dish for a seaside dinner.", "shellfish"),
]


@pytest.mark.parametrize("statement,question,needle", PREFERENCES)
def test_a_stated_preference_reaches_the_packet_for_an_advice_ask_without_a_shared_word(home, statement, question, needle):
    chat = "chat-pref-" + needle.replace(" ", "")
    _store(home, chat, "Session date: 2025/03/03 (Mon) 09:00\n" + statement, _epoch(2025, 3, 3))
    for i in range(4):
        _store(home, chat, f"Session date: 2025/03/1{i} (Mon) 09:00\nCan you explain how tides work, part {i}?", _epoch(2025, 3, 10 + i))
    block, telemetry = _ask(home, chat, question)
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["kind"] == "preference" and ec["complete"] is True, ec
    assert needle.lower() in block.lower() and "<preference>" in block


NOT_PREFERENCES = ["I love the idea of a weekly review!", "I like those suggestions, thanks.", "I love how the app divides the tasks.", "I like the thought of a quiet morning."]


@pytest.mark.parametrize("text", NOT_PREFERENCES)
def test_an_acknowledgement_of_the_assistants_idea_is_not_a_preference(text):
    assert not [f for f in extract_facts(text, _epoch(2025, 3, 3), "user") if f.value_type == "preference"], text


TRANSITIONS = [  # (earlier state, later transition, question, old value, new value, key words)
    ("I drive a Toyota Yaris to work most days.", "Picked up a Mazda 3 last week after trading in my Toyota Yaris.", "Which car do I drive these days?", "Toyota Yaris", "Mazda 3"),
    ("I work as a line cook at a bistro downtown.", "Things changed, I moved from being a line cook to a pastry chef in May.", "What is my current job?", "line cook", "pastry chef"),
    ("i live in Ghent, close to the station", "we moved to Leuven this month, boxes everywhere", "Where do I live now?", "Ghent", "Leuven"),
    ("My employer is Helix Labs, a small biotech.", "I switched from Helix Labs to Northwind Analytics in April.", "Who do I work for now?", "Helix Labs", "Northwind Analytics"),
]


@pytest.mark.parametrize("earlier,later,question,old,new", TRANSITIONS)
def test_a_state_change_is_written_as_a_replacement_and_the_packet_shows_the_chain(home, earlier, later, question, old, new):
    chat = "chat-state-" + new.replace(" ", "")[:8]
    _store(home, chat, "Session date: 2025/01/10 (Fri) 09:00\n" + earlier, _epoch(2025, 1, 10))
    _store(home, chat, "Session date: 2025/05/20 (Tue) 09:00\n" + later, _epoch(2025, 5, 20))
    block, telemetry = _ask(home, chat, question)
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["kind"] == "current_value" and ec["complete"] is True, ec
    assert f"REPLACED later by \"{new}\"" in block and new in block, block
    facts = telemetry["evidence_packet_facts"]
    current = [f for f in facts if f.get("value_type") == "state" and not f.get("replaced_by")]
    assert current and current[0]["value"] == new


def test_first_two_dated_events_are_the_earliest_two_not_the_best_ranked(home):
    chat = "chat-ordinal"
    _store(home, chat, "Session date: 2025/02/08 (Sat) 09:00\nI feel stuck lately; I saw the dentist on 7 February and need a filling.", _epoch(2025, 2, 8))
    _store(home, chat, "Session date: 2025/04/05 (Sat) 09:00\nMy sleep is bad; I went to the dentist on 4 April about the grinding.", _epoch(2025, 4, 5))
    _store(home, chat, "Session date: 2025/04/26 (Sat) 09:00\nI visited the dentist again on 25 April for a check and an update on the filling, dentist dentist.", _epoch(2025, 4, 26))
    block, telemetry = _ask(home, chat, "How many days were there between my first two dentist appointments?")
    assert "56 days" in block, block


def test_a_long_user_sentence_is_matched_like_any_sentence_not_like_a_list():
    from core.memory_receipts import match_receipts

    long_sentence = ("Those are solid tips and I appreciate them, I feel like I have been stuck in a rut for a while now, especially since I went to the "
                     "optician on 7 February and realized I need new glasses, maybe audiobooks will help me adjust while I wait for them.")
    facts = [f.as_dict() for f in extract_facts(long_sentence, _epoch(2025, 2, 8), "user")]
    receipts = [{"receipt_id": "r1", "occurrence_id": "u1", "role": "user", "statement_at": _epoch(2025, 2, 8), "facts": facts}]
    assert match_receipts(receipts, "When did I go to the optician?"), "one shared content word must be enough for a sentence"
