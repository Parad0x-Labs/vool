"""Asking HOW to do something is not asking for it to be done.

The defect this module exists to close
--------------------------------------
Measured live on ff7f0d65, served surface::

    U: give me the git command to squash the last 3 commits. raw text only, no markdown
    A: `/Users/<user>/.vool_runtime_v050/workspace` is not inside a git repository.
       route=deterministic:workspace_runtime_fast_path

The user asked for a COMMAND -- general knowledge, answerable with no workspace at all. The planner
saw "git" and "commits", returned a `workspace.git_*` intent, and the fast path executed an
inspection of this machine's workspace. The answer is true and entirely beside the point: nobody
asked whether the workspace was a git repository.

The rule
--------
A request for instructions is a request for KNOWLEDGE. It names no target here, needs no tool, and
must not be answered by executing the thing it is asking about.

This is deliberately narrow. It answers one question -- "is this asking how, rather than asking to?"
-- and nothing else. It does not decide what should happen instead; declining to claim the turn is
the caller's business, and the ordinary lanes already answer instructional questions well.

On the list
-----------
The openers here are a closed class: the ways English asks for instruction. It is not vocabulary
lifted from the reported failure -- "git", "squash" and "commits" appear nowhere in it, and it does
not grow when a new tool is added. What grows the risk is the opposite mistake, so the matching is
anchored: the request has to OPEN this way, or carry the ask explicitly ("what is the command for").
A sentence that merely contains "how" in passing is not an instructional request.
"""

from __future__ import annotations

import re
from typing import Any

#: Openers that ask for instruction rather than action. Anchored at the start of the request, after
#: the ordinary politeness/filler that precedes almost any real message.
_INSTRUCTIONAL_OPENING_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|hey|hi|please|pls|and|but|also)\b[\s,]*)*"
    r"(?:"
    r"how\s+(?:do|would|can|should|does)\s+(?:i|you|we|one)\b"
    r"|how\s+to\b"
    r"|what(?:'?s| is| are)\s+the\s+(?:command|commands|syntax|steps?|way)\b"
    r"|which\s+(?:command|commands)\b"
    r"|give\s+me\s+(?:the\s+|a\s+)?(?:command|commands|syntax|steps?|example)\b"
    r"|show\s+me\s+(?:the\s+|a\s+)?(?:command|commands|syntax|steps?|example)\b"
    r"|(?:whats|what\s+is)\s+the\s+best\s+way\s+to\b"
    r"|explain\s+how\s+to\b"
    r")",
    re.IGNORECASE,
)

#: The same ask stated later in the sentence. Bounded to the explicit noun forms so an ordinary
#: mention of the word "command" in prose does not qualify.
_INSTRUCTIONAL_ASK_RE = re.compile(
    r"\b(?:give|show|tell)\s+me\s+(?:the\s+|a\s+)?(?:command|commands|syntax)\b"
    r"|\bwhat(?:'?s| is)\s+the\s+(?:command|syntax)\s+(?:to|for)\b"
    r"|\bcommand\s+(?:to|for)\s+\w+"
    # An EXAMPLE or illustration of doing something is a deliverable of text about the doing,
    # not the doing itself: "show me an example of how to fix a failing test" teaches, it does
    # not repair. Bound to the display/exemplify verbs so "fix the failing tests" stays an act.
    r"|\b(?:show|give|tell)\s+(?:me\s+|us\s+)?(?:an?\s+)?(?:example|illustration|sample|demo)\b"
    r"|\bwalk\s+(?:me|us)\s+through\b",
    re.IGNORECASE,
)

#: A request that names THIS workspace/machine is observational even when phrased instructionally
#: ("how do I see my git status here"). The possessive/deictic is what makes it about this machine.
_NAMES_THIS_TARGET_RE = re.compile(
    r"\b(?:my|our|this|the\s+current)\s+"
    r"(?:workspace|repo|repository|project|folder|directory|machine|branch|checkout)\b"
    r"|\b(?:here|on\s+(?:this|my)\s+(?:machine|box|laptop|computer))\b",
    re.IGNORECASE,
)

#: Asking for the TEXT of an artifact is authoring, not action (MF-13). Measured live 2026-08-15:
#: "write a bash one-liner that finds all `.log` files..." was classified onto the action path,
#: routed output_mode=tool_intent, the model was forced to emit a tool call (it produced a
#: meaningless operator.list_tools), the approval gate pended it, and the turn shipped "I wasn't
#: able to turn that into a completed action". "draft a polite email to David" was refused for
#: lacking an outbound channel nobody asked it to use. The verb set is the closed class of English
#: authoring verbs; the noun set is text artifacts -- deliberately NOT file/folder (writing a FILE
#: is a real machine action, see test_writing_prose_is_not_writing_a_file).
_AUTHORING_REQUEST_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|hey|hi|yo|please|pls|just|and|but|also|instead|now|then)\b[\s,]*)*"
    r"(?:(?:can|could|would)\s+(?:you|u)\s+(?:please\s+)?)?"
    r"(?:write|draft|compose|generate)\s+(?:me\s+|up\s+)?(?:a|an|the)?\s*"
    r"(?:[\w.+#/-]+\s+){0,4}?"
    r"(?:command|one[-\s]?liner|script|query|queries|email|e-mail|mail|message|sms|text\s+message|"
    r"reply|snippet|interface|function|regex|expression|schema|struct|class|type|"
    r"letter|note|memo|tweet|post|caption|paragraph|poem|essay|summary|template|statement)\b",
    re.IGNORECASE,
)

#: The same authoring ask after an in-turn pivot ("WAIT. Cancel the email. Instead, write a bash
#: one-liner..."): anchored to a sentence start, not only the turn start.
_AUTHORING_PIVOT_RE = re.compile(
    r"(?:^|[.!?]\s+)(?:instead|now|then|just)?[\s,]*"
    r"(?:write|draft|compose|generate)\s+(?:me\s+|up\s+)?(?:a|an|the)?\s*"
    r"(?:[\w.+#/-]+\s+){0,4}?"
    r"(?:command|one[-\s]?liner|script|query|queries|email|e-mail|mail|message|sms|text\s+message|"
    r"reply|snippet|interface|function|regex|expression|schema|struct|class|type|"
    r"letter|note|memo|tweet|post|caption|paragraph|poem|essay|summary|template|statement)\b",
    re.IGNORECASE,
)

#: "do not send it", "don't run this", "without executing anything": the execution verb appears
#: only under negation, which AFFIRMS the authoring reading rather than contradicting it.
_NEGATED_EXECUTION_RE = re.compile(
    r"\b(?:do\s+not|don'?t|never|without|won'?t|not\s+going\s+to|no\s+need\s+to|"
    r"you\s+(?:must|should)\s+not|must\s+not|should\s+not)\s+"
    r"(?:actually\s+)?(?:send|execute|run|apply|deploy|deliver|schedule|save)\b[^.!?]*",
    re.IGNORECASE,
)

#: An authoring turn that ALSO asks for the artifact to be executed, sent, or materialized on disk
#: stays an action -- "write an email to Bob and send it" needs the outbound machinery.
_EXPLICIT_EXECUTION_ASK_RE = re.compile(
    r"\b(?:and|then)\s+(?:send|execute|run|apply|deploy|schedule|deliver)\s+(?:it|them|that|this)\b"
    r"|\bsend\s+(?:it|this|that|the\s+(?:email|e-mail|mail|message|sms|reply))\b"
    r"|\b(?:run|execute|apply)\s+(?:it|this|that)\s+(?:now|for\s+me)\b"
    r"|\bsave\s+(?:it|this|that)\s+(?:as|to|in)\b"
    r"|\b(?:in|into|to)\s+(?:my|the|this|our)\s+(?:project|repo|repository|workspace|folder|directory)\b",
    re.IGNORECASE,
)

# An authoring noun becomes a real machine action when the sentence also names where the artifact
# must be materialized. This is deliberately preposition-bound: "write a note ABOUT my desktop"
# remains prose authoring, while "write a note TO my desktop" requests a file write.
_EXPLICIT_MACHINE_MATERIALIZATION_RE = re.compile(
    r"\b(?:write|save|create|put)\b[^.!?\n]{0,120}"
    r"\b(?:to|in|into|on)\s+(?:(?:my|the|this|our)\s+)?"
    r"(?:desktop|downloads?|documents?|home(?:\s+folder)?|machine)\b"
    r"|\b(?:write|save|create|put)\b[^.!?\n]{0,120}~/(?:desktop|downloads?|documents?)\b",
    re.IGNORECASE,
)


# These are grammatical request leads, rather than words describing a subject.
_ACTION_REQUEST_LEAD_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|hey|hi|please|pls|kindly|just|now|then|also|"
    r"instead|next|first|finally|and|but|go\s+ahead(?:\s+and)?)\b[\s,]*"
    r"|(?:can|could|would|will)\s+(?:you|u)(?:\s+please)?\s+"
    # "can't you export this chat ..." asks for the action; "could you not paint ..." does not, and
    # keeps its "not" in front of the verb, so it is never read as a request.
    r"|(?:can['’]?t|cannot|couldn['’]?t|won['’]?t|wouldn['’]?t)\s+(?:you|u)(?:\s+please)?\s+"
    r"|i\s+(?:want|need|would\s+like)(?:\s+you)?\s+to\s+"
    r"|i['’]d\s+like(?:\s+you)?\s+to\s+"
    r"|(?:let['’]s|lets|help\s+me(?:\s+to)?)\s+)*",
    re.IGNORECASE,
)
_EXPLICIT_ACTION_LEAD_RE = re.compile(
    r"\b(?:please|pls|kindly|(?:can|could|would|will|can['’]?t|cannot|couldn['’]?t|won['’]?t|wouldn['’]?t)\s+(?:you|u)|"
    r"i\s+(?:want|need|would\s+like)|i['’]d\s+like|help\s+me|let['’]s)\b",
    re.IGNORECASE,
)
# A sibling action may follow an established action request, but a sibling
# historical verb in a question cannot establish that request.
_ACTION_SEQUENCE_VERB_RE = re.compile(
    r"(?:create|make|mkdir|write|save|append|put|edit|change|delete|remove|"
    r"rename|move|export|dump|generate|draw|paint|render|design|produce|"
    r"sketch|illustrate|doodle|imagine|give\s+me|whip\s+up|cook\s+up)\b",
    re.IGNORECASE,
)
_OTHER_REQUEST_VERB_RE = re.compile(
    r"(?:tell|show|explain|describe|summarize|summarise|read|list|check|find|"
    r"compare|send|execute|run|apply|deploy|schedule|deliver|draft|compose)\b",
    re.IGNORECASE,
)
_QUOTED_ACTION_TEXT_RE = re.compile(
    chr(96) * 3 + r".*?" + chr(96) * 3
    + "|" + chr(96) + "[^" + chr(96) + "]*" + chr(96)
    + r'|"(?:\\.|[^"\\])*"|(?<!\w)\'(?:\\.|[^\'\\])*\'(?!\w)'
    + r'|“(?:\\.|[^”\\])*”|(?<!\w)‘.*?’(?!\w)',
    re.DOTALL,
)
# A colon-introduced paragraph is displayed data until its blank-line boundary.
# A new request outside that paragraph can still establish its own action clause.
_REPORTED_ACTION_BLOCK_RE = re.compile(
    r"(?m)(^.*:[ \t]*\n)([^\n]+(?:\n(?![ \t]*\n)[^\n]+)*)"
)
_ACTION_CLAUSE_BREAK_RE = re.compile(
    r",\s*(?:(?:and\s+)?then|and|but|also|next)?\s*"
    r"|\s+\b(?:and(?:\s+then)?|then|but)\b\s+",
    re.IGNORECASE,
)
_ACTION_DESCRIPTION_RE = re.compile(
    r"\b(?:who|whose|which|that|where|when|while|whether|what|how)\b",
    re.IGNORECASE,
)


def requested_action_clauses(
    text: Any, verbs: re.Pattern[str], *, location_prefix: re.Pattern[str] | None = None
) -> tuple[str, ...]:
    """Return clauses whose leading verb is actually requested.

    Quoted/code text cannot supply a request header. An explicit second request
    can follow information or a past description; a bare coordinated verb only
    inherits an already established action request. This is request grammar,
    not a grant of permission to execute anything.
    """
    raw = str(text or "")
    visible = _QUOTED_ACTION_TEXT_RE.sub(lambda match: " " * len(match.group()), raw)
    visible = _REPORTED_ACTION_BLOCK_RE.sub(
        lambda match: match.group(1) + " " * len(match.group(2)), visible
    )
    clauses: list[str] = []
    sentence_start = 0
    # Sentence-ending punctuation is attached to the word it ends. A detached period ("save it
    # as . txt in the Marchtest folder") is part of a filename, not the end of the request.
    sentence_ends = list(re.finditer(r"(?<=\S[.!?])\s+|;|\n+", visible))
    for boundary in [*sentence_ends, None]:
        sentence_end = boundary.start() if boundary is not None else len(raw)
        sentence = visible[sentence_start:sentence_end]
        if location_prefix is not None:
            lead = _ACTION_REQUEST_LEAD_RE.match(sentence)
            location = location_prefix.match(sentence[lead.end():])
            if location:
                end = lead.end() + location.end()
                sentence = sentence[:lead.end()] + sentence[lead.end():end].replace(",", " ") + sentence[end:]
        breaks = list(_ACTION_CLAUSE_BREAK_RE.finditer(sentence))
        starts = [0, *(match.end() for match in breaks)]
        ends = [*(match.start() for match in breaks), len(sentence)]
        actions: list[tuple[int, int, bool]] = []
        action_frame = False
        # A plain statement ("you are acting weird but create a hello world file") neither requests
        # nor describes an action, so an imperative after it is a request. A question, a clause
        # opening with an auxiliary ("Did Morgan create a portrait and make ..."), a description
        # ("what I saved") or another request ("Tell me ..., then create ...") ends that frame.
        statements_only = not (
            sentence.rstrip().endswith("?")
            or re.match(r"\s*(?:did|does|do|is|are|was|were|can|could|would|will|has|have|had|should)\b", sentence, re.I)
        )
        for index, (start, end) in enumerate(zip(starts, ends)):
            part = sentence[start:end]
            lead = _ACTION_REQUEST_LEAD_RE.match(part)
            offset = lead.end()
            request_start = offset
            if location_prefix is not None:
                location = location_prefix.match(part[offset:])
                if location:
                    offset += location.end()
                    offset += _ACTION_REQUEST_LEAD_RE.match(part[offset:]).end()
                else:
                    request_start = offset
            if location_prefix is None or not location:
                request_start = offset
            tail = part[offset:]
            own = verbs.match(tail)
            sequence = _ACTION_SEQUENCE_VERB_RE.match(tail)
            other = _OTHER_REQUEST_VERB_RE.match(tail)
            explicit = bool(_EXPLICIT_ACTION_LEAD_RE.search(part[:offset]))
            if (own or sequence or other) and (index == 0 or explicit or action_frame or statements_only):
                clause_start = breaks[index - 1].start() if index else 0
                actions.append((clause_start, start + request_start, bool(own)))
                action_frame = bool(sequence) and not _ACTION_DESCRIPTION_RE.search(
                    tail[(own or sequence or other).end():]
                )
            elif _ACTION_DESCRIPTION_RE.search(part):
                action_frame = False
            if own or sequence or other or _ACTION_DESCRIPTION_RE.search(part) or not re.search(r"[A-Za-z]{2,}", part):
                # A markup-only fragment (a ">" quote marker) is not a statement of the speaker's.
                statements_only = False
        for index, (_, verb_start, owned) in enumerate(actions):
            if owned:
                end = actions[index + 1][0] if index + 1 < len(actions) else len(sentence)
                clauses.append(raw[sentence_start + verb_start:sentence_start + end].strip())
        sentence_start = boundary.end() if boundary is not None else len(raw)
    return tuple(clauses)


def asks_for_instructions_not_execution(text: Any) -> bool:
    """Whether this request asks HOW to do something rather than asking for it to be done.

    True for the two shapes of the same ask: HOW-questions ("how do I mount a volume") and
    authoring requests ("write a bash one-liner that..."), because in both the deliverable is
    TEXT the user will use themselves. False whenever the request names this workspace or machine
    ("how do I check my repo's status" is about THIS target), and false when an authoring turn
    also asks for the artifact to be sent, executed, or written into the project.
    """

    request = " ".join(str(text or "").split())
    if not request:
        return False
    if _AUTHORING_REQUEST_RE.match(request) or _AUTHORING_PIVOT_RE.search(request):
        # A NEGATED execution phrase is the opposite of an execution ask: "draft the email, do
        # not send it" is authoring twice over. Scrub negated clauses before the override test.
        affirmative = _NEGATED_EXECUTION_RE.sub(" ", request)
        # A command's infinitive/relative complement describes the command;
        # it does not request that embedded operation here. A separately
        # requested send/save/run clause still retains the execution override.
        execution_verbs = re.compile(
            r"(?:write|draft|compose|generate|send|execute|run|apply|deploy|"
            r"deliver|schedule|save|create|put)\b", re.IGNORECASE
        )
        for clause in requested_action_clauses(affirmative, execution_verbs):
            if re.match(r"(?:send|execute|run|apply|deploy|deliver|schedule)\b", clause, re.IGNORECASE):
                return False
            authoring = _AUTHORING_REQUEST_RE.match(clause)
            if authoring:
                description = re.search(
                    r"\b(?:that|which)\b|\bto\s+(?:"
                    + _ACTION_SEQUENCE_VERB_RE.pattern
                    + r"|" + _OTHER_REQUEST_VERB_RE.pattern + r")",
                    clause[authoring.end():], re.IGNORECASE,
                )
                if description:
                    clause = clause[:authoring.end() + description.start()]
            if (_EXPLICIT_EXECUTION_ASK_RE.search(clause)
                    or _EXPLICIT_MACHINE_MATERIALIZATION_RE.search(clause)):
                return False
        return True
    if _NAMES_THIS_TARGET_RE.search(request):
        return False
    return bool(
        _INSTRUCTIONAL_OPENING_RE.match(request) or _INSTRUCTIONAL_ASK_RE.search(request)
    )


def asks_how_to(text: Any) -> bool:
    """Whether this request asks HOW something is done -- the instructional half of
    `asks_for_instructions_not_execution`, without its authoring reading.

    A recognizer that seats an action for "draft a reply saying X" (composing THIS mailbox's reply)
    still needs to refuse "how should I write a polite reminder email?": the deliverable of a
    how-question is an explanation, whatever verbs it mentions. False, as above, when the request names
    this workspace or machine.
    """

    request = " ".join(str(text or "").split())
    if not request or _NAMES_THIS_TARGET_RE.search(request):
        return False
    return bool(_INSTRUCTIONAL_OPENING_RE.match(request) or _INSTRUCTIONAL_ASK_RE.search(request))


__all__ = ["asks_for_instructions_not_execution", "asks_how_to"]
