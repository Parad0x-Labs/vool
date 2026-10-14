"""Collect a token's trade data locally, then compute every pattern and thesis check from it.

``market.collect`` answers "collect the last 7 days of trades for <token>": price/volume candles and
the most liquid pool's trades from the same public DEX data ``token.performance`` reads (the web
lookups switch gates them), plus the token's transfer history through the user's own chain-data key
(the same slots and the same one outbound door ``core/chain_data`` uses). The rows land in local
SQLite tables through the same DB helper the notification centre uses; in a project chat a CSV copy
per source is written into the project's own folder. ``market.analyze`` and ``market.thesis_check``
then answer "what patterns do you see" and "check my thesis" from the stored rows.

Rules this holds:

* Read-only market data. Nothing here signs, pays, trades, moves money or touches the wallet.
* Facts only. Every number is computed from stored rows; the chat model only explains them. A
  missing value is named as missing ("not enough data", "rate limited", "no key"), never guessed,
  never 0. Every metric carries its row count.
* Sources are independent and partial is honest: web off means no public candles or trades (the
  answer says how to turn them on), no key means no transfers (the answer says which key would
  read them). What did come is kept and labelled with its exact coverage -- from/to time, rows per
  source, and where paging stopped ("stopped at page 3 of ?: rate limited").
* Fail closed: nothing available, every source failed, a bad address, an unknown chain, a window
  out of range -- a structured refusal with the plain next step, and nothing stored.
* Rate limits: a bounded page budget per collect for every provider; an HTTP 429 stops that
  source's paging (that is the back-off) and is recorded, never looped on.
* Bounded storage: at most 200,000 rows per dataset and 20 datasets; the oldest unpinned dataset
  is dropped when a new one arrives, and the answer says which.
"""
from __future__ import annotations

import csv
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import chain_providers as cp
from core import token_performance as tp

SURFACE = "chain"
COLLECT = "market.collect"
DATASETS = "market.datasets"
DATASET_DELETE = "market.dataset_delete"
ANALYZE = "market.analyze"
THESIS_CHECK = "market.thesis_check"
INTENTS = (COLLECT, DATASETS, DATASET_DELETE, ANALYZE, THESIS_CHECK)

DEFAULT_WINDOW_S = 24 * 3600
MIN_WINDOW_S = 3600
MAX_WINDOW_S = 30 * 86400
#: The per-collect page budgets: every provider stops after its own budget, and an HTTP 429 stops
#: the source outright. GeckoTerminal's own ceiling is 30 calls/minute, so 1 pool page + 3 candle
#: pages + 10 trade pages stay under it for one collect.
MAX_TRADES_PAGES = 10
MAX_TRANSFER_TX_READS = 100
MAX_DATASETS = 20
MAX_ROWS = 200_000
#: Below this many stored rows the analysis refuses every metric: with 5 rows any "pattern" is
#: noise wearing a number.
MIN_ANALYZE_ROWS = 6
TABLE_CAP = 6_000
MAX_CLAIMS = 20

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SVM_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_WINDOW_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(h|hour|hours|d|day|days|w|week|weeks)s?\s*$", re.I)
_clock = time.time
_LOCK = threading.RLock()

_WEB_OFF_TEXT = ("Collecting trades uses public DEX candles and pool trades (which need web access on) and/or the "
                 "token's transfer history through a saved chain-data key. Turn on web access in Settings and, for "
                 "transfer history, add a Helius, Alchemy or Etherscan key in Settings → API Keys, then ask again. "
                 "Nothing was collected.")

#: The metric names ``market.analyze`` defines; ``market.thesis_check`` accepts only these. A name
#: with a ``:<argument>`` (a date, a wallet) takes its value from that argument's rows.
METRIC_NAMES = (
    "window_return_pct", "daily_return_pct:<UTC date>", "volatility_pct", "volume_spike_count",
    "buy_count", "sell_count", "buy_usd_total", "sell_usd_total", "buy_share_pct",
    "net_usd_flow:<wallet>", "trade_count:<wallet>", "top10_volume_share_pct",
    "first_buyers_count", "early_buyers_still_holding", "buyers_before_top_rises_count",
    "max_hourly_price_rise_pct", "rows_candles", "rows_trades", "rows_transfers",
)
_METRIC_BASES = {name.split(":")[0] for name in METRIC_NAMES}


# --------------------------------------------------------------------------- the tool contracts

def market_contracts() -> list[Any]:
    from core.runtime_tool_contracts import RuntimeToolContract

    thesis_metrics = ", ".join(METRIC_NAMES)
    return [
        RuntimeToolContract(
            intent=COLLECT,
            description=(
                "Collect a token's trade data into a local dataset that market.analyze and market.thesis_check read: "
                "price/volume candles and the most liquid pool's trades from public DEX data (needs web access on), "
                "plus the token's transfer history through the user's own chain-data key (Helius, Alchemy or Etherscan, "
                "whichever is saved). Only the sources that are allowed run, and the answer states exactly what it got "
                "and where paging stopped. Give the token contract address, the chain when it is not Solana, and a "
                "window from 1h to 30d (default 24h). Read-only market data: nothing is signed or sent. Say the dataset "
                "id back to the user; keep it for the analysis tools."
            ),
            tool_surface=SURFACE,
            capability_id="web.market_data",
            capability_claim="collect a token's candles, pool trades and transfer history into a local dataset",
            supported=True,
            unsupported_reason="",
            input_schema={
                "token": "str: token contract address",
                "chain": "str (optional): solana | ethereum | base | bnb | robinhood",
                "window": "str (optional): 1h to 30d, e.g. 24h, 7d, 30d (default 24h)",
            },
            output_schema={"dataset": "object (id, token, chain, window, coverage)", "rows": "int"},
            side_effect_class="creative_state",
            approval_requirement="none",
            timeout_policy="network_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=("read_files", "use_network_access"),
            renders_final_answer=False,
        ),
        RuntimeToolContract(
            intent=DATASETS,
            description=(
                "List the market datasets collected in this VOOL: id, token, chain, window, when, row counts per "
                "source, and whether coverage is partial. Use a dataset's id with market.analyze or market.thesis_check."
            ),
            tool_surface=SURFACE,
            capability_id="web.market_data",
            capability_claim="list the collected market datasets",
            supported=True,
            unsupported_reason="",
            input_schema={},
            output_schema={"datasets": "list"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=("read_files",),
            renders_final_answer=False,
        ),
        RuntimeToolContract(
            intent=DATASET_DELETE,
            description=(
                "Delete one collected market dataset (dataset_id) and its rows from VOOL's local storage. A CSV copy "
                "written into a project folder stays on disk. Nothing is sent anywhere."
            ),
            tool_surface=SURFACE,
            capability_id="web.market_data",
            capability_claim="delete a collected market dataset",
            supported=True,
            unsupported_reason="",
            input_schema={"dataset_id": "str: the dataset to delete (from market.datasets)"},
            output_schema={"deleted": "str (the dataset id)"},
            side_effect_class="creative_state",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=("read_files",),
            renders_final_answer=False,
        ),
        RuntimeToolContract(
            intent=ANALYZE,
            description=(
                "Compute patterns from a dataset market.collect saved. Code computes every number: return over the "
                "window and per day; volatility; volume spikes (hourly volume z-score > 3, with times); buy/sell count "
                "and USD per hour and the buy share; top 20 wallets by net USD flow and by trade count; the first 50 "
                "buyers and whether they still hold (net flow); wallets that bought within 1h before the 3 biggest "
                "hourly price rises; concentration of traded USD in the top 10 wallets. Every metric names its row "
                "count; a metric with too few rows says \"not enough data\", never a number. Give dataset_id (from "
                "market.datasets) and optionally focus (a word like flows, spikes, buyers)."
            ),
            tool_surface=SURFACE,
            capability_id="web.market_analysis",
            capability_claim="compute pattern metrics from a collected market dataset",
            supported=True,
            unsupported_reason="",
            input_schema={"dataset_id": "str: the dataset to analyze", "focus": "str (optional): a section word"},
            output_schema={"metrics": "object", "sections": "list"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=("read_files",),
            renders_final_answer=False,
        ),
        RuntimeToolContract(
            intent=THESIS_CHECK,
            description=(
                "Check a user's thesis about a collected dataset by evaluating claims written with ONLY the metric "
                f"names market.analyze defines: {thesis_metrics}. "
                "Each claim is an object {metric, comparator (> < >= <= = within), value, tolerance? (a number, for = "
                "and within), window? (e.g. 24h: the last 24h of the dataset), note? (the user's words for this claim)}. "
                "Turn the thesis into checkable claims first; never invent a metric name. Code evaluates each claim "
                "from the stored rows: supported, contradicted, or not checkable (an unknown metric, or not enough "
                "data for it) -- never coerced. The reply shows each claim as written next to its verdict, so the user "
                "sees what was actually tested."
            ),
            tool_surface=SURFACE,
            capability_id="web.market_analysis",
            capability_claim="evaluate thesis claims as metrics against a collected dataset",
            supported=True,
            unsupported_reason="",
            input_schema={
                "dataset_id": "str: the dataset the thesis is about",
                "claims": "list (1-20) of {metric, comparator, value, tolerance?, window?, note?}",
            },
            output_schema={"verdicts": "list"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=("read_files",),
            renders_final_answer=False,
        ),
    ]


def run_tool(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None = None):
    from core.execution.models import _tool_observation
    from core.runtime_execution_tools import RuntimeExecutionResult

    args = dict(arguments or {})
    if intent == COLLECT:
        out = _collect(args, source_context)
    elif intent == DATASETS:
        out = _list_datasets()
    elif intent == DATASET_DELETE:
        out = _delete_dataset(args)
    elif intent == ANALYZE:
        out = _analyze(args)
    elif intent == THESIS_CHECK:
        out = _thesis_check(args)
    else:
        out = {"ok": False, "status": "unsupported_intent", "text": "That is not a market data tool."}
    observation = _tool_observation(intent=intent, tool_surface=SURFACE, ok=out["ok"], status=out["status"],
                                    dataset=str(out.get("dataset") or ""))
    details = {key: value for key, value in out.items() if key != "text"}
    details["observation"] = observation
    return RuntimeExecutionResult(handled=True, ok=out["ok"], status=out["status"], response_text=out["text"],
                                  details=details)


# --------------------------------------------------------------------------- small helpers

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(ts: int | float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _iso_date(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _ts_of(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN never becomes a fact


def _money(value: float | None) -> str:
    if value is None:
        return "?"
    return f"${value:,.0f}" if abs(value) >= 1000 else f"${value:,.2f}"


def _window_of(value: object) -> tuple[int | None, str]:
    """(seconds, canonical text) for 1h..30d, or (None, why-not)."""
    if value is None or value == "":
        return DEFAULT_WINDOW_S, "24h"
    if isinstance(value, bool):
        return None, "the window must be like 12h or 7d"
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        try:
            hours = int(value)
        except (TypeError, ValueError):
            return None, "the window must be like 12h or 7d"
        seconds = hours * 3600
        text = f"{hours}h"
    else:
        match = _WINDOW_RE.match(str(value))
        if not match:
            return None, "the window must be like 12h or 7d"
        size, unit = float(match.group(1)), match.group(2).lower()
        seconds = int(size * {"h": 3600, "d": 86400, "w": 7 * 86400}[unit[0]])
        size_text = match.group(1)
        text = f"{size_text}{unit[0]}"
    if not MIN_WINDOW_S <= seconds <= MAX_WINDOW_S:
        return None, f"the window can be 1h to 30d; {text} is outside that"
    return seconds, text


def _web_on(source_context: dict[str, Any] | None) -> bool:
    try:
        from core.remote_fetch_policy import effective_web_available

        return bool(effective_web_available(source_context))
    except Exception:
        return False


def _public(*values: str) -> None:
    from core.secret_redaction import register_public_identifier

    for value in values:
        if value:
            register_public_identifier(value)


def _short(token: str) -> str:
    return f"{token[:6]}…{token[-4:]}" if len(token) > 16 else token


def _result(ok: bool, status: str, text: str, **details: Any) -> dict[str, Any]:
    return {"ok": ok, "status": status, "text": text, **details}


# --------------------------------------------------------------------------- storage


def _conn():
    from storage.db import get_connection

    return get_connection()


def _init_tables() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_datasets (
                id TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                chain TEXT NOT NULL,
                window TEXT NOT NULL,
                created_at TEXT NOT NULL,
                coverage_json TEXT NOT NULL,
                chat_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_rows (
                dataset_id TEXT NOT NULL,
                source TEXT NOT NULL,
                ts INTEGER NOT NULL,
                kind TEXT NOT NULL,
                wallet TEXT NOT NULL DEFAULT '',
                amount REAL,
                usd REAL,
                price REAL,
                tx TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_rows_dataset_ts ON market_rows(dataset_id, ts)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_analyses (
                dataset_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                focus TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL,
                PRIMARY KEY (dataset_id, created_at)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _dataset_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    try:
        coverage = json.loads(data.pop("coverage_json") or "{}")
    except (TypeError, ValueError):
        coverage = {}
    data["coverage"] = coverage if isinstance(coverage, dict) else {}
    return data


def _get_dataset(dataset_id: str) -> dict[str, Any] | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM market_datasets WHERE id = ?", (str(dataset_id or "").strip(),)).fetchone()
    finally:
        conn.close()
    return _dataset_row(row) if row else None


def _load_rows(dataset_id: str) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT source, ts, kind, wallet, amount, usd, price, tx FROM market_rows WHERE dataset_id = ? ORDER BY ts",
            (dataset_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"source": r["source"], "ts": int(r["ts"]), "kind": r["kind"], "wallet": r["wallet"],
         "amount": r["amount"], "usd": r["usd"], "price": r["price"], "tx": r["tx"]}
        for r in rows
    ]


def _store_dataset(token: str, chain: str, window_text: str, coverage: dict[str, Any], chat_id: str,
                   project_id: str, rows_by_source: dict[str, list[dict[str, Any]]]) -> tuple[str, list[dict[str, Any]]]:
    """Insert the dataset and its rows, then keep at most MAX_DATASETS (this chat's stay)."""
    with _LOCK:
        _init_tables()
        dataset_id = "md_" + uuid.uuid4().hex[:12]
        created = _utcnow()
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO market_datasets (id, token, chain, window, created_at, coverage_json, chat_id, project_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (dataset_id, token, chain, window_text, created, json.dumps(coverage, sort_keys=True), chat_id, project_id),
            )
            for source, rows in rows_by_source.items():
                conn.executemany(
                    "INSERT INTO market_rows (dataset_id, source, ts, kind, wallet, amount, usd, price, tx)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(dataset_id, source, r["ts"], r["kind"], r.get("wallet") or "", r.get("amount"),
                      r.get("usd"), r.get("price"), r.get("tx") or "") for r in rows],
                )
            conn.commit()
        finally:
            conn.close()
        dropped = _prune_datasets(chat_id)
    return dataset_id, dropped


def _prune_datasets(current_chat: str) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT id, token, created_at, chat_id FROM market_datasets ORDER BY created_at").fetchall()
        surplus = len(rows) - MAX_DATASETS
        if surplus <= 0:
            return []
        droppable = [r for r in rows if not (current_chat and r["chat_id"] == current_chat)]
        pinned = [r for r in rows if r not in droppable]
        victims = droppable[:surplus] + pinned[: max(0, surplus - len(droppable))]
        for row in victims:
            conn.execute("DELETE FROM market_rows WHERE dataset_id = ?", (row["id"],))
            conn.execute("DELETE FROM market_analyses WHERE dataset_id = ?", (row["id"],))
            conn.execute("DELETE FROM market_datasets WHERE id = ?", (row["id"],))
        conn.commit()
        return [{"id": row["id"], "token": row["token"], "created_at": row["created_at"]} for row in victims]
    finally:
        conn.close()


def _save_analysis(dataset_id: str, focus: str, result: dict[str, Any]) -> None:
    with _LOCK:
        _init_tables()
        conn = _conn()
        try:
            conn.execute("INSERT OR REPLACE INTO market_analyses (dataset_id, created_at, focus, result_json)"
                         " VALUES (?, ?, ?, ?)",
                         (dataset_id, _utcnow(), str(focus or "")[:60], json.dumps(result, sort_keys=True)))
            conn.commit()
        finally:
            conn.close()


def _write_csv_copies(project_id: str, token: str, rows_by_source: dict[str, list[dict[str, Any]]],
                      now: int) -> dict[str, str]:
    """A CSV copy per source, only when the chat belongs to a project whose folder exists."""
    if not project_id:
        return {}
    try:
        from core.project_store import project_root

        root = project_root(project_id)
    except Exception:
        return {}
    if not root:
        return {}
    folder = Path(root) / "market-data" / f"{token}-{_iso_date(now)}"
    files: dict[str, str] = {}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for source, rows in sorted(rows_by_source.items()):
            if not rows:
                continue
            path = folder / f"{source}.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["time_utc", "kind", "wallet", "amount", "usd", "price", "tx"])
                for row in rows:
                    writer.writerow([_iso(row["ts"]), row["kind"], row.get("wallet") or "",
                                     "" if row.get("amount") is None else row["amount"],
                                     "" if row.get("usd") is None else row["usd"],
                                     "" if row.get("price") is None else row["price"], row.get("tx") or ""])
            files[source] = str(path)
    except OSError:
        return files
    return files


# --------------------------------------------------------------------------- collect


def _collect(args: dict[str, Any], source_context: dict[str, Any] | None) -> dict[str, Any]:
    token = str(args.get("token") or args.get("address") or args.get("mint") or args.get("contract") or "").strip()
    named_chain = str(args.get("chain") or args.get("network") or "").strip()
    window_s, window_text = _window_of(args.get("window"))
    if not token or not (_EVM_RE.fullmatch(token) or _SVM_RE.fullmatch(token)):
        return _result(False, "bad_subject", "Give the token's contract address to collect trades for. Nothing was collected.")
    if window_s is None:
        return _result(False, "invalid_arguments", f"{window_text}. Nothing was collected.")
    chain = cp.resolve_chain(named_chain) if named_chain else ""
    if named_chain and not chain:
        return _result(False, "unknown_chain",
                       f"Collecting trades covers {', '.join(cp.CHAIN_LABELS[c] for c in cp.CHAINS)}; "
                       f"{named_chain[:40]!r} is not one of them. Nothing was collected.")
    if not chain:
        if token.startswith("0x"):
            return _result(False, "chain_needed",
                           "That address could be on Ethereum, Base, BNB Smart Chain or Robinhood Chain. Which chain is "
                           "it on? Nothing was collected.")
        chain = cp.CHAIN_SOLANA
    now = int(_clock())
    since = now - window_s
    context = source_context if isinstance(source_context, dict) else {}
    chat_id = str(context.get("runtime_session_id") or context.get("chat_id") or context.get("session_id") or "")
    project_id = str(context.get("_trusted_project_id") or context.get("project_id") or "")

    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    coverage: dict[str, Any] = {"token": token, "chain": chain, "window": window_text,
                                "from": _iso(since), "to": _iso(now), "sources": {}, "partial": False}
    web_on = _web_on(context)

    # -- public DEX candles and pool trades (the web lookups switch gates both) -------------
    if web_on:
        network = tp._NETWORKS.get(chain, "")
        if not network:
            coverage["sources"]["candles"] = coverage["sources"]["trades"] = {
                "status": "skipped", "rows": 0, "detail": "the public DEX source has no data for this chain"}
        else:
            pool: dict[str, Any] = {}
            try:
                pool = tp._top_pool(token, network)
                network = pool["network"]
            except tp.PriceError as exc:
                coverage["sources"]["candles"] = coverage["sources"]["trades"] = {
                    "status": "failed", "rows": 0, "detail": exc.message}
            if pool:
                coverage["pool"] = pool["address"]
                coverage["symbol"] = pool.get("symbol") or ""
                coverage["sources"]["candles"] = _collect_candles(network, pool, token, since, now, rows_by_source)
                coverage["sources"]["trades"] = _collect_trades(network, pool, token, since, now, rows_by_source)
    else:
        coverage["sources"]["candles"] = coverage["sources"]["trades"] = {
            "status": "skipped", "rows": 0, "detail": "web access is off (public DEX data needs it on)"}

    # -- transfer history through the user's own chain-data key ----------------------------
    coverage["sources"]["transfers"] = _collect_transfers(token, chain, since, now, rows_by_source)
    _price_transfer_rows(rows_by_source)

    total_rows = sum(len(rows) for rows in rows_by_source.values())
    if not total_rows:
        return _nothing_collected(coverage)
    if total_rows > MAX_ROWS:
        keep = total_rows - MAX_ROWS
        for source in ("transfers", "trades", "candles"):
            if keep <= 0:
                break
            take = min(keep, len(rows_by_source.get(source) or []))
            if take:
                rows_by_source[source] = rows_by_source[source][:len(rows_by_source[source]) - take]
                keep -= take
                coverage["sources"][source]["rows"] = len(rows_by_source[source])
                coverage["sources"][source]["stopped_reason"] = "row cap reached (200,000 rows per dataset)"
                coverage["sources"][source]["status"] = "partial"
        total_rows = sum(len(rows) for rows in rows_by_source.values())

    for source, rows in rows_by_source.items():
        entry = coverage["sources"].get(source) or {}
        entry["rows"] = len(rows)
        if rows:
            entry["from_row"] = _iso(min(r["ts"] for r in rows))
            entry["to_row"] = _iso(max(r["ts"] for r in rows))
        coverage["sources"][source] = entry
    succeeded = [name for name, rows in rows_by_source.items() if rows]
    coverage["partial"] = any((coverage["sources"][name] or {}).get("status") in ("partial", "failed")
                              or (coverage["sources"][name] or {}).get("stopped_reason")
                              for name in coverage["sources"]) or len(succeeded) < 3

    files = _write_csv_copies(project_id, token, rows_by_source, now)
    if files:
        coverage["files"] = files
    dataset_id, dropped = _store_dataset(token, chain, window_text, coverage, chat_id, project_id, rows_by_source)
    _public(token, *sorted({r["wallet"] for rows in rows_by_source.values() for r in rows if r.get("wallet")})[:30])

    who = coverage.get("symbol") or _short(token)
    lines = [f"Collected {window_text} of market data for {who} ({token}) on {cp.CHAIN_LABELS.get(chain, chain)}: "
             f"dataset {dataset_id}, {total_rows:,} row(s) stored locally."]
    for source in ("candles", "trades", "transfers"):
        entry = coverage["sources"].get(source) or {}
        line = f"- {source}: {entry.get('rows', 0):,} row(s)"
        if entry.get("from_row"):
            line += f", {entry['from_row']} to {entry['to_row']}"
        if entry.get("provider"):
            label = cp.config_for(str(entry["provider"]))
            line += f", via {label.label if label else entry['provider']}"
        if entry.get("status") == "failed":
            line += f" -- failed: {entry.get('detail') or 'no data'}"
        elif entry.get("status") == "skipped":
            line += f" -- not read: {entry.get('detail') or 'not allowed'}"
        elif entry.get("stopped_reason"):
            line += f" -- {entry['stopped_reason']}"
        lines.append(line)
    if coverage["partial"]:
        lines.append("Partial dataset: it is labelled with what is missing above.")
    if files:
        lines.append(f"CSV copies written into the project folder: {', '.join(sorted(files.values()))}.")
    for row in dropped:
        lines.append(f"Kept {MAX_DATASETS} datasets: removed the oldest, {row['id']} "
                     f"({_short(row['token'])}, collected {str(row['created_at'])[:10]}).")
    lines.append(f"Analyze it with market.analyze (dataset {dataset_id}).")
    return _result(True, "ok", "\n".join(lines), dataset=dataset_id, coverage=coverage, rows=total_rows)


def _nothing_collected(coverage: dict[str, Any]) -> dict[str, Any]:
    sources = coverage.get("sources") or {}
    if all((sources.get(name) or {}).get("status") == "skipped" for name in ("candles", "trades", "transfers")):
        return _result(False, "nothing_available", _WEB_OFF_TEXT + " Nothing was collected.", coverage=coverage)
    reasons = []
    for name in ("candles", "trades", "transfers"):
        entry = sources.get(name) or {}
        if entry.get("status") in ("failed", "skipped", "empty"):
            reasons.append(f"{name}: {entry.get('detail') or entry.get('status')}")
    text = ("No trade data could be collected for that token in that window, so nothing was stored. "
            "What each source said: " + "; ".join(reasons) + ".")
    return _result(False, "nothing_collected", text, coverage=coverage)


def _collect_candles(network: str, pool: dict[str, Any], token: str, since: int, now: int,
                     rows_by_source: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    try:
        timeframe, aggregate, step = tp._timeframe(now - since)
        candles = tp._candles(network, pool["address"], token, timeframe, aggregate, since, step)
    except tp.PriceError as exc:
        return {"status": "failed", "rows": 0, "detail": exc.message}
    rows = [{"ts": int(c[0]), "kind": "candle", "wallet": "", "amount": None,
             "usd": _float(c[5]), "price": _float(c[4]), "tx": ""}
            for c in candles if int(c[0]) >= since and _float(c[4])]
    if rows:
        rows_by_source["candles"] = rows
    entry: dict[str, Any] = {"status": "ok" if rows else "empty", "rows": len(rows)}
    if rows:
        entry["detail"] = f"{aggregate}-{timeframe} candles"
    else:
        entry["detail"] = "no candles in that window"
    return entry


def _pool_base_token(network: str, token: str) -> str:
    """The pool's base token address, which decides which side a trade's buy/sell names."""
    from urllib.parse import quote

    try:
        payload = tp._get(f"/networks/{network}/tokens/{quote(token)}/pools", {"page": 1})
    except Exception:
        return ""
    for pool in payload.get("data") or []:
        tokens = (((pool.get("relationships") or {}).get("tokens") or {}).get("data")) or []
        if isinstance(tokens, list) and tokens and isinstance(tokens[0], dict):
            return str(tokens[0].get("id") or "")
    return ""


def _collect_trades(network: str, pool: dict[str, Any], token: str, since: int, now: int,
                    rows_by_source: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    from urllib.parse import quote

    base = _pool_base_token(network, token)
    rows: list[dict[str, Any]] = []
    stopped = ""
    page_no = 1
    while page_no <= MAX_TRADES_PAGES:
        try:
            payload = tp._get(f"/networks/{network}/pools/{quote(pool['address'])}/trades", {"page": page_no})
        except tp.PriceError as exc:
            if rows:
                rows_by_source["trades"] = rows  # what page 1 brought is kept, labelled partial
            return {"status": "partial" if rows else "failed", "rows": len(rows),
                    "stopped_reason": f"stopped at page {page_no} of ?: {exc.message}", "detail": exc.message}
        data = [t for t in payload.get("data") or [] if isinstance(t, dict)]
        if not data:
            break
        oldest: int | None = None
        for trade in data:
            attrs = trade.get("attributes") or {}
            ts = _ts_of(attrs.get("block_timestamp"))
            if ts is None:
                continue
            oldest = ts if oldest is None else min(oldest, ts)
            if not since <= ts <= now + 300:
                continue
            kind = str(attrs.get("kind") or "").lower()
            usd = _float(attrs.get("volume_in_usd"))
            amount = None
            if kind in ("buy", "sell"):
                if base and base != token:
                    # the source's direction names the pool's base token; ours is the other side
                    kind = "sell" if kind == "buy" else "buy"
                amount = _float(attrs.get("to_amount") if kind == "buy" else attrs.get("from_amount"))
            price = usd / amount if usd is not None and amount else None
            rows.append({"ts": ts, "kind": kind, "wallet": str(attrs.get("tx_from_address") or ""),
                         "amount": amount, "usd": usd, "price": price, "tx": str(attrs.get("tx_id") or "")})
        if oldest is not None and oldest < since:
            break
        page_no += 1
    if page_no > MAX_TRADES_PAGES:
        stopped = f"stopped at page {MAX_TRADES_PAGES} of ?: page budget"
    if rows:
        rows_by_source["trades"] = rows
    entry: dict[str, Any] = {"status": "ok" if rows else "empty", "rows": len(rows), "stopped_reason": stopped}
    if not rows:
        entry["detail"] = "no trades in that window"
    return entry


def _collect_transfers(token: str, chain: str, since: int, now: int,
                       rows_by_source: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    from core import chain_data

    budget = MAX_ROWS - sum(len(rows) for rows in rows_by_source.values())
    if budget <= 0:
        return {"status": "skipped", "rows": 0, "detail": "row cap already reached"}
    out = chain_data.token_transfers(token, chain, since, until_ts=now, max_rows=budget,
                                     max_requests=MAX_TRANSFER_TX_READS)
    if out["status"] != "ok":
        detail = str(out.get("message") or out["status"])
        return {"status": "failed" if out["status"] not in ("key_missing", "not_offered") else "skipped",
                "rows": 0, "detail": detail, "provider": out.get("provider") or ""}
    rows = out["rows"]
    if rows:
        rows_by_source["transfers"] = rows
    entry: dict[str, Any] = {"status": "ok" if rows else "empty", "rows": len(rows),
                             "provider": out.get("provider") or "", "stopped_reason": str(out.get("stopped_reason") or "")}
    if not rows:
        entry["detail"] = "no transfers found in that window"
    return entry


def _price_transfer_rows(rows_by_source: dict[str, list[dict[str, Any]]]) -> None:
    """Give transfer rows a USD figure from the collected candles (the last close at or before
    each transfer). No candles, or no candle that old: the row keeps no USD, never a guess."""
    candles = sorted((r["ts"], r["price"]) for r in rows_by_source.get("candles") or [] if r.get("price"))
    if not candles:
        return
    times = [c[0] for c in candles]

    def price_at(ts: int) -> float | None:
        import bisect

        index = bisect.bisect_right(times, ts) - 1
        return candles[index][1] if index >= 0 else None

    for row in rows_by_source.get("transfers") or []:
        price = price_at(row["ts"])
        amount = row.get("amount")
        if price is not None and amount:
            row["price"] = price
            row["usd"] = round(price * amount, 6)


# --------------------------------------------------------------------------- datasets tools


def _list_datasets() -> dict[str, Any]:
    _init_tables()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT d.id, d.token, d.chain, d.window, d.created_at, d.coverage_json,"
            " (SELECT COUNT(*) FROM market_rows r WHERE r.dataset_id = d.id) AS rows"
            " FROM market_datasets d ORDER BY d.created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return _result(True, "ok", "No market datasets are collected yet. Collect one with market.collect.")
    datasets = []
    lines = [f"{len(rows)} market dataset(s) stored locally (newest first):"]
    for row in rows:
        data = _dataset_row(row)
        data["rows"] = int(row["rows"])
        datasets.append(data)
        sources = data["coverage"].get("sources") or {}
        counts = ", ".join(f"{name} {((sources.get(name) or {}).get('rows') or 0):,}"
                           for name in ("candles", "trades", "transfers") if sources.get(name))
        partial = " [partial]" if data["coverage"].get("partial") else ""
        lines.append(f"- {data['id']}: {_short(data['token'])} on {cp.CHAIN_LABELS.get(data['chain'], data['chain'])}, "
                     f"window {data['window']}, {data['rows']:,} row(s) ({counts}), collected "
                     f"{str(data['created_at'])[:19].replace('T', ' ')} UTC{partial}")
    return _result(True, "ok", "\n".join(lines), datasets=datasets)


def _delete_dataset(args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = str(args.get("dataset_id") or args.get("id") or "").strip()
    if not dataset_id:
        return _result(False, "missing_dataset", "Give the dataset_id to delete (list them with market.datasets).")
    _init_tables()
    dataset = _get_dataset(dataset_id)
    if not dataset:
        return _result(False, "unknown_dataset", f"There is no market dataset {dataset_id}. Nothing was deleted.")
    with _LOCK:
        conn = _conn()
        try:
            rows = conn.execute("SELECT COUNT(*) FROM market_rows WHERE dataset_id = ?", (dataset_id,)).fetchone()
            conn.execute("DELETE FROM market_rows WHERE dataset_id = ?", (dataset_id,))
            conn.execute("DELETE FROM market_analyses WHERE dataset_id = ?", (dataset_id,))
            conn.execute("DELETE FROM market_datasets WHERE id = ?", (dataset_id,))
            conn.commit()
        finally:
            conn.close()
    text = (f"Deleted dataset {dataset_id} ({_short(dataset['token'])}, window {dataset['window']}) and its "
            f"{int(rows[0]):,} row(s).")
    files = (dataset.get("coverage") or {}).get("files") or {}
    if files:
        text += f" The CSV copies in the project folder stay on disk: {', '.join(sorted(files.values()))}."
    return _result(True, "ok", text, deleted=dataset_id)


# --------------------------------------------------------------------------- analysis


def _analyze(args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = str(args.get("dataset_id") or args.get("id") or "").strip()
    focus = str(args.get("focus") or "").strip().lower()
    if not dataset_id:
        return _result(False, "missing_dataset", "Give the dataset_id to analyze (list them with market.datasets).")
    _init_tables()
    dataset = _get_dataset(dataset_id)
    if not dataset:
        return _result(False, "unknown_dataset", f"There is no market dataset {dataset_id}.")
    rows = _load_rows(dataset_id)
    metrics = _compute_metrics(dataset, rows)
    _save_analysis(dataset_id, focus, metrics)
    text = _render_analysis(dataset, metrics, rows, focus)
    _public(dataset["token"], *sorted({r["wallet"] for r in rows if r.get("wallet")})[:30])
    return _result(True, "ok", text, dataset=dataset_id, metrics=metrics)


def _source_rows(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    return [r for r in rows if r["source"] == source]


def _compute_metrics(dataset: dict[str, Any], rows: list[dict[str, Any]],
                     window_s: int | None = None) -> dict[str, Any]:
    """Every number the analyze reply and the thesis checks read. Values are absent (never 0 or
    guessed) when their rows are missing or too few; each section records its row count."""
    import statistics

    if window_s:
        end = max((r["ts"] for r in rows), default=int(_clock()))
        rows = [r for r in rows if end - window_s <= r["ts"] <= end]
    candles = sorted(_source_rows(rows, "candles"), key=lambda r: r["ts"])
    trades = sorted(_source_rows(rows, "trades"), key=lambda r: r["ts"])
    transfers = _source_rows(rows, "transfers")
    metrics: dict[str, Any] = {"sections": [], "values": {}, "rows_total": len(rows)}
    values = metrics["values"]
    values["rows_candles"] = len(candles)
    values["rows_trades"] = len(trades)
    values["rows_transfers"] = len(transfers)
    if len(rows) < MIN_ANALYZE_ROWS:
        metrics["sections"].append({"title": "not enough data",
                                    "lines": [f"not enough data: {len(rows)} row(s) collected; the analysis needs at "
                                              f"least {MIN_ANALYZE_ROWS} before any number is computed"]})
        return metrics

    closes = [r["price"] for r in candles if r.get("price")]
    lines: list[str] = []
    if len(closes) >= 2:
        window_return = (closes[-1] / closes[0] - 1) * 100
        values["window_return_pct"] = round(window_return, 2)
        per_day: dict[str, list[float]] = {}
        for row in candles:
            if row.get("price"):
                per_day.setdefault(_iso_date(row["ts"]), []).append(row["price"])
        day_bits = []
        for day, prices in sorted(per_day.items()):
            if len(prices) >= 2:
                day_return = (prices[-1] / prices[0] - 1) * 100
                values[f"daily_return_pct:{day}"] = round(day_return, 2)
                day_bits.append(f"{day}: {day_return:+.1f}%")
        changes = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes)) if closes[i - 1]]
        if len(changes) >= 9:
            values["volatility_pct"] = round(statistics.stdev(changes), 2)
        hour_closes: dict[int, float] = {}
        for row in candles:
            if row.get("price"):
                hour_closes[row["ts"] - row["ts"] % 3600] = row["price"]
        rises = sorted(((hour_closes[h] / hour_closes[h - 3600] - 1) * 100, h)
                       for h in hour_closes if h - 3600 in hour_closes and hour_closes[h - 3600] > 0)
        biggest_rises = [r for r in rises if r[0] > 0][-3:]
        if biggest_rises:
            values["max_hourly_price_rise_pct"] = round(biggest_rises[-1][0], 2)
        lines.append(f"return over the window {values.get('window_return_pct')}%"
                     + (f"; per day: {', '.join(day_bits)}" if day_bits else "")
                     + (f"; volatility {values.get('volatility_pct')}% per candle" if "volatility_pct" in values else "")
                     + (f"; biggest hourly rise {values.get('max_hourly_price_rise_pct')}%"
                        if "max_hourly_price_rise_pct" in values else ""))
        metrics["sections"].append({"title": "prices", "rows": len(candles), "lines": lines,
                                    "biggest_rises": biggest_rises, "hour_closes": hour_closes})
    else:
        metrics["sections"].append({"title": "prices", "rows": len(candles),
                                    "lines": [f"not enough data: {len(candles)} candle row(s); return and volatility "
                                              f"need at least 2"]})

    # -- hourly volume and spikes ---------------------------------------------------------
    hourly: dict[int, float] = {}
    for row in candles:
        if row.get("usd"):
            hourly[row["ts"] - row["ts"] % 3600] = hourly.get(row["ts"] - row["ts"] % 3600, 0.0) + float(row["usd"])
    if len(hourly) >= 12:
        volumes = list(hourly.values())
        mean, deviation = statistics.mean(volumes), statistics.pstdev(volumes)
        spikes = sorted((h, v) for h, v in hourly.items() if deviation > 0 and (v - mean) / deviation > 3)
        values["volume_spike_count"] = len(spikes)
        spike_bits = [f"{_iso(h)} ({_money(v)})" for h, v in spikes[-10:]]
        lines = [f"{len(hourly)} hourly volume points, mean {_money(mean)}",
                 f"{len(spikes)} spike(s) above 3 sd: {', '.join(spike_bits) if spike_bits else 'none'}"]
        metrics["sections"].append({"title": "volume", "rows": len(candles), "lines": lines})
    else:
        metrics["sections"].append({"title": "volume", "rows": len(candles),
                                    "lines": [f"not enough data: {len(hourly)} hourly volume point(s); spike "
                                              f"detection needs at least 12"]})

    # -- buy/sell ---------------------------------------------------------------------------
    buys = [r for r in trades if r["kind"] == "buy"]
    sells = [r for r in trades if r["kind"] == "sell"]
    values["buy_count"] = len(buys)
    values["sell_count"] = len(sells)
    if trades:
        buy_usd = sum(r["usd"] for r in buys if r.get("usd"))
        sell_usd = sum(r["usd"] for r in sells if r.get("usd"))
        priced = sum(1 for r in trades if r.get("usd") is not None)
        values["buy_usd_total"] = round(buy_usd, 2)
        values["sell_usd_total"] = round(sell_usd, 2)
        if buy_usd + sell_usd > 0:
            values["buy_share_pct"] = round(buy_usd / (buy_usd + sell_usd) * 100, 1)
        lines = [f"{len(buys)} buy(s) {_money(buy_usd)} vs {len(sells)} sell(s) {_money(sell_usd)}"
                 f" ({priced} of {len(trades)} trade row(s) carry a USD figure)"]
        if "buy_share_pct" in values:
            lines.append(f"buy share {values['buy_share_pct']}% of traded USD")
        metrics["sections"].append({"title": "buy/sell", "rows": len(trades), "lines": lines})
    else:
        metrics["sections"].append({"title": "buy/sell", "rows": 0,
                                    "lines": ["not enough data: no buy/sell rows (the public trades source did not "
                                              "contribute to this dataset)"]})

    # -- wallet flows -----------------------------------------------------------------------
    flow: dict[str, float] = {}
    for row in trades + transfers:
        if row.get("usd") is None or not row.get("wallet"):
            continue
        sign = 1.0 if row["kind"] in ("buy", "in") else -1.0
        flow[row["wallet"]] = flow.get(row["wallet"], 0.0) + sign * float(row["usd"])
    for wallet, net in flow.items():
        values[f"net_usd_flow:{wallet}"] = round(net, 2)
    counts: dict[str, int] = {}
    for row in trades:
        if row.get("wallet"):
            counts[row["wallet"]] = counts.get(row["wallet"], 0) + 1
    for wallet, count in counts.items():
        values[f"trade_count:{wallet}"] = count
    usd_rows = sum(1 for r in trades + transfers if r.get("usd") is not None and r.get("wallet"))
    if flow:
        top_flow = sorted(flow.items(), key=lambda item: abs(item[1]), reverse=True)[:20]
        lines = [f"{len(flow)} wallet(s), {usd_rows} row(s) with a USD figure; net USD flow = bought + received - "
                 f"sold - sent, priced where a price was known"]
        lines += [f"{_short(wallet)} {'+' if net >= 0 else ''}{_money(net)}" for wallet, net in top_flow]
        metrics["sections"].append({"title": "net USD flow", "rows": usd_rows, "lines": lines})
    else:
        metrics["sections"].append({"title": "net USD flow", "rows": usd_rows,
                                    "lines": ["not enough data: no row carries both a wallet and a USD figure"]})
    if counts:
        top_count = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:20]
        metrics["sections"].append({"title": "trade count", "rows": len(trades),
                                    "lines": [f"{_short(wallet)} {count}" for wallet, count in top_count]})
    else:
        metrics["sections"].append({"title": "trade count", "rows": len(trades),
                                    "lines": ["not enough data: no trade rows, so no per-wallet trade counts"]})

    # -- first buyers ------------------------------------------------------------------------
    first_buy: dict[str, int] = {}
    net_token: dict[str, float] = {}
    for row in trades + transfers:
        if not row.get("wallet") or row.get("amount") is None or row["kind"] not in ("buy", "sell", "in", "out"):
            continue
        sign = 1.0 if row["kind"] in ("buy", "in") else -1.0
        net_token[row["wallet"]] = net_token.get(row["wallet"], 0.0) + sign * float(row["amount"])
        if row["source"] == "trades" and row["kind"] == "buy":
            first_buy[row["wallet"]] = min(first_buy.get(row["wallet"], row["ts"]), row["ts"])
    if first_buy:
        buyers = sorted(first_buy.items(), key=lambda item: item[1])[:50]
        holding = [wallet for wallet, _ in buyers if net_token.get(wallet, 0.0) > 0]
        values["first_buyers_count"] = len(buyers)
        values["early_buyers_still_holding"] = len(holding)
        metrics["sections"].append({"title": "first buyers", "rows": len(buys),
                                    "lines": [f"of the first {len(buyers)} buyer(s), {len(holding)} still hold by net "
                                              f"flow over the collected window",
                                              *(_short(wallet) for wallet, _ in buyers[:10])]})

    # -- buyers before the biggest hourly rises ----------------------------------------------
    biggest_rises = next((s.get("biggest_rises") or [] for s in metrics["sections"] if s.get("title") == "prices"), [])
    if biggest_rises and buys:
        early = set()
        for _pct, hour in biggest_rises:
            early.update(r["wallet"] for r in buys if r.get("wallet") and hour - 3600 <= r["ts"] < hour)
        values["buyers_before_top_rises_count"] = len(early)
        metrics["sections"].append({"title": "buyers before rises", "rows": len(buys),
                                    "lines": [f"{len(early)} wallet(s) bought within 1h before each of the "
                                              f"{len(biggest_rises)} biggest hourly rise(s)"
                                              + (f" ({', '.join(f'{pct:+.1f}% at {_iso(h)}' for pct, h in biggest_rises)})"
                                                 if biggest_rises else ""),
                                              *(_short(wallet) for wallet in sorted(early)[:10])]})

    # -- volume concentration -----------------------------------------------------------------
    wallet_volume: dict[str, float] = {}
    for row in trades + transfers:
        if row.get("usd") is not None and row.get("wallet"):
            wallet_volume[row["wallet"]] = wallet_volume.get(row["wallet"], 0.0) + float(row["usd"])
    if wallet_volume:
        total = sum(wallet_volume.values())
        top10 = sum(value for _, value in sorted(wallet_volume.items(), key=lambda item: item[1], reverse=True)[:10])
        if total > 0:
            values["top10_volume_share_pct"] = round(top10 / total * 100, 1)
            metrics["sections"].append({"title": "concentration", "rows": usd_rows,
                                        "lines": [f"the top 10 wallet(s) account for "
                                                  f"{values['top10_volume_share_pct']}% of the traded USD "
                                                  f"({_money(top10)} of {_money(total)}, {len(wallet_volume)} wallet(s))"]})
    return metrics


def _render_analysis(dataset: dict[str, Any], metrics: dict[str, Any], rows: list[dict[str, Any]], focus: str) -> str:
    coverage = dataset.get("coverage") or {}
    head = (f"Dataset {dataset['id']} ({_short(dataset['token'])} on {cp.CHAIN_LABELS.get(dataset['chain'], dataset['chain'])}, "
            f"window {dataset['window']}, collected {str(dataset['created_at'])[:19].replace('T', ' ')} UTC, "
            f"{len(rows)} row(s)")
    if coverage.get("partial"):
        head += ", partial coverage"
    head += "). Every number below is computed from the stored rows; each section names its row count."
    sections = metrics.get("sections") or []
    if focus:
        kept = [s for s in sections if focus in str(s.get("title") or "").lower()]
        if kept:
            sections = kept
        else:
            head += f" No section matched {focus!r}; showing everything."
    body = [head]
    for section in sections:
        row_note = f" ({section['rows']} row(s))" if section.get("rows") is not None else ""
        body.append(f"[{section['title']}{row_note}]")
        body.extend(f"- {line}" for line in section.get("lines") or [])
    body.append("The full result is saved with the dataset; check a thesis against it with market.thesis_check.")
    text = "\n".join(body)
    if len(text.encode("utf-8")) > TABLE_CAP:
        text = text[:TABLE_CAP].rsplit("\n", 1)[0] + "\n(table capped at 6 KB; the full result is saved with the dataset)"
    return text


# --------------------------------------------------------------------------- thesis check


_COMPARATORS = {">": lambda a, v: a > v, "<": lambda a, v: a < v, ">=": lambda a, v: a >= v,
                "<=": lambda a, v: a <= v, "=": lambda a, v, t: abs(a - v) <= t,
                "within": lambda a, v, t: abs(a - v) <= t}


def _thesis_check(args: dict[str, Any]) -> dict[str, Any]:
    dataset_id = str(args.get("dataset_id") or args.get("id") or "").strip()
    claims = args.get("claims")
    if not dataset_id:
        return _result(False, "missing_dataset", "Give the dataset_id the thesis is about (list them with market.datasets).")
    if not isinstance(claims, list) or not claims:
        return _result(False, "invalid_arguments",
                       "Write the thesis as claims first: a list of {metric, comparator, value} using the metric names "
                       "the market.analyze tool defines, then check them. Nothing was evaluated.")
    if len(claims) > MAX_CLAIMS:
        return _result(False, "too_many", f"That is {len(claims)} claims; write {MAX_CLAIMS} or fewer at a time.")
    _init_tables()
    dataset = _get_dataset(dataset_id)
    if not dataset:
        return _result(False, "unknown_dataset", f"There is no market dataset {dataset_id}.")
    rows = _load_rows(dataset_id)
    window_s, _why = _window_of(args.get("window")) if args.get("window") is not None else (None, "")
    verdicts = []
    for claim in claims:
        verdicts.append(_evaluate_claim(claim if isinstance(claim, dict) else {}, dataset, rows))
    lines = [f"Thesis check on dataset {dataset_id} ({_short(dataset['token'])}, window {dataset['window']}, "
             f"{len(rows)} row(s)). Each claim as written, next to what the stored rows say:"]
    for verdict in verdicts:
        lines.append(f"- {verdict['claim_text']} -> {verdict['verdict']}. {verdict['detail']}")
    lines.append("Supported means the rows agree with the claim; contradicted means they say the opposite; "
                 "not checkable means VOOL cannot compute that metric from this dataset.")
    return _result(True, "ok", "\n".join(lines), dataset=dataset_id, verdicts=verdicts, window_seconds=window_s)


def _evaluate_claim(claim: dict[str, Any], dataset: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric = str(claim.get("metric") or "").strip()
    comparator = str(claim.get("comparator") or "").strip()
    value = _float(claim.get("value"))
    note = str(claim.get("note") or "").strip()
    window_s, why = _window_of(claim.get("window")) if claim.get("window") is not None else (None, "")
    claim_text = json.dumps({k: v for k, v in claim.items() if k not in ("note",)}, sort_keys=True)
    if note:
        claim_text = f"{note} [{claim_text}]"
    base = metric.split(":")[0]

    def out(verdict: str, detail: str, actual: Any = None) -> dict[str, Any]:
        return {"claim_text": claim_text, "metric": metric, "verdict": verdict, "detail": detail, "actual": actual}

    if not metric:
        return out("not checkable", "the claim names no metric")
    if base not in _METRIC_BASES:
        return out("not checkable", f"VOOL does not compute {metric!r}; it computes only the metrics the "
                                    f"market.analyze tool defines")
    if not isinstance(claim.get("value"), (int, float)) or isinstance(claim.get("value"), bool) or value is None:
        return out("not checkable", "the claim's value is not a number")
    if window_s is None and why:
        return out("not checkable", f"the claim's window is not usable: {why}")
    metrics = _compute_metrics(dataset, rows, window_s=window_s)
    actual = metrics["values"].get(metric)
    rows_total = metrics["rows_total"]
    if actual is None:
        if rows_total < MIN_ANALYZE_ROWS:
            return out("not checkable", f"not enough data: {rows_total} row(s) collected; at least {MIN_ANALYZE_ROWS} "
                                        f"are needed before any number is computed")
        return out("not checkable", f"not enough data for {metric} in this dataset (its section in market.analyze "
                                    f"says which rows are missing)")
    if comparator in (">", "<", ">=", "<="):
        ok = _COMPARATORS[comparator](actual, value)
        return out("supported" if ok else "contradicted", f"{metric} is {actual}, claimed {comparator} {value}", actual)
    if comparator in ("=", "within"):
        tolerance = _float(claim.get("tolerance"))
        if tolerance is None:
            return out("not checkable", f"{comparator!r} needs a tolerance (how close the value must be)")
        ok = abs(actual - value) <= tolerance
        return out("supported" if ok else "contradicted",
                   f"{metric} is {actual}, claimed {comparator} {value} within {tolerance}", actual)
    return out("not checkable", f"the comparator {comparator!r} is not one of > < >= <= = within")


__all__ = ["ANALYZE", "COLLECT", "DATASETS", "DATASET_DELETE", "INTENTS", "METRIC_NAMES", "SURFACE", "THESIS_CHECK",
           "market_contracts", "run_tool"]
