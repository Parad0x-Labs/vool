"""Audit machine-tool fixes: move_path denies secret dirs, consent title is neutral, and NL
'exact contents:' modifiers no longer leak into the written file body."""
from __future__ import annotations

from pathlib import Path

from core.execution.constants import _PLAIN_CREATE_FILE_WITH_CONTENT_RE
from core.machine_file_ops import _is_protected, _protected_roots
from core.os_consent_gate import _CONSENT_TITLE


def test_move_path_denies_secret_dirs():
    protected = _protected_roots()
    home = Path.home()
    for secret in (home / ".ssh" / "id_rsa", home / ".aws" / "credentials", home / ".gnupg" / "x"):
        assert _is_protected(secret, protected), secret
    # An ordinary Desktop file is still movable.
    assert not _is_protected(home / "Desktop" / "note.txt", protected)


def test_consent_title_is_not_wallet_specific():
    assert "wallet" not in _CONSENT_TITLE.lower()
    assert "VOOL" in _CONSENT_TITLE


def test_nl_exact_contents_modifiers_do_not_leak_into_body():
    for text, expected in [
        ("create file canary.txt with exact contents: VOOL-CANARY", "VOOL-CANARY"),
        ("create file a.txt with the exact contents: HELLO", "HELLO"),
        ("write file b.txt with these exact contents: X Y Z", "X Y Z"),
        ("create file f.txt with contents: justthis", "justthis"),
    ]:
        m = _PLAIN_CREATE_FILE_WITH_CONTENT_RE.search(text)
        assert m is not None and m.group("content").strip() == expected, text
