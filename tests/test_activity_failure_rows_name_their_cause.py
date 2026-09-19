"""Guard: a FAILED Activity row names why it failed, for every failure family in the panel.

OPERATOR-REPORTED DEFECT. A turn failed and the Activity panel showed one line:

    ⚠ Model call failed: qwen2.5:7b

and nothing that said why. The event behind that row -- `model_lane_failed`, emitted by
`core/memory_first_router.py` -- was already carrying the whole cause in its `error` and
`fallback_reason` fields ("HTTPConnectionPool(host='127.0.0.1', port=11434): Read timed out.
(read timeout=59.99)"), and `/api/runtime/events` had already flattened both onto the row the
browser held. `ledgerCause` read three keys -- `reason`, `error_kind`, `rejection_reason` -- and
`model_lane_failed` carries NONE of them, so the panel fell back to the event's own message, which
is the fixed sentence "<provider> failed; trying fallback if available." That sentence names a
provider and no cause. The data was in the browser; the renderer dropped it.

What this file covers, and what it does not:

* COVERED by executing real code. The ledger renderer is JavaScript inside a Python string, so
  these tests lift the REAL `ledgerCause` / `ledgerRow` / `turnFailureReport` / `activityDetailLines`
  source out of the emitted page and run it under node against the exact event payloads the emit
  sites produce. Nothing here greps the page for a string.
* COVERED by driving the real writer. The capture-side fixes -- the exception class on the local
  model lane, the recorded cause on a contract rejection, the tool step's error, the retrieval
  failure's message -- are asserted by CALLING the function that emits them and reading what it
  emitted, never by asserting on source text.
* NOT COVERED by automation. Nothing here proves the panel is mounted, that the poller reaches
  `/api/runtime/events`, that CSS leaves the sub-line visible, or that a real qwen2.5:7b timeout
  travels the whole path from adapter to pixels. Those need the app driven by hand.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()

# --------------------------------------------------------------------------- #
# The real payloads, field for field as the emit sites produce them
# --------------------------------------------------------------------------- #
READ_TIMEOUT = "HTTPConnectionPool(host='127.0.0.1', port=11434): Read timed out. (read timeout=59.99)"

# core/memory_first_router.py, the `model_lane_failed` emit. The operator's row.
OPERATOR_LANE_FAILURE = {
    "event_type": "model_lane_failed",
    "message": "ollama-local failed; trying fallback if available.",
    "task_kind": "chat",
    "output_mode": "text",
    "provider_role": "primary",
    "lane": "local_first",
    "lane_type": "local_first",
    "phase": "failed",
    "selected_provider_id": "ollama-local",
    "provider_id": "ollama-local",
    "selected_model": "qwen2.5:7b",
    "model_id": "qwen2.5:7b",
    "attempted": ["ollama-local"],
    "error": READ_TIMEOUT,
    "fallback_reason": READ_TIMEOUT,
    "attempt_seconds": 60.0,
}

# core/retrieval_observability.py, the terminal receipt for a search that died.
RETRIEVAL_FAILURE = {
    "event_type": "web_retrieval_failed",
    "message": "Web retrieval failed.",
    "kind": "web_search",
    "status": "failed",
    "source_count": 0,
    "failure_class": "ConnectTimeout",
    "failure_reason": "HTTPSConnectionPool(host='duckduckgo.com', port=443): Max retries exceeded",
}


# --------------------------------------------------------------------------- #
# node harness: the page's own functions, executed
# --------------------------------------------------------------------------- #
def _script() -> str:
    scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL), key=len, reverse=True)
    assert scripts, "no inline script found in the chat page"
    return scripts[0]


def _slice(source: str, start: str, end: str) -> str:
    lo = source.index(start)
    hi = source.index(end, lo)
    return source[lo:hi]


def _ledger_js() -> str:
    """The real renderer source, lifted verbatim out of the page.

    The category rules and `activityDetailLines` are included deliberately: `ledgerRow` calls
    `activityCategoryFor` for every `tool_*` event and `turnFailureReport` calls
    `activityDetailLines` for every failure, so a harness without them cannot render a tool row at
    all -- it throws ReferenceError, which is how a whole family of rows stayed untested.
    """
    source = _script()
    esc = re.search(r"^function esc\(t\) \{.*$", source, re.MULTILINE)
    assert esc, "esc() not found in the chat page script"
    return "\n".join(
        [
            esc.group(0),
            # pageT/pageTF: ledger rows localize their labels through these; without
            # them the lifted renderer throws ReferenceError under node.
            _slice(source, "function pageT(", "function pageTF("),
            _slice(source, "function pageTF(", "const queueEl"),
            _slice(source, "function panelRow(", "const LEDGER_SKIP"),
            _slice(source, "const LEDGER_SKIP", "function ledgerRanNoTool"),
            _slice(source, "const ACTIVITY_CATEGORY_RULES", "function activityCategoryFor("),
            _slice(source, "function activityCategoryFor(", "function activityToolLabel("),
            _slice(source, "function activityDetailLines(", "// Builds {categories"),
        ]
    )


_DOM_STUB = (
    "globalThis.document = { createElement: () => ({ textContent: '',"
    " get innerHTML() { return String(this.textContent).replace(/&/g, '&amp;')"
    ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;'); } }) };\n"
)


def _run_node(harness: str, *, js: str | None = None) -> Any:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat-page ledger script")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(_DOM_STUB + (js if js is not None else _ledger_js()) + "\n" + harness)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=120)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"chat-page ledger JS failed under node:\n{result.stderr}"
    return json.loads(result.stdout)


def _row(event: dict, *, js: str | None = None) -> dict:
    return _run_node(
        f"const row = ledgerRow({json.dumps(event)});\n"
        "console.log(JSON.stringify(row === null ? {skipped: true} : row));\n",
        js=js,
    )


def _report(events: list[dict], commit: str = "b148d2cc") -> str:
    return _run_node(
        f"console.log(JSON.stringify({{ text: turnFailureReport({json.dumps(events)}, {json.dumps(commit)}) }}));\n"
    )["text"]


# --------------------------------------------------------------------------- #
# 1. A failure event carrying a reason RENDERS that reason
# --------------------------------------------------------------------------- #
def test_the_operator_row_now_names_the_read_timeout_it_died_on() -> None:
    """The reported defect, in one assertion: the row said `qwen2.5:7b` and nothing else."""
    row = _row(OPERATOR_LANE_FAILURE)
    assert row["cls"] == "fail", row
    assert "qwen2.5:7b" in row["title"], row["title"]
    assert "Read timed out" in row["sub"], row["sub"]
    assert "11434" in row["sub"], row["sub"]
    # The generic escalation sentence is not a cause and must not be what the row settles for.
    assert "trying fallback if available" not in row["sub"], row["sub"]


def test_a_hung_candidate_is_distinguishable_from_an_instantly_refused_one() -> None:
    """Same provider, same model, same failure text -- the clock is the only thing that separates a
    lane that burned the whole read timeout from one that was refused on contact."""
    hung = _row(OPERATOR_LANE_FAILURE)
    refused = _row({**OPERATOR_LANE_FAILURE, "attempt_seconds": 0.04})
    assert "after 60s" in hung["sub"], hung["sub"]
    assert "after 0.0s" in refused["sub"], refused["sub"]


def test_the_typed_error_class_separates_timeout_from_refusal_from_circuit_open() -> None:
    """`error_class` is the one token that answers the triage question, and nothing read it."""
    seen = {}
    for reason, error_class in (
        ("Read timed out.", "PROVIDER_TIMEOUT"),
        ("Provider returned a tool call this runtime refused to execute.", "MALFORMED_TOOL_CALL"),
        ("connection refused", "PROVIDER_CONNECTION_ERROR"),
    ):
        row = _row(
            {
                "event_type": "model.call_failed",
                "message": "Model call failed with ollama-local.",
                "provider_id": "ollama-local",
                "model_id": "qwen2.5:7b",
                "reason": reason,
                "error_class": error_class,
            }
        )
        assert error_class in row["sub"], (error_class, row["sub"])
        seen[row["sub"]] = 1
    assert len(seen) == 3, "three different failure classes must not render as the same line"
    # circuit_open has no error_class -- it never reached a provider -- and still has to say so.
    circuit = _row(
        {
            "event_type": "model.call_failed",
            "message": "Model call failed with ollama-local.",
            "provider_id": "ollama-local",
            "model_id": "qwen2.5:7b",
            "reason": "circuit_open",
        }
    )
    assert "circuit_open" in circuit["sub"], circuit["sub"]


def test_a_local_exception_class_reaches_the_row() -> None:
    """`str(exc)` alone cannot tell a transport failure from a defect in this runtime."""
    row = _row(
        {
            "event_type": "model.call_failed",
            "message": "Model call failed with ollama-local.",
            "provider_id": "ollama-local",
            "model_id": "qwen2.5:7b",
            "reason": "'NoneType' object is not subscriptable",
            "exception_class": "TypeError",
            "retryable": False,
        }
    )
    assert "TypeError" in row["sub"], row["sub"]
    assert "not retryable" in row["sub"], row["sub"]


def test_a_failed_retrieval_is_a_failure_row_and_not_a_running_bullet() -> None:
    """Unkeyed in LEDGER_MAP, `web_retrieval_failed` drew the neutral ['run','•',''] default -- the
    one row saying the turn's grounding never arrived rendered as ordinary progress."""
    row = _row(RETRIEVAL_FAILURE)
    assert row["cls"] == "fail", row
    assert row["icon"] != "•", row
    assert "ConnectTimeout" in row["sub"], row["sub"]
    assert "duckduckgo.com" in row["sub"], row["sub"]
    # The label and the event's own message are one fact; the row must not say it twice.
    assert row["title"].lower().count("retrieval failed") == 1, row["title"]


def test_a_blocked_tool_does_not_render_as_a_finished_one() -> None:
    """`humanToolTitle` had three branches and a catch-all that said "finished", so every other
    `tool_*` event inherited it -- `tool_repeat_blocked` drew a fail icon over "Search finished"."""
    blocked = _row(
        {
            "event_type": "tool_repeat_blocked",
            "message": "Repeated tool request detected. Switching to grounded synthesis instead of looping.",
            "tool_name": "web.search",
        }
    )
    assert blocked["cls"] == "fail", blocked
    assert "finished" not in blocked["title"].lower(), blocked["title"]
    assert "blocked" in blocked["title"].lower(), blocked["title"]
    # A tool still RUNNING must not be described in the past tense either.
    running = _row({"event_type": "tool_synthesizing", "message": "Synthesizing", "tool_name": "web.search"})
    assert "finished" not in running["title"].lower(), running["title"]
    # Control: the two events that ARE lifecycle outcomes keep their per-category wording.
    assert "finished" in _row({"event_type": "tool_executed", "tool_name": "web.search", "message": "ok"})["title"].lower()
    assert "failed" in _row({"event_type": "tool_failed", "tool_name": "web.search", "message": "no"})["title"].lower()


def test_a_failed_tool_step_states_its_cause_not_just_its_bucket() -> None:
    """A tool step's message is the step SUMMARY and its status is a bucket; the executor's own
    recorded cause lived in `reason` / `exception_class` and the tool branch never read them."""
    row = _row(
        {
            "event_type": "tool_failed",
            "message": "Tool step finished.",
            "tool_name": "workspace.write_file",
            "status": "error",
            "reason": "[Errno 13] Permission denied: '/etc/hosts'",
            "exception_class": "PermissionError",
        }
    )
    assert row["cls"] == "fail", row
    assert "PermissionError" in row["sub"], row["sub"]
    assert "Permission denied" in row["sub"], row["sub"]


# --------------------------------------------------------------------------- #
# 2. A failure with NO reason degrades gracefully
# --------------------------------------------------------------------------- #
def test_a_failure_with_no_cause_field_at_all_neither_crashes_nor_shows_an_empty_field() -> None:
    row = _row({"event_type": "model.call_failed", "message": "Model call failed with ollama-local.",
                "provider_id": "ollama-local", "model_id": "qwen2.5:7b"})
    assert row["cls"] == "fail", row
    assert "qwen2.5:7b" in row["title"], row["title"]
    # Not an empty sub-line, and not a dangling separator from a bit that was never there.
    assert row["sub"].strip(), row
    assert " · ·" not in row["sub"] and not row["sub"].strip().endswith("·"), row["sub"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"event_type": "model.call_failed"},
        {"event_type": "model.call_failed", "reason": None},
        {"event_type": "model.call_failed", "reason": ""},
        {"event_type": "model.call_failed", "reason": "   "},
        {"event_type": "model.call_failed", "reason": 0},
        {"event_type": "model.call_failed", "reason": False},
        {"event_type": "model.call_failed", "reason": []},
        {"event_type": "model.call_failed", "reason": {"nested": "object"}},
        {"event_type": "model.call_failed", "error": None, "fallback_reason": None},
        {"event_type": "model.call_failed", "attempt_seconds": "not a number"},
        {"event_type": "model.call_failed", "attempt_seconds": -1},
        {"event_type": "web_retrieval_failed", "failure_class": None, "failure_reason": None},
        {"event_type": "tool_failed", "tool_name": None, "reason": None},
        {"event_type": "model_lane_failed", "error": {"unexpected": "shape"}},
    ],
)
def test_a_malformed_or_empty_failure_payload_never_takes_the_panel_down(payload: dict) -> None:
    """The panel renders whatever the server sent. A row that throws blanks the whole timeline, so
    every degenerate shape has to produce a row object rather than an exception."""
    row = _row(payload)
    assert isinstance(row, dict), payload
    assert "cls" in row and "icon" in row and "title" in row, (payload, row)
    assert " · ·" not in str(row.get("sub") or ""), (payload, row)


def test_a_cause_line_cannot_grow_without_bound() -> None:
    """Nine cause fields, each a long provider string, must not produce a row that swamps the panel.
    The untruncated values still have to survive into the copyable evidence."""
    huge = {
        "event_type": "model.call_failed",
        "message": "Model call failed with ollama-local.",
        "provider_id": "ollama-local",
        "model_id": "qwen2.5:7b",
        "reason": "R" * 4000,
        "error": "E" * 4000,
        "error_kind": "K" * 4000,
        "error_class": "C" * 4000,
        "exception_class": "X" * 4000,
        "fallback_reason": "F" * 4000,
        "rejection_reason": "J" * 4000,
    }
    row = _row(huge)
    assert len(row["sub"]) <= 260, len(row["sub"])
    assert row["sub"].endswith("…"), row["sub"]
    # The full strings are not lost -- the copyable block still carries them.
    text = _report([huge])
    assert "R" * 200 in text, "the untruncated reason must survive into the copyable evidence"


def test_a_hostile_cause_string_cannot_inject_markup() -> None:
    html = _run_node(
        "console.log(JSON.stringify({ html: renderLedgerRows("
        + json.dumps(
            [
                {
                    "event_type": "model_lane_failed",
                    "message": "ollama-local failed; trying fallback if available.",
                    "provider_id": "ollama-local",
                    "model_id": "qwen2.5:7b",
                    "error": "<img src=x onerror=alert(1)>",
                    "fallback_reason": "</div><script>alert(2)</script>",
                }
            ]
        )
        + ") }));\n"
    )["html"]
    assert "<img" not in html
    assert "<script>" not in html
    assert "&lt;img" in html


def test_a_repeated_cause_is_printed_once() -> None:
    """`fallback_reason` is usually `error` verbatim, and `error_kind` is usually `reason` verbatim.
    Nine fields with one cause between them must read as one cause."""
    row = _row(OPERATOR_LANE_FAILURE)
    assert row["sub"].count("Read timed out") == 1, row["sub"]
    # Case and separator differences are the same fact too.
    same = _row(
        {
            "event_type": "model.call_failed",
            "provider_id": "ollama-local",
            "reason": "provider_timeout",
            "error_class": "PROVIDER_TIMEOUT",
        }
    )
    assert same["sub"].lower().count("provider_timeout") == 1, same["sub"]
    assert same["sub"] == "ollama-local · PROVIDER_TIMEOUT", same["sub"]


def test_a_successful_row_grows_no_cause_line() -> None:
    """The widened field list must not make an ANSWERED call look like it has something to report."""
    row = _row(
        {
            "event_type": "model.call_completed",
            "message": "Model call completed with ollama-local.",
            "provider_id": "ollama-local",
            "model_id": "qwen2.5:7b",
            "attempt_seconds": 3.2,
            "locality": "local",
        }
    )
    assert row["cls"] == "ok"
    assert row["sub"] == "ollama-local", row["sub"]


# --------------------------------------------------------------------------- #
# 3. SABOTAGE: revert the rendering change, watch the cause disappear
# --------------------------------------------------------------------------- #
_PRE_FIX_CAUSE_FIELDS = "const LEDGER_CAUSE_FIELDS = ['reason', 'error_kind', 'rejection_reason'];"


def _sabotaged_js() -> str:
    """The page's own renderer with ONE line reverted: the cause-field list as it was before this
    fix. Everything else -- the map, the row builder, the escaping -- is untouched."""
    js = _ledger_js()
    pattern = re.compile(r"const LEDGER_CAUSE_FIELDS = \[.*?\];", re.DOTALL)
    assert pattern.search(js), "LEDGER_CAUSE_FIELDS not found; the sabotage no longer targets the fix"
    sabotaged = pattern.sub(_PRE_FIX_CAUSE_FIELDS, js, count=1)
    assert sabotaged != js
    return sabotaged


def test_sabotage_reverting_the_cause_field_list_loses_the_operators_cause() -> None:
    """Names which line bit. With the pre-fix three-key list, `model_lane_failed` carries none of
    them, so the row falls back to the escalation sentence -- the exact defect that was reported."""
    row = _row(OPERATOR_LANE_FAILURE, js=_sabotaged_js())
    assert "Read timed out" not in row["sub"], (
        "the sabotage did not remove the cause -- something else is putting it on the row, so the "
        f"passing test above does not prove the cause-field list is what carries it: {row['sub']}"
    )
    assert "trying fallback if available" in row["sub"], row["sub"]


def test_sabotage_reverting_the_cause_field_list_loses_the_retrieval_cause() -> None:
    row = _row(RETRIEVAL_FAILURE, js=_sabotaged_js())
    assert "ConnectTimeout" not in row["sub"], row["sub"]
    assert "duckduckgo.com" not in row["sub"], row["sub"]


def test_sabotage_control_the_already_working_family_stays_green() -> None:
    """Control: `reason`-carrying events were already rendered before this change and must still be
    rendered under the sabotage. A sabotage that breaks everything proves nothing about the fix."""
    row = _row(
        {
            "event_type": "model.call_failed",
            "message": "Prompt did not fit ollama's context window.",
            "provider_id": "ollama",
            "model_id": "qwen3-coder-30b",
            "reason": "prompt_budget_exceeded",
            "error_kind": "prompt_shape",
        },
        js=_sabotaged_js(),
    )
    assert "prompt_budget_exceeded" in row["sub"], row["sub"]
    assert "prompt_shape" in row["sub"], row["sub"]


# --------------------------------------------------------------------------- #
# 4. The copyable block
# --------------------------------------------------------------------------- #
_TURN = [
    {"event_type": "task_received", "message": "chat", "client_turn_id": "turn-8f21"},
    {"event_type": "task_classified", "message": "", "task_class": "research", "client_turn_id": "turn-8f21"},
    {"event_type": "model_lane_started", "message": "", "provider_id": "ollama-local",
     "model_id": "qwen2.5:7b", "lane": "local_first", "client_turn_id": "turn-8f21"},
    dict(OPERATOR_LANE_FAILURE, client_turn_id="turn-8f21"),
    {"event_type": "model_routing_failed", "message": "All ranked provider lanes failed.",
     "lane": "local_first", "rejection_reason": "all_ranked_providers_failed", "client_turn_id": "turn-8f21"},
]


def test_one_block_identifies_the_turn_the_route_the_lanes_the_cause_and_the_build() -> None:
    text = _report(_TURN, "b148d2cc")
    assert "turn-8f21" in text, text
    assert "research" in text, text
    assert "ollama-local/qwen2.5:7b" in text, text
    assert "b148d2cc" in text, text
    assert "Read timed out" in text, text
    assert "all_ranked_providers_failed" in text, text


def test_a_candidate_named_by_several_events_is_listed_once() -> None:
    """`model_lane_started` and `model_lane_failed` are two events about ONE attempt; listing the
    candidate twice would report a second attempt that never happened."""
    lanes_line = [line for line in _report(_TURN).splitlines() if line.startswith("Lanes run:")]
    assert len(lanes_line) == 1, lanes_line
    assert lanes_line[0].count("ollama-local/qwen2.5:7b") == 1, lanes_line[0]


def test_a_turn_that_did_not_fail_produces_no_failure_report() -> None:
    """The block is prepended to the Activity copy; emitting one for a clean turn would tell a user
    their working turn had failed."""
    clean = [
        {"event_type": "task_received", "message": "chat", "client_turn_id": "t1"},
        {"event_type": "model_lane_completed", "message": "", "provider_id": "ollama-local",
         "model_id": "qwen2.5:7b", "client_turn_id": "t1"},
        {"event_type": "task_completed", "message": "done", "client_turn_id": "t1"},
    ]
    assert _report(clean) == ""


def test_a_field_that_was_never_recorded_says_so_rather_than_vanishing() -> None:
    """"No lane ran" and "lanes were not captured" are different bugs, and a block that silently
    omits the row cannot tell them apart."""
    text = _report([dict(OPERATOR_LANE_FAILURE)], "")
    assert "Turn: (not recorded)" in text, text
    assert "Route: (not recorded)" in text, text
    assert "Build: (not recorded)" in text, text


def test_an_unsubstituted_build_placeholder_is_not_pasted_as_a_commit() -> None:
    text = _report([dict(OPERATOR_LANE_FAILURE)], "__PAGE_BUILD_COMMIT__")
    assert "__PAGE_BUILD_COMMIT__" not in text, text
    assert "Build: (not recorded)" in text, text


def test_the_page_still_parses_and_still_serves() -> None:
    """The renderer is JS inside a Python string: one stray quote kills the whole script."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to syntax-check the page script")
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(_script())
        path = handle.name
    try:
        result = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=120)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"chat-page JS syntax error:\n{result.stderr}"

    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(path="/chat", query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool")
    assert res.status == 200
    assert b"function turnFailureReport" in res.body


# --------------------------------------------------------------------------- #
# 5. Capture side: the writers now record what the renderer reads
# --------------------------------------------------------------------------- #
def test_the_local_model_lane_records_the_exception_class_it_died_on() -> None:
    """Driven through the REAL router: a mocked adapter raises a real exception and the emitted
    `model.call_failed` is read back. `classify_error_class` returns None for anything it cannot
    place, so without the class name a local failure reaches the panel as bare exception text with
    nothing saying whether it was transport or a defect in this runtime."""
    from unittest import mock

    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import reset_provider_health
    from core.model_registry import ModelRegistry
    from core.task_router import create_task_record
    from storage.db import get_connection
    from storage.migrations import run_migrations

    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()

    registry = ModelRegistry()
    manifest = registry.register_manifest(
        {
            "provider_name": "local-qwen-http", "model_name": "qwen2.5:7b", "source_type": "http",
            "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied", "weights_bundled": False, "redistribution_allowed": True,
            "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize", "structured_json", "tool_intent"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"}, "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )
    router = MemoryFirstRouter(registry)
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}

    class ReadTimeout(OSError):
        """Stands in for the adapter's own transport error type, name and all.

        Deliberately NOT suffixed "Error": the assertion below is that the runtime records the
        exception's real type name, so renaming this class to satisfy a naming rule would test a
        name the runtime never sees.
        """

    adapter.run_structured_task.side_effect = ReadTimeout(READ_TIMEOUT)

    emitted: list[dict[str, Any]] = []

    def _capture(source_context, *, event_type, message, details=None):
        emitted.append({"event_type": event_type, "message": message, **dict(details or {})})

    with mock.patch.object(registry, "build_adapter", return_value=adapter), mock.patch(
        "core.memory_first_router.emit_runtime_event", _capture
    ):
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
            output_mode="tool_intent",
            task=create_task_record("show the current directory"),
            source_context={"requested_model": manifest.model_name, "_owner_local": True},
        )

    assert adapter.run_structured_task.called, "the adapter must have been invoked for this to mean anything"
    assert response is None and error
    failures = [e for e in emitted if e["event_type"] == "model.call_failed"]
    assert failures, f"no model.call_failed was emitted; got {[e['event_type'] for e in emitted]}"
    failure = failures[-1]
    assert failure.get("exception_class") == "ReadTimeout", failure
    assert READ_TIMEOUT in str(failure.get("reason") or ""), failure


def test_a_contract_rejection_forwards_the_cause_the_decision_already_held() -> None:
    """The soft shapes name themselves in `details['reason']`; a hard rejection names itself in
    `details['contract_error']`. Neither reached the event, so five different defects rendered as
    the single sentence "Model output rejected"."""
    from core.memory_first_router import _contract_failure_details, _soft_failure_details

    class _Decision:
        def __init__(self, details):
            self.details = details

    # The three soft shapes, built by the function that actually classifies them.
    monologue = _soft_failure_details("Let me look at this... But wait... Actually, hmm.")
    assert monologue is not None, "the classifier no longer recognises a reasoning monologue"
    out = _contract_failure_details(_Decision(monologue))
    assert "monologue" in out["reason"], out
    assert out["error_kind"] == "reasoning_only_completion", out

    empty = _soft_failure_details("")
    assert empty is not None
    out = _contract_failure_details(_Decision(empty))
    assert out["error_kind"] == "empty_completion", out
    assert out["reason"], out

    # The hard rejection path.
    out = _contract_failure_details(
        _Decision({"contract_error": "response contained a foreign tool marker", "warnings": ["w1", "w2"]})
    )
    assert out["reason"] == "response contained a foreign tool marker", out
    assert out["error_kind"] == "contract_validation", out
    assert out["contract_warnings"] == ["w1", "w2"], out


def test_a_rejection_with_no_recorded_cause_stays_visibly_unexplained() -> None:
    """An invented cause is worse than a missing one: it sends the next reader somewhere wrong."""
    from core.memory_first_router import _contract_failure_details

    class _Decision:
        details: dict[str, Any] = {}

    assert _contract_failure_details(_Decision()) == {}
    assert _contract_failure_details(object()) == {}
    assert _contract_failure_details(None) == {}


def test_a_tool_step_forwards_its_recorded_error_and_nothing_when_it_succeeded() -> None:
    from core.agent_runtime.builder.controller import _tool_failure_details

    out = _tool_failure_details(
        {"error": "[Errno 13] Permission denied: '/etc/hosts'", "exception_class": "PermissionError",
         "tool_call_id": "tool-1"}
    )
    assert out["reason"] == "[Errno 13] Permission denied: '/etc/hosts'", out
    assert out["exception_class"] == "PermissionError", out

    # A tool that exists but cannot run is a different investigation from one that ran and raised.
    gapped = _tool_failure_details({"capability_gap": {"gap_kind": "missing_auth", "tool": "web.search"}})
    assert gapped["error_kind"] == "missing_auth", gapped

    # A successful step must be unchanged: this rides on tool_executed too.
    assert _tool_failure_details({"tool_call_id": "tool-2", "approval_state": "not_required"}) == {}
    assert _tool_failure_details({}) == {}


def test_a_tool_error_string_is_bounded_and_never_a_traceback() -> None:
    from core.agent_runtime.builder.controller import _TOOL_FAILURE_TEXT_LIMIT, _tool_failure_details

    traceback_ish = "Traceback (most recent call last):\n" + ("  File \"x.py\", line 1\n" * 500)
    out = _tool_failure_details({"error": traceback_ish})
    assert len(out["reason"]) <= _TOOL_FAILURE_TEXT_LIMIT, len(out["reason"])
    assert "\n" not in out["reason"], "a ledger row is one line"


def test_a_failed_retrieval_records_why_and_not_only_the_exception_type() -> None:
    """`failure_class` alone is "ConnectTimeout" with no host, no URL, no status -- and the event's
    own message is the fixed sentence "Web retrieval failed."."""
    from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval

    context: dict[str, Any] = {}
    receipt = begin_web_retrieval(context, kind="web_search", query="who won", task_id="t1")
    terminal = finish_web_retrieval(
        context,
        receipt,
        notes=[],
        failure=ConnectionError("HTTPSConnectionPool(host='duckduckgo.com', port=443): Max retries exceeded"),
    )
    assert terminal["status"] == "failed"
    assert terminal["failure_class"] == "ConnectionError", terminal
    assert "duckduckgo.com" in terminal["failure_reason"], terminal
    # A success records no failure reason at all.
    ok = finish_web_retrieval(context, dict(receipt), notes=[{"origin_domain": "example.com"}], failure=None)
    assert ok["failure_reason"] == "", ok


def test_a_retrieval_failure_reason_is_redacted_and_bounded_and_cannot_break_the_receipt() -> None:
    """Adversarial: a failing request URL can carry a credential, an exception message can be
    unbounded, and an exception whose __str__ itself raises must not take down the one event that
    records the retrieval ended."""
    from core.retrieval_observability import _FAILURE_REASON_LIMIT, _failure_reason

    long_reason = _failure_reason(RuntimeError("x" * 50_000))
    assert 0 < len(long_reason) <= _FAILURE_REASON_LIMIT, len(long_reason)

    leaked = _failure_reason(RuntimeError("GET https://api.example.com/v1?api_key=sk-live-abcdef1234567890 failed"))
    assert "sk-live-abcdef1234567890" not in leaked, leaked

    class _Hostile(RuntimeError):
        def __str__(self) -> str:
            raise ValueError("this exception cannot describe itself")

    assert _failure_reason(_Hostile()) == ""
    assert _failure_reason(None) == ""


def test_the_conversation_card_states_the_same_cause_the_panel_does() -> None:
    """`core/task_event_model.py` is the SECOND reader of these events (the conversation card and
    the task rail). Its diagnostics block read `reason` only, so `model_lane_failed` -- which names
    its cause `error`/`fallback_reason` -- produced no diagnostics block at all, and the card and
    the panel disagreed about whether the turn had said why it failed."""
    from core.task_event_model import build_task_event

    card = build_task_event(dict(OPERATOR_LANE_FAILURE))
    assert card is not None, "the lane failure no longer reaches the card at all"
    diagnostics = card.get("diagnostics")
    assert diagnostics, card
    assert "Read timed out" in diagnostics["reason"], diagnostics

    typed = build_task_event(
        {
            "event_type": "model.call_failed",
            "message": "Model call failed with ollama-local.",
            "reason": "boom",
            "error_class": "PROVIDER_TIMEOUT",
            "exception_class": "ReadTimeout",
            "retryable": True,
        }
    )
    assert typed["diagnostics"]["error_class"] == "PROVIDER_TIMEOUT", typed
    assert typed["diagnostics"]["exception_class"] == "ReadTimeout", typed
    assert typed["diagnostics"]["retryable"] is True, typed


def test_the_card_still_strips_urls_credentials_and_absolute_paths_from_the_widened_fields() -> None:
    """The widened read must not widen what can leak: an adapter's `error` is raw exception text
    and `raise_for_status()` alone puts the provider URL, query string included, in it."""
    from core.task_event_model import build_task_event

    card = build_task_event(
        {
            "event_type": "model_lane_failed",
            "message": "failed",
            "error": "GET https://api.example.com/v1?api_key=sk-live-abcdef123456 failed for /Users/me/private/key.pem",
        }
    )
    reason = card["diagnostics"]["reason"]
    assert "sk-live-abcdef123456" not in reason, reason
    assert "api.example.com" not in reason, reason
    assert "/Users/me/private" not in reason, reason


def test_a_succeeding_lane_still_carries_no_diagnostics_block() -> None:
    """Non-failures were byte-identical before this change and must stay that way: every extra
    field on the hot path is a field that can leak."""
    from core.task_event_model import build_task_event

    card = build_task_event(
        {
            "event_type": "model_lane_completed",
            "message": "ollama-local answered.",
            "provider_id": "ollama-local",
            "model_id": "qwen2.5:7b",
        }
    )
    assert card is not None
    assert card.get("diagnostics") is None, card


def test_wiring_guard_the_contract_rejection_emit_forwards_the_recorded_cause() -> None:
    """NOT a behaviour test, and labelled so deliberately.

    `_contract_failure_details` is unit-tested above against real decisions, but the emit site that
    consumes it sits inside `_execute_provider_task`, whose contract-rejection branch needs a full
    ranked-manifest fixture with a failing validator and a second candidate to reach. That drive is
    not built here, so what this asserts is only that the call is still wired into the emit -- it
    would catch the call being deleted, and it would NOT catch the emitted event being dropped
    further down the path. Stated plainly rather than left to look like coverage it is not.
    """
    import inspect

    from core.memory_first_router import MemoryFirstRouter

    source = inspect.getsource(MemoryFirstRouter._execute_provider_task)
    assert "model_lane_contract_failed" in source, "the emit moved; this guard now proves nothing"
    assert "_contract_failure_details(decision)" in source, (
        "the contract rejection no longer forwards the cause the decision recorded"
    )


def test_the_turn_trace_cli_prints_the_cause_fields_it_used_to_drop() -> None:
    """`core/turn_trace.py` is the engineer-side reader of the same ledger. It printed `reason` and
    `error` only, so `model_lane_failed` (fallback_reason), `model_routing_failed`
    (rejection_reason) and the retrieval receipts (failure_class) all traced with a blank cause."""
    from core.turn_trace import _INTERESTING

    for field in ("error_class", "exception_class", "fallback_reason", "rejection_reason",
                  "failure_class", "failure_reason", "retryable", "attempt_seconds"):
        assert field in _INTERESTING, field
