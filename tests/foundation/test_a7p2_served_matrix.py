"""A7 pass-002 — served answer paths under the semantic-byte law.

Exercises the actual served emitters where A7 participates: buffered response,
NDJSON streaming, OpenAI SSE conversion, replay, refusal/no-answer, and a
provider retry/fallback turn. The invariant under attack: bytes on the wire ==
admitted == finalized bytes, every time, on every surface.
"""
from __future__ import annotations

import hashlib
import json

import pytest

import storage.db as sdb
from core.finalization import (
    FinalizationRejected,
    finalize_answer,
    replay_finalized_answer,
)
from core.semantic.semantic_result_seam import (
    admit_semantic_result,
    reset_admission,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "a7p2served.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ollama_chunk_frames(chunks: list[bytes]) -> list[dict]:
    frames = []
    for raw in chunks:
        payload = json.loads(raw.decode("utf-8"))
        frames.append(payload)
    return frames


def _sse_data_events(stream) -> list[dict]:
    events = []
    buf = b""
    for piece in stream:
        buf += piece
        while b"\n\n" in buf:
            block, buf = buf.split(b"\n\n", 1)
            line = next(
                l for l in block.split(b"\n") if l.startswith(b"data: ")
            )
            body = line[len(b"data: "):]
            if body.strip() == b"[DONE]":
                continue
            events.append(json.loads(body.decode("utf-8")))
    return events


def test_streamed_wire_bytes_equal_committed_bytes(fresh_store):
    from core.web.api.runtime import ollama_stream_chunks

    text = "The streamed answer travels exactly once sealed."
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t", canonical_content=text)

    frames = _ollama_chunk_frames(ollama_stream_chunks({}, "test-model", response_commit=commit))
    wire_text = "".join(f["message"]["content"] for f in frames[:-1])
    assert wire_text == text  # BYTE LAW: wire == committed, no strip/rewrite
    done = frames[-1]
    assert done["done"] is True
    assert done["vool_response_commit"]["finalization_id"] == commit["finalization_id"]
    assert done["vool_response_commit"]["content_hash"] == _sha(text)


def test_openai_sse_conversion_preserves_bytes_and_finality(fresh_store):
    from core.web.api.runtime import ollama_stream_chunks, openai_sse_stream_from_ollama_chunks

    text = "SSE surface must not mutate semantic meaning."
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t", canonical_content=text)

    events = _sse_data_events(
        openai_sse_stream_from_ollama_chunks(
            ollama_stream_chunks({}, "m", response_commit=commit), "m"
        )
    )
    deltas = "".join(
        e["choices"][0]["delta"].get("content", "") for e in events if e.get("choices")
    )
    assert deltas == text
    finals = [e for e in events if isinstance(e.get("vool_response_commit"), dict)]
    assert finals and finals[-1]["vool_response_commit"]["finalization_id"] == commit["finalization_id"]


def test_buffered_commit_shim_forwards_sealed_truth_without_resealing(fresh_store):
    from core.web.api.runtime import _hashlast_gate

    text = "buffered lane bytes"
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t", canonical_content=text)
    result_payload = {"response": "TAMPERED", "vool_response_commit": commit}

    from core.web.api.runtime import _response_commit

    forwarded = _response_commit(result_payload, source_context=None)
    # The attached commit IS the canonical truth — never reminted from the
    # tampered payload text.
    assert forwarded["finalization_id"] == commit["finalization_id"]
    assert forwarded["canonical_content"] == text
    assert _hashlast_gate(forwarded) is None


def test_hash_last_per_serve_gate_blocks_mutated_commit(fresh_store):
    from core.web.api.runtime import _response_commit

    text = "integrity checked"
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t", canonical_content=text)
    # Post-admission mutation attempt: someone edits canonical_content in place.
    mutated = dict(commit)
    mutated["canonical_content"] = text + "  "
    with pytest.raises(RuntimeError, match="HASH-LAST"):
        _response_commit({"vool_response_commit": mutated}, source_context=None)


def test_provider_retry_fallback_cannot_overwrite_sealed_truth(fresh_store):
    text_v1 = "first provider won"
    reset_admission()
    admit_semantic_result({"response": text_v1, "route_reason": "model_lane"})
    v1 = finalize_answer(turn_id="t", canonical_content=text_v1)
    # Fallback provider returns divergent bytes for the same admitted turn.
    with pytest.raises(FinalizationRejected):
        finalize_answer(turn_id="t", canonical_content="fallback divergent bytes")
    # v1 remains THE truth; replay serves it unchanged.
    replayed = replay_finalized_answer(principal="owner_local", semantic_result_id=v1["semantic_result_id"])
    assert replayed["canonical_content"] == text_v1


def test_no_answer_stream_frame_is_typed_zero_prose(fresh_store):
    from core.web.api.runtime import no_answer_terminal_line

    from core.finalization import no_answer_terminal

    terminal = no_answer_terminal(turn_id="t-na2", reason_code="provider_no_content")
    line = no_answer_terminal_line(terminal).decode("utf-8")
    frame = json.loads(json.loads(line)["vool_terminal"] and line)["vool_terminal"]
    assert frame["status"] == "no_answer_terminal"
    assert frame["reason_code"] == "provider_no_content"


def test_replay_serve_matches_admitted_bytes_exactly(fresh_store):
    text = "replay must be byte-true"
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t", canonical_content=text)
    for _ in range(3):
        served = replay_finalized_answer(principal="owner_local", semantic_result_id=commit["semantic_result_id"])
        assert served["canonical_content"] == text
        assert served["content_hash"] == _sha(text)
        assert served["finalization_id"] == commit["finalization_id"]
