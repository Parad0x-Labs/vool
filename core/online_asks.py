"""Which asks need the web for a right answer: public facts that change, and fresh information.

One reading for every door that has to agree on it:

* the clean loop leaves an online ask to the normal pipeline (`core.clean_loop.route.is_online_ask`),
  so its local brain never answers one from memory;
* the tool-demand authority seats web.search for it (`core.tool_demand_signals`): with Web lookups on
  the model can look it up, and with them off the front door says so before any model call (and the
  entity check, which skips a turn any tool family is seated for, never asks it back);
* the reasoning lane's web-off note reads it too (`turn_reasoning._asks_about_something_that_changes`);
* the local file lane never searches this computer for one (`machine_present_file_intent`): "find the
  cheapest flights to oslo next week" is not a file named "cheapest flights".

Measured on the v0.8 line, desktop, Web lookups off (red team round 3, R3-08, 2026-11-12): "is the
warsaw zoo open on mondays in winter?" was answered "typically open on Mondays during winter", "whats
the opening hours of the natural history museum in vienna on sundays" from memory, "whats the cheapest
flight from krakow to rome in january" was asked back "Are you referring to Kraków in Poland or Kraków
in another country?", and "how many people live in tallinn right now" chose web.research with the web
off and then said "That is not mentioned in the records I have". With Web lookups on, "latest OpenClaw
release notes" seated no web tool (skills audit, 2026-11-12).

The person's own things stay out: "when is my flight to Lisbon?", "how much did I pay for the boiler
service?" and "open the latest pdf in my downloads" are their records and files. Opening hours and
prices need a public subject as well (a museum, a zoo, a named city, a flight, a day of the week), so
a question about something only the person's records hold ("What are the opening hours of the repair
desk?") is not sent to the web. Contributor: sls_0x.
"""
from __future__ import annotations

import re
from functools import lru_cache

# The person's own bookings, payments, files and records, and a reminder they ask for ("remind me to
# call the bank when it opens tomorrow" sets a reminder; "remind me what time the louvre closes" asks).
_OWNER_RE = re.compile(
    r"\b(?:my|our|mine|ours)\b"
    r"|\bremind\s+(?:me|us)\b(?!\s+(?:what|how|where|who|which|whether|if)\b)"
    r"|\b(?:i|we)\s+(?:paid|booked|bought|saved|have|had|got|ordered|spent|keep|kept|wrote|noted|told|said|"
    r"mentioned|put|stored|owe|need|asked|talked|discussed|spoke|chatted|decided|agreed|planned)\b"
    r"|\byou\s+(?:said|told|mentioned|wrote|suggested|found|showed)\b"
    r"|\b(?:did|do|have|had|am|was|were|will)\s+(?:i|we)\b"
    r"|\bi(?:'m|'ve|'d|'ll)\b",
    re.IGNORECASE,
)
# "from my notes", "according to what we said": the person's records, by their own words.
_FROM_RECORDS_RE = re.compile(
    r"\b(?:using|use|from|based\s+on|according\s+to|per)\s+(?:the\s+|these\s+|those\s+)?"
    r"(?:(?:imported|prior|previous|past|earlier|old|saved)\s+)*"
    r"(?:conversations?|chats?|records|notes|messages|what\s+(?:i|we)\s+(?:said|told\s+you|wrote))\b",
    re.IGNORECASE,
)
# Things on this computer or in this chat, which no web page holds.
_LOCAL_THING_RE = re.compile(
    r"\b(?:files?|folders?|pdfs?|docs?|documents?|downloads?|desktop|screenshots?|photos?|pictures?|images?|"
    r"(?<!release\s)(?<!patch\s)notes|notebooks?|journal|diary|e-?mails?|mail|inbox|messages?|texts?|chats?|"
    r"conversations?|invoices?|spreadsheets?|csvs?|xlsx|xls|txt|presentations?|attachments?|"
    r"receipts?|statements?|backups?|commits?|branch(?:es)?|builds?|logs?|tabs?|windows?|apps?|ports?|"
    r"processes|disk|drive|repo(?:sitory)?|reminders?|calendar|to-?dos?|records|"
    r"this\s+(?:computer|mac|laptop|machine|chat|project|folder))\b",
    re.IGNORECASE,
)

# Fresh information: what is new, latest or current. "Latest" alone is not enough: it has to be
# the latest of something the world publishes (news, a version, a release, a score) or "the latest
# on" a subject. "Explain why a stack removes its newest item first" is a question about stacks
# (measured 2026-11-12: it seated a web search next to a reminder and the reminder turn failed).
_FRESH_RE = re.compile(
    r"\b(?:latest|newest|most\s+recent)\s+(?:[\w'’.-]+\s+){0,3}?"
    r"(?:news|headlines?|versions?|releases?|updates?|patch(?:es)?|firmware|models?|episodes?|seasons?|"
    r"albums?|singles?|trailers?|scores?|standings|rankings?|polls?|forecasts?|prices?|rates?|figures|"
    r"statistics|developments|guidance|regulations|apis?|sdks?|frameworks?)\b"
    r"|\b(?:the\s+)?latest\s+(?:on|about|from|regarding|with)\b"
    r"|\bnews\s+(?:on|about|from|regarding|of|for|in)\b|\bany\s+news\b"
    r"|\bwhat'?s\s+new\s+(?:in|with|on|at|from)\b"
    r"|\bcurrent\s+(?:version|release|status|price|prices|rate|rates|record|population)\s+(?:of|for)\b",
    re.IGNORECASE,
)
_RELEASE_NOTES_RE = re.compile(r"\b(?:release|patch)\s+notes\b|\bchange\s*log\b", re.IGNORECASE)
# Writing release notes for one's own work is authoring, not a lookup.
_AUTHORING_RE = re.compile(
    r"\b(?:write|writes|draft|compose|create|make|generate|prepare|update|edit|add|fill\s+in)\b", re.IGNORECASE
)

# When a place is open.
_OPENING_RE = re.compile(
    r"\b(?:opening|closing|business|visiting)\s+(?:hours|times|time)\b"
    r"|\b(?:when|what\s+time)\s+(?:does|do|is|are|will)\s+(?:the\s+)?(?:[\w'’.-]+\s+){0,5}?"
    r"(?:open|opens|close|closes|shut|shuts)\b"
    r"|\b(?:when|what\s+time)\s+(?:the\s+)?(?:[\w'’.-]+\s+){0,5}?(?:opens|closes|shuts)\b"
    r"|\b(?:is|are|will)\s+(?:the\s+)?(?:[\w'’.-]+\s+){0,5}?(?:be\s+)?(?:open|closed)\b"
    r"|\b(?:open|closed)\s+(?:on|today|tonight|tomorrow|now|until|till|late|during|over)\b",
    re.IGNORECASE,
)
# What something costs.
_PRICE_RE = re.compile(
    r"\bhow\s+much\s+(?:is|are|r|does|do|would|will|'s|for)\b"
    r"|\bwhat(?:'s|\s+is|\s+does|\s+do|\s+would|\s+will)\b[^.?!]{0,60}?\b(?:cost|price|fare|fee)s?\b"
    r"|\b(?:price|prices|cost|fare|fares|fee|fees)\s+(?:of|for|to|from|at)\b"
    r"|\b(?:ticket|entry|admission|entrance)\s+(?:price|prices|cost|costs|fee|fees)\b"
    r"|\b(?:cheapest|cheap|affordable)\s+(?:way|flights?|trains?|tickets?|bus(?:es)?|ferr(?:y|ies)|hotels?|"
    r"hostels?|rooms?|time)\b"
    r"|\b(?:train|bus|ferry|metro|subway|tram|taxi|air|plane)\s+fares?\b",
    re.IGNORECASE,
)
# When public transport runs: timetables change, and the last tram is a fact of today.
_SCHEDULE_RE = re.compile(
    r"\b(?:next|last|first|earliest)\s+(?:train|bus|tram|ferry|flight|metro|subway|coach|boat|plane)s?\b"
    r"|\b(?:train|bus|tram|ferry|flight|metro|subway|coach)\s+(?:times|timetables?|schedules?|departures?|arrivals?)\b"
    r"|\b(?:when|what\s+time)\s+(?:does|do|is|are|will)\s+(?:the\s+)?(?:[\w'\u2019.-]+\s+){0,3}?"
    r"(?:train|bus|tram|ferry|flight|metro|subway|coach|boat|plane)s?\s+(?:[\w'\u2019.-]+\s+){0,3}?"
    r"(?:leave|leaves|depart|departs|arrive|arrives|run|runs|go|goes|land|lands)\b",
    re.IGNORECASE,
)
_PAST_RE = re.compile(r"\b(?:did|was|were|cost\s+me|paid)\b", re.IGNORECASE)
_MATH_RE = re.compile(r"\d\s*(?:[-+*/x×^%]|percent\b|times\b)|\b(?:plus|minus|times|divided\s+by)\b", re.IGNORECASE)
# Goods and services anyone can buy, whose price is public.
_PUBLIC_GOODS_RE = re.compile(
    r"\b(?:flights?|trains?|bus(?:es)?|coach|ferr(?:y|ies)|metro|subway|underground|tram|taxis?|ubers?|"
    r"tickets?|day\s+pass|passes|entry|admission|entrance|fares?|hotels?|hostels?|coffee|cappuccino|beer|pint|"
    r"petrol|diesel|fuel)\b",
    re.IGNORECASE,
)
# How many, right now.
_COUNT_RE = re.compile(
    r"\bhow\s+many\s+(?:people|persons|residents|inhabitants|citizens|visitors|tourists|passengers|fans|"
    r"spectators|cases|users|subscribers|followers|employees|students|satellites)\b"
    r"|\b(?:population|attendance|crowds?|queues?|wait(?:ing)?\s+times?)\b",
    re.IGNORECASE,
)
_NOW_RE = re.compile(
    r"\b(?:right\s+now|now|currently|at\s+the\s+moment|today|tonight|these\s+days|nowadays|so\s+far|"
    r"this\s+(?:week|month|year|morning|afternoon|evening|weekend))\b",
    re.IGNORECASE,
)
# What a city charges drivers: parking rules and road charges change by council and by day. Skills
# A/B on the v0.8 head (2026-11-13): "is Sunday free parking in London?" was asked back "Did you mean
# London (city), Sunday (day of the week)?".
_ROAD_CHARGE_RE = re.compile(
    r"\bfree\s+(?:on-?street\s+|street\s+|car\s+)?parking\b"
    r"|\bparking\s+(?:is\s+)?(?:free|charges?|fees?|rules?|restrictions?|costs?|prices?|tickets?\s+(?:cost|price)|hours)\b"
    r"|\b(?:pay|paying)\s+(?:for|to)\s+park\b|\bcan\s+(?:i|we|you)\s+park\b"
    r"|\b(?:congestion|clean\s+air|low\s+emission)\s+(?:charge|zone)\b|\bulez\b|\btolls?\b",
    re.IGNORECASE,
)
# A day, season or holiday a place keeps its own hours on.
_HOURS_ANCHOR_RE = re.compile(
    r"\b(?:mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?|weekends?|weekdays?|"
    r"winter|summer|spring|autumn|fall|christmas|easter|new\s+year'?s?|(?:public|bank)\s+holidays?|"
    r"today|tonight|tomorrow|right\s+now|now)\b",
    re.IGNORECASE,
)
# Places the public visits by their kind.
_VENUE_RE = re.compile(
    r"\b(?:zoo|zoos|\w*museum\w*|museo|gallery|galleries|aquarium|castle|cathedral|basilica|abbey|palace|"
    r"opera(?:\s+house)?|theme\s+park|amusement\s+park|water\s+park|national\s+park|botanical\s+gardens?|"
    r"planetarium|stadium|embassy|consulate|(?:town|city)\s+hall|post\s+office|tower|monument|"
    r"ski\s+(?:resort|lifts?)|tourist\s+(?:office|information)|visitor\s+cent(?:re|er)|airport)\b",
    re.IGNORECASE,
)
# Place names that are also everyday English words or names; they never count as a place alone.
_PLACE_STOP = frozenset({
    "christmas", "easter", "jersey", "wake", "virgin", "darwin", "regina", "phoenix", "resolute", "creston",
    "nome", "currie", "oral", "stanley", "midway", "johnston", "lindeman", "chatham", "norfolk", "thule",
    "wallis", "yap", "atka", "adak", "menominee", "canary", "cocos", "chagos", "comoro",
})


@lru_cache(maxsize=1)
def _place_re() -> re.Pattern[str] | None:
    names: set[str] = set()
    try:
        import zoneinfo

        regions = {"Europe", "Asia", "Africa", "America", "Australia", "Pacific", "Atlantic", "Indian"}
        for zone in zoneinfo.available_timezones():
            parts = zone.split("/")
            if len(parts) == 2 and parts[0] in regions:
                names.add(parts[1].replace("_", " ").lower())
    except Exception:
        pass
    try:
        from core.entity_compatibility import _CITY_COUNTRY

        names.update(str(city).lower() for city in _CITY_COUNTRY)
        names.update(str(country).lower() for country in _CITY_COUNTRY.values())
    except Exception:
        pass
    names = {name for name in names if len(name) > 3 and name not in _PLACE_STOP}
    if not names:
        return None
    alternation = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.IGNORECASE)


def _names_a_place(text: str) -> bool:
    pattern = _place_re()
    return bool(pattern is not None and pattern.search(text))


# What a country or carrier asks of a traveller: visas, entry permits, passports, vaccinations. These
# rules change and differ by place, and "do I need a visa for a layover in doha" says "I" about the
# traveller, not about the person's records. Red team round 3 (W3, 2026-11-12, v0.8 head 10, Web
# lookups off): it was answered "I don't have that saved, and I looked through your other chats".
_ENTRY_DOC = (
    r"(?:visas?|e-?visas?|transit\s+visas?|esta|etias|eta|entry\s+(?:permits?|requirements?|rules?)|"
    r"(?:work|residence|travel)\s+permits?|passports?|international\s+driving\s+(?:permits?|licen[cs]es?)|"
    r"vaccin\w*|jabs?|yellow\s+fever|malaria\s+(?:pills|tablets)|covid\s+(?:tests?|pass(?:es)?))"
)
_ENTRY_RULE_RE = re.compile(
    rf"\b(?:need|needs|needed|require[sd]?|requirements?|necessary|mandatory|have\s+to\s+(?:get|have|show))\b"
    rf"[^.?!]{{0,40}}?\b{_ENTRY_DOC}\b"
    rf"|\b{_ENTRY_DOC}\s+(?:requirements?|rules?|regulations?|policy|needed|required|necessary|free|on\s+arrival)\b"
    rf"|\b{_ENTRY_DOC}\s+(?:do|does|will|would|should)\s+(?:i|we|you|they|one)\s+(?:need|have\s+to\s+get)\b"
    r"|\bentry\s+(?:requirements?|rules?|restrictions?)\s+(?:for|to|of|in|into)\b",
    re.IGNORECASE,
)
# The person's own copy of a document is a file or a record: "find my passport scan", "where is the
# pdf of my visa", "when does my passport expire".
_OWN_DOCUMENT_RE = re.compile(
    rf"\b(?:my|our)\s+(?:\w+\s+)?{_ENTRY_DOC}\s+(?:scans?|copy|copies|photos?|pdfs?|files?|numbers?|expir\w*)\b"
    r"|\b(?:scans?|copy|copies|photos?|pdfs?|files?)\s+of\s+(?:my|our)\b"
    r"|\b(?:does|did|will)\s+(?:my|our)\b[^.?!]{0,30}\bexpire\b"
    r"|\b(?:according\s+to|based\s+on|per|going\s+by|from|in)\s+(?:my|our)\s+(?:notes|records|chats?|files?|documents?|emails?)\b",
    re.IGNORECASE,
)


def _entry_rule_ask(text: str) -> bool:
    return bool(_ENTRY_RULE_RE.search(text)) and not _OWN_DOCUMENT_RE.search(text) and not _FROM_RECORDS_RE.search(text)


def _public_fact_shape(text: str) -> bool:
    """The words alone: a public fact that changes, or fresh information."""
    if _entry_rule_ask(text):
        return True
    if (
        _ROAD_CHARGE_RE.search(text)
        and not re.search(r"\b(?:my|our)\b", text, re.IGNORECASE)
        and not _FROM_RECORDS_RE.search(text)
        and (_VENUE_RE.search(text) or _names_a_place(text) or _HOURS_ANCHOR_RE.search(text))
    ):
        return True
    if _OWNER_RE.search(text) or _FROM_RECORDS_RE.search(text):
        return False
    local = bool(_LOCAL_THING_RE.search(text))
    if _FRESH_RE.search(text) and not local:
        return True
    if _RELEASE_NOTES_RE.search(text) and not local and not _AUTHORING_RE.search(text):
        return True
    if local:
        return False
    if _SCHEDULE_RE.search(text):
        return True
    public = bool(_VENUE_RE.search(text)) or _names_a_place(text)
    if _OPENING_RE.search(text) and (public or _HOURS_ANCHOR_RE.search(text)):
        return True
    if (
        _PRICE_RE.search(text)
        and not _PAST_RE.search(text)
        and not _MATH_RE.search(text)
        and (public or _PUBLIC_GOODS_RE.search(text) or _NOW_RE.search(text))
    ):
        return True
    return bool(_COUNT_RE.search(text) and _NOW_RE.search(text))


@lru_cache(maxsize=1024)
def asks_for_online_facts(text: str) -> bool:
    """Whether the ask needs the web to be answered right (see the module docstring).

    Only the person's instruction counts when the turn pastes the material it asks about ("Here are
    my notes ... the latest news from the council ..."), and a turn that forbids looking things up
    ("don't search, just tell me") is never an online ask.
    """
    value = " ".join(str(text or "").split())
    if not value or len(value) > 2000 or not _public_fact_shape(value[:600]):
        return False
    try:
        from core.inline_payload import supplied_instruction

        instruction = supplied_instruction(value)
        if instruction is not None and not _public_fact_shape(" ".join(instruction.split())):
            return False
    except Exception:
        pass
    try:
        from core.retrieval_constraints import analyze_retrieval_constraints
        from core.stipulated_frame import stipulated_frame_forbids_retrieval

        constraints = analyze_retrieval_constraints(value)
        if stipulated_frame_forbids_retrieval(value) or constraints.forbids_external_retrieval:
            return False
        # An example someone might type ("For instance, someone could ask: what's the latest news
        # on kiln prices?") is not the person's own ask: the reading covers only what is left
        # once such examples are set aside (both measured 2026-11-12 seating a web search).
        if not _public_fact_shape(" ".join(str(constraints.eligible_text or "").split())[:600]):
            return False
    except Exception:
        pass
    return True


def release_notes_are_authored(text: str) -> bool:
    """Whether the release notes or changelog in the ask are the person's own to write or keep
    ("write release notes for this project", "draft the changelog for my app"), not a product's
    published notes to look up ("latest OpenClaw release notes", "release notes for firefox 140").

    The live-information lane and the requirement authority both count "release notes" and
    "changelog" as fresh-lookup words, so with Web lookups off "write release notes for this
    project, grouped under Added, Changed and Fixed" was refused as a web lookup (served, v0.8
    candidate 28fc78dd, 2026-11-12), while this module never read it as an online ask.
    """
    value = " ".join(str(text or "").split())[:600]
    if not _RELEASE_NOTES_RE.search(value):
        return False
    return not asks_for_online_facts(value)


# This computer's own state, and this chat or project: words no public page uses for the world.
# The person's records ("put it in my notes", "save it to a file") are left out on purpose: they are
# where an answer goes as often as what a question is about.
_THIS_COMPUTER_RE = re.compile(
    r"\b(?:ports?|processes|(?:open|running)\s+tabs|logs?|backups?|"
    r"this\s+(?:computer|mac|laptop|machine|chat|conversation|project|folder|repo|repository|workspace))\b",
    re.IGNORECASE,
)


def asks_about_this_computer_or_project(text: str) -> bool:
    """Whether the ask is about this computer's own state, this chat or this project ("is port 8080
    open right now", "write release notes for this project"), which no web page answers."""
    value = " ".join(str(text or "").split())[:600]
    if not value or asks_for_online_facts(value):
        return False
    return bool(_THIS_COMPUTER_RE.search(value))



# The person's own plans, bookings and records, and what they asked for or did ("when is my flight to
# Lisbon?", "remind me to call the bank when it opens tomorrow"): their records answer these, or a
# reminder is set, whatever the requirement authority reads into the words. "my blog" in "can i
# legally use a photo from a news site on my blog" is where the photo goes, not what is asked about.
_OWN_RECORD_RE = re.compile(
    r"\b(?:my|our)\s+(?:[\w'’-]+\s+)?(?:flights?|bookings?|reservations?|appointments?|orders?|tickets?|trips?|"
    r"holidays?|hotels?|meetings?|invoices?|payments?|bills?|subscriptions?|accounts?|plans?|deliver(?:y|ies)|"
    r"parcels?|packages?|shifts?|classes|lessons?|budget|savings|salary|rent|lease|loan|mortgage|policy|claim)\b"
    r"|\bremind\s+(?:me|us)\b"
    r"|\b(?:i|we)\s+(?:paid|booked|bought|saved|ordered|spent|wrote|noted|told|said|mentioned|decided|agreed|planned)\b"
    r"|\b(?:did|have|had|was|were)\s+(?:i|we)\b",
    re.IGNORECASE,
)

_RECORD_NOUN = (
    r"(?:notes|notebooks?|journal(?:\s+entries)?|diary|e-?mails?|mail|inbox|messages|texts|chats|"
    r"files|documents|docs|downloads|screenshots|photos|pictures|voice\s+memos|memos|entries)"
)
_WINDOW = (
    r"(?:today|tonight|yesterday|(?:this|last|past|previous)\s+(?:week|month|morning|afternoon|evening|night|weekend|year))"
)
# A time window that says WHICH of the person's records: "my notes from this week", "this morning's
# emails", "what did i write in my journal yesterday", "what's new in my inbox today".
_RECORDS_OF_A_WINDOW_RE = re.compile(
    r"\b(?:my|our)\s+(?:[\w'’-]+\s+){0,2}?" + _RECORD_NOUN + r"\s+(?:(?:from|of|for|in|during|since|over|written|taken|saved|made)\s+)?(?:the\s+)?" + _WINDOW + r"\b"
    r"|\b" + _WINDOW + r"(?:'s|’s)\s+(?:[\w'’-]+\s+){0,2}?" + _RECORD_NOUN + r"\b"
    r"|\b(?:in|from|through)\s+(?:my|our)\s+(?:[\w'’-]+\s+){0,2}?" + _RECORD_NOUN + r"\s+" + _WINDOW + r"\s*[?.!]?$",
    re.IGNORECASE,
)


def dates_own_records(text: str) -> bool:
    """Whether a time window in the ask says which of the person's own notes, mail or files it means
    ("summarise my notes from this week"): those are on this computer or in VOOL, and no web page holds
    them. A window that dates something else ("what does my journal say the rent is, right now?")
    is not this. Read only."""
    return bool(_RECORDS_OF_A_WINDOW_RE.search(" ".join(str(text or "").split())[:600]))


def requirements_want_a_lookup(text: str, source_context: dict | None = None) -> bool:
    """Whether the requirement authority holds this ask to current information from the web, so the
    publication gate will only let an answer out that a lookup backs.

    Every door before the model has to read that the same way, or the turn reaches the gate with no
    lookup and is withheld. Red team round 3 (W2, 2026-11-12, v0.8 head 10): "can i legally use a
    photo from a news site on my blog" is held to a lookup by the requirement authority; with Web
    lookups off it got "That is not mentioned in the records I have" instead of the web-off line, and
    with them on "I didn't look anything up for this ... so I'm not sending it". A question about this
    computer or this project, or one the turn's own material answers, is never held to the web.
    """
    value = " ".join(str(text or "").split())
    if not value or len(value) > 2000:
        return False
    if asks_for_online_facts(value):
        return True
    head = value[:600]
    if _THIS_COMPUTER_RE.search(head) or _OWN_DOCUMENT_RE.search(head) or _OWN_RECORD_RE.search(head):
        return False
    if _FROM_RECORDS_RE.search(head) or (_OWNER_RE.search(head) and _LOCAL_THING_RE.search(head)):
        # The person's own notes, files, mail or chats ("summarise my notes from this week"): "this
        # week" dates them, it does not make them news. Skills A/B on the v0.8 head (2026-11-13): that
        # ask got the web-off line, loop on and off.
        return False
    try:
        from core.execution_requirements import requirements_for

        need = requirements_for(value, source_context=source_context or {})
    except Exception:
        return False
    if need.user_material_supplied or not need.current_information_required or not need.external_evidence_required:
        return False
    return any(str(toolset).startswith("web") for toolset in (need.allowed_toolsets or ()))


__all__ = [
    "asks_about_this_computer_or_project",
    "asks_for_online_facts",
    "release_notes_are_authored",
    "requirements_want_a_lookup",
]
