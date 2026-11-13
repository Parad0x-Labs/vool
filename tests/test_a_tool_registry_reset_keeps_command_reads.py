"""A tool-registry reset keeps the core command reads (order-leak repro, v0.8 line 2026-11-13).

The blackbox suite's isolation fixture resets the tool registry. The command registry is built
once per process and projects its model-offerable reads only on that first build, so after a
reset ``operator.command.archaeology.trace`` stayed gone and the file-history tests failed only
when they ran after that suite.
"""
from __future__ import annotations

TRACE = "operator.command.archaeology.trace"


def test_a_reset_after_the_command_registry_is_built_keeps_its_reads():
    import core.capability_graph as graph
    from core.command_registry.registry import registry
    from core.tool_offer_assembly import assemble_tool_offer
    from core.tool_registry import reset, tool_for_intent

    registry()
    assert tool_for_intent(TRACE) is not None
    reset()
    assert tool_for_intent(TRACE) is not None
    registry()
    graph.refresh_from_registry()
    intents = assemble_tool_offer(user_text="which task changed this file", source_context={}).intents
    assert TRACE in intents


def test_a_reset_still_drops_a_registered_extension_tool():
    from dataclasses import replace

    from core.runtime_tool_contracts import runtime_tool_contracts
    from core.tool_registry import register, reset, tool_for_intent

    base = runtime_tool_contracts()[0]
    register(replace(base, intent="plugin.example.reset_probe"))
    assert tool_for_intent("plugin.example.reset_probe") is not None
    reset()
    assert tool_for_intent("plugin.example.reset_probe") is None
