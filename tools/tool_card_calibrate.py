"""Calibrate tool-card routing on a set of asks: how words and meaning score each kind.

    python -m tools.tool_card_calibrate <asks.jsonl> [--out report.json]

Each line needs "ask" (or the first user turn of "turns") and "kind" (needs_tool,
missing_connection, no_tool). For every ask it records the best card by word overlap alone and,
when the local embedder (Ollama nomic-embed-text) answers, the best raw cosine by meaning. It then
reports, for a grid of release floors, how many needs-a-tool asks would get their tools and how many
no-tool asks would be handed tools anyway, so the band in core.tool_card_router can be set from
evidence. Prints counts only, never the asks, so it can run on a frozen set without exposing it.
Reads only; spends nothing; calls no chat model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ask_text(row: dict) -> str:
    if row.get("ask"):
        return str(row["ask"])
    for turn in row.get("turns") or ():
        if isinstance(turn, dict) and str(turn.get("role") or "user") == "user":
            return str(turn.get("content") or turn.get("text") or "")
        if isinstance(turn, str):
            return turn
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("asks")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    from core import tool_card_router as router
    from core.embedding_service import cosine_similarity
    from core.tool_registry import registered_tools

    rows = [json.loads(line) for line in Path(args.asks).read_text(encoding="utf-8").splitlines() if line.strip()]
    contracts = registered_tools()
    index = router._index(contracts)
    vectors = router._semantic_vectors(index, wait=True)
    backend = router._semantic_backend() or "none"
    scored = []
    for row in rows:
        text = _ask_text(row)
        words = router.match_cards(text, contracts=contracts, floor=0.0, semantic="off", limit=1)
        cosine = 0.0
        if vectors:
            query, _stamp = router._embed_ask(text)
            for entry in index.entries:
                best = max((cosine_similarity(query, vectors[a.text]) for a in entry.examples if a.text in vectors), default=0.0)
                veto = max((cosine_similarity(query, vectors[a.text]) for a in entry.not_for if a.text in vectors), default=0.0)
                if best > veto:
                    cosine = max(cosine, best)
        scored.append({"kind": str(row.get("kind") or ""), "words": words[0].score if words else 0.0, "cosine": round(cosine, 4)})

    def tally(key: str, floor: float) -> dict:
        out: dict[str, list[int]] = {}
        for item in scored:
            hit, total = out.setdefault(item["kind"], [0, 0])
            out[item["kind"]] = [hit + (item[key] >= floor), total + 1]
        return {kind: f"{hit}/{total}" for kind, (hit, total) in sorted(out.items())}

    report = {
        "asks": len(scored),
        "embedder": backend,
        "cards": len(index.entries),
        "words": {f"{f:.2f}": tally("words", f) for f in (0.42, 0.5, 0.6, 0.75)},
        "meaning_cosine": {f"{f:.2f}": tally("cosine", f) for f in (0.6, 0.65, 0.7, 0.75, 0.8, 0.85)} if vectors else {},
    }
    text = json.dumps(report, indent=1)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
