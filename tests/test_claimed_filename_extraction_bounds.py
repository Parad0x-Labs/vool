"""Claimed-filename extraction must stay bounded and agree with the legacy language.

Both honesty gates (inspection claims in action_honesty_validator, denial subjects in
evidence_claim_binder) extract candidate paths with one shared scanner in
``file_target_contract``; the historical pattern backtracked through every suffix of a dotless
run at every start position.
"""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("script", [
    "from core.agent_runtime.action_honesty_validator import _claimed_inspection_targets; "
    "_claimed_inspection_targets('a' * 50000, 'b' * 50000)",
    "import core.agent_runtime; from core.runtime_evidence import TurnEvidence; "
    "from core.agent_runtime.evidence_claim_binder import _denial_subjects; "
    "_denial_subjects('I did not read ' + 'a' * 50000, TurnEvidence())",
    "import core.agent_runtime; from core.runtime_evidence import TurnEvidence; "
    "from core.agent_runtime.evidence_claim_binder import _denial_subjects; "
    "_denial_subjects('I did not open ' + 'a/' * 30000 + 'z', TurnEvidence())",
])
def test_claimed_target_extraction_is_bounded(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=3,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("text,expected", [
    ("file.txt", ["file.txt"]),
    ("`notes.md`", ["notes.md"]),
    ("'config.yaml'", ["config.yaml"]),
    ("/abs/path/notes.md", ["abs/path/notes.md"]),
    ("src/a-b/file.txt", ["src/a-b/file.txt"]),
    # Multi-dot names keep the legacy cut; dotted code expressions stay out via the
    # extension gate in the callers, not by changing extraction.
    ("archive.tar.gz", ["archive.tar"]),
    ("workspace.write_file", ["workspace.write"]),
    ("verylong.abcdefghij", ["verylong.abcdef"]),
    ("no dot here", []),
    ("a//b.txt", ["b.txt"]),
    ("a/b.c/d", ["a/b.c"]),
    ("read a.py then b/c.py please", ["a.py", "b/c.py"]),
    ("did you find notes.md?", ["notes.md"]),
])
def test_extraction_preserves_the_legacy_language(text, expected):
    from core.agent_runtime.file_target_contract import iter_claimed_file_candidates

    assert list(iter_claimed_file_candidates(text)) == expected


def test_inspection_claims_still_bind_named_files():
    from core.agent_runtime.action_honesty_validator import _claimed_inspection_targets

    assert _claimed_inspection_targets("I inspected `src/app.py` closely", "") == ["src/app.py"]
    # A dotted code quote is not a claimed file: the extension gate refuses it.
    assert _claimed_inspection_targets("checked `blob.startswith` usage", "") == []


def test_denial_subjects_still_bind_named_files():
    import core.agent_runtime
    from core.agent_runtime.evidence_claim_binder import SUBJECT_PATH, _denial_subjects
    from core.runtime_evidence import TurnEvidence

    subjects = _denial_subjects("I did not read config.yaml", TurnEvidence())
    assert (SUBJECT_PATH, "config.yaml") in subjects
