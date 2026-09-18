"""Redaction + outbound scan for the safe bug reporter.

Cause-named families: every privacy class the objective lists (tokens, keys, cookies, auth
headers, usernames, home paths, emails, IPs, high-entropy secrets) has at least three shapes
here plus negative controls proving typed structure survives (stack frames, lane ids,
timestamps, versions). Sabotage linkage: removing a rule family must fail the test named for
that family (see docs/SAFE_BUG_REPORTER_2026-09-01.md sabotage table).
"""
from __future__ import annotations

import pytest

from core.bug_report.redaction import redact_text
from core.bug_report.scanner import scan_text

# --- token/key family (vendor shapes) ----------------------------------------
TOKENS = [
    "sk-ant-api03-" + "a1B2c3D4e5F6g7H8i9J0k1L2",
    "sk-or-v1-" + "b" * 48,
    "ghp_" + "C1d2E3f4G5h6J7k8L9m0n1O2p3Q4",
    "github_pat_11AABBCC_" + "x1y2z3w4v5u6t7s8r9q0p1o2",
    "gsk_" + "A9b8C7d6E5f4G3h2J1k0L9m8",
    "AKIA" + "IOSFODNN7EXAMPLE",
    "xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
    "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3",
    "glpat-" + "A1b2C3d4E5f6G7h8I9j0",
    "npm_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
]

# --- cookie / auth header family ----------------------------------------------
HEADERS = [
    "Cookie: session=abc123DEF456ghi789; token=zzzyyyxxxwwwvvvuuuttt",
    "Set-Cookie: sid=d41d8cd98f00b204e9800998ecf8427e; Path=/; HttpOnly",
    "Authorization: Basic dXNlcm5hbWU6cGFzc3dvcmQ=",
    "authorization: Bearer YaZ0123456789abcdefABCDE",
    "X-Api-Key: 9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c",
    "Proxy-Authorization: Bearer QqRrSsTtUuVvWwXxYyZz001122",
]

# --- home path / username family ----------------------------------------------
PATHS = [
    "/Users/fixtureuser/Desktop/vool-checkout/core/foo.py",
    "/home/alice/projects/runner.log",
    "C:\\Users\\bob\\AppData\\Local\\VOOL\\vool.db",
    "C:/Users/carol/Documents/settings.yaml",
    "~/Library/Application Support/VOOL/state.json",
    "$HOME/.vool_local/data/memory.jsonl",
]

# --- email family ---------------------------------------------------------------
EMAILS = [
    "alice@example.com",
    "bob.smith+bugs@mail.example.org",
    "carol123@test.co.uk",
]

# --- IP family -------------------------------------------------------------------
IPS = [
    "192.168.1.42",
    "10.0.0.7",
    "172.16.254.3",
    "2001:db8::1",
    "fe80::1%en0",
    "::1",
]

# --- high-entropy family ----------------------------------------------------------
ENTROPY = [
    "q4Yk2mW8nR5pT7xV3bZ9cD1fG6hJ4kL0sA",  # mixed base64ish
    "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2",  # 46-char alternating
    "dGVoaXNpc25vdGFzZWNyZXRrZXlmb3J0ZXN0aW5ncHVycG9zZXM=",  # base64 of real text
]

# --- labelled secrets (env style) ---------------------------------------------------
LABELED = [
    "DB_PASSWORD=hunter2secure",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "MY_AUTH_TOKEN: abcdef1234567890abcdef",
]


def _assert_secret_free(label: str, sanitized: str) -> None:
    findings = scan_text(sanitized)
    assert not findings, f"{label}: outbound scan still flags content after redaction: {[f.rule for f in findings]}"


@pytest.mark.parametrize("raw", TOKENS)
def test_token_family_is_masked_and_scan_clean(raw: str) -> None:
    sanitized, _summary = redact_text(f"call failed with key {raw} at boot")
    assert raw not in sanitized
    assert "[redacted" in sanitized
    _assert_secret_free("token", sanitized)


@pytest.mark.parametrize("raw", HEADERS)
def test_cookie_and_auth_header_family_is_masked(raw: str) -> None:
    sanitized, _summary = redact_text(f"request header was {raw}")
    for secret_frag in ("session=abc123DEF456", "dXNlcm5hbWU6", "YaZ0123456789", "9f8e7d6c5b4a", "QqRrSsTtUu", "d41d8cd98f00"):
        assert secret_frag not in sanitized, f"header value fragment leaked: {secret_frag}"
    _assert_secret_free("header", sanitized)


@pytest.mark.parametrize("raw", PATHS)
def test_home_path_family_is_masked(raw: str) -> None:
    sanitized, _summary = redact_text(f"failed to open {raw} for reading")
    for username in ("fixtureuser", "alice", "bob", "carol"):
        assert username not in sanitized, f"username leaked via path: {username}"
    _assert_secret_free("path", sanitized)
    # structure survives: the file being discussed is still identifiable
    assert ".py" in sanitized or ".log" in sanitized or ".json" in sanitized or ".yaml" in sanitized or ".db" in sanitized


@pytest.mark.parametrize("raw", EMAILS)
def test_email_family_is_masked(raw: str) -> None:
    sanitized, _summary = redact_text(f"owner {raw} reported the crash")
    assert raw not in sanitized
    assert "@" not in sanitized.replace("[redacted-email]", "")
    _assert_secret_free("email", sanitized)


@pytest.mark.parametrize("raw", IPS)
def test_ip_family_is_masked(raw: str) -> None:
    sanitized, _summary = redact_text(f"connect ECONNREFUSED {raw} port 5432")
    assert raw not in sanitized
    _assert_secret_free("ip", sanitized)


@pytest.mark.parametrize("raw", ENTROPY)
def test_high_entropy_family_is_masked(raw: str) -> None:
    sanitized, _summary = redact_text(f"payload was {raw} and then it stopped")
    assert raw not in sanitized
    _assert_secret_free("entropy", sanitized)


@pytest.mark.parametrize("raw", LABELED)
def test_labeled_secret_family_is_masked(raw: str) -> None:
    sanitized, _summary = redact_text(f"env dump line: {raw}")
    for value in ("hunter2secure", "wJalrXUtnFEMI", "abcdef1234567890abcdef"):
        assert value not in sanitized
    _assert_secret_free("labeled", sanitized)


def test_username_labels_are_masked() -> None:
    sanitized, _summary = redact_text("user=fixtureuser owner=alice principal=bob.smith ran the turn")
    for name in ("fixtureuser", "alice", "bob.smith"):
        assert name not in sanitized
    _assert_secret_free("username-label", sanitized)


# --- structure preservation (negative controls) -------------------------------

def test_redaction_preserves_stack_shape_and_code_identifiers() -> None:
    raw = (
        'Traceback (most recent call last):\n'
        '  File "/Users/fixtureuser/vool/core/conductor.py", line 88, in plan\n'
        '    raise ValueError("bad span")\n'
        "ValueError: bad span"
    )
    sanitized, _summary = redact_text(raw)
    assert 'File "' in sanitized
    assert "conductor.py" in sanitized
    assert "line 88" in sanitized
    assert "in plan" in sanitized
    assert "ValueError: bad span" in sanitized
    assert "fixtureuser" not in sanitized


def test_redaction_preserves_timestamps_versions_and_times() -> None:
    raw = "2026-09-01T12:34:56 ERROR adapter failed at version 1.2.3 after 12:34:56 elapsed"
    sanitized, _summary = redact_text(raw)
    assert "2026-09-01T12:34:56" in sanitized
    assert "1.2.3" in sanitized
    assert "12:34:56" in sanitized  # a clock time is not an IPv6 address


def test_redaction_is_deterministic() -> None:
    raw = "token sk-or-v1-" + "z" * 48 + " at /Users/fixtureuser/x"
    first, summary_a = redact_text(raw)
    second, summary_b = redact_text(raw)
    assert first == second
    assert summary_a.total_replacements == summary_b.total_replacements
    assert summary_a.total_replacements >= 2


def test_redaction_summary_counts_by_rule() -> None:
    sanitized, summary = redact_text("key ghp_" + "C1d2E3f4G5h6J7k8L9m0n1O2p3Q4 plus alice@example.com")
    assert sanitized
    assert summary.rule_counts.get("secret_vendor") == 1
    assert summary.rule_counts.get("email") == 1
    assert summary.total_replacements == 2


# --- adversarial fixtures from disk ---------------------------------------------

def test_adversarial_stack_fixture_is_clean_after_redaction() -> None:
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "bug_report" / "adversarial_stack.txt"
    raw = fixture.read_text(encoding="utf-8")
    sanitized, _summary = redact_text(raw)
    for poison in (
        "sk-or-v1-",
        "ghp_",
        "fixtureuser",
        "alice@example.com",
        "192.168.13.66",
        "AKIA",
        "sup3rs3cr3t",
        "SGVsbG8",  # entropy-only base64 cookie -- no other rule family catches this
        "wJalrXUtnFEMI",
    ):
        assert poison not in sanitized, f"fixture poison {poison!r} survived redaction"
    _assert_secret_free("fixture-stack", sanitized)


def test_adversarial_log_fixture_is_clean_after_redaction() -> None:
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "bug_report" / "adversarial_log.log"
    raw = fixture.read_text(encoding="utf-8")
    sanitized, _summary = redact_text(raw)
    for poison in ("session=SGVsbG8", "10.42.7.9", "basic_auth=dXNlcj", "/home/dave/"):
        assert poison not in sanitized, f"fixture poison {poison!r} survived redaction"
    _assert_secret_free("fixture-log", sanitized)
