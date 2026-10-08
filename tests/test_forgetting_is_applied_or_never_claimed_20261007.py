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
from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile

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


def test_a_forget_that_lands_while_the_capsule_is_assembled_reaches_no_carrier(tmp_path, monkeypatch):
    """Review 2026-10-08 (second review of the forget lane): a forget completed after the capsule's lines were packed
    (here: as the whole-turn lane faces its laws) left the value in the distilled lines above the lane, though the
    lane itself refused the erased turn. Forget blocks on every carrier, so the final block carries no "5906"."""
    from core.memory.entries import resolve_memory_access_policy

    profile = _profile(tmp_path)
    chat = "forget-mid-assembly"
    _ingest(profile, chat, [
        (_ts("2026-02-03T09:00:00"), "My storage unit code is 5906."),
        (_ts("2026-02-20T09:00:00"), "The shed roof needs new tar paper."),
    ])
    lane_laws = cr._whole_turn_units_facing_the_laws
    forgot = []

    def forget_during_assembly(*args, **kwargs):
        if not forgot:
            forgot.append(cr.forget_session_memory(
                chat, "5906", access_policy=resolve_memory_access_policy(chat_id=chat),
                source_context={"chat_id": chat, "runtime_home": str(profile)}))
        return lane_laws(*args, **kwargs)

    monkeypatch.setattr(cr, "_whole_turn_units_facing_the_laws", forget_during_assembly)
    capsule, telemetry = _capsule(profile, chat, "As of 20 February 2026, what was my storage unit code?")
    assert forgot, "the forget never ran inside the assembly"
    assert "5906" not in capsule, (capsule, telemetry.get("whole_turn_lane_refused"))
    assert "5906" not in str(telemetry.get("evidence_packet_snapshot") or ""), telemetry.get("evidence_packet_snapshot")
    delivered = [ref for ref in telemetry.get("evidence_refs", []) if ref.get("delivered")]
    assert not any("5906" in str(ref.get("line") or "") for ref in delivered), delivered
    assert "tar paper" in capsule, capsule  # the unrelated record still rides


def test_a_multi_line_record_forgotten_during_assembly_leaves_whole(tmp_path, monkeypatch):
    """A record is dropped whole, whatever its lines start with: "<code>7731</code>" under its opening line."""
    from core.memory.entries import resolve_memory_access_policy

    profile = _profile(tmp_path)
    chat = "forget-mid-assembly-multiline"
    _ingest(profile, chat, [
        (_ts("2026-02-03T09:00:00"), "Here is the yard gate code from the landlord email:\n<code>7731</code>\nKeep it handy."),
        (_ts("2026-02-20T09:00:00"), "The yard gate squeaks, it needs oil."),
    ])
    lane_laws = cr._whole_turn_units_facing_the_laws

    def forget_during_assembly(*args, **kwargs):
        cr.forget_session_memory(chat, "7731", access_policy=resolve_memory_access_policy(chat_id=chat),
                                 source_context={"chat_id": chat, "runtime_home": str(profile)})
        return lane_laws(*args, **kwargs)

    monkeypatch.setattr(cr, "_whole_turn_units_facing_the_laws", forget_during_assembly)
    capsule, telemetry = _capsule(profile, chat, "What is the yard gate code from the landlord email?")
    assert "7731" not in capsule and "Keep it handy" not in capsule, capsule
    assert "7731" not in str(telemetry.get("evidence_packet_snapshot") or "")


def test_a_withheld_pair_names_the_member_whose_value_was_erased(tmp_path):
    """Review 2026-10-08: a [user, assistant] unit withheld because the assistant turn was erased named the still-active
    user turn, so the packet kept the erased assistant's rows. The refusal names the erased member."""
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "forgetting-pair"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    append_conversation_event(
        session_id=chat, user_input="Log the harbor inventory order.",
        assistant_output="The harbor inventory order code is TARP-9.",
        source_context={"chat_id": chat, "runtime_home": str(profile)}, access_policy=policy)
    memory = VoolMemory(runtime_home=str(profile))
    try:
        units = cr._whole_turn_units(memory, "Restate the harbor inventory order you provided.", [], "",
                                    session_id=chat, pair_turns=True)
    finally:
        memory.close()
    pair = next(unit for unit in units if len(unit) == 2)
    assert [member.role for member in pair] == ["user", "assistant"]
    cr.forget_session_memory(chat, "TARP-9", access_policy=policy,
                             source_context={"chat_id": chat, "runtime_home": str(profile)})
    refused = []
    assert not cr._whole_turn_units_facing_the_laws([pair], verdicts={}, runtime_home=str(profile), refused=refused)
    assert [row["occurrence_id"] for row in refused] == [pair[1].occurrence_id], refused
    assert pair[1].occurrence_id in cr._hydration_refusals([], refused), refused


def _forget_inside(monkeypatch, profile, chat: str, token: str) -> list:
    """Run a real forget of *token* once, right after the lane rendered its lines (the capsule is mid-assembly)."""
    from core.memory.entries import resolve_memory_access_policy

    render_lane = cr._whole_turn_lines
    done: list = []

    def after_lane(*args, **kwargs):
        lines = render_lane(*args, **kwargs)
        if not done:
            done.append(cr.forget_session_memory(chat, token, access_policy=resolve_memory_access_policy(chat_id=chat),
                                                 source_context={"chat_id": chat, "runtime_home": str(profile)}))
        return lines

    monkeypatch.setattr(cr, "_whole_turn_lines", after_lane)
    return done


def test_an_unreadable_revocation_ledger_delivers_no_memory(tmp_path, monkeypatch):
    """Review 2026-10-08 (A): a forget completed mid-assembly, then the delivery read of the ledger failed and was read
    as "nothing revoked", so "5906" was delivered. An unreadable ledger is never an empty one: no memory rides."""
    import sqlite3

    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "forget-ledger-unreadable"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "My storage unit code is 5906."),
                            (_ts("2026-02-20T09:00:00"), "The shed roof needs new tar paper.")])
    done = _forget_inside(monkeypatch, profile, chat, "5906")
    read = VoolMemory.revoked_tokens

    def failing_read(self, scope, **kwargs):
        if done:
            raise sqlite3.OperationalError("ledger unreadable")
        return read(self, scope, **kwargs)

    monkeypatch.setattr(VoolMemory, "revoked_tokens", failing_read)
    capsule, telemetry = _capsule(profile, chat, "What is my storage unit code?")
    assert done, "the forget never ran inside the assembly"
    assert "5906" not in capsule, (capsule, telemetry)


def test_a_token_split_over_bullet_lines_is_still_forgotten(tmp_path, monkeypatch):
    """Review 2026-10-08 (B): a record holding "- TARP" and "- 9" on separate bullet lines was cut apart by a line
    parser, so the normalized token "tarp 9" survived. Records are removed whole, as packed."""
    profile = _profile(tmp_path)
    chat = "forget-split-bullets"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"),
                             "Here is the yard gate code from the landlord email:\n- TARP\n- 9\n- Keep it handy."),
                            (_ts("2026-02-20T09:00:00"), "The yard gate squeaks, it needs oil.")])
    done = _forget_inside(monkeypatch, profile, chat, "TARP-9")
    capsule, telemetry = _capsule(profile, chat, "What is the yard gate code from the landlord email?")
    assert done, "the forget never ran inside the assembly"
    assert not cr.text_carries_revoked_token(capsule, ("TARP-9",)), (capsule, telemetry)
    assert "Keep it handy" not in capsule, capsule


def test_a_revoked_token_on_a_still_active_record_is_not_served(tmp_path):
    """Review 2026-10-08: a revocation in the ledger whose record was never erased (a partial erase, a rebuilt source)
    served the value on the next recall. The ledger binds every delivery, not only the erasure."""
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "forget-ledger-only"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "My storage unit code is 5906."),
                            (_ts("2026-02-20T09:00:00"), "The shed roof needs new tar paper.")])
    memory = VoolMemory(runtime_home=str(profile))
    try:
        memory.record_revocation("5906", chat_id=chat)
    finally:
        memory.close()
    capsule, telemetry = _capsule(profile, chat, "What is my storage unit code?")
    assert "5906" not in capsule, (capsule, telemetry)


def test_a_replacement_the_as_of_law_refuses_is_never_named_on_the_earlier_row(tmp_path, monkeypatch):
    """Review 2026-10-08 (C): with the later row left out of a bounded packet, the earlier row still said
    '[REPLACED later by "47 crowns" ...]' to an as-of September ask."""
    import core.evidence_compiler as compiler
    from tests.test_anchor_carrier_stored_record_20261003 import _as_of_capsule

    monkeypatch.setenv("VOOL_MEMORY_RECEIPTS", "1")
    monkeypatch.setenv("VOOL_EVIDENCE_COMPILER", "1")
    profile = _profile(tmp_path)
    chat = "forget-replacement-note"
    _ingest(profile, chat, [
        (_ts("2026-02-03T09:00:00"), "Session date: 10:00 am on 3 February, 2026\nOrrin: The allotment rent at Wexcombe is 41 crowns a season."),
        (_ts("2026-11-06T09:00:00"), "Session date: 10:00 am on 6 November, 2026\nOrrin: Wexcombe allotment rent goes up to 47 crowns from "
                                     "November with irrigation access and garden maintenance fees" + " covering the paths and fences" * 14 + "."),
    ])
    compile_packet = compiler.compile_packet
    packets: list = []

    def bounded(*args, **kwargs):
        packet = compile_packet(*args, **{**kwargs, "max_tokens": 80, "allow_hop": False})
        packets.append(packet)
        return packet

    monkeypatch.setattr(compiler, "compile_packet", bounded)
    capsule = _as_of_capsule(profile, chat, "as of september 2026 how much was the wexcombe allotment rent", "2026-09-05T00:00:00")
    assert packets and [fact["value"] for fact in packets[0].facts] == ["41 crowns"], packets
    assert packets[0].facts[0].get("replaced_by"), "precondition: the shown row names its replacement"
    assert compiler.PACKET_HEADER in capsule and "41 crowns" in capsule, capsule
    assert "47 crowns" not in capsule, capsule


def test_records_that_contain_each_other_leave_whole_and_the_rest_still_rides(tmp_path):
    """Review 2026-10-08: a carrying record whose text sits inside another carrying record made the cut look
    incomplete, withheld the block and still claimed the unrelated records delivered. Both leave; the rest rides."""
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "forget-nested-records"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "My locker is bay 12."),
                            (_ts("2026-02-04T09:00:00"), "Again: my locker is bay 12."),
                            (_ts("2026-02-05T09:00:00"), "My locker key hangs on the blue hook by the locker.")])
    control, _ = _capsule(profile, chat, "Where is my locker and its key?")
    assert "blue hook" in control and control.count("bay 12") == 2, control  # precondition: all three ride
    memory = VoolMemory(runtime_home=str(profile))
    try:
        memory.record_revocation("bay 12", chat_id=chat)
    finally:
        memory.close()
    capsule, telemetry = _capsule(profile, chat, "Where is my locker and its key?")
    assert not cr.text_carries_revoked_token(capsule, ("bay 12",)), (capsule, telemetry)
    assert "blue hook" in capsule, (capsule, telemetry)
    delivered = [ref for ref in telemetry.get("evidence_refs", []) if ref.get("delivered")]
    assert not any(cr.text_carries_revoked_token(str(ref.get("line") or ""), ("bay 12",)) for ref in delivered), delivered


def _forget_command(profile, chat: str, text: str) -> tuple[bool, str]:
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    return maybe_handle_memory_command(text, session_id=chat, access_policy=resolve_memory_access_policy(chat_id=chat),
                                       source_context={"chat_id": chat, "runtime_home": str(profile)})


@pytest.mark.parametrize("target", ["ZX-41", "stored memory", "created files", "audit in progress"])
def test_a_no_match_reply_survives_the_final_claim_check_whatever_the_target_says(tmp_path, target):
    """Review 2026-10-08 (third review): the forget reply quotes the target back, and the final claim check read
    'matched "stored memory"' as a claim that something was stored ("created files": a claimed execution; "audit in
    progress": a claimed running task) and replaced what the forget command did. A quoted echo of the user's own
    words is a mention, not a claim."""
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    profile = _profile(tmp_path)
    chat = "forget-literal-target"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "My locker key hangs on the blue hook.")])
    prompt = "forget " + target
    handled, reply = _forget_command(profile, chat, prompt)
    assert handled and reply.startswith("Nothing was removed"), reply
    final = enforce_final_action_honesty({"response": reply, "mode": "advice_only"}, user_input=prompt,
                                         session_id=chat, source_context={"chat_id": chat})
    assert final["response"] != reply, final


def test_a_claim_beside_a_quoted_echo_is_still_checked(tmp_path):
    """The quoted echo is blanked for the claim check only: a reply that claims a write outside the quotes is still
    replaced."""
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    prompt = "please keep ZX-41"
    reply = 'I stored it in workspace memory: "ZX-41".'
    final = enforce_final_action_honesty({"response": reply, "mode": "advice_only"}, user_input=prompt,
                                         session_id="forget-echo-claim", source_context={"chat_id": "forget-echo-claim"})
    assert final["response"] != reply, final


def test_a_failed_erasure_is_never_reported_as_no_match(tmp_path, monkeypatch):
    """Review 2026-10-08 (third review): the semantic store denied its read, the value stayed recallable, and the reply
    said "Forget found no memory ... that matched". A failed erasure is unknown, and says so."""
    import sqlite3

    profile = _profile(tmp_path)
    chat = "forget-store-failure"
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), "My storage unit code is MINT-84.")])
    control, _ = _capsule(profile, chat, "What is my storage unit code?")
    assert "MINT-84" in control, control
    open_store = cr._open_memory_for_runtime

    def denying_store(home):
        memory = open_store(home)

        def authorize(action, table, *rest):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == "memory_nodes" else sqlite3.SQLITE_OK

        memory._conn.set_authorizer(authorize)
        return memory

    with monkeypatch.context() as patch:
        patch.setattr(cr, "_open_memory_for_runtime", denying_store)
        handled, reply = _forget_command(profile, chat, "forget MINT-84")
    assert handled, reply
    assert "no memory" not in reply.lower() and "Forget applied" not in reply, reply
    assert "could not be verified" in reply, reply


def _rent_rows(newer: str, sentence: str = "The rent is 41 crowns.", value_type: str = "count"):
    from core.evidence_compiler import Packet, _render_line, obligations

    old = {"value_type": value_type, "value": "41 crowns", "sentence": sentence, "replaced_by": "r:later",
           "replaced_by_occurrence": "u:later", "receipt_id": "r:old", "occurrence_id": "u:old", "role": "user"}
    if value_type == "state":
        old["norm"] = "rent=41 crowns"
    receipts = {"r:old": {"role": "user", "statement_at": _ts("2026-02-03T09:00:00")},
                "r:later": {"role": "user", "statement_at": _ts("2026-11-06T09:00:00"),
                            "changes": [{"old_value": "41 crowns", "new_value": newer}]}}
    return Packet(obligation=obligations("How much was the rent?"), lines=[_render_line(receipts["r:old"], old, receipts)],
                  facts=[old])


@pytest.mark.parametrize("reason", ["withdrawn", "future-relative-to-as-of", "window-expired"])
@pytest.mark.parametrize("newer", ["47 crowns", '47 "gold" crowns', "47 crowns\nwith irrigation"])
def test_a_refused_replacement_leaves_no_note_whatever_its_value_holds(reason, newer):
    """Review 2026-10-08 (third review): a quote or a newline in the replacing value kept the REPLACED note."""
    from types import SimpleNamespace

    from core.evidence_compiler import filter_packet, render

    packet = _rent_rows(newer)
    assert newer in render(packet)
    refused = cr._packet_refusals(packet, verdicts={"u:later": SimpleNamespace(eligible=False, reason=reason)})
    filtered = render(filter_packet(packet, refused))
    assert "41 crowns" in filtered and newer not in filtered and "REPLACED later" not in filtered, filtered


def test_a_refused_replacement_never_cuts_the_users_own_until_date():
    """Review 2026-10-08 (third review): withholding the replacing record's "until <day>" also cut the user's own
    "until 2026-08-01" out of the quoted sentence."""
    from core.evidence_compiler import filter_packet, render

    sentence = "My allotment rent is 41 crowns until 2026-08-01 and includes the shed."
    packet = _rent_rows("47 crowns", sentence=sentence, value_type="state")
    assert sentence in render(packet) and "until 2026-11-06" in render(packet)
    filtered = render(filter_packet(packet, {"u:later": "temporal:future-relative-to-as-of"}))
    assert sentence in filtered and "until 2026-11-06" not in filtered and "47 crowns" not in filtered, filtered


def test_a_compiled_row_keeps_its_sentence_when_its_replacement_is_refused(tmp_path, monkeypatch):
    """The same through real ingestion and the compiler: "I live in Wexcombe until 2026-08-01 ..." keeps its date."""
    from core.evidence_compiler import compile_packet, filter_packet, render
    from core.vool_memory import VoolMemory

    monkeypatch.setenv("VOOL_MEMORY_RECEIPTS", "1")
    profile = _profile(tmp_path)
    chat = "forget-compiled-until"
    sentence = "I live in Wexcombe until 2026-08-01 and tend the allotment."
    _ingest(profile, chat, [(_ts("2026-02-03T09:00:00"), sentence),
                            (_ts("2026-11-06T09:00:00"), "I now live in Brindle with a large garden.")])
    memory = VoolMemory(runtime_home=str(profile))
    try:
        packet = compile_packet(memory, chat, "Where do I live?", max_tokens=2000)
    finally:
        memory.close()
    states = [fact for fact in packet.facts if fact.get("value_type") == "state"]
    old = next(fact for fact in states if fact.get("replaced_by"))
    later = next(fact for fact in states if fact["receipt_id"] == old["replaced_by"])
    assert sentence in render(packet), render(packet)
    filtered = render(filter_packet(packet, {later["occurrence_id"]: "temporal:future-relative-to-as-of"}))
    assert sentence in filtered, filtered
