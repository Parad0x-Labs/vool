"""The slice-1 node kinds. Every line of domain knowledge in the conductor lives in this file.

That containment is the point. `core.conductor.graph`, `.scheduler`, `.planner`, `.compose` and
`.receipts` contain no weather, market, arithmetic or repository vocabulary at all, so a seventh
domain is a registration here rather than an edit there.

Two rules this module keeps that the lanes it borrows from do not:

* **An adapter sees only its own clause.** `expand_arguments` is handed the node's request text and
  nothing else. On main, `_extract_weather_locations` runs against the whole normalized message,
  which is how ``"…weather for Kaunas and Tallinn and the price of bitcoin"`` produced a real
  weather subtask with ``location='price of bitcoin'`` that then fetched
  ``wttr.in/price%20of%20bitcoin``. Scoping the input makes that class of contamination
  unreachable rather than merely harder.
* **Derived work is computed, not narrated.** `comparison` does arithmetic over the dependency
  results; `conclusion` runs a real scoped search. Neither asks a model what the answer is. A
  comparison over two numbers delegated to a model is a fabrication risk taken for no benefit.

Weather and market work is executed by importing the live-data lane's own subtask runners rather
than re-implementing the fetches. One weather implementation, no drift. The permission gap that
comes with them is inherited and stated in the slice report, not papered over: those runners call
`tools.web.web_research` directly and do not pass through
`core.tool_intent_executor.execute_tool_intent`. Workspace nodes, which touch the filesystem, do
go through that seam.
"""
from __future__ import annotations

import itertools
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.conductor.capabilities import (
    REMOTE_FETCH_EFFECT_CLASS,
    OperationCapability,
    OperationEffect,
)
from core.conductor.fresh_data_operations import FRESH_DATA_OPERATIONS
from core.conductor.node import ConductorNode
from core.conductor.registry import NodeContext, OperationSpec, register_operation
from core.conductor.result_presentation import RESULT_PRESENTATION_OPERATION
from core.turn_ir import ClauseKind, TurnClause

# --- shared text helpers -------------------------------------------------------------------
# Deliberately small and local. These shape a clause the planner already isolated; they never
# decide WHAT a clause is about. That decision belongs to the planner and to the recognizers.

_WORD_RE = re.compile(r"[A-Za-z0-9_.]+")

#: Nouns that describe the *shape* of a code question rather than its subject. Dropping them turns
#: "the provider retry implementation" into the terms that actually appear in source.
_GENERIC_CODE_NOUNS = frozenset(
    {
        "implementation", "implementations", "code", "logic", "mechanism", "mechanisms",
        "handling", "layer", "module", "modules", "file", "files", "function", "functions",
        "method", "methods", "class", "classes", "path", "paths", "part", "parts", "thing",
        "stuff", "side", "area", "piece", "bit", "system", "systems", "setup",
    }
)

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these",
        "those", "is", "are", "was", "were", "be", "been", "being", "do", "does", "did", "have",
        "has", "had", "of", "in", "on", "at", "for", "to", "from", "with", "without", "into",
        "about", "as", "by", "it", "its", "our", "your", "my", "me", "we", "you", "i", "also",
        "please", "tell", "show", "get", "give", "check", "look", "find", "inspect", "examine",
        "review", "read", "see", "whether", "what", "which", "how", "why", "where", "when",
        "any", "some", "all", "there", "here", "current", "currently", "now", "today",
        "run", "running", "use", "using", "make", "made", "can", "could", "would", "should",
        "briefly", "brief", "explain", "explanation", "calculation", "calculate",
    }
)


def _content_terms(text: str) -> list[str]:
    """Distinctive lowercase terms in `text`, longest first, generic shape-nouns removed."""
    seen: list[str] = []
    for raw in _WORD_RE.findall(str(text or "").lower()):
        term = raw.strip(".")
        if len(term) < 3 or term in _STOPWORDS or term in _GENERIC_CODE_NOUNS:
            continue
        if term not in seen:
            seen.append(term)
    seen.sort(key=len, reverse=True)
    return seen


def _slug(text: str, *, limit: int = 40) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return (cleaned or "node")[:limit]


# --- calculation ---------------------------------------------------------------------------


#: Sentence boundaries INSIDE one clause. This is not decomposition -- the planner already decided
#: this clause is one request. It is the calculation adapter locating its own subject inside the
#: text it was handed, exactly as the weather adapter locates place names inside its own clause.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SQUARE_ROOT_RE = re.compile(
    r"^\s*(?:(?:please|now)\s+)*(?:calculate|compute|find|what\s+is)\s+"
    r"(?:the\s+)?square\s+root\s+of\s+(?P<value>\d+(?:\.\d+)?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)


def _evaluate_arithmetic(text: str) -> str | None:
    from core.agent_runtime.fast_paths_utility import (
        evaluate_direct_math_request,
        evaluate_word_math_request,
    )

    candidate = str(text or "").strip()
    if not candidate:
        return None
    square_root = _SQUARE_ROOT_RE.match(candidate)
    if square_root is not None:
        value = float(square_root.group("value"))
        result = math.sqrt(value)
        source = f"{value:g}"
        rendered = f"{result:g}"
        return f"sqrt({source}) = {rendered}."
    return evaluate_direct_math_request(candidate) or evaluate_word_math_request(candidate)


def _arithmetic_fragment(request_text: str) -> str | None:
    """The part of this clause the evaluator can actually answer, or None.

    `evaluate_direct_math_request` matches a whole request, so "What is 137 x 29?" evaluates and
    "What is 137 x 29? Explain the calculation briefly." does not -- measured live, that one word
    of trailing prose was enough to lose the arithmetic entirely. A planner that keeps a request
    and its "explain briefly" together is being reasonable, so the adapter accommodates it rather
    than the prompt forbidding it.

    A LEAD-IN bound into the same clause is the same accommodation from the other side: the
    planner kept the user's enumerating preamble attached to the first question ("Answer all
    three: What is 5+5?"), no sentence boundary separates them, and the arithmetic died as "not
    determined" while the evaluator answers the bare question (measured live, 2026-09-08). A
    colon is a clause boundary the user wrote; its segments are tried after sentence parts, so
    the existing precedence is unchanged. Every candidate must still fully evaluate -- a stray
    colon inside "10:30" yields fragments that simply do not evaluate and cost nothing.
    """
    text = str(request_text or "").strip()
    if not text:
        return None
    if _evaluate_arithmetic(text):
        return text
    candidates = list(_SENTENCE_SPLIT_RE.split(text))
    if ":" in text:
        for segment in text.split(":"):
            candidates.extend(_SENTENCE_SPLIT_RE.split(segment))
    for fragment in candidates:
        fragment = fragment.strip()
        if fragment and _evaluate_arithmetic(fragment):
            return fragment
    return None


#: Operator symbols a person writes that Python's parser does not accept. Mapped to the operator
#: they DENOTE, purely so the expression can be parsed at all -- the identity below comes from the
#: parse tree, never from this substitution. That distinction matters: a string normalizer would
#: also have to decide that "137 x 29" and "29 x 137" differ, which the tree already knows.
_OPERATOR_SYNONYMS = {"\u00d7": "*", "x": "*", "X": "*", "\u00f7": "/"}
_OPERATOR_SYNONYM_RE = re.compile(r"(?<=[\d\s)])\s*(\u00d7|[xX]|\u00f7)\s*(?=[\d(])")


def _canonical_expression_identity(text: str) -> str:
    """A canonical identity for one arithmetic expression, from its PARSE TREE.

    `137 x 29`, `137\u00d729`, `137 * 29` and `137*29` are one piece of work written four ways, and
    under the previous string key they were four execution keys -- so the same sum was scheduled,
    dispatched and reported up to four times in one turn. Spacing, and the symbol a person happened
    to type for multiplication, are notation. The tree is the work.

    `ast.dump` is the identity because `core.conductor.shared_context.evaluate_grounded_expression`
    already parses arithmetic with `ast`, so this is the representation the runtime holds rather
    than a second one invented here. Operand ORDER survives it: `29 x 137` has a different tree and
    keeps a different key, which is what "meaningfully different work stays different" requires.

    Anything that will not parse falls back to its whitespace-folded text, which is what every
    calculation key was before this.
    """
    import ast

    body = " ".join(str(text or "").split()).rstrip(".,;:?!")
    if not body:
        return ""
    parseable = _OPERATOR_SYNONYM_RE.sub(lambda m: _OPERATOR_SYNONYMS[m.group(1)], body)
    try:
        tree = ast.parse(parseable, mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return body.casefold()
    return ast.dump(tree, annotate_fields=False)


def _calculation_execution_key(arguments: Mapping[str, Any]) -> str:
    """The arithmetic identity of a calculation node, however its text was stored."""
    from core.conductor.requirements import arithmetic_core

    raw = str(arguments.get("expression_text") or arguments.get("entity") or "")
    fragment = arithmetic_core(_arithmetic_fragment(raw) or raw)
    return _canonical_expression_identity(fragment)


def _calculation_expand(request_text: str) -> list[dict[str, Any]]:
    """One node when the runtime's own evaluator can answer some part of this clause.

    The evaluator is the recognizer: if nothing in the clause evaluates there is no arithmetic
    here, and claiming otherwise would produce a node that can only fail. No separate keyword list
    decides "is this maths" -- the thing that would compute the answer decides.
    """
    fragment = _arithmetic_fragment(request_text)
    if fragment is None:
        return []
    return [{"entity": "calculation", "expression_text": fragment}]


def _calculation_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    answer = _evaluate_arithmetic(str(node.arguments.get("expression_text") or ""))
    if not answer:
        raise ValueError("no evaluable arithmetic in this clause")
    rendered = str(answer).strip()
    # A sentence-final period belongs to the request, not to the expression. The direct evaluator
    # accepts ``333.`` as the numeric literal 333 and faithfully echoes it, which produced the
    # reader-visible ``999 - 333. = 666.`` on the real API path. Keep decimal points inside an
    # expression, but remove a lone terminal point immediately before the equality sign.
    rendered = re.sub(r"(?<=\d)\.(?=\s*=)", "", rendered)
    # `evaluate_direct_math_request` returns "137*29 = 3973." -- keep the whole statement as the
    # working, and pull the value out separately so `required_result_fields` has something exact to
    # enforce and a downstream node has a number rather than a sentence.
    value = rendered.rsplit("=", 1)[-1].strip().rstrip(".") if "=" in rendered else ""
    return {"statement": rendered, "value": value}


def _calculation_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    return str(result.get("statement") or "").strip()


# --- weather -------------------------------------------------------------------------------


#: A clause that is nothing but a weather word and its place(s), with no preposition: "weather
#: rome", "forecast Riga and Oslo". The shared extractor's clause patterns need "in/for/at", which
#: is right for FUSED prompt text -- but a planner clause was already routed here as a weather
#: request, so inside this operation the bare form is unambiguous. Clause-scoped on purpose; the
#: shared extractor is untouched.
_BARE_WEATHER_CLAUSE_RE = re.compile(
    r"^\s*(?:what(?:'s|\s+is)\s+)?(?:the\s+)?(?:weather|wether|wheather|forecast)\s+"
    r"(?:like\s+)?(?!in\b|for\b|at\b)(?P<rest>\S.*?)[\s?.!]*$",
    re.IGNORECASE,
)


def _clause_weather_locations(request_text: str) -> list[str]:
    """Every place THIS clause names: the shared extractor first, then the bare-clause form."""
    from tools.web.web_research import _extract_weather_locations, _is_plausible_weather_location

    text = str(request_text or "")
    found = [str(loc or "").strip().lower() for loc in _extract_weather_locations(text)]
    found = [loc for loc in found if loc]
    if found:
        return found
    match = _BARE_WEATHER_CLAUSE_RE.match(text)
    if not match:
        return []
    out: list[str] = []
    for part in re.split(r",|\band\b", match.group("rest"), flags=re.IGNORECASE):
        key = " ".join(part.split()).strip().lower()
        if key and _is_plausible_weather_location(key):
            out.append(key)
    return out


def _weather_expand(request_text: str) -> list[dict[str, Any]]:
    """One node per location named in THIS clause."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for location in _clause_weather_locations(request_text):
        key = str(location or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"entity": key.title(), "location": key})
    return out


def _weather_subjects_named(request_text: str) -> list[str]:
    """Every place this text NAMES, before anything decides whether it can be observed.

    Same extractor the expansion uses, deliberately: for weather the naming layer and the deciding
    layer genuinely coincide -- `_extract_weather_locations` recognizes a place and the plausibility
    filter lives further down, inside the fetch. That is why weather never dropped a place at
    expansion and needs no residue rule of its own.
    It is declared anyway because the obligation floor reads the USER'S REQUEST, not the planner's
    clause, and a planner that drops "and Nairobi" from its clause text is a different defect from a
    recognizer that drops it. This is the seam that sees the first one.
    """
    out: list[str] = []
    seen: set[str] = set()
    for location in _clause_weather_locations(request_text):
        key = str(location or "").strip().casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(key.title())
    return out


def _weather_realize_subject(subject: str) -> dict[str, Any] | None:
    """A bare place name into weather arguments.

    Every named place is realizable: whether an observation actually comes back is decided by the
    fetch, and a place that returns nothing fails as EXECUTION_FAILED. That distinction is the point
    -- "we tried and got nothing" is a different sentence from "nothing here can do that", and a
    plausibility guess made here would report the second while meaning the first.
    """
    place = " ".join(str(subject or "").split())
    if not place:
        return None
    return {"entity": place.title(), "location": place.casefold()}


def _turn_forbids(node: ConductorNode, ctx: NodeContext, toolset: str) -> bool:
    """Whether the TURN this node came from banned `toolset`.

    The conductor splits a message into clauses and builds a node per clause. A prohibition binds
    the turn it was stated in, not the sentence -- so the analysis has to run against the whole
    original request, which `SharedTurnContext.original_request` carries for every node.

    Measured live 2026-08-18, `route=conductor_multi_intent_plan`, `model_ran=False`, so no model
    was in a position to refuse:

        "Tell me the current ambient temperature in Helsinki, Finland. I strictly forbid you from
         utilizing any external data retrieval, web functions, or weather tools."

        clause 1 -> weather node, own text carries no ban  -> FETCHED wttr.in
        clause 2 -> the prohibition planned as its own REQUEST, reported "could not be answered"

    These two runners consulted no constraint authority at all (`analyze_retrieval_constraints`
    appeared zero times in this module), so the ban was computed correctly upstream and simply had
    no consumer here.

    Scoped negation survives because the scoping lives in the DOMAIN mapping, not in which text is
    read: "don't look up the weather in Vilnius, but get me the gold price" produces a negative
    clause whose domain is `weather` alone, so the market node still runs.
    """
    from core.retrieval_constraints import analyze_retrieval_constraints

    original = ""
    shared = getattr(ctx, "shared_context", None)
    if shared is not None:
        original = str(getattr(shared, "original_request", "") or "").strip()
    constraints = analyze_retrieval_constraints(original or node.request_text)
    return bool(
        constraints.forbids_all_tools
        or constraints.forbids_external_retrieval
        or constraints.forbids(toolset)
    )


def _weather_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Executed by the live-data lane's own weather runner -- one implementation, no fork."""
    from core.agent_runtime.live_data_plan import _weather_subtask
    from core.agent_runtime.live_data_runner import _run_weather_subtask

    if _turn_forbids(node, ctx, "weather"):
        raise ValueError("this turn forbids weather retrieval, so no observation was made")

    location = str(node.arguments.get("location") or "")
    subtask = _weather_subtask("conductor", location)
    outcome = _run_weather_subtask(subtask, timeout_s=ctx.timeout_s)
    if outcome.result is None:
        raise ValueError(outcome.failure_reason or "no weather observation returned")
    return dict(outcome.result)


def _weather_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    place = str(
        result.get("place_label") or node.arguments.get("entity") or node.arguments.get("location") or ""
    )
    condition = str(result.get("condition") or "").strip()
    temp = result.get("temperature_c")
    source = str(result.get("source") or "").strip()
    temp_text = f"{temp}°C" if temp is not None else "temperature unavailable"
    tail = f" (source: {source})" if source else ""
    return f"{place}: {condition}, {temp_text}{tail}".replace(": ,", ":")


# --- market --------------------------------------------------------------------------------


def _market_expand(request_text: str) -> list[dict[str, Any]]:
    """One node per asset named in THIS clause, resolved through the live-data alias tables."""
    from core.agent_runtime.fast_live_info_price import price_assets_named
    from core.agent_runtime.live_data_plan import _resolve_price_alias
    from tools.web.web_research import (
        _looks_like_market_quote_query_all,
        _looks_like_price_query_all,
    )

    text = str(request_text or "")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(asset_key: str, name: str, kind: str) -> None:
        if not asset_key or asset_key in seen:
            return
        seen.add(asset_key)
        out.append({"entity": name, "asset_key": asset_key, "kind": kind})

    for target in _looks_like_market_quote_query_all(text):
        _add(target.asset_key, target.asset_name, "commodity")
    for coin_id in _looks_like_price_query_all(text):
        _add(coin_id, coin_id.replace("-", " ").title(), "crypto")
    # `price_assets_named` names an asset by presence alone, with no price keyword required. The
    # two functions above both gate on a keyword first, so a clause like "BTC and ETH" -- exactly
    # what a planner produces after splitting -- resolves to nothing without this pass.
    for alias in price_assets_named(text):
        resolved = _resolve_price_alias(alias)
        if resolved is not None:
            asset_key, kind, asset_name = resolved
            _add(asset_key, asset_name, kind)
    return out


def _market_subjects_named(request_text: str) -> list[str]:
    """Every asset this clause NAMES, before anything decides whether it can be served.

    The naming layer, deliberately separated from `_market_expand`, which is the deciding layer.
    Between the two sit an authority check and a set of keyword-adjacency rules, and both were
    measured removing an asset the user had plainly written:

        "the current trading price of 1 Bitcoin (BTC) and 1 ounce of Gold in USD" -> Bitcoin only
        "BTC live price, Gold live price"                                          -> Gold only

    Those two drop opposite assets through unrelated mechanisms, which is the whole argument for
    reporting the difference rather than repairing either filter. What is named here is not a
    claim that the asset can be quoted -- only that the user asked about it, which is the fact the
    runtime was losing.

    Every subject is returned under the SAME canonical name `_market_expand` puts in a node's
    ``entity``, resolved through the same alias tables. Identity has to come from the domain, not
    from comparing strings: "1 Bitcoin (BTC)" names one asset twice, and a residue rule matching on
    text would report the parenthetical restatement as a second, unserved request.
    """
    from core.agent_runtime.fast_live_info_price import _PRICE_ASSET_ALIASES
    from core.agent_runtime.live_data_plan import _resolve_price_alias
    from tools.web.web_research import _MARKET_QUOTE_TARGETS

    lowered = " ".join(str(request_text or "").casefold().split())
    if not lowered:
        return []
    candidates: list[tuple[str, str]] = [
        (alias, target.asset_name)
        for target in _MARKET_QUOTE_TARGETS
        for alias in target.aliases
    ]
    for alias in _PRICE_ASSET_ALIASES:
        resolved = _resolve_price_alias(alias)
        candidates.append((alias, resolved[2] if resolved else alias.title()))
    # Longest alias first, and a claimed span is never re-read, so "bitcoin" is matched once rather
    # than also yielding the "bit" inside it.
    claimed: list[tuple[int, int]] = []
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for alias, label in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        for match in re.finditer(rf"\b{re.escape(alias)}\b", lowered):
            start, end = match.span()
            if any(start >= s and end <= e for s, e in claimed):
                continue
            claimed.append((start, end))
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append((start, label))
    return [label for _start, label in sorted(found)]


def _market_realize_subject(subject: str) -> dict[str, Any] | None:
    """A bare asset name into market arguments, or None when nothing here can quote it.

    Routed through the same alias resolution `_market_expand` uses, so a subject realized here is
    served by exactly the machinery a planned one would be. What differs is only the INPUT shape:
    the clause recognizers need price-query phrasing and return nothing for a bare name, which is
    why `_market_expand("Gold")` is empty while `_resolve_price_alias("gold")` resolves. An
    unresolvable name -- platinum, at the time of writing -- returns None, and the obligation comes
    to rest in CAPABILITY_UNAVAILABLE instead of being reported as though it had been attempted.
    """
    from core.agent_runtime.live_data_plan import _resolve_price_alias
    from tools.web.web_research import _MARKET_QUOTE_TARGETS

    name = " ".join(str(subject or "").split())
    if not name:
        return None
    lowered = name.casefold()
    for target in _MARKET_QUOTE_TARGETS:
        if lowered == target.asset_name.casefold() or lowered in {
            alias.casefold() for alias in target.aliases
        }:
            return {
                "entity": target.asset_name,
                "asset_key": target.asset_key,
                "kind": "commodity",
            }
    resolved = _resolve_price_alias(lowered)
    if resolved is None:
        return None
    asset_key, kind, asset_name = resolved
    return {"entity": asset_name, "asset_key": asset_key, "kind": kind}


def _market_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    from core.agent_runtime.live_data_plan import _market_subtask
    from core.agent_runtime.live_data_runner import _run_market_subtask

    if _turn_forbids(node, ctx, "market_prices"):
        raise ValueError("this turn forbids market retrieval, so no quote was fetched")

    subtask = _market_subtask(
        "conductor",
        str(node.arguments.get("asset_key") or ""),
        str(node.arguments.get("entity") or node.arguments.get("asset_key") or ""),
        kind=str(node.arguments.get("kind") or "crypto"),
    )
    outcome = _run_market_subtask(subtask, timeout_s=ctx.timeout_s)
    if outcome.result is None:
        # A quote that did not come back is a fact about the LIVE SOURCE, not a runtime fault.
        # Raised as a transport-family error so the scheduler files it as TRANSPORT_FAILED and
        # the reader is told "the live source for this could not be reached" -- measured on
        # rig s75 (2026-09-10, CoinGecko refusing three daemons in six minutes): a ValueError
        # here became "the runtime hit an internal fault while working on this".
        raise ConnectionError(outcome.failure_reason or "no quote returned")
    return dict(outcome.result)


def _market_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    name = str(node.arguments.get("entity") or node.arguments.get("asset_key") or "")
    price = result.get("price")
    currency = str(result.get("currency") or "").strip()
    change = result.get("change_24h_pct")
    price_text = f"{price} {currency}".strip() if price is not None else "price unavailable"
    change_text = f", {change:+.2f}% 24h" if isinstance(change, (int, float)) else ""
    source = str(result.get("source") or "").strip()
    tail = f" (source: {source})" if source else ""
    return f"{name}: {price_text}{change_text}{tail}"


# --- workspace investigation -----------------------------------------------------------------


#: Where an implementation lives. Prose and state files mention a subject; source files ARE it.
#:
#: Measured on the first live drive of acceptance B: an unfiltered search for "provider retry"
#: ranked CURRENT_STATE.json, AGENT_HANDOVER.md and CLAUDE.md above every source file, because
#: documentation naturally repeats the words a feature is described by. The conclusion node then
#: found "circuit breaker" in those same documents and answered YES -- confidently, and wrong. The
#: repository's actual state is that `core/retry_policy.py` is bare exponential backoff and
#: `core/circuit_breaker.py` has no importers at all.
_SOURCE_EXTENSIONS = frozenset(
    {
        "py", "pyi", "js", "jsx", "ts", "tsx", "mjs", "cjs", "go", "rs", "java", "kt", "swift",
        "c", "h", "cc", "cpp", "hpp", "cs", "rb", "php", "scala", "sh", "bash", "zsh", "ps1",
        "sql", "vue", "svelte", "m", "mm",
    }
)


def _is_source_path(path: str) -> bool:
    suffix = str(path or "").rsplit(".", 1)
    return len(suffix) == 2 and suffix[1].lower() in _SOURCE_EXTENSIONS


def _search_workspace(ctx: NodeContext, query: str, *, limit: int = 60) -> dict[str, Any]:
    """One scoped, permission-gated text search through the conductor's injected tool seam.

    Matches are filtered to source files. A question about an implementation is a question about
    code, and letting documentation outrank it produces an answer that is about what the project
    SAYS rather than about what it DOES.
    """
    if ctx.run_tool_intent is None:
        raise ValueError("no tool seam available for workspace investigation")
    execution = ctx.run_tool_intent(
        {"intent": "workspace.search_text", "arguments": {"query": query, "limit": limit}}
    )
    details = dict(getattr(execution, "details", {}) or {})
    observation = details.get("observation")
    if isinstance(observation, Mapping):
        details = {**details, **dict(observation)}
    raw = details.get("matches")
    matches = [
        match
        for match in (raw if isinstance(raw, list) else [])
        if isinstance(match, Mapping) and _is_source_path(str(match.get("path") or ""))
    ]
    return {
        "query": query,
        "ok": bool(getattr(execution, "ok", False)),
        "matches": matches,
    }


_WORKSPACE_SOURCE_CUES = frozenset(
    {
        "code",
        "codebase",
        "file",
        "files",
        "function",
        "implementation",
        "implements",
        "module",
        "repository",
        "repo",
        "script",
        "source",
        "test",
        "tests",
        "workspace",
    }
)


def _workspace_source_inquiry(request_text: str) -> bool:
    """True only when the clause itself names project/source scope.

    Content terms alone are not a domain signal.  A sink, a tyre shop and an exchange rate all
    contain searchable nouns; letting that make them workspace inquiries is the measured defect.
    """

    words = {token.strip(".").lower() for token in _WORD_RE.findall(str(request_text or ""))}
    return bool(words & _WORKSPACE_SOURCE_CUES)


def _workspace_capability_accepts(clause: TurnClause) -> bool:
    return _workspace_source_inquiry(clause.request_text)


def _workspace_conclusion_accepts(clause: TurnClause) -> bool:
    return bool(_conclusion_expand(clause.request_text))


def _workspace_expand(request_text: str) -> list[dict[str, Any]]:
    if not _workspace_source_inquiry(request_text):
        return []
    terms = _content_terms(request_text)
    if not terms:
        return []
    return [{"entity": " ".join(terms[:3]), "terms": terms[:3], "subject": str(request_text).strip()}]


def _workspace_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Search each distinctive term, then rank files by how many of them they carry.

    Ranking by term coverage rather than raw hit count is what separates the file that *implements*
    a subject from the file that merely mentions one of its words a hundred times.
    """
    terms = [str(t) for t in (node.arguments.get("terms") or []) if str(t).strip()]
    if not terms:
        raise ValueError("no searchable terms in this clause")

    per_file: dict[str, set[str]] = {}
    hits: dict[str, int] = {}
    total = 0
    for term in terms:
        found = _search_workspace(ctx, term)
        for match in found["matches"]:
            if not isinstance(match, Mapping):
                continue
            path = str(match.get("path") or "").strip()
            if not path:
                continue
            per_file.setdefault(path, set()).add(term)
            hits[path] = hits.get(path, 0) + 1
            total += 1

    ranked = sorted(per_file, key=lambda p: (len(per_file[p]), hits.get(p, 0)), reverse=True)
    return {
        "terms": terms,
        "match_count": total,
        "files": ranked[:10],
        "file_terms": {path: sorted(per_file[path]) for path in ranked[:10]},
    }


def _workspace_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    files = [str(f) for f in (result.get("files") or [])]
    subject = str(node.arguments.get("subject") or node.arguments.get("entity") or "the subject")
    if not files:
        return f"{subject}: no matching source found."
    listed = ", ".join(files[:5])
    return f"{subject}: {result.get('match_count', 0)} matches across {len(files)} files — {listed}."


# --- derived: comparison ---------------------------------------------------------------------

#: metric field -> (clause cues, direction). Direction is which extreme the cue asks for.
_COMPARABLE_METRICS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("temperature_c", ("warm", "warmer", "warmest", "hot", "hotter", "hottest", "temperature"), "max"),
    ("temperature_c", ("cold", "colder", "coldest", "cool", "cooler", "coolest"), "min"),
    ("change_24h_pct", ("moved", "mover", "move", "change", "changed", "gained", "rose"), "max_abs"),
)


def _comparison_expand(request_text: str) -> list[dict[str, Any]]:
    lowered = " ".join(str(request_text or "").lower().split())
    if not lowered:
        return []
    for metric, cues, direction in _COMPARABLE_METRICS:
        if any(cue in lowered for cue in cues):
            return [{"entity": metric, "metric": metric, "direction": direction}]
    return []


def _comparison_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Arithmetic over the dependencies' structured results. No model is consulted."""
    metric = str(node.arguments.get("metric") or "")
    direction = str(node.arguments.get("direction") or "max")

    candidates: list[tuple[str, float]] = []
    for dep_id, result in ctx.dependency_results.items():
        value = result.get(metric)
        if isinstance(value, (int, float)):
            candidates.append((dep_id, float(value)))
    if len(candidates) < 2:
        raise ValueError(
            f"comparison needs at least two dependencies carrying {metric!r}; got {len(candidates)}"
        )

    key = (lambda item: abs(item[1])) if direction == "max_abs" else (lambda item: item[1])
    winner = (min if direction == "min" else max)(candidates, key=key)
    return {
        "metric": metric,
        "direction": direction,
        "winner_node_id": winner[0],
        "winner_value": winner[1],
        "considered": {dep_id: value for dep_id, value in candidates},
    }


#: metric -> (reading noun, unit suffix). Presentation only; the arithmetic never consults it.
_METRIC_LABELS: dict[str, tuple[str, str]] = {
    "temperature_c": ("temperature", "°C"),
    "change_24h_pct": ("24h change", "%"),
}

_SUPERLATIVES = {"max": "highest", "min": "lowest", "max_abs": "largest absolute"}


def _comparison_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    """Every name and number here comes from the computed result; none is phrased by a model.

    Entities are written as `[[node:<id>]]` because this operation holds only the results it was
    handed, not the plan. `compose_answer` rewrites those to entity labels -- which means the
    sentence cannot name something the comparison did not actually compare.
    """
    considered = dict(result.get("considered") or {})
    metric = str(result.get("metric") or "")
    noun, unit = _METRIC_LABELS.get(metric, (metric, ""))
    superlative = _SUPERLATIVES.get(str(result.get("direction") or "max"), "highest")
    winner_id = str(result.get("winner_node_id") or "")

    def _number(value: Any) -> str:
        # A provider's 0.7031258092819259 is precision, not information, and printing it beside
        # the "+0.70%" the market row already showed reads as two different figures.
        if isinstance(value, float):
            return f"{value:.2f}".rstrip("0").rstrip(".")
        return str(value)

    others = ", ".join(
        f"[[node:{dep}]] {_number(value)}{unit}"
        for dep, value in considered.items()
        if dep != winner_id
    )
    tail = f"; compared with {others}" if others else ""
    winner_value = _number(result.get("winner_value"))
    return f"[[node:{winner_id}]] has the {superlative} {noun} at {winner_value}{unit}{tail}."


# --- derived: conclusion ----------------------------------------------------------------------

_CONCLUSION_SUBJECT_RE = re.compile(
    r"\b(?:has|have|contains?|includes?|implements?|uses?|supports?)\s+(?:an?\s+|any\s+)?(?P<subject>.+)$",
    re.IGNORECASE,
)


def _conclusion_expand(request_text: str) -> list[dict[str, Any]]:
    """A presence question: "…whether X has a Y". The subject is Y, and it must be stated."""
    text = " ".join(str(request_text or "").strip().split()).rstrip("?.")
    match = _CONCLUSION_SUBJECT_RE.search(text)
    if not match:
        return []
    subject = match.group("subject").strip()
    terms = _content_terms(subject)
    if not terms:
        return []
    return [{"entity": subject, "subject": subject, "terms": terms[:2]}]


def _conclusion_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Search for the subject, then ask whether it appears in the files the dependency identified.

    This is the shape that makes the answer correct rather than merely plausible. "Does the retry
    implementation have a circuit breaker" is not answered by "is there a circuit breaker
    anywhere" -- a `CircuitBreaker` class can exist and be imported by nothing. Scoping the second
    search to the first node's file set is what distinguishes those two answers, and it is only
    possible because the derived node reads its dependency's structured result.
    """
    terms = [str(t) for t in (node.arguments.get("terms") or []) if str(t).strip()]
    if not terms:
        raise ValueError("no searchable terms for this conclusion")

    dependency_files: set[str] = set()
    for result in ctx.dependency_results.values():
        for path in result.get("files") or []:
            dependency_files.add(str(path))

    anywhere: dict[str, int] = {}
    for term in terms:
        for match in _search_workspace(ctx, term)["matches"]:
            if isinstance(match, Mapping):
                path = str(match.get("path") or "").strip()
                if path:
                    anywhere[path] = anywhere.get(path, 0) + 1

    in_scope = sorted(path for path in anywhere if path in dependency_files)
    return {
        "subject": str(node.arguments.get("subject") or ""),
        "terms": terms,
        "present_in_scope": bool(in_scope),
        "exists_elsewhere": bool(anywhere) and not in_scope,
        "scope_files": sorted(dependency_files)[:10],
        "in_scope_files": in_scope[:10],
        "elsewhere_files": sorted(anywhere)[:10],
    }


def _conclusion_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    subject = str(result.get("subject") or "it")
    scope_count = len(result.get("scope_files") or [])
    if result.get("present_in_scope"):
        where = ", ".join(result.get("in_scope_files") or [])
        return f"Yes — {subject} appears in {where}."
    if result.get("exists_elsewhere"):
        where = ", ".join((result.get("elsewhere_files") or [])[:3])
        return (
            f"No — {subject} does not appear in any of the {scope_count} files that implement it. "
            f"It does exist elsewhere in the codebase ({where}), but not on this path."
        )
    return f"No — {subject} does not appear anywhere in the searched source."


# --- general: reasoning over what the whole message established --------------------------------
#
# The three operations below are the only ones in this file that read anything beyond their own
# clause, and they are the reason `wants_shared_context` is opt-in. They exist because a message can
# state its facts in one sentence and ask about them in six others:
#
#     "I exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came home with 2,100 kr."
#     Calculate how much money they effectively spent during the trip, both in kr and USD.
#
# Handed the second sentence alone -- which is exactly what `_calculation_expand` is handed, and
# correctly so -- there is no arithmetic in it. Measured on the shipped build, every one of that
# message's seven clauses failed closed for this reason and the plan was declined whole.
#
# What these do NOT do is let a model answer freehand. `quantitative_reasoning` takes a model's
# proposed *expressions* and evaluates them itself over the message's own numbers;
# `factual_explanation` closes numeric claims only when its clause invokes supplied evidence;
# `missing_information` consults no model at all. The division of labour is the same one the rest of
# this package keeps: the model proposes structure, the runtime produces the values.

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)

#: A clause that asks for a QUANTITY. Deliberately narrower than "mentions a number": "Suggest two
#: ways they could reduce currency-conversion losses" is about money and asks for no figure, and
#: sending it to an arithmetic node produces a node that can only fail.
#: `multiply`, `divide` and `subtract` were added on 2026-08-17. The list named every way of ASKING
#: for a figure and no way of NAMING the operation, so "Look up the current Bitcoin price. Multiply
#: that exact fetched price by 3" was refused at `:912` before the dependency was ever consulted --
#: measured live, the price shipped alone and the arithmetic vanished with no failure line. The
#: dependency machinery itself works: hand-wired, `bitcoin_price * 3` computed exactly, and
#: `market_quote` already exports `price` through `scheduler._derived_facts`. Only admission failed.
#: The discriminator was a single word: "calculate 64 / 8" was admitted while "Divide 64 by 8" -- the
#: same arithmetic, same literals -- was refused.
#:
#: WHY ONLY THESE THREE. `_clause_has` matches plain substrings with no word boundary, so a cue is
#: admitted anywhere inside a longer word. Measured: `ratio` fires inside "ope-ratio-n" ("Cancel the
#: file operation"), `times` inside `"times_five"` and "how many times", `add` on "Add a calendar
#: event", `product` on "Product A costs $240", `plus` inside "surplus". Each would admit a clause
#: that asks for no figure -- the exact harm the note above warns about. `multiply`, `divide` and
#: `subtract` have no such collision in ordinary English. Widening further needs word boundaries
#: first, which is a change to `_clause_has` with its own blast radius across all 19 original cues.
_QUANTITY_ASK_CUES = (
    "calculate", "compute", "work out", "figure out", "how much", "how many", "estimate",
    "total", "difference", "more or less", "good or bad", "better or worse", "stronger",
    "weaker", "purchasing power", "how far", "how long", "what would it cost", "worth",
    "multiply", "divide", "subtract",
)

_QUANTITATIVE_QUESTION_RE = re.compile(
    r"^\s*(?:what|which)\s+(?:(?:is|are)\s+the\s+)?"
    r"(?:percentage|proportion|fraction|ratio|amount|quantity|number)\b",
    re.IGNORECASE,
)


def governed_computation_clause(request_text: str, context: Any) -> bool:
    """Recognise a quantitative child of a source-bound computation heading.

    Quantitative questions and elliptical noun phrases can inherit COMPUTE;
    other questions, commands and presentation requests keep their jurisdiction.
    No heading is prepended to model text, and no foreign clause
    can borrow the original turn's instruction.
    """
    from core.turn_ir import ClauseKind, classify_clause_kind, parse_turn_ir

    nominal = bool(re.match(r"^\s*(?:the|a|an)\s+", request_text, re.IGNORECASE))
    question = bool(re.match(r"^\s*how\s+(?:many|much)\b", request_text, re.IGNORECASE)
                    or _QUANTITATIVE_QUESTION_RE.match(request_text))
    if not (nominal or question):
        return False
    turn = parse_turn_ir(str(getattr(context, "original_request", "") or ""))
    span = turn.governing_instruction_span
    if span is None or classify_clause_kind(turn.source_text[span[0]:span[1]]) is not ClauseKind.COMPUTE:
        return False
    key = " ".join(request_text.split())
    authorized = set()
    for clause in turn.clauses:
        text = " ".join(clause.request_text.split())
        child_nominal = bool(re.match(r"^\s*(?:the|a|an)\s+", text, re.IGNORECASE))
        child_question = bool(re.match(r"^\s*how\s+(?:many|much)\b", text, re.IGNORECASE)
                              or _QUANTITATIVE_QUESTION_RE.match(text))
        if ((child_nominal and clause.kind is ClauseKind.UNKNOWN)
                or (child_question and clause.kind is ClauseKind.KNOW)):
            authorized.add(text)
    # Grouping must preserve the authority of each exact source child, not merely inherit
    # a heading because the joined request happens to begin with a quantitative phrase.
    remaining = key
    while remaining:
        matches = [text for text in authorized
                   if remaining == text or remaining.startswith(text + " ")]
        if len(matches) != 1:
            return False
        matched = matches[0]
        authorized.remove(matched)
        remaining = remaining[len(matched):].lstrip()
    return bool(key)

#: A clause that asks for an EXPLANATION or a RECOMMENDATION rather than a figure.
_EXPLANATION_CUES = (
    "explain", "why ", "describe", "suggest", "recommend", "ways", "what is", "what are",
    "which countries", "which country", "how could", "how can", "tell me about", "tell me what",
    "reduce",
)

# ``_EXPLANATION_CUES`` covers ordinary direct questions, but not the equally ordinary indirect
# form a multipart planner often emits: ``what API means in software``.  The operation is still
# admitted only for a typed KNOW clause and only when the planner explicitly names it; this helper
# recognizes the request shape without turning every sentence containing ``means`` into knowledge
# authority.
_TERM_MEANING_REQUEST_RE = re.compile(
    r"\b(?:what(?:'s|s)?\s+[^.!?;\n]{1,96}?\s+means?\b"
    r"|what\s+(?:does|do|did)\s+[^.!?;\n]{1,96}?\s+mean\b"
    r"|what\s+[^.!?;\n]{1,96}?\s+stands?\s+for\b"
    r"|meaning\s+of\s+[^.!?;\n]{1,96}"
    r"|define\s+[^.!?;\n]{1,96})",
    re.IGNORECASE,
)


def _asks_for_explanation(text: str) -> bool:
    return _clause_has(text, _EXPLANATION_CUES) or bool(
        _TERM_MEANING_REQUEST_RE.search(str(text or ""))
    )

#: A clause that asks what could NOT be established.
_MISSING_INFO_CUES = (
    "assumption", "assumptions", "assume", "cannot be determined", "can't be determined",
    "cannot determine", "not be determined", "what is missing", "whats missing", "unknown",
    "undetermined", "insufficient",
)

#: Integers a reply may state without them tracing to the message. An enumeration ("two ways", "3
#: options") is a fact about the answer's own shape, not a measurement of the user's situation, and
#: no fabricated exchange rate or amount hides at this scale.
_MAX_UNGROUNDED_ENUMERATION = 12

_REPLY_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")


def _clause_has(text: str, cues: tuple[str, ...]) -> bool:
    lowered = " " + " ".join(str(text or "").lower().split()) + " "
    return any(cue in lowered for cue in cues)


def _shared_or_empty(shared: Any) -> Any:
    from core.conductor.shared_context import SharedTurnContext

    return shared if shared is not None else SharedTurnContext()


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a model reply that should be one JSON object. Returns {} for anything else."""
    text = str(raw or "").strip()
    if not text:
        return {}
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _format_number(value: float) -> str:
    text = f"{value:,.4f}".rstrip("0").rstrip(".")
    return text or "0"


# --- general: quantitative reasoning ------------------------------------------------------------


_QUANTITATIVE_SYSTEM_PROMPT = (
    "You set up the arithmetic for a question. You do NOT do the arithmetic -- the runtime "
    "evaluates every expression you write and will discard any number you state yourself.\n"
    "\n"
    "Return ONLY a JSON object:\n"
    '  "steps"           - array of {"label": short name, "expression": arithmetic, "unit": unit}\n'
    '  "cannot_determine"- array of plain sentences, each naming ONE thing the message does not\n'
    "                      establish that this part of the answer would need\n"
    "\n"
    "Rules for every expression:\n"
    "- Use ONLY + - * / ( ) and these names: the fact_N labels listed below, and step_N to refer\n"
    "  to the Nth step you already wrote in this same reply.\n"
    "- Earlier nodes' results use the listed dependency symbols, such as dependency_1_value_1.\n"
    "  Use their exact symbol, not their display label or an earlier node's step_N.\n"
    "- For physical unit conversion, use the listed unit_* symbols. Their factors come from the\n"
    "  runtime unit table; do not supply a remembered conversion constant as a literal.\n"
    "- Never write a number that is not listed as a fact. A rate you worked out yourself is a\n"
    "  step, not a literal. An expression containing an unlisted number is discarded whole.\n"
    "- Percentage fact_N contains its raw magnitude, not its fractional multiplier. Prefer the\n"
    "  listed fact_N_share symbol for percentage multiplication; it is already divided by 100.\n"
    "  Do not divide a share by 100 again. To express a computed ratio as percent, multiply by 100.\n"
    "- step_N is the original integer position in steps, not its label. Labels are display text.\n"
    "- If a step would need something the message never stated, do not guess it: leave the step\n"
    "  out and put what is missing into cannot_determine instead.\n"
    "- No prose, no markdown fence. Only the JSON object."
)


def _quantitative_expand(
    request_text: str, shared: Any = None, dependency_values: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Serve a clause that asks for a figure, when figures to work from will exist.

    "Will exist" rather than "are stated", and that is the repair. Admission used to be decided
    against the MESSAGE alone, so a request whose every number comes from a live lookup --
    "get the price of X and Y, then work out how many Y one X buys" -- was refused at PLAN time,
    before any lookup ran. The node became UNRESOLVED and no amount of correct propagation
    downstream could revive it: a dependency cannot feed a node that was never planned.

    A clause whose declared dependencies will export values is therefore admitted on that basis.
    This is not a weaker check -- it is the same check against the right set. `_quantitative_run`
    still evaluates every expression over grounded operands only, so a clause admitted here and
    then handed nothing reports what it could not establish rather than inventing it.
    """
    context = _shared_or_empty(shared)
    if not context.has_numbers and not tuple(dependency_values):
        return []
    if not (
        _clause_has(request_text, _QUANTITY_ASK_CUES)
        or _QUANTITATIVE_QUESTION_RE.match(request_text)
        or governed_computation_clause(request_text, context)
    ):
        # The purchasable-amount shape admits itself: a purchase verb plus a
        # "with N <asset>" payment leg is a figure ask even when a typo breaks
        # every literal cue ("how mcuh ... can i buy with 10 bnb" — measured
        # live). Same predicates the deterministic minter and the C2 fallback
        # key on, so planner, admission and computation cannot disagree. A
        # chained conversion ("10000 eur to gbp and then to eth") is a figure
        # ask for the same reason — its second leg exists only as a computation.
        purchase_shape = (
            purchasable_amount_operands(request_text) is not None
            and bool(_PURCHASE_VERB_RE.search(request_text))
        )
        if not purchase_shape and not fx_chain_shape(request_text):
            return []
    return [
        {
            "entity": _slug(request_text, limit=32) or "quantity",
            "clause": " ".join(str(request_text or "").split()),
            # Recorded on the node so a receipt shows WHICH numbers this node was given, without
            # anyone having to re-run the extraction to find out -- and so the renderer can write
            # the working as the user's own figures rather than as fact_1 / fact_2.
            "fact_labels": [fact.label for fact in context.facts],
            "fact_values": {fact.label: fact.value for fact in context.facts},
            # The operand ROLES this clause resolves to, recorded at plan time so the receipt
            # shows which asset is bought and which pays before any price arrives, and so the
            # computation reads the same decision the planner made.
            **(
                {"roles": roles.as_argument()}
                if (roles := resolve_purchase_roles(request_text)) is not None
                else {}
            ),
        }
    ]


#: An anaphoric arithmetic clause: an operator phrase over "that/this/the result|answer|number"
#: or a bare "it"/"that", with the operand in the message ("take that result and divide it by
#: 6", "multiply it by 3", "add 5 to that", "then divide by 6"). The reference resolves to a
#: dependency's exported value, never to the model: the number is already in the plan's hands.
_ANAPHORIC_REF = (
    r"(?:(?:that|this|the)\s+(?:result|answer|number|total|value)|\bit\b|\bthat\b)"
)
_ANAPHORIC_OPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdivide\s+(?:(?:(?:that|this|the)\s+(?:result|answer|number|total|value)|\bit\b|\bthat\b)\s+)?by\s+(?P<n>-?\d+(?:\.\d+)?)", re.IGNORECASE), "/"),
    (re.compile(r"\b(?:multiply|times)\s+(?:(?:(?:that|this|the)\s+(?:result|answer|number|total|value)|\bit\b|\bthat\b)\s+)?by\s+(?P<n>-?\d+(?:\.\d+)?)", re.IGNORECASE), "*"),
    (re.compile(r"\badd\s+(?P<n>-?\d+(?:\.\d+)?)\s+to\s+(?:(?:(?:that|this|the)\s+(?:result|answer|number|total|value)|\bit\b|\bthat\b))", re.IGNORECASE), "+"),
    (re.compile(r"\bsubtract\s+(?P<n>-?\d+(?:\.\d+)?)\s+from\s+(?:(?:(?:that|this|the)\s+(?:result|answer|number|total|value)|\bit\b|\bthat\b))", re.IGNORECASE), "-"),
)


def _anaphoric_arithmetic_binding(
    clause: str, ctx: NodeContext
) -> tuple[str, float, str, str] | None:
    """One operation over a value a dependency already exported, model-free.

    "Now take that result and divide it by 6" is one division of a number the plan holds by a
    number in the message. The only thing the generation seam was ever asked for here was the
    EXPRESSION -- and with a dead model lane that turned a derivable figure into "not
    determined" (measured live 2026-09-08). Same doctrine as `_purchasable_amount_fallback` and
    `_fx_chain_amount_fallback`: a shape the runtime can compute exactly never buys a generation.

    Binds ONLY when exactly one numeric value arrived from declared dependencies -- with two or
    more the anaphora is ambiguous and the honest path stays the generation seam. Returns None
    for every clause that is not this shape.
    """
    text = " ".join(str(clause or "").split())
    if not text:
        return None
    numeric: list[tuple[str, float]] = []
    for name, value in dict(ctx.derived_facts or {}).items():
        try:
            numeric.append((str(name), float(value)))
        except (TypeError, ValueError):
            continue
    if len(numeric) != 1:
        return None
    _dep_name, dep_value = numeric[0]
    for pattern, op in _ANAPHORIC_OPS:
        match = pattern.search(text)
        if match is None:
            continue
        operand = float(match.group("n"))
        if op == "/" and operand == 0:
            return None
        value = {"*": dep_value * operand, "/": dep_value / operand, "+": dep_value + operand, "-": dep_value - operand}[op]
        expression = f"{dep_value:g} {op} {operand:g}"
        return ("that result", value, "", expression)
    return None


def _quantitative_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Ask for expressions; compute the values here. A model never supplies a number."""
    from core.conductor.shared_context import (
        UngroundedExpressionError,
        evaluate_grounded_expression,
        percentage_share_bindings,
        render_briefing,
    )

    context = _shared_or_empty(ctx.shared_context)
    # The message's own numbers OR a dependency's exported values. Requiring the first alone was
    # the run-time twin of the plan-time gate in `_quantitative_expand`: a node whose figures all
    # arrive from live siblings cleared the graph, received them, and then refused itself on the
    # grounds that the MESSAGE stated no numbers.
    if not context.facts and not dict(ctx.derived_facts or {}):
        raise ValueError("no numeric facts were established by this message or its dependencies")

    clause = str(node.arguments.get("clause") or node.request_text or "")
    valuation = _asset_valuation_request(clause)
    if valuation is not None:
        return _asset_valuation_run(valuation, ctx)

    # DETERMINISTIC FIRST for the shapes this runtime can compute exactly
    # (measured live 2026-08-30: with the local model merely SLOW-but-alive,
    # the generation call burned the node's whole wall-clock deadline before the
    # model-free path could run — a one-division figure timed out). The
    # purchasable-amount and chained-conversion shapes are one division over
    # grounded dependency prices; a model adds latency and risk, never
    # correctness, so they never buy a generation. The generation seam is
    # therefore required only where a generation is actually bought (below):
    # a role-bound derivation computes with no seam at all, which is also what
    # lets the planner schedule it as model-free work (`node_needs_generation`).
    recorded = node.arguments.get("roles")
    roles = PurchaseRoles.from_argument(recorded) if isinstance(recorded, Mapping) else None
    if roles is None:
        roles = resolve_purchase_roles(clause)
    if roles is not None and roles.problem:
        # A role the MESSAGE does not establish -- no asset to buy, or two of them -- is exactly
        # what the contract's `cannot_determine` is for: the quotes this turn owes are served by
        # their own nodes, and this node states the one thing it cannot compute and why. Raising
        # here would reach the reader as a generic internal fault (the composer never reads
        # exception text, by design), which is a refusal without its reason.
        return {"steps": [], "values": {}, "cannot_determine": [roles.problem]}
    try:
        from core.conductor.supplied_prices import price_source_scope

        binding = purchasable_amount_binding(
            clause, ctx, roles=roles,
            price_scope=price_source_scope(node.arguments, clause, context),
        )
    except ValueError as exc:
        # The purchase shape refused with its reason: a leg's price never arrived, prices quoted
        # in currencies that would have to cross silently, a unit that does not measure the
        # asset. A stated outcome under the contract's own `cannot_determine` -- never an
        # internal fault, and never a generation asked to guess the figure the runtime declined.
        return {"steps": [], "values": {}, "cannot_determine": [" ".join(str(exc).split())]}
    if binding is not None:
        step = binding.as_step()
        problem = step_binding_problem(step, depends_on=getattr(node, "depends_on", None))
        if problem is not None:
            return {"steps": [], "values": {}, "cannot_determine": [problem]}
        return {"steps": [step], "values": {step["label"]: step["value"]}, "cannot_determine": []}
    deterministic = _fx_chain_amount_fallback(clause, ctx)
    if deterministic is None:
        deterministic = _anaphoric_arithmetic_binding(clause, ctx)
    if deterministic is not None:
        label, value, unit, expression = deterministic
        return {
            "steps": [{"label": label, "expression": expression, "value": value, "unit": unit}],
            "values": {label: value},
            "cannot_determine": [],
        }
    # Plain arithmetic the runtime's own evaluator answers, model-free. The planner names
    # `quantitative_reasoning` for clauses like this (the named-operation-wins rule then keeps
    # the calculation adapter out), and with a dead model lane the derivable figure died as
    # "not determined: the model returned no arithmetic plan" while the evaluator held the
    # exact answer (measured live, 2026-09-08, acceptance turn 18's "5 + 5"). Same doctrine
    # as the fallbacks above: a shape the runtime can compute exactly never buys a generation.
    direct = _arithmetic_fragment(clause)
    if direct is not None:
        answer = _evaluate_arithmetic(direct)
        if answer:
            value_text = str(answer).rsplit("=", 1)[-1].strip().rstrip(".")
            try:
                direct_value = float(value_text)
            except ValueError:
                direct_value = 0.0
            return {
                "steps": [
                    {
                        "label": direct,
                        "expression": direct,
                        "value": direct_value,
                        "unit": "",
                    }
                ],
                "values": {direct: direct_value},
                "cannot_determine": [],
            }

    if ctx.run_generation is None:
        raise ValueError("no generation seam available for quantitative reasoning")
    from core.conductor.quantity_units import (
        IncompatibleQuantityUnitsError,
        dimensions,
        display_unit,
        expression_dimensions,
        literal_dimensions,
        source_quantities,
    )

    conversions = quantity_conversion_bindings(context.original_request)
    quantities = source_quantities(context, _QUANTITY_UNITS)
    unit_bindings = {name: dimensions(quantity.unit) for name, quantity in quantities.items()}
    from core.conductor.registry import computed_dependency_bindings

    dependency_bindings = computed_dependency_bindings(ctx.dependency_results)
    unit_bindings.update({name: item["unit_dimensions"] for name, item in dependency_bindings.items()})
    for name, conversion in conversions.items():
        unit_bindings[name] = dimensions(conversion["target_unit"] + "/" + conversion["source_unit"])
    briefing = render_briefing(context, derived_facts=ctx.derived_facts, clause=clause)
    if dependency_bindings:
        briefing += "\nComputed dependency symbols (labels describe values; use only the symbols):\n"
        briefing += "\n".join(
            f"  {name} = {item['value']!r}; label={json.dumps(item['label'])}; "
            f"unit={display_unit(item['unit_dimensions']) if item['unit_dimensions'] else 'unresolved or dimensionless'}"
            for name, item in dependency_bindings.items()
        )
        derivations = {name: item["derivation"] for name, item in dependency_bindings.items()
                       if "derivation" in item}
        if derivations:
            briefing += (
                "\nExecuted dependency derivations (read-only provenance, NOT new operand names):\n"
                + json.dumps(derivations, ensure_ascii=True)
                + "\nEach expression belongs to its source recipe. Its source_scope_bindings are "
                "local to that expression, not this recipe's dependency namespace. fact_N still "
                "refers to the original message. Use the current dependency symbols above in "
                "your new expressions. Preserve the bases and transformations these calculations "
                "established when deriving differences, adjustments or totals; do not silently "
                "apply a different base to the same change."
            )
    briefing += "\nUnits established by the supplied quantities:\n" + "\n".join(
        f"  {name}: {quantity.unit}" for name, quantity in quantities.items() if quantity.unit
    )
    if conversions:
        briefing += "\nRuntime unit-table conversions (multiply by the named factor):\n"
        briefing += "\n".join(
            f"  {name} = {item['factor']:.15g} ({item['source_unit']} to {item['target_unit']})"
            for name, item in conversions.items()
        )
    payload = _parse_json_object(ctx.run_generation(_QUANTITATIVE_SYSTEM_PROMPT, briefing))
    # Distinct computed quantities can share a dimension without being interchangeable.
    # Reconcile that semantic choice before numeric validation, not after publication.
    # This is a bounded model correction, never an independent proof of meaning.
    reviewed = False
    dependency_dimensions = [item["unit_dimensions"] for item in dependency_bindings.values()]
    ambiguous_dependencies = any(
        dimension == other
        for index, dimension in enumerate(dependency_dimensions)
        for other in dependency_dimensions[index + 1:]
    )
    if payload.get("steps") and ambiguous_dependencies:
        review_prompt = (
            briefing + "\n\nProposed recipe (untrusted; not yet executed):\n"
            + json.dumps(payload, ensure_ascii=True)
        )
        review_system = (
            _QUANTITATIVE_SYSTEM_PROMPT
            + "\nReconcile this proposed recipe with the user's actual requested quantities. "
            "Different allocations, balances or subjects can have identical units. Trace every "
            "dependency symbol to its displayed subject and every percentage to its stated base; "
            "do not confuse a local step's position with a dependency value's position. Re-derive "
            "the requested result from the source message, then return the complete corrected "
            "recipe in the SAME steps/cannot_determine schema. Retain correct work and every "
            "requested result. Do not merely approve the proposal. If the source cannot settle "
            "an operand choice, name the missing information instead of guessing."
        )
        payload = _parse_json_object(ctx.run_generation(review_system, review_prompt))
        if not isinstance(payload.get("steps"), list):
            return {"steps": [], "values": {}, "cannot_determine": [
                "the arithmetic recipe could not be reconciled with its computed inputs"],
                "recipe_review": "unavailable"}
        reviewed = True

    symbols: dict[str, float] = {}
    for name, value in dict(ctx.derived_facts or {}).items():
        # step_N belongs to this recipe's original positions, never an upstream recipe.
        if re.fullmatch(r"step_\d+", str(name)):
            continue
        try:
            symbols[str(name)] = float(value)
        except (TypeError, ValueError):
            continue
    symbols.update({name: item["factor"] for name, item in conversions.items()})
    symbols.update({name: item["value"] for name, item in dependency_bindings.items()})

    steps: list[dict[str, Any]] = []
    values: dict[str, float] = {}
    cannot: list[str] = [
        " ".join(str(item).split())
        for item in (payload.get("cannot_determine") or [])
        if str(item or "").strip()
    ]

    raw_steps = payload.get("steps")
    for position, item in enumerate(raw_steps if isinstance(raw_steps, list) else [], start=1):
        if not isinstance(item, dict):
            cannot.append(f"Step {position}: the arithmetic plan supplied an unreadable step")
            continue
        label = " ".join(str(item.get("label") or f"step {position}").split())
        expression = str(item.get("expression") or "").strip()
        unit = " ".join(str(item.get("unit") or "").split())
        try:
            value = evaluate_grounded_expression(expression, facts=context.facts, symbols=symbols)
            result_dimensions = expression_dimensions(expression, unit_bindings, literal_dimensions(quantities))
        except (UngroundedExpressionError, IncompatibleQuantityUnitsError) as exc:
            # NOT dropped and NOT repaired. A step the runtime cannot ground is reported as a thing
            # the answer could not establish -- which is the honest reading of "the model wanted to
            # use a number nobody supplied", and is exactly what the user asked to be told.
            cannot.append(f"{label}: {exc}")
            continue
        # Rejected steps leave holes. Compacting the successful list would bind
        # references to the wrong expression or invalidate independent descendants.
        symbols[f"step_{position}"] = value
        unit_bindings[f"step_{position}"] = result_dimensions
        if result_dimensions:
            unit = display_unit(result_dimensions)
        elif result_dimensions == {} and unit not in {"", "%", "percent", "ratio"}:
            unit = ""
        values[label] = value
        steps.append({"step_id": f"step_{position}", "label": label, "expression": expression,
                      "value": value, "unit": unit, "unit_dimensions": result_dimensions,
                      "unit_authority": ("source_expression" if result_dimensions else
                                         "dimensionless_expression" if result_dimensions == {} else "unresolved")})

    if not steps:
        # C2 (AUD-20260829-003): generation supplied no steps -- a dead/failed model
        # lane must not turn a derivable figure into an "internal fault". For the
        # purchasable-amount shape the arithmetic is one division over grounded
        # operands the plan already fetched; compute it here, model-free. Every other
        # shape falls through to the honest raise below.
        fallback = _purchasable_amount_fallback(clause, ctx)
        if fallback is None:
            fallback = _fx_chain_amount_fallback(clause, ctx)
        if fallback is not None:
            label, value, unit, expression = fallback
            steps.append({"label": label, "expression": expression, "value": value, "unit": unit})
            symbols[f"step_{len(steps)}"] = value
            values[label] = value
    if not steps:
        # A derivation that could not be set up is a stated outcome, not a runtime fault. Measured
        # 2026-09-08 on the operator's live turn: the free model's reply carried no `steps`, the
        # raise that used to sit here became NODE_EXCEPTION, and the reader was told "the runtime hit
        # an internal fault" for arithmetic the runtime never received.
        if cannot:
            reason = "; ".join(cannot)
        elif not payload:
            reason = "the model returned no arithmetic plan for this clause (empty or unreadable reply)"
        else:
            reason = "the model's arithmetic plan named no evaluable step for this clause"
        return {"steps": [], "values": {}, "cannot_determine": [reason]}
    return {"steps": steps, "values": values, "cannot_determine": cannot,
            "recipe_review": "model_reconciled" if reviewed else "not_required",
            "expression_bindings": {**percentage_share_bindings(context.facts),
                                    **{name: item["factor"] for name, item in conversions.items()},
                                    **{name: item["value"] for name, item in dependency_bindings.items()}},
            "conversion_provenance": conversions}


_PURCHASE_VERB_RE = re.compile(
    r"\b(?:buys?|buying|gets?|getting|grabs?|grabbing|affords?|fits?|fitting|"
    r"purchases?|purchasing|picks?\s+up)\b",
    re.IGNORECASE,
)


def _quantitative_fulfillment(result: Mapping[str, Any]) -> Any:
    from core.runtime_task_outcome import FulfillmentStatus

    if not result.get("steps") or not result.get("values"):
        return FulfillmentStatus.FAILED
    if result.get("cannot_determine"):
        return FulfillmentStatus.PARTIALLY_FULFILLED
    return FulfillmentStatus.FULFILLED


def _quantitative_model_free(arguments: Mapping[str, Any]) -> bool:
    """Whether a quantitative node with these plan-time arguments will never buy a generation.

    The declaration `node_needs_generation` reads. Roles resolved at plan time make the run
    model-free by construction: a role problem is a stated `cannot_determine`, a bound payment
    leg is one division over dependency prices, and a binding refusal is a stated reason
    (`_quantitative_run`). Only a payment-less role set ("how much gold can I buy?") can still
    fall through to the expression path, so it is not declared model-free.

    Measured on the owner's turn (2026-09-10): the two role-bound derivations sat behind the
    single generation slot while a projected fragment node bought a 66 s cloud call, and both
    died at the plan deadline with their quotes already in hand.
    """
    if _asset_valuation_request(str(arguments.get("clause") or "")) is not None:
        return True
    roles = PurchaseRoles.from_argument((arguments or {}).get("roles"))
    if roles is None:
        return False
    return bool(roles.problem) or roles.payment is not None

#: The unit a commodity's PRICE is quoted per, by canonical asset key -- and therefore the unit an
#: amount of it is stated in. Precious metals trade per troy ounce; Brent crude per barrel. Not a
#: display table: `_bind_direct` converts a stated payment quantity ("1 kg of silver") INTO this
#: unit before it multiplies by the price, and `_label_for` names it on the derived amount.
#: Measured on the owner's turn (2026-09-10): "how much i can buy of oil if i sell 1kg of silver"
#: read "kg" as the payment ASSET, so the derivation refused with "the payment leg 'kg' is not an
#: asset", and the whole purchase fell to a model planner that labelled it an explanation.
_COMMODITY_UNITS = {
    "gold": "troy ounces",
    "silver": "troy ounces",
    "platinum": "troy ounces",
    "palladium": "troy ounces",
    "brent_crude": "barrels",
}

#: The physical dimension each price unit measures. A payment quantity may only be converted
#: within its own dimension: kilograms into troy ounces, litres into barrels, never across.
_PRICE_UNIT_DIMENSION = {"troy ounces": "mass", "barrels": "volume"}

#: Quantity units the amount grammar reads: every alias -> (canonical spelling, dimension, size in
#: that dimension's PRICE unit -- troy ounces for mass, barrels for volume). Exact definitions:
#: 1 troy ounce = 31.1034768 g, 1 avoirdupois pound = 453.59237 g, 1 metric tonne = 1,000,000 g,
#: 1 barrel = 42 US gallons = 158.987294928 L. One canonical spelling per unit so a receipt shows
#: "kg" for "kg", "kilo", "kilos", "kilogram" and "kilograms" alike. Single-letter "t" and "l"
#: are deliberately absent: an amount grammar that reads "5 t" as five tonnes anywhere in a
#: sentence buys ambiguity nothing here needs.
_QUANTITY_UNITS: dict[str, tuple[str, str, float]] = {}
for _unit_aliases, _unit_canonical, _unit_dimension, _unit_factor in (
    (("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"), "kg", "mass", 1000.0 / 31.1034768),
    (("g", "gram", "grams"), "g", "mass", 1.0 / 31.1034768),
    (
        ("oz", "ounce", "ounces", "toz", "troy oz", "troy ounce", "troy ounces"),
        "troy oz",
        "mass",
        1.0,
    ),
    (("lb", "lbs", "pound", "pounds"), "lb", "mass", 453.59237 / 31.1034768),
    (("ton", "tons", "tonne", "tonnes"), "tonne", "mass", 1_000_000.0 / 31.1034768),
    (("barrel", "barrels", "bbl", "bbls"), "barrel", "volume", 1.0),
    (("litre", "litres", "liter", "liters"), "litre", "volume", 1.0 / 158.987294928),
    (("gal", "gallon", "gallons"), "gal", "volume", 1.0 / 42.0),
):
    for _unit_alias in _unit_aliases:
        _QUANTITY_UNITS[_unit_alias] = (_unit_canonical, _unit_dimension, _unit_factor)

_UNIT_WORD = "|".join(
    re.escape(alias).replace(r"\ ", r"\s+")
    for alias in sorted(_QUANTITY_UNITS, key=len, reverse=True)
)


def quantity_conversion_bindings(request_text: str) -> dict[str, dict[str, Any]]:
    """Expose same-dimension conversions between explicit units from the existing table.

    The purchase grammar may interpret ounces in a metal trade, but general
    arithmetic cannot assume that an unqualified ounce means a troy ounce.
    """
    from core.conductor.quantity_units import explicit_physical_unit_pattern

    units: dict[str, tuple[str, float]] = {}
    for alias, (canonical, dimension, factor) in _QUANTITY_UNITS.items():
        unit_pattern = explicit_physical_unit_pattern(alias, canonical)
        if unit_pattern is None:
            continue
        pattern = r"(?<![^\W\d])" + unit_pattern + r"(?!\w)"
        if re.search(pattern, request_text, re.IGNORECASE):
            units[canonical] = (dimension, factor)
    result = {}
    for source, (dimension, factor) in sorted(units.items()):
        for target, (target_dimension, target_factor) in sorted(units.items()):
            if source == target or dimension != target_dimension:
                continue
            name = f"unit_{source.replace(' ', '_')}_to_{target.replace(' ', '_')}"
            result[name] = {"source_unit": source, "target_unit": target,
                            "factor": factor / target_factor, "authority": "runtime_quantity_units"}
    return result

#: An OPTIONAL unit between an amount and the asset it measures: "1kg of silver", "2 ounces of
#: gold", "10 barrels of oil", "1.5oz gold". The unit must end at a word boundary and be followed
#: by whitespace, so "1 gold" never splits into the unit "g" and the asset "old". `_UNIT_LEG`
#: captures the unit; `_UNIT_LEG_NC` is the same shape without a group, for patterns whose match
#: is re-read by `_AMOUNT_TOKEN_RE` afterwards.
_UNIT_LEG = rf"(?:({_UNIT_WORD})\b\s+(?:of\s+)?)?"
_UNIT_LEG_NC = rf"(?:(?:{_UNIT_WORD})\b\s+(?:of\s+)?)?"


def _canonical_unit(word: str | None) -> str:
    """The canonical spelling of a quantity unit the grammar captured, or "" for none."""
    key = " ".join(str(word or "").casefold().split())
    if not key:
        return ""
    entry = _QUANTITY_UNITS.get(key)
    return entry[0] if entry is not None else ""


def _unit_dimension(unit: str) -> str:
    entry = _QUANTITY_UNITS.get(str(unit or "").casefold())
    return entry[1] if entry is not None else ""


def _payment_unit_problem(unit: str, role: PurchaseRole | None) -> str:
    """Why a stated quantity unit cannot measure `role`, or "" when it can (or none was stated).

    A unit is a claim about the asset's dimension. Kilograms measure silver (priced per troy ounce)
    and litres measure crude (priced per barrel); neither measures Ethereum, and barrels do not
    measure gold. The mismatch is a stated refusal with its reason, never a silent count.
    """
    if not unit or role is None:
        return ""
    price_unit = _COMMODITY_UNITS.get(str(role.key or "").casefold(), "")
    stated = _unit_dimension(unit)
    if not price_unit:
        return (
            f"'{unit}' is a unit of {stated or 'measure'}, and {role.entity} is not priced by "
            f"{stated or 'measure'}, so the stated amount cannot be valued"
        )
    if _PRICE_UNIT_DIMENSION.get(price_unit, "") != stated:
        return (
            f"'{unit}' is a unit of {stated or 'measure'}, but {role.entity} is priced per "
            f"{price_unit.rstrip('s')}, so the stated amount cannot be valued"
        )
    return ""


def _quantity_in_price_unit(
    quantity: float, unit: str, role: PurchaseRole
) -> tuple[float, str, str]:
    """(quantity expressed in the asset's price unit, that unit, conversion note or "").

    Dimensional arithmetic, stated in the working: "1 kg = 32.1507 troy ounces". A quantity with
    no stated unit is already in the price unit (a count of coins, or troy ounces of a metal --
    the reading every earlier derivation used). Raises ValueError with the reader-facing reason
    when the unit does not measure the asset.
    """
    problem = _payment_unit_problem(unit, role)
    if problem:
        raise ValueError(problem)
    price_unit = _COMMODITY_UNITS.get(str(role.key or "").casefold(), "")
    if not unit or not price_unit:
        return quantity, price_unit, ""
    canonical, _dimension, factor = _QUANTITY_UNITS[unit.casefold()]
    converted = quantity * factor
    if abs(factor - 1.0) < 1e-12:
        return quantity, price_unit, ""
    note = f"{_format_number(quantity)} {canonical} = {_format_number(converted)} {price_unit}"
    return converted, price_unit, note


#: The payment leg of a purchasable-amount ask: "with/using/for/from N <asset>"
#: or the inverted "if i HAVE/SELL N <asset>" phrasing (measured live:
#: "if i have 1 btc how much bnb i can buy if i sell it" — payment stated as a
#: holding, not a budget). Single-word asset: payment legs are tickers/names.
#: A bare "N [unit of] <asset>" anywhere in the request -- what an anaphoric payment leg binds
#: back to. Groups: (1) the amount, (2) an optional unit, (3) the asset word.
_AMOUNT_TOKEN_RE = re.compile(
    rf"\b(\d[\d.,]*)\s*{_UNIT_LEG}([a-z][a-z0-9-]{{1,20}})\b", re.IGNORECASE
)


def _leg_from_match(match: re.Match[str]) -> tuple[float, str, str] | None:
    """(quantity, canonical unit or "", asset text) from a three-group amount match, or None."""
    try:
        quantity = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    if quantity <= 0:
        return None
    return quantity, _canonical_unit(match.group(2)), match.group(3).strip().casefold()

#: Cardinal number words the amount grammar folds to digits before it reads an amount -- a CLOSED
#: class of units, tens, scales and "half". Measured on the built candidate 60a91da3 (2026-09-07):
#: "with one bitcoin" produced no payment operand because the grammar accepted digits only, so the
#: conductor declined and a quote-only lane answered the amount question with a price.
_NUMBER_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_NUMBER_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_NUMBER_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
_NUMBER_WORD = "|".join(
    sorted([*_NUMBER_UNITS, *_NUMBER_TENS, *_NUMBER_SCALES, "half"], key=len, reverse=True)
)
_NUMBER_WORDS_RE = re.compile(
    rf"\b(?:{_NUMBER_WORD})(?:(?:[\s-]+|\s+and\s+(?:an?\s+)?)(?:{_NUMBER_WORD}))*\b(?:\s+an?\b)?",
    re.IGNORECASE,
)
_ARTICLE_AMOUNT_RE = re.compile(r"\b(an?)\s+([a-z][a-z0-9-]{1,20})\b", re.IGNORECASE)


def _numeral_value(words: list[str]) -> float | None:
    """The value of a cardinal written in words, or None when no number word is present."""
    total = 0.0
    current = 0.0
    half = False
    seen = False
    for word in words:
        if word in _NUMBER_UNITS:
            current += _NUMBER_UNITS[word]
            seen = True
        elif word in _NUMBER_TENS:
            current += _NUMBER_TENS[word]
            seen = True
        elif word in _NUMBER_SCALES:
            scale = _NUMBER_SCALES[word]
            current = (current or 1) * scale
            if scale >= 1000:
                total += current
                current = 0.0
            seen = True
        elif word == "half":
            half = True
            seen = True
    if not seen:
        return None
    value = total + current
    if half:
        value = 0.5 if value == 0 else value + 0.5
    return value


def _priceable_word_follows(body: str, end: int) -> bool:
    # A unit may stand between the numeral and the asset ("one kilogram of silver"): the numeral
    # still folds, because the word the amount MEASURES is the priceable one.
    following = re.match(
        rf"\s*{_UNIT_LEG_NC}([a-z][a-z0-9-]{{1,20}})\b", body[end:], re.IGNORECASE
    )
    return following is not None and _resolve_role(following.group(1).casefold(), allow_currency=True) is not None


def _fold_number_words(text: str) -> str:
    """`text` with cardinal number words written as digits, for the amount grammar only.

    The returned text is read by the amount regexes and discarded; nothing keeps its offsets. A
    numeral folds only when the word after it is one the alias tables can price ("one bitcoin",
    "twenty five eth", "half a bitcoin"), so "one of my friends" is left alone; an article folds
    to 1 under the same condition ("a bitcoin", never "a friend").
    """
    body = str(text or "")

    def _fold(match: re.Match[str]) -> str:
        if not _priceable_word_follows(body, match.end()):
            return match.group(0)
        words = re.findall(r"[a-z]+", match.group(0).casefold())
        value = _numeral_value(words)
        if value is None:
            return match.group(0)
        return f"{value:g} "

    folded = _NUMBER_WORDS_RE.sub(_fold, body)

    def _article(match: re.Match[str]) -> str:
        if _resolve_role(match.group(2).casefold(), allow_currency=True) is None:
            return match.group(0)
        return f"1 {match.group(2)}"

    return _ARTICLE_AMOUNT_RE.sub(_article, folded)

_WITH_AMOUNT_RE = re.compile(
    rf"\b(?:with|using|for|from|have|hold|own|sell|selling|spend|spending)\s+"
    rf"(\d[\d.,]*)\s*{_UNIT_LEG}([a-z][a-z0-9-]{{1,20}})\b",
    re.IGNORECASE,
)


#: "N AAA to BBB ... then to CCC" — a chained conversion whose second leg has no
#: amount of its own (it consumes the first leg's converted amount).
_FX_CHAIN_SHAPE_RE = re.compile(
    r"\b\d[\d.,]*\s+[a-z]{3}\s+to\s+[a-z]{3}\b"
    r"[^.?!]*?\b(?:and\s+then|then|after\s+that)\s+to\s+[a-z]{3}\b",
    re.IGNORECASE,
)


def fx_chain_shape(text: str) -> bool:
    """Whether the text carries a chained conversion ("...to X and then to Y")."""
    return _FX_CHAIN_SHAPE_RE.search(str(text or "")) is not None


#: The INVERSE purchasable shape: the quantity rides the TARGET ("to buy 1 btc")
#: and the payment asset is the unknown being asked about ("how much gold i
#: need to sell"). Measured live 2026-08-30: the direct parser matched nothing
#: and the turn fell to a quotes-only lane.
_BUY_AMOUNT_RE = re.compile(
    rf"\b(?:to\s+)?(?:buy|buying|get|getting|purchase|purchasing|afford)\s+"
    rf"(\d[\d.,]*)\s*{_UNIT_LEG}([a-z][a-z0-9-]{{1,20}})\b",
    re.IGNORECASE,
)


def purchasable_target_leg(text: str) -> tuple[float, str, str] | None:
    """(quantity, unit or "", target-asset text) when the clause carries "to buy N [unit of] <asset>"."""
    match = _BUY_AMOUNT_RE.search(_fold_number_words(str(text or "")))
    if not match:
        return None
    return _leg_from_match(match)


def purchasable_target_operands(text: str) -> tuple[float, str] | None:
    """(quantity, target-asset text) when the clause carries "to buy N <asset>"."""
    leg = purchasable_target_leg(text)
    return None if leg is None else (leg[0], leg[2])


#: Function words that may stand between "how much/many" (or a purchase verb) and the asset it
#: governs -- partitives, determiners and unit nouns. A CLOSED class, never an asset name: "how much
#: of gold", "how many ounces of gold" and "how much gold" name the same target and resolve it
#: through the same alias tables as every other asset; "how much can I buy" names none. Measured
#: live 2026-09-06: `how much <word>` captured "of", the target fell back to the FIRST alias in the
#: message -- silver, from a sibling price ask -- and one Bitcoin was divided by the silver price
#: under a "Gold amount" label.
_TARGET_SKIP_WORDS = (
    r"(?:of|in|into|the|a|an|some|any|more|worth|troy|ounces?|oz|grams?|kilos?|kilograms?|kg|"
    r"tons?|tonnes?|coins?|units?|tokens?|shares?|pieces?|bars?|barrels?|bbls?|gallons?|gal|litres?|liters?)"
)
_HOW_MUCH_TARGET_RE = re.compile(
    rf"\bhow\s+(?:much|many)\s+(?:{_TARGET_SKIP_WORDS}\s+)*([a-z][a-z0-9-]{{1,20}})\b",
    re.IGNORECASE,
)
#: The object of the purchase verb itself ("buy gold with 1 btc"). A digit after the verb is the
#: INVERSE shape's quantity and is not an object here.
_VERB_OBJECT_TARGET_RE = re.compile(
    rf"\b(?:buys?|buying|gets?|getting|grabs?|grabbing|affords?|purchases?|purchasing|picks?\s+up)"
    rf"\s+(?:{_TARGET_SKIP_WORDS}\s+)*([a-z][a-z0-9-]{{1,20}})\b",
    re.IGNORECASE,
)
#: "gold and silver" right after a candidate: a second candidate, never a tie
#: broken by position.
_COORDINATED_ALTERNATIVE_RE = re.compile(
    rf"\s+(?:or|and)\s+(?:{_TARGET_SKIP_WORDS}\s+)*([a-z][a-z0-9-]{{1,20}})\b", re.IGNORECASE
)

#: An ELLIPTICAL coordinated ask across a sentence boundary: "... if I sell 1 eth? also how much
#: of gold". The second ask names only its target; the payment leg is the one already stated, and
#: the additive connector says the SAME hypothetical proceeds buy this target too. Measured live
#: (owner turn, 2026-09-10): "how much of silver I can buy if I sell 1 eth? also how much of gold"
#: read as ONE ambiguous exclusive choice -- "the asset to buy is ambiguous (Silver, Gold)" -- and
#: neither conversion was computed. It is not an allocation and not a choice: each target is its
#: own derivation over the same stated proceeds, exactly as "and" coordination distributes.
#: The boundary is a real sentence end, not a comma, and the connector is additive ("also"),
#: never "or" -- an alternative stays the ambiguity `resolve_purchase_roles` reports.
_SENTENCE_ADDITIVE_ASK_RE = re.compile(
    r"[?.!;]\s*(?:[a-z0-9'-]+\s+){0,3}?(?:and\s+)?also,?\s+(?:[a-z0-9'-]+\s+){0,3}?"
    r"how\s+(?:much|many)\b",
    re.IGNORECASE,
)


def _how_much_asset(text: str) -> str:
    ask = _HOW_MUCH_TARGET_RE.search(str(text or ""))
    return ask.group(1).strip().casefold() if ask else ""


#: "…with it", "…with that" — a payment leg that refers to an amount the SAME request already
#: named, rather than stating one. Measured live: "What is 1000 EUR to RUB, how much gold can I
#: buy with it" produced no operands at all, so no derivation was planned and the lane answered
#: the amount question with a price quote.
_WITH_ANAPHORA_RE = re.compile(
    r"\b(?:with|using|for|spend|spending)\s+(?:it|that|this|the\s+same)\b",
    re.IGNORECASE,
)


#: The INVERTED payment leg: the payers come first and the purchase verb follows -- "how much gold
#: 1 bnb or 1 eth buys me", "what 2 sol gets me". Measured 2026-09-07 on new wording of the operator's
#: purchase class: no preposition, so `_WITH_AMOUNT_RE` saw no leg and nothing was derived.
_INVERTED_PAYMENT_RE = re.compile(
    rf"\b((?:\d[\d.,]*\s*{_UNIT_LEG_NC}[a-z][a-z0-9-]{{1,20}})"
    rf"(?:\s+or\s+\d[\d.,]*\s*{_UNIT_LEG_NC}[a-z][a-z0-9-]{{1,20}})*)"
    r"\s+(?:buys?|gets?|fetch(?:es)?|would\s+(?:buy|get)|will\s+(?:buy|get)|can\s+(?:buy|get))\b",
    re.IGNORECASE,
)


_INVERTED_BENEFICIARY_RE = re.compile(r"\s*(?:me|us|for\s+me|for\s+us)\b", re.IGNORECASE)


def _inverted_payment_legs(body: str) -> tuple[tuple[float, str, str], ...]:
    """The inverted payment leg -- "1 bnb or 1 eth buys me", "2 eth or 5 sol buy me" -- as
    (quantity, unit or "", payer) triples. A bare ratio is NOT a payment: "how many solana one eth buys" and
    "how many ounces of gold one btc buys" state no money and belong to the expression path
    (`test_a_ratio_with_no_money_anywhere_is_left_to_the_expression_path`, 57a613c0). What makes
    the inverted shape a purchase is a beneficiary after the verb ("buys ME") or a choice of payers
    ("1 bnb OR 1 eth"); one payer with no beneficiary is the ratio, however it is worded."""
    match = _INVERTED_PAYMENT_RE.search(body)
    if match is None:
        return ()
    beneficiary = _INVERTED_BENEFICIARY_RE.match(body, match.end()) is not None
    alternatives = bool(re.search(r"\s+or\s+\d", match.group(1), re.IGNORECASE))
    # "how many litres of crude can 2 BNB buy?": an ability modal before the payer frames a purchase
    # too. "does one eth buy" is left out on purpose -- that is the ratio's own question form.
    modal = re.search(r"\b(?:can|could|would|will)\s*$", body[: match.start()], re.IGNORECASE) is not None
    if not beneficiary and not alternatives and not modal:
        return ()
    legs: list[tuple[float, str, str]] = []
    for token in _AMOUNT_TOKEN_RE.finditer(match.group(1)):
        leg = _leg_from_match(token)
        if leg is None:
            return ()
        legs.append(leg)
    return tuple(legs)


def purchasable_amount_leg(text: str) -> tuple[float, str, str] | None:
    """(quantity, unit or "", payment-asset text) when the request carries "with N [unit of] <asset>".

    The payment asset text is raw ("10 bnb" -> "bnb"); callers resolve it through the alias
    tables before comparing it to a canonical entity name. The unit is the canonical spelling
    from `_QUANTITY_UNITS` ("1kg of silver" -> "kg") or "" when the amount is a bare count.

    An ANAPHORIC payment leg -- "with it", "with that" -- resolves to the last amount named
    BEFORE it in the same request. That is the operator's own sentence read the way they wrote
    it, and it is the same law the chained conversion already runs on: later obligations consume
    earlier results. An explicit leg always wins; an anaphora with nothing before it to bind to
    stays unresolved, so the caller reaches its honest "cannot" rather than inventing a quantity.
    """
    body = _fold_number_words(str(text or ""))
    match = _WITH_AMOUNT_RE.search(body)
    if not match:
        inverted = _inverted_payment_legs(body)
        if inverted:
            return inverted[0]
        anaphora = _WITH_ANAPHORA_RE.search(body)
        if anaphora is None:
            return None
        # The nearest amount stated before the anaphora -- never one stated after it, which
        # would be a different clause answering a question the operator had not asked yet.
        prior = [m for m in _AMOUNT_TOKEN_RE.finditer(body) if m.end() <= anaphora.start()]
        if not prior:
            return None
        match = prior[-1]
    return _leg_from_match(match)


def purchasable_amount_operands(text: str) -> tuple[float, str] | None:
    """(quantity, payment-asset text) when the request carries "with N <asset>" -- the unit-blind
    view of `purchasable_amount_leg`, kept for the callers that only ask WHETHER a leg exists."""
    leg = purchasable_amount_leg(text)
    return None if leg is None else (leg[0], leg[2])


def _amount_in_price_currency(
    result: Mapping[str, Any], price_currency: str
) -> float | None:
    """The dependency's own amount expressed in `price_currency`, or None.

    An fx leg carries two grounded figures for the same money: `converted_amount` in
    its `quote` currency, and the untouched `amount` in its `base`. At most one of
    them can be divided by a price quoted in `price_currency`; the other belongs to a
    different clause of the same turn. Returning None when neither does is what makes
    the caller raise its honest "cannot" instead of crossing currencies silently --
    a converted figure is a real number about the wrong question.

    Both figures come from the declared dependency results. Nothing here converts,
    re-derives or guesses: an amount the turn does not already hold in the price's
    currency is simply not an operand.
    """
    quote = str(result.get("quote") or "").strip().upper()
    converted = result.get("converted_amount")
    if converted is not None and converted != "":
        # No currency echo on either side leaves nothing to contradict -- the
        # long-standing behaviour, kept.
        if not price_currency or not quote or quote == price_currency:
            try:
                return float(converted)
            except (TypeError, ValueError):
                return None
        # The conversion went somewhere else. Fall through: the same leg may still
        # hold the untouched amount in the currency the price IS quoted in.
    base = str(result.get("base") or "").strip().upper()
    amount = result.get("amount")
    if amount is not None and amount != "" and price_currency and base == price_currency:
        try:
            return float(amount)
        except (TypeError, ValueError):
            return None
    return None


def _money_legs(dependencies: Mapping[str, Any]) -> list[tuple[float, str]]:
    """Every grounded money figure this turn already holds, with the currency it is IN.

    An fx leg carries two: the untouched ``amount`` in its ``base``, and ``converted_amount``
    in its ``quote``. Both are the SAME money, so either is a valid starting point for a
    bridge -- the one that matters is whichever has a live rate into the price's currency.
    """
    legs: list[tuple[float, str]] = []
    for result in dependencies.values():
        if not isinstance(result, Mapping):
            continue
        for value_key, code_key in (("amount", "base"), ("converted_amount", "quote")):
            value = result.get(value_key)
            code = str(result.get(code_key) or "").strip().upper()
            if value in (None, "") or not code:
                continue
            try:
                legs.append((float(value), code))
            except (TypeError, ValueError):
                continue
    return legs


def _bridge_amount_into_price_currency(
    ctx: NodeContext, dependencies: Mapping[str, Any], price_currency: str
) -> tuple[float, str] | None:
    """The turn's own money, converted into the price's currency by the live FX authority.

    WHY THIS EXISTS. "How much gold can I buy with it" after "1000 EUR to RUB" leaves the turn
    holding 1,000 EUR and 100,700 RUB while the gold price is quoted in USD. Neither figure may
    divide that price -- crossing currencies silently is the RC-3 fabrication this codebase
    already refuses -- so the clause raised its honest "cannot" and the reader got a price quote
    where they had asked for an amount.

    But the turn is not missing information; it is missing ONE conversion it is entirely capable
    of making, with the same authority it already used for the conversion the user asked for.
    So: fetch base -> price_currency from ``resolve_fx_quote`` (the same seam
    ``core.conductor.fresh_data_operations`` uses, under the same retrieval authorization and the
    same remote-fetch veto), and return the money together with a human-readable statement of
    the rate that produced it. Nothing here invents a number, and the bridge rate is stated in
    the expression so the assumption is visible in the answer rather than buried.

    Fails CLOSED: no authorization, no providers, a forbidden turn, an unavailable quote or a
    non-positive rate all return None and leave the caller's honest "cannot" exactly as it was.
    """
    if not price_currency:
        return None
    from core.conductor.fresh_data_operations import (
        _configured_fx_providers,
        _runtime_retrieval_allowed,
    )

    # FAIL CLOSED, INCLUDING ON A CONTEXT THAT CANNOT ANSWER. The bridge is an addition to a
    # path whose refusal is already correct, so anything that stops it from being certain --
    # an unauthorized turn, no configured provider, or a caller-supplied context that does not
    # carry the retrieval fields at all -- must leave the existing honest "cannot" untouched
    # rather than surface as an error of its own.
    try:
        if not _runtime_retrieval_allowed(ctx):
            return None
        providers = _configured_fx_providers(ctx)
    except Exception:
        return None
    if not providers:
        return None
    from core.fresh_data.fx import resolve_fx_quote

    seen: set[str] = set()
    for money, code in _money_legs(dependencies):
        if code == price_currency or code in seen:
            continue
        seen.add(code)
        try:
            quote = resolve_fx_quote(
                code, price_currency, providers=providers, timeout_s=ctx.timeout_s
            )
        except Exception:
            continue
        if quote is None or not getattr(quote, "available", False):
            continue
        try:
            rate = float(quote.rate)
        except (TypeError, ValueError, AttributeError):
            continue
        if rate <= 0:
            continue
        bridged = money * rate
        note = (
            f"{_format_number(money)} {code} x {_format_number(rate)} "
            f"{price_currency}/{code}"
        )
        return bridged, note
    return None


# --- purchasable amount: operand ROLES, bound to canonical entities and receipts -----------------


@dataclass(frozen=True)
class PurchaseRole:
    """One asset (or currency) in a purchase ask, resolved through the runtime's own tables."""

    text: str  # the word as typed, casefolded
    kind: str  # "crypto" | "commodity" | "currency"
    key: str  # canonical asset key, or ISO currency code
    entity: str  # display name

    def names(self) -> frozenset[str]:
        return frozenset(n for n in (self.text.casefold(), self.key.casefold(), self.entity.casefold()) if n)

    def as_dict(self) -> dict[str, str]:
        return {"text": self.text, "kind": self.kind, "key": self.key, "entity": self.entity}

    @classmethod
    def from_dict(cls, payload: Any) -> PurchaseRole | None:
        if not isinstance(payload, Mapping):
            return None
        try:
            return cls(
                text=str(payload.get("text") or ""),
                kind=str(payload.get("kind") or ""),
                key=str(payload.get("key") or ""),
                entity=str(payload.get("entity") or ""),
            )
        except Exception:
            return None


@dataclass(frozen=True)
class PurchaseRoles:
    """WHO is bought and WHAT pays, decided by grammar and the alias tables -- never by position.

    ``shape`` is "direct" ("how much gold can I buy with 1 btc": the quantity rides the payment) or
    "inverse" ("how much gold do I need to sell to buy 1 btc": the quantity rides the target).
    ``problem`` is the reader-facing reason the derivation must REFUSE: a target that is missing
    or ambiguous, a payment leg nothing can price. A role that cannot be resolved is a refusal,
    not a guess -- there is no "first alias that is not the payment" anywhere below.
    """

    shape: str
    target: PurchaseRole | None
    payment: PurchaseRole | None
    quantity: float | None
    problem: str = ""
    #: The unit the stated quantity is in ("kg", "troy oz", "barrel", ...), canonical spelling,
    #: or "" for a bare count. Rides the roles so the binding converts it into the asset's price
    #: unit with the working shown, and a receipt records what the user actually wrote.
    quantity_unit: str = ""

    def as_argument(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "target": self.target.as_dict() if self.target else None,
            "payment": self.payment.as_dict() if self.payment else None,
            "quantity": self.quantity,
            "problem": self.problem,
            "quantity_unit": self.quantity_unit,
        }

    @classmethod
    def from_argument(cls, payload: Any) -> PurchaseRoles | None:
        if not isinstance(payload, Mapping) or not payload.get("shape"):
            return None
        quantity = payload.get("quantity")
        try:
            quantity = float(quantity) if quantity not in (None, "") else None
        except (TypeError, ValueError):
            quantity = None
        return cls(
            shape=str(payload.get("shape")),
            target=PurchaseRole.from_dict(payload.get("target")),
            payment=PurchaseRole.from_dict(payload.get("payment")),
            quantity=quantity,
            problem=str(payload.get("problem") or ""),
            quantity_unit=str(payload.get("quantity_unit") or ""),
        )


def _currency_code(reference: Any) -> str:
    for attribute in ("code", "iso_code", "iso", "currency", "symbol"):
        value = getattr(reference, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    if isinstance(reference, Mapping):
        for attribute in ("code", "iso_code", "iso", "currency"):
            value = reference.get(attribute)
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
    text = str(reference or "").strip().upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else ""


def _resolve_role(word: str, *, allow_currency: bool) -> PurchaseRole | None:
    """The canonical asset (or, when allowed, currency) a word names, or None."""
    text = str(word or "").strip().casefold()
    if not text:
        return None
    from core.agent_runtime.live_data_plan import _resolve_price_alias

    resolved = _resolve_price_alias(text)
    if resolved is not None:
        key, kind, name = resolved
        return PurchaseRole(
            text=text,
            kind=str(kind or "asset"),
            key=str(key),
            entity=str(name or key).strip() or str(key),
        )
    if allow_currency:
        from core.currency_intent import resolve_currency_literal

        reference = resolve_currency_literal(text)
        code = _currency_code(reference) if reference is not None else ""
        if not code:
            code = text.upper() if reference is not None and re.fullmatch(r"[a-z]{3}", text) else ""
        if code:
            return PurchaseRole(text=text, kind="currency", key=code, entity=code)
    return None


def _target_candidates(text: str, payment: PurchaseRole | None) -> list[PurchaseRole]:
    """Every asset in a TARGET role: the object of "how much/many" or of the purchase verb, plus
    any alternative coordinated right after it. Resolved through the alias tables; the payment
    asset is never a target; order of appearance decides nothing."""
    body = str(text or "")
    found: dict[str, PurchaseRole] = {}

    def consider(word: str, end: int) -> None:
        role = _resolve_role(word, allow_currency=False)
        if role is None or (payment is not None and role.key == payment.key):
            return
        found.setdefault(role.key, role)
        alternative = _COORDINATED_ALTERNATIVE_RE.match(body, end)
        if alternative is not None:
            consider(alternative.group(1), alternative.end())

    for pattern in (_HOW_MUCH_TARGET_RE, _VERB_OBJECT_TARGET_RE):
        for match in pattern.finditer(body):
            consider(match.group(1), match.end())
    return list(found.values())


def resolve_purchase_roles(text: str) -> PurchaseRoles | None:
    """The roles of a purchasable-amount ask, or None when `text` is not one.

    Direct shape: the payment leg is the existing `purchasable_amount_operands` parse ("with /
    for / if I sell N <asset>", or an anaphoric "with it"); the target is the asset in the
    target role. Inverse shape: `purchasable_target_operands` ("to buy N <asset>") plus the
    asked-about asset in the "how much" phrase. Both legs resolve to canonical entities here,
    so the planner, the admission gate and the computation read ONE decision.
    """
    body = str(text or "")
    if not body.strip() or not _PURCHASE_VERB_RE.search(body):
        return None
    direct = purchasable_amount_leg(body)
    if direct is not None:
        quantity, unit, payment_text = direct
        payment = _resolve_role(payment_text, allow_currency=True)
        if payment is None:
            return PurchaseRoles(
                "direct",
                None,
                None,
                quantity,
                problem=(
                    f"the payment leg '{payment_text}' is not an asset or currency this runtime "
                    "can price, so the purchasable amount cannot be computed"
                ),
                quantity_unit=unit,
            )
        unit_problem = _payment_unit_problem(unit, payment)
        if unit_problem:
            return PurchaseRoles(
                "direct", None, payment, quantity, problem=unit_problem, quantity_unit=unit
            )
        candidates = _target_candidates(body, payment)
        if not candidates:
            return PurchaseRoles(
                "direct",
                None,
                payment,
                quantity,
                problem=_missing_target_problem(body, payment),
                quantity_unit=unit,
            )
        if len(candidates) > 1:
            names = ", ".join(role.entity for role in candidates)
            return PurchaseRoles(
                "direct",
                None,
                payment,
                quantity,
                problem=(
                    f"the asset to buy is ambiguous ({names}); name one and the amount can be "
                    "computed"
                ),
                quantity_unit=unit,
            )
        return PurchaseRoles("direct", candidates[0], payment, quantity, quantity_unit=unit)
    inverse = purchasable_target_leg(body)
    if inverse is not None:
        quantity, unit, target_text = inverse
        target = _resolve_role(target_text, allow_currency=False)
        if target is None:
            return PurchaseRoles(
                "inverse",
                None,
                None,
                quantity,
                problem=(
                    f"'{target_text}' is not an asset this runtime can price, so the amount to "
                    "sell cannot be computed"
                ),
                quantity_unit=unit,
            )
        unit_problem = _payment_unit_problem(unit, target)
        if unit_problem:
            return PurchaseRoles(
                "inverse", target, None, quantity, problem=unit_problem, quantity_unit=unit
            )
        payment_word = _how_much_asset(body)
        payment = _resolve_role(payment_word, allow_currency=False) if payment_word else None
        if payment is None:
            return PurchaseRoles(
                "inverse",
                target,
                None,
                quantity,
                problem="the asset to sell is not named, so the amount to sell cannot be computed",
                quantity_unit=unit,
            )
        if payment.key == target.key:
            return PurchaseRoles(
                "inverse",
                target,
                payment,
                quantity,
                problem=(
                    f"{target.entity} is both the asset bought and the asset sold, so there is "
                    "nothing to compute"
                ),
                quantity_unit=unit,
            )
        return PurchaseRoles("inverse", target, payment, quantity, quantity_unit=unit)
    # No payment leg in this clause at all: the money is a DEPENDENCY the turn already holds --
    # the fx leg of "1000 EUR to RUB, then how much gold can I buy" -- and the binding takes it
    # from that leg by its currency. The clause still has to name what is bought.
    candidates = _target_candidates(body, None)
    if not candidates:
        return None
    if len(candidates) > 1:
        names = ", ".join(role.entity for role in candidates)
        return PurchaseRoles(
            "direct",
            None,
            None,
            None,
            problem=f"the asset to buy is ambiguous ({names}); name one and the amount can be computed",
        )
    return PurchaseRoles("direct", candidates[0], None, None)


#: "... if I have 1 btc or 1 eth": a second payer stated right after the first, with its own amount.
# --- ALLOCATION: one stated sum split into equal parts, each part buying a named thing -------------
#
# Measured on the owner's native app (2026-09-10 23:48, build c647b707): "I have 1000eur, i want to
# split it equally in 4 parts and buy ADA, TESLA shares, gold and chinese yuan. How much of each i
# will get?" was cut into four fragments at the demand boundary -- the sum, "and buy ADA", the three
# remaining names, and the question -- and no fragment held the allocation: ADA was quoted alone,
# the sum went to a model, the names were "noted", and the question was adjudicated for entity
# ambiguity. The same shape one turn earlier: "15 base coins, sell half to buy BTC and other half
# to buy bronze" quoted BTC and dropped everything else.
#
# An allocation is ONE request whose outputs depend on one another: a payment (the sum and what it
# is in), a number of equal parts, and the things each part buys. The roles are read here by
# grammar and the same alias tables every purchase role resolves through; nothing is priced by
# position, and a target the tables cannot resolve is a STATED non-fulfilment (a currency the fx
# authority knows, an asset the price tables know, a ticker nobody can resolve -- each named).

_SPLIT_VERB_RE = re.compile(
    r"\b(?:split|splits|splitting|divide|divides|dividing|break|breaking|spread|spreading|"
    r"allocate|allocates|allocating|distribute|distributes|distributing|share|sharing|put|putting)\b",
    re.IGNORECASE,
)
_EQUAL_SHARE_RE = re.compile(
    r"\b(?:equally|evenly|equal(?:ly)?|in\s+equal\s+(?:parts|shares|portions|amounts))\b",
    re.IGNORECASE,
)
_PARTS_COUNT_RE = re.compile(
    r"\b(?:in|into)\s+(\d{1,2}|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:equal\s+)?(?:parts?|ways?|portions?|pieces?|shares?|chunks?|buckets?|halves)\b",
    re.IGNORECASE,
)
_HALF_AND_OTHER_HALF_RE = re.compile(r"\bhalf\b.*\bother\s+half\b", re.IGNORECASE | re.DOTALL)
_AMONG_RE = re.compile(r"\b(?:among|amongst|between|across)\b", re.IGNORECASE)
#: The objects of a purchase verb: everything after it up to the clause end, the NEXT allocation
#: leg ("and other half ..."), the question ("how much ..."), or a payment leg ("with ...").
_PURCHASE_OBJECTS_RE = re.compile(
    r"\b(?:to\s+)?(?:buy|buying|purchase|purchasing|get|getting|grab|grabbing|pick\s+up|into(?!\s+\d))\s+"
    r"(?P<objects>.+?)"
    r"(?=\s*(?:[.?!;]|\band\s+(?:the\s+)?(?:other|another|rest|remaining)\b|\bthen\b|"
    r"\bhow\s+(?:much|many)\b|\bwith\b|\busing\b|\bfor\s+the\b|$))",
    re.IGNORECASE | re.DOTALL,
)
_OBJECT_SPLIT_RE = re.compile(r"\s*(?:,|;|&|/|\band\b|\bor\b|\bplus\b)\s*", re.IGNORECASE)
_OBJECT_QUALIFIER_RE = re.compile(
    r"\b(?:shares?|stocks?|coins?|tokens?|equity|equities|some|a|an|the|of|in|into|more|"
    r"other|another|rest|remaining|half|quarter|part|parts|each|my|our)\b",
    re.IGNORECASE,
)
_OBJECT_PRONOUNS = frozenset({"it", "them", "each", "that", "this", "those", "these", "one", "ones"})
_TICKER_DOLLAR_RE = re.compile(r"\$\s?([A-Za-z]{2,6})\b")  # `$ BASE`: the ingress-normalized form
_TICKER_COIN_RE = re.compile(r"\b([A-Za-z]{2,6})\s+(?:coins?|tokens?)\b", re.IGNORECASE)
_PARTS_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
#: The allocation recognizer's OWN words, for the shared typo budget (`core.typo_fold`).
_ALLOCATION_VOCABULARY = (
    "split", "splitting", "divide", "dividing", "spread", "allocate", "distribute", "equally",
    "evenly", "equal", "parts", "part", "ways", "portions", "pieces", "shares", "chunks", "half",
    "halves", "other", "among", "between", "across", "into",
)


@dataclass(frozen=True)
class AllocationTarget:
    """One thing a part of the sum buys, as written and as the runtime's tables resolve it."""

    text: str  # the object as typed, qualifiers stripped, casefolded ("tesla", "chinese yuan")
    role: PurchaseRole | None  # crypto / commodity / currency, or None when nothing resolves it
    ticker_shaped: bool  # written as a ticker ("$BASE", "base coins"): unresolved means ASK, not "not an asset"


@dataclass(frozen=True)
class AllocationRoles:
    """A sum split into equal parts, each part buying one target."""

    payment: PurchaseRole | None  # what the sum is in (a currency or a priced asset), or None
    payment_text: str  # the payer as typed ("eur", "base")
    quantity: float | None  # the whole sum
    quantity_unit: str  # canonical unit of the sum ("kg") or "" for a count or money
    parts: int
    targets: tuple[AllocationTarget, ...]
    problem: str = ""  # a stated reason no share can be computed (a parts/targets mismatch)
    payment_ticker_shaped: bool = False
    valuation_currency: str = ""

    @property
    def share(self) -> float | None:
        if self.quantity is None or self.parts <= 0:
            return None
        return float(self.quantity) / float(self.parts)


def _ticker_shaped(word: str, original: str) -> bool:
    """Whether the user wrote `word` as a ticker: `$WORD`, or `WORD coin(s)/token(s)`."""
    folded = str(word or "").casefold()
    if not folded:
        return False
    if any(m.group(1).casefold() == folded for m in _TICKER_DOLLAR_RE.finditer(original)):
        return True
    return any(m.group(1).casefold() == folded for m in _TICKER_COIN_RE.finditer(original))


def _resolve_target_phrase(phrase: str) -> PurchaseRole | None:
    """The role a multi-word object names: the whole phrase, then its last word, then its first."""
    words = [w for w in str(phrase or "").split() if w]
    if not words:
        return None
    for candidate in (" ".join(words), words[-1], words[0]):
        role = _resolve_role(candidate, allow_currency=True)
        if role is not None:
            return role
    return None


#: The targets a split names WITHOUT a purchase verb of their own: "split it evenly between btc
#: and gold", "spread it across ada, gold and silver".
_AMONG_OBJECTS_RE = re.compile(
    r"\b(?:among|amongst|between|across)\s+(?P<objects>.+?)"
    r"(?=\s*(?:[.?!;,]\s*(?:how|what|and\s+how)|[.?!;]|\bthen\b|\bhow\s+(?:much|many)\b|\bwith\b|\busing\b|$))",
    re.IGNORECASE | re.DOTALL,
)


def _purchase_objects(text: str) -> list[str]:
    """Every object of a purchase verb in the text, as typed (qualifiers stripped, casefolded),
    in order, once each. "buy -ADA, TESLA shares, gold and chinese yuan" -> ada, tesla, gold,
    chinese yuan; "to buy BTC and other half to buy bronze" -> btc, bronze; "split it evenly
    between btc and gold" -> btc, gold."""
    from core.input_normalizer import normalize_user_text

    body = normalize_user_text(str(text or "")).normalized_text
    found: list[str] = []
    matches = list(_PURCHASE_OBJECTS_RE.finditer(body))
    matches.extend(_AMONG_OBJECTS_RE.finditer(body))
    for match in matches:
        for item in _OBJECT_SPLIT_RE.split(match.group("objects")):
            cleaned = _OBJECT_QUALIFIER_RE.sub(" ", item)
            cleaned = re.sub(r"[^A-Za-z0-9$ ]+", " ", cleaned)
            cleaned = " ".join(cleaned.replace("$", "").split()).casefold()
            if not cleaned or cleaned in _OBJECT_PRONOUNS or re.search(r"\d", cleaned):
                continue
            if len(cleaned) > 40:
                continue
            if cleaned not in found:
                found.append(cleaned)
    return found


def allocation_purchase_roles(text: str) -> AllocationRoles | None:
    """The allocation this text states, or None when it is not one.

    Three things must be present, each read by its own grammar: a purchase verb with at least two
    objects, a split cue (an explicit count of parts, "half ... other half", or "equally/evenly"
    with a split verb or "among/between"), and a stated payment leg (the sum: "I have 1000 EUR",
    "15 base coins"). A text missing any of them is left to the other purchase readings.
    """
    body = str(text or "")
    if not body.strip() or not _PURCHASE_VERB_RE.search(body):
        return None
    # The split vocabulary is folded through the runtime's ONE typo budget (`core.typo_fold`), the
    # way every fixed-word recognizer folds its own words: the owner typed "eaqully" and "parths"
    # (2026-09-10 23:48), and a recognizer that only reads clean spellings never saw the split.
    from core.typo_fold import fold_near_miss_tokens

    folded = fold_near_miss_tokens(_fold_number_words(body).casefold(), _ALLOCATION_VOCABULARY)
    explicit = _PARTS_COUNT_RE.search(folded)
    half = _HALF_AND_OTHER_HALF_RE.search(folded) is not None
    equal = _EQUAL_SHARE_RE.search(folded) is not None and (
        _SPLIT_VERB_RE.search(folded) is not None or _AMONG_RE.search(folded) is not None
    )
    if explicit is None and not half and not equal:
        return None
    leg = purchasable_amount_leg(folded)
    if leg is None:
        return None
    quantity, unit, payment_text = leg
    objects = [o for o in _purchase_objects(body) if o != str(payment_text).casefold()]
    if len(objects) < 2:
        return None
    payment = _resolve_role(payment_text, allow_currency=True)
    if explicit is not None:
        token = explicit.group(1).casefold()
        parts = int(token) if token.isdigit() else _PARTS_WORDS.get(token, 0)
    elif half:
        parts = 2
    else:
        parts = len(objects)
    problem = ""
    if parts <= 0:
        problem = "the number of parts could not be read, so no share can be computed"
    elif parts != len(objects):
        problem = (
            f"you asked for {parts} equal parts but named {len(objects)} things to buy "
            f"({', '.join(objects)}); say which of them get a part and each share can be computed"
        )
    targets = tuple(
        AllocationTarget(
            text=obj, role=_resolve_target_phrase(obj), ticker_shaped=_ticker_shaped(obj.split()[-1], body)
        )
        for obj in objects
    )
    valuation_currency = ""
    conversion = re.search(r"\bconvert\b[^?.;]{0,40}?\b(?:to|into)\s+([A-Za-z]+)", body, re.I)
    if conversion:
        currency_role = _resolve_role(conversion.group(1), allow_currency=True)
        if currency_role is not None and currency_role.kind == "currency":
            valuation_currency = currency_role.key
    return AllocationRoles(
        payment=payment,
        payment_text=str(payment_text).casefold(),
        quantity=quantity,
        quantity_unit=unit,
        parts=parts,
        targets=targets,
        problem=problem,
        payment_ticker_shaped=_ticker_shaped(str(payment_text), body),
        valuation_currency=valuation_currency,
    )


def _asset_valuation_request(text: str) -> tuple[float, PurchaseRole, str] | None:
    """A crypto quantity valued in a named currency, using the shared asset readers."""
    match = re.fullmatch(r"how much is ([0-9]+(?:\.[0-9]+)?) ([A-Za-z][A-Za-z0-9 -]*) in ([A-Za-z]+)[?]?", text.strip(), re.I)
    if not match:
        return None
    asset = _resolve_role(match.group(2), allow_currency=True)
    currency = _resolve_role(match.group(3), allow_currency=True)
    if asset is None or asset.kind != "crypto" or currency is None or currency.kind != "currency":
        return None
    return float(match.group(1)), asset, currency.key


def _asset_valuation_run(valuation: tuple[float, PurchaseRole, str], ctx: NodeContext) -> dict[str, Any]:
    quantity, asset, currency = valuation
    price_dependency = _price_dependency_for(asset, ctx.dependency_results)
    if price_dependency is None:
        raise ValueError(f"No observed price for {asset.entity}; its value was not computed")
    price_id, observed = price_dependency
    price = float(observed.get("price") or 0)
    quoted_currency = str(observed.get("currency") or "").upper()
    rate, rate_id = 1.0, ""
    if not quoted_currency:
        raise ValueError("The price does not name its currency")
    if quoted_currency != currency:
        rates = [(key, value) for key, value in ctx.dependency_results.items()
                 if str(value.get("base") or "").upper() == quoted_currency
                 and str(value.get("quote") or "").upper() == currency
                 and value.get("status") == "available"]
        if len(rates) != 1:
            raise ValueError(f"A single observed {quoted_currency}/{currency} rate is required")
        rate_id, fx = rates[0]
        rate = float(fx.get("rate") or 0)
    if not all(math.isfinite(n) and n > 0 for n in (quantity, price, rate)):
        raise ValueError("The amount, price and exchange rate must be positive finite numbers")
    value = quantity * price * rate
    step = {"label": f"{quantity:g} {asset.entity} value", "expression": f"{quantity:g} × {price:g} × {rate:g}",
            "value": value, "unit": currency,
            "valuation_evidence": {"price_dependency": price_id, "fx_dependency": rate_id,
                                   "quantity": quantity, "price": price, "rate": rate,
                                   "from_currency": quoted_currency, "to_currency": currency}}
    return {"steps": [step], "values": {"step_1": value}, "cannot_determine": [], "method": "asset_valuation"}


def unresolved_ticker_question(word: str) -> str:
    """The ONE identifying question an unresolved ticker earns -- the asset's full name -- so the
    rest of the task can proceed the moment it is answered. Never a guess at which asset it is."""
    shown = str(word or "").strip().upper()
    return (
        f"'{shown}' is a ticker this runtime cannot resolve to one asset (it is not on the price "
        f"tables or the coin index it can quote); tell me the asset's full name as listed on its "
        "exchange and this part can be priced"
    )


_ALTERNATIVE_PAYMENT_RE = re.compile(
    rf"\s+or\s+(\d[\d.,]*)\s*{_UNIT_LEG}([a-z][a-z0-9-]{{1,20}})\b", re.IGNORECASE
)


def asks_for_a_purchasable_amount(request_text: str) -> bool:
    """A clause that asks HOW MUCH of an asset a sum buys -- direct or inverted grammar, or with the
    money left to an earlier leg. A derivation, whatever operation a model named for it, and never a
    bare quote: the planner promotes such a clause and the coverage grain does not let the quote
    family own it (measured 2026-09-07: "How much gold can I buy?" owned as a quote let the mixed
    lane end the turn with a price and no amount)."""
    text = str(request_text or "")
    if not text.strip() or not _PURCHASE_VERB_RE.search(text) or not _clause_has(text, _QUANTITY_ASK_CUES):
        return False
    body = _fold_number_words(text)
    try:
        roles = resolve_purchase_roles(text)
    except Exception:
        return False
    if roles is None or roles.target is None or roles.shape != "direct":
        return False
    if roles.payment is None and _WITH_ANAPHORA_RE.search(body) is None:
        # A payment-less ask is a purchase only when the target is the ONLY priced asset named:
        # "how much gold can I buy?" states no money and is answered with a stated cannot. "how
        # many grams of silver one ounce of gold buys" names a second priced asset -- that is the
        # ratio the expression path serves, whatever unit word sits between the number and it.
        # Counted through the same alias tables the roles resolve with (the price recognizer's
        # market gate reads "grams of silver" as no asset at all).
        target_text = str(roles.target.text or "").casefold()
        for token in re.findall(r"[a-z][a-z0-9-]{2,20}", body.casefold()):
            if token == target_text or token in _RATIO_SCAN_STOP_WORDS:
                continue
            if _resolve_role(token, allow_currency=False) is not None:
                return False
    return True


#: Words a purchase clause carries that are never a second asset, kept out of the ratio scan so a
#: stray alias collision cannot turn a plain ask into a ratio.
_RATIO_SCAN_STOP_WORDS = frozenset(
    {"how", "much", "many", "can", "could", "would", "will", "buy", "buys", "buying", "get", "gets",
     "purchase", "afford", "with", "for", "the", "and", "you", "please", "now", "today", "worth"}
)

#: Pronouns and function words "how much <word>" captures when the target comes later in the
#: clause ("how much I can buy of oil"). Never reported as an asset nothing can price.
_NOT_A_TARGET_WORDS = frozenset(
    {"i", "we", "you", "it", "one", "more", "less", "that", "this", "they", "he", "she", "of", "do",
     "does", "did", "is", "are", "was", "would", "could", "should", "can", "to", "money", "cash"}
)


def _missing_target_problem(body: str, payment: PurchaseRole | None) -> str:
    """The reason a direct purchase names no target this runtime can price.

    Two different facts wear one sentence otherwise: "the asset to buy is not named" is TRUE for
    "how much can I buy with 1 eth" and FALSE for "how much copper can I buy with 10 bnb" -- there
    the asset IS named and the runtime cannot quote it. Measured served (A4, 2026-09-10): the
    copper ask was declined as unnamed. The word after "how much/many" is reported when it is a
    content word that resolves to nothing, and the generic reason is kept for a pronoun or a
    clause that names nothing at all.
    """
    asked = _how_much_asset(body)
    if (
        asked
        and asked not in _NOT_A_TARGET_WORDS
        and asked not in _RATIO_SCAN_STOP_WORDS
        and not asked.isdigit()
        and (payment is None or asked not in payment.names())
        and _resolve_role(asked, allow_currency=False) is None
    ):
        return (
            f"'{asked}' is not an asset this runtime can price, so the purchasable amount "
            "cannot be computed"
        )
    return "the asset to buy is not named, so the purchasable amount cannot be computed"


def alternative_payment_operands(text: str) -> tuple[tuple[float, str, str], ...]:
    """Every (quantity, unit or "", payer text) of a payment leg that lists payers with "or" -- "if
    i have 1 btc or 1 eth" -- in the order written, or () when the leg names one payer (or none)."""
    body = _fold_number_words(str(text or ""))
    match = _WITH_AMOUNT_RE.search(body)
    if match is None:
        inverted = _inverted_payment_legs(body)
        return inverted if len(inverted) >= 2 else ()
    legs: list[tuple[float, str, str]] = []
    first = _leg_from_match(match)
    if first is None:
        return ()
    legs.append(first)
    position = match.end()
    while True:
        following = _ALTERNATIVE_PAYMENT_RE.match(body, position)
        if following is None:
            break
        leg = _leg_from_match(following)
        if leg is None:
            return ()
        legs.append(leg)
        position = following.end()
    return tuple(legs) if len(legs) >= 2 else ()


def alternative_purchase_roles(text: str) -> tuple[PurchaseRoles, ...]:
    """One resolved role set per PAYER when the payment leg offers several with "or" -- "how much
    gold can I buy if I have 1 btc or 1 eth" -- each over the same target, or () otherwise. Measured
    live 2026-09-07: the leg was quoted for both payers and derived for the first only; the second
    payer's amount vanished without a row. "or" between payers asks for each figure, exactly as
    "and" between targets does in `distributed_purchase_roles`."""
    body = str(text or "")
    if not body.strip() or not _PURCHASE_VERB_RE.search(body):
        return ()
    legs = alternative_payment_operands(body)
    if len(legs) < 2:
        return ()
    base = resolve_purchase_roles(body)
    if base is None or base.problem or base.target is None or base.shape != "direct":
        return ()
    roles: list[PurchaseRoles] = []
    for quantity, unit, payer_text in legs:
        if quantity <= 0:
            return ()
        payer = _resolve_role(payer_text, allow_currency=True)
        if payer is not None and payer.key == base.target.key:
            return ()
        if payer is None:
            # A payer nothing here can price still gets ITS derivation -- one that refuses with the
            # reason -- so the other payers are derived and this one is named, never dropped.
            # Measured 2026-09-07 (new wording, isolated alias table without USDC): the whole
            # alternative branch bailed and the turn fell back to one derivation for the first payer.
            payer = PurchaseRole(text=payer_text, kind="unpriced", key="", entity=payer_text)
            roles.append(
                PurchaseRoles(
                    "direct",
                    base.target,
                    payer,
                    quantity,
                    problem=(
                        f"the payment leg '{payer_text}' is not an asset or currency this runtime "
                        "can price, so the purchasable amount for it cannot be computed"
                    ),
                    quantity_unit=unit,
                )
            )
            continue
        unit_problem = _payment_unit_problem(unit, payer)
        roles.append(
            PurchaseRoles(
                "direct", base.target, payer, quantity, problem=unit_problem, quantity_unit=unit
            )
        )
    return tuple(roles)


def distributed_purchase_roles(text: str) -> tuple[PurchaseRoles, ...]:
    """One resolved role set per target when ONE payment leg coordinates several targets --
    "how much gold and how much silver can I buy with one bitcoin", or the elliptical
    "... if I sell 1 eth? also how much of gold" -- or () otherwise.

    "and" distributes the shared predicate over its objects, and so does an additive
    sentence ("? also how much of gold"): each object is its own derivation over the same
    stated proceeds -- the same hypothetical applied twice, never an allocation of one
    payment across the targets. "or" asks for one of them and stays the ambiguity
    `resolve_purchase_roles` reports; a text with a single target, no payment leg, or a
    second ask that states its OWN divergent payment (a different sum to sell) is not this
    shape. Measured on the built candidate 60a91da3 (2026-09-07): the coordinated ask
    resolved to "the asset to buy is ambiguous (Gold, Silver)" once it was read as one
    demand; measured on the owner's live turn (2026-09-10): the elliptical ask did the
    same and the turn shipped quotes with no amounts.
    """
    body = str(text or "")
    if not body.strip() or not _PURCHASE_VERB_RE.search(body):
        return ()
    direct = purchasable_amount_leg(body)
    if direct is None:
        return ()
    quantity, unit, payment_text = direct
    payment = _resolve_role(payment_text, allow_currency=True)
    if payment is None or _payment_unit_problem(unit, payment):
        return ()
    candidates = _target_candidates(body, payment)
    if len(candidates) < 2:
        return ()
    folded = body.casefold()
    positions = sorted(
        ((folded.find(role.text), role) for role in candidates if folded.find(role.text) >= 0),
        key=lambda pair: pair[0],
    )
    if len(positions) != len(candidates):
        return ()
    elliptical = False
    for (start, role), (next_start, _next_role) in itertools.pairwise(positions):
        between = folded[start + len(role.text) : next_start]
        if re.search(r"\bor\b", between):
            return ()
        if re.search(r"\band\b", between):
            continue
        if _SENTENCE_ADDITIVE_ASK_RE.search(between):
            # The next target heads its own sentence-sized ask ("? also how much of gold"),
            # so it is a SECOND derivation over the same proceeds -- but only while every
            # payment amount the text states is that same leg. A second ask with its own
            # divergent sum ("also, if I sell 2 eth, how much gold") is a different purchase
            # this one-payment fan-out must not fold into the first.
            elliptical = True
            continue
        return ()
    if elliptical:
        legs = {
            _leg_from_match(match)
            for match in _WITH_AMOUNT_RE.finditer(_fold_number_words(folded))
        }
        if len(legs) > 1:
            return ()
    return tuple(
        PurchaseRoles("direct", role, payment, quantity, quantity_unit=unit)
        for _start, role in positions
    )


@dataclass(frozen=True)
class BoundOperand:
    """One operand as it was actually taken from a declared dependency's result."""

    role: str
    dep_id: str
    asset_key: str
    kind: str
    entity: str
    price: float | None
    currency: str
    source: str = ""
    source_url: str = ""
    retrieved_at: str = ""
    dep_entity: str = ""  # the dependency result's own entity echo, when it carries one
    source_span: tuple[int, int] | None = None
    fact_label: str = ""
    source_unit: str = ""
    source_value: float | None = None
    price_unit: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "dep_id": self.dep_id,
            "asset_key": self.asset_key,
            "kind": self.kind,
            "entity": self.entity,
            "dep_entity": self.dep_entity,
            "price": self.price,
            "currency": self.currency,
            "source": self.source,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            **({"source_span": list(self.source_span), "fact_label": self.fact_label,
                "source_unit": self.source_unit, "source_value": self.source_value,
                "price_unit": self.price_unit}
               if self.source_span is not None else {}),
        }


@dataclass(frozen=True)
class OperandBinding:
    """A purchasable-amount derivation with every operand named: which dependency, which entity,
    which price, which currency. The label is DERIVED from the divisor's entity, never guessed
    from the clause, and `as_step()` carries the binding on the step so the receipt shows it and
    `step_binding_problem` can re-check it before anything is published."""

    shape: str
    target: BoundOperand
    payment: BoundOperand
    quantity: float  # in the priced asset's own price unit (troy ounces, barrels, coins)
    amount: float  # the numerator, in the divisor's currency
    value: float
    currency: str
    label: str
    unit: str
    expression: str
    bridge_note: str = ""
    #: What the user WROTE for the quantity, when it carried a unit the binding converted:
    #: "1 kg" became 32.1507 troy ounces. Receipt fields; the arithmetic reads `quantity`.
    stated_quantity: float | None = None
    stated_unit: str = ""

    @property
    def divisor_role(self) -> str:
        return "target" if self.shape == "direct" else "payment"

    def divisor(self) -> BoundOperand:
        return self.target if self.shape == "direct" else self.payment

    def as_step(self) -> dict[str, Any]:
        payment = self.payment.as_dict()
        payment["quantity"] = self.quantity if self.shape == "direct" else None
        return {
            "label": self.label,
            "expression": self.expression,
            "value": self.value,
            "unit": self.unit,
            "operands": {
                "shape": self.shape,
                "divisor_role": self.divisor_role,
                "target": self.target.as_dict(),
                "payment": payment,
                "quantity": self.quantity,
                "stated_quantity": self.stated_quantity,
                "stated_unit": self.stated_unit,
                "amount": self.amount,
                "currency": self.currency,
                "bridge_note": self.bridge_note,
            },
        }

    def as_tuple(self) -> tuple[str, float, str, str]:
        return (self.label, self.value, self.unit, self.expression)


def _price_dependency_for(
    role: PurchaseRole, dependencies: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any]] | None:
    """The declared dependency whose result IS this role's live price -- by canonical key on the
    node id, or by the result's own entity echo. Never the first result that happens to carry a
    price."""
    names = role.names()
    exact: tuple[str, Mapping[str, Any]] | None = None
    echoed: tuple[str, Mapping[str, Any]] | None = None
    marked: tuple[str, Mapping[str, Any]] | None = None
    for dep_id, result in dict(dependencies or {}).items():
        if not isinstance(result, Mapping) or result.get("price") is None:
            continue
        marker = str(dep_id).casefold()
        tail = marker.rsplit(":", 1)[-1]
        entity = str(result.get("entity") or "").strip().casefold()
        asset_key = str(result.get("asset_key") or "").strip().casefold()
        if exact is None and (tail in names or asset_key in names):
            exact = (str(dep_id), result)
        elif echoed is None and entity and entity in names:
            echoed = (str(dep_id), result)
        elif marked is None and any(len(n) > 2 and n in marker for n in names):
            marked = (str(dep_id), result)
    return exact or echoed or marked


def _bound(role_name: str, dep_id: str, result: Mapping[str, Any], role: PurchaseRole) -> BoundOperand:
    try:
        price = float(result["price"])
    except (TypeError, ValueError, KeyError):
        price = None
    return BoundOperand(
        role=role_name,
        dep_id=dep_id,
        asset_key=role.key,
        kind=role.kind,
        entity=role.entity,
        price=price,
        currency=str(result.get("currency") or "").strip().upper(),
        source=str(result.get("source") or result.get("source_label") or "").strip(),
        source_url=str(result.get("source_url") or "").strip(),
        retrieved_at=str(result.get("retrieved_at") or "").strip(),
        dep_entity=str(result.get("entity") or "").strip(),
        source_span=tuple(result["source_span"]) if not dep_id and result.get("source") == "user_supplied" and result.get("source_span") else None,
        fact_label=str(result.get("fact_label") or ""),
        source_unit=str(result.get("source_unit") or ""),
        source_value=result.get("source_value"),
        price_unit=str(result.get("price_unit") or ""),
    )


def _price_operand_for(role: PurchaseRole, dependencies: Mapping[str, Any], ctx: NodeContext,
                       clause: str) -> tuple[str, Mapping[str, Any]] | None:
    from core.conductor.supplied_prices import supplied_prices

    binding = supplied_prices(getattr(ctx, "shared_context", None), clause).get(role.key)
    if binding is not None:
        if binding["problem"]:
            raise ValueError(binding["problem"])
        return "", binding
    return _price_dependency_for(role, dependencies)


def _label_for(role: PurchaseRole) -> tuple[str, str]:
    """The step label and result unit for an amount of `role`'s asset, by CANONICAL KEY.

    The unit table is keyed by asset key, so "Brent crude" (key `brent_crude`) reads its barrels
    and a display name that differs from its key can never lose its unit.
    """
    entity = str(role.entity or role.key or "")
    unit = _COMMODITY_UNITS.get(str(role.key or "").casefold(), "")
    label = f"{entity} amount ({unit})" if unit else f"{entity} amount"
    return label, unit


def _money_operand(
    roles: PurchaseRoles,
    target: BoundOperand,
    dependencies: Mapping[str, Any],
    ctx: NodeContext,
) -> tuple[float, str, BoundOperand]:
    """(amount in the price's currency, bridge note, payment operand) for a MONEY payment.

    Three sources, tried in the order of how directly they state the money, none positional:
    the request's own figure when its currency is the price's; the declared fx leg that holds
    THIS money (matched by currency) in the price's currency; one live bridge through the FX
    authority with the rate stated. Two different amounts that could both pay are an ambiguity,
    and an ambiguity refuses.
    """
    price_currency = target.currency
    code = roles.payment.key if roles.payment is not None else ""
    quantity = roles.quantity
    if code and quantity is not None and (not price_currency or code == price_currency):
        amount = float(quantity)
        if amount <= 0:
            raise ValueError("the amount to spend is not a positive number")
        payment = BoundOperand(
            role="payment", dep_id="", asset_key=code, kind="currency", entity=code, price=None,
            currency=code,
        )
        return amount, "", payment
    hits: list[tuple[str, float, str]] = []
    for dep_id, result in dict(dependencies or {}).items():
        if str(dep_id) == target.dep_id or not isinstance(result, Mapping):
            continue
        legs = {
            str(result.get("base") or "").strip().upper(),
            str(result.get("quote") or "").strip().upper(),
        } - {""}
        if not legs or (code and code not in legs):
            continue
        got = _amount_in_price_currency(result, price_currency)
        if got is not None:
            hits.append((str(dep_id), got, price_currency or code))
    if len({round(amount, 6) for _, amount, _ in hits}) > 1:
        raise ValueError(
            f"more than one amount in {price_currency or 'the price currency'} could pay for "
            "this; say which one"
        )
    if hits:
        dep_id, amount, currency = hits[0]
        payment = BoundOperand(
            role="payment", dep_id=dep_id, asset_key=currency, kind="currency", entity=currency,
            price=None, currency=currency,
        )
        return amount, "", payment
    others = {k: v for k, v in dict(dependencies or {}).items() if str(k) != target.dep_id}
    bridged = _bridge_amount_into_price_currency(ctx, others, price_currency)
    if bridged is not None:
        amount, note = bridged
        payment = BoundOperand(
            role="payment", dep_id="", asset_key=code or price_currency, kind="currency",
            entity=code or price_currency, price=None, currency=code or price_currency,
        )
        return amount, note, payment
    raise ValueError(
        f"this needs the amount expressed in {price_currency or 'the price currency'} "
        "to divide by the live price"
    )


def _bind_direct(
    roles: PurchaseRoles, dependencies: Mapping[str, Any], ctx: NodeContext, clause: str = ""
) -> OperandBinding | None:
    assert roles.target is not None
    if roles.payment is None:
        # No payment leg in the clause: this is the purchasable-amount shape ONLY when a declared
        # dependency actually holds money (an fx leg). "how many solana one eth buys" names no
        # money at all -- it is a ratio over two prices, and it belongs to the general expression
        # path over derived facts, which this binding must not pre-empt with a refusal.
        others = {k: v for k, v in dict(dependencies or {}).items()}
        if not _money_legs(others):
            return None
    hit = _price_operand_for(roles.target, dependencies, ctx, clause)
    if hit is None:
        raise ValueError(
            f"the current price of {roles.target.entity} was not available, so the purchasable "
            "amount cannot be computed"
        )
    target = _bound("target", hit[0], hit[1], roles.target)
    if target.price is None or target.price <= 0:
        raise ValueError("the live price returned is not a positive number")
    price_currency = target.currency

    if roles.payment is None or roles.payment.kind == "currency":
        amount, bridge_note, payment = _money_operand(roles, target, dependencies, ctx)
        amount = round(amount, 2)
        numerator = bridge_note or _format_number(amount)
        expression = f"{numerator} / {_format_number(target.price)}"
        quantity = float(roles.quantity) if roles.quantity is not None else amount
    else:
        try:
            stated_quantity = float(roles.quantity if roles.quantity is not None else 0.0)
        except (TypeError, ValueError):
            stated_quantity = 0.0
        if stated_quantity <= 0:
            raise ValueError("the amount to spend is not a positive number")
        # DIMENSIONAL ARITHMETIC before the price is touched: a payment stated in kilograms of a
        # metal priced per troy ounce is multiplied by that price only once it IS troy ounces,
        # and the working says so ("1 kg = 32.1507 troy ounces"). A unit that does not measure
        # the payment asset refuses here with its reason (`_quantity_in_price_unit`).
        quantity, _payment_unit, conversion = _quantity_in_price_unit(
            stated_quantity, roles.quantity_unit, roles.payment
        )
        stated_unit = roles.quantity_unit if conversion else ""
        pay_hit = _price_operand_for(roles.payment, dependencies, ctx, clause)
        if pay_hit is None:
            raise ValueError(
                f"the current price of {roles.payment.entity} was not available, so the "
                "purchasable amount cannot be computed"
            )
        payment = _bound("payment", pay_hit[0], pay_hit[1], roles.payment)
        if payment.price is None or payment.price <= 0:
            raise ValueError("the payment asset's live price is not a positive number")
        if payment.currency and price_currency and payment.currency != price_currency:
            raise ValueError(
                f"prices are quoted in different currencies ({price_currency} vs "
                f"{payment.currency}); refusing to cross silently"
            )
        bridge_note = ""
        amount = quantity * payment.price
        expression = (
            f"{conversion}; " if conversion else ""
        ) + (
            f"{_format_number(quantity)} x {_format_number(payment.price)} / "
            f"{_format_number(target.price)}"
        )
        label, unit = _label_for(roles.target)
        return OperandBinding(
            shape="direct",
            target=target,
            payment=payment,
            quantity=quantity,
            amount=amount,
            value=amount / target.price,
            currency=price_currency or payment.currency,
            label=label,
            unit=unit,
            expression=expression,
            bridge_note=bridge_note,
            stated_quantity=stated_quantity if conversion else None,
            stated_unit=stated_unit,
        )
    label, unit = _label_for(roles.target)
    return OperandBinding(
        shape="direct",
        target=target,
        payment=payment,
        quantity=quantity,
        amount=amount,
        value=amount / target.price,
        currency=price_currency or payment.currency,
        label=label,
        unit=unit,
        expression=expression,
        bridge_note=bridge_note,
    )


def _bind_inverse(roles: PurchaseRoles, dependencies: Mapping[str, Any], ctx: NodeContext,
                  clause: str) -> OperandBinding:
    assert roles.target is not None and roles.payment is not None
    pay_hit = _price_operand_for(roles.payment, dependencies, ctx, clause)
    if pay_hit is None:
        raise ValueError(
            f"the current price of {roles.payment.text} was not available, so the amount to sell "
            "cannot be computed"
        )
    target_hit = _price_operand_for(roles.target, dependencies, ctx, clause)
    if target_hit is None:
        raise ValueError(
            f"the current price of {roles.target.text} was not available, so the amount to sell "
            "cannot be computed"
        )
    payment = _bound("payment", pay_hit[0], pay_hit[1], roles.payment)
    target = _bound("target", target_hit[0], target_hit[1], roles.target)
    if payment.currency and target.currency and payment.currency != target.currency:
        raise ValueError(
            f"prices are quoted in different currencies ({target.currency} vs "
            f"{payment.currency}); refusing to cross silently"
        )
    if payment.price is None or payment.price <= 0:
        raise ValueError("the payment asset's live price is not a positive number")
    if target.price is None or target.price <= 0:
        raise ValueError("the live price returned is not a positive number")
    try:
        quantity = float(roles.quantity if roles.quantity is not None else 0.0)
    except (TypeError, ValueError):
        quantity = 0.0
    if quantity <= 0:
        raise ValueError("the amount to buy is not a positive number")
    # The quantity rides the TARGET here ("to buy 2 oz of gold"): convert it into the target's
    # price unit before the price is applied, exactly as the direct shape converts its payment.
    stated_quantity = quantity
    quantity, _target_unit, conversion = _quantity_in_price_unit(
        stated_quantity, roles.quantity_unit, roles.target
    )
    amount = quantity * target.price
    label, unit = _label_for(roles.payment)
    return OperandBinding(
        shape="inverse",
        target=target,
        payment=payment,
        quantity=quantity,
        amount=amount,
        value=amount / payment.price,
        currency=payment.currency or target.currency,
        label=label,
        unit=unit,
        expression=(f"{conversion}; " if conversion else "")
        + (
            f"{_format_number(quantity)} x {_format_number(target.price)} / "
            f"{_format_number(payment.price)}"
        ),
        stated_quantity=stated_quantity if conversion else None,
        stated_unit=roles.quantity_unit if conversion else "",
    )


def purchasable_amount_binding(
    clause: str, ctx: NodeContext, roles: PurchaseRoles | Mapping[str, Any] | None = None,
    *, price_scope: str | None = None,
) -> OperandBinding | None:
    """The purchasable-amount derivation, every operand bound to a declared dependency by ROLE.

    Returns None when the clause is not this shape. Raises ValueError with the reader-facing
    reason when it IS this shape and the derivation must refuse: a target that is missing or
    ambiguous, a leg whose price is absent, currencies that would have to cross silently.
    Nothing here takes "the first dependency with a price" or "the first alias in the message".
    """
    if isinstance(roles, Mapping):
        roles = PurchaseRoles.from_argument(roles)
    if roles is None:
        roles = resolve_purchase_roles(str(clause or ""))
    if roles is None:
        return None
    if roles.problem:
        raise ValueError(roles.problem)
    dependencies = dict(ctx.dependency_results or {})
    if roles.shape == "inverse":
        return _bind_inverse(roles, dependencies, ctx, price_scope if price_scope is not None else clause)
    return _bind_direct(roles, dependencies, ctx, price_scope if price_scope is not None else clause)


def step_binding_problem(
    step: Mapping[str, Any], *, depends_on: Sequence[str] | None = None
) -> str | None:
    """Why a bound derivation step must NOT be published, or None when its binding holds.

    Defence in depth behind `purchasable_amount_binding`: the label must name the asset whose
    price is the divisor, the value must follow from the operands, the operands must share one
    currency, and every operand must come from a dependency the node declared. A step without
    `operands` is a model-planned expression and is not judged here.
    """
    operands = step.get("operands") if isinstance(step, Mapping) else None
    if not isinstance(operands, Mapping):
        return None
    divisor_role = str(operands.get("divisor_role") or "target")
    divisor = operands.get(divisor_role)
    if not isinstance(divisor, Mapping):
        return "this figure's operands are incomplete, so it was not published"
    entity = str(divisor.get("entity") or "").strip()
    label = str(step.get("label") or "").strip()
    if not entity or not label.casefold().startswith(entity.casefold()):
        return (
            "this figure's label does not name the asset whose price it divides by"
            f"{f' ({entity})' if entity else ''}, so it was not published"
        )
    try:
        price = float(divisor.get("price"))
        amount = float(operands.get("amount"))
        value = float(step.get("value"))
    except (TypeError, ValueError):
        return "this figure's operands are incomplete, so it was not published"
    if price <= 0 or abs(value - amount / price) > 1e-6 * max(1.0, abs(value)):
        return "this figure does not follow from its own operands, so it was not published"
    currencies = {
        str(operands.get("currency") or "").strip().upper(),
        str(divisor.get("currency") or "").strip().upper(),
    } - {""}
    if len(currencies) > 1:
        return "this figure mixes currencies, so it was not published"
    for role_name in ("target", "payment"):
        operand = operands.get(role_name)
        if not isinstance(operand, Mapping):
            continue
        dep_id = str(operand.get("dep_id") or "")
        if not dep_id or str(operand.get("kind") or "") == "currency":
            continue
        names = {
            str(operand.get("asset_key") or "").casefold(),
            str(operand.get("entity") or "").casefold(),
        } - {""}
        # Canonical node ids carry the asset key as their last segment; a dependency whose id
        # names a different asset is the live defect's exact shape (a "Gold" divisor taken from
        # the silver node). Ad-hoc ids without a segment are not judged by their spelling.
        if ":" in dep_id and dep_id.rsplit(":", 1)[-1].casefold() not in names:
            return (
                f"this figure's {role_name} price was taken from a result for a different asset, "
                "so it was not published"
            )
        echo = str(operand.get("dep_entity") or "").casefold()
        if echo and echo not in names:
            return (
                f"this figure's {role_name} price was taken from a result for a different asset, "
                "so it was not published"
            )
    if depends_on is not None:
        declared = {str(d) for d in depends_on}
        for role_name in ("target", "payment"):
            dep_id = str((operands.get(role_name) or {}).get("dep_id") or "")
            if dep_id and dep_id not in declared:
                return (
                    "this figure was computed from a result the step never declared as a "
                    "dependency, so it was not published"
                )
    return None


def _purchasable_amount_fallback(
    clause: str, ctx: NodeContext
) -> tuple[str, float, str, str] | None:
    """(label, value, unit, expression) of the role-bound derivation; see
    `purchasable_amount_binding`, which is the authority this wraps."""
    binding = purchasable_amount_binding(clause, ctx)
    return None if binding is None else binding.as_tuple()

_CHAIN_END_RE = re.compile(r"\bto\s+([a-z]{3})\b", re.IGNORECASE)


def _fx_chain_amount_fallback(
    clause: str, ctx: NodeContext
) -> tuple[str, float, str, str] | None:
    """Deterministic "N AAA to BBB and then to CCC": converted amount / live price.

    The chain's last leg exists only as a computation over grounded dependency
    results — the bridged fiat amount and the destination asset's live price.
    A model supplies no number on this path. Returns None when the clause is not
    a chained-conversion ask; raises ValueError naming the missing operand when
    it is one but an operand is absent.
    """
    text = str(clause or "")
    if not fx_chain_shape(text):
        return None
    converted_value: float | None = None
    price_value: float | None = None
    price_currency = ""
    converted_candidates: list[tuple[str, float]] = []
    for _dep_id, result in dict(ctx.dependency_results or {}).items():
        if not isinstance(result, Mapping):
            continue
        if price_value is None and result.get("price") is not None:
            try:
                price_value = float(result["price"])
                price_currency = str(result.get("currency") or "").strip().upper()
            except (TypeError, ValueError):
                price_value = None
            continue
        if result.get("converted_amount"):
            try:
                converted_candidates.append(
                    (
                        str(result.get("quote") or "").strip().upper(),
                        float(result["converted_amount"]),
                    )
                )
            except (TypeError, ValueError):
                continue
    # The derivation divides by a price stated in the bridge's currency: the
    # converted leg whose quote currency matches the price wins. When the price
    # names a currency and NO leg is quoted in it there is no operand -- the old
    # "else the first stands" rule divided a GBP amount by a USD price and served
    # the quotient as an answer. A candidate with no currency echo at all is not a
    # contradiction, so the no-echo turn keeps its long-standing behaviour.
    matching = [c for quote, c in converted_candidates if price_currency and quote == price_currency]
    if matching:
        converted_value = matching[-1]
    elif not price_currency:
        converted_value = converted_candidates[0][1] if converted_candidates else None
    elif converted_candidates:
        raise ValueError(
            "the chained conversion's intermediate amount is not stated in "
            f"{price_currency}, the currency the live price is quoted in, so the "
            "final amount cannot be computed without crossing currencies silently"
        )
    if price_value is None:
        raise ValueError(
            "the destination asset's live price was not available, so the chained "
            "amount cannot be computed"
        )
    if converted_value is None:
        raise ValueError(
            "the chained conversion's intermediate amount was not available, so "
            "the final amount cannot be computed"
        )
    end_code = ""
    for match in _CHAIN_END_RE.finditer(text):
        end_code = match.group(1)
    entity = "the asset"
    if end_code:
        from core.agent_runtime.live_data_plan import _resolve_price_alias

        resolved = _resolve_price_alias(end_code.lower())
        if resolved is not None:
            entity = str(resolved[2]).strip() or entity
    value = converted_value / price_value
    expression = f"{_format_number(converted_value)} / {_format_number(price_value)}"
    return (f"{entity} amount", value, "", expression)


def _readable_expression(expression: str, fact_values: Mapping[str, Any]) -> str:
    """`fact_1 / fact_2` written as `8,500 / 1,240`, so the working reads as the user's own numbers.

    Longest label first: replacing `fact_1` before `fact_11` would leave `1` glued to the digits it
    was substituted in front of, and a working that reads `85001` is worse than one that reads
    `fact_11`.
    """
    rendered = str(expression or "")
    for label in sorted(dict(fact_values or {}), key=len, reverse=True):
        try:
            value = float(fact_values[label])
        except (TypeError, ValueError):
            continue
        rendered = rendered.replace(label, _format_number(value))
    return rendered


def _quantitative_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    """Renders from typed result fields only (`steps`/`cannot_determine`).

    `arguments["clause"]` is control metadata — the registry excludes it from the
    execution key (`_DISPLAY_ONLY_ARGUMENTS`) — and it may never cross into the served
    bytes: measured live 2026-08-30, the whole three-sentence request was printed
    between the quotes and the amount line of a successful answer. A node that FAILS
    still names its clause in the composer's unserved row; that is disclosure, and it
    is the only place request text may appear.
    """
    fact_values = {**(node.arguments.get("fact_values") or {}),
                   **(result.get("expression_bindings") or {})}
    lines: list[str] = []
    rows: list[list[str]] = []
    steps = list(result.get("steps") or [])
    #: `step_2` means nothing to a reader; the label it refers to does. Built before the loop so a
    #: step can only ever name one that was already computed.
    step_labels = {
        str(item.get("step_id") or f"step_{index}"): str(item.get("label") or "")
        for index, item in enumerate(steps, 1)
    }
    depends_on = getattr(node, "depends_on", None)
    for step in steps:
        problem = step_binding_problem(step, depends_on=depends_on)
        if problem is not None:
            # Defence in depth: a bound derivation whose binding no longer holds is disclosed,
            # never printed as a figure -- correct quote receipts vouch for the quotes, not for
            # an arithmetic step bound to the wrong one.
            lines.append(f"  not determined: {problem}")
            continue
        expression = _readable_expression(str(step.get("expression") or ""), fact_values)
        expression = re.sub(
            r"\bstep_\d+\b",
            lambda match: f"({step_labels[match[0]]})" if step_labels.get(match[0]) else match[0],
            expression,
        )
        unit = str(step.get("unit") or "").strip()
        tail = f" {unit}" if unit else ""
        value = _format_number(float(step.get("value") or 0.0))
        rows.append([str(step.get("label")), expression, f"{value}{tail}"])
    if len(rows) > 1:
        from core.presentation.model import Paragraph, RenderDocument, Span, Table
        from core.presentation.render_markdown import MarkdownRenderer

        renderer = MarkdownRenderer()
        table_rows = [[label, renderer.render(RenderDocument(blocks=[
            Paragraph(runs=[Span(expression, style="code")]),
        ])), value] for label, expression, value in rows]
        lines.insert(0, renderer.render(RenderDocument(blocks=[Table(
            headers=["Result", "Calculation", "Value"], rows=table_rows,
        )])))
    elif rows:
        label, expression, value = rows[0]
        lines.insert(0, f"  {label}: {expression} = {value}")
    for item in result.get("cannot_determine") or []:
        lines.append(f"  not determined: {item}")
    if any(_step_involves_a_commodity(step) for step in steps):
        # A metal or crude conversion at a benchmark quote (spot or front-month futures per troy
        # ounce / per barrel) is a hypothetical exchange of value, not a physical purchase: a
        # retail bar, coin or fuel delivery carries premiums, spread and delivery on top of it.
        # Stated once per answer, deterministically, so the figure is never read as a shop price.
        lines.append(_BENCHMARK_CONVERSION_NOTE)
    return "\n".join(lines).strip()


#: The fixed qualification a commodity conversion carries (see `_quantitative_render`).
_BENCHMARK_CONVERSION_NOTE = (
    "  (benchmark conversion at the quoted prices, before dealer premiums, spread and "
    "delivery; not a retail purchase quote)"
)


def _step_involves_a_commodity(step: Mapping[str, Any]) -> bool:
    operands = step.get("operands") if isinstance(step, Mapping) else None
    if not isinstance(operands, Mapping):
        return False
    return any(
        str((operands.get(role_name) or {}).get("kind") or "") == "commodity"
        for role_name in ("target", "payment")
        if isinstance(operands.get(role_name), Mapping)
    )


# --- general: what the message does not establish -----------------------------------------------


def _missing_information_expand(request_text: str, shared: Any = None) -> list[dict[str, Any]]:
    context = _shared_or_empty(shared)
    if not _clause_has(request_text, _MISSING_INFO_CUES):
        return []
    if not context.missing and not context.facts:
        return []
    return [{"entity": "missing_information", "clause": " ".join(str(request_text or "").split())}]


def _missing_information_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Deterministic. Consults no model, because the answer is already computed.

    What is missing was established by `extract_shared_context` from the message's own shape, and
    by whichever earlier nodes reported an operand they could not ground. Asking a model to
    enumerate assumptions instead would be asking the component most likely to invent one.
    """
    context = _shared_or_empty(ctx.shared_context)
    items = [item.statement for item in context.missing]
    kinds = [item.kind for item in context.missing]
    for result in ctx.dependency_results.values():
        for reported in result.get("cannot_determine") or []:
            text = " ".join(str(reported).split())
            if text and text not in items:
                items.append(text)
                kinds.append("node_reported")
    if not items:
        items = ["nothing — every value this answer needs is stated in the message"]
        kinds = ["none"]
    return {"items": items, "kinds": kinds}


def _missing_information_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    items = [str(item) for item in (result.get("items") or [])]
    header = "Cannot be determined from what you gave, and was not guessed at:"
    return "\n".join([header] + [f"  - {item}" for item in items])


# --- general: explanation grounded in the message's own numbers ---------------------------------


_EXPLANATION_SYSTEM_PROMPT = (
    "You answer ONE part of a longer message. Use the supplied facts where relevant and stable "
    "conceptual knowledge where the clause asks for an explanation.\n"
    "\n"
    "Rules:\n"
    "- Answer only the part you were given. Do not answer the other parts.\n"
    "- Do not imply that you performed a physical or digital action. Explain only.\n"
    "- Do not invent situation-specific facts that are absent from the briefing.\n"
    "- Follow the numeric-authority rule in the briefing.\n"
    "- Anything listed as ambiguous or not determinable stays that way: say so plainly instead of\n"
    "  choosing the most likely reading.\n"
    "- If an acronym or term has several meanings and the clause gives no domain, ask which meaning\n"
    "  the user intends instead of choosing one.\n"
    "- Plain prose. No JSON, no markdown fence, no headings."
)

_CLOSED_NUMERIC_AUTHORITY = "closed_evidence"
_OPEN_KNOWLEDGE_AUTHORITY = "open_knowledge"


_FRESH_OR_PLACE_KNOWLEDGE_RE = re.compile(
    r"\bcurrent\b(?=\s+\w)|\b(?:currently|latest|live|now|today|tonight|tomorrow|yesterday)\b"
    r"|\bthis\s+(?:hour|week|month|year)\b"
    r"|\b(?:near\s+me|nearby|opening\s+hours|directions\s+to)\b",
    re.IGNORECASE,
)
_META_RETRIEVAL_LIMIT_RE = re.compile(
    r"\b(?:explain|say|tell)\b[^.!?\n]{0,120}\bwhy\b[^.!?\n]{0,120}"
    r"\b(?:live\s+)?(?:web\s+)?search(?:\s+tool)?\b[^.!?\n]{0,80}"
    r"\b(?:fail|cannot|can't|will\s+not|won't|inaccurate)\b",
    re.IGNORECASE,
)


def _stable_knowledge_capability_accepts(clause: TurnClause) -> bool:
    text = str(clause.request_text or "")
    if not _asks_for_explanation(text):
        return False
    # This is a question ABOUT the epistemic limit of live search, not a request FOR live data.
    if _META_RETRIEVAL_LIMIT_RE.search(text):
        return True
    return not bool(_FRESH_OR_PLACE_KNOWLEDGE_RE.search(text))


#: Cues that name a COMPUTATION over supplied figures: closed numeric authority on their own.
_COMPUTATION_CUES = (
    "calculate", "compute", "work out", "figure out", "estimate", "total", "difference",
    "multiply", "divide", "subtract", "purchasing power", "what would it cost",
)
#: A clause that points back at something the message or an earlier node supplied.
_SUPPLIED_VALUE_REFERENCE_RE = re.compile(
    r"\b(?:that|this|these|those|it|its|the\s+result|the\s+above|the\s+previous|each|both|"
    r"the\s+(?:first|second|third|last)\s+one|per\s+(?:unit|item|person|day|month|year))\b",
    re.IGNORECASE,
)


def _explanation_numeric_authority(request_text: str, shared: Any = None) -> str:
    """Close numeric claims only when this clause actually invokes supplied numeric evidence.

    Closed: the clause carries a number, names a computation, references a supplied value
    ("how much is THAT in USD", "how many of THOSE"), or names an unresolved ambiguity token.
    Open: a stable-knowledge count with no referent -- "How many continents are there?",
    "How many sides does a hexagon have?". MEASURED on the final-build pack (98f6ed63, turn 3):
    every "how many" was closed, the briefing told the author to state a number only if it was
    a message fact, and the continents clause was served as "not determinable from the provided
    facts" -- a stable fact withheld by the frame, not by the model. The plain lane answered
    the same turn in full on the frozen build only because its planner had timed out.
    """

    text = str(request_text or "")
    context = _shared_or_empty(shared)
    if _REPLY_NUMBER_RE.search(text) or _clause_has(text, _COMPUTATION_CUES):
        return _CLOSED_NUMERIC_AUTHORITY
    if _clause_has(text, _QUANTITY_ASK_CUES) and _SUPPLIED_VALUE_REFERENCE_RE.search(text):
        return _CLOSED_NUMERIC_AUTHORITY
    lowered = text.casefold()
    for ambiguity in context.unresolved_ambiguities:
        token = ambiguity.token.casefold()
        if token and re.search(rf"(?<!\w){re.escape(token)}(?!\w)", lowered):
            return _CLOSED_NUMERIC_AUTHORITY
    return _OPEN_KNOWLEDGE_AUTHORITY


def named_knowledge_operation() -> Any:
    """The one operation that declared itself the plan's KNOW-family server, or None.

    Lookup is by the declared flag, not by name, so the planner holds no operation-name
    literal for this rule (the same law `test_the_route_metadata_comes_from_the_proposal`
    pins for lane ids). Registration REJECTS a second flag-bearer, so two is a state this
    lookup cannot silently resolve: if it ever observes one anyway (a registry populated
    outside `register_operation`), it raises rather than picking a winner — choosing the
    first of conflicting authorities is not fail-closed.
    """
    from core.conductor.registry import known_operations

    bearers = [spec for spec in known_operations() if spec.serves_named_know_clauses]
    if len(bearers) > 1:
        raise RuntimeError(
            "multiple operations declare serves_named_know_clauses: "
            + ", ".join(sorted(spec.name for spec in bearers))
        )
    return bearers[0] if bearers else None


def stable_knowledge_freshness_refuses(text: str) -> bool:
    """Whether `text` carries a freshness cue the open-knowledge authority never licenses.

    The same closed structural vocabulary `_stable_knowledge_capability_accepts` uses at
    plan time ("current", "latest", "now", ...), asked of arbitrary text at publication
    time. A current-bound statement is current-information shaped whichever node rendered
    it, so this refuses even inside a knowledge-classified render.
    """

    value = str(text or "")
    if _META_RETRIEVAL_LIMIT_RE.search(value):
        return False
    return bool(_FRESH_OR_PLACE_KNOWLEDGE_RE.search(value))


def knowledge_clause_admits_named(clause: TurnClause) -> bool:
    """Named-path admission for the knowledge family: the freshness half of the capability gate.

    The shape half (`_asks_for_explanation`) is deliberately ABSENT here — that is the F41
    repair. A plain KNOW question ("In what year did the Berlin Wall fall?") carries no
    explanation cue, so the cue cannot be the discriminator when the planner has already
    involved this family; the runtime's typed KNOW verdict is, and it is checked by the
    caller. What this function keeps, unchanged and load-bearing, is the freshness guard:
    a current/latest-bound question must keep dying here so the grounding law holds (the
    turn-19 "latest stable version of Python" refusal is correct and measured).
    """
    text = str(clause.request_text or "")
    if _META_RETRIEVAL_LIMIT_RE.search(text):
        return True
    return not bool(_FRESH_OR_PLACE_KNOWLEDGE_RE.search(text))


def _plain_know_question(request_text: str) -> bool:
    """One typed KNOW clause that passes the freshness guard — no word cues of our own.

    The discriminator is TurnIR's kind verdict over the clause text, the same authority the
    capability layer already trusts for `accepted_kinds`. Anything else — multiple clauses,
    a non-KNOW kind, an unparseable text, a freshness cue — returns False, so this can only
    WIDEN admission for the question shape nothing else in the plan could serve.
    """
    try:
        from core.turn_ir import parse_turn_ir

        clauses = parse_turn_ir(str(request_text or "")).clauses
    except Exception:
        return False
    if len(clauses) != 1 or clauses[0].kind is not ClauseKind.KNOW:
        return False
    return knowledge_clause_admits_named(clauses[0])


def _named_explanation_expand(request_text: str, shared: Any = None) -> list[dict[str, Any]]:
    """Expand an explicitly named, semantically admitted knowledge explanation.

    This is intentionally broader than the general fallback below.  A planner that names
    ``factual_explanation`` for an independent KNOW clause may execute it; an unrelated unclaimed
    clause is never offered this resolver and therefore cannot be swallowed as generic prose.
    A plain KNOW question (no explanation cue) is admissible since F41: the clause died
    unresolved in every mixed turn because no family recognised the shape.
    """

    if not (_asks_for_explanation(request_text) or _plain_know_question(request_text)):
        return []
    return [
        {
            "entity": _slug(request_text, limit=32) or "explanation",
            "clause": " ".join(str(request_text or "").split()),
            "numeric_authority": _explanation_numeric_authority(request_text, shared),
        }
    ]


def _explanation_expand(request_text: str, shared: Any = None) -> list[dict[str, Any]]:
    """Serve an explanatory clause -- but only where answering it without the message would go wrong.

    The gate is on the CONTEXT, not only on the clause, and it is deliberately the narrowest
    condition that still covers the defect: the message must have left something AMBIGUOUS or
    UNDETERMINED. That is precisely when an explanation written from the clause alone states
    something the message never established -- "kr" as Swedish, "$" as US, a rate nobody quoted.

    Anything looser makes this a catch-all. An earlier draft admitted any clause in a message with
    two numbers in it, and "What is 137 x 29? Explain the calculation briefly." promptly stopped
    failing closed: `tests/test_conductor_multi_intent.py` and `tests/test_conductor_plan_and_graph.py`
    both caught it. A message with nothing ambiguous in it has no shared story to lose, and its
    explanatory clauses are left to the lanes that already answer them.
    """
    context = _shared_or_empty(shared)
    if not context.has_numbers:
        # The defect class is a message that states FIGURES in one sentence and asks about them in
        # others. A message with no figures has no such context to lose, and claiming its
        # explanatory clauses here would trade a working lane for a new one on no evidence.
        return []
    if not context.unresolved_ambiguities and not context.missing:
        return []
    if not _asks_for_explanation(request_text):
        return []
    return [
        {
            "entity": _slug(request_text, limit=32) or "explanation",
            "clause": " ".join(str(request_text or "").split()),
            "numeric_authority": _explanation_numeric_authority(request_text, shared),
        }
    ]


def _grounded_values(context: Any, derived: Mapping[str, Any]) -> list[float]:
    values = [fact.value for fact in context.facts]
    for value in dict(derived or {}).values():
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _ungrounded_numbers(text: str, allowed: list[float]) -> list[str]:
    """Numbers in `text` that trace to nothing the runtime established."""
    out: list[str] = []
    for token in _REPLY_NUMBER_RE.findall(str(text or "")):
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if value == int(value) and 0 <= value <= _MAX_UNGROUNDED_ENUMERATION:
            continue
        if any(abs(value - candidate) < 0.005 for candidate in allowed):
            continue
        if any(abs(value - round(candidate, 2)) < 0.005 for candidate in allowed):
            continue
        out.append(token)
    return out


def _explanation_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    from core.conductor.shared_context import render_briefing

    context = _shared_or_empty(ctx.shared_context)
    if ctx.run_generation is None:
        raise ValueError("no generation seam available for this explanation")

    clause = str(node.arguments.get("clause") or node.request_text or "")
    briefing = render_briefing(context, derived_facts=ctx.derived_facts, clause=clause)
    numeric_authority = str(
        node.arguments.get("numeric_authority") or _OPEN_KNOWLEDGE_AUTHORITY
    )
    if numeric_authority == _CLOSED_NUMERIC_AUTHORITY:
        briefing += (
            "\n\nNumeric-authority rule: closed evidence. State a number only if it is listed "
            "as a message fact or already-computed value."
        )
    else:
        briefing += (
            "\n\nNumeric-authority rule: open stable knowledge. Ordinary factual numbers may be "
            "stated; do not invent situation-specific measurements, tool results, or calculations."
        )
    text = " ".join(str(ctx.run_generation(_EXPLANATION_SYSTEM_PROMPT, briefing) or "").split())
    if not text:
        raise ValueError("the explanation came back empty")

    invented = _ungrounded_numbers(text, _grounded_values(context, ctx.derived_facts))
    if numeric_authority == _CLOSED_NUMERIC_AUTHORITY and invented:
        # Fail the node rather than ship the sentence. A figure nobody established is the failure
        # mode this whole slice exists to remove, and one that arrives inside fluent prose is
        # harder to catch than one that arrives as a missing section.
        raise ValueError(
            "the explanation stated numbers this message never established: " + ", ".join(invented)
        )
    return {"text": text, "clause": clause, "numeric_authority": numeric_authority}


def _explanation_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    return str(result.get("text") or "").strip()


# --- general: unavailable effects and independent safe content -------------------------------


def _unavailable_action_accepts(clause: TurnClause) -> bool:
    from core.action_availability import unavailable_action_report
    from core.conductor.capabilities import decide_operation_compatibility
    from core.conductor.registry import expand_named_clause, known_operations

    if unavailable_action_report(clause.request_text) is None:
        return False
    # The model can explicitly name ``unavailable_action`` even when a real effect operation is
    # also in the catalog. Do not let that proposal shadow a typed integration: domain admission
    # plus successful argument expansion is stronger evidence than this fallback's absence report.
    for spec in known_operations():
        capability = spec.capability
        if capability is None or capability.effect not in {
            OperationEffect.SIDE_EFFECT,
            OperationEffect.LIVE_OBSERVATION,
        }:
            continue
        if not decide_operation_compatibility(capability, clause).allowed:
            continue
        try:
            if expand_named_clause(spec, clause.request_text, None):
                return False
        except Exception:
            continue
    return True


def _unavailable_action_expand(request_text: str) -> list[dict[str, Any]]:
    from core.action_availability import unavailable_action_report

    report = unavailable_action_report(request_text)
    if report is None:
        return []
    return [
        {
            "entity": _slug(request_text, limit=32) or "unavailable_action",
            **report.to_dict(),
        }
    ]


def _unavailable_action_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    # No executor is read and no tool intent exists on this operation.  The result is the runtime's
    # capability status, not a failed attempt to make the effect happen.
    del ctx
    return {
        "request": str(node.arguments.get("request") or node.request_text or "").strip(),
        "kind": str(node.arguments.get("kind") or "physical_embodiment"),
        "available": False,
        "attempted": False,
        "reason": str(node.arguments.get("reason") or "no matching effect capability is available"),
        "next_step": str(node.arguments.get("next_step") or ""),
    }


def _unavailable_action_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    reason = str(result.get("reason") or "no matching effect capability is available").strip()
    next_step = str(result.get("next_step") or "").strip()
    text = f"I cannot perform that action: {reason}. I did not attempt it."
    if next_step:
        text += f" {next_step}"
    return text


_FIRE_TRIANGLE_RE = re.compile(
    r"\b(?:fire|combustion)\s+triangle\b"
    r"|\btriangle\s+of\s+(?:fire(?!\s+(?:departments?|stations?|fighters?))|combustion)\b",
    re.IGNORECASE,
)
_FIRE_PROCEDURE_RE = re.compile(
    r"\b(?:accelerants?|incendiary|arson|molotov|burn\s+down|set\s+(?:it|them|\w+)\s+on\s+fire)\b"
    r"|\bignite\b[^.!?;\n]{0,50}\b(?:gasoline|petrol|fuel|vapou?r)\b"
    r"|\b(?:how\s+to|instructions?|steps?|procedure|method|best\s+way)\b"
    r"[^.!?;\n]{0,100}\b(?:start|make|build|ignite|spread|intensify|feed)\b[^.!?;\n]{0,40}\bfire\b"
    r"|\b(?:exact|required|minimum)\s+(?:ignition\s+)?temperature\b",
    re.IGNORECASE,
)
_AVIATION_BRACE_RE = re.compile(
    r"\bbrace(?:d|s|ing)?\b[^.!?;\n]{0,50}\b(?:impact|crash|landing)\b"
    r"|\b(?:impact|crash|landing)\b[^.!?;\n]{0,50}\bbrace(?:d|s|ing)?\b",
    re.IGNORECASE,
)
_WING_AERODYNAMICS_RE = re.compile(
    r"\b(?:aerodynamics?|lift|airflow|pressure)\b[^.!?;\n]{0,100}\b(?:airplane|aircraft|paper\s+airplane)?\s*wings?\b"
    r"|\b(?:airplane|aircraft|paper\s+airplane)\s+wings?\b[^.!?;\n]{0,100}"
    r"\b(?:work|fly|lift|aerodynamics?|airflow|pressure)\b",
    re.IGNORECASE,
)
_TIRE_FRICTION_RE = re.compile(
    r"\b(?:friction|traction|grip)\b[^.!?;\n]{0,100}\b(?:tires?|tyres?|wheels?)\b"
    r"|\b(?:tires?|tyres?|wheels?)\b[^.!?;\n]{0,100}\b(?:friction|traction|grip)\b",
    re.IGNORECASE,
)
_ALTERNATING_CURRENT_RE = re.compile(
    r"\balternating\s+current\b|\bcurrent\s*\(\s*ac\s*\)|\bac\s+(?:electricity|current)\b",
    re.IGNORECASE,
)
_THERMAL_PRINTER_RE = re.compile(
    r"\b(?:direct[- ]thermal|thermal(?:[- ]transfer|[- ]printing|\s+printers?|\s+printheads?))\b"
    r"[^.!?;\n]{0,100}"
    r"\b(?:work|mechanism|print|image|heat|paper|ribbon)\b"
    r"|\b(?:how|mechanism|explain|describe)\b[^.!?;\n]{0,100}"
    r"\b(?:direct[- ]thermal|thermal(?:[- ]transfer)?\s+(?:printers?|printing|printheads?))\b",
    re.IGNORECASE,
)
_THERMAL_PRINTER_PROCEDURE_RE = re.compile(
    r"\b(?:drivers?|firmware|spooler|cups|usb|bluetooth|network|ip\s+address|permissions?)\b"
    r"|\b(?:install|download|update|repair|fix|replace|open|disassemble|clean|calibrate|configure|"
    r"connect|pair|reset|flash|solder|bypass)\b[^.!?;\n]{0,100}\b(?:printer|printhead|thermal\s+head)\b"
    r"|\b(?:printer|printhead|thermal\s+head)\b[^.!?;\n]{0,100}"
    r"\b(?:install|repair|fix|replace|open|disassemble|clean|calibrate|configure|connect|reset)\b",
    re.IGNORECASE,
)
_KERNEL_PANIC_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)|explain|describe|define|tell\s+me\s+what)\b"
    r"[^.!?;\n]{0,80}\bkernel\s+panic\b"
    r"|\bkernel\s+panic\b[^.!?;\n]{0,80}\b(?:mean|definition|operating\s+system)\b",
    re.IGNORECASE,
)
_REVIEWED_PROCEDURE_RE = re.compile(
    r"\b(?:how\s+to|instructions?|steps?|procedure|method)\b[^.!?;\n]{0,100}"
    r"\b(?:bypass|disable|sabotage|wire|rewire|connect|touch|short|drift|burnout|crash)\b"
    r"|\b(?:live\s+wires?|mains\s+voltage|bypass\s+(?:a\s+)?breaker|disable\s+(?:the\s+)?grid|"
    r"sabotage\s+(?:a\s+)?tire|cut\s+(?:the\s+)?brakes?)\b",
    re.IGNORECASE,
)
_REVIEWED_SAFE_KNOWLEDGE: dict[str, dict[str, str]] = {
    "fire_triangle": {
        "entity": "fire triangle",
        "text": (
            "The fire triangle consists of heat, fuel, and oxygen. Combustion continues only while "
            "all three are present; removing any one breaks the triangle and stops combustion."
        ),
        "detail_level": "stable_nonprocedural_safety_fact",
    },
    "aviation_brace": {
        "entity": "aviation brace guidance",
        "text": (
            "If this is a real in-flight emergency, follow the cabin crew and the safety card for "
            "your specific seat immediately. Brace when instructed: keep the seat belt low and "
            "tight, adopt the shown brace position, protect your head and neck, and remain braced "
            "until the aircraft stops moving."
        ),
        "detail_level": "stable_emergency_safety_guidance",
    },
    "wing_aerodynamics": {
        "entity": "airplane wing aerodynamics",
        "text": (
            "A wing's shape and angle redirect airflow and create a pressure distribution around "
            "the wing; the resulting aerodynamic force includes lift. Structural bracing keeps a "
            "full-size wing stiff and preserves that shape, while crisp folds serve the same shape-"
            "setting role in a paper airplane."
        ),
        "detail_level": "stable_nonprocedural_science_fact",
    },
    "tire_friction": {
        "entity": "tire friction",
        "text": (
            "Static friction between the tires and road provides traction, or grip. That traction "
            "lets the road exert force on the tires for acceleration, braking, and turning; if the "
            "tires slide, available control usually decreases."
        ),
        "detail_level": "stable_nonprocedural_science_fact",
    },
    "alternating_current": {
        "entity": "alternating current",
        "text": (
            "Alternating current (AC) is electric current whose direction periodically reverses; "
            "the voltage polarity also alternates over time."
        ),
        "detail_level": "stable_nonprocedural_science_fact",
    },
    "thermal_printer": {
        "entity": "thermal printer mechanism",
        "text": (
            "A thermal printer's printhead selectively heats tiny elements to form the image. In "
            "direct-thermal printing, that heat darkens heat-sensitive paper. In thermal-transfer "
            "printing, the heated printhead melts ink, wax, or resin from a ribbon onto the media."
        ),
        "detail_level": "stable_nonprocedural_technology_fact",
    },
    "kernel_panic": {
        "entity": "kernel panic",
        "text": (
            "A kernel panic is a fatal operating-system kernel error from which the kernel cannot "
            "safely recover. The system halts or restarts to prevent further damage or corruption; "
            "it is the Unix-like analogue of a Windows stop error."
        ),
        "detail_level": "stable_nonprocedural_operating_system_fact",
    },
}


def _reviewed_safe_knowledge_key(text: str) -> str:
    normalized = str(text or "")
    if _FIRE_TRIANGLE_RE.search(normalized) and not _FIRE_PROCEDURE_RE.search(normalized):
        return "fire_triangle"
    if _REVIEWED_PROCEDURE_RE.search(normalized):
        return ""
    if _AVIATION_BRACE_RE.search(normalized):
        return "aviation_brace"
    if _WING_AERODYNAMICS_RE.search(normalized):
        return "wing_aerodynamics"
    if _TIRE_FRICTION_RE.search(normalized):
        return "tire_friction"
    if _ALTERNATING_CURRENT_RE.search(normalized):
        return "alternating_current"
    if _THERMAL_PRINTER_RE.search(normalized) and not _THERMAL_PRINTER_PROCEDURE_RE.search(normalized):
        return "thermal_printer"
    if _KERNEL_PANIC_RE.search(normalized):
        return "kernel_panic"
    return ""


def _reviewed_safe_knowledge_accepts(clause: TurnClause) -> bool:
    return bool(_reviewed_safe_knowledge_key(clause.request_text))


def _reviewed_safe_knowledge_expand(request_text: str) -> list[dict[str, Any]]:
    text = " ".join(str(request_text or "").strip().split())
    key = _reviewed_safe_knowledge_key(text)
    fact = _REVIEWED_SAFE_KNOWLEDGE.get(key)
    if fact is None:
        return []
    return [{"entity": fact["entity"], "knowledge_key": key, "request": text}]


def _reviewed_safe_knowledge_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    # This registry contains only reviewed, stable, nonprocedural facts. No generation or tool seam
    # is read, so an independent harmless sibling cannot expire behind model loading or inference.
    del ctx
    key = str(node.arguments.get("knowledge_key") or "")
    fact = _REVIEWED_SAFE_KNOWLEDGE.get(key)
    if fact is None:
        raise ValueError("no reviewed safe knowledge fact matches this request")
    return {
        "text": fact["text"],
        "detail_level": fact["detail_level"],
        "knowledge_key": key,
    }


def _reviewed_safe_knowledge_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    del node
    return str(result.get("text") or "").strip()


_SAFE_CHEMICAL_OVERVIEW_RE = re.compile(
    r"\b(?:explain|describe|state|what\s+is|what's)\b[^.!?;\n]{0,100}"
    r"\b(?:basic|high[- ]level|general|chemical)?\s*(?:composition|components?|ingredients?)\b"
    r"[^.!?;\n]{0,60}\b(?:gunpowder|black\s+powder)\b"
    r"|\b(?:gunpowder|black\s+powder)\b[^.!?;\n]{0,80}"
    r"\b(?:composition|components?|ingredients?)\b",
    re.IGNORECASE,
)
_CHEMICAL_PROCEDURE_RE = re.compile(
    r"\b(?:ratios?|proportions?|percentages?|percent|amounts?|quantit(?:y|ies)|doses?|"
    r"grams?|ounces?|parts?)\b"
    r"|\b(?:how\s+to|steps?|procedure|instructions?|prepare|make|manufacture|mix|combine|"
    r"grind|mill|pack|confine|ignite|detonate)\b",
    re.IGNORECASE,
)


def _safe_chemical_overview_accepts(clause: TurnClause) -> bool:
    text = str(clause.request_text or "")
    return bool(_SAFE_CHEMICAL_OVERVIEW_RE.search(text)) and not bool(
        _CHEMICAL_PROCEDURE_RE.search(text)
    )


def _safe_chemical_overview_expand(request_text: str) -> list[dict[str, Any]]:
    text = " ".join(str(request_text or "").strip().split())
    if not _SAFE_CHEMICAL_OVERVIEW_RE.search(text) or _CHEMICAL_PROCEDURE_RE.search(text):
        return []
    return [{"entity": "high-level black powder composition", "request": text}]


def _safe_chemical_overview_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    # Stable high-level chemistry only.  No generation seam is used, so a model-level safety
    # refusal cannot erase this harmless sibling. Ratios, preparation, optimization, ignition
    # procedures, confinement, and other procedural instructions are outside the result contract.
    del node, ctx
    return {
        "text": (
            "Traditional black powder is a mixture of potassium nitrate, charcoal, and sulfur. "
            "At a high level, potassium nitrate supplies oxygen, charcoal is the main fuel, and "
            "sulfur supports more reliable combustion. This is a composition overview, "
            "not preparation or use instructions."
        ),
        "detail_level": "high_level_nonprocedural",
    }


def _safe_chemical_overview_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    del node
    return str(result.get("text") or "").strip()


_SAFE_CONTENT_RE = re.compile(
    r"\b(?:write|draft|create|generate|provide|give|make|return)\b"
    r"[^.!?;\n]{0,140}\b(?:code|script|program|function|snippet|recipe|poem|story|instructions?|steps?)\b"
    r"|\b(?:code|script|program|function|snippet|recipe|poem|story)\b"
    r"[^.!?;\n]{0,100}\b(?:for|that|which|using|in)\b",
    re.IGNORECASE,
)
_CONTENT_WORKSPACE_EFFECT_RE = re.compile(
    r"(?:^|\s)(?:~|\.{0,2})?/[^\s]+|\b(?:file|folder|directory|workspace|repository|repo|codebase)\b"
    r"[^.!?;\n]{0,60}\b(?:write|save|create|edit|modify|append)\b"
    r"|\b(?:write|save|create|edit|modify|append)\b[^.!?;\n]{0,60}"
    r"\b(?:file|folder|directory|workspace|repository|repo|codebase)\b"
    r"|\b(?:write|save|create|edit|modify|append)\b[^.!?;\n]{0,100}"
    r"\b(?:to|as|in)\s+[^\s/]+\.(?:py|pyw|js|ts|sh|rb|go|rs|java|c|cpp|h)\b",
    re.IGNORECASE,
)


def _safe_content_accepts(clause: TurnClause) -> bool:
    text = str(clause.request_text or "")
    return bool(_SAFE_CONTENT_RE.search(text)) and not bool(_CONTENT_WORKSPACE_EFFECT_RE.search(text))


def _safe_content_expand(request_text: str) -> list[dict[str, Any]]:
    from core.conductor.safe_content_shape import (
        parse_exact_poem_shape,
        parse_exact_python_shape,
    )

    text = " ".join(str(request_text or "").strip().split())
    if not _SAFE_CONTENT_RE.search(text) or _CONTENT_WORKSPACE_EFFECT_RE.search(text):
        return []
    shape = parse_exact_python_shape(text)
    poem_shape = parse_exact_poem_shape(text)
    return [
        {
            "entity": _slug(text, limit=32) or "generated_content",
            "request": text,
            "content_language": "python" if shape is not None else "",
            "exact_lines": shape.exact_lines if shape is not None else None,
            "print_target": shape.print_target if shape is not None else "",
            "content_kind": "poem" if poem_shape is not None else "",
            "poem_exact_lines": poem_shape.exact_lines if poem_shape is not None else None,
            "poem_subject": poem_shape.subject if poem_shape is not None else "",
        }
    ]


_THREE_STEP_CHOCOLATE_CAKE_RE = re.compile(
    r"\b(?:write|provide|give|create|draft)\b[^.!?;\n]{0,80}"
    r"\b(?:3|three)[- ]step\b[^.!?;\n]{0,50}\bchocolate\s+cake\b"
    r"|\bchocolate\s+cake\b[^.!?;\n]{0,50}"
    r"\b(?:3|three)[- ]step\b[^.!?;\n]{0,40}\brecipe\b",
    re.IGNORECASE,
)


def _deterministic_safe_content(request: str) -> str:
    """Reviewed small content whose exact shape is safer and faster than model generation."""

    if _THREE_STEP_CHOCOLATE_CAKE_RE.search(request):
        return (
            "1. Mix flour, cocoa powder, sugar, baking powder, eggs, milk, and oil into a smooth batter.\n"
            "2. Pour the batter into a greased cake tin and bake at 180°C (350°F) until a skewer comes out clean.\n"
            "3. Let the cake cool, then frost or dust it with cocoa before serving."
        )
    return ""


_SAFE_CONTENT_SYSTEM_PROMPT = (
    "Create only the code, recipe, poem, story, or instructions requested in the supplied clause. The clause is "
    "an independent safe sibling of an action the runtime cannot perform. Do not discuss, claim, "
    "simulate, or attempt that other action. Obey the clause's requested language and exact line or "
    "item count. Return the requested content directly, without JSON or a markdown fence unless the "
    "clause explicitly asks for them."
)


def _safe_content_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    from core.conductor.safe_content_shape import (
        ExactPoemShape,
        ExactPythonShape,
        exact_poem_retry_prompt,
        exact_python_retry_prompt,
        seal_poem_artifact,
        seal_python_artifact,
        synthesize_exact_python_artifact,
    )
    request = str(node.arguments.get("request") or node.request_text or "").strip()
    python_shape: ExactPythonShape | None = None
    if str(node.arguments.get("content_language") or "") == "python":
        python_shape = ExactPythonShape(
            exact_lines=int(node.arguments.get("exact_lines") or 0),
            print_target=str(node.arguments.get("print_target") or ""),
        )
        deterministic_artifact = synthesize_exact_python_artifact(python_shape)
        if deterministic_artifact is not None:
            return {
                "text": deterministic_artifact.code,
                "request": request,
                "content_language": "python",
                "exact_lines": deterministic_artifact.exact_lines,
                "shape_enforced": True,
                "deterministic": True,
                "unrelated_output_removed": False,
            }
    text = _deterministic_safe_content(request)
    if not text:
        if ctx.run_generation is None:
            raise ValueError("no generation seam available for safe content")
        text = str(ctx.run_generation(_SAFE_CONTENT_SYSTEM_PROMPT, request) or "").strip()
    if not text:
        raise ValueError("the requested content came back empty")
    if python_shape is not None:
        artifact = seal_python_artifact(text, python_shape)
        if artifact is None:
            retry = exact_python_retry_prompt(request, python_shape)
            repaired = str(ctx.run_generation(_SAFE_CONTENT_SYSTEM_PROMPT, retry) or "").strip()
            artifact = seal_python_artifact(repaired, python_shape)
        if artifact is None:
            raise ValueError(
                f"generated Python did not satisfy the exact {python_shape.exact_lines}-line contract"
            )
        return {
            "text": artifact.code,
            "request": request,
            "content_language": "python",
            "exact_lines": artifact.exact_lines,
            "shape_enforced": True,
            "unrelated_output_removed": artifact.unrelated_output_removed,
        }
    if str(node.arguments.get("content_kind") or "") == "poem":
        shape = ExactPoemShape(
            exact_lines=int(node.arguments.get("poem_exact_lines") or 0),
            subject=str(node.arguments.get("poem_subject") or ""),
        )
        artifact = seal_poem_artifact(text, shape)
        if artifact is None:
            retry = exact_poem_retry_prompt(request, shape)
            repaired = str(ctx.run_generation(_SAFE_CONTENT_SYSTEM_PROMPT, retry) or "").strip()
            artifact = seal_poem_artifact(repaired, shape)
        if artifact is None:
            raise ValueError(
                f"generated poem did not satisfy the exact {shape.exact_lines}-line "
                f"{shape.subject!r} subject contract"
            )
        return {
            "text": artifact.poem,
            "request": request,
            "content_kind": "poem",
            "exact_lines": artifact.exact_lines,
            "subject": artifact.subject,
            "shape_enforced": True,
            "unrelated_output_removed": artifact.unrelated_output_removed,
        }
    return {"text": text, "request": request}


def _safe_content_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    text = str(result.get("text") or "").strip()
    if result.get("shape_enforced") and result.get("content_language") == "python":
        return f"```python\n{text}\n```"
    return text


# --- machine observation -----------------------------------------------------

#: The binding between the runtime's own claim registry and the read-only machine intents. The
#: family NAMES live in `core.agent_runtime.intent_claims`; the intent names live in
#: `core.runtime_tool_contracts`. This map is the one place the two are joined -- a clause the
#: claim registry owns is served by reading the host, never by asking a model to reason it out.
_MACHINE_FAMILY_INTENTS = {
    "disk_usage": ("machine.disk_usage", ()),
    "machine_specs": ("machine.inspect_specs", ()),
    "list_processes": ("machine.list_processes", ()),
    "host_state": ("machine.host_state", ()),
    #: An explicit path the recognizer already extracted; never a guessed one.
    "list_directory": ("machine.list_directory", ("path",)),
    "find_folder": ("machine.find_folder", ("name",)),
}


def _machine_expand(request_text: str) -> list[dict[str, Any]]:
    """The clause -> tool binding, when the runtime's claim registry already owns the clause.

    The question answered here is not "does this clause look numeric" but "has the runtime
    already proven what this clause IS". `probe_claims` is that proof -- the same registry the
    whole-turn arbitration reads (`core.agent_runtime.answer_coverage`), so a clause that counts
    as machine-owned there is machine-served here rather than renamed by the planner model into
    an operation that cannot read a disk. Measured at 1f8dba98: the planner named
    `quantitative_reasoning` for "how much free disk space do I have?", the named operation
    expanded, and the node failed against an answer the host could have stated exactly.
    """
    from core.agent_runtime.intent_claims import probe_claims

    bound: list[tuple[str, dict[str, Any]]] = []
    families: set[str] = set()
    for claim in probe_claims(request_text):
        entry = _MACHINE_FAMILY_INTENTS.get(claim.family)
        if entry is None:
            continue
        intent, argument_keys = entry
        arguments: dict[str, Any] = {}
        for key in argument_keys:
            value = str(claim.argument or "").strip()
            if not value:
                # A target the recognizer did not extract is not invented here.
                break
            arguments[key] = value
        else:
            bound.append((intent, arguments))
            families.add(claim.family)
    if len(families) != 1:
        # No machine claim is the ordinary decline. Two DIFFERENT machine families owning one
        # clause is an ambiguity the runtime has already proven; binding either would be a guess.
        return []
    intent, arguments = bound[0]
    return [
        {
            "entity": _slug(request_text, limit=32) or "machine",
            "clause": " ".join(str(request_text or "").split()),
            "intent": intent,
            "intent_arguments": arguments,
        }
    ]


def _machine_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Execute the bound machine read through the ONE permission-gated tool seam."""
    if ctx.run_tool_intent is None:
        raise ValueError("no tool seam available for a machine observation")
    intent = str(node.arguments.get("intent") or "")
    execution = ctx.run_tool_intent(
        {
            "intent": intent,
            "arguments": dict(node.arguments.get("intent_arguments") or {}),
        }
    )
    if execution is None or not bool(getattr(execution, "ok", False)):
        status = (
            str(getattr(execution, "status", "") or "") if execution is not None else ""
        )
        raise ValueError(status or "the machine read did not run")
    # The dispatcher's own phrasing is the answer, byte for byte: a second rendering beside the
    # fast lane's is how two lanes come to disagree about the same reading.
    text = str(getattr(execution, "response_text", "") or "").strip()
    if not text:
        raise ValueError("no reading returned")
    return {"text": text, "intent": intent}


def _machine_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    return str(result.get("text") or "").strip()


# --- clock ------------------------------------------------------------------------------------


def _clock_expand(request_text: str) -> list[dict[str, Any]]:
    """One node per place whose LOCAL TIME this clause asks for.

    `extract_utility_timezones_all` is the SAME resolver the clock fast path uses, so the
    conductor and the fast lane cannot disagree about which places were named. Measured live
    2026-08-29 (watch session 2026-08-29T1150Z): "what time is in rome now and in paris?" was
    decomposed by the conductor into clauses that resolved to `machine_observation` (which then
    refused: "found nothing to act on") and to weather -- the conductor had no clock family at
    all, so two clock clauses died and one city was answered by the wrong domain.
    """
    from core.agent_runtime.fast_paths_utility import extract_utility_timezones_all

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for zone, label in extract_utility_timezones_all(request_text):
        key = zone.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"entity": label, "zone": zone})
    return out


def _clock_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Read this place's clock from the runtime's own clock. No network, no model."""
    from core.agent_runtime.fast_paths_utility import utility_now_for_timezone

    zone = str(node.arguments.get("zone") or "").strip()
    if not zone:
        raise ValueError("no timezone resolved for this clock clause")
    now = utility_now_for_timezone(zone)
    label = str(node.arguments.get("entity") or zone)
    return {
        "text": f"Current time in {label} is {now:%H:%M %Z}.",
        "zone": zone,
        "label": label,
    }


def _clock_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    return str(result.get("text") or "").strip()


def _clock_capability_accepts(clause: Any) -> bool:
    """A clause asking the CURRENT TIME in a named place -- the clock family's whole diet.

    The same recognizers the clock fast path gates on
    (`core.agent_runtime.fast_paths_utility`), so the conductor and the fast lane cannot
    disagree about what a clock ask is. An event time ("when does the train leave?") or a
    duration ask carries no resolvable place-clock frame and stays out.
    """
    from core.agent_runtime.fast_paths_utility import (
        _ASKS_TIME_RE,
        _time_word_is_the_clock,
        extract_utility_timezones_all,
    )

    text = str(
        getattr(clause, "request_text", None) or getattr(clause, "request", "") or ""
    )
    if not text.strip():
        return False
    if not extract_utility_timezones_all(text):
        return False
    lowered = " ".join(text.lower().split())
    return bool(
        _ASKS_TIME_RE.search(lowered)
        or _time_word_is_the_clock(lowered, has_timezone=True)
    )


# --- sea/water temperature -------------------------------------------------------------------


def _water_expand(request_text: str) -> list[dict[str, Any]]:
    """One node per water-temperature ask in this clause.

    The SAME extractor the live-data plan lane uses (`extract_water_asks`), so
    the two doors cannot disagree about what a water ask is or which place it
    names. Measured live (AUD-20260829-003 C4): no family read sea-temperature
    asks, so the weather family mis-claimed them ("found nothing to act on")
    in multi-slot turns and solo asks fell to the model, which improvised
    unsourced prose.
    """
    from core.fresh_data.water_temperature import extract_water_asks

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for place, _span in extract_water_asks(request_text):
        key = place.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"entity": place, "place": place})
    return out


def _water_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    """Executed by the live-data lane's own water runner -- one implementation, no fork."""
    from core.agent_runtime.live_data_plan import _water_subtask
    from core.agent_runtime.live_data_runner import _run_water_subtask

    if _turn_forbids(node, ctx, "weather"):
        # A turn that forbids weather retrieval forbids marine observation too --
        # same remote-observation class, same per-turn ledger.
        raise ValueError("this turn forbids weather retrieval, so no observation was made")

    place = str(node.arguments.get("place") or "")
    subtask = _water_subtask("conductor", place)
    outcome = _run_water_subtask(subtask, timeout_s=ctx.timeout_s)
    if outcome.result is None:
        raise ValueError(outcome.failure_reason or "no sea-temperature observation returned")
    return dict(outcome.result)


def _water_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    label = str(result.get("label") or node.arguments.get("place") or "").strip()
    celsius = result.get("temperature_c")
    if celsius is None:
        return ""
    line = f"Water temperature at {label}: {celsius}°C"
    fahrenheit = result.get("temperature_f")
    if fahrenheit is not None:
        line += f" ({fahrenheit}°F)"
    source = str(result.get("source") or "").strip()
    if source:
        line += f" (source: {source})"
    return line


def _water_capability_accepts(clause: Any) -> bool:
    """A clause asking the WATER/SEA temperature at a named place — this family's
    whole diet. Scoped so air-weather clauses ("weather in Rome") stay with the
    weather family, and event/duration asks stay out entirely."""
    from core.fresh_data.water_temperature import WATER_ASK_RE, extract_water_asks

    text = str(
        getattr(clause, "request_text", None) or getattr(clause, "request", "") or ""
    )
    if not text.strip() or not WATER_ASK_RE.search(text):
        return False
    return bool(extract_water_asks(text))


# --- registration -----------------------------------------------------------------------------

SLICE_1_OPERATIONS: tuple[OperationSpec, ...] = (
    RESULT_PRESENTATION_OPERATION,
    OperationSpec(
        name="calculation",
        description="arithmetic the runtime evaluates exactly (e.g. '137 x 29')",
        expand_arguments=_calculation_expand,
        # Keyed on the ARITHMETIC this node will actually evaluate, extracted by the same function
        # `run` uses. The planner stores a whole clause here ("What is 137 x 29?") while a
        # projection stores the expression, and keying on the raw text made those two different
        # pieces of work -- so the same sum was planned twice.
        execution_key=_calculation_execution_key,
        run=_calculation_run,
        render=_calculation_render,
        required_result_fields=("statement", "value"),
        # `value` is exactly what a dependent anaphoric clause ("now divide that result by 6")
        # needs to bind as its operand. Without this declaration the scheduler's export harvest
        # saw nothing and the dependent received a number it had to re-derive from prose -- with
        # a dead model lane, a derivable figure died as "not determined" (measured live
        # 2026-09-08: "12 * 8 = 96." followed by exactly that).
        exported_value_fields=("value",),
        tool_intent="",
        capability=OperationCapability(
            effect=OperationEffect.COMPUTED_VALUE,
            domain="arithmetic",
            accepted_kinds=frozenset(
                {ClauseKind.KNOW, ClauseKind.COMPUTE, ClauseKind.UNKNOWN}
            ),
        ),
    ),
    OperationSpec(
        name="weather_lookup",
        description="current weather for one or more named places",
        expand_arguments=_weather_expand,
        run=_weather_run,
        render=_weather_render,
        required_result_fields=("condition", "temperature_c"),
        tool_intent="web.research",
        exported_value_fields=("temperature_c", "feels_like_c", "humidity_pct", "wind_kmph"),
        subjects_named=_weather_subjects_named,
        realize_subject=_weather_realize_subject,
        capability=OperationCapability(
            effect=OperationEffect.LIVE_OBSERVATION,
            domain="weather",
            accepted_kinds=frozenset(
                {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}
            ),
            effect_class=REMOTE_FETCH_EFFECT_CLASS,
        ),
    ),
    OperationSpec(
        name="market_quote",
        description="current price and 24h change for one or more named assets (BTC, ETH, gold)",
        expand_arguments=_market_expand,
        run=_market_run,
        render=_market_render,
        required_result_fields=("price", "currency"),
        tool_intent="web.research",
        exported_value_fields=("price", "change_24h_pct"),
        subjects_named=_market_subjects_named,
        realize_subject=_market_realize_subject,
        capability=OperationCapability(
            effect=OperationEffect.LIVE_OBSERVATION,
            domain="market",
            accepted_kinds=frozenset(
                {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}
            ),
            effect_class=REMOTE_FETCH_EFFECT_CLASS,
        ),
    ),
    OperationSpec(
        name="workspace_investigation",
        description="find and read the source in this project that implements a named subject",
        expand_arguments=_workspace_expand,
        run=_workspace_run,
        render=_workspace_render,
        required_result_fields=("files", "match_count"),
        tool_intent="workspace.search_text",
        capability=OperationCapability(
            effect=OperationEffect.WORKSPACE_EVIDENCE,
            domain="workspace source",
            accepted_kinds=frozenset(
                {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}
            ),
            accepts_clause=_workspace_capability_accepts,
        ),
    ),
    OperationSpec(
        name="comparison",
        description="pick the extreme among earlier results (warmest place, largest mover)",
        expand_arguments=_comparison_expand,
        run=_comparison_run,
        render=_comparison_render,
        required_result_fields=("winner_node_id", "metric"),
        is_derived=True,
        capability=OperationCapability(
            effect=OperationEffect.DERIVED_ANALYSIS,
            domain="comparison",
            accepted_kinds=frozenset({ClauseKind.KNOW, ClauseKind.UNKNOWN}),
            accepted_dependency_effects=frozenset(
                {OperationEffect.COMPUTED_VALUE, OperationEffect.LIVE_OBSERVATION}
            ),
        ),
    ),
    OperationSpec(
        name="conclusion",
        description="decide whether something an earlier node found does or does not contain a named feature",
        expand_arguments=_conclusion_expand,
        run=_conclusion_run,
        render=_conclusion_render,
        required_result_fields=("subject", "present_in_scope"),
        tool_intent="workspace.search_text",
        is_derived=True,
        capability=OperationCapability(
            effect=OperationEffect.WORKSPACE_EVIDENCE,
            domain="workspace source conclusion",
            accepted_kinds=frozenset(
                {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}
            ),
            accepts_clause=_workspace_conclusion_accepts,
            accepted_dependency_effects=frozenset({OperationEffect.WORKSPACE_EVIDENCE}),
        ),
    ),
    OperationSpec(
        name="unavailable_action",
        description=(
            "report that a requested physical, outbound-emergency, device, or infrastructure "
            "effect is unavailable and was not attempted"
        ),
        expand_arguments=_unavailable_action_expand,
        run=_unavailable_action_run,
        render=_unavailable_action_render,
        required_result_fields=("request", "kind", "available", "attempted", "reason"),
        capability=OperationCapability(
            effect=OperationEffect.ACTION_STATUS,
            domain="runtime action availability",
            accepted_kinds=frozenset({ClauseKind.ACT, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}),
            accepts_clause=_unavailable_action_accepts,
        ),
    ),
    OperationSpec(
        name="reviewed_safe_knowledge",
        description=(
            "answer a reviewed stable safety or science fact without model generation, retrieval, "
            "or procedural instructions"
        ),
        expand_arguments=_reviewed_safe_knowledge_expand,
        run=_reviewed_safe_knowledge_run,
        render=_reviewed_safe_knowledge_render,
        required_result_fields=("text", "detail_level", "knowledge_key"),
        capability=OperationCapability(
            effect=OperationEffect.KNOWLEDGE_ANSWER,
            domain="reviewed stable nonprocedural knowledge",
            accepted_kinds=frozenset({ClauseKind.KNOW}),
            accepts_clause=_reviewed_safe_knowledge_accepts,
        ),
    ),
    OperationSpec(
        name="safe_chemical_overview",
        description=(
            "give a stable, high-level, nonprocedural chemical composition overview when detailed "
            "preparation or use instructions are neither requested nor appropriate"
        ),
        expand_arguments=_safe_chemical_overview_expand,
        run=_safe_chemical_overview_run,
        render=_safe_chemical_overview_render,
        required_result_fields=("text", "detail_level"),
        capability=OperationCapability(
            effect=OperationEffect.KNOWLEDGE_ANSWER,
            domain="safe high-level chemical knowledge",
            accepted_kinds=frozenset({ClauseKind.KNOW}),
            accepts_clause=_safe_chemical_overview_accepts,
        ),
    ),
    OperationSpec(
        name="missing_information",
        description=(
            "state exactly what the message does not establish -- ambiguous units, benchmarks it "
            "names but never supplies, assumptions an answer would have to make"
        ),
        expand_arguments=_missing_information_expand,
        run=_missing_information_run,
        render=_missing_information_render,
        required_result_fields=("items", "kinds"),
        wants_shared_context=True,
        serves_unclaimed_clause=True,
        # First refusal. A clause asking what is unknown must never be taken by an operation that
        # would try to work it out instead.
        general_priority=10,
        capability=OperationCapability(
            effect=OperationEffect.MISSING_INFORMATION,
            domain="message gaps",
            accepted_kinds=frozenset({ClauseKind.KNOW, ClauseKind.CLARIFY}),
        ),
    ),
    OperationSpec(
        name="quantitative_reasoning",
        description=(
            "work out a figure from numbers stated anywhere in the message (amounts, rates, "
            "totals, what a change would cost) -- the runtime evaluates the arithmetic itself"
        ),
        expand_arguments=_quantitative_expand,
        run=_quantitative_run,
        render=_quantitative_render,
        required_result_fields=("steps", "values"),
        assess_fulfillment=_quantitative_fulfillment,
        needs_generation=True,
        # A node whose roles were bound at plan time computes model-free; the scheduler must not
        # hold it behind the single generation slot (see `_quantitative_model_free`).
        model_free_arguments=_quantitative_model_free,
        wants_shared_context=True,
        wants_dependency_values=True,
        serves_unclaimed_clause=True,
        general_priority=20,
        capability=OperationCapability(
            effect=OperationEffect.COMPUTED_VALUE,
            domain="grounded quantitative reasoning",
            accepted_kinds=frozenset(
                {ClauseKind.KNOW, ClauseKind.COMPUTE, ClauseKind.UNKNOWN}
            ),
        ),
    ),
    OperationSpec(
        name="safe_content_generation",
        description=(
            "generate an independent safe code snippet, script, recipe, or instructions without "
            "performing a sibling physical action"
        ),
        expand_arguments=_safe_content_expand,
        run=_safe_content_run,
        render=_safe_content_render,
        required_result_fields=("text", "request"),
        needs_generation=True,
        capability=OperationCapability(
            effect=OperationEffect.GENERATED_CONTENT,
            domain="safe in-chat content generation",
            accepted_kinds=frozenset({ClauseKind.CREATE}),
            accepts_clause=_safe_content_accepts,
        ),
    ),
    OperationSpec(
        name="factual_explanation",
        description=(
            "explain or advise on one part of a message whose facts the rest of the plan shares, "
            "with closed numeric authority only when the clause invokes supplied figures"
        ),
        expand_arguments=_explanation_expand,
        run=_explanation_run,
        render=_explanation_render,
        required_result_fields=("text",),
        needs_generation=True,
        wants_shared_context=True,
        serves_unclaimed_clause=True,
        serves_named_know_clauses=True,
        # Last. Everything above answers something specific; this answers what is left.
        general_priority=90,
        capability=OperationCapability(
            effect=OperationEffect.KNOWLEDGE_ANSWER,
            domain="knowledge explanation",
            accepted_kinds=frozenset({ClauseKind.KNOW}),
            accepts_clause=_stable_knowledge_capability_accepts,
        ),
        expand_named_arguments=_named_explanation_expand,
    ),
    OperationSpec(
        name="time_clock",
        description=(
            "report the current local time in named places, read from this runtime's own "
            "clock — one node per place"
        ),
        expand_arguments=_clock_expand,
        run=_clock_run,
        render=_clock_render,
        required_result_fields=("text",),
        serves_unclaimed_clause=True,
        outranks_planner_naming=True,
        # Ahead of machine_observation (5): both are LIVE_OBSERVATION reads, and a time clause
        # that landed there died as "found nothing to act on" (measured live 2026-08-29).
        # Ordering costs nothing: the capability scope check keeps non-clock clauses away.
        general_priority=4,
        capability=OperationCapability(
            effect=OperationEffect.LIVE_OBSERVATION,
            domain="the current time in named places",
            accepted_kinds=frozenset({ClauseKind.KNOW, ClauseKind.OBSERVE}),
            accepts_clause=_clock_capability_accepts,
        ),
    ),
    OperationSpec(
        name="water_temperature",
        description=(
            "current sea/water temperature at a named sea or coastal place — one node per ask, "
            "observed from open-meteo's marine API"
        ),
        expand_arguments=_water_expand,
        run=_water_run,
        render=_water_render,
        required_result_fields=("temperature_c", "label", "source"),
        tool_intent="web.research",
        serves_unclaimed_clause=True,
        outranks_planner_naming=True,
        # Ahead of weather_lookup and machine_observation: a sea-temperature clause the
        # planner named "weather" died as "found nothing to act on" (measured live,
        # AUD-20260829-003 C4); the capability scope check keeps non-water clauses away.
        general_priority=4,
        capability=OperationCapability(
            effect=OperationEffect.LIVE_OBSERVATION,
            domain="sea/water temperature at named places",
            # UNKNOWN included because a bare "sea temp in corfu" clause kinds as UNKNOWN
            # (measured live: weather_lookup's own UNKNOWN arm is what let IT steal the
            # clause after this family refused on kind alone).
            accepted_kinds=frozenset(
                {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}
            ),
            accepts_clause=_water_capability_accepts,
            # The transport declaration this capability was missing when it landed:
            # remote work (open-meteo geocoding + the marine API) that the manual
            # receipt set never learned about, so it shipped with zero receipts
            # (measured at a2308a26). Declared here, the derivation covers it.
            effect_class=REMOTE_FETCH_EFFECT_CLASS,
        ),
    ),
    OperationSpec(
        name="machine_observation",
        description=(
            "observe this machine's own state -- free disk space, hardware specs, running "
            "processes, battery or uptime, a directory listing, finding a named folder -- read "
            "from the host itself, never worked out"
        ),
        expand_arguments=_machine_expand,
        run=_machine_run,
        render=_machine_render,
        required_result_fields=("text",),
        serves_unclaimed_clause=True,
        # The claim this operation serves is the runtime's own deterministic reading of the
        # clause, so it binds ahead of the planner's naming: a model that has no machine
        # vocabulary will otherwise hand a disk question to a reasoning operation, which then
        # fails against a fact the host could have stated exactly. See
        # `core.conductor.planner._resolve_clause`.
        outranks_planner_naming=True,
        # Ahead of the refusal and catch-all generals; the expander only ever fires on a clause
        # the claim registry already owns, so ordering costs nothing.
        general_priority=5,
        capability=OperationCapability(
            effect=OperationEffect.LIVE_OBSERVATION,
            domain="this machine's measured state",
            accepted_kinds=frozenset({ClauseKind.KNOW, ClauseKind.OBSERVE}),
        ),
    ),
    *FRESH_DATA_OPERATIONS,
)


def register_slice_1_operations(*, replace: bool = True) -> None:
    """Idempotent registration. Called at import of `core.conductor.planner`."""
    for spec in SLICE_1_OPERATIONS:
        register_operation(spec, replace=replace)


__all__ = ["SLICE_1_OPERATIONS", "register_slice_1_operations"]
