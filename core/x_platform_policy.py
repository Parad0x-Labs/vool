"""The ONE versioned X (Twitter) platform policy for the X editorial studio.

Every platform constraint the editorial engine enforces lives HERE — never scattered as
constants across modules — and every pinned fact names its official source. The policy is
data, not folklore: where official documentation states no constraint (notably an X Article
character limit and a thread-length cap), this module refuses to invent one and returns the
typed ``validation_unknown`` certainty instead (see UNSPECIFIED).

Weighted counting implements the official twitter-text v3 rules exactly
(twitter/twitter-text ``config/v3.json`` + ``config/README.md``): a weighted length where
every code point weighs 100 or 200 per the config's four ranges, every URL weighs
``transformedURLLength`` (23 t.co characters) whatever its true length, and — because the
config sets ``emojiParsingEnabled: true`` — every emoji cluster (however many code points
or zero-width joiners compose it) counts as ONE code point at the default weight of 200.
The total divides by the config's scale of 100.
"""
from __future__ import annotations

import math
import re

PLATFORM_POLICY_VERSION = "x-platform-2026-09"

# --- Pinned constraints (official sources, verified 2026-09-04) --------------------
#
# SOURCES maps every pinned fact to the official page it was verified against (help.x.com
# pages verified via Internet Archive snapshots of the official pages; twitter-text fetched
# directly from GitHub). If a fact cannot be verified from an official page, it does not
# belong in this module — see UNSPECIFIED below.
SOURCES: dict[str, str] = {
    "standard_post_limit": "https://help.x.com/en/using-x/how-to-post (2026-08-19 snapshot)",
    "premium_long_post_limit": "https://help.x.com/en/using-x/types-of-posts (2026-07-26 snapshot)",
    "long_post_premium_eligibility": "https://help.x.com/en/using-x/x-premium (2026-08-31 snapshot)",
    "long_post_mobile_limit": "https://help.x.com/en/using-x/how-to-post (2026-08-19 snapshot)",
    "url_accounting": "https://developer.twitter.com/en/docs/basics/counting-characters + twitter-text config/v3.json",
    "article_limits": "https://help.x.com/en/using-x/articles (2026-05-28 snapshot)",
    "article_eligibility": "https://help.x.com/en/using-x/articles (2026-05-28 snapshot)",
    "thread_compose": "https://help.x.com/en/using-x/create-a-thread (2025-12-02 snapshot)",
    "twitter_text": "https://github.com/twitter/twitter-text (config/v3.json, blob 358aface)",
}

STANDARD_POST_LIMIT = 280
PREMIUM_LONG_POST_LIMIT = 25_000
# The official how-to-post page documents 4,000 characters in the iOS/Android compose
# flow versus 25,000 on web (2026-08-19 snapshot). Both are official; the binding limit
# for a draft is the smaller one the operator's target surface may enforce.
LONG_POST_MOBILE_APP_LIMIT = 4_000
# t.co wrapping: every URL counts as 23 characters whatever its true length.
URL_ACCOUNTED_CHARS = 23

# What official documentation does NOT specify. The policy refuses to invent these and
# reports typed validation_unknown instead:
UNSPECIFIED = (
    "article_character_limit",  # no official character/length limit for X Articles
    "thread_maximum_posts",     # no official cap on posts per thread
)

# X Article formatting and media officially supported by the composer (About Articles,
# 2026-05-28 snapshot) — the editorial engine may rely on these and nothing more.
ARTICLE_SUPPORTED_FORMATTING = (
    "headings", "subheadings", "bold", "italics", "strikethrough",
    "indentation", "numbered lists", "bulleted lists",
)
ARTICLE_SUPPORTED_MEDIA = ("images", "video", "GIFs", "posts", "links")
ARTICLE_ELIGIBILITY = "Premium and Premium+ subscribers, Premium Businesses and Premium Organizations"

MODES = ("post", "long_post", "thread", "article")
MODE_LABELS = {
    "post": "standard post",
    "long_post": "Premium long post",
    "thread": "thread (each item is a post)",
    "article": "X Article",
}


def length_limit(mode: str) -> int | None:
    """The per-item character limit for a mode, or None when officially unstated."""
    if mode == "post":
        return STANDARD_POST_LIMIT
    if mode == "long_post":
        return PREMIUM_LONG_POST_LIMIT
    if mode == "thread":
        return STANDARD_POST_LIMIT
    return None  # X Articles: no official character limit exists — never invent one.


def length_limit_with_certainty(mode: str) -> tuple[int | None, str]:
    """(limit, certainty) so callers can return typed validation_unknown instead of guessing.

    - post:    (280, "official")
    - long_post: (25_000, "official_premium") — the limit exists ONLY for eligible Premium
      accounts; eligibility itself is not verifiable locally.
    - thread:  per-item (280, "official") — a thread is composed of posts.
    - article: (None, "validation_unknown") — no official character limit is stated.
    """
    if mode == "post":
        return STANDARD_POST_LIMIT, "official"
    if mode == "long_post":
        return PREMIUM_LONG_POST_LIMIT, "official_premium"
    if mode == "thread":
        return STANDARD_POST_LIMIT, "official"
    return None, "validation_unknown"


# --- twitter-text v3 weighted counting (config/v3.json, blob 358aface) --------------

# The EXACT shipped config: version 3, maxWeightedTweetLength 280, scale 100,
# defaultWeight 200, emojiParsingEnabled true, transformedURLLength 23, and exactly four
# weight-100 ranges. There are no emoji ranges in the config: with emojiParsingEnabled,
# official README — "the weighted Tweet length considers all emoji as a single code point
# (with a default weight of 200), including longer grapheme clusters combined by
# zero-width joiners" — i.e. every emoji cluster weighs 200 (two characters) however many
# code points compose it.
TWITTER_TEXT_CONFIG_VERSION = "3"
_WEIGHTED_100_RANGES: tuple[tuple[int, int], ...] = (
    (0, 4351),
    (8192, 8205),
    (8208, 8223),
    (8242, 8247),
)
_SCALE = 100
_DEFAULT_WEIGHT = 200
_TRANSFORMED_URL_WEIGHT = URL_ACCOUNTED_CHARS * _SCALE

# Emoji cluster: one base emoji code point (the twemoji-supported BMP set, plus the whole
# supplementary emoji plane), then any sequence of variation selectors, skin-tone
# modifiers, and ZWJ-joined further bases — the whole cluster is ONE emoji and weighs 200,
# matching emojiParsingEnabled. Code points above U+1F000 outside the twemoji sets are
# treated as emoji here: in editorial prose that only ever makes counting stricter.
_EMOJI_BASE = (
    "[\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u2199\u21A9\u21AA\u231A\u231B\u2328"
    "\u23CF\u23E9-\u23FA\u24C2\u25AA-\u25AB\u25B6\u25C0\u25FB-\u25FE\u2600-\u2604"
    "\u260E\u2611\u2614-\u2615\u2618\u261D\u2620\u2622\u2623\u2626\u262A\u262E\u262F"
    "\u2638\u2639\u263A\u2640\u2642\u2648-\u2653\u265F\u2660\u2663\u2665\u2666\u2668"
    "\u267B\u267E\u267F\u2692\u2693\u2694\u2696\u2697\u2699\u269B\u269C\u26A0\u26A1"
    "\u26A7\u26AA\u26AB\u26B0\u26B1\u26BD\u26BE\u26C4\u26C5\u26C8\u26CE\u26CF\u26D1"
    "\u26D3\u26D4\u26E9\u26EA\u26F0-\u26F5\u26F7-\u26FA\u26FD\u2702\u2705\u2708-\u270D"
    "\u270F\u2712\u2714\u2716\u271D\u2721\u2728\u2733\u2734\u2744\u2747\u274C\u274E"
    "\u2753\u2754\u2755\u2757\u2763\u2764\u2795\u2796\u2797\u27A1\u27B0\u27BF\u2934"
    "\u2935\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55\u3030\u303D\u3297\u3299"
    "]|[\U0001F000-\U0001FAFF]"
)
_EMOJI_SEQUENCE = re.compile(
    "(?:[#*0-9]\uFE0F?\u20E3)"                       # keycaps
    "|(?:[\U0001F1E6-\U0001F1FF]{2})"                # regional-indicator flags
    "|(?:" + _EMOJI_BASE + "(?:\uFE0F|[\U0001F3FB-\U0001F3FF]|\u200D(?:" + _EMOJI_BASE + "|[#*0-9]))*)"
)

_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# Trailing punctuation that prose puts after a URL without being part of it.
_URL_TRAILING = ".,;:!?'\"”’)]}"
_EMOJI_WEIGHT = 200


def _code_point_weight(cp: int) -> int:
    for start, end in _WEIGHTED_100_RANGES:
        if start <= cp <= end:
            return 100
    return _DEFAULT_WEIGHT


def weighted_length(text: str) -> int:
    """The official twitter-text v3 weighted length of ``text``, in characters.

    URLs (http(s):// or www.) each count as ``URL_ACCOUNTED_CHARS`` whatever their true
    length; every emoji cluster counts as one double-weight code point (two characters)
    however it is composed (emojiParsingEnabled); other code points weigh 100 inside the
    config's four ranges and 200 elsewhere; the total divides by the config's scale.
    """
    cleaned = str(text or "")
    if not cleaned:
        return 0

    total = 0
    last = 0
    for match in _URL_PATTERN.finditer(cleaned):
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in _URL_TRAILING:
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        total += _segment_weight(cleaned[last:match.start()])
        total += _segment_weight(trailing)
        total += _TRANSFORMED_URL_WEIGHT
        last = match.start() + len(match.group(0))
    total += _segment_weight(cleaned[last:])
    return math.ceil(total / _SCALE)


def _segment_weight(segment: str) -> int:
    """Weight one piece of prose: emoji clusters at 200, other code points per ranges."""
    total = 0
    position = 0
    for match in _EMOJI_SEQUENCE.finditer(segment):
        total += sum(_code_point_weight(ord(ch)) for ch in segment[position:match.start()])
        total += _EMOJI_WEIGHT
        position = match.end()
    total += sum(_code_point_weight(ord(ch)) for ch in segment[position:])
    return total


def find_urls(text: str) -> list[str]:
    """URLs as the counting rules see them (trailing punctuation excluded)."""
    found: list[str] = []
    for match in _URL_PATTERN.finditer(str(text or "")):
        raw = match.group(0)
        while raw and raw[-1] in _URL_TRAILING:
            raw = raw[:-1]
        if raw:
            found.append(raw)
    return found


# --- Account capability / eligibility truth -----------------------------------------

# What the runtime can and cannot know about the operator's account. Drafting is
# permission-free; publishing is NOT implemented in this lane (a future separate
# OAuth-bound external effect). These strings are typed guidance, not guesses.
PREMIUM_LONG_POST_NOTE = (
    "The 25,000-character limit applies to eligible X Premium accounts (all tiers); "
    "without Premium the standard 280-character limit applies. The official how-to-post "
    "page documents a 4,000-character compose limit in the iOS/Android apps versus 25,000 "
    "on web — verify your subscription and target surface before relying on long posts."
)
ARTICLE_ELIGIBILITY_NOTE = (
    "Publishing Articles is officially limited to Premium and Premium+ subscribers, "
    "Premium Businesses and Premium Organizations. Verify eligibility with the operator."
)
ARTICLE_UNKNOWN_NOTE = (
    "Official X documentation states no character limit for X Articles; length is "
    "validation_unknown and is not checked."
)

PUBLISH_REFUSAL_GUIDANCE = (
    "I can draft, edit and convert the text, but I cannot publish, schedule or post it. "
    "Publishing is not a capability of this runtime. Use the copy-ready text in your own "
    "X composer."
)


def unsupported_capability_guidance(user_text: str) -> str:
    """Typed guidance when the request asks for a capability this lane must never have."""
    lowered = str(user_text or "").lower()
    publish_shaped = any(
        token in lowered
        for token in ("publish", "schedule this", "schedule it", "auto-post", "autopost",
                      "post it to", "post this to", "send the tweet", "tweet it out", "dm it")
    )
    return PUBLISH_REFUSAL_GUIDANCE if publish_shaped else ""


__all__ = [
    "ARTICLE_ELIGIBILITY",
    "ARTICLE_ELIGIBILITY_NOTE",
    "ARTICLE_SUPPORTED_FORMATTING",
    "ARTICLE_SUPPORTED_MEDIA",
    "ARTICLE_UNKNOWN_NOTE",
    "LONG_POST_MOBILE_APP_LIMIT",
    "MODES",
    "MODE_LABELS",
    "PLATFORM_POLICY_VERSION",
    "PREMIUM_LONG_POST_LIMIT",
    "PREMIUM_LONG_POST_NOTE",
    "PUBLISH_REFUSAL_GUIDANCE",
    "SOURCES",
    "STANDARD_POST_LIMIT",
    "TWITTER_TEXT_CONFIG_VERSION",
    "UNSPECIFIED",
    "URL_ACCOUNTED_CHARS",
    "find_urls",
    "length_limit",
    "length_limit_with_certainty",
    "unsupported_capability_guidance",
    "weighted_length",
]
