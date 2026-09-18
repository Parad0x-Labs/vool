"""fact_extractor must scrub Linux home paths, not only macOS /Users (cross-platform parity)."""
from __future__ import annotations

from core.fact_extractor import _redact_sensitive_text


def test_linux_home_paths_are_redacted():
    assert "[REDACTED]" in _redact_sensitive_text("logs at /home/alice/private/log.txt")
    assert "alice" not in _redact_sensitive_text("logs at /home/alice/private/log.txt")
    assert "[REDACTED]" in _redact_sensitive_text("root key /root/.ssh/id_rsa here")


def test_macos_home_still_redacted():
    assert "[REDACTED]" in _redact_sensitive_text("at /Users/bob/Desktop/secret.txt")
