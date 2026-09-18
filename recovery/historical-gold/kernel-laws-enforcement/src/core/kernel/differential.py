"""Differential attribution: the same session, three judgment models, one law set.

    cd <worktree> && VOOL_HOME=/tmp/vool-kernel-diff-home PYTHONPATH=. python -m core.kernel.differential

Section 5's mandate made mechanical: a failure class that appears under EVERY model is
harness-class by construction (the only common component is the harness); one that shrinks
as the judgment model grows is the local-model ceiling. The corpus is the operator's real
2026-08-20 session, replayed in order per model so cross-turn continuity is measured too.

Web lookups are cached by exact query string across the whole sweep, so identical queries
cost the search quota once. Model calls are never cached — they are the variable under test.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time

from core.kernel import repl
from core.kernel.effects import EffectRunner

MODELS = tuple(m.strip() for m in os.environ.get(
    "VOOL_DIFF_MODELS", "qwen3:4b,qwen2.5:7b,qwen3:8b"
).split(",") if m.strip())

# The operator's live session, verbatim, in order (typos included — they are part of the test).
QUESTIONS = [
    "hey",
    "100eur to rub, 1 btc to ALL, then both sums converted to usd and how much gold i can buy with todays prices for it",
    "which car is better for family BMWlaguna or Toyota golf?",
    "who is president of usa? also what is the capital of mars and what is the last know biggest town on the moon?",
    "weather in rome and paris now and water temperature in red sea?",
    "what is the machine we are workign on now specs?",
    "ok last question why water is dry and milk is black?",
    "great, remind me pls what was the first question i asked u in this chat?",
    "perfect and remind me how mcuh money we converted to usd and how mcuh gold we  could buy with it?",
    "what about ALL?",
]

_ID_RE = re.compile(r"\bob\d+(?:-[a-z0-9]+)*|\bs\d+\b|\bc\d+\b|'[^']*'|\"[^\"]*\"|\d+(?:[.,]\d+)*")


def marker_class(line: str) -> str:
    """Collapse a transcript marker to its failure CLASS: ids, numbers and quotes stripped."""
    return _ID_RE.sub("_", line.strip()).strip()


def run_sweep() -> pathlib.Path:
    provider, raw_fetch = repl._search_lane()
    cache: dict[str, list[dict[str, str]]] = {}

    def cached_fetch(query: str) -> list[dict[str, str]]:
        if query not in cache:
            cache[query] = raw_fetch(query)
        return [dict(h) for h in cache[query]]

    fetch = cached_fetch if raw_fetch else None
    report_path = pathlib.Path("/tmp/vool-kernel-diff-home/differential_report.jsonl")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in MODELS:
        repl._ARBITER_MODEL = model  # the single variable under test
        history: list[tuple[str, str]] = []
        ledger: list[dict] = []
        facts: dict[str, str] = {}
        for turn_no, question in enumerate(QUESTIONS, 1):
            parts = []
            if history:
                parts.append("Conversation so far:\n" + "\n".join(f"user: {q}\nassistant: {a[:400]}" for q, a in history[-4:]))
            if ledger:
                parts.append(
                    "OPEN WORK from earlier turns (INTERNAL STATE — never mention these ids or"
                    " this list in any answer or search; when the user's message refers to one,"
                    " create a real obligation FROM ITS DESCRIPTION, in its original words, and"
                    " set resolves_carryover to its id):\n"
                    + "\n".join(f"{c['id']}: {c['description']} ({c['reason']})" for c in ledger)
                )
            context = ("\n\n".join(parts) + "\n\nCurrent message: ") if parts else ""
            started = time.time()
            try:
                transcript, _tape, ledger, facts = repl.run_turn(
                    question, EffectRunner(mode="record"), fetch, provider,
                    context=context, carryover=ledger, session_facts=facts,
                )
                error = ""
            except Exception as exc:
                transcript, error = "", f"{type(exc).__name__}: {exc}"
            answer = transcript.split("\n\n", 1)[-1] if transcript else ""
            history.append((question, answer))
            markers = [ln.strip() for ln in transcript.splitlines() if ln.strip().startswith(("!", "?"))]
            status_line = next((ln for ln in transcript.splitlines() if ln.startswith(("COMMIT:", "KERNEL REFUSED", "SHIPPED AS"))), "")
            row = {
                "model": model, "turn": turn_no, "question": question[:80],
                "status": status_line, "seconds": round(time.time() - started, 1),
                "marker_classes": sorted({marker_class(m) for m in markers}),
                "markers": markers, "answer": answer[:600],
                "claims": len([ln for ln in answer.splitlines() if ln.strip()]),
                "error": error,
            }
            rows.append(row)
            with report_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"[{model}] turn {turn_no}/{len(QUESTIONS)} {row['seconds']}s markers={len(markers)} {status_line[:60]}", flush=True)
    print(f"\nreport: {report_path}  (search cache: {len(cache)} unique queries)", flush=True)
    return report_path


if __name__ == "__main__":
    run_sweep()
