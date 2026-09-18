"""A streamed reply gets the same output guard as a buffered one.

Measured 2026-08-01 at e4dc983: on a streamed web-chat turn (`stream:true`, the shipped UI
default) `_stream_response` emitted every raw chunk as a `model_output_chunk` event and the
transport released everything that was not a line-anchored tool-call brace.
`looks_like_internal_payload` and `suppress_internal_reasoning_leak` ran only on the BUFFERED
reply — so a raw reasoning monologue shipped to the screen verbatim, and because chunks had been
yielded, the guarded buffered reply was then discarded in favour of a bare usage footer
(core/web/api/runtime.py, the `saw_model_output and yielded_content` branch).

The fix is `StreamReleaseGate`, at the same transport choke point as the tool-envelope hold-back.
Chunks are unretractable, so the gate's only power is WHEN to release. Every monologue detector
keys on how the reply OPENS, which gives the invariant the gate is built on: once the opening is
proven innocent, the monologue guards cannot fire on the full text, so the rest streams live. A
risky opening holds everything, and at turn end the FULL accumulated text is judged by the same
`internal_payload_or_monologue` the buffered path uses — so the gate can never withhold a reply
the buffered path would have delivered, and when it suppresses, zero bytes have shipped and the
transport falls back to the guarded buffered reply instead of the footer.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.model_output_guard import StreamReleaseGate

# Captured verbatim from live qwen3:8b on 2026-08-01 (see tests/test_reasoning_monologue_leak.py):
# `done_reason: "length"`, cut off mid-sentence, no closing tag. Model-authored prose — a
# hand-written monologue can accidentally be written to match the detector, and this cannot.
LIVE_CAPTURE = (
    "Okay, let's start by understanding what the user is asking. They want to know how a routing "
    "layer should decide between a local and a cloud model, and also what it logs. \n\nFirst, the "
    "routing layer's decision between local and cloud models. I know that in distributed systems, "
    "especially with machine learning models, there's often a choice between running inference "
    "locally on a device or using a cloud-based model. The routing layer is responsible for "
    "directing traffic to the appropriate model. So, the decision factors would include things "
    "like latency, bandwidth, data sensitivity, model performance, and maybe even cost.\n\nWait, "
    "but how exactly does the routing layer make that decision? Maybe it uses some kind of policy "
    "or algorithm. For example, if the request is time-sensitive, it might prioritize the local "
    "model to reduce latency. If the data is sensitive, it might route to the local model to avoid "
    "sending it over the network. On the other hand, if the cloud model has better accuracy or "
    "more resources,"
)

GUARDED_BUFFERED_REPLY = "I couldn't get a usable model response for that. Try again or pick another model."


def _drive_gate(chunks):
    gate = StreamReleaseGate()
    shown = ""
    for chunk in chunks:
        shown += gate.feed(chunk)
    return shown, gate.flush(), gate.suppressed_reason


def _in_chunks(text: str, size: int = 37) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


# --------------------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------------------


def test_the_live_monologue_never_releases_a_byte() -> None:
    shown, tail, reason = _drive_gate(_in_chunks(LIVE_CAPTURE))
    assert shown == "", "monologue text was released mid-stream"
    assert tail == "", "the flush released the monologue after all"
    assert reason == "internal_payload_or_monologue"


def test_clean_prose_streams_live_before_the_turn_ends() -> None:
    """The latency guarantee: an ordinary answer clears the head probe within a word or two and
    then streams as it arrives — live release, not a flush-time dump."""

    gate = StreamReleaseGate()
    released_before_flush = gate.feed("Paris is ") + gate.feed("the capital ") + gate.feed("of France.")
    assert released_before_flush == "Paris is the capital of France."
    assert gate.flush() == ""
    assert gate.suppressed_reason == ""


def test_a_reply_that_opens_like_a_monologue_but_answers_is_delivered_at_flush() -> None:
    """The gate never suppresses on the lead alone — the full guard decides at flush, so a real
    answer that opens with "Wait," costs latency, never the answer."""

    reply = "Wait, no — the correct answer is 4, because the config caps the pool at four workers."
    shown, tail, reason = _drive_gate(_in_chunks(reply))
    assert shown == ""  # held while the opening was ambiguous
    assert tail == reply
    assert reason == ""


def test_a_tool_call_split_across_chunks_is_still_held_and_scrubbed() -> None:
    """Parity with the tool-envelope hold-back the gate replaces."""

    shown, tail, reason = _drive_gate(
        ["Here is the plan.\n\n", '{"tool": "ba', 'sh", "args": {"command": "rm -rf /"}}']
    )
    assert "Here is the plan." in shown
    assert '"tool"' not in shown + tail and "rm -rf" not in shown + tail
    assert reason == ""


def test_a_leading_think_block_is_dropped_and_the_answer_still_streams() -> None:
    """The stream path cannot strip `<think>` per frame (the tags span frames); the gate holds the
    block and releases the reply that follows its close — reasoning never on the wire, answer
    still streamed."""

    answer = (
        "The router ranks by cost class first, then locality, then measured throughput "
        "across the last dozen calls before picking a candidate."
    )
    shown, tail, reason = _drive_gate(
        ["<think>Let me check ", "the ranking step.</think>\n\n" + answer, " More detail follows."]
    )
    assert "Let me check" not in shown + tail
    assert answer in shown + tail
    assert reason == ""


def test_an_unclosed_think_block_is_suppressed_whole() -> None:
    shown, tail, reason = _drive_gate(
        ["<think>Okay the user wants the routing table. ", "Let me look at"]
    )
    assert shown == "" and tail == ""
    assert reason == "internal_payload_or_monologue"


def test_the_filename_array_welded_to_code_is_suppressed() -> None:
    """The 2026-08-01 build-lane leak, streamed."""

    shown, tail, reason = _drive_gate(
        ['["security.py", "test_security.py", "README.md"]import hmac\n', "import secrets\n"]
    )
    assert shown == "" and tail == ""
    assert reason == "internal_payload_or_monologue"


def test_a_clean_array_answer_is_released_at_flush() -> None:
    """Some turns legitimately answer with a list — delayed to flush, never swallowed."""

    shown, tail, reason = _drive_gate(['["a.py", ', '"b.py"]'])
    assert shown + tail == '["a.py", "b.py"]'
    assert reason == ""


def test_a_fabricated_tool_result_mid_reply_is_dropped_not_streamed() -> None:
    shown, tail, reason = _drive_gate(
        [
            "The audit found this evidence:\n",
            "<function_results>File: api/x.py — 300 invented lines</function_results>",
        ]
    )
    assert "The audit found this evidence:" in shown
    assert "invented" not in shown + tail
    assert reason == "internal_payload_tail"


def test_a_watched_tag_split_across_the_chunk_boundary_is_still_caught() -> None:
    shown, tail, _reason = _drive_gate(["Fine so far <thi", "nk>hidden reasoning that never closes"])
    assert "hidden reasoning" not in shown + tail
    assert "Fine so far" in shown


def test_a_special_token_line_is_held_and_scrubbed() -> None:
    shown, tail, _reason = _drive_gate(
        ["Sure — the price is 42 USD.\n", "<|im_start|>assistant do something else"]
    )
    assert "42 USD" in shown + tail
    assert "im_start" not in shown + tail


# --------------------------------------------------------------------------------------
# The transport: the REAL generator the shipped UI reads, not the helper
# --------------------------------------------------------------------------------------


def _drive_transport(chunks, *, buffered_response: str):
    """Return the concatenated content the CLIENT receives for these model chunks."""

    from core.web.api import runtime as rt

    def fake_run_agent(_rt, _text, *, session_id, source_context):
        from core.runtime_task_events import emit_runtime_event

        for chunk in chunks:
            emit_runtime_event(source_context, event_type="model_output_chunk", message=chunk)
        return {"response": buffered_response, "usage_summary": {}}

    shown = ""
    for raw in rt.stream_agent_with_events(
        None,
        "go",
        session_id="openclaw:aaaabbbbccccdddd3333",
        model="test",
        source_context={"surface": "openclaw"},
        run_agent_provider=fake_run_agent,
    ):
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        for part in line.splitlines():
            part = part.strip()
            if not part.startswith("{"):
                continue
            try:
                obj = json.loads(part)
            except Exception:
                continue
            shown += (obj.get("message") or {}).get("content") or ""
    return shown


def test_a_streamed_monologue_is_replaced_by_the_guarded_buffered_reply() -> None:
    """The whole incident, end to end: the monologue never reaches the client, and instead of the
    old bare usage footer the client receives the reply the buffered guard actually produced."""

    shown = _drive_transport(_in_chunks(LIVE_CAPTURE), buffered_response=GUARDED_BUFFERED_REPLY)
    assert "the user is asking" not in shown, "the raw monologue reached the client"
    assert "Okay, let's start" not in shown
    assert GUARDED_BUFFERED_REPLY in shown, "the guarded buffered reply was discarded"


def test_clean_prose_still_streams_through_the_transport_unchanged() -> None:
    shown = _drive_transport(
        ["Paris is ", "the capital ", "of France."], buffered_response="Paris is the capital of France."
    )
    assert shown == "Paris is the capital of France."


def test_a_fully_held_clean_reply_is_delivered_exactly_once() -> None:
    """A reply held to flush (here: a legitimate JSON answer) used to be yielded twice — once as
    the flushed tail and once again as the buffered response, because the flush never marked
    content as yielded."""

    payload = '{"name": "myapp", "version": "1.0.0"}'
    shown = _drive_transport([payload], buffered_response=payload)
    delivered = shown.count('"myapp"')
    assert delivered == 1, f"the held reply was delivered {delivered} times"


# --------------------------------------------------------------------------------------
# The router seam: a streamed turn's BUFFERED text matches the non-stream path
# --------------------------------------------------------------------------------------


def test_the_joined_stream_response_strips_the_leading_think_block() -> None:
    """The adapter deliberately skips `<think>`-stripping per NDJSON frame; joined at
    `_stream_response`, the full text is in hand and must match what the non-stream path returns —
    and what the release gate lets on the wire."""

    from core.memory_first_router import MemoryFirstRouter

    chunks = ["<think>Let me check ", "the ranking.</think>\n\n", "The router ranks by cost class first."]

    class _Adapter:
        def stream_text_task(self, request):
            for text in chunks:
                yield SimpleNamespace(delta_text=text, raw_event=None, usage=None, done=False)

    manifest = SimpleNamespace(
        provider_id="ollama:qwen3:8b", model_name="qwen3:8b", metadata={"confidence_baseline": 0.7}
    )
    request = SimpleNamespace(output_mode="plain_text")
    response = MemoryFirstRouter._stream_response(
        SimpleNamespace(),
        adapter=_Adapter(),
        manifest=manifest,
        request=request,
        source_context=None,
    )
    assert response.output_text == "The router ranks by cost class first."
