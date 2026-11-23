"""A Doctor check that could not run says so in plain words, never with a Python class name.

Trust-features audit of 900c7bc19: with the phone module failing, the Doctor showed "This check could not run
(ModuleNotFoundError)." to the person. The fault stays in the row's values for a report; the sentence shows none.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

CATALOGS = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "core/i18n/catalogs/*.json")))


@pytest.mark.parametrize("path", CATALOGS, ids=lambda p: Path(p).stem)
def test_the_sentence_names_no_fault(path) -> None:
    said = json.loads(Path(path).read_text(encoding="utf-8"))["messages"]["doctor.said.check_failed"]
    assert "{fault}" not in said and "Error" not in said, said


def test_the_crashed_row_keeps_its_fault_for_a_report(monkeypatch) -> None:
    from core import vool_doctor

    def crash():
        raise ModuleNotFoundError("phone")

    monkeypatch.setattr(vool_doctor, "CHECKS", (("crash", lambda: crash()),))
    [row] = vool_doctor.run_checks()["checks"]
    assert row["reason"] == "check_failed" and row["values"]["fault"] == "ModuleNotFoundError"
