#!/usr/bin/env python3
"""Differential V3: per-axis, strict whole-turn scoring of RequestGraph producers against the gold.

Supersedes the V2 instrument (``scripts/semantic_resolver_differential.py``, kept frozen), whose
scorer compared counts and awarded 20/20 to unrelated invented questions. V3 scores every semantic
axis INDEPENDENTLY (``core.semantic.graph_diff.AXES``) and reports STRICT whole-turn correctness --
every axis must pass -- beside the per-axis coverage, so a reading cannot win by getting a count
right and a report says WHICH axis a producer gets wrong.

Producers (columns):

    naive     a deterministic conjunction splitter (control / floor; no model, no network)
    lexical   today's reading (``core.semantic.producers.lexical``) -- what the resolver must beat
    model     the registered GraphSemanticResolver, when one is registered and a keyed run fills it
              (``--openrouter-model`` builds one from a free OpenRouter tag; EVAL-ONLY transport,
              called from this script, never from ``core/semantic``)

The gold corpus is FROZEN on every field (``ops.semantic_requestgraph_gold.GOLD_DIGEST``); the
script refuses to run on a drifted corpus. These 20 cases are DEVELOPMENT diagnostics, not a holdout.

    python scripts/semantic_differential_v3.py --table
    python scripts/semantic_differential_v3.py --json --out /path/report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

SCHEMA = "semantic_differential_v3"


def _openrouter_api_key() -> str:
    """The OpenRouter key from env, else the runtime credential store. Never printed."""
    key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key
    try:
        from core.credential_store import get_credential

        return str(get_credential("llm.cloud.openrouter") or "").strip()
    except Exception:
        return ""


def openrouter_eval_transport(model: str, *, timeout_s: float = 45.0, urlopen=None, api_key: str = ""):
    """A minimal OpenAI-compatible chat transport for OFFLINE evaluation only. Lives here, outside
    ``core/semantic``, so no frame of that package is on the provider call's stack."""
    import urllib.request

    key = api_key or _openrouter_api_key()
    if not key:
        raise SystemExit("no OpenRouter key: set OPENROUTER_API_KEY or store llm.cloud.openrouter")
    opener = urlopen or urllib.request.urlopen

    def _transport(system: str, user: str, *, json_schema: dict[str, Any] | None = None) -> str:
        body: dict[str, Any] = {
            "model": model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_schema is not None:
            body["response_format"] = {"type": "json_object"}
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with opener(request, timeout=timeout_s) as response:
            reply = json.loads(response.read().decode("utf-8"))
        return str(reply["choices"][0]["message"]["content"])

    return _transport


@dataclass
class NaiveGraphProducer:
    """Deterministic conjunction splitter as a graph: one request + one slot per fragment."""

    name = "naive"

    def produce(self, text: str, *, turn_id: str):
        from core.semantic.graph_builder import RequestGraphBuilder

        parts = [text]
        for sep in (" and ", " also ", "; ", ", and ", ", "):
            parts = [seg for chunk in parts for seg in chunk.split(sep)]
        parts = [p.strip() for p in parts if p.strip()]
        builder = RequestGraphBuilder(text, turn_id=turn_id)
        for fragment in parts:
            rid = builder.add_request(fragment)
            builder.add_slot(rid, expected=fragment)
        return builder.build()


@dataclass
class LexicalGraphProducer:
    name = "lexical"

    def produce(self, text: str, *, turn_id: str):
        from core.semantic.producers.lexical import lexical_request_graph

        return lexical_request_graph(text, turn_id=turn_id)


@dataclass
class ModelGraphProducer:
    """The registered two-phase resolver driven by an injected transport (this script's)."""

    transport: Any
    name = "model"

    def produce(self, text: str, *, turn_id: str):
        from core.semantic.canonical_text import CanonicalText
        from core.semantic.resolver_registry import active_resolver

        resolver = active_resolver()
        if resolver is None or not hasattr(resolver, "prepare"):
            return None
        operations, _descriptions = _operations_and_descriptions()
        prepared = resolver.prepare(CanonicalText.of(text), operations=operations, turn_id=turn_id)
        if prepared is None:
            return None
        try:
            reply = self.transport(prepared.system_prompt, prepared.user_prompt, json_schema=prepared.json_schema)
        except Exception:
            resolver.record_transport_failure()
            return None
        return resolver.finish(prepared, reply)


def _operations_and_descriptions() -> tuple[tuple[str, ...], dict[str, str]]:
    """The live operation catalog (registry names), with a small fallback when it is unavailable."""
    try:
        from core.semantic.operation_catalog import catalog_entries

        rows = catalog_entries(include_unsupported=False)
        if rows:
            names = tuple(r["name"] for r in rows if r["name"])
            return names, {r["name"]: r["description"] for r in rows if r["name"]}
    except Exception:
        pass
    fallback = {
        "market_quote": "current price and 24h change for one or more named assets",
        "weather_lookup": "current weather for one or more named places",
        "calculation": "arithmetic the runtime evaluates exactly",
        "water_temperature": "current sea/water temperature at a named place",
        "time_clock": "the current local time in named places",
    }
    return tuple(fallback), fallback


def score_producer(producer, *, cases) -> list[dict[str, Any]]:
    """One row per case: per-axis verdicts, strict whole-turn, and the failed axes."""
    from core.semantic.graph_diff import AXES, compare_graphs

    rows: list[dict[str, Any]] = []
    for case in cases:
        gold = case["gold"]
        try:
            candidate = producer.produce(case["text"], turn_id=f"eval:{case['id']}")
        except Exception as exc:
            rows.append({"id": case["id"], "cls": case["cls"], "status": f"error:{type(exc).__name__}",
                         "strict": False, "axes": dict.fromkeys(AXES, False), "failed": list(AXES)})
            continue
        if candidate is None:
            rows.append({"id": case["id"], "cls": case["cls"], "status": "abstained",
                         "strict": False, "axes": dict.fromkeys(AXES, False), "failed": list(AXES)})
            continue
        cmp = compare_graphs(gold, candidate)
        rows.append({
            "id": case["id"], "cls": case["cls"], "status": "scored",
            "strict": cmp.whole_turn_correct,
            "axes": {a.axis: a.matched for a in cmp.axes},
            "failed": list(cmp.failed_axes()),
        })
    return rows


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from core.semantic.graph_diff import AXES

    total = len(rows)
    return {
        "total": total,
        "strict_correct": sum(1 for r in rows if r["strict"]),
        "strict_pct": round(100.0 * sum(1 for r in rows if r["strict"]) / total, 1) if total else 0.0,
        "axis_coverage": {axis: sum(1 for r in rows if r["axes"].get(axis)) for axis in AXES},
        "abstained": sum(1 for r in rows if r["status"] == "abstained"),
        "errors": sum(1 for r in rows if str(r["status"]).startswith("error")),
    }


def run_differential(*, with_model: bool = False, transport=None) -> dict[str, Any]:
    from ops import semantic_requestgraph_gold as gold_corpus

    if gold_corpus.gold_digest() != gold_corpus.GOLD_DIGEST:
        raise SystemExit("the gold corpus drifted from GOLD_DIGEST; refusing to score against unfrozen gold")
    cases = [
        {"id": c.id, "cls": c.cls, "text": c.text, "gold": gold_corpus.gold_graph(c.id)}
        for c in gold_corpus.gold_cases()
    ]
    producers: list[Any] = [NaiveGraphProducer(), LexicalGraphProducer()]
    if with_model:
        producers.append(ModelGraphProducer(transport=transport))
    columns: dict[str, Any] = {}
    for producer in producers:
        rows = score_producer(producer, cases=cases)
        columns[producer.name] = {"rows": rows, "summary": summarize(rows)}
    return {
        "schema": SCHEMA,
        "gold_digest": gold_corpus.GOLD_DIGEST,
        "case_ids": [c["id"] for c in cases],
        "model_present": with_model,
        "columns": columns,
    }


def render_table(report: dict[str, Any]) -> str:
    from core.semantic.graph_diff import AXES

    cols = list(report["columns"])
    out = [f"SEMANTIC DIFFERENTIAL V3  ({report['schema']}, gold {report['gold_digest']})"]
    out.append("  STRICT whole-turn: " + "   ".join(
        f"{name.upper()} {report['columns'][name]['summary']['strict_correct']}/{report['columns'][name]['summary']['total']}"
        for name in cols
    ) + ("" if report["model_present"] else "   MODEL PENDING (no keyed run)"))
    out.append("")
    header = f"  {'CASE':5} {'CLS':16} " + " ".join(f"{name[:8]:>8}" for name in cols) + "  failed axes (last column)"
    out.append(header)
    out.append("  " + "-" * (len(header) + 20))
    by_case = {name: {r["id"]: r for r in report["columns"][name]["rows"]} for name in cols}
    for cid in report["case_ids"]:
        cells = []
        for name in cols:
            row = by_case[name][cid]
            cells.append(f"{'OK' if row['strict'] else 'XX'}{'' if row['status'] == 'scored' else '*':>8}"[:8].rjust(8))
        last = by_case[cols[-1]][cid]
        cls = last["cls"]
        out.append(f"  {cid:5} {cls:16} " + " ".join(cells) + "  " + ",".join(last["failed"][:6]))
    out.append("")
    out.append("  per-axis coverage (cases passing that axis):")
    for axis in AXES:
        out.append(f"    {axis:22} " + "   ".join(
            f"{name[:8]:>8}={report['columns'][name]['summary']['axis_coverage'][axis]:2d}" for name in cols
        ))
    out.append("")
    out.append("  OK/XX = strict whole-turn; * = abstained or error. Gold is FROZEN; do not tune after seeing results.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RequestGraph differential V3 (per-axis, strict whole-turn)")
    parser.add_argument("--openrouter-model", default="", help="build + register a model resolver from a free OpenRouter tag (needs a key)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--table", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    transport = None
    with_model = False
    if args.openrouter_model:
        from core.semantic.resolver import ModelSemanticResolver
        from core.semantic.resolver_registry import register_resolver

        _ops, descriptions = _operations_and_descriptions()
        register_resolver(ModelSemanticResolver(catalog_descriptions=descriptions))
        transport = openrouter_eval_transport(args.openrouter_model)
        with_model = True

    report = run_differential(with_model=with_model, transport=transport)
    report["model_tag"] = args.openrouter_model or None
    text = json.dumps(report, indent=2) if args.json else render_table(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
