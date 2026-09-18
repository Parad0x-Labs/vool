"""Adversarial pins for Law 4 (`core/kernel/effects.py`): everything replays, divergence is named.

Every test here is built around a misuse or a divergence, not a greeting (project rule 0.4):
changed args, exhausted tapes, reordered control flow, effects that raise mid-record,
unserializable arguments, hand-mangled JSON, and aliasing attacks on the tape itself. The
stub `_never_call` raises AssertionError if replay ever touches a real function — the one
behaviour that, if it regressed, would turn "replay" back into a live run.
"""
from __future__ import annotations

import json
import math

import pytest

from core.kernel.effects import DivergenceError, EffectJournal, EffectRunner


def _never_call(*args: object, **kwargs: object) -> object:
    raise AssertionError("replay invoked the effect function; replay must serve from the tape only")


class _CountingFn:
    """Callable that counts invocations, so 'fn was not called' is asserted, not assumed."""

    def __init__(self, result: object = "ok") -> None:
        self.calls = 0
        self.result = result

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return self.result


# ---------------------------------------------------------------- record mode


def test_record_executes_fn_and_appends_a_complete_entry() -> None:
    fn = _CountingFn(result={"rate": 1.17})
    runner = EffectRunner(mode="record")
    result = runner.run("fx.lookup", fn, "EUR", base="USD")
    assert result == {"rate": 1.17}
    assert fn.calls == 1
    entries = runner.journal.entries()
    assert len(entries) == 1
    assert entries[0]["effect_id"] == "fx.lookup"
    assert entries[0]["result"] == {"rate": 1.17}
    assert isinstance(entries[0]["args_hash"], str) and entries[0]["args_hash"]


def test_record_mode_fn_exception_propagates_and_is_recorded_as_an_outcome() -> None:
    """DESIGN EVOLUTION (2026-08-19): failures are outcomes, recorded and replayable.

    The first design appended nothing on failure ("a failed effect must not poison the
    tape") and this test pinned it. Live use falsified it the same day: a REPL turn whose
    arithmetic tool refused mid-run could not be replayed — replay met the NEXT effect on
    the tape and reported effect_id_mismatch, a divergence pointing at control flow when
    the truth was "this effect failed here". A tape that cannot reproduce failure can only
    replay the turns that never needed debugging, which is backwards.
    """
    from core.kernel.effects import ReplayedEffectFailure

    runner = EffectRunner(mode="record")

    def boom() -> object:
        raise RuntimeError("provider 503")

    # The original exception still propagates unchanged to the caller...
    with pytest.raises(RuntimeError, match="provider 503"):
        runner.run("fx.lookup", boom)
    # ...and the failure is ON the tape as an error outcome.
    assert len(runner.journal) == 1
    entry = runner.journal.entries()[0]
    assert entry["error"] == {"type": "RuntimeError", "message": "provider 503"}
    assert "result" not in entry

    # Recording continues cleanly after the failure.
    runner.run("fx.lookup", _CountingFn(result=2), "EUR")

    # Replay reproduces the failure AT ITS POSITION (never calling the fn), then the
    # following success — the whole run, failures included, surviving a JSON round-trip.
    replay = EffectRunner(mode="replay", journal=EffectJournal.from_json(runner.journal.to_json()))
    with pytest.raises(ReplayedEffectFailure) as exc:
        replay.run("fx.lookup", _never_call)
    assert exc.value.error_type == "RuntimeError"
    assert exc.value.error_message == "provider 503"
    assert replay.run("fx.lookup", _never_call, "EUR") == 2


def test_record_refuses_non_serializable_positional_arg_naming_it_and_never_runs_fn() -> None:
    fn = _CountingFn()
    runner = EffectRunner(mode="record")
    with pytest.raises(ValueError, match=r"positional argument 1") as excinfo:
        runner.run("fs.write", fn, "path.txt", object())
    assert "fs.write" in str(excinfo.value)
    assert fn.calls == 0, "an effect whose args cannot be recorded must not execute"
    assert len(runner.journal) == 0


def test_record_refuses_non_serializable_kwarg_naming_it() -> None:
    runner = EffectRunner(mode="record")
    with pytest.raises(ValueError, match=r"keyword argument 'handle'"):
        runner.run("fs.write", _CountingFn(), text="x", handle=object())
    assert len(runner.journal) == 0


def test_record_refuses_nan_args_because_the_tape_must_stay_parseable_json() -> None:
    runner = EffectRunner(mode="record")
    with pytest.raises(ValueError, match=r"positional argument 0"):
        runner.run("math.effect", _CountingFn(), math.nan)
    assert len(runner.journal) == 0


def test_record_refuses_non_serializable_result_and_appends_nothing() -> None:
    runner = EffectRunner(mode="record")
    with pytest.raises(ValueError, match="result"):
        runner.run("db.open", _CountingFn(result=object()))
    assert len(runner.journal) == 0


def test_effect_id_must_be_a_non_empty_string() -> None:
    runner = EffectRunner(mode="record")
    with pytest.raises(ValueError):
        runner.run("", _CountingFn())
    with pytest.raises(ValueError):
        runner.run(None, _CountingFn())  # type: ignore[arg-type]
    assert len(runner.journal) == 0


# ---------------------------------------------------------------- replay divergence


def _one_entry_journal() -> EffectJournal:
    runner = EffectRunner(mode="record")
    runner.run("fx.lookup", _CountingFn(result=42), "EUR", base="USD")
    return runner.journal


def test_replay_serves_the_recorded_result_without_calling_fn() -> None:
    replay = EffectRunner(mode="replay", journal=_one_entry_journal())
    # _never_call raises AssertionError if touched — a passing test proves fn never ran.
    assert replay.run("fx.lookup", _never_call, "EUR", base="USD") == 42


def test_replay_with_one_changed_positional_arg_is_args_hash_mismatch() -> None:
    replay = EffectRunner(mode="replay", journal=_one_entry_journal())
    with pytest.raises(DivergenceError) as excinfo:
        replay.run("fx.lookup", _never_call, "GBP", base="USD")
    assert excinfo.value.reason == "args_hash_mismatch"
    assert excinfo.value.effect_id == "fx.lookup"
    assert "fx.lookup" in str(excinfo.value)


def test_replay_with_one_changed_kwarg_value_is_args_hash_mismatch() -> None:
    replay = EffectRunner(mode="replay", journal=_one_entry_journal())
    with pytest.raises(DivergenceError) as excinfo:
        replay.run("fx.lookup", _never_call, "EUR", base="JPY")
    assert excinfo.value.reason == "args_hash_mismatch"


def test_replay_distinguishes_1_from_string_1_type_confusion() -> None:
    runner = EffectRunner(mode="record")
    runner.run("page.fetch", _CountingFn(), 1)
    replay = EffectRunner(mode="replay", journal=runner.journal)
    with pytest.raises(DivergenceError) as excinfo:
        replay.run("page.fetch", _never_call, "1")
    assert excinfo.value.reason == "args_hash_mismatch"


def test_replay_past_the_end_is_journal_exhausted() -> None:
    replay = EffectRunner(mode="replay", journal=_one_entry_journal())
    replay.run("fx.lookup", _never_call, "EUR", base="USD")
    with pytest.raises(DivergenceError) as excinfo:
        replay.run("fx.lookup", _never_call, "EUR", base="USD")
    assert excinfo.value.reason == "journal_exhausted"
    assert excinfo.value.effect_id == "fx.lookup"


def test_replay_against_an_empty_journal_is_journal_exhausted() -> None:
    replay = EffectRunner(mode="replay", journal=EffectJournal())
    with pytest.raises(DivergenceError) as excinfo:
        replay.run("kernel.clock", _never_call)
    assert excinfo.value.reason == "journal_exhausted"


def test_replay_out_of_order_is_effect_id_mismatch_because_the_tape_is_not_a_lookup_table() -> None:
    runner = EffectRunner(mode="record")
    runner.run("step.one", _CountingFn(result=1))
    runner.run("step.two", _CountingFn(result=2))
    replay = EffectRunner(mode="replay", journal=runner.journal)
    # step.two IS on the tape — a lookup table would happily serve it. Strict order refuses.
    with pytest.raises(DivergenceError) as excinfo:
        replay.run("step.two", _never_call)
    assert excinfo.value.reason == "effect_id_mismatch"
    assert excinfo.value.effect_id == "step.two"


def test_wrong_effect_id_and_wrong_args_reports_effect_id_mismatch_first() -> None:
    # Priority pin: comparing arg hashes across two DIFFERENT effects would misdirect the
    # investigation toward arguments when the divergence is control flow.
    replay = EffectRunner(mode="replay", journal=_one_entry_journal())
    with pytest.raises(DivergenceError) as excinfo:
        replay.run("other.effect", _never_call, "completely", different="args")
    assert excinfo.value.reason == "effect_id_mismatch"


def test_replay_never_invokes_fn_even_while_diverging() -> None:
    fn = _CountingFn()
    replay = EffectRunner(mode="replay", journal=_one_entry_journal())
    with pytest.raises(DivergenceError):
        replay.run("fx.lookup", fn, "changed-arg")
    with pytest.raises(DivergenceError):
        replay.run("wrong.id", fn)
    assert fn.calls == 0


def test_divergence_error_is_a_runtime_error_with_a_closed_reason_set() -> None:
    assert issubclass(DivergenceError, RuntimeError)
    err = DivergenceError("x", "journal_exhausted")
    assert err.effect_id == "x"
    assert err.reason == "journal_exhausted"
    with pytest.raises(ValueError):
        DivergenceError("x", "made_up_reason")


# ---------------------------------------------------------------- canonicalization


def test_kwargs_call_site_order_does_not_change_the_effect_identity() -> None:
    runner = EffectRunner(mode="record")
    runner.run("fx.lookup", _CountingFn(result=7), a=1, b=2)
    replay = EffectRunner(mode="replay", journal=runner.journal)
    assert replay.run("fx.lookup", _never_call, b=2, a=1) == 7


def test_nested_dict_key_order_does_not_change_the_effect_identity() -> None:
    runner = EffectRunner(mode="record")
    runner.run("api.post", _CountingFn(result="sent"), {"b": 2, "a": [1, {"y": 0, "x": 9}]})
    replay = EffectRunner(mode="replay", journal=runner.journal)
    assert replay.run("api.post", _never_call, {"a": [1, {"x": 9, "y": 0}], "b": 2}) == "sent"


def test_unicode_args_survive_the_round_trip_and_replay() -> None:
    runner = EffectRunner(mode="record")
    runner.run("search.web", _CountingFn(result=["žąsis"]), "žąsis ☃", lang="lt")
    journal = EffectJournal.from_json(runner.journal.to_json())
    replay = EffectRunner(mode="replay", journal=journal)
    assert replay.run("search.web", _never_call, "žąsis ☃", lang="lt") == ["žąsis"]


# ---------------------------------------------------------------- clock and rand


def test_clock_and_rand_replay_identically_after_a_json_round_trip() -> None:
    record = EffectRunner(mode="record")
    t1 = record.clock()
    v1 = record.rand()
    t2 = record.clock()
    journal = EffectJournal.from_json(record.journal.to_json())
    replay = EffectRunner(mode="replay", journal=journal)
    assert replay.clock() == t1
    assert replay.rand() == v1
    assert replay.clock() == t2


def test_replaying_rand_before_clock_is_effect_id_mismatch() -> None:
    record = EffectRunner(mode="record")
    record.clock()
    record.rand()
    replay = EffectRunner(mode="replay", journal=record.journal)
    with pytest.raises(DivergenceError) as excinfo:
        replay.rand()
    assert excinfo.value.reason == "effect_id_mismatch"
    assert excinfo.value.effect_id == "kernel.rand"


# ---------------------------------------------------------------- journal integrity


def test_multi_effect_json_round_trip_then_full_replay() -> None:
    record = EffectRunner(mode="record")
    record.run("fx.lookup", _CountingFn(result={"EUR": 1.17}), "EUR")
    record.clock()
    record.run("db.query", _CountingFn(result=[[1, "row"], [2, None]]), "SELECT 1", limit=2)
    record.rand()
    text = record.journal.to_json()
    replay = EffectRunner(mode="replay", journal=EffectJournal.from_json(text))
    assert replay.run("fx.lookup", _never_call, "EUR") == {"EUR": 1.17}
    replay.clock()
    assert replay.run("db.query", _never_call, "SELECT 1", limit=2) == [[1, "row"], [2, None]]
    replay.rand()
    # And the round trip is lossless: serializing the reloaded journal reproduces the tape.
    assert json.loads(EffectJournal.from_json(text).to_json()) == json.loads(text)


def test_entries_returns_a_tuple_of_copies_so_the_tape_cannot_be_rewritten_by_aliasing() -> None:
    journal = _one_entry_journal()
    view = journal.entries()
    assert isinstance(view, tuple)
    view[0]["result"] = "forged"
    view[0]["args_hash"] = "forged"
    replay = EffectRunner(mode="replay", journal=journal)
    assert replay.run("fx.lookup", _never_call, "EUR", base="USD") == 42


def test_replayed_mutable_result_is_a_fresh_copy_not_an_alias_into_the_tape() -> None:
    runner = EffectRunner(mode="record")
    runner.run("db.query", _CountingFn(result={"rows": [1, 2]}), "SELECT")
    journal = runner.journal
    first = EffectRunner(mode="replay", journal=journal).run("db.query", _never_call, "SELECT")
    first["rows"].append(999)  # type: ignore[index]
    second = EffectRunner(mode="replay", journal=journal).run("db.query", _never_call, "SELECT")
    assert second == {"rows": [1, 2]}


def test_record_mode_caller_mutating_args_or_result_after_the_call_cannot_rewrite_the_tape() -> None:
    payload = {"query": ["a"]}
    result_obj = {"rows": [1]}
    runner = EffectRunner(mode="record")
    runner.run("db.query", _CountingFn(result=result_obj), payload)
    payload["query"].append("b")
    result_obj["rows"].append(2)
    entry = runner.journal.entries()[0]
    assert entry["result"] == {"rows": [1]}
    replay = EffectRunner(mode="replay", journal=runner.journal)
    assert replay.run("db.query", _never_call, {"query": ["a"]}) == {"rows": [1]}


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        '{"effect_id": "x"}',  # top level must be a list
        '[{"effect_id": "x", "args_hash": "h"}]',  # missing result
        '[{"effect_id": "x", "args_hash": "h", "result": 1, "extra": 2}]',  # unexpected key
        '[{"effect_id": "", "args_hash": "h", "result": 1}]',  # empty effect id
        '[{"effect_id": "x", "args_hash": 7, "result": 1}]',  # non-string hash
        '[["x", "h", 1]]',  # entry is not a mapping
    ],
)
def test_from_json_rejects_malformed_or_hand_mangled_tapes(text: str) -> None:
    with pytest.raises(ValueError):
        EffectJournal.from_json(text)


def test_record_entry_direct_misuse_is_rejected_before_anything_is_appended() -> None:
    journal = EffectJournal()
    with pytest.raises(ValueError):
        journal.record({"effect_id": "x"})
    with pytest.raises(ValueError):
        journal.record({"effect_id": "x", "args_hash": "h", "result": object()})
    assert len(journal) == 0


# ---------------------------------------------------------------- constructor misuse


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        EffectRunner(mode="reply")  # type: ignore[arg-type]


def test_replay_mode_without_a_journal_is_rejected() -> None:
    with pytest.raises(ValueError):
        EffectRunner(mode="replay")
