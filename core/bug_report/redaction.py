"""Deterministic, model-free redaction for bug-report material.

This is the pipeline's FIRST sanitization stage and it is deliberately not a model: the
same input must produce the same output every time, on every machine, auditable by regex.
Layering:

1. bug-report-specific families the objective names: cookies, auth headers, home paths /
   usernames, emails, IPs, high-entropy runs, labelled secrets (including the ``X is Y``
   prose form), hex blobs.
2. ``core.secret_redaction.redact_secrets`` -- the repo's existing high-confidence secret
   masker (vendor token shapes, JWTs, PEM blocks, Bearer values) -- run as a counted
   backstop so nothing that library catches can survive either.

Redaction is privacy-first: it would rather over-mask than leak. Structure that
reproduction needs (file names, function names, line numbers, timestamps, log levels,
lane/tool/model ids) is preserved because the rules only bind to value shapes, not to
code identifiers.
"""
from __future__ import annotations

import math
import re
import unicodedata

from core.bug_report.schema import RedactionSummary
from core.secret_redaction import redact_personal_secrets as _backstop_redact_personal
from core.secret_redaction import redact_secrets as _backstop_redact

# --- header families (whole-value masking; label kept so the report stays readable).
# The (?![ \t]*\[redacted) guard after the colon keeps matching idempotent: an already-
# masked "Cookie: [redacted]" must not match again (the scanner shares these patterns),
# and placing it directly after the colon closes the zero-width backtracking path.

_COOKIE_HEADER_RE = re.compile(
    r"(?i)(?<![a-z0-9-])(set-cookie|cookie)\s*:(?![ \t]*\[redacted)\s*[^\r\n]+"
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)(?<![a-z0-9-])(authorization|proxy-authorization|x-api-key|api-key|x-auth-token|x-session-token)"
    r"\s*:(?![ \t]*\[redacted)\s*[^\r\n]+"
)

# --- home paths / usernames -------------------------------------------------------------
# /Users/<name>, /home/<name>, C:\Users\<name>, C:/Users/<name>: mask just the username
# segment; the project-relative remainder is what reproduction needs.
_HOME_SEGMENT_RE = re.compile(
    r"(?i)([a-z]:)?[\\/](users|home)[\\/][A-Za-z0-9._-]+"
)
_TILDE_HOME_RE = re.compile(r"(?<![A-Za-z0-9])~(?=/|\\|\s|$)")
_HOME_ENV_RE = re.compile(r"(?<![A-Za-z0-9])\$HOME\b", re.IGNORECASE)

# --- usernames in labelled positions -------------------------------------------------------
_USERNAME_LABEL_RE = re.compile(
    r"(?i)(?<![a-z0-9_])((?:[a-z0-9]+[_-])*(?:user|owner|principal|username|login|runas|account)(?:[_-][a-z0-9]+)*)"
    r"\s*[=:]\s*[\"']?([A-Za-z0-9._@+-]{2,64})[\"']?"
)

# --- emails ----------------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w-])")

# --- IPs ---------------------------------------------------------------------------------------
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# IPv6: require `::`, or a hextet chain with at least one non-decimal hex group. A clock
# time like 12:34:56 is all-decimal with no `::` and must NOT be masked.
_IPV6_CANDIDATE_RE = re.compile(r"(?<![\w:.])([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?:%[A-Za-z0-9]+)?(?![\w:.])")

# --- labelled secrets (bug-report layer: adds session/cookie/auth labels and "X is Y") ----------
_BR_SECRET_LABEL = (
    r"password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|bearer"
    r"|client[_-]?secret|private[_-]?key|passphrase|credential|session|cookie|csrf|basic[_-]?auth"
)
_BR_LABELED_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:" + _BR_SECRET_LABEL + r")(?:[_-][A-Za-z0-9]+)*)"
    r"\s*[:=]\s*[\"']?(?!\[redacted)([^\s\"']{4,})[\"']?"
)
_BR_LABELED_IS_RE = re.compile(
    r"(?i)\b(password|secret|passphrase|token|api[_-]?key)\s+is\s+[\"']?([A-Za-z0-9+=/_-]{4,})[\"']?(?![\w-])"
)
# AWS access-key ids longer than the canonical 16: core.secret_redaction pins
# AKIA[0-9A-Z]{16}\b exactly, so a 17+ char run (real keys get pasted with padding or
# transcription drift) falls through. Privacy-first: catch the longer runs here too.
_AKIA_LONG_RE = re.compile(r"\bAKIA[0-9A-Z]{16,}\b")

# --- personal financial / contact identifiers (a user's own words, pasted into a report) ----------
# Card numbers: 13-19 digits, optionally grouped by single spaces or dashes; only a run that
# passes the Luhn check (the wallet's own card rule) is masked, so ids and counts survive.
_CARD_CANDIDATE_RE = re.compile(r"(?<![\w-])\d(?:[ -]?\d){12,18}(?![\w-])")
# PIN / passcode / CVV / one-time code in prose: "my PIN is 4433", "cvv: 123", "OTP 482913".
_PIN_LABEL = (
    r"pin(?:\s*code)?|passcode|pass\s*code|cvv2?|cvc2?|csc|security\s+code|otp"
    r"|one[- ]time\s+(?:code|password|pin)|verification\s+code|2fa\s+code|sms\s+code"
)
_PIN_LABELED_RE = re.compile(
    r"(?i)\b(" + _PIN_LABEL + r")\b(\s*(?:is|was|=|:|#|-)?\s*)(?!\[redacted)([A-Za-z0-9]{3,12})\b"
)
# Phone numbers: an international "+" number (8-15 digits with spaces, dashes, dots or
# brackets), or a labelled local number ("phone 612 34567", "call me on 0612-345-67").
_PHONE_INTL_RE = re.compile(r"(?<![\w+])\+\d[\d .()-]{6,22}\d(?![\d])")
_PHONE_LABELED_RE = re.compile(
    r"(?i)\b(phone|tel|telephone|mobile|cell|whatsapp|signal|call\s+me(?:\s+(?:on|at))?|text\s+me(?:\s+(?:on|at))?"
    r"|my\s+number(?:\s+is)?)\b(\s*[:#=-]?\s*)(\(?\d[\d .()-]{5,20}\d)(?![\d])"
)
# IBAN: country code, two check digits, 11-30 alphanumerics (optionally in groups of four);
# masked only when the ISO 13616 mod-97 check holds.
_IBAN_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}(?![A-Za-z0-9])")
# Wallet addresses: EVM, Bitcoin bech32, and base58 (Solana / legacy Bitcoin) shapes.
_EVM_ADDRESS_RE = re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_BECH32_ADDRESS_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:bc|tb|ltc)1[02-9ac-hj-np-z]{25,87}(?![A-Za-z0-9])")
_BASE58_ADDRESS_RE = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])")


def _digits_of(text: str) -> str:
    return re.sub(r"\D", "", text)


def _is_card_number(match_text: str) -> bool:
    digits = _digits_of(match_text)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _is_iban(match_text: str) -> bool:
    compact = match_text.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    try:
        numeric = "".join(str(int(ch, 36)) for ch in rearranged)
        return int(numeric) % 97 == 1
    except ValueError:
        return False


def _is_intl_phone(match_text: str) -> bool:
    return 8 <= len(_digits_of(match_text)) <= 15


def _is_labeled_phone(match_text: str) -> bool:
    return 6 <= len(_digits_of(match_text)) <= 15


def _is_base58_address(match_text: str) -> bool:
    return (
        any(c.isdigit() for c in match_text)
        and any(c.isupper() for c in match_text)
        and any(c.islower() for c in match_text)
    )


# --- entropy / hex blobs --------------------------------------------------------------------------
_ENTROPY_RUN_RE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{20,}(?![A-Za-z0-9+/=_-])")
_HEX_BLOB_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")

_ENTROPY_THRESHOLD = 3.8  # bits/char; random hex ~= 4.0, random base64 ~= 6.0, English words < 3.5

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(text)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _looks_like_secret_run(run: str) -> bool:
    """A long run is masked when it mixes letters and digits and looks random, or is a hex blob."""
    if _HEX_BLOB_RE.fullmatch(run):
        return True
    has_alpha = any(c.isalpha() for c in run)
    has_digit = any(c.isdigit() for c in run)
    if not (has_alpha and has_digit):
        return False
    return _shannon_entropy(run) >= _ENTROPY_THRESHOLD


def _is_v6_candidate(match_text: str) -> bool:
    text = match_text
    if "::" in text:
        return True
    groups = [g for g in text.split(":") if g]
    if len(groups) < 3:
        return False
    return any(any(ch in "abcdefABCDEF" for ch in g) for g in groups)


def normalize_text(text: str) -> str:
    """NFKC-normalize and strip control characters, ANSI escapes, and bidi overrides."""
    value = unicodedata.normalize("NFKC", str(text))
    value = _BIDI_RE.sub("", value)
    value = _ANSI_ESCAPE_RE.sub("", value)
    value = _CONTROL_CHARS_RE.sub(" ", value)
    return value


def redact_text(text: str) -> tuple[str, RedactionSummary]:
    """Return (sanitized text, per-rule replacement counts). Deterministic; never raises."""
    counts: dict[str, int] = {}
    total = 0

    def bump(rule: str, by: int) -> None:
        nonlocal total
        if by:
            counts[rule] = counts.get(rule, 0) + by
            total += by

    def apply(rule: str, pattern: re.Pattern, replacement) -> None:
        nonlocal value
        bump(rule, len(pattern.findall(value)))
        value = pattern.sub(replacement, value)

    value = normalize_text(text)
    if not value:
        return value, RedactionSummary()

    # Layer 0 -- the repo-wide high-confidence vendor secret masker, FIRST, so precise
    # token shapes (ghp_/sk-/AKIA/JWT/PEM/Bearer) are attributed to secret_vendor and
    # never fall through to the coarser entropy rule. Plain-word card numbers, PINs and
    # passwords are left to this module's own rules below, so each is counted under its own
    # name; the repo-wide personal masker runs again as the last pass.
    pre_markers = value.count("[redacted")
    backstopped = _backstop_redact(value, personal=False)
    if backstopped != value:
        bump("secret_vendor", backstopped.count("[redacted") - pre_markers)
        value = backstopped

    def _header_sub(m: re.Match) -> str:
        return f"{m.group(1)}: [redacted]"

    apply("cookie_header", _COOKIE_HEADER_RE, _header_sub)
    apply("auth_header", _AUTH_HEADER_RE, _header_sub)

    def _home_sub(m: re.Match) -> str:
        drive = m.group(1) or ""
        return f"{drive}/{m.group(2)}/[redacted-user]"

    apply("home_path", _HOME_SEGMENT_RE, _home_sub)
    apply("home_path", _TILDE_HOME_RE, "[redacted-home]")
    apply("home_path", _HOME_ENV_RE, "[redacted-home]")

    def _username_sub(m: re.Match) -> str:
        return f"{m.group(1)}: [redacted-user]"

    apply("username_label", _USERNAME_LABEL_RE, _username_sub)
    apply("email", _EMAIL_RE, "[redacted-email]")

    def _ipv6_sub(m: re.Match) -> str:
        return "[redacted-ip]" if _is_v6_candidate(m.group(0)) else m.group(0)

    apply("ip_v4", _IPV4_RE, "[redacted-ip]")
    v6_hits = sum(1 for m in _IPV6_CANDIDATE_RE.finditer(value) if _is_v6_candidate(m.group(0)))
    value = _IPV6_CANDIDATE_RE.sub(_ipv6_sub, value)
    bump("ip_v6", v6_hits)

    def _labeled_sub(m: re.Match) -> str:
        return f"{m.group(1)}: [redacted]"

    def _labeled_is_sub(m: re.Match) -> str:
        return f"{m.group(1)} is [redacted]"

    # Personal financial / contact identifiers BEFORE the labelled-secret family, so "PIN is
    # 4433" is attributed to its own rule and a card number never reaches the entropy rule.
    def _conditional(rule: str, pattern: re.Pattern, check, replacement) -> None:
        nonlocal value
        hits = 0

        def _sub(m: re.Match) -> str:
            nonlocal hits
            if not check(m):
                return m.group(0)
            hits += 1
            return replacement(m) if callable(replacement) else replacement

        value = pattern.sub(_sub, value)
        bump(rule, hits)

    _conditional("wallet_address", _EVM_ADDRESS_RE, lambda m: True, "[redacted-address]")
    _conditional("card_number", _CARD_CANDIDATE_RE, lambda m: _is_card_number(m.group(0)), "[redacted-card]")
    _conditional("iban", _IBAN_CANDIDATE_RE, lambda m: _is_iban(m.group(0)), "[redacted-iban]")
    # The value must carry a digit: "the pin is 4433" is a PIN, "the pin is set" is prose.
    _conditional(
        "pin_code", _PIN_LABELED_RE, lambda m: any(c.isdigit() for c in m.group(3)),
        lambda m: f"{m.group(1)}{m.group(2) or ' '}[redacted-pin]",
    )
    _conditional("phone", _PHONE_INTL_RE, lambda m: _is_intl_phone(m.group(0)), "[redacted-phone]")
    _conditional(
        "phone", _PHONE_LABELED_RE, lambda m: _is_labeled_phone(m.group(3)),
        lambda m: f"{m.group(1)}{m.group(2) or ' '}[redacted-phone]",
    )
    _conditional("wallet_address", _BECH32_ADDRESS_RE, lambda m: True, "[redacted-address]")
    _conditional("wallet_address", _BASE58_ADDRESS_RE, lambda m: _is_base58_address(m.group(0)), "[redacted-address]")

    apply("labeled_secret", _BR_LABELED_RE, _labeled_sub)
    apply("labeled_secret", _BR_LABELED_IS_RE, _labeled_is_sub)
    apply("secret_vendor", _AKIA_LONG_RE, "[redacted-api-key]")

    # Backstop: anything the repo-wide personal-secret masker still sees (a card, PIN or
    # password worded in a way the rules above do not read) is masked here, never shipped.
    pre_markers = value.count("[redacted")
    backstopped = _backstop_redact_personal(value)
    if backstopped != value:
        bump("personal_secret", max(1, backstopped.count("[redacted") - pre_markers))
        value = backstopped

    def _entropy_sub(m: re.Match) -> str:
        run = m.group(0)
        return "[redacted-secret]" if _looks_like_secret_run(run) else run

    entropy_hits = sum(1 for m in _ENTROPY_RUN_RE.finditer(value) if _looks_like_secret_run(m.group(0)))
    value = _ENTROPY_RUN_RE.sub(_entropy_sub, value)
    bump("high_entropy", entropy_hits)

    return value, RedactionSummary(rule_counts=counts, total_replacements=total)


__all__ = ["normalize_text", "redact_text"]
