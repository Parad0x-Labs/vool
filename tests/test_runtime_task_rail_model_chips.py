from __future__ import annotations

from core.runtime_task_rail import render_runtime_task_rail_html
from core.runtime_task_rail_event_render import RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
from core.runtime_task_rail_trace_styles import RUNTIME_TASK_RAIL_TRACE_STYLES


def test_event_render_surfaces_lane_model_complexity_chips() -> None:
    js = RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
    # The rail must render the per-turn routing VOOL actually used.
    assert "event.lane" in js
    assert "event.complexity" in js
    assert "actual_adapter_model_id" in js
    assert "meta-chip route" in js
    # Fast-path (trivial "hi") turns have no model - show that honestly.
    assert "fast-path" in js
    # tokens/sec surfaced when present.
    assert "tokens_per_second" in js
    assert "orchestration lane" in js
    for label in ("requested model", "policy model", "selected model", "actual model"):
        assert label in js


def test_task_rail_separates_model_review_from_validation_copy() -> None:
    js = RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
    assert "label: 'Model review'" in js
    assert "Review passed; this is model-review evidence" in js
    assert "Review flagged by the reviewer model; this is a reviewer verdict" in js
    assert "Model review was blocked before a reviewer verdict was produced" in js
    assert "Model review was degraded or unavailable; no reviewer verdict was produced" in js
    assert "Model review failed to run to a valid verdict" in js
    assert "Model-review outcome is unavailable; no reviewer verdict can be inferred" in js
    assert "label: 'Validation'" in js
    assert "recorded test, lint, or format check failed" in js
    assert "label: 'Verifier'" not in js
    assert "Verification is still in flight" not in js


def test_event_render_shows_tok_per_sec_and_dual_cost_estimate() -> None:
    js = RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
    # tok/s chip must still render for real runs.
    assert "tok/s</span>" in js
    # Alongside it, a $/min value estimate at BOTH a cloud-equivalent rate and VOOL's mesh rate,
    # computed from the throughput (tok/s * 60 * per-token price).
    assert "CLOUD_USD_PER_TOKEN" in js
    assert "MESH_USDC_PER_TOKEN" in js
    assert "tps * 60" in js
    assert "/min cloud" in js
    assert "/min mesh" in js
    # The rate is a tunable constant, not a magic literal buried in the chip.
    assert "const CLOUD_USD_PER_TOKEN = 0.0000006" in js
    assert "const MESH_USDC_PER_TOKEN = 0.000001" in js


def test_route_chip_style_is_defined() -> None:
    assert ".meta-chip.route" in RUNTIME_TASK_RAIL_TRACE_STYLES


def test_full_trace_page_includes_routing_chips_and_style() -> None:
    html = render_runtime_task_rail_html()
    assert "meta-chip route" in html
    assert ".meta-chip.route" in html
    assert "actual_adapter_model_id" in html
