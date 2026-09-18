"""Self-test for `tests/claim_sequence_monitor.py`.

TEST INFRASTRUCTURE ONLY -- this proves the MONITOR's own mechanics (does it wrap, does it log,
does it restore, does patching the location it says is observable actually work, does patching the
location it says is blind actually NOT work), not that the product's routing decisions are
correct. Where a test drives a real currency-lane defect (the "ALL" -> Albanian-lek collision),
that is incidental: the assertion is "the monitor recorded this probe returning truthy", which is a
fact about the monitor, not an endorsement or a claim that the collision itself is fixed.
"""
from __future__ import annotations

import importlib

from tests.claim_sequence_monitor import (
    REGISTRY,
    ClaimMonitor,
    attribute,
    monitor,
    report,
)

# Real defect-shaped prompt (measured on this runtime): "ALL" matches the identity-only currency
# gate's registered-code regex (`\bALL\b`, meant for the Albanian lek) purely because of "ALL CAPS",
# with "name" supplying the identity-context word the gate also requires. Used here ONLY as a
# reliable real trigger for `static_currency_identity_admitted` -- not something this file fixes.
ALL_CAPS_CURRENCY_PROMPT = "Take the name of my city, reverse the letters, and output it in ALL CAPS"
PLAIN_ARITHMETIC_PROMPT = "What is 47 plus 18?"


def test_registry_covers_the_16_required_symbols_across_at_least_7_clusters() -> None:
    required_symbols = {
        "maybe_answer_currency_value",
        "asks_for_dynamic_currency_value",
        "static_currency_identity_admitted",
        "looks_like_grounded_price_lookup",
        "_recent_price_subject",
        "url_read_request",
        "looks_like_builder_request",
        "is_build_instruction",
        "is_opted_out",
        "looks_like_personalized_plan_request",
        "turn_may_hold_several_requests",
        "build_live_data_plan",
        "parse_raw_output_contract",
        "apply_raw_output_contract",
        "intake_request_text",
        "record_slice_answer",
    }
    present_symbols = {spec.symbol for spec in REGISTRY}
    assert required_symbols <= present_symbols
    assert len(REGISTRY) >= 16
    assert len({spec.cluster for spec in REGISTRY}) >= 7


def test_every_registry_symbol_and_also_patch_target_resolves_to_a_real_callable() -> None:
    """Every module/symbol pair the REGISTRY names -- including every `also_patch` entry --
    must actually import and expose a callable. A registry entry pointing at a name that no
    longer exists would silently produce a monitor with an invisible gap, exactly what the task
    forbids."""
    with monitor() as m:
        pass
    assert m.install_failures == []
    for spec in REGISTRY:
        mod = importlib.import_module(spec.module)
        assert callable(getattr(mod, spec.symbol, None)), f"{spec.module}.{spec.symbol}"
        for extra_module in spec.also_patch:
            extra = importlib.import_module(extra_module)
            assert callable(getattr(extra, spec.symbol, None)), f"{extra_module}.{spec.symbol}"


def test_monitor_records_currency_lane_claiming_the_all_caps_prompt() -> None:
    """Required self-test from the task spec: for a prompt known to be claimed by the currency
    lane, the monitor must record `static_currency_identity_admitted` returning truthy."""
    calls = attribute(ALL_CAPS_CURRENCY_PROMPT)
    identity_calls = [c for c in calls if c.symbol == "static_currency_identity_admitted"]
    assert identity_calls, "static_currency_identity_admitted was never invoked"
    assert all(c.returned_truthy for c in identity_calls), (
        f"expected every call to return truthy for {ALL_CAPS_CURRENCY_PROMPT!r}; "
        f"got {[c.return_repr for c in identity_calls]}"
    )


def test_monitor_does_not_claim_a_plain_arithmetic_prompt_via_currency() -> None:
    """Required self-test from the task spec, negative side: a plain arithmetic prompt must NOT
    make `static_currency_identity_admitted` return truthy."""
    calls = attribute(PLAIN_ARITHMETIC_PROMPT)
    identity_calls = [c for c in calls if c.symbol == "static_currency_identity_admitted"]
    assert identity_calls, "static_currency_identity_admitted was never invoked"
    assert not any(c.returned_truthy for c in identity_calls), (
        f"expected no truthy call for {PLAIN_ARITHMETIC_PROMPT!r}; "
        f"got {[c.return_repr for c in identity_calls]}"
    )


def test_monitor_mechanics_install_and_restore_are_clean() -> None:
    """Assert the monitor's own mechanics: installing replaces the module attribute with a
    wrapper, calling through it logs a record, and leaving the `with` block restores the exact
    original object -- not just an equal-behaving one."""
    mod = importlib.import_module("core.currency_value_contract")
    original = mod.static_currency_identity_admitted

    m = ClaimMonitor()
    assert m.calls == []
    m.install()
    try:
        assert mod.static_currency_identity_admitted is not original, "install did not wrap"
        mod.static_currency_identity_admitted("ALL is a currency code, name it")
        matching = [c for c in m.calls if c.symbol == "static_currency_identity_admitted"]
        assert len(matching) == 1
        assert matching[0].module == "core.currency_value_contract"
        assert matching[0].returned_truthy is True
    finally:
        m.uninstall()
    assert mod.static_currency_identity_admitted is original, "uninstall did not restore identity"


def test_monitor_restores_even_when_the_wrapped_block_raises() -> None:
    mod = importlib.import_module("core.currency_value_contract")
    original = mod.static_currency_identity_admitted

    class _SimulatedFailureError(Exception):
        pass

    try:
        with monitor():
            assert mod.static_currency_identity_admitted is not original
            raise _SimulatedFailureError("simulated failure inside the monitored block")
    except _SimulatedFailureError:
        pass
    assert mod.static_currency_identity_admitted is original


def test_monitor_call_log_is_ordered_not_just_a_set() -> None:
    """`.calls` is a list, appended to in real invocation order -- not deduplicated, not sorted by
    symbol name. A prompt that triggers the same probe more than once (e.g. once directly and
    once as a nested call from another probe in the registry) must show every occurrence, in the
    order it happened."""
    calls = attribute(ALL_CAPS_CURRENCY_PROMPT)
    assert isinstance(calls, list)
    assert len(calls) > len(REGISTRY), (
        "expected more raw call events than registry entries for this prompt, because "
        "maybe_answer_currency_value's real body calls asks_for_dynamic_currency_value and "
        "static_currency_identity_admitted itself, and those are ALSO independently probed"
    )
    timestamps = [c.timestamp for c in calls]
    assert timestamps == sorted(timestamps), "call log must be in real invocation order"


# ---------------------------------------------------------------------------------------------
# Regression checks for the documented blind spots: prove, live, that patching ONLY the defining
# module misses the real production call path for these three symbols, and that `monitor()`'s
# `also_patch` entry is what actually closes the gap. If a future refactor removes the re-export
# these depend on, these tests fail loudly rather than the documentation quietly going stale.
# ---------------------------------------------------------------------------------------------


def test_defining_module_patch_alone_is_blind_to_the_real_builder_facade_path() -> None:
    from core.agent_runtime.fast_path_facade import FastPathFacadeMixin

    defining = importlib.import_module("core.agent_runtime.fast_paths_builder")
    original = defining.looks_like_builder_request
    calls = []

    def wrapper(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    defining.looks_like_builder_request = wrapper
    try:
        FastPathFacadeMixin._looks_like_builder_request(None, "please tidy the code a bit")
    finally:
        defining.looks_like_builder_request = original

    assert calls == [], (
        "expected the real facade path (agent_fast_paths.looks_like_builder_request, a copy "
        "bound at core/agent_runtime/fast_paths.py import time) to NOT observe a patch applied "
        "only to the defining module -- if this now fails, the re-export was removed and the "
        "REGISTRY's also_patch note for looks_like_builder_request is stale"
    )


def test_also_patch_target_closes_the_builder_facade_blind_spot() -> None:
    from core.agent_runtime.fast_path_facade import FastPathFacadeMixin

    with monitor() as m:
        FastPathFacadeMixin._looks_like_builder_request(None, "please tidy the code a bit")

    hits = [
        c
        for c in m.calls
        if c.symbol == "looks_like_builder_request" and c.patched_module == "core.agent_runtime.fast_paths"
    ]
    assert hits, "monitor() with its also_patch entry should have observed the facade call"


def test_defining_module_patch_alone_is_blind_to_the_real_intake_request_text_path() -> None:
    import core.agent_runtime.agent as agent_mod

    defining = importlib.import_module("core.within_turn_retraction")
    original = defining.intake_request_text
    calls = []

    def wrapper(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    defining.intake_request_text = wrapper
    try:
        # apps.vool_agent bound its OWN copy of this name at module import time; calling it
        # through that bound copy is exactly what VoolAgent._run_once_inner does.
        agent_mod.intake_request_text("hello there")
    finally:
        defining.intake_request_text = original

    assert calls == [], (
        "expected the agent module's own module-level copy of intake_request_text to NOT observe "
        "a patch applied only to the defining module core.within_turn_retraction -- if this now "
        "fails, apps/vool_agent.py stopped importing it at module top level and the REGISTRY's "
        "also_patch note is stale"
    )


def test_also_patch_target_closes_the_intake_request_text_blind_spot() -> None:
    import core.agent_runtime.agent as agent_mod

    with monitor() as m:
        agent_mod.intake_request_text("hello there")

    hits = [
        c
        for c in m.calls
        if c.symbol == "intake_request_text" and c.patched_module == "core.agent_runtime.agent"
    ]
    assert hits, "monitor() with its also_patch entry should have observed the call through the agent module's own copy (core.agent_runtime.agent since M1)"


def test_report_produces_a_readable_ordered_trace_with_observability_caveats() -> None:
    text = report(ALL_CAPS_CURRENCY_PROMPT)
    assert "static_currency_identity_admitted" in text
    assert "CLAIMED" in text
    assert "Observability caveats" in text
    # Every non-observable symbol's caveat must be listed so a reader never has to trust an
    # unstated assumption about coverage.
    for spec in REGISTRY:
        if spec.observability != "observable":
            assert spec.symbol in text
