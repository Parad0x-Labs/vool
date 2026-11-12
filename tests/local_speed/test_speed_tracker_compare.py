"""The speed tracker's regression gate: unit-level, no daemon, no model.

The seeded-regression PROOF (a real run going red on an inflated system prompt) is a live
exercise; these tests pin the comparator and the scorer's arithmetic so the gate itself cannot
silently pass.
"""
from __future__ import annotations

import json
from pathlib import Path

from ops.speed_tracker import compare, score_run


def _baseline() -> dict:
    return {"turns": {
        "quick": {"n": 2, "prompt_bytes_median": 4000, "serial_calls_median": 1,
                  "uncached_bytes_median": 2000, "vool_s_median": 3.0, "reloads": 0},
        "tool": {"n": 2, "prompt_bytes_median": 8000, "serial_calls_median": 2,
                 "uncached_bytes_median": 5000, "vool_s_median": 6.0, "reloads": 0},
    }}


def test_a_tenth_heavier_prompt_is_red() -> None:
    current = _baseline()
    current["turns"]["quick"]["prompt_bytes_median"] = 4401
    lines = compare(_baseline(), current)
    assert any("quick.prompt_bytes_median" in line for line in lines), lines


def test_within_tolerance_is_green() -> None:
    current = _baseline()
    current["turns"]["quick"]["prompt_bytes_median"] = 4400  # exactly +10%: not a regression
    current["turns"]["tool"]["vool_s_median"] = 6.59
    assert compare(_baseline(), current) == []


def test_more_reloads_is_red_even_at_equal_bytes() -> None:
    current = _baseline()
    current["turns"]["tool"]["reloads"] = 1
    lines = compare(_baseline(), current)
    assert any("tool.reloads" in line for line in lines), lines


def test_a_vanished_or_new_turn_type_is_reported() -> None:
    current = _baseline()
    current["turns"]["heavy"] = current["turns"].pop("tool")
    lines = compare(_baseline(), current)
    assert any("vanished" in line for line in lines), lines
    assert any("new turn type" in line for line in lines), lines


def _write_run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    base = 1000.0
    engine_rows = [
        # turn 1: one warm call on the same runner as the previous call, sharing a prefix
        {"t": base + 1, "method": "POST", "path": "/api/chat", "body": {
            "model": "qwen3:8b", "messages": [
                {"role": "system", "content": "You are the tracker persona."},
                {"role": "user", "content": "Which planet has the most moons?"},
            ]},
         "residency": "warm", "runner_key": ["qwen3:8b", 8192, None, 4]},
        # turn 1: a reload on the same model with a different runner key
        {"t": base + 2, "method": "POST", "path": "/api/chat", "body": {
            "model": "qwen3:8b", "messages": [{"role": "user", "content": "aux"}]},
         "residency": "reload", "runner_key": ["qwen3:8b", 4096, None, 4]},
        # a prewarm-shaped call: excluded from work
        {"t": base + 3, "method": "POST", "path": "/api/chat", "body": {
            "model": "qwen3:8b", "messages": [{"role": "user", "content": " "}],
            "options": {"num_predict": 1}},
         "residency": "warm", "runner_key": ["qwen3:8b", 8192, None, 4]},
    ]
    with open(run / "engine.jsonl", "w", encoding="utf-8") as fh:
        for row in engine_rows:
            fh.write(json.dumps(row) + "\n")
    turns = [
        {"kind": "turn", "type": "quick", "t_sent": base, "t_first_text": base + 1.5, "t_done": base + 1.6},
        {"kind": "run_end", "t": base + 9},
    ]
    with open(run / "turns.jsonl", "w", encoding="utf-8") as fh:
        for row in turns:
            fh.write(json.dumps(row) + "\n")
    return run


def test_the_scorer_counts_calls_bytes_and_reloads_from_raw_logs(tmp_path: Path) -> None:
    result = score_run(_write_run(tmp_path))
    quick = result["turns"]["quick"]
    assert quick["n"] == 1
    # Two work calls (the prewarm is excluded), one of them serial before the answer text.
    assert quick["serial_calls_median"] == 1
    assert quick["prompt_bytes_median"] > 0
    assert quick["reloads"] == 1
    assert result["reloads_total"] == 1
