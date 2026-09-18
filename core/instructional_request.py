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
        return not (
            _EXPLICIT_EXECUTION_ASK_RE.search(affirmative)
            or _EXPLICIT_MACHINE_MATERIALIZATION_RE.search(affirmative)
        )
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
