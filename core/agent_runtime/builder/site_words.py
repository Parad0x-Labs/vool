"""A one-page website built from the person's own words, with no model in the loop.

Audit mailweb 2026-10-27 (runs B03, B05-B07): "id love a basic one page website for the bakery, can
you do that?" reached the model-driven builder lane; the local model returned an empty or garbage
file list and the turn died on "the model did not return a usable file list", or the generation
hung until the 240 s turn timeout with no reply at all. The person's sentence already carried
everything a simple page needs -- a business name, what they sell, opening hours, a phone number --
and none of that needs a model to find.

Pure text work, no I/O: a sentence goes in, `SiteWords` or one complete `index.html` string comes
out. The page is a single self-contained file (inline CSS only; no external fonts, scripts or
images; no JavaScript) so a fresh install can open it straight from disk with no server and no
network. The footer line is a plain marker so later turns can find this page again on disk.

Name extraction is a SHAPE, not a noun list: "... called Paws", "... named Paws", a quoted name,
or the "for my/our/the <thing>" phrase itself ("for my bakery" -> "My Bakery"). A list of known
business nouns would recognize the audit's bakery and nothing else the next person types.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class SiteWords:
    """What the person's own sentence said about their business.

    Every field may be empty; the renderer leaves the matching page section out rather than invent
    content -- a page with no phone number beats one with a made-up phone number.
    """

    name: str = ""
    tagline: str = ""
    phone: str = ""
    hours: str = ""


_PUNCT = ".,!?;:\"'`"

# Trailing sentence furniture that a captured name must shed: "for my dog groomer called Paws, can
# you do that?" captured up to four tokens after "called", and the tail of that capture is the next
# clause, not part of the name.
_NAME_BOUNDARY_WORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "with", "that", "which", "please", "pls", "thanks", "thank",
        "cheers", "can", "could", "would", "will", "you", "u", "do", "dont", "if", "asap", "is",
        "are", "in", "on", "at", "to", "from", "for", "of", "open", "opens", "opening", "hours",
        "phone", "number", "call", "text", "email", "we", "i", "she", "he", "they", "it", "its",
        "runs", "run", "called", "named", "sells", "sell", "makes", "make",
    }
)

# Words that start a NEW CLAUSE rather than continue a name. A name capture window can swallow the
# next clause whole ("for the bakery we serve fresh bread" captured "bakery we serve fresh"), and
# right-trimming alone cannot remove words buried mid-capture. Cutting at the first clause word
# fixes that; words a real business name does contain ("and" in "Salt and Pepper", "&", short
# prepositions) are deliberately NOT in this set and stay inside the name.
_NAME_CLAUSE_WORDS = frozenset(
    {
        "we", "i", "she", "he", "they", "you", "it", "can", "could", "would", "will", "do", "dont",
        "please", "thanks", "that", "which", "open", "opens", "opening", "hours", "phone", "number",
        "call", "text", "email", "sell", "sells", "make", "makes", "run", "runs", "called", "named",
        "is", "are",
    }
)

# "... called Paws", "... named Sugar & Spice". Up to four word-ish tokens; a stop-word trim below
# removes anything of the next clause that slipped inside the four-token window.
_CALLED_NAME_RE = re.compile(
    r"\b(?:called|named)\s+[\"'`“”‘’]?"
    r"([A-Za-z0-9&][\w'&.\-]*(?:\s+[A-Za-z0-9&][\w'&.\-]*){0,3})",
    re.IGNORECASE,
)

# A name the person put in quotes: my shop 'Crumb & Co'. The quote must open after a boundary and
# close before one, so an apostrophe inside a word ("id love", "we're") cannot start a capture.
_QUOTED_NAME_RE = re.compile(
    r"(?:^|[\s(\[])[\"'“”‘’]([A-Za-z0-9&][^\"'“”‘’\n]{1,48}?)[\"'“”‘’](?=$|[\s.,!?;:)\]])"
)

# "for my bakery", "for our family restaurant", "for the cake shop" -- the possessive becomes part
# of the name ("My Bakery"), so the page heading is still the person's own phrasing.
_FOR_PHRASE_RE = re.compile(
    r"\bfor\s+(my|our|the|his|her|their)\s+([a-z][a-z'&\-]*(?:\s+[a-z][a-z'&\-]*){0,3})",
    re.IGNORECASE,
)

# What the person said they do or sell, in their own words. Three shapes, most specific first:
# a "we sell ..." clause, a "that sells ..." clause attached to the thing, or a bare participle.
_TAGLINE_RES = (
    re.compile(
        r"\b((?:we|i)\s+(?:only\s+|also\s+|mostly\s+)?"
        r"(?:sell|make|bake|serve|cook|offer|do|fix|repair|speciali[sz]e\s+in)\b[^.,;!?]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bthat\s+((?:sells?|makes?|bakes?|serves?|offers?|fixes?|repairs?|speciali[sz]es?\s+in)\b[^.,;!?]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b((?:selling|making|baking|serving|offering|fixing|repairing|speciali[sz]ing\s+in)\b[^.,;!?]*)",
        re.IGNORECASE,
    ),
)
# "i make a website for my bakery" is the ASK, not a description of the bakery -- the offer clause
# must not become the tagline when its object is the site being asked for.
_SITE_NOUN = r"(?:websites?|web\s*pages?|webpages?|homepages?|home\s*pages?|landing\s+pages?|sites?|pages?)"
_TAGLINE_MAKES_THE_SITE_RE = re.compile(
    r"^\s*(?:we|i)\s+(?:only\s+|also\s+|mostly\s+)?(?:make|makes|do|build|create|design|write)\s+"
    r"(?:a|an|the|some|my|our)?\s*" + _SITE_NOUN,
    re.IGNORECASE,
)

# A phone number is a run of digits with the separators people actually type (spaces, dashes,
# brackets, dots), long enough that it cannot be a time ("8 till 6" has letters between the digits
# and never matches) or a year. The person's formatting is kept verbatim.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w/])(\(?\+?\d[\d\s().\-]{5,24}\d\)?)(?![\w/])")

# Opening hours. A time may carry am/pm; a span joins two times with to/till/until/through or a
# dash; days are whole words so "money" cannot pose as "mon". Five shapes, most specific first.
_HOURS_DAY = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|"
    r"weekdays?|weekends?|every\s+day|daily)"
)
_HOURS_TIME = r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?"
_HOURS_JOIN = r"\s*(?:to|till|until|through|thru|and|[-–—])\s*"
_HOURS_TIME_SPAN = _HOURS_TIME + _HOURS_JOIN + _HOURS_TIME
_HOURS_LEAD = r"(?:opens?|open(?:ing)?|hours?)\b[:\s]*"
_HOURS_RES = (
    # "monday to friday 8 till 6" -- a day (or day span) followed by a time span.
    re.compile(
        r"\b" + _HOURS_DAY + r"(?:\s*" + _HOURS_JOIN + r"\s*\b" + _HOURS_DAY + r")?\s*,?\s*"
        + _HOURS_TIME_SPAN,
        re.IGNORECASE,
    ),
    # "open 9 to 5", "opening hours 9-5", "hours: 9 to 5".
    re.compile(r"\b" + _HOURS_LEAD + _HOURS_TIME_SPAN, re.IGNORECASE),
    # "9 to 5" with no lead at all.
    re.compile(r"\b" + _HOURS_TIME_SPAN, re.IGNORECASE),
    # "open until 6", "open from 8", "open till 10pm".
    re.compile(r"\bopen\s+(?:until|till|from|at)\s+" + _HOURS_TIME, re.IGNORECASE),
    # "open monday to friday" -- days with no times attached.
    re.compile(
        r"\b" + _HOURS_LEAD + _HOURS_DAY + r"(?:\s*" + _HOURS_JOIN + r"\s*\b" + _HOURS_DAY + r")?\b",
        re.IGNORECASE,
    ),
)


def _clean(text: str) -> str:
    """Whitespace collapsed to single spaces: one line of text for every regex below."""
    return " ".join(str(text or "").split())


def _capitalized(text: str) -> str:
    """Title-shape a name the person typed, keeping words they already capitalized.

    "paws" -> "Paws" and "my bakery" -> "My Bakery", while "Paws" and "McVitie's" stay as typed --
    upper-casing an already-capitalized word would shout the person's own name back at them.
    """
    words: list[str] = []
    for word in _clean(text).split():
        words.append(word if any(ch.isupper() for ch in word) else word[:1].upper() + word[1:])
    return " ".join(words)


def _cut_at_clause_word(words: list[str]) -> list[str]:
    """Drop everything from the first word that opens a new clause ("we", "open", "call", ...).

    Applied BEFORE the right-trim: a capture window of up to four tokens can swallow the start of
    the next clause ("for the bakery we serve fresh bread"), and trailing words like "fresh" are
    ordinary name words no trim will remove. The first word of a capture is never a cut point, so a
    business genuinely called "We Wash Dogs" keeps its name.
    """
    kept: list[str] = []
    for word in words:
        if kept and word.strip(_PUNCT).lower() in _NAME_CLAUSE_WORDS:
            break
        kept.append(word)
    return kept


def _trim_boundary_words(words: list[str]) -> list[str]:
    kept = _cut_at_clause_word(list(words))
    while kept and kept[-1].strip(_PUNCT).lower() in _NAME_BOUNDARY_WORDS:
        kept.pop()
    return [word.strip(_PUNCT) for word in kept if word.strip(_PUNCT)]


def _name_from(body: str) -> str:
    match = _CALLED_NAME_RE.search(body)
    if match:
        words = _trim_boundary_words(match.group(1).split())
        if words:
            return _capitalized(" ".join(words))
    quoted = _QUOTED_NAME_RE.search(body)
    if quoted:
        words = _trim_boundary_words(quoted.group(1).split())
        if words:
            return _capitalized(" ".join(words))
    for_phrase = _FOR_PHRASE_RE.search(body)
    if for_phrase:
        words = _trim_boundary_words(for_phrase.group(2).split())
        if words:
            return _capitalized(for_phrase.group(1) + " " + " ".join(words))
    return ""


def _tagline_from(body: str) -> str:
    for pattern in _TAGLINE_RES:
        for match in pattern.finditer(body):
            if _TAGLINE_MAKES_THE_SITE_RE.match(match.group(1)):
                continue
            clause = _clean(match.group(1)).strip(" ,;:")
            return clause[:1].upper() + clause[1:] if clause else ""
    return ""


def _phone_from(body: str) -> str:
    best = ""
    best_digits = 6  # a candidate must beat six digits to be a phone number at all
    for match in _PHONE_CANDIDATE_RE.finditer(body):
        candidate = match.group(1)
        digits = sum(char.isdigit() for char in candidate)
        if digits <= best_digits:
            continue
        candidate = candidate.strip(" .-")
        # "(020) 7946 0000" can capture from the first digit, leaving a bracket that closes
        # nothing; drop it rather than print a dangling one.
        while candidate.endswith(")") and candidate.count(")") > candidate.count("("):
            candidate = candidate[:-1].rstrip()
        if sum(char.isdigit() for char in candidate) >= 7:
            best, best_digits = candidate, digits
    return best


def _hours_from(body: str) -> str:
    for pattern in _HOURS_RES:
        match = pattern.search(body)
        if match:
            # A bare "hours"/"opening hours" lead names the field, not the hours; the page already
            # labels the row "Hours", so the lead is dropped rather than printed twice.
            return _clean(
                re.sub(r"^(?:opening\s+hours|hours?)\s*[:\-]?\s*", "", match.group(0), flags=re.IGNORECASE)
            )
    return ""


def extract_site_words(text: str) -> SiteWords:
    """The name / tagline / phone / hours a person gave in one plain sentence, or "" per field.

    Never raises and never guesses: a sentence with no recognizable shape yields empty fields, and
    the page renderer omits the missing sections rather than fill them with invented content.
    """
    body = _clean(text)
    return SiteWords(
        name=_name_from(body),
        tagline=_tagline_from(body),
        phone=_phone_from(body),
        hours=_hours_from(body),
    )


_SITE_PAGE_CSS = """\
      :root { --accent: #9a5b2d; }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        color: #2b2b2b;
        background: #faf7f2;
        line-height: 1.6;
      }
      main { max-width: 640px; margin: 0 auto; padding: 3rem 1.25rem 2rem; }
      h1 { font-size: 2rem; line-height: 1.2; margin: 0 0 0.75rem; }
      .tagline { font-size: 1.15rem; color: #5f5f5f; margin: 0 0 2rem; }
      .details { border-left: 4px solid var(--accent); padding: 0.35rem 0 0.35rem 1rem; margin: 0 0 2.5rem; }
      .details p { margin: 0.4rem 0; font-size: 1.05rem; }
      .label {
        display: inline-block;
        min-width: 4.5rem;
        font-size: 0.8rem;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--accent);
      }
      footer { max-width: 640px; margin: 0 auto; padding: 1rem 1.25rem 2rem; color: #8a8a8a; font-size: 0.9rem; }\
"""


def render_simple_site_html(words: SiteWords) -> str:
    """One complete, self-contained index.html from the person's own words.

    Inline CSS only, system fonts, one accent colour, a max-width container and a mobile viewport:
    it opens from disk on a fresh install with no server, no network and no build step. The footer
    line is a plain marker later turns use to find this page again; nothing else is appended to it.
    """
    name = _clean(words.name) or "Welcome"
    tagline = _clean(words.tagline)
    hours = _clean(words.hours)
    phone = _clean(words.phone)
    parts = [f"        <h1>{escape(name)}</h1>"]
    if tagline:
        parts.append(f'        <p class="tagline">{escape(tagline)}</p>')
    rows = []
    if hours:
        rows.append(f'          <p><span class="label">Hours</span> {escape(hours)}</p>')
    if phone:
        rows.append(f'          <p><span class="label">Phone</span> {escape(phone)}</p>')
    if rows:
        parts.append('        <div class="details">')
        parts.extend(rows)
        parts.append("        </div>")
    body = "\n".join(parts)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"    <title>{escape(name)}</title>\n"
        f"    <style>\n{_SITE_PAGE_CSS}\n    </style>\n"
        "  </head>\n"
        "  <body>\n"
        "    <main>\n"
        f"{body}\n"
        "    </main>\n"
        "    <footer>Made on this computer</footer>\n"
        "  </body>\n"
        "</html>\n"
    )
