"""The populated-catalog twin of the unavailable-case laws in test_projection_parity.

The parity file pins `plugins.lifecycle.transition` as structurally UNAVAILABLE in an empty
catalog. That is half the law: the same command must be AVAILABLE — and really execute, with
receipts and preserved lifecycle state — the moment the machine's lifecycle store holds a
really-admitted pack. Availability that answers from anything other than the live lifecycle
state (a cached projection, a forced probe result) would break one half or the other; these
cases hold both halves to the real authority.
"""
from __future__ import annotations

import pytest

PACK_ID = "parity-populated-pack"


@pytest.fixture
def populated_catalog(tmp_path, monkeypatch):
    """One really-admitted pack in a per-test lifecycle store and plugins root.

    The store is isolated per test (the strongest idiom in this suite) and admission goes
    through the real three acts — install, verify, enable — never a forced availability flag.
    """
    from tests._toolchain_fixtures import admit_plugin, make_plugin, reset_toolchain_state

    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    reset_toolchain_state()
    pack = make_plugin(tmp_path, plugin_id=PACK_ID, admit=False)
    admit_plugin(PACK_ID, pack)
    yield pack
    reset_toolchain_state()


def _semantics(payload: dict) -> dict:
    """The truth projections must agree on, with the one honest exception normalized:
    each real act stamps its evidence row with the time it happened."""
    from copy import deepcopy

    data = deepcopy(payload["data"] or {})
    evidence = data.get("evidence")
    if isinstance(evidence, dict):
        evidence.pop("at", None)
    return {
        "ok": payload["ok"],
        "exit_code": payload["execution"]["exit_code"],
        "fault": payload["fault"],
        "data": data,
        "summary": payload["summary"],
    }


def test_available_transition_executes_identically_across_cli_chat_api(capsys, populated_catalog):
    from core import plugin_lifecycle
    from tests.command_registry.test_projection_parity import _api_envelope, _chat_envelope, _cli_envelope

    args = {"action": "disable", "plugin_id": PACK_ID}
    argv = ["plugins", "lifecycle", "transition", "--action", "disable", "--plugin-id", PACK_ID]

    cli_payload = _cli_envelope(argv, capsys)
    assert cli_payload["ok"] is True, cli_payload
    assert cli_payload["execution"]["exit_code"] == 0
    assert cli_payload["fault"] is None
    assert any(r.get("kind") == "plugin_lifecycle" for r in cli_payload["receipts"]), (
        "a mutating command must emit receipts"
    )
    assert cli_payload["data"]["stage"] == "verified", "disable is a real stage transition, not a label"

    # Stage the same pre-state through the real authority, then the same act through the
    # other two projections: identical truth, only execution.projection may differ.
    plugin_lifecycle.enable(PACK_ID)
    chat_payload = _chat_envelope("plugins.lifecycle.transition", args)
    plugin_lifecycle.enable(PACK_ID)
    api_payload = _api_envelope("plugins.lifecycle.transition", args)

    assert _semantics(cli_payload) == _semantics(chat_payload) == _semantics(api_payload)
    assert chat_payload["execution"]["projection"] == "chat"
    assert api_payload["execution"]["projection"] == "api"


def test_populated_state_survives_legitimate_transitions(populated_catalog):
    """Lifecycle state is durable truth: legitimate acts change the stage and keep the record —
    nothing here may delete or reset the operator's plugin state."""
    from core import plugin_lifecycle

    assert plugin_lifecycle.is_available(PACK_ID) is True

    plugin_lifecycle.disable(PACK_ID)
    assert plugin_lifecycle.is_available(PACK_ID) is False, "disabled is not available"
    plugin_lifecycle.enable(PACK_ID)

    record = plugin_lifecycle.record_for(PACK_ID)
    assert record is not None and record.stage == "enabled"
    events = [row["event"] for row in record.evidence]
    assert events.count("disabled") >= 1 and events.count("enabled") >= 2, events
    assert plugin_lifecycle.is_available(PACK_ID) is True, "the pack's availability is preserved"


def test_palette_renders_the_populated_command_available_without_reason(populated_catalog):
    from core.command_registry.projections import palette_data
    from core.command_registry.registry import registry

    row = next(
        r for r in palette_data(registry())["commands"] if r["command_id"] == "plugins.lifecycle.transition"
    )
    assert row["available"] is True
    assert row["unavailable_reason"] is None, "an available row carries no dimming reason"
