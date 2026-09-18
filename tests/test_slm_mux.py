"""SLM-MUX confidence-selection core (pure, no live model)."""
from __future__ import annotations

from core.slm_mux import default_answer_key, slm_mux_select


def _sampler(samples_by_model):
    state = {m: list(s) for m, s in samples_by_model.items()}

    def sampler(model, _prompt):
        lst = state.get(model, [])
        return lst.pop(0) if lst else ""

    return sampler


def test_most_self_consistent_model_is_selected() -> None:
    # reasoning model lands on 18 every time; general model scatters -> reasoning wins.
    sampler = _sampler({
        "deepseek-r1:14b": ["The answer is 18.", "So it's 18.", "= 18"],
        "qwen2.5:7b": ["12", "18", "24"],
    })
    res = slm_mux_select("2+2*8?", ["deepseek-r1:14b", "qwen2.5:7b"], sampler, k=3)
    assert res.model == "deepseek-r1:14b"
    assert res.confidence == 1.0
    assert "18" in res.answer
    # the losing model's confidence is recorded for transparency
    conf = {c.model: c.confidence for c in res.candidates}
    assert conf["qwen2.5:7b"] < 0.5


def test_numeric_answers_cluster_across_phrasing() -> None:
    sampler = _sampler({"m": ["The total is 1,234.", "1234", "So, 1234."]})
    res = slm_mux_select("q", ["m"], sampler, k=3)
    assert res.confidence == 1.0  # all three normalize to the same number despite phrasing


def test_ties_break_by_preference_order() -> None:
    # both fully self-consistent -> the first-listed (preferred) model wins.
    sampler = _sampler({"pref": ["yes", "yes"], "other": ["no", "no"]})
    res = slm_mux_select("q", ["pref", "other"], sampler, k=2)
    assert res.model == "pref"


def test_a_confident_minority_model_beats_a_scattered_default() -> None:
    # This is the whole point: the smaller/second model, when confident, can override.
    sampler = _sampler({
        "general": ["A", "B", "C", "D"],       # 25% self-consistency (scattered)
        "reasoning": ["42", "42", "42", "10"],  # 75% self-consistency
    })
    res = slm_mux_select("q", ["general", "reasoning"], sampler, k=4)
    assert res.model == "reasoning" and res.confidence == 0.75


def test_default_answer_key() -> None:
    assert default_answer_key("The result is 1,234.5") == "1234.5"
    assert default_answer_key("Paris") == "paris"
    assert default_answer_key("Final answer:\nParis, France") == "paris france"
    assert default_answer_key("   ") == ""


def test_degenerate_inputs_do_not_crash() -> None:
    assert slm_mux_select("q", [], lambda m, p: "x", k=3).model == ""
    empty = slm_mux_select("q", ["m"], lambda m, p: "", k=3)
    assert empty.confidence == 0.0
