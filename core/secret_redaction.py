"""Secret-only redaction for anything persisted to disk (chat log, memory, summaries).

Unlike task_router.redact_text (a lossy SUMMARY redactor that also collapses whitespace and strips
URLs/emails), this masks ONLY high-confidence secrets and leaves ordinary prose intact, so it is safe
to run on the raw conversation before it is written to disk. It is high-precision on purpose: it would
rather miss an unlabelled secret than mangle a normal message. Pair it with encryption at rest — this
reduces plaintext exposure, it is not a substitute for protecting the store's key.
"""
from __future__ import annotations

import re
import threading

#: Exact-value scrub registry (credential intelligence, 2026-09-02). The pattern rules below
#: only catch keys with a KNOWN vendor shape; a custom-gateway key like ``zk9-…`` matches
#: nothing and would survive every rule. The intake boundary therefore registers the exact
#: pasted value here the moment it holds it, and ``redact_secrets`` scrubs those exact values
#: FIRST — so the conversation-log choke point, credential diagnostics, and the bug-report
#: backstop (which delegates here) cannot leak a format no pattern knows.
_EXACT_CAP = 64
_EXACT_MIN_LEN = 8
_exact_lock = threading.Lock()
_exact_secrets: dict[str, None] = {}


def register_exact_secret(value: str) -> None:
    """Register one exact secret value for scrubbing (>= 8 chars). Bounded to the most recent
    ``_EXACT_CAP`` values so a long process cannot grow an unbounded plaintext set."""
    cleaned = str(value or "").strip()
    if len(cleaned) < _EXACT_MIN_LEN:
        return
    with _exact_lock:
        _exact_secrets[cleaned] = None
        while len(_exact_secrets) > _EXACT_CAP:
            _exact_secrets.pop(next(iter(_exact_secrets)))


def clear_exact_secrets_for_tests() -> None:
    with _exact_lock:
        _exact_secrets.clear()
        _public_identifiers.clear()


# Key-SHAPED strings the runtime itself minted as public -- a confirmed transaction signature is
# 64 bytes of base58 exactly like a Solana secret key, and a shape rule cannot tell them apart.
# The wallet registers each signature it broadcasts or renders; the base58 rules then leave that
# exact value readable and keep masking every unregistered run. Bounded like the secret set.
_public_identifiers: dict[str, None] = {}


def register_public_identifier(value: str) -> None:
    """Mark one exact key-shaped value as public (a tx signature the runtime produced)."""
    cleaned = str(value or "").strip()
    if len(cleaned) < 32:
        return
    with _exact_lock:
        _public_identifiers[cleaned] = None
        while len(_public_identifiers) > _EXACT_CAP:
            _public_identifiers.pop(next(iter(_public_identifiers)))


def _is_public_identifier(value: str) -> bool:
    with _exact_lock:
        return value in _public_identifiers


def _mask_key_shaped(match: re.Match[str]) -> str:
    run = match.group(0)
    return run if _is_public_identifier(run) else "[redacted-key]"


def _exact_secrets_for_tests():
    """Read-only view for tests auditing the bounded window."""
    with _exact_lock:
        return dict(_exact_secrets)


# PEM private-key blocks (RSA/EC/OPENSSH/generic).
_PEM_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.DOTALL)
# JSON Web Tokens (header.payload.signature, base64url).
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b")
# Well-known API-key / token shapes (prefix + sufficient length to avoid false positives).
_API_KEY_RE = re.compile(
    r"\b("
    r"sk-(?:ant-)?[A-Za-z0-9_-]{16,}"        # OpenAI / Anthropic / OpenRouter (sk-or-) / DeepSeek / Moonshot
    r"|gsk_[A-Za-z0-9]{20,}"                   # Groq
    r"|AKIA[0-9A-Z]{16}"                       # AWS access key id
    r"|gh[posru]_[A-Za-z0-9]{20,}"             # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"           # Slack
    r"|AIza[A-Za-z0-9_-]{20,}"                 # Google
    r"|glpat-[A-Za-z0-9_-]{16,}"               # GitLab
    r"|npm_[A-Za-z0-9]{20,}"                   # npm
    r")\b"
)
# Long base58 (>= 64 chars) — a Solana secret key / seed. A 32-44 char base58 (a public address) is
# deliberately NOT matched, so ordinary wallet addresses in chat stay readable.
_B58_SECRET_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{64,88}\b")
# Bitcoin WIF private key: 51-52 base58 chars starting 5/K/L. Distinguishable from a Solana
# public address (32-44 chars) by length + prefix, so this does not mask ordinary addresses.
_WIF_RE = re.compile(r"\b[5KL][1-9A-HJ-NP-Za-km-z]{50,51}\b")
# "Bearer <token>" with a SPACE (Authorization headers, OAuth) — the labelled rule below only
# catches label:value / label=value, so a space-separated bearer token would otherwise persist.
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
# A credential that lives in a URL PATH instead of a header. UsePod authenticates a prepaid call
# with ``https://api.usepod.ai/proxy/<token>/v1``: the path segment after ``/proxy/`` IS the key, so
# the URL itself is a secret, and an HTTP library's own error text ("... for url: <url>"), a log
# line or a stored base URL carries it verbatim. No vendor-shape rule above can see it (a UUID has
# no prefix), and the exact-value registry only knows a token once something has resolved it. The
# segment is masked in the plain, percent-encoded and JSON-escaped spellings of the path; the
# accountless ``/proxy/x402/`` path holds no token and stays readable.
_PATH_TOKEN_SEGMENT = r"(?!x402(?![A-Za-z0-9_-]))[A-Za-z0-9][A-Za-z0-9_-]{15,127}(?![A-Za-z0-9_-])"
_PATH_TOKEN_RE = re.compile(r"(/proxy/|%2[Ff]proxy%2[Ff]|\\/proxy\\/)(" + _PATH_TOKEN_SEGMENT + r")")
# label: value / label=value where the label names a secret. Keeps the label, masks the value.
#
# The label may be part of a COMPOUND env-var style name (AWS_SECRET_ACCESS_KEY, DB_PASSWORD,
# MY_AUTH_TOKEN). A plain \b cannot find it there: underscore is a word character, so \bsecret\b
# never matches inside AWS_SECRET_ACCESS_KEY, and every env-var form leaked. Allow underscore/dash
# separated segments on either side and capture the WHOLE key name, so the output still says which
# key was masked. Values were previously caught only when they matched a known vendor shape (sk-,
# ghp_, ...), which left generic passwords fully exposed.
_SECRET_LABEL = (
    r"password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|bearer"
    r"|client[_-]?secret|private[_-]?key|seed[_-]?phrase|mnemonic|passphrase|credential"
)
_LABELED_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:" + _SECRET_LABEL + r")(?:[_-][A-Za-z0-9]+)*)"
    r"\s*[:=]\s*[\"']?([^\s\"']{4,})[\"']?"
)

_SPECIFIC = (_PEM_RE, _JWT_RE, _API_KEY_RE, _B58_SECRET_RE, _WIF_RE, _BEARER_RE, _PATH_TOKEN_RE)


def redact_url_path_tokens(text: str) -> str:
    """Mask only credentials carried in a URL path (see ``_PATH_TOKEN_RE``). Never raises.

    For call sites that serialize a URL on purpose (a diagnostic, a redirect ``Location``) and must
    keep the rest of the URL readable.
    """
    if not text:
        return text
    return _PATH_TOKEN_RE.sub(lambda match: f"{match.group(1)}[redacted-path-token]", str(text))


def redact_secrets(text: str) -> str:
    """Return `text` with high-confidence secrets masked and everything else untouched. Never raises."""
    if not text:
        return text
    value = str(text)
    with _exact_lock:
        exact = sorted(_exact_secrets, key=len, reverse=True)
    for secret in exact:
        if secret in value:
            value = value.replace(secret, "[redacted-credential]")
    value = redact_url_path_tokens(value)
    value = _PEM_RE.sub("[redacted-private-key]", value)
    value = _JWT_RE.sub("[redacted-jwt]", value)
    value = _API_KEY_RE.sub("[redacted-api-key]", value)
    value = _B58_SECRET_RE.sub(_mask_key_shaped, value)
    value = _WIF_RE.sub(_mask_key_shaped, value)
    value = _BEARER_RE.sub("Bearer [redacted]", value)
    # Run labelled last so "api_key: sk-..." collapses cleanly even after the value was masked above.
    value = _LABELED_RE.sub(lambda m: f"{m.group(1)}: [redacted]", value)
    return value


def contains_secret(text: str) -> bool:
    """True if `text` appears to contain a high-confidence secret (used to gate/flag, never to store)."""
    if not text:
        return False
    value = str(text)
    with _exact_lock:
        if any(secret in value for secret in _exact_secrets):
            return True
    for pattern in _SPECIFIC:
        for match in pattern.finditer(value):
            if pattern in (_B58_SECRET_RE, _WIF_RE) and _is_public_identifier(match.group(0)):
                continue
            return True
    return bool(_LABELED_RE.search(value))


__all__ = [
    "clear_exact_secrets_for_tests",
    "contains_secret",
    "redact_secrets",
    "redact_url_path_tokens",
    "register_exact_secret",
    "register_public_identifier",
]
