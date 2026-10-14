"""How a token did after a moment in time: the price then, the price now, and the peak and low after.

``token.performance`` answers "how did that call do?" for one or more (token, time) pairs, from
public DEX trading data: the token's most liquid pool and its price candles (GeckoTerminal's public
API, no key). It is what turns "who called it" into "and were they right": ``x.token_callers`` can
run it on every earlier call it finds.

Rules this holds:

* Off unless the user has web access on (the same switch as web search and page fetches); with it
  off nothing goes online and the answer says how to turn it on. Every request goes through the one
  outbound door, so Local Only and the effect ledger apply.
* Facts only. Each call gets the price at the time, now, the highest and lowest after, and when the
  peak came. No score, no verdict; the model and the user judge.
* Every gap is named, never filled: a token with no trading pool, a call made before trading began,
  a call older than the candles reach, a rate limit. A missing price is never a guessed one.
* Cashtags without an address can't be priced (many tokens share a ticker) and are reported as such.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

INTENT = "token.performance"
SURFACE = "chain"
API_ORIGIN = "https://api.geckoterminal.com/api/v2"
REQUEST_TIMEOUT_S = 15.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_CALLS = 15
MAX_PAGES = 3
_NETWORKS = {"solana": "solana", "ethereum": "eth", "base": "base", "bnb": "bsc", "arbitrum": "arbitrum"}
_CHAIN_OF_NETWORK = {v: k for k, v in _NETWORKS.items()}
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SVM_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_CACHE_S = 120.0
_clock = time.time
WEB_OFF_TEXT = ("Past token prices come from public trading data online, and web access is off. Turn on web "
                "access in Settings to check how calls did. Nothing was looked up.")


class PriceError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- the tool contract

def performance_contracts() -> list[Any]:
    from core.runtime_tool_contracts import RuntimeToolContract

    return [
        RuntimeToolContract(
            intent=INTENT,
            description=(
                "How a token did after a moment: its price then, now, and the highest and lowest after (with when the "
                f"peak came), from public DEX trading data. Give up to {MAX_CALLS} calls, each a token contract address "
                "and a time (ISO, e.g. the time of an X post). Use it to judge past calls, e.g. after x.token_callers. "
                "Read-only. Needs web access on. Facts only: it never rates a call."
            ),
            tool_surface=SURFACE,
            capability_id="web.token_performance",
            capability_claim="read a token's past and current price from public DEX data",
            supported=True,
            unsupported_reason="",
            input_schema={
                "calls": f"list (1-{MAX_CALLS}) of {{token: contract address, at: ISO time, chain?: solana|ethereum|base|bnb|arbitrum}}",
                "token": "str (optional): one token address instead of calls",
                "at": "str (optional): the time for that one token",
                "chain": "str (optional): the chain for that one token",
            },
            output_schema={"calls": "list"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="network_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=("use_network_access",),
            renders_final_answer=False,
        ),
    ]


def run_tool(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None = None):
    from core.execution.models import _tool_observation
    from core.runtime_execution_tools import RuntimeExecutionResult

    args = dict(arguments or {})
    calls = args.get("calls")
    if not isinstance(calls, list) or not calls:
        calls = [{"token": args.get("token"), "at": args.get("at"), "chain": args.get("chain")}] if args.get("token") else []
    out = evaluate(calls, source_context=source_context)
    observation = _tool_observation(intent=INTENT, tool_surface=SURFACE, ok=out["ok"], status=out["status"],
                                    message=out["text"], calls=out.get("calls"))
    return RuntimeExecutionResult(handled=True, ok=out["ok"], status=out["status"], response_text=out["text"],
                                  details={"calls": out.get("calls"), "observation": observation})


# --------------------------------------------------------------------------- evaluation

def _when(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def _iso(ts: int | float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _web_on(source_context: dict[str, Any] | None) -> bool:
    try:
        from core.remote_fetch_policy import effective_web_available

        return bool(effective_web_available(source_context))
    except Exception:
        return False


def evaluate(calls: list[Any], *, source_context: dict[str, Any] | None = None, now: int | None = None) -> dict[str, Any]:
    """Price at the time, now, peak and low after, for each (token, time) call. Never raises."""
    rows = [c for c in calls if isinstance(c, dict)]
    if not rows:
        return {"ok": False, "status": "invalid_arguments", "text": "Give a token address and the time to check from.", "calls": []}
    if len(rows) > MAX_CALLS:
        return {"ok": False, "status": "too_many", "calls": [],
                "text": f"That is {len(rows)} calls; ask for {MAX_CALLS} or fewer at a time. Nothing was looked up."}
    if not _web_on(source_context):
        return {"ok": False, "status": "web_off", "calls": [], "text": WEB_OFF_TEXT}
    now = int(now or _clock())
    results = []
    for row in rows:
        token = str(row.get("token") or row.get("address") or "").strip()
        result: dict[str, Any] = {"token": token, "at": _iso(_when(row.get("at"))) if _when(row.get("at")) else ""}
        try:
            result.update(_one(token, _when(row.get("at")), str(row.get("chain") or "").strip().lower(), now))
        except PriceError as exc:
            result.update(status=exc.status, message=exc.message)
        except (KeyError, TypeError, ValueError, IndexError):
            result.update(status="bad_answer", message="the trading data came back in a shape VOOL does not recognise")
        results.append(result)
        if result.get("status") in {"rate_limited", "refused"}:
            for rest in rows[len(results):]:
                results.append({"token": str(rest.get("token") or ""), "status": "not_checked",
                                "message": "not checked: " + result["message"]})
            break
    priced = [r for r in results if r.get("status") == "ok"]
    lines = [_line(r) for r in results]
    text = (f"Price check for {len(results)} call(s), {len(priced)} priced (public DEX data from GeckoTerminal; "
            "prices in USD):\n" + "\n".join(f"- {line}" for line in lines))
    return {"ok": bool(priced), "status": "ok" if priced else "nothing_priced", "text": text, "calls": results}


def _line(r: dict[str, Any]) -> str:
    who = r.get("symbol") or r.get("token") or "?"
    if r.get("status") != "ok":
        return f"{who}: {r.get('message')}."
    bits = [f"{who} at {r['at']}: ${r['price_at']:.6g}", f"now ${r['price_now']:.6g} ({r['change_now_pct']:+.1f}%)"]
    if r.get("peak_after") is not None:
        bits.append(f"peak after ${r['peak_after']:.6g} ({r['peak_pct']:+.1f}%) at {r['peak_at']}")
    if r.get("low_after") is not None:
        bits.append(f"low after ${r['low_after']:.6g} ({r['low_pct']:+.1f}%)")
    if r.get("candle"):
        bits.append(f"from {r['candle']} candles")
    if r.get("note"):
        bits.append(r["note"])
    return ", ".join(bits)


def _one(token: str, at: int | None, chain: str, now: int) -> dict[str, Any]:
    if not token or not (_EVM_RE.fullmatch(token) or _SVM_RE.fullmatch(token)):
        if token.startswith("$"):
            raise PriceError("no_address", "a ticker alone can't be priced (many tokens share it); it needs the contract address")
        raise PriceError("invalid_arguments", "that is not a token contract address")
    if at is None:
        raise PriceError("invalid_arguments", "give the time to check from (for example the post's time)")
    if at > now:
        raise PriceError("invalid_arguments", "that time is in the future")
    network = _NETWORKS.get(chain, "") if chain else ("solana" if not token.startswith("0x") else "")
    if chain and not network:
        raise PriceError("unknown_chain", f"past prices cover {', '.join(sorted(_NETWORKS))}; {chain[:30]} is not one")
    pool = _top_pool(token, network)
    network = pool["network"]
    timeframe, aggregate, step = _timeframe(now - at)
    candles = _candles(network, pool["address"], token, timeframe, aggregate, at, step)
    if not candles:
        raise PriceError("no_trades", "no trades were recorded for it in that window")
    candles.sort(key=lambda c: c[0])
    first_ts = candles[0][0]
    note = ""
    if at < first_ts - step:
        if pool.get("created_at") and _when(pool["created_at"]) and at < (_when(pool["created_at"]) or 0):
            note = "posted before its main pool started trading; price shown is the first trade after"
        else:
            note = "the price history doesn't reach back that far; price shown is the earliest available"
    # The price at the post is the close of the last candle that had ended by then; peak and low count
    # only candles that started after it, so a move earlier in the same candle is never credited.
    ended = [c for c in candles if c[0] + step <= at]
    after = [c for c in candles if c[0] >= at]
    if ended:
        price_at = float(ended[-1][4])
    elif after:
        price_at = float(after[0][1])
    else:
        price_at = float(candles[-1][1])
        note = note or "posted inside the latest candle; price shown is that candle's open"
    price_now = float(candles[-1][4])
    last_trade = candles[-1][0] + step
    if now - last_trade > 3 * step:
        note = "; ".join(n for n in (note, f"last trade {_iso(candles[-1][0])}, so 'now' is that price") if n)
    peak = max(after, key=lambda c: float(c[2])) if after else None
    low = min(after, key=lambda c: float(c[3])) if after else None
    if price_at <= 0:
        raise PriceError("no_trades", "the trading data has no usable price at that time")

    def pct(value: float) -> float:
        return round((value / price_at - 1) * 100, 1)

    from core.secret_redaction import register_public_identifier

    register_public_identifier(token)
    return {
        "status": "ok", "chain": _CHAIN_OF_NETWORK.get(network, network), "symbol": pool.get("symbol") or "",
        "pool": pool["address"], "price_at": price_at, "price_now": price_now, "change_now_pct": pct(price_now),
        "peak_after": float(peak[2]) if peak else None, "peak_pct": pct(float(peak[2])) if peak else None,
        "peak_at": _iso(peak[0]) if peak else "",
        "low_after": float(low[3]) if low else None, "low_pct": pct(float(low[3])) if low else None,
        "candle": f"{aggregate}-{timeframe}", "liquidity_usd": pool.get("liquidity_usd"), "note": note,
    }


def _timeframe(age_s: int) -> tuple[str, int, int]:
    """Candle size: fine enough to see the move, coarse enough to reach the call in a few pages."""
    if age_s <= 2 * 86400:
        return "minute", 5, 300
    if age_s <= 40 * 86400:
        return "hour", 1, 3600
    return "day", 1, 86400


# --------------------------------------------------------------------------- GeckoTerminal reads

def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    import urllib.error

    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote_url

    url = API_ORIGIN + path + ("?" + urlencode(params) if params else "")
    with _cache_lock:
        hit = _cache.get(url)
        if hit and time.time() - hit[0] < _CACHE_S:
            return hit[1]
    try:
        response = open_remote_url(url, headers={"Accept": "application/json;version=20230302"}, method="GET",
                                   timeout=REQUEST_TIMEOUT_S, no_proxy=True, provider_id="market.geckoterminal",
                                   keyed_or_keyless="keyless")
        raw = response.read(MAX_RESPONSE_BYTES + 1) or b""
    except RemoteFetchRefusedError:
        raise PriceError("refused", "VOOL is not allowed to go online right now (for example Local Only is on)") from None
    except urllib.error.HTTPError as exc:
        code = int(exc.code or 0)
        if code == 404:
            raise PriceError("not_found", "no trading pool was found for it") from None
        if code == 429:
            raise PriceError("rate_limited", "the public price source is limiting requests right now; try again in a minute") from None
        raise PriceError("provider_error", f"the public price source could not answer (HTTP {code})") from None
    except Exception as exc:
        from core.credential_intelligence.verification import is_timeout_error

        if is_timeout_error(exc):
            raise PriceError("timeout", "the public price source did not answer in time") from None
        raise PriceError("unreachable", "could not reach the public price source") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise PriceError("too_large", "the price source answered with more data than VOOL reads")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PriceError("bad_answer", "the price source answered with something that is not data") from None
    with _cache_lock:
        _cache[url] = (time.time(), payload)
    return payload


def _top_pool(token: str, network: str) -> dict[str, Any]:
    """The token's most liquid pool (on ``network``, or on any network for an EVM address)."""
    if network:
        payload = _get(f"/networks/{network}/tokens/{quote(token)}/pools", {"page": 1})
        pools = [(network, p) for p in payload.get("data") or [] if isinstance(p, dict)]
    else:
        payload = _get("/search/pools", {"query": token, "page": 1})
        pools = []
        for p in payload.get("data") or []:
            if not isinstance(p, dict):
                continue
            net = str(((p.get("relationships") or {}).get("network") or {}).get("data", {}).get("id") or "")
            if net in _CHAIN_OF_NETWORK:
                pools.append((net, p))
    best = None
    for net, p in pools:
        attrs = p.get("attributes") or {}
        try:
            liquidity = float(attrs.get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            liquidity = 0.0
        if best is None or liquidity > best["liquidity_usd"]:
            name = str(attrs.get("name") or "")
            best = {"network": net, "address": str(attrs.get("address") or ""), "liquidity_usd": liquidity,
                    "created_at": attrs.get("pool_created_at"), "symbol": name.split("/")[0].strip()[:20]}
    if not best or not best["address"]:
        raise PriceError("not_found", "no trading pool was found for it")
    return best


def _candles(network: str, pool: str, token: str, timeframe: str, aggregate: int, at: int, step: int) -> list[list[float]]:
    """Candles from just before ``at`` until now, newest pages first, at most MAX_PAGES requests."""
    found: list[list[float]] = []
    before = None
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"aggregate": aggregate, "limit": 1000, "currency": "usd", "token": token}
        if before:
            params["before_timestamp"] = before
        payload = _get(f"/networks/{network}/pools/{quote(pool)}/ohlcv/{timeframe}", params)
        page = (((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
        rows = [[float(x) for x in c[:6]] for c in page if isinstance(c, list) and len(c) >= 5]
        if not rows:
            break
        found.extend(rows)
        oldest = min(r[0] for r in rows)
        if oldest <= at - step or len(rows) < 1000:
            break
        before = int(oldest)
    seen: dict[float, list[float]] = {}
    for row in found:
        seen[row[0]] = row
    return list(seen.values())


__all__ = ["INTENT", "MAX_CALLS", "WEB_OFF_TEXT", "evaluate", "performance_contracts", "run_tool"]
