"""Document typing must stay bounded on blank-line runs.

``document_type_for`` is the one typing authority behind paste/attachment staging; its log,
YAML, shell and SQL detectors used leading ``\\s*`` runs that crossed blank lines, so every
newline in a run restarted the same whitespace scan.
"""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("script", [
    "from core.chat_attachments import document_type_for; document_type_for('a:\\n' + '\\n' * 20000 + 'x')",
    "from core.chat_attachments import document_type_for; document_type_for('y:\\n' + '\\n' * 20000 + 'SELECT a')",
    "from core.chat_attachments import document_type_for; document_type_for('z:\\n' + '\\n' * 20000 + 'INFO x')",
    "from core.chat_attachments import document_type_for; document_type_for('w:\\n' + '\\n' * 20000 + '- x')",
    "from core.chat_attachments import document_type_for; document_type_for('intro\\n' + '\\n' * 20000 + 'sudo x\\nchmod u+w f\\n')",
])
def test_blank_runs_do_not_restart_whitespace_scans(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=3,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("text,rule", [
    ("sudo rm -rf /\nnpm install\ngit status", "shell"),
    ("intro\n\n\nsudo x\nnpm y", "shell"),
    ("   cd /tmp\n  export A=1", "shell"),
    ("#!/bin/bash\nsudo x\nnpm y", "shell"),
    ("SELECT * FROM t;\nUPDATE t SET a=1", "sql"),
    ("\n\nSELECT a\nFROM t", "sql"),
    ("  insert into t values (1)", "sql"),
    ("2026-09-25 10:00:00 INFO start\n2026-09-25 10:00:01 ERROR bad\n2026-09-25 10:00:02 INFO end", "log"),
    ("\n\nINFO x\n\n\nERROR y\n\n\nINFO z", "log"),
    ("# heading\n\nbody", "markdown"),
    ("plain prose only", "text"),
])
def test_classification_is_unchanged(text, rule):
    from core.chat_attachments import document_type_for

    assert document_type_for(text)[2] == rule


def test_yaml_nesting_keeps_cross_blank_line_language():
    from core.chat_attachments import _yaml_nesting

    # The old \s crossed blank lines: these verdicts are part of the typing contract.
    assert _yaml_nesting("  - x") is True
    assert _yaml_nesting("\n- x") is True
    assert _yaml_nesting("a:\n- x") is False
    assert _yaml_nesting("a:\n\n- x") is True
    assert _yaml_nesting("- x") is False
    assert _yaml_nesting("  x") is True
    assert _yaml_nesting(" x") is False
    assert _yaml_nesting("\n\nx") is True
    assert _yaml_nesting("\n x") is True
    assert _yaml_nesting(" x\n\ny") is False
