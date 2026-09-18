"""The acceptance differential: frozen corpus, penalty-taxonomy scoring, well-formed table.

Loaded from scripts/ by path (scripts is not a package). Runs with the naive control — no model,
no network — and unit-tests the scorer directly, since the scorer is the acceptance logic that
gates authority promotion. It must be impossible to "win" by guessing more intents.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parents[2] / "scripts" / "semantic_resolver_differential.py"


def _load():
    spec = importlib.util.spec_from_file_location("semantic_resolver_differential", _HARNESS)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register before exec: @dataclass resolves cls.__module__ via sys.modules during exec.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def h():
    return _load()


# -- the freeze guard (do not tune the corpus after seeing model results) -----


def test_corpus_is_frozen(h) -> None:
    assert h._corpus_digest() == h._CORPUS_FROZEN_SHA, (
        "the frozen corpus/ground-truth changed — this is only allowed as a deliberate diff that "
        "also updates _CORPUS_FROZEN_SHA, never a quiet tune to move the model score"
    )


# -- the run + table ----------------------------------------------------------


def test_naive_run_scores_both_baselines_and_leaves_model_pending(h) -> None:
    report = h.run_differential("naive")
    assert report["schema"] == "semantic_resolver_differential_v2"
    assert report["model_present"] is False
    s = report["summary"]
    assert s["total"] == len(h.CORPUS)
    assert 0 <= s["naive_correct"] <= s["total"]
    assert 0 <= s["heuristic_correct"] <= s["total"]
    assert s["model_correct"] is None            # no keyed run → no model score
    for row in report["rows"]:
        assert row["model"] is None
        assert {"intents", "correct", "failure", "confidence"} <= set(row["naive"])
        assert {"intents", "tool", "prohibition", "correct", "failure"} <= set(row["heuristic"])


def test_table_renders_pending_model_column(h) -> None:
    table = h.render_table(h.run_differential("naive"))
    assert "MODEL PENDING (no keyed run)" in table
    assert "PENDING" in table  # the per-row model cells
    assert "do not tune" in table.lower()


# -- the scorer IS the acceptance logic: pin the penalty taxonomy -------------


def _gt(**kw):
    base = {"intents": 1, "tool": True, "prohibition": False, "ambiguous": False}
    base.update(kw)
    return base


def test_scorer_penalizes_each_failure_type(h) -> None:
    R = h.Reading
    # invented intent: more than asked.
    assert h.score(R(2, True, None, None, "committed"), _gt(intents=1)) == (False, "invented_intent")
    # missing slot: fewer than asked.
    assert h.score(R(1, True, None, None, "committed"), _gt(intents=2)) == (False, "missing_slot")
    # false tool obligation: demanded a tool where none warranted.
    assert h.score(R(1, True, None, None, "committed"), _gt(intents=1, tool=False)) == (
        False, "false_tool_obligation")
    # missing prohibition: a prohibition not represented.
    assert h.score(R(0, False, False, None, "abstain"), _gt(intents=0, tool=False, prohibition=True)) == (
        False, "missing_prohibition")
    # collapsed ambiguity: committed where ambiguous.
    assert h.score(R(1, True, None, False, "committed"), _gt(intents=1, ambiguous=True)) == (
        False, "collapsed_ambiguity")


def test_scorer_rewards_preserved_unknown_over_confident_wrong(h) -> None:
    R = h.Reading
    gt = _gt(intents=1, ambiguous=True)
    preserved = R(1, True, True, True, "abstain")     # abstained → ambiguity preserved
    collapsed = R(1, True, None, False, "committed")  # same count, but collapsed the ambiguity
    assert h.score(preserved, gt) == (True, "correct")
    assert h.score(collapsed, gt)[0] is False
    # The key property: identical intent count, yet preserving UNKNOWN scores better than collapsing.


def test_scorer_marks_a_missing_model_pending_not_correct(h) -> None:
    assert h.score(None, _gt()) == (False, "pending")


def test_scorer_prohibition_takes_priority_over_intent_count(h) -> None:
    R = h.Reading
    # A prohibition turn (expected 0 intents) where the reading both invented an intent AND missed
    # the prohibition: the more consequential failure (missing prohibition) is reported.
    gt = _gt(intents=0, tool=False, prohibition=True)
    assert h.score(R(2, False, False, None, "committed"), gt) == (False, "missing_prohibition")


# -- the eval backend: request build + reply parse (no network, no key) -------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openrouter_eval_backend_builds_request_and_parses_reply(h) -> None:
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(
            json.dumps({"choices": [{"message": {"content": '[{"request":"x","operation":"unknown"}]'}}]}).encode()
        )

    backend = h.openrouter_eval_backend("some/free-model:free", urlopen=fake_urlopen, api_key="test-key")
    reply = backend("SYS", "USER")
    assert reply == '[{"request":"x","operation":"unknown"}]'
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == "some/free-model:free"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]


def test_openrouter_backend_end_to_end_with_the_resolver(h) -> None:
    """The eval backend + ModelSemanticResolver produce typed proposals from a canned model reply."""
    from core.semantic.canonical_text import CanonicalText
    from core.semantic.resolver import ModelSemanticResolver

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(
            json.dumps(
                {"choices": [{"message": {"content":
                    '[{"request":"the price of gold","operation":"market.quote",'
                    '"entities":[{"text":"gold","kind":"asset"}]}]'}}]}
            ).encode()
        )

    backend = h.openrouter_eval_backend("m:free", urlopen=fake_urlopen, api_key="k")
    resolver = ModelSemanticResolver(backend)
    (proposal,) = resolver.propose(CanonicalText.of("what is the price of gold"), operations=("market.quote",))
    assert proposal.operation == "market.quote"
    assert proposal.spans[0].resolve(CanonicalText.of("what is the price of gold")) == "gold"
