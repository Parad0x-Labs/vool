"""Deterministic arithmetic for user-grounded travel and cross-place spending questions.

This is deliberately not an FX lookup.  It accepts only rates written in the user's turn, resolves
the named places against the same local ISO reference data as the rest of the currency domain, and
uses :class:`~decimal.Decimal` throughout.  If a cross-currency calculation has no usable supplied
rate, this lane names that missing evidence explicitly and performs no arithmetic; a currency-
identity question needs no rate.

The parser recognizes a semantic shape, not benchmark strings: a holding, a place, a purchase and
its place, plus zero-to-two equations such as ``1 GBP = 1.70 CAD``.  Two equations form a tiny
sealed conversion graph, which handles user-supplied cross rates without any network retrieval.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from core.currency_intent import ISO_4217, LOCATION_TO_CODE

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = r"(?:units?(?:\s+of\s+(?:local\s+)?currency)?|[A-Za-z]{2,5})"

_HOLDING_RE = re.compile(
    # "I have / hold / carry / am holding N units ... in <place>", ended by the next clause: the
    # purchase ("and want to", "and buy"), or the journey ("and fly/travel/go/head to"). Measured
    # 2026-09-07 on new wording of the frozen story: "I hold 2,500 units ... and fly to Bangkok"
    # parsed as no story at all.
    # "I've got" is a holding too, and the place ends where the next clause opens: a new sentence
    # ("Cyprus. Then I go to"), a coordinated clause (", then I go to" / "and buy"), or the bare
    # subject ("I want to"). Measured 2026-09-07 on fresh wording: "Say I have 12,000 units ... in
    # Nicosia, Cyprus, then I go to Belfast" parsed as no story because ", then I go" was not a
    # clause opener the place could end on.
    rf"\bi(?:'ve|\u2019ve)?\s+(?:have|hold|carry|got|am\s+holding|am\s+carrying)\s+(?P<amount>{_NUMBER})\s+(?P<unit>{_UNIT})\s+in\s+"
    r"(?P<location>.+?)(?=(?:\.\s+(?:then\s+)?i\s+|,?\s+(?:and\s+then|and|then)\s+(?:i\s+)?(?:want\s+to\s+|buy\b|(?:fly|travel|go|head)\s+to\b)|"
    r"\s+i\s+(?:want\s+to\s+|(?:travel|fly|go|head)\s+to\s+|buy\b)))",
    re.IGNORECASE | re.DOTALL,
)

_RATE_RE = re.compile(
    rf"(?<![\d,])(?P<left_amount>{_NUMBER})\s*(?P<left>[A-Z]{{3}})\s*=\s*"
    rf"(?P<right_amount>{_NUMBER})\s*(?P<right>[A-Z]{{3}})\b",
    re.IGNORECASE,
)

_CALCULATION_MARKERS = (
    "left",
    "remain",
    "left over",
    "remaining",
    "remainder",
    "deficit",
    "short",
    "enough",
    "break even",
    "show the math",
    "show math",
    "calculate",
    # "what's my balance afterwards" asks for the remainder in other words.
    "balance",
    "which one is worth more",
)
_IDENTITY_MARKERS = (
    "currencies the same",
    "currency the same",
    "name the currencies",
    "name them",
    # "do both places use the same currency / the same money?"
    "same currency",
    "same money",
)
_SPACED_INITIALISM_RE = re.compile(r"(?<!\w)(?P<letters>[a-z](?:\s+[a-z]){1,4})(?!\w)", re.IGNORECASE)

# Qualifiers whose monetary jurisdiction is more precise than their city name.  These are stable
# geographic facts, not prompt phrases; longest-match resolution below still handles ordinary
# countries and cities through LOCATION_TO_CODE.
_REGION_TO_CODE: dict[str, str] = {
    "puerto rico": "USD",
    "ontario": "CAD",
    "tennessee": "USD",
    "texas": "USD",
    "florida": "USD",
    "new jersey": "USD",
    "washington state": "USD",
    "washington dc": "USD",
    "dc": "USD",
    "district of columbia": "USD",
    "costa rica": "CRC",
    "venezuela": "VES",
    "bolivia": "BOB",
    "united kingdom": "GBP",
    "uk": "GBP",
}

# Subnational monetary jurisdictions are stable reference membership, not inferred exchange data.
# Keep the complete U.S. state and Canadian province/territory sets here so comma-qualified cities
# resolve from the jurisdiction the user actually named instead of requiring one-off city aliases.
_US_JURISDICTIONS = frozenset(
    {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
        "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
        "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
        "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
        "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
        "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
        "virginia", "washington", "west virginia", "wisconsin", "wyoming",
        "district of columbia", "washington dc",
        "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
        "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
        "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
        "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
    }
)
_CANADIAN_JURISDICTIONS = frozenset(
    {
        "alberta", "british columbia", "manitoba", "new brunswick", "newfoundland and labrador",
        "nova scotia", "ontario", "prince edward island", "quebec", "saskatchewan",
        "northwest territories", "nunavut", "yukon",
        "ab", "bc", "mb", "nb", "nl", "ns", "on", "pe", "qc", "sk", "nt", "nu", "yt",
    }
)
_REGION_TO_CODE.update({region: "USD" for region in _US_JURISDICTIONS})
_REGION_TO_CODE.update({region: "CAD" for region in _CANADIAN_JURISDICTIONS})
_AMBIGUOUS_MONETARY_QUALIFIERS = frozenset({"georgia"})

# ``LOCATION_TO_CODE`` is optimized for recognition aliases and is intentionally incomplete as a
# list of issuing jurisdictions.  The ISO facts themselves already name those jurisdictions, so a
# comma qualifier such as ``Lima, Peru`` should use that authority rather than fall back to the
# city.  Keep only one-to-one region bindings; a shared or ambiguous issuer label proves nothing.
_ISSUER_REGION_CODES: dict[str, set[str]] = {}
for _code, _fact in ISO_4217.items():
    _ISSUER_REGION_CODES.setdefault(_fact.region.casefold(), set()).add(_code)
_ISSUER_REGION_TO_CODE: dict[str, str] = {
    region: next(iter(codes)) for region, codes in _ISSUER_REGION_CODES.items() if len(codes) == 1
}

_LOCATION_REQUEST_TAIL_RE = re.compile(
    r",\s*(?:am|are|can|calculate|compare|did|do|does|explain|how|if|is|name|show|"
    r"what|when|where|which|who|why|will|would)\b.*$",
    re.IGNORECASE | re.DOTALL,
)
_PARENTHETICAL_LOCATION_RE = re.compile(
    r"^\s*(?P<base>[^()[\]{}]{1,100}?)\s*[([]\s*(?P<qualifier>[^()\[\]{}]{1,160}?)\s*[)\]]\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SuppliedRate:
    left_amount: Decimal
    left_code: str
    right_amount: Decimal
    right_code: str


@dataclass(frozen=True)
class TravelSpendRequest:
    source_amount: Decimal
    source_code: str
    source_location: str
    target_cost: Decimal
    target_code: str
    target_location: str
    rates: tuple[SuppliedRate, ...]
    identity_only: bool = False
    asks_enough: bool = False
    missing_rate: bool = False
    #: The actually-missing pair when a local-currency ask blocks the calculation and that
    #: pair differs from the holdings' own codes (a foreign-unit holding in a local-currency
    #: question). Empty means the missing pair is source/target as parsed.
    missing_pair: tuple[str, str] = ()


@dataclass(frozen=True)
class Holding:
    amount: Decimal
    code: str
    location: str


@dataclass(frozen=True)
class HoldingComparisonRequest:
    holdings: tuple[Holding, Holding]
    target_code: str
    rates: tuple[SuppliedRate, ...]


TravelCurrencyRequest = TravelSpendRequest | HoldingComparisonRequest


@dataclass(frozen=True)
class _ConversionStep:
    source_code: str
    target_code: str
    numerator: Decimal
    denominator: Decimal


def _decimal(raw: str) -> Decimal | None:
    try:
        value = Decimal(str(raw).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return value if value >= 0 and value.is_finite() else None


def _flat(value: str) -> str:
    # Public input normalization separates punctuation, so ``D.C.`` reaches this parser as
    # ``D. C.``. Resolve punctuation-separated jurisdiction initialisms canonically instead of
    # maintaining aliases for every surface spelling (D.C./D. C./D C, U.K./U. K./U K, and so on).
    # The match requires at least two one-letter tokens, so ordinary place names keep their spaces.
    normalized = " ".join(str(value or "").casefold().replace(".", " ").split())
    return _SPACED_INITIALISM_RE.sub(
        lambda match: match.group("letters").replace(" ", ""),
        normalized,
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _explicit_code(unit: str, location: str) -> str:
    candidates = re.findall(r"\b[A-Z]{3}\b", f"{unit} {location}")
    valid = [code for code in candidates if code in ISO_4217]
    return valid[0] if len(set(valid)) == 1 else ""


def _parenthetical_location_code(normalized: str, *, explicit: str = "") -> str | None:
    """Resolve an explicitly typed state/country qualifier; return None when no frame exists.

    Parentheses are semantic evidence here, not decoration.  ``Georgia (US state)`` and
    ``Georgia (country in the Caucasus)`` name different entities even after public-input
    normalization removes the space before ``(``.  The qualifier chooses between existing
    authoritative registries; it never creates a currency from an unregistered base name.
    """

    match = _PARENTHETICAL_LOCATION_RE.fullmatch(normalized)
    if match is None:
        return None
    base = " ".join(match.group("base").strip(" ,").split())
    qualifier = " ".join(match.group("qualifier").split())
    if explicit and qualifier.upper() == explicit:
        return explicit
    qualifier_compact = re.sub(r"[^a-z]", "", qualifier)
    state_typed = bool(
        (
            re.search(r"\bstate\b", qualifier)
            and re.search(r"\b(?:us|usa|united states|america|american)\b", qualifier)
        )
        or re.fullmatch(
            r"(?:the|a)?(?:us|usa|unitedstates|american)state|"
            r"(?:the|a)?statein(?:the)?(?:us|usa|unitedstates|america)",
            qualifier_compact,
        )
    )
    country_marker_present = bool(
        re.search(r"\b(?:country|nation|republic|sovereign|caucasus)\b", qualifier)
        or re.search(r"(?:country|nation|republic|sovereign|caucasus)", qualifier_compact)
    )
    country_typed = bool(
        re.fullmatch(
            r"(?:the\s+|an?\s+)?(?:"
            r"(?:sovereign\s+)?(?:country|nation)(?:\s+in\s+(?:the\s+)?caucasus)?|"
            r"republic(?:\s+in\s+(?:the\s+)?caucasus)?|"
            r"caucasus\s+(?:country|nation))",
            qualifier,
        )
        or re.fullmatch(
            r"(?:the|an?)?(?:"
            r"(?:sovereign)?(?:country|nation)(?:inthecaucasus)?|"
            r"republic(?:inthecaucasus)?|caucasus(?:country|nation))",
            qualifier_compact,
        )
    )
    # Conflicting type evidence is unresolved rather than whichever branch happens to run first.
    if state_typed and country_marker_present:
        return ""
    if state_typed:
        located = "USD" if base in _US_JURISDICTIONS and len(base) > 2 else ""
    elif country_typed:
        located = _ISSUER_REGION_TO_CODE.get(base, "")
    else:
        # Non-type metadata does not erase an otherwise unambiguous base location. The recursive
        # call is safe because ``base`` excludes every bracket accepted by the frame regex.
        return _location_code(base, unit=explicit)
    return located if located and (not explicit or explicit == located) else ""


def _location_code(location: str, *, unit: str = "") -> str:
    explicit = _explicit_code(unit, location)
    normalized = _flat(location)

    parenthetical = _parenthetical_location_code(normalized, explicit=explicit)
    if parenthetical is not None:
        return parenthetical
    if normalized in _AMBIGUOUS_MONETARY_QUALIFIERS:
        return explicit

    # In a qualified city, the trailing jurisdiction wins over a same-named city elsewhere:
    # London, Ontario is CAD; London, UK is GBP.  This is a general comma-qualified-place rule.
    #
    # The qualifier is also negative evidence against the bare-city fallback.  If the authority
    # tables do not know ``Manchester, New Hampshire``, resolving the word ``Manchester`` alone
    # as GBP is not a partial success: it discards the user's disambiguator and silently changes
    # the entity.  Keep the location unresolved instead.  An explicit ISO code in the monetary
    # unit remains usable because it identifies the money directly rather than inferring it from
    # the unresolved place.
    if "," in normalized:
        qualifier = normalized.rsplit(",", 1)[1].strip()
        if qualifier in _AMBIGUOUS_MONETARY_QUALIFIERS:
            return explicit
        qualified = (
            _REGION_TO_CODE.get(qualifier)
            or LOCATION_TO_CODE.get(qualifier)
            or _ISSUER_REGION_TO_CODE.get(qualifier)
        )
        if qualified:
            # The monetary unit names the money; the place only says where the traveller stands.
            # "I have 400 USD in Kyoto, Japan" is a coherent holding, so an explicit ISO code wins
            # over the place's own currency instead of unresolving the story (which sent a
            # perfectly stated calculation to a model call, measured 2026-09-07).
            return explicit or qualified
        return explicit

    # Two-letter postal abbreviations are authoritative only as the whole comma qualifier.  A
    # free scan would reinterpret arbitrary spaced letters (``Alpha B C`` -> ``bc``) as a province.
    region_matches = {
        code
        for place, code in _REGION_TO_CODE.items()
        if len(place) > 2 and _contains_phrase(normalized, place)
    }
    if len(region_matches) == 1:
        located = next(iter(region_matches))
        return located if not explicit or explicit == located else ""

    matches = {code for place, code in LOCATION_TO_CODE.items() if _contains_phrase(normalized, place)}
    if explicit:
        return explicit if not matches or explicit in matches else ""
    return next(iter(matches)) if len(matches) == 1 else ""


def _rates(text: str) -> tuple[SuppliedRate, ...]:
    rows: list[SuppliedRate] = []
    for match in _RATE_RE.finditer(text):
        left_code = match.group("left").upper()
        right_code = match.group("right").upper()
        left_amount = _decimal(match.group("left_amount"))
        right_amount = _decimal(match.group("right_amount"))
        if (
            left_code not in ISO_4217
            or right_code not in ISO_4217
            or left_code == right_code
            or left_amount is None
            or right_amount is None
            or left_amount <= 0
            or right_amount <= 0
        ):
            return ()
        rate = SuppliedRate(left_amount, left_code, right_amount, right_code)
        if rate not in rows:
            rows.append(rate)
    return tuple(rows) if len(rows) <= 2 else ()


def _purchase(text: str, holding_end: int) -> tuple[Decimal, str, str] | None:
    tail = text[holding_end:]
    patterns = (
        # "buy a 5,000 unit item in Bogota, New Jersey"
        re.compile(
            rf"\bbuy\s+(?:an?\s+)?(?P<cost>{_NUMBER})\s+units?\s+item\s+in\s+"
            r"(?P<location>[^.?]+)",
            re.IGNORECASE | re.DOTALL,
        ),
        # "buy a car in Berlin, Germany that costs 50,000 EUR"
        re.compile(
            rf"\bbuy\b.*?\bin\s+(?P<location>[^.?]+?)\s+(?:that\s+)?(?:costs?|for)\s+"
            rf"(?P<cost>{_NUMBER})\s+(?P<unit>{_UNIT})",
            re.IGNORECASE | re.DOTALL,
        ),
        # "buy a phone for 30,000 units ... in Taipei, Taiwan"
        re.compile(
            rf"\bbuy\b.*?(?:\bfor|\bcosts?)\s+(?P<cost>{_NUMBER})\s+(?P<unit>{_UNIT})\s+"
            r"in\s+(?P<location>[^.?]+)",
            re.IGNORECASE | re.DOTALL,
        ),
        # "travel to ..., and buy a meal for 50 units ..."
        re.compile(
            rf"\btravel\s+to\s+(?P<location>.+?)\s*,?\s*and\s+buy\b.*?"
            rf"(?:\bfor|\bcosts?)\s+(?P<cost>{_NUMBER})\s+(?P<unit>{_UNIT})",
            re.IGNORECASE | re.DOTALL,
        ),
        # "fly to Bangkok, Thailand to buy a suit for 30,000 units ..." -- the journey names the place
        # and the purchase follows it (new wording of the frozen story, 2026-09-07)
        re.compile(
            rf"\b(?:fly|travel|go|head)\s+to\s+(?P<location>.+?)\s*,?\s*(?:\band\s+)?(?:\bto\s+)?\bbuy\b.*?"
            rf"(?:\bfor|\bcosts?)\s+(?P<cost>{_NUMBER})\s+(?P<unit>{_UNIT})",
            re.IGNORECASE | re.DOTALL,
        ),
    )
    for pattern in patterns:
        match = pattern.search(tail)
        if match is None:
            continue
        cost = _decimal(match.group("cost"))
        location = _LOCATION_REQUEST_TAIL_RE.sub("", match.group("location")).strip(" ,")
        location = " ".join(location.split())
        unit = match.groupdict().get("unit") or ""
        code = _location_code(location, unit=unit)
        if cost is not None and code:
            return cost, code, location
    return None


def _path(source_code: str, target_code: str, rates: tuple[SuppliedRate, ...]) -> tuple[_ConversionStep, ...]:
    if source_code == target_code:
        return ()
    graph: dict[str, list[_ConversionStep]] = {}
    for rate in rates:
        graph.setdefault(rate.left_code, []).append(
            _ConversionStep(rate.left_code, rate.right_code, rate.right_amount, rate.left_amount)
        )
        graph.setdefault(rate.right_code, []).append(
            _ConversionStep(rate.right_code, rate.left_code, rate.left_amount, rate.right_amount)
        )
    queue: deque[tuple[str, tuple[_ConversionStep, ...]]] = deque([(source_code, ())])
    seen = {source_code}
    while queue:
        code, steps = queue.popleft()
        for step in graph.get(code, []):
            candidate = (*steps, step)
            if step.target_code == target_code:
                return candidate
            if len(candidate) < 2 and step.target_code not in seen:
                seen.add(step.target_code)
                queue.append((step.target_code, candidate))
    return ()


def _comparison_intent(text: str, rates: tuple[SuppliedRate, ...]) -> HoldingComparisonRequest | None:
    if "which one is worth more" not in _flat(text) or len(rates) != 2:
        return None
    match = re.search(
        rf"\bi\s+have\s+(?P<a>{_NUMBER})\s+(?P<u1>{_UNIT})\s+in\s+(?P<l1>.+?)\s+and\s+"
        rf"(?P<b>{_NUMBER})\s+(?P<u2>{_UNIT})\s+in\s+(?P<l2>[^.?]+).*?"
        r"which\s+one\s+is\s+worth\s+more\s+in\s+(?P<target>[A-Z]{3})\b",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None
    amount_a, amount_b = _decimal(match.group("a")), _decimal(match.group("b"))
    target = match.group("target").upper()
    location_a = " ".join(match.group("l1").strip(" ,").split())
    location_b = " ".join(match.group("l2").strip(" ,").split())
    code_a = _location_code(location_a, unit=match.group("u1"))
    code_b = _location_code(location_b, unit=match.group("u2"))
    if (
        amount_a is None
        or amount_b is None
        or not code_a
        or not code_b
        or target not in ISO_4217
        or not _path(code_a, target, rates)
        or not _path(code_b, target, rates)
    ):
        return None
    return HoldingComparisonRequest(
        holdings=(Holding(amount_a, code_a, location_a), Holding(amount_b, code_b, location_b)),
        target_code=target,
        rates=rates,
    )


def travel_spend_intent(text: str) -> TravelCurrencyRequest | None:
    """Parse a sealed user-supplied travel/spend calculation, otherwise return ``None``."""

    raw = str(text or "")
    normalized = _flat(raw)
    if not raw.strip() or not any(marker in normalized for marker in (*_CALCULATION_MARKERS, *_IDENTITY_MARKERS)):
        return None
    rates = _rates(raw)
    # A present but invalid/oversized equation set is not the same evidence state as no equation.
    # Preserve the existing fail-closed behavior instead of relabeling malformed supplied data as
    # a merely absent rate.
    if _RATE_RE.search(raw) is not None and not rates:
        return None
    comparison = _comparison_intent(raw, rates)
    if comparison is not None:
        return comparison

    holding = _HOLDING_RE.search(raw)
    if holding is None:
        return None
    source_amount = _decimal(holding.group("amount"))
    source_location = " ".join(holding.group("location").strip(" ,").split())
    source_code = _location_code(source_location, unit=holding.group("unit"))
    purchase = _purchase(raw, holding.end())
    if source_amount is None or not source_code or purchase is None:
        return None
    target_cost, target_code, target_location = purchase

    asks_identity = any(marker in normalized for marker in _IDENTITY_MARKERS)
    asks_calculation = any(marker in normalized for marker in _CALCULATION_MARKERS)
    conversion_path_missing = source_code != target_code and not _path(source_code, target_code, rates)
    missing_pair: tuple[str, str] = ()
    # A question phrased in LOCAL-currency units asks for the place's own money. The holding's
    # explicit foreign unit is a coherent holding (measured 2026-09-07), but it may not quietly
    # redefine the denomination of the ANSWER: "how many units of local currency" with USD
    # amounts in an XAF jurisdiction still needs USD to XAF, and without a supplied rate that
    # conversion is missing -- a same-currency subtraction would answer a question nobody asked.
    if "local currency" in normalized:
        local_target = _location_code(target_location) or _location_code(source_location)
        if local_target and source_code != local_target and not _path(source_code, local_target, rates):
            conversion_path_missing = True
            missing_pair = (source_code, local_target)
    if conversion_path_missing:
        if not asks_identity and not asks_calculation:
            return None
        identity_only = asks_identity and not asks_calculation
    else:
        identity_only = False
    missing_rate = conversion_path_missing and not identity_only
    return TravelSpendRequest(
        source_amount=source_amount,
        source_code=source_code,
        source_location=source_location,
        target_cost=target_cost,
        target_code=target_code,
        target_location=target_location,
        rates=rates,
        identity_only=identity_only,
        asks_enough="enough" in normalized,
        missing_rate=missing_rate,
        missing_pair=missing_pair,
    )


def _money(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rendered = f"{rounded:,.2f}"
    return rendered.rstrip("0").rstrip(".")


def _identity(code: str) -> str:
    fact = ISO_4217[code]
    return f"{fact.name} ({fact.code})"


def _convert(amount: Decimal, path: tuple[_ConversionStep, ...]) -> tuple[Decimal, list[str]]:
    current = amount
    lines: list[str] = []
    for step in path:
        result = current * step.numerator / step.denominator
        lines.append(
            f"{_money(current)} {step.source_code} × "
            f"({_money(step.numerator)} {step.target_code} / "
            f"{_money(step.denominator)} {step.source_code}) = "
            f"{_money(result)} {step.target_code}."
        )
        current = result
    return current, lines


def render_travel_spend(request: TravelCurrencyRequest) -> str:
    """Render named currencies and auditable arithmetic using only the parsed request."""

    if isinstance(request, HoldingComparisonRequest):
        rows: list[tuple[Holding, Decimal, list[str]]] = []
        for holding in request.holdings:
            value, math = _convert(holding.amount, _path(holding.code, request.target_code, request.rates))
            rows.append((holding, value, math))
        first, second = rows
        if first[1] == second[1]:
            conclusion = f"They are equal at {_money(first[1])} {request.target_code}."
        else:
            winner = first if first[1] > second[1] else second
            conclusion = (
                f"The {_identity(winner[0].code)} holding is worth more: {_money(winner[1])} {request.target_code}."
            )
        return "\n".join(
            [
                f"{first[0].location}: {_identity(first[0].code)}.",
                *first[2],
                f"{second[0].location}: {_identity(second[0].code)}.",
                *second[2],
                conclusion,
                "I used only the exchange rates you supplied; I did not retrieve a live rate.",
            ]
        )

    same = request.source_code == request.target_code
    identity_lines = [
        f"{request.source_location}: {_identity(request.source_code)}.",
        f"{request.target_location}: {_identity(request.target_code)}.",
        f"The currencies are {'the same' if same else 'not the same'}.",
    ]
    if request.identity_only:
        return "\n".join([*identity_lines, "No exchange rate was needed or retrieved."])
    if request.missing_rate:
        left, right = request.missing_pair or (request.source_code, request.target_code)
        local_line = ""
        if request.missing_pair:
            # Name the place's own money the question asked for, so the missing pair is not a
            # currency that appeared from nowhere beside holdings named as something else.
            local_place = request.target_location or request.source_location
            local_line = f"The local currency in {local_place} is the {_identity(right)}."
        return "\n".join(
            [
                *identity_lines,
                *([local_line] if local_line else []),
                f"An exact deficit or remainder cannot be calculated without a "
                f"{left}/{right} exchange rate.",
                "No rate was supplied, retrieved, or invented; provide that rate to calculate the balance.",
            ]
        )

    if same:
        converted = request.source_amount
        math_lines = [f"No exchange is needed: {_money(converted)} {request.target_code}."]
    else:
        converted, math_lines = _convert(
            request.source_amount, _path(request.source_code, request.target_code, request.rates)
        )
    balance = converted - request.target_cost
    math_lines.append(
        f"{_money(converted)} {request.target_code} − {_money(request.target_cost)} "
        f"{request.target_code} = {_money(balance)} {request.target_code}."
    )
    if balance < 0:
        prefix = "No, you do not have enough. " if request.asks_enough else ""
        result = f"{prefix}You are {_money(-balance)} {request.target_code} short."
    elif balance > 0:
        prefix = "Yes, you have enough. " if request.asks_enough else ""
        result = f"{prefix}You have {_money(balance)} {request.target_code} left over."
    else:
        result = f"You break even with 0 {request.target_code} remaining."
    evidence = (
        "No live rate was needed or retrieved."
        if same
        else "I used only the exchange rate(s) you supplied; I did not retrieve a live rate."
    )
    return "\n".join([*identity_lines, *math_lines, result, evidence])


__all__ = [
    "HoldingComparisonRequest",
    "SuppliedRate",
    "TravelCurrencyRequest",
    "TravelSpendRequest",
    "render_travel_spend",
    "travel_spend_intent",
]
