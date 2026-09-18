"""Resolve a crypto ticker to a CoinGecko coin id against a live, rank-ordered index.

Why this module exists
----------------------
`tools/web/web_research.py` carried a 12-entry hand-written `_CRYPTO_ALIASES` dict. Anything absent
from it was invisible to the entire deterministic price lane: on 2026-08-04 "what is the price of
ARB and LTC?" resolved only LTC, because `arb` was not a key. The reflex fix -- add
`"arb": "arbitrum"` -- is the hard-coded input->output mapping banned by CLAUDE.md Section 0.2: it
satisfies exactly the query just tested and leaves SUI, TIA, JUP, PEPE and every future listing
broken in the same way, each waiting for its own dict edit.

So the alias table stops being the source of truth for *which symbols exist*. This module answers
that from CoinGecko's own market-cap-ordered index, fetched once and cached. Adding a coin to the
runtime now requires no code change at all.

Why the top 250 and not every coin
----------------------------------
CoinGecko lists ~18k coins. Many low-rank symbols collide with ordinary English, so resolving
against the full list would read a plain word as an asset -- the exact failure the three earlier
attempts at this problem shipped (see `price_request_leaves_unresolved_content`'s docstring in
core/agent_runtime/fast_live_info_price.py for that history).

Measured against the real index on 2026-08-04, not assumed:

  * top 250  -- collides with `btw` (Bitway, rank 126) and `cash` (Cash, rank 228). Nothing else in
    a 90-word list of ordinary price-question vocabulary.
  * top 500  -- adds `would` (rank 292).
  * per-token `/search` -- resolves `now` to ChangeNOW (rank 647) and `check` to Checkmate (1389),
    and rate-limits (HTTP 429) after roughly a dozen calls even spaced 2.5s apart.

Every ticker a person actually asks about conversationally sits far above the cut: ARB 93, SUI 33,
LTC 28, TIA 119, JUP 88, PEPE 58, HBAR 29, ONDO 43, ENA 73, SEI 129. So the rank ceiling is not a
tuned threshold laid over a guess -- it *is* the discriminator, and it costs nothing real: a coin
below the cut simply falls through to general web search, which handles "<TICKER> price" well
(measured: 5 clean exchange results in 3.0s).

Why the stopword filter here is safe
------------------------------------
`_NEVER_A_TICKER` is **subtractive only**. It can remove a candidate before lookup; it can never
promote one. A word wrongly listed there costs one general web search. That is the opposite
direction from the banned pattern, which was a hand-written list *deciding* that some leftover
token IS an asset -- a false positive there is a fabricated price. Nothing in this module ever
names an asset the index did not confirm.

Failure behaviour: every path fails closed. No network, remote fetch disabled, a malformed payload
or a cold cache all yield "" -- the caller then behaves exactly as it did before this module
existed (static alias table, then general search). This module can never make the runtime answer
with less information than it had; it can only add resolutions.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from core.remote_fetch_policy import remote_fetch_forbidden
from core.runtime_paths import active_vool_home

# Rank ceiling. See the module docstring for the measurements behind this number -- it is the
# collision boundary of the real index, not a round number picked for looks.
_MAX_RANK = 250
_INDEX_URL = (
    "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
    f"&order=market_cap_desc&per_page={_MAX_RANK}&page=1&sparkline=false"
)
# The index moves slowly (a coin's rank, not its price), so a long TTL is correct: this cache must
# never sit on the latency path of an actual quote.
_CACHE_TTL_S = 6 * 3600.0
_FETCH_TIMEOUT_S = 8.0
# A FAILED fetch is remembered for seconds, not for `_CACHE_TTL_S`. Reusing the success TTL here
# meant one transient failure -- a CoinGecko 429, a flapping link during startup -- disabled every
# dynamic resolution in that process for six hours, silently reverting the runtime to the static
# alias table it just replaced. Long enough to stop a dead network putting a fetch timeout on every
# token of every price question; short enough that recovery is automatic.
_NEGATIVE_CACHE_TTL_S = 30.0

# Ordinary English that collides with a symbol inside the rank ceiling, plus the price-question
# vocabulary that would otherwise burn a lookup. Subtractive only -- see the module docstring.
# `btw` (Bitway, 126) and `cash` (Cash, 228) are the two measured collisions in the top 250; the
# rest are here to keep obvious scaffolding from reaching the index at all.
_NEVER_A_TICKER = frozenset({
    "btw", "cash", "price", "cost", "worth", "value", "rate", "market", "cap", "trading",
    "how", "much", "what", "whats", "hows", "the", "and", "for", "its", "that", "this",
    "these", "those", "now", "today", "current", "currently", "latest", "recent", "tell",
    "give", "show", "right", "help", "about", "going", "down", "maybe", "please", "pls",
    "you", "know", "mean", "meant", "way", "just", "still", "again", "did", "does", "was",
    "were", "has", "have", "had", "will", "would", "could", "should", "settle", "settled",
    "closed", "opened", "traded", "hit", "are", "is", "it", "of", "my", "me", "we", "our",
    "can", "get", "see", "new", "old", "good", "bad", "high", "low", "top", "big", "buy",
    "sell", "hold", "coin", "token", "crypto", "money", "usd", "dollar", "each", "per",
})

_TOKEN_RE = re.compile(r"[a-z0-9]{2,12}")

_lock = threading.Lock()
_memory_index: dict[str, str] | None = None
_memory_stamp: float = 0.0


def _cache_path() -> Path:
    return active_vool_home() / "cache" / "coingecko_rank_index.json"


def _read_disk_cache() -> dict[str, str] | None:
    try:
        raw = _cache_path().read_text(encoding="utf-8")
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        fetched_at = float(payload.get("fetched_at") or 0.0)
    except (TypeError, ValueError):
        return None
    if (time.time() - fetched_at) > _CACHE_TTL_S:
        return None
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict) or not symbols:
        return None
    return {str(key): str(value) for key, value in symbols.items() if key and value}


def _write_disk_cache(symbols: dict[str, str]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash mid-write cannot leave a half-parsed cache that would make
        # every later read fail closed until someone deletes it by hand.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"fetched_at": time.time(), "symbols": symbols}, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        return


def _fetch_index() -> dict[str, str]:
    """symbol -> coin id for the top `_MAX_RANK` coins. {} on any failure."""
    if remote_fetch_forbidden():
        return {}
    request = urllib.request.Request(_INDEX_URL, headers={"User-Agent": "VOOL-PRICE/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_S) as response:
            rows = json.loads(response.read(4_000_000).decode("utf-8", errors="ignore"))
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    best: dict[str, tuple[str, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().lower()
        coin_id = str(row.get("id") or "").strip()
        if not symbol or not coin_id:
            continue
        try:
            rank = int(row.get("market_cap_rank") or 10**9)
        except (TypeError, ValueError):
            rank = 10**9
        if rank > _MAX_RANK:
            continue
        # Two coins can share a symbol even inside the top 250; the higher-cap one wins, which is
        # what a person naming a bare ticker means every time.
        if symbol not in best or rank < best[symbol][1]:
            best[symbol] = (coin_id, rank)
    return {symbol: coin_id for symbol, (coin_id, _rank) in best.items()}


def _index() -> dict[str, str]:
    global _memory_index, _memory_stamp
    with _lock:
        if _memory_index is not None:
            age = time.time() - _memory_stamp
            ttl = _CACHE_TTL_S if _memory_index else _NEGATIVE_CACHE_TTL_S
            if age <= ttl:
                return _memory_index
        cached = _read_disk_cache()
        if cached:
            _memory_index = cached
            _memory_stamp = time.time()
            return _memory_index
        fetched = _fetch_index()
        if fetched:
            _write_disk_cache(fetched)
            _memory_index = fetched
            _memory_stamp = time.time()
            return _memory_index
        # Cache the miss briefly in memory only, so a dead network does not put an 8s timeout on
        # every token of every price question. Not persisted -- a cold start retries immediately.
        _memory_index = {}
        _memory_stamp = time.time()
        return _memory_index


def warm_index() -> bool:
    """Populate the cache ahead of a request. True when the index is usable."""
    return bool(_index())


def reset_cache_for_test() -> None:
    global _memory_index, _memory_stamp
    with _lock:
        _memory_index = None
        _memory_stamp = 0.0


def resolve_symbol(token: str) -> str:
    """CoinGecko coin id for `token`, or "" when it is not a top-ranked coin symbol."""
    candidate = str(token or "").strip().lower()
    if not candidate or candidate in _NEVER_A_TICKER:
        return ""
    if not _TOKEN_RE.fullmatch(candidate):
        return ""
    return _index().get(candidate, "")


def resolve_tokens(text: str, *, skip_spans: list[tuple[int, int]] | None = None) -> list[tuple[int, str]]:
    """Every (position, coin id) the text names by ticker, left to right.

    `skip_spans` lets a caller exclude character ranges an earlier, higher-priority matcher already
    claimed -- so the static alias table keeps precedence and a coin is never resolved twice.
    """
    lowered = str(text or "").lower()
    claimed = list(skip_spans or [])
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(lowered):
        start, end = match.span()
        if any(start >= s and end <= e for s, e in claimed):
            continue
        coin_id = resolve_symbol(match.group(0))
        if not coin_id or coin_id in seen:
            continue
        seen.add(coin_id)
        found.append((start, coin_id))
    return found


def index_size() -> int:
    """Number of symbols currently resolvable. 0 means the index is cold or unreachable."""
    return len(_index())


def describe_index() -> dict[str, Any]:
    """Diagnostics for the runtime status surface -- never raises."""
    return {
        "symbols": index_size(),
        "max_rank": _MAX_RANK,
        "cache_path": str(_cache_path()),
        "ttl_seconds": _CACHE_TTL_S,
    }
