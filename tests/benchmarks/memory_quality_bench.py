"""
Memory-quality benchmark (held-out yardstick) for VOOL's long-term memory.

Measures the axes the agent-memory field standardized in 2026 (BEAM-style),
on a labeled multi-session corpus, fully offline (embed() falls back to
hash-BoW; richer with an Ollama embed model present):

  recall@k              — does the LIVE answer surface for a query?
  supersession-correct  — does the query return the UPDATED value, not the
                          stale one it replaced? (the gap VOOL has today)
  dedup-efficiency      — nodes stored vs distinct facts (lower bloat = better)
  importance-retention  — are high-value facts (keys/ports/deadlines) retrievable,
                          not buried under filler?

Run baseline (current code) then re-run after upgrades to quantify the delta:
    .venv/bin/python -m tests.benchmarks.memory_quality_bench
    .venv/bin/python -m tests.benchmarks.memory_quality_bench --json

It exercises the real VoolMemory API (node_store / node_search) and uses the
new optional kwargs (importance=, supersede=) when the upgraded store provides
them, falling back cleanly so the SAME script grades old and new code.
"""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
import time
from pathlib import Path

# allow running as a file or module
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.context_retrieval import _score_importance  # the live runtime's scorer
from core.embedding_service import embed, embedding_backend
from core.vool_memory import VoolMemory

# ── labeled corpus: a personal-assistant relationship over 3 sessions ──────────
# Each turn: (session, role, content, fact_id, supersedes_fact_id, importance_hint)
# fact_id groups restatements/updates of the same fact; supersedes marks an update.
Turn = tuple

CORPUS: list[dict] = [
    # — session 1: facts get set —
    {"s": 1, "role": "user", "text": "Set the deploy server to listen on port 8096.", "fact": "deploy_port", "val": "8096", "hi": True},
    {"s": 1, "role": "assistant", "text": "Deploy server configured for port 8096."},
    {"s": 1, "role": "user", "text": "I'm using Postgres as the primary database for this project.", "fact": "database", "val": "postgres", "hi": True},
    {"s": 1, "role": "assistant", "text": "Noted, Postgres is the primary database."},
    {"s": 1, "role": "user", "text": "Just thinking out loud about color schemes, nothing decided yet.", "fact": None},
    {"s": 1, "role": "user", "text": "The launch deadline is 2026-07-15, hard date.", "fact": "deadline", "val": "2026-07-15", "hi": True},
    {"s": 1, "role": "user", "text": "My production API key is sk-live-abc123xyz789, keep it safe.", "fact": "api_key", "val": "sk-live-abc123xyz789", "hi": True},
    {"s": 1, "role": "assistant", "text": "Stored the key securely; I will not echo it back."},
    {"s": 1, "role": "user", "text": "Random aside: the weather has been nice this week."},
    {"s": 1, "role": "assistant", "text": "Glad to hear the weather is pleasant."},
    # — session 2: updates that SUPERSEDE, plus a duplicate —
    {"s": 2, "role": "user", "text": "Port 8096 conflicts with another service. Change the deploy port to 9090 now.", "fact": "deploy_port", "val": "9090", "supersedes": "deploy_port", "hi": True},
    {"s": 2, "role": "assistant", "text": "Updated: the deploy server now uses port 9090."},
    {"s": 2, "role": "user", "text": "Reminder that I'm using Postgres as the primary database.", "fact": "database", "val": "postgres", "dup": True},
    {"s": 2, "role": "user", "text": "We slipped the timeline; move the launch deadline to 2026-08-01.", "fact": "deadline", "val": "2026-08-01", "supersedes": "deadline", "hi": True},
    {"s": 2, "role": "assistant", "text": "Deadline moved to 2026-08-01."},
    {"s": 2, "role": "user", "text": "Some filler chatter about lunch options for the team."},
    {"s": 2, "role": "user", "text": "Another unrelated note about meeting room bookings."},
    # — session 3: filler + VERBATIM restatements (true duplicates to dedup) —
    {"s": 3, "role": "user", "text": "Catching up after a break, lots of small talk today."},
    {"s": 3, "role": "user", "text": "Discussed font choices briefly, undecided."},
    {"s": 3, "role": "user", "text": "The CI pipeline had a flaky test we will look at later."},
    {"s": 3, "role": "user", "text": "I'm using Postgres as the primary database for this project.", "fact": "database", "val": "postgres", "dup": True},
    {"s": 3, "role": "user", "text": "My production API key is sk-live-abc123xyz789, keep it safe.", "fact": "api_key", "val": "sk-live-abc123xyz789", "dup": True},
]

# Sessions are weeks apart (realistic): a fact set in week 1 and UPDATED in week 3
# must be distinguishable by recency. Maps session -> seconds-ago anchor.
SESSION_AGE_DAYS = {1: 21.0, 2: 7.0, 3: 0.3}

# queries: the live answer the system SHOULD retrieve, and the stale value it must NOT prefer
QUERIES: list[dict] = [
    {"q": "what port does my deploy server use?", "want": "9090", "stale": "8096", "fact": "deploy_port"},
    {"q": "which database did I choose for the project?", "want": "postgres", "fact": "database"},
    {"q": "when is the launch deadline?", "want": "2026-08-01", "stale": "2026-07-15", "fact": "deadline"},
    {"q": "what is my production api key?", "want": "sk-live-abc123xyz789", "fact": "api_key"},
    {"q": "what datastore am I persisting data in?", "want": "postgres", "fact": "database"},  # paraphrase (semantic)
]

TOP_K = 4


def _kw(text: str) -> list[str]:
    import re
    toks = re.findall(r"[a-zA-Z0-9_\-]{3,}", text.lower())
    return list(dict.fromkeys(toks))[:8]


def _store_supports(name: str, mem: VoolMemory) -> bool:
    try:
        return name in inspect.signature(mem.node_store).parameters
    except (ValueError, TypeError):
        return False


def ingest(mem: VoolMemory) -> int:
    """Ingest raw turns the way the real agent loop does — NO fact/supersede hints.
    The store must EARN dedup (near-identical detection) and supersession
    (recency-weighted ranking). importance uses the system's own scorer, exactly
    as core.context_retrieval.store_turn does."""
    supports_importance = _store_supports("importance", mem)
    stored = 0
    now = time.time()
    for i, t in enumerate(CORPUS):
        text = t["text"]
        # session anchor (weeks apart) + small intra-session offset by turn index
        age_days = SESSION_AGE_DAYS.get(t["s"], 0.3) - (i * 0.001)
        ts = now - age_days * 86400.0
        kwargs = dict(
            content=text,
            keywords=_kw(text),
            tags=[t["role"]],
            context_description=f"session {t['s']} turn {i}",
            embedding=embed(text),
            timestamp=ts,
        )
        if supports_importance:
            kwargs["importance"] = _score_importance(text)  # the real scorer, no labels
        try:
            mem.node_store(**kwargs)
            stored += 1
        except TypeError:
            kwargs.pop("importance", None)
            kwargs.pop("timestamp", None)
            mem.node_store(**kwargs)
            stored += 1
    return stored


def _search(mem: VoolMemory, query: str, k: int) -> list[tuple]:
    """Prefer the hybrid/ranked search if the upgraded store exposes it."""
    qv = embed(query)
    # upgraded store may add a 'mode' or 'hybrid' kwarg or a separate method
    if hasattr(mem, "node_search_hybrid"):
        try:
            return mem.node_search_hybrid(query, qv, top_k=k)  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        return mem.node_search(qv, top_k=k, min_score=0.0)
    except TypeError:
        return mem.node_search(qv, top_k=k)


def evaluate(mem: VoolMemory) -> dict:
    distinct_facts = len({t["fact"] for t in CORPUS if t.get("fact")})
    fact_turns = sum(1 for t in CORPUS if t.get("fact"))
    stored = mem.node_count()
    writes = len(CORPUS)
    dup_turns = sum(1 for t in CORPUS if t.get("dup"))  # turns that SHOULD be deduped
    caught = max(0, writes - stored)  # each NOOP-dedup reduces stored by 1

    recall_hits = 0
    supersession_ok = 0
    supersession_total = 0
    importance_hits = 0
    importance_total = 0
    rows = []
    for query in QUERIES:
        results = _search(mem, query["q"], TOP_K)
        texts = [n.content.lower() for (n, _s) in results]
        joined = " || ".join(texts)
        want = query["want"].lower()
        hit = any(want in tx for tx in texts)
        recall_hits += int(hit)
        # supersession: stale value must NOT appear ABOVE the live one (or at all in top-k)
        stale_ok = True
        if "stale" in query:
            supersession_total += 1
            stale = query["stale"].lower()
            want_idx = next((j for j, tx in enumerate(texts) if want in tx), None)
            stale_idx = next((j for j, tx in enumerate(texts) if stale in tx), None)
            # ok if live present and (stale absent OR stale ranked below live)
            stale_ok = want_idx is not None and (stale_idx is None or stale_idx > want_idx)
            supersession_ok += int(stale_ok)
        # importance retention: high-value facts should be retrievable
        if query["fact"] in {"api_key", "deploy_port", "deadline", "database"}:
            importance_total += 1
            importance_hits += int(hit)
        rows.append({"q": query["q"], "hit": hit, "stale_ok": stale_ok, "top": joined[:120]})

    return {
        "backend": embedding_backend(),
        "distinct_facts": distinct_facts,
        "fact_turns": fact_turns,
        "writes_attempted": writes,
        "nodes_stored": stored,
        "duplicate_turns": dup_turns,
        "duplicates_caught": caught,
        "dedup_pct": round(100 * caught / dup_turns) if dup_turns else 100,
        "recall_at_k_pct": round(100 * recall_hits / len(QUERIES)),
        "supersession_correct_pct": round(100 * supersession_ok / supersession_total) if supersession_total else 100,
        "importance_retention_pct": round(100 * importance_hits / importance_total) if importance_total else 100,
        "rows": rows,
    }


def main() -> int:
    json_out = "--json" in sys.argv
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="bench", db_path=str(Path(tmp) / "mem.db"))
        ingest(mem)
        result = evaluate(mem)
        mem.close()

    if json_out:
        print(json.dumps(result))
        return 0

    print("Memory-quality benchmark (held-out)\n")
    print(f"  embed backend         : {result['backend']}")
    print(f"  writes / nodes stored : {result['writes_attempted']} -> {result['nodes_stored']}")
    print(f"  dedup (NOOP) caught   : {result['duplicates_caught']}/{result['duplicate_turns']} duplicate turns = {result['dedup_pct']}%")
    print(f"  recall@{TOP_K}            : {result['recall_at_k_pct']}%")
    print(f"  supersession correct  : {result['supersession_correct_pct']}%   (returns UPDATED value, not the stale one)")
    print(f"  importance retention  : {result['importance_retention_pct']}%   (high-value facts retrievable)")
    print("\n  per-query:")
    for r in result["rows"]:
        flag = "ok " if r["hit"] and r["stale_ok"] else "FAIL"
        print(f"    [{flag}] {r['q']}")
        print(f"           -> {r['top']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
