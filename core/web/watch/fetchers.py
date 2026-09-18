from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime
from urllib import request

from .tls import normalize_base_url, ssl_context_for_url
from core.remote_fetch_policy import open_remote


def agent_is_online(agent: dict[str, object]) -> bool:
    status = str((agent or {}).get("status") or "").strip().lower()
    if bool((agent or {}).get("online")):
        return True
    return status in {"online", "idle", "busy", "limited"}


def agent_profile_key(agent: dict[str, object]) -> tuple[str, str, tuple[str, ...]]:
    label = str(agent.get("display_name") or agent.get("claim_label") or agent.get("agent_id") or "agent").strip().lower()
    region = str(agent.get("home_region") or agent.get("current_region") or "global").strip().lower()
    capabilities = tuple(
        sorted(
            str(item).strip().lower()
            for item in list(agent.get("capabilities") or [])
            if str(item).strip()
        )
    )
    return (label, region, capabilities)


def agent_profile_rank(agent: dict[str, object]) -> tuple[int, int, int, str]:
    status = str(agent.get("status") or "").strip().lower()
    transport = str(agent.get("transport_mode") or "").strip().lower()
    status_rank = {
        "busy": 4,
        "online": 3,
        "idle": 2,
        "limited": 1,
    }.get(status, 0)
    transport_rank = {
        "channel_openclaw": 4,
        "vool_agent": 3,
        "direct": 2,
        "hole_punch": 1,
        "relay": 1,
        "lan_only": 0,
        "background_openclaw": 0,
    }.get(transport, 0)
    capability_count = len([item for item in list(agent.get("capabilities") or []) if str(item).strip()])
    return (status_rank, transport_rank, capability_count, str(agent.get("agent_id") or ""))


def distinct_visible_agents(agents: list[dict[str, object]]) -> list[dict[str, object]]:
    chosen: dict[tuple[str, str, tuple[str, ...]], dict[str, object]] = {}
    for agent in list(agents or []):
        key = agent_profile_key(agent)
        current = chosen.get(key)
        if current is None or agent_profile_rank(agent) > agent_profile_rank(current):
            chosen[key] = dict(agent)
    return list(chosen.values())


def normalize_dashboard_presence(snapshot: dict) -> dict:
    normalized = dict(snapshot or {})
    raw_agents = [dict(item) for item in list(normalized.get("agents") or []) if isinstance(item, dict)]
    if not raw_agents:
        return normalized

    distinct_agents = distinct_visible_agents(raw_agents)
    stats = dict(normalized.get("stats") or {})
    raw_presence = stats.get("presence_agents", stats.get("active_agents", 0))
    stats["presence_agents"] = int(raw_presence or 0)
    stats["raw_online_agents"] = sum(1 for agent in raw_agents if agent_is_online(agent))
    stats["raw_visible_agents"] = len(raw_agents)
    stats["duplicate_visible_agents"] = max(0, len(raw_agents) - len(distinct_agents))
    stats["active_agents"] = sum(1 for agent in distinct_agents if agent_is_online(agent))
    stats["visible_agents"] = len(distinct_agents)
    normalized["stats"] = stats
    normalized["agents"] = distinct_agents

    region_counts: dict[str, int] = {}
    for agent in distinct_agents:
        region = str(agent.get("current_region") or agent.get("home_region") or "global").strip().lower()
        if agent_is_online(agent):
            region_counts[region] = region_counts.get(region, 0) + 1

    region_stats: list[dict[str, object]] = []
    for row in list(normalized.get("stats", {}).get("region_stats") or []):
        if not isinstance(row, dict):
            region_stats.append(row)
            continue
        region = str(row.get("region") or "global").strip().lower()
        updated = dict(row)
        if region in region_counts:
            updated["online_agents"] = region_counts[region]
        region_stats.append(updated)
    if region_stats:
        normalized["stats"]["region_stats"] = region_stats
    return normalized


def parse_dashboard_timestamp(value: object) -> float:
    if value in (None, "", 0):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def dashboard_trading_presence_ts(snapshot: dict) -> float:
    trading = dict(snapshot.get("trading_learning") or {})
    heartbeat = dict(trading.get("latest_heartbeat") or {})
    summary = dict(trading.get("latest_summary") or {})
    latest_ts = max(
        parse_dashboard_timestamp(heartbeat.get("last_tick_ts")),
        parse_dashboard_timestamp(heartbeat.get("post_created_at")),
        parse_dashboard_timestamp(summary.get("post_created_at")),
    )
    for topic in list(trading.get("topics") or []):
        if not isinstance(topic, dict):
            continue
        latest_ts = max(
            latest_ts,
            parse_dashboard_timestamp(topic.get("updated_at")),
            parse_dashboard_timestamp(topic.get("created_at")),
        )
    return latest_ts


def dashboard_freshness_key(snapshot: dict) -> tuple[float, float, int]:
    presence_ts = dashboard_trading_presence_ts(snapshot)
    generated_ts = parse_dashboard_timestamp(snapshot.get("generated_at"))
    active_agents = int(dict(snapshot.get("stats") or {}).get("active_agents", 0) or 0)
    return (presence_ts, generated_ts, active_agents)


def http_get_json(
    url: str,
    *,
    timeout_seconds: int,
    auth_token: str | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    ssl_context_for_url_func: Callable[..., object | None] = ssl_context_for_url,
) -> dict:
    req = request.Request(url, method="GET")
    req.add_header("Content-Type", "application/json")
    token = str(auth_token or "").strip()
    if token:
        req.add_header("X-Vool-Meet-Token", token)
    # THE SHARED DOOR, not a raw socket: veto enforced before any I/O.
    with open_remote(
        req,
        timeout=timeout_seconds,
        context=ssl_context_for_url_func(
            url,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        ),
    ) as resp:
        return json.loads(resp.read().decode("utf-8"))


def proxy_voolbook_get(
    upstream_base_urls: tuple[str, ...],
    path: str,
    *,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[..., dict] = http_get_json,
    normalize_base_url_func: Callable[[str], str] = normalize_base_url,
) -> dict:
    tokens = {
        normalize_base_url_func(base): str(token).strip()
        for base, token in dict(auth_tokens_by_base_url or {}).items()
        if str(base).strip() and str(token).strip()
    }

    def _fetch_one(base: str) -> tuple[bool, str, dict | str]:
        clean = str(base).rstrip("/")
        target = f"{clean}{path}"
        token = tokens.get(normalize_base_url_func(clean)) or auth_token
        try:
            result = fetch_json(
                target,
                timeout_seconds=timeout_seconds,
                auth_token=token,
                tls_ca_file=tls_ca_file,
                tls_insecure_skip_verify=tls_insecure_skip_verify,
            )
        except Exception as exc:
            return False, clean, str(exc)
        if result.get("ok"):
            return True, clean, result
        return False, clean, str(result.get("error") or "not ok")

    errors: list[str] = []
    max_workers = max(1, min(len(upstream_base_urls), 8))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_one, base) for base in upstream_base_urls]
        for future in as_completed(futures):
            ok, clean, payload_or_error = future.result()
            if ok:
                for pending in futures:
                    pending.cancel()
                return dict(payload_or_error)
            errors.append(f"{clean}: {payload_or_error}")
    raise ValueError("VoolBook proxy failed: " + "; ".join(errors))


def fetch_dashboard_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
    normalize_base_url_func: Callable[[str], str] = normalize_base_url,
) -> dict:
    tokens = {
        normalize_base_url_func(base): str(token).strip()
        for base, token in dict(auth_tokens_by_base_url or {}).items()
        if str(base).strip() and str(token).strip()
    }
    fetch = fetch_json or (
        lambda url, token: http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )

    def _fetch_one(base: str) -> tuple[bool, str, dict | str, tuple[float, float, int] | None]:
        clean = str(base).rstrip("/")
        target = f"{clean}/v1/hive/dashboard"
        token = tokens.get(normalize_base_url_func(clean)) or auth_token
        try:
            payload = fetch(target, token)
        except Exception as exc:  # pragma: no cover - network errors
            return False, clean, str(exc), None
        if payload.get("ok"):
            result = normalize_dashboard_presence(payload.get("result") or {})
            result["source_meet_url"] = clean
            freshness = dashboard_freshness_key(result)
            return True, clean, result, freshness
        return False, clean, str(payload.get("error") or "upstream returned not ok"), None

    errors: list[str] = []
    best_result: dict | None = None
    best_key: tuple[float, float, int] | None = None
    settle_deadline: float | None = None
    max_workers = max(1, min(len(upstream_base_urls), 8))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {executor.submit(_fetch_one, base) for base in upstream_base_urls}
        while pending:
            timeout = None
            if settle_deadline is not None:
                timeout = max(0.0, settle_deadline - time.monotonic())
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                ok, clean, payload_or_error, freshness = future.result()
                if ok:
                    result = dict(payload_or_error)
                    if best_result is None or (freshness or (0.0, 0.0, 0)) > (best_key or (0.0, 0.0, 0)):
                        best_result = result
                        best_key = freshness
                    if settle_deadline is None:
                        settle_deadline = time.monotonic() + 0.15
                else:
                    errors.append(f"{clean}: {payload_or_error}")
        for future in pending:
            future.cancel()
    if best_result is not None:
        return best_result
    raise ValueError("All upstream meet nodes failed for dashboard fetch: " + "; ".join(errors))


def fetch_topic_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    topic_id: str,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
    normalize_base_url_func: Callable[[str], str] = normalize_base_url,
) -> dict:
    tokens = {
        normalize_base_url_func(base): str(token).strip()
        for base, token in dict(auth_tokens_by_base_url or {}).items()
        if str(base).strip() and str(token).strip()
    }
    fetch = fetch_json or (
        lambda url, token: http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )
    normalized_topic_id = str(topic_id or "").strip()
    errors: list[str] = []
    for base in upstream_base_urls:
        clean = str(base).rstrip("/")
        target = f"{clean}/v1/hive/topics/{normalized_topic_id}"
        token = tokens.get(normalize_base_url_func(clean)) or auth_token
        try:
            payload = fetch(target, token)
        except Exception as exc:  # pragma: no cover - network errors
            errors.append(f"{clean}: {exc}")
            continue
        if payload.get("ok"):
            result = dict(payload.get("result") or {})
            result["source_meet_url"] = clean
            return result
        errors.append(f"{clean}: {payload.get('error') or 'upstream returned not ok'}")
    raise ValueError("All upstream meet nodes failed for topic fetch: " + "; ".join(errors))


def fetch_topic_posts_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    topic_id: str,
    limit: int = 120,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
    normalize_base_url_func: Callable[[str], str] = normalize_base_url,
) -> list[dict]:
    tokens = {
        normalize_base_url_func(base): str(token).strip()
        for base, token in dict(auth_tokens_by_base_url or {}).items()
        if str(base).strip() and str(token).strip()
    }
    fetch = fetch_json or (
        lambda url, token: http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )
    normalized_topic_id = str(topic_id or "").strip()
    safe_limit = max(1, min(int(limit), 200))
    errors: list[str] = []
    for base in upstream_base_urls:
        clean = str(base).rstrip("/")
        target = f"{clean}/v1/hive/topics/{normalized_topic_id}/posts?limit={safe_limit}"
        token = tokens.get(normalize_base_url_func(clean)) or auth_token
        try:
            payload = fetch(target, token)
        except Exception as exc:  # pragma: no cover - network errors
            errors.append(f"{clean}: {exc}")
            continue
        if payload.get("ok"):
            return list(payload.get("result") or [])
        errors.append(f"{clean}: {payload.get('error') or 'upstream returned not ok'}")
    raise ValueError("All upstream meet nodes failed for topic post fetch: " + "; ".join(errors))
