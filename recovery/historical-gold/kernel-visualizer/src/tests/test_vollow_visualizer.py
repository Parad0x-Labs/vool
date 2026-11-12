"""Self-checks for the VOOL trace visualizer reader + the gated effect hook.

These prove (a) the observer parses a real journal row into typed Turns and
(2) the kernel-side side-channel is behavior-neutral when disabled and
write-only when enabled.
"""
import os

from core.kernel.effects import EffectRunner, EffectJournal

from vool_visualizer.observer import load_journal, _row_to_turn

DEMO = ("review-bus/"
        "dm-delta-review-20260821/round0_raw_evidence/hardmix_repl_sessions.jsonl")


def test_observer_parses_demo_journal_rows():
    turns = load_journal(DEMO)
    assert len(turns) >= 1
    t4 = next((t for t in turns if t.turn_index == 4), None)
    assert t4 is not None
    # turn 4 must show the weather egress effects, ordered
    ids = [e.effect_id for e in t4.effects]
    assert ids[0] == "model.extract"
    assert any("web.search." in i for i in ids)
    assert any(i == "kernel.arith" for i in ids)
    assert t4.answer_present and t4.answer


def test_hook_disabled_is_strict_no_op(tmp_path, monkeypatch):
    monkeypatch.delenv("VOOL_VIS_EVENTS", raising=False)
    runner = EffectRunner(mode="record")
    out = runner.run("probe.disabled", lambda: 11)
    assert out == 11
    assert len(runner.journal.entries()) == 1


def test_hook_enabled_writes_side_channel_but_not_journal(tmp_path, monkeypatch):
    ev = tmp_path / "vis.jsonl"
    monkeypatch.setenv("VOOL_VIS_EVENTS", str(ev))
    runner = EffectRunner(mode="record")
    before = runner.journal.to_json()
    runner.run("kernel.arith", lambda: 7)
    runner.run("web.search.brave", lambda: [{"title": "t"}])
    # canonical journal gained ONLY its own entries (identical scheme)
    assert "kernel.arith" in runner.journal.to_json()
    lines = ev.read_text().splitlines()
    assert len(lines) == 2
    assert all('"' in l for l in lines)
    # journal identity: enabling the hook must not inject event rows
    assert 'vis_events' not in runner.journal.to_json()
