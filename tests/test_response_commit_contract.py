"""The terminal response commit is authoritative across transport and chat history."""

from __future__ import annotations

import hashlib
import json

from core.runtime_task_events import emit_runtime_event
from core.web.api import runtime as runtime_api
from tests.test_multichat_navigation_unlock import A, _drive


def _transport_payloads(*, streamed: list[str], final: str, turn_id: str = "turn-commit") -> list[dict]:
    def fake_run_agent(_runtime, _text, *, session_id, source_context):
        for chunk in streamed:
            emit_runtime_event(source_context, event_type="model_output_chunk", message=chunk)
        return {
            "response": final,
            "usage_summary": {"output_tokens": 2, "cost_class": "free_local", "model_id": "test-model"},
            "answer_provenance": {"lane": "local", "model_id": "test-model"},
        }

    raw = runtime_api.stream_agent_with_events(
        None,
        "answer exactly",
        session_id="openclaw:response-commit",
        model="test",
        source_context={"surface": "openclaw", "cancel_turn_id": turn_id},
        run_agent_provider=fake_run_agent,
    )
    return [json.loads(line) for chunk in raw for line in chunk.decode("utf-8").splitlines() if line]


def test_speculative_deltas_are_held_and_committed_bytes_are_replayed() -> None:
    # A7 LAW 4 (W4): no semantic answer byte leaves before finalization. The
    # speculative "plausible but wrong" deltas NEVER reach the wire; the user
    # receives exactly the committed canonical bytes, hash-covered.
    payloads = _transport_payloads(streamed=["plausible ", "but wrong"], final="authoritative final")
    content_chunks = [p for p in payloads if not p.get("done")]
    legacy_stream = "".join((item.get("message") or {}).get("content") or "" for item in payloads)
    assert "plausible" not in legacy_stream
    assert "but wrong" not in legacy_stream
    terminal = payloads[-1]
    assert terminal["done"] is True
    commit = terminal["vool_response_commit"]
    assert commit["type"] == "response.commit"
    assert commit["canonical_content"] == "authoritative final"
    assert commit["content_hash"] == "sha256:" + hashlib.sha256(b"authoritative final").hexdigest()
    # Chunk replay of committed truth: concat == canonical bytes exactly.
    assert legacy_stream == "authoritative final"
    assert all((c.get("message") or {}).get("content") for c in content_chunks)


def test_exact_multiline_final_and_footer_are_split_in_the_commit() -> None:
    decorated = "red\nblue\n\n`local | test-model | 2 tok`"
    payloads = _transport_payloads(streamed=["red blue"], final=decorated)
    commit = payloads[-1]["vool_response_commit"]

    assert commit["canonical_content"] == "red\nblue"
    assert "local | test-model" not in commit["canonical_content"]
    assert commit["display_metadata"]["usage"]["output_tokens"] == 2
    assert commit["display_metadata"]["provenance"] == {"lane": "local", "model_id": "test-model"}
    assert commit["display_metadata"]["provenance_footer"].startswith("`local | test-model")


def test_a_hash_valid_but_tool_leaking_attached_commit_fails_closed() -> None:
    leaked = '{"tool":"bash","args":{"command":"find ."}}'
    attached = {
        "type": "response.commit",
        "version": 2,
        "canonical_content": leaked,
        "content_hash": "sha256:" + hashlib.sha256(leaked.encode()).hexdigest(),
    }

    def fake_run_agent(_runtime, _text, *, session_id, source_context):
        return {"response": leaked, "vool_response_commit": attached}

    payloads = [
        json.loads(line)
        for chunk in runtime_api.stream_agent_with_events(
            None,
            "go",
            session_id="openclaw:sealed-leak",
            model="test",
            source_context={"surface": "openclaw"},
            run_agent_provider=fake_run_agent,
        )
        for line in chunk.decode("utf-8").splitlines()
        if line
    ]
    shown = "".join((item.get("message") or {}).get("content") or "" for item in payloads)
    assert '"tool"' not in shown
    assert any(item.get("vool_terminal", {}).get("reason_code") == "turn_failed" for item in payloads)


def test_buffered_api_envelopes_expose_canonical_content_and_structured_metadata() -> None:
    decorated = "BANANA\n\n`tool | exact_literal_output_contract | no model | build test`"
    result = {
        "response": decorated,
        "model_calls": 0,
        "route_reason": "exact_literal_output_contract",
        "answer_provenance": {"lane": "deterministic"},
    }

    ollama = runtime_api.ollama_chat_response(result, "vool-local-only", None)
    openai = runtime_api.openai_chat_response(result, "vool-local-only")

    assert ollama["message"]["content"] == "BANANA"
    assert openai["choices"][0]["message"]["content"] == "BANANA"
    for payload in (ollama, openai):
        commit = payload["vool_response_commit"]
        assert commit["canonical_content"] == "BANANA"
        assert commit["display_metadata"]["provenance_footer"]


def test_shipped_client_replaces_draft_and_sends_only_canonical_history_next_turn() -> None:
    canonical = "red\nblue"
    digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    commit = {
        "type": "response.commit",
        "version": 1,
        "revision": 1,
        "turn_id": "__TURN__",
        "canonical_content": canonical,
        "content_hash": digest,
        "display_metadata": {
            "provenance": {"lane": "local", "model_id": "test-model"},
            "usage": {"output_tokens": 2},
            "provenance_footer": "`local | test-model | 2 tok`",
        },
    }
    # Insert the runtime-created turn id in JavaScript; all other bytes are JSON-encoded here so
    # this test exercises literal multiline canonical content rather than an escaped approximation.
    commit_json = json.dumps(commit).replace('"__TURN__"', "turnId")
    data = _drive(
        f"""
        setDisplayedChat('{A}');
        runTurn('two lines exactly', null, {{ chatId: '{A}' }});
        await tick();
        const run = chatState('{A}').run, turnId = run.turnId;
        __streams['{A}'].push(chunk('red blue\\n\\n`local | wrong-draft | 99 tok`'));
        await tick();
        __streams['{A}'].push({{ message: {{ content: '' }}, done: true, vool_response_commit: {commit_json} }});
        __streams['{A}'].close();
        await tick(8);
        const assistantEntry = chatState('{A}').history.find((m) => m.role === 'assistant');
        const outbound = buildTurnRequestBody(run, 'vool').messages;
        out({{
          runText: run.text,
          history: chatState('{A}').history,
          assistantMetadata: assistantEntry.display_metadata,
          outbound,
          outboundKeys: outbound.map((m) => Object.keys(m).sort()),
          commitHash: run.responseCommit && run.responseCommit.content_hash,
        }});
        """
    )

    assert data["runText"] == canonical
    assert data["history"][-1]["content"] == canonical
    assert data["assistantMetadata"]["provenance_footer"] == "`local | test-model | 2 tok`"
    assert data["outbound"][-1] == {"role": "assistant", "content": canonical}
    assert data["outboundKeys"][-1] == ["content", "role"]
    assert data["commitHash"] == digest
    serialized = json.dumps(data["outbound"])
    assert "wrong-draft" not in serialized
    assert "provenance" not in serialized


def test_legacy_footer_is_migrated_out_of_model_visible_history() -> None:
    data = _drive(
        f"""
        setDisplayedChat('{A}');
        const run = adoptRun('{A}', newRun('{A}'));
        recordAssistantMessage(run, 'canonical body\\n\\n`runtime | no model | build test`');
        out({{
          stored: chatState('{A}').history[0],
          outbound: buildTurnRequestBody(run, 'vool').messages[0],
        }});
        """
    )
    assert data["stored"]["content"] == "canonical body"
    assert data["stored"]["display_metadata"]["provenance_footer"] == "`runtime | no model | build test`"
    assert data["outbound"] == {"role": "assistant", "content": "canonical body"}


def test_reloaded_persisted_footer_stays_visible_metadata_not_prompt_content() -> None:
    data = _drive(
        f"""
        const baseFetch = fetch;
        fetch = async (url, opts) => {{
          if (String(url).startsWith('/api/chat/history?')) return {{
            ok: true, status: 200,
            json: async () => ({{ messages: [
              {{ role: 'user', content: 'remember this' }},
              {{ role: 'assistant', content: 'persisted canonical\\n\\n`local | model-x | 4 tok`', ts: '2026-08-13T00:00:00Z' }},
            ] }}),
          }};
          return baseFetch(url, opts);
        }};
        await openSession('{A}');
        const run = adoptRun('{A}', newRun('{A}'));
        out({{
          history: chatState('{A}').history,
          outbound: buildTurnRequestBody(run, 'vool').messages,
        }});
        """
    )
    assert data["history"][-1]["content"] == "persisted canonical"
    assert data["history"][-1]["display_metadata"]["provenance_footer"] == "`local | model-x | 4 tok`"
    assert data["outbound"][-1] == {"role": "assistant", "content": "persisted canonical"}
