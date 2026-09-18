"""A tool that ran and a tool the model was told about are not the same thing.

Measured live 2026-08-03. The operator asked "can you check whole workspace project? tell me your
insights what is wrong or broken and what is good also what improvements you would suggest" and the
final answer was, in full:

    "I'll do a proper audit. Let me read the key files first."

The model's opening preamble, shipped as the finished answer. The receipts showed 3 tools run, 8
deferred, and a `tool_repeat_blocked` in the middle — so the story looked like "the model looped".

It did not loop. It never received anything. `_runtime_tool_observation_message` spent its 12,000
character window OLDEST-FIRST with no per-item limit, so the first observation could take all of
it and every later one hit `remaining <= 0` and was dropped without a word. The incident's first
tool was `workspace.list_tree` at ~100KB. Reduced to a fixture and measured before the fix:

    observations in       : 7
    observation lines out : 1
    markers that survived : []

Six files read, zero results delivered. The model asked again — correctly, having been given
nothing — and the repeat guard scored that as looping.

`max_chars` is a real PROMPT-WINDOW bound and stays: this block shares one context window with the
system prompt, the transcript and the user's message. What changed is how it is spent — an equal
slice each, surplus from the small ones handed back to the large ones, and anything that still
cannot fit CLIPPED and SAID rather than dropped. Same rule as everywhere else this session: a cap
is allowed, a silent cap is not.
"""
from __future__ import annotations

import pytest

from core.prompt_normalizer import _runtime_tool_observation_message

BUDGET = 12000


def _obs(index: int, size: int = 20) -> dict:
    return {
        "tool": "workspace.read_file",
        "path": f"f{index:02d}.py",
        "response": f"MARKER_{index:02d} " + ("y" * size),
    }


def _huge(tool: str = "workspace.list_tree", size: int = 100_000) -> dict:
    return {"tool": tool, "response": "x" * size}


def _render(observations: list[dict]):
    return _runtime_tool_observation_message({"runtime_tool_observations": observations})


# --------------------------------------------------------------------------------------
# The measured incident.
# --------------------------------------------------------------------------------------


def test_one_huge_observation_no_longer_starves_every_later_one() -> None:
    """The reduced incident. Before the fix this delivered exactly one line and no markers."""

    message = _render([_huge()] + [_obs(i) for i in range(1, 7)])

    survived = [i for i in range(1, 7) if f"MARKER_{i:02d}" in message.content]
    assert survived == [1, 2, 3, 4, 5, 6], (
        "a read that executed never reached the prompt, so the model asks for it again and the "
        "repeat guard calls that looping"
    )


def test_every_observation_gets_a_line() -> None:
    message = _render([_huge()] + [_obs(i) for i in range(1, 7)])

    assert message.metadata["same_turn_tool_observations"] == 7


def test_the_clipped_observation_says_it_was_clipped() -> None:
    """A silently shortened result is indistinguishable from a short one.

    That is how a model comes to believe it read a whole file. `workspace.read_file` already
    reports its own truncation; a clip applied here has to do the same.
    """

    message = _render([_huge()] + [_obs(i) for i in range(1, 4)])

    assert "clipped here" in message.content
    assert "more characters not shown" in message.content
    assert message.metadata["observations_clipped"] == ["workspace.list_tree"]


# --------------------------------------------------------------------------------------
# The window is still a bound. Fixing starvation by ignoring the budget fails the turn closed.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "observations"),
    [
        ("incident shape", [_huge()] + [_obs(i) for i in range(1, 7)]),
        ("eleven observations", [_huge()] + [_obs(i, 3000) for i in range(1, 11)]),
        ("all tiny", [_obs(i) for i in range(6)]),
        ("all huge", [_huge("workspace.read_file", 50_000) for _ in range(6)]),
        ("single huge", [_huge()]),
        ("one below the floor", [_huge(), _obs(1, 1)]),
    ],
)
def test_the_rendered_block_never_exceeds_the_window(label: str, observations: list[dict]) -> None:
    """The RENDERED block, not the JSON alone.

    The first version of this fix charged only for the encoded payload and overran by 70
    characters, because every entry also costs a `- ` prefix, a newline, and possibly a clip
    notice. A bound that is only approximately a bound is not one.
    """

    message = _render(observations)

    assert len(message.content) <= BUDGET, f"{label}: {len(message.content)} > {BUDGET}"


def test_small_observations_do_not_waste_the_window() -> None:
    """Equal slices alone would leave five sixths of the budget unspent.

    Six 20-char results beside one 100KB result: if each simply took a seventh, the big one would
    be cut to ~1700 characters while ~10,000 went unused. The surplus has to flow back.
    """

    message = _render([_huge()] + [_obs(i) for i in range(1, 7)])

    assert len(message.content) > BUDGET * 0.75, (
        "the surplus from the small observations was never handed back to the large one"
    )


# --------------------------------------------------------------------------------------
# What could not be shown at all is named.
# --------------------------------------------------------------------------------------


def test_observations_beyond_the_item_cap_are_named_not_dropped() -> None:
    message = _render([_obs(i) for i in range(40)])

    assert len(message.metadata["observations_withheld"]) == 8
    assert "workspace.read_file(f00.py)" in message.metadata["observations_withheld"]
    assert "NOT shown above" in message.content, "the model is not told its earlier work is missing"


def test_nothing_is_claimed_withheld_when_nothing_was() -> None:
    """The control. A notice about results nobody lost reads as a failure that did not happen."""

    message = _render([_obs(i) for i in range(4)])

    assert message.metadata["observations_withheld"] == []
    assert "NOT shown above" not in message.content


def test_the_item_cap_matches_what_the_store_keeps() -> None:
    """Showing fewer than the store retains is a second silent cut behind the first.

    Retention moved 12 -> 32 on 2026-08-03: a batched round appends up to 8 observations, so a
    29-tool turn evicted its early reads and the model re-requested files it had already been
    given (README.md read three times in one 9-round live turn). The CHARACTER budget is the real
    bound - 29 ordinary reads render in ~11.7k of 12k, and 32 large ones are clipped by the same
    fair-share rule - so the item cap only decides how many candidates that budget chooses between.
    """

    import inspect

    from core.agent_runtime import response_policy_tool_history as store

    assert "observations[-32:]" in inspect.getsource(store.append_tool_result_to_source_context), (
        "the store's retention changed; the prompt cap must move with it"
    )
    assert _render([_obs(i) for i in range(32)]).metadata["same_turn_tool_observations"] == 32


def test_a_batched_turns_worth_of_reads_all_survive() -> None:
    """29 tools is what the measured live turn ran. None of them may be evicted in silence."""

    message = _render([_obs(i) for i in range(29)])

    assert message.metadata["same_turn_tool_observations"] == 29
    assert message.metadata["observations_withheld"] == []
    assert len(message.content) <= BUDGET


def test_no_observations_renders_nothing() -> None:
    assert _render([]) is None


def test_a_label_names_the_path_so_it_can_be_acted_on() -> None:
    """"`workspace.read_file` did not run" is unactionable when five of them were requested."""

    message = _render([_obs(i) for i in range(40)])

    assert all("(" in name for name in message.metadata["observations_withheld"])


# --------------------------------------------------------------------------------------
# What the STORE dropped before the renderer ever saw it.
# --------------------------------------------------------------------------------------


def test_results_evicted_by_the_store_are_counted_and_stated() -> None:
    """The renderer names what IT withholds. It cannot name what never reached it.

    The loop allows 12 model rounds and a round can append up to 8 batch members, so a turn can
    produce ~96 observations against a 32-entry store. Without this counter the model receives 32
    results and is told nothing is missing — silently multiplying the exact loss every other fix in
    this file exists to remove, in proportion to how hard the turn worked.
    """

    message = _render([_obs(i) for i in range(32)])
    assert "fell out of the retained window" not in message.content

    with_eviction = _runtime_tool_observation_message(
        {
            "runtime_tool_observations": [_obs(i) for i in range(32)],
            "runtime_tool_observations_evicted": 64,
        }
    )

    assert "64 earlier tool results" in with_eviction.content
    assert "fell out of the retained window" in with_eviction.content


def test_the_store_counts_what_it_drops() -> None:
    """Through the real store, because the renderer test above passes whether or not it counts."""

    from core.agent_runtime.response_policy_tool_history import (
        append_tool_result_to_source_context,
    )

    class _Execution:
        response_text = "x"
        ok = True
        status = "executed"
        mode = "tool_executed"
        tool_name = "workspace.read_file"
        details: dict = {}

    class _Agent:
        def _tool_step_summary(self, text, fallback=""):
            return str(text or fallback)

        def _tool_history_observation_prompt(self, observation):
            return str(observation)

    context = {"runtime_tool_observations": [_obs(i) for i in range(40)]}
    updated = append_tool_result_to_source_context(
        _Agent(), context, execution=_Execution(), tool_name="workspace.read_file"
    )

    assert len(updated["runtime_tool_observations"]) == 32
    assert int(updated.get("runtime_tool_observations_evicted") or 0) >= 8, (
        "the store dropped results and recorded nothing, so the model is told nothing"
    )


def test_a_turn_that_fits_records_no_eviction() -> None:
    """The control. A count of zero must not render a notice about a loss that did not happen."""

    from core.agent_runtime.response_policy_tool_history import (
        append_tool_result_to_source_context,
    )

    class _Execution:
        response_text = "x"
        ok = True
        status = "executed"
        mode = "tool_executed"
        tool_name = "workspace.read_file"
        details: dict = {}

    class _Agent:
        def _tool_step_summary(self, text, fallback=""):
            return str(text or fallback)

        def _tool_history_observation_prompt(self, observation):
            return str(observation)

    updated = append_tool_result_to_source_context(
        _Agent(), {"runtime_tool_observations": [_obs(i) for i in range(3)]},
        execution=_Execution(), tool_name="workspace.read_file",
    )

    assert not updated.get("runtime_tool_observations_evicted")
