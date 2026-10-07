"""A past-tense ask faces the absence gate like a present-tense one (core/context_retrieval.py).

The capsule drops a user record as absent-facet noise when two or more of the ask's discriminating
terms were never retained anywhere the caller may read. The gate's arming condition disarmed it for
every ask whose scope reads PAST ("what did I say ...", "what were ..."), so a past-tense ask about a
facet, place or person the chat never mentioned was served the nearest record whole, and the reader
could answer another entity's value. The fear behind the disarm (a past ask's frame words, "first
come up", reading as absent content) is met by filtering that vocabulary from the discriminators,
not by switching the gate off. Every record and question below was written for this file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import _hash_backend

LOCKER = ("The swimming pool locker on the lower corridor is locker 214. Its key fob colour is lime "
          "green. Those are all the details I keep for that locker.")
GATEHOUSE = ("The orchard gatehouse alarm code is 5-9-2-6. Its shutters are painted slate blue. Those are "
             "all the details I keep for the gatehouse.")
VALUES = ("214", "5-9-2-6")

# past-tense asks about a facet, a place or a person the chat never mentioned: refused
REFUSE = {
    "original": "What did I say about the opening hours of the swimming pool locker on the lower corridor?",
    # clean paraphrases
    "p1": "What did I tell you the opening hours of the swimming pool locker on the lower corridor were?",
    "p2": "What were the opening hours of the swimming pool locker on the lower corridor?",
    "p3": "Did I ever mention the rental price of the swimming pool locker on the lower corridor?",
    "p4": "What did I say the locker number of the locker at the rowing club boathouse was?",
    "p5": "What was Ingrid Halvorsen's locker number for the swimming pool locker on the lower corridor?",
    # sloppy / user-typed
    "s1": "what did i say opening hours swimming pool locker lower corridor",
    "s2": "wat did i say the openning hours of the pool locker on the lower corridor were",
    "s3": "locker number rowing club boathouse what did i tell u",
    "s4": "Ingrid Halvorsen locker number swimming pool lower corridor what did I say?",
    "s5": "wat were the openning hours for the pool locker lower corridor",
    # "who" IS the asked facet here, and the chat never retained the sharing or the cousin: still refused
    "who_facet": "Who did I say shares the swimming pool locker on the lower corridor with my cousin?",
    # an embedded interrogative heads the ask too: the actor is the asked facet, never retained here
    "who_embedded": "Tell me who I said installed the swimming pool locker on the lower corridor.",
}

# the same past frames over a facet the chat DID retain: served
KEEP = {
    "own_value": ("What did I say the locker number of the swimming pool locker on the lower corridor was?", "214"),
    "own_facet": ("What did I tell you the key fob colour of the swimming pool locker on the lower corridor was?",
                  "lime green"),
    "other_record": ("What was the alarm code of the orchard gatehouse?", "5-9-2-6"),
    "sloppy_own": ("key fob colour swimming pool locker lower corridor what did i say", "lime green"),
    # a relative clause's "who" is ask frame, never an absent facet (measured: "The plumber who bled the
    # radiators - how much was his bill?" armed the gate on {who, bill} and withheld the invoice)
    "who_clause": ("The person who keeps the swimming pool locker on the lower corridor - what did I say the number was?",
                   "214"),
    "who_clause_present": ("The person who keeps the swimming pool locker on the lower corridor - what is the number?",
                           "214"),
}


def _store(tmp_path: Path, chat: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    profile = tmp_path / "home"
    profile.mkdir(parents=True, exist_ok=True)
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("NULLA_WORKSPACE_ROOT", "VOOL_WORKSPACE_ROOT"):
        monkeypatch.setenv(name, str(profile / "workspace"))
    ensure_chat_namespace(chat, grant_current_receipts=False)
    for body, reply in ((LOCKER, "Noted. A locker like that usually wants its hinge oiled each spring."),
                        (GATEHOUSE, "Noted. A gatehouse alarm usually wants a battery check each autumn.")):
        receipt = cr.store_turn(chat, body, reply,
                                access_policy=resolve_memory_access_policy(chat_id=chat),
                                source_context={"runtime_home": str(profile), "statement_at": 1767600000.0})
        assert receipt["status"] in ("stored", "retained"), receipt
    return profile


def _capsule(profile: Path, chat: str, question: str) -> tuple[str, dict]:
    from core.memory.entries import resolve_memory_access_policy

    messages = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}],
                                   access_policy=resolve_memory_access_policy(chat_id=chat),
                                   source_context={"runtime_home": str(profile), "chat_id": chat},
                                   env={"NULLA_CONTEXT_CAPSULE_V2": "1", "VOOL_CONTEXT_CAPSULE_V2": "1"})
    capsule = "\n".join(m["content"] for m in messages if m["role"] == "system")
    return capsule, cr.get_last_retrieval_telemetry()


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("case", sorted(REFUSE))
def test_a_past_tense_ask_about_an_absent_facet_entity_or_person_is_refused(tmp_path, monkeypatch, case):
    profile = _store(tmp_path, "pool-" + case, monkeypatch)
    capsule, telemetry = _capsule(profile, "pool-" + case, REFUSE[case])
    for value in VALUES:
        assert value not in capsule, (case, telemetry.get("whole_turn_lines"), capsule)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("case", sorted(KEEP))
def test_a_past_tense_ask_the_records_answer_is_still_served(tmp_path, monkeypatch, case):
    question, value = KEEP[case]
    profile = _store(tmp_path, "pool-" + case, monkeypatch)
    capsule, _telemetry = _capsule(profile, "pool-" + case, question)
    assert value in capsule, capsule


# the frame vocabulary 344be9ec feared: a history ask whose only "absent" terms name its time frame
HISTORY = [
    ("When did the swimming pool locker on the lower corridor first come up?", "214"),
    ("What was the swimming pool locker on the lower corridor originally?", "214"),
    ("What did I say earlier about the orchard gatehouse alarm code, before anything changed?", "5-9-2-6"),
    ("When did I first bring up the orchard gatehouse alarm code?", "5-9-2-6"),
    ("What did I say about the orchard gatehouse alarm code the first time it came up?", "5-9-2-6"),
]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question,value", HISTORY)
def test_a_history_ask_whose_absent_terms_are_its_own_frame_is_not_gated(tmp_path, monkeypatch, question, value):
    profile = _store(tmp_path, "pool-hist-" + str(abs(hash(question)) % 10_000), monkeypatch)
    capsule, telemetry = _capsule(profile, "pool-hist-" + str(abs(hash(question)) % 10_000), question)
    assert value in capsule, (telemetry.get("whole_turn_lines"), capsule)


@pytest.mark.usefixtures("_hash_backend")
def test_a_superseded_value_still_answers_a_before_ask(tmp_path, monkeypatch):
    """The history law the disarm protected: a past ask over a corrected facet keeps the earlier value."""
    from core.memory.entries import resolve_memory_access_policy

    profile = _store(tmp_path, "pool-toll", monkeypatch)
    policy = resolve_memory_access_policy(chat_id="pool-toll")
    for stated, body, reply in (
            (1767600000.0, "The bridge toll was 4 guilders before the spring revision.", "4 guilders, noted."),
            (1767700000.0, "It rose to 6 guilders in the spring revision.", "6 guilders from spring, noted.")):
        receipt = cr.store_turn("pool-toll", body, reply, access_policy=policy,
                                source_context={"runtime_home": str(profile), "statement_at": stated})
        assert receipt["status"] in ("stored", "retained"), receipt
    capsule, _telemetry = _capsule(profile, "pool-toll", "What was the bridge toll before the spring revision?")
    assert "4 guilders" in capsule, capsule


@pytest.mark.usefixtures("_hash_backend")
def test_an_as_of_ask_keeps_the_as_of_law(tmp_path, monkeypatch):
    """Adversarial near-miss: a dated ask is the as-of law's, not the gate's; the record stated that day serves."""
    profile = _store(tmp_path, "pool-asof", monkeypatch)
    capsule, _telemetry = _capsule(
        profile, "pool-asof", "What was the swimming pool locker number on the lower corridor as of 2026-01-05?")
    assert "214" in capsule, capsule
