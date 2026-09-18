from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def resolve_request_id(headers: Mapping[str, str] | None) -> str:
    normalized_headers = {str(key).lower(): value for key, value in dict(headers or {}).items()}
    for header_name in (REQUEST_ID_HEADER, CORRELATION_ID_HEADER):
        raw = str(normalized_headers.get(header_name.lower()) or "").strip()
        if raw and _REQUEST_ID_RE.fullmatch(raw):
            return raw
    return uuid.uuid4().hex


def response_headers_with_request_id(
    headers: Mapping[str, str] | None,
    *,
    request_id: str,
) -> dict[str, str]:
    merged = dict(headers or {})
    merged[REQUEST_ID_HEADER] = request_id
    merged.setdefault(CORRELATION_ID_HEADER, request_id)
    return merged


def log_http_request(
    logger: logging.Logger,
    *,
    component: str,
    method: str,
    path: str,
    status_code: int,
    latency_ms: float,
    request_id: str,
) -> None:
    logger.info(
        "http_request",
        extra={
            "event": "http_request",
            "component": component,
            "request_id": request_id,
            "trace_id": request_id,
            "details": {
                "method": method,
                "path": path,
                "status_code": int(status_code),
                "latency_ms": round(float(latency_ms), 3),
            },
        },
    )
