"""C1 measurement: cold build and warm route-decision time of the document index on 5,000 documents.

Run: python3.12 audit/clean-loop-r5-fixes/benchmark_index.py
It builds a synthetic home in a fresh temporary folder (removed afterwards) and reports one line.
"""
from __future__ import annotations

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
_SYLLABLES = ("ka", "ren", "vo", "lis", "mar", "tu", "sel", "dor", "an", "quil", "bre", "zo")


def _name(number: int) -> str:
    # A distinct invented name per document, so the name test sees a realistic spread.
    a, b, c = number % 12, (number // 12) % 12, (number // 144) % 12
    return (_SYLLABLES[a] + _SYLLABLES[b] + _SYLLABLES[c]).capitalize()


ASK = f"When does {_name(4242)} arrive at the harbour office?"
GENERAL = "What is the capital of a country with many islands?"


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    for number in range(5000):
        path = DOCS / f"fragment_{number:04}.txt"
        if not path.exists():
            path.write_text(
                f"Visit plan {number}.\nGuest {_name(number)} is expected at the harbour office on day "
                f"{number % 28 + 1}; bring the blue folder and the parking slip.\n" * 3,
                encoding="utf-8",
            )
    import os

    os.environ["HOME"] = str(HOME)
    import core.runtime_execution_tools as runtime_tools

    runtime_tools._machine_home = lambda: HOME
    runtime_tools._readable_machine_roots = lambda: (DOCS,)

    ctx = ToolContext(home=HOME, source_context={"surface": "cli"})
    start = time.perf_counter()
    # The first owner turn: the route's own short slice, then the rest built off the turn.
    tools.document_matches(ASK, ctx)
    cold_turn_ms = (time.perf_counter() - start) * 1000
    tools.warm_index(ctx)
    while not tools._content_index_complete(ctx):
        if time.perf_counter() - start > 600:
            raise RuntimeError("index did not complete in 600 s")
        time.sleep(0.05)
    slice_ms = [cold_turn_ms]
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
    general = []
    for _ in range(100):
        started = time.perf_counter()
        decision = route.decide(
            GENERAL, source_context={"surface": "cli"}, resolve_brain=lambda: object(),
            env={route.FLAG_ENV: "1"}, document_match=lambda ask: tools.document_matches(ask, ctx),
            file_words=lambda: set(),
        )
        general.append((time.perf_counter() - started) * 1000)
        if decision.admit:
            raise RuntimeError("a general question was admitted")
    general_p95 = sorted(general)[94]
    print(f"documents={indexed} cold_turn_route_ms={max(slice_ms):.1f} cold_build_ms={cold_ms:.1f} (route slice + background) warm_decision_p95_ms={p95:.1f} warm_general_decision_p95_ms={general_p95:.1f} byte_budget_used={byte_budget_used} byte_budget_limit={tools._TEXT_INDEX_BYTES}")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
