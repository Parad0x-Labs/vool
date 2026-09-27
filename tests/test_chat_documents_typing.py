"""The ONE type authority for composed text: deterministic, server-side, parser-backed where a
parser exists.

The sibling lane (build/vool-working-mark-20260902) typed pastes in the page with a regex sniff;
converged, the page sends bytes and the server names the document -- one authority, the chip
shows its verdict. The families and the sibling's own cases are pinned here against
`document_type_for` and through `stage_document`, plus the window selection's own laws.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys

import pytest

from core import chat_attachments as ca
from core import runtime_paths

SESSION = "openclaw:d0c0d0c0d0c0d0c0a7a7"

CASES = [
    ("log", 'Traceback (most recent call last):\n  File "x.py"\nError: boom', ".log", "text/plain"),
    ("log", "2026-09-02T10:00:00 INFO service ready\n2026-09-02T10:00:01 INFO ok\n2026-09-02T10:00:02 WARN slow\n", ".log", "text/plain"),
    ("log", "INFO: a\nWARN: b\nERROR: c\n", ".log", "text/plain"),
    ("json", '{"a": 1, "b": [2, 3]}', ".json", "application/json"),
    ("json", "[1, 2, 3]", ".json", "application/json"),
    ("yaml", "service:\n  name: ledger\n  ports:\n    - 8080\n", ".yaml", "application/yaml"),
    ("csv", "region,amount\nnorth,1250\nsouth,875\n", ".csv", "text/csv"),
    ("csv", "region\tamount\nnorth\t1250\nsouth\t875\n", ".tsv", "text/tab-separated-values"),
    ("python", "def main():\n    import os\n    return os.path\n", ".py", "text/x-python"),
    ("shell", "#!/bin/sh\nset -eu\nrsync -a ./build/ target:/srv/app/\n", ".sh", "text/x-sh"),
    ("shell", "sudo apt-get update\nsudo apt-get install -y jq\n", ".sh", "text/x-sh"),
    ("sql", "SELECT region, SUM(amount)\nFROM sales\nGROUP BY region;", ".sql", "application/sql"),
    ("javascript", "const rates = { eu: 1 };\nfunction convert(usd) { return usd * rates.us; }\n", ".js", "text/javascript"),
    ("markdown", "# Heading\n\nbody text", ".md", "text/markdown"),
    ("markdown", "some prose\n\n```python\nprint(1)\n```\n", ".md", "text/markdown"),
    ("text", "just some ordinary prose\nwith lines\n", ".txt", "text/plain"),
    ("text", '{"a": 1,}\n', ".txt", "text/plain"),
]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.mark.parametrize("rule,text,extension,media_type", CASES)
def test_every_family_is_typed_deterministically(rule: str, text: str, extension: str, media_type: str) -> None:
    verdicts = {ca.document_type_for(text) for _ in range(3)}
    assert verdicts == {(extension, media_type, rule)}, verdicts
    record = ca.stage_document(session_id=SESSION, data=text.encode("utf-8"))
    assert record["name"].endswith(extension) and record["media_type"] == media_type


def test_the_strongest_signal_wins_in_a_fixed_order() -> None:
    assert ca.DOCUMENT_TYPE_RULES == ("log", "json", "yaml", "csv", "python", "shell", "sql", "javascript", "markdown", "text")
    # A traceback that mentions `import` is a log, not Python; YAML with a SELECT in a value is YAML.
    assert ca.document_type_for("Traceback (most recent call last):\n  File \"a.py\", line 1, in <module>\n    import x\nImportError: boom")[2] == "log"
    assert ca.document_type_for("query:\n  sql: SELECT a FROM b\n  rows:\n    - 1\n")[2] == "yaml"


def test_the_page_carries_no_type_sniff_of_its_own() -> None:
    """One type authority: the composer sends bytes and shows the server's name and type."""
    from tests.chat_page_js_harness import script

    src = script()
    assert "pasteDocKind" not in src and "PASTE_DOC_THRESHOLD" not in src
    assert "docThreshold()" in src and "X-Vool-Attachment-Source" in src


def test_a_declared_name_decides_the_type_and_is_kept() -> None:
    dropped = ca.stage_document(session_id=SESSION, data=b"anything at all\n", declared_name="notes.md", source="composer_drop")
    assert (dropped["name"], dropped["media_type"], dropped["source"]) == ("notes.md", "text/markdown", "composer_drop")
    with pytest.raises(ca.AttachmentRefused) as refused:
        ca.stage_document(session_id=SESSION, data=b"x" * 10, declared_name="archive.zip")
    assert refused.value.code == "unsupported_type"


# --------------------------------------------------------------- the window selection's own laws


def test_windows_are_exact_line_ranges_that_tile_the_document() -> None:
    text = "".join(f"line {i}\n" for i in range(1000))
    windows = ca._line_windows(text)
    assert windows[0][0] == 1 and windows[-1][1] == 1000
    assert all(b[0] == a[1] + 1 for a, b in itertools.pairwise(windows)), "windows must tile without gaps or overlap"
    assert "".join(w[2] for w in windows) == text, "windows must reproduce the document byte for byte"
    assert all(w[1] - w[0] + 1 <= ca.CHUNK_LINES for w in windows)


def test_a_question_pulls_its_own_window_in_and_the_omissions_are_named() -> None:
    lines = [f"filler line number {i} with nothing of note" for i in range(3000)]
    lines[1500] = "the needle: cache invalidation bug in region west"
    text = "\n".join(lines) + "\n"
    rendered, truncated, selection = ca.select_document_text(text, budget=20_000, question="what does it say about the cache invalidation bug?")
    assert truncated and "the needle: cache invalidation bug" in rendered
    assert rendered.startswith("filler line number 0 ") and "filler line number 2999" in rendered
    assert any(a <= 1501 <= b for a, b in selection["included_ranges"])
    assert selection["omitted_ranges"] and all(a <= b for a, b in selection["omitted_ranges"])
    assert "[… lines" in rendered and "omitted (" in rendered
    assert len(rendered) <= 20_000
    # Without the question, the middle window is not chosen: the ranking is what found it.
    plain, _, plain_selection = ca.select_document_text(text, budget=20_000, question="")
    assert "the needle" not in plain and plain_selection["included_ranges"][0] == [1, 40]


def test_a_budget_too_small_for_two_windows_still_delivers_an_exact_head() -> None:
    text = "".join(f"row {i:04d}\n" for i in range(500))
    rendered, truncated, selection = ca.select_document_text(text, budget=200, question="row 0400")
    assert truncated and rendered.startswith("row 0000\n") and len(rendered) <= 200
    assert selection["included_ranges"] and selection["omitted_ranges"]


# ---------------------------------------------------------------------------
# attachment_typing bounds contracts (folded here from a standalone file: a new
# collected test file re-partitions the shards through ops/shard_resolver.py, and
# the documented TestTemporaryRules order-dependence makes partition shifts a gate
# roulette until its served-fixture-lane owner lands the fix).
# ---------------------------------------------------------------------------

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
