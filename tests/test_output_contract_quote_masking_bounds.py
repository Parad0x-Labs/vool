"""Quoted-span masking must stay bounded on unclosed escaped-quote runs.

The public entrypoint ``parse_raw_output_contract`` masks quoted documentation before any
directive pattern runs; an unclosed double-quote whose interior keeps escaping its own
closing quotes must not make that mask rescan the suffix once per quote.
"""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("script", [
    "from core.raw_output_contract import _masked_quoted_text; _masked_quoted_text('\"a\\\\' * 20000)",
    "from core.raw_output_contract import parse_raw_output_contract; parse_raw_output_contract("
    "'return exactly ' + '\"a\\\\' * 20000)",
    "from core.raw_output_contract import _masked_quoted_text; _masked_quoted_text('\"' + ('ab\\\\' * 20000))",
])
def test_escaped_quote_runs_do_not_rescan_suffixes(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=3,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("text,expected", [
    ("", ""),
    ('"', '"'),
    ('""', " "),
    ("''", " "),
    ('"a"', " "),
    ('"a\\"b"', " "),
    # An escaped quote stays interior; the span closes at the NEXT unescaped quote.
    ('"say \\"hi\\"" ok', "  ok"),
    ('"a\\', '"a\\'),
    # Unclosed/invalid-escape openers stay unmasked; later quotes still open valid spans.
    ('"broken \\"still\\" u\'ntil \' real', '"broken \\"still\\" u  real'),
    ('"a\\\nb"', '"a\\\nb"'),
    ('x "y" z', "x   z"),
    ('a"b"c', "a c"),
    ("a'b'c", "a c"),
    ("'a\nb'", "'a\nb'"),
    ('he said "one" and \'two\'', "he said   and  "),
])
def test_quote_masking_preserves_span_boundaries(text, expected):
    from core.raw_output_contract import _masked_quoted_text

    assert _masked_quoted_text(text) == expected


def test_quoted_documentation_still_masks_downstream_instructions():
    from core.raw_output_contract import parse_raw_output_contract

    prompt = (
        'In our docs, "return exactly \\"ONE WORD\\" and nothing else" is an example; '
        "explain why it is brittle."
    )
    assert parse_raw_output_contract(prompt) is None
