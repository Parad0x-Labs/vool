"""Skills: the instructions a plugin ships for how to use its tools.

A tool tells the model *what it can do*. A skill tells it *how this user wants it done* — search
before filing, quote the issue key, keep the summary under 80 characters. Claude Code's format is
markdown with YAML frontmatter, and both plugins already installed on this machine are written that
way, so this reads the shape that is already on disk rather than inventing another.

Two things change versus `core/plugin_catalog._skill_frontmatter`:

**Real YAML.** That parser is `line.split(":", 1)` per line, which turns `allowed-tools: [a, b]`
into the *string* `"[a, b]"` and truncates any value at its first colon. A description reading
"Use when: the user pastes a stack trace" silently loses everything after "when".

**The body is used.** Today it is parsed and thrown away — only `name` and `description` survive —
so the actual instructions a skill author wrote never reach the model. Here the body is what gets
injected when a skill matches.

Matching is a ranking signal and never a gate. A skill that does not match simply does not add its
instructions; it never removes a tool from the catalog. `allowed-tools` narrows the catalog *for a
turn where that skill matched*, which is a budget lever, not a permission decision.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_MAX_BODY_CHARS = 8000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str = ""
    body: str = ""
    allowed_tools: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    plugin_id: str = ""
    path: str = ""
    # The package contract (P1 native skill library). Every field defaults empty, because the two
    # packages that predate the contract declare only some of them — surfacing these must never
    # turn a legacy package into an unparseable one.
    version: str = ""
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    recovery: tuple[str, ...] = ()
    cumulative_test_law: tuple[str, ...] = ()

    @property
    def match_corpus(self) -> str:
        """Everything an author wrote that says when this skill applies.

        `description` carries the trigger sentence by convention ("Use when the user ..."), and the
        installed plugins already write it that way, so it is part of the corpus rather than
        requiring authors to duplicate themselves into `triggers`.
        """

        return " ".join([self.name, self.description, " ".join(self.triggers)]).lower()


def _as_tuple(value: Any) -> tuple[str, ...]:
    """Accept a YAML list or a comma-separated string; authors write both."""

    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    text = str(value or "").strip()
    if not text:
        return ()
    text = text.strip("[]")
    return tuple(part.strip().strip("'\"") for part in text.split(",") if part.strip())


def parse_skill(path: Path, *, plugin_id: str = "") -> Skill | None:
    """Read one SKILL.md. Returns None when there is no frontmatter to read."""

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_skill_content(text, path=path, plugin_id=plugin_id)


def parse_skill_content(text: str, *, path: Path, plugin_id: str = "") -> Skill | None:
    """Parse an already captured instruction snapshot without re-opening its source."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None

    front: dict[str, Any] = {}
    try:
        import yaml

        loaded = yaml.safe_load(match.group(1))
        if isinstance(loaded, dict):
            front = loaded
    except Exception:
        # A malformed skill must not take down the plugin that ships it. Falling through with an
        # empty header still yields a usable skill: the body is the valuable part.
        front = {}

    body = (match.group(2) or "").strip()[:_MAX_BODY_CHARS]
    return Skill(
        name=str(front.get("name") or Path(path).parent.name).strip(),
        description=str(front.get("description") or "").strip(),
        body=body,
        allowed_tools=_as_tuple(front.get("allowed-tools") or front.get("allowed_tools")),
        triggers=_as_tuple(front.get("triggers")),
        plugin_id=str(plugin_id or ""),
        path=str(path),
        version=str(front.get("version") or "").strip(),
        capabilities=_as_tuple(front.get("capabilities")),
        permissions=_as_tuple(front.get("permissions")),
        effects=_as_tuple(front.get("effects")),
        inputs=_as_tuple(front.get("inputs")),
        outputs=_as_tuple(front.get("outputs")),
        stop_conditions=_as_tuple(front.get("stop-conditions") or front.get("stop_conditions")),
        recovery=_as_tuple(front.get("recovery")),
        cumulative_test_law=_as_tuple(
            front.get("cumulative-test-law") or front.get("cumulative_test_law")
        ),
    )


def load_skills(plugin_root: Path, *, plugin_id: str = "") -> tuple[Skill, ...]:
    """Every skill under one plugin's `skills/` directory."""

    skills_dir = Path(plugin_root) / "skills"
    if not skills_dir.is_dir():
        return ()
    found: list[Skill] = []
    for entry in sorted(skills_dir.iterdir()):
        candidate = entry / "SKILL.md" if entry.is_dir() else entry
        if candidate.name != "SKILL.md" or not candidate.is_file():
            continue
        skill = parse_skill(candidate, plugin_id=plugin_id)
        if skill is not None:
            found.append(skill)
    return tuple(found)


# Function words that appear in every description and every request. Counting them as overlap
# made "what is the capital of France" match a skill whose description contained "the" (measured
# 2026-09-02): a skill would then be injected into turns that had nothing to do with it.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "when", "user", "asks", "use", "from", "into",
        "your", "you", "are", "was", "were", "will", "can", "has", "have", "had", "not", "but", "its",
        "any", "all", "one", "two", "how", "what", "which", "who", "why", "where", "then", "than",
        "also", "just", "only", "some", "them", "they", "their", "there", "here", "about", "after",
        "before", "over", "under", "each", "per", "via", "get", "got", "make", "made", "does", "did",
        "should", "would", "could", "may", "might", "please", "want", "wants", "need", "needs",
        "these", "those", "much", "many", "very", "really", "actually", "anyway", "just", "such",
        "even", "ever", "still", "yet", "now", "something", "anything", "everything", "nothing",
        "someone", "anyone", "everyone", "thing", "things",
    }
)

# Words that name the KIND of help wanted rather than a topic. A request sharing only these
# with a skill ("explain ...", "help me ...") says nothing about WHICH skill: every authoring
# skill "writes", every explainer "explains". They never carry a match alone; they only stand
# behind a topic word that is itself specific — "explain the bill" reaches the bill skill,
# "write a short poem" must not reach every skill mentioning "short" (measured 2026-10-28
# against probe/browse-skills-53: "short" appears in 11 of 53 descriptions).
_KIND_WORDS = frozenset(
    {
        "explain", "write", "make", "help", "plan", "check", "list", "table",
        "summarise", "summarize", "draft", "fix",
    }
)

# Light suffix rules. Word forms must meet: politely/polite, receipts/receipt, organise/organize,
# translate/translation, planner/planning, price/prices/pricing, note/notes/noted. Word forms that
# are different words must not: letter/letting, prize/price, apply/app, reply/rep, file/fill,
# plane/plan, notes/not, version/verse, offer/off, order/ord (measured 2026-10-29 over the 70
# Browse skills: the earlier loop folded 125 word groups onto stems under four letters). This is a
# morphology rule, never a vocabulary list: nothing here knows any individual request.
#
# Each suffix carries the shortest remainder it may leave. A remainder must hold a vowel, so
# "string" never loses "ing". "-er" needs five letters left (planner, printer, builder, but never
# letter, order, paper, offer or winner); "-ion" five (translation, but never session, option).
_SUFFIXES = (
    ("ising", 5), ("izing", 5), ("ised", 5), ("ized", 5), ("edly", 4), ("ing", 3), ("ion", 5),
    ("ise", 5), ("ize", 5), ("ment", 5), ("ed", 3), ("ly", 4), ("er", 5),
)
# Inflections whose base may have lost a silent 'e' (noted/note, pricing/price) or doubled its
# last consonant (planning/plan, letting/let).
_INFLECTIONS = frozenset({"ing", "ed", "er"})
_VOWELS = frozenset("aeiou")
_PAIR_REACH = 3


def _shape(word: str) -> str:
    """'c'/'v' per letter; 'y' after a consonant counts as a vowel."""

    marks: list[str] = []
    for i, ch in enumerate(word):
        if ch in _VOWELS or (ch == "y" and i > 0 and marks[-1] == "c"):
            marks.append("v")
        else:
            marks.append("c")
    return "".join(marks)


def _measure(word: str) -> int:
    """How many vowel-then-consonant runs a word holds (Porter's m): plan 1, translat 2."""

    return re.sub(r"(.)\1+", r"\1", _shape(word)).count("vc")


def _short(word: str) -> bool:
    """One syllable ending consonant-vowel-consonant, the last not w, x or y: dat, pric, not."""

    shape = _shape(word)
    return (
        _measure(word) == 1 and len(word) >= 3 and shape.endswith("cvc") and word[-1] not in "wxy"
    )


def _stem(token: str) -> str:
    """Fold one word form onto its stem: a plural, then at most one suffix, then the silent-e and
    doubled-consonant fixes that let noted/notes/note and planning/plan meet."""

    word = token
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("sses", "xes", "zes", "ches", "shes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) >= 4:
        word = word[:-1]
    for suffix, keep in _SUFFIXES:
        rest = word[: -len(suffix)]
        if suffix == "ly" and rest.endswith("p"):
            continue  # apply, reply, supply: the "ly" is the word's own
        if word.endswith(suffix) and len(rest) >= keep and any(ch in _VOWELS or ch == "y" for ch in rest):
            word = rest
            if suffix in _INFLECTIONS:
                if len(word) >= 4 and word[-1] == word[-2] and word[-1] not in _VOWELS | set("lszy"):
                    word = word[:-1]
                elif _short(word):
                    word += "e"
            break
    if word.endswith("e") and len(word) > 3:
        base = word[:-1]
        if _measure(base) > 1 or (_measure(base) == 1 and not _short(base)):
            word = base
    return word


_KIND_STEMS = frozenset(_stem(word) for word in _KIND_WORDS)


def _raw_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", str(text or "").lower()) if t]


# Two-letter words are function words, except the handful of everyday abbreviations a person
# types for a topic ("cv", "ad", "ai", "pc"). These are the function words; any other two-letter
# word is kept.
_TWO_LETTER_FUNCTION_WORDS = frozenset(
    {
        "am", "an", "as", "at", "be", "by", "do", "go", "he", "hi", "if", "in", "is", "it", "me",
        "my", "no", "of", "oh", "ok", "on", "or", "so", "to", "up", "us", "we", "ah", "eh", "ha",
        "um", "uh", "ya", "ye", "yo", "re", "vs", "eg", "ie", "mr", "ms", "st", "nd", "rd", "th",
        "ll", "ve", "im", "id", "pm", "km", "kg", "cm", "mm", "lb", "oz", "hr", "pa", "ma", "da",
        "un", "na", "ta", "aw", "ow", "er", "hm", "mo", "wk", "yr", "ur", "u2", "sd",
    }
)


# Particles carry no topic on their own ("back up your claim", "out of the office") but are part
# of what a trigger means: "money back" is not "money". They count inside triggers only.
_PARTICLES = frozenset({"back", "out", "off", "away", "down", "around"})


def _is_content(token: str, *, particles: bool = False) -> bool:
    if token.isdigit() or token in _STOPWORDS:
        return False
    if token in _PARTICLES:
        return particles
    if len(token) == 2:
        return token.isalpha() and token not in _TWO_LETTER_FUNCTION_WORDS
    return len(token) > 2


def _content_stems(text: str, *, particles: bool = False) -> list[str]:
    """The ordered, stemmed content words — function words removed, word forms folded. With
    particles, "back", "out" and their kind stay in place, as a trigger phrase needs them."""

    return [_stem(t) for t in _raw_tokens(text) if _is_content(t, particles=particles)]


def _tokens(text: str) -> set[str]:
    return set(_content_stems(text))


def _contains_phrase(ask_stems: list[str], phrase_stems: list[str]) -> bool:
    """A whole trigger, in the ask. Two content words must stand close together, in either order,
    with at most two words between them ("polite reply", "reply, polite and short"). Three or
    more must all be present however the ask arranges them ("compress and archive my workspace
    into a vault" matches the trigger "compress archive vault workspace"). One word out of a
    multi-word trigger is never a match on its own."""

    if not phrase_stems:
        return False
    span = len(phrase_stems)
    if span == 1:
        return phrase_stems[0] in ask_stems
    if not set(phrase_stems) <= set(ask_stems):
        return False
    if span >= 3:
        return True
    first, second = phrase_stems
    places = {stem: [i for i, s in enumerate(ask_stems) if s == stem] for stem in (first, second)}
    return any(abs(i - j) <= _PAIR_REACH for i in places[first] for j in places[second])


@dataclass(frozen=True)
class _Match:
    score: float
    index: int
    skill: Skill
    reasons: tuple[str, ...]


def _ask_words(user_text: str) -> dict[str, str]:
    """Each content stem of the ask, mapped back to the first word the person actually typed."""

    words: dict[str, str] = {}
    for token in _raw_tokens(user_text):
        if _is_content(token):
            words.setdefault(_stem(token), token)
    return words


def _in_ask_order(stems: set[str], ask_stems: list[str]) -> list[str]:
    return [stem for stem in dict.fromkeys(ask_stems) if stem in stems]


# How much one shared word is worth, by where the skill carries it. A trigger the author wrote as
# a single word and a word of the skill's own name are statements of what the skill is for; a
# description word is a fair hint; a word that only appears inside a longer trigger counts as part
# of that phrase and weakly alone; a word of the instructions themselves is the weakest hint.
_W_NAME = 1.0
_W_TRIGGER = 1.0
_W_PHRASE_WORD = 0.7
_W_DESCRIPTION = 0.6
_W_BODY = 0.3
# A skill qualifies on words alone when their worth reaches this bar. One rare trigger word or name
# word reaches it; one description word never does (at most 0.6), so one ordinary shared word is
# never enough.
_BAR = 1.0
_STRONG_BAR = 0.7
_KIND_PAIR_BAR = 0.55
# The rarity scale treats a small candidate set as if it were a library of this many skills, so a
# lane of one or two of the person's own skills does not make every word look common.
_LIBRARY_FLOOR = 30
# What a whole multi-word trigger adds to the score, on top of its words.
_PHRASE_BASE = 0.4
_PHRASE_STEP = 0.15


@dataclass(frozen=True)
class _Fields:
    name: frozenset[str]
    triggers: frozenset[str]
    phrase_words: frozenset[str]
    description: frozenset[str]
    body: frozenset[str]
    phrases: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def card(self) -> frozenset[str]:
        return self.name | self.triggers | self.phrase_words | self.description

    @property
    def every(self) -> frozenset[str]:
        return self.card | self.body

    def weight(self, stem: str) -> float:
        return max(
            _W_NAME if stem in self.name else 0.0,
            _W_TRIGGER if stem in self.triggers else 0.0,
            _W_DESCRIPTION if stem in self.description else 0.0,
            _W_PHRASE_WORD if stem in self.phrase_words else 0.0,
            _W_BODY if stem in self.body else 0.0,
        )


def _fields(skill: Skill) -> _Fields:
    phrases: list[tuple[str, tuple[str, ...]]] = []
    singles: set[str] = set()
    inside: set[str] = set()
    for trigger in skill.triggers:
        stems = tuple(dict.fromkeys(_content_stems(trigger, particles=True)))
        if not stems:
            continue
        if len(stems) == 1 and stems[0] in _KIND_STEMS:
            continue  # a trigger that is only a kind-of-help word ("write") says nothing
        if len(stems) == 1 and stems[0] in _PARTICLES:
            continue
        phrases.append((trigger, stems))
        (singles if len(stems) == 1 else inside).update(stem for stem in stems if stem not in _PARTICLES)
    return _Fields(
        name=frozenset(_content_stems(skill.name)),
        triggers=frozenset(singles),
        phrase_words=frozenset(inside),
        description=frozenset(_content_stems(skill.description)),
        body=frozenset(_content_stems(skill.body)),
        phrases=tuple(phrases),
    )


_FIELDS_CACHE: dict[Skill, _Fields] = {}


def _fields_of(skill: Skill) -> _Fields:
    found = _FIELDS_CACHE.get(skill)
    if found is None:
        if len(_FIELDS_CACHE) > 512:
            _FIELDS_CACHE.clear()
        found = _FIELDS_CACHE[skill] = _fields(skill)
    return found


# A question about the world that points at no material of the person's own ("why do cats sleep so
# much", "is it legal to ...") asks for an answer, not for a skill: every Browse skill works on
# what the person supplies. Such a question needs much stronger evidence before a skill is added.
_QUESTION_OPENERS = frozenset(
    {
        "why", "is", "are", "does", "do", "did", "will", "should", "which", "what", "whats", "how",
        "when", "where", "who", "can", "could", "would", "am", "was", "were", "has", "have",
    }
)
_POINTERS = frozenset({"this", "these", "here", "heres", "below", "attached", "pasted", "following", "those", "them"})
_REQUEST_RE = re.compile(r"^(?:(?:can|could|would|will) (?:you|u|we)|(?:can|could) (?:i|we) (?:get|have))\b")
# A question that opens on its verb ("should I ...", "can I ...", "do I ...", "is it ...") asks
# for a yes or a no: a verdict on the person's own case, which the model gives without a skill.
_VERDICT_OPENERS = frozenset({"should", "can", "could", "do", "does", "did", "is", "are", "am", "will", "would", "was", "were", "has", "have"})
_GENERAL_BAR = 3.0


_SUBJECT_RE = re.compile(r"\babout\b([^.,;:!?]*)")


def _subject_stems(user_text: str) -> set[str]:
    return {stem for part in _SUBJECT_RE.findall(str(user_text or "").lower()) for stem in _content_stems(part)}


def _outside_subject(user_text: str) -> str:
    return _SUBJECT_RE.sub(" ", str(user_text or "").lower())


_SENTENCE_RE = re.compile(r"[.?!;]+\s+|\s+-+\s+|[.?!]+$")


def _general_question(user_text: str) -> bool:
    tokens = _raw_tokens(user_text)
    if not tokens or tokens[0] not in _QUESTION_OPENERS:
        return False
    if _REQUEST_RE.match(" ".join(tokens)):
        return False
    if any(token in _POINTERS for token in tokens):
        return False
    # "should I go to the gym more? make me a plan": a second sentence after the question is
    # the person asking for something, so the ask is no longer a question alone.
    sentences = [part for part in _SENTENCE_RE.split(str(user_text or "")) if _raw_tokens(part)]
    if len(sentences) > 1:
        return False
    # "should I put my hobbies on my cv", "can I claim my internet on my taxes": a yes-or-no
    # question asks for a verdict, even when "I" and "my" make it the person's own case.
    if tokens[0] in _VERDICT_OPENERS:
        return True
    # "which chart should I use", "what should we pack": a wh-question about the person's own
    # case asks for something to be worked out for them.
    return not re.search(r"\b(?:should|can|could|do) (?:i|we)\b", " ".join(tokens))


def _qualify(skills: tuple[Skill, ...], user_text: str) -> list[_Match]:
    """Every skill this request qualifies for, best first, with the plain reasons it qualified.

    The one qualification and scoring path: rank_skills and match_reasons both read it, so a
    reason shown to a person is always the reason the ranker used.
    """

    ask_stems = _content_stems(user_text)
    if not ask_stems:
        return []
    phrase_ask = _content_stems(user_text, particles=True)
    # "write a haiku about databases": what follows "about" is the subject of the piece, not the
    # job, so on its own it never seats a skill (it still counts beside other evidence).
    subject_only = _subject_stems(user_text) - set(_content_stems(_outside_subject(user_text)))
    asked = _ask_words(user_text)
    ask_stem_set = set(ask_stems)
    # "use the release-notes skill" names the skill outright. That is the strongest statement of
    # intent there is, and it must not lose to a skill whose description merely shares more words.
    spoken = " " + " ".join(_raw_tokens(user_text)) + " "

    asking_in_general = _general_question(user_text)
    asking_verdict = asking_in_general and _raw_tokens(user_text)[0] in _VERDICT_OPENERS
    fields = [_fields_of(skill) for skill in skills]
    # How many candidate skills carry each stem anywhere: a word many skills carry says little
    # about which one this ask means, so rarity decides how much a shared word is worth.
    # A word on a skill's card (name, triggers, description) is weighed by how many cards carry
    # it; a word only in a skill's instructions by how many skills mention it anywhere, since
    # every set of instructions says "paste", "list" and "steps".
    owners: dict[str, int] = {}
    card_owners: dict[str, int] = {}
    for found in fields:
        for stem in found.every & ask_stem_set:
            owners[stem] = owners.get(stem, 0) + 1
        for stem in found.card & ask_stem_set:
            card_owners[stem] = card_owners.get(stem, 0) + 1
    library = max(len(skills), _LIBRARY_FLOOR)

    def _scale(count: int) -> float:
        return math.log(library / count) / math.log(library) if count else 0.0

    def _rarity(stem: str, found: _Fields) -> float:
        if stem in found.card:
            return _scale(card_owners.get(stem, 0))
        return _scale(owners.get(stem, 0))

    matches: list[_Match] = []
    for index, skill in enumerate(skills):
        found = fields[index]
        # Naming the skill: the words of its name, verbatim and adjacent...
        name_words = " ".join(_raw_tokens(skill.name)).strip()
        named = bool(name_words) and len(name_words) > 2 and f" {name_words} " in spoken
        # ...or every stem of a multi-word name in the ask, provided the name is specific
        # enough to be naming ("explain what this sql query does" names the sql-explainer;
        # "explain this in plain words" only borrows two generic words of plain-explainer).
        name_covered = (
            len(found.name) >= 2
            and found.name <= ask_stem_set
            and any(stem not in _KIND_STEMS and card_owners.get(stem, 0) <= 3 for stem in found.name)
        )
        # A whole multi-word trigger in the ask. A single-word trigger is weighed as a word below.
        hits = [
            (trigger, stems)
            for trigger, stems in found.phrases
            if len(stems) > 1 and _contains_phrase(phrase_ask, list(stems))
        ]
        # Kind-of-help words never carry weight of their own: every authoring skill writes.
        worth = {
            stem: found.weight(stem) * _rarity(stem, found)
            for stem in ask_stem_set - _KIND_STEMS
            if found.weight(stem)
        }
        words = sum(worth.values())
        # One word of the skill's name, or one single-word trigger, that few other skills carry
        # says which skill this is ("invoice", "flashcards"); prose words need company.
        strong = max(
            (worth[stem] for stem in worth if (stem in found.name or stem in found.triggers) and stem not in subject_only),
            default=0.0,
        )
        # A kind-of-help word the skill's card also uses may stand behind one specific topic
        # word: "fix the spelling" reaches the proofreading skill, "explain how it works" must
        # not reach whichever skill happens to say "worked example".
        kind_backed = bool(ask_stem_set & _KIND_STEMS & found.card) and max(worth.values(), default=0.0) >= _KIND_PAIR_BAR
        qualified_without_backing = (
            named or name_covered or bool(hits) or bool((ask_stem_set - subject_only) & found.triggers)
            or strong >= _STRONG_BAR
            or (words >= _BAR and len(worth) >= 2)
        )
        if not (qualified_without_backing or kind_backed):
            continue
        # A whole trigger adds a little on top of its words, which already count at trigger
        # weight; a larger bonus let incidental two-word phrases outrank the skill an ask
        # describes (measured 2026-10-29).
        score = (
            (5.0 if named else 0.0)
            + (3.5 if name_covered else 0.0)
            + sum(_PHRASE_BASE + _PHRASE_STEP * len(stems) for _trigger, stems in hits)
            + words
        )
        reasons: list[str] = []
        if named or name_covered:
            reasons.append("names the skill")
        reasons.extend(f'trigger "{trigger}"' for trigger, _stems in hits)
        ordered = _in_ask_order(set(worth), ask_stems)
        written = {stems[0]: trigger for trigger, stems in found.phrases if len(stems) == 1}
        reasons.extend(f'trigger "{written[stem]}"' for stem in ordered if stem in written and stem not in found.name)
        reasons.extend(f"word from the skill's name \"{asked.get(stem, stem)}\"" for stem in ordered if stem in found.name)
        rest = [stem for stem in ordered if stem not in found.name and stem not in found.triggers]
        if rest:
            shared = ", ".join(f'"{asked.get(stem, stem)}"' for stem in rest)
            if qualified_without_backing:
                reasons.append("shared words " + shared)
            else:
                # Picked only because a kind-of-help word on the card stands behind the topic word.
                backing = [stem for stem in dict.fromkeys(ask_stems) if stem in _KIND_STEMS and stem in found.card]
                label = "shared word " if len(rest) == 1 else "shared words "
                reasons.append(label + shared + " backed by "
                               + ", ".join(f'"{asked.get(stem, stem)}"' for stem in backing))
        # The person's own question carrying a trigger word for word ("how many rows are in
        # the orders table" holds "how many rows") is the author's own wording, general or not.
        verbatim = any(
            len(_raw_tokens(trigger)) >= 3 and f" {' '.join(_raw_tokens(trigger))} " in spoken
            for trigger, _stems in found.phrases
        )
        # In a yes-or-no question the name's words spread through it ("do I need to reply to
        # every email") are not the person naming the skill; only the name verbatim is.
        general_score = score - (3.5 if name_covered and asking_verdict else 0.0)
        if asking_in_general and general_score < _GENERAL_BAR and not named and not verbatim:
            continue
        matches.append(_Match(score=score, index=index, skill=skill, reasons=tuple(reasons)))
    matches.sort(key=lambda match: (-match.score, match.index))
    return matches


def rank_skills(skills: tuple[Skill, ...], user_text: str, *, limit: int = 2) -> tuple[Skill, ...]:
    """The skills this request is actually about, best first.

    Overlap only — nothing here decides whether a tool is available. A request that matches no
    skill still reaches the model with the full catalog; it just gets no extra instructions.

    Measured against the 70 Browse skills (2026-10-28 and 2026-10-29): one shared ordinary word
    must never inject a skill ("what rows of seats are best on a plane"), yet everyday wording
    ("my phone bill doubled") must reach its skill. A skill qualifies when the ask:

    * names the skill — its name verbatim, or every stem of a specific multi-word name;
    * contains one of its triggers — a multi-word trigger as a phrase (two words close together,
      three or more anywhere), a single-word trigger as that word;
    * uses a word of the skill's name that few other candidates carry;
    * shares enough weighed words: each is worth its field weight (name, trigger, description,
      phrase word, instructions) times its rarity across the candidates, and two or more must
      reach the bar together. Kind-of-help words (explain, write, help, plan, check, list, ...)
      carry no weight; one may only stand behind a specific topic word the skill's card uses.

    A general question that points at no material of the person's own ("is it legal to ...",
    "why do cats sleep so much") needs a much higher score. So does a one-sentence yes-or-no
    question about the person's own case ("should I ... my cv", "can my landlord ...", "do I need
    to ..."): it asks for a verdict, not a skill's output. A wh-question ("what should I pack"), a
    question asking for something ("can you ...", "can I get ..."), or a question followed by a
    request in a second sentence still counts as a request. The subject after "about"
    ("a haiku about databases") never seats a skill alone. Naming the skill wins; `limit` caps
    the result.
    """

    return tuple(match.skill for match in _qualify(skills, user_text)[:limit])


def match_reasons(skills: tuple[Skill, ...], user_text: str) -> dict[str, list[str]]:
    """Why each qualifying skill matched this request, in plain words, best match first.

    skill name -> reasons such as 'names the skill', 'trigger "<phrase>"', 'word from the
    skill's name "<word>"' and 'shared words "<a>", "<b>"' (the person's own words). Computed by
    the same path as rank_skills; a skill that is absent did not qualify.
    """

    reasons: dict[str, list[str]] = {}
    for match in _qualify(skills, user_text):
        reasons.setdefault(match.skill.name, list(match.reasons))
    return reasons


def instructions_for(skills: tuple[Skill, ...]) -> str:
    """The prompt fragment a matched skill contributes: its body, as its author wrote it."""

    blocks = [f"## {skill.name}\n{skill.body}".strip() for skill in skills if skill.body]
    return "\n\n".join(blocks)


def narrowed_tools(skills: tuple[Skill, ...]) -> tuple[str, ...]:
    """The union of `allowed-tools` across matched skills, or empty for "do not narrow".

    Empty means the full catalog, never an empty catalog — a skill that forgot to list its tools
    must not silently leave the model with nothing to call.
    """

    names: list[str] = []
    for skill in skills:
        names.extend(skill.allowed_tools)
    return tuple(dict.fromkeys(names))


# The native skill library lives in core.native_skill_library — the ONE contract, selection,
# configuration and projection authority for repo-native, plugin-typed and MCP-sourced skills.
# This module is the SKILL.md parser and the plugin-tree lexical ranker only. The former
# select_native_skill/natives block (lexical, capability-gated) was REMOVED at reconciliation:
# its only callers were tests, now re-pointed, and two selection authorities is the defect
# this file will not grow back.


__all__ = [
    "Skill",
    "instructions_for",
    "load_skills",
    "match_reasons",
    "narrowed_tools",
    "parse_skill",
    "rank_skills",
]
