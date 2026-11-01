"""Retrieval evidence follows the window of the model expected to answer, on every path. An unknown or
too-small window keeps the declared default, so no turn gets less than before. Contributor: sls_0x."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.context_retrieval as cr
from core import memory_first_router as router_module
from core.context_capsule_v2 import resolve_budget
from tests.test_capsule_match_strength_eviction_20261002 import (
    HARBOR_DECISIVE,
    HARBOR_FILLERS,
    _seed,
    fresh_profile,  # noqa: F401
)

TURN = [{"role": "user", "content": "what did I decide about the harbor"}]


def test_a_cloud_turn_gets_the_full_ceiling():
    budget = cr._window_evidence_budget({"model_context_window": 131072}, TURN)
    assert budget.free_tokens == budget.evidence_target_tokens == cr._WINDOW_EVIDENCE_CEILING_TOKENS


def test_a_32k_local_turn_gets_evidence_within_its_window():
    budget = cr._window_evidence_budget({"model_context_window": 32768}, TURN)
    assert 420 < budget.free_tokens < cr._WINDOW_EVIDENCE_CEILING_TOKENS
    assert budget.free_tokens + budget.output_reserve_tokens + cr._WINDOW_PROMPT_RESERVE_TOKENS < 32768


@pytest.mark.parametrize("key", ["available_prompt_tokens", "model_context_window", "context_window", "num_ctx"])
def test_every_window_key_is_read(key):
    assert cr._window_evidence_budget({key: 32768}, TURN) is not None


@pytest.mark.parametrize("context", [None, {}, {"model_context_window": 0}, {"model_context_window": "soon"}])
def test_an_unknown_window_keeps_the_declared_default(context):
    assert cr._window_evidence_budget(context, TURN) is None


def test_a_small_or_full_window_keeps_the_declared_default_rather_than_nothing():
    assert cr._window_evidence_budget({"model_context_window": 4096}, TURN) is None
    long_chat = [{"role": "user", "content": "word " * 60000}]
    assert cr._window_evidence_budget({"model_context_window": 32768}, long_chat) is None


def test_an_explicit_budget_is_never_replaced(fresh_profile, monkeypatch):
    seen = {}

    def capture(*args, **kwargs):
        seen["budget"] = kwargs.get("budget")
        return list(args[2])

    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setattr(cr, "_capsule_v2_inject_retrieved", capture)
    explicit = resolve_budget(bucket="B", role="general")
    chat = "explicit-budget"
    _seed(fresh_profile, chat, "Ilse", HARBOR_FILLERS, HARBOR_DECISIVE)
    cr.inject_retrieved(
        chat, "q", TURN, access_policy=cr.resolve_memory_access_policy(chat_id=chat),
        source_context={"chat_id": chat, "runtime_home": fresh_profile, "model_context_window": 131072},
        budget=explicit,
    )
    assert seen["budget"] is explicit


def _capture_build(monkeypatch, *, window, surface):
    """The request seam every lane uses: what context reaches the prompt path for this turn."""
    router = router_module.MemoryFirstRouter.__new__(router_module.MemoryFirstRouter)
    router.planned_context_window_tokens = lambda **_: window
    seen = {}

    class Stop(Exception):
        pass

    def fake_normalize(**kwargs):
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(router_module, "normalize_prompt", fake_normalize)
    context = {"surface": surface}
    with pytest.raises(Stop):
        router._build_request(
            task=SimpleNamespace(task_id="t"), classification={"task_class": "unknown"},
            interpretation=SimpleNamespace(raw_text="q", user_demand_text=""), context_result=None, persona=None,
            output_mode="plain_text", task_kind="normal_chat", surface=surface, source_context=context,
        )
    return seen["source_context"], context


@pytest.mark.parametrize("surface, window", [
    ("channel", 131072),            # desktop chat on a cloud model
    ("mobile_companion", 131072),   # a phone turn
    ("api", 32768),                 # a remote turn on a 32K local model
    ("cli", 8192),                  # a small local model
])
def test_every_surface_reaches_the_prompt_path_with_its_window(monkeypatch, surface, window):
    delivered, original = _capture_build(monkeypatch, window=window, surface=surface)
    assert delivered["model_context_window"] == window
    assert delivered is original  # the prompt path records onto this same mapping


def test_an_unknown_window_stamps_nothing(monkeypatch):
    delivered, _ = _capture_build(monkeypatch, window=0, surface="channel")
    assert "model_context_window" not in delivered


def test_a_window_the_turn_already_carries_is_kept(monkeypatch):
    router = router_module.MemoryFirstRouter.__new__(router_module.MemoryFirstRouter)
    router.planned_context_window_tokens = lambda **_: 999
    stamped = router.stamp_planned_context_window({"num_ctx": 32768}, classification={})
    assert stamped.get("model_context_window") is None and stamped["num_ctx"] == 32768


@pytest.mark.parametrize("window", [131072, 32768])
def test_a_normal_turn_delivers_evidence_end_to_end(fresh_profile, window):
    chat = f"window-{window}"
    _seed(fresh_profile, chat, "Ilse", HARBOR_FILLERS, HARBOR_DECISIVE)
    question = "Which harbor does Ilse describe as the quietest one in Nordvik?"
    policy = cr.resolve_memory_access_policy(chat_id=chat)
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}], access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": fresh_profile, "model_context_window": window},
    )
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    assert "Nordvik" in block
    telemetry = cr.get_last_retrieval_telemetry()
    assert int(telemetry["evidence_budget"]["free_tokens"]) > 420



@pytest.mark.parametrize("windows,expected", [([], 0), ([8192, 32768], 8192), ([32768, 0], 0)])
def test_the_pool_window_fits_every_candidate_or_is_unknown(monkeypatch, windows, expected):
    monkeypatch.setattr(router_module, "provider_capability_truth_for_manifest", lambda w: SimpleNamespace(context_window=w))
    assert router_module._smallest_context_window(windows) == expected
