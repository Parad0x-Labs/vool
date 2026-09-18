"""On a near-miss, no argument-free machine read may be guessed into an answer.

A NEAR-MISS is "no detector claimed anything, but a tool-ish noun is in there". The whole
read-only menu is offered to a 0.6B model, and every family that needs an ARGUMENT falls open
when it has none. The families whose arguments are CONSTANT cannot fall open, so on a near-miss a
wrong pick silently replaces the user's answer with a report about their machine.

The front door already knew this and guarded it -- for two of the four. `disk_usage` and
`machine_specs` were listed; `list_processes` (a sort key derived from the sentence) and
`host_state` (`{}`) were not, and neither of those can be None either.

Measured live 2026-07-30 on e56ed09: "Explain how an operating system decides which process gets
CPU time next." was claimed by no detector, counted as a near-miss on the word "process", and
answered with "Top processes by CPU right now:" -- a ranking of the operator's own Mac in reply to
a question about scheduling theory.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core import runtime_paths
from core.agent_runtime.intent_claims import near_miss, probe_claims
from core.agent_runtime.turn_frontdoor import _maybe_arbitrate_intent

# Sentences that no detector claims but that carry a tool-ish noun, so the arbiter IS consulted.
#
# This is not an edge case: narrowing the disk, specs and process detectors turned every one of
# the sentences they used to over-claim into an uncorroborated near-miss. Without the guard below
# the over-claim simply moves from the detector to the arbiter, and the same prose gets answered
# with the same machine report by a different route.
NEAR_MISS_PROSE = (
    "Explain how an operating system decides which process gets CPU time next.",
    "Our warehouse is out of floor space so the pallets of hard drive spares are stacked in the corridor.",
    "In the 1990s a typical hard drive held 40 megabytes and cost more than the rest of the machine.",
    "My landlord says the storage space in the basement is included in the rent, is that normal in Lithuania?",
    "Our new hire asked what CPU means, can you give her a one paragraph answer?",
    "The memory of that trip to Nida is the only thing keeping me going this winter.",
    "Her thesis is about how memory works in people with early dementia.",
    "Our deployment process uses a lot of memory on the CI box, not on my laptop.",
    "My colleague says his machine has 64 GB of RAM and still swaps, is that plausible?",
    "Translate this into Lithuanian: the device has eight cores and sixteen gigabytes of memory.",
)

# Every family whose mapped arguments are constant, so it can never fall open on a missing one.
ARGUMENT_FREE_FAMILIES = ("disk_usage", "machine_specs", "list_processes", "host_state")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    runtime_paths.configure_runtime_home(tmp_path / "home")
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")
    yield
    runtime_paths.configure_runtime_home(None)


def _gate(text: str, gate: str = ""):
    """One of the front door's TWO arbitration call sites, named by `gate`.

    Naming it is load-bearing HERE in particular. The gate is split because the two signals want
    opposite positions -- competing readings settled before the deterministic lanes, a near-miss
    only after they have all declined -- and each call site declines the other's signal outright.
    A near-miss case left on the ambiguity gate would therefore get None for the wrong reason, and
    the guard below (which asserts exactly None, and no tool call) would pass without the
    uncorroborated-near-miss rule ever being consulted. Same class of vacuum the docstring on
    `test_an_argument_free_pick_on_a_near_miss_is_declined` already warns about.
    """
    from core.agent_runtime.turn_frontdoor import _ARBITRATE_ON_AMBIGUITY

    return _maybe_arbitrate_intent(
        mock.Mock(),
        effective_input=text,
        session_id="openclaw:neartestneartest0000",
        source_surface="chat",
        source_context={},
        gate=gate or _ARBITRATE_ON_AMBIGUITY,
    )


@pytest.mark.parametrize("prose", NEAR_MISS_PROSE)
def test_these_sentences_really_are_uncorroborated_near_misses(prose: str) -> None:
    """Guards the premise: if a detector starts claiming these, the test below stops proving anything."""
    claims = probe_claims(prose)
    assert claims == [], (prose, claims)
    assert near_miss(prose, claims) is True, prose


@pytest.mark.parametrize("prose", NEAR_MISS_PROSE)
@pytest.mark.parametrize("family", ARGUMENT_FREE_FAMILIES)
def test_an_argument_free_pick_on_a_near_miss_is_declined(prose: str, family: str) -> None:
    """The tool must never RUN -- asserting only on the return value proves nothing here.

    `_maybe_arbitrate_intent` ends in a blanket `except Exception: return None` so that
    arbitration can never break a turn. A tool patched with `side_effect=AssertionError` is
    therefore swallowed and the gate still returns None, so the test passes whether the guard
    works or not. Checked by sabotage: with the guard reverted to its original two families, the
    side_effect form of this test stayed green on all 40 cases. Assert on the CALL.
    """
    from core.agent_runtime.turn_frontdoor import _ARBITRATE_ON_NEAR_MISS
    from core.intent_arbiter import ArbiterDecision

    tool = mock.Mock(return_value=mock.Mock(ok=True, details={}, response_text="a machine report"))
    with (
        mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision(family, "")),
        mock.patch("core.runtime_execution_tools.execute_runtime_tool", tool),
        mock.patch(
            "core.agent_runtime.fast_paths_machine._machine_tool_fast_path_result",
            return_value={"answered": family},
        ),
    ):
        result = _gate(prose, gate=_ARBITRATE_ON_NEAR_MISS)
    assert tool.call_args_list == [], (family, prose, tool.call_args_list)
    assert result is None, (family, prose, result)


def test_an_ambiguous_message_keeps_the_arbiters_authority() -> None:
    """The decline is scoped to near-misses. Where families really did claim, the pick still runs.

    Otherwise this fix would be the under-claiming kind: `_candidate_options` only offers families
    that actually claimed, so on an ambiguous message the arbiter is choosing between real
    readings rather than guessing at one.
    """
    from core.intent_arbiter import ArbiterDecision

    ambiguous = "right, can you check Token hunter folder on this machine and run audit, but only audit no changes"
    claims = probe_claims(ambiguous)
    assert len({claim.family for claim in claims}) >= 2, claims
    assert near_miss(ambiguous, claims) is False

    execution = mock.Mock(ok=True, details={}, response_text="Machine specs for this host:")
    with (
        mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision("machine_specs", "")),
        mock.patch("core.runtime_execution_tools.execute_runtime_tool", return_value=execution),
        mock.patch("core.agent_runtime.fast_paths_machine._machine_tool_fast_path_result", return_value={"ok": 1}),
    ):
        assert _gate(ambiguous) == {"ok": 1}
