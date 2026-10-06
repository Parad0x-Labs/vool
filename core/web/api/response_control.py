from __future__ import annotations

import re
from typing import Any

_EXACT_RESPONSE_RE = re.compile(
    r"^\s*(?:\[[^\]\r\n]{1,96}\]\s*)?"
    r"(?:reply|respond|answer|return|say|output|print)\s+"
    r"(?:with\s+)?exactly"
    r"(?:\s+(?:this|the(?:\s+following)?)\s+"
    r"(?:marker|text|string|phrase|token|word))?"
    r"(?:\s+and\s+(?:nothing\s+else|no\s+other\s+text|no\s+extra\s+text|no\s+extra\s+characters))?"
    r"\s*:?\s*(?P<target>(?![:\s]).+?)\s*"
    r"(?:\s+(?:and\s+)?(?:nothing\s+else|no\s+other\s+text|no\s+extra\s+text|no\s+extra\s+characters))?"
    r"[.!?]*\s*$",
    re.IGNORECASE | re.DOTALL,
)
# The pinned pattern above stays compiled as the differential spec. The served boundary used to run
# it directly on the raw user message: the lazy target retried its trailing \s* / closing-phrase /
# [.!?]*\s*$ surface from every candidate end, so a 32k "reply with exactly" message with a
# punctuation desert cost 4.7 s (measured on main cfae90f) and a whitespace desert minutes —
# CodeQL 128. `_match_exact_response` below computes the SAME first match linearly: the prefix's
# optional groups are tried in engine order over a bounded choice tree, and the earliest valid
# target end follows from the message's trailing whitespace/punctuation/phrase structure, which is
# analysed once.

_WRAPPED_TARGET_RE = re.compile(r"^[`\"'](?P<value>.*?)[`\"']$", re.DOTALL)

_EXACT_VERBS = ("reply", "respond", "answer", "return", "say", "output", "print")
_EXACT_NOUN_WORDS = ("marker", "text", "string", "phrase", "token", "word")
_EXACT_TAIL_PHRASES = (
    ("nothing", "else"),
    ("no", "other", "text"),
    ("no", "extra", "text"),
    ("no", "extra", "characters"),
)

_CI_LITERAL_CACHE: dict[str, re.Pattern[str]] = {}


def _ci_at(text: str, pos: int, literal: str) -> bool:
    """Case-insensitive literal test at ``pos`` with the engine's own IGNORECASE folding."""
    rx = _CI_LITERAL_CACHE.get(literal)
    if rx is None:
        rx = re.compile(re.escape(literal), re.IGNORECASE)
        _CI_LITERAL_CACHE[literal] = rx
    return rx.match(text, pos) is not None


def _tail_e_ranges(text: str, n: int) -> tuple[int, list[tuple[int, int]]]:
    r"""Earliest-end analysis of the trailing surface ``\s* (?:\s+ phrase)? [.!?]* \s*$``.

    Returns ``(a_lo, b_ranges)``: a candidate target end ``e`` is valid either anywhere in
    ``[a_lo, n]`` (whitespace run, then the final punctuation run to end-of-string — an empty
    tail at ``e == n`` included) or in one of the half-open ``[lo, hi)`` ranges where a closing
    phrase preceded by at least one whitespace character sits flush against the final punctuation
    run. The message is stripped, so nothing but punctuation or a closing phrase can follow the
    target.
    """
    r = n
    while r > 0 and text[r - 1] in ".!?":
        r -= 1
    a_lo = r
    while a_lo > 0 and text[a_lo - 1].isspace():
        a_lo -= 1
    ranges: list[tuple[int, int]] = []
    for phrase in _EXACT_TAIL_PHRASES:
        pos = r
        parsed = True
        for index in range(len(phrase) - 1, -1, -1):
            word = phrase[index]
            start = pos - len(word)
            if start < 0 or not _ci_at(text, start, word):
                parsed = False
                break
            pos = start
            if index > 0:
                if pos == 0 or not text[pos - 1].isspace():
                    parsed = False
                    break
                while pos > 0 and text[pos - 1].isspace():
                    pos -= 1
        if not parsed:
            continue
        # Without the optional "and": at least one whitespace must sit between the target's end
        # and the phrase (the group's own `\s+`).
        if pos > 0 and text[pos - 1].isspace():
            ws_start = pos
            while ws_start > 0 and text[ws_start - 1].isspace():
                ws_start -= 1
            if ws_start < pos:
                ranges.append((ws_start, pos))
            # With the optional "and": `\s+and\s+phrase` -- the "and" ends just before the
            # whitespace run that precedes the phrase (any length on either side, one minimum),
            # and the target's end leaves at least one whitespace before the "and".
            if ws_start >= 4 and _ci_at(text, ws_start - 3, "and"):
                and_start = ws_start - 3
                if and_start > 0 and text[and_start - 1].isspace():
                    ws_before_and = and_start
                    while ws_before_and > 0 and text[ws_before_and - 1].isspace():
                        ws_before_and -= 1
                    if ws_before_and < and_start:
                        ranges.append((ws_before_and, and_start))
    return a_lo, ranges


def _match_exact_response(stripped: str) -> str | None:
    """The pinned pattern's ``target`` group at its first match, or None — one linear pass."""
    text = stripped
    n = len(text)
    if n == 0:
        return None
    a_lo, b_ranges = _tail_e_ranges(text, n)

    def target_from(pos: int) -> str | None:
        # `\s*:?\s*` then a target that cannot open with ':' or whitespace.
        while pos < n and text[pos].isspace():
            pos += 1
        if pos < n and text[pos] == ":":
            pos += 1
            while pos < n and text[pos].isspace():
                pos += 1
        if pos >= n or text[pos] == ":" or text[pos].isspace():
            return None
        candidates: list[int] = []
        if max(pos + 1, a_lo) <= n:
            candidates.append(max(pos + 1, a_lo))
        for lo, hi in b_ranges:
            e = max(pos + 1, lo)
            if e < hi:
                candidates.append(e)
        if not candidates:
            return None
        return text[pos:min(candidates)]

    def noun_from(pos: int) -> list[int]:
        # `\s+(?:this|the(?:\s+following)?)\s+(noun-word)` tried in engine order.
        if pos >= n or not text[pos].isspace():
            return []
        while pos < n and text[pos].isspace():
            pos += 1
        out: list[int] = []
        if _ci_at(text, pos, "this"):
            for end in _ws_word(pos + 4):
                out.append(end)
        if _ci_at(text, pos, "the"):
            base = pos + 3
            following: list[int] = []
            q = base
            if q < n and text[q].isspace():
                while q < n and text[q].isspace():
                    q += 1
                if _ci_at(text, q, "following"):
                    following.append(q + 9)
            for head_end in ([*following, base] if following else [base]):
                for end in _ws_word(head_end):
                    out.append(end)
        return out

    def _ws_word(pos: int) -> list[int]:
        # `\s+` then one noun word.
        if pos >= n or not text[pos].isspace():
            return []
        while pos < n and text[pos].isspace():
            pos += 1
        return [pos + len(word) for word in _EXACT_NOUN_WORDS if _ci_at(text, pos, word)]

    def phrase_from(pos: int) -> list[int]:
        # `\s+and\s+(phrase)` — every phrase that parses, in alternation order.
        if pos >= n or not text[pos].isspace():
            return []
        while pos < n and text[pos].isspace():
            pos += 1
        if not _ci_at(text, pos, "and"):
            return []
        pos += 3
        if pos >= n or not text[pos].isspace():
            return []
        while pos < n and text[pos].isspace():
            pos += 1
        ends: list[int] = []
        for phrase in _EXACT_TAIL_PHRASES:
            q = pos
            ok = True
            for word in phrase:
                if not _ci_at(text, q, word):
                    ok = False
                    break
                q += len(word)
                if word is not phrase[-1]:
                    if q >= n or not text[q].isspace():
                        ok = False
                        break
                    while q < n and text[q].isspace():
                        q += 1
            if ok:
                ends.append(q)
        return ends

    def from_exactly(pos: int) -> str | None:
        # The literal cue, then the optional noun group, the optional and-group and the
        # colon+target — engine order: each optional participates before it is skipped.
        if not _ci_at(text, pos, "exactly"):
            return None
        pos += len("exactly")
        for noun_end in [*noun_from(pos), None]:
            noun_base = noun_end if noun_end is not None else pos
            for and_end in [*phrase_from(noun_base), None]:
                base = and_end if and_end is not None else noun_base
                found = target_from(base)
                if found is not None:
                    return found
        return None

    def from_verb(pos: int) -> str | None:
        # `(?:with\s+)?exactly …` — with first, then without.
        for with_end in _with_ends(pos):
            found = from_exactly(with_end)
            if found is not None:
                return found
        return None

    def _with_ends(pos: int) -> list[int]:
        out: list[int] = []
        if _ci_at(text, pos, "with"):
            q = pos + 4
            if q < n and text[q].isspace():
                while q < n and text[q].isspace():
                    q += 1
                out.append(q)
        out.append(pos)
        return out

    tag_ends: list[int] = []
    if text[0] == "[":
        j = 1
        while j < n and text[j] not in "]\r\n" and j <= 96:
            j += 1
        if j < n and text[j] == "]" and j > 1:
            k = j + 1
            while k < n and text[k].isspace():
                k += 1
            tag_ends.append(k)
    tag_ends.append(0)
    for start in tag_ends:
        for verb in _EXACT_VERBS:
            if _ci_at(text, start, verb):
                q = start + len(verb)
                if q < n and text[q].isspace():
                    while q < n and text[q].isspace():
                        q += 1
                    found = from_verb(q)
                    if found is not None:
                        return found
    return None


def exact_response_target(user_text: str) -> str:
    target_match = _match_exact_response(str(user_text or "").strip())
    if target_match is None:
        return ""
    # This legacy literal binder runs after the typed raw-output validator. It must never reinterpret
    # a shape instruction ("reply with exactly two lines ...") as the literal bytes to return;
    # doing so overwrites the already-validated two-line answer at the final API boundary. Literal
    # contracts may still use this compatibility path, while line/word/sentence/list shapes stay
    # owned by ``raw_output_contract``.
    from core.raw_output_contract import parse_raw_output_contract

    raw_contract = parse_raw_output_contract(user_text)
    # The typed parser is the ONE interpretation authority for exact-output requests. When it
    # binds a literal, these bytes are the payload and are served verbatim -- this legacy
    # rewriter never re-extracts its own target beside it. Measured at 84bf8b6a: for
    # `Respond with exactly this and nothing else: VERBATIM-PROOF-2291` the typed contract
    # already held the right bytes while `_EXACT_RESPONSE_RE` re-derived the target
    # `this and nothing else: VERBATIM-PROOF-2291` (cue residue) and OVERWROTE the correct
    # fast-path answer at this boundary. Two extractors that disagree can only ever ship one
    # of them; the typed one is the one the fast path, the seal and finalization all read.
    if raw_contract is not None and raw_contract.exact_text is not None:
        return raw_contract.exact_text
    literal_colon_form = bool(re.search(r"\bexactly\s*:", str(user_text or ""), re.IGNORECASE))
    if raw_contract is not None and raw_contract.exact_text is None:
        # The typed parser recognized a strict whole-reply output instruction and REFUSED to
        # bind literal bytes (empty payload, ambiguous delimiter, multiline colon payload).
        # A non-None contract is exactly that recognition -- the parser returns None for
        # ordinary prose. This rewriter re-extracting its own target after that refusal is the
        # measured defect: `Respond with exactly this and nothing else:` (empty payload) bound
        # `this and nothing else:` here and served the cue residue. The refusal is the answer.
        return ""
    target = _clean_exact_target(target_match)
    if not target or "\n" in target or len(target) > 240:
        return ""
    # P0 MIXED-DEMAND — a literal binding may claim the turn's OUTPUT only when the
    # user actually supplied a literal.
    #
    # Measured at base 840a2392: "Return exactly the second line of notes.txt.
    # Calculate 37 x 19. Decide whether that line describes walking." matched the
    # bare form, and this binder OVERWROTE the whole turn's composed answer with an
    # echo of the request — the file read, the arithmetic and the judgement were all
    # discarded at the final boundary after the runtime had executed them. This is
    # the same whole-turn claim the lane catalog governs, made by a rewriter that is
    # not a lane, so it answered to no law at all.
    #
    # The discriminator is what "exactly" is modifying. An EXPLICIT literal — quoted,
    # delimited, or introduced by a colon — is bytes to echo and keeps the legacy
    # path unchanged. A BARE target is a literal only while it is not itself work:
    # "exactly PONG" names a token, "exactly the second line of notes.txt" names a
    # file read whose precision the adverb is qualifying. That question is not
    # answered here — it is asked of the canonical demand registry, so a family the
    # registry learns to execute later is a family this binder stops swallowing,
    # with no edit to this file.
    if not literal_colon_form and not _explicitly_delimited_target(target_match):
        if _target_is_executable_demand(target):
            return ""
    return target


def _explicitly_delimited_target(raw_target: str) -> bool:
    """Whether the user wrapped the target in quotes/backticks — an explicit literal."""
    target = re.sub(r"\s+", " ", str(raw_target or "").strip())
    return bool(_WRAPPED_TARGET_RE.match(target))


def _target_is_executable_demand(target: str) -> bool:
    """Whether the 'literal' is really a request the runtime knows how to execute.

    Asked of the ONE canonical registry (`demand_ownership.demand_coverage`), never
    of a vocabulary kept here: a target holding SEVERAL demand units is several
    requests and no single literal, and a target a registered lane claims is that
    lane's work. Fail-soft toward the pre-existing behaviour — an unavailable or
    raising registry leaves the legacy binding exactly as it was.
    """
    text = str(target or "").strip()
    if not text:
        return False
    try:
        from core.agent_runtime.demand_ownership import demand_coverage

        coverage = demand_coverage(text)
    except Exception:
        return False
    if coverage.unit_count > 1:
        return True
    return any(bool(lanes) for lanes in coverage.per_unit_lanes)


def apply_exact_response_control(result: dict[str, Any], user_text: str) -> dict[str, Any]:
    target = exact_response_target(user_text)
    if not target:
        return result
    response = str(dict(result or {}).get("response") or "").strip()
    # The marker records a validated literal contract even when its bytes are
    # already correct. Downstream semantic guards must honor that contract;
    # whether this binder changed the response is recorded separately.
    controlled = dict(result or {})
    controlled["response"] = target
    controlled["response_control"] = {
        "mode": "exact_target",
        "target": target,
        "changed": response != target,
        "original_response_excerpt": response[:240],
    }
    return controlled


def _clean_exact_target(raw_target: str) -> str:
    target = str(raw_target or "").strip()
    target = re.sub(r"\s+", " ", target)
    wrapped = _WRAPPED_TARGET_RE.match(target)
    if wrapped:
        target = wrapped.group("value").strip()
    return target.strip()
