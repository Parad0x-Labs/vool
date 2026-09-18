"""Differential V3: the per-axis, strict whole-turn instrument over the frozen gold corpus.

Pins that the harness is well-formed, that gold-as-candidate scores strict 20/20, that the invented
counterparts score strict 0/20, that the lexical column is a measurement rather than a pass, and
that the eval-only transport builds the request correctly with a fake urlopen (no network, no key).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

from core.semantic.graph_diff import AXES
from ops import semantic_requestgraph_gold as gold_corpus

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "semantic_differential_v3.py"


def _load():
    spec = importlib.util.spec_from_file_location("semantic_differential_v3", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class _GoldProducer:
    name = "gold"

    def produce(self, text, *, turn_id):
        case = next(c for c in gold_corpus.gold_cases() if c.text == text)
        return gold_corpus.gold_graph(case.id)


class _InventedProducer:
    name = "invented"

    def produce(self, text, *, turn_id):
        case = next(c for c in gold_corpus.gold_cases() if c.text == text)
        return gold_corpus.invented_counterpart(case.id)


def _cases():
    return [{"id": c.id, "cls": c.cls, "text": c.text, "gold": gold_corpus.gold_graph(c.id)} for c in gold_corpus.gold_cases()]


def test_gold_as_candidate_scores_strict_twenty_of_twenty() -> None:
    h = _load()
    rows = h.score_producer(_GoldProducer(), cases=_cases())
    summary = h.summarize(rows)
    assert summary["strict_correct"] == 20 and summary["total"] == 20
    assert all(summary["axis_coverage"][axis] == 20 for axis in AXES)


def test_invented_counterparts_score_strict_zero_of_twenty() -> None:
    h = _load()
    rows = h.score_producer(_InventedProducer(), cases=_cases())
    summary = h.summarize(rows)
    assert summary["strict_correct"] == 0
    assert summary["axis_coverage"]["source_binding"] == 0


def test_naive_and_lexical_columns_are_measurements_not_passes() -> None:
    h = _load()
    report = h.run_differential()
    assert report["schema"] == "semantic_differential_v3" and report["model_present"] is False
    assert set(report["columns"]) == {"naive", "lexical"}
    for name in ("naive", "lexical"):
        summary = report["columns"][name]["summary"]
        assert summary["total"] == 20 and 0 <= summary["strict_correct"] < 20
        assert set(summary["axis_coverage"]) == set(AXES)
    lexical = {r["id"]: r for r in report["columns"]["lexical"]["rows"]}
    assert "slot_coverage" in lexical["op2"]["failed"]


def test_table_and_json_are_well_formed() -> None:
    h = _load()
    report = h.run_differential()
    table = h.render_table(report)
    assert "MODEL PENDING (no keyed run)" in table
    assert "STRICT whole-turn" in table and "per-axis coverage" in table
    for cid in report["case_ids"]:
        assert cid in table
    payload = json.loads(json.dumps(report))
    assert payload["gold_digest"] == gold_corpus.GOLD_DIGEST


def test_refuses_a_drifted_gold_corpus(monkeypatch) -> None:
    import pytest

    h = _load()
    monkeypatch.setattr(gold_corpus, "GOLD_DIGEST", "0000000000000000")
    with pytest.raises(SystemExit, match="drifted"):
        h.run_differential()


def test_eval_transport_builds_the_request_and_never_prints_the_key() -> None:
    h = _load()
    seen = {}

    class _Response:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return _Response(json.dumps({"choices": [{"message": {"content": '{"requests": []}'}}]}).encode())

    transport = h.openrouter_eval_transport("m:free", urlopen=fake_urlopen, api_key="k")
    reply = transport("SYSTEM", "USER", json_schema={"type": "object"})
    assert reply == '{"requests": []}'
    assert seen["url"].startswith("https://openrouter.ai/") and seen["auth"] == "Bearer k"
    assert seen["body"]["messages"][0]["content"] == "SYSTEM" and seen["body"]["response_format"] == {"type": "json_object"}
    assert "Bearer k" not in h.render_table(h.run_differential())
