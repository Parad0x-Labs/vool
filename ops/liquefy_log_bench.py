#!/usr/bin/env python3
"""Liquefy cold-log benchmark against REAL VOOL logs (P1 measurement law).

Protocol (dossier 2026-08-31 §36.13, minimized): per real log class —
  ratio        raw bytes vs Liquefy-COL2 segment vs plain zstd-19 control
  write        per-event hot-append latency; seal throughput
  read         hot read vs cold verified read (both hash gates + decompress)
  search       cold column-scoped search vs raw substring scan control

Originals are READ-ONLY: every file is copied into a temp workdir first, and
no log content ever lands in the output — numbers only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.liquefy.store import LiquefyLogStore

try:
    import zstandard

    _ZSTD = zstandard.ZstdCompressor(level=19, write_checksum=True)
except ImportError:
    _ZSTD = None

DEFAULT_SOURCES = [
    ("routing_decisions", Path.home() / "Desktop/vool-benches/.vool_local/data/routing_decisions.jsonl"),
    ("vault_audit_events", Path.home() / "Desktop/vool-benches/.vool_local/data/liquefy_vault/audit/events.jsonl"),
    ("cloud_route_receipts", Path.home() / "Desktop/vool-benches/.vool_local/data/cloud_route_receipts/session.jsonl"),
    ("conversation_log", Path.home() / "Desktop/vool-benches/.vool_local/data/conversation_log.jsonl"),
]


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def bench_class(name: str, source: Path, workdir: Path, sample_cap: int) -> dict | None:
    if not source.exists():
        return None
    local_copy = workdir / f"{name}.jsonl"
    shutil.copyfile(source, local_copy)
    raw_bytes = local_copy.read_bytes()
    lines = [line for line in raw_bytes.split(b"\n") if line.strip()][:sample_cap]
    if len(lines) < 10:
        return None
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    if len(records) < 10:
        return None

    with tempfile.TemporaryDirectory(prefix=f"liquefy-bench-{name}") as store_dir:
        store = LiquefyLogStore(Path(store_dir), segment_max_events=100_000)

        # write: per-event hot append
        append_latencies = []
        for record in records:
            start = time.perf_counter()
            store.append([{"kind": str(record.get("kind") or record.get("event") or "log"), "source": name,
                           "payload": record, "ref": {"line_no": len(append_latencies)}}])
            append_latencies.append((time.perf_counter() - start) * 1000)

        # seal: cold transition
        start = time.perf_counter()
        sealed = store.seal()
        seal_ms = (time.perf_counter() - start) * 1000

        # read: hot vs cold verified
        cold_read_ms, hot_read_ms = [], []
        hot_records = records[len(records) // 2:]
        store.append([{"kind": "hot", "source": name, "payload": record, "ref": {"line_no": 10_000 + i}}
                      for i, record in enumerate(hot_records)])
        for seq in range(1, len(records) + 1, max(1, len(records) // 20)):
            start = time.perf_counter()
            store.read(seq)
            cold_read_ms.append((time.perf_counter() - start) * 1000)
        for seq in range(len(records) + 1, len(records) + len(hot_records) + 1, max(1, len(hot_records) // 10)):
            start = time.perf_counter()
            store.read(seq)
            hot_read_ms.append((time.perf_counter() - start) * 1000)

        # search: cold grep vs raw scan control (needle from a real middle record)
        needle_record = records[len(records) // 3]
        needle = ""
        for value in needle_record.values():
            text = str(value)
            if 8 <= len(text) <= 48 and text.isprintable():
                needle = text
                break
        search_timings, raw_scan_timings = [], []
        for _ in range(5):
            start = time.perf_counter()
            store.search(needle, limit=20)
            search_timings.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            assert needle.encode("utf-8") in raw_bytes or True  # raw-scan control timing only
            raw_scan_timings.append((time.perf_counter() - start) * 1000)

        header = store.segment_header(sealed.segment_id) if sealed else {}
        canonical_bytes = int(header.get("original_bytes") or 0)
        blob_bytes = int(header.get("blob_bytes") or 0)

    zstd_bytes = len(_ZSTD.compress(b"\n".join(lines))) if _ZSTD else None
    return {
        "class": name,
        "records": len(records),
        "raw_bytes": len(raw_bytes),
        "canonical_bytes": canonical_bytes,
        "liquefy_blob_bytes": blob_bytes,
        "liquefy_ratio_vs_raw": round(len(raw_bytes) / max(1, blob_bytes), 2),
        "liquefy_ratio_vs_canonical": round(canonical_bytes / max(1, blob_bytes), 2),
        "zstd19_bytes": zstd_bytes,
        "zstd19_ratio_vs_raw": round(len(raw_bytes) / zstd_bytes, 2) if zstd_bytes else None,
        "col2_wins_over_zstd19": (blob_bytes < zstd_bytes) if zstd_bytes else None,
        "append_p50_ms": round(statistics.median(append_latencies), 4),
        "append_p95_ms": round(_percentile(append_latencies, 95), 4),
        "seal_ms_total": round(seal_ms, 2),
        "seal_us_per_event": round(seal_ms * 1000 / len(records), 2),
        "cold_read_p50_ms": round(statistics.median(cold_read_ms), 3),
        "hot_read_p50_ms": round(statistics.median(hot_read_ms), 4),
        "search_p50_ms": round(statistics.median(search_timings), 3),
        "raw_scan_ms": round(statistics.median(raw_scan_timings), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-cap", type=int, default=4000, help="max events per class")
    parser.add_argument("--out", type=Path, default=None, help="write JSON results here")
    args = parser.parse_args()

    results = []
    with tempfile.TemporaryDirectory(prefix="liquefy-bench") as workdir:
        for name, source in DEFAULT_SOURCES:
            row = bench_class(name, source, Path(workdir), args.sample_cap)
            if row:
                results.append(row)
                print(f"[bench] {name}: {row['records']} records, ratio {row['liquefy_ratio_vs_raw']}x (zstd19 {row['zstd19_ratio_vs_raw']}x)")
            else:
                print(f"[bench] {name}: skipped (missing or unparsable)")

    print(f"\n{'class':<22} {'ratio':>7} {'zstd19':>7} {'win':>4} {'app_p95':>8} {'seal/ev':>8} {'cold_rd':>8} {'hot_rd':>8} {'search':>8} {'rawscan':>8}")
    for row in results:
        print(f"{row['class']:<22} {row['liquefy_ratio_vs_raw']:>6}x {row['zstd19_ratio_vs_raw']:>6}x "
              f"{('YES' if row['col2_wins_over_zstd19'] else 'no'):>4} "
              f"{row['append_p95_ms']:>7}ms {row['seal_us_per_event']:>7}us {row['cold_read_p50_ms']:>7}ms "
              f"{row['hot_read_p50_ms']:>7}ms {row['search_p50_ms']:>7}ms {row['raw_scan_ms']:>7}ms")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"protocol": "vool.liquefy.bench.v1", "results": results}, indent=2) + "\n")
        print(f"\nresults -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
