from __future__ import annotations

import json

from core.agent_runtime.creative_revise import maybe_revise, revise_enabled
from core.prompt_doctor import DIMENSIONS

_KEYS = [d.key for d in DIMENSIONS]


def scores(value: float, **overrides) -> str:
    d = {k: value for k in _KEYS}
    d.update(overrides)
    return json.dumps(d)


class _Stub:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, _prompt: str) -> str:
        self.calls += 1
        return self.replies.pop(0)


def test_disabled_by_default_returns_text_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_CREATIVE_REVISE", raising=False)
    stub = _Stub([scores(9)])
    r = maybe_revise("a prompt", model_client=stub, medium="video")
    assert r.revised is False
    assert r.text == "a prompt"
    assert stub.calls == 0  # no model call when disabled
    assert revise_enabled() is False


def test_enabled_ships_first_pass_no_rewrite(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_CREATIVE_REVISE", "1")
    stub = _Stub([scores(9)])
    r = maybe_revise("already great", model_client=stub, medium="video")
    assert r.revised is False       # scored high, nothing rewritten
    assert r.text == "already great"


def test_enabled_rewrites_weak_prompt(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_CREATIVE_REVISE", "on")
    stub = _Stub([scores(5), "A SHARPER PROMPT", scores(9)])
    r = maybe_revise("weak prompt", model_client=stub, medium="video")
    assert r.revised is True
    assert r.text == "A SHARPER PROMPT"


def test_enabled_unsafe_block_withholds_text(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_CREATIVE_REVISE", "yes")
    stub = _Stub([scores(9, safety=1)])
    r = maybe_revise("unsafe idea", model_client=stub, medium="video")
    assert r.blocked is True
    assert r.text == ""


def test_empty_input_short_circuits(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_CREATIVE_REVISE", "1")
    stub = _Stub([])
    r = maybe_revise("   ", model_client=stub, medium="video")
    assert r.revised is False and r.text == "   "
    assert stub.calls == 0
