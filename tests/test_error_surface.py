"""Guard: an unhandled exception must not ship internals into the user's chat bubble.

Both the streaming transport (`Runtime error: {exc}`) and the JSON 500 responses (`{"error":
str(exc)}`) rendered raw exception text as the assistant's answer — absolute paths exposing the
operator's username and layout, and anything the raising code interpolated, including a credential
in scope at the raise site.
"""
from __future__ import annotations

from core.error_surface import safe_error_text


def test_absolute_paths_are_reduced_to_a_basename():
    out = safe_error_text(FileNotFoundError("cannot open /Users/example-user/Desktop/web0/secrets.env"))
    assert "/Users/example-user" not in out
    assert "example-user" not in out
    assert "secrets.env" in out          # still names the file that failed
    assert "FileNotFoundError" in out    # the class is the useful, safe part


def test_windows_paths_are_reduced_too():
    out = safe_error_text(OSError(r"failed on C:\Users\kas\AppData\vool\model.bin"))
    assert "AppData" not in out and r"C:\Users" not in out
    assert "model.bin" in out


def test_credentials_in_the_message_are_redacted():
    out = safe_error_text(RuntimeError("auth failed for sk-or-v1-abcdef0123456789abcdefghij"))
    assert "abcdef0123456789" not in out
    assert "RuntimeError" in out


def test_always_returns_something_useful_and_never_raises():
    assert safe_error_text(TimeoutError()).strip()
    assert "TimeoutError" in safe_error_text(TimeoutError())
    assert safe_error_text(None).strip()
    assert safe_error_text("bare string").strip()


def test_long_detail_is_truncated():
    out = safe_error_text(RuntimeError("x" * 4000))
    assert len(out) < 300
