"""Guards for the live context benchmark's honesty properties.

These run offline: the summarizer's model call is stubbed, so what is pinned here is the
benchmark's reporting shape and corpus layout, never a retention score. The retention
number itself only means something against live Ollama and is deliberately not asserted.
"""
from __future__ import annotations

import itertools
import os

import pytest

from core import conversation_summarizer
from tests.benchmarks import memory_compression_bench as bench


@pytest.fixture
def stub_summarizer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the model call so the corpus/report shape can be tested without Ollama."""

    def _fake(model: str, messages: list[dict], timeout: int = 60) -> str:
        return "## Key Facts\n- stub\n## Decisions Made\n- none\n## Open Questions\nNone\n## Context Summary\nStub."

    monkeypatch.setattr(conversation_summarizer, "_call_ollama", _fake)


def _fact_positions(history: list[dict[str, str]]) -> list[int]:
    plants = {plant for plant, _question, _answer in bench.FACTS}
    return [index for index, message in enumerate(history) if message["content"] in plants]


@pytest.mark.parametrize("turns", [12, 30, 60, 100, 200])
def test_build_history_plants_every_fact(turns: int) -> None:
    positions = _fact_positions(bench.build_history(turns))
    assert len(positions) == len(bench.FACTS)


@pytest.mark.parametrize("turns", [30, 60, 100, 200])
def test_facts_are_distributed_not_front_loaded(turns: int) -> None:
    """A front-loaded corpus makes the window's zero a property of the fixture, not the window."""
    history = bench.build_history(turns)
    positions = _fact_positions(history)
    # facts must not all sit in the opening turns: the last one lands in the final third
    assert positions[-1] > len(history) * 2 / 3
    # and they must be spread rather than clustered anywhere
    gaps = [b - a for a, b in itertools.pairwise(positions)]
    assert min(gaps) > 1


def test_sliding_window_score_tracks_turn_count() -> None:
    """The window's retention must be earned: short history keeps facts, long history loses them."""

    def _window_score(turns: int) -> int:
        history = bench.build_history(turns)
        return bench._retained_answers(
            [{"role": "system", "content": bench.SYSTEM_PROMPT}, *history[-bench.WINDOW_SIZE :]]
        )

    assert _window_score(12) > _window_score(30) > _window_score(100)


def test_spread_reports_range_not_point_estimate() -> None:
    assert bench._spread([3, 4, 3, 5]) == {"min": 3, "median": 3.5, "max": 5, "samples": 4}


def test_spread_handles_single_sample() -> None:
    assert bench._spread([4]) == {"min": 4, "median": 4.0, "max": 4, "samples": 1}


def test_repeats_multiply_the_reported_sample_count(stub_summarizer: None) -> None:
    results = bench.run_benchmark(turns=12, repeats=2)
    live = results["live_runtime"]
    assert live["repeats"] == 2
    assert live["retained_facts"]["samples"] == 2 * len(bench.FACTS)
    assert live["peak_tokens"]["samples"] == 2 * len(bench.FACTS)


def test_live_run_isolates_the_l3_store(stub_summarizer: None) -> None:
    """The bench must not read the operator's accumulated memory."""
    live = bench.run_benchmark(turns=12, repeats=1)["live_runtime"]
    assert live["l3_store"] == "isolated-temp-home"
    assert live["l3_nodes_visible"] == 0


def test_isolation_restores_the_runtime_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolation must hand back whatever home was configured, not clear it."""
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", "/sentinel/home")
    before_override = runtime_paths._VOOL_HOME_OVERRIDE
    with bench._isolated_l3_store() as home:
        assert str(home) != "/sentinel/home"
        assert runtime_paths.active_vool_home() == home.resolve()
    assert before_override == runtime_paths._VOOL_HOME_OVERRIDE
    assert os.environ["VOOL_HOME"] == "/sentinel/home"


def test_baselines_are_labelled_as_bounds_not_competitors(stub_summarizer: None) -> None:
    results = bench.run_benchmark(turns=12, repeats=1)
    assert results["sliding_window"]["kind"] == "illustrative_bound"
    assert results["raw"]["kind"] == "reference_ceiling"


def test_degraded_run_is_flagged_when_the_model_call_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A summarizer that cannot run leaves the transcript uncompacted and scores a perfect,
    unearned result; the run must report that it measured nothing."""

    def _boom(model: str, messages: list[dict], timeout: int = 60) -> str:
        raise OSError("ollama unreachable")

    # The picked model has to be forced: conftest blocks live Ollama under pytest, so the
    # summarizer would decline to call a model at all and _boom would never be reached --
    # this test would then pass while exercising a different degradation entirely.
    monkeypatch.setattr(conversation_summarizer, "_pick_model_uncached", lambda: "qwen2.5:7b")
    monkeypatch.setattr(conversation_summarizer, "_call_ollama", _boom)
    live = bench.run_benchmark(turns=30, repeats=1)["live_runtime"]
    assert live["summarizer_ran"] is False
    assert "ollama unreachable" in live["fallback_reason"]


def test_degraded_run_is_flagged_when_no_model_qualifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other way compaction silently does not happen: nothing installed is trustworthy."""
    monkeypatch.setattr(conversation_summarizer, "_pick_model_uncached", lambda: "")
    live = bench.run_benchmark(turns=30, repeats=1)["live_runtime"]
    assert live["summarizer_ran"] is False
    assert "no local model large enough" in live["fallback_reason"]


def test_degraded_run_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(model: str, messages: list[dict], timeout: int = 60) -> str:
        raise OSError("ollama unreachable")

    monkeypatch.setattr(conversation_summarizer, "_call_ollama", _boom)
    monkeypatch.setattr(
        "sys.argv", ["memory_compression_bench", "--turns", "30", "--repeats", "1", "--json"]
    )
    with pytest.raises(SystemExit) as excinfo:
        bench.main()
    assert excinfo.value.code == 1
