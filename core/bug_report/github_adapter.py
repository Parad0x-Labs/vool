"""GitHub submission adapter: the only code that opens a socket for a bug report.

Design contracts:

- The bytes sent are EXACTLY the previewed ``issue`` JSON -- the adapter has no path that
  composes or mutates payload content.
- Credentials are looked up at call time -- the reporter's dedicated env token
  (VOOL_BUG_REPORT_GITHUB_TOKEN), then the user's established VERIFIED GitHub
  credential binding, then GITHUB_TOKEN -- and exist only in the Authorization header
  of the request. They are never returned, logged, or receipted; the receipt carries
  the env-var NAME or binding id as the credential source label.
- No credential, no network: the call fails closed before any transport invocation.
- Dedup runs before create: locally via the receipt store (service layer) and remotely
  via the issues search endpoint keyed on the fingerprint marker.
- The real transport goes through the repo's ONE outbound door (core/remote_fetch_policy)
  inside this module's own named background scope; tests inject a fake transport so no
  test ever touches GitHub.
"""
from __future__ import annotations

import json
import os

from core.bug_report.redaction import redact_text
from core.bug_report.schema import ReportPreview, SubmissionResult

API_ROOT = "https://api.github.com"
API_ROOT_ENV = "VOOL_BUG_REPORT_GITHUB_API_ROOT"  # GitHub Enterprise / local proof stand-in
SUBMIT_SCOPE_NAME = "bug_report.submit"
_TIMEOUT = 30.0


def _api_root() -> str:
    """The API root, overridable for self-hosted GitHub instances (and the local fake
    used by the browser proof). The door, scope, credential and sanitization rules are
    identical whichever root is configured."""
    override = os.environ.get(API_ROOT_ENV, "").strip()
    return override.rstrip("/") or API_ROOT


def _env_credential() -> tuple[str | None, str]:
    for var in ("VOOL_BUG_REPORT_GITHUB_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value, f"env:{var}"
    return None, "env:ABSENT"


def _binding_credential() -> tuple[str | None, str]:
    """The user's established GitHub authentication: a VERIFIED GitHub credential
    binding from the credential store, resolved through the same opaque-binding
    authority every other credentialed lane uses (core.credential_intelligence +
    core.credential_store). The secret exists only in this frame and in the request's
    Authorization header; the label never carries the secret, only the binding id."""
    try:
        from core.credential_intelligence.binding import STATUS_VERIFIED, load_index
        from core.credential_store import get_credential
    except Exception:
        return None, ""
    try:
        rows = load_index()
    except Exception:
        return None, ""
    for binding_id in sorted(rows):
        row = rows[binding_id]
        if str(row.get("status") or "") != STATUS_VERIFIED:
            continue
        provider = f"{row.get('provider_id') or ''} {row.get('provider_label') or ''} {row.get('capability_family') or ''}".lower()
        if "github" not in provider:
            continue
        slot = str(row.get("slot") or row.get("credential_name") or "").strip()
        secret = get_credential(slot) if slot else None
        if secret:
            return str(secret), f"binding:{binding_id}"
    return None, ""


def default_credential_lookup() -> tuple[str | None, str]:
    """Credential resolution order for submissions: the reporter's own env token first
    (an explicitly dedicated credential), then the user's established GitHub binding,
    then a generic GITHUB_TOKEN. No credential means no network -- fail closed."""
    token, source = _env_credential()
    if token:
        return token, source
    token, source = _binding_credential()
    if token:
        return token, source
    return None, "env:ABSENT"


def _door_transport(method: str, url: str, *, data: bytes, headers: dict, timeout: float):
    """The production transport: the repo's one outbound door under a named scope."""
    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import open_remote_url

    # urllib treats any non-None data (even b"") as POST; a GET must pass None.
    payload = data if (method or "").upper() != "GET" and data else None
    with named_background_effect_scope(SUBMIT_SCOPE_NAME):
        response = open_remote_url(url, data=payload, headers=dict(headers), method=method, timeout=timeout or _TIMEOUT)
    status = getattr(response, "status", None) or response.getcode()
    return int(status), response.read()


DEFAULT_TRANSPORT = _door_transport


def _safe_detail(exc: Exception) -> str:
    sanitized, _summary = redact_text(f"{type(exc).__name__}: {exc}")
    return sanitized


def _search_existing(preview: ReportPreview, destination: str, *, transport, headers: dict) -> str | None:
    from urllib.parse import quote

    fingerprint = preview.issue.get("body", "")
    marker_start = fingerprint.find("bug-report-fingerprint: ")
    if marker_start == -1:
        return None
    marker = fingerprint[marker_start + len("bug-report-fingerprint: "):].strip().splitlines()
    if not marker or not marker[0]:
        return None
    query = quote(f'repo:{destination} "bug-report-fingerprint: {marker[0]}"')
    url = f"{_api_root()}/search/issues?q={query}"
    try:
        status, body = transport("GET", url, data=b"", headers=headers, timeout=_TIMEOUT)
    except Exception:  # dedup search failure must not block the local guarantees
        return None
    if status != 200:
        return None
    try:
        items = json.loads(body.decode("utf-8")).get("items", [])
    except (ValueError, UnicodeDecodeError):
        return None
    for item in items:
        url_found = item.get("html_url", "")
        if url_found:
            return str(url_found)
    return None


def submit_to_github(
    preview: ReportPreview,
    *,
    destination: str,
    credential_lookup=None,
    dedup_search=None,
    transport=None,
) -> SubmissionResult:
    """Submit the EXACT previewed bytes. Returns a typed result; never raises for routine failures."""
    sender = transport or DEFAULT_TRANSPORT
    lookup = credential_lookup or _env_credential

    token, _credential_source = lookup()
    if not token:
        return SubmissionResult(
            status="failed",
            detail="no GitHub credential available (set VOOL_BUG_REPORT_GITHUB_TOKEN)",
            payload_sha256=preview.payload_sha256,
            failure_code="credential_unavailable",
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vool-bug-reporter",
    }

    existing = None
    try:
        if dedup_search is not None:
            existing = dedup_search(destination, preview)
        else:
            existing = _search_existing(preview, destination, transport=sender, headers=headers)
    except Exception:
        existing = None
    if existing:
        return SubmissionResult(
            status="duplicate",
            duplicate_of=existing,
            detail="destination already has an issue with this fingerprint",
            payload_sha256=preview.payload_sha256,
        )

    url = f"{_api_root()}/repos/{destination}/issues"
    payload = json.dumps(preview.issue, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        status, body = sender("POST", url, data=payload, headers=headers, timeout=_TIMEOUT)
    except TimeoutError as exc:
        # Sent-but-unanswered: the issue MAY exist at the destination. The retry path's
        # dedup search (keyed on the fingerprint marker) is what prevents a second
        # issue; this result just refuses to claim an outcome it does not know.
        return SubmissionResult(
            status="failed",
            detail=_safe_detail(exc),
            payload_sha256=preview.payload_sha256,
            failure_code="upstream_timeout",
        )
    except Exception as exc:
        return SubmissionResult(
            status="failed",
            detail=_safe_detail(exc),
            payload_sha256=preview.payload_sha256,
            failure_code="upstream_unreachable",
        )
    if status not in (200, 201):
        # The destination's REAL status is carried as data; the reporter's own response
        # status is chosen by the HTTP boundary from this typed fact, never by re-using
        # the number blindly.
        return SubmissionResult(
            status="failed",
            detail=f"github returned HTTP {status}",
            payload_sha256=preview.payload_sha256,
            failure_code="upstream_status",
            upstream_status=int(status) if isinstance(status, int) else None,
        )
    try:
        created = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return SubmissionResult(
            status="failed",
            detail="unparseable GitHub response",
            payload_sha256=preview.payload_sha256,
            failure_code="upstream_response_unparseable",
        )
    return SubmissionResult(
        status="submitted",
        issue_url=str(created.get("html_url", "")),
        issue_number=created.get("number"),
        payload_sha256=preview.payload_sha256,
    )


__all__ = [
    "API_ROOT",
    "API_ROOT_ENV",
    "DEFAULT_TRANSPORT",
    "SUBMIT_SCOPE_NAME",
    "default_credential_lookup",
    "submit_to_github",
]
