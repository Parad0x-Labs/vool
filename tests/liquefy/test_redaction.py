"""Redaction-at-ingest contract: secrets die before any persistence."""
from __future__ import annotations

from core.liquefy.redaction import redact_payload


def test_aws_key_redacted_with_class_count():
    payload = {"note": "key is AKIAIOSFODNN7EXAMPLE rotate me", "other": 1}
    redacted, counts = redact_payload(payload)
    assert "AKIAIOSFODNN7EXAMPLE" not in str(redacted)
    assert "[REDACTED:aws_access_key]" in redacted["note"]
    assert counts == {"aws_access_key": 1}


def test_sensitive_field_names_redacted_wholesale():
    payload = {"password": "hunter2-secret", "api-key": "sk-livetest-abcdefgh", "size": 12}
    redacted, _counts = redact_payload(payload)
    assert redacted["password"] == "[REDACTED:password]"
    assert redacted["api-key"] == "[REDACTED:api_key]"
    assert redacted["size"] == 12


def test_nested_structures_and_token_families():
    payload = {
        "headers": {"Authorization": "basic-YWRtaW46aHVudGVyMg==", "note": "curl -H 'Authorization: Bearer abc.def.ghi-jkl-mno-pqr'"},
        "env": ["ghp_" + "a" * 30, "xoxb-" + "123456789" * 3],
        "pem": "-----BEGIN PRIVATE KEY-----",
    }
    redacted, counts = redact_payload(payload)
    flat = str(redacted)
    assert "ghp_" + "a" * 30 not in flat
    assert "xoxb-" + "123456789" * 3 not in flat
    assert "abc.def.ghi" not in flat
    assert "-----BEGIN PRIVATE KEY-----" not in flat
    assert counts["github_token"] == 1
    assert counts["slack_token"] == 1
    assert counts["bearer_token"] == 1


def test_receipt_carries_counts_never_text():
    payload = {"note": "AKIAIOSFODNN7EXAMPLE and ghp_" + "b" * 30}
    _, counts = redact_payload(payload)
    assert set(counts) <= {"aws_access_key", "github_token"}
    for value in counts.values():
        assert isinstance(value, int)


def test_ordinary_prose_untouched():
    payload = {"message": "the passport number field is missing; skunk crossed the road"}
    redacted, counts = redact_payload(payload)
    assert redacted["message"] == payload["message"]
    assert counts == {}
