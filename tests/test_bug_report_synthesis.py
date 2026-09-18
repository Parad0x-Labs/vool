"""Synthesis governance: the LLM never owns sanitization.

Cause families: local/model-free formatting works and is deterministic; cloud synthesis is
OFF by default; a model may only ever receive already-scanned material (typed gate refuses
dirty payloads); model output is re-scanned and fails closed if it introduces anything
unsafe; the model may only restructure the allowed sections.
"""
from __future__ import annotations

import json

import pytest

from core.bug_report.schema import SanitizedMaterial, UnsafeReportContentError
from core.bug_report.synthesis import cloud_synthesis_enabled, synthesize_local, synthesize_with_model

SECRET = "sk-or-v1-" + "m" * 48


def _clean_material() -> SanitizedMaterial:
    return SanitizedMaterial.from_payload({
        "title": "daemon crash",
        "category": "crash",
        "expected": "turn completes",
        "actual": "daemon crashed with RuntimeError",
        "repro_steps": ["start daemon", "send one turn"],
        "error": {"exc_type": "RuntimeError", "message": "boom", "frames": []},
        "components": {"lanes": ["turn_frontdoor_deterministic"], "tools": [], "models": []},
        "fingerprint": "ab12cd34ef56",
    })


def test_local_synthesis_works_without_any_model() -> None:
    material = _clean_material()
    title, body = synthesize_local(material)
    assert title and "crash" in title.lower()
    assert "## Expected" in body and "## Actual" in body
    assert "## Minimal reproduction" in body
    assert "1. start daemon" in body
    assert "bug-report-fingerprint: ab12cd34ef56" in body
    assert SECRET not in body


def test_local_synthesis_is_deterministic() -> None:
    material = _clean_material()
    assert synthesize_local(material) == synthesize_local(material)


def test_cloud_synthesis_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_BUG_REPORT_CLOUD_SYNTHESIS", raising=False)
    from core import runtime_flags

    assert runtime_flags.flag("bug_report_cloud_synthesis").default is False
    assert cloud_synthesis_enabled() is False


def test_cloud_synthesis_enabled_only_via_explicit_optin(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_BUG_REPORT_CLOUD_SYNTHESIS", "1")
    assert cloud_synthesis_enabled() is True
    monkeypatch.setenv("VOOL_BUG_REPORT_CLOUD_SYNTHESIS", "0")
    assert cloud_synthesis_enabled() is False


def test_sanitized_material_refuses_dirty_payload() -> None:
    with pytest.raises(UnsafeReportContentError):
        SanitizedMaterial.from_payload({"title": f"crash with {SECRET}"})
    with pytest.raises(UnsafeReportContentError):
        SanitizedMaterial.from_payload({"note": "repro at /Users/fixtureuser/vool"})
    with pytest.raises(UnsafeReportContentError):
        SanitizedMaterial.from_payload({"note": "mail alice@example.com"})


def test_model_never_sees_secrets_when_cloud_synthesis_runs() -> None:
    seen: list[dict] = []

    def fake_model(payload: dict) -> str:
        seen.append(payload)
        return synthesize_local(SanitizedMaterial.from_payload(payload))[1]

    material = _clean_material()
    _title, body = synthesize_with_model(material, model_fn=fake_model, enabled=True)
    assert seen, "the model must have been invoked"
    dumped = json.dumps(seen[0])
    for poison in (SECRET, "fixtureuser", "@example.com"):
        assert poison not in dumped
    assert "## Expected" in body  # output still usable


def test_model_output_that_introduces_a_secret_fails_closed() -> None:
    def malicious_model(payload: dict) -> str:
        return f"## Actual\nleaked {SECRET}\nbug-report-fingerprint: ab12cd34ef56"

    material = _clean_material()
    _title, body = synthesize_with_model(material, model_fn=malicious_model, enabled=True)
    assert SECRET not in body
    assert "## Expected" in body  # deterministic local formatting used as the fallback


def test_model_output_cannot_introduce_new_sections() -> None:
    def wandering_model(payload: dict) -> str:
        return "## Environment\nattacker section\n## Expected\nok\nbug-report-fingerprint: ab12cd34ef56"

    material = _clean_material()
    _title, body = synthesize_with_model(material, model_fn=wandering_model, enabled=True)
    assert "attacker section" not in body  # only a restricted subset of sections may carry over


def test_disabled_cloud_synthesis_never_calls_the_model() -> None:
    def must_not_run(payload: dict) -> str:
        raise AssertionError("model called while cloud synthesis is disabled")

    material = _clean_material()
    _title, body = synthesize_with_model(material, model_fn=must_not_run, enabled=False)
    assert "## Expected" in body
