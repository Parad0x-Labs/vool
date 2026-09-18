"""A plugin tool the user names by its dotted intent token is the sharpest explicit request
there is — and prose normalization (sentence segmentation inserts a space after the period)
must not be able to break it before demand signals run.

RED (2026-09-03, served plugin journey): "use the lielens. apply_repair tool" — the turn
planner's normalized text — seated NOTHING, the turn routed to a plain-text lane and failed.
The fix is at the owning seam: ``_plugin_intents`` matches against a whitespace-fused variant
of the text, which is safe because the match vocabulary is the closed set of REGISTERED plugin
intents, not open prose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.tool_demand_signals import resolve_demand_signals

_PACK = {
    "name": "token-pack",
    "version": "1.0.0",
    "runtime": {"contract_version": 1},
    "tools": [
        {
            "intent": "token-pack.apply_repair",
            "description": "Apply the approved repair and report the focused test result.",
            "handler": {"kind": "subprocess", "entry": "bin/run"},
            "input_schema": {"type": "object", "additionalProperties": False, "properties": {"note": {"type": "string"}}},
            "side_effect_class": "read_only",
            "approval_requirement": "none",
            "claim": {"target_argument": "note"},
        }
    ],
}


@pytest.fixture
def registered_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import capability_graph, plugin_tools, tool_registry
    from core.runtime_flags import override

    manifest = tmp_path / "plugins" / "token-pack" / ".codex-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(json.dumps(_PACK), encoding="utf-8")
    tool_registry.reset()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    try:
        with override("plugin_runtime_tools", True):
            plugin_tools.load_all(tmp_path)
            yield
    finally:
        tool_registry.reset()
        capability_graph.reset()
        capability_graph.init_graph()
        capability_graph.bootstrap_from_registry()


def test_exact_token_seats_the_intent(registered_pack) -> None:
    signals = resolve_demand_signals("use the token-pack.apply_repair tool")
    assert "token-pack.apply_repair" in signals.explicit_intents


def test_normalized_token_with_a_space_still_seats(registered_pack) -> None:
    """The served defect: sentence segmentation rewrote `token-pack.apply_repair` into
    `token-pack. apply_repair` before demand signals ran, and the seat was lost."""
    signals = resolve_demand_signals("use the token-pack. apply_repair tool")
    assert "token-pack.apply_repair" in signals.explicit_intents, signals


def test_prose_that_names_only_the_pack_seats_nothing(registered_pack) -> None:
    """The fused match may not become a fuzzy match: naming the pack without the tool
    token seats no intent (the family may appear; the explicit intent must not)."""
    signals = resolve_demand_signals("use the token-pack tool please")
    assert "token-pack.apply_repair" not in signals.explicit_intents
