"""Buy and sell activity for a token right now: trade counts, traded USD and price change per window.

``token.flow`` answers "is <token> being bought or sold?" for one token from public DEX trading
data: the token's most liquid pools (up to 5, each named with its DEX and pair), and for each
window (5m, 1h, 6h, 24h) how many trades were buys and sells, the USD the pool traded, and how
its price moved. Totals across pools come with the pools they cover named.

Rules this holds:

* Off unless the user has web access on (the same switch as web search and page fetches); with it
  off nothing goes online and the answer says how to turn it on. Every request goes through the one
  outbound door, so Local Only and the effect ledger apply. No key is used or asked for.
* Facts only, each with its window. The public source reports buy and sell trade counts per pool
  and the pool's whole traded USD; it does not split that USD between buys and sells and does not
  count unique buyers or sellers, so those are named as not reported, never guessed, never 0, and a
  pool without the split is never counted as half and half.
* Every gap is named: a token with no trading pool, a pool that reports no split, a window with no
  trades, a rate limit. A refused or partial answer never shows numbers.
* Cashtags without an address can't be checked (many tokens share a ticker) and are refused as such.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib.parse import quote, urlencode

INTENT = "token.flow"
SURFACE = "chain"
API_ORIGIN = "https://api.geckoterminal.com/api/v2"
REQUEST_TIMEOUT_S = 15.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_POOLS = 5
DEFAULT_WINDOWS = ("5m", "1h", "6h", "24h")
_WINDOW_CODES = {"5m": "m5", "1h": "h1", "6h": "h6", "24h": "h24"}
_NETWORKS = {"solana": "solana", "ethereum": "eth", "base": "base", "bnb": "bsc", "arbitrum": "arbitrum"}
_CHAIN_OF_NETWORK = {v: k for k, v in _NETWORKS.items()}
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SVM_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_CACHE_S = 60.0
WEB_OFF_TEXT = ("Buy and sell activity comes from public trading data online, and web access is off. Turn on web "
                "access in Settings to see it. Nothing was looked up.")


class FlowError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- the tool contract

def flow_contracts() -> list[Any]:
    from core.runtime_tool_contracts import RuntimeToolContract

    return [
        RuntimeToolContract(
            intent=INTENT,
            description=(
                "Buy and sell activity for one token right now, from public DEX trading data: its most liquid pools "
                f"(up to {MAX_POOLS}, each named with its DEX and pair) and, per window (default {', '.join(DEFAULT_WINDOWS)}), "
                "buy trades, sell trades, traded USD and price change, with totals across the pools that report a split. "
                "Read-only. Needs web access on. Facts only: it never says buy or sell pressure, and never advises."
            ),
            tool_surface=SURFACE,
            capability_id="token.flow",
            capability_claim="read a token's buy and sell activity from public DEX data",
            supported=True,
            unsupported_reason="",
            input_schema={
                "token": "str: token contract address",
                "chain": "str (optional): solana | ethereum | base | bnb | arbitrum",
                "windows": f"list (optional) of windows: {', '.join(DEFAULT_WINDOWS)}",
            },
            output_schema={"token": "str", "pools": "list", "totals": "dict"},
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
    out = evaluate(args.get("token"), args.get("chain"), args.get("windows"), source_context=source_context)
    observation = _tool_observation(intent=INTENT, tool_surface=SURFACE, ok=out["ok"], status=out["status"],
                                    message=out["text"])
    return RuntimeExecutionResult(handled=True, ok=out["ok"], status=out["status"], response_text=out["text"],
                                  details={"token": out.get("token", ""), "chain": out.get("chain", ""),
                                           "windows": out.get("windows", []), "pools": out.get("pools", []),
                                           "totals": out.get("totals"), "observation": observation})


# --------------------------------------------------------------------------- evaluation

def _web_on(source_context: dict[str, Any] | None) -> bool:
    try:
        from core.remote_fetch_policy import effective_web_available

        return bool(effective_web_available(source_context))
    except Exception:
        return False


def _windows_of(raw: Any) -> list[str]:
    if raw is None:
        return list(DEFAULT_WINDOWS)
    given = raw if isinstance(raw, list) else [raw]
    windows: list[str] = []
    for item in given:
        text = str(item or "").strip().lower().replace(" ", "")
        if text not in _WINDOW_CODES:
            raise FlowError("invalid_arguments",
                            f"windows can be {', '.join(DEFAULT_WINDOWS)}; {str(item)[:20]!r} is not one. Nothing was looked up.")
        if text not in windows:
            windows.append(text)
    return windows or list(DEFAULT_WINDOWS)


def evaluate(token: Any, chain: Any = None, windows: Any = None, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-window buy/sell counts, traded USD and price change for one token's top pools. Never raises."""
    token = str(token or "").strip()
    if not token:
        return {"ok": False, "status": "invalid_arguments", "text": "Give a token contract address to check.", "pools": [], "totals": None}
    if not _web_on(source_context):
        return {"ok": False, "status": "web_off", "text": WEB_OFF_TEXT, "pools": [], "totals": None}
    try:
        wanted = _windows_of(windows)
    except FlowError as exc:
        return {"ok": False, "status": exc.status, "text": exc.message, "pools": [], "totals": None}
    if not (_EVM_RE.fullmatch(token) or _SVM_RE.fullmatch(token)):
        if token.startswith("$"):
            return {"ok": False, "status": "no_address", "pools": [], "totals": None,
                    "text": "A ticker alone can't be checked (many tokens share it); give the contract address."}
        return {"ok": False, "status": "invalid_arguments", "pools": [], "totals": None,
                "text": "That is not a token contract address."}
    chain = str(chain or "").strip().lower()
    network = _NETWORKS.get(chain, "") if chain else ("solana" if not token.startswith("0x") else "")
    if chain and not network:
        return {"ok": False, "status": "unknown_chain", "pools": [], "totals": None,
                "text": f"Buy and sell activity covers {', '.join(sorted(_NETWORKS))}; {chain[:30]} is not one."}
    try:
        pools = _top_pools(token, network)
    except FlowError as exc:
        return {"ok": False, "status": exc.status, "text": exc.message, "pools": [], "totals": None}
    from core.secret_redaction import register_public_identifier

    register_public_identifier(token)
    return {"ok": True, "status": "ok", "token": token, "chain": _CHAIN_OF_NETWORK.get(pools[0]["network"], pools[0]["network"]),
            "symbol": pools[0].get("symbol") or "", "windows": wanted, "pools": pools,
            "totals": _totals(pools, wanted), "text": _text(token, pools, wanted)}


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _num(value: Any, *, allow_negative: bool = False) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if (allow_negative or out >= 0) else None


def _read_pool(pool: dict[str, Any], code: str) -> dict[str, Any]:
    """One pool, one window: counts, traded USD and price change, each None when not reported."""
    attrs = pool.get("attributes") or {}
    trades = ((attrs.get("transactions") or {}).get(code)) or {}
    volume = (_num((attrs.get("volume_usd") or {}).get(code))
              if isinstance(attrs.get("volume_usd"), dict) else None)
    change = (_num(((attrs.get("price_change_percentage") or {}).get(code)), allow_negative=True)
              if isinstance(attrs.get("price_change_percentage"), dict) else None)
    return {"buys": _count(trades.get("buys")), "sells": _count(trades.get("sells")), "volume_usd": volume,
            "change_pct": change}


def _has_split(window: dict[str, Any]) -> bool:
    return window["buys"] is not None and window["sells"] is not None


def _totals(pools: list[dict[str, Any]], windows: list[str]) -> dict[str, Any]:
    """Sums over the pools that report each figure; the coverage of every sum is counted, never assumed."""
    out: dict[str, Any] = {}
    for window in windows:
        code = _WINDOW_CODES[window]
        split = [p for p in pools if _has_split(_read_pool(p, code))]
        with_volume = [p for p in pools if (_read_pool(p, code))["volume_usd"] is not None]
        row: dict[str, Any] = {
            "pools_with_split": len(split), "pools_total": len(pools),
            "buys": sum(_read_pool(p, code)["buys"] for p in split) if split else None,
            "sells": sum(_read_pool(p, code)["sells"] for p in split) if split else None,
            "volume_usd": sum(_read_pool(p, code)["volume_usd"] for p in with_volume) if with_volume else None,
        }
        trades = (row["buys"] + row["sells"]) if row["buys"] is not None and row["sells"] is not None else 0
        # The share the source supports is a share of trades, said as that; the USD split it does not carry.
        row["buy_share_of_trades"] = round(row["buys"] / trades, 4) if trades else None
        out[window] = row
    return out


def _usd(value: float | None) -> str:
    if value is None:
        return "USD not reported"
    return f"${value:,.0f}" if value >= 1000 else f"${value:,.2f}"


def _count_text(value: int | None) -> str:
    return f"{value:,}" if value is not None else "?"


def _pct(value: float | None) -> str:
    return f"{value:+.1f}%" if value is not None else "change not reported"


def _text(token: str, pools: list[dict[str, Any]], windows: list[str]) -> str:
    head = pools[0].get("symbol") or (f"{token[:4]}…{token[-4:]}" if len(token) > 12 else token)
    lines = [f"Buy and sell activity for {head} ({_CHAIN_OF_NETWORK.get(pools[0]['network'], pools[0]['network'])}), "
             f"public pool data, windows {', '.join(windows)}. Trade counts are buys and sells; the source does not "
             "split traded USD between buys and sells and does not count unique buyers or sellers."]
    totals = _totals(pools, windows)
    for window in windows:
        row = totals[window]
        if row["pools_with_split"] == 0:
            lines.append(f"{window}: no pool reports a buy/sell split; "
                         f"traded USD {_usd(row['volume_usd'])} across {row['pools_total']} pool(s).")
            continue
        share = row["buy_share_of_trades"]
        share_text = f"{share * 100:.1f}% of trades were buys" if share is not None else "no trades reported in this window"
        coverage = f" across the {row['pools_with_split']} of {row['pools_total']} pool(s) reporting a split"
        lines.append(f"{window}{coverage}: {_count_text(row['buys'])} buys, {_count_text(row['sells'])} sells, "
                     f"{share_text}, traded USD {_usd(row['volume_usd'])}.")
    lines.append("Pools by liquidity:")
    for pool in pools:
        parts = [f"- {pool['dex']} {pool['name']}"]
        for window in windows:
            row = _read_pool(pool, _WINDOW_CODES[window])
            if _has_split(row):
                parts.append(f"{window} {_count_text(row['buys'])} buys / {_count_text(row['sells'])} sells, "
                             f"{_usd(row['volume_usd'])}, {_pct(row['change_pct'])}")
            else:
                parts.append(f"{window} buy/sell split not reported, {_usd(row['volume_usd'])}, {_pct(row['change_pct'])}")
        detail = f"{parts[0]}: " + "; ".join(parts[1:]) if len(parts) > 1 else parts[0]
        lines.append(detail)
    return "\n".join(lines)


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
        raise FlowError("refused", "VOOL is not allowed to go online right now (for example Local Only is on)") from None
    except urllib.error.HTTPError as exc:
        code = int(exc.code or 0)
        if code == 404:
            raise FlowError("not_found", "no trading pool was found for it") from None
        if code == 429:
            raise FlowError("rate_limited", "the public trading source is limiting requests right now; try again in a minute") from None
        raise FlowError("provider_error", "the public trading source could not answer right now") from None
    except Exception as exc:
        from core.credential_intelligence.verification import is_timeout_error

        if is_timeout_error(exc):
            raise FlowError("timeout", "the public trading source did not answer in time") from None
        raise FlowError("unreachable", "could not reach the public trading source") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise FlowError("too_large", "the trading source answered with more data than VOOL reads")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FlowError("bad_answer", "the trading source answered with something that is not data") from None
    with _cache_lock:
        _cache[url] = (time.time(), payload)
    return payload


def _top_pools(token: str, network: str) -> list[dict[str, Any]]:
    """The token's pools by liquidity (on ``network``, or on any known network for an EVM address)."""
    if network:
        payload = _get(f"/networks/{network}/tokens/{quote(token)}/pools", {"page": 1})
        found = [(network, p) for p in payload.get("data") or [] if isinstance(p, dict)]
    else:
        payload = _get("/search/pools", {"query": token, "page": 1})
        found = []
        for p in payload.get("data") or []:
            if not isinstance(p, dict):
                continue
            net = str(((p.get("relationships") or {}).get("network") or {}).get("data", {}).get("id") or "")
            if net in _CHAIN_OF_NETWORK:
                found.append((net, p))
    pools: list[dict[str, Any]] = []
    for net, p in found:
        attrs = p.get("attributes") or {}
        try:
            liquidity = float(attrs.get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            liquidity = 0.0
        name = str(attrs.get("name") or "")
        dex = str((((p.get("relationships") or {}).get("dex") or {}).get("data") or {}).get("id") or "")
        if not attrs.get("address") or not name:
            continue
        pools.append({"network": net, "address": str(attrs.get("address") or ""), "name": name,
                      "dex": dex or "unknown DEX", "liquidity_usd": liquidity,
                      "symbol": name.split("/")[0].strip()[:20], "attributes": attrs})
    pools.sort(key=lambda p: p["liquidity_usd"], reverse=True)
    if not pools:
        raise FlowError("not_found", "no trading pool was found for it")
    return pools[:MAX_POOLS]


__all__ = ["INTENT", "MAX_POOLS", "WEB_OFF_TEXT", "evaluate", "flow_contracts", "run_tool"]
