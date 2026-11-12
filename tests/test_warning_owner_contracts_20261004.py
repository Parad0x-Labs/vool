"""First-party warning owners (CI warning intake, 2026-10-04): each one fixed at its owner.

Main CI reported first-party deprecations from the arithmetic evaluator (``ast.Num``) and the work
receipt clock (``datetime.utcfromtimestamp``), and unknown-mark warnings for markers the suite uses
without registering them. Each repair must keep the behaviour it touches byte for byte: the same
expressions are evaluated or refused, and the receipt keeps its exact timestamp wire format.
"""
from __future__ import annotations

import time
import warnings

import pytest

from core.task_router import evaluate_direct_math_request
from core.web0_work_receipt import _iso


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 + 3", "2 + 3 = 5."),
        ("7 * 6", "7 * 6 = 42."),
        ("-4 + 10", "-4 + 10 = 6."),
        ("10 / 4", "10 / 4 = 2.5."),
    ],
)
def test_arithmetic_is_evaluated_without_a_deprecated_ast_node(text: str, expected: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert evaluate_direct_math_request(text) == expected


@pytest.mark.parametrize("text", ["2j + 3", "__import__('os')", "2 ** 'a'"])
def test_unsupported_expressions_are_still_refused(text: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert evaluate_direct_math_request(text) is None


@pytest.mark.parametrize("ts", [0.0, 1.5, 1_700_000_000.0, 1_700_000_000.123456, 4_102_444_800.0])
def test_the_receipt_timestamp_keeps_its_exact_wire_format(ts: float) -> None:
    whole = int(ts // 1)
    micro = round((ts - whole) * 1_000_000)
    expected = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(whole)) + (f".{micro:06d}" if micro else "") + "Z"
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert _iso(ts) == expected


def test_the_semantic_markers_the_suite_uses_are_registered(pytestconfig: pytest.Config) -> None:
    registered = {str(line).split(":", 1)[0].strip() for line in pytestconfig.getini("markers")}
    assert {"served", "real_restart_store", "email_live"} <= registered
