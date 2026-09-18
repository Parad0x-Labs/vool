"""The collapsed Proof Chip names its coverage beside the server's state word.

`VERIFIED` alone read as "the answer is verified" while it certified only that the committed
bytes re-hash. The chip now carries the server's coverage label (`compact.coverage.label`)
verbatim -- `3/3 lookups`, `1 of 3 lookups failed · 1 derived step unbound`, `no lookups` -- so
a reader sees what was looked up next to what was verified. The state word itself is unchanged
and still taken verbatim from the server.
"""
from __future__ import annotations

import copy

from tests.test_proof_chip_ui import PROOF_OK, _drive

MOUNT = """
const host = document.createElement('div');
const chip = await mountProofChip(host, 'sess', 'req-1');
out({
  headText: chip ? chip.querySelector('.proof-chip-head').textContent : '',
  stateWord: chip ? (chip.querySelector('.pc-state') || {}).textContent : '',
  coverage: chip ? ((chip.querySelector('.pc-coverage') || {}).textContent || '') : '',
  bodyText: chip ? chip.querySelector('.proof-chip-body').textContent : '',
});
"""


def _with_coverage(label: str, observations: list[dict] | None = None) -> dict:
    proof = copy.deepcopy(PROOF_OK)
    proof["compact"]["coverage"] = {
        "observations": {"total": 3, "succeeded": 3, "failed": 0, "cached": 0, "pending": 0},
        "derived": {"total": 1, "bound": 1, "unbound": 0},
        "label": label,
    }
    proof["expanded"]["coverage"] = proof["compact"]["coverage"]
    proof["expanded"]["observations"] = observations or [
        {"node_id": "btc", "operation": "market_quote", "kind": "observation", "ok": True, "state": "succeeded",
         "host": "api.coingecko.com", "duration_s": 0.41, "failure_code": "", "depends_on": [], "cached": False},
        {"node_id": "ratio", "operation": "quantitative_reasoning", "kind": "derived", "ok": True, "state": "succeeded",
         "host": "", "duration_s": 0.02, "failure_code": "", "depends_on": ["btc", "gold"], "bound": True, "cached": False},
    ]
    return proof


def test_collapsed_chip_shows_the_servers_coverage_label_verbatim() -> None:
    result = _drive(MOUNT, proof=_with_coverage("3/3 lookups"))
    assert result["stateWord"] == "Record verified"
    assert result["coverage"] == "3/3 lookups"
    assert "Record verified" in result["headText"] and "3/3 lookups" in result["headText"]


def test_a_failed_lookup_label_reaches_the_collapsed_chip_unchanged() -> None:
    label = "1 of 3 lookups failed · 1 derived step unbound"
    result = _drive(MOUNT, proof=_with_coverage(label))
    assert result["coverage"] == label


def test_expanded_view_lists_each_lookup_with_its_source_and_outcome() -> None:
    result = _drive(MOUNT, proof=_with_coverage("3/3 lookups"))
    body = result["bodyText"]
    assert "Lookups" in body
    assert "market_quote" in body and "api.coingecko.com" in body
    assert "quantitative_reasoning" in body and "bound" in body


def test_a_proof_without_coverage_renders_no_coverage_segment() -> None:
    """Older projections (no coverage field) must not invent one."""
    result = _drive(MOUNT, proof=copy.deepcopy(PROOF_OK))
    assert result["coverage"] == ""
    assert result["stateWord"] == "Record verified"
