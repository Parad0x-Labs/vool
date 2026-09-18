"""Entity/context compatibility: a modifier that contradicts its entity must never be reasoned past.

QA-050-027, generalised. The reported defect arrived wearing currency clothes — VOOL read `kr` as
NOK, `¥` as JPY, and then wrote "Copenhagen (NOK)" and "Shanghai (JPY)" after being told the
locations — but the currency part is incidental. What actually happened is that a later fact
contradicted an earlier assumption and the runtime kept reasoning from the assumption. The same
shape, in domains that have nothing to do with money:

    Toyota Passat      — the Passat is a Volkswagen
    Renault Golf       — the Golf is a Volkswagen
    Apple Galaxy       — the Galaxy is a Samsung
    Samsung iPhone     — the iPhone is an Apple
    Paris in Germany   — Paris is in France
    Berlin in France   — Berlin is in Germany
    Copenhagen NOK     — Copenhagen is in Denmark, which issues DKK

Answering any of those as though the pairing were fine — ranking them, pricing them, explaining
them — is reasoning from a poisoned binding, and every figure downstream inherits the poison.

Two outcomes, and the difference between them is the whole design
------------------------------------------------------------------

**Rebind** when one side was never pinned and the other side pins it. "500 kr … the kr is in
Copenhagen": `kr` was open across DKK/ISK/NOK/SEK, and Copenhagen selects DKK from inside that set.
Nothing is contradicted; something previously unknown became known. Earlier conclusions that ranged
over the open set are still void, and saying so is `core.currency_comparison.Invalidation`'s job.

**Clarify** when both sides are specific and they disagree. "Copenhagen NOK" is not an ambiguity
being resolved — it is two definite claims that cannot both hold, and there is no fact in any table
that says which one the user meant. Picking the city, picking the currency, or quietly answering
about something else are all the same error. Ask.

That test — *is the contradicted side ambiguous, or definite?* — is domain-independent, and it is
why this module is one mechanism rather than four special cases.

What this is NOT
-----------------
Not a knowledge graph, and not an attempt at one. It is a handful of small closed tables of the
form "this child belongs to that parent", plus one rule for reading a clause. When no table covers
a pairing, this module returns nothing at all and says nothing — silence, not a guess. A runtime
that invented certainty about pairings it has no authority for would be a worse defect than the one
being fixed here.

Adding a domain
----------------
`register_authority()` takes an `EntityAuthority` and every caller picks it up with no change:
the checker, the conductor briefing, and the fast path all iterate `authorities()`. That is the
blast-radius rule made concrete, and `tests/test_v050_complex_currency_ppp_reasoning.py` registers
a throwaway authority and watches it be enforced, so the interface cannot rot into decoration.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# =================================================================================================
# The authority: one small closed table saying which parent each child belongs to.
# =================================================================================================


@dataclass(frozen=True)
class EntityAuthority:
    """A closed child→parent table plus the words needed to say what a mismatch means.

    `members` is the authority. `parent_aliases` lets a parent be written more than one way
    ("VW", "Volkswagen"; "the UK", "Britain") without the members table learning about spelling.
    """

    domain: str
    child_label: str
    parent_label: str
    relation: str
    members: Mapping[str, str]
    parent_aliases: Mapping[str, str] = None  # type: ignore[assignment]
    #: How a parent reads in the clarifying question — "a city IN Germany", "a model FROM Toyota".
    parent_preposition: str = "from"

    def parents(self) -> set[str]:
        return set(self.members.values())

    def alias_map(self) -> dict[str, str]:
        """Every written form of a parent -> the canonical parent name."""

        out = {parent.lower(): parent for parent in self.parents()}
        for written, parent in dict(self.parent_aliases or {}).items():
            out[written.lower()] = parent
        return out


# --- geography: which country a city is in ------------------------------------------------------

CITY_TO_COUNTRY: dict[str, str] = {
    "copenhagen": "Denmark", "københavn": "Denmark", "aarhus": "Denmark", "odense": "Denmark",
    "oslo": "Norway", "bergen": "Norway", "trondheim": "Norway", "stavanger": "Norway",
    "stockholm": "Sweden", "gothenburg": "Sweden", "göteborg": "Sweden", "malmö": "Sweden",
    "malmo": "Sweden", "reykjavik": "Iceland", "reykjavík": "Iceland",
    "helsinki": "Finland",
    "shanghai": "China", "beijing": "China", "peking": "China", "shenzhen": "China",
    "guangzhou": "China", "chengdu": "China", "hangzhou": "China",
    "tokyo": "Japan", "osaka": "Japan", "kyoto": "Japan", "yokohama": "Japan",
    "nagoya": "Japan", "sapporo": "Japan", "fukuoka": "Japan",
    "london": "the United Kingdom", "manchester": "the United Kingdom",
    "edinburgh": "the United Kingdom", "glasgow": "the United Kingdom",
    "new york": "the United States", "san francisco": "the United States",
    "chicago": "the United States", "los angeles": "the United States",
    "boston": "the United States", "seattle": "the United States", "miami": "the United States",
    "washington dc": "the United States",
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada", "ottawa": "Canada",
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia", "perth": "Australia",
    "auckland": "New Zealand", "wellington": "New Zealand",
    "zurich": "Switzerland", "zürich": "Switzerland", "geneva": "Switzerland",
    "bern": "Switzerland", "basel": "Switzerland",
    "paris": "France", "lyon": "France", "marseille": "France", "bordeaux": "France",
    "berlin": "Germany", "munich": "Germany", "hamburg": "Germany", "frankfurt": "Germany",
    "stuttgart": "Germany", "düsseldorf": "Germany",
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain", "seville": "Spain",
    "rome": "Italy", "milan": "Italy", "naples": "Italy", "turin": "Italy", "florence": "Italy",
    "amsterdam": "the Netherlands", "rotterdam": "the Netherlands",
    "lisbon": "Portugal", "porto": "Portugal", "dublin": "Ireland", "vienna": "Austria",
    "brussels": "Belgium", "athens": "Greece",
    "warsaw": "Poland", "krakow": "Poland", "kraków": "Poland", "gdansk": "Poland",
    "prague": "Czechia", "brno": "Czechia", "budapest": "Hungary",
    "bucharest": "Romania", "sofia": "Bulgaria",
    "istanbul": "Türkiye", "ankara": "Türkiye", "izmir": "Türkiye",
    "moscow": "Russia", "st petersburg": "Russia", "kyiv": "Ukraine", "kiev": "Ukraine",
    "mumbai": "India", "delhi": "India", "new delhi": "India", "bangalore": "India",
    "bengaluru": "India", "chennai": "India", "kolkata": "India", "hyderabad": "India",
    "seoul": "South Korea", "busan": "South Korea",
    "bangkok": "Thailand", "chiang mai": "Thailand", "phuket": "Thailand",
    "hanoi": "Vietnam", "ho chi minh city": "Vietnam", "saigon": "Vietnam",
    "jakarta": "Indonesia", "bali": "Indonesia", "denpasar": "Indonesia",
    "kuala lumpur": "Malaysia", "penang": "Malaysia",
    "manila": "the Philippines", "cebu": "the Philippines",
    "taipei": "Taiwan", "kaohsiung": "Taiwan",
    "são paulo": "Brazil", "sao paulo": "Brazil", "rio de janeiro": "Brazil",
    "brasilia": "Brazil",
    "mexico city": "Mexico", "guadalajara": "Mexico", "monterrey": "Mexico",
    "buenos aires": "Argentina", "santiago": "Chile", "bogota": "Colombia",
    "bogotá": "Colombia", "lima": "Peru",
    "johannesburg": "South Africa", "cape town": "South Africa", "pretoria": "South Africa",
    "durban": "South Africa",
    "lagos": "Nigeria", "abuja": "Nigeria", "cairo": "Egypt", "alexandria": "Egypt",
    "nairobi": "Kenya", "casablanca": "Morocco", "marrakech": "Morocco", "rabat": "Morocco",
    "dubai": "the United Arab Emirates", "abu dhabi": "the United Arab Emirates",
    "riyadh": "Saudi Arabia", "jeddah": "Saudi Arabia", "doha": "Qatar",
    "tel aviv": "Israel", "jerusalem": "Israel",
    "karachi": "Pakistan", "lahore": "Pakistan", "islamabad": "Pakistan",
    "dhaka": "Bangladesh", "colombo": "Sri Lanka", "kathmandu": "Nepal",
    "almaty": "Kazakhstan", "astana": "Kazakhstan", "baku": "Azerbaijan", "tbilisi": "Georgia",
}

_COUNTRY_ALIASES: dict[str, str] = {
    "the uk": "the United Kingdom", "uk": "the United Kingdom", "britain": "the United Kingdom",
    "great britain": "the United Kingdom", "england": "the United Kingdom",
    "scotland": "the United Kingdom", "wales": "the United Kingdom",
    "the us": "the United States", "usa": "the United States",
    "the usa": "the United States", "america": "the United States",
    "the united states of america": "the United States",
    "holland": "the Netherlands", "netherlands": "the Netherlands",
    "turkey": "Türkiye", "czech republic": "Czechia", "uae": "the United Arab Emirates",
    "emirates": "the United Arab Emirates", "philippines": "the Philippines",
    "korea": "South Korea", "prc": "China",
}

# --- automotive: which brand a model belongs to -------------------------------------------------
#
# Every entry is a model whose brand is unambiguous. Purely numeric names ("500", "911") are left
# out on purpose: "500 kr" must never read as a Fiat, and a table that cannot tell them apart is
# worse than no entry.

MODEL_TO_BRAND: dict[str, str] = {
    "passat": "Volkswagen", "golf": "Volkswagen", "polo": "Volkswagen", "tiguan": "Volkswagen",
    "jetta": "Volkswagen", "touareg": "Volkswagen", "arteon": "Volkswagen", "id.4": "Volkswagen",
    "camry": "Toyota", "corolla": "Toyota", "prius": "Toyota", "rav4": "Toyota",
    "hilux": "Toyota", "yaris": "Toyota", "land cruiser": "Toyota", "supra": "Toyota",
    "clio": "Renault", "megane": "Renault", "mégane": "Renault", "captur": "Renault",
    "kangoo": "Renault", "scenic": "Renault", "twingo": "Renault",
    "civic": "Honda", "accord": "Honda", "cr-v": "Honda", "jazz": "Honda", "nsx": "Honda",
    "mustang": "Ford", "fiesta": "Ford", "f-150": "Ford", "ranger": "Ford", "kuga": "Ford",
    "model 3": "Tesla", "model s": "Tesla", "model x": "Tesla", "model y": "Tesla",
    "cybertruck": "Tesla",
    "octavia": "Škoda", "fabia": "Škoda", "superb": "Škoda", "kodiaq": "Škoda",
    "ibiza": "SEAT", "leon": "SEAT", "ateca": "SEAT",
    "cayenne": "Porsche", "macan": "Porsche", "panamera": "Porsche", "taycan": "Porsche",
    "qashqai": "Nissan", "juke": "Nissan", "leaf": "Nissan", "micra": "Nissan",
    "sportage": "Kia", "ceed": "Kia", "sorento": "Kia", "picanto": "Kia",
    "tucson": "Hyundai", "kona": "Hyundai", "santa fe": "Hyundai", "ioniq": "Hyundai",
    "xc90": "Volvo", "xc60": "Volvo", "xc40": "Volvo",
    "a-class": "Mercedes-Benz", "c-class": "Mercedes-Benz", "e-class": "Mercedes-Benz",
    "s-class": "Mercedes-Benz",
    "panda": "Fiat", "punto": "Fiat", "tipo": "Fiat",
    "insignia": "Opel", "astra": "Opel", "corsa": "Opel",
    "mx-5": "Mazda", "cx-5": "Mazda", "miata": "Mazda",
    "outback": "Subaru", "impreza": "Subaru", "forester": "Subaru",
}

_BRAND_ALIASES: dict[str, str] = {
    "vw": "Volkswagen", "merc": "Mercedes-Benz", "mercedes": "Mercedes-Benz",
    "skoda": "Škoda", "seat": "SEAT", "vauxhall": "Opel",
}

# --- consumer electronics: which maker a product line belongs to --------------------------------

PRODUCT_TO_BRAND: dict[str, str] = {
    "iphone": "Apple", "ipad": "Apple", "macbook": "Apple", "imac": "Apple",
    "airpods": "Apple", "apple watch": "Apple", "vision pro": "Apple",
    "galaxy": "Samsung", "galaxy s": "Samsung", "galaxy fold": "Samsung", "galaxy tab": "Samsung",
    "pixel": "Google", "chromecast": "Google", "nest hub": "Google",
    "surface": "Microsoft", "xbox": "Microsoft",
    "playstation": "Sony", "walkman": "Sony", "bravia": "Sony",
    "nintendo switch": "Nintendo", "game boy": "Nintendo",
    "kindle": "Amazon", "alexa": "Amazon", "echo dot": "Amazon", "fire tv": "Amazon",
    "thinkpad": "Lenovo", "ideapad": "Lenovo",
    "roomba": "iRobot", "quest": "Meta", "steam deck": "Valve",
}

_MAKER_ALIASES: dict[str, str] = {
    "ms": "Microsoft", "meta/facebook": "Meta", "facebook": "Meta",
}

# --- the currency authority, read from the one currency reference table -------------------------


def _currency_members() -> dict[str, str]:
    from core.currency_intent import CITY_TO_CODE, NATIONALITY_TO_CODE, PLACE_TO_CODE

    return {**PLACE_TO_CODE, **CITY_TO_CODE, **NATIONALITY_TO_CODE}


_BUILTIN: list[EntityAuthority] = [
    EntityAuthority(
        domain="geography",
        child_label="city",
        parent_label="country",
        relation="is in",
        members=CITY_TO_COUNTRY,
        parent_aliases=_COUNTRY_ALIASES,
        parent_preposition="in",
    ),
    EntityAuthority(
        domain="currency_geography",
        child_label="place",
        parent_label="currency",
        relation="uses",
        members=_currency_members(),
        parent_aliases={},
        parent_preposition="that uses",
    ),
    EntityAuthority(
        domain="automotive",
        child_label="model",
        parent_label="brand",
        relation="is made by",
        members=MODEL_TO_BRAND,
        parent_aliases=_BRAND_ALIASES,
        parent_preposition="from",
    ),
    EntityAuthority(
        domain="consumer_electronics",
        child_label="product",
        parent_label="maker",
        relation="is made by",
        members=PRODUCT_TO_BRAND,
        parent_aliases=_MAKER_ALIASES,
        parent_preposition="from",
    ),
]

_EXTRA: list[EntityAuthority] = []
_LOCK = threading.Lock()


def register_authority(authority: EntityAuthority) -> None:
    """Add a compatibility table. Every caller picks it up without changing."""

    with _LOCK:
        _EXTRA.append(authority)


def unregister_authority(domain: str) -> None:
    """Drop a registered table again. Registration is not a one-way door."""

    with _LOCK:
        _EXTRA[:] = [item for item in _EXTRA if item.domain != domain]


def authorities() -> tuple[EntityAuthority, ...]:
    with _LOCK:
        return tuple(_BUILTIN) + tuple(_EXTRA)


# =================================================================================================
# Reading a clause
# =================================================================================================

#: Where one statement ends. "vs" and "versus" are here because a comparison of two contradictory
#: pairings ("Toyota Passat vs Renault Golf") is two separate wrong pairings, not one.
_CLAUSE_SPLIT_RE = re.compile(
    r"[.;:!?\n]|\bvs\.?\b|\bversus\b|\bcompared (?:to|with)\b|\band\b|\bwhile\b|\bwhereas\b|\bbut\b",
    re.IGNORECASE,
)

#: What may sit BETWEEN a child and its claimed parent for the pairing to be attributive rather
#: than incidental. "Berlin to France" is a journey and must not read as a claim about Berlin;
#: "Berlin in France", "Germany with Warsaw" and a bare "Toyota Passat" are claims. Only this
#: closed set of connectors counts, which is what keeps the checker quiet on ordinary prose.
#: Bare adjacency: "Toyota Passat", "Copenhagen NOK". Whitespace and brackets only — a comma is
#: NOT enough on its own, because "Warsaw is in Poland, Berlin is in Germany" puts a comma between
#: Poland and Berlin and every pairing in that sentence is correct.
_ADJACENT_BETWEEN_RE = re.compile(r"^[\s\-–—/|()\[\]]*$")
#: An explicit connector: "Paris in Germany", "Germany with Warsaw", "Copenhagen, which uses NOK".
_CONNECTED_BETWEEN_RE = re.compile(
    r"^[\s,()\[\]\-–—/|'’]*"
    r"(?:(?:which|that)\s+)?"
    r"(?:(?:is|are|was|were|’s|'s)\s+)?"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:in|of|with|from|for|inside|within|based in|located in|over in|out in|priced in|uses?)"
    r"[\s,()\[\]\-–—/|]*$",
    re.IGNORECASE,
)
#: A bare copula: "Berlin is Germany", "the Passat is Toyota".
_COPULA_BETWEEN_RE = re.compile(
    r"^[\s,()\[\]\-–—/|'’]*(?:is|are|was|were|’s|'s)[\s,()\[\]\-–—/|]*$", re.IGNORECASE
)


def _is_attributive(between: str) -> bool:
    return bool(
        _ADJACENT_BETWEEN_RE.match(between)
        or _CONNECTED_BETWEEN_RE.match(between)
        or _COPULA_BETWEEN_RE.match(between)
    )

#: How far apart a child and a parent may sit and still be one claim.
_MAX_GAP = 24


@dataclass(frozen=True)
class EntityContradiction:
    """Two definite claims in one clause that cannot both be true."""

    domain: str
    child: str
    child_label: str
    named_parent: str
    actual_parent: str
    parent_label: str
    relation: str
    #: The pairing exactly as the message wrote it, so the reply quotes the user, not the table.
    written: str = ""
    #: The child as the message wrote it — "iPhone", not a title-cased table key.
    child_written: str = ""
    parent_preposition: str = "from"

    def _child(self) -> str:
        return self.child_written or self.child

    def statement(self) -> str:
        quoted = self.written or f"{self.named_parent} {self._child()}"
        return (
            f"“{quoted}” does not hold: the {self.child_label} {self._child()} {self.relation} "
            f"{self.actual_parent}, not {self.named_parent}."
        )

    def question(self) -> str:
        return (
            f"Did you mean {self._child()} ({self.actual_parent}), or a different "
            f"{self.child_label} {self.parent_preposition} {self.named_parent}?"
        )


@dataclass(frozen=True)
class EntityConsistencyReport:
    """What the message's pairings establish and what they break."""

    contradictions: tuple[EntityContradiction, ...] = ()

    @property
    def consistent(self) -> bool:
        return not self.contradictions

    def domains(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.contradictions:
            if item.domain not in seen:
                seen.append(item.domain)
        return tuple(seen)


def _compiled(names: list[str]) -> re.Pattern[str] | None:
    if not names:
        return None
    return re.compile(
        r"(?<![a-z0-9])(?:"
        + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        + r")(?![a-z])"
    )


def _clause_conflicts(
    clause: str, original: str, authority: EntityAuthority
) -> list[EntityContradiction]:
    child_re = _compiled([str(name) for name in authority.members])
    aliases = authority.alias_map()
    parent_re = _compiled(list(aliases))
    if child_re is None or parent_re is None:
        return []
    children = list(child_re.finditer(clause))
    if not children:
        return []
    parents = list(parent_re.finditer(clause))
    if not parents:
        return []

    def written(start: int, end: int) -> str:
        return original[start:end] if len(original) == len(clause) else clause[start:end]

    # Each child belongs to at most ONE claimed parent, and each parent to at most one child:
    # the nearest, taken greedily. Without that, "Warsaw is in Poland, Berlin is in Germany" pairs
    # Berlin with Poland as well as with Germany and reports a contradiction in a sentence where
    # every pairing is correct. Nearest-first matching reads it the way a reader does.
    candidates: list[tuple[int, int, int, str, str]] = []
    for child_index, child_match in enumerate(children):
        for parent_index, parent_match in enumerate(parents):
            if parent_match.start() >= child_match.end():
                between = clause[child_match.end() : parent_match.start()]
            elif parent_match.end() <= child_match.start():
                between = clause[parent_match.end() : child_match.start()]
            else:
                # Overlapping spans: the same words read two ways, not two claims.
                continue
            if len(between) > _MAX_GAP or not _is_attributive(between):
                continue
            candidates.append(
                (len(between), child_index, parent_index, child_match.group(0), between)
            )

    out: list[EntityContradiction] = []
    used_children: set[int] = set()
    used_parents: set[int] = set()
    for _gap, child_index, parent_index, child, _between in sorted(candidates):
        if child_index in used_children or parent_index in used_parents:
            continue
        used_children.add(child_index)
        used_parents.add(parent_index)
        child_match = children[child_index]
        parent_match = parents[parent_index]
        actual = authority.members[child]
        named = aliases[parent_match.group(0)]
        if named == actual:
            continue
        span = (
            (child_match.start(), parent_match.end())
            if parent_match.start() >= child_match.end()
            else (parent_match.start(), child_match.end())
        )
        out.append(
            EntityContradiction(
                domain=authority.domain,
                child=child,
                child_label=authority.child_label,
                named_parent=named,
                actual_parent=actual,
                parent_label=authority.parent_label,
                relation=authority.relation,
                written=written(*span),
                child_written=written(child_match.start(), child_match.end()),
                parent_preposition=authority.parent_preposition,
            )
        )
    return out


def entity_consistency_report(text: str) -> EntityConsistencyReport:
    """Every pairing in `text` that an authority table says cannot hold.

    Deterministic, consults no model, and empty for anything no table covers. A message whose
    pairings are all fine, and a message about something none of these tables knows, produce the
    identical empty report — this module never guesses a domain it has no authority for.
    """

    original = " ".join(str(text or "").split())
    lowered = original.lower()
    if not lowered:
        return EntityConsistencyReport()
    found: list[EntityContradiction] = []
    seen: set[tuple[str, str, str]] = set()
    cursor = 0
    for clause in _CLAUSE_SPLIT_RE.split(lowered):
        start = lowered.find(clause, cursor) if clause else cursor
        cursor = start + len(clause)
        stripped = clause.strip()
        if not stripped:
            continue
        offset = start + clause.index(stripped)
        source = original[offset : offset + len(stripped)]
        for authority in authorities():
            for conflict in _clause_conflicts(stripped, source, authority):
                key = (conflict.domain, conflict.child, conflict.named_parent)
                if key in seen:
                    continue
                seen.add(key)
                found.append(conflict)
    return EntityConsistencyReport(contradictions=tuple(found))


_CONTENT_WORD_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_SPLIT_RE = re.compile(r"[.;!?\n]")
#: A sentence of fewer than this many content words is filler, not a second request.
_MIN_CONTENT_WORDS = 3


def uncovered_by_contradictions(text: str, report: EntityConsistencyReport) -> tuple[str, ...]:
    """Sentences of `text` that the contradictions do not account for.

    Same rule the currency lane applies to itself: a gate that claims a turn ends it, so asking a
    clarifying question about one clause may not silently discard the rest of the message. When
    there is residue the question travels as an observation instead of as the whole answer.
    """

    covered = [item.written.lower() for item in report.contradictions if item.written]
    residue: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(str(text or "")):
        stripped = " ".join(sentence.split())
        if not stripped:
            continue
        words = [w for w in _CONTENT_WORD_RE.findall(stripped.lower()) if len(w) > 2]
        if len(words) < _MIN_CONTENT_WORDS:
            continue
        if any(fragment in stripped.lower() for fragment in covered):
            continue
        residue.append(stripped)
    return tuple(residue)


def contradiction_observation(
    report: EntityConsistencyReport, *, unanswered: Sequence[str] = ()
) -> dict[str, Any] | None:
    """The broken pairings, typed, for a turn this lane is not going to answer on its own.

    Declining must not mean discarding — a model handed "Toyota Passat" with nothing said will
    answer about some car, and which car is then a coin toss. The reading travels on the same
    observation channel the workspace tools use, so it reaches the model on every surface.
    """

    if report.consistent:
        return None
    return {
        "schema": "tool_observation_v1",
        "intent": "entity.contradiction",
        "tool_surface": "runtime",
        "ok": True,
        "status": "contradiction",
        "read_only": True,
        "final_answer": False,
        "pairings": [
            {
                "domain": item.domain,
                "written": item.written,
                "child": item.child,
                "named_parent": item.named_parent,
                "actual_parent": item.actual_parent,
            }
            for item in report.contradictions
        ],
        "unanswered_here": list(unanswered),
        "instruction": (
            "These pairings are contradicted by this runtime's own reference tables:\n  "
            + "\n  ".join(
                f"{item.statement()} {item.question()}" for item in report.contradictions
            )
            + "\nDo not rank, calculate or explain from either reading. Ask which was meant."
        ),
    }


def render_entity_contradictions(report: EntityConsistencyReport) -> str:
    """The clarification. States each broken pairing, asks, and computes nothing."""

    if report.consistent:
        return ""
    lines = ["Those don't go together, so I've stopped before answering rather than pick one."]
    lines.append("")
    for conflict in report.contradictions:
        lines.append(f"- {conflict.statement()}")
        lines.append(f"  {conflict.question()}")
    lines.append("")
    lines.append(
        "Tell me which side is right for each and I'll answer from there. Guessing would put a "
        "wrong binding under every number and explanation that followed it."
    )
    return "\n".join(lines)


__all__ = [
    "CITY_TO_COUNTRY",
    "MODEL_TO_BRAND",
    "PRODUCT_TO_BRAND",
    "EntityAuthority",
    "EntityConsistencyReport",
    "EntityContradiction",
    "authorities",
    "contradiction_observation",
    "entity_consistency_report",
    "register_authority",
    "render_entity_contradictions",
    "uncovered_by_contradictions",
    "unregister_authority",
]
