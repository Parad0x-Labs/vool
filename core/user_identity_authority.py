"""Local authority for the two names a turn can be asked about.

The runtime holds two completely separate identities and they had been collapsing into one:

* the **assistant/product** name -- ``VOOL``, owned by :mod:`core.onboarding`;
* the **user's own saved name** -- the ``preferred_name`` item of the Operator Profile
  (:mod:`core.operator_profile`), learned in chat or set under "What VOOL remembers about you".

The live defect: asked "yo say my name!" the runtime answered with its OWN name, and asked
"whats my name?" it asked the user for a name that was already saved. The deterministic local
answer path existed but its trigger was a literal substring allow-list ("what's my name",
"who am i", ...), so every phrasing outside those exact strings fell through to a model that had
only a buried "Address the user as X" line to work from -- and to a cloud escalation for a question
whose answer is a field in a local JSON file.

This module is the single place that decides *whose* name a message is asking about and what the
locally stored answer is. It classifies intent over normalized tokens (self-possessive + a name
word + a recall intent) rather than matching phrases, so sloppy input ("whats my nmae?", "say my
nam pls", "u know my name right?") lands on the same authority as the clean phrasing, and a write
("my name is Rick", "rename yourself to Atlas") is never mistaken for a question.

Scope: recall is for a DIRECT request for a name, and nothing else
------------------------------------------------------------------
The second live defect, 2026-08-12. Asked a 71-word hypothetical reasoning question ending "…what
currency will I be paid in, and who am I meeting? After answering, explain why your internal web
search tools would fail to verify this transaction", the runtime answered::

    Your name is Bender — that's the name saved in your settings.

The classifier matched an UNANCHORED ``\\bwho\\s+(?:am|m)\\s+i\\b``, so a clause with a complement,
buried in the middle of a long prompt, claimed the whole turn. Three rules now bound what recall may
take, and each is enforced in the function rather than described here:

* **complement-free** -- a self-reference frame ("who am I", "what am I called") is a name question
  only when nothing but a locator ("in settings", "to you") or filler follows it in its clause. With
  a complement it is asking about the complement's object: who they are MEETING, PAYING, SELLING to;
* **clause-scoped** -- the frame is matched inside one clause of the message, never across the
  whole string, so a fragment cannot claim a multi-part prompt;
* **not inside a frame the user built** -- a turn that stipulates its own premises ("assume…",
  "in a fictional world…") owns the names in it. See :mod:`core.hypothetical_frame`. Quoted spans
  are stripped for the same reason: a prompt the user is SHOWING is data, not a request.

All three fail closed. An unusual phrasing that misses them costs a trip to the model; a phrasing
that wrongly hits them costs the user their whole turn, answered with a name out of settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "IdentityQuestion",
    "SavedUserName",
    "assistant_display_name",
    "assistant_identity_answer",
    "classify_identity_question",
    "identity_context_lines",
    "needs_prior_turns_to_classify",
    "saved_user_name",
    "user_identity_answer",
]


# --- stored values -------------------------------------------------------------------------

#: Where the user's saved name comes from, recorded on every answer so a settings-derived name is
#: never confused with the assistant's own name or with a model-harvested guess.
SETTINGS_PROVENANCE = "operator_profile.preferred_name"


@dataclass(frozen=True)
class SavedUserName:
    """The user's own name as stored on this machine, with where it came from."""

    name: str = ""
    provenance: str = ""

    @property
    def known(self) -> bool:
        return bool(self.name)


def saved_user_name() -> SavedUserName:
    """The user's saved display name from local settings, or an empty record.

    ``user_address`` resolves through the Operator Profile (the one authority for the name), for
    the turn in flight; :mod:`core.fact_extractor` refuses to let a model-harvested line override it.
    """
    try:
        from core.user_preferences import user_address

        name = str(user_address() or "").strip()
    except Exception:
        return SavedUserName()
    if not name:
        return SavedUserName()
    return SavedUserName(name=name, provenance=SETTINGS_PROVENANCE)


def assistant_display_name() -> str:
    """The assistant/product name. Never a valid answer to "what is *my* name"."""
    try:
        from core.onboarding import get_agent_display_name

        return str(get_agent_display_name() or "").strip() or "VOOL"
    except Exception:
        return "VOOL"


# --- intent classification -----------------------------------------------------------------


@dataclass(frozen=True)
class IdentityQuestion:
    """Which identity a message asks about.

    ``subject`` is one of ``"user"`` (their own saved name), ``"assistant"`` (the assistant or
    product name) or ``""`` (neither -- including every write/declaration, which belongs to the
    preference-command path, not to recall).
    """

    subject: str = ""
    reason: str = ""

    @property
    def asks_user_identity(self) -> bool:
        return self.subject == "user"

    @property
    def asks_assistant_identity(self) -> bool:
        return self.subject == "assistant"


# Chat shorthand normalized before tokenizing, so "u know my name right?" and "wuts ur name"
# reach the same tokens as their spelled-out forms.
_SHORTHAND = {
    "u": "you",
    "ur": "your",
    "urs": "yours",
    "yr": "your",
    "youre": "you",
    "wut": "what",
    "wuts": "whats",
    "whts": "whats",
    "wat": "what",
    "watz": "whats",
    "im": "i",
    "iam": "i",
    "pls": "",
    "plz": "",
    "plss": "",
    "rn": "",
    "tho": "",
    "lol": "",
}

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9']+")


def _name_typos(word: str) -> frozenset[str]:
    """Transposition and single-deletion misspellings of ``word``.

    Substitutions are deliberately excluded: "name" -> "same"/"game"/"lame" are all one
    substitution away and are ordinary English, so allowing them would make the classifier fire on
    unrelated sentences. Transpositions ("nmae", "naem") and deletions ("nam", "nme") are the
    typing slips that actually occur and none of them is a common word.
    """
    variants: set[str] = set()
    for index in range(len(word) - 1):
        swapped = list(word)
        swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
        variants.add("".join(swapped))
    for index in range(len(word)):
        variants.add(word[:index] + word[index + 1 :])
    variants.discard(word)
    return frozenset(v for v in variants if len(v) >= 3)


_NAME_WORDS = frozenset({"name", "names", "nickname", "nicknames", "handle", "username"}) | _name_typos("name")
_SETTINGS_WORDS = (
    frozenset({"settings", "setting", "preferences", "preference", "profile", "prefs", "config"})
    | _name_typos("settings")
    | _name_typos("profile")
)
_SELF_WORDS = frozenset({"my", "mine", "me", "i", "im"})
_ASSISTANT_WORDS = frozenset({"your", "yours", "you", "yourself", "its", "it"})
_PRODUCT_WORDS = frozenset({"app", "application", "product", "assistant", "bot", "agent", "program", "tool"})
_RECALL_WORDS = frozenset(
    {
        "what",
        "whats",
        "which",
        "who",
        "whos",
        "do",
        "does",
        "did",
        "know",
        "knows",
        "remember",
        "remembers",
        "recall",
        "say",
        "tell",
        "call",
        "called",
        "saved",
        "save",
        "stored",
        "store",
        "set",
        "give",
        "show",
        "use",
        "address",
        "see",
        "have",
        "got",
    }
)

_SAVE_WORDS = frozenset({"saved", "save", "stored", "store", "set", "entered", "typed", "put", "added"})
_BARE_OBJECT_WORDS = frozenset({"it", "that", "this", "there"})

# A declaration assigns a name; it must reach the preference/memory write path and must never be
# read as a question. "call me by my name" is a request to USE the stored name, not to set one, so
# the lookahead keeps it out of this pattern.
_USER_NAME_DECLARATION_RE = re.compile(
    r"\b(?:"
    r"my\s+(?:name|nickname|handle)\s*(?:'s|\bis\b|\bwas\b|=|:)"
    # The assigned name must be a WORD. `\S` also matched a trailing "?", so "what do you call me ?"
    # was a declaration of the name "?" and never reached the recall path below.
    r"|(?:call|address|refer\s+to)\s+me\s+(?:as\s+)?(?!by\b|my\b|using\b|with\b|the\b)[^\s?!.,;:]"
    r"|i\s+go\s+by\s+\S"
    # An imperative mutation verb on "my name" has no recall reading. "saved"/"stored" are
    # deliberately absent: "i saved my name already" IS a recall complaint, not a write.
    r"|(?:set|change|update|make|edit|rename)\s+my\s+(?:name|nickname|handle)\b"
    r")",
    re.IGNORECASE,
)
# "what should I call you?" -- an identity question with no name word and no "who are you" in it.
# The reflexive mirror is deliberately absent: "what should i call me" is not something anyone
# types, while "call me by my name" IS, and it belongs to the name-word path below.
_ASSISTANT_ADDRESS_RE = re.compile(r"\bwhat\s+(?:should|shall|do|can|would)\s+i\s+call\s+you\b")
# "what do you call me?" -- the OTHER mirror, and a real one: it is a direct request for the user's
# saved name carrying no name word at all, so neither the frames above nor the name-word path below
# saw it, and it fell through to a model for a value sitting in local settings.
_USER_ADDRESS_RE = re.compile(r"\bwhat\s+(?:do|would|should|shall|can)\s+you\s+call\s+me\b")
# "what is the app called", "what is this assistant", "what are you called". Anchored at the end so
# the frame has to BE the whole question: "what is the tool for reading files" names a product word
# but is asking what the tool does, not what it is called.
_PRODUCT_REFERENCE_RE = re.compile(
    r"\bwhat\s+(?:is|are)\s+(?:this|the|your)\s+"
    r"(?:app|application|product|tool|program|assistant|bot|agent)"
    r"(?:\s+(?:called|named))?$"
)
# "are you VOOL?", "are you a bot?" -- a yes/no question about what the assistant IS. Bounded to a
# single trailing word so "are you sure", "are you busy", "are you done" fall straight through; the
# word itself still has to name a product or match the configured product name.
_ASSISTANT_YES_NO_RE = re.compile(r"\bare\s+you\s+(?:the\s+|an?\s+)?(?P<what>[a-z0-9][a-z0-9'-]*)$")
# "my app is named Lumen", "rename yourself to Atlas" -- naming something that is not the user.
_THIRD_PARTY_NAMING_RE = re.compile(
    r"\b(?:is|are|was)\s+(?:named|called)\s+\S|\brenam(?:e|ing)\b|\byour\s+name\s+is\b|\byou\s+are\s+called\b",
    re.IGNORECASE,
)

# Interrogative object fronting: the noun precedes its subject in ``what name did I save?``. This
# is a grammatical user-name query, unlike the transitive imperative ``name the currencies``.
_USER_SAVED_NAME_QUESTION_RE = re.compile(
    r"\b(?:what|which)\s+(?:profile\s+|saved\s+|stored\s+)?(?:name|nickname|handle)\s+"
    r"(?:did|do|have)\s+i\s+(?:save|saved|store|stored|set|enter|entered|put|use|used)\b",
    re.IGNORECASE,
)
_ASSISTANT_NAME_OBJECT_QUESTION_RE = re.compile(
    r"\b(?:what|which)\s+(?:name|nickname|handle)\s+"
    r"(?:(?:should|shall|can|do|would)\s+i\s+(?:call|use)\s+(?:for\s+)?you|do\s+you\s+use)\b",
    re.IGNORECASE,
)


def _tokens(text: str) -> list[str]:
    lowered = str(text or "").casefold().replace("’", "'")
    raw = [part for part in _TOKEN_SPLIT_RE.split(lowered) if part]
    out: list[str] = []
    for token in raw:
        mapped = _SHORTHAND.get(token, token)
        if not mapped:
            continue
        out.append(mapped.strip("'"))
    return [token for token in out if token]


#: Grammatical possessors of a name. Subject/object pronouns are deliberately absent: ``I``,
#: ``me``, ``you`` and ``it`` can occur near the verb *name* without owning its direct object.
#: The old proximity scan admitted all of them, so ``buy it? Explicitly name the currencies``
#: crossed the question-mark boundary and read ``it`` as the assistant whose name was requested.
_NAME_POSSESSOR = {
    "my": "user",
    "mine": "user",
    "your": "assistant",
    "yours": "assistant",
    "its": "assistant",
}

#: Nouns/adjectives that may sit between a real possessor and the identity noun: ``my profile
#: name``, ``your full name``. This is an explicit grammar slot, not a distance allowance.
_NAME_NOUN_MODIFIERS = _SETTINGS_WORDS | frozenset(
    {
        "account", "current", "display", "family", "first", "full", "given", "last", "legal", "preferred",
        "profile", "saved", "stored", "user",
    }
)

_NAME_OBJECT_OPENERS = frozenset(
    {
        "a", "all", "an", "any", "both", "each", "either", "every", "it", "one", "some",
        "that", "the", "them", "these", "this", "those", "two", "three", "four", "five",
        "your", "my",
    }
)


def _owner_of_name_word(tokens: list[str], index: int) -> str:
    """Who owns the identity *noun* at ``index``, using possessive/compound grammar only.

    This intentionally does not ask which identity-looking pronoun is nearest. ``name`` is also a
    transitive verb (``name the currencies``), and subject/object pronouns near that verb do not
    make it an identity noun. Valid ownership shapes are ``your name``, ``my profile name``,
    ``bot name`` and ``name of this app``.
    """
    if index:
        previous = tokens[index - 1]
        if previous in _NAME_POSSESSOR:
            return _NAME_POSSESSOR[previous]
        if previous in _PRODUCT_WORDS:
            return "assistant"
        if previous in _NAME_NOUN_MODIFIERS and index >= 2:
            possessor = _NAME_POSSESSOR.get(tokens[index - 2])
            if possessor:
                return possessor

    # ``the name of your assistant`` / ``the name of this app``.
    after = tokens[index + 1 : index + 6]
    if after and after[0] == "of":
        reference = [token for token in after[1:] if token not in {"a", "an", "the", "this", "that"}]
        if reference:
            if reference[0] in _PRODUCT_WORDS:
                return "assistant"
            possessor = _NAME_POSSESSOR.get(reference[0])
            if possessor:
                return possessor
    return ""


def _is_name_verb(tokens: list[str], index: int) -> bool:
    """Whether singular ``name`` has a direct object and is therefore a verb, not identity data."""
    if tokens[index] != "name" or index + 1 >= len(tokens) or _owner_of_name_word(tokens, index):
        return False
    following = tokens[index + 1]
    if following in _NAME_OBJECT_OPENERS or following.isdigit():
        return True
    # A bare plural/common noun is also a direct object: ``name currencies``, ``name databases``.
    return following not in {"is", "was", "of", "please"} and following not in _RECALL_WORDS


# A clause boundary on the RAW text. The self-reference frames below have to be scoped to a single
# clause, and `_tokens` throws punctuation away, so the split has to happen before tokenizing.
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;:,\n]+|\s+[-–—]{1,2}\s+")

#: The identity frames that contain no name word at all, as token sequences.
_SELF_REFERENCE_FRAMES = (
    ("who", "am", "i"),
    ("who", "m", "i"),
    ("what", "am", "i", "called"),
)

#: What may follow a self-reference frame and leave it a question about the user's own name.
#:
#: This is an allow-list, and that direction is the fix. "who am I" was matched by an unanchored
#: `\bwho\s+am\s+i\b`, so ANY predicate after it came along for free -- and the live defect is
#: exactly that: "…and who am I meeting?" was answered "Your name is Bender". A frame with a
#: complement is not asking who the USER is; it is asking about the complement's object -- who they
#: are MEETING, TALKING TO, PAYING, SELLING to. Only a locator for where the name is stored ("in
#: settings", "to you", "in your memory") or conversational filler leaves the frame intact.
#:
#: Failing closed costs an unusual phrasing a trip to the model, which is a slow answer. Failing
#: open costs a hypothetical, a quoted example or a roleplay the whole turn, answered with a name
#: out of settings -- which is the defect being removed.
_SELF_REFERENCE_TAIL_WORDS = (
    frozenset(
        {
            # locators
            "in", "on", "at", "to", "for", "by", "with", "from", "of", "according",
            "the", "a", "an", "this", "that", "these", "your", "my", "our", "you", "yours",
            "app", "apps", "application", "system", "memory", "memories", "record", "records",
            "database", "db", "chat", "conversation", "file", "here",
            # filler and tag questions
            "again", "now", "then", "still", "even", "exactly", "actually", "really", "anyway",
            "anymore", "currently", "right", "though", "please", "eh", "huh",
            "do", "does", "did", "know", "remember", "recall", "tell", "say", "name", "names",
        }
    )
    | _SETTINGS_WORDS
)


# A quoted span: the shape a user writes a prompt, an example or a test case in. Single quotes are
# deliberately absent -- "what's my name" carries an apostrophe and treating it as a quote opener
# would eat the question itself.
_QUOTED_SPAN_RE = re.compile(r"\"[^\"]*\"|“[^”]*”|`[^`]*`|«[^»]*»")


def _outside_quoted_examples(text: str) -> str:
    """``text`` with quoted spans removed -- unless the quote IS the message.

    "Here is a test case: \"who am I?\" — should that hit the identity path?" is a question ABOUT
    the identity path, not a request for the user's name. Someone who types `"what is my name?"`
    with the quotes around the whole thing is just quoting themselves, so a message left with
    nothing outside the quotes keeps its original text.
    """
    raw = str(text or "")
    stripped = _QUOTED_SPAN_RE.sub(" ", raw)
    if stripped == raw or len(_tokens(stripped)) < 2:
        return raw
    return stripped


def _clauses(text: str) -> list[list[str]]:
    """``text`` split at clause boundaries, each clause tokenized."""
    tokenized = (_tokens(part) for part in _CLAUSE_SPLIT_RE.split(str(text or "")))
    return [tokens for tokens in tokenized if tokens]


def _self_reference_identity_clause(text: str) -> bool:
    """True when some clause of ``text`` is a complement-free "who am I" / "what am I called"."""
    for clause in _clauses(text):
        for frame in _SELF_REFERENCE_FRAMES:
            width = len(frame)
            for start in range(len(clause) - width + 1):
                if tuple(clause[start : start + width]) != frame:
                    continue
                if all(word in _SELF_REFERENCE_TAIL_WORDS for word in clause[start + width :]):
                    return True
    return False


def _supplies_own_premises(text: str) -> bool:
    """True when the turn stipulates its own facts ("assume…", "in a fictional world…").

    Inside such a frame the names on the table belong to the frame, not to this machine's settings:
    a president called Buster, a mayor called Luna, a character the user just invented. Answering
    any of them out of ``user_preferences`` is the live defect in its general form, so the whole
    identity fast path stands down and the turn goes to the model with the frame in its context.

    A bare "don't search the web" is NOT this. It is a routing instruction and supplies no premise,
    so "don't search the web — what's my name?" is still answered from settings.
    """
    try:
        from core.hypothetical_frame import detect_hypothetical_frame

        return detect_hypothetical_frame(text).supplies_premises
    except Exception:
        return False


def _is_anaphoric_saved_setting_complaint(tokens: list[str]) -> bool:
    """True for "i saved it already in settigns?!?! u dont see?!" -- a complaint whose subject is a
    bare pronoun. On its own it names no identity (it could be about a key, a path, a model), so it
    only resolves against a prior turn."""
    token_set = set(tokens)
    if not (token_set & _SAVE_WORDS) and not (token_set & _SETTINGS_WORDS):
        return False
    if not (token_set & _BARE_OBJECT_WORDS):
        return False
    return not (token_set & _NAME_WORDS)


def needs_prior_turns_to_classify(text: str) -> bool:
    """True only when ``text`` cannot be classified without the previous turns.

    Callers use this to keep the conversation-history read off the hot path: an ordinary message
    is decided from its own words, and only a bare-pronoun follow-up needs anything more.
    """
    tokens = _tokens(text)
    if not tokens:
        return False
    if _USER_NAME_DECLARATION_RE.search(str(text)) or _THIRD_PARTY_NAMING_RE.search(str(text)):
        return False
    return _is_anaphoric_saved_setting_complaint(tokens)


def classify_identity_question(
    text: str,
    *,
    recent_user_texts: tuple[str, ...] | list[str] = (),
) -> IdentityQuestion:
    """Decide whether ``text`` asks for the user's saved name or the assistant's own name.

    ``recent_user_texts`` are earlier user messages in this chat, newest first. They are consulted
    only to resolve a pronoun ("i saved it already in settigns") back to the identity question it
    is following up on -- never to widen an otherwise unrelated message.
    """
    # A prompt, example or test case the user QUOTED is data they are showing, not a request they
    # are making, so every test below reads the message with its quoted spans removed.
    raw = _outside_quoted_examples(str(text or "").strip()).strip()
    if not raw:
        return IdentityQuestion()
    tokens = _tokens(raw)
    if not tokens:
        return IdentityQuestion()

    if _USER_NAME_DECLARATION_RE.search(raw):
        return IdentityQuestion(reason="user_name_declaration")
    if _THIRD_PARTY_NAMING_RE.search(raw):
        return IdentityQuestion(reason="third_party_naming")
    # Ahead of every frame below, because a stipulated frame changes what the words in it refer to,
    # not just which frame they match. Inside "assume the US President is a golden retriever named
    # Buster", a name is the frame's, and no local answer is the right one.
    if _supplies_own_premises(raw):
        return IdentityQuestion(reason="hypothetical_frame_supplies_the_names")

    token_set = set(tokens)
    has_recall_intent = bool(token_set & _RECALL_WORDS) or raw.rstrip().endswith("?")

    # "who am i", "what am i called", "who am i in settings", "what do you call me" -- identity
    # questions with no name word in them at all. The first two are scoped to a complement-free
    # clause by `_self_reference_identity_clause`; an unanchored substring test is what let "…and
    # who am I meeting?" claim the turn.
    joined = " ".join(tokens)
    if _USER_ADDRESS_RE.search(joined) or _self_reference_identity_clause(raw):
        return IdentityQuestion(subject="user", reason="self_reference_question")
    if (
        re.search(r"\bwho\s+(?:are|r)\s+you\b", joined)
        or re.search(r"\bwhat\s+are\s+you\s+called\b", joined)
        or _ASSISTANT_ADDRESS_RE.search(joined)
    ):
        return IdentityQuestion(subject="assistant", reason="assistant_reference_question")
    if _PRODUCT_REFERENCE_RE.search(joined):
        return IdentityQuestion(subject="assistant", reason="product_reference_question")
    if _USER_SAVED_NAME_QUESTION_RE.search(joined):
        return IdentityQuestion(subject="user", reason="saved_name_object_question")
    if _ASSISTANT_NAME_OBJECT_QUESTION_RE.search(joined):
        return IdentityQuestion(subject="assistant", reason="assistant_name_object_question")
    yes_no = _ASSISTANT_YES_NO_RE.search(joined)
    if yes_no:
        what = yes_no.group("what")
        # The product name is read from the registry rather than written in here, so a rename moves
        # this answer with it instead of leaving a literal behind to go stale.
        if what in _PRODUCT_WORDS or what == assistant_display_name().casefold():
            return IdentityQuestion(subject="assistant", reason="assistant_reference_question")

    # Ownership is clause-local and grammatical. Punctuation is semantically important here:
    # ``do I have enough? Explicitly name the currencies`` has an object pronoun in one clause and
    # the verb *name* in the next. Flattening them into one proximity window invented an assistant
    # identity request and preempted the currency calculation.
    clauses = _clauses(raw)
    owners = [
        owner
        for clause in clauses
        for index, token in enumerate(clause)
        if token in _NAME_WORDS and not _is_name_verb(clause, index)
        for owner in (_owner_of_name_word(clause, index),)
        if owner
    ]
    if not owners:
        if any(
            _is_name_verb(clause, index)
            for clause in clauses
            for index, token in enumerate(clause)
            if token in _NAME_WORDS
        ):
            return IdentityQuestion(reason="name_verb_with_object")
        if _is_anaphoric_saved_setting_complaint(tokens):
            for prior in list(recent_user_texts)[:4]:
                if classify_identity_question(prior).asks_user_identity:
                    return IdentityQuestion(subject="user", reason="anaphoric_saved_name_followup")
            return IdentityQuestion(reason="anaphoric_saved_setting_unresolved")
        return IdentityQuestion(reason="no_identity_subject")
    # The assistant's own name wins a mixed message ("you should know your name and mine") only
    # when the user's is absent -- the user's name is the one the runtime kept getting wrong.
    subject = "user" if "user" in owners else "assistant"
    # The recall gate is symmetric. Applied to the user only, "i like your name" and "your name
    # suits you" were assistant identity QUESTIONS with a deterministic answer waiting behind them,
    # so a compliment would have been answered "My name is VOOL."
    if not has_recall_intent:
        return IdentityQuestion(reason=f"{subject}_name_mention_without_recall")
    return IdentityQuestion(subject=subject, reason="name_word_subject")


# --- answers and prompt context --------------------------------------------------------------


def user_identity_answer(*, fallback_name: str = "", fallback_provenance: str = "") -> tuple[str, str, str]:
    """The local answer to "what is my name", as ``(text, name, provenance)``.

    ``fallback_name`` lets a caller supply a name recovered from durable memory when settings hold
    none; settings always win. With nothing stored anywhere the answer says so plainly rather than
    asking the user to repeat something they believe they already saved.
    """
    saved = saved_user_name()
    name = saved.name or str(fallback_name or "").strip()
    provenance = saved.provenance if saved.known else (str(fallback_provenance or "").strip() if name else "")
    if not name:
        return (
            "I don't have a name saved for you. Tell me to remember it (\"remember to call me ...\") "
            "or add it under \"What I remember about you\" in Settings, and I'll "
            "use it from then on.",
            "",
            "",
        )
    if provenance == SETTINGS_PROVENANCE:
        return (f"Your name is {name} — the name in your profile.", name, provenance)
    return (f"Your name is {name}.", name, provenance)


#: Where the assistant/product name comes from, recorded on every answer for the same reason the
#: user's name records its own provenance: so a settings-derived fact is never reported as a model's
#: recollection.
ASSISTANT_NAME_PROVENANCE = "identity.agent_display_name"


def assistant_identity_answer(question: IdentityQuestion | None = None) -> tuple[str, str, str]:
    """The local answer to "what is YOUR name", as ``(text, name, provenance)``.

    The product name is a value this machine already holds, so this needs no model, no memory
    subsystem and no network -- which is the whole point: the live defect spent a 14B local model,
    a cloud escalation and nearly three minutes producing a string that was sitting in a local
    identity file the entire time.
    """
    name = assistant_display_name()
    reason = str(getattr(question, "reason", "") or "")
    if reason == "product_reference_question":
        return (f"This app is called {name}.", name, ASSISTANT_NAME_PROVENANCE)
    return (f"My name is {name}.", name, ASSISTANT_NAME_PROVENANCE)


def identity_context_lines() -> list[str]:
    """Compact identity grounding for prompt assembly.

    Two short lines, one per identity, and nothing else from the user's settings -- an ordinary
    turn has no business carrying humor level, autonomy mode or a signature block.

    The user/assistant separation is stated inside the FIRST line rather than left to be inferred
    from the two lines sitting next to each other. The context budgeter trims item content from the
    end, and on a longer turn it clipped the trailing line mid-sentence -- which is exactly the
    clause that stops "say my name!" being answered with the product name.
    """
    lines: list[str] = []
    saved = saved_user_name()
    agent_name = assistant_display_name()
    if saved.known:
        lines.append(
            f'The USER\'s name is "{saved.name}" — their preferred name from their profile. It is '
            f'NOT your name. Address the user as "{saved.name}", and answer "{saved.name}" when '
            "they ask what their name is. Do NOT call them any other name; ignore names that "
            "appear in image prompts, quotes, or generated content."
        )
    else:
        lines.append(
            "The USER's name is not known — their profile holds none, and your own name is NOT theirs. "
            "Do NOT invent or assume one from conversation context, image prompts, or quoted "
            "content, and never answer your own name when they ask for theirs — say you do not know "
            "it yet and invite them to tell you what to call them."
        )
    if agent_name:
        lines.append(
            f'YOUR own name is "{agent_name}" — the assistant/product name, never the user\'s. It '
            "overrides any other name for yourself that appears in stored memory, saved summaries, "
            "notes, or earlier transcripts. Never introduce yourself as anything else."
        )
    return lines
