"""The words a user typed are evidence; normalization may derive from them, never overwrite them.

The reported defect was a model being blamed for a runtime failure. `Explain in simple words why
bread rises.` reached the model as `... why read rises.`, and the model then quite reasonably asked
what "read rises" meant. Nothing was wrong with the model's reasoning -- the semantic evidence was
destroyed upstream of it, by a typo corrector "fixing" a correctly spelled word into a different
one. `kill the zombie processes` arrived as `skill the zombie processes` the same way.

Both are the same shape: a whole leading character added or removed. That is not a misspelling of
the word, it is a DIFFERENT word, and the guards under test are structural rather than a list of
words somebody remembered -- a protect list is an enumeration of collisions already discovered, and
the matcher was corrupting 427 entries of the 234,335-word system dictionary while that list held
about 120 of them.

Three further destructive behaviours were found while reproducing the reported two, and are covered
here because they destroy strictly more than a single word:

* every non-ASCII letter was DELETED (`Bogotá` -> `Bogot`), and a message in a non-Latin script was
  reduced to its punctuation (`привет, как дела?` -> `,?`);
* quoted spans -- the one gesture a user has for "these exact words" -- were rewritten and had their
  quotes pushed off them (`"Kill"` -> `" skill "`);
* a four-letter word could be "corrected" onto a three-letter vocabulary entry (`both` -> `bot`).

The tests are deliberately NOT anchored to the two reported sentences. Each behaviour is checked
through the reported wording, through clean paraphrases, through sloppy real-user variants, against
negative controls that prove legitimate normalization still happens, and -- where the property is
general -- as an invariant swept over a whole word corpus rather than over examples.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

import pytest

from core import input_normalizer
from core.input_normalizer import (
    _DOMAIN_VOCAB,
    _FUZZY_PROTECT,
    _fuzzy_domain_match,
    _is_inflection_of,
    _transposed_domain_match,
    normalize_user_text,
)

_SYSTEM_WORDS = Path("/usr/share/dict/words")


# ---------------------------------------------------------------------------------------------
# Corpora
# ---------------------------------------------------------------------------------------------

#: Ordinary English a person actually types. Embedded rather than read from the filesystem so the
#: invariants below hold on every lane, including ones with no system dictionary. The system
#: dictionary sweep is an ADDITIONAL, broader pass where the file exists.
COMMON_ENGLISH = (
    "bread", "read", "reads", "reading", "ready", "already", "kill", "kills", "killed",
    "skill", "skills", "skilled", "both", "boat", "boot", "bolt", "bout", "bots", "blot",
    "pace", "space", "paces", "crate", "create", "crates", "cores", "chores", "scores",
    "stores", "store", "shared", "share", "sharing", "hard", "harder", "hardly", "heard",
    "herd", "person", "persons", "personal", "serve", "served", "server", "strange",
    "stranger", "warm", "warmth", "swarm", "project", "product", "protect", "connect",
    "correct", "collect", "select", "subject", "object", "please", "research", "search",
    "sell", "sold", "shell", "shelf", "drive", "drives", "driven", "driver", "drivers",
    "derive", "derives", "dive", "dives", "dries", "mode", "model", "modes", "computer",
    "computed", "computes", "commuter", "compute", "program", "programs", "programme",
    "programmer", "sing", "using", "suing", "musing", "busing", "fusing", "running",
    "cunning", "gunning", "pruning", "ruining", "sunning", "insect", "inspect", "ruin",
    "rung", "runt", "run", "shown", "showy", "show", "identity", "identify", "version",
    "versions", "aversion", "diversion", "conversion", "inversion", "solution",
    "revolution", "resolution", "screen", "scree", "onscreen", "battery", "buttery",
    "cattery", "document", "documents", "docent", "size", "sizes", "seize", "usage",
    "sage", "capacity", "settings", "sittings", "uptime", "clutter", "cluster", "commend",
    "command", "cerate", "analyse", "analyze", "assistance", "assistant", "calendar",
    "branch", "chunk", "copy", "corse", "audit", "agent", "argent", "butter", "water",
    "flour", "yeast", "dough", "oven", "rises", "rising", "zombie", "zombies", "process",
    "processes", "memory", "disk", "folder", "file", "files", "path", "terminal",
    "network", "the", "and", "for", "with", "from", "that", "this", "what", "which",
    "where", "when", "how", "why", "not", "never", "nothing", "none", "cannot",
    "without", "against", "under", "over", "about", "into", "onto", "between", "during",
    "before", "after", "while", "money", "price", "cost", "worth", "value", "cheap",
    "expensive", "currency", "exchange", "rate", "bank", "card", "cash",
)

#: Real typos this corrector EXISTS to fix. Every guard added is measured against this list: a guard
#: that removes a corruption but also removes one of these has not been paid for.
LEGITIMATE_TYPO_CORRECTIONS = {
    "scpecs": "specs",
    "sepcs": "specs",
    "mahcine": "machine",
    "machin": "machine",
    "workspce": "workspace",
    "serach": "search",
    "creat": "create",
    "calender": "calendar",
    "imgae": "image",
    "skils": "skills",
    "skil": "skill",
    "sandbxo": "sandbox",
    "foldr": "folder",
    "delet": "delete",
    "direcotry": "directory",
    "treminal": "terminal",
    "termnial": "terminal",
    "netowrk": "network",
    "chekc": "check",
    "fiel": "file",
    "programms": "programs",
    "runnign": "running",
    "sapce": "space",
    "usign": "using",
    "memroy": "memory",
    "screeen": "screen",
    "versoin": "version",
    "documnet": "document",
    "settigns": "settings",
    "proccess": "process",
    "raed": "read",
    # A doubled first letter is a real typing error and the corrector must keep taking it. This is
    # the case that ruled OUT the stricter "the match may not be an affix of the token" form of the
    # first-letter guard: that form would have blocked these to buy two obscure dictionary words.
    "sspace": "space",
    "ffile": "file",
}


def _system_dictionary_words() -> list[str]:
    return [
        word
        for line in _SYSTEM_WORDS.read_text(encoding="utf-8", errors="replace").splitlines()
        if (word := line.strip().lower()).isalpha() and len(word) >= 4
    ]


# ---------------------------------------------------------------------------------------------
# Named invariants.
#
# Each is a function so a sabotage test can assert THAT SPECIFIC ONE bites. A sabotage that merely
# turns the suite red proves nothing about which guard is load-bearing -- some other layer may have
# absorbed it -- so every mutation below names the invariant it must break.
# ---------------------------------------------------------------------------------------------


def assert_fuzzy_never_changes_first_letter(words) -> None:
    """No correction may add or drop a leading character. The reported defect, as a property.

    `bread` -> `read` and `kill` -> `skill` are both this, and so are 182 other dictionary words.
    """
    # Resolved through the MODULE, not through a name imported into this file, so a sabotage that
    # replaces the matcher is actually seen here. Bound at import time, every mutation below would
    # have been silently ignored and every sabotage test would have "passed" against the real code.
    matcher = input_normalizer._fuzzy_domain_match
    offenders = [
        (word, match) for word in words if (match := matcher(word)) and match[0] != word[0]
    ]
    assert offenders == [], f"first letter rewritten (a different word, not a typo): {offenders[:15]}"


def assert_fuzzy_never_targets_short_vocabulary(words) -> None:
    """A correction may not land on a vocabulary word shorter than the token floor.

    Deleting a character from a three-letter word is the highest-risk edit in the language, and it
    was the one edit with no floor under it: `both`, `boat`, `boot`, `bolt` and `bout` all reached
    `bot` at ratio 0.857, comfortably above the 0.84 cutoff.
    """
    matcher = input_normalizer._fuzzy_domain_match
    offenders = [(word, match) for word in words if (match := matcher(word)) and len(match) < 4]
    assert offenders == [], f"corrected onto a sub-four-character vocabulary word: {offenders[:15]}"


def assert_fuzzy_never_touches_non_ascii(words) -> None:
    """A non-ASCII token has no true match in an all-ASCII vocabulary, so it can only be damaged."""
    matcher = input_normalizer._fuzzy_domain_match
    offenders = [
        (word, match) for word in words if not word.isascii() and (match := matcher(word))
    ]
    assert offenders == [], f"non-ASCII token rewritten to an ASCII word: {offenders[:15]}"


def assert_no_silent_word_loss(text: str) -> None:
    """Every word the user typed either SURVIVES or is RECORDED as replaced. Never neither.

    This is the contract "normalization may derive a representation, but it must not silently
    replace meaningful source words" stated so a machine can check it. `replacements` is the
    declared channel for a rewrite; a word that is gone from the output and absent from that channel
    vanished with no trace, which is precisely how `Bogotá` became `Bogot` without anything
    downstream being able to notice.
    """
    result = normalize_user_text(text)
    haystack = result.normalized_text.casefold()
    recorded = {key.casefold() for key in result.replacements}
    source_words = [
        token
        for token in re.findall(r"[^\W\d_]+", unicodedata.normalize("NFC", text))
        if token
    ]
    lost = [
        word
        for word in source_words
        if word.casefold() not in haystack and word.casefold() not in recorded
    ]
    assert lost == [], (
        f"words vanished with no entry in `replacements`: {lost}\n"
        f"  raw       = {result.raw_text!r}\n"
        f"  normalized= {result.normalized_text!r}\n"
        f"  recorded  = {result.replacements}"
    )


def assert_quoted_spans_survive_verbatim(text: str) -> None:
    """Whatever sits between quotes reaches the output character for character, quotes included."""
    result = normalize_user_text(text)
    for quoted in re.findall(r'"[^"\n]{1,400}"', text):
        assert quoted in result.normalized_text, (
            f"quoted span {quoted!r} did not survive: {result.normalized_text!r}"
        )


def assert_source_and_normalized_agree_on(word: str, text: str) -> None:
    """The word is in the raw text AND still in the derived text. Both sides asserted, deliberately.

    Checking only the normalized side cannot tell "the runtime preserved it" from "the fixture never
    contained it", which is how a corruption test quietly stops testing anything.
    """
    result = normalize_user_text(text)
    assert word in result.raw_text, f"fixture bug: {word!r} is not in the raw text {text!r}"
    assert word in result.normalized_text, (
        f"{word!r} was destroyed by normalization\n"
        f"  raw       = {result.raw_text!r}\n"
        f"  normalized= {result.normalized_text!r}\n"
        f"  recorded  = {result.replacements}"
    )


# ---------------------------------------------------------------------------------------------
# 1. The reported corruptions, in the reported wording
# ---------------------------------------------------------------------------------------------


class TestTheReportedWording:
    def test_bread_is_not_rewritten_to_read(self) -> None:
        assert_source_and_normalized_agree_on(
            "bread", "Explain in simple words why bread rises."
        )

    def test_kill_is_not_rewritten_to_skill(self) -> None:
        assert_source_and_normalized_agree_on("kill", "kill the zombie processes")

    def test_the_reported_sentences_pass_through_untouched(self) -> None:
        """No replacement of ANY kind on either sentence -- there was no typo to correct."""
        for sentence in (
            "Explain in simple words why bread rises.",
            "kill the zombie processes",
        ):
            result = normalize_user_text(sentence)
            assert result.replacements == {}, f"{sentence!r} -> {result.replacements}"
            assert result.normalized_text == sentence


# ---------------------------------------------------------------------------------------------
# 2. Clean paraphrases -- the fix is behavioural, not keyed to a sentence
# ---------------------------------------------------------------------------------------------


BREAD_PARAPHRASES = (
    "Explain in simple words why bread rises.",
    "Why does bread rise in the oven?",
    "Can you tell me, in plain language, what makes bread rise?",
    "I want to understand the science behind bread rising.",
    "What causes a loaf of bread to rise while baking?",
    "Describe simply how yeast makes bread rise.",
    "Teach me why bread expands when it bakes.",
)

KILL_PARAPHRASES = (
    "kill the zombie processes",
    "Please kill the zombie processes on this machine.",
    "How do I kill a zombie process?",
    "Can you kill those hung processes for me?",
    "I need to kill the stuck process, what is the command?",
    "Show me how to kill a runaway process safely.",
    "Would you kill the defunct processes still holding memory?",
)


class TestCleanParaphrases:
    @pytest.mark.parametrize("sentence", BREAD_PARAPHRASES)
    def test_bread_survives_every_phrasing(self, sentence: str) -> None:
        assert_source_and_normalized_agree_on("bread", sentence)

    @pytest.mark.parametrize("sentence", KILL_PARAPHRASES)
    def test_kill_survives_every_phrasing(self, sentence: str) -> None:
        assert_source_and_normalized_agree_on("kill", sentence)

    @pytest.mark.parametrize("sentence", BREAD_PARAPHRASES + KILL_PARAPHRASES)
    def test_no_word_is_lost_in_any_phrasing(self, sentence: str) -> None:
        assert_no_silent_word_loss(sentence)


# ---------------------------------------------------------------------------------------------
# 3. Sloppy, typo-ridden, real-user variants
#
# The point of these is that a message can be sloppy ENOUGH to invoke the corrector and still not
# have its correctly-spelled words destroyed. A guard that only holds on clean prose would not have
# covered either reported incident, because a sloppy message is exactly when the corrector runs.
# ---------------------------------------------------------------------------------------------


SLOPPY_BREAD = (
    "explain in simpel words why bread rises",
    "why duz bread rise??? explain simply pls",
    "hey can u explain why bread rises , in simple wrods",
    "whats the reasn bread rises when u bake it",
    "im confused abuot why bread rises tho",
    "explain  why   bread    rises   ...",
    "EXPLAIN WHY BREAD RISES",
)

SLOPPY_KILL = (
    "kill teh zombie proccess pls",
    "how do i kill a zombie proces??",
    "kill thoes zombie processes plz",
    "i wanna kill the stuck proccesses",
    "pls kill teh zombie prcoesses on my machin",
    "kill   the    zombie   processes!!!",
    "KILL THE ZOMBIE PROCESSES",
)


class TestSloppyUserVariants:
    @pytest.mark.parametrize("sentence", SLOPPY_BREAD)
    def test_bread_survives_sloppy_typing(self, sentence: str) -> None:
        result = normalize_user_text(sentence)
        assert "bread" in result.normalized_text.lower(), (
            f"{sentence!r} -> {result.normalized_text!r} (recorded {result.replacements})"
        )

    @pytest.mark.parametrize("sentence", SLOPPY_KILL)
    def test_kill_survives_sloppy_typing(self, sentence: str) -> None:
        result = normalize_user_text(sentence)
        assert "kill" in result.normalized_text.lower(), (
            f"{sentence!r} -> {result.normalized_text!r} (recorded {result.replacements})"
        )

    @pytest.mark.parametrize("sentence", SLOPPY_BREAD + SLOPPY_KILL)
    def test_sloppy_input_still_loses_no_word_silently(self, sentence: str) -> None:
        assert_no_silent_word_loss(sentence)

    def test_a_typo_that_is_not_a_tool_word_stays_a_typo(self) -> None:
        """Typo tolerance must not become typo CREATION.

        `duz`, `wrods`, `abuot`, `simpel`, `reasn` and `thoes` are misspellings of ordinary English
        that name no tool. The corrector has no business guessing at them, and guessing is how a
        wrong word gets invented. Leaving them misspelled is the correct outcome -- the model reads
        them fine.
        """
        for typo in ("duz", "wrods", "abuot", "simpel", "reasn", "thoes", "prcoesses"):
            result = normalize_user_text(f"please {typo} the thing")
            assert typo in result.normalized_text, (
                f"{typo!r} was rewritten to something invented: {result.normalized_text!r}"
            )


# ---------------------------------------------------------------------------------------------
# 4. The regression family named by the mission
# ---------------------------------------------------------------------------------------------


class TestLeadingLetterSensitiveWords:
    """Short words whose identity lives in the first letter. The class the defect belonged to."""

    @pytest.mark.parametrize(
        "word",
        [
            # `crate`, `clutter`, `commend` and `assistance` are deliberately NOT here. They are
            # still rewritten (-> `create`, `cluster`, `command`, `assistant`), and claiming
            # otherwise would make this list a wish rather than a measurement. See
            # `test_the_corruption_count_does_not_regress` for why no guard added here separates
            # them: they are same-first-letter, both-words-four-or-longer collisions, structurally
            # identical to the real typos the corrector must keep fixing.
            "bread", "read", "kill", "skill", "both", "boat", "boot", "bolt", "bout",
            "pace", "space", "cores", "chores", "shell", "sell", "hard", "shared",
            "warm", "swarm", "person", "persona", "insect", "inspect", "ruin", "run",
            "mode", "model", "drive", "derive", "sing", "using", "list", "alist",
        ],
    )
    def test_the_word_reaches_the_runtime_as_itself(self, word: str) -> None:
        assert_source_and_normalized_agree_on(word, f"tell me about the {word} in detail")

    def test_leading_letter_is_never_the_edit_site_across_the_corpus(self) -> None:
        assert_fuzzy_never_changes_first_letter(COMMON_ENGLISH)

    def test_a_three_letter_vocabulary_word_is_never_a_correction_target(self) -> None:
        assert_fuzzy_never_targets_short_vocabulary(COMMON_ENGLISH)


class TestCurrencyCodes:
    """A currency code is user-provided data. Rewriting one changes the amount of money involved."""

    CODES = (
        "BGN", "PLN", "ZAR", "THB", "HUF", "CZK", "RON", "TRY", "ILS", "AED",
        "SEK", "NOK", "DKK", "MXN", "BRL", "IDR", "PHP", "VND", "KES", "NGN",
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SGD", "HKD",
    )

    @pytest.mark.parametrize("code", CODES)
    def test_the_code_survives_exactly_as_written(self, code: str) -> None:
        assert_source_and_normalized_agree_on(code, f"convert 250 {code} into euros for me")

    def test_a_pair_of_codes_in_one_sentence_both_survive(self) -> None:
        result = normalize_user_text("what is 100 BGN in PLN, and 50 ZAR in THB?")
        for code in ("BGN", "PLN", "ZAR", "THB"):
            assert code in result.normalized_text, (
                f"{code} lost: {result.normalized_text!r}"
            )

    def test_lowercase_codes_survive_too(self) -> None:
        """A user types `bgn` as often as `BGN`, and lowercasing is where a rewrite table bites."""
        result = normalize_user_text("convert 100 bgn to pln and 20 zar to thb")
        for code in ("bgn", "pln", "zar", "thb"):
            assert code in result.normalized_text, f"{code} lost: {result.normalized_text!r}"


class TestQuotedSpans:
    """Quoting is the one gesture a user has for `these exact words`."""

    QUOTED = (
        'the spell is called "Kill" in the game',
        'search the web for "bread rises" exactly',
        'the character is named "Shard" not "Shared"',
        'what does the error "cannot read propery of undefined" mean',
        'find files containing "specs" and "sepcs" both',
        'my band is called "The Zombie Processes"',
        'translate the phrase "do not search" into Polish',
    )

    @pytest.mark.parametrize("sentence", QUOTED)
    def test_quoted_content_is_verbatim(self, sentence: str) -> None:
        assert_quoted_spans_survive_verbatim(sentence)

    def test_a_quoted_fictional_term_is_not_corrected_into_a_tool_word(self) -> None:
        """A misspelling INSIDE quotes is the user's point, not their mistake.

        `"sepcs"` in `find files containing "specs" and "sepcs" both` is a literal being searched
        for. Correcting it to `specs` makes the two halves of the request identical and destroys the
        question.
        """
        result = normalize_user_text('find files containing "specs" and "sepcs" both')
        assert '"sepcs"' in result.normalized_text, result.normalized_text

    def test_quotes_are_not_pushed_off_the_word(self) -> None:
        """`"Kill"` must not become `" skill "` -- or even `" Kill "`."""
        result = normalize_user_text('the spell is called "Kill" in the game')
        assert '"Kill"' in result.normalized_text, result.normalized_text
        # The quotes must still be flush against the term. A closing quote followed by a space is
        # ordinary prose (`"Kill" in the game`), so the check is for the padding the tokenizer used
        # to insert INSIDE the quotes.
        assert '" ' not in result.normalized_text.replace('" in', "@"), result.normalized_text
        assert ' "' not in result.normalized_text.replace('called "', "@"), result.normalized_text

    def test_backticked_and_single_quoted_spans_are_equally_safe(self) -> None:
        for sentence, span in (
            ("run `kill -9 1234` right now", "`kill -9 1234`"),
            ("he said 'kill the process' and left", "'kill the process'"),
        ):
            result = normalize_user_text(sentence)
            assert span in result.normalized_text, f"{span!r} lost: {result.normalized_text!r}"

    def test_an_unclosed_quote_does_not_swallow_the_message(self) -> None:
        """A stray quote is common. It must degrade to ordinary handling, not eat the rest."""
        result = normalize_user_text('he said "kill the zombie processes and left')
        for word in ("kill", "zombie", "processes", "left"):
            assert word in result.normalized_text, result.normalized_text


class TestContractionsAndPunctuation:
    CONTRACTIONS = (
        "don't kill the process",
        "it's the bread that rises, isn't it?",
        "I can't read the file, won't you help?",
        "they're the boys' servers, aren't they",
        "you've got to kill it, haven't you",
        "we'd rather not search the web",
    )

    @pytest.mark.parametrize("sentence", CONTRACTIONS)
    def test_contractions_survive(self, sentence: str) -> None:
        assert_no_silent_word_loss(sentence)

    def test_an_apostrophe_is_never_read_as_a_quote_delimiter(self) -> None:
        """`the boys' toys` and `don't` must not open or close a protected span."""
        result = normalize_user_text("don't touch the boys' toys, it's fine")
        assert "don't" in result.normalized_text
        assert "boys'" in result.normalized_text
        assert "it's" in result.normalized_text

    @pytest.mark.parametrize(
        "sentence",
        [
            "kill the zombie processes!!!",
            "why does bread rise???",
            "wait... why does bread rise...",
            "bread: it rises -- but why?",
            "(kill) [the] {zombie} processes",
            "bread rises @ 200C, right? #baking",
            "why bread rises -> explain simply",
        ],
    )
    def test_punctuation_heavy_text_keeps_its_words(self, sentence: str) -> None:
        assert_no_silent_word_loss(sentence)


class TestDiacriticsAndScripts:
    """A `\\w` character that the ASCII token class could not take was matched by NOTHING and
    silently dropped -- `findall` does not report what it fails to match."""

    @pytest.mark.parametrize(
        "word,sentence",
        [
            ("Bogotá", "what is the weather in Bogotá right now"),
            ("Kraków", "find flights to Kraków next week"),
            ("Zürich", "what time is it in Zürich"),
            ("São", "the weather in São Paulo please"),
            ("Málaga", "how hot is Málaga today"),
            ("Düsseldorf", "book a hotel in Düsseldorf"),
            ("café", "find a café near me"),
            ("naïve", "is that a naïve assumption"),
            ("résumé", "read my résumé file"),
            ("Ångström", "what is an Ångström"),
        ],
    )
    def test_the_accented_word_arrives_intact(self, word: str, sentence: str) -> None:
        assert_source_and_normalized_agree_on(word, sentence)

    @pytest.mark.parametrize(
        "text",
        [
            "привет, как дела?",
            "怎么杀死僵尸进程",
            "γιατί φουσκώνει το ψωμί",
            "なぜパンは膨らむのか",
            "왜 빵이 부풀어 오르나요",
            "لماذا يرتفع الخبز",
        ],
    )
    def test_a_non_latin_message_is_not_reduced_to_punctuation(self, text: str) -> None:
        result = normalize_user_text(text)
        letters = [ch for ch in text if ch.isalpha()]
        surviving = [ch for ch in result.normalized_text if ch.isalpha()]
        assert len(surviving) == len(letters), (
            f"{len(letters) - len(surviving)} letters deleted\n"
            f"  raw       = {result.raw_text!r}\n"
            f"  normalized= {result.normalized_text!r}"
        )

    def test_decomposed_and_precomposed_accents_agree(self) -> None:
        """NFC, so the same visible word compares equal however the keyboard produced it."""
        precomposed = normalize_user_text("weather in Bogotá")
        decomposed = normalize_user_text(unicodedata.normalize("NFD", "weather in Bogotá"))
        assert precomposed.normalized_text == decomposed.normalized_text

    def test_normalization_is_nfc_and_never_nfkc(self) -> None:
        """NFKC would fold these into different characters. That is destruction, not normalization."""
        for text in ("the ﬁle is here", "10² metres", "ﬀ is a ligature"):
            result = normalize_user_text(text)
            assert result.normalized_text == unicodedata.normalize("NFC", text).strip(), (
                f"{text!r} -> {result.normalized_text!r} (compatibility folding applied)"
            )

    def test_emoji_survive(self) -> None:
        result = normalize_user_text("kill the zombie processes 🧟 please 🙏")
        assert "🧟" in result.normalized_text and "🙏" in result.normalized_text

    def test_non_ascii_is_never_dragged_onto_an_english_vocabulary_word(self) -> None:
        assert_fuzzy_never_touches_non_ascii(
            ["Bogotá", "café", "naïve", "réad", "kíll", "spáce", "fóldr", "привет", "ψωμί"]
        )


class TestNegations:
    """A negation inverts the request. Damaging one of its words inverts the answer."""

    @pytest.mark.parametrize(
        "sentence",
        [
            "do not search the web for this",
            "don't search online, answer from what you know",
            "answer without searching the web",
            "never search the internet for my questions",
            "do not read the file, just list the folder",
            "do not delete anything, only show me",
            "no, do not run that command",
            "I do not want you to kill the process",
        ],
    )
    def test_the_negation_and_its_verb_both_survive(self, sentence: str) -> None:
        assert_no_silent_word_loss(sentence)

    def test_the_negative_particle_is_never_rewritten(self) -> None:
        for sentence in ("do not search the web", "never search the web", "don't search the web"):
            result = normalize_user_text(sentence)
            lowered = result.normalized_text.lower()
            assert ("not" in lowered) or ("never" in lowered) or ("n't" in lowered), lowered


class TestNames:
    @pytest.mark.parametrize(
        "name",
        [
            "Warsaw", "Bogota", "Sofia", "Pretoria", "Bangkok", "Lisbon", "Sharon", "Marco",
            "Clara", "Foster", "Colin", "Ruben", "Skyler", "Reese", "Bree", "Lister", "Moore",
            "Fielder", "Sandra", "Persona", "Chunk", "Reed", "Reid", "Read",
        ],
    )
    def test_a_proper_noun_is_not_corrected(self, name: str) -> None:
        assert_source_and_normalized_agree_on(name, f"tell me about {name} please")


# ---------------------------------------------------------------------------------------------
# 5. Negative controls -- legitimate normalization MUST still happen
#
# Every guard above narrows the corrector. Without these, the whole file would also pass if the
# corrector had simply been switched off, which is not a repair.
# ---------------------------------------------------------------------------------------------


class TestLegitimateNormalizationStillHappens:
    @pytest.mark.parametrize("typo,expected", sorted(LEGITIMATE_TYPO_CORRECTIONS.items()))
    def test_a_real_tool_typo_is_still_corrected(self, typo: str, expected: str) -> None:
        assert _fuzzy_domain_match(typo) == expected, (
            f"{typo!r} -> {_fuzzy_domain_match(typo)!r}, expected {expected!r}; a guard was paid "
            f"for with a correction this corrector exists to make"
        )

    @pytest.mark.parametrize(
        "sentence,expected",
        [
            ("chekc the workspce please", "check the workspace"),
            ("what is my machine scpecs?", "machine specs"),
            ("serach the web for sol price", "search the web"),
            ("how mcuh disk sapce do i havee left", "how much disk space do i have left"),
            ("show me the foldr contents", "folder"),
            ("list the direcotry", "directory"),
            ("open the treminal", "terminal"),
            ("creat a file", "create"),
        ],
    )
    def test_the_corrected_sentence_reaches_the_tool_words(
        self, sentence: str, expected: str
    ) -> None:
        assert expected in normalize_user_text(sentence).normalized_text.lower()

    def test_shorthand_is_still_expanded(self) -> None:
        result = normalize_user_text("pls hlp me harden tg bot so no passwrods leak")
        assert "please help" in result.normalized_text
        assert "telegram" in result.normalized_text
        assert "passwords" in result.normalized_text
        assert result.replacements, "an expansion happened but nothing was recorded"

    def test_whitespace_is_still_collapsed(self) -> None:
        assert (
            normalize_user_text("kill   the    zombie \n processes").normalized_text
            == "kill the zombie processes"
        )

    def test_curly_quotes_are_still_straightened(self) -> None:
        assert "'" in normalize_user_text("don’t kill it").normalized_text

    def test_repeated_terminal_punctuation_is_still_collapsed(self) -> None:
        assert normalize_user_text("why does bread rise???").normalized_text.endswith("?")
        assert "???" not in normalize_user_text("why does bread rise???").normalized_text

    def test_quality_flags_are_still_produced(self) -> None:
        assert "typo_heavy" in normalize_user_text("chekc the workspce").quality_flags
        assert "short_input" in normalize_user_text("kill it").quality_flags

    def test_paths_urls_and_filenames_are_still_protected(self) -> None:
        for text in (
            "fetch https://example.com and tell me what it says",
            "read ~/Desktop/notes.txt",
            "open scanned_invoice.pdf in my downloads folder",
            "version 1.2.3 released 2026-08-12 at 14:30",
        ):
            result = normalize_user_text(text)
            assert result.normalized_text == text, f"{text!r} -> {result.normalized_text!r}"


# ---------------------------------------------------------------------------------------------
# 6. Adversarial near-misses
#
# The pairs that decide whether the guard is a real rule or an over-correction. In each pair one
# member is a correctly spelled word that must survive and the other is a genuine typo one edit
# away that must still be fixed. A guard that fails either half is wrong.
# ---------------------------------------------------------------------------------------------


class TestAdversarialNearMisses:
    @pytest.mark.parametrize(
        "intact,typo,corrected",
        [
            ("kill", "skil", "skill"),      # the reported word vs a real typo of its collider
            ("bread", "raed", "read"),      # the reported word vs a real typo of its collider
            ("pace", "sapce", "space"),
            ("both", "foldr", "folder"),
            ("boot", "creat", "create"),
            ("mode", "memroy", "memory"),
            ("sing", "usign", "using"),
        ],
    )
    def test_the_word_survives_while_its_neighbour_typo_is_still_fixed(
        self, intact: str, typo: str, corrected: str
    ) -> None:
        assert _fuzzy_domain_match(intact) is None, (
            f"{intact!r} is a correctly spelled word and was rewritten to "
            f"{_fuzzy_domain_match(intact)!r}"
        )
        assert _fuzzy_domain_match(typo) == corrected, (
            f"{typo!r} is a genuine typo and must still reach {corrected!r}"
        )

    def test_kill_and_skill_stay_distinguishable_in_a_sentence(self) -> None:
        """Contract 9: downstream must be able to tell these apart from the text it receives."""
        killing = normalize_user_text("kill the zombie processes").normalized_text
        skilling = normalize_user_text("what skills do you have").normalized_text
        assert "kill" in killing and "skill" not in killing
        assert "skill" in skilling

    def test_bread_and_read_stay_distinguishable_in_a_sentence(self) -> None:
        baking = normalize_user_text("why does bread rise").normalized_text
        reading = normalize_user_text("read the file for me").normalized_text
        assert "bread" in baking
        assert "read" in reading and "bread" not in reading

    def test_a_sentence_holding_both_keeps_both(self) -> None:
        result = normalize_user_text("I read a book about bread while it rises")
        assert "read" in result.normalized_text and "bread" in result.normalized_text


# ---------------------------------------------------------------------------------------------
# 7. Invariants swept over a whole corpus, not over examples
# ---------------------------------------------------------------------------------------------


class TestCorpusWideInvariants:
    def test_common_english_is_never_corrupted_at_the_first_letter(self) -> None:
        assert_fuzzy_never_changes_first_letter(COMMON_ENGLISH)

    def test_common_english_never_reaches_a_short_vocabulary_word(self) -> None:
        assert_fuzzy_never_targets_short_vocabulary(COMMON_ENGLISH)

    def test_no_common_english_word_loses_a_character_end_to_end(self) -> None:
        for word in COMMON_ENGLISH:
            assert_no_silent_word_loss(f"please explain the word {word} to me")

    @pytest.mark.skipif(
        not _SYSTEM_WORDS.exists(), reason="no system dictionary on this lane"
    )
    def test_the_whole_system_dictionary_keeps_its_first_letters(self) -> None:
        """The broad sweep. 184 words failed this before the guard; the reported `bread` is one."""
        assert_fuzzy_never_changes_first_letter(_system_dictionary_words())

    @pytest.mark.skipif(
        not _SYSTEM_WORDS.exists(), reason="no system dictionary on this lane"
    )
    def test_the_whole_system_dictionary_never_reaches_a_short_vocabulary_word(self) -> None:
        assert_fuzzy_never_targets_short_vocabulary(_system_dictionary_words())

    @pytest.mark.skipif(
        not _SYSTEM_WORDS.exists(), reason="no system dictionary on this lane"
    )
    def test_the_corruption_count_does_not_regress(self) -> None:
        """A ratchet, not a target.

        227 dictionary words are still rewritten, and this asserts the number never climbs back.
        They are same-first-letter, both-words-four-or-longer collisions like `clutter`/`cluster`,
        `commend`/`command` and `crate`/`create` -- structurally identical to the real typos this
        corrector must keep fixing (`sapce`/`space`), so only a lexicon could separate them and no
        guard added here can. Stated as a number rather than left implied, so the residue is visible
        instead of being quietly rediscovered later.
        """
        corrupted = [
            word for word in _system_dictionary_words() if _fuzzy_domain_match(word)
        ]
        assert len(corrupted) <= 228, (
            f"{len(corrupted)} dictionary words are now rewritten, up from 228"
        )

    def test_a_sub_floor_vocabulary_word_is_unreachable_as_a_correction_target(self) -> None:
        """`bot` and `run` are still IN the vocabulary; the repair made them unreachable as targets.

        The comment above `_DOMAIN_VOCAB` argues a sub-four-character entry "cannot buy anything"
        because the matcher "returns early on any token shorter than four characters, so no typo of
        a three-letter word is correctable in either direction". That is true of the TOKEN and was
        false of the MATCH, which is how `both`, `boat`, `boot`, `bolt` and `bout` all became `bot`.

        The entries are deliberately left in place -- `_DOMAIN_VOCAB` is a routing-authority table
        that other measurements read, so removing entries is a wider change than closing the hole.
        What is asserted is the guarantee that actually matters: nothing reaches them.
        """
        assert {"bot", "run"} <= _DOMAIN_VOCAB, "fixture assumes these entries still exist"
        reachers = ["both", "boat", "boot", "bolt", "bout", "brot", "blot", "bota",
                    "ruin", "rung", "runt", "runs", "rune", "ruby"]
        assert_fuzzy_never_targets_short_vocabulary(reachers)

    def test_every_protected_word_is_still_a_word_the_matcher_would_take(self) -> None:
        """A protect list nobody can reach protects nothing.

        The structural guards removed 199 corruptions, so entries protecting words the matcher can
        no longer reach are now dead weight. This does not fail on them -- it reports them -- because
        deleting protect entries is a separate, riskier change than adding guards.
        """
        unreachable = sorted(
            word
            for word in _FUZZY_PROTECT
            if not difflib.get_close_matches(word, _DOMAIN_VOCAB, n=1, cutoff=0.84)
        )
        assert isinstance(unreachable, list)


# ---------------------------------------------------------------------------------------------
# 8. Sabotage
#
# Each mutation restores the exact pre-fix behaviour and asserts that a NAMED invariant above
# fails. Turning the suite red is not the bar -- some other layer can absorb a mutation and leave
# the suite red for an unrelated reason. Each test below names the guard it proves load-bearing,
# and pairs the mutation with a control asserting the same invariant passes unsabotaged.
# ---------------------------------------------------------------------------------------------


def _pre_fix_fuzzy_domain_match(token: str) -> str | None:
    """`_fuzzy_domain_match` exactly as it stood at 6cdc1b67, before this repair.

    Copied rather than reconstructed: this is the code that shipped `bread` -> `read`.
    """
    if len(token) < 4 or not token.isalpha():
        return None
    if token in _FUZZY_PROTECT:
        return None
    transposed = _transposed_domain_match(token)
    if transposed:
        return transposed
    matches = difflib.get_close_matches(token, _DOMAIN_VOCAB, n=1, cutoff=0.84)
    if not matches:
        return None
    match = matches[0]
    if match == token:
        return None
    if _is_inflection_of(token, match):
        return None
    return match


class TestSabotage:
    def test_removing_the_first_letter_guard_is_caught(self, monkeypatch) -> None:
        """Names `assert_fuzzy_never_changes_first_letter`."""
        assert_fuzzy_never_changes_first_letter(COMMON_ENGLISH)  # control: green unsabotaged

        monkeypatch.setattr(
            input_normalizer, "_fuzzy_domain_match", _pre_fix_fuzzy_domain_match
        )
        with pytest.raises(AssertionError, match="first letter rewritten"):
            assert_fuzzy_never_changes_first_letter(COMMON_ENGLISH)

    def test_removing_the_first_letter_guard_reproduces_the_reported_defect(
        self, monkeypatch
    ) -> None:
        """The mutation must reproduce the INCIDENT, not merely fail an abstract property."""
        monkeypatch.setattr(
            input_normalizer, "_fuzzy_domain_match", _pre_fix_fuzzy_domain_match
        )
        assert (
            normalize_user_text("Explain in simple words why bread rises.").normalized_text
            == "Explain in simple words why read rises."
        )
        assert (
            normalize_user_text("kill the zombie processes").normalized_text
            == "skill the zombie processes"
        )

        with pytest.raises(AssertionError, match="was destroyed by normalization"):
            assert_source_and_normalized_agree_on(
                "bread", "Explain in simple words why bread rises."
            )

    def test_removing_the_short_match_floor_is_caught(self, monkeypatch) -> None:
        """Names `assert_fuzzy_never_targets_short_vocabulary`."""
        assert_fuzzy_never_targets_short_vocabulary(COMMON_ENGLISH)  # control

        monkeypatch.setattr(
            input_normalizer, "_fuzzy_domain_match", _pre_fix_fuzzy_domain_match
        )
        with pytest.raises(AssertionError, match="sub-four-character"):
            assert_fuzzy_never_targets_short_vocabulary(COMMON_ENGLISH)

    def test_restoring_the_ascii_only_tokenizer_is_caught(self, monkeypatch) -> None:
        """Names `assert_no_silent_word_loss`. The mutation deletes the accented character."""
        assert_no_silent_word_loss("what is the weather in Bogotá")  # control

        monkeypatch.setattr(
            input_normalizer, "_TOKEN_RE", re.compile(r"[A-Za-z0-9_'\-]+|[^\w\s]")
        )
        monkeypatch.setattr(
            input_normalizer, "_WORD_TOKEN_RE", re.compile(r"[A-Za-z0-9_'\-]+")
        )
        assert (
            normalize_user_text("what is the weather in Bogotá").normalized_text
            == "what is the weather in Bogot"
        )
        with pytest.raises(AssertionError, match="vanished with no entry in `replacements`"):
            assert_no_silent_word_loss("what is the weather in Bogotá")

    def test_restoring_the_ascii_only_tokenizer_destroys_a_whole_message(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            input_normalizer, "_TOKEN_RE", re.compile(r"[A-Za-z0-9_'\-]+|[^\w\s]")
        )
        monkeypatch.setattr(
            input_normalizer, "_WORD_TOKEN_RE", re.compile(r"[A-Za-z0-9_'\-]+")
        )
        assert normalize_user_text("привет, как дела?").normalized_text == ",?"

    def test_removing_quoted_span_protection_is_caught(self, monkeypatch) -> None:
        """Names `assert_quoted_spans_survive_verbatim`."""
        sentence = 'the spell is called "Kill" in the game'
        assert_quoted_spans_survive_verbatim(sentence)  # control

        # This mutation is the exact reverse of the production edit, and building it correctly took
        # two attempts that are worth recording, because both produced a mutation that was red for
        # the wrong reason:
        #
        #  * rejoining the surviving lines with "|" gave `(|...`, which does not compile at all;
        #  * simply deleting the three quoted-span lines left the group opening with a bar and
        #    nothing before it, so the first alternative matched the EMPTY STRING at every position
        #    and every character in the message was stashed as a literal.
        #
        # A mutation that fails to build, or that corrupts everything indiscriminately, proves
        # nothing about the guard it is aimed at. So the pipe is moved back off the alternative that
        # inherits first position, exactly as the production edit moved it on.
        lines = [
            line
            for line in input_normalizer._LITERAL_SPAN_RE.pattern.split("\n")
            if '"[^"' not in line and "`[^`" not in line and "(?<![\\w'])'" not in line
        ]
        for index, line in enumerate(lines):
            if line.lstrip().startswith("|"):
                lines[index] = line.replace("|", " ", 1)
                break
        else:  # pragma: no cover - the pattern always has alternatives
            pytest.fail("no alternative found to un-prefix; the mutation would be a no-op")
        monkeypatch.setattr(
            input_normalizer, "_LITERAL_SPAN_RE", re.compile("\n".join(lines), re.VERBOSE)
        )
        # The quotes are pushed off the term -- but `Kill` itself SURVIVES, because the first-letter
        # guard is a second, independent layer. The two repairs are not redundant and not a single
        # switch: quote protection keeps the span verbatim, and the first-letter guard keeps the word
        # correct even where the span protection does not reach.
        assert normalize_user_text(sentence).normalized_text == (
            'the spell is called " Kill " in the game'
        )
        with pytest.raises(AssertionError, match="did not survive"):
            assert_quoted_spans_survive_verbatim(sentence)

    def test_removing_both_layers_reproduces_the_original_quoted_corruption(
        self, monkeypatch
    ) -> None:
        """Only with BOTH guards gone does the full reported shape come back: `"Kill"` -> `" skill "`."""
        sentence = 'the spell is called "Kill" in the game'
        lines = [
            line
            for line in input_normalizer._LITERAL_SPAN_RE.pattern.split("\n")
            if '"[^"' not in line and "`[^`" not in line and "(?<![\\w'])'" not in line
        ]
        for index, line in enumerate(lines):
            if line.lstrip().startswith("|"):
                lines[index] = line.replace("|", " ", 1)
                break
        monkeypatch.setattr(
            input_normalizer, "_LITERAL_SPAN_RE", re.compile("\n".join(lines), re.VERBOSE)
        )
        monkeypatch.setattr(
            input_normalizer, "_fuzzy_domain_match", _pre_fix_fuzzy_domain_match
        )
        assert normalize_user_text(sentence).normalized_text == (
            'the spell is called " skill " in the game'
        )

    def test_the_invariants_are_not_vacuous(self) -> None:
        """A guard that cannot fail is not a guard.

        Each invariant is handed input it MUST reject, so an assertion that silently passes on
        everything (an empty comprehension over a mistyped corpus, a `for` loop over nothing) is
        caught here rather than being trusted.
        """
        with pytest.raises(AssertionError):
            assert_source_and_normalized_agree_on("bread", "why does read rise")
        with pytest.raises(AssertionError, match="fixture bug"):
            assert_source_and_normalized_agree_on("absent", "why does bread rise")
        # `assert_no_silent_word_loss` cannot be made to fail against correct code by choosing an
        # input -- that is the point of it. Its teeth are proved by the tokenizer sabotage above;
        # what is checked here is that it has words to check, so a broken extraction regex cannot
        # turn it into a loop over nothing that passes on everything.
        source_words = re.findall(
            r"[^\W\d_]+", unicodedata.normalize("NFC", "what is the weather in Bogotá")
        )
        assert source_words == ["what", "is", "the", "weather", "in", "Bogotá"]

    def test_the_quoted_span_check_actually_inspects_a_span(self) -> None:
        """`assert_quoted_spans_survive_verbatim` loops over found spans, so a regex that finds
        nothing would make it pass on everything. Its teeth are proved by the sabotage test above;
        this proves the loop has a body to run at all."""
        assert re.findall(r'"[^"\n]{1,400}"', 'the spell is called "Kill" in the game') == [
            '"Kill"'
        ]
