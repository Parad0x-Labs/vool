"""How a spoken or typed name finds a saved contact: deterministic, Unicode-aware, never a model decision.

Tiers, strongest first:

* ``id``         -- a contact id (``ct-...``) or endpoint id (``ep-...``) named exactly;
* ``exact_name`` -- the whole display name, after NFKC normalisation, case folding and whitespace collapsing;
* ``alias``      -- a saved alias under the same normalisation;
* ``name_token`` -- one whole word of the display name ("Alex" for "Alex Chen");
* ``confusable`` -- a different string that looks like the name, an alias or one word of it (UTS #39 skeleton,
  core.contacts.identity): never chosen, always asked about;
* ``fuzzy``      -- anything weaker: accents folded ("Zoe" for "Zoë"), a prefix, a near spelling.

The first four are strong: a request that strongly matches exactly one contact names that contact, unless a lookalike of
it is saved too. Confusable and fuzzy matches are only ever choices or suggestions, because characters that look alike
and accents or spellings can separate two real people; the caller asks instead of choosing.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

MATCH_ID = "id"
MATCH_EXACT_NAME = "exact_name"
MATCH_ALIAS = "alias"
MATCH_NAME_TOKEN = "name_token"
MATCH_CONFUSABLE = "confusable"
MATCH_FUZZY = "fuzzy"

STRONG_MATCHES: tuple[str, ...] = (MATCH_ID, MATCH_EXACT_NAME, MATCH_ALIAS, MATCH_NAME_TOKEN)
MATCH_RANK: dict[str, int] = {MATCH_ID: 0, MATCH_EXACT_NAME: 1, MATCH_ALIAS: 2, MATCH_NAME_TOKEN: 3, MATCH_CONFUSABLE: 4, MATCH_FUZZY: 5}

#: below this a fuzzy candidate is not worth showing as a suggestion
FUZZY_THRESHOLD = 0.72

MAX_NAME_CHARS = 200

_SPACE = re.compile(r"\s+")
_TOKEN_SPLIT = re.compile(r"[\s,.;:()\[\]{}<>\"'/\\|_\-]+")
#: zero-width non-joiner and joiner shape letters in several scripts; they are kept in a display name and ignored for matching
_JOINERS = ("‌", "‍")


def name_key(text: object) -> str:
    """The exact-match key. Diacritics are identity here: "Zoë" and "Zoe" are different keys. Joiners are not."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    for joiner in _JOINERS:
        value = value.replace(joiner, "")
    return _SPACE.sub(" ", value).strip()


def fold_key(text: object) -> str:
    """The suggestion key: ``name_key`` with combining marks removed. Only ever used to rank suggestions."""
    decomposed = unicodedata.normalize("NFKD", name_key(text))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def tokens(text: object) -> tuple[str, ...]:
    return tuple(token for token in _TOKEN_SPLIT.split(name_key(text)) if token)


def clean_display(text: object) -> str:
    """A display string as the owner typed it, minus control and format characters (joiners kept) and surrounding space."""
    value = unicodedata.normalize("NFC", str(text or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C" or ch == " " or ch in _JOINERS)
    return _SPACE.sub(" ", value).strip()[:MAX_NAME_CHARS]


def match_tier(query: str, *, display_name: str, aliases: tuple[str, ...] | list[str] = ()) -> str | None:
    """The strongest strong tier this contact reaches for the query, or None."""
    wanted = name_key(query)
    if not wanted:
        return None
    if wanted == name_key(display_name):
        return MATCH_EXACT_NAME
    if any(wanted == name_key(alias) for alias in aliases):
        return MATCH_ALIAS
    query_tokens = tokens(query)
    if len(query_tokens) == 1 and query_tokens[0] in tokens(display_name):
        return MATCH_NAME_TOKEN
    return None


def fuzzy_score(query: str, *, display_name: str, aliases: tuple[str, ...] | list[str] = ()) -> float:
    """0..1 similarity on the folded keys of the name and every alias; the best one counts."""
    wanted = fold_key(query)
    if not wanted:
        return 0.0
    best = 0.0
    for candidate in (display_name, *aliases):
        folded = fold_key(candidate)
        if not folded:
            continue
        if folded == wanted:
            return 0.99
        score = difflib.SequenceMatcher(None, wanted, folded).ratio()
        folded_tokens = _TOKEN_SPLIT.split(folded)
        if any(token.startswith(wanted) for token in folded_tokens if token) and len(wanted) >= 2:
            score = max(score, 0.8)
        if any(token == wanted for token in folded_tokens if token):
            score = max(score, 0.9)
        best = max(best, score)
    return round(best, 4)
