"""Guard: an Activity row states the cause of its own failure.

One audit turn failed on three provider lanes. The router emitted, for EACH lane,
`model.call_failed` carrying reason='prompt_budget_exceeded', error_kind='prompt_shape' and a
prompt_budget telemetry dict. GET /api/runtime/events returned all three fields (details_json is
flattened onto the event), so the data was already in the browser -- and the panel drew three bare
"Model call failed" lines with no cause. Recovering it took hours of manual tracing.

Two things broke it, both in core/vool_chat_page.py:
  1. LEDGER_MAP was keyed only on UNDERSCORE names (model_lane_failed). The emitted type
     "model.call_failed" has a DOT, so ledgerRow fell to its ['run','•',''] default and rendered a
     neutral bullet through the generic else-branch.
  2. Nothing in ledgerRow ever read reason / error_kind / rejection_reason / prompt_budget.

These tests do not string-match the page source. They extract the real ledger-rendering functions
out of the emitted HTML and EXECUTE them under node against the exact event payloads the router
emits, then assert on the HTML the panel would show.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()

# The incident payload, field for field as core/memory_first_router.py emits it and as
# list_runtime_session_events returns it (details_json flattened onto the row).
PROMPT_BUDGET_FAILURE = {
    "event_type": "model.call_failed",
    "message": "Prompt did not fit ollama's context window.",
    "seq": 41,
    "provider_id": "ollama",
    "model_id": "qwen3-coder-30b",
    "reason": "prompt_budget_exceeded",
    "error_kind": "prompt_shape",
    "provider_health_recorded": False,
    "prompt_budget": {
        "num_ctx": 8192,
        "available_prompt_tokens": 6144,
        "estimated_prompt_tokens_before": 9412,
        "estimated_prompt_tokens_after": 9412,
        "status": "rejected",
    },
}


def _script() -> str:
    scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL), key=len, reverse=True)
    assert scripts, "no inline script found in the chat page"
    return scripts[0]


def _slice(source: str, start: str, end: str) -> str:
    lo = source.index(start)
    hi = source.index(end, lo)
    return source[lo:hi]


def _ledger_js() -> str:
    """The real esc/panelRow/ledger* source, lifted verbatim out of the page."""
    source = _script()
    esc = re.search(r"^function esc\(t\) \{.*$", source, re.MULTILINE)
    assert esc, "esc() not found in the chat page script"
    return "\n".join(
        [
            esc.group(0),
            _slice(source, "function panelRow(", "const LEDGER_SKIP"),
            _slice(source, "const LEDGER_SKIP", "function ledgerRanNoTool"),
        ]
    )


def _run_node(harness: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat-page ledger script")
    # esc() goes through the DOM; a two-line stub keeps the REAL escaping path under test.
    stub = (
        "globalThis.document = { createElement: () => ({ textContent: '',"
        " get innerHTML() { return String(this.textContent).replace(/&/g, '&amp;')"
        ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;'); } }) };\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(stub + _ledger_js() + "\n" + harness)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"chat-page ledger JS failed under node:\n{result.stderr}"
    return json.loads(result.stdout)


def _row(event: dict) -> dict:
    return _run_node(
        f"const row = ledgerRow({json.dumps(event)});\n"
        "console.log(JSON.stringify(row === null ? {skipped: true} : row));\n"
    )


def _rows_html(events: list[dict]) -> str:
    return _run_node(
        f"console.log(JSON.stringify({{ html: renderLedgerRows({json.dumps(events)}) }}));\n"
    )["html"]


# --------------------------------------------------------------------------- #
# The incident itself
# --------------------------------------------------------------------------- #
def test_a_failed_model_call_carries_its_reason_to_the_ui() -> None:
    row = _row(PROMPT_BUDGET_FAILURE)
    assert row["cls"] == "fail", row
    assert "Model call failed" in row["title"]
    assert "qwen3-coder-30b" in row["title"]
    assert "prompt_budget_exceeded" in row["sub"], row["sub"]
    assert "prompt_shape" in row["sub"], row["sub"]


def test_a_dotted_model_event_is_not_rendered_as_an_unknown_bullet() -> None:
    # The whole loss was ['run','•',''] swallowing every dotted type: neutral icon, no label, no cause.
    for event_type, expect_cls, expect_label in (
        ("model.call_started", "run", "Model running"),
        ("model.call_completed", "ok", "Model answered"),
        ("model.call_failed", "fail", "Model call failed"),
    ):
        row = _row({"event_type": event_type, "message": "x", "model_id": "qwen3", "provider_id": "ollama"})
        assert row["cls"] == expect_cls, (event_type, row)
        assert row["icon"] != "•", (event_type, row)
        assert expect_label in row["title"], (event_type, row)


def test_three_failed_lanes_each_state_their_own_cause() -> None:
    # The audit turn's exact shape: three lanes down, three distinct providers, one shared cause.
    events = [
        dict(PROMPT_BUDGET_FAILURE, seq=seq, provider_id=provider, model_id=model)
        for seq, provider, model in (
            (41, "ollama", "qwen3-coder-30b"),
            (42, "lmstudio", "qwen2.5-coder-14b"),
            (43, "openrouter", "anthropic/claude-sonnet-4"),
        )
    ]
    html = _rows_html(events)
    assert html.count("Model call failed") == 3, html
    assert html.count("prompt_budget_exceeded") == 3, html
    for provider in ("ollama", "lmstudio", "openrouter"):
        assert provider in html, provider
    assert "xp-row fail" in html


def test_the_prompt_budget_numbers_reach_the_row() -> None:
    # The measured overflow (9412 needed vs 6144 available) is the one number that ends the triage.
    row = _row(PROMPT_BUDGET_FAILURE)
    assert "9412 tokens vs 6144 available" in row["sub"], row["sub"]


# --------------------------------------------------------------------------- #
# Shape of the cause line
# --------------------------------------------------------------------------- #
def test_the_cause_line_stays_terse() -> None:
    # reason can be str(exc) from an adapter -- a full traceback string must not blow up the row.
    row = _row(
        {
            "event_type": "model.call_failed",
            "message": "Model call failed with ollama.",
            "provider_id": "ollama",
            "model_id": "qwen3",
            "reason": "HTTPConnectionPool(host='127.0.0.1', port=11434): " + ("x" * 400),
        }
    )
    assert len(row["sub"]) < 160, len(row["sub"])
    assert "HTTPConnectionPool" in row["sub"]
    assert row["sub"].endswith("…")


def test_a_repeated_error_kind_is_not_printed_twice() -> None:
    row = _row(
        {
            "event_type": "model.call_failed",
            "provider_id": "ollama",
            "reason": "timeout",
            "error_kind": "timeout",
        }
    )
    assert row["sub"].count("timeout") == 1, row["sub"]


def test_a_successful_model_call_grows_no_cause_line() -> None:
    row = _row(
        {
            "event_type": "model.call_completed",
            "message": "Model call completed with ollama.",
            "provider_id": "ollama",
            "model_id": "qwen3",
            "prompt_budget": {"status": "fit", "available_prompt_tokens": 6144},
        }
    )
    assert row["cls"] == "ok"
    assert row["sub"] == "ollama", row["sub"]


def test_a_failure_with_no_reason_field_still_says_more_than_the_provider() -> None:
    row = _row(
        {
            "event_type": "model.call_failed",
            "message": "Model call failed with ollama.",
            "provider_id": "ollama",
            "model_id": "qwen3",
        }
    )
    assert "Model call failed with ollama." in row["sub"], row["sub"]


def test_an_unmapped_ledger_event_still_states_its_reason() -> None:
    # The generic else-branch used to render the bare message and drop every detail field.
    row = _row({"event_type": "some_future_event", "message": "Something stopped", "reason": "budget_exhausted"})
    assert row["sub"] == "budget_exhausted", row["sub"]


def test_the_terminal_routing_failure_reads_as_a_failure() -> None:
    # The turn's last row was "All ranked provider lanes failed." drawn as a neutral bullet, with
    # rejection_reason sitting unread in the payload.
    row = _row(
        {
            "event_type": "model_routing_failed",
            "message": "All ranked provider lanes failed.",
            "rejection_reason": "all_ranked_providers_failed",
            "lane": "local",
        }
    )
    assert row["cls"] == "fail", row
    assert "Routing failed" in row["title"]
    assert "all_ranked_providers_failed" in row["sub"], row["sub"]


def test_the_streaming_chunk_event_is_still_skipped() -> None:
    # model_output_chunk carries the answer text token by token; it must never become a row.
    assert _row({"event_type": "model_output_chunk", "message": "hello"}) == {"skipped": True}


def test_a_hostile_reason_string_cannot_inject_markup() -> None:
    html = _rows_html([dict(PROMPT_BUDGET_FAILURE, reason="<img src=x onerror=alert(1)>")])
    assert "<img" not in html
    assert "&lt;img" in html


# --------------------------------------------------------------------------- #
# The page itself still ships
# --------------------------------------------------------------------------- #
def test_the_chat_page_script_is_valid_javascript() -> None:
    # The panel is JS inside a Python string: one stray quote kills the WHOLE script, not just this row.
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to syntax-check the page script")
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(_script())
        path = handle.name
    try:
        result = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"chat-page JS syntax error:\n{result.stderr}"


def test_the_chat_route_still_serves_the_page() -> None:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(path="/chat", query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool")
    assert res.status == 200
    assert b"function ledgerCause" in res.body


def test_turn_scoping_and_chat_scoped_polling_are_preserved() -> None:
    # The live projection remains turn-scoped, while ingestion concurrency belongs to the chat so a
    # newly-created run cannot launch a second replay from an independent busy/cursor state.
    assert "e.client_turn_id !== run.turnId" in HTML
    assert "'/api/runtime/events?session='" in HTML
    assert "owner.chatLedgerBusy" in HTML
