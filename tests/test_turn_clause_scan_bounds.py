"""Clause splitting must not re-run the prohibition authority per separator suffix.

``parse_turn_ir`` asks ``classify_clause_kind`` about the clause after every structural
separator; that classification consults the retrieval-prohibition authority, which re-scanned
the whole remaining text once per separator. A separator-heavy run with no whitespace after
its separators made every suffix pay the full analysis: the public
``resolve_write_demand``/``parse_turn_ir`` path exceeded a 3s subprocess deadline at 8000
separators and took ~7s at 4000.
"""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("script", [
    "from core.turn_ir import parse_turn_ir; "
    "parse_turn_ir('inside this workspace create ' + 'a.' * 4000, response_shape_parser=None)",
    "from core.execution.write_demand import resolve_write_demand; "
    "resolve_write_demand('inside this workspace create ' + 'a.' * 4000)",
    "from core.turn_ir import parse_turn_ir; "
    "parse_turn_ir('create file ' + 'a.' * 4000 + 'b in c folder saying y', response_shape_parser=None)",
])
def test_separator_runs_skip_per_suffix_prohibition_analysis(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=3,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("text,prohibited,kind,clause_count", [
    ("Do NOT search the web for this", True, "CONSTRAINT", None),
    ("get the rate without using the web", True, "UNKNOWN", None),
    ("no web searches please, just answer", True, "CONSTRAINT", None),
    ("Kaunas and Tallinn, tell me the weather", False, "KNOW", 2),
    ("Do NOT search the web for this, what is 2+2?", True, None, 2),
    ("Find my keys. Never search the internet. Tell me where they are.", True, None, 3),
])
def test_clause_splitting_and_prohibitions_are_unchanged(text, prohibited, kind, clause_count):
    """A prohibition still constrains its clause and splits nothing it did not split before."""
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.turn_ir import classify_clause_kind, parse_turn_ir

    assert analyze_retrieval_constraints(text).has_prohibition is prohibited
    if kind is not None:
        assert classify_clause_kind(text).name == kind
    doc = parse_turn_ir(text, response_shape_parser=None)
    assert len(doc.clauses) >= 1
    if clause_count is not None:
        assert len(doc.clauses) == clause_count
