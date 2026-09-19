"""Gauntlet — category 7: model router / lanes.

Existing coverage (tests/test_local_inference_autopilot.py, test_model_selection_local_heal.py,
test_vool_agent_brake.py) already pins the complexity matrix, VRAM fit, and the
provider self-heal. This file fills the audit's remaining router gaps, all
deterministic (no live model):

  - resolve_fallback_budget_seconds — the wall-clock budget for the provider
    fallback loop — was a pure function with no test.
  - the control path (stop/freeze) must never depend on the model: driving
    /stopx402 and /stopall through the HTTP dispatch must resolve without ever
    invoking the model provider.
  - a compact _message_complexity sanity pin at the routing boundary.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

pytestmark = [pytest.mark.gauntlet]


# ---------------------------------------------------------------------------
# 1. resolve_fallback_budget_seconds matrix (pure function)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lane", ["deep", "cloud", "human"])
def test_heavy_lanes_have_no_fallback_budget(lane):
    from core.memory_first_router import resolve_fallback_budget_seconds

    assert resolve_fallback_budget_seconds(lane, forced_cpu=False, no_usable_gpu=False) is None
    # Heavy lanes stay unbounded even when CPU-only.
    assert resolve_fallback_budget_seconds(lane, forced_cpu=True, no_usable_gpu=True) is None


def test_ordinary_lane_gpu_budget_is_60s():
    from core.memory_first_router import resolve_fallback_budget_seconds

    assert resolve_fallback_budget_seconds("daily", forced_cpu=False, no_usable_gpu=False) == 60.0


@pytest.mark.parametrize("forced_cpu,no_gpu", [(True, False), (False, True), (True, True)])
def test_ordinary_lane_cpu_budget_is_180s(forced_cpu, no_gpu):
    from core.memory_first_router import resolve_fallback_budget_seconds

    # CPU-only runs get the larger budget so a slow local generation can finish.
    assert resolve_fallback_budget_seconds("daily", forced_cpu=forced_cpu, no_usable_gpu=no_gpu) == 180.0


# ---------------------------------------------------------------------------
# 2. Control path is model-independent: stop/freeze never invoke the model
# ---------------------------------------------------------------------------

def _model_must_not_run(*_args, **_kwargs):
    raise AssertionError("the model provider was invoked for a control-command turn")


def test_http_stopx402_resolves_without_the_model(tmp_path, monkeypatch):
    from core import runtime_paths, vool_wallet
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    vool_wallet.get_or_create_wallet(runtime_home=tmp_path)  # real wallet in the tmp home

    resp = dispatch_post(
        path="/api/chat",
        body={"messages": [{"role": "user", "content": "/stopx402"}]},
        headers={},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: str(tmp_path),
        run_agent_provider=_model_must_not_run,
        resolve_null_domain_provider=lambda name: None,
    )
    assert resp.status == 200


def test_http_stopall_resolves_without_the_model(tmp_path, monkeypatch):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    # Patch the real detached-stop spawn so no VOOL processes are actually killed;
    # the point is only that the brake resolves the turn without the model.
    spawn_spy = mock.Mock(return_value=True)
    monkeypatch.setattr("installer.vool_stop.spawn_detached_stop", spawn_spy)

    resp = dispatch_post(
        path="/api/chat",
        body={"messages": [{"role": "user", "content": "/stopall"}]},
        headers={},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: str(tmp_path),
        run_agent_provider=_model_must_not_run,
        resolve_null_domain_provider=lambda name: None,
        client_host="127.0.0.1",  # /stopall is owner-local only; the owner's loopback session
    )
    assert resp.status == 200
    spawn_spy.assert_called_once()  # the shutdown was launched via the injected seam
    body = json.loads(resp.body)
    assert "shutting down" in json.dumps(body).lower() or "shut" in json.dumps(body).lower()


# ---------------------------------------------------------------------------
# 3. Message-complexity routing boundary (compact sanity pin)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hi", "trivial"),
        ("hey there", "trivial"),
        ("build me a full-stack website with auth", "heavy"),
        ("build me coolproject.null website", "heavy"),  # imperative build of a .null target
        ("how do i build a website?", ""),  # a question is NOT an imperative build
        ("npm run build", ""),              # tooling chatter is not a heavy build
        ("", ""),
    ],
)
def test_message_complexity_routing_boundary(text, expected):
    from core.local_inference_autopilot import _message_complexity

    assert _message_complexity(text) == expected
