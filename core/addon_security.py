"""Neutral bounded add-on downloads and independent scan evidence validation."""

from __future__ import annotations

import urllib.request

CLIENT_USER_AGENT = "VOOL"

MAX_RESPONSE = 2 * 1024 * 1024

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
        raise AddonError("scanner_invalid_report", "The safety check came back incomplete, so nothing was turned on. Try the check again.", 502)
    if not any(isinstance(a, dict) and a.get("name") == name and isinstance(a.get("digest"), str) and a["digest"] for a in report["artifacts"]):
        raise AddonError("scanner_no_coverage", "The safety check came back incomplete, so nothing was turned on. Try the check again.", 502)
    if any(not isinstance(f, dict) or not f.get("ruleId") or f.get("severity") not in {"low", "medium", "high", "critical"} for f in report["findings"]):
        raise AddonError("scanner_invalid_report", "The safety check came back incomplete, so nothing was turned on. Try the check again.", 502)
    # This is VOOL's default admission rule, not a scanner installation permission.
    # A separate, version-bound owner decision may accept findings; it never alters this report.
    report = dict(report)
    report["eligible"] = report["verdict"] == "pass" and not report["findings"] and not report["policy"]["violations"]
    return report
