"""VOOL's own commands are never the clean loop's turn, and a local-only turn never gets a cloud brain.

Measured on CI #697 and #699 (v0.8 line, 94ce6169): the runner's home folder holds the repository, so
the loop's route found the owner's files named by words like "tools" and admitted turns that were not
about them. The loop's model was called first, twice (the answer and the look-first reminder), and the
turn then fell through to the lane that owns it:

- "use the model's own tools" is VOOL's switch, answered without a model; the switch still answered,
  but its "Done." was then cut as a claim the model made with no tool run;
- a plugin tool ask for a turned-off plugin reached the provider before the plugin lane said it was off;
- under Local Only, a model pinned at a paid cloud lane got two loop generations, because the loop's
  steps reach the router's provider seam without the turn's routing plan and its local-only fence.

A file in the owner's home naming the same words is set up here, so the route would admit these asks
on their words alone.
"""
from __future__ import annotations

import pytest

OWNER = {"surface": "desktop", "_owner_local": True, "client_host": "127.0.0.1"}


def _decide(ask: str):
    from core.clean_loop.route import decide

    # A ready brain and file words that name every word of the asks: only the route's own gates decide.
    return decide(
        ask,
        source_context=dict(OWNER),
        session_id="vool-command-turn",
        resolve_brain=lambda: object(),
        env={},
        file_words=lambda: {"model", "models", "own", "tools", "tool", "plugin", "digest", "text"},
        document_match=lambda _ask: True,
    )


@pytest.mark.parametrize(
    "ask",
    [
        "use the model's own tools",
        "use the model’s own tools",
        "go back to VOOL's tools",
        "which tools are you using?",
    ],
)
def test_the_models_own_tools_switch_is_vools_command(ask: str) -> None:
    decision = _decide(ask)
    assert decision.admit is False and decision.reason == "vool_command", decision


def test_a_plugin_tool_ask_is_the_plugin_lanes(monkeypatch) -> None:
    from core import tool_demand_signals

    real = tool_demand_signals.resolve_demand_signals

    class _Signals:
        required_families = ("plugin",)
        explicit_intents = ("vool-plugin-template.sha256",)

    def _signals(text, *args, **kwargs):
        return _Signals() if "vool-plugin-template.sha256" in str(text) else real(text, *args, **kwargs)

    monkeypatch.setattr(tool_demand_signals, "resolve_demand_signals", _signals)
    decision = _decide('Use the vool-plugin-template.sha256 tool to compute the text digest.\n{"text": "x"}')
    assert decision.admit is False and decision.reason == "plugin_turn", decision


def test_a_files_ask_is_still_the_loops() -> None:
    decision = _decide("which of my files mention the model tools?")
    assert decision.admit is True, decision


def test_a_local_only_turn_gets_no_cloud_brain(monkeypatch) -> None:
    from core.clean_loop import brain

    class _Router:
        def _requested_model_manifest(self, context):
            return object()  # a cloud manifest for whatever is pinned

    monkeypatch.setattr(brain, "_is_local", lambda manifest: False)
    monkeypatch.setattr(brain, "_may_write_answers", lambda manifest, request_text="": True)
    monkeypatch.delenv(brain.CLOUD_ENV, raising=False)
    pinned = {**OWNER, "requested_model": "c18-stub-cloud:paid"}
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    assert brain.resolve_cloud_manifest(_Router(), dict(pinned)) is not None
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    assert brain.resolve_cloud_manifest(_Router(), dict(pinned)) is None
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    assert brain.resolve_cloud_manifest(_Router(), {**pinned, "local_only_mode": True}) is None


def test_a_cloud_model_that_may_not_write_answers_is_no_brain(monkeypatch) -> None:
    from core import final_answer_authorship, policy_engine
    from core.clean_loop import brain

    class _Router:
        def _requested_model_manifest(self, context):
            return object()

    class _Verdict:
        def __init__(self, eligible: bool) -> None:
            self.eligible = eligible

    monkeypatch.setattr(brain, "_is_local", lambda manifest: False)
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    monkeypatch.delenv(brain.CLOUD_ENV, raising=False)
    pinned = {**OWNER, "requested_model": "c18-stub-cloud:paid"}
    monkeypatch.setattr(final_answer_authorship, "decide_final_answer_author", lambda **kw: _Verdict(False))
    assert brain.resolve_cloud_manifest(_Router(), dict(pinned)) is None
    monkeypatch.setattr(final_answer_authorship, "decide_final_answer_author", lambda **kw: _Verdict(True))
    assert brain.resolve_cloud_manifest(_Router(), dict(pinned)) is not None
