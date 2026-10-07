"""v14.6 port: the kernel's receipts packet never rides past a capsule that withheld everything unless the packet is
complete (port assertion-gate Q04, F03, F07 and the absent-qualifier facet case were red with the kernel on), and a
number followed by another word is not an age ("is 60 crowns" typed as age 60). Contributor: sls_0x."""
from types import SimpleNamespace

import pytest

import core.context_retrieval as cr
from core.memory_receipts import extract_facts

T = 1750000000.0


@pytest.mark.parametrize("sentence, kinds", [
    ("The beginner course fee is 60 crowns.", set()),
    ("My longest tunnel route so far is 26 km.", {"measure"}),
    ("I'm 34.", {"age"}),
    ("I turned 40 last week.", {"age"}),
    ("She is 29 years old.", {"age"}),
    ("The hall is 60 metres long.", {"measure"}),
])
def test_a_number_followed_by_a_word_is_not_an_age(sentence, kinds):
    got = {f.value_type for f in extract_facts(sentence, T, "user")} & {"age", "measure"}
    assert got == kinds, [(f.value_type, f.norm) for f in extract_facts(sentence, T, "user")]


def _packet(complete: bool):
    return SimpleNamespace(lines=["- [2026-03-01] user said: \"The beginner course fee is 60 crowns.\"  <amount: 60>"],
                           facts=[{"sentence": "x", "value_type": "amount", "role": "user"}],
                           telemetry={"complete": complete, "obligation": {"kind": "single_fact"}})


def test_an_incomplete_packet_does_not_ride_alone_past_a_withholding_capsule(monkeypatch):
    monkeypatch.setattr(cr, "_set_retrieval_telemetry", lambda t: None)
    transcript = [{"role": "user", "content": "What is the advanced course fee?"}]
    out = cr._packet_only_injection(list(transcript), _packet(False), {"capsule_mode": "no_hits"}, chat_id="c", question="q")
    assert out == transcript


def test_a_complete_packet_still_rides_alone(monkeypatch):
    monkeypatch.setattr(cr, "_set_retrieval_telemetry", lambda t: None)
    transcript = [{"role": "user", "content": "What is the beginner course fee?"}]
    out = cr._packet_only_injection(list(transcript), _packet(True), {"capsule_mode": "no_hits"}, chat_id="c", question="q")
    assert len(out) == 2 and "<retrieved_context>" in out[0]["content"]


@pytest.mark.parametrize("body, kinds", [
    ("ASSISTANT: You might instead book the Amber canyon walk for 110 euros.", set()),                      # the assistant's words in a user record
    ("I paid 95 euros for the helmet.\nASSISTANT: You could also try the 110 euro one.", {"amount"}),     # the user's own clause stays
    ("**ASSISTANT:** Try the 110 euro option.", set()),
])
def test_the_other_speakers_labelled_words_inside_a_record_are_not_this_roles_facts(body, kinds):
    got = {f.value_type for f in extract_facts(body, T, "user")} & {"amount", "preference", "state"}
    assert got == kinds, [(f.value_type, f.norm) for f in extract_facts(body, T, "user")]
    if "110" in body and kinds == {"amount"}:
        assert all("110" not in f.value for f in extract_facts(body, T, "user"))


def test_an_assistant_record_quoting_the_user_keeps_only_the_assistants_words():
    body = "USER: I paid 95 euros for the helmet.\nASSISTANT: Noted, the 110 euro one is better value."
    facts = extract_facts(body, T, "assistant")
    assert all("95" not in f.value for f in facts), [(f.value_type, f.value) for f in facts]


# ─── the verbatim turn lane is the last section of the block ───────────────────────────────────

import calendar
from datetime import datetime, timezone

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d, hh=9):
    return float(calendar.timegm(datetime(y, m, d, hh, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as es

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("NULLA_CONTEXT_CAPSULE_V2", "VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile); es._best_embed_model = lambda: None
    from storage.migrations import run_migrations
    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def test_the_turn_lane_is_the_last_section_after_the_receipts_packet(home):
    chat = "chat-lane-order"
    ensure_chat_namespace(chat, grant_current_receipts=False); policy = resolve_memory_access_policy(chat_id=chat)
    for i, text in enumerate(["I bought a bike helmet for $95 on 3 March, the shop was busy and the fitting took a while.",
                              "Today I picked up bike lights for $48 and a bell, the lights are bright enough for the lane home.",
                              "The commute is forty minutes each way, which is why the lights matter so much to me."]):
        cr.store_turn(chat, text, "Noted.", access_policy=policy, source_context={"chat_id": chat, "runtime_home": home, "statement_at": _epoch(2025, 3, 3 + i)})
    cr.reset_retrieval_telemetry()
    q = "How much did I spend on bike gear altogether?"
    out = cr.inject_retrieved(chat, q, [{"role": "user", "content": q}], access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    assert "Evidence receipts" in block, block
    lane = block.find("Evidence turns (whole records")
    packet = block.find("Evidence receipts")
    if lane >= 0:
        assert lane > packet, block
        assert "This question needs" not in block[lane:], block[lane:]
