"""Strict server-side comment filter. The browser never decides what is published.

Clear violations are refused outright. Text the English term lists cannot judge
(mostly non-Latin script, shouting, character floods) is held for a maintainer.
"""
from __future__ import annotations

import re
import unicodedata

MAX_COMMENT = 1000
REFUSAL = ("Comments can't include links, contact details, promotions, crypto addresses or keys, "
           "or offensive, sexual, scam or illegal content.")

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_INVISIBLE = re.compile(r"[­͏؜ᅟᅠ឴឵᠋-᠏​-‏‪-‮"
                        r"⁠-⁯ㅤ︀-️﻿ﾠ]")

# Whole words after normalization (leet folded, simple plural/verb endings removed).
_WORDS = {
    "offensive": {
        "fuck", "fucking", "fucker", "motherfucker", "shit", "shitty", "bullshit", "bitch", "cunt", "asshole",
        "arsehole", "dick", "dickhead", "bastard", "wanker", "twat", "slut", "whore", "prick", "cock", "pussy",
        "retard", "retarded", "faggot", "fag", "nigger", "nigga", "kike", "spic", "chink", "tranny", "kys",
    },
    "sexual": {
        "porn", "porno", "pornhub", "xxx", "nude", "nudes", "nsfw", "hentai", "onlyfans", "escort",
        "camgirl", "blowjob", "handjob", "milf", "sexting", "horny", "fetish", "loli", "csam",
    },
    "illegal": {
        "cocaine", "heroin", "meth", "methamphetamine", "mdma", "fentanyl", "ketamine", "carding", "cvv",
        "fullz", "counterfeit", "hitman", "ransomware", "keylogger", "botnet", "doxx", "dox",
    },
    "scam": {
        "airdrop", "giveaway", "presale", "rugpull",
    },
}
# Phrases matched on the normalized, single-spaced text.
_PHRASES = {
    "scam": ("seed phrase", "recovery phrase", "secret phrase", "private key", "wallet connect", "connect your wallet",
             "connect wallet", "double your", "guaranteed profit", "guaranteed return", "free crypto", "free sol",
             "free eth", "free btc", "free usdt", "free token", "free nft", "claim your", "claim now", "send sol",
             "send eth", "send btc", "send usdt", "investment opportunity", "passive income", "100x", "1000x"),
    "contact": ("join my telegram", "telegram group", "telegram channel", "whatsapp group", "join my whatsapp",
                "discord server", "join my discord", "t me", "wa me", "dm me", "pm me", "message me", "inbox me", "contact me", "text me", "call me", "add me"),
    "promo": ("buy now", "promo code", "discount code", "coupon", "use code", "check out my", "subscribe to my",
              "follow me", "follow my", "visit my", "my channel", "limited offer", "limited time", "click here",
              "sign up now", "earn money", "make money"),
    "sexual": ("sex cam", "sex chat", "hot singles", "adult content", "hook up", "hookup"),
    "illegal": ("child porn", "stolen card", "stolen account", "fake passport", "fake id", "buy drugs",
                "ddos for hire", "hack any account", "hack your", "hacked account", "kill yourself"),
}
_PATTERNS = {
    "link": re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|\b[a-z0-9-]+\s?(?:\.|\(dot\)|\[dot\])\s?"
                       r"(?:com|net|org|io|xyz|ru|cn|me|gg|co|app|dev|ly|link|site|online|shop|store|top|info|biz|"
                       r"tk|ml|ga|cf|click|live|fun|vip|club|pro|finance|exchange|cash|money)\b)"),
    "contact": re.compile(r"(?i)[\w.+-]+@[\w-]+\.[\w.-]+|(?<![\w@])@[a-z0-9_]{2,}|(?:\+?\d[\s().-]?){8,}"),
    "crypto": re.compile(r"\b0x[0-9a-fA-F]{40}\b|\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b|\bbc1[0-9a-z]{25,62}\b|"
                         r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"),
    "defanged": re.compile(r"(?i)[\[({]\s*(?:\.|dot|slash|/)\s*[\])}]|\bhxxps?\b"),
}
# Links spelled out in words, matched on the normalized, single-spaced text:
# "example dot com slash review", "example (dot) com", "https colon slash slash".
# Endings that are everyday English after "dot" or before "slash" (top, me, live...) are left out.
_SPOKEN_TLDS = ("com|net|org|io|xyz|ru|cn|gg|co|app|dev|ly|site|online|shop|store|info|biz|tk|ml|ga|cf|click|vip|"
                "club|finance|exchange|ai|tv|cc|uk|de|cloud|tech")
_SLASH_TLDS = "com|net|org|io|xyz|ru|cn|gg|ly|ai|tv|cc|uk|de|dev|app|site|online|info|biz"
_SPELLED = re.compile(r"(?<!\w)(?:[a-z0-9]+ dot (?:" + _SPOKEN_TLDS + r")|dot ?(?:com|net|org|io)|(?:" +
                      _SLASH_TLDS + r") slash|slash slash|colon slash|https? colon)(?!\w)")


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text)).casefold()
    return folded.translate(_LEET)


def _stems(word: str) -> set[str]:
    stems = {word, re.sub(r"(.)\1+", r"\1", word)}
    for ending in ("ing", "ers", "er", "ed", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            stems.add(word[:-len(ending)])
    return stems


def _words(normal: str) -> list[str]:
    tokens = re.findall(r"[^\W_]+", normal)
    # "f u c k" / "f.u.c.k": join runs of single letters into one word.
    joined, run = [], []
    for token in [*tokens, ""]:
        if len(token) == 1:
            run.append(token)
            continue
        if len(run) >= 3:
            joined.append("".join(run))
        run = []
        if token:
            joined.append(token)
    return joined


def review(text: object) -> tuple[str, str]:
    """Return ("refused", reason), ("held", reason) or ("published", "")."""
    if not isinstance(text, str):
        return "refused", "comment text required"
    body = text.strip()
    if not body or len(body) > MAX_COMMENT:
        return "refused", f"comments are 1 to {MAX_COMMENT} characters"
    raw = unicodedata.normalize("NFKC", _INVISIBLE.sub("", body))
    if any(unicodedata.category(char) in {"Cc", "Cf", "Co", "Cs"} and char != "\n" for char in raw):
        return "refused", "comments are plain text"
    if "\n\n\n" in body:
        return "refused", "too many blank lines"
    for pattern in _PATTERNS.values():
        if pattern.search(raw):
            return "refused", REFUSAL
    normal = normalize(body)
    spaced = " ".join(re.findall(r"[^\W_]+", normal))
    words = set()
    for word in _words(normal):
        words |= _stems(word)
    for terms in _WORDS.values():
        if words & terms:
            return "refused", REFUSAL
    if _SPELLED.search(spaced):
        return "refused", REFUSAL
    for phrases in _PHRASES.values():
        if any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", spaced) for phrase in phrases):
            return "refused", REFUSAL
    letters = [char for char in body if char.isalpha()]
    if letters:
        latin = sum(1 for char in letters if "LATIN" in unicodedata.name(char, ""))
        if latin * 2 < len(letters):
            return "held", "waiting for review"
        upper = sum(1 for char in letters if char.isupper())
        if len(letters) >= 20 and upper * 10 >= len(letters) * 7:
            return "held", "waiting for review"
    if re.search(r"(.)\1{5,}", body):
        return "held", "waiting for review"
    return "published", ""
