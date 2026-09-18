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
_WRAPPED_TARGET_RE = re.compile(r"^[`\"'](?P<value>.*?)[`\"']$", re.DOTALL)


def exact_response_target(user_text: str) -> str:
    match = _EXACT_RESPONSE_RE.match(str(user_text or "").strip())
    if not match:
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
    target = _clean_exact_target(match.group("target"))
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
    if not literal_colon_form and not _explicitly_delimited_target(match.group("target")):
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
    if response == target:
        return result
    controlled = dict(result or {})
    controlled["response"] = target
    controlled["response_control"] = {
        "mode": "exact_target",
        "target": target,
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
