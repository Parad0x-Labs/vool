"""Sea-surface temperature observation — open-meteo marine, no key, no model.

The water-temperature capability's ONE fetch implementation, shared by every door
(conductor family, live-data plan, retry). Given free text naming a sea or a
coastal place: resolve it to a point (named-sea anchor table, else open-meteo
geocoding), read the CURRENT `sea_surface_temperature`, and return typed data.

Laws this module keeps:
- NO model, NO prose fallback, NO invented numbers: any failure returns None and
  the caller serves the honest failure for the slot.
- One provider, honestly labelled ("open-meteo.com (marine)"). A second provider
  is welcome the day one exists without a key; a fake fallback is not.
- Named seas resolve through an explicit geography table (representative coastal
  points), because sea bodies are not cities and must not be geocoded to a random
  same-named land feature.
- Remote calls go through the same policy-aware opener the weather lane uses.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
_USER_AGENT = "VOOL-WATER/1.0"

#: Representative coastal observation points for named seas — general geography,
#: not answer data. Anchored where a reading for "the <sea>" is representative
#: of the sea's mainstream conditions; label names the point so the reply can
#: say WHERE on the sea the reading is from.
SEA_ANCHORS: dict[str, tuple[str, float, float]] = {
    "baltic sea": ("Jurmala, Latvia (Baltic Sea)", 56.97, 23.77),
    "baltic": ("Jurmala, Latvia (Baltic Sea)", 56.97, 23.77),
    "red sea": ("Hurghada, Egypt (Red Sea)", 27.27, 33.81),
    "mediterranean sea": ("Valletta, Malta (Mediterranean)", 35.89, 14.51),
    "mediterranean": ("Valletta, Malta (Mediterranean)", 35.89, 14.51),
    "adriatic sea": ("Split, Croatia (Adriatic Sea)", 43.51, 16.44),
    "adriatic": ("Split, Croatia (Adriatic Sea)", 43.51, 16.44),
    "aegean sea": ("Izmir, Turkey (Aegean Sea)", 38.42, 27.13),
    "aegean": ("Izmir, Turkey (Aegean Sea)", 38.42, 27.13),
    "black sea": ("Varna, Bulgaria (Black Sea)", 43.20, 27.91),
    "north sea": ("Aberdeen, UK (North Sea)", 57.15, -2.09),
    "dead sea": ("Ein Bokek, Israel (Dead Sea)", 31.40, 35.40),
    "caspian sea": ("Baku, Azerbaijan (Caspian Sea)", 40.36, 49.83),
    "arabian sea": ("Muscat, Oman (Arabian Sea)", 23.60, 58.60),
    "south china sea": ("Da Nang, Vietnam (South China Sea)", 16.07, 108.22),
    "caribbean sea": ("Havana, Cuba (Caribbean Sea)", 23.11, -82.37),
    "coral sea": ("Cairns, Australia (Coral Sea)", -16.92, 145.77),
    "tasman sea": ("Sydney, Australia (Tasman Sea)", -33.87, 151.21),
    "bay of biscay": ("Biarritz, France (Bay of Biscay)", 43.48, -1.56),
    "persian gulf": ("Dubai, UAE (Persian Gulf)", 25.27, 55.30),
    "gulf of mexico": ("Tampa, USA (Gulf of Mexico)", 27.95, -82.46),
    "gulf of finland": ("Helsinki, Finland (Gulf of Finland)", 60.15, 25.00),
    "bothnian bay": ("Oulu, Finland (Bothnian Bay)", 65.01, 25.47),
    "barents sea": ("Murmansk, Russia (Barents Sea)", 68.97, 33.08),
    "norwegian sea": ("Alesund, Norway (Norwegian Sea)", 62.47, 6.15),
    "irish sea": ("Dublin, Ireland (Irish Sea)", 53.35, -6.20),
    "english channel": ("Brighton, UK (English Channel)", 50.82, -0.14),
    "baltic sea near jurmala": ("Jurmala, Latvia (Baltic Sea)", 56.97, 23.77),
    "red sea near hurghada": ("Hurghada, Egypt (Red Sea)", 27.27, 33.81),
}

#: A water-temperature ask: water/sea words near temperature words, both orders,
#: tolerant of the misspellings a real user types ("watter", "tempperature") —
#: plus the colloquial no-temperature-word form ("hows the water/sea"), which
#: `core.measurement_medium` also recognizes (one vocabulary decision, two
#: surfaces: detection there, span+place extraction here).
_WATER_WORD = r"(?:watter|water|sea|ocean|marine|seawater)"
_TEMP_WORD = r"(?:temps?|temperatures?|tempperature|warm|hot|cold|degrees?)"
WATER_ASK_RE = re.compile(
    rf"{_WATER_WORD}[\w\s]{{0,24}}{_TEMP_WORD}"
    rf"|{_TEMP_WORD}[\w\s]{{0,24}}{_WATER_WORD}"
    rf"|how'?s\s+(?:the\s+)?{_WATER_WORD}\b",
    re.IGNORECASE,
)

_PLACE_PREPOSITION_RE = re.compile(
    r"\b(?:in|near|at|off|by|for|around|along)\b\s+(?P<place>[^.!?;,]{2,48})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WaterTemperatureResult:
    label: str
    temperature_c: float
    temperature_f: float
    observed: str
    source: str
    asked_as: str


def _open_remote(request: urllib.request.Request, timeout: float):
    """Same policy-aware opener the weather lane uses — one remote-fetch law."""
    from tools.web.web_research import _open_remote as _opener

    return _opener(request, timeout=timeout)


def _normalize_place(place: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", str(place or "").lower())
    cleaned = " ".join(cleaned.split())
    for filler in ("the ", "a "):
        if cleaned.startswith(filler):
            cleaned = cleaned[len(filler) :]
    # A place does not continue past a conjunction or a trailing filler word —
    # "baltic sea and in berlin now" asks about the Baltic Sea, not a compound.
    cleaned = re.split(r"\s+(?:and|&|also|then|plus)\s+", cleaned)[0].strip()
    cleaned = re.sub(
        r"\s+(?:now|today|right now|please|pls|thanks|thank you)$", "", cleaned
    ).strip()
    # Temporal fillers a real user appends to the medium, not the place:
    # "the adriatic these days", "baltic sea during summer".
    cleaned = re.sub(
        r"\s+(?:these days|nowadays|currently|at the moment|lately|"
        r"during\s+(?:the\s+)?(?:summer|winter|spring|autumn|fall)|"
        r"in\s+(?:the\s+)?(?:summer|winter|spring|autumn|fall)|"
        r"this\s+(?:week|month|year|morning|afternoon|evening|season|summer|winter))$",
        "",
        cleaned,
    ).strip()
    return cleaned


def _anchor_for(normalized: str) -> tuple[str, float, float] | None:
    """Anchor lookup with real-user typo tolerance on the SEA NAME only.

    "baltc sea" is how the operator typed the Baltic Sea (verbatim, watch
    session 2026-08-29). Difflib ratio >= 0.8 against anchor keys — the same
    spirit as the weather lane's "wheather"/"weathr" tolerance. City geocoding
    stays exact: fuzzy-matching a typo'd CITY to a random sea anchor would be
    the opposite of careful.
    """
    if not normalized:
        return None
    direct = SEA_ANCHORS.get(normalized)
    if direct is not None:
        return direct
    if not normalized.endswith(("sea", "gulf", "bay", "biscay")):
        return None
    import difflib

    close = difflib.get_close_matches(normalized, list(SEA_ANCHORS), n=1, cutoff=0.8)
    return SEA_ANCHORS[close[0]] if close else None


def extract_water_asks(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Every water-temperature ask in `text` as (place_text, (start, end)).

    The span covers the whole matched ask phrase so callers can blank it before
    another extractor (weather) reads the same text — that is how a "sea temp
    near Palma" clause stops being claimed by the weather family. The place is
    the prepositional object inside/after the ask ("in the Baltic Sea", "near
    Hurghada"); without a preposition the trailing tokens of the clause are the
    place ("baltic sea water temp" style).
    """
    out: list[tuple[str, tuple[int, int]]] = []
    for match in WATER_ASK_RE.finditer(str(text or "")):
        start, end = match.span()
        window = str(text[start:end])
        # The blanked region covers the ask AND its place, so the weather extractor
        # reading the same text cannot claim the place as an air-conditions city
        # (measured live: "hows the water in the north sea" produced an air row for
        # "North Sea" beside the marine row).
        blank_start, blank_end = start, end
        place = ""
        preposition = _PLACE_PREPOSITION_RE.search(window)
        if preposition:
            place = preposition.group("place").strip()
            blank_end = max(blank_end, start + preposition.end())
        else:
            residue = WATER_ASK_RE.sub(" ", window)
            residue = " ".join(residue.split())
            if 2 <= len(residue) <= 48:
                place = residue
        if not place:
            # Leading-place forms: "red sea water temp", "mediterranean water
            # temperature" — the place sits BEFORE the ask words. Take up to three
            # tokens immediately preceding the match window, stopping at any
            # clause punctuation or conjunction.
            head = str(text[:start])
            head = re.split(r"[.!?,;:]|\s+and\s+|\s+also\s+", head)[-1]
            words = head.split()
            leading: list[str] = []
            for word in reversed(words):
                if len(leading) >= 3:
                    break
                # Casual connectors and one-letter fragments ("n black sea", "so hows")
                # are not part of a place name.
                if len(word) < 2 or not re.search(r"[A-Za-z\u00C0-\u024F]", word):
                    break
                if re.fullmatch(
                    r"(?:the|a|of|in|near|at|is|whats|what|how|tell|me|so|ok|then|pls|please)",
                    word,
                    re.IGNORECASE,
                ):
                    break
                leading.insert(0, word)
            candidate = " ".join(leading)
            # "black sea water temp": the window begins with the medium word that
            # belongs to the sea's NAME -- the name is leading + that word.
            window_first = window.split()[0] if window.split() else ""
            if leading and re.fullmatch(r"sea|ocean|gulf|bay", window_first, re.IGNORECASE):
                candidate = f"{candidate} {window_first.lower()}"
            if 2 <= len(candidate) <= 48:
                place = candidate
                blank_start = start - (len(" ".join(leading)) + 1 if leading else 0)
        if not place:
            # Look just past the match ("water temperature in ..." sometimes
            # binds 'in ...' outside the matched window when words intervene).
            tail = str(text[end : end + 60]).split(".")[0].split("?")[0]
            tail_match = _PLACE_PREPOSITION_RE.search(tail)
            if tail_match:
                place = tail_match.group("place").strip()
                blank_end = max(blank_end, end + tail_match.end())
        if not place:
            continue
        # Display-case cleanup only (resolution lowercases separately): cut at a
        # conjunction, drop trailing fillers — the place a reader sees in the
        # ledger row should be the place, not the rest of the sentence.
        place = re.split(r"\s+(?:and|&|also|then|plus)\s+", place)[0].strip(" .,;:!?")
        place = re.sub(
            r"\s+(?:now|today|right now|please|pls|thanks|thank you)[\s.!?]*$",
            "",
            place,
            flags=re.IGNORECASE,
        ).strip(" .,;:!?")
        if len(place) < 2:
            continue
        out.append((place, (blank_start, blank_end)))
    return out


def _geocode(place: str, *, timeout_s: float) -> tuple[str, float, float] | None:
    query = urllib.parse.urlencode(
        {"name": str(place or "").strip()[:64], "count": 1, "language": "en", "format": "json"}
    )
    request = urllib.request.Request(
        f"{_GEOCODING_URL}?{query}", headers={"User-Agent": _USER_AGENT}
    )
    try:
        with _open_remote(request, timeout=min(max(timeout_s, 3.0), 10.0)) as response:
            import json

            payload = json.loads(response.read(120000).decode("utf-8", errors="ignore"))
    except Exception:
        return None
    results = list((payload or {}).get("results") or [])
    if not results:
        return None
    first = results[0] or {}
    try:
        lat = float(first["latitude"])
        lon = float(first["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    name = str(first.get("name") or place).strip()
    country = str(first.get("country_code") or "").strip()
    label = f"{name} ({country})" if country else name
    return (label, lat, lon)


def resolve_water_point(
    place_text: str, *, timeout_s: float = 10.0
) -> tuple[str, float, float] | None:
    """(label, lat, lon) for a named sea or coastal place, or None.

    Resolution order: exact anchor, fuzzy anchor, geocode. Geocoding retries
    with trailing words dropped (once per word, max two drops) — "adriatic
    these days" must resolve to the Adriatic, not fail because a filler word
    rode along into the geocoder.
    """
    normalized = _normalize_place(place_text)
    if not normalized:
        return None
    anchor = _anchor_for(normalized)
    if anchor is not None:
        return anchor
    candidate = normalized
    for _attempt in range(3):
        point = _geocode(candidate, timeout_s=timeout_s)
        if point is not None:
            return point
        words = candidate.split()
        if len(words) <= 1:
            break
        candidate = " ".join(words[:-1])
    return None


def water_temperature_lookup(
    place_text: str, *, timeout_s: float = 15.0
) -> WaterTemperatureResult | None:
    """The CURRENT sea-surface temperature for a named sea/coastal place, or None.

    Never fabricates: no provider value, no answer. `current` is preferred; when
    the marine API has no current reading for the point the last non-null hourly
    value of today is used and labelled with its own timestamp.
    """
    import json
    import time as _time

    point = resolve_water_point(place_text, timeout_s=timeout_s)
    if point is None:
        return None
    label, lat, lon = point
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "current": "sea_surface_temperature",
            "hourly": "sea_surface_temperature",
            "forecast_days": 1,
        }
    )
    request = urllib.request.Request(
        f"{_MARINE_URL}?{query}", headers={"User-Agent": _USER_AGENT}
    )
    try:
        with _open_remote(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
            payload = json.loads(response.read(300000).decode("utf-8", errors="ignore"))
    except Exception:
        return None
    current = dict((payload or {}).get("current") or {})
    value = current.get("sea_surface_temperature")
    observed = str(current.get("time") or "").strip()
    if value is None:
        hourly = dict((payload or {}).get("hourly") or {})
        times = list(hourly.get("time") or [])
        temps = list(hourly.get("sea_surface_temperature") or [])
        now_iso = _time.strftime("%Y-%m-%dT%H:%M", _time.gmtime())
        best_index, best_time = None, ""
        for index, (stamp, temp) in enumerate(zip(times, temps)):
            if temp is None:
                continue
            if str(stamp) <= now_iso:
                best_index, best_time = index, str(stamp)
        if best_index is None and temps:
            for index in range(len(temps) - 1, -1, -1):
                if temps[index] is not None:
                    best_index, best_time = index, str(times[index])
                    break
        if best_index is None:
            return None
        value = temps[best_index]
        observed = best_time
    try:
        celsius = round(float(value), 1)
    except (TypeError, ValueError):
        return None
    return WaterTemperatureResult(
        label=label,
        temperature_c=celsius,
        temperature_f=round(celsius * 9 / 5 + 32, 1),
        observed=observed,
        source="open-meteo.com (marine)",
        asked_as=str(place_text or "").strip(),
    )
