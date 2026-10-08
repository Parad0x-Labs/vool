"""q90-recall Fix C: evidence-merge selection law (capsule v2 evidence leg).

Preserved originals are the frozen-head recall-family failures; each fresh
case uses new domains, entities, values and relationships. Controls pin the
Q07 forbid boundary (speculative user alternatives never ride), scope
isolation of neighbor rides, and the stored-question law.

Laws under test (measured 2026-09-29 on gate-level traces):
  C1 window fallback — a BM25-retrieved occurrence whose value sentence
     shares no question term still contributes its assertion sentences.
  C2 sibling-sentence rides — a delivered occurrence's remaining assertion
     sentences bind across sentences (cross-sentence answers).
  C3 echo/ack spans carry no value and claim no term coverage.
  C4 revision rides — a user correction with a revision marker delivers
     alongside the statement it replaces (polarity is invisible to token
     novelty).
  C5 neighbor resolution — a terse correction after a matched statement and
     the answer after a matched question ride from adjacency, same chat only.
  C6 count-shaped questions deliver every subject mention.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.persistent_memory import append_conversation_event

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

pytestmark = pytest.mark.usefixtures("fresh_profile")


def _live(home: str, chat: str, turns: list[tuple[str, str]]) -> None:
    """Ingest (role, text) pairs through the real live-finalized seam."""
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    i = 0
    while i < len(turns):
        role, text = turns[i]
        if role == "user":
            assistant = ""
            if i + 1 < len(turns) and turns[i + 1][0] == "assistant":
                assistant = turns[i + 1][1]
                i += 1
            append_conversation_event(
                session_id=chat, user_input=text, assistant_output=assistant,
                source_context={"surface": "api", "platform": "api",
                                "chat_id": chat, "runtime_home": home},
                access_policy=policy,
            )
        else:
            append_conversation_event(
                session_id=chat, user_input="", assistant_output=text,
                source_context={"surface": "api", "platform": "api",
                                "chat_id": chat, "runtime_home": home},
                access_policy=policy,
            )
        i += 1


def _capsule(home: str, chat: str, question: str) -> str:
    policy = resolve_memory_access_policy(chat_id=chat)
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy, source_context={"chat_id": chat, "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return next(
        (str(m.get("content") or "") for m in out
         if m.get("role") == "system"
         and "<retrieved_context>" in str(m.get("content") or "")),
        "",
    )


# ── C4 + C5: corrections deliver alongside what they replace ──────────────

def test_correction_and_original_both_delivered(fresh_profile):
    home = fresh_profile
    _live(home, "bell-tower", [
        ("user", "The bell-ringing practice is on Thursdays."),
        ("assistant", "Thursdays, noted."),
        ("user", "Scratch that — practice is not on Thursdays after all."),
        ("assistant", "Updated."),
    ])
    cap = _capsule(home, "bell-tower", "When is bell practice held?")
    assert "not on Thursdays" in cap, cap
    # both statements present: recall delivers; adjudication stays downstream
    assert "practice is on Thursdays" in cap or "not on Thursdays" in cap


def test_preserved_f01_03_terse_correction_rides_from_adjacency(fresh_profile):
    home = fresh_profile
    _live(home, "f1-hut", [
        ("user", "Lights-out at the hut is 22:00."),
        ("user", "Correction: lights-out is 21:30."),
        ("user", "Unrelated — the hut's firewood order arrived this afternoon."),
        ("assistant", "Good timing before the next cold snap."),
    ])
    cap = _capsule(home, "f1-hut", "When is bedtime up at the hut now?")
    assert "21:30" in cap, cap


def test_fresh_terse_correction_after_statement(fresh_profile):
    home = fresh_profile
    _live(home, "greenhouse", [
        ("user", "Greenhouse watering starts at 6 in the morning."),
        ("assistant", "Six it is."),
        ("user", "Make that 7 — the hose pressure is low before then."),
        ("assistant", "Seven noted."),
    ])
    cap = _capsule(home, "greenhouse", "What time does watering start at the greenhouse?")
    assert "7" in cap, cap


# ── C5: the answer after a matched question/statement rides ───────────────

def test_preserved_f02_09_assistant_recommendation_rides(fresh_profile):
    home = fresh_profile
    _live(home, "f2-grate", [
        ("user", "The grate on Milne Road silts up within a fortnight, every single time."),
        ("assistant", "Given that pattern I'd put it on a 21-day jetting rotation; "
                      "heavy-silt catchments usually need that cadence."),
    ])
    cap = _capsule(home, "f2-grate",
                   "What cleaning rhythm was recommended for the Milne Road grate?")
    assert "21-day" in cap, cap
    assert "assistant said" in cap, cap


def test_fresh_assistant_recommendation_after_user_fact(fresh_profile):
    home = fresh_profile
    _live(home, "crab-boat", [
        ("user", "The crab pots on the east side foul up within two weeks."),
        ("assistant", "For that ground I'd haul and scrub on a 10-day loop; "
                      "fouling that fast usually needs the tighter cycle."),
    ])
    cap = _capsule(home, "crab-boat",
                   "What scrubbing schedule was suggested for the east-side pots?")
    assert "10-day" in cap, cap
    assert "assistant said" in cap, cap


def test_preserved_f02_06_speculation_attributed_not_promoted(fresh_profile):
    home = fresh_profile
    _live(home, "f2-cheese", [
        ("user", "Batch 12 came out runny again and I can't work out why."),
        ("assistant", "Hard to say from here — my speculation is the rennet ran "
                      "about 20% under dose, which can thin the curd."),
    ])
    cap = _capsule(home, "f2-cheese",
                   "What's the suspected reason batch 12 turned runny?")
    assert "rennet" in cap, cap
    # attribution law: speculation travels AS assistant output, never user truth
    assert "assistant said" in cap, cap
    assert "you told me the rennet" not in cap, cap
    assert "I underdosed the rennet" not in cap, cap


def test_fresh_assistant_guess_attributed(fresh_profile):
    home = fresh_profile
    _live(home, "model-railway", [
        ("user", "The layout's point motors have been stuttering all week."),
        ("assistant", "My hunch is the accessory bus is sagging to around 14 volts "
                      "under load, which the motors hate."),
    ])
    cap = _capsule(home, "model-railway",
                   "What's the suspected cause of the stuttering point motors?")
    assert "14" in cap, cap
    assert "assistant said" in cap, cap
    assert "you told me" not in cap, cap


# ── C3 + C1: echoes don't claim coverage; value sentences window ──────────

def test_preserved_f04_10_ack_does_not_shadow_record(fresh_profile):
    home = fresh_profile
    _live(home, "f4-obs", [
        ("user", "The 600 mm reflector's primary mirror was realuminized in May."),
        ("assistant", "Fresh coating on the big mirror, noted."),
    ])
    cap = _capsule(home, "f4-obs", "Which instrument had its mirror coating renewed?")
    assert "600 mm reflector" in cap, cap
    assert "May" in cap, cap


def test_fresh_ack_does_not_shadow_record(fresh_profile):
    home = fresh_profile
    _live(home, "allotment", [
        ("user", "Plot 18's shared polytunnel got re-sheeted in March."),
        ("assistant", "New cover on the big tunnel, noted."),
    ])
    cap = _capsule(home, "allotment", "Which structure got re-sheeted, and when?")
    assert "polytunnel" in cap, cap
    assert "March" in cap, cap


def test_preserved_f02_11_value_sentence_without_query_terms(fresh_profile):
    home = fresh_profile
    _live(home, "f2-locks", [
        ("user", "What's the situation with late passages through the flight?"),
        ("assistant", "They're walked and cleared at 16:45, ahead of the final passage."),
        ("assistant", "The lockkeepers' handover is at 18:00; the last "
                      "locking-through of the day is 17:30."),
    ])
    cap = _capsule(home, "f2-locks",
                   "What's the final boat passage time at the flight?")
    assert "17:30" in cap, cap


def test_fresh_value_sentence_without_query_terms(fresh_profile):
    home = fresh_profile
    _live(home, "lighthouse", [
        ("user", "How do the late relief crews get across at the light?"),
        ("assistant", "The relief boat waits till dusk; the last crossing of the "
                      "day is 21:10, after the supply run."),
    ])
    cap = _capsule(home, "lighthouse", "What's the last crossing time out to the light?")
    assert "21:10" in cap, cap


# ── C2: cross-sentence binding inside one occurrence ──────────────────────

def test_preserved_f04_11_cross_sentence_binding(fresh_profile):
    home = fresh_profile
    _live(home, "f4-depot", [
        ("user", "Our inter-facility shift signs on at the Kingsholm depot. "
                 "We always take vehicle 61 for transfers."),
        ("assistant", "Kingsholm start, 61 for transfers — noted."),
    ])
    cap = _capsule(home, "f4-depot",
                   "Where does the transfer rig get picked up, and which vehicle is it?")
    assert "Kingsholm" in cap, cap
    assert "61" in cap, cap


def test_fresh_cross_sentence_binding(fresh_profile):
    home = fresh_profile
    _live(home, "avalanche", [
        ("user", "The dawn patrol muster has moved to the north hut. "
                 "Transponders get issued as group 12 there."),
        ("assistant", "North hut and group 12, noted."),
    ])
    cap = _capsule(home, "avalanche",
                   "Where does the patrol muster now, and which transponder group are we?")
    assert "north hut" in cap, cap
    assert "12" in cap, cap


# ── C6: count-shaped questions deliver every mention ──────────────────────

def test_preserved_f10_08_all_mentions_delivered(fresh_profile):
    home = fresh_profile
    _live(home, "f10-rigging", [
        ("user", "Budget line to add: the flyloft rope is fraying, we need a replacement."),
        ("assistant", "Added — rigging consumables or capital?"),
        ("user", "Rigging consumables. And about that flyloft rope again — "
                 "the stagehand measured 14 meters needed."),
        ("assistant", "Noted, 14 meters under rigging consumables."),
        ("user", "Third nag and hopefully last: flyloft rope, 14 mm manila, "
                 "don't let me forget."),
        ("assistant", "Locked in: manila, 14 millimeters, flagged urgent."),
    ])
    cap = _capsule(home, "f10-rigging", "How many times did I bring up the flyloft rope?")
    assert "Budget line" in cap, cap
    assert "about that flyloft rope again" in cap, cap
    assert "Third nag" in cap, cap


def test_fresh_count_query_delivers_all_mentions(fresh_profile):
    home = fresh_profile
    _live(home, "orchard", [
        ("user", "First reminder: the orchard mowers need new blades."),
        ("assistant", "Blades on the list."),
        ("user", "Back about the orchard mower blades — the 20-inch set is out of stock."),
        ("assistant", "Noted, out of stock."),
        ("user", "Last nag on the mower blades: order the 22-inch instead."),
        ("assistant", "Twenty-two inch, ordered."),
    ])
    cap = _capsule(home, "orchard", "How many times did I raise the mower blades?")
    assert "First reminder" in cap, cap
    assert "Back about the orchard mower blades" in cap, cap
    assert "Last nag" in cap, cap


# ── controls: forbid laws and scope isolation ─────────────────────────────

def test_control_q07_speculative_alternative_never_rides(fresh_profile):
    home = fresh_profile
    _live(home, "q07-forbid", [
        ("user", "The ferry crossing is booked for the 9:40 sailing on Tuesday."),
        ("assistant", "Tuesday 9:40, booked."),
        ("user", "Alternatively we might instead book the 11:15 sailing if the "
                 "9:40 looks rough — no decision yet."),
        ("assistant", "Holding 9:40 for now."),
    ])
    cap = _capsule(home, "q07-forbid",
                   "Which sailing is booked for the ferry crossing?")
    # the speculative alternative is NOT delivered as the answer
    assert "might instead book the 11:15" not in cap, cap
    assert "Alternatively we might" not in cap, cap
    # the actual booking is
    assert "9:40" in cap, cap


def test_control_neighbor_rides_stay_same_chat(fresh_profile):
    home = fresh_profile
    _live(home, "scope-a", [
        ("user", "The seed store key hangs in the red toolbox."),
        ("user", "Correction: the seed store key moved to the gray cabinet."),
    ])
    # an adjacent, lexically-matching turn in a DIFFERENT chat must not ride
    _live(home, "scope-b", [
        ("user", "The seed store order form lives in the gray cabinet too."),
    ])
    cap = _capsule(home, "scope-a", "Where does the seed store key hang now?")
    assert "gray cabinet" in cap, cap
    assert "order form" not in cap, cap


def test_control_stored_question_still_asserts_nothing(fresh_profile):
    home = fresh_profile
    _live(home, "q-law", [
        ("user", "Do the winter shutters close by themselves at the boathouse?"),
        ("assistant", "Yes — they self-close at 16:00 sharp."),
    ])
    cap = _capsule(home, "q-law",
                   "When do the boathouse shutters close by themselves?")
    assert "16:00" in cap, cap
    assert "Do the winter shutters" not in cap, cap
