"""Selected-provider credential verification — ONE key, ONE provider, ONE request.

The verifier takes the pasted secret and exactly ONE ``ProviderDescriptor`` whose pinned
``verify_endpoint`` is the only place the key is ever sent. There is no provider list here to
loop over, no env lookup to subvert (the endpoint comes from the descriptor; the structural
test pins that this module never reads any env table), and the transport bypasses proxy env
routing (``no_proxy=True``) so even an ambient HTTP_PROXY cannot position itself between the
key and the pinned host.

Four laws, each measured broken on 2026-09-14 (validation-logs/autodetection-20260914):

* **The key rides where the provider documents it.** The descriptor names the placement — a Bearer
  header, a named header such as Brave's ``X-Subscription-Token``, or a JSON body field. Sending
  ``Authorization: Bearer`` to every provider made a valid Brave key come back ``unexpected 422``.
* **A redirect never carries the key to another origin.** urllib's default redirect handler re-sends
  every ordinary request header to whatever a ``Location`` names (a 302 delivered the key to a second
  origin). The key header now rides UNREDIRECTED, which urllib never copies onto a redirect hop, and
  the outbound door (``open_keyed_request``) refuses an answer that came from another origin
  (``redirected``) and asks a same-origin redirect target once more, with the key.
* **A 2xx is not proof by itself.** A captive portal's HTML page and another service's JSON both
  used to verify. The body must be JSON in the shape the descriptor documents.
* **An endpoint that answers without a key cannot confirm one.** An address the operator entered (the
  custom OpenAI-compatible endpoint) documents nothing about its auth, and a public model list would
  "verify" any string. Such an endpoint is asked once WITHOUT the key first: an answer in the documented
  shape is ``public_endpoint``; any other answer except 401/403 is reported as itself; in both cases the
  key is never sent. Only an endpoint that refuses the keyless request is asked with the key.

The truths stay DISTINCT because each is a different operator situation: ``verified``; ``invalid``
(the provider rejected the key — by status, or by an error code the descriptor says names the
credential); ``unauthorized`` (valid, not permitted); ``exhausted`` (no credit or quota);
``rate_limited``; ``provider_unavailable`` (408/5xx); ``endpoint_not_found`` (404 — a wrong base
URL); ``timeout``; ``network_unavailable``; ``redirected``; ``malformed_response``;
``unexpected_schema``; ``public_endpoint``; ``refused`` (nothing was sent); ``unexpected`` (anything
else, named by its status and the provider's own error code — never guessed into "invalid").
Throttling, outage, timeout, unreachable and the rest of ``INCONCLUSIVE_STATUSES`` say nothing about
the key and are never reported as a bad one. Response BODIES are never copied into ``detail`` (a body
can echo the key): details are fixed safe phrases, plus at most the provider's short error-code token.

The request rides the ONE outbound door (``remote_fetch_policy.open_remote_url``) inside the
named background scope ``credential.verify``, so the per-turn veto and effect accounting
apply, and the ledger records the host and outcome — never the key.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse, urlsplit

from core.credential_intelligence.provider_registry import ProviderDescriptor
from core.remote_fetch_policy import CredentialRedirectRefusedError, RemoteFetchRefusedError

STATUS_VERIFIED = "verified"
STATUS_INVALID = "invalid"
STATUS_EXHAUSTED = "exhausted"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_UNAUTHORIZED = "unauthorized"
STATUS_NETWORK_UNAVAILABLE = "network_unavailable"
STATUS_REFUSED = "refused"
STATUS_UNEXPECTED = "unexpected"
STATUS_TIMEOUT = "timeout"
STATUS_PROVIDER_UNAVAILABLE = "provider_unavailable"
STATUS_ENDPOINT_NOT_FOUND = "endpoint_not_found"
STATUS_REDIRECTED = "redirected"
STATUS_MALFORMED_RESPONSE = "malformed_response"
STATUS_UNEXPECTED_SCHEMA = "unexpected_schema"
STATUS_PUBLIC_ENDPOINT = "public_endpoint"

#: Outcomes that carry NO evidence about the key itself: the provider, the path or the policy was
#: what answered. None of them may be surfaced as a rejected key or withdraw a working lane.
INCONCLUSIVE_STATUSES = frozenset({
    STATUS_RATE_LIMITED,
    STATUS_NETWORK_UNAVAILABLE,
    STATUS_TIMEOUT,
    STATUS_PROVIDER_UNAVAILABLE,
    STATUS_ENDPOINT_NOT_FOUND,
    STATUS_REDIRECTED,
    STATUS_MALFORMED_RESPONSE,
    STATUS_UNEXPECTED_SCHEMA,
    STATUS_PUBLIC_ENDPOINT,
    STATUS_REFUSED,
    STATUS_UNEXPECTED,
})

VERIFY_TIMEOUT_S = 10.0
#: A verification answer is a key record or a model/result list; a body past this is not one.
_MAX_BODY_BYTES = 2_000_000
_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: The keyless answers after which an entered endpoint is asked with the key: it demands one.
_KEY_DEMANDED_STATUSES = (401, 403)

_details = {
    STATUS_VERIFIED: "authorized",
    STATUS_INVALID: "key rejected by provider",
    STATUS_EXHAUSTED: "account balance or quota exhausted",
    STATUS_RATE_LIMITED: "provider throttled the request",
    STATUS_UNAUTHORIZED: "key valid but not permitted this request",
    STATUS_NETWORK_UNAVAILABLE: "provider unreachable",
    STATUS_REFUSED: "verification refused before any request",
    STATUS_UNEXPECTED: "unexpected provider response",
    STATUS_TIMEOUT: "provider did not answer in time",
    STATUS_PROVIDER_UNAVAILABLE: "provider reported an outage",
    STATUS_ENDPOINT_NOT_FOUND: "no verification endpoint at this address",
    STATUS_REDIRECTED: "provider redirected to another origin; the key was not sent there",
    STATUS_MALFORMED_RESPONSE: "the answer was not an API response",
    STATUS_UNEXPECTED_SCHEMA: "the answer does not have this provider's documented shape",
    STATUS_PUBLIC_ENDPOINT: "the endpoint answers without a key, so it cannot confirm one; the key was not sent",
}

_MISSING = object()


@dataclass(frozen=True)
class VerificationOutcome:
    """What the ONE provider said about the ONE key. No key material anywhere."""

    status: str
    provider_id: str
    http_status: int | None
    detail: str
    account: str
    checked_at: str
    endpoint_host: str
    #: "ok" | "exhausted" | "unknown" — the account's credit, kept apart from the key's validity.
    account_state: str = "unknown"
    #: Public billing metadata from a verified answer (label, remaining limit, free tier …).
    account_detail: dict[str, object] = field(default_factory=dict)
    retry_after_s: float | None = None
    #: The provider's own short error-code token when it sent one (never its message).
    provider_error_code: str = ""
    #: The origin a refused redirect pointed at (scheme://host[:port] only, never a path).
    redirect_origin: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "provider_id": self.provider_id,
            "http_status": self.http_status,
            "detail": self.detail,
            "account": self.account,
            "checked_at": self.checked_at,
            "endpoint_host": self.endpoint_host,
            "account_state": self.account_state,
            "account_detail": dict(self.account_detail),
            "retry_after_s": self.retry_after_s,
            "provider_error_code": self.provider_error_code,
            "redirect_origin": self.redirect_origin,
        }


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _outcome(status: str, descriptor: ProviderDescriptor, http_status: int | None, host: str, *,
             account: str = "", detail: str = "", **extra: object) -> VerificationOutcome:
    return VerificationOutcome(
        status=status, provider_id=descriptor.provider_id, http_status=http_status,
        detail=detail or _details.get(status, status), account=str(account or ""),
        checked_at=_utcnow(), endpoint_host=host, **extra,  # type: ignore[arg-type]
    )


def _host_of(url: str) -> str:
    try:
        return str(urlsplit(str(url)).hostname or "")
    except Exception:
        return ""


def _origin_text(url: str) -> str:
    """scheme://host[:port] — no path or query, which may carry a token."""
    try:
        parts = urlsplit(str(url or ""))
        if not parts.scheme or not parts.hostname:
            return ""
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}"
    except Exception:
        return ""


def _loopback(host: str) -> bool:
    import ipaddress

    h = str(host or "").strip().lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _safe_transport(url: str) -> bool:
    """Same gate as the completion adapter: https anywhere, or plain http ONLY to a genuine
    loopback host (a local model server). ``http://127.0.0.1.evil.com`` fails this."""
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    scheme = (parsed.scheme or "").lower()
    if scheme == "https":
        return True
    return scheme == "http" and _loopback(parsed.hostname or "")


def _merge_query(url: str, params: dict[str, str]) -> str:
    if not params:
        return url
    from urllib.parse import urlencode

    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def _dig(payload: object, path: tuple[str, ...]) -> object:
    node = payload
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return _MISSING
    return node


def _shape_ok(descriptor: ProviderDescriptor, payload: object) -> bool:
    if not isinstance(payload, (dict, list)):
        return False
    if not descriptor.response_path:
        return True
    for path in (descriptor.response_path, descriptor.fallback_response_path):
        if not path:
            continue
        node = _dig(payload, path)
        if node is _MISSING:
            continue
        if descriptor.response_kind == "list":
            if isinstance(node, list):
                return True
        elif descriptor.response_kind == "object":
            if isinstance(node, dict):
                return True
        elif node is not None:
            return True
    return False


def _account_id(payload: object, descriptor: ProviderDescriptor) -> str:
    if not isinstance(payload, dict):
        return ""
    for path in descriptor.account_paths:
        node = _dig(payload, path)
        if isinstance(node, (str, int)) and not isinstance(node, bool) and str(node).strip():
            return str(node).strip()
    return ""


def _account(descriptor: ProviderDescriptor, payload: object) -> tuple[str, dict[str, object]]:
    """Account credit state and public billing metadata, read only where the descriptor says."""
    detail: dict[str, object] = {}
    if not isinstance(payload, dict):
        return "unknown", detail
    for name, path in descriptor.account_fields.items():
        node = _dig(payload, tuple(path))
        if node is _MISSING:
            continue
        if node is None or isinstance(node, (bool, int, float)):
            detail[name] = node
        elif isinstance(node, str):
            detail[name] = node.strip()[:80]
    state = "unknown"
    if descriptor.exhausted_when_zero:
        node = _dig(payload, descriptor.exhausted_when_zero)
        if node is None:
            state = "ok"  # no limit is set on this key
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            state = "exhausted" if node <= 0 else "ok"
    return state, detail


def provider_error_code_from_body(body: bytes) -> str:
    """The provider's short error-code token from a JSON error body (``error.code``, then
    ``error.type``, then a top-level ``code``), or "". Never its message: a message can echo input."""
    try:
        payload = json.loads(bytes(body or b"").decode("utf-8", errors="replace"))
    except Exception:
        return ""
    candidates: list[object] = []
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            candidates.extend((error.get("code"), error.get("type")))
        candidates.append(payload.get("code"))
    for candidate in candidates:
        if isinstance(candidate, str) and _ERROR_CODE_RE.match(candidate.strip()):
            return candidate.strip()
    return ""


def _retry_after(headers: object) -> float | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    try:
        seconds = float(str(getter("Retry-After") or "").strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def is_timeout_error(exc: BaseException) -> bool:
    """True when a transport failure was the bound expiring, not a refused or dead connection."""
    import socket

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return "timed out" in str(reason if reason is not None else exc).lower()


def classify_verification_response(descriptor: ProviderDescriptor, *, status: int, body: bytes,
                                   headers: object = None, host: str = "") -> VerificationOutcome:
    """What one provider answer means for one key. Shared with the stored-key Test probe so the
    two doors can never disagree about the same answer."""
    if 200 <= status < 300:
        try:
            payload = json.loads(bytes(body or b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _outcome(STATUS_MALFORMED_RESPONSE, descriptor, status, host)
        if not _shape_ok(descriptor, payload):
            return _outcome(STATUS_UNEXPECTED_SCHEMA, descriptor, status, host)
        account_state, account_detail = _account(descriptor, payload)
        return _outcome(
            STATUS_VERIFIED, descriptor, status, host, account=_account_id(payload, descriptor),
            account_state=account_state, account_detail=account_detail,
        )
    if 300 <= status < 400:
        # A redirect urllib declined to follow (a POST answered 307/308): nothing was re-sent.
        location = str(getattr(headers, "get", lambda _name: "")("Location") or "")
        return _outcome(STATUS_REDIRECTED, descriptor, status, host, redirect_origin=_origin_text(location))
    code = provider_error_code_from_body(body)
    if code and code in descriptor.invalid_error_codes:
        return _outcome(STATUS_INVALID, descriptor, status, host, provider_error_code=code)
    if status in descriptor.invalid_statuses:
        return _outcome(STATUS_INVALID, descriptor, status, host, provider_error_code=code)
    if status in descriptor.unauthorized_statuses:
        return _outcome(STATUS_UNAUTHORIZED, descriptor, status, host, provider_error_code=code)
    if status in descriptor.exhausted_statuses:
        return _outcome(STATUS_EXHAUSTED, descriptor, status, host, provider_error_code=code)
    if status in descriptor.rate_limit_statuses:
        return _outcome(STATUS_RATE_LIMITED, descriptor, status, host, provider_error_code=code,
                        retry_after_s=_retry_after(headers))
    if status in descriptor.not_found_statuses:
        return _outcome(STATUS_ENDPOINT_NOT_FOUND, descriptor, status, host, provider_error_code=code)
    if status in descriptor.unavailable_statuses or 500 <= status < 600:
        return _outcome(STATUS_PROVIDER_UNAVAILABLE, descriptor, status, host, provider_error_code=code,
                        retry_after_s=_retry_after(headers))
    detail = f"unexpected provider status {status}" + (f" ({code})" if code else "")
    return _outcome(STATUS_UNEXPECTED, descriptor, status, host, detail=detail, provider_error_code=code)


#: A path-safe credential segment. A slash, dot, percent sign, whitespace or query/fragment marker in a
#: paste would steer a URL-path credential to another path, so such a value is never placed.
_PATH_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{15,127}$")


def _credential_header_names(descriptor: ProviderDescriptor) -> tuple[str, ...]:
    """The headers that hold the key — none when it rides the body or the URL path. Either way the
    request is marked as carrying a key, so the door judges any redirect it met."""
    style = str(descriptor.auth_style or "bearer").strip().lower()
    if style == "bearer":
        return ("Authorization",)
    if style == "header" and str(descriptor.auth_name or "").strip():
        return (str(descriptor.auth_name).strip(),)
    return ()


def _place_credential(key: str | None, descriptor: ProviderDescriptor) -> tuple[str, dict[str, str], bytes | None] | None:
    """Build (url, headers, body) with the key where the descriptor documents it, or None for a
    placement this verifier does not support. ``key=None`` builds the same request with no key in it,
    except for a URL-path credential: without its token that request is another path, so it is never
    built, and only a path-safe token is ever placed into the pinned template."""
    method = str(descriptor.verify_method or "GET").upper()
    style = str(descriptor.auth_style or "bearer").strip().lower()
    name = str(descriptor.auth_name or "").strip()
    if style == "url_path_token":
        template = str(descriptor.verify_endpoint or "")
        if key is None or method != "GET" or "{credential}" not in template or not _PATH_TOKEN_RE.fullmatch(key):
            return None
        path_headers = {"Accept": "application/json"}
        path_headers.update(descriptor.extra_headers)
        return _merge_query(template.replace("{credential}", key), dict(descriptor.verify_query)), path_headers, None
    if not (style == "bearer" or (style == "header" and name) or (style == "body" and name and method == "POST")):
        return None
    headers = {"Accept": "application/json"}
    body = dict(descriptor.verify_body) if descriptor.verify_body else None
    if key is not None:
        if style == "bearer":
            headers["Authorization"] = f"Bearer {key}"
        elif style == "header":
            headers[name] = key
        else:
            body = {**(body or {}), name: key}
    headers.update(descriptor.extra_headers)
    data = None
    if body is not None and method == "POST":
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    return _merge_query(descriptor.verify_endpoint, dict(descriptor.verify_query)), headers, data


def _ask(placed: tuple[str, dict[str, str], bytes | None], descriptor: ProviderDescriptor, timeout_s: float,
         host: str, *, keyed: bool) -> tuple[int, bytes, object] | VerificationOutcome:
    """One request through the outbound door: (status, body, response headers), or the outcome of a
    request that produced no provider answer."""
    import urllib.error

    # Resolved at call time so a test (or a future policy wrapper) that replaces
    # core.remote_fetch_policy.open_remote_url governs this request too — the module-level
    # name would keep the import-time binding instead.
    from core.remote_fetch_policy import open_remote, open_remote_url

    url, headers, data = placed
    path_token = str(descriptor.auth_style or "").strip().lower() == "url_path_token"
    try:
        if path_token:
            # The URL itself carries the credential: a redirect is returned, never followed (it is
            # judged ``redirected`` below with only its origin kept), and no ambient proxy sits
            # between the token and the pinned origin.
            import urllib.request

            response = open_remote(
                urllib.request.Request(url, data=data, headers=headers, method=descriptor.verify_method),
                timeout=timeout_s,
                no_proxy=True,
                provider_id=descriptor.provider_id,
                keyed_or_keyless="keyed",
                redirect_policy="refuse",
            )
        else:
            response = open_remote_url(
                url,
                data=data,
                headers=headers,
                method=descriptor.verify_method,
                timeout=timeout_s,
                no_proxy=True,
                provider_id=descriptor.provider_id,
                keyed_or_keyless="keyed" if keyed else "keyless",
                # Judged as a key-bearing exchange either way: a keyless first ask is still refused an
                # answer from another origin.
                credential_headers=_credential_header_names(descriptor) if keyed else (),
            )
    except RemoteFetchRefusedError:
        return _outcome(STATUS_REFUSED, descriptor, None, host, detail="the runtime did not permit this request")
    except CredentialRedirectRefusedError as exc:
        return _outcome(STATUS_REDIRECTED, descriptor, None, host, redirect_origin=exc.redirect_origin)
    except Exception as exc:
        # urllib raises HTTPError for every non-2xx status — that exception IS the provider's
        # answer (it carries the status and the body), not a transport failure. A bound that
        # expired is a timeout, not "unreachable".
        if not isinstance(exc, urllib.error.HTTPError):
            return _outcome(STATUS_TIMEOUT if is_timeout_error(exc) else STATUS_NETWORK_UNAVAILABLE, descriptor, None, host)
        response = exc
    status = int(getattr(response, "status", None) or getattr(response, "code", None) or 0)
    try:
        body = response.read(_MAX_BODY_BYTES) or b""
    except Exception as exc:
        if 200 <= status < 300:
            return _outcome(STATUS_TIMEOUT if is_timeout_error(exc) else STATUS_NETWORK_UNAVAILABLE, descriptor, None, host)
        body = b""
    return status, body, getattr(response, "headers", None)


def verify_provider_credential(secret: str, descriptor: ProviderDescriptor,
                               *, timeout_s: float = VERIFY_TIMEOUT_S,
                               scope: str = "credential.verify") -> VerificationOutcome:
    """Verify `secret` against `descriptor`'s pinned endpoint — the one provider the operator
    selected. One request with the key (asked once more only when the provider redirects within its
    own origin), preceded for an operator-entered endpoint by the same request without it; never tries
    any other provider; nothing env-derived is consulted for the destination. Only a ``verified``
    outcome is persistence evidence. ``scope`` names the effect scope the request reports itself
    under, so the stored-key Test probe can run this same policy under its own name."""
    from core.effect_gateway import named_background_effect_scope

    key = str(secret or "").strip()
    host = _host_of(descriptor.verify_endpoint)
    if not key:
        return _outcome(STATUS_REFUSED, descriptor, None, host, detail="no key to verify")
    if not descriptor.verify_endpoint:
        return _outcome(STATUS_REFUSED, descriptor, None, host, detail="no verification endpoint configured")
    if not _safe_transport(descriptor.verify_endpoint):
        return _outcome(STATUS_REFUSED, descriptor, None, host,
                        detail="insecure transport: a key is only sent over https or to this machine")
    placed = _place_credential(key, descriptor)
    keyless = _place_credential(None, descriptor) if descriptor.user_endpoint else None
    if str(descriptor.auth_style or "").strip().lower() == "url_path_token":
        if descriptor.user_endpoint:
            # An operator endpoint is asked WITHOUT the key first, and a path token has no keyless
            # form of its request: that law cannot be kept, so nothing is sent.
            return _outcome(STATUS_REFUSED, descriptor, None, host,
                            detail="a URL-path credential cannot be verified at an endpoint that must first be asked without it")
        if placed is None:
            return _outcome(STATUS_REFUSED, descriptor, None, host,
                            detail="the credential is not a path-safe token for this provider's URL path; nothing was sent")
        from core.secret_redaction import register_exact_secret

        register_exact_secret(key)  # the URL that carries it is scrubbed from every ledger and error
    elif placed is None or (descriptor.user_endpoint and keyless is None):
        return _outcome(STATUS_REFUSED, descriptor, None, host,
                        detail=f"unsupported key placement {descriptor.auth_style!r}")

    with named_background_effect_scope(scope):
        if descriptor.user_endpoint:
            first = _ask(keyless, descriptor, timeout_s, host, keyed=False)
            if isinstance(first, VerificationOutcome):
                return first
            status, body, headers = first
            if status not in _KEY_DEMANDED_STATUSES:
                judged = classify_verification_response(descriptor, status=status, body=body, headers=headers, host=host)
                if judged.status == STATUS_VERIFIED:
                    return _outcome(STATUS_PUBLIC_ENDPOINT, descriptor, status, host)
                if judged.status in (STATUS_INVALID, STATUS_UNAUTHORIZED, STATUS_EXHAUSTED):
                    # A verdict about a key that no request carried is not a verdict about this key.
                    return _outcome(STATUS_UNEXPECTED, descriptor, status, host,
                                    detail=f"unexpected provider status {status} without a key",
                                    provider_error_code=judged.provider_error_code)
                return judged
        answer = _ask(placed, descriptor, timeout_s, host, keyed=True)
    if isinstance(answer, VerificationOutcome):
        return answer
    status, body, headers = answer
    return classify_verification_response(descriptor, status=status, body=body, headers=headers, host=host)


__all__ = [
    "INCONCLUSIVE_STATUSES",
    "STATUS_ENDPOINT_NOT_FOUND",
    "STATUS_EXHAUSTED",
    "STATUS_INVALID",
    "STATUS_MALFORMED_RESPONSE",
    "STATUS_NETWORK_UNAVAILABLE",
    "STATUS_PROVIDER_UNAVAILABLE",
    "STATUS_PUBLIC_ENDPOINT",
    "STATUS_RATE_LIMITED",
    "STATUS_REDIRECTED",
    "STATUS_REFUSED",
    "STATUS_TIMEOUT",
    "STATUS_UNAUTHORIZED",
    "STATUS_UNEXPECTED",
    "STATUS_UNEXPECTED_SCHEMA",
    "STATUS_VERIFIED",
    "VERIFY_TIMEOUT_S",
    "VerificationOutcome",
    "classify_verification_response",
    "is_timeout_error",
    "provider_error_code_from_body",
    "verify_provider_credential",
]
