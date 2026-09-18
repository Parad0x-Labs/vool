"""A7 W4 gate — emitter gate / exact byte release (in-process half).

Proves:

- Post-hash mutation is dead: buffered envelopes serve EXACT committed bytes
  (leading-whitespace fixture survives verbatim; M04-class).
- The provenance footer travels in display_metadata, never in answer body.
- SSE conversion carries finality identity on the terminal event (M08-class).
- Typed non-answer frames survive SSE conversion unmuxed.
"""
from __future__ import annotations

import hashlib
import json

from core.web.api import runtime as runtime_api


def _result(response_text: str) -> dict:
    return {
        "response": response_text,
        "usage_summary": {"output_tokens": 2, "cost_class": "free_local", "model_id": "test-model"},
        "answer_provenance": {"lane": "local", "model_id": "test-model"},
    }


def test_buffered_envelope_serves_exact_committed_bytes_leading_whitespace():
    # strip_provenance_footer only rstrips, so leading whitespace exposes any
    # post-hash normalization. The envelope must serve the exact hashed bytes.
    result = _result("   YES, exactly so")
    ollama = runtime_api.ollama_chat_response(result, "vool-local-only", None)
    openai = runtime_api.openai_chat_response(result, "vool-local-only")
    commit = ollama["vool_response_commit"]
    raw = ollama["message"]["content"]
    assert raw == commit["canonical_content"]
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == commit["content_hash"].split(":", 1)[1]
    assert openai["choices"][0]["message"]["content"] == commit["canonical_content"]
    # Leading whitespace survived: nothing normalized after hashing.
    assert raw.startswith("   YES")


def test_footer_travels_in_metadata_never_in_body():
    decorated = "BANANA\n\n`local | test-model | 2 tok`"
    ollama = runtime_api.ollama_chat_response(_result(decorated), "vool-local-only", None)
    body = ollama["message"]["content"]
    assert "test-model" not in body or body.startswith("BANANA")
    assert "`local |" not in body
    footer = ollama["vool_response_commit"]["display_metadata"]["provenance_footer"]
    assert "test-model" in footer


def test_sse_conversion_carries_finality_and_bytes_exactly():
    commit = {
        "type": "response.commit",
        "version": 1,
        "revision": 1,
        "finalization_id": "fc:abc",
        "turn_id": "t",
        "semantic_result_id": "",
        "status": "answer_present",
        "canonical_content": "exact replay bytes",
        "content_hash": "sha256:" + hashlib.sha256(b"exact replay bytes").hexdigest(),
        "display_metadata": {},
    }
    ndjson = runtime_api.ollama_stream_chunks(
        {"response": "exact replay bytes"}, "m", response_commit=commit
    )
    sse = list(runtime_api.openai_sse_stream_from_ollama_chunks(iter(ndjson), "m"))
    events = []
    for raw in sse:
        text = raw.decode("utf-8")
        if text.startswith("data: ") and "[DONE]" not in text:
            events.append(json.loads(text[len("data: "):]))
    concat = "".join(
        (ev["choices"][0]["delta"] or {}).get("content") or ""
        for ev in events
        if ev.get("choices")
    )
    assert concat == "exact replay bytes"
    terminal = [ev for ev in events if ev.get("vool_response_commit")]
    assert terminal, "SSE terminal event lost the finalization identity (M08)"
    assert terminal[-1]["vool_response_commit"]["content_hash"] == commit["content_hash"]
    assert sse[-1] == b"data: [DONE]\n\n"


def test_sse_forwards_typed_frames_unmuxed():
    typed = json.dumps({"vool_terminal": {"type": "no_answer.terminal", "reason_code": "x"}}).encode()
    frames = [typed + b"\n"]
    out = list(runtime_api.openai_sse_stream_from_ollama_chunks(iter(frames), "m"))
    assert any(b"vool_terminal" in chunk for chunk in out)
