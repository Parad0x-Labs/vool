"""Malformed private-key blocks must be masked without quadratic retries."""
import subprocess
import sys

import pytest

from core.secret_redaction import contains_secret, redact_secrets


@pytest.mark.parametrize("label", ["", "RSA ", "EC ", "OPENSSH ", "ENCRYPTED "])
def test_private_key_blocks_preserve_surrounding_text(label):
    block = f"-----BEGIN {label}PRIVATE KEY-----\nfixture only\n-----END {label}PRIVATE KEY-----"
    assert contains_secret(block)
    assert redact_secrets("before\n" + block + "\nafter") == "before\n[redacted-private-key]\nafter"


def test_unterminated_private_key_is_not_written_in_cleartext():
    block = "-----BEGIN PRIVATE KEY-----\nfixture-secret-body"
    assert contains_secret(block)
    assert redact_secrets("before\n" + block) == "before\n[redacted-private-key]"


def test_nested_openers_do_not_restart_the_body_scan():
    script = "from core.secret_redaction import redact_secrets; s='-----BEGIN PRIVATE KEY-----\\n'*20000; assert redact_secrets(s) == '[redacted-private-key]'"
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5)
    assert completed.returncode == 0, completed.stderr


def test_public_key_and_unpaired_end_remain_readable():
    text = "-----BEGIN PUBLIC KEY-----fixture-----END PUBLIC KEY----- and -----END PRIVATE KEY-----"
    assert not contains_secret(text)
    assert redact_secrets(text) == text
