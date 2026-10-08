"""Forgetting works on the real turn path: tell, retract, ask again (2026-10-07).

Measured before this change (landing 94d98d6 / af953bd, served check): "Forget the storage unit code I gave you, it is wrong."
answered "Forget applied. Removed 0 memory entries." and the next "What is my storage unit code?" still served 5906; nine
other retraction phrasings never reached the forget lane, the temporal owner withdrew the record, and the whole-turn lane
rendered it anyway with its value; a source deleted or altered after discovery was served by the same lane. Three laws:

1. VOOL never says it forgot when nothing was removed: the forget lane either removes a stored entry or hands the turn to
   the ordinary path, where it is stored as a retraction.
2. After any retraction the user's own words carry, the next recall serves the withdrawn value in no lane.
3. The whole-turn lane renders only what the store holds at render time, as ranked.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile  # noqa: F401

pytestmark = pytest.mark.usefixtures("_hash_backend")


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def _front_door(profile, chat: str, text: str, stated: str) -> tuple[bool, str]:
    """The served path for one user turn: the memory command lane first, then the turn is recorded (the fast path and
    the ordinary path both append the conversation event)."""
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    policy = resolve_memory_access_policy(chat_id=chat)
    handled, reply = maybe_handle_memory_command(
        text, session_id=chat, access_policy=policy,
        source_context={"surface": "api", "platform": "api", "chat_id": chat, "runtime_home": str(profile)},
    )
    _ingest(profile, chat, [(stated, text)])
    return handled, reply


_RETRACTIONS = [
    "Forget the storage unit code I gave you, it is wrong.",
    "forget the storage unit code, it was a typo",
    "Kindly disregard the storage unit code from Monday.",
    "u can ignore that storage unit code lol",
    "Could you ignore what I said about the storage unit? The code changed.",
]


@pytest.mark.parametrize("retraction", _RETRACTIONS)
def test_tell_retract_ask_serves_the_withdrawn_value_in_no_lane(tmp_path, retraction):
    profile = _profile(tmp_path)
    chat = "forgetting"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "My storage unit code is 5906."),
                            (_ts("2026-02-20T09:00:00"), "The shed roof needs new tar paper.")])
    handled, reply = _front_door(profile, chat, retraction, _ts("2026-03-05T09:00:00"))
    capsule, telemetry = _capsule(profile, chat, "What is my storage unit code?")
    assert "5906" not in capsule, (retraction, handled, reply, capsule)
    if handled:
        # law 1: a reply that claims an erasure names a count above zero; otherwise it says nothing was removed
        assert "Removed 0" not in reply and ("Forget applied" not in reply or "Removed 0" not in reply), reply
        assert not ("Forget applied" in reply and "5906" not in capsule and "Nothing was removed" in reply), reply
    lane = [r for r in telemetry.get("evidence_refs", []) if r.get("delivery_stage") == "whole_turn_lane" and r.get("delivered")]
    assert all("5906" not in str(r.get("body") or r.get("summary") or "") for r in lane), lane


def test_a_forget_that_matches_nothing_is_not_claimed_as_applied(tmp_path):
    profile = _profile(tmp_path)
    chat = "forgetting"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "My storage unit code is 5906.")])
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    handled, reply = maybe_handle_memory_command(
        "Forget the storage unit code I gave you, it is wrong.", session_id=chat,
        access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"surface": "api", "platform": "api", "chat_id": chat, "runtime_home": str(profile)},
    )
    assert not (handled and "Forget applied" in reply), (handled, reply)
    assert handled and reply.startswith("Nothing was removed"), (handled, reply)


def test_a_reminder_is_not_a_retraction_on_the_same_path(tmp_path):
    profile = _profile(tmp_path)
    chat = "forgetting"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "My storage unit code is 5906.")])
    _front_door(profile, chat, "Don't forget the storage unit code.", _ts("2026-03-05T09:00:00"))
    capsule, _ = _capsule(profile, chat, "What is my storage unit code?")
    assert "5906" in capsule, capsule


def test_the_lane_renders_nothing_the_store_no_longer_holds(tmp_path, monkeypatch):
    """A turn deleted between the lane's ranking and its render is not served from the ranked copy."""
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "forgetting"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "The ceramic kiln code is 7731."),
                            (_ts("2026-02-20T09:00:00"), "The shed roof needs new tar paper.")])
    owner = cr._whole_turn_units
    deleted: list[str] = []

    def delete_after_ranking(*args, **kwargs):
        units = owner(*args, **kwargs)
        if units and not deleted:
            memory = VoolMemory(runtime_home=str(profile))
            try:
                for unit in units:
                    for occurrence in unit:
                        if "7731" in str(getattr(occurrence, "body", "")):
                            assert memory.occurrence_delete(occurrence_id=occurrence.occurrence_id) == 1
                            deleted.append(occurrence.occurrence_id)
            finally:
                memory.close()
        return units

    monkeypatch.setattr(cr, "_whole_turn_units", delete_after_ranking)
    capsule, telemetry = _capsule(profile, chat, "What is the ceramic kiln code?")
    assert deleted, "the control never reached the ranking boundary"
    lane = [r for r in telemetry.get("evidence_refs", []) if r.get("delivery_stage") == "whole_turn_lane" and r.get("delivered")]
    assert not any(r.get("occurrence_id") in deleted for r in lane), lane
    lane_text = capsule.split("Evidence turns", 1)[1] if "Evidence turns" in capsule else ""
    assert "7731" not in lane_text, capsule
    assert any(r.get("occurrence_id") in deleted for r in telemetry.get("whole_turn_lane_refused", [])), telemetry.get("whole_turn_lane_refused")


@pytest.mark.parametrize("target", ["its password", "this is my old address", "that's my pin", "which is my pin"])
def test_a_forget_target_without_a_reason_clause_is_kept_whole(target):
    """Review 2026-10-07: the reason-tail strip needs a real separator; a target that merely starts with "its",
    "this is" or "that's" is the whole target, never an empty one."""
    from core.persistent_memory import _normalize_forget_target

    assert _normalize_forget_target(target) == target.strip(" .,!?")


@pytest.mark.parametrize("text, head", [
    ("the storage unit code I gave you, it is wrong", "the storage unit code I gave you"),
    ("the storage unit code it was a typo", "the storage unit code"),
    ("my pin? its wrong", "my pin"),
    ("the old route - because it changed", "the old route"),
])
def test_a_reason_clause_after_a_separator_is_not_the_target(text, head):
    from core.persistent_memory import _normalize_forget_target

    assert _normalize_forget_target(text) == head


def test_a_second_forget_of_an_erased_fact_is_confirmed_as_already_forgotten(tmp_path):
    """Review 2026-10-07: a tombstoned fact removes 0 on the next forget; that is "already forgotten", not a no-match
    (which falls through) and not "Forget applied"."""
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    from core.runtime_paths import configure_runtime_home

    profile = _profile(tmp_path)
    configure_runtime_home(profile)
    chat = "forgetting-twice"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    ctx = {"surface": "api", "platform": "api", "chat_id": chat, "runtime_home": str(profile)}
    handled, reply = maybe_handle_memory_command("Remember that the boathouse gate code is QX-4471", session_id=chat,
                                                 access_policy=policy, source_context=ctx)
    assert handled, reply
    # the exact stored text is the target, so the second forget meets its tombstone (a tombstone keeps only a
    # salted digest of the text; a keyword that is not the whole text cannot be matched to one)
    handled, reply = maybe_handle_memory_command("Forget the boathouse gate code is QX-4471", session_id=chat,
                                                 access_policy=policy, source_context=ctx)
    assert handled and reply.startswith("Forget applied"), reply
    handled, reply = maybe_handle_memory_command("Forget the boathouse gate code is QX-4471", session_id=chat,
                                                 access_policy=policy, source_context=ctx)
    assert handled and "already forgotten" in reply and "Forget applied" not in reply, (handled, reply)
    # a keyword that matches nothing stored and no tombstone text says so, and claims nothing
    handled, reply = maybe_handle_memory_command("Forget QX-9999", session_id=chat, access_policy=policy, source_context=ctx)
    assert handled and reply.startswith("Nothing was removed") and "Forget applied" not in reply, (handled, reply)


@pytest.mark.parametrize("text, expected", [
    ("my Thanks Giving plans", "my Thanks Giving plans"),       # a courtesy word inside the target is not a tail
    ("my pin please", "my pin"),                                  # a courtesy word at the very end is
    ("my pin because it changed", "my pin because it changed"),  # a clause after one content word, no punctuation: kept whole
    ("my pin, please", "my pin"),
])
def test_courtesy_and_clause_words_strip_only_where_the_review_allows(text, expected):
    from core.persistent_memory import _normalize_forget_target

    assert _normalize_forget_target(text) == expected


def test_a_forget_naming_a_phrase_never_deletes_every_entry_sharing_a_stopword(tmp_path):
    """Review 2026-10-07 (release checks): "Forget my Thanks Giving plans" stripped to "my" on 643f8fbe and the keyword
    path removed all four entries. The three unrelated facts must survive, and the reply must not claim them."""
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import list_memory_entries, resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    from core.runtime_paths import configure_runtime_home

    profile = _profile(tmp_path)
    configure_runtime_home(profile)
    chat = "forgetting-broad"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    ctx = {"surface": "api", "platform": "api", "chat_id": chat, "runtime_home": str(profile)}
    for fact in ("My dog is called Rex.", "My car is a red Volvo.", "My sister lives in Kaunas.",
                 "Thanks Giving is at my aunt's house this year."):
        handled, reply = maybe_handle_memory_command(f"Remember that {fact}", session_id=chat, access_policy=policy, source_context=ctx)
        assert handled, reply
    before = [str(r.get("text") or "") for r in list_memory_entries(access_policy=policy, limit=20)]
    assert len(before) == 4, before
    handled, reply = maybe_handle_memory_command("Forget my Thanks Giving plans", session_id=chat, access_policy=policy, source_context=ctx)
    after = [str(r.get("text") or "") for r in list_memory_entries(access_policy=policy, limit=20)]
    assert any("Rex" in t for t in after) and any("Volvo" in t for t in after) and any("Kaunas" in t for t in after), (reply, after)
    assert "Removed 4" not in reply and "Removed 3" not in reply and "Forget applied" not in reply, reply
