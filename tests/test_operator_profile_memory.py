"""Operator Profile — the memory law, proven at the authority and interpretation seams.

RED at base 39fc1808: the runtime had no typed profile. "call me X" wrote ``user_address`` into
``data/user_preferences.json`` silently and with no history; "my name is X" was harvested by the
memory learner into a durable fact without confirmation; a second "call me Y" overwrote the first
with no resolution; a pasted token could be saved as an email signature; no account preference
existed at all; and nothing about the user's name was governed by A8 WITHHOLD/ERASE.

Each test names one clause of the law it pins. The served siblings in
``test_operator_profile_served.py`` drive the same clauses through the production HTTP door.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tests.operator_profile_rig import (
    OWNER,
    admit_finalize,
    erase,
    profile_env_generator,
    profile_source_context,
    request_scope,
    simulate_restart,
    store_email_credential,
    withhold,
)


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    yield from profile_env_generator(tmp_path, monkeypatch)

SESSION_A = "chat-a"
SESSION_B = "chat-b"


def _profile():
    from core import operator_profile

    return operator_profile


# ---------------------------------------------------------------------------
# 1. explicit persistence reports the exact change and survives a restart
# ---------------------------------------------------------------------------


def test_explicit_remember_persists_reports_exact_change_and_survives_restart(profile_env):
    p = _profile()
    change = p.remember(OWNER, "preferred_name", "Alex", session_id=SESSION_A, turn_id="t1")
    assert change.kind == "saved"
    assert "preferred name -> Alex" in change.report and "global" in change.report
    assert change.item is not None and change.item.revision == 1 and change.item.origin == "explicit"
    rows = p.history_for_item(change.item.item_id)
    assert [row["action"] for row in rows] == ["create"]
    simulate_restart(profile_env)
    after = p.resolve(OWNER, "preferred_name", session_id=SESSION_B)
    assert after is not None and after.value_text == "Alex"
    assert after.source_session_id == SESSION_A and after.source_turn_id == "t1"
    assert after.created_at and after.updated_at and after.sensitivity == "personal"


# ---------------------------------------------------------------------------
# 2. a strong implicit preference is a candidate, not a durable fact
# ---------------------------------------------------------------------------


def test_strong_implicit_preference_is_a_candidate_not_durable(profile_env):
    p = _profile()
    change = p.propose_candidate(OWNER, "preferred_name", "Alex", session_id=SESSION_A, turn_id="t1")
    assert change.kind == "candidate"
    assert change.report == "Remember: address you as Alex"
    assert change.item is not None and change.item.status == "candidate" and change.item.scope == "chat"
    assert p.resolve(OWNER, "preferred_name", session_id=SESSION_A) is None
    assert p.list_items(OWNER, session_id=SESSION_A) == []
    simulate_restart(profile_env)
    assert p.resolve(OWNER, "preferred_name", session_id=SESSION_A) is None
    # Save makes it explicit and global; Only-this-chat keeps it in its chat.
    saved = p.decide_candidate(change.item.item_id, "save")
    assert saved.kind == "saved" and saved.item is not None and saved.item.origin == "explicit" and saved.item.scope == "global"


def test_candidate_edit_and_dismiss(profile_env):
    p = _profile()
    cand = p.propose_candidate(OWNER, "preferred_name", "Alex", session_id=SESSION_A).item
    edited = p.decide_candidate(cand.item_id, "edit", value="Saul")
    assert edited.kind == "saved" and edited.item.value_text == "Saul"
    cand2 = p.propose_candidate(OWNER, "response_style", "concise", session_id=SESSION_A).item
    gone = p.decide_candidate(cand2.item_id, "dismiss")
    assert gone.kind == "forgotten"
    assert p.resolve(OWNER, "response_style", session_id=SESSION_A) is None
    assert p.decide_candidate(cand2.item_id, "save").kind == "refused_invalid"


# ---------------------------------------------------------------------------
# 3. chat-only never leaks; weak inference stays chat-local and cannot be promoted
# ---------------------------------------------------------------------------


def test_only_this_chat_never_resolves_in_another_chat(profile_env):
    p = _profile()
    cand = p.propose_candidate(OWNER, "preferred_name", "Alex", session_id=SESSION_A).item
    kept = p.decide_candidate(cand.item_id, "only_this_chat")
    assert kept.kind == "saved" and kept.item.scope == "chat" and kept.item.scope_key == SESSION_A
    assert p.resolve(OWNER, "preferred_name", session_id=SESSION_A).value_text == "Alex"
    assert p.resolve(OWNER, "preferred_name", session_id=SESSION_B) is None
    assert p.resolve(OWNER, "preferred_name") is None
    assert p.hydration_for_turn(OWNER, session_id=SESSION_B) == ([], [])
    assert all(item.scope != "chat" for item in p.list_items(OWNER))
    assert p.export_profile(OWNER)["items"] == []


def test_weak_inference_is_chat_local_and_never_promoted(profile_env):
    p = _profile()
    weak = p.note_chat_local(OWNER, "preferred_name", "Alex", session_id=SESSION_A, confidence=0.5)
    assert weak.kind == "chat_local" and weak.item.origin == "inferred" and weak.item.confidence < 0.8
    assert p.resolve(OWNER, "preferred_name", session_id=SESSION_A).value_text == "Alex"
    assert p.resolve(OWNER, "preferred_name", session_id=SESSION_B) is None
    refused = p.move_scope(weak.item.item_id, "global")
    assert refused.kind == "refused_invalid" and "inferred" in refused.report
    assert p.resolve(OWNER, "preferred_name", session_id=SESSION_B) is None


# ---------------------------------------------------------------------------
# 4. work / personal overrides resolve deterministically
# ---------------------------------------------------------------------------


def test_work_personal_overrides_resolve_deterministically(profile_env):
    p = _profile()
    p.remember(OWNER, "email_signature", "Alex", scope="global")
    p.remember(OWNER, "email_signature", "Alex K. | Parad0x Labs", scope="work")
    p.remember(OWNER, "email_signature", "S.", scope="personal")
    assert p.resolve(OWNER, "email_signature", session_id=SESSION_A).value_text == "Alex"
    p.remember(OWNER, "context_mode", "work", scope="chat", session_id=SESSION_A)
    assert p.resolve(OWNER, "email_signature", session_id=SESSION_A).value_text == "Alex K. | Parad0x Labs"
    assert p.resolve(OWNER, "email_signature", session_id=SESSION_B).value_text == "Alex"
    assert p.resolve(OWNER, "email_signature", context_mode="personal").value_text == "S."
    # chat beats project beats context beats global
    p.remember(OWNER, "email_signature", "proj", scope="project", scope_key="proj-1")
    assert p.resolve(OWNER, "email_signature", session_id=SESSION_A, project_id="proj-1").value_text == "proj"
    p.remember(OWNER, "email_signature", "chatsig", scope="chat", session_id=SESSION_A)
    assert p.resolve(OWNER, "email_signature", session_id=SESSION_A, project_id="proj-1").value_text == "chatsig"
    for _ in range(5):
        assert p.resolve(OWNER, "email_signature", session_id=SESSION_A, project_id="proj-1").value_text == "chatsig"


# ---------------------------------------------------------------------------
# 5. contradiction requires resolution
# ---------------------------------------------------------------------------


def test_contradiction_requires_replace_scope_or_keep(profile_env):
    p = _profile()
    first = p.remember(OWNER, "preferred_name", "Alex").item
    conflict = p.remember(OWNER, "preferred_name", "Rick", session_id=SESSION_A)
    assert conflict.kind == "conflict" and "Replace" in conflict.report and "Rick" in conflict.report
    assert p.resolve(OWNER, "preferred_name").value_text == "Alex"
    assert conflict.item.status == "candidate" and conflict.item.conflict_with == first.item_id
    # keep
    kept = p.resolve_conflict(conflict.item.item_id, "keep")
    assert kept.kind == "forgotten" and p.resolve(OWNER, "preferred_name").value_text == "Alex"
    # scope: both live
    conflict2 = p.remember(OWNER, "preferred_name", "Mr K", session_id=SESSION_A)
    scoped = p.resolve_conflict(conflict2.item.item_id, "scope", scope="work")
    assert scoped.kind == "saved" and scoped.item.scope == "work"
    assert p.resolve(OWNER, "preferred_name").value_text == "Alex"
    assert p.resolve(OWNER, "preferred_name", context_mode="work").value_text == "Mr K"
    # replace: new revision, undoable
    conflict3 = p.remember(OWNER, "preferred_name", "Rick", session_id=SESSION_A)
    replaced = p.resolve_conflict(conflict3.item.item_id, "replace")
    assert replaced.kind == "updated" and replaced.item.item_id == first.item_id and replaced.item.revision == 2
    assert p.resolve(OWNER, "preferred_name").value_text == "Rick"
    undone = p.restore_previous(first.item_id)
    assert undone.kind == "updated" and undone.item.value_text == "Alex" and undone.item.revision == 3


# ---------------------------------------------------------------------------
# 6/7. sensitive facts are never inferred; secrets never enter storage
# ---------------------------------------------------------------------------


def test_sensitive_identity_and_account_facts_are_never_inferred(profile_env):
    p = _profile()
    store_email_credential("work")
    assert p.propose_candidate(OWNER, "email_signature", "Alex", session_id=SESSION_A).kind == "refused_sensitive"
    assert p.note_chat_local(OWNER, "email_signature", "Alex", session_id=SESSION_A).kind == "refused_sensitive"
    assert p.propose_candidate(OWNER, "default_account.email", "email.smtp.work", session_id=SESSION_A).kind == "refused_sensitive"
    assert p.remember(OWNER, "email_signature", "Alex", origin="inferred").kind == "refused_sensitive"
    assert p.list_items(OWNER, include_candidates=True, session_id=SESSION_A) == []
    # explicit is fine
    assert p.remember(OWNER, "email_signature", "Alex").kind == "saved"


@pytest.mark.parametrize(
    "value",
    [
        "Alex token=sk-abcdefghijklmnopqrstuvwxyz123456",
        "cookie: sid=9f8e7d6c5b4a3f2e1d0c",
        "password: hunter2hunter2",
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    ],
)
def test_secrets_tokens_cookies_passwords_never_enter_profile_storage(profile_env, value):
    p = _profile()
    for writer in (
        lambda: p.remember(OWNER, "email_signature", value),
        lambda: p.remember(OWNER, "preferred_name", value),
        lambda: p.propose_candidate(OWNER, "response_style", value, session_id=SESSION_A),
        lambda: p.note_chat_local(OWNER, "response_style", value, session_id=SESSION_A),
    ):
        change = writer()
        assert change.kind == "refused_secret", change
        assert "never store" in change.report and "Scrubbed" in change.report
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in change.report
        assert "9f8e7d6c5b4a3f2e1d0c" not in change.report and "hunter2hunter2" not in change.report
    assert p.list_items(OWNER, include_candidates=True, include_deleted=True, session_id=SESSION_A) == []
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM operator_profile_history").fetchone()[0] == 0
    finally:
        conn.close()
    # edits on a good item are refused the same way
    item = p.remember(OWNER, "email_signature", "Alex").item
    assert p.edit_item(item.item_id, value, expected_revision=1).kind == "refused_secret"
    assert p.get_item(item.item_id).value_text == "Alex"


# ---------------------------------------------------------------------------
# 8. default account is an opaque credential binding
# ---------------------------------------------------------------------------


def test_default_account_resolves_through_opaque_binding_only(profile_env):
    p = _profile()
    name = store_email_credential("work")
    assert p.remember(OWNER, "default_account.email", "credential:email.smtp.nope").kind == "refused_binding"
    assert p.remember(OWNER, "default_account.email", "app-password-1234").kind == "refused_binding"
    change = p.remember(OWNER, "default_account.email", name)
    assert change.kind == "saved"
    assert change.item.value == {"binding_ref": f"credential:{name}", "account": "work"}
    assert change.item.value_text == f"credential:{name}"
    assert change.item.sensitivity == "account"
    exported = json.dumps(p.export_profile(OWNER))
    assert "app-password-1234" not in exported and "example.invalid" not in exported
    assert f"credential:{name}" in exported
    from core.email_tools import resolve_default_account

    assert resolve_default_account("smtp", principal=OWNER) == "work"
    # The chosen account is the mailbox for BOTH transports. This slot has no IMAP entry, so a read is
    # refused as send-only -- never redirected to the literal `default` slot, which was the defect the
    # account authority (core.email_accounts, 2026-09-14) replaced.
    from core.email_accounts import select_account

    assert resolve_default_account("imap", principal=OWNER) == ""
    assert select_account("imap", principal=OWNER).status == "account_send_only"
    # the module never touches the credential VALUE
    source = Path(p.__file__).read_text(encoding="utf-8")
    assert "get_credential" not in source


# ---------------------------------------------------------------------------
# 9. profile context never grants permission
# ---------------------------------------------------------------------------


def test_profile_context_never_grants_email_or_social_permission(profile_env):
    p = _profile()
    from core.mode_permission_policy import PermissionEffect, decide_tool_call

    name = store_email_credential("work")
    ctx = profile_source_context(runtime_session_id=SESSION_A, cancel_turn_id="t-1")
    before = decide_tool_call(intent="email.send", arguments={"to": "a@b.c", "subject": "s", "body": "b"}, task_id="t-1", source_context=ctx)
    assert before.effect != PermissionEffect.ALLOW
    p.remember(OWNER, "default_account.email", name)
    p.remember(OWNER, "preferred_name", "Alex")
    lines, used = p.hydration_for_turn(OWNER, session_id=SESSION_A)
    hydrated = dict(ctx, profile_used=used, profile_context_lines=lines)
    after = decide_tool_call(intent="email.send", arguments={"to": "a@b.c", "subject": "s", "body": "b", "account": "work"}, task_id="t-1", source_context=hydrated)
    assert after.effect == before.effect
    for intent in ("email.read", "x.trending", "wallet.spend"):
        verdict = decide_tool_call(intent=intent, arguments={}, task_id="t-1", source_context=hydrated)
        assert verdict.effect != PermissionEffect.ALLOW, intent
    # and the static law: the profile module imports no permission/approval/credential-value surface
    source = Path(p.__file__).read_text(encoding="utf-8")
    import re as _re

    for forbidden in ("mode_permission_policy", "capability_tokens", "execution_gate", "credential_intelligence"):
        assert not _re.search(rf"^\s*(?:from|import)\s+core\.{forbidden}\b", source, _re.MULTILINE), forbidden
    for forbidden in ("grant_internal_authority", "get_credential(", "decide_tool_call"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# 10. A8 WITHHOLD / ERASE remove serving and model-context access
# ---------------------------------------------------------------------------


def test_withhold_and_erase_gate_every_reader_and_hydration(profile_env):
    p = _profile()
    commit = admit_finalize("Saved to your profile: preferred name -> Alex (global).", request_id="req-profile-1")
    with request_scope("req-profile-1"):
        change = p.remember(OWNER, "preferred_name", "Alex", session_id=SESSION_A, turn_id="t1")
    assert change.item.request_id == "req-profile-1"
    assert p.resolve(OWNER, "preferred_name").value_text == "Alex"
    assert p.hydration_for_turn(OWNER)[0]
    withhold(commit["finalization_id"])
    assert p.resolve(OWNER, "preferred_name") is None
    assert p.get_item(change.item.item_id) is None
    assert p.list_items(OWNER) == []
    assert p.hydration_for_turn(OWNER) == ([], [])
    assert p.export_profile(OWNER)["items"] == []
    from core.user_preferences import user_address

    assert user_address() == ""
    outcome = erase(commit["finalization_id"])
    assert outcome["sweep"]["operator_profile"] == "completed"
    assert outcome["sweep_complete"] is True
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute("SELECT status, value_text FROM operator_profile_items WHERE item_id = ?", (change.item.item_id,)).fetchone()
        assert row["status"] == "deleted" and row["value_text"] == ""
        blobs = conn.execute("SELECT previous_json, next_json FROM operator_profile_history WHERE item_id = ?", (change.item.item_id,)).fetchall()
        assert all("Alex" not in (b["previous_json"] + b["next_json"]) for b in blobs)
    finally:
        conn.close()
    assert p.restore_previous(change.item.item_id).kind != "updated"


def test_erase_by_plaintext_derivative_tombstones_unlineaged_item(profile_env):
    p = _profile()
    item = p.remember(OWNER, "email_signature", "the vault passphrase is 7F3-QX9-2210").item
    commit = admit_finalize("Analysis: the vault passphrase is 7F3-QX9-2210 — canonical.", request_id="req-x")
    erase(commit["finalization_id"])
    assert p.get_item(item.item_id) is None


# ---------------------------------------------------------------------------
# 11. edit / forget / undo / export
# ---------------------------------------------------------------------------


def test_edit_forget_undo_export_and_move_scope(profile_env):
    p = _profile()
    item = p.remember(OWNER, "response_style", "concise").item
    edited = p.edit_item(item.item_id, "detailed", expected_revision=1)
    assert edited.kind == "updated" and edited.item.revision == 2 and "was 'concise'" in edited.report
    moved = p.move_scope(item.item_id, "work", expected_revision=2)
    assert moved.kind == "updated" and moved.item.scope == "work" and moved.item.revision == 3
    gone = p.forget_item(item.item_id, expected_revision=3)
    assert gone.kind == "forgotten" and p.get_item(item.item_id) is None
    assert p.export_profile(OWNER)["items"] == []
    back = p.restore_previous(item.item_id)
    assert back.kind == "updated" and back.item.status == "active" and back.item.value_text == "detailed" and back.item.revision == 5
    rows = p.history_for_item(item.item_id)
    assert [r["action"] for r in rows] == ["create", "update", "move_scope", "forget", "restore"]
    exported = p.export_profile(OWNER)
    assert exported["format"] == "vool.operator_profile.v1"
    assert [i["value"] for i in exported["items"]] == ["detailed"]
    assert {"item_id", "scope", "origin", "last_used_at", "created_at", "revision", "sensitivity"} <= set(exported["items"][0])


def test_pause_blocks_learning_and_explicit_saves_report_it(profile_env):
    p = _profile()
    p.set_paused(OWNER, True)
    assert p.is_paused(OWNER)
    assert p.remember(OWNER, "preferred_name", "Alex").kind == "paused"
    assert p.propose_candidate(OWNER, "preferred_name", "Alex", session_id=SESSION_A).kind == "paused"
    assert p.note_chat_local(OWNER, "preferred_name", "Alex", session_id=SESSION_A).kind == "paused"
    p.set_paused(OWNER, False)
    assert p.remember(OWNER, "preferred_name", "Alex").kind == "saved"


# ---------------------------------------------------------------------------
# 12. concurrent updates use revision / CAS conflict handling
# ---------------------------------------------------------------------------


def test_concurrent_edits_are_cas_guarded(profile_env):
    p = _profile()
    item = p.remember(OWNER, "response_style", "concise").item
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker(value: str) -> None:
        barrier.wait()
        try:
            change = p.edit_item(item.item_id, value, expected_revision=1)
            outcomes.append(change.kind)
        except p.RevisionConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=worker, args=(v,)) for v in ("detailed", "formal")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["conflict", "updated"]
    final = p.get_item(item.item_id)
    assert final.revision == 2 and final.value_text in {"detailed", "formal"}
    with pytest.raises(p.RevisionConflict):
        p.edit_item(item.item_id, "plain", expected_revision=1)
    assert len(p.history_for_item(item.item_id)) == 2


# ---------------------------------------------------------------------------
# 13. user A never reaches user B
# ---------------------------------------------------------------------------


def test_principal_isolation_between_users(profile_env):
    p = _profile()
    a = p.principal_for_request({"surface": "telegram", "platform": "telegram", "source_user_id": "111"})
    b = p.principal_for_request({"surface": "telegram", "platform": "telegram", "source_user_id": "222"})
    owner = p.principal_for_request(profile_source_context())
    assert a == "channel:telegram:111" and b == "channel:telegram:222" and owner == OWNER
    assert p.principal_for_request({"surface": "telegram", "platform": "telegram", "source_user_id": "111", "is_group": True}) == ""
    assert p.principal_for_request({"surface": "telegram"}) == ""
    assert p.remember(a, "preferred_name", "Alice").kind == "saved"
    assert p.resolve(b, "preferred_name") is None and p.resolve(owner, "preferred_name") is None
    assert p.list_items(b) == [] and p.export_profile(b)["items"] == []
    assert p.hydration_for_turn(b) == ([], [])
    assert p.remember("", "preferred_name", "Nobody").kind == "no_principal"


# ---------------------------------------------------------------------------
# 14. ordinary hydration is empty without items; legacy field migrates once
# ---------------------------------------------------------------------------


def test_hydration_is_empty_without_items(profile_env):
    p = _profile()
    assert p.hydration_for_turn(OWNER, session_id=SESSION_A) == ([], [])


def test_legacy_settings_field_migrates_into_the_profile_once(profile_env):
    from core import user_preferences as up

    prefs = up.load_preferences()
    prefs.user_address = "Legacy Name"
    prefs.email_signature = "Legacy Sig"
    up.save_preferences(prefs)
    assert up.user_address() == ""  # the JSON field is not an authority any more
    assert up.migrate_legacy_profile_fields() == {"preferred_name": "Legacy Name", "email_signature": "Legacy Sig"}
    assert up.user_address() == "Legacy Name"
    assert up.migrate_legacy_profile_fields() == {}
    p = _profile()
    item = p.resolve(OWNER, "preferred_name")
    assert item is not None and item.origin == "settings" and item.value_text == "Legacy Name"
    sig = p.resolve(OWNER, "email_signature")
    assert sig is not None and sig.value_text == "Legacy Sig"
    # the JSON field is no longer an authority: it is emptied, and a later read is profile-backed
    raw = json.loads((Path(profile_env["home"]) / "data" / "user_preferences.json").read_text())
    assert raw.get("user_address", "") == "" and raw.get("email_signature", "") == ""
    p.forget_item(item.item_id)
    assert up.user_address() == ""
    from core.user_identity_authority import saved_user_name

    assert not saved_user_name().known


# ---------------------------------------------------------------------------
# interpretation: the strength matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("remember to call me Alex", ("preferred_name", "Alex", "explicit", "global")),
        ("from now on call me Alex", ("preferred_name", "Alex", "explicit", "global")),
        ("Remember that my name is Alex and I live in Berlin.", ("preferred_name", "Alex", "explicit", "global")),
        ("set my name to Rick", ("preferred_name", "Rick", "explicit", "global")),
        ("call me Alex", ("preferred_name", "Alex", "strong", "global")),
        ("my name is Alex", ("preferred_name", "Alex", "strong", "global")),
        ("for work, call me Alex", ("preferred_name", "Alex", "strong", "work")),
        ("I'm Alex btw", ("preferred_name", "Alex", "weak", "chat")),
        ("from now on be concise", ("response_style", "concise", "explicit", "global")),
        ("be concise", ("response_style", "concise", "strong", "global")),
        ("I prefer detailed answers", ("response_style", "detailed", "strong", "global")),
        ("keep it short here", ("response_style", "concise", "weak", "chat")),
        ("reply in Lithuanian from now on", ("language", "Lithuanian", "explicit", "global")),
        ("reply in Lithuanian", ("language", "Lithuanian", "strong", "global")),
        ("my timezone is Europe/Berlin", ("timezone", "Europe/Berlin", "strong", "global")),
        ("remember my signature: Alex — Parad0x Labs", ("email_signature", "Alex — Parad0x Labs", "explicit", "global")),
        ("my email signature is: Alex, Parad0x Labs", ("email_signature", "Alex, Parad0x Labs", "explicit", "global")),
        ("use my work inbox by default", ("default_account.email", "work", "explicit", "global")),
        ("post from my parad0x account by default", ("default_account.social", "parad0x", "explicit", "global")),
        ("this is a work chat", ("context_mode", "work", "strong", "chat")),
        ("no bullet points", ("format_preference", "prose, no bullet points", "strong", "global")),
    ],
)
def test_interpretation_strength_matrix(text, expected):
    from core.operator_profile_interpretation import interpret_profile_turn

    proposals = interpret_profile_turn(text)
    assert proposals, text
    got = proposals[0]
    assert (got.category, got.value, got.strength, got.scope) == expected


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "what is 2 + 2?",
        "what's my name?",
        "use my work inbox",
        "short answer please, what is the capital of France?",
        "assume my name is Bender; what currency will I be paid in?",
        'he said "call me Bob" yesterday',
        "I'm tired",
        "write an email to Bob, my signature is Alex",
        "call me later",
        "my signature dish is lasagna, what wine goes with it?",
    ],
)
def test_interpretation_negative_controls(text):
    from core.operator_profile_interpretation import interpret_profile_turn

    assert [p for p in interpret_profile_turn(text) if p.action == "remember"] == [], text


def test_interpretation_forget_undo_show_and_tool_lane():
    from core.operator_profile_interpretation import interpret_profile_turn, proposals_from_tool_arguments

    assert interpret_profile_turn("forget my name")[0].action == "forget"
    assert interpret_profile_turn("forget my signature")[0] .category == "email_signature"
    assert interpret_profile_turn("undo that")[0].action == "undo"
    assert interpret_profile_turn("what do you remember about me")[0].action == "show"
    multi = interpret_profile_turn("call me Alex and be concise")
    assert [(p.category, p.strength) for p in multi] == [("preferred_name", "strong"), ("response_style", "strong")]
    tool = proposals_from_tool_arguments("profile.remember", {"category": "preferred_name", "value": "Alex", "scope": "work"})
    assert tool and tool[0].strength == "explicit" and tool[0].scope == "work"
    assert proposals_from_tool_arguments("profile.remember", {"category": "password", "value": "x"}) == []
    assert proposals_from_tool_arguments("profile.forget", {"category": "email_signature"})[0].action == "forget"
