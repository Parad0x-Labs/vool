"""Read-only X (Twitter) trending fetch, off by default.

A bearer token (and optionally a trends endpoint) lives in the encrypted credential store under
`x.api.<account>` as a JSON blob {"bearer_token","trends_endpoint"} (a bare token string is also
accepted). `fetch_trending` does an authenticated read-only GET; nothing fires unless the operator
both configures a token and enables the tool. Fails closed (structured result, never a raw exception).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from core import credential_store

_DEFAULT_ENDPOINT = "https://api.twitter.com/2/trends/by/woeid/{woeid}"
_MAX_RESPONSE_BYTES = 2_000_000  # cap the response read so a misbehaving endpoint can't exhaust memory


@dataclass
class TrendResult:
    ok: bool
    status: str
    message: str
    trends: list[dict[str, Any]] = field(default_factory=list)


def _config(account: str) -> dict[str, Any] | None:
    raw = credential_store.get_credential(f"x.api.{str(account or 'default').strip()}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"bearer_token": str(raw).strip()}  # allow a bare token string


def fetch_trending(*, account: str = "default", woeid: int = 1, limit: int = 20) -> TrendResult:
    """Fetch trending topics for a location (read-only). Returns a structured result; never raises."""
    cfg = _config(account) or {}
    token = str(cfg.get("bearer_token") or "").strip()
    if not token:
        return TrendResult(False, "needs_credentials", "No X API bearer token stored (x.api.<account>).")
    try:
        woeid_int = int(woeid)
        limit_int = max(1, int(limit))
    except (ValueError, TypeError):
        return TrendResult(False, "invalid_arguments", "woeid and limit must be integers.")
    endpoint = str(cfg.get("trends_endpoint") or _DEFAULT_ENDPOINT).replace("{woeid}", str(woeid_int))
    req = urllib.request.Request(endpoint, headers={"Authorization": f"Bearer {token}"})
    trends: list[dict[str, Any]] = []
    try:
        from core.remote_fetch_policy import open_remote

        with open_remote(req, timeout=15) as resp:  # operator-configured endpoint
            payload = json.loads(resp.read(_MAX_RESPONSE_BYTES).decode("utf-8"))
        data = payload.get("data") if isinstance(payload, dict) else payload
        for item in data if isinstance(data, list) else []:
            if len(trends) >= limit_int:
                break
            if isinstance(item, dict):
                trends.append({
                    "name": str(item.get("trend_name") or item.get("name") or ""),
                    "count": item.get("tweet_count") or item.get("count"),
                })
    except Exception as exc:  # network / auth / rate-limit / malformed payload — never raise
        return TrendResult(False, "fetch_failed", f"X trends fetch failed ({type(exc).__name__}): {exc}")
    return TrendResult(True, "executed", f"Fetched {len(trends)} trending topic(s).", trends=trends)


__all__ = ["TrendResult", "fetch_trending"]
