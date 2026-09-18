"""Record integrity must never read as independent answer correctness."""
from __future__ import annotations

import copy

import pytest

from core.proof_projection import build_turn_proof
from tests.test_proof_chip_ui import PROOF_OK, _drive
from tests.test_proof_projection import _bind_turn, _insert_finalization


@pytest.mark.parametrize("answer", ["77 + 2 = 80.", "Every bird can fly."])
def test_intact_but_false_answer_certifies_only_the_record(answer: str) -> None:
    request = "scope-" + str(len(answer))
    session = "record-scope-" + request
    _bind_turn(session, request, answer)
    _insert_finalization(request, "turn-" + request, answer)
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["state"] == "VERIFIED"
    assert proof["verification_scope"] == "record_integrity"
    assert proof["compact"]["state_label"] == "Record verified"
    assert "not independently verify" in proof["verification_explanation"]


@pytest.mark.parametrize("state", ["VERIFIED", "RECORDED", "INCOMPLETE", "UNVERIFIED"])
def test_legacy_proof_is_visibly_scoped_without_upgrading_its_state(state: str) -> None:
    proof = copy.deepcopy(PROOF_OK)
    proof["state"] = state
    proof["compact"]["state"] = state
    result = _drive("""
const chip = await mountProofChip(document.createElement('div'), 'sess', 'req-1');
out({label: chip.querySelector('.pc-state').textContent,
     title: chip.querySelector('.proof-chip-head').title,
     detail: chip.querySelector('.proof-chip-body').textContent});
""", proof=proof)
    assert result["label"] == "Record " + state.lower()
    assert "record" in result["title"].lower()
    assert "not independently verify" in result["detail"]


def test_server_scope_label_is_rendered_as_literal_text() -> None:
    proof = copy.deepcopy(PROOF_OK)
    proof["compact"]["state_label"] = "Record saved <review>"
    result = _drive("""
const chip = await mountProofChip(document.createElement('div'), 'sess', 'req-1');
out({label: chip.querySelector('.pc-state').textContent});
""", proof=proof)
    assert result["label"] == "Record saved <review>"
