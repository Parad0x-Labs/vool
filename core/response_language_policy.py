"""One canonical typed response-language policy for ordinary plain-text chat.

Precedence (highest first), decided in :func:`response_language_policy_for_text`:

1. **explicit_output_request** — the turn itself names the output language
   (``Answer in Lithuanian``, ``Atsakyk lietuviškai``, ``日本語で答えて``). Detection runs on
   the turn with QUOTED MATERIAL REMOVED (quotation spans and text after a meta-linguistic
   lead-in like "explain the sentence:"), and a candidate instruction preceded by a NEGATION
   ("don't answer in Japanese") is rejected: quoted subjects and negated instructions are not
   output commitments.
2. **operator_preference** — the applicable scoped operator profile ``language`` item
   (chat > project > context > global via ``core.operator_profile.resolve``), consulted only
   when the turn names no language itself.
3. **evidenced_input_language** / **english_user_turn** — evidence the table below marks
   EXCLUSIVE to one language. SHARED SCRIPTS ARE NOT AUTHORITY: Cyrillic, Arabic script,
   Devanagari and Han are each shared by several languages and resolve nothing by themselves —
   they resolve only through letters that decide WHICH language the script is
   (ы→ru, є→uk, ў→be, پگچژ→fa, ...; one such letter suffices *because the script is already
   established*). Unique scripts (kana, hangul, Thai, Greek, Hebrew, Armenian, Ethiopic,
   Georgian) resolve their language directly. In the Latin family, two DISTINCT letters
   exclusive to a language are required — naturalized loanwords (ñ in "señor", ã in "São") are
   names, not authority. Below that bar the policy resolves to NO expectation: uncertain input
   is never silently forced to English or to anything else. Pure Han (no kana) is ambiguous
   between Chinese and Japanese; Dutch, Estonian, French, Swahili, Zulu and their neighbours
   have no exclusive orthography at all and therefore cannot be inferred — only requested or
   preferred.

Exempt from the expectation entirely (existing behavior, preserved): translation/bilingual /
multilingual requests, turns carrying code fences, and empty turns.

The output check is a SCRIPT-FAMILY guard, not a language classifier: each expectation belongs
to a script family (en/lt/es/... → Latin; ru/uk/... → Cyrillic; ja/zh → CJK; ko → Hangul; ...),
and an answer is rejected when its DOMINANT family differs from the expected family — a Russian
answer violates a Japanese expectation exactly as a CJK answer violates an English one. Code,
quoted documents, filenames and amounts are removed or bounded before measuring, so strict
literal bytes keep their existing safeguards.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")

#: Closed, typed support table: BCP-47 primary tag -> English names, common endonyms and
#: scoped-preference spellings. One table serves the explicit-request tier, operator-preference
#: normalization and instruction wording. This is a LANGUAGE table, not a translated-phrase
#: dictionary: intent still comes from the user's own words, never from enumerated phrases.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "lt": "Lithuanian", "lv": "Latvian", "et": "Estonian",
    "pl": "Polish", "de": "German", "it": "Italian", "fr": "French", "nl": "Dutch",
    "pt": "Portuguese", "es": "Spanish", "sv": "Swedish", "da": "Danish", "nb": "Norwegian",
    "fi": "Finnish", "cs": "Czech", "sk": "Slovak", "hu": "Hungarian", "ro": "Romanian",
    "el": "Greek", "tr": "Turkish", "uk": "Ukrainian", "ru": "Russian", "be": "Belarusian",
    "bg": "Bulgarian", "sr": "Serbian", "mk": "Macedonian", "hr": "Croatian",
    "he": "Hebrew", "ar": "Arabic", "fa": "Persian", "ur": "Urdu", "hi": "Hindi",
    "bn": "Bengali", "ta": "Tamil", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian",
    "ms": "Malay", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
    "sw": "Swahili", "ha": "Hausa", "yo": "Yoruba", "ig": "Igbo", "zu": "Zulu",
    "am": "Amharic", "ka": "Georgian", "hy": "Armenian",
}
_LANGUAGE_ALIASES: dict[str, str] = {
    "english": "en", "lithuanian": "lt", "lietuviu": "lt", "lietuvių": "lt",
    "lietuviu kalba": "lt", "lietuvių kalba": "lt",
    "latvian": "lv", "latviesu": "lv", "latviešu": "lv",
    "estonian": "et", "eesti": "et", "eesti keel": "et",
    "polish": "pl", "polski": "pl",
    "german": "de", "deutsch": "de",
    "italian": "it", "italiano": "it",
    "french": "fr", "français": "fr", "francais": "fr",
    "dutch": "nl", "nederlands": "nl", "flemish": "nl",
    "portuguese": "pt", "português": "pt", "portugues": "pt",
    "brazilian portuguese": "pt", "brasilian portuguese": "pt",
    "spanish": "es", "espanol": "es", "español": "es", "castellano": "es",
    "swedish": "sv", "svenska": "sv", "danish": "da", "dansk": "da",
    "norwegian": "nb", "norsk": "nb", "finnish": "fi", "suomi": "fi",
    "czech": "cs", "cesky": "cs", "čeština": "cs", "slovak": "sk", "slovensky": "sk",
    "hungarian": "hu", "magyar": "hu", "romanian": "ro", "română": "ro", "romana": "ro",
    "greek": "el", "turkish": "tr", "türkçe": "tr", "turkce": "tr",
    "ukrainian": "uk", "українська": "uk", "russian": "ru", "русский": "ru",
    "belarusian": "be", "беларуская": "be", "bulgarian": "bg", "български": "bg",
    "serbian": "sr", "српски": "sr", "macedonian": "mk", "македонски": "mk",
    "croatian": "hr", "hrvatski": "hr",
    "hebrew": "he", "עברית": "he", "arabic": "ar", "العربية": "ar",
    "persian": "fa", "farsi": "fa", "فارسی": "fa", "urdu": "ur", "اردو": "ur",
    "hindi": "hi", "हिन्दी": "hi", "bengali": "bn", "bangla": "bn", "বাংলা": "bn",
    "tamil": "ta", "தமிழ்": "ta", "thai": "th", "ไทย": "th",
    "vietnamese": "vi", "tiếng việt": "vi", "indonesian": "id", "bahasa indonesia": "id",
    "malay": "ms", "bahasa melayu": "ms",
    "japanese": "ja", "nihongo": "ja", "日本語": "ja",
    "chinese": "zh", "mandarin": "zh", "中文": "zh", "simplified chinese": "zh",
    "traditional chinese": "zh", "taiwanese mandarin": "zh",
    "korean": "ko", "한국어": "ko",
    "swahili": "sw", "kiswahili": "sw", "hausa": "ha", "harshen hausa": "ha",
    "yoruba": "yo", "igbo": "ig", "asụsụ igbo": "ig", "zulu": "zu", "isizulu": "zu",
    "amharic": "am", "አማርኛ": "am", "georgian": "ka", "ქართული": "ka",
    "armenian": "hy",
}

#: Script FAMILY of each expectation and of the answer's letters. The guard compares DOMINANT
#: answer family with the expected family; languages sharing a family (en/es, ru/uk, ja/zh)
#: are honestly indistinguishable at script level and share the family's verdict.
_LANGUAGE_FAMILY: dict[str, str] = {}
for _codes, _family in (
    (("en", "lt", "lv", "et", "pl", "de", "it", "fr", "nl", "pt", "es", "sv", "da", "nb",
      "fi", "cs", "sk", "hu", "ro", "el", "tr", "hr", "vi", "id", "ms", "sw", "ha", "yo",
      "ig", "zu"), "latin"),
    (("uk", "ru", "be", "bg", "sr", "mk"), "cyrillic"),
    (("ar", "fa", "ur"), "arabic"),
    (("hi",), "devanagari"),
    (("bn",), "bengali"),
    (("ta",), "tamil"),
    (("th",), "thai"),
    (("ja", "zh"), "cjk"),
    (("ko",), "hangul"),
    (("he",), "hebrew"),
    (("am",), "ethiopic"),
    (("ka",), "georgian"),
    (("hy",), "armenian"),
):
    for _code in _codes:
        _LANGUAGE_FAMILY[_code] = _family

#: Answer-side letter buckets: one regular expression per script family.
_ANSWER_FAMILY_PATTERNS: dict[str, re.Pattern[str]] = {
    "latin": re.compile(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]"),
    "cyrillic": re.compile(r"[\u0400-\u04ff]"),
    "arabic": re.compile(r"[\u0600-\u06ff\u0750-\u077f]"),
    "devanagari": re.compile(r"[\u0900-\u097f]"),
    "bengali": re.compile(r"[\u0980-\u09ff]"),
    "tamil": re.compile(r"[\u0b80-\u0bff]"),
    "thai": re.compile(r"[\u0e00-\u0e7f]"),
    "kana": re.compile(r"[\u3040-\u30ff]"),
    "han": re.compile(r"[\u3400-\u9fff\uf900-\ufaff]"),
    "hangul": re.compile(r"[\uac00-\ud7af\u1100-\u11ff]"),
    "greek": re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]"),
    "hebrew": re.compile(r"[\u0590-\u05ff]"),
    "armenian": re.compile(r"[\u0530-\u058f]"),
    "ethiopic": re.compile(r"[\u1200-\u137f]"),
    "georgian": re.compile(r"[\u10a0-\u10ff]"),
}
_MIN_UNEXPECTED_SCRIPT_LETTERS = 4
_UNEXPECTED_SCRIPT_DOMINANCE = 0.60

#: Input-evidence authority. A language resolves ONLY on evidence exclusive to it:
#:  - ``script``: a script nothing else uses — two distinct runs (>=2 chars) resolve it.
#:  - ``letters`` + ``min_distinct``: letters/marks exclusive within the supported set.
#:    Latin letters need TWO distinct (loanwords like ñ/ã are names, not authority);
#:    script-deciding Cyrillic/Arabic letters and prose marks (¿ ¡ ß) need ONE, because the
#:    script (or the prose convention) is already established and they only say WHICH language.
_INPUT_EVIDENCE: dict[str, dict[str, Any]] = {
    # unique scripts
    "ja": {"script": re.compile(r"[\u3040-\u30ff]{2,}")},
    "ko": {"script": re.compile(r"[\uac00-\ud7af]{2,}")},
    "th": {"script": re.compile(r"[\u0e00-\u0e7f]{2,}")},
    "el": {"script": re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]{2,}")},
    "he": {"script": re.compile(r"[\u0590-\u05ff]{2,}")},
    "hy": {"script": re.compile(r"[\u0530-\u058f]{2,}")},
    "am": {"script": re.compile(r"[\u1200-\u137f]{2,}")},
    "ka": {"script": re.compile(r"[\u10a0-\u10ff]{2,}")},
    "bn": {"script": re.compile(r"[\u0980-\u09ff]{2,}")},
    "ta": {"script": re.compile(r"[\u0b80-\u0bff]{2,}")},
    # shared-script deciding letters (one suffices; the script is already established)
    "uk": {"letters": "їєґ", "min_distinct": 1},
    "ru": {"letters": "ыэъ", "min_distinct": 1},
    "be": {"letters": "ў", "min_distinct": 1},
    "sr": {"letters": "ђћџњљ", "min_distinct": 1},
    "mk": {"letters": "ѓѕќ", "min_distinct": 1},
    "fa": {"letters": "پچژگ", "min_distinct": 1},
    "ur": {"letters": "ٹڈڑںے", "min_distinct": 1},
    # Latin languages with genuinely exclusive letters (two DISTINCT required)
    "lt": {"letters": "ėįų", "min_distinct": 2},
    "pl": {"letters": "łńśźżć", "min_distinct": 2},
    "lv": {"letters": "āēīģķļņ", "min_distinct": 2},
    "cs": {"letters": "ěřů", "min_distinct": 2},
    "hu": {"letters": "őű", "min_distinct": 2},
    "ro": {"letters": "ășț", "min_distinct": 2},
    "tr": {"letters": "ğış", "min_distinct": 2},
    "it": {"letters": "òì", "min_distinct": 2},
    "vi": {"letters": "ơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđ", "min_distinct": 2},
    "ha": {"letters": "ɓɗƙƴ", "min_distinct": 2},
    "yo": {"letters": "ẹṣ", "min_distinct": 2},
    "ig": {"letters": "ịụ", "min_distinct": 2},
    # German ß: a single unambiguous prose mark — never a loanword in another language.
    "de": {"letters": "ß", "min_distinct": 1},
    # Spanish ¿ ¡: inverted punctuation exists in no other prose convention. ñ is deliberately
    # ABSENT: it is naturalized in English loanwords (señor, jalapeño, El Niño) and is a name,
    # not authority.
    "es": {"letters": "¿¡", "min_distinct": 1},
    # pt: ã and õ are both required — either alone appears in quoted names (São, São Paulo).
    "pt": {"letters": "ãõ", "min_distinct": 2},
    # Languages with NO exclusive orthography (nl, et, fr, sv, da, nb, fi, en, sw, zu, id, ms,
    # bg, sk, hr) resolve NOTHING from input evidence. They remain fully supported through the
    # explicit-request and operator-preference tiers.
}

_REQUESTED_LANGUAGE = (
    r"(?:chinese|japanese|korean|spanish|french|german|italian|portuguese|arabic|"
    r"russian|ukrainian|hindi|thai|greek|hebrew|armenian|lithuanian)"
)
_TRANSLATION_KEYWORDS = re.compile(
    r"\b(?:translate|translation|translated|bilingual|multilingual)\b",
    re.IGNORECASE,
)
# The remaining legacy exemption forms (kept verbatim minus the keywords split out above):
# "in/into/using <language>" and "in both English and <language>".
_TRANSLATION_REQUEST = re.compile(
    rf"\b(?:in|into|using)\s+(?:both\s+)?{_REQUESTED_LANGUAGE}\b|"
    rf"\b(?:in|using)\s+both\s+english\s+and\s+{_REQUESTED_LANGUAGE}\b",
    re.IGNORECASE,
)
_TIER1_VERB = r"(?:answer|reply|respond|write|say|speak)"
# "Answer in <language>" / "respond in <language>" — NOT "in both X and Y" and not a
# bilingual "in X and Y" form. The captured word must be a table name to count.
_TIER1_NAMED_REQUEST = re.compile(
    rf"\b{_TIER1_VERB}\s+(?:it\s+)?in\s+(?!both\b)([a-z]+)\b(?!\s+and\b)",
    re.IGNORECASE,
)
# Unambiguous endonym markers, bounded to languages whose adverbial form is distinctive:
# "in-language" forms that are explicit output requests, not a phrase table.
_TIER1_ENDONYMS = (
    (re.compile(r"\blietuvi[sš]kai\b|\blietuvi[uj]ų kalba\b", re.IGNORECASE), "lt"),
    (re.compile(r"\bauf deutsch\b", re.IGNORECASE), "de"),
    (re.compile(r"日本語で"), "ja"),
    (
        re.compile(
            r"\b(?:answer|reply|respond|write|say|speak|explain|tell)\s+"
            r"(?:it\s+)?(?:en|in)\s+(?:español|espanol|castellano)\b",
            re.IGNORECASE,
        ),
        "es",
    ),
)
# Quoted material is a subject, not an instruction: straight/curly/guillemet spans…
_QUOTED_SPAN = re.compile(
    r'"[^"\n]{1,400}"'              # "..."
    r"|[“][^”\n]{1,400}[”]"         # “…”
    r"|[«][^»\n]{1,400}[»]"         # «…»
)
# …and everything after a meta-linguistic lead-in ("Explain the sentence: …"), where the text
# names a linguistic object rather than commits to an output language.
_METALINGUISTIC_LEADIN = re.compile(
    r"\b(?:sentences?|phrases?|quot(?:e|ed|ation)|words?|lines?|titles?|texts?|utterances?"
    r"|messages?|statements?|questions?|passages?|fragments?|terms?|expressions?)\s*:[^\n]*",
    re.IGNORECASE,
)
# A negation in front of a candidate instruction vetoes it ("Don't answer in Japanese").
_NEGATION_BEFORE = re.compile(
    r"(?:\b(?:do\s+not|don't|dont|did\s+not|didn't|never|stop|not|no)\b[^.!?\n]{0,40})$",
    re.IGNORECASE,
)

# Closed English function-word/request-verb evidence set (exact tokens, case-insensitive).
# Cross-language collisions exist ("a", "me", "no", German "Name"/"was"), so evidence requires
# a high-signal English token plus share/distinctness thresholds — two routes, see
# _english_evidence. Short English questions ("What is a database index?") keep the guard via
# the high-signal route; collision-heavy foreign sentences ("No me lo das a mí") stay unresolved.
_ENGLISH_EVIDENCE_WORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "for",
        "with", "about", "from", "as", "at", "by", "into", "over", "under", "between",
        "because", "while", "during", "without", "within", "than", "then", "so", "too",
        "very", "just", "also", "any", "some", "is", "are", "was", "were", "be", "been",
        "am", "do", "does", "did", "have", "has", "had", "can", "could", "will", "would",
        "should", "may", "might", "must", "shall", "not", "no", "yes", "i", "you", "your",
        "my", "me", "it", "its", "this", "that", "these", "those", "there", "here",
        "what", "which", "who", "whom", "whose", "when", "where", "why", "how", "please",
        "explain", "tell", "show", "give", "help", "need", "want", "know", "think",
        "spelled", "mean", "means",
    }
)
_ENGLISH_HIGH_SIGNAL_WORDS = frozenset(
    {
        "what", "why", "how", "when", "where", "which", "who", "please",
        "explain", "tell", "show", "help", "the",
    }
)
_MIN_ENGLISH_MATCHES = 4
_MIN_DISTINCT_ENGLISH_MATCHES = 3
_MIN_ENGLISH_SHARE = 0.25
_SHORT_TURN_MIN_MATCHES = 3
_SHORT_TURN_MIN_SHARE = 0.30

_ALPHABETIC_TOKEN = re.compile(r"[A-Za-z]+")
_MIN_SCRIPT_CHARS = 6


@dataclass(frozen=True)
class ResponseLanguagePolicy:
    expected_language: str = "none"
    reason: str = "not_applicable"

    def to_dict(self) -> dict[str, str]:
        return {
            "expected_language": self.expected_language,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ResponseLanguageCheck:
    compliant: bool
    violations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "compliant": self.compliant,
            "violations": list(self.violations),
        }


def normalize_language_token(raw: Any) -> str:
    """Map a scoped-preference value (``"Lithuanian"``, ``"lt"``, ``"Lietuvių"``) to a code.

    A value that is not exactly one supported language (multi-language or free text) resolves to
    "" and is skipped — a preference the policy cannot read must never be guessed.
    """
    text = str(raw or "").casefold().strip()
    if not text:
        return ""
    # BCP-47 tags keep their primary subtag ("lt-LT"); parentheticals are commentary.
    text = re.sub(r"\([^)]*\)", " ", text).strip()
    if text in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[text]
    if text in LANGUAGE_NAMES:
        return text
    primary = re.split(r"[-_]", text, maxsplit=1)[0].strip()
    if primary in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[primary]
    if primary in LANGUAGE_NAMES:
        return primary
    return ""


def _operator_language_preference(principal: str, session_id: str) -> str:
    """The applicable scoped operator ``language`` preference, or "" when none applies.

    Fail-soft by design: any profile-store failure costs the preference tier, never the turn.
    """
    if not principal:
        return ""
    try:
        from core.operator_profile import resolve

        item = resolve(str(principal), "language", session_id=str(session_id or ""))
    except Exception:
        return ""
    if item is None:
        return ""
    return normalize_language_token(getattr(item, "value", ""))


def _strip_quoted_material(text: str) -> str:
    """Remove quoted spans and meta-linguistic objects: they are subjects to talk ABOUT,
    never output commitments and not the operator's own prose."""
    stripped = _QUOTED_SPAN.sub(" ", text)
    stripped = _METALINGUISTIC_LEADIN.sub(" ", stripped)
    return stripped


def _explicit_output_language(text: str) -> str:
    for named in _TIER1_NAMED_REQUEST.finditer(text):
        if _NEGATION_BEFORE.search(text[: named.start()]):
            continue
        code = normalize_language_token(named.group(1))
        if code:
            return code
    for pattern, code in _TIER1_ENDONYMS:
        match = pattern.search(text)
        if match and not _NEGATION_BEFORE.search(text[: match.start()]):
            return code
    return ""


def _evidenced_input_language(text: str) -> str:
    """Resolve input language ONLY on evidence exclusive to one language (see table).

    Script evidence is character MASS (>= 6 script characters): Thai and Amharic prose runs
    without spaces are one run, while a stray borrowed glyph or two ("こんにちは friend") is
    neither a sentence nor authority.
    """
    for code, evidence in _INPUT_EVIDENCE.items():
        script = evidence.get("script")
        if script is not None:
            if sum(len(run) for run in script.findall(text)) >= _MIN_SCRIPT_CHARS:
                return code
            continue
        letters = set(evidence["letters"]).intersection(text)
        if len(letters) >= int(evidence["min_distinct"]):
            return code
    if _CJK.search(text):
        # Pure Han (no kana above) is ambiguous between Chinese and Japanese: no silent
        # choice, and no silent English.
        return ""
    return _english_evidence(text)


def _english_evidence(text: str) -> str:
    tokens = _ALPHABETIC_TOKEN.findall(text)
    if len(tokens) < _SHORT_TURN_MIN_MATCHES:
        return ""
    folded = [token.casefold() for token in tokens]
    matches = [token for token in folded if token in _ENGLISH_EVIDENCE_WORDS]
    distinct = set(matches)
    share = len(matches) / len(tokens)
    high_signal = any(token in _ENGLISH_HIGH_SIGNAL_WORDS for token in folded)
    # Full-sentence route: several distinct function words across a quarter of the turn.
    if (
        len(matches) >= _MIN_ENGLISH_MATCHES
        and len(distinct) >= _MIN_DISTINCT_ENGLISH_MATCHES
        and share >= _MIN_ENGLISH_SHARE
    ):
        return "en"
    # Short-question route: a high-signal English token carrying a third of a short turn.
    if high_signal and len(matches) >= _SHORT_TURN_MIN_MATCHES and share >= _SHORT_TURN_MIN_SHARE:
        return "en"
    return ""


def _negated(match: re.Match[str], text: str) -> bool:
    """True when a sentence-bounded look-behind shows the instruction was negated."""
    return bool(_NEGATION_BEFORE.search(text[: match.start()]))


def _negation_vetoed_candidates(text: str, pattern: re.Pattern[str]) -> bool:
    """Every match of ``pattern`` sits behind a negation with no sentence boundary."""
    matches = list(pattern.finditer(text))
    return bool(matches) and all(_negated(match, text) for match in matches)


def response_language_policy_for_text(
    user_text: str,
    *,
    principal: str = "",
    session_id: str = "",
) -> ResponseLanguagePolicy:
    """Derive the typed output-language expectation for THIS turn from the user's own words.

    Callers own identity, not language: the decision is always re-derived from the raw turn and
    never accepted from an API caller. ``principal``/``session_id`` only enable the scoped
    operator-preference tier; without them the derivation is preference-free. The turn is
    NFC-normalized first: composed and decomposed forms of the same letters must decide the
    same way.
    """
    text = unicodedata.normalize("NFC", str(user_text or "").strip())
    if not text:
        return ResponseLanguagePolicy()
    decision_text = _strip_quoted_material(text)
    if not decision_text.strip():
        # The turn is nothing but quoted material: a subject, not a commitment.
        return ResponseLanguagePolicy(reason="quoted_subject")
    if _TRANSLATION_KEYWORDS.search(decision_text):
        return ResponseLanguagePolicy(reason="translation_request")
    if "```" in text:
        return ResponseLanguagePolicy()
    explicit = _explicit_output_language(decision_text)
    if explicit:
        return ResponseLanguagePolicy(
            expected_language=explicit, reason="explicit_output_request"
        )
    if (
        _TRANSLATION_REQUEST.search(decision_text)
        and not _negation_vetoed_candidates(decision_text, _TRANSLATION_REQUEST)
    ):
        return ResponseLanguagePolicy(reason="translation_request")
    preference = _operator_language_preference(principal, session_id)
    if preference:
        return ResponseLanguagePolicy(
            expected_language=preference, reason="operator_preference"
        )
    evidenced = _evidenced_input_language(decision_text)
    if evidenced == "en":
        return ResponseLanguagePolicy(expected_language="en", reason="english_user_turn")
    if evidenced:
        return ResponseLanguagePolicy(
            expected_language=evidenced, reason="evidenced_input_language"
        )
    return ResponseLanguagePolicy(reason="insufficient_evidence")


#: kana and han letters bucket into the CJK expectation family (ja/zh share the script; the
#: guard is a script-family guard and says so honestly).
_ANSWER_BUCKET_TO_FAMILY: dict[str, str] = {
    "kana": "cjk",
    "han": "cjk",
}

def check_response_language(
    text: str,
    policy: dict[str, Any] | None,
) -> ResponseLanguageCheck:
    """Detect an answer whose dominant script family is not the expected one.

    This is intentionally a script-FAMILY guard, not a language classifier. Each expectation
    belongs to a family (``en``/``lt``/``es``/... → Latin; ``ru``/``uk``/... → Cyrillic;
    ``ja``/``zh`` → CJK; ``ko`` → Hangul; ...). A Cyrillic answer to a Japanese expectation is
    exactly as wrong as a CJK answer to an English expectation: in both cases the dominant
    family of the answer differs from the expected family. Languages sharing a family are
    honestly indistinguishable at script level and share the verdict. Code is removed before
    measuring because identifiers and string literals are not response language, and
    translation or bilingual prompts never enable this policy.
    """
    if not isinstance(policy, dict) or policy.get("expected_language", "none") in (
        "",
        "none",
    ):
        return ResponseLanguageCheck(compliant=True)
    expected_family = _LANGUAGE_FAMILY.get(str(policy.get("expected_language")))
    if expected_family is None:
        return ResponseLanguageCheck(compliant=True)
    rendered = str(text or "").strip()
    if not rendered:
        return ResponseLanguageCheck(compliant=True)
    prose = _INLINE_CODE.sub(" ", _CODE_BLOCK.sub(" ", rendered))

    counts: dict[str, int] = {}
    for family, pattern in _ANSWER_FAMILY_PATTERNS.items():
        count = len(pattern.findall(prose))
        if count:
            family = _ANSWER_BUCKET_TO_FAMILY.get(family, family)
            counts[family] = counts.get(family, 0) + count
    measured = sum(counts.values())
    if not measured:
        return ResponseLanguageCheck(compliant=True)
    dominant_family = max(counts, key=lambda name: counts[name])
    dominant_count = counts[dominant_family]
    dominant_share = dominant_count / measured
    if (
        dominant_family != expected_family
        and dominant_count >= _MIN_UNEXPECTED_SCRIPT_LETTERS
        and dominant_share >= _UNEXPECTED_SCRIPT_DOMINANCE
    ):
        return ResponseLanguageCheck(
            compliant=False,
            violations=("unexpected_output_language",),
        )
    return ResponseLanguageCheck(compliant=True)


def response_language_retry_instruction(policy: dict[str, Any] | None) -> str:
    expected = ""
    if isinstance(policy, dict):
        expected = str(policy.get("expected_language") or "")
    if expected == "en":
        return "Return the answer in English only, while preserving the answer's meaning."
    if expected and expected in LANGUAGE_NAMES:
        return (
            f"Return the answer in {LANGUAGE_NAMES[expected]} only, "
            "while preserving the answer's meaning."
        )
    return "Return the answer in the user's language."


def response_language_provider_instruction(policy: dict[str, Any] | None) -> str:
    """The one bounded system-prompt line that carries the policy onto the provider request.

    English keeps its proven post-hoc guard + bounded repair channel and adds no prompt bytes;
    every other typed expectation states itself on the first call, so the model is never left
    to guess an output language the policy has already decided.
    """
    if not isinstance(policy, dict):
        return ""
    expected = str(policy.get("expected_language") or "")
    if expected in {"", "none", "en"}:
        return ""
    name = LANGUAGE_NAMES.get(expected)
    return f"Respond in {name}." if name else ""


def response_language_safe_fallback() -> str:
    return "I couldn't return that response in English. Please try again."
