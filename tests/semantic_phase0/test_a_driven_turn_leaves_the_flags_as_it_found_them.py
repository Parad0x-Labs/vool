"""A driven turn puts the REACH and resolver flags back the way it found them.

CI shard 3 on 4b39b40d5 (2026-11-24): a file that drove turns through the replay `_drive` helper with
the flag "0" left it at "0" for every later file in the shard, and the shadow runtime's test found no
turn recorder. The helpers now hold the flags for one turn only, whether the turn answers or raises.
"""
from __future__ import annotations

import os

import pytest


class _Agent:
    def __init__(self, raises: bool) -> None:
        self.raises = raises

    def run_once(self, text, **_kwargs):
        if self.raises:
            raise RuntimeError("turn failed")
        return {"response": text}


@pytest.mark.parametrize("before", [None, "1", "0"])
@pytest.mark.parametrize("raises", [False, True])
def test_the_replay_drive_restores_the_reach_flag(monkeypatch, before, raises):
    from tests.semantic_phase0.test_semantic_replay_equivalence import _drive

    if before is None:
        monkeypatch.delenv("VOOL_SEMANTIC_REACH", raising=False)
    else:
        monkeypatch.setenv("VOOL_SEMANTIC_REACH", before)
    _drive(lambda: _Agent(raises), "hi", "s", flag="0" if before != "0" else "1")
    assert os.environ.get("VOOL_SEMANTIC_REACH") == before


@pytest.mark.parametrize("raises", [False, True])
def test_the_shadow_drive_restores_both_flags(monkeypatch, raises):
    from tests.semantic_phase0.test_semantic_shadow_equivalence import _drive

    monkeypatch.delenv("VOOL_SEMANTIC_REACH", raising=False)
    monkeypatch.setenv("VOOL_SEMANTIC_RESOLVER", "off")
    _drive(lambda: _Agent(raises), "hi", "s", mode="shadow")
    assert os.environ.get("VOOL_SEMANTIC_REACH") is None
    assert os.environ.get("VOOL_SEMANTIC_RESOLVER") == "off"
