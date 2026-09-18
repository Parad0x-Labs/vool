"""Operator Profile — served proofs through the production chat / API door.

Every drive here goes through ``dispatch_post`` / ``dispatch_get`` with a REAL ``VoolAgent``
(no model: every turn lands on a deterministic lane) and reads served bytes plus the durable
store, never a mocked seam. RED at base 39fc1808 (no profile authority; see the memory suite).
"""
from __future__ import annotations

import json
import threading

import pytest

from tests.operator_profile_rig import (
    OWNER,
    body_json,
    canonical_session,
    chat,
    chat_stream,
    erase,
    make_agent,
    profile_env_generator,
    run_once_in_thread,
    served_get,
    served_post,
    simulate_restart,
    store_email_credential,
    withhold,
)


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    yield from profile_env_generator(tmp_path, monkeypatch)

A = "profile-served-a"
B = "profile-served-b"


def _profile_items(agent, session: str = "") -> dict:
    query = {"session": [session]} if session else {}
    response = served_get(agent, "/api/profile", query)
    assert int(response.status) == 200, response.body
    return body_json(response)


# --- 1. explicit preference survives restart --------------------------------


def test_served_explicit_preference_persists_across_restart(profile_env):
    agent = make_agent()
    turn = chat(agent, "remember to call me Alex", A)
    assert "Saved to your profile" in turn["text"] and "preferred name -> Alex" in turn["text"]
    saved = turn["payload"].get("vool_profile") or {}
    assert saved.get("saved") and saved["saved"][0]["category"] == "preferred_name"
    simulate_restart(profile_env)
    agent2 = make_agent()
    listing = _profile_items(agent2)
    assert [i["value"] for i in listing["items"] if i["category"] == "preferred_name"] == ["Alex"]
    reply = chat(agent2, "what's my name?", B)["text"]
    assert "Alex" in reply


# --- 2. implicit preference is a candidate, not durable ---------------------


def test_served_implicit_preference_becomes_candidate_chip_not_fact(profile_env):
    agent = make_agent()
    turn = chat(agent, "call me Alex", A)
    chip = (turn["payload"].get("vool_profile") or {}).get("candidates") or []
    assert chip and chip[0]["text"] == "Remember: address you as Alex"
    assert chip[0]["actions"] == ["save", "edit", "only_this_chat"]
    assert chip[0]["candidate_id"].startswith("opi-")
    listing = _profile_items(agent)
    assert listing["items"] == [] and listing["candidates"][0]["candidate_id"] == chip[0]["candidate_id"]
    assert "Alex" not in chat(agent, "what's my name?", A)["text"]
    # the streamed door carries the same chip
    streamed = chat_stream(agent, "call me Alex", A)
    frames = [f["vool_profile"] for f in streamed["frames"] if isinstance(f.get("vool_profile"), dict)]
    assert frames and frames[0]["candidates"][0]["text"] == "Remember: address you as Alex"


# --- 3. Only-this-chat never leaks ------------------------------------------


def test_served_only_this_chat_never_leaks_to_another_chat(profile_env):
    agent = make_agent()
    chip = (chat(agent, "call me Alex", A)["payload"]["vool_profile"])["candidates"][0]
    response = served_post(agent, "/api/profile/candidate", {"candidate_id": chip["candidate_id"], "action": "only_this_chat"})
    assert int(response.status) == 200 and body_json(response)["change"]["kind"] == "saved"
    assert "Alex" in chat(agent, "what's my name?", A)["text"]
    assert "Alex" not in chat(agent, "what's my name?", B)["text"]
    assert _profile_items(agent)["items"] == []
    assert [i["scope"] for i in _profile_items(agent, A)["items"]] == ["chat"]
    exported = body_json(served_get(agent, "/api/profile/export"))
    assert exported["items"] == []


# --- 4. work / personal overrides -------------------------------------------


def test_served_work_personal_overrides(profile_env):
    agent = make_agent()
    for scope, value in (("global", "Alex"), ("work", "Alex K. | Parad0x Labs")):
        response = served_post(agent, "/api/profile/remember", {"category": "email_signature", "value": value, "scope": scope})
        assert int(response.status) == 200 and body_json(response)["change"]["kind"] == "saved"
    from core.email_tools import email_signature_for

    assert email_signature_for(session_id=canonical_session(A)) == "Alex"
    turn = chat(agent, "this is a work chat", A)
    chip = (turn["payload"].get("vool_profile") or {}).get("candidates") or []
    assert chip and chip[0]["text"] == "Remember: treat this chat as work"
    served_post(agent, "/api/profile/candidate", {"candidate_id": chip[0]["candidate_id"], "action": "only_this_chat"})
    assert email_signature_for(session_id=canonical_session(A)) == "Alex K. | Parad0x Labs"
    assert email_signature_for(session_id=canonical_session(B)) == "Alex"


# --- 5. contradiction requires resolution -----------------------------------


def test_served_contradiction_requires_resolution(profile_env):
    agent = make_agent()
    chat(agent, "remember to call me Alex", A)
    turn = chat(agent, "remember to call me Rick", A)
    assert "Replace" in turn["text"] and "Alex" in turn["text"]
    conflict = (turn["payload"].get("vool_profile") or {}).get("conflicts") or []
    assert conflict and conflict[0]["actions"] == ["replace", "scope", "keep"]
    assert "Alex" in chat(agent, "what's my name?", A)["text"]
    response = served_post(agent, "/api/profile/resolve", {"candidate_id": conflict[0]["candidate_id"], "action": "replace"})
    assert int(response.status) == 200 and body_json(response)["change"]["kind"] == "updated"
    assert "Rick" in chat(agent, "what's my name?", A)["text"]
    undo = chat(agent, "undo that", A)["text"]
    assert "Restored" in undo and "Alex" in undo
    assert "Alex" in chat(agent, "what's my name?", B)["text"]


# --- 6. signature reused ----------------------------------------------------


def test_served_signature_is_reused_by_email_reply(profile_env, monkeypatch):
    import smtplib

    sent: list[dict] = []

    class _SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            sent.append({"login": u})

        def send_message(self, message):
            sent.append({"body": message.get_content(), "from": message["From"]})

    monkeypatch.setattr(smtplib, "SMTP_SSL", _SMTP)
    monkeypatch.setattr("core.usage_quota.check_quota", lambda *a, **k: type("Q", (), {"allowed": True, "used": 0, "limit": 9})())
    monkeypatch.setattr("core.usage_quota.consume_quota", lambda *a, **k: None)
    agent = make_agent()
    store_email_credential("default")
    turn = chat(agent, "remember my signature: Alex — Parad0x Labs", A)
    assert "email signature -> Alex" in turn["text"]
    from core.email_tools import reply_email

    result = reply_email(to="bob@example.invalid", subject="hi", body="thanks", session_id=canonical_session(A))
    assert result.ok, result.message
    assert sent[-1]["body"].rstrip().endswith("Alex — Parad0x Labs")


# --- 7. default account via opaque binding ---------------------------------


def test_served_default_account_resolves_through_binding(profile_env, monkeypatch):
    import smtplib

    logins: list[str] = []

    class _SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            logins.append(u)

        def send_message(self, message):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", _SMTP)
    monkeypatch.setattr("core.usage_quota.check_quota", lambda *a, **k: type("Q", (), {"allowed": True, "used": 0, "limit": 9})())
    monkeypatch.setattr("core.usage_quota.consume_quota", lambda *a, **k: None)
    agent = make_agent()
    store_email_credential("default")
    store_email_credential("work")
    turn = chat(agent, "use my work inbox by default", A)
    assert "default email account -> credential:email.smtp.work" in turn["text"]
    listing = _profile_items(agent)
    item = next(i for i in listing["items"] if i["category"] == "default_account.email")
    assert item["value"] == {"binding_ref": "credential:email.smtp.work", "account": "work"}
    assert "app-password" not in json.dumps(listing)
    from core.email_tools import send_email

    assert send_email(to="a@b.invalid", subject="s", body="b").ok
    assert logins[-1] == "work@example.invalid"
    assert send_email(to="a@b.invalid", subject="s", body="b", account="default").ok
    assert logins[-1] == "default@example.invalid"
    bad = chat(agent, "use my nonexistent inbox by default", B)["text"]
    assert "no stored credential" in bad.lower()


# --- 8. profile never grants permission ------------------------------------


def test_served_profile_never_grants_email_permission(profile_env):
    agent = make_agent()
    store_email_credential("work")
    chat(agent, "use my work inbox by default", A)
    chat(agent, "remember to call me Alex", A)
    from core.mode_permission_policy import PermissionEffect, decide_tool_call
    from core.request_trust import OWNER_LOCAL_KEY

    ctx = {"surface": "web", OWNER_LOCAL_KEY: True, "runtime_session_id": A, "cancel_turn_id": "t-9"}
    from core import operator_profile

    lines, used = operator_profile.hydration_for_turn(OWNER, session_id=A)
    assert used
    ctx.update({"profile_used": used, "profile_context_lines": lines})
    for intent in ("email.send", "email.read", "x.trending"):
        verdict = decide_tool_call(intent=intent, arguments={"to": "a@b.c", "subject": "s", "body": "b"}, task_id="t-9", source_context=ctx)
        assert verdict.effect != PermissionEffect.ALLOW, intent
    # and the served turn that would send does not send
    turn = chat(agent, "send an email to bob@example.invalid saying hi", A)["text"]
    assert "sent" not in turn.lower() or "not" in turn.lower()


# --- 9. pasted secret refused and scrubbed ---------------------------------


def test_served_pasted_secret_is_refused_and_scrubbed(profile_env):
    agent = make_agent()
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    turn = chat(agent, f"remember my signature: Alex token={secret}", A)
    assert "never store" in turn["text"] and secret not in turn["text"]
    assert _profile_items(agent)["items"] == []
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM operator_profile_history").fetchone()[0] == 0
    finally:
        conn.close()
    from core.memory.files import conversation_log_path

    assert secret not in conversation_log_path().read_text(encoding="utf-8")
    cookie = chat(agent, "remember my signature: cookie: sid=9f8e7d6c5b4a3f2e1d0c", A)["text"]
    assert "never store" in cookie and "9f8e7d6c5b4a3f2e1d0c" not in cookie


# --- 10. WITHHOLD / ERASE block serving and model context -------------------


def test_served_withhold_and_erase_block_serving_and_context(profile_env):
    agent = make_agent()
    turn = chat(agent, "remember to call me Alex", A)
    commit = turn["payload"]["vool_response_commit"]
    fid = str(commit.get("finalization_id") or "")
    assert fid
    assert [i["value"] for i in _profile_items(agent)["items"]] == ["Alex"]
    withhold(fid)
    assert _profile_items(agent)["items"] == []
    assert "Alex" not in chat(agent, "what's my name?", B)["text"]
    from core.bootstrap_context import profile_context_lines_for_session

    assert not any("Alex" in line for line in profile_context_lines_for_session(session_id=B))
    assert body_json(served_get(agent, "/api/profile/export"))["items"] == []
    outcome = erase(fid)
    assert outcome["sweep"]["operator_profile"] == "completed"
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert "Alex" not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM operator_profile_items").fetchall()])
        assert "Alex" not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM operator_profile_history").fetchall()])
    finally:
        conn.close()


# --- 11. edit / forget / undo / export through the API ----------------------


def test_served_edit_forget_undo_export(profile_env):
    agent = make_agent()
    chat(agent, "remember to call me Alex", A)
    item = _profile_items(agent)["items"][0]
    edited = body_json(served_post(agent, "/api/profile/item", {"item_id": item["item_id"], "value": "Saul", "expected_revision": item["revision"]}))
    assert edited["ok"] and edited["item"]["revision"] == 2 and edited["item"]["value"] == "Saul"
    moved = body_json(served_post(agent, "/api/profile/scope", {"item_id": item["item_id"], "scope": "work", "expected_revision": 2}))
    assert moved["ok"] and moved["item"]["scope"] == "work"
    forgotten = body_json(served_post(agent, "/api/profile/forget", {"item_id": item["item_id"]}))
    assert forgotten["ok"] and forgotten["change"]["kind"] == "forgotten"
    assert _profile_items(agent)["items"] == []
    restored = body_json(served_post(agent, "/api/profile/restore", {"item_id": item["item_id"]}))
    assert restored["ok"] and restored["item"]["status"] == "active" and restored["item"]["value"] == "Saul"
    exported = body_json(served_get(agent, "/api/profile/export"))
    assert exported["format"] == "vool.operator_profile.v1" and [i["value"] for i in exported["items"]] == ["Saul"]
    paused = body_json(served_post(agent, "/api/profile/pause", {"paused": True}))
    assert paused["ok"] and paused["paused"] is True
    assert "paused" in chat(agent, "remember to call me Rick", A)["text"].lower()
    assert _profile_items(agent)["paused"] is True
    served_post(agent, "/api/profile/pause", {"paused": False})
    # the route family is owner-local
    assert int(served_get(agent, "/api/profile", client_host="10.0.0.9").status) == 403
    assert int(served_post(agent, "/api/profile/forget", {"item_id": item["item_id"]}, client_host="10.0.0.9").status) == 403


# --- 12. concurrent updates: revision / CAS ---------------------------------


def test_served_concurrent_edits_conflict_on_revision(profile_env):
    agent = make_agent()
    chat(agent, "remember to call me Alex", A)
    item = _profile_items(agent)["items"][0]
    statuses: list[int] = []
    barrier = threading.Barrier(2)

    def worker(value: str) -> None:
        barrier.wait()
        response = served_post(agent, "/api/profile/item", {"item_id": item["item_id"], "value": value, "expected_revision": 1})
        statuses.append(int(response.status))

    threads = [threading.Thread(target=worker, args=(v,)) for v in ("Saul", "Rick")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [200, 409]
    assert _profile_items(agent)["items"][0]["revision"] == 2


# --- 13. user A never reaches user B ---------------------------------------


def test_served_channel_user_a_never_reaches_user_b(profile_env):
    agent = make_agent()
    ctx_a = {"surface": "telegram", "platform": "telegram", "source_user_id": "111", "runtime_session_id": "tg-111"}
    ctx_b = {"surface": "telegram", "platform": "telegram", "source_user_id": "222", "runtime_session_id": "tg-222"}
    reply = run_once_in_thread(agent, "remember to call me Alice", session_id="tg-111", source_context=dict(ctx_a))
    assert "Saved to your profile" in str(reply.get("response") or "")
    from core import operator_profile

    assert operator_profile.resolve("channel:telegram:111", "preferred_name").value_text == "Alice"
    assert operator_profile.resolve("channel:telegram:222", "preferred_name") is None
    assert operator_profile.resolve(OWNER, "preferred_name") is None
    reply_b = run_once_in_thread(agent, "what's my name?", session_id="tg-222", source_context=dict(ctx_b))
    assert "Alice" not in str(reply_b.get("response") or "")
    assert _profile_items(agent)["items"] == []


# --- 14. ordinary chat stays behaviour-equivalent ---------------------------


def test_served_ordinary_chat_is_byte_equivalent_without_profile_items(profile_env, monkeypatch):
    agent = make_agent()
    baseline = chat(agent, "what is 17 * 3?", A)
    assert "vool_profile" not in baseline["payload"]
    import core.operator_profile_turn as turn_module

    calls = {"observe": 0}
    real_observe = turn_module.observe_profile_turn

    def counting(*a, **k):
        calls["observe"] += 1
        return real_observe(*a, **k)

    monkeypatch.setattr(turn_module, "observe_profile_turn", counting)
    with_hook = chat(agent, "what is 17 * 3?", B)
    monkeypatch.setattr(turn_module, "observe_profile_turn", lambda *a, **k: None)
    without_hook = chat(agent, "what is 17 * 3?", "profile-served-c")
    assert calls["observe"] == 1
    assert with_hook["text"] == without_hook["text"] == baseline["text"]
    assert "vool_profile" not in with_hook["payload"] and "vool_profile" not in without_hook["payload"]
    from core import operator_profile

    assert operator_profile.list_items(OWNER, include_candidates=True, include_deleted=True, session_id=B) == []
