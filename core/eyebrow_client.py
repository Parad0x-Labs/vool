"""Bounded Eyebrow v1 client. Fixed origin, no redirects, no automatic paid retries."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

KEY_NAME = "security.eyebrow"
API_ORIGIN = "https://api.eyebrow.cc"
CLIENT_USER_AGENT = "VOOL/0.6 (+https://vool.dev)"
MAX_RESPONSE = 2 * 1024 * 1024


def valid_version_report(payload: object) -> bool:
    """The version response accepted by both key intake and explicit key tests."""
    return (isinstance(payload, dict) and isinstance(payload.get("engine"), dict)
            and isinstance(payload.get("service"), str))


class AddonError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_bytes(url: str, *, data: bytes | None = None, headers=None, limit=MAX_RESPONSE):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, data=data, headers={"User-Agent": CLIENT_USER_AGENT, **(headers or {})})
    with opener.open(request, timeout=25) as reply:
        content = reply.read(limit + 1)
        if len(content) > limit:
            raise AddonError("response_too_large", "The response exceeded the size limit. Nothing was enabled.")
        return content, dict(reply.headers)


def request_api(endpoint: str, payload: dict | None = None) -> tuple[dict, str]:
    from core.credential_store import get_credential, strict_reads

    if endpoint not in {"/v1/version", "/v1/scan"}:
        raise AddonError("unsupported_endpoint", "Unsupported security operation.")
    with strict_reads():
        key = get_credential(KEY_NAME)
    if not key:
        raise AddonError("eyebrow_key_missing", "Add your Eyebrow key in Settings → API Keys first.")
    try:
        raw, headers = fetch_bytes(
            API_ORIGIN + endpoint,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"},
        )
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value, next((str(v)[:32] for k, v in headers.items() if k.lower() == "x-quota-remaining"), "")
    except urllib.error.HTTPError as exc:
        from core.credential_intelligence.verification import is_gateway_denial
        if is_gateway_denial(exc.code, exc.read(MAX_RESPONSE)):
            raise AddonError("eyebrow_gateway_blocked", "Eyebrow’s gateway blocked this client request (HTTP 403). This does not verify or reject your key. No automatic retry was made.", 502) from None
        reasons = {
            401: ("eyebrow_unauthorized", "Eyebrow rejected the key. Replace it and try again."),
            413: ("eyebrow_too_large", "This add-on exceeds Eyebrow’s size limit."),
            422: ("eyebrow_unsupported", "Eyebrow could not inspect this content. It remains unchecked."),
            429: ("eyebrow_quota", "Eyebrow’s rate or quota limit was reached. Check your plan before retrying."),
            503: ("eyebrow_busy", "Eyebrow is busy. Retry manually later."),
            504: ("eyebrow_timeout", "Eyebrow timed out. Scan usage may still count; nothing was enabled."),
        }
        code, message = reasons.get(exc.code, ("eyebrow_unavailable", "Eyebrow did not complete the check. Nothing was enabled."))
        raise AddonError(code, message, 502) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AddonError("eyebrow_unreachable", "Could not reach Eyebrow. No automatic retry was made; nothing was enabled.", 502) from None
    except (ValueError, UnicodeError):
        raise AddonError("eyebrow_invalid_report", "Eyebrow returned an unreadable report. Nothing was enabled.", 502) from None


def scan_skill(name: str, content: str) -> dict:
    report, quota = request_api("/v1/scan", {"source": {"kind": "inline", "format": "skill", "name": name, "content": content}})
    report = validate_report(name, report)
    report["quota_remaining"] = quota
    return report


def validate_report(name: str, report: dict) -> dict:
    """Validate evidence independently of the owner's later installation decision."""
    # HTTP 200 is not a pass. Neither absent coverage nor malformed findings is a clean scan.
    if (not isinstance(report, dict) or report.get("kind") != "scan" or report.get("verdict") not in {"pass", "fail"}
            or not isinstance(report.get("engine"), str) or not report["engine"]
            or not isinstance(report.get("service"), str)
            or not isinstance(report.get("lockfile"), dict)
            or not isinstance(report.get("artifacts"), list) or not report["artifacts"]
            or not isinstance(report.get("findings"), list)
            or not isinstance(report.get("policy"), dict)
            or not isinstance(report["policy"].get("violations"), list)):
        raise AddonError("eyebrow_invalid_report", "The scan did not provide complete inspection evidence. Nothing was enabled.", 502)
    if not any(isinstance(a, dict) and a.get("name") == name and isinstance(a.get("digest"), str) and a["digest"] for a in report["artifacts"]):
        raise AddonError("eyebrow_no_coverage", "The report does not identify the submitted skill. Nothing was enabled.", 502)
    if any(not isinstance(f, dict) or not f.get("ruleId") or f.get("severity") not in {"low", "medium", "high", "critical"} for f in report["findings"]):
        raise AddonError("eyebrow_invalid_report", "The findings could not be validated. Nothing was enabled.", 502)
    # This is VOOL's default admission rule, not an Eyebrow installation permission.
    # A separate, version-bound owner decision may accept findings; it never alters this report.
    report = dict(report)
    report["eligible"] = report["verdict"] == "pass" and not report["findings"] and not report["policy"]["violations"]
    return report
