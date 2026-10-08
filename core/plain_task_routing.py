from __future__ import annotations

import re

_TRANSLATE_RE = re.compile(r"^\s*translate\b", re.IGNORECASE)
_REWRITE_RE = re.compile(r"^\s*(?:please\s+)?rewrite\b", re.IGNORECASE)
_SUMMARIZE_RE = re.compile(r"^\s*(?:please\s+)?(?:summari[sz]e|write\s+a\s+summary\s+of)\b", re.IGNORECASE)
_SIMPLE_CODE_RE = re.compile(
    r"^\s*(?:please\s+)?write\b.{0,80}\b(?:function|python|javascript|typescript|code)\b",
    re.IGNORECASE,
)
_SIMPLE_EXPLAIN_RE = re.compile(r"^\s*(?:please\s+)?explain\b", re.IGNORECASE)

# Ordinary requests that one answering model can handle together without tools or a decomposition
# pass. This is deliberately narrower than the generic turn planner: it recognizes only direct
# knowledge/text operations, and therefore cannot swallow a file, web, machine, or side-effecting
# request. The boundary matcher only splits outside quotes and only splits a conjunction when what
# follows has its own request head. "salt and pepper" therefore stays one subject, while "explain
# salt and calculate 8 x 7" becomes two requests.
_ORDINARY_REQUEST_HEAD_RE = re.compile(
    r"^(?:(?:please|pls|plz)\s+)?(?:"
    r"answer|calculate|calc|compute|define|describe|explain|give|gimme|list|make|mak|name|provide|"
    r"say|suggest|summari[sz]e|tell|translate|draft|rewrit(?:e)?|reword|write|"
    r"what|what'?s|whats|wht|wat|which|who|why|how|where|when"
    r")\b",
    re.IGNORECASE,
)
_CONNECTOR_REQUEST_RE = re.compile(
    r"\b(?:and|also|plus|then)\b\s*,?\s+(?="
    r"(?:(?:please|pls|plz)\s+)?(?:"
    r"answer|calculate|calc|compute|define|describe|explain|give|gimme|list|make|mak|name|provide|"
    r"say|suggest|summari[sz]e|tell|translate|draft|rewrit(?:e)?|reword|write|"
    r"what|what'?s|whats|wht|wat|which|who|why|how|where|when"
    r")\b)",
    re.IGNORECASE,
)
_INLINE_LIST_MARKER_RE = re.compile(r"(?<!\w)(?:[A-Za-z]|\d{1,2})\s*[).:-]\s+")
_LEADING_CONNECTOR_RE = re.compile(r"^(?:(?:and|also|plus|then)\s*,?\s+)+", re.IGNORECASE)
_RESPONSE_SHAPE_PREFIX_RE = re.compile(
    r"^(?:in\s+(?:(?:one|two|three|\d+)\s+)?(?:plain\s+|short\s+|brief\s+)?"
    r"(?:sentence|sentences|line|lines|words?)|briefly|in\s+brief)\s*,?\s+",
    re.IGNORECASE,
)
_MULTIPART_PREAMBLE_RE = re.compile(
    r"^(?:(?:please|pls|plz)\s+)?(?:"
    r"(?:do|answer|handle)\s+(?:these|them|all|em)"
    r"(?:\s+(?:separately|separate|below|in\s+order|parts?))*"
    r"|(?:three|3)\s+(?:little|lil|small|quick)\s+(?:things|requests?)"
    r"(?:\s+(?:please|pls|plz))?"
    r")\s*:?$",
    re.IGNORECASE,
)
_MULTIPART_PREAMBLE_PREFIX_RE = re.compile(
    r"^(?:(?:please|pls|plz)\s+)?(?:"
    r"(?:do|answer|handle)\s+(?:these|them|all|em)"
    r"(?:\s+(?:separately|separate|below|in\s+order|parts?))*"
    r"|(?:three|3)\s+(?:little|lil|small|quick)\s+(?:things|requests?)"
    r"(?:\s+(?:please|pls|plz))?"
    r")\s*:\s*",
    re.IGNORECASE,
)
_OPERATIONAL_REQUEST_RE = re.compile(
    r"\b(?:search|grep|find)\b[^.!?;\n]{0,80}\b(?:files?|folders?|workspace|repo(?:sitory)?|codebase)\b|"
    r"\b(?:files?|folders?|workspace|repo(?:sitory)?|codebase)\b[^.!?;\n]{0,80}\b(?:search|grep|find)\b|"
    r"\b(?:read|open|inspect|edit|modify|create|delete|remove|rename|move|append|save|run|execute|"
    r"download|upload|send|post|write)\b[^.!?;\n]{0,80}\b(?:file|folder|directory|workspace|repo(?:sitory)?|"
    r"codebase|path|terminal|shell|command|script|readme)\b|"
    r"\b(?:look\s*up|browse|search)\b[^.!?;\n]{0,80}\b(?:web|online|internet|latest|current|news|weather|"
    r"forecast|price|score)\b",
    re.IGNORECASE,
)
_CONCRETE_FILE_OR_PATH_RE = re.compile(
    r"(?:^|[\s('\"`])(?:~|\.{1,2})?/[^\s'\"`,;)]{2,}|"
    r"\b[A-Za-z0-9_.-]+\.(?:py|toml|json|ya?ml|md|txt|ts|tsx|js|jsx|csv|pdf|docx?)\b",
    re.IGNORECASE,
)
_LOCAL_RESOURCE_REQUEST_RE = re.compile(
    r"\b(?:where|which\s+place|what\s+place)\s+(?:can|could|should|would)\s+(?:i|we)\b|"
    r"\b(?:find|locate|recommend|suggest)\b[^.!?;\n]{0,100}"
    r"\b(?:near\s+me|nearby|around\s+me|in\s+my\s+area)\b|"
    r"\b(?:near\s+me|nearby|in\s+my\s+area)\b",
    re.IGNORECASE,
)
_ANSWER_PART_MARKER_RE = re.compile(
    r"(?m)^\s*(?:\*\*)?(?P<marker>[A-Za-z]|\d{1,2})[).:-](?:\*\*)?\s+\S"
)

# These labels are an upstream semantic verdict, not words guessed from the request. A research
# turn must retain the tool lane even when its related subquestions happen to look like an
# ordinary multipart question grammatically.
_TOOL_REACHABLE_MULTIPART_TASK_CLASSES = frozenset({"research", "chat_research"})

_QUOTE_PAIRS = {'"': '"', "“": "”", "'": "'", "‘": "’"}


_INTERROGATIVE_FOCUS_RE = re.compile(r",\s*((?:what|which)\s+.+)$", re.IGNORECASE)


def _interrogative_focus_floor(visible: str) -> int:
    """Where `_INTERROGATIVE_FOCUS_RE` can first match: its `.+` cannot cross a newline, so a match
    starts after the last inner newline or at the last comma before it. Searching from there finds
    the same match without re-scanning every earlier line from every earlier comma (quadratic)."""
    body = visible[:-1] if visible.endswith("\n") else visible
    last_newline = body.rfind("\n")
    if last_newline < 0:
        return 0
    comma = body.rfind(",", 0, last_newline)
    return comma if comma >= 0 else last_newline


def _is_word_apostrophe(text: str, index: int, char: str) -> bool:
    return bool(
        char in {"'", "’"}
        and index > 0
        and index + 1 < len(text)
        and text[index - 1].isalnum()
        and text[index + 1].isalnum()
    )


def _unquoted_match_starts(text: str, pattern: re.Pattern[str]) -> tuple[re.Match[str], ...]:
    """Return regex matches whose start is outside a quoted span."""

    matches = list(pattern.finditer(text))
    if not matches:
        return ()
    accepted: list[re.Match[str]] = []
    closing: str | None = None
    match_index = 0
    for index, char in enumerate(text):
        if closing is not None:
            if char == closing:
                closing = None
            continue
        if char in _QUOTE_PAIRS and not _is_word_apostrophe(text, index, char):
            closing = _QUOTE_PAIRS[char]
            continue
        while match_index < len(matches) and matches[match_index].start() < index:
            match_index += 1
        if match_index < len(matches) and matches[match_index].start() == index:
            accepted.append(matches[match_index])
            match_index += 1
    return tuple(accepted)


def _split_at_matches(text: str, matches: tuple[re.Match[str], ...]) -> list[str]:
    if not matches:
        return [text]
    parts: list[str] = []
    start = 0
    for match in matches:
        prefix = text[start : match.start()].strip()
        if prefix:
            parts.append(prefix)
        start = match.end()
    suffix = text[start:].strip()
    if suffix:
        parts.append(suffix)
    return parts


def _split_unquoted_sentences(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    closing: str | None = None
    for index, char in enumerate(text):
        if closing is not None:
            if char == closing:
                closing = None
            continue
        if char in _QUOTE_PAIRS and not _is_word_apostrophe(text, index, char):
            closing = _QUOTE_PAIRS[char]
            continue
        if char in ".?!;\n":
            part = text[start : index + (0 if char == "\n" else 1)].strip()
            if part:
                parts.append(part)
            start = index + 1
    suffix = text[start:].strip()
    if suffix:
        parts.append(suffix)
    return parts


def _outside_quoted_spans(text: str) -> str:
    """Text outside quoted examples, preserving spaces so word boundaries remain trustworthy."""

    visible: list[str] = []
    closing: str | None = None
    raw = str(text or "")
    for index, char in enumerate(raw):
        if closing is not None:
            if char == closing:
                closing = None
            visible.append(" ")
            continue
        if char in _QUOTE_PAIRS and not _is_word_apostrophe(raw, index, char):
            closing = _QUOTE_PAIRS[char]
            visible.append(" ")
            continue
        visible.append(char)
    return "".join(visible)


def _normalized_ordinary_clause(text: str) -> str:
    clause = str(text or "").strip(" \t\r\n.!?;:")
    clause = _LEADING_CONNECTOR_RE.sub("", clause).strip()
    clause = _RESPONSE_SHAPE_PREFIX_RE.sub("", clause).strip()
    return clause


#: An imperative whose whole object is the request before it -- "explain it in detail",
#: "describe that", "say more". It elaborates the previous clause; counting it as a second
#: part made the ordinary-chat guard demand a numbered two-part reply and re-ask a single
#: advisory question twice (measured on the presentation s2 pin).
_ELABORATION_CONTINUATION_RE = re.compile(
    r"^(?:please\s+|pls\s+|plz\s+)?(?:explain|describe|expand|elaborate|detail|"
    r"tell\s+me\s+more|say\s+more|go\s+deeper|drill\s+down)\b"
    r"(?:\s+(?:it|that|this|them|in\s+detail|more|further|briefly|shortly|at\s+length))+\s*\.?\s*$",
    re.IGNORECASE,
)


_NUMBERED_ANSWER_INSTRUCTION_RE = re.compile(
    r"^(?:(?:please|pls|plz)\s+)?(?:"
    r"(?:number|label|enumerate)\s+(?:(?:each|every|all|the|your)\s+)*(?:answers?|responses?|parts?|them)\b"
    r"|(?:give|provide|use|return|write)\s+(?:(?:the|your|a)\s+)?numbered\s+(?:answers?|responses?|list)\b"
    r"|(?:answer|respond|reply)\s+(?:with|using|in)\s+(?:a\s+)?numbered\s+(?:answers?|responses?|list)\b"
    r"|answer\s+(?:each|every|all)\b[^.!?]{0,40}\b(?:with|using|in)\s+numbered\s+(?:answers?|responses?|list)\b"
    r")", re.IGNORECASE,
)
_ANSWER_DETAIL_HEAD_RE = re.compile(
    r"^(?:(?:please|pls|plz)\s+)?(?:give|provide|show|state|report|return|include)\s+"
    r"(?P<details>both\s+.+)$", re.IGNORECASE,
)
_QUESTION_PREDICATE_RE = re.compile(
    r"\b(?:did|do|does|is|are|was|were|has|have|had|can|could|will|would|should)\b", re.IGNORECASE,
)


def _numbered_answer_instruction(clause: str) -> bool:
    return bool(_NUMBERED_ANSWER_INSTRUCTION_RE.match(_normalized_ordinary_clause(
        _outside_quoted_spans(clause))))


def _answer_length_directive(clause: str) -> bool:
    """A clause that only states the answer's length shapes the other parts; it is not a part."""
    from core.ordinary_chat_response_guard import is_answer_length_directive

    return is_answer_length_directive(_outside_quoted_spans(clause))


def user_requires_numbered_answers(text: str) -> bool:
    """Only an unquoted instruction addressed to the answer imposes numbered output."""
    return any(_numbered_answer_instruction(part) for part in _split_unquoted_sentences(text))


def _dependent_answer_details(clause: str, preceding_request: str) -> bool:
    """Recognize a co-referring detail instruction, not a new independent deliverable.

    ``both`` refers back to the preceding question's answer. An indefinite new artifact
    (``give both a title and a slogan``) remains independent. This is structural binding;
    it neither certifies that a reply contains these details nor reads their truth.
    """
    prior = _normalized_ordinary_clause(_outside_quoted_spans(preceding_request))
    if not re.match(r"^(?:what|which|who|why|how|where|when)\b", prior, re.IGNORECASE):
        return False
    match = _ANSWER_DETAIL_HEAD_RE.match(_normalized_ordinary_clause(_outside_quoted_spans(clause)))
    if match is None:
        return False
    details = match.group("details")
    return bool(re.search(r"\band\b|,", details, re.IGNORECASE)) and not bool(
        re.search(r"\b(?:a|an)\b", details, re.IGNORECASE)
        or _CONNECTOR_REQUEST_RE.search(details)
    )


def requested_answer_field_shape(text: str) -> str:
    """Classify requested field coordination without certifying semantic coverage.

    The same structural authority serves scalar shortcuts and retrieval composition.
    Quoted or descriptive object conjunctions do not create requested fields; an
    unknown request grants neither scalar authority nor positive field coordination.
    """
    from core.response_constraints import requested_output_item_count
    from core.turn_ir import ClauseKind, classify_clause_kind, parse_turn_ir

    requested_items = requested_output_item_count(text)
    if requested_items is not None and requested_items > 1:
        return "multiple_items"
    visible = _outside_quoted_spans(text)
    if _OPERATIONAL_REQUEST_RE.search(visible) or _CONCRETE_FILE_OR_PATH_RE.search(visible):
        return "unknown"
    # Bind the interrogative after leading contextual adjuncts before TurnIR's
    # connective split. A noun such as "rename" inside "before ... move and rename"
    # is context, not an imperative. A genuinely requested prefix is never discarded.
    focus = _INTERROGATIVE_FOCUS_RE.search(visible, _interrogative_focus_floor(visible))
    if focus is not None:
        prefix = visible[:focus.start()]
        context_parts = [part.strip() for part in prefix.split(",") if part.strip()]
        if context_parts and all(classify_clause_kind(part) is ClauseKind.UNKNOWN for part in context_parts):
            visible = focus.group(1)
    turn = parse_turn_ir(visible, response_shape_parser=None)
    requests = [clause.request_text for clause in turn.clauses if clause.kind is not ClauseKind.UNKNOWN]
    if len(requests) != 1:
        return "unknown"
    request = _normalized_ordinary_clause(requests[0])
    match = re.match(r"^(?:what|which)\s+(.+)$", request, re.IGNORECASE)
    if match is None:
        return "unknown"
    nominal = match.group(1)
    predicate = _QUESTION_PREDICATE_RE.search(nominal)
    if predicate is not None:
        # "What is the code?" puts the requested noun after the copula; an inverted
        # past question ("What code did I set?") puts it before the predicate.
        nominal = (nominal[predicate.end():] if predicate.start() == 0 else nominal[:predicate.start()])
    nominal = re.split(r"\b(?:for|of|from|in|on|at|with|by)\b", nominal, maxsplit=1, flags=re.IGNORECASE)[0]
    if not nominal.strip():
        return "unknown"
    return ("coordinated_fields" if re.search(r"\band\b|,", nominal, re.IGNORECASE)
            else "single_field")


def scalar_answer_covers_requested_shape(text: str) -> bool:
    """Only a positively established single field permits a scalar shortcut."""
    return requested_answer_field_shape(text) == "single_field"


def ordinary_plain_requests(text: str) -> tuple[str, ...]:
    """Return every request when the whole turn is a small, non-operational text task.

    An operational fragment invalidates the whole ordinary lane. That is load-bearing: a message
    containing "search the files" plus two prose transformations must still reach workspace tools,
    not become a no-tools prompt merely because two clauses happen to be ordinary in isolation.
    """

    raw = str(text or "").strip()
    if not raw or _OPERATIONAL_REQUEST_RE.search(raw) or _CONCRETE_FILE_OR_PATH_RE.search(raw):
        return ()
    raw = _MULTIPART_PREAMBLE_PREFIX_RE.sub("", raw, count=1).strip()

    requests: list[str] = []
    for sentence in _split_unquoted_sentences(raw):
        listed = _split_at_matches(
            sentence,
            _unquoted_match_starts(sentence, _INLINE_LIST_MARKER_RE),
        )
        for listed_part in listed:
            connected = _split_at_matches(
                listed_part,
                _unquoted_match_starts(listed_part, _CONNECTOR_REQUEST_RE),
            )
            for part in connected:
                clause = _normalized_ordinary_clause(part)
                if (
                    not clause
                    or _MULTIPART_PREAMBLE_RE.fullmatch(clause)
                    or _numbered_answer_instruction(clause)
                    or _answer_length_directive(clause)
                ):
                    continue
                if requests and _dependent_answer_details(clause, requests[-1]):
                    requests[-1] = requests[-1] + ". " + clause
                    continue
                if not _ORDINARY_REQUEST_HEAD_RE.match(clause):
                    return ()
                if (
                    requests
                    and _ELABORATION_CONTINUATION_RE.fullmatch(clause)
                ):
                    # Depth asked of the request just collected, not a new part.
                    continue
                requests.append(clause)
    return tuple(requests)


def ordinary_plain_request_count(text: str) -> int:
    """Count independent, non-operational requests in a small plain-text turn.

    The count is only a routing/prompt-shape signal.  It does not split or execute the turn, so a
    three-part explanation/calculation/title prompt still makes exactly one answering-model call.
    """

    return len(ordinary_plain_requests(text))


def multipart_task_class_requires_tool_reachability(task_class: str) -> bool:
    """Whether an upstream task verdict forbids demotion to ordinary no-tool chat."""

    return str(task_class or "").strip().lower() in _TOOL_REACHABLE_MULTIPART_TASK_CLASSES


def multipart_has_non_plain_request(text: str) -> bool:
    """Whether any unquoted part needs information or action beyond one plain model answer.

    This is deliberately about capability shape, not named entities or reported prompts. Explicit
    workspace/web/action forms are already owned by the operational and path recognizers. Current
    real-world facts reuse the runtime's shared recency authority, while local resource questions
    are recognized by their place-seeking frame (``where can I ...`` / ``... near me``), not by a
    vocabulary of businesses, cities, or services.
    """

    visible = " ".join(_outside_quoted_spans(text).strip().split())
    if not visible:
        return False
    if _OPERATIONAL_REQUEST_RE.search(visible) or _CONCRETE_FILE_OR_PATH_RE.search(visible):
        return True
    # "list" and "tell" are ordinary request heads, so "List the files in my workspace and tell me
    # which ones are markdown." counted as two plain parts and went to the no-tools lane (measured
    # on 9adff83, 2026-10-06). A part that names the workspace and asks what files it holds needs
    # the workspace tools, whatever verb opens it; the workspace recognizer owns that reading.
    from core.agent_runtime.workspace_intent_detection import asks_to_list_the_bound_workspace

    if asks_to_list_the_bound_workspace(visible):
        return True

    from core.task_router import looks_like_live_recency_lookup

    return bool(
        looks_like_live_recency_lookup(visible)
        or _LOCAL_RESOURCE_REQUEST_RE.search(visible)
    )


def is_ordinary_multi_part_plain_task(text: str, *, task_class: str = "") -> bool:
    """True when every part belongs in one complete, tool-free model answer."""

    if multipart_task_class_requires_tool_reachability(task_class):
        return False
    return ordinary_plain_request_count(text) >= 2 and not multipart_has_non_plain_request(text)


def ordinary_multi_part_answer_status(user_text: str, answer_text: str) -> str:
    """Structural coverage verdict; unindexed prose is not a semantic completeness proof."""
    required = ordinary_plain_request_count(user_text)
    if required < 2:
        return "not_applicable"
    return ordinary_indexed_answer_status(
        required, answer_text, numbering_required=user_requires_numbered_answers(user_text),
    )


# Runtime narration about which model or provider served "this turn" is operator detail the
# runtime itself emits (core.agent_runtime.memory_runtime._single_candidate_failure_hint). When a
# provider hands that narration back as its whole reply, it carries no requested part at all: the
# measured case was "<model-id> was the only model this turn was allowed to use" becoming the entire
# final answer to "Explain ... Calculate ... Give a 7-word title."
_RUNTIME_SELECTION_NARRATION_RE = re.compile(
    r"\b(?:models?|providers?)\b[^.!?\n]{0,80}\bthis\s+turn\b(?!\s+of\b)"
    r"|\bthis\s+turn\b(?!\s+of\b)[^.!?\n]{0,80}\b(?:models?|providers?)\b",
    re.IGNORECASE,
)
_ANSWER_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def answer_is_only_runtime_selection_narration(answer_text: str) -> bool:
    """True when every sentence of a reply is runtime model/provider-selection narration.

    Structural: such a reply addresses no requested part. A reply that answers and also mentions
    a model is not covered here; unindexed prose otherwise stays coverage-unknown.
    """
    sentences = [part.strip() for part in _ANSWER_SENTENCE_SPLIT_RE.split(str(answer_text or "")) if part.strip()]
    return bool(sentences) and all(_RUNTIME_SELECTION_NARRATION_RE.search(part) for part in sentences)


def ordinary_indexed_answer_status(required_parts: int, answer_text: str, *, numbering_required: bool) -> str:
    present = ordinary_answer_part_indexes(answer_text)
    if not present and answer_is_only_runtime_selection_narration(answer_text):
        return "missing_requested_parts"
    if not present and not numbering_required:
        return "unindexed_semantic_coverage_unknown"
    if not all(index in present for index in range(1, int(required_parts) + 1)):
        return "missing_requested_parts"
    return "indexed_parts_present_semantics_unknown"


def ordinary_multi_part_answer_complete(user_text: str, answer_text: str) -> bool:
    """Whether structural evidence establishes an omission of a requested answer part.

    The legacy name is retained for callers. True means no established structural
    omission, not semantic correctness; the status API exposes the unknown verdict.
    """
    return ordinary_multi_part_answer_status(user_text, answer_text) != "missing_requested_parts"


def ordinary_answer_part_indexes(answer_text: str) -> frozenset[int]:
    """Sequential answer indexes rendered with either numeric or alphabetic labels."""

    present: set[int] = set()
    for match in _ANSWER_PART_MARKER_RE.finditer(str(answer_text or "")):
        marker = match.group("marker")
        present.add(int(marker) if marker.isdigit() else ord(marker.upper()) - ord("A") + 1)
    return frozenset(present)


# A question asked ABOUT THE SPEAKER'S OWN FACTS is a memory-recall request,
# not a detached generation task (measured, paid calibration 2026-09-27:
# "How many engineers do I lead when I just started my new role…? How many
# do I lead now?" was classed multi_part_qa, whose minimal route loads NO
# session context — the model answered from nothing). Such questions must
# take the ordinary chat path where memory recall runs.
#
# The decision is PER CONSTITUENT REQUEST, not over the whole prompt: a mixed
# turn ("Where did I leave my passport, and how do I renew it?") contains a
# personal-fact clause that still needs recall — a generic instructional
# clause elsewhere in the turn must not veto it (measured, independent review
# 2026-09-27: the whole-prompt manner check misrouted that turn to the
# no-context plain lane). The clause set is the module's own authoritative
# request splitter (ordinary_plain_requests), so preamble and quote handling
# match every other plain-task decision.
#
# Within a clause, two shapes ask for the speaker's own stored facts:
#   * an inverted state/past auxiliary over I/we: "How many did I lead",
#     "When did we move", "Have I paid", "Where do I store my seeds" — past
#     manner included: "How did I pay" asks what the speaker DID (remembered
#     history), not for instructions;
#   * an interrogative possessive frame: "What is my plot number",
#     "When is my appointment", "What did my dentist tell me".
# Present/modal manner ("how do/can/should I …") and advice-modals ask for
# instructions and stay plain; a bare "how" is excluded from the possessive
# frame precisely because "how … my X" is usually a how-to.
_SELF_FACT_AUX_RE = re.compile(
    r"\b(?:did|was|were|had|have|has|am|is|are|do|does)\s+(?:i|we)\b",
    re.IGNORECASE,
)
#: PRESENT/MODAL instructional manner. Past "did" is deliberately absent:
#: "How did I pay for it?" recalls the user's own past, it does not ask to
#: be taught (measured, review 2026-09-27).
_HOWTO_MANNER_RE = re.compile(
    r"\bhow\s+(?:do|does|can|could|should|would)\s+(?:i|we)\b",
    re.IGNORECASE,
)
# "how much/how many" joined the head class after the same misroute one more time
# (q90 routing, V corpus F15-05's mixed shape, 2026-09-29): "How much is my copay
# and how much is my deductible?" -- every clause is a possessive quantity recall,
# none carries the inverted-auxiliary shape, so the turn classed multi_part_qa and
# its minimal route loaded NO session context. A QUANTITY interrogative over
# my/our is as much a recall request as "what is my plot number"; bare "how"
# stays excluded (its "how ... my X" reading is a how-to).
_SELF_FACT_POSSESSIVE_RE = re.compile(
    r"\b(?:what|which|when|where|who|whose|why|how\s+much|how\s+many)\b[^.!?]{0,60}?\b(?:my|our)\b",
    re.IGNORECASE,
)
_ADVICE_MODAL_RE = re.compile(
    r"\b(?:should|can|could|would|might|must|may)\s+(?:i|we)\b",
    re.IGNORECASE,
)


def _clause_requests_recall(text: str) -> bool:
    """Whether any constituent request of *text* asks about the speaker's own
    facts. Quoted spans are invisible to the tests (a quoted example question
    is not the user's own request), mirroring the module's other matchers.
    Clause normalization strips terminal punctuation, so the question mark is
    checked once for the whole turn by the caller, not per clause."""
    clauses = ordinary_plain_requests(text)
    if not clauses:
        clauses = tuple(
            sentence for sentence in _split_unquoted_sentences(text) if sentence.strip()
        ) or (str(text or "").strip(),)
    for clause in clauses:
        visible = _outside_quoted_spans(clause)
        if _HOWTO_MANNER_RE.search(visible):
            continue
        if _SELF_FACT_AUX_RE.search(visible):
            return True
        if _SELF_FACT_POSSESSIVE_RE.search(visible) and not _ADVICE_MODAL_RE.search(
            visible
        ):
            return True
    return False


def _first_person_recall_question(text: str) -> bool:
    raw = str(text or "").strip()
    if "?" not in raw:
        return False
    return _clause_requests_recall(raw)


def first_person_recall_question(text: str) -> bool:
    """Whether *text* is a question about the speaker's own facts (the recall test above)."""
    return _first_person_recall_question(text)


#: A word that can only open a noun phrase: a determiner, possessive, quantifier or numeral.
#: After a joining token it proves the joiner coordinates two noun phrases inside one request
#: ("the day I X and the day I Y", "my charger and my cable"), not two requests.
_NOUN_PHRASE_OPENER_RE = re.compile(
    r"(?:the|a|an|my|our|your|his|her|their|its|this|that|these|those|some|each|every|both|"
    r"either|another|other|one|two|three|four|five|\d[\w.,]*)\b",
    re.IGNORECASE,
)
#: The joining tokens the planner gate admits on (`turn_planner._JOINING_TOKENS`).
_PLANNER_JOINER_RE = re.compile(r"\b(?:and|also|plus|then)\b|[,;]", re.IGNORECASE)
_INTERNAL_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?;:]\s+\S")


def _joiner_opens_noun_phrase(visible: str, match: re.Match[str]) -> bool:
    joiner = match.group(0).lower()
    if joiner in {"also", "then", ";"}:
        # "also"/"then" sequence requests; a semicolon separates them. Never a proven NP join.
        return False
    rest = visible[match.end():].lstrip(" ,")
    if not rest:
        return False
    if _NOUN_PHRASE_OPENER_RE.match(rest):
        return True
    # A capitalized proper name ("Lisbon and Porto"), but never the pronoun "I", which opens a
    # clause of its own.
    first = rest.split(None, 1)[0]
    return first[:1].isupper() and first.rstrip("?,.!") not in {"I", "I'm", "I've", "I'd", "I'll"}


def single_clause_recall_question(text: str) -> bool:
    """Whether *text* is one question about the speaker's own facts, making one request.

    Used to skip the clause-decomposition call: such a question is answered by one reader call
    from memory, and a planner that returns one clause (the common outcome) spends a model call to
    learn nothing. The test is grammatical and fails toward the planner:

    - exactly one question mark, at the end, and no internal sentence boundary;
    - no operational, path, live-recency or place-seeking request anywhere in it;
    - every joining token the planner gate admits on ("and", "plus", ",", ...) is followed by a
      word that can only open a noun phrase (a determiner, possessive, quantifier, numeral or a
      capitalized name), so the joiner coordinates noun phrases rather than requests; "also",
      "then" and ";" always count as a second request;
    - the question asks about the speaker's own facts (`_first_person_recall_question`).

    Quoted spans are invisible, as in every other matcher in this module.
    """

    raw = " ".join(str(text or "").split())
    if not raw:
        return False
    visible = " ".join(_outside_quoted_spans(raw).split())
    if visible.count("?") != 1 or not visible.endswith("?"):
        return False
    if _INTERNAL_SENTENCE_BOUNDARY_RE.search(visible):
        return False
    if multipart_has_non_plain_request(visible):
        return False
    for match in _PLANNER_JOINER_RE.finditer(visible):
        if not _joiner_opens_noun_phrase(visible, match):
            return False
    return _first_person_recall_question(raw)


_OWN_THING_RE = re.compile(
    r"\b(?:my|our)\s+(?!own\b)[a-z]|\bwhat\s+(?:i|we)(?:'m|\s+am|'re|\s+are|'ve\s+been|\s+have\s+been)\s+(?:building|working\s+on|making|writing|planning|doing)\b",
    re.IGNORECASE,
)
_SUPPLIED_MATERIAL_RE = re.compile(r"(?::|\n|[\"“'‘`])\s*(?:\S+\s+){7,}\S+")


def _asks_about_own_things_without_material(raw: str) -> bool:
    """Whether a text-task request operates on the speaker's own things and supplies nothing to operate on.

    "Summarize my project" or "Explain what I'm building" carries no text of its own: its object is the user's
    records, so it is a memory question, not a one-shot text task. Found on a spent benchmark question (2026-10-07):
    the one-shot route stripped every context message, and the answer described VOOL's own product instead of the
    user's project. A request that pastes the material ("Summarize my notes: ...") stays a text task.
    """
    return bool(_OWN_THING_RE.search(raw)) and not _SUPPLIED_MATERIAL_RE.search(raw)


def plain_task_kind(text: str) -> str:
    """Return the plain model task kind for one-shot text tasks.

    These are user-facing generation tasks, not operational workflows. Keeping this
    classifier small prevents normal copy/translation/explanation prompts from
    inheriting tool doctrine, action routing, or unrelated session context.
    """
    raw = " ".join(str(text or "").strip().split())
    if not raw:
        return ""
    if (_SUMMARIZE_RE.search(raw) or _SIMPLE_EXPLAIN_RE.search(raw)) and _asks_about_own_things_without_material(raw):
        # Describing the speaker's own thing is answered from their records. (Translating or rewriting
        # keeps its kind: those asks bring their text, and without it the model asks for it.)
        return ""
    if is_ordinary_multi_part_plain_task(text) and not _first_person_recall_question(raw):
        return "multi_part_qa"
    if _TRANSLATE_RE.search(raw):
        return "translation"
    if _REWRITE_RE.search(raw):
        return "rewrite"
    if _SUMMARIZE_RE.search(raw):
        return "summary"
    if _SIMPLE_CODE_RE.search(raw):
        return "simple_code"
    if _SIMPLE_EXPLAIN_RE.search(raw):
        return "explanation"
    return ""


def is_plain_task(text: str) -> bool:
    return bool(plain_task_kind(text))


__all__ = [
    "answer_is_only_runtime_selection_narration",
    "is_ordinary_multi_part_plain_task",
    "is_plain_task",
    "multipart_has_non_plain_request",
    "multipart_task_class_requires_tool_reachability",
    "ordinary_answer_part_indexes",
    "ordinary_indexed_answer_status",
    "ordinary_multi_part_answer_complete",
    "ordinary_multi_part_answer_status",
    "ordinary_plain_request_count",
    "ordinary_plain_requests",
    "plain_task_kind",
    "scalar_answer_covers_requested_shape",
    "user_requires_numbered_answers",
]
