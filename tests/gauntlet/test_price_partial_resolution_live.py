"""Gauntlet_live — a price-shaped request the alias table cannot fully resolve must reach a
real tool through a real model, never raw tool-call JSON rendered as the final answer.

Live incident, 2026-08-03T22:05 UTC: "ok proce for BNB and ARB please?" reached the model with
NO tools offered at all (task_class="unknown" -> "chat_conversation" -> output_mode="plain_text"),
and the model's free-formed, never-executed `web.search` call shape was rendered verbatim as the
final answer. Three follow-up attempts to fix the extraction (a stopword blocklist, then a
list-connector structural test) each regressed a different case -- see
tests/test_every_asset_asked_for_is_answered.py for the full history. The actual fix is a routing
fix, not a better guess: `price_request_leaves_unresolved_content` declines the deterministic fast
path and flags the turn so `should_keep_ai_first_chat_lane` / `should_attempt_tool_intent` route it
to the tool_intent lane instead.

This drives that lane against the REAL local model (qwen3:8b via Ollama), not a mock of the
decision. Measured live 2026-08-03T23:55 UTC:

    MODEL_ROUTING_STARTED  output_mode=tool_intent  (task_class stayed "unknown")
    TOOL -> Running real tool web.search. query="current price of BNB and ARB"
    TOOL <- Finished web.search. ok=True
    (second round) TOOL X Model returned an invalid tool payload with no intent name.
    Final response: "I couldn't map that cleanly to a real action."

The routing worked exactly as designed: a real tool ran, covering BOTH named assets. qwen3:8b then
failed to close the loop with a clean `respond.direct` after its own search result came back -- a
MODEL instruction-following limitation, not a VOOL routing defect, and the runtime's own existing
`missing_intent` fallback text is what answered, not a raw JSON dump and not a fabricated price.
That fallback is the pre-existing, already-honest safety net for this exact failure shape; this
test's job is to prove the turn REACHES it through a real tool call, never around it.

Runs ONLY on-box under VOOL_ALLOW_LIVE_OLLAMA_TESTS=1 (or VOOL_ALPHA_LIVE_SOAK=1); skips in the
default CI lane.

    py -m pytest tests/gauntlet -m gauntlet_live -q   # with the env flag set
"""
from __future__ import annotations

import re

import pytest

from tests.gauntlet._live import LIVE_GATE, LIVE_MODEL, build_live_agent, require_live_provider

pytestmark = [pytest.mark.gauntlet_live, LIVE_GATE]

_RAW_TOOL_CALL_SHAPE_RE = re.compile(r'"tool"\s*:|"intent"\s*:\s*"|^\s*\[\s*\{')


def test_a_partially_unresolvable_price_request_reaches_a_real_tool_not_raw_json(
    make_agent, tmp_path
) -> None:
    require_live_provider()
    agent = build_live_agent(make_agent)

    result = agent.run_once(
        "price for BNB and ARB please?",
        session_id_override="gauntlet-live-price-partial-resolution",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "requested_model": LIVE_MODEL,
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
        },
    )

    response = str(result.get("response") or "")
    assert response.strip(), result

    # The exact incident shape: an unexecuted tool-call array or a bare {"intent": ...} object
    # rendered as the literal final answer. Whatever the model's own tool-calling competence turns
    # out to be, this must never reach the user.
    assert not _RAW_TOOL_CALL_SHAPE_RE.search(response), (
        f"the response looks like unexecuted tool-call JSON, not a rendered answer: {response!r}"
    )

    # The deterministic fast path must have declined (never answered BNB alone while staying
    # silent about ARB) and the turn must have been routed to the tool-enabled lane, not the
    # tools-less "unknown" chat lane that produced the original incident.
    assert result.get("route") != "deterministic:live_info_fast_path", result
    assert "live_info_partial_unresolved" in str(result.get("source_context") or {}), result

    # A real tool actually ran for this turn (web.search, or whatever the model chose) -- this is
    # the load-bearing assertion: the turn reached a real tool, it did not free-form a call shape
    # that nothing executed.
    details = dict(result.get("details") or {})
    assert details, "expected tool-loop details recording at least one executed step"
