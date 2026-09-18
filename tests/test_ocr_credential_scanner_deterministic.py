"""A credential recovered by OCR is refused — proved without betting on Vision.

The existing pixels-only guard
(``tests/test_artifact_readers_confinement.py::test_a_credential_read_by_ocr_out_of_a_scanned_page_is_refused``)
renders a credential into a raster, asks macOS Vision to read it back, and then
asserts the door refuses. It passes on this machine, and a direct probe shows
Vision recovering the canary character-exact — but at ``mean_confidence 0.50``
against a ``0.30`` floor. That is a 0.20 margin on a synthetic raster, decided by
the OS version and the Vision model revision, not by this repo. It was recorded
as failing elsewhere for exactly that reason.

So the *policy* half is proved here deterministically instead: known OCR text is
handed to the REAL production seam, ``_refuse_credential_shaped_extraction``,
which runs the real per-unit ``scan_document_text`` and raises the real
``AttachmentRefused``. Nothing about the policy is mocked — only the raster is
removed from the question, because whether Vision can read a particular set of
pixels is not what this law is about.

The pixels-only test stays exactly as it is. Between them: this file proves the
scanner refuses whatever OCR produced, and that one proves Vision produces it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

#: Credential-SHAPED, assembled at runtime so this file never contains a scannable literal.
CANARY_AWS = "AKIA" + "Q7X4M2NPLVZW9TCD"
#: A GitHub-token shape. Verified against the live scanner rather than assumed:
#: the shorter "sk-<hex>" form some fixtures use is NOT matched by
#: scan_document_text, so a test built on it would refuse nothing and pass.
CANARY_GH = "ghp_" + "A" * 36
#: AWS's own documented example key. It must keep passing, or every document that
#: explains how to configure credentials becomes unattachable.
PLACEHOLDER_AWS = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture()
def door(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments

    return chat_attachments


def _ocr_extraction(text: str, *, locator: str = "page 1") -> dict:
    """The shape an OCR read of a scanned page hands the door."""
    return {
        "text": text,
        "units": [{"text": text, "locator": locator}],
        "meta": {"source": "ocr", "ocr_pages": 1},
    }


def test_a_credential_that_only_ocr_could_have_produced_is_refused(door) -> None:
    """The scanner sees OCR output exactly as it sees a text layer."""
    with pytest.raises(door.AttachmentRefused) as caught:
        door._refuse_credential_shaped_extraction(
            "screenshot.pdf", _ocr_extraction(f"AWS key {CANARY_AWS} do not share")
        )
    assert caught.value.code == "secret_detected", caught.value.code
    # The refusal says WHERE, which is the difference between an actionable
    # message and a hunt through a forty-page document.
    assert "page 1" in caught.value.message, caught.value.message


def test_the_refusal_names_the_page_the_credential_was_on(door) -> None:
    extraction = {
        "text": "clean\n" + CANARY_GH,
        "units": [
            {"text": "nothing to see here", "locator": "page 1"},
            {"text": "nothing here either", "locator": "page 2"},
            {"text": f"token {CANARY_GH}", "locator": "page 3"},
        ],
        "meta": {"source": "ocr", "ocr_pages": 3},
    }
    with pytest.raises(door.AttachmentRefused) as caught:
        door._refuse_credential_shaped_extraction("scan.pdf", extraction)
    assert caught.value.code == "secret_detected"
    assert "page 3" in caught.value.message, caught.value.message


def test_the_documented_placeholder_still_passes(door) -> None:
    """A document that EXPLAINS credentials is not a document that leaks one."""
    door._refuse_credential_shaped_extraction(
        "guide.pdf", _ocr_extraction(f"Set your key, e.g. {PLACEHOLDER_AWS}, then run the tool.")
    )


def test_ordinary_scanned_prose_passes(door) -> None:
    door._refuse_credential_shaped_extraction(
        "invoice.pdf",
        _ocr_extraction("Invoice 55182 — total 91.40 — account 4417 — thank you for your business"),
    )


def test_the_credential_never_reaches_a_derivative_on_disk(door, tmp_path: Path) -> None:
    """The refusal precedes _write_derivative, so no file holds the extracted secret.

    Asserted against the real staging area as a DELTA, and by scanning every byte
    under the data dir for the canary — a derivative written and then deleted
    would still be a leak this catches while it exists.
    """
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(door.AttachmentRefused):
        door._refuse_credential_shaped_extraction(
            "screenshot.pdf", _ocr_extraction(f"AWS key {CANARY_AWS}")
        )
    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before, f"the refusal wrote files: {sorted(after - before)}"
    for path in after:
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        assert CANARY_AWS.encode() not in blob, f"the credential landed in {path}"


def test_sabotage_dropping_the_per_unit_scan_lets_the_credential_through(
    door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan is load-bearing: neutralize it and the refusal disappears.

    A guard whose removal changes nothing is not a guard. This names the exact
    seam — the per-unit call into the document scanner — rather than disabling
    the whole door, so the sabotage cannot be satisfied by some other refusal.
    """
    monkeypatch.setattr(door, "_refuse_credential_shaped", lambda *a, **k: None)
    # No refusal at all now — the credential would ride straight into a derivative.
    door._refuse_credential_shaped_extraction(
        "screenshot.pdf", _ocr_extraction(f"AWS key {CANARY_AWS}")
    )


def test_the_real_ocr_smoke_is_preserved_elsewhere() -> None:
    """This file must not become a reason to delete the raster proofs.

    Two tests own the claim that Vision actually recovers characters from pixels.
    If either is renamed or removed, this fails and names the replacement that is
    owed, instead of the OCR claim quietly becoming untested.
    """
    root = Path(__file__).resolve().parents[1]
    owed = {
        "tests/test_artifact_readers_served_m2.py": (
            "test_image_ocr_reader_recovers_characters_a_vision_model_would_paraphrase"
        ),
        "tests/test_artifact_readers_confinement.py": (
            "test_a_credential_read_by_ocr_out_of_a_scanned_page_is_refused"
        ),
    }
    for relative, name in owed.items():
        path = root / relative
        assert path.exists(), f"the real OCR proof {relative} is gone"
        assert name in path.read_text(encoding="utf-8"), (
            f"{relative} no longer contains {name}; this deterministic file proves the "
            "SCANNER, never that Vision can read a raster — that claim needs its own test"
        )
