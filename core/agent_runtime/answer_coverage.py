"""The answer-coverage contract: what a front-door claim is allowed to consume.

THE MEASURED DEFECT (2026-08-12). One message asked eight things:

    what is TRY?
    i meant money TRY
    1000 TRY to USD?
    500 kr to dollars
    A traveler says: "I exchanged 8,500 kr for $1,240, ..." Explain whether the exchange was
    good or bad compared with the normal exchange rate.
    write a README for Thunder
    where was this file stored?
    Create a small Python project called StormWatch with a README and one app.py file.

The conductor planned seven nodes, three succeeded, and the composed partial was discarded because
fewer clauses were served than were unserved. The turn then fell to the front door, where
``looks_like_code_audit_request`` claimed the WHOLE message and -- the chat being General with the
default workspace binding -- answered it with `workspace_audit_default_scope_refused`. Every other
question vanished: not refused, not listed, absent.

Run the detectors clause by clause and the cause is plain (``looks_like_code_audit_request`` per
clause, measured):

    clause 0-4  currency          clause 5  file write     clause 6  receipt location
    clause 7    project build     NO CLAUSE is an audit request

    whole text  audit=True, target='app.py'

The audit verb lives in clause 4 (the traveler story) and the filename in clause 7. Neither clause
asks for an audit; the whole-text read fuses a verb and a target from two different requests into a
claim no clause makes. The same whole-text read runs the other way too:
``looks_like_agentic_build_request`` is True for clause 7 alone and False for the whole message, and
``looks_like_location_ask`` is True for clause 6 alone and False for the whole message -- so the
StormWatch build and the receipt question were not merely outvoted, they were invisible.

THE CONTRACT. A claim carries a SCOPE.

* ``whole_turn`` -- this family answers the entire message and may end the turn.
* ``slice``      -- this family answers named clauses and may not end the turn on its own.

A family may only preempt the whole turn when no OTHER family claims a clause it does not claim
itself. That single rule is deliberately the whole test, because it is the only one that is safe in
both directions:

* it never blocks a single-domain turn, however many sentences it is spread over -- "Audit this
  repo. Find the security vulnerabilities. Give me a verdict." has one claiming family, so the
  audit lane keeps its whole-turn authority;
* it never lets a family that claims NOTHING (the fused audit read above) swallow the clauses other
  families do claim.

Co-claims on the same clause are not a conflict: ``project_build`` and ``file_write`` both read
"Create a small Python project ..." and neither is taking anything from the other.

WHAT A BLOCKED CLAIM DOES. It does not disappear. `record_slice_answer` writes the family's answer
for its OWN clauses into the turn's coverage record and attaches it as a typed tool observation, so
the deterministic answer survives and the remaining clauses route onward to a lane that can serve
them. That is what keeps "1000 TRY to USD?" from being answered by a model that invents a rate
(QA-050-026) while "create StormWatch" still reaches the builder.

Probes here must stay side-effect free: they describe what a family's own detector would do, and
nothing here executes a tool, calls a model, or writes to disk.
"""
from __future__ import annotations

import dataclasses
import re
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

#: A claim that answers the entire message and may end the turn.
CLAIM_SCOPE_WHOLE_TURN = "whole_turn"
#: A claim that answers named clauses only. Ending the turn on one drops the rest.
CLAIM_SCOPE_SLICE = "slice"

# Stable family ids. The routing decision log and the coverage record both carry these, so renaming
# one invalidates recorded history -- don't.
FAMILY_CURRENCY = "currency"
FAMILY_ASSISTANT_IDENTITY = "assistant_identity"
FAMILY_RECEIPT_LOCATION = "receipt_location"
FAMILY_WORKSPACE_AUDIT = "workspace_audit"
FAMILY_PROJECT_BUILD = "project_build"
FAMILY_FILE_WRITE = "file_write"
#: The user's own stored profile/preferences. Registered so a memory clause is VISIBLE to every
#: lane's arbitration: while it was absent, a "what's my codename?" clause read as owed to nobody,
#: and the private-memory lane in `core/web/api/runtime.py` terminated the whole turn with no
#: `claim_may_preempt_turn` check -- the only pre-agent exit without one.
FAMILY_MEMORY_RECALL = "memory_recall"
#: Price/quote clauses. Read through `price_assets_named`, which carries the semantic-claim
#: authority gate, so registering it cannot claim "restaurants near me" shapes.
FAMILY_MARKET_QUOTE = "market_quote"
#: Read through an injected probe (`coverage_for(..., probe=...)`): the live-info classifier needs
#: the agent instance and the turn's interpretation, which this module deliberately does not hold.
FAMILY_LIVE_INFO = "live_info"

#: Where the turn's coverage record travels. Server-derived, never caller-supplied.
COVERAGE_CONTEXT_KEY = "turn_answer_coverage"

# A clause ends at a newline, a bullet, or a sentence terminator -- but a terminator inside quotes
# or inside a number is not a boundary. The traveler story quotes a whole sentence ("I exchanged
# 8,500 kr for $1,240, then spent $300 ...") and splitting inside it would invent two requests where
# the user wrote one.
_QUOTE_PAIRS = {'"': '"', "“": "”", "'": "'", "‘": "’"}
_TERMINATORS = ".?!;"
# "8,500" / "$1,240" / "app.py" / "1.5" -- a terminator with an alphanumeric on both sides is inside
# a token, not between two sentences.
_INTRA_TOKEN_RE = re.compile(r"[\w]$")


def _is_boundary(text: str, index: int) -> bool:
    char = text[index]
    if char not in _TERMINATORS:
        return False
    if char == "." and _INTRA_TOKEN_RE.search(text[:index]) and index + 1 < len(text):
        following = text[index + 1]
        if following.isalnum():
            return False
    return True


@dataclass(frozen=True)
class TurnSlice:
    """One clause of the user's message, with the span it occupies in the original text."""

    slice_id: str
    index: int
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class ClaimCoverage:
    """What one family would consume of this turn, and what it would leave behind."""

    family: str
    scope: str
    consumed: tuple[str, ...]
    uncovered: tuple[str, ...]
    #: Clauses another family claims and this one does not. Non-empty means the claim is a slice.
    conflicting: tuple[str, ...]

    @property
    def covers_whole_turn(self) -> bool:
        return self.scope == CLAIM_SCOPE_WHOLE_TURN


#: An apostrophe that FOLLOWS a word character belongs to the word -- "what's", "cats'", "o'clock"
#: -- and is not a quote opening. Same idea as `_INTRA_TOKEN_RE` above, which already keeps the "."
#: in "8,500" and "app.py" from ending a clause.
_APOSTROPHES = frozenset({"'", "\u2019"})


def _opens_a_quote(text: str, index: int, char: str) -> bool:
    """Whether this character starts a quoted span rather than sitting inside a word.

    Measured 2026-08-18, and the reason this exists. Every apostrophe was treated as a quote
    opening, so the FIRST contraction in a message opened a span that never closed and the splitter
    found no further boundary:

        "What is your name? Also what's the weather in Vilnius?"   -> 2 clauses, arbitration works
        "What's your name? Also what's the weather in Vilnius?"    -> 1 clause,  identity ends the
                                                                      turn and the weather half is
                                                                      never asked

    The same sentence, one contraction apart. Since a contraction is how the question is ordinarily
    typed, the whole per-clause arbitration was reachable mainly by the phrasing nobody uses.

    A leading apostrophe still opens a quote ('hello there'), because there is no word character
    before it to belong to.
    """

    if char not in _APOSTROPHES:
        return char in _QUOTE_PAIRS
    return index == 0 or not text[index - 1].isalnum()


def _bullet_boundary(text: str, index: int, start: int) -> bool:
    """Whether " - " at `index` opens the next item of a bulleted list whose newlines were
    collapsed. The prompt normalizer joins whitespace before the interpretation reads a turn, so
    "Operators should be able to:\n- acknowledge an incident\n- silence alerts" reaches it as
    "Operators should be able to: - acknowledge an incident - silence alerts". A " - " that
    follows a colon-terminated introducer, or a clause that itself began as an item, is the
    list's own structure (measured 2026-09-16); a hyphen inside ordinary prose ("a - b") that
    follows neither is left alone."""
    if not (char_at(text, index) == " " and char_at(text, index + 1) == "-" and char_at(text, index + 2) == " "):
        return False
    segment = text[start:index]
    stripped = segment.strip()
    return bool(stripped) and (stripped.endswith(":") or stripped.startswith("- "))


def char_at(text: str, index: int) -> str:
    return text[index] if 0 <= index < len(text) else ""


def _split_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    closing: str | None = None
    for index, char in enumerate(text):
        if closing is not None:
            if char == closing:
                closing = None
            continue
        if _opens_a_quote(text, index, char):
            closing = _QUOTE_PAIRS[char]
            continue
        if char == "\n" or _is_boundary(text, index):
            end = index if char == "\n" else index + 1
            spans.append((start, end))
            start = index + 1
            continue
        if _bullet_boundary(text, index, start):
            spans.append((start, index))
            start = index + 1
    if start < len(text):
        spans.append((start, len(text)))
    return spans


@lru_cache(maxsize=128)
def turn_slices(text: str) -> tuple[TurnSlice, ...]:
    """The message's clauses, in the order the user wrote them.

    Empty and punctuation-only fragments are dropped: they carry no request, and counting them
    would make an ordinary "Hello!  " look like a two-part message.
    """
    value = str(text or "")
    slices: list[TurnSlice] = []
    for start, end in _split_spans(value):
        body = value[start:end]
        stripped = body.strip()
        if not stripped or not any(char.isalnum() for char in stripped):
            continue
        offset = start + (len(body) - len(body.lstrip()))
        slices.append(
            TurnSlice(
                slice_id=f"s{len(slices) + 1}",
                index=len(slices),
                text=stripped,
                start=offset,
                end=offset + len(stripped),
            )
        )
    return tuple(slices)


# ------------------------------------------------------------------ per-clause family probes


def _claims_currency(clause: str) -> bool:
    from core.agent_runtime.fast_paths_currency import currency_fast_path

    return currency_fast_path(clause) is not None


def _claims_assistant_identity(clause: str) -> bool:
    from core.user_identity_authority import classify_identity_question

    return bool(classify_identity_question(clause).asks_assistant_identity)


def _claims_receipt_location(clause: str) -> bool:
    from core.action_receipt_location import looks_like_location_ask

    return looks_like_location_ask(clause)


def _claims_workspace_audit(clause: str) -> bool:
    from core.agent_runtime.workspace_audit import looks_like_code_audit_request

    return looks_like_code_audit_request(clause)


def _claims_project_build(clause: str) -> bool:
    from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

    if looks_like_agentic_build_request(clause):
        return True
    # `resolve_small_project_plan` is the module's name for this. The import read
    # `small_project_plan`, which does not exist, so EVERY call raised ImportError and
    # `slice_families`' `except Exception: continue` swallowed it -- the handler that exists so a
    # broken detector cannot block a lane was instead hiding a dead one. This arm had never run.
    from core.agent_runtime.builder.small_project_plan import resolve_small_project_plan

    return resolve_small_project_plan(clause) is not None


def _claims_file_write(clause: str) -> bool:
    # "write a README for Thunder" is an artifact the user asked for. No front-door family claims
    # it, and without a probe here it would be an invisible clause -- which is how it was dropped.
    from core.agent_runtime.build_request_intent import imperative_build_sentences

    return bool(imperative_build_sentences(clause))


def _claims_market_quote(clause: str) -> bool:
    # Price/quote clauses. `price_assets_named` already carries the authority gate (a market word
    # beside an index-resolved token is not a claim -- "mid-priced dinner restaurants near me"
    # stays unclaimed), so this delegates rather than re-deriving. Without this family a "price of
    # btc" clause was owned by NO registered family: invisible to the arbitration, so any other
    # lane could end the turn on top of it.
    from core.agent_runtime.fast_live_info_price import price_assets_named

    return bool(price_assets_named(clause))


def _claims_memory_recall(clause: str) -> bool:
    # The lane's own detector, from its single definition in `core.memory_recall_intent` --
    # the same reading the private-memory lane applies, per clause, without prior turns (the
    # lane supplies its history-aware reading through `coverage_for(probe=...)`, the existing
    # override pattern).
    from core.memory_recall_intent import looks_like_private_memory_recall

    return looks_like_private_memory_recall(clause, recent_user_texts=())


def _claims_live_info(clause: str) -> bool:
    # Weather, news and fresh-lookup clauses. Registering this family is what makes the
    # arbitration below able to SEE them: `conflicting` counts only clauses claimed by a family in
    # this tuple, so while live-info was absent, a weather clause read as owed to nobody and any
    # other family was free to end the turn on top of it. Measured before this probe existed:
    #
    #   "What is your name? Also what's the weather in Vilnius?"  -> identity covers_whole_turn=True
    #   "What is your name? Also 1000 TRY to USD?"                -> identity covers_whole_turn=False
    #
    # The same shape, and the only difference was that currency happened to be registered here and
    # weather did not. The turn ended on the name and the weather half was never asked.
    #
    # This is the text-only reading. The front door supplies a richer per-clause probe for its own
    # live-info query (it has the agent and the turn's interpretation); `coverage_for(probe=...)`
    # overrides this family's membership when it does, so the two never disagree in that path.
    from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode

    # A water-temperature ask is a live-info read even though `live_info_mode`
    # declines it (that lane's renderer is the wrong instrument for a sea reading —
    # the TYPED plan lane serves it). Without this arm the arbitration read the
    # unit as owed to nobody while the conductor was actively serving it, and the
    # disclosure sweep served "not something this runtime can look up" BESIDE a
    # succeeded marine observation (measured live, 2026-08-30).
    from core.measurement_medium import requests_a_water_temperature

    if requests_a_water_temperature(clause):
        return True
    return bool(live_info_mode(None, clause, interpretation=None))


_PROBES: tuple[tuple[str, object], ...] = (
    (FAMILY_CURRENCY, _claims_currency),
    (FAMILY_ASSISTANT_IDENTITY, _claims_assistant_identity),
    (FAMILY_RECEIPT_LOCATION, _claims_receipt_location),
    (FAMILY_WORKSPACE_AUDIT, _claims_workspace_audit),
    (FAMILY_PROJECT_BUILD, _claims_project_build),
    (FAMILY_FILE_WRITE, _claims_file_write),
    (FAMILY_MEMORY_RECALL, _claims_memory_recall),
    (FAMILY_MARKET_QUOTE, _claims_market_quote),
    (FAMILY_LIVE_INFO, _claims_live_info),
)


@lru_cache(maxsize=1)
def _machine_family_probes() -> tuple[tuple[str, object], ...]:
    """The machine/tool claim registry, adapted so this arbitration can SEE those clauses.

    `conflicting` below counts only clauses a REGISTERED family claims, and the nine
    machine/tool families lived in a second, disjoint registry
    (`core.agent_runtime.intent_claims`) -- so a clause one of them owned read as owed to nobody,
    and any front-door family was free to end the turn on top of it. Measured at e31ddc7d:

        "1000 TRY to USD? And how much free disk space do I have?"
            slices: (s1 (currency,), s2 ())  ->  currency covers_whole_turn=True
        "What is your name? And how much RAM does this machine have?"
            ->  identity claim_may_preempt_turn=True

    Both turns ended on the first clause; the machine half was never asked. This is the same
    shape the live-info registration repaired for weather clauses, one registry over.

    The (family, probe) pairs live once, in `intent_claims._PROBE_FAMILIES`; this adapts their
    `IntentClaim | None` answers to the boolean this module's probes return. Restating the list
    here would be the two-registries defect all over again.
    """
    from core.agent_runtime.intent_claims import _PROBE_FAMILIES

    def _as_bool(probe: object) -> object:
        def call(clause: str) -> bool:
            return probe(clause) is not None  # type: ignore[operator]

        return call

    return tuple((family, _as_bool(probe)) for family, probe in _PROBE_FAMILIES)


def _retracted_before(text: str) -> int:
    """The offset past which the turn's clauses still stand; -1 when nothing was taken back.

    A clause the user withdraws LATER in the same turn is not a claim on anything, and reading
    clauses in isolation cannot see that. "Look up the live exchange rate for USD to JPY. Use that
    live rate to convert $500. ACTUALLY, cancel the live rate lookup. Just assume the rate is 150
    JPY." claims the live-info family on its first clause and the currency family on its fourth --
    so the two collide, and the currency lane loses a turn the user plainly left to it.

    `core.within_turn_retraction` already owns this reading for the planner; this is the same
    question asked of the same helper rather than a second vocabulary of cue words.
    """
    from core.within_turn_retraction import live_request_after_retraction

    surviving = live_request_after_retraction(text)
    if surviving is None:
        return -1
    stripped = str(text or "").rstrip()
    if not stripped.endswith(surviving):
        # The helper returns a stripped suffix; if it is not one, do not guess at offsets.
        return -1
    return len(stripped) - len(surviving)


def _families_claiming(clause: str) -> tuple[str, ...]:
    """Every registered family that reads this clause text on its own.

    Factored out of `slice_families` so the RSS demand layer can ask the SAME question at unit
    grain without a second, drifting copy of the probe loop. A probe that raises is treated as
    "did not claim": a broken detector must never block a lane that would otherwise have answered.

    Deliberately NOT memoized. The probe loop runs per call, exactly as it did inside
    `slice_families` before this was extracted, because the suite's sabotage tests monkeypatch
    `_PROBES` and clear only the caches they know about — a cache here would serve them the
    un-sabotaged reading and quietly make those proofs vacuous.
    """
    claimed: list[str] = []
    for family, probe in (*_PROBES, *_machine_family_probes()):
        try:
            if probe(clause):  # type: ignore[operator]
                claimed.append(family)
        except Exception:
            continue
    return tuple(claimed)


_SLICE_ASKS_READING: ContextVar[bool] = ContextVar("answer_coverage_slice_asks_reading", default=False)


def _slice_asks_nothing(text: str, slice_id: str) -> bool:
    """Whether this clause, read in its message, asks for nothing: every unit the interpretation
    minted inside it is context, an enumerated item or a literal.

    A family probe reads a clause ON ITS OWN TEXT, and a clause taken alone has no message left
    to tell a described capability from a request. Measured served 2026-09-16 (candidate
    035dea9b): the live-info family claimed "For example, from Discord an admin should be able
    to: ... view bot status and recent errors ... survive restarts." as a fresh lookup, retrieved
    Discord permission tutorials, paid for a wording call and widened the turn to
    current-information -- while the interpretation of the whole message read that clause as a
    statement about the proposed system with its feature list. Applied to the CALLER-SUPPLIED
    probe (`coverage_for(..., probe=...)`, the live-info family's reading, whose vocabulary is
    recency words beside domain nouns): such a clause is not probed; a clause with any request
    in it is probed exactly as before. The registered family probes (`_PROBES`) keep reading
    every clause on its own text: a clarification such as "i meant money TRY" is context of the
    request before it and is still the currency family's clause. Fails open (the probe runs) when
    the interpretation is unavailable or is being read right now.

    RE-ENTRANCY. Reading the interpretation runs the registry's whole-message binders, whose
    probes may consult `coverage_for(..., probe=...)` and land back here for the same text
    before that interpretation is cached. Measured 2026-09-16 on "100 EUR to USD at 1.10. Also
    500 GBP to JPY at 190.5.": the nested read recursed to RecursionError inside the workspace
    and operator probes, the swallowed error read as "did not claim", and the currency lane
    lost its first clause. A nested read therefore fails OPEN (the probe runs on the clause text
    exactly as before this gate existed); only the outermost reading gates.
    """
    if _SLICE_ASKS_READING.get() or _INTERPRETING.get() > 0:
        return False
    token = _SLICE_ASKS_READING.set(True)
    try:
        in_progress = _IN_PROGRESS.get()
        if in_progress is not None and text in in_progress:
            return False
        units = interpret_request(text).units
    except Exception:
        return False
    finally:
        _SLICE_ASKS_READING.reset(token)
    inside = [unit for unit in units if unit.slice_id == slice_id]
    if not inside:
        return False
    return all(unit.kind in (KIND_CONTEXT, KIND_ENUMERATION, KIND_LITERAL) for unit in inside)


@lru_cache(maxsize=128)
def slice_families(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``((slice_id, (family, ...)), ...)`` -- every family that reads each clause on its own.

    A probe that raises is treated as "did not claim". A broken detector must never be able to
    block a lane that would otherwise have answered.

    Clauses the turn later retracts claim nothing: withdrawing a request withdraws the claim it
    would otherwise have made on the arbitration below.
    """
    rows: list[tuple[str, tuple[str, ...]]] = []
    cut = _retracted_before(text)
    for item in turn_slices(text):
        if cut >= 0 and item.end <= cut:
            rows.append((item.slice_id, ()))
            continue
        rows.append((item.slice_id, _families_claiming(item.text)))
    return tuple(rows)


def _probe_claims(probe: object, text: str, slice_id: str) -> bool:
    """Run a caller-supplied per-clause reading. A raising probe reads as "did not claim"."""
    for item in turn_slices(text):
        if item.slice_id != slice_id:
            continue
        if _slice_asks_nothing(text, slice_id):
            return False
        try:
            return bool(probe(item.text))  # type: ignore[operator]
        except Exception:
            return False
    return False


def claimed_slice_ids(text: str, family: str) -> tuple[str, ...]:
    """The clause ids this family reads on their own, in order."""
    return tuple(
        slice_id for slice_id, families in slice_families(text) if family in families
    )


def unclaimed_slices(text: str) -> tuple[str, ...]:
    """Slices NO registered family reads — work that would vanish behind a whole-turn claim.

    `covers_whole_turn` is computed per claimed slice, so a clause the probe registry has no
    family for rides invisibly beside a green gate. Measured live 2026-08-29: "weather in
    rome rn, 100 usd to rub, 18^2, and name one graph db. all 4 pls no essay" — the currency
    family took WHOLE_TURN, and the weather/arithmetic/knowledge clauses (claimed by nobody,
    since no arithmetic or general-knowledge family exists in the registry) were dropped with
    three of four asks unanswered. Callers deciding whether to CLOSE a turn check this before
    trusting a whole-turn verdict.
    """
    return tuple(
        slice_id for slice_id, families in slice_families(text) if not families
    )


def fused_cross_domain_slices(text: str) -> tuple[str, ...]:
    """Slices claimed by families from TWO OR MORE different domain groups, where the second
    reading is a STRONG domain marker rather than a weak probe.

    One slice, several KINDS of work: "weather in rome rn, 100 usd to rub, 18^2 …" carries
    currency AND a weather request on one comma-run slice with no clause punctuation. A family
    answering the whole text serves only its own reading and the other domains vanish (measured
    live 2026-08-29, Q001 — three of four clauses dropped). The STRONG-marker requirement keeps
    weak probes from contesting: "how much is 100 EUR in USD at 1.10?" is co-read by live_info
    on "how much"+digits alone, but it is one currency request and stays whole-turn (this exact
    sentence is a frozen regression case). Distinct from `unclaimed_slices`: here the slice IS
    claimed — just by more than one kind of work at once.
    """
    from core.agent_runtime.fast_live_info_mode_weather_markers import _WEATHER_MARKERS

    fused: list[str] = []
    for item in turn_slices(text):
        row = next(
            (fams for sid, fams in slice_families(text) if sid == item.slice_id), ()
        )
        groups = {_FAMILY_DOMAIN_GROUPS.get(name, name) for name in row}
        if len(groups) < 2:
            continue
        lowered = f" {item.text.lower().strip()} "
        if any(marker.strip().lower() in lowered for marker in _WEATHER_MARKERS):
            fused.append(item.slice_id)
    return tuple(fused)


def coverage_for(text: str, family: str, *, probe: object | None = None) -> ClaimCoverage:
    """Whether `family` may end this turn, and exactly which clauses it accounts for.

    The scope is `whole_turn` unless some clause is claimed by a DIFFERENT family and not by this
    one. Everything else -- how many clauses the message has, how many this family claims, whether
    any clause is claimed at all -- is deliberately not part of the test. A single-domain message
    spread over five sentences must keep its lane, and a family that fused a claim out of two
    unrelated clauses must not keep the turn.

    `probe` lets a caller supply the per-clause reading for a family this module cannot compute on
    its own. The live-info lane is the case: its classifier needs the agent instance and the turn's
    interpretation, and importing either here would put the whole agent behind a coverage check.
    The call site owns the reading; the arbitration stays in one place.
    """
    rows = slice_families(text)
    if probe is not None:
        rows = tuple(
            (
                slice_id,
                tuple(sorted(set(families) | {family})) if _probe_claims(probe, text, slice_id)
                else tuple(name for name in families if name != family),
            )
            for slice_id, families in rows
        )
    if len(rows) < 2:
        # One clause cannot be split away from another that does not exist -- UNLESS the one
        # clause provably FUSES several requests. Punctuation-only slicing cannot see the clause
        # boundaries of a run-on ("What is weather in Rome also I have 100 US convert to RUB and
        # tell me how much gold I can buy" is ONE slice here), so the old unconditional
        # whole-turn grant let the first family that recognised any part of it end the whole
        # turn -- measured live 2026-08-28: the currency fast path answered that message alone
        # and the weather and gold clauses were dropped with the coverage gate green.
        #
        # The veto needs BOTH signals, so single-request turns keep their lane:
        #   - some OTHER registered family also claims this slice (an overlapping reading of the
        #     SAME request -- "gold price?" read by market_quote and live_info -- is only a
        #     contest when the shape test below also fires);
        #   - the slice's own text is multi-request-shaped (`turn_may_hold_several_requests`:
        #     joining tokens or several questions -- deliberately cheap, deliberately permissive).
        consumed = tuple(slice_id for slice_id, families in rows if family in families)
        claimant_group = _FAMILY_DOMAIN_GROUPS.get(family, family)
        contested = tuple(
            slice_id
            for slice_id, families in rows
            if any(
                _FAMILY_DOMAIN_GROUPS.get(name, name) != claimant_group for name in families
            )
        )
        if consumed and contested and _fused_slice_holds_several_requests(text):
            return ClaimCoverage(
                family=family,
                scope=CLAIM_SCOPE_SLICE,
                consumed=consumed,
                uncovered=(),
                conflicting=contested,
            )
        return ClaimCoverage(
            family=family,
            scope=CLAIM_SCOPE_WHOLE_TURN,
            consumed=consumed,
            uncovered=(),
            conflicting=(),
        )
    consumed = tuple(slice_id for slice_id, families in rows if family in families)
    conflicting = tuple(
        slice_id
        for slice_id, families in rows
        if families and family not in families
    )
    uncovered = tuple(slice_id for slice_id, _ in rows if slice_id not in consumed)
    scope = CLAIM_SCOPE_SLICE if conflicting else CLAIM_SCOPE_WHOLE_TURN
    return ClaimCoverage(
        family=family,
        scope=scope,
        consumed=consumed,
        uncovered=uncovered if scope == CLAIM_SCOPE_SLICE else (),
        conflicting=conflicting,
    )


#: Families that read the SAME KIND of request, so overlapping claims between them are two
#: readings of one thing rather than two things. "Create a project ... with a README and one
#: app.py file" is claimed by project_build AND file_write -- nested readings of ONE build, not a
#: fused pair of requests -- and market_quote/live_info both read "gold price?". A family absent
#: from this map is its own group: claims across groups are claims on DIFFERENT work, which is
#: what makes a single fused slice contested.
_FAMILY_DOMAIN_GROUPS: dict[str, str] = {
    FAMILY_MARKET_QUOTE: "live_lookup",
    FAMILY_LIVE_INFO: "live_lookup",
    FAMILY_WORKSPACE_AUDIT: "workspace",
    FAMILY_PROJECT_BUILD: "workspace",
    FAMILY_FILE_WRITE: "workspace",
}


def _fused_slice_holds_several_requests(text: str) -> bool:
    """Whether a single punctuation-slice plausibly fuses several requests.

    Delegates to the turn planner's admission test (joining tokens / several question marks) so
    the two components agree on what "several requests" means. Fail-toward-False: an unavailable
    or raising test degrades to the pre-repair whole-turn grant, never to a new veto.
    """
    try:
        from core.agent_runtime.turn_planner import turn_may_hold_several_requests

        return bool(turn_may_hold_several_requests(text))
    except Exception:
        return False


def claim_may_preempt_turn(text: str, family: str) -> bool:
    """The one question a front-door lane asks before returning a whole-turn answer."""
    return coverage_for(text, family).covers_whole_turn


def is_mixed_intent_turn(text: str) -> bool:
    """Two or more DISTINCT families reading two or more DIFFERENT clauses of one message.

    This is the "no whole-turn fallback exists" test. When it is True, every front-door family is a
    slice claim by construction, so a composer that gives up and falls back is falling back to a
    lane that will answer part of the message and drop the rest.
    """
    rows = slice_families(text)
    if len(rows) < 2:
        return False
    claiming = {slice_id: set(families) for slice_id, families in rows if families}
    if len(claiming) < 2:
        return False
    seen: set[str] = set()
    for families in claiming.values():
        seen |= families
    if len(seen) < 2:
        return False
    # At least one family must be missing from at least one claimed clause, or the families all
    # read the same message the same way and there is nothing to arbitrate.
    return any(
        any(family not in families for families in claiming.values()) for family in seen
    )


# ------------------------------------------------------------------------- the coverage record


def slice_text(text: str, slice_ids: tuple[str, ...] | list[str]) -> str:
    """The user's own words for the named clauses, joined in the order they were written."""
    wanted = set(slice_ids)
    return " ".join(item.text for item in turn_slices(text) if item.slice_id in wanted)


@lru_cache(maxsize=128)
def slice_entity_state(text: str) -> tuple[tuple[str, dict[str, object]], ...]:
    """Per-clause entity resolution, including contradictions a LATER clause creates.

    Each clause owns its own state. That is the point: a clause whose entity binding cannot be
    trusted is one clause with a problem, not a turn that failed. "500 kr to dollars" followed by
    "I'm in Copenhagen" invalidates the NOK/SEK reading of the first clause and leaves the second,
    third and eighth clauses entirely alone -- and every conclusion the poisoned binding would have
    supported is withheld rather than computed and shipped.

    Contradictions are accumulated in clause order, so a later anchor reaches back. `rebind` names
    what the anchor implies; `clarify` names a pairing nothing here may resolve for the user.
    """
    from core.entity_compatibility import (
        Verdict,
        entity_conflicts,
        resolve_entities,
        settlements_across,
    )

    slices = turn_slices(text)
    rows: list[tuple[str, dict[str, object]]] = []
    for index, item in enumerate(slices):
        # Its own impossible pairings, then what every LATER clause settles about its guesses.
        # `settlements_across` reads this clause's tokens against the later clause's anchors, so a
        # contradiction can only ever be attributed to the clause that actually made the guess --
        # "what is TRY?" is never reported as contradicted by a price six sentences down that it
        # has nothing to do with.
        conflicts = list(entity_conflicts(item.text))
        for later in slices[index + 1 :]:
            for settlement in settlements_across(item.text, later.text):
                if settlement not in conflicts:
                    conflicts.append(settlement)
        rows.append(
            (
                item.slice_id,
                {
                    "entities": [
                        {
                            "category": mention.category.value,
                            "value": mention.value,
                            "surface": mention.surface,
                            "authority": mention.authority.value,
                        }
                        for mention in resolve_entities(item.text)
                    ],
                    "conflicts": [
                        {
                            "verdict": conflict.verdict.value,
                            "anchor": conflict.anchor.surface,
                            "invalidated": conflict.invalidated.surface,
                            "implied": conflict.implied,
                            "explanation": conflict.explanation,
                        }
                        for conflict in conflicts
                    ],
                    "needs_clarification": any(
                        conflict.verdict is Verdict.CLARIFY for conflict in conflicts
                    ),
                    "binding_unsafe": bool(conflicts),
                },
            )
        )
    return tuple(rows)


def slice_binding_is_unsafe(text: str, slice_id: str) -> bool:
    """Whether this clause's entity binding is contradicted, so nothing may be computed from it."""
    for candidate, state in slice_entity_state(text):
        if candidate == slice_id:
            return bool(state.get("binding_unsafe"))
    return False


def record_slice_answer(
    source_context: dict[str, object] | None,
    *,
    text: str,
    family: str,
    response: str,
    reason: str = "",
    consumed: tuple[str, ...] | list[str] | None = None,
    consumed_units: tuple[str, ...] | list[str] | None = None,
) -> ClaimCoverage:
    """Keep a blocked lane's answer for its OWN clauses, and name what it did not cover.

    The lane no longer ends the turn, so without this its work would be thrown away and the clause
    it owned would be answered by whatever lane runs next -- which for a currency conversion means a
    model inventing a rate. Recorded as a typed tool observation for the same reason the workspace
    audit records its evidence: the answering lane must be able to tell a runtime-established fact
    from its own prose.

    `consumed` narrows the record to the clauses this call actually served. One family can produce
    two records for one turn -- an answer for the clauses it could serve and a contradiction report
    for the one it could not -- and each must claim only its own clauses, or a clause that was
    deliberately withheld would be counted as answered.

    `consumed_units` is the same declaration one grain finer: the DEMAND units this call
    served, bound at their own spans (see `units_matching_needle`). It rides the record
    beside the clause-space `consumed` (which arbitration still reads); the ledger
    consumer prefers it, because clause grain absolves co-clause siblings nothing
    served (Incident 3, measured live 2026-08-30).
    """
    coverage = coverage_for(text, family)
    if consumed is not None:
        coverage = ClaimCoverage(
            family=coverage.family,
            scope=coverage.scope,
            consumed=tuple(consumed),
            uncovered=coverage.uncovered,
            conflicting=coverage.conflicting,
        )
    if not isinstance(source_context, dict):
        return coverage
    record = source_context.get(COVERAGE_CONTEXT_KEY)
    if not isinstance(record, dict):
        record = {"slices": [], "answers": []}
    entity_state = dict(slice_entity_state(text))
    record["slices"] = [
        {
            "slice_id": item.slice_id,
            "index": item.index,
            "text": item.text,
            **entity_state.get(item.slice_id, {}),
        }
        for item in turn_slices(text)
    ]
    answers = [dict(item) for item in list(record.get("answers") or []) if isinstance(item, dict)]
    answer = {
        "family": family,
        "scope": coverage.scope,
        "consumed": list(coverage.consumed),
        "reason": reason or family,
        "response": str(response or ""),
    }
    if consumed_units is not None:
        answer["consumed_units"] = [str(unit) for unit in consumed_units]
    if answer not in answers:
        answers.append(answer)
    record["answers"] = answers
    answered_ids = {
        slice_id for item in answers for slice_id in list(item.get("consumed") or [])
    }
    record["uncovered"] = [
        item.slice_id for item in turn_slices(text) if item.slice_id not in answered_ids
    ]
    record["needs_clarification"] = [
        slice_id
        for slice_id, state in slice_entity_state(text)
        if state.get("needs_clarification")
    ]
    source_context[COVERAGE_CONTEXT_KEY] = record

    observations = [
        dict(item)
        for item in list(source_context.get("runtime_tool_observations") or [])
        if isinstance(item, dict)
    ]
    observation = {
        "schema": "tool_observation_v1",
        "intent": f"turn.slice_answer.{family}",
        "tool_surface": "answer_coverage",
        "ok": True,
        "status": "executed",
        "read_only": True,
        "final_answer": False,
        "slice_ids": list(coverage.consumed),
        "slice_text": slice_text(text, coverage.consumed),
        "response_preview": str(response or "")[:4000],
        "instruction": (
            "This message asks several separate things. The text above already answers the clauses "
            "listed in `slice_text` and is established runtime fact -- carry it into the reply "
            "verbatim in the order the user wrote it, do not recompute it, and then answer the "
            "remaining clauses. If a remaining clause cannot be served, say so explicitly rather "
            "than omitting it."
        ),
    }
    if not observations or observations[-1] != observation:
        observations.append(observation)
    source_context["runtime_tool_observations"] = observations[-12:]
    return coverage


def record_slice_contradiction(
    source_context: dict[str, object] | None,
    *,
    text: str,
    family: str,
    slice_id: str,
) -> None:
    """Report a clause whose entity binding is contradicted, WITHOUT answering it.

    Fable's direction, applied to a poisoned binding: a declining deterministic lane supplies
    grounded facts forward rather than guessing. So this records what is actually known -- which
    reading was invalidated, by which anchor, and what that anchor implies -- and does not compute
    a conversion, a ranking or a percentage from a premise that has just been shown false.

    It is one clause. The other clauses' answers stand.
    """
    if not isinstance(source_context, dict):
        return
    state = dict(slice_entity_state(text)).get(slice_id) or {}
    conflicts = list(state.get("conflicts") or [])
    if not conflicts:
        return
    lines = [slice_text(text, (slice_id,))]
    lines += [str(conflict.get("explanation") or "") for conflict in conflicts]
    implied = [str(conflict.get("implied") or "") for conflict in conflicts if conflict.get("implied")]
    if implied:
        lines.append(
            "Confirm the currency and I'll answer this part; I won't compute anything from the "
            f"reading that no longer holds. On the evidence here it is {implied[0]}."
        )
    record_slice_answer(
        source_context,
        text=text,
        family=family,
        response="\n".join(line for line in lines if line),
        reason=f"{family}_binding_contradicted",
        consumed=(slice_id,),
    )


# ---------------------------------------------------------------- RSS: the turn's demand set
#
# AUD-20260829-003. The turn's demand set was never minted from the request: the only demand
# artifact was ONE prose obligation holding a 240-character prefix of the message, discharged from
# the mere existence of served bytes. A requested slot no recognizer read had no representation
# anywhere -- it could not be answered, refused, listed, retried or counted -- and the closure
# certificate truthfully reported `covered: true` about a set that did not describe the request.
#
# Two measured constraints shape what follows, and both are the OPPOSITE of the obvious design:
#
# 1. POLARITY. The sealed remedy proposed minting on demand SHAPE (interrogative / imperative /
#    request-marked). Measured over the project's own frozen fixtures (E012): "100 EUR to USD at
#    1.10." and "Also 500 GBP to JPY at 190.5." are bare declarative fragments and mint NOTHING
#    under that rule -- 3 of the 5 official currency fixtures, plus the sabotage text, invisible
#    to the demand layer entirely. The taxonomy of requests is open and cannot be enumerated. The
#    taxonomy of greetings/thanks/acknowledgements is closed and short. So the test is a DENYLIST:
#    a content-bearing unit is demand UNLESS it is recognisably conversational. Unknown shape mints.
#
# 2. GRAIN. `turn_slices` splits on `.?!;` and newline only. The audit's own canonical acceptance
#    prompt ("What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in
#    Rome, and what is the water temperature in the Baltic Sea?") is ONE slice, so a slice-grain
#    demand set cannot represent it and 1-of-4 coverage would certify as a complete one-slot turn.
#    `demand_units` therefore sub-splits a slice at a coordination boundary, but ONLY where the
#    following fragment opens like a fresh request. `turn_slices` itself is untouched and no door,
#    probe or arbitration consults this decomposition -- admission and routing are unchanged.

#: Conjunctions that start a new demand unit only when what follows opens like a request.
_DEMAND_UNIT_SPLIT_CONNECTORS = ("and", "also", "plus")
#: Sequencing markers. "and then X" is a next STEP, not a conjoined noun phrase, so it starts a
#: unit even when the step is written as a bare phrase -- "10000usd to eur and then to gold
#: please" is two requests and the second was the leg silently dropped in the served defect.
_SEQUENCE_CONNECTORS = ("then",)

#: Heads that open a fresh request. Used ONLY to decide whether a comma/conjunction inside one
#: clause starts a new demand unit -- never to decide whether a unit is demand at all (that is the
#: denylist above, and keying mint on these heads is exactly the under-mint-to-zero defect).
_DEMAND_HEADS = frozenset(
    {
        "what", "whats", "when", "where", "why", "who", "whom", "whose", "which", "how",
        "is", "are", "was", "were", "do", "does", "did", "can", "could", "will", "would",
        "should", "may", "might", "has", "have", "had", "am",
        "tell", "give", "show", "convert", "find", "list", "name", "explain", "calculate",
        "compute", "check", "write", "create", "make", "get", "fetch", "search", "look",
        "describe", "compare", "translate", "summarise", "summarize", "send", "add", "remove",
        "open", "run", "build", "audit", "review", "price", "quote",
        # C7 (2026-09-05): "say" is a request verb like every other head here, and it was
        # only absent because a 3-letter verb could never reach the content-word branch
        # anyway. Case-insensitive code matching gives every 2-5 letter token a route to
        # anchoring, so the heads have to be complete: without this, "say ok" anchors on
        # "say" and the runtime starts requiring answers to echo the instruction.
        "say",
        # R1g: 'define' was absent while every sibling request verb (describe,
        # summarize, explain, compare) is present, so "summarize the gold
        # standard and define liquidity" minted ONE fused unit — the second
        # demand was invisible to the demand layer (found by the R1g additivity
        # gauntlet on the untouched base).
        "define",
        # P0 MIXED-DEMAND TERMINAL CLOSURE: the COMPLETION class. "And finish
        # with a two-word joke." opens a fresh request exactly like "tell me…"
        # does, but no head recognized it, so the EXECUTION mint merged it into
        # the request before it ("Convert 100 US dollars to euros. And finish
        # with a two-word joke." = one unit) while the OBLIGATION mint counted
        # three — two mints, two stories, and the seam that dispatches units
        # never saw the joke at all (measured on the served surface at base
        # 84bf8b6a). Scoped to verbs whose object is work the user still owes a
        # reply for ('finish with', 'conclude with', 'wrap up with'); pure
        # discourse markers stay out.
        "finish", "conclude", "wrap",
        # Same class, measured by the refusal gauntlet: "Transfer 50 dollars to
        # Dave." merged into the conversion before it, so the currency lane met
        # a conversion-and-a-move as ONE unit, declined the pair, and the
        # refusal the user needed to SEE (this lane will not move money) never
        # reached the reply. "transfer" opens a request exactly like "send".
        "transfer",
        # P0 MIXED-DEMAND: the VERDICT class. Every verb above either fetches a
        # thing ('convert', 'find', 'price') or produces prose about one
        # ('explain', 'describe', 'compare', 'define'); the class that takes a
        # proposition and returns a JUDGEMENT about it was absent entirely. So
        # "Return the second line of notes.txt. Calculate 37 x 19. Decide
        # whether that line describes walking." merged the arithmetic and the
        # judgement into ONE execution unit — three demands, two identities,
        # and the third demand could never be dispatched or reported (measured
        # on the untouched base 840a2392).
        #
        # SCOPED DELIBERATELY, and this is the load-bearing part: only verbs
        # whose object is a PROPOSITION to be judged are added. Pure OUTPUT
        # verbs ('return', 'output', 'print', 'display') are deliberately NOT
        # added — they open presentation riders ("Return the results as exactly
        # two tables: …") which the module's own docstring names as a fragment
        # that "executes as nothing on its own", and promoting them to demand
        # heads would shred a request from its rendering instructions. The
        # falsifiable direction is pinned by test: that table-shape paragraph
        # must still merge into the request it renders.
        "decide", "determine", "classify", "judge", "verify", "confirm",
        "identify", "count", "evaluate", "assess",
    }
)

#: A unit made ENTIRELY of these mints nothing. Everything else mints, including rhetorical and
#: sarcastic questions -- RSS refuses to judge whether a question is "really" a request, because
#: that judgement is the family recognition this layer exists to avoid. Such a unit is counted in
#: the certificate and can never produce a user-visible row (see `units_with_registered_demand`).
_CONVERSATIONAL_TOKENS = frozenset(
    {
        "hi", "hello", "hey", "yo", "sup", "howdy", "greetings", "there", "welcome",
        "good", "morning", "afternoon", "evening", "night", "day",
        "thanks", "thank", "thankyou", "thx", "ty", "cheers", "appreciate", "appreciated",
        "you", "your", "yours", "u", "ur", "i", "im", "me", "my", "we", "it", "its",
        "very", "much", "lot", "lots", "a", "an", "the", "so", "too", "as", "for", "of",
        "ok", "okay", "kk", "cool", "great", "nice", "awesome", "super", "perfect",
        "excellent", "lovely", "brilliant", "sweet", "fine", "alright", "right", "well",
        "please", "kindly", "sorry", "bye", "goodbye", "later", "cya", "ciao",
        "all", "done", "finished", "mate", "man", "dude", "friend", "buddy", "folks",
        "no", "np", "problem", "sure", "yes", "yeah", "yep", "yup", "nope", "nah",
        "hmm", "hm", "ah", "oh", "haha", "lol", "heh", "hah", "huh", "eh", "wow",
        "damn", "fuck", "hell", "shit",
        # R1g — correction filler. A fragment made ENTIRELY of these plus the
        # social words above is the CONNECTIVE TISSUE of a correction, not a
        # request: 'sorry I mean', 'Actually', 'ignore that' minted as phantom
        # demand units beside the demands they modify (found by the R1g
        # corrections gauntlet). A real ask keeps a token outside this set —
        # "ignore all previous instructions" keeps 'previous'/'instructions'
        # and still mints.
        "mean", "actually", "ignore",
        # Opinion/banter verbs. A unit made ENTIRELY of these is an aside, not a request:
        # "ok so u think u so cool heh?" (E001, the operator's own opener) minted a slot that
        # no lane could ever discharge. Any real ask adds a token outside this set --
        # "what do you think about X" keeps "about" and "X" and still mints.
        "think", "thinks", "thought", "guess", "reckon", "suppose", "seem", "seems",
        "really", "pretty", "quite", "clever", "smart", "funny", "silly", "cheeky",
        # Urgency/format adverbials. A fragment made ENTIRELY of these is a qualifier on the
        # ask that follows it ("real quick, what can you do locally ..."), not a second demand:
        # minting it blocked the single-unit deterministic lanes and the capability question
        # fell through to a full model turn. A real ask keeps a token outside this set.
        "real", "quick", "quickly", "briefly", "short", "simple", "shorter", "concise",
        "and", "then", "also", "just", "now", "still", "again", "separately",
        "am", "is", "are", "was", "were", "be", "been", "doing", "going", "help", "out",
        "how", "what", "whats", "up", "s", "to", "on", "in", "with", "that", "this",
        "can", "could", "would", "will", "do", "does", "did", "have", "has", "had", "get",
    }
)

#: Tokens that carry no distinguishing content when asking "did the answer mention this unit".
_CONTENT_STOP_TOKENS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "then", "also", "into", "from", "you",
        "your", "how", "what", "whats", "when", "where", "why", "who", "which", "much",
        "many", "can", "could", "will", "would", "should", "may", "might", "have", "has",
        "had", "are", "was", "were", "been", "being", "does", "did", "not", "but", "its",
        "it", "about", "there", "here", "please", "just", "now", "some", "any", "all",
        "tell", "give", "show", "get", "want", "need", "like", "make", "let", "know",
        "one", "two", "out", "too", "very", "more", "most", "than", "them", "they", "their",
    }
)

_UNIT_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]+", re.UNICODE)

#: Words naming the SHAPE of an answer rather than a thing to look up. A unit built only from
#: these plus function/social words asks how to present work already being done -- "Give me a
#: verdict." after "Audit this project." -- so it rides along with the turn it elaborates and
#: must never become an unavailable row. This is the concrete guard for the suite's sharpest
#: control ("Three sentences, one domain: blocking this would be the regression").
_META_OUTPUT_TOKENS = frozenset(
    {
        "verdict", "answer", "answers", "result", "results", "summary", "summaries",
        "detail", "details", "explanation", "explanations", "breakdown", "overview",
        "report", "conclusion", "conclusions", "recommendation", "recommendations",
        "opinion", "opinions", "thoughts", "list", "output", "response", "reply",
        "essay", "words", "sentence", "sentences", "short", "brief", "quick", "full",
        "number", "numbers", "figure", "figures", "digits", "decimals", "total", "totals",
        "everything", "both", "each", "them", "these", "those", "it", "back",
        # "its contents" names the FORM of what to return about a thing the request already
        # named, exactly like "details" and "summary" above it. Measured served 2026-09-09
        # (build c9200e0c, acceptance turn 16): "Read the file /tmp/... and tell me its
        # contents." minted "and tell me its contents." as its own demand, that demand was
        # served on its own with no object, and the model resolved "its" against the PREVIOUS
        # turn -- the reply carried a file-read refusal followed by three bullets about why the
        # sky is blue.
        "contents", "content",
        # Comparative/superlative SELECTION over the turn's own subjects. "which one is
        # warmer?" after a two-city weather ask names no thing to look up — the cities
        # are the turn's subjects and the comparison is the SHAPE of their answer, the
        # same law "give me a verdict" already follows. Measured live 2026-08-30
        # (Incident 2): the derivation served "Warmest current city: Warsaw" while this
        # unit was reported `unanswered` — an obligation answered and simultaneously
        # reported unanswered. The falsifiable direction is pinned by test: a
        # comparison that names its own entities ("which is cheaper, gold or silver?")
        # keeps them and still mints as a real demand.
        "warmer", "warmest", "colder", "coldest", "hotter", "hottest", "cooler",
        "coolest", "bigger", "biggest", "larger", "largest", "smaller", "smallest",
        "higher", "highest", "lower", "lowest", "cheaper", "cheapest", "pricier",
        "priciest", "faster", "fastest", "slower", "slowest", "stronger", "strongest",
        "weaker", "weakest", "mover", "movers", "move", "moves", "moved",
        # Anaphora HEADS over the turn's own subjects: "which CITY is warmer?" /
        # "which PLACE moved most?" point at the subjects the turn already named
        # ("what is the biggest city in europe" still mints -- "europe" is a real
        # content token and keeps the unit its own demand).
        "city", "cities", "town", "towns", "place", "places",
    }
)

#: The anaphora selectors of `_META_OUTPUT_TOKENS` -- comparative/superlative forms and
#: the interrogative heads that open them -- separately named: the rider law matches
#: them with the module's own TYPO tolerance (`_near_miss` -- the same closed lexical
#: tolerance `_DEMAND_HEADS` already enjoys), because the users who type "which one is
#: warmr?" / "whihc city is warmest?" are the users this class exists for. Property 1
#: is preserved: a typo still MINTS (the mint is not a spelling test); this only
#: affects whether a unit that names nothing else rides the turn it elaborates.
_ANAPHORA_SELECTOR_TOKENS = frozenset(
    {
        "warmer", "warmest", "colder", "coldest", "hotter", "hottest", "cooler",
        "coolest", "bigger", "biggest", "larger", "largest", "smaller", "smallest",
        "higher", "highest", "lower", "lowest", "cheaper", "cheapest", "pricier",
        "priciest", "faster", "fastest", "slower", "slowest", "stronger", "strongest",
        "weaker", "weakest", "mover", "movers", "moved", "which", "whose", "whom",
    }
)


def _is_selector_or_its_typo(token: str) -> bool:
    """An anaphora selector, or one typo of one (`_near_miss`'s two edit classes)."""
    stripped = token.strip(".,;:!?'\"()")
    if stripped in _ANAPHORA_SELECTOR_TOKENS:
        return True
    if len(stripped) < 5:
        return False
    return any(_near_miss(stripped, selector) for selector in _ANAPHORA_SELECTOR_TOKENS)

#: Openers of a tag question or opinion check attached to a request by a comma. They continue
#: the SAME request ("100 EUR to USD at 1.10, don't you think that's fair?") and must not start
#: a new demand unit.
_TAG_OPENERS = frozenset(
    {
        "don", "dont", "doesn", "doesnt", "isn", "isnt", "aren", "arent", "wasn", "weren",
        "wouldn", "couldn", "shouldn", "won", "can", "right", "yeah", "yes", "no", "nope",
        "ok", "okay", "huh", "eh", "innit", "agreed", "surely", "or",
    }
)


@dataclass(frozen=True)
class DemandUnit:
    """One thing the user asked for, at the grain the closure certificate accounts in.

    M3 mint enrichment: `start`/`end` are the EXACT source spans (half-open code-
    point ranges into the original text -- `text == original[start:end].strip()`
    up to the leading-space adjustment the splitter documents); `polarity` is
    "request" for every MINTED unit (prohibitions mint nothing by the RED-1 NEW-3
    law -- they are preserved, not vanished, via `prohibited_clauses`); `freshness`
    carries the recency cue the unit itself states, read from the SAME marker set
    the live-info mode classifier already owns (no new vocabulary); `output_
    constraint` is the unit's own requested-output shape when it states one
    (empty = none stated -- never inferred).
    """

    unit_id: str
    slice_id: str
    index: int
    text: str
    start: int
    end: int
    polarity: str = "request"
    freshness: str = ""
    output_constraint: str = ""
    #: M4 interpretation (contract 2026-09-08). `kind` is one of KIND_REQUEST (mints a slot),
    #: KIND_CONSTRAINT / KIND_LITERAL / KIND_ENUMERATION / KIND_CONTEXT (belong to the request they
    #: attach to, named in `depends_on`) or KIND_UNRESOLVED. A request's `depends_on` names the
    #: request or context it refers back to; `literal_spans` marks content that is data, never an
    #: instruction; `unresolved_refs` are anaphors nothing in the message binds; `origin` says who
    #: filled the fields ("heuristic" today; "model" is reserved for the interpretation call).
    kind: str = "request"
    depends_on: tuple[str, ...] = ()
    literal_spans: tuple[tuple[int, int], ...] = ()
    unresolved_refs: tuple[str, ...] = ()
    origin: str = "heuristic"


def _normalise_digits(text: str) -> str:
    """Fold digit-valued characters onto ASCII digits before tokenising.

    `18^2` is answered `18² = 324`, and SUPERSCRIPT TWO is a digit-valued character that
    `[0-9]` does not match -- so the answer tokenised to `18`/`²` and the request to `18`/`2`,
    and the runtime could not see that it had answered its own question. This is a text
    normalisation over a Unicode property, not a rule about exponents.
    """
    value = str(text or "")
    if value.isascii():
        return value
    out = []
    for char in value:
        if char.isascii():
            out.append(char)
            continue
        try:
            digit = unicodedata.digit(char)
        except (TypeError, ValueError):
            out.append(char)
            continue
        # Nd folds in place (a full-width digit IS part of its number); No -- superscripts,
        # subscripts -- is notationally separate from the number beside it, so it becomes its
        # own token rather than being glued on ("18²" must not read as "182").
        out.append(str(digit) if unicodedata.category(char) == "Nd" else f" {digit}")
    return "".join(out)


def _unit_tokens(text: str) -> tuple[str, ...]:
    return tuple(
        match.group(0).lower()
        for match in _UNIT_TOKEN_RE.finditer(_normalise_digits(text))
    )


def is_conversational_unit(text: str) -> bool:
    """Whether this fragment is pure conversational packaging and mints no demand.

    Closed vocabulary, whole-unit: every token must be social. A digit is disqualifying, so
    "1000 TRY to USD?" can never read as conversational however it is padded. Measured against
    E011's politeness population -- "hi there.", "thank you very much", "ok cool.",
    "appreciate it mate", "thanks a lot", "hey can you help me out." -- all of which trip the
    reverted `unclaimed_slices` guard and are exactly the shape family the 114-flow revert was
    about.
    """
    tokens = _unit_tokens(text)
    if not tokens:
        return True
    if any(token[0].isdigit() for token in tokens):
        return False
    return all(token in _CONVERSATIONAL_TOKENS for token in tokens)


def _has_content(fragment: str) -> bool:
    return any(char.isalnum() for char in str(fragment or ""))


_SPLIT_LEADERS = frozenset(
    {"and", "then", "also", "plus", "so", "ok", "okay", "but", "now", "just", "please", "separately"}
)


def _near_miss(token: str, head: str) -> bool:
    """One typo away -- the shared closed budget in `core.typo_fold.near_miss` (adjacent
    transposition or one inserted/deleted character, never a substitution). Kept under this name
    because the grain's tests and callers bind to it; the algorithm has ONE home so the machine
    recognizers and the demand grain cannot drift apart."""
    from core.typo_fold import near_miss

    return near_miss(token, head)


@lru_cache(maxsize=512)
def _is_demand_head(token: str) -> bool:
    if token in _DEMAND_HEADS:
        return True
    if len(token) < 4:
        return False
    return any(_near_miss(token, head) for head in _DEMAND_HEADS if len(head) >= 4)


def _opens_a_demand(fragment: str) -> bool:
    """Whether a fragment following a coordination boundary opens like a fresh request.

    The leader skip is typo-tolerant for the same reason `_is_demand_head` already is, and it
    has to be, or the two disagree and a mistyped connective swallows the request behind it.

    Measured on the operator's own evening turn: "and thne tell me the water tempperature in
    baltc sea" opens a genuinely fresh request whose HEAD is "tell". The loop reached "thne"
    first -- one transposition from the leader "then" -- did not recognise it as a leader,
    tested it as a head, and returned False. The fragment was then fused into the PRECEDING
    execution unit, so a water reading the runtime served was filed against the gold
    obligation, gold certified satisfied with no gold bytes, and the water slot was disclosed
    as "not dispatched" three lines under its own answer.

    `_near_miss` is the module's existing closed budget -- adjacent transposition or one
    inserted/deleted character, never a substitution -- and is already applied to heads
    directly below. This applies it to the leaders they are compared against.
    """
    tokens = _unit_tokens(fragment)
    for index, token in enumerate(tokens):
        if (
            token in _DEMAND_UNIT_SPLIT_CONNECTORS
            or token in _SPLIT_LEADERS
            or any(_near_miss(token, leader) for leader in _SPLIT_LEADERS if len(leader) >= 4)
        ):
            continue
        # A head with nothing after it names nothing to ask about. Measured live 2026-09-07:
        # "... for easy read and compare" minted "and compare" as a request of its own and the
        # closure reported it answered in part. A bare verb after a conjunction is the same
        # predicate applied to the object the unit before it already carries ("compare [them]");
        # a head WITH an object ("and compare it with silver", "and define liquidity") still opens.
        return _is_demand_head(token) and index + 1 < len(tokens)
    return False


def _introduces_no_new_subject(fragment: str) -> bool:
    """True when a fragment names nothing of its own -- only connectors, demand heads, function
    words, answer-shape words, selectors and numbers.

    The generalisation of `_is_bare_head_fragment`, which catches the same class only when the
    verb stands completely alone ("and then explain"). A fragment whose object is an anaphor is
    the same predicate applied to what the request before it already carries, and splitting it
    off produces a demand with no subject that a later lane must resolve on its own.

    Measured served 2026-09-09, build c9200e0c, acceptance turn 16: "Read the file /tmp/... and
    tell me its contents." split at the `and`; the second unit was served alone; "its" bound to
    the PREVIOUS TURN, and the reply was a file-read refusal followed by three bullets about why
    the sky is blue.

    Deliberately narrow: a fragment naming ANY thing of its own -- "gold with it", "and compare
    it with silver", "and what is the water temperature in the Baltic Sea?" -- still opens a
    fresh request, which is what keeps the frozen four-slot case
    ("1000 EUR to RUB, gold with it, weather in Rome, baltic sea water temp") at four demands.
    """
    from core.task_router import looks_like_direct_math_request

    # Arithmetic has its own subject in its operands. Numbers are otherwise ignored here
    # because presentation instructions such as "show 3 bullets" inherit the prior subject.
    if looks_like_direct_math_request(_strip_leaders(fragment)):
        return False
    for token in _unit_tokens(fragment):
        if (
            token in _DEMAND_UNIT_SPLIT_CONNECTORS
            or token in _SEQUENCE_CONNECTORS
            or token in _SPLIT_LEADERS
            or token in _CONVERSATIONAL_TOKENS
            or token in _CONTENT_STOP_TOKENS
            or token in _META_OUTPUT_TOKENS
        ):
            continue
        if _is_demand_head(token) or _is_selector_or_its_typo(token):
            continue
        if token[0].isdigit():
            continue
        return False
    return True


def _is_bare_head_fragment(fragment: str) -> bool:
    """True when, after its connectors and leaders, the fragment is exactly one demand head --
    a verb with nothing to act on ("and then explain")."""
    body = [
        token
        for token in _unit_tokens(fragment)
        if not (
            token in _DEMAND_UNIT_SPLIT_CONNECTORS
            or token in _SEQUENCE_CONNECTORS
            or token in _SPLIT_LEADERS
            or (len(token) >= 4 and (_near_miss(token, "then") or any(_near_miss(token, leader) for leader in _SPLIT_LEADERS if len(leader) >= 4)))
        )
    ]
    return len(body) == 1 and _is_demand_head(body[0])


def opens_a_fresh_request(fragment: str) -> bool:
    """Public wrapper over `_opens_a_demand` — the one authority for whether a minted
    unit boundary is EXECUTION-safe (R1e). The mint sub-splits a slice permissively —
    a bare multi-token phrase after a comma is its own UNIT ("1000 EUR to RUB, gold
    with it, weather in Rome") because the render gate wants the finest unanswered
    slot. Executing that grain would shred shared context: "Compare 500 kr, $500" and
    "and ¥500 for a traveler in Copenhagen and Shanghai" are ONE comparison whose
    amounts and anchors landed in separate units, and running them as isolated
    sub-turns lost the anchor ("`kr` is unresolved without a country anchor"). A
    boundary is execution-safe only when the following fragment opens with a demand
    head — a fresh REQUEST, not a continuation phrase."""
    return _opens_a_demand(str(fragment or ""))


def _opens_a_phrase(fragment: str) -> bool:
    """Whether a comma-separated fragment is its own request rather than a list item.

    MEASURED LIVE 2026-08-29 over HTTP, and the reason this exists. The killer shape

        "1000 EUR to RUB, gold with it, weather in Rome, baltic sea water temp"

    minted ONE unit and certified `demand_minted: 1, covered: true` while serving the FX leg
    alone -- the same root defect one layer down. Every fragment there is a bare noun phrase:
    no terminal "?", no clause verb, no demand head. Segmentation that waits for punctuation or
    interrogative form cannot see them, so this reads STRUCTURE instead:

    * a fragment of two or more tokens is a PHRASE and stands as its own request;
    * a single-token fragment is a LIST ITEM and does not ("price of xmr, xlm, bch" is one ask
      over three assets, and "Rome, Italy" is one place);
    * a fragment opening with a tag/opinion marker continues the same request
      ("100 EUR to USD at 1.10, don't you think that's fair?");
    * a fragment that is pure conversational packaging is not a request at all.
    """
    tokens = _unit_tokens(fragment)
    lead = 0
    while lead < len(tokens) and (
        tokens[lead] in _DEMAND_UNIT_SPLIT_CONNECTORS or tokens[lead] in _SPLIT_LEADERS
    ):
        lead += 1
    body = tokens[lead:]
    if len(body) < 2:
        return False
    if body[0] in _TAG_OPENERS:
        return False
    return not is_conversational_unit(" ".join(body))


#: The interrogative heads a VERBLESS conjunct can open with. "how much gold" carries no predicate,
#: so it is not a request on its own; the coordination that follows it is shared structure.
_SHARED_PREDICATE_HEAD_RE = re.compile(r"\b(?:how\s+(?:much|many)|which|what)\b", re.IGNORECASE)


def _shared_predicate_range(clause: str) -> tuple[int, int] | None:
    """The span of a coordination whose conjuncts share ONE predicate, or None.

    Measured on the built candidate 60a91da3 (2026-09-07): "How much gold and how much silver can
    I buy with one bitcoin right now?" was minted as two demands; the bare "How much gold" was
    answered by a price quote, the rest by a quote-only plan, and the turn closed as complete with
    neither amount computed. The predicate belongs to BOTH objects.

    The reading is grammatical, not a phrase list: the clause opens with an interrogative head, the
    left conjunct is a bare noun phrase (no verb, no request head, at most four tokens), and the
    right conjunct either repeats the same head ("and how much silver ...") or opens with another
    bare noun phrase ("and silver can I buy ..."). A right conjunct that opens with a DIFFERENT
    request head ("and what is the weather in Rome") opens a fresh request and is not protected; a
    left conjunct with its own verb ("how much IS gold and how much is silver") is complete and
    keeps splitting.
    """
    value = str(clause or "")
    lowered = value.lower()
    head = _SHARED_PREDICATE_HEAD_RE.search(lowered)
    if head is None:
        return None
    if any(
        token not in _DEMAND_UNIT_SPLIT_CONNECTORS and token not in _SPLIT_LEADERS
        for token in _unit_tokens(lowered[: head.start()])
    ):
        return None
    rest = lowered[head.end() :]
    conjunction = re.search(r"\band\b", rest)
    if conjunction is None:
        return None
    left = _unit_tokens(rest[: conjunction.start()])
    if not left or len(left) > 4 or any(token in _DEMAND_HEADS for token in left):
        return None
    right = re.sub(r"\s+", " ", rest[conjunction.end() :]).strip()
    right_tokens = _unit_tokens(right)
    if not right_tokens:
        return None
    head_text = re.sub(r"\s+", " ", head.group(0))
    if not right.startswith(head_text) and right_tokens[0] in _DEMAND_HEADS:
        return None
    return (head.start(), len(value))


#: Finite verbs that open a QUESTION at the head of a clause ("can users ...", "is it ...") and,
#: after a subject, make a DECLARATIVE clause ("users can ...", "the bot is ..."). The modals among
#: them govern coordinated bare verb phrases ("users can earn ..., choose ..., check ...").
_FINITE_VERB_HEADS = frozenset(
    {
        "is", "are", "was", "were", "am", "has", "have", "had", "do", "does", "did",
        "can", "could", "will", "would", "should", "may", "might", "must", "shall",
    }
)
_MODAL_HEADS = frozenset({"can", "could", "will", "would", "should", "may", "might", "must", "shall"})
#: A conjunct carrying one of these is addressed to the runtime ("..., and tell me how to fix it")
#: and opens a request of its own however the clause before it reads.
_ASSISTANT_ADDRESS_TOKENS = frozenset({"me", "us", "you", "your", "please", "pls", "plz"})
#: Leaders that open a NEW clause after a comma rather than a conjunct of the one before it.
_CLAUSE_OPENING_LEADERS = frozenset({"so", "but", "however", "because", "since", "if", "unless", "while", "whereas"})


def _governing_finite_verb(body: tuple[str, ...] | list[str]) -> str:
    """The finite verb a declarative body is built on, or "" when the body does not read as a
    third-person declarative: its FIRST head-shaped token must be a finite verb with a subject
    standing before it. "users can earn points from activity" -> "can"; "the important part is
    that ..." -> "is". A question puts the finite verb first ("can users earn points"), a wh-question
    opens with its wh-word, and an imperative opens with its verb ("check balances", "explain the
    architecture"); none of those is a statement here. A body with no head at all ("gold with it",
    "brief me on the sahel") is not a statement either and keeps the mint's default."""
    if not body or body[0] in _WH_WORDS:
        return ""
    for index, token in enumerate(body):
        if token in _FINITE_VERB_HEADS:
            return token if index >= 1 else ""
        if _is_demand_head(token) or token in _IMPERATIVE_STEP_VERBS or token in _EFFECT_STEP_VERBS:
            return ""
    return ""


def _subject_precedes_its_verb(body: tuple[str, ...] | list[str]) -> bool:
    """The third-person declarative reading of a fragment (see `_governing_finite_verb`)."""
    return bool(_governing_finite_verb(body))


def _shared_subject_range(clause: str) -> tuple[int, int] | None:
    """The span of a DECLARATIVE clause whose coordinated conjuncts continue its statement, or None.

    MEASURED 2026-09-16 on candidate 035dea9b. A design brief described the system it asked about:
    "Users can earn points from activity, choose Alliance or Horde, check balances, transfer points,
    and view faction rankings." Cut at its commas, "check balances" and "transfer points" are
    indistinguishable from imperatives addressed to the runtime, and that is what they became: two
    demand units, bound as build steps by the builder's whole-message reading, each dispatched as
    its own paid sub-turn ("check balances ... did not return a usable reply").

    The reading is grammatical, not a phrase list. The clause opens with a subject and its finite
    verb (`_governing_finite_verb`). After a MODAL ("users can ...") a bare verb phrase after a
    coordinator is a coordinated infinitive sharing that subject ("..., choose ..., check ..."); a
    conjunct that is itself a declarative ("..., and a compromised command should not ...") continues
    the statement whatever the first verb was; a bare object list ("points, badges and titles")
    continues it too. The protection ENDS before any conjunct addressed to the runtime ("..., and
    tell me how to fix it"), any question ("..., and what is the gold price?"), a new clause led by
    "so"/"but"/..., and -- under a non-modal verb -- any bare verb phrase ("this code has a bug, fix
    it" keeps its imperative). A question ("Can users earn points, and check balances?") or an
    imperative ("Check balances, and transfer points") opens with its verb and is never protected.
    """
    value = str(clause or "")
    lowered = value.lower()
    _leaders, body = _leader_split(_unit_tokens(lowered))
    governing = _governing_finite_verb(body)
    if not governing:
        return None
    modal = governing in _MODAL_HEADS
    coordinator = re.compile(r",|\b(?:and|also|plus|then)\b")
    end = len(value)
    run_start: int | None = None
    for match in coordinator.finditer(lowered):
        if run_start is None:
            run_start = match.start()
        rest = lowered[match.end():]
        stop = coordinator.search(rest)
        piece = rest[: stop.start()] if stop else rest
        piece_tokens = _unit_tokens(piece)
        if not piece_tokens:
            continue  # ", and": the same boundary, read on the conjunct that follows it
        opens_new_request = (
            piece_tokens[0] in _WH_WORDS
            or piece_tokens[0] in _CLAUSE_OPENING_LEADERS
            or piece.strip().endswith("?")
            or any(token in _ASSISTANT_ADDRESS_TOKENS for token in piece_tokens)
            # A conjunct stating what the USER HOLDS ("i have 100 usd") is the amount half of a
            # conversion ask, not declarative prose about a system being described: protecting
            # across it fused the weather request before it into one demand (measured on the
            # sloppy comma-splice) and orphaned the conversion that follows the amount.
            or _opens_an_amount_frame(piece)
        )
        if not opens_new_request and not _subject_precedes_its_verb(piece_tokens) and not modal:
            # A bare verb phrase can only share a MODAL's subject; after "has"/"is" it is a
            # clause of its own ("..., fix it").
            opens_new_request = _is_demand_head(piece_tokens[0]) or piece_tokens[0] in _IMPERATIVE_STEP_VERBS
        if opens_new_request:
            end = run_start - 1
            break
        run_start = None
    if end <= 0:
        return None
    return (0, end)


_COMPARISON_HEAD_RE = re.compile(r"\b(?:compare|contrast|comparing|contrasting)\b", re.IGNORECASE)
_FACET_LIST_RE = re.compile(r":\s*(?P<items>[^:.;!?]{4,240})(?=[.;!?]|$)")


def _coordination_protected_ranges(clause: str) -> list[tuple[int, int]]:
    """Character ranges in `clause` whose "and"/comma coordination joins ONE request."""
    protected: list[tuple[int, int]] = []
    shared = _shared_predicate_range(clause)
    if shared is not None:
        protected.append(shared)
    statement = _shared_subject_range(clause)
    if statement is not None:
        protected.append(statement)
    facet = _FACET_LIST_RE.search(clause)
    if facet:
        items = [part.strip() for part in re.split(r",|\band\b|;", facet.group("items")) if part.strip()]
        # Facets are attributes ("production periods", "prices"); a list whose items open with a
        # demand head ("do two things: define liquidity and summarize the gold standard") is a
        # list of REQUESTS and keeps splitting.
        def _is_instruction(item: str) -> bool:
            tokens = item.lower().split()
            return len(tokens) >= 2 and tokens[0] in _DEMAND_HEADS

        if (
            2 <= len(items) <= 8
            and all(len(item.split()) <= 4 for item in items)
            and not any(_is_instruction(item) for item in items)
        ):
            protected.append((facet.start(), facet.end()))
    head = _COMPARISON_HEAD_RE.search(clause)
    if head:
        stop = facet.start() if facet else len(clause)
        # The compared pair: from the comparison verb up to the facet list (or the clause end),
        # but never across a comma that opens a fresh request ("..., and what is ...").
        fresh = re.search(r",\s*(?:and|then|also|plus)\s+(?:tell|what|how|which|when|where|who|give|show|find|check|is|are|can|could|would|do|does|did|convert)\b", clause[head.end():stop], re.IGNORECASE)
        if fresh:
            stop = head.end() + fresh.start()
        protected.append((head.start(), stop))
    return protected


#: A clause subject that can stand alone before a connector ("I also want ...", "we then need ...").
_PRONOUN_SUBJECTS = frozenset({"i", "we", "you", "it", "they", "he", "she", "this", "that", "these", "those"})

#: Openers of a CONDITIONAL SETUP -- a clause that names a condition, not an ask. Its request
#: is completed by the question that follows it, so the two are one demand, not two.
_CONDITIONAL_OPENERS = frozenset(
    {"if", "when", "whenever", "unless", "assuming", "suppose", "supposing", "provided"}
)

#: Heads of a question fragment, plus the approximation adverbs that lead one
#: ("roughly how much ..."). A conditional setup whose continuation opens this way is being
#: ASKED ABOUT, not followed by a second request.
_INTERROGATIVE_HEADS = frozenset(
    {
        "what", "whats", "when", "where", "why", "who", "whom", "whose", "which", "how",
        "is", "are", "was", "were", "do", "does", "did", "can", "could", "will", "would",
        "should", "may", "might", "has", "have", "had", "am",
        "roughly", "approximately", "about", "around", "any",
    }
)


def _conditional_setup_question(left: str, fragment: str) -> bool:
    """Whether `fragment` is the question form OF the conditional `left` beside it.

    "If I exchange 100 EUR to USD, roughly how much USD do I get?" is ONE request: the
    conditional names the conversion, the question asks its outcome. Splitting at the comma
    minted the setup and its own question as two demands; the second dispatched, failed, and
    was reported unanswered UNDER the answer that had just answered it (acceptance turn 10,
    2026-09-08, served verbatim on the isolated daemon 2026-09-09). A conditional SETUP opens
    with a condition word; an interrogative continuation (a demand head, or a terminal "?")
    asks about that setup's result. Both sides must hold -- an imperative continuation
    ("If you find the file, delete it") keeps today's behaviour.
    """
    left_words = str(left or "").strip().lower().split()
    if not left_words or left_words[0] not in _CONDITIONAL_OPENERS:
        return False
    fragment = str(fragment or "").strip()
    if not fragment:
        return False
    if fragment.endswith("?"):
        return True
    head = fragment.lower().split()[0]
    return head in _INTERROGATIVE_HEADS


def _unit_spans(clause: str) -> list[tuple[int, int]]:
    """Sub-split ONE clause at coordination boundaries that open a fresh request.

    A boundary is a comma, or a coordinating connector, whose remainder opens with a demand head.
    Quoted spans are never split (the traveler-story fixture is one clause and must stay one).
    Both sides must carry content, so a trailing "and" or a list comma never splits.
    """
    value = str(clause or "")
    lowered = value.lower()
    quoted: set[int] = set()
    closing: str | None = None
    comma_edges: list[int] = []
    for index, char in enumerate(value):
        if closing is not None:
            quoted.add(index)
            if char == closing:
                closing = None
            continue
        if _opens_a_quote(value, index, char):
            closing = _QUOTE_PAIRS[char]
            quoted.add(index)
            continue
        if char == ",":
            # "1,000 EUR" is one amount. A comma with a digit on both sides is inside a
            # number, never between two requests.
            if (
                index
                and index + 1 < len(value)
                and value[index - 1].isdigit()
                and value[index + 1].isdigit()
            ):
                continue
            comma_edges.append(index + 1)
    # (offset, requires_a_demand_head). Quoted spans are never split: the traveler-story
    # fixture is one clause by frozen contract and carries commas, "then" and a trailing
    # imperative inside its quotation.
    boundaries: dict[int, bool] = {edge: True for edge in comma_edges if edge - 1 not in quoted}
    tokens = [
        (match.start(), match.group(0).lower())
        for match in _UNIT_TOKEN_RE.finditer(lowered)
        if match.start() not in quoted
    ]
    for position, (offset, token) in enumerate(tokens):
        if token in _SEQUENCE_CONNECTORS or (len(token) >= 4 and _near_miss(token, "then")):
            edge = offset
            if position and tokens[position - 1][1] == "and":
                edge = tokens[position - 1][0]
            needs_head = False
        elif position and token in _DEMAND_UNIT_SPLIT_CONNECTORS:
            if (
                token == "plus"
                and position + 1 < len(tokens)
                and tokens[position - 1][1][0].isdigit()
                and tokens[position + 1][1][0].isdigit()
            ):
                # An arithmetic operator cannot terminate the question before its operand.
                continue
            edge, needs_head = offset, True
        else:
            continue
        if edge <= 0:
            continue
        near = next((key for key in boundaries if abs(key - edge) <= 2), None)
        if near is not None:
            boundaries[near] = boundaries[near] and needs_head
            continue
        boundaries[edge] = needs_head
    # Coordination that does not open a fresh request: the two subjects a comparison joins
    # ("compare the Passat AND the Golf") and the members of a facet list a colon introduces
    # ("...: production periods, sales, regions, engines AND prices"). Measured 2026-09-06: a
    # detailed comparison was minted as three demands and every one reported unanswered under an
    # answer that had just published six supported claims about it.
    for start, end in _coordination_protected_ranges(value):
        for edge in list(boundaries):
            if start < edge <= end:
                del boundaries[edge]
    spans: list[tuple[int, int]] = []
    cursor = 0
    ordered = sorted(boundaries)
    for position, boundary in enumerate(ordered):
        if boundary <= cursor:
            continue
        left = value[cursor:boundary]
        # The fragment this boundary would OPEN, not the whole remainder: "price of xmr, xlm,
        # bch" must weigh "xlm" on its own (a list item) rather than "xlm, bch ..." (which is
        # multi-token and would split every enumeration in the tree).
        stop = next((edge for edge in ordered[position + 1 :] if edge > boundary), len(value))
        fragment = value[boundary:stop]
        if not _has_content(left) or not _has_content(fragment):
            continue
        left_tokens = _unit_tokens(left)
        if len(left_tokens) == 1 and left_tokens[0] in _PRONOUN_SUBJECTS:
            # "I also want a private server ...": the connector sits right after the subject.
            # Cutting there leaves a lone pronoun (which mints nothing) and a subjectless
            # "also want ..." that reads as a request of its own. Measured 2026-09-16. A bare
            # discourse leader on the left ("Also, what time is it?") still cuts as before.
            continue
        if _conditional_setup_question(left, fragment):
            continue
        if boundaries[boundary] and not (
            _opens_a_demand(fragment) or _opens_a_phrase(fragment) or _opens_an_amount_frame(fragment)
        ):
            continue
        if _continues_an_amount_frame(left, fragment):
            # "i have 100 usd, convert to rub" is ONE request: the conversion verb consumes the
            # amount and currency the fragment before it holds. Cutting between them minted a
            # sourceless "convert to rub" no lane could bind and glued the amount onto whatever
            # request happened to precede it (measured on the sloppy comma-splice: weather + a
            # fused amount as one unit, the conversion orphaned as another).
            continue
        if _introduces_no_new_subject(fragment):
            # Nothing of its own to ask about: this continues the request it follows.
            continue
        if not boundaries[boundary] and _is_bare_head_fragment(fragment):
            # A sequence marker followed by a bare verb ("... and then explain", "then
            # summarize") names no step of its own: it is the same predicate applied to what the
            # request before it produced. Measured 2026-09-07 on new wording of the operator's
            # "and compare" class: "make that shorter and then explain" minted two units and the
            # second could only ever be reported unanswered.
            continue
        spans.append((cursor, boundary))
        cursor = boundary
    spans.append((cursor, len(value)))
    return [(start, end) for start, end in spans if _has_content(value[start:end])]


#: Openers of a PROHIBITION. "Do not send any money." is an instruction about what NOT to do:
#: it can never be answered, so minting it as demand creates an obligation that can only ever
#: be reported unmet -- and the runtime then tells its user it could not comply with a
#: restriction (RED-1 NEW-3, sourced from this project's own served-defect census rows).
_PROHIBITION_OPENERS = (
    ("do", "not"), ("does", "not"), ("dont",), ("don",), ("never",), ("avoid",),
    ("without",), ("no", "need"), ("skip",),
)

#: A fragment that opens by STATING WHAT THE USER HOLDS ("i have 100 usd") opens a fresh
#: request of its own: it is the amount half of a conversion ask, and failing to split it from
#: the request before it fused unrelated demands together.
_AMOUNT_FRAME_RE = re.compile(
    r"^(?:i|we)\s+(?:have|ve|got|own|hold)\b[^,;]{0,32}\d",
    re.IGNORECASE,
)
#: A conversion verb that takes "to/into" continues the amount frame before it.
_CONVERT_CONTINUATION_RE = re.compile(
    r"^(?:convert|exchange|change|turn|switch)\b[^,;]{0,32}\b(?:to|into)\b",
    re.IGNORECASE,
)
#: An amount with a currency-ish tail anywhere in a fragment.
_AMOUNT_WITH_CODE_RE = re.compile(r"\d[\d.,]*\s*(?:usd|us\b|eur|gbp|rub|try|jpy|chf|pln|cad|aud|inr|czk|sek|nok|dkk|huf|ron|bgn)", re.IGNORECASE)


def _opens_an_amount_frame(fragment: str) -> bool:
    return bool(_AMOUNT_FRAME_RE.match(str(fragment or "").strip()))


def _continues_an_amount_frame(left: str, fragment: str) -> bool:
    """Whether `fragment` is the conversion half of the amount `left` states."""
    return bool(
        _CONVERT_CONTINUATION_RE.match(str(fragment or "").strip())
        and _AMOUNT_WITH_CODE_RE.search(str(left or ""))
    )


def is_prohibition_unit(text: str) -> bool:
    """Whether this unit forbids something rather than asking for it."""
    tokens = _unit_tokens(text)
    lead = 0
    while lead < len(tokens) and (
        tokens[lead] in _DEMAND_UNIT_SPLIT_CONNECTORS or tokens[lead] in _SPLIT_LEADERS
    ):
        lead += 1
    head = tokens[lead : lead + 2]
    return any(tuple(head[: len(opener)]) == opener for opener in _PROHIBITION_OPENERS)


# ============================================================================================
# M4 -- ONE interpretation of the message (contract: docs/REQUEST_INTERPRETATION_MIGRATION_CONTRACT
# _2026-09-08.md). The mint cuts fragments exactly as before; a classification pass gives every
# fragment a KIND, attaches non-request fragments to the request they belong to, records
# dependencies and literal spans, and lets the lane registry's whole-message binders decide the
# request grain inside a text a lane admits whole (a travel story's narration is context, a
# workflow's steps are requests). `demand_units()` is the request-kind view of this
# interpretation and keeps its contract: one unit per thing the user asked for.
# ============================================================================================

KIND_REQUEST = "request"
KIND_CONSTRAINT = "constraint"
KIND_LITERAL = "literal"
KIND_ENUMERATION = "enumeration"
KIND_CONTEXT = "context"
KIND_UNRESOLVED = "unresolved"

_KIND_PREFIX = {
    KIND_CONSTRAINT: "c", KIND_LITERAL: "l", KIND_ENUMERATION: "e", KIND_CONTEXT: "x", KIND_UNRESOLVED: "r",
}

#: Verbs that open an instruction step in imperative position although they are not question heads
#: ("replace TODO with DONE", "see how many monolith files it has", "suggest which ones to split").
#: A closed list of editing / inspection / workflow steps; presentation verbs ("compare") stay out
#: because a bare "and compare" is the predicate applied to the request before it, not a step.
_IMPERATIVE_STEP_VERBS = frozenset(
    {
        "replace", "rename", "move", "copy", "delete", "edit", "set", "install", "uninstall",
        "test", "verify", "lint", "format", "commit", "deploy", "update", "upgrade", "put",
        "save", "append", "read", "inspect", "scan", "analyse", "analyze", "evaluate", "count",
        "see", "suggest", "recommend", "fix", "repair", "refactor", "split", "extract", "generate",
        "execute", "start", "stop", "restart", "launch", "kill", "print", "log", "trace", "walk",
        "locate", "identify", "diagnose", "debug", "measure", "estimate", "flag", "highlight",
    }
)
#: A bare effect verb after a connector is a step of its own ("then retry"), unlike a bare
#: presentation verb ("and compare"), which elaborates the request before it.
_EFFECT_STEP_VERBS = frozenset({"retry", "rerun", "repeat", "redo"})
_SEQUENCE_LEADERS = frozenset({"then", "next", "finally", "afterwards", "afterward", "later"})
_CONTINUATION_PREPOSITIONS_MINT = frozenset(
    {"to", "in", "at", "for", "from", "with", "of", "on", "by", "into", "about", "via", "per",
     "against", "under", "over", "as", "than", "vs", "versus"}
)
_ANAPHORS = frozenset(
    {"it", "its", "that", "this", "them", "those", "these", "there", "same", "again", "more"}
)
#: A headless fragment carrying one of these is a sentence about a situation ("my pc is super
#: slow lately", "i think about my best friend a lot"), not an item of a list.
#: A correction of the request before it ("i meant money TRY", "no, i mean the file", "sorry,
#: I meant euros"): context of that request, never a demand of its own. A registered lane may
#: well admit its words (the currency probe claims "money TRY"); the words are still a gloss on
#: what was just asked, and answering them apart from it is how a clarification came to be listed
#: as "not dispatched" under the very answer it clarified.
_CLARIFICATION_RE = re.compile(
    r"^\s*(?:no[,.]?\s+|sorry[,.]?\s+|oops[,.]?\s+|wait[,.]?\s+)?(?:i\s+meant?|i\s+was\s+asking\s+about|to\s+clarify|i\s+mean\s+the|that\s+is[,]?\s+i\s+mean)\b",
    re.IGNORECASE,
)

#: A bulleted or numbered list item, marker included, as the slice splitter keeps it.
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+\u2022]|\d+[.)])\s+")

#: Verbs that, coordinated after a request, instruct how its answer is presented: "and mention X
#: as an example", "and cite the source", "and include a diagram". Never "name" or "list": those
#: open demands of their own ("Name both currencies").
_PRESENTATION_VERBS = frozenset({"mention", "cite", "include", "note", "reference", "highlight", "flag"})

#: What marks a coordinated "mention/include/cite" as being about the ANSWER's form.
_PRESENTATION_MARKER_RE = re.compile(
    r"\b(?:as\s+(?:an\s+)?examples?|for\s+example|the\s+sources?|a\s+(?:diagram|table|chart|summary|link|caveat)|"
    r"(?:your|the)\s+(?:reasoning|steps|assumptions|sources)|in\s+(?:the|your)\s+(?:answer|reply|response)|briefly|in\s+passing)\b",
    re.IGNORECASE,
)

_WH_WORDS = frozenset({"what", "where", "when", "which", "who", "whom", "whose", "how", "why", "whether"})

#: The prohibition arms of a constraint: a message made only of one of these mints no request.
_PROHIBITION_RE = re.compile(
    r"^\s*(?:(?:and|but|also|please|oh)\s+)*(?:do\s+not|don'?t|never|no\s+other|nothing\s+(?:else|more)|"
    r"leave\s+.+?\s+alone|skip\s+(?:everything|anything)\s+else|without\s+(?:changing|modifying|touching))\b",
    re.IGNORECASE,
)

#: A statement has a SUBJECT-VERB shape: it opens with a first-person or demonstrative subject
#: ("i think", "my pc is", "it keeps", "the fan is") and carries a stative verb within reach. A
#: bare object phrase with a trailing pronoun ("gold with it") is not a statement -- measured
#: 2026-09-08 on the four-slot served string, where "gold with it" fell to context and a slot
#: vanished.
_FIRST_PERSON_STARTERS = frozenset({"i", "im", "ive", "my", "we", "our", "idk"})
_SUBJECT_STARTERS = frozenset({"it", "its", "this", "that", "there", "the", "a", "an", "these", "those", "he", "she", "they"})
_STATEMENT_VERBS = frozenset(
    {"is", "are", "was", "were", "has", "have", "had", "says", "seems", "looks", "keeps", "wont", "cant",
     "doesnt", "dont", "am", "feel", "think", "got", "get", "keep", "want", "need", "know", "guess"}
)


def _reads_as_a_statement(body: tuple[str, ...] | list[str]) -> bool:
    if not body:
        return False
    if body[0] in _FIRST_PERSON_STARTERS:
        return True
    if body[0] in _SUBJECT_STARTERS and any(token in _STATEMENT_VERBS for token in body[1:4]):
        return True
    # A third-person declarative: a subject standing before its finite verb ("users can earn
    # points ...", "normal discord users must never ..."). Measured 2026-09-16: statements about a
    # proposed system minted as requests, and their described capabilities were dispatched as work.
    return _subject_precedes_its_verb(body)


_STATEMENT_TOKENS = frozenset(
    {
        "i", "im", "ive", "my", "we", "our", "it", "its", "this", "that", "there", "is", "are",
        "was", "were", "has", "have", "had", "says", "seems", "looks", "keeps", "wont", "cant",
        "doesnt", "dont", "idk", "am", "feel", "think", "got", "get", "keep",
    }
)

#: A sentence that constrains the request before it instead of asking for anything new: "Do not
#: create anything else.", "Nothing else, please.", "Leave every other file alone."
_CONSTRAINT_OPENER_RE = re.compile(
    r"^(?:(?:and|but|also|please|oh)\s+)*(?:do\s+not|don'?t|never|nothing\s+(?:else|more)|no\s+other|"
    r"only\s+(?:that|those|these)|leave\s+.+?\s+alone|skip\s+(?:everything|anything)\s+else|"
    r"and\s+that'?s\s+(?:it|all)|that'?s\s+(?:it|all)|read[\s-]+only\b|without\s+(?:changing|modifying|touching))"
)
#: "show your math", "walk me through the math", "break down the steps": a presentation instruction
#: about the answer, attached to the request it qualifies. The object must END the sentence, so
#: "explain the steps of photosynthesis" stays a request.
_PRESENTATION_INSTRUCTION_RE = re.compile(
    r"^(?:(?:and|also|then|plus|please|oh|now)\s+)*(?:show|walk|take|break|lay|spell|write|detail|explain|give)\b"
    r"[^.?!]{0,40}?\b(?:maths?|steps?|workings?|calculations?|arithmetic|breakdown|reasoning)\b\s*[.!?]*\s*$"
)
_LITERAL_MARKER_RE = re.compile(
    r"\b(?:containing|with\s+the\s+text|that\s+says|saying|with\s+content|with\s+the\s+content)\b\s*:?\s*",
    re.IGNORECASE,
)
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


@dataclass(frozen=True)
class RequestInterpretation:
    """The message read once: every fragment with its kind, its attachment and its dependencies."""

    text: str
    units: tuple[DemandUnit, ...]

    @property
    def requests(self) -> tuple[DemandUnit, ...]:
        return tuple(unit for unit in self.units if unit.kind == KIND_REQUEST)

    @property
    def constraints(self) -> tuple[DemandUnit, ...]:
        return tuple(unit for unit in self.units if unit.kind == KIND_CONSTRAINT)

    @property
    def literals(self) -> tuple[DemandUnit, ...]:
        return tuple(unit for unit in self.units if unit.kind == KIND_LITERAL)

    @property
    def enumerations(self) -> tuple[DemandUnit, ...]:
        return tuple(unit for unit in self.units if unit.kind == KIND_ENUMERATION)

    @property
    def context(self) -> tuple[DemandUnit, ...]:
        return tuple(unit for unit in self.units if unit.kind == KIND_CONTEXT)

    @property
    def unresolved(self) -> tuple[DemandUnit, ...]:
        return tuple(unit for unit in self.units if unit.kind == KIND_UNRESOLVED or unit.unresolved_refs)

    def attached_to(self, request_id: str) -> tuple[DemandUnit, ...]:
        """The non-request fragments that belong to `request_id`."""
        return tuple(
            unit for unit in self.units if unit.kind != KIND_REQUEST and request_id in unit.depends_on
        )

    def group_span(self, request_id: str) -> tuple[int, int]:
        """The source span of a request together with everything attached to it."""
        members = [unit for unit in self.units if unit.unit_id == request_id] + list(
            self.attached_to(request_id)
        )
        if not members:
            return (0, 0)
        return (min(unit.start for unit in members), max(unit.end for unit in members))


#: A constraint riding INSIDE a fragment after a comma: "and what it imports first, don't change
#: anything". The comma is the boundary the splitter kept; the constraint is a unit of its own.
_INLINE_CONSTRAINT_CUT_RE = re.compile(
    r",\s+(?=(?:and\s+|but\s+)?(?:do\s+not|don'?t|never|without\s+(?:changing|modifying|touching)|"
    r"leave\s+.+?\s+alone|nothing\s+else|no\s+other|read[\s-]+only)\b)",
    re.IGNORECASE,
)


def _cut_inline_constraints(fragments: list[tuple[str, int, int, str]]) -> list[tuple[str, int, int, str]]:
    out: list[tuple[str, int, int, str]] = []
    for text, start, end, slice_id in fragments:
        match = _INLINE_CONSTRAINT_CUT_RE.search(text)
        if match is None or match.start() == 0:
            out.append((text, start, end, slice_id))
            continue
        head = text[: match.start()].rstrip()
        tail = text[match.end():]
        out.append((head, start, start + len(head), slice_id))
        tail_start = start + match.end()
        out.append((tail.strip(), tail_start, tail_start + len(tail.strip()), slice_id))
    return out


def _raw_fragments(value: str) -> list[tuple[str, int, int, str]]:
    """Every fragment the mint cuts, in order, with exact spans -- prohibitions included (they
    become constraints), conversational packaging excluded (it mints nothing, as before)."""
    return _cut_inline_constraints(_raw_fragments_uncut(value))


def _raw_fragments_uncut(value: str) -> list[tuple[str, int, int, str]]:
    cut = _retracted_before(value)
    fragments: list[tuple[str, int, int, str]] = []
    for item in turn_slices(value):
        if cut >= 0 and item.end <= cut:
            continue
        slice_text = item.text
        slice_start = item.start
        if cut >= 0 and item.start < cut < item.end:
            # R1g -- a slice that STRADDLES the retraction cut keeps only its surviving suffix.
            slice_text = item.text[cut - item.start :]
            slice_start = cut
        for start, end in _unit_spans(slice_text):
            body = slice_text[start:end]
            stripped = body.strip().strip(",")
            if not stripped or is_conversational_unit(stripped):
                continue
            offset = slice_start + start + (len(body) - len(body.lstrip()))
            fragments.append((stripped, offset, offset + len(stripped), item.slice_id))
    return fragments


def _literal_spans(value: str) -> tuple[tuple[int, int], ...]:
    """Spans of the message that are content to copy, never instructions: quoted and backticked
    text, and the contents the write lane's own parser reads ("containing ...", "Put ONE, TWO")."""
    spans: list[tuple[int, int]] = []
    closing: str | None = None
    opened = -1
    for index, char in enumerate(value):
        if closing is not None:
            if char == closing:
                spans.append((opened + 1, index))
                closing = None
            continue
        if char == "`":
            closing, opened = "`", index
            continue
        if _opens_a_quote(value, index, char):
            closing, opened = _QUOTE_PAIRS[char], index
    try:
        from core.execution.write_demand import resolve_write_demand

        demand = resolve_write_demand(value)
        # Only a LITERAL write names content to copy; a brief ("write a limerick about copper")
        # describes prose to compose and marks nothing literal.
        literal = demand if demand is not None and getattr(demand, "is_literal", False) else None
        for item in getattr(literal, "items", ()) or () if literal is not None else ():
            content = str(getattr(item, "content", "") or "").strip()
            if not content:
                continue
            at = value.find(content)
            if at < 0:
                at = value.casefold().find(content.casefold())
            if at >= 0:
                spans.append((at, at + len(content)))
    except Exception:
        pass
    return tuple(spans)


def _inside_literal(start: int, end: int, spans: tuple[tuple[int, int], ...]) -> bool:
    width = max(1, end - start)
    covered = 0
    for lo, hi in spans:
        overlap = min(end, hi) - max(start, lo)
        if overlap > 0:
            covered += overlap
    return covered / width >= 0.8


_QUESTION_HEAD_RE = re.compile(r"^(?:what|which|where|when|who|whom|whose|why|how)\b", re.IGNORECASE)


def _is_contextual_question(text: str) -> bool:
    # Complement clauses have already been joined to their governing request by
    # the splitter. A remaining standalone question is work even without a named
    # object or auxiliary verb ("Which one moved most?").
    return bool(_QUESTION_HEAD_RE.match(_strip_leaders(text).strip()))


def _is_output_shape_unit(text: str) -> bool:
    """A fragment that says HOW to answer, not what about ("show the math", "just the number")."""
    lowered = " ".join(str(text or "").casefold().split())
    if _is_contextual_question(lowered):
        return False
    if _PRESENTATION_INSTRUCTION_RE.match(lowered):
        return True
    tokens = _unit_tokens(text)
    if not tokens or any(token[0].isdigit() for token in tokens):
        return False
    return all(
        token in _CONVERSATIONAL_TOKENS
        or token in _CONTENT_STOP_TOKENS
        or token in _META_OUTPUT_TOKENS
        or _is_selector_or_its_typo(token)
        for token in tokens
    )


def _strip_leaders(text: str) -> str:
    """The fragment without its leading connectors ("and oil prices pls" -> "oil prices pls")."""
    body = str(text or "")
    while True:
        match = re.match(r"^\s*(?:and|also|plus|then|next|finally|afterwards|but|so|ok|okay|now|just|please)\b[\s,]*", body, re.IGNORECASE)
        if not match or match.end() >= len(body):
            return body
        body = body[match.end():]


#: A wh-word followed (within two words) by a finite verb is a QUESTION: "what is the gold
#: price", "how much gold can I buy". Without one it is an embedded clause: "what it imports
#: first", "how it gets loaded", "where the config lives".
_WH_QUESTION_RE = re.compile(
    r"^(?:what|which|where|when|who|whom|whose|why|how(?:\s+much|\s+many|\s+long|\s+far|\s+old)?)\s+"
    r"(?:\w+\s+){0,2}?(?:is|are|was|were|am|do|does|did|can|could|should|would|will|shall|may|might|has|have|had)\b",
    re.IGNORECASE,
)


def _is_embedded_wh_clause(text: str) -> bool:
    body = " ".join(_leader_split(_unit_tokens(text))[1])
    return bool(body) and not _WH_QUESTION_RE.match(body)


def _head_takes_a_wh_complement(request_text: str) -> bool:
    """The request before it is a walkthrough/explanation whose object is a wh-clause ("walk me
    through where ...", "tell me how ...", "show me what ...", "explain why ..."), not itself a
    wh-question ("what is 1500 EUR in USD")."""
    tokens = _leader_split(_unit_tokens(request_text))[1]
    if not tokens or tokens[0] in _WH_WORDS:
        return False
    return any(token in _WH_WORDS for token in tokens[1:])


def _contains_embedded_request(text: str) -> bool:
    """A fragment the splitter kept whole (a quotation, a colon) may carry its request AFTER the
    quoted material: 'A traveler says: "..." Explain whether the exchange was good'. Any
    sentence-initial piece inside it that opens a demand makes the fragment a request.

    A colon under a DECLARATIVE opener introduces what the statement describes, not a request:
    "an admin should be able to: - add or remove points - restart selected bot services" lists the
    proposed system's features, and "add" there is a described capability. Measured 2026-09-16
    (candidate 035dea9b, whitespace-collapsed by the requirements authority): the described list
    read as a request and its "recent errors" item made the turn current-information-required."""
    value = str(text or "")
    pieces = re.split(r'(?<=["\u201d])\s+', value)
    if any(_opens_a_demand(piece) for piece in pieces[1:] if piece.strip()):
        return True
    head, colon, tail = value.partition(":")
    if not colon or not tail.strip():
        return False
    _leaders, body = _leader_split(_unit_tokens(head))
    if _subject_precedes_its_verb(body):
        return False
    return any(_opens_a_demand(piece) for piece in re.split(r":\s+", tail) if piece.strip())


_NUMBER_WORDS = frozenset(
    {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
     "first", "second", "third", "fourth", "fifth", "last", "none"}
)


def _is_value_shaped(text: str, body: tuple[str, ...] | list[str]) -> bool:
    """A comma-led fragment that is a VALUE of the list before it: a file token, a number or
    number word, a proper name (every word capitalized), or a single token."""
    raw = str(text or "").strip().rstrip(".,;:")
    if re.search(r"\b[\w./-]+\.[A-Za-z0-9]{1,6}\b", raw):
        return True
    if all(token.isdigit() or token in _NUMBER_WORDS for token in body):
        return True
    words = [word for word in re.findall(r"[A-Za-z][\w'-]*", raw)]
    if len(words) <= 1:
        return True
    return all(word[0].isupper() for word in words)


def _registered_lane_admits(text: str) -> bool:
    try:
        from core.agent_runtime.demand_ownership import a_registered_lane_claims

        return bool(a_registered_lane_claims(_strip_leaders(text)))
    except Exception:
        return False


def _leader_split(tokens: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lead = 0
    while lead < len(tokens) and (
        tokens[lead] in _DEMAND_UNIT_SPLIT_CONNECTORS
        or tokens[lead] in _SPLIT_LEADERS
        or tokens[lead] in _SEQUENCE_LEADERS
    ):
        lead += 1
    return tokens[:lead], tokens[lead:]


_LOCATIVE_LEADER_RE = re.compile(
    r"^\s*(?:inside|in|within|under|there|from\s+there)\s+(?:it|that|this|there|the\s+\w+|\w+)\s*,?\s+",
    re.IGNORECASE,
)


def imperative_step(text: str) -> bool:
    """Whether a fragment opens with an instruction step in imperative position -- after its
    connectors ("then", "and") and after a locative lead-in that places the step ("Inside it
    create notes.txt", "In that folder add a README")."""
    body_text = str(text or "")
    placed = _LOCATIVE_LEADER_RE.sub("", body_text, count=1)
    _leaders, body = _leader_split(_unit_tokens(placed if placed.strip() else body_text))
    return bool(body) and (body[0] in _IMPERATIVE_STEP_VERBS or body[0] in _EFFECT_STEP_VERBS)


def _classify_fragments(value: str, fragments: list[tuple[str, int, int, str]]) -> list[DemandUnit]:
    literal_spans = _literal_spans(value)
    units: list[DemandUnit] = []
    last_request: str | None = None
    pending: list[int] = []  # non-request fragments before the first request, attached to it once minted
    #: The kind of the fragment that introduced the current list with a ":". A list under a
    #: STATEMENT ("an admin should be able to:") enumerates what the statement describes -- its
    #: items are the proposed system's features, not instructions to the runtime, whatever verb
    #: they open with. A list under a REQUEST ("Do the following:") keeps its steps.
    colon_owner: str | None = None
    for index, (text, start, end, slice_id) in enumerate(fragments):
        tokens = _unit_tokens(text)
        leaders, body = _leader_split(tokens)
        lowered = " ".join(text.casefold().split())
        asks = lowered.rstrip().endswith("?") or _opens_a_demand(text) or _contains_embedded_request(text)
        previous_text = fragments[index - 1][0] if index else ""
        after_colon_list = previous_text.rstrip().endswith(":") or (
            index and units and units[-1].kind == KIND_ENUMERATION
        )
        comma_led = bool(index) and value[fragments[index - 1][2] : start].strip(" ").startswith(",")
        clarifies = bool(index) and last_request is not None and bool(_CLARIFICATION_RE.match(text)) and not lowered.rstrip().endswith("?")
        bulleted = bool(_LIST_ITEM_RE.match(text))
        follows_item = bool(index) and bool(units) and units[-1].kind == KIND_ENUMERATION
        described_item = (
            colon_owner == KIND_CONTEXT
            and not lowered.rstrip().endswith("?")
            and (
                (bulleted and (previous_text.rstrip().endswith(":") or follows_item))
                # "- view bot status" + "and recent errors": the cut tail of a described item.
                or (not bulleted and follows_item and bool(leaders))
            )
        )
        if not described_item:
            colon_owner = None
        if any(lo <= start and end <= hi for lo, hi in literal_spans) or (
            _inside_literal(start, end, literal_spans) and not asks
        ):
            # A question wholly inside explicitly supplied file contents is still data.
            # Keep the looser overlap rule guarded so the surrounding write remains a request.
            kind = KIND_LITERAL
        elif clarifies:
            kind = KIND_CONTEXT
        elif described_item:
            kind = KIND_ENUMERATION
        elif (
            index
            and leaders
            and body
            and body[0] in _PRESENTATION_VERBS
            and last_request is not None
            and _PRESENTATION_MARKER_RE.search(text)
        ):
            # "explain how a trie works AND MENTION trie.py AS AN EXAMPLE": an instruction about how
            # to present the answer before it, not a demand of its own. A constraint rides. Without
            # a presentation marker ("also include enthalpy") the fragment adds a subject and stays
            # a request.
            kind = KIND_CONSTRAINT
        elif (
            index
            and leaders
            and body
            and body[0] in _WH_WORDS
            and last_request is not None
            and _is_embedded_wh_clause(text)
            and _head_takes_a_wh_complement(units[[u.unit_id for u in units].index(last_request)].text)
        ):
            # A coordinated wh-COMPLEMENT of the request before it: "walk me through where this
            # repo starts AND WHAT IT IMPORTS FIRST" asks one walkthrough with two parts, not two
            # walkthroughs. A coordinated wh-QUESTION ("and what is the gold price") is a request
            # of its own and never folds. Kept as an enumeration so the request's execution text
            # carries both parts.
            kind = KIND_ENUMERATION
        elif is_prohibition_unit(text) or _CONSTRAINT_OPENER_RE.match(lowered) or _is_output_shape_unit(text):
            kind = KIND_CONSTRAINT
        elif asks or imperative_step(text) or _registered_lane_admits(text):
            kind = KIND_REQUEST
        elif body and _reads_as_a_statement(body) and not (index and after_colon_list and len(body) <= 3):
            # A sentence about a situation, not a thing asked for: "my pc is super slow lately",
            # "i think about my best friend a lot". It is the context of the request beside it.
            kind = KIND_CONTEXT
        elif index and leaders and body and body[0] in _CONTINUATION_PREPOSITIONS_MINT and last_request is not None:
            # An elliptical DEPENDENT step ("convert 100 usd to eur and then TO gold"): a request of
            # its own whose operand is the result of the request before it. The dependency is what
            # the executor needs; batching for dispatch happens at the execution grain.
            kind = KIND_REQUEST
        elif (
            index
            and len(body) <= 3
            and not leaders
            and not any(token in _ANAPHORS for token in body)
            and (after_colon_list or (comma_led and _is_value_shaped(text, body)))
        ):
            # A list item that belongs to the request before it: "b.txt", "TWO", "Puerto Rico.",
            # the entries after "out of this mess:". Never a demand of its own. A terse ask is not
            # a value: "gold with it" points back with an anaphor, "Rome weather" is a lowercase
            # noun phrase -- both are requests of their own (measured 2026-09-08 on the four-slot
            # served string and its paraphrases).
            kind = KIND_ENUMERATION
        else:
            # The mint's default, unchanged from the grain before kinds existed: a fragment with
            # content is a thing the user asked for ("brief me on the Sahel", "and rank every
            # option"). Whole-message binders demote what is theirs (a story's narration).
            kind = KIND_REQUEST
        unit_id = f"t{index}"
        if lowered.rstrip().endswith(":"):
            colon_owner = kind
        if kind == KIND_REQUEST:
            anaphoric = any(token in _ANAPHORS for token in body)
            sequenced = any(token in _SEQUENCE_LEADERS for token in leaders) or (
                bool(body) and body[0] in _EFFECT_STEP_VERBS
            )
            depends: tuple[str, ...] = ()
            unresolved: tuple[str, ...] = ()
            elliptical = bool(leaders) and bool(body) and body[0] in _CONTINUATION_PREPOSITIONS_MINT
            if last_request is not None and (anaphoric or sequenced or elliptical):
                depends = (last_request,)
            elif anaphoric:
                if units:
                    depends = (units[-1].unit_id,)  # the statement before it is the antecedent
                else:
                    unresolved = tuple(dict.fromkeys(token for token in body if token in _ANAPHORS))
            unit = DemandUnit(
                unit_id=unit_id, slice_id=slice_id, index=index, text=text, start=start, end=end,
                freshness=_unit_freshness_cue(text), kind=KIND_REQUEST, depends_on=depends,
                unresolved_refs=unresolved,
            )
            for position in pending:
                units[position] = dataclasses.replace(units[position], depends_on=(unit_id,))
            pending = []
            units.append(unit)
            last_request = unit_id
            continue
        attach: tuple[str, ...] = (last_request,) if last_request is not None else ()
        slice_initial = index == 0 or fragments[index - 1][3] != slice_id
        if kind == KIND_CONTEXT and slice_initial and index + 1 < len(fragments) and not clarifies:
            # A statement that opens a sentence introduces the request after it ("A traveler says:
            # ... Explain whether the exchange was good") -- it attaches FORWARD, and falls back to
            # the request before it only when no request follows.
            attach = ()
        unit = DemandUnit(
            unit_id=unit_id, slice_id=slice_id, index=index, text=text, start=start, end=end,
            polarity="constraint" if kind == KIND_CONSTRAINT else "request",
            kind=kind, depends_on=attach,
            literal_spans=((start, end),) if kind == KIND_LITERAL else (),
        )
        if not attach:
            pending.append(len(units))
        units.append(unit)
    return units


def _apply_bindings(value: str, units: list[DemandUnit]) -> list[DemandUnit]:
    """The registry's whole-message binders decide the request grain inside a text a lane admits
    whole: promoted steps become requests chained in order; demoted narration becomes context."""
    try:
        from core.agent_runtime.demand_ownership import whole_message_bindings

        overrides = dict(whole_message_bindings(value, tuple(units)) or {})
    except Exception:
        overrides = {}
    if not overrides:
        return units
    out = list(units)
    previous_request: str | None = None
    previous_step: str | None = None
    for position, unit in enumerate(out):
        wanted = overrides.get(unit.unit_id)
        if wanted == "step":
            # A chained step: a request that depends on the step bound before it (a workflow's
            # run -> replace -> retry; an inspection's check -> see -> suggest).
            depends = unit.depends_on or ((previous_step,) if previous_step is not None else ())
            out[position] = dataclasses.replace(
                unit, kind=KIND_REQUEST, polarity="request", depends_on=depends, literal_spans=()
            )
            previous_step = unit.unit_id
            previous_request = unit.unit_id
            continue
        if wanted == KIND_REQUEST and unit.kind != KIND_REQUEST:
            depends = (previous_request,) if previous_request is not None else ()
            out[position] = dataclasses.replace(
                unit, kind=KIND_REQUEST, polarity="request", depends_on=depends, literal_spans=()
            )
        elif wanted is not None and wanted != KIND_REQUEST and unit.kind == KIND_REQUEST:
            attach = (previous_request,) if previous_request is not None else ()
            if not attach:
                # Nothing precedes it: like a statement that opens a message, the demoted opener
                # and everything that hung on it introduce the request AFTER them.
                following = next(
                    (
                        other.unit_id
                        for other in out[position + 1 :]
                        if other.kind == KIND_REQUEST
                        and overrides.get(other.unit_id) in (None, KIND_REQUEST, "step")
                    ),
                    None,
                )
                attach = (following,) if following is not None else ()
            out[position] = dataclasses.replace(unit, kind=wanted, depends_on=attach)
            # everything that hung on the demoted request moves to the request before it
            for other_position, other in enumerate(out):
                if other.kind != KIND_REQUEST and unit.unit_id in other.depends_on:
                    out[other_position] = dataclasses.replace(other, depends_on=attach)
            continue
        if out[position].kind == KIND_REQUEST:
            previous_request = out[position].unit_id
    return out


def _promote_a_lone_fragment(units: list[DemandUnit]) -> list[DemandUnit]:
    """A message with content mints at least one request. A lone statement ("wat is 18^2", "my pc is
    slow"), a lone constraint ("reply with just the number") or a lone list item has nothing to be
    context OF; it is the request. Measured 2026-09-08: "wat is 18^2" read as context of nothing,
    minted no demand obligation, and the closure certificate lost its census."""
    if not units or any(unit.kind == KIND_REQUEST for unit in units):
        return units
    for position, unit in enumerate(units):
        if unit.kind == KIND_LITERAL:
            continue
        if unit.kind == KIND_CONSTRAINT and _PROHIBITION_RE.match(unit.text):
            # A lone prohibition ("do not send any money") asks for nothing to be done; it stays
            # the constraint it is (gauntlet pc-prohibition). A lone instruction about the reply's
            # FORM ("reply with just the number") is answered, so it is the request.
            continue
        promoted = dataclasses.replace(unit, kind=KIND_REQUEST, polarity="request", depends_on=(), literal_spans=())
        out = list(units)
        out[position] = promoted
        # everything else attaches to the request it now has
        for other_position, other in enumerate(out):
            if other_position != position and other.kind != KIND_REQUEST:
                out[other_position] = dataclasses.replace(other, depends_on=(promoted.unit_id,))
        return out
    return units


def _finalize_ids(units: list[DemandUnit]) -> tuple[DemandUnit, ...]:
    """Requests are u1..uN in order (the obligation set's ids); other kinds carry a kind prefix.
    A request that restates an earlier one (same anchors) is dropped with what hung on it."""
    units = _promote_a_lone_fragment(list(units))
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    kept: list[DemandUnit] = []
    seen_anchors: list[tuple[tuple[str, ...], str]] = []
    dropped: set[str] = set()
    for unit in units:
        if unit.kind == KIND_REQUEST:
            anchors = unit_anchors(unit.text)
            leaders, body = _leader_split(_unit_tokens(unit.text))
            # The head is the first token that says what is asked; an echo word ("again", "also",
            # "once more") in front of it is not a different head.
            head = next((token for token in body if token not in _ANAPHORS and token not in {"also", "once", "more"}), "")
            # A step named in SEQUENCE ("and then run app.py") is never a restatement; an anaphoric
            # echo ("and again 100 EUR to USD") is exactly one, so a dependency alone does not
            # exempt a unit -- only a sequence word does.
            sequenced = any(token in _SEQUENCE_LEADERS for token in leaders)
            # A RESTATEMENT shares the earlier request's anchors AND its head ("what is TRY? what
            # is TRY"). "create app.py and then run app.py" shares the file and nothing else: two
            # steps of one job, never one demand said twice.
            if anchors and not sequenced and any(
                anchors == seen and (head == seen_head or not head) for seen, seen_head in seen_anchors
            ):
                dropped.add(unit.unit_id)
                continue
            if anchors:
                seen_anchors.append((anchors, head))
        prefix = "u" if unit.kind == KIND_REQUEST else _KIND_PREFIX.get(unit.kind, "f")
        counters[prefix] = counters.get(prefix, 0) + 1
        mapping[unit.unit_id] = f"{prefix}{counters[prefix]}"
        kept.append(unit)
    final: list[DemandUnit] = []
    for position, unit in enumerate(kept):
        if unit.kind != KIND_REQUEST and unit.depends_on and all(dep in dropped for dep in unit.depends_on):
            continue
        depends = tuple(mapping[dep] for dep in unit.depends_on if dep in mapping)
        final.append(dataclasses.replace(unit, unit_id=mapping[unit.unit_id], index=position, depends_on=depends))
    requests = [unit for unit in final if unit.kind == KIND_REQUEST]
    for number, unit in enumerate(requests):
        if unit.index != number:
            pass
    return tuple(final)


_IN_PROGRESS: ContextVar[dict[str, tuple[DemandUnit, ...]] | None] = ContextVar(
    "answer_coverage_interpretation_in_progress", default=None
)


#: Depth of interpretations in progress on this thread -- classification AND binding. The
#: classification asks the registered lanes whether they admit a fragment
#: (`_registered_lane_admits`), and a lane's probe may consult a reader that itself reads the
#: interpretation (`core.execution_requirements.asked_text`, `_slice_asks_nothing`). Those readers
#: fail OPEN (the whole text, the probe as before) while any interpretation is in progress, so a
#: reading can never start another reading of its own fragments. Measured 2026-09-16: without
#: it, "100 EUR to USD at 1.10. Also 500 GBP to JPY at 190.5." recursed through the workspace
#: and operator probes to RecursionError, the swallowed failure was memoized by `slice_families`,
#: and the currency lane lost its first clause for the rest of the process.
_INTERPRETING: ContextVar[int] = ContextVar("answer_coverage_interpreting_depth", default=0)


def interpretation_in_progress() -> bool:
    """Whether this thread is inside `interpret_request` right now (see `_INTERPRETING`)."""
    return _INTERPRETING.get() > 0


def _interpret_uncached(text: str) -> RequestInterpretation:
    value = str(text or "")
    depth_token = _INTERPRETING.set(_INTERPRETING.get() + 1)
    try:
        units = _classify_fragments(value, _raw_fragments(value))
        heuristic = _finalize_ids(units)
        guard = dict(_IN_PROGRESS.get() or {})
        guard[value] = tuple(unit for unit in heuristic if unit.kind == KIND_REQUEST)
        token = _IN_PROGRESS.set(guard)
        try:
            bound = _apply_bindings(value, list(heuristic))
        finally:
            _IN_PROGRESS.reset(token)
    finally:
        _INTERPRETING.reset(depth_token)
    if bound != list(heuristic):
        return RequestInterpretation(text=value, units=_finalize_ids(bound))
    return RequestInterpretation(text=value, units=heuristic)


def _catalog_key() -> tuple[str, ...]:
    """The active lane catalog's identity. The reading consults the registry (admissions,
    binders), so a cached interpretation is only valid for the catalog it was read under -- a
    test or a runtime that toggles lanes must not be served another catalog's reading."""
    try:
        from core.lane_registry import active_catalog

        return tuple(spec.lane_id for spec in active_catalog())
    except Exception:
        return ()


@lru_cache(maxsize=256)
def _interpret_cached(text: str, catalog: tuple[str, ...]) -> RequestInterpretation:
    return _interpret_uncached(text)


def interpret_request(text: str) -> RequestInterpretation:
    """ONE reading of the message: every fragment with kind, attachment and dependencies."""
    return _interpret_cached(str(text or ""), _catalog_key())


interpret_request.cache_clear = _interpret_cached.cache_clear  # type: ignore[attr-defined]


def demand_units(text: str) -> tuple[DemandUnit, ...]:
    """The turn's REQUESTED-SLOT SET: one unit per thing the user asked for -- the request-kind
    view of `interpret_request`. Minted from the request itself at intake, before routing, and
    never from family recognition alone. While a whole-message binder is reading THIS text, the
    heuristic requests are returned so a binder cannot recurse into its own interpretation."""
    value = str(text or "")
    in_progress = _IN_PROGRESS.get()
    if in_progress is not None and value in in_progress:
        return in_progress[value]
    return interpret_request(value).requests


demand_units.cache_clear = interpret_request.cache_clear  # type: ignore[attr-defined]


def _mint_units(value: str) -> list[DemandUnit]:
    """Compatibility: the request units, as a list."""
    return list(demand_units(value))


# --------------------------------------------------------------------------------------------
# Work of its own beside words set aside
# --------------------------------------------------------------------------------------------

#: Words that open a noun phrase or a prepositional phrase. A fragment that opens with one names a thing or a place, not
#: an instruction: after "continue", "the research on oil prices" is what to continue, and after "carry on", "in the
#: Work folder" says where -- even when a lane would read those words as its own request.
_PHRASE_OPENERS = frozenset(
    {"the", "a", "an", "my", "your", "our", "their", "his", "her", "its", "this", "that", "these", "those"}
) | _CONTINUATION_PREPOSITIONS_MINT

#: Demand heads that ask only as a question. Without a question mark, an auxiliary or a wh-word opens a statement or an
#: embedded clause ("where you stopped", "if you can"); such a fragment asks for work only when a lane reads it as work.
_QUESTION_ONLY_HEADS = _WH_WORDS | {
    "whats", "is", "are", "was", "were", "am", "do", "does", "did", "can", "could", "will", "would", "should", "may",
    "might", "has", "have", "had",
}


def _opens_work(body: tuple[str, ...], fragment: str) -> bool:
    """Whether a fragment (`body`: its tokens after connectors and leaders) opens an instruction or a question of its own.

    A question mark, an instruction step ("delete", "rename", "move"), a demand head that is a verb ("show", "tell",
    "search", "convert"), or -- for a verb neither list names ("schedule", "clean") -- a lane that reads the fragment as
    its work, unless the fragment opens a noun or prepositional phrase. An infinitive keeps its verb: in "proceed to
    delete my Apple note", "to delete" opens the instruction.
    """
    if body[:1] == ("to",) and len(body) > 1 and body[1] not in _PHRASE_OPENERS:
        body = body[1:]
    if not body:
        return False
    if " ".join(str(fragment or "").split()).endswith("?"):
        return True
    opener = body[0]
    if opener in _IMPERATIVE_STEP_VERBS or opener in _EFFECT_STEP_VERBS:
        return True
    if opener in _PHRASE_OPENERS:
        return False
    if _is_demand_head(opener) and opener not in _QUESTION_ONLY_HEADS:
        return True
    return _registered_lane_admits(fragment)


def _names_an_operand_of_its_own(body: tuple[str, ...]) -> bool:
    """Whether the words after a fragment's opener name something to act on beyond verbs, function words and words that
    point back.

    'delete my Apple note "Groceries"' names the note; 'delete it', 'send it again' and 'show me more' name nothing of
    their own and lean on the turn before them. Every word class here is this module's own reading.
    """
    if body[:1] == ("to",):
        body = body[1:]
    return any(
        not (
            token in _ANAPHORS
            or token in _CONVERSATIONAL_TOKENS
            or token in _CONTENT_STOP_TOKENS
            or token in _META_OUTPUT_TOKENS
            or token in _PHRASE_OPENERS
            or token in _SPLIT_LEADERS
            or token in _SEQUENCE_LEADERS
            or token in _DEMAND_UNIT_SPLIT_CONNECTORS
            or token in _SEQUENCE_CONNECTORS
            or token in _IMPERATIVE_STEP_VERBS
            or token in _EFFECT_STEP_VERBS
            or token in _DEMAND_HEADS
            or _is_selector_or_its_typo(token)
        )
        for token in body[1:]
    )


def asks_for_work_of_its_own(text: str, *, set_aside: tuple[tuple[int, int], ...] = ()) -> bool:
    """Whether `text`, apart from the character spans `set_aside`, asks for work of its own.

    The text is cut and classified exactly as `interpret_request` cuts and classifies it, over the whole text, so a
    prohibition keeps its verb ("don't go ahead and delete ...") and quoted material stays a literal. A fragment minted as
    a request asks for work of its own when, with the set-aside spans blanked, it opens an instruction or a question
    (`_opens_work`) and names an operand of its own (`_names_an_operand_of_its_own`).

    The mint's default -- a fragment with content is a request -- is deliberately not evidence here: "with the Q3 plan"
    and "the research on oil prices" are requests to the closure certificate and nothing to run on their own. The caller
    that sets words aside is `core.agent_runtime.proceed_intent_support.proceed_carries_its_own_request`: a follow-up's
    go-ahead words, so what is left says whether the follow-up brought a request of its own.
    """
    value = str(text or "")
    chars = list(value)
    for start, end in set_aside:
        chars[max(start, 0):max(end, 0)] = " " * (max(end, 0) - max(start, 0))
    rest = "".join(chars)
    for unit in _classify_fragments(value, _raw_fragments(value)):
        if unit.kind != KIND_REQUEST:
            continue
        fragment = rest[unit.start:unit.end]
        _leaders, body = _leader_split(_unit_tokens(fragment))
        if _opens_work(body, fragment) and _names_an_operand_of_its_own(body):
            return True
    return False


def _unit_freshness_cue(unit_text: str) -> str:
    """The recency cue the unit ITSELF states, from the marker set the live-info
    mode classifier already owns (`_FRESH_LOOKUP_MARKERS`) -- the existing
    freshness authority, read at unit grain; no new vocabulary, no inference
    (a unit with no cue is "", never a guess)."""
    from core.agent_runtime.fast_live_info_mode_markers import _FRESH_LOOKUP_MARKERS

    lowered = f" {str(unit_text or '').lower()} "
    for marker in _FRESH_LOOKUP_MARKERS:
        if f" {marker}" in lowered and (
            marker.startswith(("latest", "current", "price", "right"))
        ):
            return str(marker)
    return ""


def prohibited_clauses(text: str) -> tuple[str, ...]:
    """The PROHIBITION clauses of the message, preserved -- never vanished.

    The mint drops them by law (a prohibition can never be an obligation, so
    minting one would create an unfulfillable slot -- RED-1 NEW-3); this helper
    is the preservation half of the polarity contract: the clauses are named,
    so a consumer can see WHY they minted nothing."""
    value = str(text or "")
    kept: list[str] = []
    for item in turn_slices(value):
        for _start, _end in _unit_spans(item.text):
            body = item.text[_start:_end].strip().strip(",")
            if body and is_prohibition_unit(body):
                kept.append(body)
    return tuple(dict.fromkeys(kept))


def unit_families(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``((unit_id, (family, ...)), ...)`` -- the same probe reading, at demand-unit grain."""
    return tuple((unit.unit_id, _families_claiming(unit.text)) for unit in demand_units(text))


def units_with_registered_demand(text: str) -> tuple[str, ...]:
    """Demand units at least one REGISTERED family reads -- positive evidence of a distinct ask.

    This is the render gate, and it is deliberately positive: an unanswered unit becomes a
    user-visible "could not be answered" row only when a registered family reads it. Keying that
    row on ABSENCE of a claim is exactly the reverted bare-unclaimed-slice guard that rerouted 114
    frozen closed-contract flows (decision record 2026-08-29-class-A-guard-narrowing) -- ordinary
    politeness and multi-sentence single-domain elaboration are ownerless by design, so absence
    can never be allowed to drive user-visible behaviour.
    """
    return tuple(unit_id for unit_id, families in unit_families(text) if families)


def units_in_slices(text: str, slice_ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """The demand units that live inside the named clauses -- the slice->unit half of the map."""
    wanted = {str(slice_id) for slice_id in (slice_ids or ())}
    return tuple(unit.unit_id for unit in demand_units(text) if unit.slice_id in wanted)


def units_matching_needle(text: str, *needles: str) -> tuple[str, ...]:
    """The demand units whose own text contains a needle, at word boundaries.

    The unit-grain half of the span binding `live_data_plan._interim_slice_id` performs
    at clause grain. Clause grain is COARSER than the demand set (units sub-split a
    clause at coordination boundaries but inherit the clause's slice_id), so a receipt
    mapped clause->units absolves every co-clause sibling of the unit it actually
    served — measured live 2026-08-30 (Incident 3): one London weather receipt
    discharged the diesel, petrol and Riga-Vilnius demands sharing its clause. This
    binds the needle to the units whose OWN span contains it: the receipt then names
    the unit it served, and only that unit.

    Same contract as `_interim_slice_id`: needles are normalized (lowercase, collapsed
    whitespace), longest first; no match returns () — never guess a span.
    """
    ordered: list[str] = []
    for needle in needles:
        token = " ".join(str(needle or "").lower().split())
        if token and token not in ordered:
            ordered.append(token)
    if not ordered:
        return ()
    units = demand_units(str(text or ""))
    if not units:
        return ()
    matched: list[str] = []
    for needle in sorted(ordered, key=len, reverse=True):
        pattern = re.compile(
            r"\b" + r"\s+".join(re.escape(part) for part in needle.split(" ")) + r"\b"
        )
        for unit in units:
            if pattern.search(unit.text.lower()) and unit.unit_id not in matched:
                matched.append(unit.unit_id)
    return tuple(matched)


#: Headers under which the runtime names work it did NOT do. Everything from the earliest one
#: onward is disclosure, not answer.
#: The colon is required: it is what makes the phrase a HEADER introducing rows rather than a
#: sentence about one. Without it, a turn whose whole text is the phrase cut its own answer to
#: nothing.
_UNSERVED_DISCLOSURE_HEADERS = (
    "could not be answered:",
    "not answered:",
    "unavailable:",
)


def answering_body(answer: str) -> str:
    """The part of a served answer that ANSWERS, with the unserved disclosure removed.

    MEASURED LIVE 2026-08-29 over HTTP, and the reason this exists. The four-slot prompt served
    a body whose own "Could not be answered:" section named the gold slot and the Baltic slot --
    and the certificate reported `demand_satisfied: 4, demand_unanswered: 0`, because the
    consumption reading found each unit's words in the served bytes and could not tell an answer
    from a refusal that quotes the question back. A refusal naming a slot is the strongest
    possible evidence that the slot was NOT served; crediting it inverted the meaning of the
    count and violated acceptance criterion 8 in the opposite direction.
    """
    value = str(answer or "")
    offset = 0
    for line in value.splitlines(keepends=True):
        stripped = line.lstrip(" \t-*\u2022").lower()
        # The header only counts when it OPENS a line. A model asked what the phrase means
        # answers by quoting it -- `"Could not be answered:" in a status report means ...` --
        # and cutting there left an empty answering body, so the runtime denied its own
        # correct answer (RED-1 NEW-5). A quotation mark before the header is the difference
        # between talking about a disclosure and making one.
        if any(stripped.startswith(header) for header in _UNSERVED_DISCLOSURE_HEADERS):
            return value[:offset]
        offset += len(line)
    return value


def units_without_own_object(text: str) -> tuple[str, ...]:
    """Units that name no thing to look up -- they shape an answer the turn is already giving.

    "Give me a verdict." after "Audit this project." asks for a FORM, not a fact; so does
    "just the number" or "keep it short". Such a unit rides along with the turn it elaborates:
    it is discharged when anything in the turn is, and can never become an unavailable row.
    This is what keeps the suite's sharpest control -- three sentences, one domain -- from
    telling its user that two thirds of the request went unanswered.
    """
    riders: list[str] = []
    for unit in demand_units(text):
        if _is_contextual_question(unit.text):
            continue
        tokens = _unit_tokens(unit.text)
        if not tokens:
            continue
        if any(token[0].isdigit() for token in tokens):
            continue
        if all(
            token in _CONVERSATIONAL_TOKENS
            or token in _CONTENT_STOP_TOKENS
            or token in _META_OUTPUT_TOKENS
            or _is_selector_or_its_typo(token)
            for token in tokens
        ):
            riders.append(unit.unit_id)
    return tuple(riders)


#: Evidence verdicts for one demand unit against the served answer.
DEMAND_SATISFIED = "satisfied"
DEMAND_UNANSWERED = "unanswered"
#: Some of the unit's anchors are in the answer and some are not. The runtime cannot show it
#: answered the unit and cannot show it did not: it makes NO claim, renders NO row, and the
#: certificate does not count it as covered.
DEMAND_INDETERMINATE = "indeterminate"

_ANCHOR_MIN_LEN = 4
#: Case-INSENSITIVE, because an operator types `100 eur to gbp` as readily as `100 EUR to
#: GBP` and a slot that anchors differently by capitalisation is a slot the runtime can lose
#: to the shift key. Matching alone is not enough to make a token a code -- `to`, `is` and
#: `the` all fit the shape -- so the branch that consumes this is gated on the same stop-token
#: sets the content-word branch uses. The gate is not optional: without it the ladder starts
#: demanding that answers echo function words.
_CODE_RE = re.compile(r"^[A-Za-z]{2,5}$")


def _echo_carries_an_object(unit: Any, anchors: tuple[str, ...], body: str) -> bool:
    """Whether the region echoing this unit's anchors supplies anything the unit did not.

    THE RESTATEMENT HOLE (measured live): a turn that answered one slot and refused the other
    in prose -- "The water temperature in the Baltic Sea is not available." -- graded BOTH
    slots `satisfied`. The refusal names the slot, so every anchor is present, and a lexical
    ladder read the runtime's own words coming back as evidence it had answered.

    This is the law the module already applies to REQUESTS (`units_without_own_object`: a unit
    naming no thing to look up rides along), turned to face the answer. An echo that adds no
    object -- no number, no content word the unit did not already contain -- is the question
    restated, and a restatement is not an answer to it.

    An OBJECT is a value or a named thing: a number, or a capitalised token the question did
    not itself supply. That is a test on the token's own SHAPE, not on a vocabulary -- there
    is no list of refusal phrases here and none would help, because a denial can be worded any
    way at all. What a denial cannot do is carry the thing that was asked for. "…is 17.2 °C
    (source: open-meteo.com (marine))" carries a number; "…is not available" carries no value
    and names nothing new, however it is phrased.

    An ordinary lowercase content word is deliberately NOT an object: "available",
    "unavailable", "unknown" and "missing" are all content words by length, and admitting them
    would make this law agree with exactly the sentences it exists to catch.
    """
    own = set(_unit_tokens(unit.text))
    wanted = set(anchors)
    echoing = False
    for line in str(body or "").splitlines():
        tokens = _unit_tokens(line)
        if not tokens:
            continue
        present = set(tokens) | {t.replace(",", "").replace(".", "") for t in tokens}
        if not wanted.issubset(present):
            continue
        echoing = True
        for index, match in enumerate(_UNIT_TOKEN_RE.finditer(_normalise_digits(line))):
            token = match.group(0)
            lowered = token.lower()
            if lowered in own or lowered in wanted:
                continue
            if token[0].isdigit():
                return True  # a value
            if (
                token[0].isupper()
                and index > 0
                and lowered not in _CONVERSATIONAL_TOKENS
                and lowered not in _CONTENT_STOP_TOKENS
                and lowered not in _META_OUTPUT_TOKENS
                and lowered not in _DEMAND_HEADS
            ):
                return True  # a named thing
    # No single line carries every anchor: the echo is spread across the answer, and a
    # per-line reading cannot adjudicate that. Keep the long-standing behaviour rather than
    # inventing an accusation out of a layout difference.
    return not echoing



def _is_stop_token_or_its_typo(token: str) -> bool:
    """A function/social/shaping word, or one typo of one -- never a content anchor.

    The operator types the way people type, and this module already tolerates that for
    SELECTORS (`_is_selector_or_its_typo`) and for demand HEADS (`_near_miss`). The stop-token
    check did not, so a mistyped connective became a content anchor: measured on a real served
    turn, `thne` -- one transposition from `then` -- was minted as an anchor of the water slot.

    That is not a cosmetic imprecision. An anchor is a token a truthful answer would have to
    mention, and no answer about Baltic water temperature contains `thne`. Carrying it made the
    slot unmatchable against its own answer, so the runtime served the reading AND listed the
    slot under "Could not be answered" -- C8's criterion, broken by a typo.

    The tolerance runs ONE WAY and is the module's existing closed budget: an adjacent
    transposition or a single inserted/deleted character, never a substitution -- which is the
    edit that turns ordinary words into stop words. Measured over the whole red_d attack corpus,
    this drops ZERO anchors; the operator's typos of real content words (`tempperature`,
    `baltc`, `berling`) are not near-misses of any stop token and still anchor.
    """
    if (
        token in _CONVERSATIONAL_TOKENS
        or token in _CONTENT_STOP_TOKENS
        or token in _META_OUTPUT_TOKENS
        or token in _DEMAND_HEADS
    ):
        return True
    if len(token) < _ANCHOR_MIN_LEN:
        return False
    return any(
        _near_miss(token, stop)
        for stop in (*_CONVERSATIONAL_TOKENS, *_CONTENT_STOP_TOKENS, *_DEMAND_HEADS)
    )

def unit_anchors(unit_text: str, *, sentence_initial_ok: bool = False) -> tuple[str, ...]:
    """The tokens that identify WHAT this unit asked for.

    An anchor is a token a truthful answer to this unit would have to mention: a number, a
    currency/asset code, a proper noun, or a content word. Request verbs (`tell`, `find`,
    `convert` -- the demand heads), function words, social words and answer-shaping words are
    NOT anchors: they say how to answer, not what about.

    MEASURED LIVE 2026-08-29 (RED-1 NEW-1). The previous reading kept tokens on
    `len >= 3 and not a stop word`, which for `what is 18^2` leaves the EMPTY SET -- so no
    evidence could ever discharge it and the runtime appended `* what is 18^2 -- no answering
    lane claimed this part of the request` underneath its own correct `18² = 324`. A number is
    the most identifying token a request can carry and it was being dropped for being short.
    """
    anchors: list[str] = []
    raw = _normalise_digits(unit_text)
    for index, match in enumerate(_UNIT_TOKEN_RE.finditer(raw)):
        token = match.group(0)
        lowered = token.lower()
        if lowered in anchors:
            continue
        if token[0].isdigit():
            anchors.append(lowered)  # a number is always identifying, however short
            continue
        if _CODE_RE.match(token) and not _is_stop_token_or_its_typo(lowered):
            anchors.append(lowered)  # EUR / USD / BTC / eur / usd / btc
            continue
        if (
            token[0].isupper()
            and (index > 0 or sentence_initial_ok)
            and lowered not in _CONVERSATIONAL_TOKENS
            and lowered not in _DEMAND_HEADS
        ):
            anchors.append(lowered)  # Rome / Baltic / Berlin
            continue
        if len(lowered) < _ANCHOR_MIN_LEN:
            continue
        if _is_stop_token_or_its_typo(lowered):
            continue
        anchors.append(lowered)
    return tuple(anchors)


def _anchor_present(anchor: str, served: frozenset[str]) -> bool:
    """Whether the answer mentions this anchor, allowing an ordinary morphological tail.

    `why did that fail?` is answered with "failed"; `the water temperature` with
    "temperatures". Exact-token matching denied both. Prefix matching is bounded to anchors of
    four characters or more so that short tokens cannot collide.
    """
    if anchor in served:
        return True
    bare = anchor.replace(",", "").replace(".", "")
    if bare in served:
        return True
    if len(anchor) < _ANCHOR_MIN_LEN:
        return False
    return any(
        token.startswith(anchor) or anchor.startswith(token)
        for token in served
        if len(token) >= _ANCHOR_MIN_LEN
    )


def unit_answer_evidence(text: str, answer: str) -> dict[str, str]:
    """What the served answer shows about each demand unit: answered, not answered, or unknown.

    THE RUNG THAT DECIDES DISCLOSURE, and the seam RED-1 attacked from both sides at once:

    * NEW-1 -- a unit whose anchors were all short or stop-words could never be discharged, so
      a correct answer was publicly disowned in the same reply. Fixed by the anchor model
      above and by treating a unit with NO anchors as answered: it names nothing to look up,
      so there is nothing that can have gone missing.
    * NEW-4 -- `and the water temperature in the United States` was discharged because
      `Kanona, United States of America` (a DIFFERENT slot's wrong-location answer) shares the
      tokens `united` and `states`. Fixed by requiring EVERY anchor: a partial match is now
      `indeterminate`, which claims nothing and renders nothing. Incidental collision can no
      longer buy coverage, because coverage now needs the whole of what was asked for.

    Both were one seam read from two sides: absence of evidence was being read as evidence of
    absence, and presence of ANY evidence as evidence of completeness. Neither holds.
    """
    served = frozenset(_unit_tokens(answering_body(answer)))
    served = served | {token.replace(",", "").replace(".", "") for token in served}
    body = answering_body(answer)
    verdicts: dict[str, str] = {}
    for unit in demand_units(text):
        anchors = unit_anchors(unit.text)
        if not anchors:
            # A unit naming nothing gives the ladder nothing to check -- which is a fact
            # about the QUESTION, and cannot be read as evidence about the answer. Measured
            # live: "how hot is the sun" graded `satisfied` against an EMPTY answer, so a
            # slot the runtime never touched discharged itself by being unspecific. L5:
            # satisfaction requires non-empty evidence bound to the slot. `indeterminate`
            # renders nothing and accuses nothing; it just declines to claim.
            verdicts[unit.unit_id] = DEMAND_INDETERMINATE
            continue
        present = [anchor for anchor in anchors if _anchor_present(anchor, served)]
        if len(present) == len(anchors):
            verdicts[unit.unit_id] = (
                DEMAND_SATISFIED
                if _echo_carries_an_object(unit, anchors, body)
                else DEMAND_INDETERMINATE
            )
        elif present:
            verdicts[unit.unit_id] = DEMAND_INDETERMINATE
        else:
            # PHASE A (PLAN-discharge-channel.md §3, 2026-08-30): the text ladder may no longer
            # accuse. Absence of lexical echo is not evidence of absence -- the served answer
            # "Ny: Overcast, 24 C ... Source: wttr.in" never repeats the asking word "weather",
            # and that zero-echo verdict used to render a public refusal over a correct,
            # tool-receipted answer. `indeterminate` renders nothing and asserts nothing; this
            # branch may no longer reach for the accusing verdict. Provable `unanswered` returns
            # only via the dispatch record (Phase B4), never from this ladder.
            verdicts[unit.unit_id] = DEMAND_INDETERMINATE
    return verdicts


def unit_is_disclosed_in(unit_text: str, content: str) -> bool:
    """Whether the served bytes already name this unit -- so a sweep must not name it twice.

    Compared on content tokens rather than raw substrings: the composer writes
    "What is the water temperature in the Baltic Sea?" for a unit the splitter produced as
    "and what is the water temperature in the Baltic Sea?", and a substring test reports those
    as different and renders the row a second time.

    And compared the way the runtime's ONE binding rule compares -- `refused_slot_register`'s
    `anchor_named_in`, tolerating the operator's own spelling within a closed edit budget. The
    record quotes the request VERBATIM while the answer spells things correctly, so an exact
    token test asks the answer to repeat the typo. Measured on a real served turn: the runtime
    answered "Water temperature at Jurmala, Latvia (Baltic Sea): 17.8 C" for a slot recorded as
    "the water tempperature in baltc sea", failed to match it against its own answer, and
    listed the slot under "Could not be answered" -- C8's criterion, broken by two typos.

    EVERY anchor must still be named, which is what keeps this from suppressing a row the
    reader needs: a near-miss on one word does not make an unrelated line count as an answer.
    """
    wanted = list(unit_anchors(unit_text))
    if not wanted:
        return False
    from core.refused_slot_register import anchor_named_in

    for line in str(content or "").splitlines():
        present = frozenset(_unit_tokens(line))
        if all(anchor_named_in(token, present) for token in wanted):
            return True
    return False


def units_present_in_answer(text: str, answer: str) -> tuple[str, ...]:
    """Units whose own content tokens appear in the served answer.

    The consumption evidence available at the one seam every answer passes through, when the
    answering lane recorded no per-slice coverage of its own. Deliberately GENEROUS -- one
    overlapping content token is enough -- because over-granting can only suppress a row, never
    fabricate one, while under-granting can only surface a unit that already carries positive
    rival demand. Numbers are compared with separators stripped, so "1,747.80" answers "1500 eur
    to usd" through its own "1500" and "eur"/"usd" tokens rather than by prose shape.
    """
    served = _unit_tokens(answering_body(answer))
    if not served:
        return ()
    served_set = set(served) | {token.replace(",", "").replace(".", "") for token in served}
    present: list[str] = []
    for unit in demand_units(text):
        for token in _unit_tokens(unit.text):
            if len(token) < 3 or token in _CONTENT_STOP_TOKENS:
                continue
            if token in served_set or token.replace(",", "").replace(".", "") in served_set:
                present.append(unit.unit_id)
                break
    return tuple(present)


def uncovered_slice_texts(source_context: dict[str, object] | None, *, text: str) -> tuple[str, ...]:
    """The clauses no recorded slice answer accounts for -- what still has to be answered."""
    record = (source_context or {}).get(COVERAGE_CONTEXT_KEY)
    if not isinstance(record, dict):
        return tuple(item.text for item in turn_slices(text))
    wanted = {str(slice_id) for slice_id in list(record.get("uncovered") or [])}
    return tuple(item.text for item in turn_slices(text) if item.slice_id in wanted)


__all__ = [
    "CLAIM_SCOPE_SLICE",
    "CLAIM_SCOPE_WHOLE_TURN",
    "COVERAGE_CONTEXT_KEY",
    "DEMAND_INDETERMINATE",
    "DEMAND_SATISFIED",
    "DEMAND_UNANSWERED",
    "FAMILY_ASSISTANT_IDENTITY",
    "FAMILY_CURRENCY",
    "FAMILY_FILE_WRITE",
    "FAMILY_LIVE_INFO",
    "FAMILY_PROJECT_BUILD",
    "FAMILY_RECEIPT_LOCATION",
    "FAMILY_WORKSPACE_AUDIT",
    "ClaimCoverage",
    "DemandUnit",
    "TurnSlice",
    "answering_body",
    "claim_may_preempt_turn",
    "claimed_slice_ids",
    "coverage_for",
    "demand_units",
    "is_conversational_unit",
    "is_mixed_intent_turn",
    "is_prohibition_unit",
    "prohibited_clauses",
    "record_slice_answer",
    "record_slice_contradiction",
    "slice_binding_is_unsafe",
    "slice_entity_state",
    "slice_families",
    "slice_text",
    "turn_slices",
    "unclaimed_slices",
    "uncovered_slice_texts",
    "unit_anchors",
    "unit_answer_evidence",
    "unit_families",
    "units_in_slices",
    "units_matching_needle",
    "units_present_in_answer",
    "units_with_registered_demand",
    "units_without_own_object",
]
