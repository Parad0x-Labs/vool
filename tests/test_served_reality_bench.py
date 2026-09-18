"""Pytest wrapper: the served-reality bench's offline self-tests.

Runs the bench's own instrument checks (schema fidelity, wire-byte retention,
stub determinism, failure classification, skip semantics, mutation anchors,
CAS verifier, persistence) WITHOUT launching a daemon — the corpus and the
mutation controls are driven by the runner (`python -m
ops.served_reality.runner`), not by pytest, because they boot the real staged
product.

Run:
    .venv/bin/python -m pytest tests/test_served_reality_bench.py -q
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="module")
def selftest():
    return importlib.import_module("ops.served_reality.selftest")


_selftest_module = importlib.import_module("ops.served_reality.selftest")
_ALL_CHECKS = _selftest_module._checks


@pytest.mark.parametrize("name,fn", [(n, f) for n, f in _ALL_CHECKS], ids=[n for n, _ in _ALL_CHECKS])
def test_selftest_check(name, fn, selftest):
    fn()


def test_corpus_has_the_permanent_23(selftest):
    from ops.served_reality.corpus import CORPUS

    assert len(CORPUS) == 23, "the permanent corpus is 23 cases"
    assert all(case.required for case in CORPUS), "every permanent case is required"
    ids = [case.case_id for case in CORPUS]
    assert len(set(ids)) == 23
    for expected in (
        "ordinary-chat",
        "strict-literal-output",
        "mixed-demands",
        "exact-identifiers-urls",
        "local-cloud-local",
        "simultaneous-chat-isolation",
        "no-web-rule",
        "live-search",
        "unavailable-exhausted-key",
        "file-write-approval",
        "denied-write",
        "shell-test-tool",
        "attachment",
        "council",
        "retry-exact-failure",
        "cancellation",
        "restart-continuity",
        "privacy-erasure",
        "corrupted-cas",
        "uncertified-author",
        "unsupported-claim",
        "refusal-truth",
        "evidence-receipt-correspondence",
    ):
        assert expected in ids, expected


def test_mutation_controls_cover_the_six_required_defects(selftest):
    from ops.served_reality.mutations import MUTATION_CONTROLS

    by_id = {control.mutation_id: control for control in MUTATION_CONTROLS}
    assert set(by_id) == {
        "m1-dropped-request-unit",
        "m2-bypassed-permission",
        "m3-fabricated-tool-success",
        "m4-wrong-turn-evidence",
        "m5-corrupted-cas-accepted",
        "m6-false-receipt-verification",
    }
    assert all(control.injects in {"stub", "app-patch", "home-tamper"} for control in MUTATION_CONTROLS)
