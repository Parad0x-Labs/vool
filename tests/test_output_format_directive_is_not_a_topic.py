"""How the answer must LOOK never decides whether the runtime goes to the network.

Found by live Set 6 on the converged candidate (set6-24, 2026-08-13):

    Explain quantum tunneling using exactly three words. NO JSON. NO markdown. NO punctuation.

performed TEN web calls with a full started/completed retrieval receipt pair. The answer was
correct ("particles pass barrier"), so nothing about the reply gave the retrieval away -- only the
receipts did. The cause is that "json" is a specific-troubleshooting marker and the format
directive left it sitting in the retrieval-eligible text, so a rule about punctuation bought a
research lane.

`fix(runtime): separate structured task and output authority` already established this boundary for
the task router. The retrieval decision never received it. These tests pin both directions, because
the interesting half is the one that must NOT change: "json" as a genuine subject still researches.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.curiosity_roamer import _adaptive_research_decision
from core.retrieval_constraints import (
    analyze_retrieval_constraints,
    analyze_retrieval_request_authority,
)


def _decision(prompt: str, *, task_class: str = "research") -> dict[str, object]:
    with mock.patch("core.curiosity_roamer.policy_engine.allow_web_fallback", return_value=True):
        return _adaptive_research_decision(
            user_input=prompt,
            classification={"task_class": task_class},
            interpretation=SimpleNamespace(topic_hints=["web", "json", "current", "error"]),
            source_context={"surface": "api", "platform": "api"},
        )


@pytest.mark.parametrize(
    "prompt",
    (
        # The exact live reproduction.
        "Explain quantum tunneling using exactly three words. NO JSON. NO markdown. NO punctuation.",
        # Fresh subject, fresh format vocabulary, same invariant.
        "Define entropy in two words. No markdown. Raw text only.",
        "Summarise photosynthesis without bullet points or headings.",
        "Name the seven continents. Do not use tables or emoji.",
        # Sloppy user-style.
        "explain osmosis in 3 words, dont use markdown or curly braces",
    ),
)
@pytest.mark.parametrize("task_class", ("research", "chat_research", "unknown", "chat_conversation"))
def test_a_format_directive_never_buys_a_research_lane(prompt: str, task_class: str) -> None:
    assert _decision(prompt, task_class=task_class)["enabled"] is False


@pytest.mark.parametrize(
    "prompt",
    (
        "Look up the latest SQLite release notes online.",
        "Verify whether asyncio.run is supported in Python 3.12 using current docs.",
        "Check Toly on X and tell me who that is.",
        # The discrimination that matters: json as a SUBJECT, not as a format rule.
        "My json parser throws a KeyError on nested arrays, what is the current fix?",
        # A format word inside a genuine live request must not disable retrieval either.
        "Look up the current markdown spec version on the CommonMark site.",
    ),
)
def test_a_real_live_request_still_researches(prompt: str) -> None:
    assert _decision(prompt)["enabled"] is True


@pytest.mark.parametrize(
    "prompt",
    (
        "Explain gravity. NO JSON.",
        "Define recursion. Raw text only.",
        "List the noble gases without markdown.",
    ),
)
def test_a_format_directive_is_not_a_retrieval_prohibition(prompt: str) -> None:
    """The opposite error, and the more dangerous one: "no markdown" is not "no web".

    If a format rule were mistaken for a retrieval veto, the runtime would silently refuse live
    data the user never forbade, and would report a prohibition that was never stated.
    """

    constraints = analyze_retrieval_constraints(prompt)

    assert constraints.has_prohibition is False
    assert constraints.forbids_external_retrieval is False
    assert constraints.forbids_all_tools is False
    assert constraints.prohibited_toolsets == frozenset()


def test_a_real_retrieval_prohibition_still_registers_alongside_a_format_rule() -> None:
    """Both kinds of rule in one turn: the retrieval veto survives, the format rule stays inert."""

    authority = analyze_retrieval_request_authority(
        "Explain the dead cat bounce idiom. Do NOT look up live prices. No markdown."
    )

    assert authority.constraints.has_prohibition is True
    assert "no markdown" not in authority.candidate_text.casefold()
    assert _decision("Explain the dead cat bounce idiom. Do NOT look up live prices. No markdown.")[
        "enabled"
    ] is False


def test_the_format_directive_leaves_the_actual_subject_intact() -> None:
    """The strip must remove the rule and nothing else -- the topic still has to be searchable."""

    eligible = analyze_retrieval_constraints(
        "Look up the current CommonMark release. No markdown in your reply."
    ).eligible_text.casefold()

    assert "commonmark" in eligible
    assert "current" in eligible


import core.retrieval_constraints as _constraints_module_at_import

#: Captured at import so a monkeypatched module attribute can still be restored to the
#: genuine pattern mid-test (monkeypatch.undo would drop the second layer too).
_ORIGINAL_OUTPUT_FORMAT_DIRECTIVE_RE = (
    _constraints_module_at_import._OUTPUT_FORMAT_DIRECTIVE_RE
)

_SABOTAGE_PROMPT = (
    "Explain quantum tunneling using exactly three words. NO JSON. NO markdown. NO punctuation."
)


def test_sabotage_restoring_format_text_to_the_eligible_surface_researches_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the strip is load-bearing rather than cosmetic.

    UPDATED 2026-08-15: this invariant is now defended TWICE, and disabling one layer alone no
    longer flips the outcome -- which is why this sabotage started passing-by-absorption and the
    assertion had to be rebuilt rather than deleted.

      layer 1 (here): `_OUTPUT_FORMAT_DIRECTIVE_RE` strips the format directive before the text
              ever reaches the research-eligible surface;
      layer 2 (new):  `_TROUBLESHOOTING_ARTIFACT_MARKERS` -- a bare format name ("json") is an
              output constraint, not evidence of a broken system, so it authorizes research only
              when the same text also carries a problem cue.

    Disabling layer 1 alone leaves layer 2 holding the line, so the honest way to prove layer 1
    still carries weight is to disable BOTH and show research fires, then restore layer 1 alone
    and show it blocks on its own.
    """

    import re

    import core.curiosity_roamer as roamer_module
    import core.retrieval_constraints as constraints_module

    dead = re.compile(r"(?!x)x")

    # Both layers down: the pre-repair world, where a format directive was research evidence.
    monkeypatch.setattr(constraints_module, "_OUTPUT_FORMAT_DIRECTIVE_RE", dead)
    # Layer 2 down means the PRE-REPAIR classification: a bare format name counted as a problem
    # all by itself. Promote the artifact names into the problem family (and empty the artifact
    # family so nothing still demands a cue) -- appending to the artifact tuple instead would
    # leave the split intact and sabotage nothing.
    monkeypatch.setattr(
        roamer_module,
        "_TROUBLESHOOTING_PROBLEM_MARKERS",
        roamer_module._TROUBLESHOOTING_PROBLEM_MARKERS + roamer_module._TROUBLESHOOTING_ARTIFACT_MARKERS,
    )
    monkeypatch.setattr(roamer_module, "_TROUBLESHOOTING_ARTIFACT_MARKERS", ())
    assert _decision(_SABOTAGE_PROMPT)["enabled"] is True, (
        "with both guards disabled a bare format directive no longer reaches research at all -- "
        "the sabotage is landing somewhere it cannot measure"
    )

    # Layer 1 restored, layer 2 still down: layer 1 blocks on its own.
    monkeypatch.setattr(
        constraints_module,
        "_OUTPUT_FORMAT_DIRECTIVE_RE",
        _ORIGINAL_OUTPUT_FORMAT_DIRECTIVE_RE,
    )
    assert _decision(_SABOTAGE_PROMPT)["enabled"] is False


def test_the_second_guard_blocks_a_bare_format_name_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror of the above: layer 2 holds with layer 1 disabled.

    Without this, the pair could both be dead and the suite would still be green through the
    combined sabotage above.
    """

    import re

    import core.retrieval_constraints as constraints_module

    monkeypatch.setattr(constraints_module, "_OUTPUT_FORMAT_DIRECTIVE_RE", re.compile(r"(?!x)x"))

    assert _decision(_SABOTAGE_PROMPT)["enabled"] is False


def test_a_format_name_beside_a_real_problem_cue_still_researches() -> None:
    """Negative control for layer 2: the split must not disarm genuine troubleshooting."""

    assert _decision("my config.json throws a parse error on startup, how do I fix it")["enabled"] is True
