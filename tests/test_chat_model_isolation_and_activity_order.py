"""Owner regressions plus different-model, background-send and late-event controls."""
from __future__ import annotations

import json

from tests.chat_page_js_harness import DOM, run_node, script


def drive(code: str) -> dict:
    result = run_node(DOM + script() + "\nawait new Promise(r => setImmediate(r));\n" + code)
    assert result["errors"] == []
    return result


def test_chat_pin_survives_new_chat_local_selection_and_reopen():
    result = drive("""
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');
setModelValue('nvidia/nemotron-3.5-lightning:free');
const first = displayedChat;
newChat();
const fresh = modelValue;
setModelValue(LOCAL_ONLY_MODEL);
const second = displayedChat;
await openSession(first);
out({fresh, reopened: modelValue, background: effectiveModel(second, 'Explain cellular respiration')});
""")
    assert result["fresh"] == "vool"
    assert result["reopened"] == "nvidia/nemotron-3.5-lightning:free"
    assert result["background"] == "vool-local-only"


def test_different_clouds_and_storage_restore_do_not_inherit_project_changes():
    result = drive("""
const a = 'openclaw:bbbbbbbbbbbbbbbbbbbb', b = 'openclaw:cccccccccccccccccccc';
setDisplayedChat(a); setModelValue('vendor/alpha:free'); view.projectId = 'shared';
setDisplayedChat(b); setModelValue('vendor/beta:free'); view.projectId = 'shared';
rememberActiveModel();
_lastSessions = [{session_id:a, project_id:'shared'}, {session_id:b, project_id:'shared'}];
await openSession(a);
const reopened = modelValue;
modelValue = 'vool'; restoreChatModel(a);
out({reopened, restored:modelValue, background:effectiveModel(b, 'Write a short poem')});
""")
    assert result["reopened"] == result["restored"] == "vendor/alpha:free"
    assert result["background"] == "vendor/beta:free"


def test_delayed_cloud_selection_stays_with_originating_chat():
    result = drive("""
const a = 'openclaw:dddddddddddddddddddd', b = 'openclaw:eeeeeeeeeeeeeeeeeeee';
setDisplayedChat(a); setModelValue('vool');
let release, posted;
fetch = async (url, opts) => { posted = JSON.parse(opts.body); return await new Promise(r => release=r); };
const pending = switchCloudModel('vendor/alpha:free', 'Alpha');
setDisplayedChat(b); setModelValue(LOCAL_ONLY_MODEL);
release({ok:true,status:200,json:async()=>({ok:true,model:'vendor/alpha:free',provider:'openrouter'})});
await pending;
out({posted, displayed:modelValue, a:effectiveModel(a,'Explain forests')});
""")
    assert result["posted"]["session_id"] == "openclaw:dddddddddddddddddddd"
    assert result["displayed"] == "vool-local-only"
    assert result["a"] == "vendor/alpha:free"


def test_activity_newest_first_without_corrupting_event_pairing_or_source():
    result = drive("""
const events = [
 {seq:1,event_type:'task_received',message:'old request',client_turn_id:'a'},
 {seq:2,event_type:'tool_selected',tool:'web.search',message:'search starts',client_turn_id:'a'},
 {seq:3,event_type:'tool_executed',tool:'web.search',message:'search ends',client_turn_id:'a'},
 {seq:4,event_type:'task_completed',message:'new result',client_turn_id:'a'}
];
const original = JSON.stringify(events), tree = buildActivityTree(events);
view.chatLedger = events;
const log = eventLogSections();
out({seqs:tree.categories.flatMap(c=>c.items.map(i=>(i.endEvent||i.startEvent).seq)),
 paired:tree.categories.flatMap(c=>c.items).filter(i=>i.startEvent&&i.endEvent).length,
 rows:log[0].rows, unchanged:JSON.stringify(events)===original});
""")
    assert result["seqs"][0] == 4
    assert result["paired"] == 1
    assert result["rows"][0].endswith("new result")
    assert result["rows"][-1].endswith("old request")
    assert result["unchanged"]


def test_late_cloud_reply_cannot_replace_a_newer_local_choice_in_same_chat():
    result = drive("""
setDisplayedChat('openclaw:dddddddddddddddddddd'); setModelValue('vool');
let release;
fetch = async (url, opts) => opts && opts.method === 'POST'
  ? await new Promise(r => release=r)
  : {ok:true,json:async()=>({connections:[]})};
const pending = switchCloudModel('vendor/alpha:free', 'Alpha');
setModelValue(LOCAL_ONLY_MODEL);
release({ok:true,status:200,json:async()=>({ok:true,model:'vendor/alpha:free',provider:'openrouter'})});
const accepted = await pending;
out({accepted, selected:modelValue});
""")
    assert result["accepted"] is False
    assert result["selected"] == "vool-local-only"


def test_late_server_write_cannot_overwrite_newer_chat_selection(tmp_path, monkeypatch):
    from core import cloud_escalation_policy as cep

    monkeypatch.setattr(cep, "_store_path", lambda: tmp_path / "policy.json")
    sid = "openclaw:" + "a" * 20
    assert cep.save_chat_model_selection(sid, model="new/free", provider="openrouter", revision=200)
    assert not cep.save_chat_model_selection(sid, model="old/free", provider="openrouter", revision=100)
    assert cep.chat_model_selection(sid)["model"] == "new/free"


def test_scoped_api_selection_keeps_global_policy_and_other_chats(tmp_path, monkeypatch):
    from core import cloud_escalation_policy as cep
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    monkeypatch.setattr(cep, "_store_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr("core.cloud_model_control.classify_cloud_model_cost",
                        lambda **kw: {"cost_state": "free", "reason_code": ""})
    monkeypatch.setattr("core.cloud_model_control.set_cloud_model",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("global mutation")))
    runtime = RuntimeServices(display_name="test")
    before = cep.load_policy()
    sessions = ["openclaw:" + char * 20 for char in "abc"]
    for sid, model in zip(sessions[:2], ["vendor/alpha:free", "vendor/beta:free"], strict=True):
        response = dispatch_post(path="/api/cloud/model", body={"model": model, "session_id": sid},
                                 headers={"content-type": "application/json"}, runtime=runtime,
                                 model_name="vool", workspace_root_provider=lambda: str(tmp_path),
                                 client_host="127.0.0.1")
        assert response.status == 200, response.body
    found = []
    for sid in sessions:
        response = dispatch_get(path="/api/cloud/model", query={"session_id": [sid]}, runtime=runtime,
                                model_name="vool", client_host="127.0.0.1")
        assert response.status == 200
        found.append(json.loads(response.body)["model"])
    assert found == ["vendor/alpha:free", "vendor/beta:free", ""]
    assert cep.load_policy() == before


def test_scoped_paid_selection_still_requires_confirmation_and_persistence(tmp_path, monkeypatch):
    from core import cloud_escalation_policy as cep
    from core.web.api.registry_authorities import set_cloud_model_authority
    from core.web.api.runtime import RuntimeServices

    monkeypatch.setattr(cep, "_store_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr("core.cloud_model_control.classify_cloud_model_cost",
                        lambda **kw: {"cost_state": "paid", "reason_code": ""})
    monkeypatch.setattr("core.model_price_acceptance.price_above_acceptance", lambda *a: None)
    accepted = []
    monkeypatch.setattr("core.model_price_acceptance.record_acceptance", lambda *a, **kw: accepted.append(a))
    body = {"model": "vendor/paid", "session_id": "openclaw:" + "f" * 20}
    runtime = RuntimeServices(display_name="test")
    response = set_cloud_model_authority(body, {}, runtime)
    assert response.status == 409
    assert cep.chat_model_selection(body["session_id"])["model"] == ""
    assert not accepted
    response = set_cloud_model_authority(dict(body, confirm_paid=True), {}, runtime)
    assert response.status == 200
    assert accepted
    assert cep.chat_model_selection(body["session_id"])["model"] == "vendor/paid"
    monkeypatch.setattr(cep, "_write_raw", lambda raw: False)
    response = set_cloud_model_authority(dict(body, model="vendor/another", confirm_paid=True), {}, runtime)
    assert response.status == 503
    assert cep.chat_model_selection(body["session_id"])["model"] == "vendor/paid"
    assert set_cloud_model_authority(body, {}, runtime, client_host="10.0.0.2").status == 403
    assert set_cloud_model_authority(dict(body, session_id="../bad"), {}, runtime).status == 400
