from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from core.remote_fetch_policy import open_remote

DDG_ENDPOINT = "https://api.duckduckgo.com/"


def ddg_instant_answer(query: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    text = (query or "").strip()
    if not text:
        return {}

    params = urllib.parse.urlencode(
        {
            "q": text,
            "format": "json",
            "no_redirect": "1",
            "no_html": "1",
            "skip_disambig": "0",
        }
    )
    request = urllib.request.Request(
        f"{DDG_ENDPOINT}?{params}",
        headers={"User-Agent": "VOOL-DDG/1.0"},
    )
    # The ONE outbound door: veto is enforced and the attempt reported into the
    # owning turn's ledger BEFORE any socket opens. Refusal raises
    # RemoteFetchRefusedError rather than returning an empty answer.
    with open_remote(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def best_text_blob(ddg_json: dict[str, Any]) -> str | None:
    abstract = str(ddg_json.get("AbstractText") or "").strip()
    if abstract:
        return abstract
    related = ddg_json.get("RelatedTopics") or []
    for item in related:
        if not isinstance(item, dict):
            continue
        text = str(item.get("Text") or "").strip()
        if text:
            return text
        nested = item.get("Topics")
        if not isinstance(nested, list):
            continue
        for nested_item in nested:
            if not isinstance(nested_item, dict):
                continue
            nested_text = str(nested_item.get("Text") or "").strip()
            if nested_text:
                return nested_text
    return None
