"""Entity-ambiguity adjudication for a plain single knowledge question.

MEASURED (served on f2b3fa6f, three fresh sessions, FINDINGS F45-in-progress)
---------------------------------------------------------------------------
"What is the population of Springfield?" served, on the ordinary plain-chat
route, "approximately 250,000 people" TWICE and "approximately 12,000
residents" ONCE — two different invented numbers for the same question, which
is itself the proof that nothing between the question and the published bytes
enforces anything. Priority C of the original goal: an ambiguous entity must
not receive an invented precise answer; it must receive a clarification.

THE CONTRACT
------------
Whether an entity is multiply-referenced is KNOWLEDGE, not text shape — no
structural rule can decide it, and a word list would be a phrase-list patch
wearing a law's clothes. So the decision is a typed MODEL adjudication, the
same pattern the conductor's clause split already uses: one short JSON call,
judgment-framed, temperature 0, parsed strictly. The RUNTIME enforces the
verdict's consequence: `ambiguous=true` means the answering generation never
runs and a clarification naming the referents is served instead.

A failed adjudication remains unresolved. The caller asks back rather than
publishing an entity-specific answer without a verdict.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The judgment the probe is asked for. Deliberately about the QUESTION'S own
#: decidability, never about topics: the model decides whether choosing between
#: referents is required BEFORE the question can have one answer.
AMBIGUITY_SYSTEM_PROMPT = (
    "You decide whether one user question turns on an entity that has several\n"
    "well-known referents, so the question cannot have a single correct answer\n"
    "until the user says which one they mean.\n"
    "Reply with ONLY a JSON object:\n"
    '  {"ambiguous": true or false, "referents": ["up to four names"],\n'
    '   "clarification": "one sentence asking which one the user means"}\n'
    "Rules:\n"
    "- ambiguous=false unless answering REQUIRES choosing between referents.\n"
    "- A question with one obvious real-world answer is not ambiguous, even if\n"
    "  obscure (a specific city, a specific person, a specific work).\n"
    "- Context-dependent common nouns (a file someone mentioned, 'my boss') are\n"
    "  not entity ambiguity; assume the conversation supplies them.\n"
    "- If the recent conversation supplied with the question resolves which\n"
    "  referent is meant, the question is NOT ambiguous.\n"
    "- Never answer the question. Only judge it.\n"
    "- No prose outside the JSON object."
)

AMBIGUITY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "ambiguous": {"type": "boolean"},
        "referents": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "clarification": {"type": "string"},
    },
    "required": ["ambiguous", "referents", "clarification"],
    "additionalProperties": False,
}

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_MAX_REFERENTS = 4


@dataclass(frozen=True)
class AmbiguityVerdict:
    """The probe's typed decision. `clarification` is the ask-back sentence."""

    ambiguous: bool
    referents: tuple[str, ...] = ()
    clarification: str = ""


def parse_ambiguity_verdict(raw: object) -> AmbiguityVerdict | None:
    """Strict parse of the probe's reply. Anything unparsable is None (fail-open)."""

    text = _THINK_BLOCK_RE.sub(" ", str(raw or "").strip()).strip()
    if not text:
        return None
    # The LAST JSON object: a reasoning model's think block may quote example
    # verdicts before emitting the real one, and the first brace would then be
    # the example's.
    matches = list(_JSON_OBJECT_RE.finditer(text))
    match = matches[-1] if matches else None
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("ambiguous"), bool):
        return None
    referents = tuple(
        str(item).strip()
        for item in list(payload.get("referents") or [])[:_MAX_REFERENTS]
        if str(item).strip()
    )
    clarification = str(payload.get("clarification") or "").strip()
    if payload["ambiguous"]:
        # An ambiguous verdict with nothing to ask about cannot be served.
        if not referents and not clarification:
            return None
    return AmbiguityVerdict(
        ambiguous=payload["ambiguous"],
        referents=referents,
        clarification=clarification,
    )


def render_clarification(verdict: AmbiguityVerdict, question: str) -> str:
    """The reader-facing ask-back. Names the referents the probe found."""

    asked = " ".join(str(question or "").strip().split())
    lead = verdict.clarification or "That name can refer to more than one place or thing."
    names = ", ".join(verdict.referents)
    if names:
        return f"{lead} Did you mean: {names}? (asked: {asked!r})"
    return f"{lead} (asked: {asked!r})"


#: The three states the adjudication can end in. UNRESOLVED is a state of its own —
#: a probe that timed out, returned garbage, or raised is NOT an unambiguous verdict,
#: and treating it as one is the measured defect: the fail-open path published an
#: invented Springfield number the moment adjudication failed (a65768ab, run-a).
STATE_AMBIGUOUS = "ambiguous"
STATE_UNAMBIGUOUS = "unambiguous"
STATE_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class AmbiguityOutcome:
    """What the adjudication established — never confuses 'could not tell' with 'clear'."""

    state: str
    verdict: AmbiguityVerdict | None = None
    attempts: int = 0


def build_probe_user_message(
    question: str, prior_events: list[dict] | None = None
) -> str:
    """The probe's user message: the question, with bounded PRIOR conversation.

    Prior context can RESOLVE an entity ("I'm planning a trip to Illinois" before
    "the population of Springfield") — judging the question without it would ask the
    user to re-state what the conversation already established. The events are the
    per-exchange rows `core.memory.entries.recent_conversation_events` returns, whose
    schema is `user` + `assistant` on ONE row (the transcript reader's own reading);
    anything unexpected is skipped, never guessed into shape.
    """

    parts: list[str] = []
    turns: list[str] = []
    for event in list(prior_events or [])[-4:]:
        if not isinstance(event, dict):
            continue
        user_text = " ".join(str(event.get("user") or "").split()).strip()
        assistant_text = " ".join(str(event.get("assistant") or "").split()).strip()
        if user_text:
            turns.append(f"User: {user_text[:200]}")
        if assistant_text:
            turns.append(f"Assistant: {assistant_text[:200]}")
    if turns:
        parts.append("Recent conversation, oldest first (this may resolve which "
                     "referent the user means):")
        parts.extend(turns)
    parts.append("The question to judge:")
    parts.append(str(question or "").strip())
    return "\n".join(parts)


def render_unresolved_clarification(question: str, *, attempts: int) -> str:
    """The honest ask-back when adjudication itself did not complete.

    A failed check is not a green light: the runtime could not establish that the
    question has one answer, so it does not publish one as if it had. The reply says
    exactly that — the check did not complete — and asks the user to confirm, which
    is the one resolution the runtime can still reach on its own.
    """

    asked = " ".join(str(question or "").strip().split())
    if int(attempts) > 0:
        lead = (
            f"I couldn't complete my check for whether this question has a single "
            f"well-known answer (tried {int(attempts)}x)"
        )
    else:
        lead = (
            "I couldn't run my check for whether this question has a single "
            "well-known answer"
        )
    return (
        f"{lead}, so I'd rather confirm than guess. "
        f"If you meant one specific place, person or thing, tell me which — or "
        f"rephrase with a detail that pins it down. (asked: {asked!r})"
    )


def single_plain_know_question(
    text: str, *, has_attachments: bool = False, source_context: Mapping[str, Any] | None = None
) -> bool:
    """Whether this turn is ONE plain knowledge question — the probe's whole jurisdiction.

    Structural, and deliberately narrow: the demand mint must hold exactly one
    unit, Turn IR must classify the single clause KNOW, and the turn must carry
    no attachments (an attachment makes the question's context the file's).
    Multi-question turns keep their existing paths; so do live-data turns,
    which earlier lanes claim before this question is ever asked.
    """

    if has_attachments:
        return False
    message = str(text or "").strip()
    # A question that NAMES a file is the file lanes' material, bound or not: its context is a
    # file's contents, and the disk lane's own "could not find" answer is the truthful outcome,
    # not an adjudicated clarification. The token shape is answer_binder's filename recognizer,
    # so this lane and the binder cannot drift on what a filename is.
    from core.answer_binder import _FILENAME_RE

    if _FILENAME_RE.search(message):
        return False
    if not message or len(message) > 400:
        return False
    # Runtime facts have their own deterministic owner in the front door, which runs
    # after this preflight. Consult that same reader before asking an external model
    # to adjudicate a question the runtime can answer from its own state.
    from core.runtime_lane_truth import runtime_lane_question

    if runtime_lane_question(message):
        return False
    try:
        from core.agent_runtime.answer_coverage import demand_units

        units = demand_units(message)
    except Exception:
        return False
    if units is None or len(units) != 1:
        return False
    # A demand a REGISTERED capability lane claims is not a plain knowledge question: the
    # runtime owns a deterministic, tool-backed answer for it, and the only thing this probe
    # could add is a wrong turn. Measured on the owner's turn (2026-09-10, build 772256a7):
    # "what are the largest files on this machine?" reads KNOW to Turn IR, was adjudicated for
    # entity ambiguity (the probe raised twice, 82 s of a free reasoning model), and asked back
    # about "one specific place, person or thing" while `machine.find_largest` -- the lane the
    # canonical registry names for exactly this unit -- never ran. The registry is the authority
    # (`core.agent_runtime.demand_ownership`); a registry fault leaves the probe eligible, which
    # is the pre-existing behaviour and fails toward asking, never toward inventing.
    try:
        from core.agent_runtime.demand_ownership import registered_lane_for

        if registered_lane_for(message):
            return False
    except Exception:
        pass
    # CAPABILITY ROUTING BEFORE ADJUDICATION (FINDINGS F15, owner transcript 2026-09-10 23:43 on
    # c647b707). A turn that REFERS to work already on the table is not a plain knowledge
    # question, whatever its clause reads as: "why?" after a failed lookup is a follow-up the
    # attempt door owns, and "check it on the internet" / "why dont u jsut check it on inernet"
    # after an unresolved price ask is that ask again -- the live-data continuation's re-ask.
    # Both were adjudicated for entity ambiguity and asked back about "one specific place,
    # person or thing" while the runtime held the referent all along. The referent authorities
    # are asked first; the probe keeps only turns they disown.
    try:
        from core.attempt_followup import classify_followup_intent

        if classify_followup_intent(message):
            return False
    except Exception:
        pass
    if source_context is not None:
        try:
            from core.live_data_continuation import continuation_inherits_live_data

            if continuation_inherits_live_data(message, source_context=source_context):
                return False
        except Exception:
            pass
    try:
        from core.turn_ir import ClauseKind, parse_turn_ir

        clauses = parse_turn_ir(message).clauses
    except Exception:
        return False
    return len(clauses) == 1 and clauses[0].kind is ClauseKind.KNOW
