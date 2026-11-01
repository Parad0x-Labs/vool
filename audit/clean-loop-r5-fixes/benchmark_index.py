from __future__ import annotations

"""C1 measurement: cold build and warm route-decision time of the document index on 5,000 documents.

Run: python3.12 audit/clean-loop-r5-fixes/benchmark_index.py
It builds a synthetic home in a fresh temporary folder (removed afterwards) and reports one line.
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.clean_loop import route, tools
from core.clean_loop.registry import ToolContext


ROOT = Path(tempfile.mkdtemp(prefix="vool-index-bench-")).resolve()
HOME = ROOT / "home"
DOCS = HOME / "Documents"
ASK = "When does Neralis arrive at VelvetQuartz?"


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    for number in range(5000):
        path = DOCS / f"fragment_{number:04}.txt"
        if not path.exists():
            path.write_text(f"Neralis is expected at VelvetQuartz. Entry marker {number}.\n", encoding="utf-8")
    import core.runtime_execution_tools as runtime_tools

    runtime_tools._machine_home = lambda: HOME
    runtime_tools._readable_machine_roots = lambda: (DOCS,)

    ctx = ToolContext(home=HOME, source_context={"surface": "cli"})
    start = time.perf_counter()
    rounds = 0
    slice_ms = []
    while True:
        rounds += 1
        slice_started = time.perf_counter()
        tools.document_matches(ASK, ctx)
        slice_ms.append((time.perf_counter() - slice_started) * 1000)
        state = tools._TEXT_SCANS[str(HOME)]
        if tools._content_index_complete(ctx):
            break
        if rounds >= 120:
            raise RuntimeError("index did not complete in 120 slices")
    cold_ms = (time.perf_counter() - start) * 1000
    indexed = len(tools._TEXT_CACHE)
    byte_budget_used = tools._TEXT_CACHE_BYTES

    samples = []
    for _ in range(100):
        started = time.perf_counter()
        decision = route.decide(
            ASK,
            source_context={"surface": "cli"},
            resolve_brain=lambda: object(),
            env={route.FLAG_ENV: "1"},
            document_match=lambda ask: tools.document_matches(ask, ctx),
            file_words=lambda: set(),
        )
        samples.append((time.perf_counter() - started) * 1000)
        if not decision.admit:
            raise RuntimeError(f"document-backed route did not admit: {decision.reason}")
    p95 = sorted(samples)[94]
    print(f"documents={indexed} slices={rounds} cold_build_ms={cold_ms:.1f} cold_slice_max_ms={max(slice_ms):.1f} warm_decision_p95_ms={p95:.1f} byte_budget_used={byte_budget_used} byte_budget_limit={tools._TEXT_INDEX_BYTES}")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
