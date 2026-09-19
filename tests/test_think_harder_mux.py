"""Live multi-model mux (memory_first_router): off by default, guarded, votes across top-N local models.

No live model is called — the router's _invoke_manifest is replaced with a fake that returns canned
answers per model, so this exercises the mux selection/guards deterministically.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.think_harder import mux_sample_count
from core.memory_first_router import MemoryFirstRouter, _mux_vote


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VOOL_SLM_MUX_MODE", raising=False)
    monkeypatch.delenv("VOOL_SLM_MUX_N", raising=False)
    yield


class _Manifest:
    def __init__(self, provider_id: str, model_name: str, locality: str = "local") -> None:
        self.provider_id = provider_id
        self.model_name = model_name
        self.provider_name = provider_id
        self.metadata = {"deployment_class": locality}
        self.runtime_config = {}


class _Resp:
    def __init__(self, text: str) -> None:
        self.output_text = text


def _router(answers: dict, fail: set | None = None, raise_on: set | None = None) -> MemoryFirstRouter:
    """A router with just enough wired to run _maybe_mux_manifests: a fake _invoke_manifest.

    `raise_on` models a provider whose _invoke_manifest raises BEFORE its own try (build_adapter /
    health_check), which must not hang the collector.
    """
    fail = fail or set()
    raise_on = raise_on or set()
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)

    def _fake_invoke(*, manifest, request, output_mode, task, source_context, task_kind=""):
        if manifest.model_name in raise_on:
            raise RuntimeError("build_adapter blew up before _invoke_manifest's own try")
        if manifest.model_name in fail:
            return None, None, "boom"
        return object(), _Resp(answers.get(manifest.model_name, "")), None

    router._invoke_manifest = _fake_invoke  # type: ignore[method-assign]
    return router


def _mux(router: MemoryFirstRouter, manifests, **over):
    kwargs = dict(
        ranked_manifests=manifests,
        request=None,
        output_mode="plain_text",
        task=None,
        source_context={},
        preferred_provider="",
        preferred_model="",
    )
    kwargs.update(over)
    return router._maybe_mux_manifests(**kwargs)


_THREE = [
    _Manifest("p-a", "qwen2.5:7b"),
    _Manifest("p-b", "qwen2.5:3b"),
    _Manifest("p-c", "qwen3:0.6b"),
]


# --- config ----------------------------------------------------------------

def test_mux_sample_count_default_and_env(monkeypatch) -> None:
    assert mux_sample_count() == 3
    monkeypatch.setenv("VOOL_SLM_MUX_N", "5")
    assert mux_sample_count() == 5
    monkeypatch.setenv("VOOL_SLM_MUX_N", "1")  # below the 2 floor -> default
    assert mux_sample_count() == 3
    monkeypatch.setenv("VOOL_SLM_MUX_N", "abc")
    assert mux_sample_count() == 3


# --- pure vote -------------------------------------------------------------

def test_mux_vote_picks_majority() -> None:
    a, b, c = _THREE
    succeeded = [(a, object(), _Resp("It is 42")), (b, object(), _Resp("answer: 42")), (c, object(), _Resp("nope, 7"))]
    rank = {"p-a": 0, "p-b": 1, "p-c": 2}
    winner = _mux_vote(succeeded, rank)
    assert winner[0].provider_id in {"p-a", "p-b"}  # the "42" cluster
    assert "42" in winner[2].output_text


def test_mux_vote_tie_breaks_toward_higher_rank() -> None:
    a, b = _THREE[0], _THREE[1]
    # each answer unique -> tie; higher-ranked (p-a) must win
    succeeded = [(b, object(), _Resp("beta")), (a, object(), _Resp("alpha"))]
    rank = {"p-a": 0, "p-b": 1}
    winner = _mux_vote(succeeded, rank)
    assert winner[0].provider_id == "p-a"


# --- disabled / guards (no mux) --------------------------------------------

def test_disabled_by_default_is_noop() -> None:
    router = _router({"qwen2.5:7b": "x", "qwen2.5:3b": "x"})
    assert _mux(router, _THREE) == (None, None, None, [], False)


def test_streaming_turn_is_not_muxed(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    router = _router({"qwen2.5:7b": "x", "qwen2.5:3b": "x"})
    _m, _a, _r, _att, used = _mux(router, _THREE, source_context={"runtime_event_stream_id": "s1"})
    assert used is False


def test_structured_output_is_not_muxed(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    router = _router({"qwen2.5:7b": "x", "qwen2.5:3b": "x"})
    _m, _a, _r, _att, used = _mux(router, _THREE, output_mode="json_object")
    assert used is False


def test_explicit_model_pref_is_not_overridden(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    router = _router({"qwen2.5:7b": "x", "qwen2.5:3b": "x"})
    _m, _a, _r, _att, used = _mux(router, _THREE, preferred_model="qwen2.5:7b")
    assert used is False


def test_fewer_than_two_local_models_is_noop(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    manifests = [_Manifest("p-a", "qwen2.5:7b", "local"), _Manifest("p-r", "gpt-4o", "remote")]
    router = _router({"qwen2.5:7b": "x"})
    _m, _a, _r, _att, used = _mux(router, manifests)
    assert used is False  # only one local candidate to vote across


# --- enabled: votes across models ------------------------------------------

def test_enabled_votes_and_returns_majority_winner(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    router = _router({"qwen2.5:7b": "It is 42", "qwen2.5:3b": "answer: 42", "qwen3:0.6b": "totally different"})
    manifest, adapter, response, attempted, used = _mux(router, _THREE)
    assert used is True
    assert manifest.provider_id == "p-a"  # higher-ranked of the agreeing "42" cluster
    assert "42" in response.output_text and adapter is not None
    assert "p-c" in attempted  # the dissenting model still recorded


def test_all_samples_failing_returns_no_winner_but_used(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    router = _router({}, fail={"qwen2.5:7b", "qwen2.5:3b", "qwen3:0.6b"})
    manifest, _adapter, response, attempted, used = _mux(router, _THREE)
    assert used is True and manifest is None and response is None
    assert set(attempted) == {"p-a", "p-b", "p-c"}  # falls through to the normal fallback path


def test_partial_failure_still_votes_over_survivors(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    router = _router({"qwen2.5:7b": "It is 42", "qwen2.5:3b": "answer: 42"}, fail={"qwen3:0.6b"})
    _manifest, _adapter, response, attempted, used = _mux(router, _THREE)
    assert used is True
    assert "42" in response.output_text
    assert "p-c" in attempted  # the failed sample recorded


def test_count_env_limits_models_sampled(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    monkeypatch.setenv("VOOL_SLM_MUX_N", "2")  # only vote across the top 2 models
    router = _router({"qwen2.5:7b": "It is 42", "qwen2.5:3b": "answer: 42", "qwen3:0.6b": "different"})
    _manifest, _adapter, _response, attempted, used = _mux(router, _THREE)
    assert used is True
    assert "p-c" not in attempted  # the 3rd model was never sampled (N=2)


def test_worker_exception_does_not_hang_and_is_recorded(monkeypatch) -> None:
    # A worker whose _invoke_manifest raises before enqueuing must not block result_queue.get() forever.
    monkeypatch.setenv("VOOL_SLM_MUX_MODE", "1")
    router = _router({"qwen2.5:7b": "It is 42", "qwen2.5:3b": "answer: 42"}, raise_on={"qwen3:0.6b"})
    _manifest, _adapter, response, attempted, used = _mux(router, _THREE)
    assert used is True
    assert "42" in response.output_text  # the two survivors still voted
    assert "p-c" in attempted  # the raising worker was recorded, not left hanging
