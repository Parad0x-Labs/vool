"""Words -> tools, read from the tools' own cards instead of per-tool regex.

Every registered tool that carries a card (`core.tool_cards`) is indexed here: builtins from
``core/tool_card_data/``, plugins and MCP tools from their manifests. A turn's words are scored
against each card's ``examples``; a ``not_for`` ask that fits better than any example vetoes the
card. The result is a ranked list of intents the turn may need.

Two seams read it, the same two the owner live-source recognizer already feeds:

- `core.execution_requirements` releases the plain chat lane when a card match is strong, so a turn
  that needs a tool is not answered tool-less from the model's weights;
- `core.capability_graph.model_visible_specs` seats the matched intents (inside the existing seat
  cap), so the model sees the tool. The model still decides whether to call it.

Neither seam changes who may do what. A matched write tool is only offered; its approval gate,
the wallet, caps and receipts are untouched, and an owner-only tool is matched only on the owner's
own local turn: a turn that is not owner-local gets no card seats at all (the caller passes that
fact in, read from `core.request_trust`).

Scoring is local, deterministic and model-free: stemmed content words weighted by how rare they are
across all cards (so "find" or "show", which every card has, counts for little and "disk" or
"reminder" for a lot), plus character trigrams for typos and word forms the stemmer misses. The
index follows `core.tool_registry.registry_epoch()`, so a plugin installed while VOOL runs is
routable on its next turn with no restart and no router edit.

Behind the policy flag ``tools.card_routing`` (default off) until measured; `match_cards` itself
is pure and always available to tests and the measuring harness.
"""
from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

#: A card whose best example scores at least this is SEATED (offered to the model).
SEAT_SCORE = 0.42
#: A card whose best example scores at least this also RELEASES a tool-less chat lane, when the
#: score could use meaning (the local embedder was up).
RELEASE_SCORE = 0.50
#: Word overlap alone releases only a near-verbatim fit: measured on the tool-use dev asks
#: (2026-10-29, 39 seed cards), words alone at 0.42 fired on 4 of 12 no-tool asks ("what is 17
#: times 23" met "my time zone"), so below this it only seats beside a lane another arm released.
WORDS_ONLY_RELEASE_SCORE = 0.75
#: At most this many card intents are seated from one turn.
MAX_CARD_SEATS = 4
#: A runner-up within this fraction of the top score is seated too (the ask fits both).
_NEAR_TOP = 0.85
_STEM_WEIGHT = 0.7
_TRIGRAM_WEIGHT = 0.3

POLICY_FLAG = "tools.card_routing"


@dataclass(frozen=True)
class CardMatch:
    intent: str
    score: float
    example: str
    family: str
    # Whether meaning (the local embedder) took part in the score, or word overlap alone.
    by_meaning: bool = False


@dataclass(frozen=True)
class _Ask:
    text: str
    stems: frozenset[str]
    grams: frozenset[str]


@dataclass(frozen=True)
class _Entry:
    intent: str
    family: str
    examples: tuple[_Ask, ...]
    not_for: tuple[_Ask, ...]
    surfaces: tuple[str, ...]


@dataclass(frozen=True)
class _Index:
    key: tuple[int, int]
    entries: tuple[_Entry, ...]
    idf: dict[str, float]
    vocab: frozenset[str]


def _fold(text: str) -> str:
    """Lowercase, accents off (descargas/descárgas, išsaugok/issaugok meet), apostrophes joined."""

    decomposed = unicodedata.normalize("NFKD", str(text or "").lower())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"['’]", "", plain)


def _stems(text: str) -> list[str]:
    from core.plugin_skills import _content_stems

    return _content_stems(_fold(text))


def _grams(stems: list[str]) -> frozenset[str]:
    out: set[str] = set()
    for word in stems:
        padded = f" {word} "
        out.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return frozenset(out)


def _ask(text: str) -> _Ask:
    stems = _stems(text)
    return _Ask(text=text, stems=frozenset(stems), grams=_grams(stems))


def _family_of(intent: str) -> str:
    return str(intent or "").split(".", 1)[0].strip().lower()


_lock = threading.Lock()
_cached: _Index | None = None


def _build_index(contracts: Any) -> tuple[_Entry, ...]:
    from core.tool_cards import card_for

    entries: list[_Entry] = []
    for contract in contracts:
        card = card_for(contract)
        if card is None:
            continue
        intent = str(getattr(contract, "intent", "") or "")
        entries.append(
            _Entry(
                intent=intent,
                family=_family_of(intent),
                examples=tuple(_ask(e) for e in card.examples),
                not_for=tuple(_ask(n) for n in card.not_for),
                surfaces=tuple(card.surfaces),
            )
        )
    return tuple(entries)


def _idf(entries: tuple[_Entry, ...]) -> dict[str, float]:
    """Document frequency per CARD (not per example), so a word every card uses weighs little."""

    df: Counter[str] = Counter()
    for entry in entries:
        df.update({stem for ask in entry.examples for stem in ask.stems})
    total = max(1, len(entries))
    return {stem: math.log(1.0 + total / count) for stem, count in df.items()}


def _index(contracts: Any = None) -> _Index:
    """The current index. Rebuilt when the registry or the builtin card files change."""

    global _cached
    from core import tool_cards
    from core.tool_registry import registered_tools, registry_epoch

    if contracts is not None:
        entries = _build_index(contracts)
        idf = _idf(entries)
        return _Index(key=(-1, -1), entries=entries, idf=idf, vocab=frozenset(idf))
    key = (registry_epoch(), id(tool_cards._builtin))
    with _lock:
        if _cached is not None and _cached.key == key:
            return _cached
    entries = _build_index(registered_tools())
    idf = _idf(entries)
    built = _Index(key=(registry_epoch(), id(tool_cards._builtin)), entries=entries, idf=idf, vocab=frozenset(idf))
    with _lock:
        _cached = built
    return built


def reset() -> None:
    global _cached
    with _lock:
        _cached = None


def _score(query: _Ask, ask: _Ask, idf: dict[str, float], vocab: frozenset[str]) -> float:
    if not ask.stems or not query.stems:
        return 0.0
    shared = query.stems & ask.stems
    # One shared word is coincidence ("write" in a haiku request and in "what did I write down"),
    # unless the example or the ask is itself a single word's worth of meaning.
    if not shared or (len(shared) < 2 and min(len(ask.stems), len(query.stems)) > 1):
        return 0.0
    # Query words no card knows (a file name, a person's name, a place) neither help nor hurt:
    # they are the ask's subject, not a sign of which tool it needs.
    known_query = query.stems & vocab
    weight = lambda stems: sum(idf.get(s, 0.0) for s in stems)  # noqa: E731
    denominator = weight(ask.stems) + weight(known_query)
    stem_part = (2.0 * weight(shared) / denominator) if denominator else 0.0
    gram_union = len(query.grams) + len(ask.grams)
    gram_part = (2.0 * len(query.grams & ask.grams) / gram_union) if gram_union else 0.0
    return _STEM_WEIGHT * stem_part + _TRIGRAM_WEIGHT * gram_part


# -- meaning, when a real local embedder is up ---------------------------------------------------
#
# Word overlap only finds asks phrased like an example. When the local embedding model VOOL already
# provisions for memory (core.embedding_service, nomic-embed-text through Ollama) answers, each ask
# is also compared by meaning. A cosine is mapped onto the word score's 0..1 scale so one set of
# thresholds serves both. The example vectors build once per index in the background: until they
# are ready, and whenever the embedder is down (the hash fallback is not a meaning space), matching
# is word overlap alone.
#
# The band puts RELEASE_SCORE (0.50) at cosine 0.70, chosen from
# `python -m tools.tool_card_calibrate` on the tool-use dev asks (2026-10-30, nomic-embed-text,
# 39 cards; asks that would be released by meaning, per kind):
#
#     cosine   needs_tool   no_tool   missing_connection
#     0.75     15/26        2/12      2/2
#     0.70     18/26        4/12      2/2
#     0.65     21/26        7/12      2/2
#
# 0.70 is the knee: above it each step buys more needs-a-tool asks than no-tool asks it hands tools
# to; below it the cost catches up. The old band (0.55, 0.90) released at 0.725. Released is only
# offered: the model still decides, and a no-tool ask handed tools is measured separately.
SEM_ZERO = 0.50
SEM_ONE = 0.90

_vectors_lock = threading.Lock()
_vectors: dict[str, Any] = {"key": None, "backend": "", "by_text": {}, "building": False}


def _semantic_backend() -> str:
    try:
        from core.embedding_service import NEURAL_BACKEND_PREFIX, embedding_backend

        backend = embedding_backend()
        return backend if backend.startswith(NEURAL_BACKEND_PREFIX) else ""
    except Exception:
        return ""


def _embed_ask(text: str) -> tuple[list[float], str]:
    from core.embedding_service import embed_stamped, embedding_query

    # Asks are compared with asks, so both sides take the query role.
    with embedding_query():
        return embed_stamped(text)


def _build_vectors(index: _Index, backend: str) -> None:
    texts = sorted({a.text for e in index.entries for a in (*e.examples, *e.not_for)})
    by_text: dict[str, list[float]] = {}
    try:
        for text in texts:
            vector, stamp = _embed_ask(text)
            if not stamp.startswith(backend.split("#", 1)[0]):
                return  # the embedder fell over mid-build: never mix spaces, try again later
            by_text[text] = vector
    finally:
        with _vectors_lock:
            _vectors["building"] = False
    with _vectors_lock:
        _vectors.update(key=index.key, backend=backend, by_text=by_text)


def _semantic_vectors(index: _Index, *, wait: bool) -> dict[str, list[float]]:
    """The example vectors for this index, or {} while they build or with no real embedder."""

    backend = _semantic_backend()
    if not backend:
        return {}
    with _vectors_lock:
        if _vectors["key"] == index.key and _vectors["backend"] == backend:
            return _vectors["by_text"]
        if _vectors["building"]:
            return {}
        _vectors["building"] = True
    if wait:
        _build_vectors(index, backend)
        with _vectors_lock:
            return _vectors["by_text"] if _vectors["key"] == index.key else {}
    threading.Thread(target=_build_vectors, args=(index, backend), daemon=True, name="tool-card-vectors").start()
    return {}


def _semantic_band() -> tuple[float, float]:
    """(SEM_ZERO, SEM_ONE), or a measuring run's own band from VOOL_TOOL_CARD_SEMANTIC_BAND="lo,hi"."""

    import os

    raw = str(os.environ.get("VOOL_TOOL_CARD_SEMANTIC_BAND") or "").strip()
    if raw:
        try:
            lo, hi = (float(x) for x in raw.split(",", 1))
            if 0.0 <= lo < hi <= 1.0:
                return lo, hi
        except ValueError:
            pass
    return SEM_ZERO, SEM_ONE


def _sem_scaled(cosine: float) -> float:
    lo, hi = _semantic_band()
    return max(0.0, min(1.0, (cosine - lo) / (hi - lo)))


def semantic_score(cosine: float) -> float:
    """The match score a meaning cosine maps to under the current band (for floors set in cosine)."""
    return _sem_scaled(float(cosine))


def match_cards(
    text: str,
    *,
    surface: str = "",
    contracts: Any = None,
    limit: int = MAX_CARD_SEATS,
    floor: float = SEAT_SCORE,
    semantic: str = "auto",
) -> tuple[CardMatch, ...]:
    """Cards whose examples fit these words, best first. Reads no policy and no trust.

    ``contracts`` (tests, the measuring harness) indexes exactly those contracts instead of the
    live registry. ``surface`` drops cards that narrowed themselves to other surfaces.
    ``semantic``: "auto" adds meaning once the example vectors are ready, "wait" builds them first
    (the measuring harness), "off" is word overlap alone.
    """

    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ()
    index = _index(contracts)
    if not index.entries:
        return ()
    query = _ask(cleaned)
    vectors = {} if semantic == "off" else _semantic_vectors(index, wait=semantic == "wait")
    query_vector: list[float] | None = None
    if vectors:
        try:
            query_vector, stamp = _embed_ask(cleaned)
            if not stamp.startswith(_vectors["backend"].split("#", 1)[0]):
                query_vector = None
        except Exception:
            query_vector = None
    if not query.stems and query_vector is None:
        return ()

    def fit(ask: _Ask) -> float:
        value = _score(query, ask, index.idf, index.vocab)
        if query_vector is not None and ask.text in vectors:
            from core.embedding_service import cosine_similarity

            value = max(value, _sem_scaled(cosine_similarity(query_vector, vectors[ask.text])))
        return value

    scored: list[CardMatch] = []
    for entry in index.entries:
        if surface and entry.surfaces and surface not in entry.surfaces:
            continue
        best, best_text = 0.0, ""
        for ask in entry.examples:
            value = fit(ask)
            if value > best:
                best, best_text = value, ask.text
        if best < floor:
            continue
        veto = max((fit(ask) for ask in entry.not_for), default=0.0)
        if veto >= best:
            continue
        scored.append(
            CardMatch(
                intent=entry.intent,
                score=round(best, 4),
                example=best_text,
                family=entry.family,
                by_meaning=query_vector is not None,
            )
        )
    if not scored:
        return ()
    scored.sort(key=lambda m: (-m.score, m.intent))
    top = scored[0].score
    kept = [m for m in scored if m.score >= top * _NEAR_TOP]
    return tuple(kept[: max(1, int(limit))])


def card_routing_enabled() -> bool:
    try:
        from core import policy_engine

        return bool(policy_engine.get(POLICY_FLAG, False))
    except Exception:
        return False


def _usable(intent: str) -> bool:
    """Whether a matched intent may be offered at all: registered, supported, model-visible."""

    from core.tool_registry import tool_for_intent

    contract = tool_for_intent(intent)
    if contract is None or not bool(getattr(contract, "supported", False)):
        return False
    return bool(getattr(contract, "model_visible", True))


def routed_intents(
    text: str,
    *,
    owner_local: bool,
    surface: str = "",
    floor: float = SEAT_SCORE,
) -> tuple[CardMatch, ...]:
    """The card matches this turn may be offered: flag on, owner-local turn, tool available.

    Owner-local only, the same law as the owner live-source recognizer: a remote session (a chat
    app, a shared link) never gains a seat from card words. Whatever a remote lane is offered today
    it keeps; the phone reaches this through the owner-local stamp its pairing grants, if any.
    """

    if not owner_local or not card_routing_enabled():
        return ()
    try:
        matches = match_cards(text, surface=surface, floor=floor)
    except Exception:
        return ()
    out: list[CardMatch] = []
    for match in matches:
        try:
            if _usable(match.intent):
                out.append(match)
        except Exception:
            continue
    return tuple(out)


def releases_lane(text: str, *, owner_local: bool, surface: str = "") -> tuple[CardMatch, ...]:
    """The matches strong enough to give a tool-less chat turn its tools."""

    return tuple(
        m
        for m in routed_intents(text, owner_local=owner_local, surface=surface, floor=RELEASE_SCORE)
        if m.by_meaning or m.score >= WORDS_ONLY_RELEASE_SCORE
    )


__all__ = [
    "MAX_CARD_SEATS",
    "POLICY_FLAG",
    "RELEASE_SCORE",
    "SEAT_SCORE",
    "CardMatch",
    "card_routing_enabled",
    "match_cards",
    "releases_lane",
    "reset",
    "routed_intents",
    "semantic_score",
]
