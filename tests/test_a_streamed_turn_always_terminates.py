"""A streamed turn ends with a `done` chunk even when it fails.

Starlette commits status and headers BEFORE pulling the first item from a streaming body. From that
moment a 500 is impossible: an exception truncates the body, the browser reports a failed load, and
there is nothing for the user to read. The buffered sibling in `core.web.api.service` wraps its work
in try/except and returns a 500; the streaming branch had no equivalent, and the served UI always
streams (`buildTurnRequestBody` sends `stream: true`).

Confirmed contributors to exactly that shape: `format_provenance_footer` raising UnboundLocalError
(0cc98a05), and every unguarded raise site between the worker queue and the terminal chunk.

The property pinned here is narrow and absolute: whatever the inner generator does, the caller
receives a well-formed terminating chunk. A turn that fails must become a turn that SAYS it failed.
"""

from __future__ import annotations

import json

import pytest

from core.web.api import runtime as api_runtime


def _chunks(monkeypatch, inner):
    """Drive the public generator with a stubbed inner body."""

    monkeypatch.setattr(api_runtime, "_stream_agent_with_events_inner", inner)
    return list(
        api_runtime.stream_agent_with_events(
            None,
            "anything",
            session_id="stream-test",
            source_context={},
            model="test-model",
        )
    )


def _decoded(chunks: list[bytes]) -> list[dict]:
    out = []
    for chunk in chunks:
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def test_a_generator_that_raises_before_any_output_still_terminates(monkeypatch) -> None:
    def inner(*args, **kwargs):
        raise RuntimeError("boom before first yield")
        yield  # pragma: no cover - generator marker

    payloads = _decoded(_chunks(monkeypatch, inner))

    assert payloads, "the client received nothing at all"
    assert payloads[-1]["done"] is True


def test_a_generator_that_raises_mid_stream_still_terminates(monkeypatch) -> None:
    """The measured shape: output starts, then a late raise truncates the body."""

    def inner(*args, **kwargs):
        yield api_runtime.ollama_stream_chunk(
            model="test-model", content="partial ", created_at="2026-08-14T00:00:00.0Z", done=False
        )
        raise RuntimeError("boom after first yield")

    payloads = _decoded(_chunks(monkeypatch, inner))

    assert len(payloads) >= 2
    assert payloads[0]["message"]["content"] == "partial "
    assert payloads[-1]["done"] is True


def test_the_typed_terminal_says_the_turn_failed_without_manufacturing_answer_bytes(monkeypatch) -> None:
    def inner(*args, **kwargs):
        raise ValueError("the specific cause")
        yield  # pragma: no cover

    payloads = _decoded(_chunks(monkeypatch, inner))
    terminal = next(item["vool_terminal"] for item in payloads if item.get("vool_terminal"))
    assert terminal["status"] == "no_answer_terminal"
    assert terminal["reason_code"] == "turn_failed"
    assert "failed" in terminal["detail"].lower()
    assert payloads[-1]["message"]["content"] == "", (
        "failure detail is typed terminal truth, never a fabricated assistant answer"
    )


def test_an_unbound_local_error_is_survived(monkeypatch) -> None:
    """The exact exception the provenance footer raised, since that is what motivated this."""

    def inner(*args, **kwargs):
        yield api_runtime.ollama_stream_chunk(
            model="test-model", content="hi", created_at="2026-08-14T00:00:00.0Z", done=False
        )
        parts.append("x")  # noqa: F821 - deliberately unbound, mirrors the real defect

    payloads = _decoded(_chunks(monkeypatch, inner))

    assert payloads[-1]["done"] is True


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- a healthy stream must be untouched
# ---------------------------------------------------------------------------------------------


def test_a_healthy_stream_is_passed_through_unchanged(monkeypatch) -> None:
    original = [
        api_runtime.ollama_stream_chunk(
            model="test-model", content="one ", created_at="2026-08-14T00:00:00.0Z", done=False
        ),
        api_runtime.ollama_stream_chunk(
            model="test-model", content="", created_at="2026-08-14T00:00:00.0Z", done=True
        ),
    ]

    def inner(*args, **kwargs):
        yield from original

    assert _chunks(monkeypatch, inner) == original


def test_a_healthy_stream_gets_exactly_one_terminator(monkeypatch) -> None:
    """The wrapper must not append a second `done` to a stream that already ended properly."""

    def inner(*args, **kwargs):
        yield api_runtime.ollama_stream_chunk(
            model="test-model", content="", created_at="2026-08-14T00:00:00.0Z", done=True
        )

    payloads = _decoded(_chunks(monkeypatch, inner))

    assert sum(1 for item in payloads if item.get("done") is True) == 1


def test_a_raise_AFTER_the_terminator_does_not_add_a_second_one(monkeypatch) -> None:
    """Adversarial: the stream ended correctly, then cleanup failed. One terminator, still."""

    def inner(*args, **kwargs):
        yield api_runtime.ollama_stream_chunk(
            model="test-model", content="", created_at="2026-08-14T00:00:00.0Z", done=True
        )
        raise RuntimeError("late cleanup failure")

    payloads = _decoded(_chunks(monkeypatch, inner))

    assert sum(1 for item in payloads if item.get("done") is True) == 1


def test_client_disconnect_is_not_swallowed(monkeypatch) -> None:
    """GeneratorExit must propagate, or generator close semantics break."""

    def inner(*args, **kwargs):
        raise GeneratorExit
        yield  # pragma: no cover

    monkeypatch.setattr(api_runtime, "_stream_agent_with_events_inner", inner)
    with pytest.raises(GeneratorExit):
        list(
            api_runtime.stream_agent_with_events(
                None, "x", session_id="s", source_context={}, model="m"
            )
        )
