"""Whether a request is about CURRENCY — the one authority every currency lane reads.

QA-050-026. Four live defects, one root cause: this runtime had no currency domain at all. Traced
on the untouched base 6a73d526 through the real seams (`core.task_router.classify`,
`core.market_intent.market_semantics_present`, `fast_live_info_price.price_assets_named`):

    "ok what is TRY?"                 chat_conversation  bare_def=True   market=False  assets=[]
    "i ment money TRY"                unknown            bare_def=False  market=False  assets=[]
    "1000 TRY to USD?"                unknown            bare_def=False  market=False  assets=[]
    "What is 500 kr worth in dollars?" research          bare_def=False  market=True   assets=[]

Read those four rows against what the tester actually saw and every symptom is accounted for:

* `TRY` resolves to nothing anywhere in the runtime, so "what is TRY?" reached a model with no
  grounding and the model asked for context — `TRY` is an English verb before it is an ISO code;
* "i ment money TRY" was claimed by NOTHING (`task_class=unknown`), so the only salient token left
  for the model was the bare word "money", and it answered with transaction-safety boilerplate.
  There is no runtime money gate — `core/vool_agent_brake.py` is x402 spend, which never fired.
  The safety text was the model filling a vacuum, and the fix is to stop leaving one;
* "1000 TRY to USD?" is an FX conversion and `market_semantics_present` returned **False**, so no
  grounding requirement ever attached and the model answered from parametric memory: ~$590-610,
  a stale invented rate. That False is the hallucination's mechanism, stated exactly;
* "500 kr worth in dollars" was the one that behaved better, and it did so BY ACCIDENT — it
  reached the market lane on the word "worth", which is in `MARKET_TERMS`. Nothing understood the
  `kr` ambiguity it appeared to handle. Change "worth" to "in" and that accident disappears.

The invariant this module exists to hold
-----------------------------------------

    A currency CODE has a stable definition, and this runtime may state it from local reference
    data. A currency RATE does not, and this runtime may never state one it was not given.

Those are two different questions and they get two different mechanisms. Definition is a lookup in
ISO 4217 — a published, closed, stable standard, the same class of reference data as
`_PRICE_ASSET_ALIASES`. Conversion is a live measurement, and this module CANNOT perform one: it
holds no rate table, and the only arithmetic it will do is against a rate the user typed or a live
source supplied. `tests/test_v050_currency_intent_fx_ambiguity.py` asserts that structurally by
scanning this module's own source for rate-shaped constants, so the ban cannot be undone quietly.

Why the ban needs a structural test rather than a promise
----------------------------------------------------------
A hard-coded rate is the single most attractive wrong fix here. It makes every test in the family
go green, it reads as "we handle FX now", and it is wrong the day after it lands and silently wrong
forever after. `1000 TRY to USD` needs the rate for a MOMENT, and no constant has a moment.

Not a phrase blacklist
-----------------------
Nothing here enumerates the sentences to reject. Currency reading is AFFIRMED from a bounded
positive vocabulary — ISO codes, currency names, symbols — and everything else falls through to the
ordinary lane. The transaction check (`currency_transaction_intent`) is likewise positive and
narrow: it asks for a real transfer VERB taking the money as its object. "money" is a topic word,
not an instruction, which is the whole of defect 2.

Homographs are the hard case, and they are handled by evidence, not by hope
----------------------------------------------------------------------------
Real ISO codes that are ordinary English words: TRY, ALL, CUP, MAD, SOS, TOP, BOB, GEL, BAM, RUB,
LAK, AMD, AWG. A bare lowercase "try" must never read as Turkish lira — "let me try USD mode" is
not a currency question. So a homograph code is admitted only on real evidence: the token was
written in CAPS the way a code is written, or the message carries explicit currency context
("money TRY", "TRY currency?"). Non-homograph codes (USD, SEK, DKK) need no such proof because
they are not words. That asymmetry is the rule, applied uniformly, and it is what the sloppy-variant
and near-miss tests measure.

Case is evidence, so the message is never uppercased (QA-050-027)
------------------------------------------------------------------
Three more live misses, all one mechanism. Measured on 03b04b39, before this landed:

    "what is  ALL?"                 definition=HIT  -> answered locally. Correct.
    "WHAT IS all?"                  definition=—    -> cloud Nemotron 550B, 1,512 tokens
    "what is meaning of try all?"   definition=—    -> qwen3:14b, 4,925 tokens, English-only answer
    "what is meaning of TRY?"       definition=—    -> the heavy lane, for a three-word definition

The obvious wrong fix is to uppercase the user's text and look everything up. That destroys the ONE
signal separating "what is ALL?" from "try all tests", and it is banned here by construction: the
message is preserved, `code_candidates` derives candidates from it with the typed case attached,
and `CurrencyEvidence.admits` uppercases a single candidate only once there is a reason to read it
as a code. The grounds are independent and each is checkable on its own:

* the token was typed the way a code is typed (unchanged);
* the message says money/currency/FX outright (unchanged);
* **this CHAT has already settled that same code as a currency** — code-specific, so a chat about
  TRY does not license reading a later bare "all" as the Albanian lek. `core/currency_chat_context.py`
  reads that from the chat's own answers and passes it in as `chat_codes`;
* a conversion framing word ("convert", "worth", "rate") ALONGSIDE a quantity or a code that is not
  an English word. Framing alone proves nothing — "is it worth trying all of them" is not FX.

And where the codes and the English reading BOTH fit — "what is meaning of try all?" one turn after
TRY and ALL were defined — neither reading is picked. `homograph_phrase_intent` claims the turn and
hands the choice back, locally, which is a real answer rather than 4,925 tokens spent on a guess.

This module knows nothing about routing on purpose. It cannot be made to answer anything; it only
describes what a message is asking for.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# =================================================================================================
# ISO 4217 reference data. Codes, names, symbols. DELIBERATELY NO RATES — see the module docstring
# and the structural guard in the test file. Adding a rate-shaped constant to this file is a defect
# the suite fails on by name.
# =================================================================================================


@dataclass(frozen=True)
class CurrencyFact:
    """One currency's stable identity. Everything here is true regardless of what day it is."""

    code: str
    name: str
    region: str
    symbol: str = ""
    #: Aliases that name THIS currency specifically ("turkish lira", "swedish krona").
    aliases: tuple[str, ...] = ()
    #: True when the ISO code is also an ordinary English word and therefore needs evidence.
    homograph: bool = False
    #: The everyday reading a homograph code collides with, stated so an answer can name it.
    homograph_sense: str = ""


_FACTS: tuple[CurrencyFact, ...] = (
    # --- the codes the reported defects and the QA family name, first ---
    CurrencyFact(
        "TRY", "Turkish lira", "Türkiye", "₺",
        ("turkish lira", "turkish liras", "turkey lira", "lira turca", "türk lirası", "turk lirasi"),
        homograph=True, homograph_sense="the English verb “try”",
    ),
    CurrencyFact("USD", "United States dollar", "United States", "$",
                 ("us dollar", "us dollars", "u.s. dollar", "u.s. dollars", "american dollar",
                  "american dollars", "united states dollar", "us$")),
    CurrencyFact("SEK", "Swedish krona", "Sweden", "kr",
                 ("swedish krona", "swedish kronor", "swedish crown", "sweden krona")),
    CurrencyFact("NOK", "Norwegian krone", "Norway", "kr",
                 ("norwegian krone", "norwegian kroner", "norway krone")),
    CurrencyFact("DKK", "Danish krone", "Denmark", "kr",
                 ("danish krone", "danish kroner", "denmark krone")),
    CurrencyFact("ISK", "Icelandic króna", "Iceland", "kr",
                 ("icelandic krona", "icelandic króna", "iceland krona")),
    CurrencyFact("CZK", "Czech koruna", "Czechia", "Kč",
                 ("czech koruna", "czech crown", "czech korun", "koruna", "czechia koruna")),
    CurrencyFact("SGD", "Singapore dollar", "Singapore", "S$",
                 ("singapore dollar", "singapore dollars", "singaporean dollar")),
    CurrencyFact("JPY", "Japanese yen", "Japan", "¥", ("japanese yen", "japan yen", "yen")),
    CurrencyFact("CNY", "Chinese yuan renminbi", "China", "¥",
                 ("chinese yuan", "china yuan", "yuan", "renminbi", "rmb")),
    # --- the rest of the majors and the widely-held currencies ---
    CurrencyFact("EUR", "euro", "eurozone", "€", ("euro", "euros")),
    CurrencyFact("GBP", "pound sterling", "United Kingdom", "£",
                 ("pound sterling", "british pound", "british pounds", "sterling", "uk pound")),
    CurrencyFact("CHF", "Swiss franc", "Switzerland", "CHF",
                 ("swiss franc", "swiss francs", "switzerland franc")),
    CurrencyFact("CAD", "Canadian dollar", "Canada", "C$",
                 ("canadian dollar", "canadian dollars", "canada dollar")),
    CurrencyFact("AUD", "Australian dollar", "Australia", "A$",
                 ("australian dollar", "australian dollars", "australia dollar")),
    CurrencyFact("NZD", "New Zealand dollar", "New Zealand", "NZ$",
                 ("new zealand dollar", "new zealand dollars", "kiwi dollar")),
    CurrencyFact("HKD", "Hong Kong dollar", "Hong Kong", "HK$",
                 ("hong kong dollar", "hong kong dollars")),
    CurrencyFact("TWD", "New Taiwan dollar", "Taiwan", "NT$",
                 ("taiwan dollar", "new taiwan dollar")),
    CurrencyFact("PLN", "Polish złoty", "Poland", "zł",
                 ("polish zloty", "polish złoty", "zloty", "złoty", "poland zloty")),
    CurrencyFact("HUF", "Hungarian forint", "Hungary", "Ft",
                 ("hungarian forint", "forint", "hungary forint")),
    CurrencyFact("RON", "Romanian leu", "Romania", "lei", ("romanian leu", "romania leu")),
    CurrencyFact("BGN", "Bulgarian lev", "Bulgaria", "лв", ("bulgarian lev", "bulgaria lev")),
    CurrencyFact("RUB", "Russian ruble", "Russia", "₽",
                 ("russian ruble", "russian rouble", "ruble", "rouble"),
                 homograph=True, homograph_sense="the English verb “rub”"),
    CurrencyFact("UAH", "Ukrainian hryvnia", "Ukraine", "₴", ("ukrainian hryvnia", "hryvnia")),
    CurrencyFact("INR", "Indian rupee", "India", "₹", ("indian rupee", "indian rupees", "rupee")),
    CurrencyFact("KRW", "South Korean won", "South Korea", "₩",
                 ("korean won", "south korean won", "won")),
    CurrencyFact("THB", "Thai baht", "Thailand", "฿", ("thai baht", "baht")),
    CurrencyFact("MYR", "Malaysian ringgit", "Malaysia", "RM", ("malaysian ringgit", "ringgit")),
    CurrencyFact("IDR", "Indonesian rupiah", "Indonesia", "Rp", ("indonesian rupiah", "rupiah")),
    CurrencyFact("PHP", "Philippine peso", "Philippines", "₱", ("philippine peso", "philippine pesos")),
    CurrencyFact("VND", "Vietnamese dong", "Vietnam", "₫", ("vietnamese dong", "dong")),
    CurrencyFact("MOP", "Macanese pataca", "Macau", "MOP$",
                 ("macanese pataca", "macau pataca", "pataca")),
    CurrencyFact("CRC", "Costa Rican colón", "Costa Rica", "₡",
                 ("costa rican colon", "costa rican colón", "colon")),
    CurrencyFact("VES", "Venezuelan bolívar", "Venezuela", "Bs.",
                 ("venezuelan bolivar", "venezuelan bolívar", "bolivar")),
    CurrencyFact("BRL", "Brazilian real", "Brazil", "R$", ("brazilian real", "brazilian reais")),
    CurrencyFact("MXN", "Mexican peso", "Mexico", "Mex$", ("mexican peso", "mexican pesos")),
    CurrencyFact("ARS", "Argentine peso", "Argentina", "$", ("argentine peso", "argentine pesos")),
    CurrencyFact("CLP", "Chilean peso", "Chile", "$", ("chilean peso", "chilean pesos")),
    CurrencyFact("COP", "Colombian peso", "Colombia", "$", ("colombian peso", "colombian pesos")),
    CurrencyFact("PEN", "Peruvian sol", "Peru", "S/", ("peruvian sol", "peru sol")),
    CurrencyFact("ZAR", "South African rand", "South Africa", "R", ("south african rand", "rand")),
    CurrencyFact("NGN", "Nigerian naira", "Nigeria", "₦", ("nigerian naira", "naira")),
    CurrencyFact("EGP", "Egyptian pound", "Egypt", "E£", ("egyptian pound", "egyptian pounds")),
    CurrencyFact("KES", "Kenyan shilling", "Kenya", "KSh", ("kenyan shilling", "kenya shilling")),
    CurrencyFact("MAD", "Moroccan dirham", "Morocco", "DH",
                 ("moroccan dirham", "morocco dirham"),
                 homograph=True, homograph_sense="the English adjective “mad”"),
    CurrencyFact("AED", "UAE dirham", "United Arab Emirates", "AED",
                 ("uae dirham", "emirati dirham", "dubai dirham")),
    CurrencyFact("SAR", "Saudi riyal", "Saudi Arabia", "SR", ("saudi riyal", "saudi arabian riyal")),
    CurrencyFact("IRR", "Iranian rial", "Iran", "﷼", ("iranian rial", "iran rial")),
    CurrencyFact("QAR", "Qatari riyal", "Qatar", "QR", ("qatari riyal", "qatar riyal")),
    CurrencyFact("ILS", "Israeli new shekel", "Israel", "₪", ("israeli shekel", "shekel", "new shekel")),
    CurrencyFact("PKR", "Pakistani rupee", "Pakistan", "₨", ("pakistani rupee", "pakistan rupee")),
    CurrencyFact("BDT", "Bangladeshi taka", "Bangladesh", "৳", ("bangladeshi taka", "taka")),
    CurrencyFact("LKR", "Sri Lankan rupee", "Sri Lanka", "Rs", ("sri lankan rupee",)),
    CurrencyFact("KZT", "Kazakhstani tenge", "Kazakhstan", "₸", ("kazakhstani tenge", "tenge")),
    CurrencyFact("AZN", "Azerbaijani manat", "Azerbaijan", "₼", ("azerbaijani manat", "manat")),
    CurrencyFact("GHS", "Ghanaian cedi", "Ghana", "GH₵", ("ghanaian cedi", "cedi")),
    CurrencyFact(
        "XAF",
        "Central African CFA franc",
        "CEMAC member states",
        "FCFA",
        (
            "central african cfa franc",
            "central african cfa francs",
            "cfa franc beac",
            "cfa francs beac",
        ),
    ),
    CurrencyFact("NPR", "Nepalese rupee", "Nepal", "Rs", ("nepalese rupee", "nepali rupee")),
    CurrencyFact("MMK", "Burmese kyat", "Myanmar", "K", ("burmese kyat", "myanmar kyat", "kyat")),
    CurrencyFact("KHR", "Cambodian riel", "Cambodia", "៛", ("cambodian riel", "riel")),
    # --- the remaining English-word homographs, listed so a bare one is never silently a currency ---
    CurrencyFact("ALL", "Albanian lek", "Albania", "L", ("albanian lek", "lek"),
                 homograph=True, homograph_sense="the English word “all”"),
    CurrencyFact("CUP", "Cuban peso", "Cuba", "$", ("cuban peso", "cuban pesos"),
                 homograph=True, homograph_sense="the English noun “cup”"),
    CurrencyFact("SOS", "Somali shilling", "Somalia", "Sh", ("somali shilling",),
                 homograph=True, homograph_sense="the distress signal “SOS”"),
    CurrencyFact("TOP", "Tongan paʻanga", "Tonga", "T$", ("tongan paanga", "paanga", "pa'anga"),
                 homograph=True, homograph_sense="the English word “top”"),
    CurrencyFact("BOB", "Bolivian boliviano", "Bolivia", "Bs", ("bolivian boliviano", "boliviano"),
                 homograph=True, homograph_sense="the given name “Bob”"),
    CurrencyFact("GEL", "Georgian lari", "Georgia", "₾", ("georgian lari", "lari"),
                 homograph=True, homograph_sense="the English noun “gel”"),
    CurrencyFact("BAM", "Bosnia-Herzegovina convertible mark", "Bosnia and Herzegovina", "KM",
                 ("bosnian mark", "convertible mark"),
                 homograph=True, homograph_sense="the English interjection “bam”"),
    CurrencyFact("LAK", "Lao kip", "Laos", "₭", ("lao kip", "laotian kip", "kip"),
                 homograph=True, homograph_sense="the English noun “lak”"),
    CurrencyFact("AMD", "Armenian dram", "Armenia", "֏", ("armenian dram", "dram"),
                 homograph=True, homograph_sense="the chip maker AMD"),
    CurrencyFact("AWG", "Aruban florin", "Aruba", "ƒ", ("aruban florin",),
                 homograph=True, homograph_sense="an initialism, not a word"),
)

ISO_4217: dict[str, CurrencyFact] = {fact.code: fact for fact in _FACTS}

#: Reverse index: a currency NAME or spelled-out alias -> its code. Longest-first at match time.
_NAME_TO_CODE: dict[str, str] = {}
for _fact in _FACTS:
    _NAME_TO_CODE[_fact.name.lower()] = _fact.code
    for _alias in _fact.aliases:
        _NAME_TO_CODE.setdefault(_alias.lower(), _fact.code)

# =================================================================================================
# Symbol ambiguity. A symbol names a FAMILY, not a currency, and this is the table that says so.
# =================================================================================================

#: symbol -> the codes it can mean, most commonly intended first. A single-entry tuple is a symbol
#: that is NOT ambiguous, which is exactly the Kč-vs-kr distinction the QA family asks for.
SYMBOL_FAMILIES: dict[str, tuple[str, ...]] = {
    "kr": ("SEK", "NOK", "DKK", "ISK"),
    "kr.": ("DKK", "NOK", "SEK", "ISK"),
    "kč": ("CZK",),
    "kc": ("CZK",),
    "$": ("USD", "CAD", "AUD", "SGD", "NZD", "HKD", "MXN", "ARS", "CLP", "COP", "CUP"),
    "¥": ("JPY", "CNY"),
    "£": ("GBP", "EGP"),
    "€": ("EUR",),
    "₺": ("TRY",),
    "₹": ("INR",),
    "₩": ("KRW",),
    "₽": ("RUB",),
    "zł": ("PLN",),
    "r$": ("BRL",),
    "s$": ("SGD",),
    "c$": ("CAD",),
    "a$": ("AUD",),
    "chf": ("CHF",),
    "₪": ("ILS",),
    "฿": ("THB",),
    "₴": ("UAH",),
    "₦": ("NGN",),
    "₱": ("PHP",),
    "₫": ("VND",),
}

#: Spelled-out unit words that name a family rather than one currency. "dollars" is the live case:
#: "convert 1000 TRY into dollars" almost always means USD, and "almost always" is a thing to STATE,
#: not a thing to assume silently.
UNIT_WORD_FAMILIES: dict[str, tuple[str, ...]] = {
    "dollar": SYMBOL_FAMILIES["$"],
    "dollars": SYMBOL_FAMILIES["$"],
    "bucks": ("USD",),
    "krona": ("SEK", "ISK"),
    "kronor": ("SEK",),
    "krone": ("NOK", "DKK"),
    "kroner": ("NOK", "DKK"),
    "yen": ("JPY",),
    "yuan": ("CNY",),
    "pounds": ("GBP", "EGP"),
    "pound": ("GBP", "EGP"),
    "euros": ("EUR",),
    "euro": ("EUR",),
    "lira": ("TRY",),
    "peso": ("MXN", "ARS", "CLP", "COP", "PHP", "CUP"),
    "pesos": ("MXN", "ARS", "CLP", "COP", "PHP", "CUP"),
    "rupees": ("INR", "PKR", "LKR", "NPR"),
    "rupee": ("INR", "PKR", "LKR", "NPR"),
    "francs": ("CHF",),
    "franc": ("CHF",),
    "shekels": ("ILS",),
    # "nis" is the code Israelis actually type for the shekel (the ISO code is
    # ILS; NIS is the legacy/common initialism). Measured live: "how much is
    # 1000 nis in eur" resolved no currency and fell through to the model.
    "nis": ("ILS",),
    "niss": ("ILS",),
    "rand": ("ZAR",),
    "zloty": ("PLN",),
    "koruna": ("CZK",),
    "forint": ("HUF",),
}

#: A country/place name is enough to pin a currency when the user names a PLACE instead of a unit
#: ("$300 in Singapore"). Only unambiguous single-currency places are listed.
PLACE_TO_CODE: dict[str, str] = {
    "the netherlands": "EUR", "germany": "EUR", "france": "EUR", "spain": "EUR", "italy": "EUR",
    # Every euro-area member resolves as a comma qualifier. "Lisbon, Portugal" read as NO currency
    # while bare "lisbon" was known: the qualifier is negative evidence against the bare-city
    # fallback (a right rule), so a member missing here unresolved the whole place and dropped the
    # travel story out of the typed contract (measured 2026-09-07). Same for the UK's constituent
    # countries and the UAE.
    "netherlands": "EUR", "portugal": "EUR", "belgium": "EUR", "ireland": "EUR", "finland": "EUR",
    "greece": "EUR", "slovakia": "EUR", "slovenia": "EUR", "estonia": "EUR", "latvia": "EUR",
    "lithuania": "EUR", "malta": "EUR", "cyprus": "EUR", "croatia": "EUR",
    "england": "GBP", "scotland": "GBP", "wales": "GBP", "northern ireland": "GBP",
    "united arab emirates": "AED", "uae": "AED", "the uae": "AED", "emirates": "AED",
    "singapore": "SGD", "japan": "JPY", "china": "CNY", "turkey": "TRY", "türkiye": "TRY",
    "sweden": "SEK", "norway": "NOK", "denmark": "DKK", "iceland": "ISK", "czechia": "CZK",
    "czech republic": "CZK", "poland": "PLN", "hungary": "HUF", "switzerland": "CHF",
    "canada": "CAD", "australia": "AUD", "new zealand": "NZD", "hong kong": "HKD",
    "india": "INR", "south korea": "KRW", "korea": "KRW", "thailand": "THB", "vietnam": "VND",
    "indonesia": "IDR", "malaysia": "MYR", "philippines": "PHP", "brazil": "BRL",
    "mexico": "MXN", "south africa": "ZAR", "nigeria": "NGN", "egypt": "EGP", "kenya": "KES",
    "morocco": "MAD", "israel": "ILS", "pakistan": "PKR", "bangladesh": "BDT", "taiwan": "TWD",
    "the uk": "GBP", "britain": "GBP", "the us": "USD", "the usa": "USD", "america": "USD",
    "united kingdom": "GBP", "uk": "GBP", "united states": "USD", "usa": "USD",
    # Bare "US" as a money word ("I have 100 US, convert to RUB"). Safe despite the pronoun
    # homograph: a conversion frame only fires when BOTH sides resolve, so "give 100 us to mom"
    # dies on the target, and "us" outside an amount+pair grammar is never consulted at all.
    "us": "USD",
    "puerto rico": "USD", "costa rica": "CRC", "venezuela": "VES", "bolivia": "BOB",
    "chile": "CLP", "colombia": "COP", "austria": "EUR", "bulgaria": "EUR", "romania": "RON",
    # XAF is one ISO currency shared by the six CEMAC member states.  These are issuer bindings,
    # not exchange-rate data: they are exactly the stable jurisdiction membership this registry
    # exists to carry.
    "cameroon": "XAF", "gabon": "XAF", "central african republic": "XAF", "chad": "XAF",
    "republic of the congo": "XAF", "congo-brazzaville": "XAF", "equatorial guinea": "XAF",
}

#: A CITY is the form a location arrives in when someone corrects an ambiguous symbol — "the kr is
#: in Copenhagen", "the ¥ is in Shanghai". Copenhagen is the whole of what makes that kr Danish and
#: Shanghai the whole of what makes that ¥ a yuan, and neither city appeared anywhere in this
#: runtime before QA-050-027, which is why the reported answer kept saying "Copenhagen (NOK)" and
#: "Shanghai (JPY)" after being told. Same class of reference data as PLACE_TO_CODE above: a city
#: is listed only when the country it sits in issues exactly one currency.
CITY_TO_CODE: dict[str, str] = {
    "copenhagen": "DKK", "københavn": "DKK", "aarhus": "DKK", "odense": "DKK",
    "oslo": "NOK", "bergen": "NOK", "trondheim": "NOK", "stavanger": "NOK",
    "stockholm": "SEK", "gothenburg": "SEK", "göteborg": "SEK", "malmö": "SEK", "malmo": "SEK",
    "reykjavik": "ISK", "reykjavík": "ISK",
    "shanghai": "CNY", "beijing": "CNY", "peking": "CNY", "shenzhen": "CNY", "guangzhou": "CNY",
    "chengdu": "CNY", "hangzhou": "CNY",
    "tokyo": "JPY", "osaka": "JPY", "kyoto": "JPY", "yokohama": "JPY", "nagoya": "JPY",
    "sapporo": "JPY", "fukuoka": "JPY",
    "london": "GBP", "manchester": "GBP", "edinburgh": "GBP", "glasgow": "GBP",
    "new york": "USD", "san francisco": "USD", "chicago": "USD", "los angeles": "USD",
    "boston": "USD", "seattle": "USD", "miami": "USD", "washington dc": "USD",
    "toronto": "CAD", "vancouver": "CAD", "montreal": "CAD", "ottawa": "CAD",
    "sydney": "AUD", "melbourne": "AUD", "brisbane": "AUD", "perth": "AUD",
    "auckland": "NZD", "wellington": "NZD",
    "zurich": "CHF", "zürich": "CHF", "geneva": "CHF", "bern": "CHF", "basel": "CHF",
    "prague": "CZK", "brno": "CZK",
    "warsaw": "PLN", "krakow": "PLN", "kraków": "PLN", "gdansk": "PLN",
    "budapest": "HUF", "bucharest": "RON", "sofia": "EUR",
    "istanbul": "TRY", "ankara": "TRY", "izmir": "TRY",
    "moscow": "RUB", "st petersburg": "RUB", "kyiv": "UAH", "kiev": "UAH",
    "mumbai": "INR", "delhi": "INR", "new delhi": "INR", "bangalore": "INR", "bengaluru": "INR",
    "chennai": "INR", "kolkata": "INR", "hyderabad": "INR",
    "seoul": "KRW", "busan": "KRW",
    "bangkok": "THB", "chiang mai": "THB", "phuket": "THB",
    "hanoi": "VND", "ho chi minh city": "VND", "saigon": "VND",
    "jakarta": "IDR", "bali": "IDR", "denpasar": "IDR",
    "kuala lumpur": "MYR", "penang": "MYR",
    "manila": "PHP", "cebu": "PHP",
    "taipei": "TWD", "kaohsiung": "TWD",
    "singapore city": "SGD", "hong kong island": "HKD", "kowloon": "HKD",
    "são paulo": "BRL", "sao paulo": "BRL", "rio de janeiro": "BRL", "brasilia": "BRL",
    "mexico city": "MXN", "guadalajara": "MXN", "monterrey": "MXN",
    "buenos aires": "ARS", "santiago": "CLP", "bogota": "COP", "bogotá": "COP", "lima": "PEN",
    "johannesburg": "ZAR", "cape town": "ZAR", "pretoria": "ZAR", "durban": "ZAR",
    "lagos": "NGN", "abuja": "NGN", "cairo": "EGP", "alexandria": "EGP",
    "douala": "XAF", "yaounde": "XAF", "yaoundé": "XAF", "libreville": "XAF",
    "bangui": "XAF", "n'djamena": "XAF", "ndjamena": "XAF", "brazzaville": "XAF",
    "malabo": "XAF",
    "nairobi": "KES", "casablanca": "MAD", "marrakech": "MAD", "rabat": "MAD",
    "dubai": "AED", "abu dhabi": "AED", "riyadh": "SAR", "jeddah": "SAR", "doha": "QAR",
    "tel aviv": "ILS", "jerusalem": "ILS",
    "karachi": "PKR", "lahore": "PKR", "islamabad": "PKR", "dhaka": "BDT", "colombo": "LKR",
    "kathmandu": "NPR", "almaty": "KZT", "astana": "KZT", "baku": "AZN", "tbilisi": "GEL",
    "amsterdam": "EUR", "rotterdam": "EUR", "berlin": "EUR", "munich": "EUR", "hamburg": "EUR",
    "frankfurt": "EUR", "cologne": "EUR", "paris": "EUR", "lyon": "EUR", "marseille": "EUR",
    "madrid": "EUR", "barcelona": "EUR", "valencia": "EUR", "seville": "EUR",
    "rome": "EUR", "milan": "EUR", "naples": "EUR", "turin": "EUR", "florence": "EUR",
    "lisbon": "EUR", "porto": "EUR", "dublin": "EUR", "vienna": "EUR", "brussels": "EUR",
    "helsinki": "EUR", "athens": "EUR", "tallinn": "EUR", "riga": "EUR", "vilnius": "EUR",
    "ljubljana": "EUR", "bratislava": "EUR", "zagreb": "EUR", "luxembourg": "EUR",
}

#: A NATIONALITY adjective is the other way a correction arrives — "the kr is Danish". It states
#: the issuing country outright, so it pins on the same footing as a city or a country name.
NATIONALITY_TO_CODE: dict[str, str] = {
    "danish": "DKK", "norwegian": "NOK", "swedish": "SEK", "icelandic": "ISK",
    "chinese": "CNY", "japanese": "JPY", "american": "USD", "canadian": "CAD",
    "australian": "AUD", "singaporean": "SGD", "british": "GBP", "swiss": "CHF",
    "czech": "CZK", "polish": "PLN", "hungarian": "HUF", "turkish": "TRY", "indian": "INR",
    "pakistani": "PKR", "mexican": "MXN", "brazilian": "BRL", "egyptian": "EGP",
    "korean": "KRW", "thai": "THB", "vietnamese": "VND", "indonesian": "IDR",
    "malaysian": "MYR", "philippine": "PHP", "taiwanese": "TWD",
    "new zealand": "NZD", "hong kong": "HKD", "south african": "ZAR", "nigerian": "NGN",
    "cameroonian": "XAF", "gabonese": "XAF", "chadian": "XAF", "equatoguinean": "XAF",
}

#: Every literal that names an issuing country, in any of the three forms above. One table so a
#: caller asks "what currency does this place name" once, and longest-first so "new york" beats
#: nothing and "hong kong" is not read as two unrelated words.
LOCATION_TO_CODE: dict[str, str] = {**PLACE_TO_CODE, **CITY_TO_CODE, **NATIONALITY_TO_CODE}

# =================================================================================================
# Location bindings — the rule that lets "Copenhagen" pin a kr WITHOUT letting "Singapore" pin a $.
# =================================================================================================
#
# `core/conductor/shared_context.py` already refuses to let a mentioned country resolve a unit, and
# it is right to: "then spent $300 in Singapore" does not make that dollar Singaporean, because
# travellers carry their own money and resolving it there would invent the exchange the rest of the
# answer is computed from. But "the kr is in Copenhagen" is not that sentence. It is a statement
# ABOUT the unit, and refusing it is how the reported answer stayed on NOK after being corrected.
#
# The discriminator is structural and has nothing to do with which place it is: a unit written with
# an AMOUNT attached ("$300", "500 kr") is money being spent somewhere; a unit written BARE next to
# a place ("the kr is in Copenhagen", "kr in Copenhagen") is the user saying which currency it is.
# One rule, applied to every unit and every place, and it separates the two live sentences exactly.

#: Units worth binding: the ones a location could actually disambiguate. A token that names one
#: currency already needs no help, and admitting it here would let a place override an ISO code.
_BINDABLE_UNITS: tuple[str, ...] = tuple(
    sorted(
        {token for token, family in SYMBOL_FAMILIES.items() if len(family) > 1}
        | {token for token, family in UNIT_WORD_FAMILIES.items() if len(family) > 1},
        key=len,
        reverse=True,
    )
)

_BINDABLE_UNIT_RE = re.compile(
    "|".join(
        (rf"\b{re.escape(token)}\b" if token[:1].isalpha() else re.escape(token))
        for token in _BINDABLE_UNITS
    )
)

_LOCATION_RE = re.compile(
    r"(?<![a-z])(?:"
    + "|".join(re.escape(name) for name in sorted(LOCATION_TO_CODE, key=len, reverse=True))
    + r")(?![a-z])"
)

#: Where one statement ends and the next begins. "the kr is in Copenhagen and the ¥ is in Shanghai"
#: is two statements; without the split, both places sit in one clause with both units and neither
#: binds. Numbers are split on their thousands commas too, which is harmless: a unit with an amount
#: attached is not bindable in the first place.
_CLAUSE_SPLIT_RE = re.compile(r"[.;:!?\n]|,|\band\b|\bwhile\b|\bwhereas\b|\bbut\b|\bthen\b")


@dataclass(frozen=True)
class LocationBinding:
    """A place the message ties to an ambiguous unit, and whether the two agree.

    `agrees` False is not a silent no-op. "the kr is in Tokyo" names a place whose currency is not
    in the kr family at all, and reporting that contradiction is both more useful and more accurate
    than either picking JPY or dropping the sentence on the floor.
    """

    unit: str
    place: str
    code: str
    candidates: tuple[str, ...]
    agrees: bool

    @property
    def pins(self) -> bool:
        return self.agrees


def _unit_family(token: str) -> tuple[str, ...]:
    return SYMBOL_FAMILIES.get(token) or UNIT_WORD_FAMILIES.get(token) or ()


def _has_amount_attached(clause: str, start: int, end: int) -> bool:
    """Whether the unit at [start, end) was written on an amount rather than on its own."""

    before = clause[:start].rstrip()
    after = clause[end:].lstrip()
    return bool(before[-1:].isdigit() or after[:1].isdigit())


def location_bindings(text: str) -> tuple[LocationBinding, ...]:
    """Every place this message ties to a bare ambiguous unit, in the order written.

    Deterministic, consults nothing, and returns an empty tuple for a message that merely happens
    to mention a country. That empty case is the one the traveller story depends on.
    """

    lowered = _flat(text)
    if not lowered:
        return ()
    bindings: list[LocationBinding] = []
    seen: set[tuple[str, str]] = set()
    for clause in _CLAUSE_SPLIT_RE.split(lowered):
        units = {
            match.group(0)
            for match in _BINDABLE_UNIT_RE.finditer(clause)
            if not _has_amount_attached(clause, match.start(), match.end())
        }
        if len(units) != 1:
            continue
        unit = units.pop()
        codes = {LOCATION_TO_CODE[m.group(0)]: m.group(0) for m in _LOCATION_RE.finditer(clause)}
        if len(codes) != 1:
            # No place, or two places that name different currencies. Both are "not determined".
            continue
        code, place = next(iter(codes.items()))
        family = _unit_family(unit)
        key = (unit, code)
        if key in seen:
            continue
        seen.add(key)
        bindings.append(
            LocationBinding(
                unit=unit,
                place=place,
                code=code,
                candidates=family,
                agrees=code in family,
            )
        )
    return tuple(bindings)


def resolve_currency_literal(literal: str, *, raw: str = "") -> CurrencyReference | None:
    """Public entry to the one literal resolver. Every currency lane reads this, not its own copy."""

    return _resolve_literal(literal, raw=raw or str(literal or ""))


# =================================================================================================
# Transaction intent — the negative controls. A real transfer VERB taking money as its object.
# =================================================================================================

#: Verbs that MOVE money. "money"/"currency"/"cash" are topic nouns and appear nowhere in this
#: tuple, because a topic word is not an instruction — that is defect 2 in one line.
_TRANSACTION_VERBS: tuple[str, ...] = (
    "send", "sending", "pay", "paying", "payment", "transfer", "transferring", "transferred",
    "wire", "wiring", "remit", "remitting", "withdraw", "withdrawing", "withdrawal",
    "deposit", "depositing", "buy", "buying", "purchase", "purchasing", "sell", "selling",
    "swap", "swapping", "exchange for me", "top up", "top-up", "cash out", "checkout",
)
#: Nouns that make a turn about MOVING money even without one of the verbs above.
_TRANSACTION_NOUNS: tuple[str, ...] = (
    "wallet", "my account", "bank account", "iban", "routing number", "card number",
    "invoice", "checkout",
)
_TRANSACTION_VERB_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in _TRANSACTION_VERBS) + r")\b"
)
_TRANSACTION_NOUN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(n) for n in _TRANSACTION_NOUNS) + r")\b"
)

#: Framings that talk ABOUT a transaction rather than requesting one. "how do i send money" is a
#: question about a process; "send 1000 TRY" is an instruction. The distinction is a real one and
#: this is where it lives.
_TALKED_ABOUT_TRANSACTION_RE = re.compile(
    r"\b(?:how (?:do|does|would|can) (?:i|you|one|we)|what (?:is|are)|explain|describe|"
    r"difference between|meaning of|why (?:do|does|is))\b"
)

#: Compound NOUNS that contain a transfer verb and instruct nothing. "purchasing power" is an
#: economic measurement, and reading its first word as an order to buy is how the whole comparison
#: family — the QA-050-027 prompt included — was refused by this gate before it could be answered.
#: A verb inside a noun phrase is not a verb, and this is where that is stated.
_TOPIC_COMPOUND_RE = re.compile(
    r"\b(?:purchasing|buying|purchase|spending|selling|buy(?:er|ers)?|sell(?:er|ers)?)[\s-]+"
    r"(?:power|parity|pressure|habits?|behaviou?rs?|patterns?|decisions?|intent|side|price)\b"
    r"|\bpurchasing power parity\b|\bpoint of sale\b"
)


#: What a transfer verb has to be acting ON. This is the "taking the money as its object" half of
#: the rule stated above, which the first implementation described and never enforced: it matched a
#: verb anywhere and stopped. "in what they actually buy" and "which of these buys more" then read
#: as purchase instructions, and the whole comparison family was refused before it could be read.
_MONEY_OBJECT_RE = re.compile(
    r"\d"
    r"|\b(?:money|cash|funds?|balance|invoice|bill|salary|rent|amount|savings|"
    r"crypto|coins?|tokens?|shares?)\b"
    r"|[$€£¥₹₺₩₽₪฿₴₦₱₫]"
)
#: How far past the verb its object may sit. "top up my wallet with 100 USD" needs the room;
#: anything longer stops being the verb's object and starts being a different sentence.
_OBJECT_WINDOW = 30


def _transfer_verb_takes_money(lowered: str) -> bool:
    """Whether any transfer verb in `lowered` actually has money as its object."""

    for match in _TRANSACTION_VERB_RE.finditer(lowered):
        tail = lowered[match.end() : match.end() + _OBJECT_WINDOW]
        if _MONEY_OBJECT_RE.search(tail):
            return True
        if _TRANSACTION_NOUN_RE.search(tail):
            return True
        if codes_named(tail) or names_named(tail):
            return True
        if any(
            re.search(rf"\b{re.escape(word)}\b", tail)
            for word in UNIT_WORD_FAMILIES
        ):
            return True
    return False


def currency_transaction_intent(text: str) -> bool:
    """True when the user asks to MOVE money, not merely to name or convert it.

    This is the gate that must stay shut for every definition, every conversion and every
    comparison in the QA family, and open for every negative control. It is positive and narrow on
    purpose: it needs a transfer verb or an account noun. The word "money" alone can never open it,
    and neither can a transfer verb that is part of a topic noun.
    """

    lowered = _TOPIC_COMPOUND_RE.sub(" ", _flat(text))
    if not lowered:
        return False
    has_verb = _transfer_verb_takes_money(lowered)
    has_noun = bool(_TRANSACTION_NOUN_RE.search(lowered))
    if not (has_verb or has_noun):
        return False
    # "how do i send money abroad" is a question about sending, not a request to send.
    return not _TALKED_ABOUT_TRANSACTION_RE.search(lowered)


# =================================================================================================
# Parsing helpers
# =================================================================================================

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[?!.,;:]+")


def _flat(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "").strip().lower()).strip()


def _padded(text: str) -> str:
    return f" {_PUNCT_RE.sub(' ', _flat(text))} ".replace("  ", " ")


#: Explicit currency context. Any of these present means the user has TOLD us the domain is money,
#: which is what promotes a homograph code (and what "i ment money TRY" supplies).
_CURRENCY_CONTEXT_RE = re.compile(
    r"\b(?:currenc(?:y|ies)|money|cash|fx|forex|foreign exchange|exchange rate|"
    r"iso ?4217|banknote|legal tender)\b"
)


def has_currency_context(text: str) -> bool:
    """Whether the message states outright that it is about money."""

    return bool(_CURRENCY_CONTEXT_RE.search(_flat(text)))


#: Weaker, framing-only evidence. On its own none of these says anything about money — code gets
#: converted, an idea is worth having, a heart rate is a rate — so a framing word counts only
#: ALONGSIDE a quantity or a code that is not an English word. That pairing is the whole of the
#: difference between "convert 1000 try to usd" and "is it worth trying all of them".
_EXCHANGE_FRAMING_RE = re.compile(
    r"\b(?:convert|converts|converted|converting|conversion|exchange|exchanges|exchanged|"
    r"worth|rate|rates|equivalent|equals)\b"
)

_QUANTITY_RE = re.compile(r"\d")


@dataclass(frozen=True)
class CodeCandidate:
    """A three-letter token that COULD be an ISO code, kept exactly as the user typed it.

    The case a user chose is evidence, so nothing in this module ever uppercases a MESSAGE. `code`
    uppercases the one token, for the lookup, and only after `CurrencyEvidence.admits` has said
    there is a reason to read it as a code at all. `written` keeps the original alongside it so the
    two can never be confused.
    """

    written: str
    start: int = 0

    @property
    def code(self) -> str:
        """The token uppercased FOR LOOKUP. Meaningful only once the candidate is admitted."""

        return self.written.upper()

    @property
    def fact(self) -> CurrencyFact | None:
        return ISO_4217.get(self.code)

    @property
    def written_as_code(self) -> bool:
        """True when the token was typed the way a code is typed — as a standalone ALL-CAPS word."""

        return self.written.isupper()


def code_candidates(text: str) -> tuple[CodeCandidate, ...]:
    """Every candidate the message contains, in order, admitted or not.

    Recognition, never judgement: this says what COULD be a code, and `CurrencyEvidence` says which
    of them may be read as one. Keeping the two apart is what makes the rule statable and testable
    rather than buried inside a resolver.
    """

    found: list[CodeCandidate] = []
    for match in re.finditer(r"\b[A-Za-z]{3}\b", str(text or "")):
        candidate = CodeCandidate(written=match.group(0), start=match.start())
        if candidate.fact is not None:
            found.append(candidate)
    return tuple(found)


@dataclass(frozen=True)
class CurrencyEvidence:
    """What a turn — and the chat around it — actually proves about the domain being money.

    A NON-homograph code (USD, SEK, DKK) needs none of this: it is not an English word, so there is
    nothing for it to collide with. A homograph code (TRY, ALL, BOB) is admitted only against one
    of the independent grounds below, and nothing here enumerates sentences to reject.
    """

    #: The message says money/currency/FX outright.
    explicit: bool = False
    #: Codes typed the way a code is typed, read from the RAW text before any lowercasing.
    written_as_code: frozenset[str] = frozenset()
    #: Codes THIS CHAT has already settled as currency. Code-specific on purpose: a chat that
    #: defined TRY does not license reading a later bare "all" as the Albanian lek.
    chat_codes: frozenset[str] = frozenset()
    #: A conversion/valuation framing word is present — weak, and never sufficient alone.
    exchange_framing: bool = False
    #: A digit is present, so the framing word plausibly governs an amount.
    quantity: bool = False
    #: Codes in the same message that are not English words, which make the framing concrete.
    plain_codes: frozenset[str] = frozenset()

    def admits(self, candidate: CodeCandidate, *, paired_with_settled_currency: bool = False) -> bool:
        """Whether this candidate may be read as a currency code.

        `paired_with_settled_currency` is the one ground that is a property of the PAIR rather than
        of the message: "1000 all to usd" carries no currency word at all, and the evidence is that
        a quantity governs a conversion whose other side is USD. It is passed in by the conversion
        lane, which is the only caller that can know it.
        """

        fact = candidate.fact
        if fact is None:
            return False
        if not fact.homograph:
            return True
        code = candidate.code
        if code in self.written_as_code or code in self.chat_codes or self.explicit:
            return True
        if self.exchange_framing and (self.quantity or bool(self.plain_codes)):
            return True
        return paired_with_settled_currency and self.quantity


def currency_evidence(text: str, *, chat_codes: Sequence[str] = ()) -> CurrencyEvidence:
    """Read every ground for admitting a homograph out of the message and the chat around it."""

    raw = str(text or "")
    candidates = code_candidates(raw)
    return CurrencyEvidence(
        explicit=has_currency_context(raw),
        written_as_code=frozenset(item.code for item in candidates if item.written_as_code),
        chat_codes=frozenset(
            str(code or "").strip().upper() for code in chat_codes if str(code or "").strip()
        ),
        exchange_framing=bool(_EXCHANGE_FRAMING_RE.search(_flat(raw))),
        quantity=bool(_QUANTITY_RE.search(raw)),
        plain_codes=frozenset(
            item.code for item in candidates if item.fact is not None and not item.fact.homograph
        ),
    )


def codes_named(text: str, *, chat_codes: Sequence[str] = ()) -> list[str]:
    """Every ISO code the message names, in order, with homographs held to the evidence rule."""

    evidence = currency_evidence(text, chat_codes=chat_codes)
    ordered: list[str] = []
    for candidate in code_candidates(text):
        if not evidence.admits(candidate):
            continue
        if candidate.code not in ordered:
            ordered.append(candidate.code)
    return ordered


def names_named(text: str) -> list[str]:
    """Codes the message names by spelled-out NAME ("Turkish lira", "Czech koruna")."""

    padded = _padded(text)
    hits: list[tuple[int, str]] = []
    claimed: list[tuple[int, int]] = []
    for alias in sorted(_NAME_TO_CODE, key=len, reverse=True):
        start = padded.find(f" {alias} ")
        if start < 0:
            continue
        end = start + len(alias) + 2
        if any(start >= s and end <= e for s, e in claimed):
            continue
        claimed.append((start, end))
        hits.append((start, _NAME_TO_CODE[alias]))
    ordered: list[str] = []
    for _position, code in sorted(hits):
        if code not in ordered:
            ordered.append(code)
    return ordered


@dataclass(frozen=True)
class CurrencyReference:
    """One currency the user referred to, and how sure we can be about which one they meant."""

    #: The code when it is pinned, else "".
    code: str
    #: The literal the user typed ("TRY", "kr", "$", "dollars", "Turkish lira").
    written: str
    #: Every code the literal could mean. Length > 1 is a real ambiguity to state.
    candidates: tuple[str, ...] = ()
    #: Where the reading came from, so the answer can name its own basis.
    basis: str = ""

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1

    @property
    def fact(self) -> CurrencyFact | None:
        return ISO_4217.get(self.code)

    def describe(self) -> str:
        fact = self.fact
        if fact is None:
            return self.written
        return f"{fact.code} ({fact.name})"


# =================================================================================================
# Definition intent — "what is TRY?", "TRY currency?", "Turkish lira code?", "what is Kč?"
# =================================================================================================

_DEFINITION_FRAMES: tuple[re.Pattern[str], ...] = (
    # "what is TRY", "whats TRY", "what does TRY mean", "what is TRY money"
    re.compile(r"^(?:what(?:'?s| is| are| does)?)\s+(?P<term>.+?)(?:\s+(?:mean|stand for|money|currency))?$"),
    # "TRY currency?", "TRY money?", "TRY code?"
    re.compile(r"^(?P<term>.+?)\s+(?:currency|money|code|iso code)$"),
    # "define TRY", "meaning of TRY"
    re.compile(r"^(?:define|meaning of|definition of)\s+(?P<term>.+?)$"),
    # "i meant money TRY", "i mean currency TRY" — the correction shape from defect 2
    re.compile(r"^(?:i\s+(?:me(?:a|)nt|mean|ment)\s+)(?:the\s+)?(?:money|currency|cash)\s+(?P<term>.+?)$"),
    re.compile(r"^(?:i\s+(?:me(?:a|)nt|mean|ment)\s+)(?P<term>.+?)\s+(?:money|currency|cash)$"),
    # "Turkish lira code?" is caught by frame 2; "code for Turkish lira" by this one.
    re.compile(r"^(?:(?:the\s+)?(?:iso\s+)?code\s+for)\s+(?P<term>.+?)$"),
)

#: Conversational openers a definition can carry and still be a bare definition.
_OPENER_RE = re.compile(
    r"^(?:ok(?:ay)?|so|hey|hi|hello|well|and|but|um+|uh+|right|yo|please|pls|q|quick q)\b[\s,:-]*"
)

#: Determiners that belong to the frame rather than to the term being defined.
_DETERMINER_RE = re.compile(r"^(?:the|a|an|this|that)\b\s*")

#: "what is MEANING OF try all?" asks the same question as "what is try all?". The wrapper belongs
#: to the frame, not to the term, and leaving it attached is what made a three-word definition
#: ("what is meaning of TRY?") fall past this lane into the heavy one.
_MEANING_PREFIX_RE = re.compile(r"^(?:the\s+)?(?:meaning|definition|sense)\s+of\s+")

#: A definition asks WHAT SOMETHING IS. These make it a different question and are refused here so
#: the definition lane cannot swallow a conversion or a live-value request.
_NOT_A_DEFINITION_RE = re.compile(
    r"\b(?:to|into|in|worth|convert|converted|rate|rates|exchange|equals?|=|"
    r"today|now|current|currently|latest|right now)\b"
)


@dataclass(frozen=True)
class CurrencyDefinitionRequest:
    """A request for a currency's stable identity — answerable locally, forever."""

    reference: CurrencyReference
    #: The user said "money"/"currency" outright, so the everyday homograph reading is not in play.
    money_context: bool = False


def _resolve_literal(
    literal: str,
    *,
    raw: str,
    chat_codes: Sequence[str] = (),
    paired_with_settled_currency: bool = False,
) -> CurrencyReference | None:
    """Resolve one written literal to a currency reference, or None if it names no currency."""

    token = _PUNCT_RE.sub("", str(literal or "").strip()).strip()
    if not token:
        return None
    lowered = token.lower()

    # 1. an ISO code, held to the homograph evidence rule
    candidate = CodeCandidate(written=token)
    if candidate.fact is not None:
        evidence = currency_evidence(raw, chat_codes=chat_codes)
        if not evidence.admits(
            candidate, paired_with_settled_currency=paired_with_settled_currency
        ):
            return None
        code = candidate.code
        return CurrencyReference(code=code, written=token, candidates=(code,), basis="iso_code")

    # 2. a spelled-out name
    if lowered in _NAME_TO_CODE:
        code = _NAME_TO_CODE[lowered]
        return CurrencyReference(code=code, written=token, candidates=(code,), basis="currency_name")

    # 3. a symbol, which may name a whole family
    family = SYMBOL_FAMILIES.get(lowered)
    if family:
        return CurrencyReference(
            code=family[0] if len(family) == 1 else "",
            written=token,
            candidates=family,
            basis="symbol",
        )

    # 4. a unit word ("dollars", "kronor")
    units = UNIT_WORD_FAMILIES.get(lowered)
    if units:
        return CurrencyReference(
            code=units[0] if len(units) == 1 else "",
            written=token,
            candidates=units,
            basis="unit_word",
        )

    # 5. a place ("Singapore")
    place = PLACE_TO_CODE.get(lowered)
    if place:
        return CurrencyReference(code=place, written=token, candidates=(place,), basis="place")
    return None


def _definition_terms(text: str) -> list[str]:
    """Every term a definition frame pulls out of this turn, in frame order, lowercased.

    Shared by the two lanes below — one code is a DEFINITION, a run of homograph codes is an
    AMBIGUITY — because both need the same reading of "what is X?" and neither may re-derive it.
    """

    stripped = _PUNCT_RE.sub(" ", _flat(text)).strip()
    stripped = _OPENER_RE.sub("", stripped, count=1).strip()
    if not stripped:
        return []
    terms: list[str] = []
    for frame in _DEFINITION_FRAMES:
        match = frame.match(stripped)
        if not match:
            continue
        term = (match.group("term") or "").strip()
        # "what is THE TRY currency?" — a determiner is part of the frame, not part of the term,
        # and so is a "meaning of" wrapper around it.
        term = _DETERMINER_RE.sub("", term, count=1).strip()
        term = _MEANING_PREFIX_RE.sub("", term, count=1).strip()
        term = _DETERMINER_RE.sub("", term, count=1).strip()
        if not term or len(term.split()) > 4:
            continue
        # A conversion is not a definition, even though it starts "what is".
        remainder = stripped.replace(term, " ", 1)
        if _NOT_A_DEFINITION_RE.search(remainder) or _NOT_A_DEFINITION_RE.search(term):
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _as_typed_tokens(term: str, *, raw: str) -> list[str]:
    """The term's words with the user's own capitalisation restored — case is evidence.

    The frames match against a lowercased copy, so the term comes back flattened. Every downstream
    read of case (`written_as_code`) would be answered by that flattening rather than by what the
    user typed, which is exactly the bug this whole module refuses to have.
    """

    raw_tokens = [_PUNCT_RE.sub("", token) for token in re.findall(r"[^\s]+", str(raw or ""))]
    typed: list[str] = []
    for word in str(term or "").split():
        typed.append(next((token for token in raw_tokens if token.lower() == word), word))
    return typed


def currency_definition_intent(
    text: str, *, chat_codes: Sequence[str] = ()
) -> CurrencyDefinitionRequest | None:
    """The currency whose definition this turn asks for, or None.

    Claims only when the WHOLE turn is that question. A conversion, a live-rate request or a
    sentence with more going on falls through untouched. `chat_codes` are the codes this chat has
    already settled as currency; they admit a homograph the same way typed caps do, which is what
    makes "WHAT IS all?" a currency turn one message after "what is ALL?" and leaves it an English
    question in a chat that never mentioned currency.
    """

    raw = str(text or "")
    money_context = has_currency_context(raw)
    for term in _definition_terms(raw):
        literal = " ".join(_as_typed_tokens(term, raw=raw))
        reference = _resolve_literal(literal, raw=raw, chat_codes=chat_codes)
        if reference is None:
            continue
        return CurrencyDefinitionRequest(reference=reference, money_context=money_context)
    return None


@dataclass(frozen=True)
class HomographPhraseRequest:
    """A run of ISO codes that is also an ordinary English phrase. Answerable only by asking."""

    #: The phrase as the user typed it, capitalisation intact.
    written: str
    #: Every code the phrase spells out, in order.
    codes: tuple[str, ...]


def homograph_phrase_intent(
    text: str, *, chat_codes: Sequence[str] = ()
) -> HomographPhraseRequest | None:
    """A "what does X mean?" whose X is a phrase of homograph codes, or None.

    "try all" is two ISO codes and an ordinary English phrase at the same time, and nothing inside
    the message settles which one is meant. Guessing either way is the defect: the measured turn
    spent 4,925 tokens on a pure-English answer in a chat that had just defined TRY and ALL as
    currencies. So the turn is claimed and the choice handed back — locally, no model — and only
    when there is real evidence for the currency reading. In a chat that never mentioned currency
    there is nothing to disambiguate against, "try all tests" is just English, and this lane
    declines and says nothing at all.
    """

    raw = str(text or "")
    evidence = currency_evidence(raw, chat_codes=chat_codes)
    for term in _definition_terms(raw):
        tokens = _as_typed_tokens(term, raw=raw)
        if len(tokens) < 2:
            continue
        candidates = [CodeCandidate(written=token) for token in tokens]
        if any(item.fact is None for item in candidates):
            continue
        # Only a LOWERCASE homograph has a second reading to offer. "what does USD EUR mean" is
        # unambiguous, and so is a phrase the user shouted in code case; neither is asked about.
        ambiguous = [
            item for item in candidates if item.fact.homograph and not item.written_as_code
        ]
        if not ambiguous:
            continue
        if not any(evidence.admits(item) for item in ambiguous):
            continue
        return HomographPhraseRequest(
            written=" ".join(tokens),
            codes=tuple(item.code for item in candidates),
        )
    return None


def render_homograph_phrase(request: HomographPhraseRequest) -> str:
    """Hand the choice back, naming both readings in full. No model, no guess."""

    readings = [f"{code} ({ISO_4217[code].name})" for code in request.codes]
    listed = readings[0] if len(readings) == 1 else ", ".join(readings[:-1]) + " and " + readings[-1]
    return (
        f"Do you mean the English phrase “{request.written}”, or the currency codes {listed}? "
        f"Both readings fit what you typed, so I would rather ask than pick one for you."
    )


def render_currency_definition(request: CurrencyDefinitionRequest) -> str:
    """The stable answer, from reference data. No rate, no date, nothing that goes stale."""

    reference = request.reference
    if reference.ambiguous:
        lines = [
            f"“{reference.written}” is a currency symbol shared by several currencies, so it does "
            f"not pin one on its own. It can mean:"
        ]
        for code in reference.candidates:
            fact = ISO_4217[code]
            lines.append(f"  • {code} — {fact.name} ({fact.region})")
        lines.append("Tell me which one you mean, or give the country, and I'll use it from here.")
        return "\n".join(lines)

    fact = reference.fact
    if fact is None:
        return ""
    parts = [f"{fact.code} is the ISO 4217 code for the {fact.name}, the currency of {fact.region}."]
    if fact.symbol and fact.symbol != fact.code:
        parts.append(f"Its symbol is {fact.symbol}.")
    # The homograph note is for someone who TYPED the code and might have meant the word. Someone
    # who asked by name ("Turkish lira code?") already told us the domain, and someone who said
    # "money TRY" told us outright — neither needs to be warned about a collision they did not hit.
    if fact.homograph and not request.money_context and reference.basis == "iso_code":
        parts.append(f"(Read as a word rather than a currency code, “{fact.code}” is also {fact.homograph_sense}.)")
    return " ".join(parts)


# =================================================================================================
# FX conversion intent — the defect-3 surface. Recognised here, NEVER answered with a stored rate.
# =================================================================================================

_AMOUNT = r"(?P<amount>\d[\d,_ ]*(?:\.\d+)?)"
_UNIT = r"(?P<unit>[A-Za-z]{2,20}|[^\s\dA-Za-z]{1,3}|[A-Za-z]\$)"

#: An optional conversion VERB (plus an optional pronoun) sitting between the source currency and
#: the connective. People state the pair and then the action — "100 USD convert to RUB", "i have
#: 100 usd convert it to ALL" (both observed live) — and a frame that demands the connective
#: immediately after the source reads those as no conversion at all, which silently dropped the
#: clause on multi-part turns. The segment is bounded (one verb, one pronoun) so it cannot bridge
#: unrelated sentences.
_MID_CONVERT_VERB = (
    r"(?:(?:convert(?:ed)?|exchang(?:e|ed)|swap(?:ped)?|chang(?:e|ed)|turn(?:ed)?)\s+"
    r"(?:(?:it|them|that|this|all)\s+)?)?"
)

_CONVERSION_FRAMES: tuple[re.Pattern[str], ...] = (
    # "1000 TRY to USD", "500 DKK in USD", "convert 1000 TRY into dollars",
    # "100 USD convert to RUB", "100 usd convert it to ALL"
    re.compile(
        r"(?:convert\s+)?" + _AMOUNT + r"\s*(?P<src>[^\s\d]+(?:\s+dollars?|\s+kronor?)?)\s+" + _MID_CONVERT_VERB +
        r"(?:to|in|into|=|->)\s+"
        r"(?P<dst>[^\s?]+(?:\s+dollars?|\s+kronor?)?)",
        re.IGNORECASE,
    ),
    # "¥5000 to dollars", "$300 in Singapore" — symbol glued to the front of the amount
    re.compile(
        r"(?P<src>[^\s\dA-Za-z]{1,3}|[A-Za-z]{1,2}\$)\s*" + _AMOUNT +
        r"\s+" + _MID_CONVERT_VERB + r"(?:to|in|into|=|->)\s+(?P<dst>[^\s?]+)",
        re.IGNORECASE,
    ),
    # "What is 500 kr worth in dollars?", "how much is 1000 TRY worth in USD"
    re.compile(
        _AMOUNT + r"\s*(?P<src>[^\s\d]+)\s+(?:worth|value)\s+(?:in|as)\s+(?P<dst>[^\s?]+)",
        re.IGNORECASE,
    ),
    # "convert 250 gbp 2 usd pls" — the direction word typed as the digit "2"
    # (measured live 2026-08-29; same shape as the first frame otherwise, and
    # the same settled-side resolution guard applies downstream).
    re.compile(
        r"(?:convert\s+)?" + _AMOUNT + r"\s*(?P<src>[^\s\d]+(?:\s+dollars?|\s+kronor?)?)\s+2\s+"
        r"(?P<dst>[^\s?]+(?:\s+dollars?|\s+kronor?)?)",
        re.IGNORECASE,
    ),
    # "how many yen for 100 usd" — the TARGET unit stated first. Measured live:
    # the target-first frame resolved nothing and fell to the model.
    re.compile(
        r"(?P<dst>[A-Za-z]{3,}(?:\s+dollars?|\s+kronor?)?)\s+(?:for|in|to)\s+"
        + _AMOUNT + r"\s+(?P<src>[^\s\d]+)",
        re.IGNORECASE,
    ),
)

#: A rate the USER supplied. This is the only numeric input this module will ever multiply by.
_SUPPLIED_RATE_RE = re.compile(
    r"(?:at|using|with|assume|assuming|rate(?:\s+of|\s+is|:)?)\s*"
    r"(?P<rate>\d+(?:\.\d+)?)\s*(?:(?P<unit>[A-Za-z]{3}|[^\s\dA-Za-z]{1,2})\s*)?"
    r"(?:per|/|to|for)?\s*(?:(?P<per>[A-Za-z]{3}|[^\s\dA-Za-z]{1,2}))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FxConversionRequest:
    """A conversion the user asked for. Carries no rate unless the user supplied one."""

    amount: Decimal
    source: CurrencyReference
    target: CurrencyReference
    written_amount: str = ""
    supplied_rate: Decimal | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def needs_live_rate(self) -> bool:
        """True whenever no rate is in hand. There is no third state and no stored fallback."""

        return self.supplied_rate is None


def _parse_amount(written: str) -> Decimal | None:
    cleaned = str(written or "").replace(",", "").replace("_", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _is_settled_currency(reference: CurrencyReference | None) -> bool:
    """A reference nothing could mistake for an English word — the anchor half of a pair.

    A symbol, a spelled-out name, a unit word or a place is settled by construction; a bare ISO
    code is settled only when it is not itself an English word, which is the same asymmetry the
    homograph rule runs on everywhere else.
    """

    if reference is None:
        return False
    if reference.basis != "iso_code":
        return True
    fact = reference.fact
    return fact is not None and not fact.homograph


def _supplied_rate(text: str) -> Decimal | None:
    match = _SUPPLIED_RATE_RE.search(str(text or ""))
    if not match:
        return None
    return _parse_amount(match.group("rate"))


def fx_conversion_intent(
    text: str, *, chat_codes: Sequence[str] = ()
) -> FxConversionRequest | None:
    """The conversion this turn asks for, or None. Recognition only — this answers nothing."""

    raw = str(text or "")
    if not raw.strip():
        return None
    for frame in _CONVERSION_FRAMES:
        match = frame.search(raw)
        if not match:
            continue
        amount = _parse_amount(match.group("amount"))
        if amount is None:
            continue
        written_source = match.group("src")
        written_target = match.group("dst")
        source = _resolve_literal(written_source, raw=raw, chat_codes=chat_codes)
        target = _resolve_literal(written_target, raw=raw, chat_codes=chat_codes)
        # "1000 all to usd" carries no currency word and no capital letter, and it is still plainly
        # a conversion: a quantity governs a pair whose other side is USD. One SETTLED side is the
        # evidence for the other. Both sides unsettled is not evidence of anything — "move 3 try to
        # top of the list" matches the same grammar and stays out.
        if source is None and _is_settled_currency(target):
            source = _resolve_literal(
                written_source, raw=raw, chat_codes=chat_codes, paired_with_settled_currency=True
            )
        elif target is None and _is_settled_currency(source):
            target = _resolve_literal(
                written_target, raw=raw, chat_codes=chat_codes, paired_with_settled_currency=True
            )
        if source is None or target is None:
            continue
        return FxConversionRequest(
            amount=amount,
            source=source,
            target=target,
            written_amount=match.group("amount").strip(),
            supplied_rate=_supplied_rate(raw),
        )
    return None


def _format_amount(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return f"{int(quantized):,}"
    return f"{quantized:,.2f}"


def render_fx_conversion(
    request: FxConversionRequest,
    *,
    live_rate: Decimal | None = None,
    rate_asof: str = "",
) -> str:
    """The conversion answer.

    Exactly three outcomes, and no fourth:

    * the user supplied a rate  -> arithmetic against THEIR number, shown in full;
    * a live rate was passed in -> arithmetic against that, attributed and timestamped;
    * neither                   -> say the rate is missing, name what would settle it, convert
                                   nothing. No range, no "approximately", no remembered number.

    `live_rate` is a parameter rather than a lookup so that adding a real FX feed later is a change
    at the CALLER and nothing in this module moves. That is the blast-radius rule, applied here.
    """

    source = request.source
    target = request.target
    ambiguity: list[str] = []
    for reference, role in ((source, "from"), (target, "to")):
        if reference.ambiguous:
            names = ", ".join(f"{code} ({ISO_4217[code].name})" for code in reference.candidates[:5])
            more = "" if len(reference.candidates) <= 5 else ", and others"
            ambiguity.append(
                f"“{reference.written}” ({role}) is shared by several currencies — {names}{more}."
            )

    src_label = source.describe() if source.code else f"“{source.written}”"
    dst_label = target.describe() if target.code else f"“{target.written}”"
    amount_label = f"{_format_amount(request.amount)} {src_label}"

    rate = request.supplied_rate if request.supplied_rate is not None else live_rate
    if rate is not None:
        converted = request.amount * rate
        basis = (
            "the rate you gave"
            if request.supplied_rate is not None
            else f"a live rate{f' as of {rate_asof}' if rate_asof else ''}"
        )
        lines = [
            f"{amount_label} × {rate} = {_format_amount(converted)} {dst_label}, using {basis}.",
        ]
        if request.supplied_rate is not None:
            lines.append(
                "That is arithmetic on your number — I have not checked it against a live market."
            )
        lines.extend(ambiguity)
        return "\n".join(lines)

    lines = []
    if ambiguity:
        lines.extend(ambiguity)
        lines.append("Tell me which, and the pair is pinned.")
    # Name the pair ONLY when both sides are actually pinned. Picking the first candidate of an
    # ambiguous family here would print "the current SEK/USD rate" for a message that said "kr" —
    # inventing the very fact the ambiguity notice above just said we don't have.
    if source.code and target.code:
        needed = f"the current {source.code}/{target.code} rate for a specific date and time"
    else:
        needed = "the current rate for that pair, for a specific date and time"
    lines.append(
        f"I can't convert {amount_label} to {dst_label} accurately: that needs {needed}, and I "
        f"don't have a live FX source wired up."
    )
    lines.append(
        "Give me a rate and I'll do the arithmetic exactly — append “at <rate>” and I'll use that "
        "number. Otherwise any figure I gave you would be a guess from stale training data, which "
        "for FX is worse than no answer."
    )
    return "\n".join(lines)


# =================================================================================================
# Rate lookup — "what's the USD/DKK rate today". A conversion with no amount, and the same refusal.
# =================================================================================================

_RATE_LOOKUP_FRAMES: tuple[re.Pattern[str], ...] = (
    # "current USD/DKK rate today", "USD to DKK rate", "the USD-DKK exchange rate right now"
    re.compile(
        r"\b(?P<base>[A-Za-z]{3})\b\s*(?:/|-|\bto\b|\bvs\b|\bversus\b|\bagainst\b)\s*"
        r"\b(?P<quote>[A-Za-z]{3})\b",
        re.IGNORECASE,
    ),
    # "what is the exchange rate for DKK in USD"
    re.compile(
        r"rate\s+(?:for|of)\s+\b(?P<base>[A-Za-z]{3})\b\s+(?:in|to|against)\s+"
        r"\b(?P<quote>[A-Za-z]{3})\b",
        re.IGNORECASE,
    ),
)

#: The word that makes a bare pair a request for its PRICE rather than a mention of two currencies.
_RATE_WORD_RE = re.compile(r"\b(?:rate|rates|exchange|fx|quote|quoted|price|worth|trading)\b")


@dataclass(frozen=True)
class FxRateLookupRequest:
    """A request for a pair's CURRENT price, with no amount attached to convert."""

    base: CurrencyReference
    quote: CurrencyReference
    #: The user asked for "today" / "now" / "current", so even a cached number would be wrong.
    asked_for_current: bool = False


_CURRENT_RE = re.compile(r"\b(?:today|now|right now|current|currently|latest|live|this morning)\b")


def fx_rate_lookup_intent(text: str, *, chat_codes: Sequence[str] = ()) -> FxRateLookupRequest | None:
    """The pair whose rate this turn asks for, or None. Recognition only — answers nothing."""

    raw = str(text or "")
    lowered = _flat(raw)
    # A current bare pair is itself a rate request: "What is EUR to USD today?" names two
    # currencies plus a freshness requirement, so requiring the literal word "rate" drops the FX
    # clause and lets unrelated market extraction claim neighboring words such as "rain". A bare
    # pair without either a rate cue or a current-time cue remains only a mention.
    if not lowered or not (_RATE_WORD_RE.search(lowered) or _CURRENT_RE.search(lowered)):
        return None
    if fx_conversion_intent(raw, chat_codes=chat_codes) is not None:
        # An amount was given, so it is a conversion and the conversion renderer owns it.
        return None
    for frame in _RATE_LOOKUP_FRAMES:
        for match in frame.finditer(raw):
            base = _resolve_literal(match.group("base"), raw=raw, chat_codes=chat_codes)
            quote = _resolve_literal(match.group("quote"), raw=raw, chat_codes=chat_codes)
            if base is None or quote is None or not base.code or not quote.code:
                continue
            if base.code == quote.code:
                continue
            return FxRateLookupRequest(
                base=base, quote=quote, asked_for_current=bool(_CURRENT_RE.search(lowered))
            )
    return None


def render_fx_rate_lookup(
    request: FxRateLookupRequest,
    *,
    live_rate: Decimal | None = None,
    rate_asof: str = "",
) -> str:
    """The rate answer. A number only when one was passed in; otherwise the gap, named."""

    pair = f"{request.base.code}/{request.quote.code}"
    if live_rate is not None:
        stamp = f" as of {rate_asof}" if rate_asof else ""
        return (
            f"1 {request.base.code} = {live_rate} {request.quote.code}, from a live rate{stamp}.\n"
            f"{request.base.describe()} → {request.quote.describe()}."
        )
    when = "right now" if request.asked_for_current else "for the date you mean"
    return (
        f"I can't give you the {pair} rate {when}: that is a live market measurement and no FX "
        f"source is wired up here.\n"
        f"A rate from my training data would be months stale and stated with false confidence, "
        f"which for FX is worse than no answer.\n"
        f"Read it from a rate source you trust and paste it in — “{pair} at <rate>” — and I'll use "
        f"that number for any arithmetic you want on top of it."
    )


def resolve_currency_pair(
    base_written: str, quote_written: str, *, raw: str = ""
) -> tuple[str, str]:
    """The ISO codes a written currency pair names, applying this contract's own evidence rules.

    For consumers that hold two SURFACES (a proven frame's role spans, a proposer's slots) and
    need the identity the rest of the runtime uses -- "US"/"RUB" and "usd"/"rub" both name
    USD/RUB. One side that resolves settles the evidence for the other, exactly as
    `fx_conversion_intent` reads a typed pair. A side that resolves to nothing keeps its
    upper-cased surface, so downstream ISO validation still names the real problem instead of
    this helper guessing.
    """
    context = raw or f"{base_written} to {quote_written}"
    base_ref = _resolve_literal(base_written, raw=context)
    quote_ref = _resolve_literal(quote_written, raw=context)
    if base_ref is None and _is_settled_currency(quote_ref):
        base_ref = _resolve_literal(base_written, raw=context, paired_with_settled_currency=True)
    elif quote_ref is None and _is_settled_currency(base_ref):
        quote_ref = _resolve_literal(quote_written, raw=context, paired_with_settled_currency=True)
    base = base_ref.code if base_ref is not None and base_ref.code else str(base_written or "").upper()
    quote = (
        quote_ref.code if quote_ref is not None and quote_ref.code else str(quote_written or "").upper()
    )
    return base, quote


def currency_semantics_present(text: str, *, chat_codes: Sequence[str] = ()) -> bool:
    """Whether this turn is about currency at all — the domain-admission question.

    The mirror of `core.market_intent.market_semantics_present`, and the signal that was missing
    when "1000 TRY to USD?" was admitted as an ordinary chat turn with nothing grounding it.
    """

    from core.currency_comparison import currency_comparison_intent

    return bool(
        currency_definition_intent(text, chat_codes=chat_codes)
        or fx_conversion_intent(text, chat_codes=chat_codes)
        or fx_rate_lookup_intent(text, chat_codes=chat_codes)
        or homograph_phrase_intent(text, chat_codes=chat_codes)
        or currency_comparison_intent(text)
        or (
            has_currency_context(text)
            and (codes_named(text, chat_codes=chat_codes) or names_named(text))
        )
    )


# =================================================================================================
# Reading this module's OWN answers back — how a later turn learns what an earlier one settled.
# =================================================================================================

#: Sentences the renderers above emit, and only they. A prior turn carrying one of these IS a
#: currency answer this runtime produced, so pulling codes back out of it is reading our own
#: receipt rather than guessing at prose. `test_the_receipt_markers_survive_every_renderer` renders
#: the whole family and parses it back, so a reworded answer cannot silently stop being readable.
CURRENCY_ANSWER_MARKERS: tuple[str, ...] = (
    "is the ISO 4217 code for",
    "is a currency symbol shared by several currencies",
    "is shared by several currencies",
    "or the currency codes",
    "I can't convert",
    "using the rate you gave",
    "using a live rate",
)


def codes_from_currency_answer(text: str) -> tuple[str, ...]:
    """Every ISO code named by an answer THIS module produced, or () for anything else.

    The marker gate is the whole safety property: without it, any assistant message that happened
    to contain a capitalised three-letter word would enrol that word as chat currency context.
    """

    body = str(text or "")
    if not any(marker in body for marker in CURRENCY_ANSWER_MARKERS):
        return ()
    ordered: list[str] = []
    for match in re.finditer(r"\b[A-Z]{3}\b", body):
        code = match.group(0)
        if code in ISO_4217 and code not in ordered:
            ordered.append(code)
    return tuple(ordered)


__all__ = [
    "CITY_TO_CODE",
    "CURRENCY_ANSWER_MARKERS",
    "ISO_4217",
    "LOCATION_TO_CODE",
    "NATIONALITY_TO_CODE",
    "PLACE_TO_CODE",
    "SYMBOL_FAMILIES",
    "UNIT_WORD_FAMILIES",
    "CodeCandidate",
    "CurrencyDefinitionRequest",
    "CurrencyEvidence",
    "CurrencyFact",
    "CurrencyReference",
    "FxConversionRequest",
    "FxRateLookupRequest",
    "HomographPhraseRequest",
    "LocationBinding",
    "code_candidates",
    "codes_from_currency_answer",
    "codes_named",
    "currency_definition_intent",
    "currency_evidence",
    "currency_semantics_present",
    "currency_transaction_intent",
    "fx_conversion_intent",
    "fx_rate_lookup_intent",
    "has_currency_context",
    "homograph_phrase_intent",
    "location_bindings",
    "names_named",
    "render_currency_definition",
    "render_fx_conversion",
    "render_fx_rate_lookup",
    "render_homograph_phrase",
    "resolve_currency_literal",
]
