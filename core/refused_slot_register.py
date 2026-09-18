"""The session's durable register of slots the runtime recorded as UNANSWERED, and the one
contract that stands on it: **a slot the record says was not answered may never acquire a value
from generation.**

AUD-20260829-003, acceptance criteria C9 / C10 / C12.

WHAT WAS BROKEN, AND WHY IT WAS BROKEN AT A BOUNDARY RATHER THAN IN A PROMPT
---------------------------------------------------------------------------
The RSS demand ledger made per-slot non-answer durable: `core.finalization`'s closure sweep
drives every requested slot the turn did not answer to `unanswered` and renders it. Nothing that
runs on the NEXT turn could read that.

Two layers were blind to it, both because they judge at the wrong grain:

1. **Follow-up resolution** (`core.attempt_followup.resolve_followup_attempt`) binds to
   `latest_unresolved_or_partial_attempt`, which is a WHOLE-ATTEMPT lifecycle question. A turn
   that served the FX leg and dropped three slots is `SUCCEEDED`. Measured in the daemon's own
   `runtime_followup_resolutions` table, every `why did that fail?` over such a turn recorded
   `resolution_reason = 'no unresolved or partially failed attempt in session'` with
   `fallback_used = 1` — it classified correctly, found nothing, and fell through to generation.

2. **The live-value guard** (`core.model_output_guard.unobserved_live_value_claims`) convicts an
   unobserved value only when it sits beside a currentness anchor and carries no hedge. Those two
   exemptions are correctly calibrated for an OPEN question: "the Baltic is typically 15-18 C in
   August" is climate, and general knowledge may say it. They are wrong the moment the runtime has
   a RECORDED REFUSAL for that slot, because that is exactly the state in which a hedged range is
   the fabrication. Every one of the 16 unsourced values captured on 2026-08-30 carried a hedge
   (`typically`, `usually`, `around`, `varies`, `generally`) or no currentness anchor, so the guard
   was structurally unable to see any of them.

The consequence measured across two independent chats minutes apart: three mutually incompatible
invented ranges (9-12, 15-18, 18-20 C) for ONE slot the same conversation had disclosed as
unanswerable, plus a gold figure ~3x off the runtime's own tool output.

This module supplies what both layers lacked: the record, addressable from a turn.

THE READ PATH USES PUBLIC ACCESSORS ONLY
----------------------------------------
    attempt_id  --(core.invocation.ledger.get_execution: execution_id aliases root attempt)-->
    request_id  --(core.conductor.obligation_ledger.set_for_request)-->
    (set_id, version)  --(obligation_ledger.demand_obligations)-->  per-slot state + text

No table is read behind the ledger's back and no ledger logic is reimplemented here; slot identity
comes from `core.agent_runtime.answer_coverage.unit_anchors`, the runtime's own model of what a
truthful answer to a unit would have to mention.

BINDING, AND WHY IT IS NOT A PROSE FILTER
-----------------------------------------
`values_claimed_over_refused_slots` answers one question: does this reply assert a live value FOR
a slot the record says was refused? A conviction needs all three of

  * the turn observed nothing (the caller's conjunct, unchanged),
  * a durable prior record of refusal for that slot,
  * the value's own window naming that slot.

Remove any one and nothing happens. It is a provenance contract, not a number filter: the outcome
is that the answer is replaced by what the runtime actually has on record, never that digits are
edited out of prose.

Slot naming tolerates the OPERATOR'S OWN TYPOS, because the record quotes the request verbatim and
the refused slots in the live capture are spelled `tempperature` and `baltc` while the reply that
invents values for them spells them correctly. Exact matching would have convicted nothing on the
exact capture this exists to stop. Tolerance is bounded (edit distance <= 2, tokens >= 5) and
corroborated: a slot offering two or more anchors must have two named in the window, which is the
same lesson `unit_answer_evidence` learned from RED-1 NEW-4 — one shared token does not identify a
slot. `water` and `weather` are two edits apart, and that single collision is exactly what the
corroboration requirement exists to absorb.

THE COST, NAMED
---------------
A slot is on the register whenever the sweep recorded it `unanswered`, whatever it asked for. So a
session in which the runtime failed to answer something is a session where an unobserved live value
NAMING THAT SLOT will be declined even when hedged as general knowledge. That is deliberate: the
hedge is the shape the fabrication took. It is bounded by the anchor binding (a value naming
nothing on the register is untouched) and by the lookback window (`_DEFAULT_LOOKBACK` attempts), so
ordinary conversation about anything else is unaffected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: How far back in the session the register looks. A refusal that is many turns old is no longer
#: the state the conversation is standing on; C12 is about the PRIOR failed/no-answer state, and
#: an unbounded register would arm this contract for the life of a session.
_DEFAULT_LOOKBACK = 6

#: How far either side of a value its slot may be named and still be one claim. Deliberately the
#: same width as `core.model_output_guard._LIVE_CLAIM_WINDOW`: a value and the thing it is about
#: sit within a list row plus its heading, not paragraphs apart.
_SLOT_NAMING_WINDOW = 160

#: Anchors shorter than this are matched exactly, never fuzzily: `rome`/`rate` and `gold`/`good`
#: are one and two edits apart and must never bind.
_FUZZY_MIN_LEN = 5
_FUZZY_MAX_EDITS = 2
_PREFIX_MIN_LEN = 4

_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]+", re.UNICODE)

#: The one closed-vocabulary reason the ledger records for a slot no lane claimed. Imported
#: lazily from the ledger so the two cannot drift; this is the fallback if that import fails.
_FALLBACK_REASON = "no answering lane claimed this part of the request"


@dataclass(frozen=True)
class RefusedSlot:
    """One slot the finalization sweep recorded as unanswered, with where it came from."""

    attempt_id: str
    unit_id: str
    text: str
    reason: str
    anchors: tuple[str, ...]
    #: The operation the record shows was dispatched FOR this slot, and how it ended. Empty
    #: when nothing was dispatched -- which is a different fact, and says so.
    operation: str = ""
    failure_reason: str = ""

    def as_row(self) -> str:
        """The disclosure row shape the runtime already uses for an unanswered slot.

        C9 asks the explain turn to name the failed slot AND the tool. A slot nothing was
        dispatched for has no tool to name, and "no answering lane claimed this part of the
        request" is the whole truth about it. A slot a lane DID run and fail has both, and the
        reader is entitled to them: the record holds the operation and its failure reason, and
        reporting a dispatched failure as "no answering lane" is simply wrong.
        """
        detail = self.reason
        if self.operation:
            detail = f"{self.operation} was dispatched and did not return an answer"
            if self.failure_reason:
                detail = f"{detail} ({self.failure_reason})"
        return f"* {self.text.strip()} — {detail}"


# ----------------------------------------------------------------------------- durable read


def _obligation_set_for_attempt(attempt: dict[str, Any] | None) -> tuple[str, str] | None:
    """(set_id, version) for one attempt's turn, or None.

    `open_execution` aliases `execution_id` to the retry-chain root attempt id and stores the A0
    `request_id` beside it; `open_obligation_set` freezes that same `request_id` into the set
    snapshot. Those two facts are the whole join, and both are public.
    """
    if not isinstance(attempt, dict):
        return None
    attempt_id = str(attempt.get("attempt_id") or "").strip()
    root_id = str(attempt.get("root_attempt_id") or "").strip() or attempt_id
    if not root_id:
        return None
    try:
        from core.conductor.obligation_ledger import set_for_request
        from core.invocation.ledger import get_execution
    except Exception:
        return None
    for candidate in (root_id, attempt_id):
        if not candidate:
            continue
        try:
            execution = get_execution(candidate)
        except Exception:
            execution = None
        request_id = str((execution or {}).get("request_id") or "").strip()
        if not request_id:
            continue
        try:
            bound = set_for_request(request_id)
        except Exception:
            bound = None
        if bound:
            return bound
    return None


def refused_slots_for_attempt(attempt: dict[str, Any] | None) -> tuple[RefusedSlot, ...]:
    """Every slot this attempt's turn recorded as unanswered, in minted order.

    `indeterminate` is deliberately NOT included. The ledger uses it for a slot the runtime can
    prove neither answered nor unanswered; it claims nothing, renders nothing, and must not arm a
    contract that speaks in the runtime's name.
    """
    bound = _obligation_set_for_attempt(attempt)
    if bound is None:
        return ()
    try:
        from core.agent_runtime.answer_coverage import unit_anchors
        from core.conductor.obligation_ledger import (
            RSS_REASON_NO_LANE,
            demand_obligations,
        )
    except Exception:
        return ()
    attempt_id = str((attempt or {}).get("attempt_id") or "")
    # What the record says ran for each unit. A FAILED dispatch names the operation and its
    # reason; anything else leaves the slot with the honest no-lane line.
    dispatched: dict[str, tuple[str, str]] = {}
    try:
        from core.conductor.obligation_ledger import slice_dispatches

        for row in slice_dispatches(*bound):
            if str(row.get("state") or "") not in ("FAILED", "UNSUPPORTED_ENTITY"):
                continue
            unit = str(row.get("unit_id") or "")
            if unit and unit not in dispatched:
                dispatched[unit] = (
                    str(row.get("operation") or ""),
                    str(row.get("failure_reason") or ""),
                )
    except Exception:
        dispatched = {}
    slots: list[RefusedSlot] = []
    for item in demand_obligations(*bound):
        if str(item.get("state") or "") != "unanswered":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        anchors = tuple(unit_anchors(text))
        if not anchors:
            # A unit naming nothing to look up cannot be fabricated for, and must not arm
            # anything: `thanks` and bare politeness residue resolve to no anchors at all.
            continue
        unit_id = str(item.get("unit_id") or "")
        operation, failure_reason = dispatched.get(unit_id, ("", ""))
        slots.append(
            RefusedSlot(
                attempt_id=attempt_id,
                unit_id=unit_id,
                text=text,
                reason=str(RSS_REASON_NO_LANE or _FALLBACK_REASON),
                anchors=anchors,
                operation=operation,
                failure_reason=failure_reason,
            )
        )
    return tuple(slots)


def refused_slots_for_session(
    session_id: str,
    *,
    exclude_attempt_id: str = "",
    lookback: int = _DEFAULT_LOOKBACK,
) -> tuple[RefusedSlot, ...]:
    """The session's register: refused slots from its recent attempts, newest attempt first.

    The CURRENT turn's own attempt is excluded. C12 is a rule about PRIOR failed/no-answer state,
    and a turn that included itself would arm this contract against its own in-flight answer.
    """
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return ()
    try:
        from core.runtime_continuity import recent_runtime_attempts
    except Exception:
        return ()
    try:
        attempts = recent_runtime_attempts(
            clean_session, exclude_attempt_id=str(exclude_attempt_id or ""), limit=int(lookback)
        )
    except Exception:
        return ()
    seen: set[tuple[str, str]] = set()
    register: list[RefusedSlot] = []
    for attempt in attempts:
        for slot in refused_slots_for_attempt(attempt):
            key = (slot.text.strip().casefold(), slot.unit_id)
            if key in seen:
                continue
            seen.add(key)
            register.append(slot)
    return tuple(register)


def _is_runtime_control_utterance(text: str) -> bool:
    """Whether this text is a follow-up ABOUT the runtime rather than a request for information.

    A follow-up turn mints its own demand set, so `why did that fail?` and `retry` are themselves
    recorded as unanswered slots. Left unfiltered, the second `why did that fail?` in a chat would
    resolve to the FIRST one as its antecedent -- the same self-reference CE-5 closed for the
    lifecycle tier -- and the explanation would read back the user's own control phrase as the
    thing the runtime could not answer. Decided by the runtime's existing follow-up classifier, so
    there is no second vocabulary here to drift from it.
    """
    try:
        from core.attempt_followup import classify_followup_intent

        return classify_followup_intent(text) is not None
    except Exception:
        return False


def latest_attempt_with_refused_slots(
    session_id: str,
    *,
    exclude_attempt_id: str = "",
    lookback: int = _DEFAULT_LOOKBACK,
) -> dict[str, Any] | None:
    """The newest attempt in the session that recorded an INFORMATION slot as unanswered.

    Resolution tier for `why did that fail?` / `retry` when the whole-attempt lifecycle tier finds
    nothing, which is the common case for the defect this audit is about: the turn is `SUCCEEDED`
    and its ledger carries three slots nobody answered. An attempt whose own request was a runtime
    control phrase is skipped -- it is not an antecedent, it is another reference to one.
    """
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return None
    try:
        from core.runtime_continuity import recent_runtime_attempts

        attempts = recent_runtime_attempts(
            clean_session, exclude_attempt_id=str(exclude_attempt_id or ""), limit=int(lookback)
        )
    except Exception:
        return None
    for attempt in attempts:
        if _is_runtime_control_utterance(str(attempt.get("original_request_snapshot") or "")):
            continue
        if any(
            not _is_runtime_control_utterance(slot.text)
            for slot in refused_slots_for_attempt(attempt)
        ):
            return dict(attempt)
    return None


def _turn_identity(source_context: Any) -> dict[str, Any]:
    context = source_context if isinstance(source_context, dict) else {}
    identity = context.get("_execution_identity")
    if isinstance(identity, dict) and identity.get("attempt_id"):
        return dict(identity)
    try:
        from core.semantic.semantic_admissions import current_execution_identity

        live = current_execution_identity()
    except Exception:
        live = None
    return dict(live) if isinstance(live, dict) else {}


def register_in_scope(source_context: Any) -> tuple[RefusedSlot, ...]:
    """The register for whatever session THIS turn belongs to, resolved from turn identity.

    `_r3_open_turn_execution` publishes the turn's attempt id into the turn context and into a
    ContextVar for lanes that never see the dict; both are consulted, in that order. A turn with
    no execution identity (a direct library call, a test constructing its own context) has no
    session to read, and gets an empty register rather than a guessed one.
    """
    identity = _turn_identity(source_context)
    attempt_id = str(identity.get("attempt_id") or "").strip()
    if not attempt_id:
        return ()
    try:
        from core.runtime_continuity import get_runtime_attempt

        attempt = get_runtime_attempt(attempt_id)
    except Exception:
        return ()
    session_id = str((attempt or {}).get("session_id") or "").strip()
    if not session_id:
        return ()
    return refused_slots_for_session(session_id, exclude_attempt_id=attempt_id)


# ------------------------------------------------------------------------- slot <-> value binding


#: A FIXED, AUDITABLE SCRIPT TABLE -- never a lexicon and never a translation.
#:
#: `anchor_named_in` compares codepoints, and `_TOKEN_RE` tokenises Cyrillic perfectly well, so
#: the failure is in the COMPARISON: a reply written in Russian shares no codepoint sequence
#: with a slot recorded in English, and the measured Cyrillic breach scored CLEAN by every
#: detector in the audit. Folding script -- and only script -- lets the two meet.
#:
#: Ruling 2 of docs/REFUSED_SLOT_BINDING_RULING_R1_2026-09-05.md forbids going further: no
#: bilingual lexicon, no semantics, no expected-answer matching. A script this table does not
#: cover stays UNADJUDICATED, which is the honest state; scoring it CLEAN is what made the
#: breach invisible.
_SCRIPT_FOLD = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def fold_script(token: str) -> str:
    """`token` lowercased and transliterated into Latin where the fixed table covers it."""
    lowered = str(token or "").lower()
    if lowered.isascii():
        return lowered
    return "".join(_SCRIPT_FOLD.get(ch, ch) for ch in lowered)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(fold_script(m.group(0)) for m in _TOKEN_RE.finditer(str(text or "")))


def _transliterated_tokens(text: str) -> frozenset[str]:
    """The folded forms of tokens that were NOT already Latin.

    Script folding alone does not make an inflected language comparable: "Балтийском" folds to
    `baltiiskom`, which is neither equal to, a prefix of, nor within two edits of `baltic`. The
    morphology sits past the transliteration.

    Marking which tokens were transliterated is what lets the stem reading below apply to
    exactly those and nothing else -- so an all-Latin reply is compared with byte-identical
    strictness to before, and the ASCII near-miss guards cannot be loosened by it.
    """
    return frozenset(
        fold_script(m.group(0))
        for m in _TOKEN_RE.finditer(str(text or ""))
        if not m.group(0).isascii()
    )


def _edit_within(a: str, b: str, limit: int) -> bool:
    """Bounded Levenshtein: True when `a` and `b` differ by at most `limit` edits."""
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def anchor_named_in(anchor: str, window_tokens: frozenset[str]) -> bool:
    """Whether a window names this anchor, tolerating the request's own spelling.

    Three readings, tightening as the token gets shorter:
      * exact;
      * one is a prefix of the other, both >= 4 characters (`temperature`/`temperatures`);
      * edit distance <= 2, both >= 5 characters (`tempperature`/`temperature`, `baltc`/`baltic`).

    Anchors under 5 characters get NO fuzzy reading, which is what keeps `rome` off `rate` and
    `gold` off `good` — the two collisions RED-E's own negative controls name.
    """
    return anchor_match_kind(anchor, window_tokens) != ""


def anchor_match_kind(
    anchor: str,
    window_tokens: frozenset[str],
    transliterated: frozenset[str] = frozenset(),
) -> str:
    """HOW the window named this anchor: "exact", "prefix", "stem", "fuzzy", or "".

    The single-anchor carve-out admits only readings at least as strict as a prefix, so the
    reading has to be reported rather than collapsed to a boolean.

    "stem" applies ONLY to a token that was transliterated out of another script, where the
    two forms share a leading run of at least `_FUZZY_MIN_LEN` characters -- `baltic` against
    `baltiiskom`, `temperature` against `temperatura`. An all-Latin window never reaches it, so
    the ASCII collisions the negative controls name (`rome`/`rate`, `gold`/`good`) are
    unaffected: they share one and two leading characters respectively, far under the floor.
    """
    clean = fold_script(str(anchor or "").strip())
    if not clean:
        return ""
    if clean in window_tokens:
        return "exact"
    for token in window_tokens:
        if len(clean) >= _PREFIX_MIN_LEN and len(token) >= _PREFIX_MIN_LEN:
            if token.startswith(clean) or clean.startswith(token):
                return "prefix"
    for token in transliterated:
        if len(clean) < _FUZZY_MIN_LEN or len(token) < _FUZZY_MIN_LEN:
            continue
        shared = 0
        for a, b in zip(clean, token):
            if a != b:
                break
            shared += 1
        if shared >= _FUZZY_MIN_LEN:
            return "stem"
    for token in window_tokens:
        if len(clean) >= _FUZZY_MIN_LEN and len(token) >= _FUZZY_MIN_LEN:
            if _edit_within(clean, token, _FUZZY_MAX_EDITS):
                return "fuzzy"
    return ""


def window_names_slot(window: str, slot: RefusedSlot) -> tuple[str, ...]:
    """The slot anchors this window names, or () when the window does not identify the slot.

    A slot offering two or more anchors must have at least TWO named. `unit_answer_evidence`
    learned the same thing from RED-1 NEW-4: one shared token is a collision, not an
    identification. A single-anchor slot is identified by its one anchor or not at all.
    """
    window_tokens = frozenset(_tokens(window))
    if not window_tokens:
        return ()
    transliterated = _transliterated_tokens(window)
    kinds = {a: anchor_match_kind(a, window_tokens, transliterated) for a in slot.anchors}
    named = tuple(a for a in slot.anchors if kinds[a])
    required = 1 if len(slot.anchors) < 2 else 2
    return named if len(named) >= required else ()


def uniquely_named_anchor(
    window: str, slot: RefusedSlot, register: tuple[RefusedSlot, ...] | list[RefusedSlot]
) -> tuple[str, ...]:
    """THE SINGLE-ANCHOR CARVE-OUT (ruling 1), applied only where strict binding found nothing.

    Corroboration is what keeps a Rome answer off the Baltic slot, so the two-anchor default
    stands -- but it made evasion one token wide: "The Baltic is around 17-19 C" names one
    anchor of four and walks, while adding the single word "water" brings the identical claim
    back under the register.

    Three conditions, each load-bearing:

      * matched EXACTLY or by PREFIX, never fuzzily -- which is what keeps the committed
        near-miss guards green unmodified, because Rome's only match against the Baltic slot is
        the fuzzy weather~water;
      * at least `_FUZZY_MIN_LEN` characters -- short tokens collide across unrelated slots;
      * UNIQUE across the register -- an anchor two refused slots share identifies neither, and
        binding on it would attribute the value to whichever slot happened to sort first.

    It lives HERE rather than inside `window_names_slot` so that function keeps the two-argument
    shape the committed corroboration sabotage substitutes. A widening that silently disabled
    one of this file's own sabotages would be the worst possible place to put it.
    """
    window_tokens = frozenset(_tokens(window))
    if not window_tokens or len(slot.anchors) < 2:
        return ()
    transliterated = _transliterated_tokens(window)
    named = [
        a
        for a in slot.anchors
        if anchor_match_kind(a, window_tokens, transliterated) in ("exact", "prefix", "stem")
    ]
    if len(named) != 1:
        return ()
    anchor = named[0]
    if len(fold_script(anchor)) < _FUZZY_MIN_LEN:
        return ()
    if sum(1 for other in register if anchor in other.anchors) > 1:
        return ()
    return (anchor,)


def slots_still_armed(
    register: tuple[RefusedSlot, ...] | list[RefusedSlot], source_context: Any
) -> tuple[RefusedSlot, ...]:
    """The register slots this turn's own evidence does NOT speak for.

    CAUSE B OF THE AUDIT. The contract used to sit behind `turn_ran_observations`, which asks
    a question about the WHOLE TURN: did anything at all get observed? One usable observation
    anywhere switched the contract off for every refused slot at once. Measured live, that is
    the `disarm-fx` / `disarm-mixed` / `disarm-search` class -- a turn that looked up an FX
    rate could then state a Baltic water temperature nobody had fetched, because the FX lookup
    had already answered the turn-grain question.

    Evidence is about a SLOT, not about a turn. An observation disarms the slot it names and
    no other, decided with the same binding rule the register uses everywhere else
    (`anchor_named_in`, two-anchor corroboration for a multi-anchor slot). A slot no
    observation names stays armed.

    Fail-closed: a channel this cannot read leaves every slot armed, because the contract's
    failure mode must be refusing to state a value, never stating one.
    """
    slots = tuple(register or ())
    if not slots:
        return ()
    try:
        from core.model_output_guard import _TURN_OBSERVATION_KEYS
        from core.observation_evidence import usable_observations
    except Exception:
        return slots
    context = source_context if isinstance(source_context, dict) else {}
    witnessed = " ".join(
        str(entry)
        for key in _TURN_OBSERVATION_KEYS
        for entry in usable_observations(context.get(key))
    )
    if not witnessed.strip():
        return slots
    # Deliberately WITHOUT the single-anchor carve-out. That carve-out widens CONVICTION, and
    # applying it here would widen DISARMING instead -- one loosely-named observation would
    # switch a slot off. Arming keeps the strict two-anchor reading, which fails closed.
    return tuple(slot for slot in slots if not window_names_slot(witnessed, slot))


def values_claimed_over_refused_slots(
    text: str, register: tuple[RefusedSlot, ...] | list[RefusedSlot]
) -> tuple[dict[str, Any], ...]:
    """Every live value this reply asserts for a slot the register says was refused.

    Uses `core.model_output_guard.live_value_windows`, which is the SAME value vocabulary the
    existing guard convicts on — with its currentness-anchor requirement and hedge exemption
    withdrawn, because both are calibrated for an open question and this is not one.
    """
    slots = [s for s in (register or ()) if isinstance(s, RefusedSlot)]
    if not slots or not str(text or "").strip():
        return ()
    try:
        from core.model_output_guard import live_value_windows
    except Exception:
        return ()
    claims: list[dict[str, Any]] = []
    for kind, value, window in live_value_windows(text, radius=_SLOT_NAMING_WINDOW):
        for slot in slots:
            named = window_names_slot(window, slot) or uniquely_named_anchor(
                window, slot, slots
            )
            if not named:
                continue
            claims.append(
                {
                    "kind": kind,
                    "value": value,
                    "slot": slot.text,
                    "unit_id": slot.unit_id,
                    "attempt_id": slot.attempt_id,
                    "named_anchors": list(named),
                }
            )
    return tuple(claims)


# ------------------------------------------------------------------------------- what ships


def refused_slot_notice(claims: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """What ships instead of a value invented for a refused slot.

    States the record and nothing else: which slots the runtime has on file as unanswered, the
    recorded reason, and that this turn ran no lookup. It carries no quantity, so it cannot be
    read as the answer it is declining to give, and it is built from ledger rows — there is no
    path by which a model's words reach this string.
    """
    rows: list[str] = []
    seen: set[str] = set()
    for claim in claims or ():
        slot = str((claim or {}).get("slot") or "").strip()
        if not slot or slot.casefold() in seen:
            continue
        seen.add(slot.casefold())
        rows.append(f"* {slot} — {_FALLBACK_REASON}")
    if not rows:
        return (
            "I have no answer on record for that, and I ran no lookup on this turn, so I am not "
            "going to state figures for it. Ask again and I'll fetch the real data first."
        )
    lead = (
        "I already recorded that I could not answer this, and I ran no lookup on this turn — so "
        "any figure I gave for it would be invented, and I am not going to state one:"
        if len(rows) == 1
        else "I already recorded that I could not answer these, and I ran no lookup on this turn "
        "— so any figures I gave for them would be invented, and I am not going to state them:"
    )
    return lead + "\n" + "\n".join(rows) + "\n\nAsk again and I'll fetch the real data first."


def render_register_rows(register: tuple[RefusedSlot, ...] | list[RefusedSlot]) -> str:
    """The register as disclosure rows — used by the follow-up explanation renderer."""
    rows = [slot.as_row() for slot in (register or ()) if isinstance(slot, RefusedSlot)]
    return "\n".join(rows)


__all__ = [
    "RefusedSlot",
    "anchor_named_in",
    "latest_attempt_with_refused_slots",
    "refused_slot_notice",
    "refused_slots_for_attempt",
    "refused_slots_for_session",
    "register_in_scope",
    "render_register_rows",
    "values_claimed_over_refused_slots",
    "window_names_slot",
]
