"""Decoded scalar instructions and large numeric prose must remain bounded."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("expression", [
    "parse_raw_output_contract(json.dumps({'intent':'return word X'+'\\n'*100000+'bad','format':'raw text only'}))",
    "parse_raw_output_contract('0'*100000+'X number only')",
])
def test_invalid_scalar_and_numeric_instructions_complete_promptly(expression):
    script = "import json; from core.raw_output_contract import parse_raw_output_contract; " + expression
    subprocess.run([sys.executable, "-c", script], check=True, timeout=3,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
