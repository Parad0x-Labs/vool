"""The 20-case DEVELOPMENT corpus, re-annotated as gold ``RequestGraph``s and frozen on every field.

These are the texts of ``scripts/semantic_resolver_differential.py``'s V2 corpus, VERBATIM. V2
froze seven fields per case (id, class, text, intent count, tool, prohibition, ambiguous) and scored
counts, so an unrelated reading with the right count scored perfect. Here every case carries a full
gold graph -- source spans, requests, slots, operands by role, mentions by occurrence, quantities
with dimensional identity, constraints, prohibitions, retractions, dependencies, ambiguity,
interpretation states, presentation -- and ``GOLD_DIGEST`` freezes the canonical serialization of
ALL of it. Changing any annotation moves the digest and reds the freeze test; that is the visible,
deliberate diff a gold change must be.

**These are development diagnostics, not a holdout.** Every producer in this repo was tuned with
these texts in view. They are frozen for regression; a blind generalization set is separate work.

Annotation conventions (the resolver prompt states the same ones, so a model can be measured
against them rather than against a synonym lottery):

* ``Request.state`` is RESOLVED for every interpreted request; the answerability state lives on the
  slot (ANSWERABLE_WITHOUT_TOOL, NEEDS_INPUT, AMBIGUOUS, ...).
* Entity keys and ``Quantity.asset``/``currency`` use one spelling: crypto by ticker (BTC, ETH, LTC,
  BNB, XRP), fiat by ISO-4217 (EUR, USD, RUB), equities by ticker (TSLA), commodities and everything
  else by lower-case common name (gold, silver, platinum, oil, lpg), places/products/concepts by
  their usual capitalized name.
* A prohibition's ``target`` is the object being forbidden, lower-case: "web", "tools", "gold",
  "twitter". A retraction's ``target`` is the thing withdrawn, lower-case: "search".
* Retrieval: RETRIEVAL_REQUIRED scoped to the requests that need live data; FRESHNESS only when the
  text states a recency cue ("now", "right now", "latest", "today"), spanned on that cue.
* Interpretation is recorded as stated. A retracted request keeps its constraints (it WAS a web
  search); the Retraction is what cancels it downstream.
* "X or Y" as the TARGET of one purchase ("gold or silver") is one AMBIGUOUS slot with alternatives
  (V2: am1 ambiguous). "if I have X or Y" enumerates payment scenarios and is two slots (V2: pq1 not
  ambiguous). Both follow the frozen V2 labels rather than re-deciding them here.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from core.semantic.graph_builder import RequestGraphBuilder, operand
from core.semantic.graph_serialization import canonical_json, graph_to_dict
from core.semantic.request_graph import (
    Ambiguity,
    ConstraintKind,
    DependencyKind,
    InterpretationState,
    Quantity,
    RequestGraph,
    SemanticRole,
)

GOLD_SCHEMA = "vool.semantic_requestgraph_gold.v1"

#: Guard over EVERY annotation. Computed by ``gold_digest()``; re-pin only in a commit whose message
#: says which annotation changed and why.
GOLD_DIGEST = "66c296510baac382"

_S = SemanticRole
_K = ConstraintKind
_I = InterpretationState


@dataclass(frozen=True)
class GoldCase:
    id: str
    cls: str
    text: str
    note: str
    annotate: Callable[[RequestGraphBuilder], None]


# -- the annotations ------------------------------------------------------------


def _sd1(b: RequestGraphBuilder) -> None:
    r = b.add_request("what is the capital of France")
    m = b.add_mention("France", kind="location", entity_key="France")
    b.add_slot(r, expected="capital of France", state=_I.ANSWERABLE_WITHOUT_TOOL,
               operands=(operand(_S.SUBJECT, mention_id=m),))


def _sd2(b: RequestGraphBuilder) -> None:
    r = b.add_request("explain how a hash map works")
    m = b.add_mention("hash map", kind="concept", entity_key="hash map")
    b.add_slot(r, expected="explanation of how a hash map works", state=_I.ANSWERABLE_WITHOUT_TOOL,
               operands=(operand(_S.SUBJECT, mention_id=m),))


def _sl1(b: RequestGraphBuilder) -> None:
    r = b.add_request("what is the price of gold")
    m = b.add_mention("gold", kind="asset", entity_key="gold")
    b.add_slot(r, expected="gold spot price", operands=(operand(_S.SUBJECT, mention_id=m),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r,))


def _sl2(b: RequestGraphBuilder) -> None:
    r = b.add_request("what's the weather in Oslo right now")
    m = b.add_mention("Oslo", kind="location", entity_key="Oslo")
    b.add_slot(r, expected="current weather in Oslo", operands=(operand(_S.SUBJECT, mention_id=m),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r,))
    b.add_constraint(_K.FRESHNESS, detail="right now", span=b.canonical.find("right now"), scope=(r,))


def _op1(b: RequestGraphBuilder) -> None:
    r = b.add_request("what is the price of gold and silver")
    gold = b.add_mention("gold", kind="asset", entity_key="gold")
    silver = b.add_mention("silver", kind="asset", entity_key="silver")
    b.add_slot(r, expected="gold spot price", operands=(operand(_S.SUBJECT, mention_id=gold),))
    b.add_slot(r, expected="silver spot price", operands=(operand(_S.SUBJECT, mention_id=silver),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r,))


def _op2(b: RequestGraphBuilder) -> None:
    r = b.add_request("gold, silver and platinum prices")
    for name in ("gold", "silver", "platinum"):
        m = b.add_mention(name, kind="asset", entity_key=name)
        b.add_slot(r, expected=f"{name} spot price", operands=(operand(_S.SUBJECT, mention_id=m),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r,))


def _rt1(b: RequestGraphBuilder) -> None:
    r = b.add_request("how many litecoin one bitcoin buys")
    ltc = b.add_mention("litecoin", kind="asset", entity_key="LTC")
    btc = b.add_mention("bitcoin", kind="asset", entity_key="BTC")
    b.add_slot(r, expected="amount of litecoin one bitcoin buys", operands=(
        operand(_S.TARGET, mention_id=ltc),
        operand(_S.PAYMENT, mention_id=btc, quantity=Quantity(raw="one bitcoin", exact="1", asset="BTC")),
    ))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r,))


def _rt2(b: RequestGraphBuilder) -> None:
    r0 = b.add_request("what is the price of silver?")
    r1 = b.add_request("how much gold can I buy if I sell 1 BTC now?")
    silver = b.add_mention("silver", within=b.request_span(r0), kind="asset", entity_key="silver")
    gold = b.add_mention("gold", within=b.request_span(r1), kind="asset", entity_key="gold")
    btc = b.add_mention("BTC", within=b.request_span(r1), kind="asset", entity_key="BTC")
    b.add_slot(r0, expected="silver spot price", operands=(operand(_S.SUBJECT, mention_id=silver),))
    b.add_slot(r1, expected="amount of gold purchasable with 1 BTC", operands=(
        operand(_S.TARGET, mention_id=gold),
        operand(_S.PAYMENT, mention_id=btc, quantity=Quantity(raw="1 BTC", exact="1", asset="BTC")),
    ))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r0, r1))
    b.add_constraint(_K.FRESHNESS, detail="now", span=b.canonical.find("now"), scope=(r1,))


def _mi1(b: RequestGraphBuilder) -> None:
    r0 = b.add_request("what is the weather in Rome")
    r1 = b.add_request("tell me how much gold I can buy")
    rome = b.add_mention("Rome", within=b.request_span(r0), kind="location", entity_key="Rome")
    gold = b.add_mention("gold", within=b.request_span(r1), kind="asset", entity_key="gold")
    b.add_slot(r0, expected="current weather in Rome", operands=(operand(_S.SUBJECT, mention_id=rome),))
    b.add_slot(r1, expected="amount of gold purchasable", state=_I.NEEDS_INPUT,
               reason="payment amount and asset not stated",
               operands=(operand(_S.TARGET, mention_id=gold),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r0, r1))


def _mi2(b: RequestGraphBuilder) -> None:
    r0 = b.add_request("what's the weather in oslo")
    r1 = b.add_request("how did tesla close")
    oslo = b.add_mention("oslo", within=b.request_span(r0), kind="location", entity_key="Oslo")
    tesla = b.add_mention("tesla", within=b.request_span(r1), kind="equity", entity_key="TSLA")
    b.add_slot(r0, expected="current weather in Oslo", operands=(operand(_S.SUBJECT, mention_id=oslo),))
    b.add_slot(r1, expected="Tesla last close", operands=(operand(_S.SUBJECT, mention_id=tesla),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r0, r1))


def _mi3(b: RequestGraphBuilder) -> None:
    r0 = b.add_request("what is 1000 EUR to RUB")
    r1 = b.add_request("how much gold can I buy with it")
    r2 = b.add_request("what is the weather in Rome")
    r3 = b.add_request("the water temperature in the Baltic Sea")
    eur = b.add_mention("EUR", within=b.request_span(r0), kind="currency", entity_key="EUR")
    rub = b.add_mention("RUB", within=b.request_span(r0), kind="currency", entity_key="RUB")
    gold = b.add_mention("gold", within=b.request_span(r1), kind="asset", entity_key="gold")
    rome = b.add_mention("Rome", within=b.request_span(r2), kind="location", entity_key="Rome")
    baltic = b.add_mention("Baltic Sea", within=b.request_span(r3), kind="location", entity_key="Baltic Sea")
    s0 = b.add_slot(r0, expected="1000 EUR in RUB", operands=(
        operand(_S.SOURCE, mention_id=eur, quantity=Quantity(raw="1000 EUR", exact="1000", currency="EUR")),
        operand(_S.TARGET, mention_id=rub),
    ))
    b.add_slot(r1, expected="amount of gold purchasable with the converted amount", operands=(
        operand(_S.TARGET, mention_id=gold),
        operand(_S.PAYMENT, result_ref=s0),
    ))
    b.add_slot(r2, expected="current weather in Rome", operands=(operand(_S.SUBJECT, mention_id=rome),))
    b.add_slot(r3, expected="current water temperature in the Baltic Sea",
               operands=(operand(_S.SUBJECT, mention_id=baltic),))
    b.add_dependency(r1, r0, kind=DependencyKind.VALUE, value_ref=s0)
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r0, r1, r2, r3))


def _pq1(b: RequestGraphBuilder) -> None:
    r0 = b.add_request("what is the price of oil now")
    r1 = b.add_request("how much of oil i can buy if i have 1 btc or 1 eth")
    oil0 = b.add_mention("oil", within=b.request_span(r0), kind="asset", entity_key="oil")
    oil1 = b.add_mention("oil", within=b.request_span(r1), kind="asset", entity_key="oil")
    btc = b.add_mention("btc", within=b.request_span(r1), kind="asset", entity_key="BTC")
    eth = b.add_mention("eth", within=b.request_span(r1), kind="asset", entity_key="ETH")
    b.add_slot(r0, expected="oil spot price", operands=(operand(_S.SUBJECT, mention_id=oil0),))
    b.add_slot(r1, expected="amount of oil purchasable with 1 BTC", operands=(
        operand(_S.TARGET, mention_id=oil1),
        operand(_S.PAYMENT, mention_id=btc, quantity=Quantity(raw="1 btc", exact="1", asset="BTC")),
    ))
    b.add_slot(r1, expected="amount of oil purchasable with 1 ETH", operands=(
        operand(_S.TARGET, mention_id=oil1),
        operand(_S.PAYMENT, mention_id=eth, quantity=Quantity(raw="1 eth", exact="1", asset="ETH")),
    ))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r0, r1))
    b.add_constraint(_K.FRESHNESS, detail="now", span=b.canonical.find("now"), scope=(r0,))


def _fx1(b: RequestGraphBuilder) -> None:
    r = b.add_request("convert 1000 EUR to USD")
    eur = b.add_mention("EUR", kind="currency", entity_key="EUR")
    usd = b.add_mention("USD", kind="currency", entity_key="USD")
    b.add_slot(r, expected="1000 EUR in USD", operands=(
        operand(_S.SOURCE, mention_id=eur, quantity=Quantity(raw="1000 EUR", exact="1000", currency="EUR")),
        operand(_S.TARGET, mention_id=usd),
    ))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r,))


def _lu1(b: RequestGraphBuilder) -> None:
    r = b.add_request("Find the official weight of the Apple Watch Ultra 2.")
    m = b.add_mention("Apple Watch Ultra 2", kind="product", entity_key="Apple Watch Ultra 2")
    b.add_slot(r, expected="official weight of the Apple Watch Ultra 2",
               operands=(operand(_S.SUBJECT, mention_id=m),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, detail="find the official", scope=(r,))


def _lu2(b: RequestGraphBuilder) -> None:
    r = b.add_request("search the web for the latest Python release")
    m = b.add_mention("Python", kind="software", entity_key="Python")
    b.add_slot(r, expected="latest Python release", operands=(operand(_S.SUBJECT, mention_id=m),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, detail="web", scope=(r,))
    b.add_constraint(_K.FRESHNESS, detail="latest", span=b.canonical.find("latest"), scope=(r,))


def _lpg1(b: RequestGraphBuilder) -> None:
    r0 = b.add_request("lpg price")
    r1 = b.add_request("how much of silver i can buy if i sell 1 bnb")
    lpg = b.add_mention("lpg", within=b.request_span(r0), kind="asset", entity_key="lpg")
    silver = b.add_mention("silver", within=b.request_span(r1), kind="asset", entity_key="silver")
    bnb = b.add_mention("bnb", within=b.request_span(r1), kind="asset", entity_key="BNB")
    b.add_slot(r0, expected="lpg price", operands=(operand(_S.SUBJECT, mention_id=lpg),))
    b.add_slot(r1, expected="amount of silver purchasable with 1 BNB", operands=(
        operand(_S.TARGET, mention_id=silver),
        operand(_S.PAYMENT, mention_id=bnb, quantity=Quantity(raw="1 bnb", exact="1", asset="BNB")),
    ))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r0, r1))


def _am1(b: RequestGraphBuilder) -> None:
    r = b.add_request("how much gold or silver can I buy with 1 btc?")
    gold = b.add_mention("gold", kind="asset", entity_key="gold")
    silver = b.add_mention("silver", kind="asset", entity_key="silver")
    btc = b.add_mention("btc", kind="asset", entity_key="BTC")
    b.add_slot(
        r,
        expected="amount of gold or silver purchasable with 1 BTC",
        state=_I.AMBIGUOUS,
        ambiguity=Ambiguity(alternatives=("gold", "silver"), reason="either/or target", needs_clarification=True),
        operands=(
            operand(_S.TARGET, mention_id=gold),
            operand(_S.TARGET, mention_id=silver),
            operand(_S.PAYMENT, mention_id=btc, quantity=Quantity(raw="1 btc", exact="1", asset="BTC")),
        ),
    )
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r,))


def _pr1(b: RequestGraphBuilder) -> None:
    b.add_prohibition("web", span=b.canonical.find("no web pls"))
    b.add_prohibition("gold", span=b.canonical.find("dont quote gold"))
    b.add_constraint(_K.RETRIEVAL_FORBIDDEN, detail="no web", span=b.canonical.find("no web"))


def _pr2(b: RequestGraphBuilder) -> None:
    r = b.add_request("Search the web for XRP price.")
    xrp = b.add_mention("XRP", kind="asset", entity_key="XRP")
    b.add_slot(r, expected="XRP price via web search", operands=(operand(_S.SUBJECT, mention_id=xrp),))
    b.add_constraint(_K.RETRIEVAL_REQUIRED, detail="web", scope=(r,))
    b.add_retraction("search", supersedes=r, span=b.canonical.find("WAIT. Cancel the search before execution."))


def _us1(b: RequestGraphBuilder) -> None:
    r0 = b.add_request("if it rains in Tallinn tomorrow")
    r1 = b.add_request("tell me the price of gold")
    tallinn = b.add_mention("Tallinn", within=b.request_span(r0), kind="location", entity_key="Tallinn")
    gold = b.add_mention("gold", within=b.request_span(r1), kind="asset", entity_key="gold")
    b.add_slot(r0, expected="rain forecast for Tallinn tomorrow", operands=(operand(_S.SUBJECT, mention_id=tallinn),))
    b.add_slot(r1, expected="gold spot price", operands=(operand(_S.SUBJECT, mention_id=gold),))
    b.add_constraint(_K.TIME, detail="tomorrow", span=b.canonical.find("tomorrow"), scope=(r0,))
    b.add_dependency(r1, r0, kind=DependencyKind.CONDITIONAL)
    b.add_constraint(_K.RETRIEVAL_REQUIRED, scope=(r0, r1))


CASES: tuple[GoldCase, ...] = (
    GoldCase("sd1", "single_direct", "what is the capital of France", "", _sd1),
    GoldCase("sd2", "single_direct", "explain how a hash map works", "", _sd2),
    GoldCase("sl1", "single_live", "what is the price of gold", "", _sl1),
    GoldCase("sl2", "single_live", "what's the weather in Oslo right now", "", _sl2),
    GoldCase("op1", "operand_group", "what is the price of gold and silver", "one market request, two slots", _op1),
    GoldCase("op2", "operand_group", "gold, silver and platinum prices", "one request, three slots", _op2),
    GoldCase("rt1", "ratio_role", "how many litecoin one bitcoin buys", "target litecoin, payment bitcoin", _rt1),
    GoldCase("rt2", "ratio_role", "what is the price of silver? how much gold can I buy if I sell 1 BTC now?",
             "target gold, payment BTC; freshness on the second request only", _rt2),
    GoldCase("mi1", "multi_intent", "what is the weather in Rome also tell me how much gold I can buy",
             "the purchase slot has no payment operand: NEEDS_INPUT", _mi1),
    GoldCase("mi2", "multi_intent", "what's the weather in oslo and how did tesla close", "", _mi2),
    GoldCase("mi3", "multi_intent",
             "what is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in Rome, and the water temperature in the Baltic Sea?",
             "'with it' is a VALUE dependency on the conversion slot", _mi3),
    GoldCase("pq1", "purchase_quote",
             "what is the price of oil now and how much of oil i can buy if i have 1 btc or 1 eth?",
             "'1 btc or 1 eth' enumerates two payment scenarios: two slots (V2: not ambiguous)", _pq1),
    GoldCase("fx1", "fx", "convert 1000 EUR to USD", "", _fx1),
    GoldCase("lu1", "look_it_up", "Find the official weight of the Apple Watch Ultra 2.", "explicit retrieval demand", _lu1),
    GoldCase("lu2", "look_it_up", "search the web for the latest Python release", "", _lu2),
    GoldCase("lpg1", "lpg_ambiguity", "lpg price and how much of silver i can buy if i sell 1 bnb?",
             "lpg is interpreted (a commodity); whether the runtime can serve it is downstream", _lpg1),
    GoldCase("am1", "ambiguous", "how much gold or silver can I buy with 1 btc?",
             "either/or target: one AMBIGUOUS slot with both alternatives; never silently pick one", _am1),
    GoldCase("pr1", "prohibition", "no web pls, and dont quote gold", "a prohibition, not a request: zero requests", _pr1),
    GoldCase("pr2", "prohibition", "Search the web for XRP price. WAIT. Cancel the search before execution.",
             "retracted within the turn: the request stays represented, the Retraction supersedes it", _pr2),
    GoldCase("us1", "unsupported_shape", "if it rains in Tallinn tomorrow, tell me the price of gold",
             "conditional: represented as a CONDITIONAL dependency, shape CONDITIONAL", _us1),
)

_BY_ID = {case.id: case for case in CASES}


def gold_cases() -> tuple[GoldCase, ...]:
    return CASES


def gold_case(case_id: str) -> GoldCase:
    return _BY_ID[case_id]


def gold_graph(case_id: str) -> RequestGraph:
    """A fresh gold graph for ``case_id`` (deterministic: same ids, spans and digest every time)."""
    case = _BY_ID[case_id]
    builder = RequestGraphBuilder(case.text, turn_id=f"gold:{case.id}")
    case.annotate(builder)
    return builder.build()


def gold_graphs() -> dict[str, RequestGraph]:
    return {case.id: gold_graph(case.id) for case in CASES}


def gold_digest() -> str:
    """sha256 (16 hex) over id/cls/text/note and the FULL text-free serialization of every gold."""
    rows = [
        {
            "id": case.id, "cls": case.cls, "text": case.text, "note": case.note,
            "graph": graph_to_dict(gold_graph(case.id), include_text=False),
        }
        for case in CASES
    ]
    return hashlib.sha256(canonical_json({"schema": GOLD_SCHEMA, "cases": rows}).encode("utf-8")).hexdigest()[:16]


def invented_counterpart(case_id: str) -> RequestGraph:
    """The review's exploit for this case: a graph over an UNRELATED text with the same request and
    per-request slot counts. V2 scored it perfect; V3 must fail it on slot_coverage + request_meaning."""
    gold = gold_graph(case_id)
    pieces = [f"unrelated invented request {i}" for i in range(len(gold.requests))]
    text = " and ".join(pieces) if pieces else "an unrelated invented remark"
    builder = RequestGraphBuilder(text, turn_id=f"invented:{case_id}")
    for piece, request in zip(pieces, gold.requests, strict=True):
        rid = builder.add_request(piece)
        for _ in request.slot_ids:
            builder.add_slot(rid, expected="something invented")
    if not pieces:
        # A prohibition-only gold: the exploit is an unrelated prohibition set of the same size.
        for i, _p in enumerate(gold.prohibitions):
            builder.add_prohibition(f"invented-{i}")
    return builder.build(conserve_source=False)


__all__ = [
    "CASES",
    "GOLD_DIGEST",
    "GOLD_SCHEMA",
    "GoldCase",
    "gold_case",
    "gold_cases",
    "gold_digest",
    "gold_graph",
    "gold_graphs",
    "invented_counterpart",
]
