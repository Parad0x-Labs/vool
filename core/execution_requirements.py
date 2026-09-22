"""What a turn REQUIRES, decided once, in one place, before any lane can claim it.

Why this exists
---------------
Five separate components were each deciding, independently, whether a turn needed external
evidence: `task_class`, `answer_mode_for`, `should_attempt_tool_intent`, the deep/summarization
lane selection, and the AI-first-chat keep-set. They could disagree, and whichever executed last
silently won. The EV brief made that concrete on 2026-08-06:

    task_class                 : config             <- "battery-capacity CONFIGURATION"
    routed (chat surface)      : business_advisory
    answer_mode_for            : grounded           <- the runtime already knew
    should_attempt_tool_intent : False              <- and a weaker label overruled it

The runtime knew the request demanded current evidence and still answered without a tool. Each of
the five links was fixed on its own over 2026-08-05/06, and a sixth kept appearing, because an
ordinary English word kept deciding a whole turn: "model"+"current", "this turn", "battery",
"requirements", "configuration". Patching the sixth would not have been different from patching the
fifth.

So requirements are computed ONCE, here, and every later stage consumes them. A component may
choose HOW to answer -- prompting style, model, output shape -- but may not decide that a turn
requiring evidence can proceed without it.

What this is not
----------------
Not a new classifier competing with the others. `answer_mode_for` still owns the reading of the
request's contract; this wraps that single reading in an immutable object with the consequences
attached, so there is nothing left for a second opinion to disagree with.

Not a keyword rule per topic. "current + Tesla -> search" / "current + my mood -> no search" as
separate rules is the unmaintainable path the review named. The distinction here is structural: a
question whose SUBJECT is the assistant asks about internal state, and internal state is not
retrieved from the web whatever words surround it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExecutionRequirements:
    """The turn's contract. Immutable: later stages enforce it, they do not renegotiate it."""

    answer_mode: str                       # DIRECT | GROUNDED | LIVE_DATA | AUDIT
    external_evidence_required: bool
    current_information_required: bool
    user_material_supplied: bool
    tools_required: bool
    allowed_toolsets: tuple[str, ...] = ()
    inference_allowed: bool = True
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    # A message that names several independent lookups (e.g. "gold, silver, and BTC price, plus
    # weather in Kaunas and Warsaw"). Set only for the narrow LIVE_DATA case today -- general
    # multipart detection for GROUNDED/AUDIT turns is a larger classification task, not attempted
    # here (see the docstring on `_live_data_classification`).
    multipart: bool = False
    # True when every named lookup is an independent read with no dependency on another's answer,
    # so a parallel executor may run them concurrently rather than one at a time.
    parallel_preferred: bool = False
    # DIRECT turns whose SUBSTANCE is facts about named things in the world -- a factual comparison
    # of products or places ("compare the VW Passat and the VW Golf: production periods, sales"),
    # a claim to verify ("verify whether telegram allows unlimited bots per phone number"). The
    # care level and routing are unchanged (no evidence contract was demanded, so the turn is
    # still DIRECT and still reaches adaptive research from chat); what changes is that a
    # provisional widening by that research is NOT retracted when retrieval finds nothing: the
    # model's memory of sales figures is not an answer, and the gate must see the empty retrieval.
    # A conceptual explanation ("why do i get a KeyError", "compare a list and a tuple in python")
    # names no such facts, and its widening is retracted so the model's answer publishes.
    world_facts_requested: bool = False

    def forbids_toolless_lane(self) -> bool:
        """True when no lane may claim this turn without offering tools."""
        return self.tools_required


class RequiredToolsNotOfferedError(RuntimeError):
    """A lane claimed a turn that requires tools without offering any.

    Raised rather than logged: continuing produces an answer that looks researched and is not,
    which is the failure this whole module exists to make impossible.
    """


def _live_data_classification(text: str) -> tuple[bool, bool, tuple[str, ...]] | None:
    """Whether this GROUNDED request is specifically a live-data (price/weather) lookup.

    Returns `(multipart, parallel_preferred, allowed_toolsets)`, or `None` when the request is
    GROUNDED but not live-data-shaped (general web research, current events, etc.) -- which leaves
    the ordinary GROUNDED path exactly as it behaved before this classification existed.

    Reuses the SAME recognizers the fast live-info lanes and the multipart planner's own
    servability check already use (`price_assets_named`, `_looks_like_price_query`,
    `_looks_like_market_quote_query_all`, `_looks_like_live_weather_request`,
    `turn_may_hold_several_requests`) rather than a new keyword list, so this can never disagree
    with what actually answers the request.
    """
    try:
        from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request
        from core.agent_runtime.fast_live_info_price import price_assets_named
        from tools.web.web_research import (
            _looks_like_market_quote_query_all,
            _looks_like_price_query,
            _looks_like_price_query_all,
        )
    except Exception:
        return None

    from core.retrieval_constraints import analyze_retrieval_constraints

    constraints = analyze_retrieval_constraints(text)
    # The subject of a requested poem or story is not a lookup: the rest of the request is read on its
    # own (`grounded_mode.creative_writing_remainder`, the reading the live-info lane's door applies too).
    from core.agent_runtime.grounded_mode import creative_writing_remainder

    candidate_text = creative_writing_remainder(constraints.eligible_text)
    if not candidate_text:
        return None
    lowered = candidate_text.lower()
    # SENTINEL G4, 2026-08-06 -- THE first wrong production decision, and the reason
    # "Explain gold structure." became `market_quote("Gold")`. `price_assets_named` answers
    # "which assets does this message NAME?", by alias presence alone; this line read that as
    # "is this a market request?". They are different questions: `gold` is a metal before it is a
    # ticker, so naming it was enough to admit the turn to LIVE_DATA with the `market_prices`
    # toolset, and every correct step after it (`build_live_data_plan` -> `_market_subtask` ->
    # `operation="market_quote"`) faithfully executed an already-wrong premise.
    #
    # Entity presence is now evidence only WHEN the request also says something about the market.
    # The other three recognizers below carry their own market-intent gate internally (they all
    # read `core.market_intent` too), so they are unchanged here -- this adds the gate to the one
    # path that never had it.
    from core.market_intent import market_semantics_present

    names_asset = bool(price_assets_named(candidate_text))
    # A ticker written as one (`$BASE`, "BASE coin") is a market mention by its own notation;
    # with market semantics beside it ("price", "value", "worth") the request is a live lookup
    # whether or not the symbol is on the curated tables. The plan resolves the symbol and
    # states an unsupported entity when it cannot (FINDINGS F15: these turns fell to a model).
    try:
        from core.agent_runtime.fast_live_info_price import ticker_mentions

        names_ticker = bool(ticker_mentions(candidate_text, require_market_binding=True))
        names_dollar_ticker = bool(ticker_mentions(candidate_text, dollar_only=True))
    except Exception:
        names_ticker = False
        names_dollar_ticker = False
    has_price = (
        (names_asset and market_semantics_present(candidate_text))
        or (names_ticker and market_semantics_present(candidate_text))
        or names_dollar_ticker
        or bool(_looks_like_price_query(candidate_text))
        or bool(_looks_like_price_query_all(candidate_text))
        or bool(_looks_like_market_quote_query_all(candidate_text))
    )
    has_weather = _looks_like_live_weather_request(lowered)
    # A water-temperature ask is a live-data read of its OWN kind: the weather
    # recognizer rightly declines it (`measurement_medium` — an air reading must
    # never stand in for a sea reading), but nothing claimed it afterwards, so
    # solo water asks classified as ordinary chat and the model improvised
    # unsourced prose (AUD-20260829-003 C4, measured live). The typed plan lane
    # mints water_temperature subtasks under the weather toolset (same
    # remote-observation class), from the same `requests_a_water_temperature`
    # authority the weather recognizer uses to decline.
    from core.measurement_medium import requests_a_water_temperature

    has_water = bool(requests_a_water_temperature(lowered))
    if constraints.forbids("market_prices"):
        has_price = False
    if constraints.forbids("weather"):
        has_weather = False
        has_water = False
    if not has_price and not has_weather and not has_water:
        return None

    toolsets: list[str] = []
    if has_price:
        toolsets.append("market_prices")
    if has_weather or has_water:
        toolsets.append("weather")

    try:
        from core.agent_runtime.turn_planner import turn_may_hold_several_requests
    except Exception:
        def turn_may_hold_several_requests(_text: str) -> bool:
            return False

    # Multipart when the request spans both toolsets, or when the message shape itself suggests
    # several independent lookups (several assets, several cities, several "and"/comma-joined
    # parts). Every live-data lookup this runtime recognizes is an independent read with no
    # dependency on another's answer -- unlike a general multipart plan, which may have a real
    # ordering dependency ("the weather in the city where the race is") -- so multipart here always
    # implies parallel-preferred, with no separate dependency analysis needed.
    multipart = (has_price and has_weather) or turn_may_hold_several_requests(candidate_text)
    return multipart, multipart, tuple(toolsets)


def _wallet_action_demand(text: str) -> bool:
    """Whether the text carries an explicit wallet demand AND the wallet lane exists on this runtime."""
    try:
        from core.wallet.config import wallet_enabled

        if not wallet_enabled():
            return False
        from core.tool_demand_signals import resolve_demand_signals

        return "wallet" in resolve_demand_signals(str(text or "")).required_families
    except Exception:
        return False


def _email_account_action_demand(text: str) -> bool:
    """Whether the text carries an email-account action demand AND the email lane exists.

    Same law as `_wallet_action_demand`: a request to operate the operator's own configured
    mail account ("check my unread emails, open the delivery thread and draft a reply") is an
    ACTION through the email tools, never a topic to converse about. Without this arm the
    tools-less AI-first chat lane kept such turns (they classify as plain conversation) and the
    model narrated "I'll check your inbox..." while nothing ran -- the same measured dead end
    the path-naming and evidence-demanding releases in the lane policy close. The demand signal
    is the same typed recognizer the tool offer reads, so the two cannot disagree; both email
    toggles OFF (the default) leaves every turn exactly as it was, and a user's own tool
    prohibition still wins.

    COMPOSITION IS NOT AN ACTION: "write me a cold-outreach email template" authors email TEXT
    without touching any account -- no inbox, no thread, no send. The recognizer's nouns alone
    would seat it; the account anchors (my/inbox/thread/unread/reply-to-the/forward/send-to)
    are what separate operating the account from writing about email."""
    try:
        from core import policy_engine

        if not (
            bool(policy_engine.get("email.read_enabled", False))
            or bool(policy_engine.get("email.send_enabled", False))
        ):
            return False
        from core.tool_demand_signals import resolve_demand_signals

        if "email" not in resolve_demand_signals(str(text or "")).required_families:
            return False
        import re

        lowered = str(text or "").lower()
        # Composition-only ("write me an email template") with no account anchor: not an action.
        anchored = bool(
            re.search(
                r"\b(?:my|inbox|mailbox|unread|thread|reply\s+to\s+the|forward|send\s+(?:it|them|that|this)\s+to)\b",
                lowered,
            )
        )
        composing = bool(
            re.search(r"\b(?:write|draft|compose|prepare|help\s+me\s+with)\b", lowered)
        )
        return anchored or not composing
    except Exception:
        return False


def _contacts_action_demand(text: str) -> bool:
    """Whether the text carries a Contacts demand: looking up, saving or changing the owner's saved people and services.

    Same law as `_wallet_action_demand` and `_email_account_action_demand`, read by the same typed recognizer the tool
    offer uses (core.tool_demand_signals), so the lane release and the offer cannot disagree. Contacts has no runtime
    toggle. "Contact us", a contact form or a contact page names no saved contact and is not a demand."""
    try:
        from core.tool_demand_signals import resolve_demand_signals

        return "contacts" in resolve_demand_signals(str(text or "")).required_families
    except Exception:
        return False


def _retrieval_prohibited_requirements(
    *,
    supplied: bool,
    all_tools: bool,
    live_data: bool,
    extra_reasons: tuple[str, ...] = (),
) -> ExecutionRequirements:
    """A current fact may be unavailable when the user forbids the only permitted evidence lane.

    This is not a tools-required contract: asserting that tools are required after the user
    explicitly prohibited them recreates the exact conflict that caused the forbidden fetch.  It
    is also not permission to infer a current value.  The ordinary reasoning lane receives the
    user's words and can explain that limitation, but no retrieval lane is eligible to pre-empt it.
    """

    reasons = ["explicit_tool_prohibition" if all_tools else "explicit_retrieval_prohibition"]
    if live_data:
        reasons.append("live_data_toolset_prohibited")
    reasons.extend(extra_reasons)
    return ExecutionRequirements(
        answer_mode="DIRECT",
        external_evidence_required=False,
        current_information_required=True,
        user_material_supplied=supplied,
        tools_required=False,
        allowed_toolsets=(),
        inference_allowed=False,
        reason_codes=tuple(reasons),
    )


def _conservation_tightened(text_constraints: Any, parent: Any) -> bool:
    """Whether the parent's frozen set added a prohibition the text's own
    reading did not already carry — the exact condition under which a conserved
    refusal states the parent's reason codes beside the conservation marker."""
    from core.turn_prohibitions import FAMILY_TOOLS, FAMILY_WEB

    if parent.prohibits_family(FAMILY_WEB) and not text_constraints.forbids_external_retrieval:
        return True
    if parent.prohibits_family(FAMILY_TOOLS) and not text_constraints.forbids_all_tools:
        return True
    return bool(
        set(parent.prohibited_toolsets) - set(text_constraints.prohibited_toolsets)
    )


def classify_requirements(
    user_input: str,
    *,
    task_class: str = "unknown",
) -> ExecutionRequirements:
    """The requirement reading of ``user_input`` with NO turn state touched.

    ``requirements_for`` is the turn's authority: it freezes the first decision onto the turn
    context and registers the grounding lifecycle. A producer that only wants to KNOW what a text
    needs -- the RequestGraph's lexical producer, an offline differential -- must not register
    anything (measured: a graph build was registering a lifecycle requirement). This is the
    computation alone; it cannot widen, freeze or escalate a turn.
    """
    return _compute_requirements(user_input, task_class=task_class, source_context=None)


def requirements_for(
    user_input: str,
    *,
    task_class: str = "unknown",
    source_context: dict[str, Any] | None = None,
) -> ExecutionRequirements:
    """The single authoritative reading of what this turn needs.

    `task_class` is accepted but deliberately CANNOT remove a requirement -- it may only add one
    (an audit request escalates). That asymmetry is the fix: the EV brief was labelled `config` by a
    keyword classifier, and a label must never be able to strip the evidence contract the user
    stated in their own words.

    M1 (2026-09-01) adds the freeze this module always implied: the FIRST decision computed for a
    text inside a turn's context is THE turn's decision.  Later callers reading the same text get
    the same frozen object back, so no stage can re-litigate what an earlier stage already
    scheduled or guarded on.  A different text (a planner sub-turn) computes its own decision
    without disturbing the turn's record.
    """
    text = " ".join(str(user_input or "").split())
    if isinstance(source_context, dict):
        record = current_requirement_record(source_context)
        normalized_class = str(task_class or "unknown").strip().lower()
        if (
            record is not None
            and record.request_text == text
            and normalized_class in {"", "unknown"}
        ):
            # task_class-bearing callers keep their escalation path: the frozen return
            # is for the classless readers (guards, schedulers), which are the readers
            # the freeze exists to unify.
            return record.requirements
    requirements = _compute_requirements(user_input, task_class=task_class, source_context=source_context)
    if isinstance(source_context, dict):
        _record_requirements(source_context, text, requirements)
    return requirements


#: Where the turn's frozen requirement decision rides.  Reserved exactly like the turn
#: contract's own keys: only core code writes it, and only through the helpers below --
#: a lane that wants to change the decision must escalate through this module, never by
#: writing the key.
CURRENT_REQUIREMENT_KEY = "_current_information_requirement"


@dataclass(frozen=True)
class CurrentRequirementRecord:
    """The turn's frozen requirement decision plus its lifecycle state.

    `requirements` is replaced -- monotonically, pre-synthesis only -- when a lane
    contributes a signal that widens it; `synthesis_started` closes that window, after
    which the decision can neither widen nor flip.
    """

    requirements: ExecutionRequirements
    request_text: str
    synthesis_started: bool = False


def current_requirement_record(source_context: Any) -> CurrentRequirementRecord | None:
    """The turn's frozen requirement record, or None when the context holds none."""

    if not isinstance(source_context, dict):
        return None
    record = source_context.get(CURRENT_REQUIREMENT_KEY)
    return record if isinstance(record, CurrentRequirementRecord) else None


def _record_requirements(
    source_context: dict[str, Any], text: str, requirements: ExecutionRequirements
) -> CurrentRequirementRecord:
    """First decision wins: an existing record is returned untouched, never overwritten."""

    existing = current_requirement_record(source_context)
    if existing is not None:
        _register_lifecycle(source_context, existing)
        return existing
    record = CurrentRequirementRecord(requirements=requirements, request_text=text)
    source_context[CURRENT_REQUIREMENT_KEY] = record
    _register_lifecycle(source_context, record)
    if not record.requirements.current_information_required:
        # A turn that needs no current information of its own may still be a RE-PRESENTATION of
        # the previous published answer ("present the comparison as a table"). Its support is
        # that answer's published support, adopted here so the publication gate adjudicates the
        # reformatted bytes against it. Never raises; a miss leaves the DIRECT reading intact.
        try:
            from core.grounding_lifecycle import adopt_previous_publication_if_representation
            adopt_previous_publication_if_representation(source_context, request_text=text)
        except Exception:
            pass
    return record


def _register_lifecycle(
    source_context: dict[str, Any] | None, record: CurrentRequirementRecord | None
) -> None:
    """M3 stage REQUIRED. Open this turn's grounding lifecycle the moment the canonical
    decision reads current-information.

    Here rather than at the publication seam because that seam has no context: it is reached
    through six transport doors and several pass none at all. The requirement decision is the
    one event every current-information turn has, so it is where the lifecycle can be opened
    for every one of them -- and its ABSENCE at finalization is then a fact, not a gap: no row
    means M1 never marked the turn current, which is the DIRECT/timeless case that publishes
    untouched.

    Never raises. A lifecycle that cannot be opened must not take down the requirement
    authority; the gate downstream reads the absence as "not a current-information turn",
    which is the same answer this module would have given.
    """

    if not isinstance(source_context, dict) or record is None:
        return
    if not record.requirements.current_information_required:
        return
    try:
        from core.grounding_lifecycle import register_required

        register_required(
            source_context,
            request_text=record.request_text,
            reason_codes=tuple(record.requirements.reason_codes or ()),
        )
    except Exception:
        return


def escalate_current_requirement(
    source_context: dict[str, Any] | None,
    *,
    text: str,
    source: str,
    reason_code: str,
    detail: str = "",
) -> bool:
    """A lane's freshness decision ESCALATES the canonical turn requirement.

    This is the one sanctioned alternative to a private decision.  A retrieval lane whose
    recognition fires on a turn the text-only reading did not recognize (topic hints, clause
    slices, vocabularies only it knows) contributes that recognition HERE: the frozen record
    widens to `current_information_required=True` with a `current_info_signal:*` reason code
    naming the contributor, and every later reader -- schedulers, guards -- sees the widened
    decision.

    Monotone by construction: it can only raise False to True and only before synthesis
    begins.  After `begin_synthesis_freeze`, or on an already-current turn, it changes
    nothing (an already-current turn may still gain the attribution code).  Returns whether
    the canonical requirement is current AFTER the call.
    """
    from core.current_information_signals import SIGNAL_REASON_PREFIX

    code = f"{SIGNAL_REASON_PREFIX}{reason_code}"
    if not isinstance(source_context, dict):
        # No turn context to record into: the text-only reading stands as-is.
        try:
            return requirements_for(text).current_information_required
        except Exception:
            return False
    record = current_requirement_record(source_context)
    if record is None:
        requirements_for(text, source_context=source_context)
        record = current_requirement_record(source_context)
    if record is None:
        return False
    widened = record.requirements
    if not widened.current_information_required:
        if record.synthesis_started:
            # The window closed.  Widening now would change what synthesis is already
            # answering to; the lane must decline instead.
            return False
        if widened.user_material_supplied:
            # The user supplied the material this turn is about (a pasted document, a picked
            # file). A lane's private recognizer may not send that turn to the web behind it:
            # measured live, "which order failed in the log I pasted?" was escalated by the
            # adaptive-research lane, three web pages became the turn's "evidence", and the
            # publication gate refused the answer while the document itself went unread. The
            # authority's OWN reading of the text still governs -- a live-data question stays
            # current with a document present -- so only the lane-claim path declines, and the
            # decline is written onto the record rather than swallowed.
            declined = f"{SIGNAL_REASON_PREFIX}user_material_declines:{source}"
            if declined not in widened.reason_codes:
                widened = dataclasses.replace(widened, reason_codes=(*widened.reason_codes, declined))
                source_context[CURRENT_REQUIREMENT_KEY] = dataclasses.replace(record, requirements=widened)
            return False
        widened = dataclasses.replace(widened, current_information_required=True)
    if code in widened.reason_codes:
        source_context[CURRENT_REQUIREMENT_KEY] = dataclasses.replace(record, requirements=widened)
        _register_lifecycle(source_context, current_requirement_record(source_context))
        return True
    widened = dataclasses.replace(widened, reason_codes=(*widened.reason_codes, code))
    source_context[CURRENT_REQUIREMENT_KEY] = dataclasses.replace(record, requirements=widened)
    _register_lifecycle(source_context, current_requirement_record(source_context))
    return True


#: Lanes whose retrieval is ENRICHMENT, not recognition. Their ask widens the turn like any
#: other lane's (so a search that finds evidence is bound and gated like any current turn), but
#: the widening is provisional: when the lane's own retrieval ends with NOTHING, the widening is
#: retracted before synthesis and the turn keeps the authority's own reading. Measured
#: 2026-09-07: "my code says IndexError: list index out of range, why does that happen" -- stable
#: knowledge by the authority -- was widened by adaptive research's ask, two searches found
#: nothing, and the publication gate then refused the model's correct answer for lacking retrieved
#: support. The same lane's ask on "compare the VW Passat and the VW Golf" found five sources and
#: the gate bound them; that widening stands.
PROVISIONAL_ESCALATION_SOURCES = frozenset({"adaptive_research"})


def retract_provisional_escalation(
    source_context: dict[str, Any] | None,
    *,
    source: str,
    reason: str,
    text: str = "",
) -> bool:
    """Undo a widening that ONLY provisional lanes contributed, before synthesis begins.

    Returns True when the canonical requirement was current solely because of `source` (a
    provisional lane) and is now the authority's own reading again. Returns False -- and
    changes nothing -- when the turn was current on its own reading or by a non-provisional
    lane's recognition, when `source` is not provisional, or once synthesis has started
    (`begin_synthesis_freeze`): after the freeze the decision can neither widen nor flip.
    """
    from core.current_information_signals import SIGNAL_REASON_PREFIX

    if source not in PROVISIONAL_ESCALATION_SOURCES or not isinstance(source_context, dict):
        return False
    record = current_requirement_record(source_context)
    if record is None or record.synthesis_started:
        return False
    current = record.requirements
    if not current.current_information_required:
        return False
    lane_prefix = f"{SIGNAL_REASON_PREFIX}lane:"
    lane_codes = [code for code in current.reason_codes if code.startswith(lane_prefix)]
    own_codes = [code for code in lane_codes if code[len(lane_prefix):] in PROVISIONAL_ESCALATION_SOURCES]
    if not own_codes or len(own_codes) != len(lane_codes):
        # Somebody else's recognition (a freshness lane, the user's own words) also holds the
        # turn current: nothing provisional to retract.
        return False
    baseline = requirements_for(str(text or record.request_text or ""))
    if (
        baseline.current_information_required
        or baseline.external_evidence_required
        or baseline.world_facts_requested
    ):
        # The turn's own substance is facts in the world: an empty retrieval is a fact the
        # publication gate must weigh, not a reason to answer from memory.
        return False
    kept = tuple(code for code in current.reason_codes if code not in own_codes)
    retracted = dataclasses.replace(
        current,
        current_information_required=False,
        reason_codes=(*kept, f"{SIGNAL_REASON_PREFIX}retracted:{source}:{reason}"),
    )
    source_context[CURRENT_REQUIREMENT_KEY] = dataclasses.replace(record, requirements=retracted)
    try:
        from core.grounding_lifecycle import withdraw_required

        withdraw_required(source_context, reason=f"{source}:{reason}")
    except Exception:
        pass
    return True


def begin_synthesis_freeze(source_context: dict[str, Any] | None, text: str) -> None:
    """Close the decision window: from here the requirement can neither widen nor flip.

    Called where synthesis begins (the response-guard seam reads the frozen record
    immediately after).  Idempotent, and a no-op on a context with no record it did not
    first mint from `text`.
    """
    if not isinstance(source_context, dict):
        return
    record = current_requirement_record(source_context)
    if record is None:
        requirements_for(text, source_context=source_context)
        record = current_requirement_record(source_context)
    if record is not None and not record.synthesis_started:
        source_context[CURRENT_REQUIREMENT_KEY] = dataclasses.replace(record, synthesis_started=True)
    _register_lifecycle(source_context, current_requirement_record(source_context))


def require_current_information_for_retrieval(
    source_context: dict[str, Any] | None,
    user_input: str,
    *,
    lane: str,
) -> bool:
    """THE retrieval door.  A production scheduler calls this immediately before it
    retrieves; retrieval proceeds only on True.

    True means the canonical turn requirement is `current_information_required=True` --
    either the authority's own reading already knew the vocabulary, or the lane's claim
    just escalated the frozen record through `escalate_current_requirement`.  False means
    the decision is closed and was never current: the lane must decline (fail-closed),
    because retrieving behind the authority's back is exactly how every grounding guard
    downstream got disarmed.

    A turn the user pinned closed by PROHIBITING retrieval also declines, whatever its
    currency flag says: `_retrieval_prohibited_requirements` deliberately keeps
    `current_information_required=True` so the no-invented-value guards stay armed, which
    makes "is the turn current" the wrong question for a scheduler to retrieve on.  Measured
    live 2026-09-16: "Do not browse or use external tools" + a list of given constants ran
    two web retrievals behind the prohibition and then refused the computed answer for
    lacking them.  Every lane reaches this one door -- the fast-live-info flow included --
    so the prohibition binds all of them at once.
    """
    if not isinstance(source_context, dict):
        try:
            requirements = requirements_for(str(user_input or ""))
            return (
                requirements.current_information_required
                and not _retrieval_is_prohibited(requirements)
            )
        except Exception:
            return False
    record = current_requirement_record(source_context)
    if record is not None and _record_retrieval_is_prohibited(record):
        # The prohibition check reads the RECORD, not a fresh reading: a DIRECT turn's contract
        # carries no prohibition code (that code is stamped only where the GROUNDED/live arms
        # refuse to fetch, and `should_attempt_tool_intent` gives it a narrower meaning), so a
        # lane escalating such a turn would otherwise retrieve behind the user's words. The
        # record's own request text is the frozen authority for what the user prohibited.
        return False
    if record is not None and record.requirements.current_information_required:
        return True
    if record is not None and record.synthesis_started:
        return False
    return escalate_current_requirement(
        source_context, text=str(user_input or ""), source=lane, reason_code=f"lane:{lane}"
    )


def _retrieval_is_prohibited(requirements: ExecutionRequirements) -> bool:
    """Whether the frozen contract's own reason codes say the user closed the evidence lanes."""

    return any(
        code in {"explicit_retrieval_prohibition", "explicit_tool_prohibition"}
        for code in tuple(getattr(requirements, "reason_codes", ()) or ())
    )


def _record_retrieval_is_prohibited(record: CurrentRequirementRecord) -> bool:
    """Contract codes first, then the record's own frozen text: a message that prohibits
    retrieval keeps every lane out of the web whatever the currency flag says, even when the
    reading that stamped no code (the DIRECT arm) is the one that was frozen."""

    if _retrieval_is_prohibited(record.requirements):
        return True
    try:
        from core.retrieval_constraints import analyze_retrieval_constraints

        constraints = analyze_retrieval_constraints(str(record.request_text or ""))
        return bool(constraints.forbids_external_retrieval or constraints.forbids_all_tools)
    except Exception:
        return False


def _compute_requirements(
    user_input: str,
    *,
    task_class: str = "unknown",
    source_context: dict[str, Any] | None = None,
) -> ExecutionRequirements:
    """The classification itself.  Callers reach it through `requirements_for`, which
    owns the turn-scoped freeze; this function stays pure so the freeze is impossible to
    route around by accident."""
    from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for, forbids_inference
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.stipulated_frame import stipulated_frame_active

    text = " ".join(str(user_input or "").split())
    if stipulated_frame_active(text, source_context=source_context):
        return ExecutionRequirements(
            answer_mode="DIRECT",
            external_evidence_required=False,
            current_information_required=False,
            user_material_supplied=_user_material_supplied(source_context),
            tools_required=False,
            allowed_toolsets=(),
            inference_allowed=True,
            reason_codes=("user_stipulated_frame",),
        )
    constraints = analyze_retrieval_constraints(text)
    # P0 POLICY CONSERVATION — a child sub-turn runs a CLEAN SLICE of a turn
    # whose parent may have frozen prohibitions elsewhere in the message; the
    # canonical request riding the context is the authority. The union can
    # only ADD prohibitions to this text's own reading, never remove one, so
    # on the parent's own turn it is idempotent.
    from core.turn_prohibitions import (
        REASON_CONSERVED,
        conserve_retrieval_constraints,
        conserved_request_prohibitions,
    )

    _text_constraints = constraints
    _conserved_parent = conserved_request_prohibitions(source_context)
    constraints = conserve_retrieval_constraints(constraints, source_context)
    # The conservation marker is stated only when the parent TIGHTENED this
    # reading — on the parent's own turn the freeze was minted from this very
    # text, the union is idempotent, and the reason codes stay exactly as
    # they were.
    _conservation_reasons: tuple[str, ...] = ()
    if (
        not _conserved_parent.empty
        and _conservation_tightened(_text_constraints, _conserved_parent)
    ):
        _conservation_reasons = (*tuple(_conserved_parent.reason_codes), REASON_CONSERVED)
    candidate_text = constraints.eligible_text
    mode = answer_mode_for(candidate_text)
    # The software-authoring register (see `answer_mode_for`): the request asks the runtime to
    # BUILD something, and no explicit evidence contract was stated. The live-data recognizers and
    # the freshness signals below would otherwise read the artifact's specification as a lookup.
    from core.agent_runtime.grounded_mode import (
        explicitly_promises_evidence,
        is_software_authoring_request,
        software_authoring_remainder,
    )

    authoring_request = is_software_authoring_request(candidate_text) and not explicitly_promises_evidence(candidate_text)
    if authoring_request:
        # Only an authoring-ONLY request disarms the live-data and freshness arms. Any other clause
        # that ASKS FOR current information on its own keeps them armed for the whole turn -- the
        # same lookup-shape rule `answer_mode_for` applies: the remainder arms grounding when it
        # is itself an explicit or recency LOOKUP REQUEST (or a structural price/weather demand),
        # never because a spec sentence merely carries a freshness word. Measured live
        # 2026-09-17: "The current behavior duplicates entries" inside a self-contained
        # JavaScript task opened this turn's grounding lifecycle, searched, bound unrelated
        # sources, and withheld the user's own requirements as unsupported claims.
        from core.agent_runtime.grounded_mode import _remainder_asks_for_a_lookup

        rest = software_authoring_remainder(candidate_text)
        if rest and (
            _live_data_classification(rest) is not None
            or _remainder_asks_for_a_lookup(rest)
        ):
            authoring_request = False
    reasons: list[str] = []

    normalized_class = str(task_class or "unknown").strip().lower()
    if normalized_class in {"workspace_audit"} and mode is not AnswerMode.AUDIT_GRADE:
        # A class may ESCALATE. It may never de-escalate.
        mode = AnswerMode.AUDIT_GRADE
        reasons.append("task_class_escalated_to_audit")

    supplied = _user_material_supplied(source_context)
    # The material may be IN THE MESSAGE: a pasted table, labeled measurements or a drawn graph
    # the request works on (core.self_contained_turn.turn_supplies_its_data -- the same authority
    # that owns "a turn that supplies its own premises cannot be answered by the internet").
    # Measured live 2026-09-18: a pasted deployment-results table was widened to
    # current-information by a retrieval lane, and the computed answer was then withheld for
    # lacking support from the four irrelevant pages the widening fetched. Declaring the message's
    # own data as supplied material gives it the exact protection attached documents already have:
    # a lane's recognizer may not send the turn to the web behind material the user just handed
    # over, while the authority's own live-data/current arms still govern genuine lookups.
    message_supplies_data = False
    try:
        from core.self_contained_turn import turn_supplies_its_data

        # The RAW message, not the whitespace-collapsed `text` above: a data block is a set of
        # LINES (table rows, `Name = value` measurements, `A --> B` edges), and collapsing the
        # message fuses the rows into one prose line the row-shaped recognizer can never see.
        message_supplies_data = turn_supplies_its_data(str(user_input or ""))
    except Exception:
        message_supplies_data = False
    if message_supplies_data:
        supplied = True
    if supplied:
        reasons.append("user_material_supplied")
        carried = _chat_attachment_material(dict(source_context or {}))
        if carried and carried != "user_material_supplied":
            reasons.append(carried)
        if message_supplies_data:
            reasons.append("message_supplies_data")

    # Forbidding inference ENTAILS requiring evidence. If the user ruled out guessing, retrieval is
    # the only remaining way to answer, so a turn that may not guess and may not look it up has been
    # left with nothing.
    #
    # This was live: `forbids_inference` and `answer_mode_for` read overlapping but unequal keyword
    # lists, and "no speculation" appeared only in the first. So "only tell me what you can verify,
    # no speculation" suppressed inference and stayed DIRECT -- caught by the production matrix,
    # not by any prompt fixture. Deriving the requirement removes the possibility of the two lists
    # disagreeing again, rather than adding the missing phrase to the second one.
    no_guessing = forbids_inference(text)
    if no_guessing and mode is AnswerMode.DIRECT:
        mode = AnswerMode.GROUNDED
        reasons.append("inference_forbidden_entails_evidence")

    if mode is AnswerMode.AUDIT_GRADE:
        reasons.append("audit_grade_request")
        return ExecutionRequirements(
            answer_mode="AUDIT",
            external_evidence_required=True,
            current_information_required=False,
            user_material_supplied=supplied,
            tools_required=True,
            allowed_toolsets=("workspace", "web_search", "web_fetch"),
            inference_allowed=not no_guessing,
            reason_codes=tuple(reasons),
        )

    # LIVE_DATA is checked independently of the DIRECT/GROUNDED split below, not nested inside the
    # GROUNDED branch as it first shipped. `answer_mode_for()` classified "Weather only for Berlin
    # and Copenhagen." and "Market data only for Bitcoin and gold." as DIRECT (no temporal marker
    # like "current"/"now") -- confirmed live: the LIVE_DATA path never got a chance to fire, the
    # turn fell through to the model, and the model HALLUCINATED fake prices ("$35,200" for
    # Bitcoin, real price ~$64,000) while claiming "real-time data from major exchanges". A
    # price/weather request is current-information-required regardless of what the generic
    # DIRECT/GROUNDED classifier decides -- same "escalate, never de-escalate" principle already
    # applied to `no_guessing` two lines above, extended to this domain. Safe to check
    # unconditionally: `_live_data_classification` only ever fires when its own narrow recognizers
    # (price/weather) match, so it cannot reclassify an unrelated DIRECT request like "what is the
    # capital of France".
    # A payment demand ("pay 1500 lamports to <address>", "status of payment pay-...") is an ACTION
    # this runtime performs through its wallet tools, never a topic to converse about: without this
    # the chat surface kept the turn on the tool-less lane and a certified model was asked to answer a
    # transfer request from its weights (measured 2026-09-03 on the served wallet proof). The demand
    # signal is the same one the tool offer reads, so the two cannot disagree; the wallet being OFF
    # (the default) leaves every turn exactly as it was, and a user's own tool prohibition still wins.
    if _wallet_action_demand(candidate_text) and not constraints.forbids_all_tools and not constraints.forbids("wallet"):
        reasons.append("wallet_action_request")
        return ExecutionRequirements(
            answer_mode="DIRECT",
            external_evidence_required=False,
            current_information_required=False,
            user_material_supplied=supplied,
            tools_required=True,
            allowed_toolsets=("wallet",),
            inference_allowed=not no_guessing,
            reason_codes=tuple(reasons),
        )

    # A Contacts demand ("save Alex Chen as a contact: ...", "what is Alex Chen's Telegram handle?") is served only through
    # the contacts tools: the tools-less chat lane cannot read or write the owner's saved contacts. Measured 2026-09-15 on the
    # served Contacts journey: the save request classified `integration_orchestration`, kept the tools-less lane, and the
    # certified model was offered no tool. Mode is preserved as in the email arm; a user's own tool prohibition still wins,
    # and the model still decides whether any contacts tool is called.
    if _contacts_action_demand(candidate_text) and not constraints.forbids_all_tools and not constraints.forbids("contacts"):
        reasons.append("contacts_action_request")
        return ExecutionRequirements(
            answer_mode=str(getattr(mode, "value", str(mode))),
            external_evidence_required=False,
            current_information_required=False,
            user_material_supplied=supplied,
            tools_required=True,
            allowed_toolsets=("contacts",),
            inference_allowed=not no_guessing,
            reason_codes=tuple(reasons),
        )

    # An email-account action demand ("check my unread emails", "draft a reply to the delivery
    # thread") is served only through the email tools; the tools-less chat lane cannot read an
    # inbox. Mode is preserved (a no-guessing GROUNDED contract stays grounded): only the
    # tools-required fact is added, so the lane releases the turn to the tool loop.
    if _email_account_action_demand(candidate_text) and not constraints.forbids_all_tools and not constraints.forbids("email"):
        reasons.append("email_account_action_request")
        return ExecutionRequirements(
            answer_mode=str(getattr(mode, "value", str(mode))),
            external_evidence_required=False,
            current_information_required=False,
            user_material_supplied=supplied,
            tools_required=True,
            allowed_toolsets=("email",),
            inference_allowed=not no_guessing,
            reason_codes=tuple(reasons),
        )

    live_data = None if authoring_request else _live_data_classification(text)
    if live_data is None:
        # A bare continuation of a live-data turn IS that turn's request again. Measured live:
        # "and?" / "well?" / "what about now" after a grounded weather answer all fell through to
        # the model with web_calls=0, and it answered anyway -- once denying it could reach the
        # network at all, once inventing 9 C where the fetch had said 10 C, once citing wttr.in and
        # a timestamp for a source it never contacted. Inheriting the prior request restores the
        # entity, the intent and the temporal frame together, and the live-data lane fetches again.
        # `core.live_data_continuation` only answers when the utterance introduces nothing of its
        # own, so a follow-up that names something new is untouched.
        from core.live_data_continuation import continuation_inherits_live_data

        inherited = continuation_inherits_live_data(text, source_context=source_context)
        if inherited:
            live_data = _live_data_classification(inherited)
            if live_data is not None:
                reasons.append("live_data_continuation_inherited")
    if live_data is not None:
        multipart, parallel_preferred, toolsets = live_data
        # P0 POLICY CONSERVATION at the lane gate: the classified toolsets are
        # read through the CONSERVED constraints (the child's own text unioned
        # with the parent's frozen set), mirroring exactly how
        # `_live_data_classification` kills a toolset the text itself forbids.
        # Every live toolset frozen by the parent is the typed prohibited
        # contract — no tool, no invented current value, reason codes
        # conserved — instead of a LIVE_DATA return that would fetch.
        toolsets = tuple(
            toolset for toolset in toolsets if not constraints.forbids(toolset)
        )
        if not toolsets:
            return _retrieval_prohibited_requirements(
                supplied=supplied,
                all_tools=constraints.forbids_all_tools,
                live_data=True,
                extra_reasons=_conservation_reasons,
            )
        reasons.append("live_data_request")
        if multipart:
            reasons.append("multipart_live_data_request")
        return ExecutionRequirements(
            answer_mode="LIVE_DATA",
            external_evidence_required=True,
            current_information_required=True,
            user_material_supplied=supplied,
            tools_required=True,
            allowed_toolsets=toolsets,
            inference_allowed=False,
            reason_codes=tuple(reasons),
            multipart=multipart,
            parallel_preferred=parallel_preferred,
        )

    # Ask the same classifier once against only the positive candidate text.  If it recognizes a
    # live request there but the constrained call above did not, every matching live toolset was
    # explicitly vetoed.  That conflict must resolve to no tool and no invented current value.
    unrestricted_live_data = _live_data_classification(candidate_text) if constraints.has_prohibition else None
    if unrestricted_live_data is not None and all(
        constraints.forbids(toolset) for toolset in unrestricted_live_data[2]
    ):
        return _retrieval_prohibited_requirements(
            supplied=supplied,
            all_tools=constraints.forbids_all_tools,
            live_data=True,
            extra_reasons=_conservation_reasons,
        )

    if mode is AnswerMode.GROUNDED:
        if constraints.forbids_external_retrieval:
            return _retrieval_prohibited_requirements(
                supplied=supplied,
                all_tools=constraints.forbids_all_tools,
                live_data=False,
                extra_reasons=_conservation_reasons,
            )
        reasons.append("request_promises_evidence")
        return ExecutionRequirements(
            answer_mode="GROUNDED",
            external_evidence_required=True,
            current_information_required=True,
            user_material_supplied=supplied,
            tools_required=True,
            allowed_toolsets=("web_search", "web_fetch"),
            inference_allowed=not no_guessing,
            reason_codes=tuple(reasons),
        )

    # M1 (2026-09-01) -- the signals.  Five freshness vocabularies measured (audit on
    # a2308a26) could each recognize a current-information ask this reading missed, and
    # the retrieval scheduler firing on its own vocabulary while this authority read
    # `current_information_required=False` is what disarmed every grounding guard
    # downstream (`core.unsourced_current_claim` exits early on a False requirement).
    # Each lane's recognition is now a typed signal CONSUMED HERE: same recognizer
    # tables the lanes use, contributed rather than deciding alone.  A firing signal
    # escalates -- never de-escalates -- exactly like `no_guessing` and `task_class`
    # above.  A question whose SUBJECT is the assistant still reads DIRECT whatever
    # markers surround it, for the same reason `answer_mode_for` carves it out.
    from core.agent_runtime.grounded_mode import _is_about_the_assistant
    from core.current_information_signals import SIGNAL_REASON_PREFIX, firing_signals

    # The asking units are read off the message AS WRITTEN: this authority collapses whitespace
    # for its own matching, but newlines are clause boundaries to the interpretation (a list item
    # with no period would otherwise fuse with the question after it). The prohibited spans the
    # eligible text removed are constraints, which the asking units already exclude.
    # The asking units are read off the message as written, with the REPORTED-EXAMPLE spans
    # removed (core.retrieval_constraints._reported_example_spans): an illustration of what a
    # user might type ("for example, ... latest email") is not this user's own current-
    # information request, so its temporal words must not fire the widening -- while genuine
    # quoted demands and every pasted line keep the as-written clause boundaries above.
    _example_stripped = _strip_reported_examples(asked_text(str(user_input or "")) if not constraints.has_prohibition else asked_text(candidate_text))
    signals = firing_signals(_example_stripped)
    if signals and not _is_about_the_assistant(candidate_text) and not authoring_request:
        signal_codes = tuple(f"{SIGNAL_REASON_PREFIX}{signal.reason_code}" for signal in signals)
        if constraints.forbids_external_retrieval:
            # The user vetoed the only evidence lane: the requirement still stands
            # (current=True), the turn stays toolless, and inference stays off -- the
            # ordinary reasoning lane explains the limitation rather than inventing.
            return _retrieval_prohibited_requirements(
                supplied=supplied,
                all_tools=constraints.forbids_all_tools,
                live_data=False,
                extra_reasons=signal_codes,
            )
        return ExecutionRequirements(
            answer_mode="GROUNDED",
            external_evidence_required=True,
            current_information_required=True,
            user_material_supplied=supplied,
            tools_required=True,
            allowed_toolsets=("web_search", "web_fetch"),
            inference_allowed=not no_guessing,
            reason_codes=tuple(reasons) + signal_codes,
        )

    reasons.append("stable_knowledge")
    from core.agent_runtime.grounded_mode import requests_world_facts

    # "World facts" are facts about named things the runtime would have to LEARN about. Data the
    # user just supplied in the message is not that: "comparing Passed vs Failed" of the pasted
    # table's own columns and "a deployment test produced these results" describe the supplied
    # block, and this flag's one consumer (`retract_provisional_escalation`) used it to KEEP a
    # lane's widening alive against that very table (measured live 2026-09-18).
    world_facts = bool(requests_world_facts(text)) and not message_supplies_data
    if world_facts:
        reasons.append("world_facts_requested")
    return ExecutionRequirements(
        answer_mode="DIRECT",
        external_evidence_required=False,
        current_information_required=False,
        user_material_supplied=supplied,
        tools_required=False,
        allowed_toolsets=(),
        inference_allowed=True,
        reason_codes=tuple(reasons),
        world_facts_requested=world_facts,
    )


def _strip_reported_examples(text: str) -> str:
    """Remove the reported-example spans from asking-unit text (delegates to the retrieval
    constraints' own recognizer, so the eligible text and the asking units can never disagree
    about what counts as an illustration)."""
    try:
        from core.retrieval_constraints import _reported_example_spans

        spans = _reported_example_spans(str(text or ""))
        if not spans:
            return text
        out = []
        cursor = 0
        for start, end in spans:
            out.append(text[cursor:start])
            cursor = end
        out.append(text[cursor:])
        return "".join(out)
    except Exception:
        return text


def asked_text(text: str) -> str:
    """The parts of the message that ASK the runtime for something, for every freshness reader.

    Read off the ONE interpretation (`core.agent_runtime.answer_coverage.interpret_request`): its
    request and unresolved units. Statements about a situation or a proposed system, the list
    items they enumerate, quoted literals and the constraints on the answer's form DESCRIBE or
    restrict; they ask for nothing, and a recency word inside them is not a request for today's
    facts ("Do NOT browse" is a prohibition, not a lookup).

    Measured 2026-09-16 on candidate 035dea9b: a design brief listed, among a proposed admin
    panel's features, "view bot status and recent errors". The fresh-lookup tail rule read
    "recent" beside "bot"/"telegram"/"discord" elsewhere in the text, the turn became
    current-information-required, four web retrievals returned nothing, and the publication
    gate withheld the design answer as unsupported. The recognizer tables are unchanged; they now
    read the asking units, so "look up the latest release notes" in a request still fires and the
    same words inside a described feature do not.

    Falls back to the whole text when the interpretation is unavailable, is being read right now
    (a binder consulting this authority), or yields no asking unit.

    PUBLIC because it is the one reader: the fast live-info lane's own mode decision
    (`core.agent_runtime.fast_live_info_mode_classifier.live_info_mode`) consults the same
    function, so the lane cannot recognize a lookup in text this authority reads as described
    material and then widen the turn through the retrieval door behind it (measured served,
    2026-09-16: the lane retrieved, paid for a wording call and escalated the design brief to
    current-information while the authority itself read DIRECT).
    """
    value = str(text or "")
    try:
        from core.agent_runtime.answer_coverage import (
            _IN_PROGRESS,
            KIND_CONSTRAINT,
            KIND_CONTEXT,
            KIND_ENUMERATION,
            KIND_LITERAL,
            interpret_request,
            interpretation_in_progress,
        )

        in_progress = _IN_PROGRESS.get()
        if in_progress is not None and value in in_progress:
            return value
        if interpretation_in_progress():
            # A lane's probe consulted this reader from inside a reading: starting another
            # reading of the same fragments recurses (measured 2026-09-16); read the whole text.
            return value
        units = interpret_request(value).units
    except Exception:
        return value
    if not units:
        return value
    by_id = {unit.unit_id: unit for unit in units}
    kept: list[str] = []
    seen: set[str] = set()
    for unit in units:
        if unit.kind in (KIND_CONTEXT, KIND_ENUMERATION, KIND_LITERAL, KIND_CONSTRAINT):
            continue
        # The statement a request refers back to is part of what it asks: "my build fails with
        # X. How do I fix it?" asks about X; "my friend says the current price is 50k. Is that
        # right?" asks about the current price. A described system's feature list is attached TO
        # its design question, never referred back to by it, so it stays out.
        for antecedent_id in unit.depends_on:
            antecedent = by_id.get(antecedent_id)
            if antecedent is not None and antecedent.kind == KIND_CONTEXT and antecedent_id not in seen:
                seen.add(antecedent_id)
                kept.append(str(antecedent.text or ""))
        if unit.unit_id not in seen:
            seen.add(unit.unit_id)
            kept.append(str(unit.text or ""))
    if not kept:
        return value
    if len(kept) == len(units):
        return value
    return "\n".join(kept)


def _user_material_supplied(source_context: dict[str, Any] | None) -> bool:
    """Whether the user attached something to work on.

    Summarization is only legitimate when there IS material. A long multipart research brief is not
    a summarization request because it contains bullets, a table request, or classification
    instructions -- that reading is how a research turn ended up in the deep/summarization lane
    with no tools.
    """
    context = dict(source_context or {})
    if any(context.get(key) for key in ("attachments", "user_material", "supplied_files", "media_attachments")):
        return True
    return _chat_attachment_material(context) is not None


def _chat_attachment_material(context: dict[str, Any]) -> str | None:
    """Why the ONE attachment authority says this turn has material, or None.

    Two sources, both the authority's own and never a client field: the items the door bound
    to THIS turn (`external_evidence` entries carrying an attachment id and the chat-attachment
    origin -- the door strips client-forged ones), and the chat's retained documents from
    earlier turns, which the same authority carries into this turn's model context. The reason
    code names which, so a receipt can say what the material was.
    """
    from core.agent_runtime.request_authority import bounded_evidence

    evidence = bounded_evidence(context.get("external_evidence"))
    for item in evidence.recognized:
        if item.get("attachment_id") and str(item.get("origin") or "") == "chat_attachment":
            return "user_material_supplied"
    session = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
    if session:
        try:
            from core.chat_attachments import list_documents

            if list_documents(session):
                return "chat_documents_carried"
        except Exception:
            return None
    return None


def assert_lane_can_satisfy(
    requirements: ExecutionRequirements,
    *,
    lane_supports_tools: bool,
    offered_tool_count: int,
    lane_name: str = "",
) -> None:
    """Enforcement point. Call where a lane is selected, before any model call.

    A turn that requires tools and is handed none must fail loudly here rather than proceed into
    ordinary prose -- an answer produced with no evidence for a request that demanded evidence is
    indistinguishable from a researched one, and that is precisely how three weeks of "irrelevant
    sources" reports were generated.
    """
    if not requirements.tools_required:
        return
    if not lane_supports_tools or offered_tool_count <= 0:
        raise RequiredToolsNotOfferedError(
            f"REQUIRED_TOOLS_NOT_OFFERED: lane={lane_name or 'unknown'} "
            f"supports_tools={lane_supports_tools} offered={offered_tool_count} "
            f"mode={requirements.answer_mode} reasons={list(requirements.reason_codes)}"
        )


def assert_envelope_carries_tools(
    *,
    tools_required: bool,
    offered_tool_count: int,
    native_tools_in_envelope: bool,
    structured_fallback_in_envelope: bool,
    lane_name: str = "",
) -> None:
    """The LAST check before a tools-required turn reaches the wire, run inside the adapter against
    the ACTUAL built payload -- not the request object, not the ranked manifest's declared
    capabilities. `assert_lane_can_satisfy` above runs once, at lane selection, against what the
    manifest CLAIMS; this runs again, per call, against what the adapter ACTUALLY built, so a stale
    manifest, an empty schema builder, or an adapter that silently drops `tools` while constructing
    the envelope is caught here even when everything upstream looked fine.

    `native_tools_in_envelope` and `structured_fallback_in_envelope` are deliberately both
    acceptable: a model with no native tool-calling gets the documented compatibility dialect
    (a schema-constrained structured-output request) instead, per this project's own mandate that
    the dialect "must still make the model choose and emit a tool call in a documented structured
    format" -- that is not a defect, only carrying NEITHER is.
    """

    if not tools_required:
        return
    if offered_tool_count <= 0:
        raise RequiredToolsNotOfferedError(
            f"REQUIRED_TOOLS_NOT_OFFERED: lane={lane_name or 'unknown'} offered=0 -- "
            "the turn requires tools but the schema builder produced none"
        )
    if not native_tools_in_envelope and not structured_fallback_in_envelope:
        raise RequiredToolsNotOfferedError(
            f"REQUIRED_TOOLS_NOT_OFFERED: lane={lane_name or 'unknown'} offered={offered_tool_count} -- "
            "the built envelope carries neither native tools nor a structured-output fallback"
        )
