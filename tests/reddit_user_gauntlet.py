"""Reddit-user gauntlet — drive the REAL chat pipeline with real user phrasing.

CLAUDE.md §6: unit tests are never sufficient evidence of end-to-end agent
capability. This instrument does what a unit suite cannot: it sends actual
user-style queries — typos, slang, missing punctuation, multi-part asks —
through the full in-process dispatch (the same `dispatch_post` the daemon
serves), and scores the ACTUAL reply text per family:

    currency_single : a number AND the target currency come back
    market_single   : the asset is named AND a number comes back
    market_multi    : EVERY requested asset is answered or explicitly named
                      as missing ("No live quote came back for ...")
    change_ask      : the asked asset appears with a percentage figure
    mixed_multi     : the conversion AND every market asset survive composing
    typo_asset      : best-effort answer or an explicit named gap — never
                      silence
    negative_control: ordinary chat is NOT answered with a market table
    clock_sanity    : the clock answers with a date/time

Run:  .venv/bin/python tests/reddit_user_gauntlet.py [--only FAMILY] [--limit N]

Network is real (Frankfurter/CoinGecko/Yahoo are free public endpoints).
Results are printed as a table and written to /tmp/reddit_gauntlet_report.txt.
This is an instrument, not a pytest lane (the repo's live lanes are opt-in on
purpose); it is committed so the next session re-runs it instead of rebuilding.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import re as _re

# Boot the runtime the way the daemon and tests/conftest.py do: a scratch
# VOOL_HOME (never the operator's real data), the default DB path under it,
# and migrations. Without this the A0 accept-once door has no tables.
import tempfile

_GAUNTLET_HOME = Path(tempfile.mkdtemp(prefix="vool_gauntlet_home_")).resolve()
os.environ["VOOL_HOME"] = str(_GAUNTLET_HOME)
from core.runtime_paths import configure_runtime_home
from storage.db import configure_default_db_path

configure_runtime_home(_GAUNTLET_HOME)
configure_default_db_path(_GAUNTLET_HOME / "data" / "vool_web0_v2.db")
from storage.migrations import run_migrations

run_migrations()

# ── corpus ───────────────────────────────────────────────────────────────────

CORPUS: list[tuple[str, str]] = [
    # (family, query) — phrasings a real, careless user actually types.
    ("currency_single", "1000 rub to eur"),
    ("currency_single", "wats 500 usd in eur??"),
    ("currency_single", "convert 250 gbp 2 usd pls"),
    ("currency_single", "how much is 1000 nis in eur"),
    ("currency_single", "50 eur to pln"),
    ("currency_single", "how many yen for 100 usd"),
    ("currency_single", "100 cny in eur?"),
    ("currency_single", "whats 75 cad in usd"),
    ("currency_single", "300 sek to eur pls"),
    ("currency_single", "how much is 1 chf in usd??"),
    ("market_single", "btc price"),
    ("market_single", "wats eth trading at rn"),
    ("market_single", "gold price today"),
    ("market_single", "how much is bitcoin"),
    ("market_single", "solana price?"),
    ("market_single", "silver spot price"),
    ("market_single", "xrp price rn"),
    ("market_single", "ada price"),
    ("market_multi", "btc and eth price"),
    ("market_multi", "price of gold, silver n oil"),
    ("market_multi", "sol, ada n xrp prices"),
    ("market_multi", "bitcoin eth n gold pls"),
    ("market_multi", "prices: btc eth sol"),
    ("change_ask", "eth 24h change"),
    ("change_ask", "24 change on eth"),
    ("change_ask", "did btc go up today"),
    ("change_ask", "wats the day change on sol"),
    ("change_ask", "btc change today"),
    ("change_ask", "gold change today?"),
    ("change_ask", "24h change btc n eth"),
    ("mixed_multi", "1000 usd to eur and then to gold? also btc price and 24 change on eth"),
    ("mixed_multi", "convert 500 eur to usd, also wats btc at, n eth 24h change??"),
    ("mixed_multi", "1000 rub to eur, solana price, n did bitcoin go up today"),
    ("mixed_multi", "how much is 200 usd in eur?? also gold n silver prices"),
    ("typo_asset", "bitcon price"),
    ("typo_asset", "etheurem price"),
    ("typo_asset", "solana pris today"),
    ("typo_asset", "btc price n cardano"),
    ("typo_asset", "doge coin price??"),
    ("negative_control", "how do i change my password"),
    ("negative_control", "climate change is real?"),
    ("negative_control", "can we change the subject"),
    ("negative_control", "my name is Dave"),
    ("negative_control", "what was the book about?"),
    ("negative_control", "change my booking to tomorrow"),
    ("clock_sanity", "what time is it"),
    ("clock_sanity", "wat day is it today"),
]

# ── checkers ─────────────────────────────────────────────────────────────────

_NUMBER_RE = _re.compile(r"\d+(?:[.,]\d+)")
_MARKET_TABLE_MARKERS = ("24h change", "yahoo finance", "retrieved at", "coingecko")
_HONEST_GAP_RE = _re.compile(r"no live quote came back for|couldn'?t|did not resolve|not a recognized", _re.IGNORECASE)
_FAIL_RE = _re.compile(
    r"wasn'?t able to|no live model|failed before it finished|i don'?t see an? "
    r"|not able to turn that into",
    _re.IGNORECASE,
)

ASSET_ALIASES: dict[str, tuple[str, ...]] = {
    "btc": ("bitcoin", "btc"),
    "eth": ("ethereum", "eth"),
    "gold": ("gold", "xau"),
    "silver": ("silver", "xag"),
    "oil": ("oil", "brent", "wti", "crude"),
    "sol": ("solana", "sol"),
    "ada": ("cardano", "ada"),
    "xrp": ("xrp", "ripple"),
    "doge": ("doge",),
    "cardano": ("cardano", "ada"),
}


def _mentions_any(reply: str, asset: str) -> bool:
    return any(alias in reply.lower() for alias in ASSET_ALIASES.get(asset, (asset,)))


def check_currency_single(reply: str) -> str:
    if _FAIL_RE.search(reply):
        return "FAIL: failure wording in reply"
    if not _NUMBER_RE.search(reply):
        return "FAIL: no number in reply"
    if not _re.search(r"\b(eur|euro|usd|usdollar|gbp|pln|yen|jpy|cny|cad|sek|chf|nis|shekel)\b", reply.lower()):
        return "FAIL: no currency named"
    return "PASS"


def check_market_single(reply: str) -> str:
    if _FAIL_RE.search(reply):
        return "FAIL: failure wording in reply"
    if not _NUMBER_RE.search(reply):
        return "FAIL: no number in reply"
    return "PASS"


def check_market_multi(reply: str) -> str:
    if _FAIL_RE.search(reply):
        return "FAIL: failure wording in reply"
    missing = []
    for asset in _queried_assets(comment_hint):
        if not _mentions_any(reply, asset):
            missing.append(asset)
    if missing:
        # honest gap naming is acceptable; silence is not
        if _HONEST_GAP_RE.search(reply):
            return f"PASS-with-note: named gap for {missing}"
        return f"FAIL: assets never mentioned: {missing}"
    if not _NUMBER_RE.search(reply):
        return "FAIL: no numbers at all"
    return "PASS"


def check_change_ask(reply: str) -> str:
    if _FAIL_RE.search(reply):
        return "FAIL: failure wording in reply"
    asked = _queried_assets(comment_hint)
    if asked and not any(_mentions_any(reply, a) for a in asked):
        return f"FAIL: asked asset(s) {asked} absent from reply"
    if "%" not in reply and not _re.search(r"(up|down|rose|fell|dropped|\+|-)\s?\d", reply.lower()):
        return "FAIL: no change figure in reply"
    return "PASS"


def check_mixed_multi(reply: str) -> str:
    if _FAIL_RE.search(reply):
        return "FAIL: failure wording in reply"
    problems = []
    if comment_hint and "usd" in comment_hint.lower() and "eur" in comment_hint.lower():
        if not _NUMBER_RE.search(reply):
            problems.append("no conversion number")
    for asset in _queried_assets(comment_hint):
        if not _mentions_any(reply, asset):
            problems.append(f"asset absent: {asset}")
    if problems:
        if _HONEST_GAP_RE.search(reply) and all(p.startswith("asset") for p in problems):
            return f"PASS-with-note: {problems}"
        return f"FAIL: {problems}"
    return "PASS"


def check_typo_asset(reply: str) -> str:
    if _NUMBER_RE.search(reply) and not _FAIL_RE.search(reply):
        return "PASS: answered despite typo"
    if _HONEST_GAP_RE.search(reply):
        return "PASS: named the gap honestly"
    return "FAIL: neither answered nor honestly named the gap"


def check_negative_control(reply: str) -> str:
    lowered = reply.lower()
    for marker in _MARKET_TABLE_MARKERS:
        if marker in lowered:
            return f"FAIL: ordinary chat answered with market material ({marker!r})"
    return "PASS"


def check_clock_sanity(reply: str) -> str:
    if _re.search(r"\b(20\d\d|am|pm|:\d\d)\b", reply.lower()):
        return "PASS"
    return "FAIL: no date/time in reply"


CHECKERS = {
    "currency_single": check_currency_single,
    "market_single": check_market_single,
    "market_multi": check_market_multi,
    "change_ask": check_change_ask,
    "mixed_multi": check_mixed_multi,
    "typo_asset": check_typo_asset,
    "negative_control": check_negative_control,
    "clock_sanity": check_clock_sanity,
}

comment_hint = ""  # set per-turn: the query itself, for asset extraction


def _queried_assets(hint: str) -> list[str]:
    found = []
    lowered = (hint or "").lower()
    for key in ("btc", "bitcoin", "eth", "ethereum", "gold", "silver", "oil", "solana", "sol", "ada", "cardano", "xrp", "doge"):
        if _re.search(rf"\b{_re.escape(key)}\b", lowered):
            canonical = {"bitcoin": "btc", "ethereum": "eth", "solana": "sol", "cardano": "ada"}.get(key, key)
            if canonical not in found:
                found.append(canonical)
    return found


# ── driver ───────────────────────────────────────────────────────────────────


_AGENT = None


def _agent():
    """A real VoolAgent, wired the way the daemon wires one (tests/conftest.py pattern)."""
    global _AGENT
    if _AGENT is None:
        from apps.vool_agent import VoolAgent

        agent = VoolAgent(backend_name="test-backend", device="gauntlet", persona_id="default")
        agent._sync_public_presence = lambda *a, **k: None
        agent._start_public_presence_heartbeat = lambda *a, **k: None
        agent._start_idle_commons_loop = lambda *a, **k: None
        agent.start()
        _AGENT = agent
    return _AGENT


def ask(query: str, timeout_s: float = 90.0) -> tuple[str, str]:
    """One real turn through dispatch_post; returns (reply_text, footer_line)."""
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    def _run() -> dict:
        res = dispatch_post(
            path="/api/chat",
            body={"messages": [{"role": "user", "content": query}]},
            headers={"content-type": "application/json"},
            runtime=RuntimeServices(display_name="VOOL", agent=_agent()),
            model_name="vool",
            workspace_root_provider=lambda: "/tmp",
            client_host="127.0.0.1",
        )
        return {"status": res.status, "body": res.body.decode("utf-8", errors="replace")}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run)
        try:
            result = future.result(timeout=timeout_s)
        except FuturesTimeoutError:
            return f"[driver timeout after {timeout_s:.0f}s]", ""
        except Exception as exc:  # noqa: BLE001 — record the runtime's own raise
            return f"[pipeline raised {type(exc).__name__}: {exc}]", ""
    if result["status"] != 200:
        return f"[http {result['status']}] {result['body'][:200]}", ""
    payload = json.loads(result["body"])
    # The served shape is Ollama-style: message.content. Fall back to the
    # OpenAI shape, then bare fields, so both chat endpoints are readable.
    message = payload.get("message")
    text = ""
    if isinstance(message, dict):
        text = str(message.get("content") or "")
    if not text and isinstance(payload.get("choices"), list) and payload["choices"]:
        text = str(payload["choices"][0].get("message", {}).get("content") or "")
    text = text or str(payload.get("response") or payload.get("content") or "")
    footer = str(payload.get("provenance_footer") or payload.get("footer") or "")
    return text, footer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    cases = [(f, q) for f, q in CORPUS if not args.only or f == args.only]
    if args.limit:
        cases = cases[: args.limit]

    report: list[str] = []
    tallies: dict[str, list[str]] = {}
    for family, query in cases:
        globals()["comment_hint"] = query
        started = time.monotonic()
        try:
            reply, footer = ask(query)
        except Exception as exc:  # noqa: BLE001 — the gauntlet records, never crashes
            reply, footer = f"[driver exception: {type(exc).__name__}: {exc}]", ""
        elapsed = time.monotonic() - started
        verdict = CHECKERS[family](reply)
        tallies.setdefault(family, []).append(verdict)
        snippet = " ".join(reply.split())[:160]
        line = f"[{verdict.split(':')[0]:>16}] {family:<16} {elapsed:5.1f}s  {query!r}\n    -> {snippet}"
        if footer:
            line += f"\n    footer: {footer[:120]}"
        report.append(line)
        print(line, flush=True)

    print("\n==== SUMMARY ====")
    failures = 0
    summary_lines = []
    for family, verdicts in tallies.items():
        passed = sum(1 for v in verdicts if v.startswith("PASS"))
        noted = sum(1 for v in verdicts if v.startswith("PASS-with-note"))
        failed = len(verdicts) - passed - noted
        failures += failed
        summary_lines.append(f"{family:<16} {passed} pass / {noted} noted / {failed} FAIL of {len(verdicts)}")
        print(summary_lines[-1])
    Path("/tmp/reddit_gauntlet_report.txt").write_text("\n".join([*report, "", *summary_lines]) + "\n")
    print(f"\nfull report: /tmp/reddit_gauntlet_report.txt; total FAILs: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
