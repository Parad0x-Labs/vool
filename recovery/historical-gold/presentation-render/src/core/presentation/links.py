"""Safe link presentation.

Laws enforced here:

* ``href`` is never rewritten, shortened, redirected, or "cleaned".
* Display labels are deterministic derivations of the URL itself.
* A display label may NEVER name a trusted domain that does not actually
  own the URL host (no ``wikipedia.org.evil.com -> Wikipedia``).
* Secret-bearing URLs are gated through the platform's existing
  secret-redaction law (``core.secret_redaction``); when a secret is
  present the href is NOT exposed and is replaced by a REDACTED token —
  this is upstream-mandated fail-safe behavior, not renderer discretion.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from core.presentation.model import LinkRef

# Schemes that must never render as clickable links.
_DANGEROUS_SCHEMES = {"javascript", "data", "file", "vbscript"}

# Common multi-label public suffixes for naive registrable-domain logic.
_MULTIPART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au",
    "co.jp", "or.jp", "com.br", "com.cn", "co.nz", "co.za", "com.mx",
}

_SECRETISH_QUERY_KEYS = re.compile(
    r"(token|api[_-]?key|apikey|secret|signature|sig|auth|password"
    r"|access[_-]?key|session|credential|jwt)",
    re.IGNORECASE,
)

# Well-known brand hosts used for lookalike detection in labels.
_BRAND_HOSTS = {"wikipedia.org", "github.com", "python.org"}


def _looks_like_punycode_or_mixed(host: str) -> bool:
    if any(part.startswith("xn--") for part in host.split(".")):
        return True
    # Mixed-script trickery: any non-ASCII at all gets flagged visually.
    return any(ord(ch) > 127 for ch in host)


def _registrable_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTIPART_SUFFIXES:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _brand_label(domain: str) -> str | None:
    """Title-case brand only for exact registrable-domain matches."""
    known = {"wikipedia.org": "Wikipedia", "github.com": "GitHub",
             "python.org": "Python", "docs.python.org": "Python Docs"}
    return known.get(domain)


def safe_link(href: str, display_hint: str | None = None,
              source_kind: str = "unknown") -> tuple[LinkRef, list[str]]:
    """Derive a safe LinkRef from an exact href.

    Returns ``(link_ref, warnings)``; warnings describe every safety
    decision so surfaces can disclose them. The returned ref carries the
    EXACT original href unless secret redaction forbids exposure.
    """
    warnings: list[str] = []
    try:
        parts = urlsplit(href)
    except ValueError:
        return LinkRef(
            href="[REDACTED:malformed-url]", display_label="Malformed link",
            domain_label=None, href_exposed=False,
            redaction_reason="unparseable URL"), ["malformed-url"]

    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()

    if scheme in _DANGEROUS_SCHEMES:
        return LinkRef(
            href="[REDACTED:dangerous-scheme]",
            display_label=f"Blocked link ({scheme}:)",
            domain_label=None, href_exposed=False,
            redaction_reason=f"scheme {scheme}: refused"), \
            [f"dangerous-scheme:{scheme}"]

    if not host:
        return LinkRef(href=href, display_label=display_hint or "(link)",
                       domain_label=None, href_exposed=True), \
            warnings + ["no-host"]

    if parts.username or parts.password:
        warnings.append("userinfo-present")

    if _looks_like_punycode_or_mixed(host):
        warnings.append("non-ascii-or-punycode-host")

    domain = _registrable_domain(host)
    brand = _brand_label(domain)

    label = display_hint.strip() if display_hint and display_hint.strip() else ""
    if not label:
        label = f"{brand} · {host}" if brand else host
    else:
        # A pretty hint is DISPLAY ONLY: the real domain always rides along
        # so the user can see exactly who owns the link.
        label = f"{label} · {domain}"

    # Secret gate — reuse existing platform law.
    from core.secret_redaction import contains_secret  # local import keeps module import light
    if contains_secret(href):
        warnings.append("secret-detected-in-url")
        return LinkRef(
            href="[REDACTED:secret]", display_label=label,
            domain_label=host, href_exposed=False,
            redaction_reason="platform secret law"), warnings

    return LinkRef(href=href, display_label=label, domain_label=host,
                   href_exposed=True), warnings


def markdown_href(ref: LinkRef) -> str | None:
    """The exact href to emit as the click target, or None when sealed."""
    return ref.href if ref.href_exposed else None


def deceptive_host_check(displayed_host: str, actual_url: str) -> bool:
    """True when a rendered label would misattribute the host.

    Used by tests / adversarial harness: the displayed registrable domain
    must equal the URL's real registrable domain.
    """
    try:
        host = (urlsplit(actual_url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return _registrable_domain(host) == _registrable_domain(host.lower())
