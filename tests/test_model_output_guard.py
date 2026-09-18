"""Brick 1 — the shared model-output guard + its wiring into the universal output choke point.

Covers the "Gemma leaked `<|tool_call>call:google_search(...)`" failure: foreign tool-call syntax
must never reach the user or the memory store, while prose that merely mentions a tool — and
ordinary code — survives untouched.
"""
from __future__ import annotations

from core.model_output_contracts import validate_contract
from core.model_output_guard import (
    claims_pending_tool,
    foreign_markers,
    is_ungrounded,
    scrub_foreign_markers,
)
from core.output_validator import validate_provider_output

# The exact reported leak.
LEAK = '<|tool_call>call:google_search("btc price")'
JSON_LEAK = (
    '{"tool":"bash","args":{"cmd":"ls -la '
    '\'/Users/example-user/Desktop/dna - x402\'"}}'
)


# --- detection ---------------------------------------------------------------------------------

def test_foreign_markers_detect_reported_leak():
    assert foreign_markers(LEAK)


def test_foreign_markers_detect_special_tokens_and_blocks():
    assert foreign_markers("<|im_start|>assistant")
    assert foreign_markers("<tool_call>{}</tool_call>")
    assert foreign_markers("some text\n<function=go>x</function>")
    assert foreign_markers("call:web_search(q)")


def test_foreign_markers_detect_reported_plain_json_tool_envelope():
    assert foreign_markers(JSON_LEAK) == ["json_tool_envelope:bash"]


def test_foreign_markers_ignore_clean_prose_and_code():
    # Talking ABOUT a tool is a legitimate answer.
    assert foreign_markers("You can use google_search to find the BTC price.") == []
    assert foreign_markers("The <function> keyword defines a function in some languages.") == []
    # Ordinary code must not be mistaken for a tool directive.
    assert foreign_markers('print("hello")') == []
    assert foreign_markers("result = compute(3, 4)") == []
    # INLINE inside a sentence stays clean -- detection is anchored to objects that OPEN a line.
    assert foreign_markers('Example: {"tool":"bash","args":{"cmd":"pwd"}}') == []
    # No argument object -> not a tool call, just data.
    assert foreign_markers('{"tool":"hammer","color":"red"}') == []


def test_fenced_tool_call_json_is_treated_as_a_leak():
    """Deliberate policy change (round 2), reversing the round-1 assertion that a fenced tool call is
    a legitimate example.

    Wrapping the call in ```json is one of the MOST common ways a model actually emits a tool call as
    text -- it was among the shapes measured leaking to users. Round 1 exempted it on the theory that
    a user might be asking to SEE a tool-call example. That trade is wrong for this product: nobody
    asks VOOL to print tool JSON, while models emit fenced calls constantly. Scrubbing a rare example
    costs a paragraph; leaking a call shows the user fake work that never ran and then feeds it back
    into the model's context on the next turn.
    """
    fenced = '```json\n{"tool":"bash","args":{"cmd":"pwd"}}\n```'
    assert foreign_markers(fenced)
    # Prose around it survives; only the call is excised.
    assert "Here is the plan." in scrub_foreign_markers("Here is the plan.\n\n" + fenced)


# --- scrub -------------------------------------------------------------------------------------

def test_scrub_keeps_real_prose_removes_leak():
    text = "Bitcoin is around $60k.\n" + LEAK
    cleaned = scrub_foreign_markers(text)
    assert "Bitcoin is around $60k." in cleaned
    assert "google_search" not in cleaned
    assert "tool_call" not in cleaned.lower()


def test_scrub_pure_leak_becomes_empty():
    assert scrub_foreign_markers(LEAK) == ""
    assert scrub_foreign_markers("<tool_call>whatever</tool_call>") == ""
    assert scrub_foreign_markers(JSON_LEAK) == ""


def test_scrub_leaves_clean_text_untouched():
    text = "Here is the plain answer with no markup."
    assert scrub_foreign_markers(text) == text


# --- choke-point wiring (validate_contract plain_text) -----------------------------------------

def test_plain_text_clean_is_unchanged_and_untrusted_nothing():
    v = validate_contract("plain_text", "A normal answer.")
    assert v.ok is True
    assert v.normalized_text == "A normal answer."
    assert v.confidence_penalty == 0.0
    assert v.warnings == []


def test_plain_text_leak_with_prose_is_scrubbed_and_penalised():
    v = validate_contract("plain_text", "The price is high.\n" + LEAK)
    assert v.ok is True
    assert "google_search" not in v.normalized_text
    assert "The price is high." in v.normalized_text
    assert v.confidence_penalty > 0.0
    assert "foreign_tool_markers_stripped" in v.warnings


def test_plain_text_pure_leak_fails_contract():
    v = validate_contract("plain_text", LEAK)
    assert v.ok is False
    assert v.error == "foreign_tool_syntax"
    assert v.normalized_text == ""
    assert v.confidence_penalty >= 0.5


def test_plain_text_json_tool_envelope_fails_contract():
    v = validate_contract("plain_text", JSON_LEAK)
    assert v.ok is False
    assert v.error == "foreign_tool_syntax"
    assert v.normalized_text == ""
    assert v.confidence_penalty >= 0.5


def test_tool_intent_mode_unaffected():
    # Non-plain_text contracts still validate JSON exactly as before.
    v = validate_contract("tool_intent", '{"intent": "machine.find_folder", "arguments": {}}')
    assert v.ok is True
    assert v.structured_output["intent"] == "machine.find_folder"


def test_end_to_end_through_provider_validator():
    r = validate_provider_output(provider_id="local:test", output_mode="plain_text", raw_text=LEAK)
    assert r.ok is False
    assert r.normalized_text == ""
    assert r.trust_penalty >= 0.5


# --- terminal-synthesis helpers (used by brick 2) ----------------------------------------------

def test_claims_pending_tool_fires_on_pending_action_and_leak():
    assert claims_pending_tool("Let me search for the current price.") is True
    assert claims_pending_tool("I'll run a quick check on that.") is True
    assert claims_pending_tool(LEAK) is True


def test_claims_pending_tool_ignores_advice_and_grounded_answers():
    # Advising the user is not the model claiming a pending call.
    assert claims_pending_tool("You can search Google for the latest BTC price.") is False
    assert claims_pending_tool("Bitcoin last traded near $60,000 according to the data.") is False


def test_is_ungrounded_flags_answer_that_ignores_observations():
    observations = ["Bitcoin (BTC) last traded at 60123 USD on the exchange."]
    ignored = "The weather in Paris is mild and pleasant throughout the springtime season."
    assert is_ungrounded(ignored, observations) is True


def test_is_ungrounded_passes_grounded_and_edge_cases():
    observations = ["Bitcoin (BTC) last traded at 60123 USD."]
    grounded = "Bitcoin last traded at 60123 USD according to the exchange data provided."
    assert is_ungrounded(grounded, observations) is False
    assert is_ungrounded("anything", []) is False            # no observations
    assert is_ungrounded("short", observations) is False      # trivial answer


# ---------------------------------------------------------------------------
# Round 2 — the leak the round-1 detector missed.
#
# The whole-reply rule (`s.startswith("{") and s.endswith("}")`) meant ANY prose preamble defeated
# detection. That is exactly what shipped in the live incident below: the model wrote two sentences,
# then emitted a raw tool call, and the guard certified it clean. These cases are transcribed from
# real model output, not invented shapes.
# ---------------------------------------------------------------------------

# Verbatim from the live incident transcript (nemotron-3-ultra:free).
INCIDENT = (
    "That was a system glitch — ignore it. Let me actually audit the code now.\n\n"
    "I'll start by exploring the Python source structure:\n\n"
    '{"tool": "bash", "args": {"command": "find /Users/x/Desktop -name \'*.py\' -type f | head -50", '
    '"description": "Find Python files"}}'
)


def test_prose_preamble_no_longer_defeats_the_json_detector():
    # THE live failure. Prose + a raw tool call must be caught.
    assert foreign_markers(INCIDENT), "the exact shipped incident text is still undetected"
    scrubbed = scrub_foreign_markers(INCIDENT)
    assert '"tool"' not in scrubbed and "find /Users" not in scrubbed
    assert "Let me actually audit the code now." in scrubbed  # real prose survives


def test_detects_the_common_provider_tool_call_shapes():
    shapes = {
        "fenced": 'Sure, here goes:\n\n```json\n{"tool": "bash", "args": {"command": "ls"}}\n```',
        "name_parameters": 'Let me search.\n\n{"name": "web.search", "parameters": {"q": "btc price"}}',
        "openai_tool_calls": (
            'Working on it.\n\n{"tool_calls": [{"type": "function", "function": '
            '{"name": "bash", "arguments": "{\\"command\\": \\"ls\\"}"}}]}'
        ),
        "function_input": 'One moment.\n\n{"function": "read_file", "input": {"path": "a.txt"}}',
    }
    for label, text in shapes.items():
        assert foreign_markers(text), f"{label} tool-call shape leaks to the user"


def test_whole_reply_envelope_still_detected():
    # The round-1 case must not regress.
    assert foreign_markers('{"tool": "bash", "args": {"command": "ls"}}')


def test_false_positives_are_not_introduced():
    # Broadening the detector must not start eating ordinary content. Each of these is legitimate
    # user-facing text that happens to contain JSON or tool vocabulary.
    clean = [
        "You can call the bash tool with args to run a command.",
        'print("hi")\nresult = {"a": 1}',
        'Your package.json looks fine:\n\n{"name": "myapp", "version": "1.0.0"}',
        'The payload {"a": 1} is inline in this sentence and is not a tool call.',
        "I read config.json and the name field is set to myapp.",
        '{"summary": "done", "bullets": ["a", "b"]}',  # a legitimate structured answer
    ]
    for text in clean:
        assert not foreign_markers(text), f"false positive on legitimate content: {text[:60]}"


# ---------------------------------------------------------------------------
# Round 2, fix 4 — the STREAMING path. The shipped UI posts stream:true, where chunks are
# unretractable and the guarded answer was discarded, so fixes 1-3 protected nothing a real user
# sees. These simulate the real chunk-by-chunk delivery.
# ---------------------------------------------------------------------------

from core.model_output_guard import stream_release_split


def _drive_stream(chunks):
    """Replay chunks through the hold-back split exactly as the transport does."""
    buffered, shown = "", ""
    for chunk in chunks:
        buffered += chunk
        release, buffered = stream_release_split(buffered)
        shown += release
    return shown, scrub_foreign_markers(buffered)  # what streamed live, then the flushed tail


def test_stream_never_shows_a_tool_call_split_across_chunks():
    # The incident, delivered the way a provider actually delivers it: the envelope arrives in
    # pieces, so no single chunk is detectable on its own.
    chunks = [
        "Let me actually audit ", "the code now.\n\nI'll start by exploring:\n\n",
        '{"tool": "ba', 'sh", "args": {"comm', 'and": "find . -name \'*.py\'"}}',
    ]
    shown, tail = _drive_stream(chunks)
    assert '"tool"' not in shown and "find ." not in shown, "tool call reached the screen mid-stream"
    assert tail == "", "the flushed tail still carried the tool call"
    assert "Let me actually audit the code now." in shown  # real prose still streamed live


def test_clean_prose_streams_live_with_nothing_held_back():
    # The latency guarantee: an ordinary answer must stream exactly as before, holding back nothing.
    chunks = ["Paris is ", "the capital ", "of France."]
    buffered, shown = "", ""
    for chunk in chunks:
        buffered += chunk
        release, buffered = stream_release_split(buffered)
        shown += release
        assert buffered == "", "clean prose was held back -- streaming would feel stalled"
    assert shown == "Paris is the capital of France."


def test_ordinary_line_opening_json_is_released_not_eaten():
    # A legitimate JSON block in an answer is held during the stream, then released at flush --
    # delayed, never swallowed.
    chunks = ['Your config:\n\n', '{"name": "myapp", ', '"version": "1.0.0"}\n', "\nThat looks right."]
    shown, tail = _drive_stream(chunks)
    assert '"name": "myapp"' in (shown + tail), "legitimate JSON was swallowed"
    assert "That looks right." in (shown + tail)
