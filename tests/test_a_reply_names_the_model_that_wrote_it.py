"""Every reply says which model wrote it, and the model menu says which local model is running.

Owner's Mac, 2026-11-27, VOOL Auto with ten local models installed: no reply said which model had
answered, and the model list marked none of them as the one in use. The record knew both: each turn
logs the lane that completed, and the local server answers which models it holds in memory. Neither
reached the screen. The reply's line under the answer carried only the place ("On this computer"),
and the model row read the installed list and nothing about what was running.

* The proof record names the model that wrote the reply: the lane that completed last, else the
  one model the turn sent anything to. Two models and no completed lane name no one.
* The line under the answer leads with that model's name.
* The newest finished reply's model is said on the Auto row, and on that model's own row.
* A local model the server holds in memory is marked running on its row; a listing that cannot
  read the server marks none.
"""
from __future__ import annotations

import json

from core.answer_route import EVENT_TYPE as DISPATCH
from core.answer_route import WHERE_THIS_COMPUTER
from core.persistent_memory import append_conversation_event
from core.proof_projection import build_turn_proof
from core.runtime_task_events import emit_runtime_event
from core.semantic.semantic_admissions import bound_request_context
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get
from tests.chat_page_js_harness import DOM, run_node, script


def _turn(session: str, turn: str, request_id: str, events: list[tuple[str, dict]]) -> dict:
    with bound_request_context(request_id):
        append_conversation_event(session_id=session, user_input="Question", assistant_output="An answer.")
    for event_type, details in events:
        emit_runtime_event(
            {"session_id": session, "runtime_session_id": session, "cancel_turn_id": turn},
            event_type=event_type,
            message=f"{event_type} for {turn}",
            details={**details, "request_id": request_id},
        )
    return build_turn_proof(session_id=session, request_id=request_id)


def _dispatch(model: str, operation: str) -> tuple[str, dict]:
    return (DISPATCH, {"route_where": WHERE_THIS_COMPUTER, "route_host": "127.0.0.1", "route_provider": "ollama",
                       "route_model": model, "route_operation": operation, "route_bytes": 100})


def test_the_record_names_the_lane_that_completed_last() -> None:
    proof = _turn("named-model-a", "turn-a", "req-named-a", [
        ("model_lane_selected", {"model_id": "qwen3:4b", "provider_id": "ollama-local"}),
        ("model_lane_completed", {"model_id": "qwen3:4b", "provider_id": "ollama-local"}),
        ("model_lane_completed", {"model_id": "ollama-local:qwen3:8b", "provider_id": "ollama-local"}),
    ])
    assert proof["compact"]["model"] == "qwen3:8b"


def test_a_reply_from_one_model_reading_the_ask_names_that_model() -> None:
    proof = _turn("named-model-b", "turn-b", "req-named-b", [_dispatch("qwen3:0.6b", "recall_judgement")])
    assert proof["compact"]["model"] == "qwen3:0.6b"


def test_two_models_and_no_completed_lane_name_no_one() -> None:
    proof = _turn("named-model-c", "turn-c", "req-named-c", [
        _dispatch("qwen3:0.6b", "recall_judgement"),
        _dispatch("qwen3:8b", "keep_ask"),
    ])
    assert proof["compact"]["model"] == ""


def test_the_listing_marks_the_model_the_server_holds_in_memory(monkeypatch) -> None:
    item = lambda name: type("Item", (), {"name": name, "size_bytes": 5_000_000_000})()  # noqa: E731
    monkeypatch.setattr("core.web.api.runtime.installed_ollama_model_inventory",
                        lambda **kwargs: (item("qwen3:8b"), item("qwen3:4b")))

    def listing() -> dict:
        response = dispatch_get(path="/api/models/local", query={}, runtime=RuntimeServices(), model_name="vool",
                                client_host="127.0.0.1", headers={})
        return {m["model_name"]: m["loaded"] for m in json.loads(bytes(response.body).decode("utf-8"))["models"]}

    monkeypatch.setenv("VOOL_LOADED_OLLAMA_MODELS", "qwen3:8b")
    assert listing() == {"qwen3:8b": True, "qwen3:4b": False}

    def unreadable(**kwargs):
        raise OSError("connection refused")

    monkeypatch.delenv("VOOL_LOADED_OLLAMA_MODELS")
    monkeypatch.setattr("core.local_ollama_inventory.loaded_ollama_model_names", unreadable)
    assert listing() == {"qwen3:8b": False, "qwen3:4b": False}


_PAGE = """
const __ok = (payload) => ({ ok: true, status: 200, headers: { get: () => null },
  json: async () => payload, text: async () => '{}', blob: async () => ({}) });
const __route = (map) => async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (Object.prototype.hasOwnProperty.call(map, path)) return map[path]();
  return __ok({ sessions: [], messages: [], queue: [], pins: [], events: [], models: [] });
};
const __find = (root, test) => {
  const stack = [root];
  while (stack.length) {
    const n = stack.shift();
    if (n && test(n)) return n;
    if (n && n.children) for (const k of n.children) stack.push(k);
  }
  return null;
};
const __proof = (model) => ({ bound: true, state: 'VERIFIED', compact: { actions: 0, sources: 0,
  cost: { tokens: 50 }, state: 'VERIFIED', model: model, route: { summary: 'local' } }, expanded: {} });
"""


def test_the_line_under_the_answer_and_the_model_menu_name_the_model() -> None:
    result = run_node(DOM + script() + _PAGE + """
await new Promise(setImmediate);
displayedChat = 'chat-1';
globalThis.fetch = __route({
  '/api/chat/proof': () => __ok(__proof('qwen3:8b')),
  '/api/tags': () => __ok({ models: [{ name: 'qwen3:8b', size: 5 }, { name: 'qwen3:4b', size: 3 }] }),
  '/api/models/local': () => __ok({ models: [{ model_name: 'qwen3:8b', loaded: true }, { model_name: 'qwen3:4b', loaded: false }] }),
});
const host = document.createElement('div');
const chip = await mountProofChip(host, 'chat-1', 'req-1', null, true);
const pop = document.getElementById('modelPop');
await renderLocalModels();
const autoHint = document.querySelector('#modelPop .pop-item[data-model="vool"]').querySelector('.pi-hint');
const row = (id) => __find(pop, (n) => String((n.attrs && n.attrs['data-model']) || '') === id);
const hintOf = (n) => { const h = n && __find(n, (k) => (k.className || '').split(' ').includes('pi-hint')); return h ? String(h.textContent) : ''; };
const r8 = row('qwen3:8b'), r4 = row('qwen3:4b');
out({
  model: String((__find(chip, (n) => String(n.className || '') === 'pc-model') || {}).textContent || ''),
  autoHint: autoHint ? String(autoHint.textContent) : '',
  loaded8: !!(r8 && String(r8.className).split(' ').includes('is-loaded')),
  loaded4: !!(r4 && String(r4.className).split(' ').includes('is-loaded')),
  hint8: hintOf(r8), hint4: hintOf(r4),
});
""")
    assert result.get("errors", []) == []
    assert result["model"] == "qwen3:8b"
    assert "last reply by qwen3:8b" in result["autoHint"]
    assert result["loaded8"] is True and result["loaded4"] is False
    assert "running now" in result["hint8"] and "wrote the last reply" in result["hint8"]
    assert "running now" not in result["hint4"] and "wrote the last reply" not in result["hint4"]


def test_a_reply_the_record_cannot_name_shows_no_model() -> None:
    result = run_node(DOM + script() + _PAGE + """
await new Promise(setImmediate);
globalThis.fetch = __route({ '/api/chat/proof': () => __ok(__proof('')) });
const chip = await mountProofChip(document.createElement('div'), 'chat-2', 'req-2', null, true);
out({ chip: !!chip, model: !!__find(chip, (n) => String(n.className || '') === 'pc-model'), last: lastAnswerModel['chat-2'] || '' });
""")
    assert result["chip"] is True
    assert result["model"] is False
    assert result["last"] == ""
