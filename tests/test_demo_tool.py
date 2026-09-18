from __future__ import annotations

import core.demo_source as demo_source
import core.policy_engine as policy_engine
from core.runtime_execution_tools import _demo_plan, execute_runtime_tool

_README = (
    "# VOOL\n\nhonest local AI agent that owns its keys\n\n"
    "## Features\n"
    "- Local-first - runs models on your own machine\n"
    "- Honest receipts - signed, offline-verifiable proofs\n"
)


def test_demo_plan_handler_happy(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    monkeypatch.setattr(demo_source, "fetch_readme", lambda o, r, fetcher=None: _README)
    res = _demo_plan({"url": "github.com/Parad0x-Labs/vool", "presenter": "woman", "seconds": 20})
    assert res.ok and res.status == "ok"
    assert "Demo video plan" in res.response_text
    assert res.details["plan"]["product"] == "VOOL"
    assert len(res.details["plan"]["shots"]) >= 4
    assert res.details["images"]
    assert res.details["observation"]["intent"] == "demo.plan"


def test_demo_plan_handler_applies_edits(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    monkeypatch.setattr(demo_source, "fetch_readme", lambda o, r, fetcher=None: _README)
    res = _demo_plan({
        "url": "github.com/o/r", "presenter": "woman", "seconds": 30,
        "edits": ["make it 45 seconds", "use a man presenter", "unparseable gibberish"],
    })
    assert res.ok
    assert res.details["plan"]["total_seconds"] == 45
    assert res.details["unapplied_edits"] == ["unparseable gibberish"]
    # presenter recast flowed into the shot prompts
    assert "man presenter" in res.details["plan"]["shots"][2]["generation_prompt"]


def test_demo_plan_handler_needs_url() -> None:
    assert _demo_plan({}).ok is False


def test_demo_plan_handler_rejects_bad_url(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    res = _demo_plan({"url": "not a github url"})
    assert res.ok is False and res.status == "error"


def test_demo_plan_blocked_by_per_turn_fetch_policy(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)   # web globally on
    monkeypatch.setattr(demo_source, "fetch_readme", lambda o, r, fetcher=None: _README)
    res = _demo_plan({"url": "github.com/o/r"}, source_context={"allow_remote_fetch": False})
    assert res.ok is False and res.status == "disabled_by_policy"


def test_demo_plan_disabled_when_web_off(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: False)
    res = execute_runtime_tool("demo.plan", {"url": "github.com/o/r"})
    assert res is not None and res.status == "disabled"


def test_demo_plan_dispatched_when_web_on(monkeypatch) -> None:
    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    monkeypatch.setattr(demo_source, "fetch_readme", lambda o, r, fetcher=None: _README)
    res = execute_runtime_tool("demo.plan", {"url": "github.com/o/r"})
    assert res is not None and res.ok and res.status == "ok"
