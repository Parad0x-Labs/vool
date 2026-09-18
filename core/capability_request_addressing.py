"""Who is being asked to act, when a message names a physically impossible action.

The live defect
---------------
Asked::

    Assume for a fantasy novel I am writing that "Soap-Grease" is a magical potion made of
    crushed moonstones and dragon scale, used to instantly teleport. Based ONLY on this
    fictional definition, answer in one sentence: why does Soap-Grease help you escape a
    dungeon? Do NOT search my local workspace, files, or the web.

VOOL answered::

    No. That is outside what this runtime can actually do. Instead: I can still reason about it,
    plan it, or help write instructions for a human to carry out.

Nobody had asked the runtime to teleport.  ``capability_truth_for_request`` matched the marker
``teleport`` as a bare substring of the whole message, so a potion described in a subordinate
clause -- ``used to instantly teleport``, whose subject is the potion -- was read as an
instruction to this process.  The same substring test refuses ``why does the portal gun teleport
the player?`` and every other sentence that merely *mentions* an action a program cannot perform.

The rule this module applies
----------------------------
A capability-truth refusal is owed only when the user asks **this runtime** to perform, or to
claim it can perform, the action.  So the marker is not the answer -- it is only where the
question starts.  For each mention we ask who its agent is, by walking back from the mention to
the nearest token that can carry a subject:

* second person or a name for this process (``you``, ``yourself``, ``vool``, ``runtime``) -- the
  request is addressed here, and the refusal is owed;
* any other subject (``potion``, ``wizard``, ``the ship``, ``I``) -- the action belongs to
  something else in the world the user is describing, and the runtime has not been asked for
  anything;
* nothing at all, the mention opening its own sentence -- a bare imperative (``teleport me to
  Paris``) is addressed to whoever was spoken to, which is this runtime.  That last branch is
  held to a command shape on purpose: prose describing the world also opens on the verb
  (``Teleportation in my book works via runes``, ``Teleport spells cost 3 mana``), and an opening
  word is only an order when what follows it is an object rather than a noun it modifies.

Function words, modals, ``-ly`` adverbs and the request scaffolding around a capability question
(``are you able to``, ``do you have the ability to``) are transparent to that walk: they carry no
subject of their own, so stepping through them keeps ``you`` reachable in ``are you able to
teleport`` while ``used to instantly teleport`` still stops on ``used``.

Deliberately NOT how this is done
---------------------------------
No fiction vocabulary.  Nothing here looks for ``fantasy``, ``novel``, ``magic``, ``dungeon``,
``story`` or any other topic word, and adding one would reintroduce the defect in a new form: a
message is not exempt because it sounds invented, and it is not a request because it sounds
serious.  ``Can you physically restart my unplugged server yourself?`` is refused for the same
reason the potion is answered -- the grammar says who was asked, and the subject matter says
nothing.

The one frame-level exception is a stipulated persona (``assume you are a wizard who can
teleport``).  There the user has assigned the second person to a character, so ``you`` in that
message no longer denotes this process.  That exception is deliberately narrow: it requires the
user to have addressed a persona onto ``you``, never merely to have opened with ``suppose``.
``Suppose I need help. Can you drive over and fix my server?`` carries a hypothetical opener and
is still a real request for a real physical act, and it is still refused.
"""

from __future__ import annotations

import re

from core.execution.constants import _IMPOSSIBLE_REQUEST_MARKERS

__all__ = [
    "impossible_action_agent",
    "runtime_asked_for_impossible_action",
]

# A sentence terminator only. Commas must NOT split here: `..., used to instantly teleport.` would
# become a subjectless fragment and read as an imperative aimed at the runtime, which is the exact
# misreading this module exists to remove.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']*")

# Names that denote this process. Second person plus the runtime's own names -- and nothing more
# generic. `agent`, `model`, `bot` and `ai` are left out on purpose: they are ordinary nouns for
# characters in the stories users tell ("the ship AI can teleport the crew"), so treating them as
# self-reference would refuse a description of somebody else's fictional machine.
_RUNTIME_SUBJECT_TOKENS = frozenset(
    {"you", "youre", "youve", "youll", "yourself", "yourselves", "u", "vool", "vool", "runtime"}
)

# Tokens that carry no subject of their own, so the agent of an action is whatever lies behind
# them. Three groups: English scaffolding (determiners, prepositions, conjunctions), the modal and
# auxiliary chain, and the framing a capability question puts between `you` and the verb -- `are
# you ABLE TO teleport`, `do you have the ABILITY to teleport`. Without that third group the walk
# would stop on `able` and conclude the runtime was never addressed.
_TRANSPARENT_TOKENS = frozenset(
    {
        # modals and auxiliaries
        "can", "cant", "could", "couldnt", "would", "wouldnt", "will", "wont", "shall", "should",
        "may", "might", "must", "do", "dont", "does", "doesnt", "did", "didnt", "is", "isnt",
        "are", "arent", "was", "wasnt", "were", "werent", "be", "been", "being", "am", "have",
        "havent", "has", "hasnt", "had", "gonna", "wanna", "gotta",
        # negation
        "not", "never", "no", "nor",
        # request and capability framing, which keeps the same addressee
        "please", "pls", "kindly", "want", "wants", "wanted", "need", "needs", "needed", "let",
        "lets", "help", "try", "trying", "able", "capable", "willing", "supposed", "allowed",
        "going", "ready", "ability", "abilities", "capability", "capabilities", "power", "powers",
        "way", "ways", "means", "option", "options", "chance", "possible", "somehow", "even",
        # determiners, prepositions, conjunctions, particles
        "a", "an", "the", "this", "that", "these", "those", "my", "your", "our", "their", "its",
        "his", "her", "some", "any", "to", "for", "of", "in", "on", "at", "by", "with", "from",
        "into", "onto", "over", "up", "down", "out", "about", "as", "and", "or", "but", "so",
        "then", "if", "when", "while", "just", "still", "also", "ever", "right", "now", "here",
        "there", "again", "much", "more", "most", "very", "too", "only", "maybe", "perhaps",
        "actually", "really", "literally", "simply",
    }
)

# An explicit request for physical presence or physical handling. The marker list above names
# specific impossible acts; this names the general shape of one, so that `physically restart my
# unplugged server` is recognised without anybody having to have listed `restart a server` as
# impossible. It is deliberately two-part -- an embodiment word AND a physical-world object or a
# journey -- because `physically` alone qualifies plenty of things a runtime CAN do ("physically
# delete the file"), and refusing those would be the same defect pointing the other way.
_EMBODIMENT_FRAMING_RE = re.compile(
    r"\bphysically\b|\bin\s+person\b|\bwith\s+your\s+(?:own\s+)?hands\b"
    r"|\bin\s+the\s+physical\s+world\b|\bwith\s+your\s+own\s+eyes\b",
    re.IGNORECASE,
)
_PHYSICAL_OBJECT_RE = re.compile(
    r"\bunplug(?:ged|ging)?\b|\bpower\s+(?:button|cable|cord|supply|switch|strip)\b"
    r"|\bhardware\b|\brack\b|\bprinter\b|\brouter\b|\bcable\b|\bcables\b|\bbutton\b"
    r"|\bswitch\b|\bdoor\b|\bmonitor\b|\bscreen\s+in\s+front\s+of\s+me\b|\bwall\s+socket\b"
    r"|\bbreaker\b|\bfuse\b|\bplug\s+(?:it|them|that|this|the)\b",
    re.IGNORECASE,
)
# Travelling to a place is embodiment on its own -- there is no object to name.
_JOURNEY_RE = re.compile(
    r"\b(?:come|drive|walk|fly|travel)\s+(?:on\s+)?over\b"
    r"|\b(?:come|drive|walk|fly|travel)\s+(?:on\s+)?(?:down\s+|up\s+|out\s+)?"
    r"to\s+(?:my|our|the|his|her|their)\b"
    r"|\bshow\s+up\s+(?:at|in|to)\b|\bbe\s+there\s+in\s+person\b",
    re.IGNORECASE,
)

# The user has assigned the second person to a character: `assume you are a wizard`, `roleplay as
# a starship AI`. From there on `you` in the message denotes that character, not this process.
# The opener has to reach `you are` inside the same sentence, so `Suppose I need help. Can you
# drive over...` -- a hypothetical opener followed by a real request -- does not match.
_RUNTIME_PERSONA_RE = re.compile(
    r"\b(?:assum(?:e|ing)|imagin(?:e|ing)|suppos(?:e|ing)|pretend(?:ing)?|stipulat(?:e|ing)|say)\b"
    r"[^.!?\n]{0,60}?\byou(?:'re|\s+are|\s+were)\b"
    r"|\b(?:role[-\s]?play(?:ing)?|act(?:ing)?|play(?:ing)?)\s+as\b"
    r"|\bplay\s+the\s+(?:role|part|character)\s+of\b"
    r"|\byou(?:'re|\s+are)\s+(?:now\s+)?(?:playing|role[-\s]?playing|in\s+character)\b"
    r"|\bin\s+(?:this|that|the|my|our)\s+"
    r"(?:story|novel|game|campaign|scenario|world|universe|setting|fiction),?\s+you\b",
    re.IGNORECASE,
)

# An explicit return to the real world cancels the persona: whatever was set up earlier in the
# message, the user has said they are no longer inside it.
_FRAME_EXIT_RE = re.compile(
    r"\b(?:end|drop|leave|stop|exit|forget|ignore)\s+(?:the\s+|this\s+|that\s+|my\s+)?"
    r"(?:scenario|hypothetical|fiction|roleplay|role[-\s]play|novel|story|game|character|premise)\b"
    r"|\bback\s+to\s+(?:reality|the\s+real\s+world|real\s+life)\b"
    r"|\b(?:seriously|for\s+real|no\s+joke|in\s+real\s+life)\s*(?:,|;|:)?\s*(?:now\s+)?"
    r"(?:can|could|would|will|please)\b",
    re.IGNORECASE,
)


# What may follow an opening verb for it to have been an order: an object, a particle, or the
# preposition leading to one. A plain noun after it means the word was modifying that noun --
# `teleport SPELLS cost 3 mana` describes a game, `teleport ME to Paris` asks for something.
_IMPERATIVE_OBJECT_TOKENS = frozenset(
    {
        "me", "my", "mine", "us", "our", "ours", "this", "that", "these", "those", "it", "them",
        "him", "her", "the", "a", "an", "all", "everything", "everyone", "here", "there", "back",
        "over", "to", "for", "into", "onto", "out", "up", "down", "at", "in", "on", "from",
        "and", "then", "please", "now", "right", "immediately", "again", "already",
    }
)


def _normalize(token: str) -> str:
    return token.replace("'", "").lower()


def _impossible_mentions(sentence: str) -> list[tuple[int, int]]:
    """(start, end) offsets, inside one sentence, of every named or framed impossible action."""

    lowered = sentence.lower()
    spans: list[tuple[int, int]] = []
    for marker in _IMPOSSIBLE_REQUEST_MARKERS:
        position = lowered.find(marker)
        while position != -1:
            spans.append((position, position + len(marker)))
            position = lowered.find(marker, position + 1)
    if _EMBODIMENT_FRAMING_RE.search(sentence) and _PHYSICAL_OBJECT_RE.search(sentence):
        spans.extend(match.span() for match in _EMBODIMENT_FRAMING_RE.finditer(sentence))
    spans.extend(match.span() for match in _JOURNEY_RE.finditer(sentence))
    return sorted(set(spans))


def _opens_a_command(sentence: str, mention_end: int) -> bool:
    """Whether a sentence-opening mention is an order rather than a description.

    Two things disqualify it. An inflected form is a noun doing subject duty, not a verb giving an
    order (`Teleportation ... works`, `Mind reading ... costs`). And a bare noun straight after it
    means the word was modifying that noun rather than commanding it.
    """

    remainder = sentence[mention_end:]
    if remainder[:1].isalpha():
        # The marker did not end the word: `teleport` inside `teleportation` / `teleporting`.
        return False
    next_token = _TOKEN_RE.search(remainder)
    if next_token is None:
        return True
    return _normalize(next_token.group()) in _IMPERATIVE_OBJECT_TOKENS


def _agent_of_mention(sentence: str, mention_start: int) -> str:
    """Who performs the action mentioned at `mention_start`: `runtime`, `other`, or `unaddressed`.

    Walks back through tokens that carry no subject. The first token that can be one decides it.
    """

    for token_match in reversed(list(_TOKEN_RE.finditer(sentence, 0, mention_start))):
        token = _normalize(token_match.group())
        if token in _RUNTIME_SUBJECT_TOKENS:
            return "runtime"
        if token in _TRANSPARENT_TOKENS or token.endswith("ly"):
            continue
        return "other"
    return "unaddressed"


def impossible_action_agent(text: str) -> str | None:
    """Who the message asks to perform an impossible action, or None if it names none.

    Returns `runtime` when at least one mention is addressed here -- one such mention is enough,
    since `In my novel the hero teleports. Now can you teleport my laptop?` really does end in a
    request. Returns `other` when every mention belongs to something else in the user's world.
    """

    clean = " ".join(str(text or "").split())
    if not clean:
        return None
    persona_frame = bool(_RUNTIME_PERSONA_RE.search(clean)) and not _FRAME_EXIT_RE.search(clean)
    verdict: str | None = None
    for sentence in _SENTENCE_SPLIT_RE.split(clean):
        if not sentence.strip():
            continue
        for mention_start, mention_end in _impossible_mentions(sentence):
            agent = _agent_of_mention(sentence, mention_start)
            if agent == "unaddressed" and not _opens_a_command(sentence, mention_end):
                verdict = verdict or "other"
                continue
            if agent in {"runtime", "unaddressed"}:
                # A persona stipulation rebinds the second person to a character, so an
                # in-character `you` is not this process being asked for anything.
                if persona_frame:
                    verdict = verdict or "other"
                    continue
                return "runtime"
            verdict = verdict or "other"
    return verdict


def runtime_asked_for_impossible_action(text: str) -> bool:
    """Whether the user asked THIS runtime to perform or claim a physically impossible action."""

    return impossible_action_agent(text) == "runtime"
