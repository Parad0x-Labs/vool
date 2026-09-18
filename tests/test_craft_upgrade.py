from __future__ import annotations

import pytest

from core.craft_upgrade import (
    parse_param_size,
    reauthor,
    select_best_model,
    upgrade_craft,
)

_GOOD = (
    "A sharper, more concrete directive with specific devices and camera moves an AI model can "
    "actually follow, no filler and no buzzword stacking, one dense paragraph of real craft."
)


def good_client(_prompt: str) -> str:
    return _GOOD


@pytest.mark.parametrize(
    "details, expected",
    [
        ({"parameter_size": "7B"}, 7.0),
        ({"parameter_size": "70B"}, 70.0),
        ({"parameter_size": "1.5B"}, 1.5),
        ({"parameter_size": "500M"}, 0.5),
        ({}, 0.0),
        (None, 0.0),
    ],
)
def test_parse_param_size(details, expected) -> None:
    assert parse_param_size(details) == expected


def test_select_best_model_prefers_params_then_size() -> None:
    models = [
        {"name": "qwen2.5:7b", "size": 5_000_000_000, "details": {"parameter_size": "7B"}},
        {"name": "qwen2.5:32b", "size": 20_000_000_000, "details": {"parameter_size": "32B"}},
        {"name": "nomic-embed-text", "size": 300_000_000, "details": {"parameter_size": "137M"}},
    ]
    assert select_best_model(models) == "qwen2.5:32b"
    # ties on params fall back to file size
    assert select_best_model(
        [{"name": "a", "size": 1e9, "details": {}}, {"name": "b", "size": 2e9, "details": {}}]
    ) == "b"
    assert select_best_model([]) is None
    assert select_best_model([{"name": "all-minilm-embed", "size": 1e8, "details": {}}]) is None


def test_reauthor_accepts_good_rejects_bad() -> None:
    assert reauthor("prose", "Horror", "old", good_client) == _GOOD
    assert reauthor("prose", "Horror", "old", lambda _p: "ok") is None            # too short
    assert reauthor("prose", "Horror", "old", lambda _p: "I'm sorry, I can't") is None  # refusal
    assert reauthor("prose", "Horror", "old", lambda _p: (_ for _ in ()).throw(RuntimeError())) is None


def test_reauthor_strips_fences_and_collapses() -> None:
    out = reauthor("prose", "Horror", "old", lambda _p: "```\n" + _GOOD + "\n```")
    assert out == _GOOD and "```" not in out


def test_upgrade_craft_builds_and_persists_overlay() -> None:
    captured: dict = {}
    summary = upgrade_craft(
        model_client=good_client,
        sections=["writing_craft"],
        genres=["horror", "fantasy"],
        writer=lambda ov: captured.update({"overlay": ov}),
    )
    assert summary["updated"] == ["writing_craft:horror", "writing_craft:fantasy"]
    assert captured["overlay"]["writing_craft"]["horror"]["craft_directive"] == _GOOD
    assert captured["overlay"]["writing_craft"]["fantasy"]["craft_directive"] == _GOOD


def test_upgrade_craft_skips_failed_reauthor() -> None:
    captured: dict = {}
    summary = upgrade_craft(
        model_client=lambda _p: "no",  # too short -> every genre skipped
        sections=["writing_craft"],
        genres=["horror"],
        writer=lambda ov: captured.update({"overlay": ov}),
    )
    assert summary["updated"] == []
    assert summary["skipped"] == ["writing_craft:horror"]
    assert captured["overlay"] == {}
