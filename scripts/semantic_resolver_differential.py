#!/usr/bin/env python3
"""Differential ACCEPTANCE instrument: model-first resolver vs the lexical heuristic vs naive.

This is the gate for promoting the semantic authority ladder above SHADOW. It runs a FROZEN,
labelled failure-class corpus through three readings and scores each against ground truth on the
full penalty taxonomy — not merely on intent count, so a reading cannot "win" by guessing more
aggressively:

    invented_intent        more intents than were asked (over-split)
    missing_slot           fewer intents than were asked (under-split)
    missing_prohibition    a "do not X" / retraction the reading failed to represent
    false_tool_obligation  demanded a tool/retrieval where none was warranted
    collapsed_ambiguity    committed to one reading where the turn was ambiguous / out-of-scope
    wrong_operand_binding  bound the wrong entity to a role (model-only axis; needs operands)

UNKNOWN/AMBIGUOUS preserved correctly scores as CORRECT — better than a confident wrong resolution.

The corpus and its ground truth are frozen (``_CORPUS_FROZEN_SHA`` guards it). Do NOT edit the corpus
after seeing model results: it is evidence, not a target. The resolver is pluggable:

    --resolver naive   deterministic conjunction splitter — control / baseline (no model, no network)
    --resolver model   the resolver registered via core.semantic.resolver_registry (a real backend)

The MODEL column reads PENDING until a real backend is registered and a keyed run fills it. Authority
may not be promoted on the naive/heuristic columns alone.

    python scripts/semantic_resolver_differential.py --resolver naive --table
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# --- FROZEN corpus + ground truth -------------------------------------------
# GT per case: intents (answerable slot count), tool (answering needs a tool/retrieval),
# prohibition (a "do not"/retraction that MUST be represented), ambiguous (must preserve UNKNOWN /
# not collapse), operands (expected role binding, model-only axis; None when not applicable).

CORPUS: tuple[dict[str, Any], ...] = (
    {"id": "sd1", "cls": "single_direct", "text": "what is the capital of France",
     "intents": 1, "tool": False, "prohibition": False, "ambiguous": False},
    {"id": "sd2", "cls": "single_direct", "text": "explain how a hash map works",
     "intents": 1, "tool": False, "prohibition": False, "ambiguous": False},
    {"id": "sl1", "cls": "single_live", "text": "what is the price of gold",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "sl2", "cls": "single_live", "text": "what's the weather in Oslo right now",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "op1", "cls": "operand_group", "text": "what is the price of gold and silver",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": False,
     "note": "one market intent, two operands"},
    {"id": "op2", "cls": "operand_group", "text": "gold, silver and platinum prices",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "rt1", "cls": "ratio_role", "text": "how many litecoin one bitcoin buys",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": False,
     "operands": {"target": "litecoin", "payment": "bitcoin"}},
    {"id": "rt2", "cls": "ratio_role",
     "text": "what is the price of silver? how much gold can I buy if I sell 1 BTC now?",
     "intents": 2, "tool": True, "prohibition": False, "ambiguous": False,
     "operands": {"target": "gold", "payment": "BTC"}},
    {"id": "mi1", "cls": "multi_intent", "text": "what is the weather in Rome also tell me how much gold I can buy",
     "intents": 2, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "mi2", "cls": "multi_intent", "text": "what's the weather in oslo and how did tesla close",
     "intents": 2, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "mi3", "cls": "multi_intent",
     "text": "what is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in Rome, and the water temperature in the Baltic Sea?",
     "intents": 4, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "pq1", "cls": "purchase_quote",
     "text": "what is the price of oil now and how much of oil i can buy if i have 1 btc or 1 eth?",
     "intents": 2, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "fx1", "cls": "fx", "text": "convert 1000 EUR to USD",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "lu1", "cls": "look_it_up", "text": "Find the official weight of the Apple Watch Ultra 2.",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "lu2", "cls": "look_it_up", "text": "search the web for the latest Python release",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": False},
    {"id": "lpg1", "cls": "lpg_ambiguity", "text": "lpg price and how much of silver i can buy if i sell 1 bnb?",
     "intents": 2, "tool": True, "prohibition": False, "ambiguous": False,
     "note": "lpg unknown-asset arm must be preserved as a named-but-unresolvable slot, not dropped"},
    {"id": "am1", "cls": "ambiguous", "text": "how much gold or silver can I buy with 1 btc?",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": True,
     "note": "either/or — refuse-with-both is correct; do not silently pick one"},
    {"id": "pr1", "cls": "prohibition", "text": "no web pls, and dont quote gold",
     "intents": 0, "tool": False, "prohibition": True, "ambiguous": False,
     "note": "a prohibition, not a request"},
    {"id": "pr2", "cls": "prohibition",
     "text": "Search the web for XRP price. WAIT. Cancel the search before execution.",
     "intents": 0, "tool": False, "prohibition": True, "ambiguous": False,
     "note": "retracted within the turn"},
    {"id": "us1", "cls": "unsupported_shape", "text": "if it rains in Tallinn tomorrow, tell me the price of gold",
     "intents": 1, "tool": True, "prohibition": False, "ambiguous": True,
     "note": "conditional — out-of-scope shape; represent the condition, do not answer flat"},
)

#: Guard: the frozen corpus + ground truth must not drift after model results are seen. A change to
#: any case's text or ground truth changes this digest and reds the freeze test — a visible,
#: deliberate diff, never a quiet tune to make the model's score go green.
_CORPUS_FROZEN_SHA = "409584e50726f24e"


def _corpus_digest() -> str:
    payload = json.dumps(
        [
            {k: c[k] for k in ("id", "cls", "text", "intents", "tool", "prohibition", "ambiguous")}
            for c in CORPUS
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Operations menu offered to a resolver when the live catalog is unavailable.
_FALLBACK_OPERATIONS = {
    "market.quote": "a live market/commodity/crypto price lookup",
    "weather.forecast": "a live weather or water-temperature lookup",
    "currency.convert": "convert an amount from one currency to another",
    "purchasable_amount": "how much of an asset a given amount/holding can buy",
    "web.search": "look something up on the web",
    "workspace.read_file": "read a file in the workspace",
}

_TOOL_OPERATIONS = frozenset(_FALLBACK_OPERATIONS) - {"unknown"}


def _openrouter_api_key() -> str:
    """The OpenRouter key from env, else the runtime credential store. Never returned to a caller
    that prints it — used only inside the request Authorization header below."""
    key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key
    try:
        from core.credential_store import get_credential

        return str(get_credential("llm.cloud.openrouter") or "").strip()
    except Exception:
        return ""


def openrouter_eval_backend(model: str, *, timeout_s: float = 30.0, urlopen=None, api_key: str = ""):
    """A minimal OpenAI-compatible chat backend for OFFLINE resolver evaluation only.

    EVAL-ONLY, and deliberately in the harness rather than in ``core/``: the runtime's production
    shadow wiring uses the runtime's own model-ask seam (``core.agent_runtime.turn_planner_hook``),
    which is sealed, ledgered and key-resolved. This is a stdlib-only, cloud-only (remote HTTPS; no
    local model, no agent boot) call so the differential can be run as one command against a free
    OpenRouter model. `urlopen` is injectable so the request build + reply parse are unit-testable
    without the network.
    """
    import urllib.request

    key = api_key or _openrouter_api_key()
    if not key:
        raise SystemExit(
            "no OpenRouter key: set OPENROUTER_API_KEY or store llm.cloud.openrouter, "
            "then re-run with --resolver model --openrouter-model <free-tag>"
        )
    opener = urlopen or urllib.request.urlopen

    def _backend(system: str, user: str) -> str:
        payload = json.dumps(
            {
                "model": model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with opener(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
        return str(body["choices"][0]["message"]["content"])

    return _backend


@dataclass
class NaiveSplitResolver:
    """Deterministic conjunction splitter. No model. The control and the baseline to beat."""

    def propose(self, canonical, *, operations: Sequence[str]):
        from core.semantic.types import IntentProposal

        parts = [canonical.text]
        for sep in (" and ", " also ", "; ", ", and ", ", "):
            parts = [seg for chunk in parts for seg in chunk.split(sep)]
        parts = [p.strip() for p in parts if p.strip()]
        return tuple(
            IntentProposal(index=i, request_text=frag, operation="unknown")
            for i, frag in enumerate(parts)
        )


@dataclass
class Reading:
    """The comparable signals extracted from one reading of a turn."""

    intents: int
    tool_demanded: bool | None       # None = axis not observable for this reading
    prohibition_represented: bool | None
    ambiguity_preserved: bool | None
    confidence: str
    detail: str = ""


def _operations_and_descriptions() -> tuple[tuple[str, ...], dict[str, str]]:
    try:
        from core.semantic.operation_catalog import catalog_entries

        rows = catalog_entries(include_unsupported=False)
        if rows:
            names = tuple(r["name"] for r in rows if r["name"])
            return names, {r["name"]: r["description"] for r in rows if r["name"]}
    except Exception:
        pass
    return tuple(_FALLBACK_OPERATIONS), dict(_FALLBACK_OPERATIONS)


# --- reading extractors ------------------------------------------------------


def read_naive(text: str, resolver) -> Reading:
    from core.semantic.canonical_text import CanonicalText

    proposals = resolver.propose(CanonicalText.of(text), operations=("unknown",))
    return Reading(
        intents=len(proposals),
        tool_demanded=None,          # naive has no notion of tools
        prohibition_represented=None,  # nor prohibitions
        ambiguity_preserved=None,      # nor ambiguity
        confidence="n/a",
    )


def read_heuristic(text: str) -> Reading:
    from core.agent_runtime.answer_coverage import interpret_request
    from core.execution_requirements import requirements_for
    from core.retrieval_constraints import analyze_retrieval_constraints

    intents = len(interpret_request(text).requests)
    req = requirements_for(text, source_context={"surface": "api"})
    tool = bool(req.tools_required) or req.answer_mode in {"LIVE_DATA", "GROUNDED"}
    con = analyze_retrieval_constraints(text)
    prohibition = bool(getattr(con, "has_prohibition", False) or getattr(con, "forbids_external_retrieval", False)
                       or getattr(con, "forbids_all_tools", False))
    try:
        from core.within_turn_retraction import turn_retracts_an_instruction

        prohibition = prohibition or bool(turn_retracts_an_instruction(text))
    except Exception:
        pass
    return Reading(
        intents=intents,
        tool_demanded=tool,
        prohibition_represented=prohibition,
        ambiguity_preserved=None,   # the lexical heuristic has no ambiguity/UNKNOWN concept
        confidence=req.answer_mode,
    )


def read_model(text: str, resolver, operations: Sequence[str]) -> Reading | None:
    from core.semantic.canonical_text import CanonicalText

    try:
        proposals = tuple(resolver.propose(CanonicalText.of(text), operations=tuple(operations)))
    except Exception as exc:
        return Reading(0, None, None, None, confidence=f"error:{type(exc).__name__}", detail=str(exc)[:80])
    ops = [p.operation for p in proposals]
    tool = any(o in _TOOL_OPERATIONS or (o != "unknown" and "." in o) for o in ops)
    all_unknown = bool(ops) and all(o == "unknown" for o in ops)
    # The model represents a prohibition/ambiguity by ABSTAINING (no proposals) or by declining to
    # commit an operation (all unknown). It has no authority to refuse — that is downstream — so at
    # this layer "did not confidently resolve" is the observable, correct signal for those classes.
    abstained = (len(proposals) == 0) or all_unknown
    return Reading(
        intents=len(proposals),
        tool_demanded=tool,
        prohibition_represented=abstained,
        ambiguity_preserved=abstained,
        confidence="abstain" if abstained else "committed",
    )


# --- scoring against the penalty taxonomy -----------------------------------


def score(reading: Reading | None, gt: dict[str, Any]) -> tuple[bool, str]:
    """(correct, failure_type). First violated axis wins, in consequence order."""
    if reading is None:
        return False, "pending"
    if reading.confidence.startswith("error:"):
        return False, "resolver_error"
    # 1. A prohibition that must be represented and was not (only scored where observable).
    if gt["prohibition"] and reading.prohibition_represented is False:
        return False, "missing_prohibition"
    # 2. A tool demanded where none was warranted.
    if not gt["tool"] and reading.tool_demanded is True:
        return False, "false_tool_obligation"
    # 3. Ambiguity/out-of-scope collapsed into a confident single reading.
    if gt["ambiguous"] and reading.ambiguity_preserved is False:
        return False, "collapsed_ambiguity"
    # 4/5. Slot accounting.
    if reading.intents > gt["intents"]:
        return False, "invented_intent"
    if reading.intents < gt["intents"]:
        return False, "missing_slot"
    return True, "correct"


def run_differential(resolver_kind: str) -> dict[str, Any]:
    operations, _desc = _operations_and_descriptions()
    naive = NaiveSplitResolver()
    model_resolver = None
    if resolver_kind == "model":
        from core.semantic.resolver_registry import active_resolver

        model_resolver = active_resolver()

    rows: list[dict[str, Any]] = []
    for case in CORPUS:
        text = case["text"]
        r_naive = read_naive(text, naive)
        r_heur = read_heuristic(text)
        r_model = read_model(text, model_resolver, operations) if model_resolver is not None else None
        c_naive, f_naive = score(r_naive, case)
        c_heur, f_heur = score(r_heur, case)
        c_model, f_model = score(r_model, case)
        rows.append(
            {
                "id": case["id"], "cls": case["cls"], "text": text,
                "gt": {k: case[k] for k in ("intents", "tool", "prohibition", "ambiguous")},
                "naive": {"intents": r_naive.intents, "correct": c_naive, "failure": f_naive, "confidence": r_naive.confidence},
                "heuristic": {"intents": r_heur.intents, "tool": r_heur.tool_demanded,
                              "prohibition": r_heur.prohibition_represented, "correct": c_heur,
                              "failure": f_heur, "confidence": r_heur.confidence},
                "model": None if r_model is None else {
                    "intents": r_model.intents, "tool": r_model.tool_demanded,
                    "prohibition": r_model.prohibition_represented, "ambiguity": r_model.ambiguity_preserved,
                    "correct": c_model, "failure": f_model, "confidence": r_model.confidence},
            }
        )
    return {
        "schema": "semantic_resolver_differential_v2",
        "corpus_digest": _corpus_digest(),
        "resolver": resolver_kind,
        "model_present": model_resolver is not None,
        "rows": rows,
        "summary": _summary(rows),
    }


def _summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def correct(col: str) -> int:
        return sum(1 for r in rows if r.get(col) and r[col].get("correct"))

    model_scored = any(r["model"] is not None for r in rows)
    return {
        "total": len(rows),
        "naive_correct": correct("naive"),
        "heuristic_correct": correct("heuristic"),
        "model_correct": correct("model") if model_scored else None,
        "model_present": model_scored,
    }


def _cell(reading: dict[str, Any] | None) -> str:
    if reading is None:
        return "PENDING"
    mark = "OK" if reading["correct"] else "XX"
    return f"{reading['intents']}·{mark}·{reading['failure']}"


def render_table(report: dict[str, Any]) -> str:
    s = report["summary"]
    out = [
        f"SEMANTIC RESOLVER DIFFERENTIAL  ({report['schema']}, corpus {report['corpus_digest']})",
        f"  NAIVE {s['naive_correct']}/{s['total']}   HEURISTIC {s['heuristic_correct']}/{s['total']}   "
        f"MODEL {s['model_correct'] if s['model_present'] else 'PENDING (no keyed run)'}"
        + (f"/{s['total']}" if s["model_present"] else ""),
        "",
        f"  {'CASE':5} {'CLS':16} {'GT(int/tool/proh/amb)':22} {'NAIVE':14} {'HEURISTIC':16} {'MODEL':18} FAILURE(model)",
        "  " + "-" * 108,
    ]
    for r in report["rows"]:
        gt = r["gt"]
        gt_s = f"{gt['intents']}/{int(gt['tool'])}/{int(gt['prohibition'])}/{int(gt['ambiguous'])}"
        model_fail = r["model"]["failure"] if r["model"] else "pending"
        out.append(
            f"  {r['id']:5} {r['cls']:16} {gt_s:22} {_cell(r['naive']):14} {_cell(r['heuristic']):16} "
            f"{_cell(r['model']):18} {model_fail}"
        )
    out += ["", "  cell = <intents>·<OK/XX>·<failure_type>. Corpus is FROZEN; do not tune after seeing model results."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="resolver-vs-heuristic acceptance differential")
    parser.add_argument("--resolver", choices=("naive", "model"), default="naive")
    parser.add_argument("--openrouter-model", default="",
                        help="build + register a model resolver from a free OpenRouter tag (needs a key)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--table", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    if args.openrouter_model:
        from core.semantic.resolver import ModelSemanticResolver
        from core.semantic.resolver_registry import register_resolver

        _ops, descriptions = _operations_and_descriptions()
        backend = openrouter_eval_backend(args.openrouter_model)
        register_resolver(ModelSemanticResolver(backend, catalog_descriptions=descriptions))
        args.resolver = "model"

    report = run_differential(args.resolver)
    report["model_tag"] = args.openrouter_model or None
    if args.json:
        text = json.dumps(report, indent=2)
    else:
        text = render_table(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
