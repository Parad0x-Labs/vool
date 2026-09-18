"""GOBLIN inv 11 (EXTERNAL EFFECT HONESTY) — an ambiguous external effect outcome records
as UNKNOWN (not forced into a false result/error) and replays faithfully as unknown.

Sabotage seam: revert the record-mode `except EffectOutcomeUnknown` branch and the entry
becomes an 'error' — the first assertion goes red. Revert the replay branch and the replay
test raises ReplayedEffectFailure/DivergenceError instead of ReplayedEffectUnknown.
"""
from __future__ import annotations

import pytest

from core.kernel.effects import (
    EffectJournal,
    EffectOutcomeUnknown,
    EffectRunner,
    ReplayedEffectUnknown,
)


def test_ambiguous_effect_records_unknown_not_error_and_replays_as_unknown():
    def flaky(payload):
        raise EffectOutcomeUnknown("timeout_after_send", "POST dispatched, no ack received")

    runner = EffectRunner(mode="record")
    with pytest.raises(EffectOutcomeUnknown):
        runner.run("api.post", flaky, {"to": "x"})

    assert len(runner.journal) == 1
    entry = runner.journal.entries()[0]
    assert "unknown" in entry and "error" not in entry and "result" not in entry
    assert entry["unknown"] == {"reason": "timeout_after_send", "detail": "POST dispatched, no ack received"}

    # survives a JSON round-trip through the strict loader
    restored = EffectJournal.from_json(runner.journal.to_json())
    assert restored.entries()[0]["unknown"]["reason"] == "timeout_after_send"

    # replay re-raises the unknown at the same position WITHOUT calling the effect fn
    def must_not_run(payload):
        raise AssertionError("replay must never call the effect fn")

    replay = EffectRunner(mode="replay", journal=restored)
    with pytest.raises(ReplayedEffectUnknown) as exc:
        replay.run("api.post", must_not_run, {"to": "x"})
    assert exc.value.reason == "timeout_after_send"


def test_a_plain_exception_is_still_recorded_as_error_not_unknown():
    # Negative control: only EffectOutcomeUnknown takes the unknown lane; ordinary failures
    # remain errors, so the new branch does not swallow real errors.
    def boom():
        raise ValueError("nope")

    runner = EffectRunner(mode="record")
    with pytest.raises(ValueError):
        runner.run("x.op", boom)
    entry = runner.journal.entries()[0]
    assert "error" in entry and "unknown" not in entry


def test_journal_rejects_a_malformed_unknown_entry():
    j = EffectJournal()
    with pytest.raises(ValueError):
        j.record({"effect_id": "e", "args_hash": "h", "unknown": {"reason": "", "detail": "d"}})
    with pytest.raises(ValueError):
        j.record({"effect_id": "e", "args_hash": "h", "unknown": {"reason": "r"}})
