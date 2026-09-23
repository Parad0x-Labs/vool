"""The typed plan for a LIVE_DATA request: one subtask per named asset or city, before execution.

Why a new module rather than extending `turn_planner.PlannedTask`
-------------------------------------------------------------------
`turn_planner.py` splits a message into independent REQUESTS ("gold, silver, bitcoin and bnb
price" is one request, "weather in Kaunas, Tallinn, and Warsaw" is another) -- each `PlannedTask`
carries a raw text string, not typed per-entity arguments. That granularity is right for its own
job (deciding whether a message needs to be answered in more than one sub-turn) but wrong for this
one: the benchmark this module exists to satisfy needs SEVEN typed subtasks (four assets, three
cities), each with an immutable id, a typed operation, typed arguments, and its own lifecycle and
result -- not two prose-carrying requests.

Retrofitting `turn_planner.PlannedTask` into a fully-typed, per-entity model would be a much larger
and riskier change spanning every request class it already serves correctly (40+ passing tests
today). Building a dedicated, narrowly-scoped model for the LIVE_DATA answer_mode specifically is
the smaller, safer repair -- it composes with `turn_planner` rather than replacing it: nothing here
stops a message from ALSO being split by the general planner first.

Where the entities come from
-----------------------------
Every asset and city in a plan is discovered by the SAME recognizers the deterministic fetch lanes
already use to answer these requests (`_looks_like_market_quote_query_all`,
`_looks_like_price_query_all`, `_extract_weather_locations`) -- never a new keyword list, and never
free text handed straight to a tool argument. This is what makes it structurally impossible for the
full user prompt to become a city or ticker: an entity only ever enters a subtask's `arguments`
after already passing through the same plausibility guard the live fetch itself trusts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.turn_contract import LaneProposal


class SubtaskLifecycle(str, Enum):
    PLANNED = "planned"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    # An entity the user explicitly named that does not resolve to any known, fetchable target --
    # distinct from FAILED, which means a fetch was attempted and the network/provider failed.
    # UNSUPPORTED_ENTITY means no fetch was ever attempted, because there is nothing registered to
    # fetch: "copper" (no MarketQuoteTarget registered) and "MadeUpCoin" (no crypto alias, no
    # coin_index match) both land here. The subtask exists and is reported, never silently
    # dropped -- that is the whole reason this state exists.
    UNSUPPORTED_ENTITY = "unsupported_entity"


@dataclass(frozen=True)
class LiveDataSubtask:
    """One typed lookup: one asset's price, or one city's weather. Immutable once planned."""

    subtask_id: str
    entity: str
    operation: str                       # "market_quote" | "weather_lookup"
    arguments: dict[str, Any]
    required_result_fields: tuple[str, ...]
    tool: str                            # "market_prices" | "weather"
    tool_intent: str                     # the permission-checkable intent, e.g. "web.research"
    can_run_in_parallel: bool = True
    # The request clause this subtask was dispatched for — `turn_slices(user_text)`'s id for
    # the clause the recognizer fired in (PLAN-discharge-channel.md B1). This is the span
    # provenance that lets the serving lane write a receipt naming WHAT it served: without
    # it nothing in the executed plan knows which part of the request it was dispatched for.
    # "" (never guessed) = the subtask's span could not be bound; it may not contribute to
    # any receipt. See `build_live_data_plan` for where it is populated.
    slice_id: str = ""
    # The DEMAND units (`answer_coverage.demand_units`) whose own spans contain this
    # subtask's needle — the unit-grain binding the receipt is written against. Clause
    # grain is coarser than the demand set (units sub-split a clause but inherit its
    # slice_id), so a clause-grain receipt absolves co-clause siblings nothing served —
    # measured live 2026-08-30 (Incident 3): one London weather receipt discharged the
    # diesel, petrol and Riga-Vilnius demands sharing its clause. () (never guessed) =
    # no unit's span carries the needle; the subtask renders as an unbindable dispatch.
    unit_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "entity": self.entity,
            "operation": self.operation,
            "arguments": dict(self.arguments),
            "required_result_fields": list(self.required_result_fields),
            "tool": self.tool,
            "tool_intent": self.tool_intent,
            "can_run_in_parallel": self.can_run_in_parallel,
            "slice_id": self.slice_id,
            "unit_ids": list(self.unit_ids),
        }


@dataclass
class SubtaskOutcome:
    """What actually happened to one subtask -- separate from the immutable subtask itself so a
    retry can start a fresh outcome against the same subtask identity."""

    subtask: LiveDataSubtask
    state: SubtaskLifecycle = SubtaskLifecycle.PLANNED
    result: dict[str, Any] | None = None
    failure_reason: str = ""
    approval_decision: str = ""          # "" (not evaluated) | "allow" | "require_approval" | "deny"
    # Timing telemetry -- monotonic seconds (core.timing-free `time.monotonic()`), for interval-
    # overlap math immune to wall-clock adjustment; `queued_at_iso`/`started_at_iso`/
    # `completed_at_iso` carry the human-readable wall-clock timestamp for reporting/Activity.
    # All default None: nothing outside the runner is required to populate them, and nothing that
    # constructs a SubtaskOutcome without them (e.g. render/plan tests) needs to change.
    queued_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    queued_at_iso: str = ""
    started_at_iso: str = ""
    completed_at_iso: str = ""

    @property
    def ok(self) -> bool:
        return self.state is SubtaskLifecycle.SUCCEEDED and self.result is not None

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return self.completed_at - self.started_at

    def overlaps(self, other: SubtaskOutcome) -> bool:
        """Whether this subtask's RUNNING interval overlapped `other`'s, at all."""
        if self.started_at is None or self.completed_at is None:
            return False
        if other.started_at is None or other.completed_at is None:
            return False
        return self.started_at < other.completed_at and other.started_at < self.completed_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask.subtask_id,
            "entity": self.subtask.entity,
            "state": self.state.value,
            "result": self.result,
            "failure_reason": self.failure_reason,
            "approval_decision": self.approval_decision,
            "queued_at": self.queued_at_iso,
            "started_at": self.started_at_iso,
            "completed_at": self.completed_at_iso,
            "duration_s": self.duration_s,
        }


@dataclass(frozen=True)
class LiveDataPlan:
    """The complete, inspectable plan for a LIVE_DATA turn. Exists before any tool executes."""

    plan_id: str
    attempt_id: str
    original_request: str
    subtasks: tuple[LiveDataSubtask, ...]
    # M3: the canonical demand units this plan did NOT bind any subtask to, recorded
    # explicitly at build time. The lane CONSUMES the canonical set it was handed --
    # a unit it cannot serve is named here, never silently absent (the Incident-3
    # class). The sweep still owns terminal dispositions; this is the plan-side
    # conservation record the claim/demand census reads.
    unclaimed_unit_ids: tuple[str, ...] = ()

    def market_subtasks(self) -> tuple[LiveDataSubtask, ...]:
        return tuple(
            task for task in self.subtasks if task.operation in ("market_quote", "unsupported_market_entity")
        )

    def weather_subtasks(self) -> tuple[LiveDataSubtask, ...]:
        return tuple(task for task in self.subtasks if task.operation == "weather_lookup")

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "attempt_id": self.attempt_id,
            "original_request": self.original_request,
            "subtasks": [task.to_dict() for task in self.subtasks],
        }


def build_live_data_plan(
    user_text: str,
    *,
    plan_id: str,
    attempt_id: str,
    source_context: dict | None = None,
    canonical_units: tuple | None = None,
) -> LiveDataPlan | None:
    """The typed plan for a LIVE_DATA request, or `None` when this text isn't one.

    Every asset and city comes from the same recognizers the deterministic fetch lanes already
    trust (see the module docstring). `ExecutionRequirements` decides WHETHER this is a LIVE_DATA
    turn at all -- this function does not re-derive that decision, it only enumerates the
    entities once the caller already knows the answer is yes.

    M3: `canonical_units` is the turn's minted demand set (the run_once spine's
    authority) handed IN for consumption. When provided, every unit not bound to a
    subtask is recorded on the plan as `unclaimed_unit_ids` -- the lane's
    conservation record. The lane no longer owns demand truth; it consumes the
    identity it is given. Callers that omit it keep the legacy behavior (the
    tests' direct-call shape), so this is a consuming seam, not a break.

    A bare continuation ("and?", "well?") is resolved to the request it re-asks BEFORE either step,
    because both need it: the requirement is derived from the prior text, and so is entity
    extraction -- "and?" names no city, so a plan built from it would enumerate nothing and the
    turn would fall through to the model with no evidence, which is the failure this resolves.
    """
    from core.execution_requirements import requirements_for
    from core.live_data_continuation import continuation_inheritance
    from core.retrieval_constraints import retrieval_candidate_text

    raw_text = str(user_text or "")
    inherited, inherit_kind = continuation_inheritance(user_text, source_context=source_context)
    effective_text = inherited or user_text

    requirements = requirements_for(effective_text, source_context=source_context)
    if requirements.answer_mode != "LIVE_DATA":
        return None
    candidate_text = retrieval_candidate_text(effective_text)
    if not candidate_text:
        return None
    user_text = effective_text

    subtasks: list[LiveDataSubtask] = []
    seen_market_keys: set[str] = set()

    if "market_prices" in requirements.allowed_toolsets:
        from core.agent_runtime.fast_live_info_price import price_assets_named
        from tools.web.web_research import (
            _looks_like_market_quote_query_all,
            _looks_like_price_query_all,
        )

        for target in _looks_like_market_quote_query_all(candidate_text):
            if target.asset_key in seen_market_keys:
                continue
            seen_market_keys.add(target.asset_key)
            # B2 INTERIM span binding — see _interim_slice_id / _interim_unit_ids.
            needles = (
                *target.aliases, target.asset_key.replace("-", " "), target.asset_name
            )
            slice_id = _interim_slice_id(user_text, *needles)
            unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, *needles, domain="market")
            subtasks.append(
                _market_subtask(
                    plan_id, target.asset_key, target.asset_name, kind="commodity",
                    slice_id=slice_id, unit_ids=unit_ids,
                )
            )

        for coin_id in _looks_like_price_query_all(candidate_text):
            if coin_id in seen_market_keys:
                continue
            seen_market_keys.add(coin_id)
            # B2 INTERIM span binding. The recognizer returns the canonical coin id, not the
            # surface token ("btc" -> "bitcoin"), so the aliases that resolve to this id are
            # candidates too — the needle the recognizer actually fired on is one of them.
            from tools.web.web_research import _CRYPTO_ALIASES

            alias_needles = tuple(
                alias for alias, mapped in _CRYPTO_ALIASES.items() if mapped == coin_id
            )
            needles = (coin_id, coin_id.replace("-", " "), *alias_needles)
            slice_id = _interim_slice_id(user_text, *needles)
            unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, *needles, domain="market")
            subtasks.append(
                _market_subtask(
                    plan_id, coin_id, coin_id.replace("-", " ").title(), kind="crypto",
                    slice_id=slice_id, unit_ids=unit_ids,
                )
            )

        # `requirements_for()` (via `_live_data_classification`) also consults `price_assets_named`,
        # which names an asset by presence alone -- no "price"/"cost"/"$" keyword required. The two
        # functions above both gate on `_PRICE_KEYWORDS` first, so a phrasing like "Market data only
        # for Bitcoin and gold." (no literal "price" word) was classified LIVE_DATA by the
        # requirements authority but then built ZERO subtasks here -- found live: the turn fell
        # through to the model with tools_required=True and no plan able to satisfy it, and the
        # model produced an unrelated, token-burning response. Resolve any alias
        # `price_assets_named` still finds after the keyword-gated pass, so the two can never
        # disagree about whether an asset was named again.
        # SENTINEL G4, 2026-08-06: this pass resolves an asset by NAME ALONE, deliberately skipping
        # the market-intent gate the two loops above apply -- which is how a semantic request
        # ("Explain gold structure.") became a `market_quote` subtask once
        # `_live_data_classification` had wrongly admitted the turn. That classifier is repaired at
        # its own seam, and THAT is the fix; the same gate is repeated here only so this loop's own
        # local contract ("presence is enough") cannot be read as still true by someone editing it.
        #
        # MEASURED, and corrected from an earlier claim on this block: this gate is currently
        # INERT. ARGUS found it behaviour-neutral across its whole attack corpus, and re-measuring
        # confirms it -- every path that reaches this loop has already passed
        # `_live_data_classification`, which now applies the identical `market_semantics_present`
        # check, so this one can only ever agree. It is NOT an independently proven second line of
        # defence and must not be described as one; it is a guard against a FUTURE caller that
        # reaches this loop by another door, and it earns its place only if such a caller appears.
        # Left in place, unchanged, because removing it is a behaviour-neutral edit to a reviewed
        # seam with no defect requiring it.
        from core.market_intent import market_semantics_present

        presence_pass_allowed = market_semantics_present(candidate_text)
        # The live-data classification IS this turn's independent domain
        # evidence (presence_pass_allowed gates the loop below), so asset
        # mentions resolve under the domain-established rule rather than
        # re-litigating a per-mention anchor they may not carry. Measured live:
        # "1000 usd to eur and then to gold? also btc price and 24 change on
        # eth" planned gold+bitcoin only -- eth's anchor ("change on eth") sat
        # outside the per-mention binding window and eth was silently dropped.
        # Negation withdrawal still applies inside the flag
        # (`mention_is_market_authorized` checks `market_terms_are_negated`).
        for alias in price_assets_named(
            candidate_text, domain_already_authorized=True
        ) if presence_pass_allowed else ():
            resolved = _resolve_price_alias(alias)
            if resolved is None:
                continue
            asset_key, kind, asset_name = resolved
            if asset_key in seen_market_keys:
                continue
            seen_market_keys.add(asset_key)
            # B2 INTERIM span binding — `alias` here IS the surface token that matched.
            needles = (alias, asset_name, asset_key.replace("-", " "))
            slice_id = _interim_slice_id(user_text, *needles)
            unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, *needles, domain="market")
            subtasks.append(
                _market_subtask(
                    plan_id, asset_key, asset_name, kind=kind,
                    slice_id=slice_id, unit_ids=unit_ids,
                )
            )

        # Every entity the user explicitly named must produce a subtask -- resolved (above) or
        # explicitly UNSUPPORTED_ENTITY (here), never silent omission. Found live: "Ethereum,
        # Solana, oil, and copper" answered ethereum+solana only, with oil/copper simply absent --
        # not wrong, but not honest either: the user cannot tell "I chose not to look those up"
        # from "the runtime never even understood you asked". `_extract_market_entity_candidates`
        # finds the SAME list `price_assets_named` would already have covered if resolvable; only
        # a genuinely unresolvable candidate reaches this loop.
        from tools.web.web_research import _extract_market_entity_candidates

        seen_unsupported: set[str] = set()
        for candidate in _extract_market_entity_candidates(candidate_text):
            mentions = _residual_mentions(candidate, seen_market_keys)
            for mention, resolved in mentions:
                if resolved is not None:
                    asset_key, kind, asset_name = resolved
                    if asset_key in seen_market_keys:
                        continue
                    seen_market_keys.add(asset_key)
                    # B2 INTERIM span binding — the mention is the surface text that fired.
                    needles = (mention, asset_name)
                    slice_id = _interim_slice_id(user_text, *needles)
                    unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, *needles, domain="market")
                    subtasks.append(
                        _market_subtask(
                            plan_id, asset_key, asset_name, kind=kind,
                            slice_id=slice_id, unit_ids=unit_ids,
                        )
                    )
                    continue
                if mention in seen_unsupported:
                    continue
                seen_unsupported.add(mention)
                # B2 INTERIM span binding.
                slice_id = _interim_slice_id(user_text, mention)
                unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, mention, domain="market")
                subtasks.append(
                    _unsupported_market_subtask(
                        plan_id, mention, slice_id=slice_id, unit_ids=unit_ids
                    )
                )

    if "market_prices" in requirements.allowed_toolsets:
        # TICKERS WRITTEN AS TICKERS (`$BASE`, "BASE coin"): the notation itself names a market
        # symbol, so each one is an obligation of this plan -- served when the curated tables or
        # the coin index resolve it, and otherwise STATED as an unsupported entity carrying the one
        # identifying question (the asset's full name). Measured on the owner's turns (2026-09-10,
        # c647b707, FINDINGS F15): "check $BASE price" and "$BASE coin ... the value of it" named
        # no curated alias, built no plan, and a model answered that it had no pricing.
        from core.agent_runtime.fast_live_info_price import ticker_mentions

        for symbol in ticker_mentions(candidate_text, require_market_binding=True):
            lowered = symbol.casefold()
            resolved = _resolve_price_alias(lowered)
            if resolved is not None:
                asset_key, kind, asset_name = resolved
                if asset_key in seen_market_keys:
                    continue
                seen_market_keys.add(asset_key)
                needles = (lowered, f"${lowered}", asset_name, asset_key.replace("-", " "))
                slice_id = _interim_slice_id(user_text, *needles)
                unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, *needles, domain="market")
                subtasks.append(
                    _market_subtask(
                        plan_id, asset_key, asset_name, kind=kind, slice_id=slice_id, unit_ids=unit_ids,
                    )
                )
                continue
            if any(
                str(dict(task.arguments or {}).get("requested_text") or "").casefold() == lowered
                for task in subtasks
                if task.operation == "unsupported_market_entity"
            ):
                continue
            from core.conductor.operations import unresolved_ticker_question

            slice_id = _interim_slice_id(user_text, lowered, f"${lowered}")
            unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, lowered, domain="market")
            subtasks.append(
                _unsupported_market_subtask(
                    plan_id, symbol, slice_id=slice_id, unit_ids=unit_ids,
                    reason=unresolved_ticker_question(symbol),
                )
            )

    if "weather" in requirements.allowed_toolsets:
        # Water asks are extracted FIRST and their spans blanked before the weather
        # extractor reads the same text: "sea temp near Palma" is a water-temperature
        # clause, and letting the weather family claim it produced exactly the
        # wrong-domain mis-claims measured live (weather_lookup "found nothing to act
        # on" for sea asks; solo water asks fell to the model, which improvised
        # unsourced prose). One text, two families, disjoint spans.
        from core.fresh_data.water_temperature import extract_water_asks
        from tools.web.web_research import _extract_weather_locations_with_confidence

        weather_text = candidate_text
        for water_place, (span_start, span_end) in extract_water_asks(candidate_text):
            # B2 INTERIM span binding. The extractor reports a span into `candidate_text`,
            # which need not share offsets with `user_text` (the text the slices are cut
            # from), so the place is matched the same interim way as every other site.
            water_slice_id = _interim_slice_id(user_text, water_place)
            water_unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, water_place, domain="weather")
            subtasks.append(
                _water_subtask(
                    plan_id, water_place, slice_id=water_slice_id, unit_ids=water_unit_ids
                )
            )
            weather_text = (
                weather_text[:span_start]
                + " " * (span_end - span_start)
                + weather_text[span_end:]
            )
        for location, confident in _extract_weather_locations_with_confidence(weather_text):
            # B2 INTERIM span binding.
            slice_id = _interim_slice_id(user_text, location)
            unit_ids = _bind_unit_ids(raw_text, user_text, inherit_kind, location, domain="weather")
            subtasks.append(
                _weather_subtask(
                    plan_id, location, confident=confident, slice_id=slice_id, unit_ids=unit_ids
                )
            )

    if not subtasks:
        return None
    # M3 CONSERVATION: consume the canonical set -- a unit no subtask bound is
    # NAMED here, never silently absent. No re-minting: the units are the caller's.
    unclaimed_unit_ids: tuple[str, ...] = ()
    if canonical_units:
        bound = {unit_id for task in subtasks for unit_id in task.unit_ids}
        unclaimed_unit_ids = tuple(
            unit.unit_id for unit in canonical_units if unit.unit_id not in bound
        )
    return LiveDataPlan(
        plan_id=plan_id,
        attempt_id=attempt_id,
        original_request=user_text,
        subtasks=tuple(subtasks),
        unclaimed_unit_ids=unclaimed_unit_ids,
    )


def _resolve_price_alias(alias: str) -> tuple[str, str, str] | None:
    """(asset_key, kind, display_name) for a bare alias name `price_assets_named` found, or None.

    Same two lookup tables `_looks_like_price_query_all`/`_looks_like_market_quote_query_all`
    already resolve against (`_CRYPTO_ALIASES`, `_MARKET_QUOTE_TARGETS`) -- deliberately without
    the `_PRICE_KEYWORDS` gate those two apply first, since the caller already has independent
    confirmation (from `price_assets_named`) that this alias names an asset in this message.

    The coin index is the third leg, and its absence was the duplication defect: the plan's
    primary loops resolve index-only tickers (TRX -> tron, USDC -> usd-coin) into canonical
    subtasks, while the entity-candidate loop asked THIS function -- static tables only --
    whether the same surface token was servable, got None, and minted a second, phantom
    "unsupported_market_entity" obligation for the SAME requested asset. One requested item
    became two accounted items (measured live at d5cc8637: 3 requested, 5 in the plan; BNB
    escaped only by happening to sit in the static table). Consulting the same membership
    authority here is what makes the existing `seen_market_keys` dedupe an identity dedupe:
    the alias binds to its canonical id, and the canonical id is already planned.
    """
    from tools.web.web_research import _CRYPTO_ALIASES, _MARKET_QUOTE_TARGETS

    crypto_id = _CRYPTO_ALIASES.get(alias)
    if crypto_id:
        return crypto_id, "crypto", crypto_id.replace("-", " ").title()
    for target in _MARKET_QUOTE_TARGETS:
        if alias in target.aliases:
            return target.asset_key, "commodity", target.asset_name
    from tools.web.coin_index import resolve_symbol

    indexed = resolve_symbol(alias)
    if indexed:
        return indexed, "crypto", indexed.replace("-", " ").title()
    return None


#: Words that are never an asset name: temporal qualifiers, politeness, and question filler that
#: trail a real request ("price of gold NOW", "gold TODAY PLEASE"). The exact spellings are already
#: stripped upstream; what reaches the residual loop is the MISSPELLINGS, and a typo of "now" is
#: not a request for an unknown asset. Matched with edit distance <= 1 for tokens of 3+ characters,
#: mirroring the misspelling tolerance the weather extractor already applies ("wheather", "weathr").
#: Only tokens that ALREADY failed alias resolution are tested, so a real asset can never be eaten
#: by this: it would have resolved before reaching here.
_NON_ENTITY_FILLER = frozenset({
    "now", "today", "tomorrow", "tonight", "currently", "current", "please", "right",
    "this", "week", "weekend", "here", "there", "then", "again", "still", "just",
    "much", "many", "what", "whats", "how", "the", "and", "for", "with", "about",
    # Pronouns and contracted auxiliaries of a question's tail ("... the value of it isnt?"):
    # never an unknown asset. Measured on the owner's `$BASE coin` turn (2026-09-10) -- and
    # identically on the untouched c647b707 tree -- "it isnt" minted a phantom "It Isnt --
    # not a recognized market entity" row beside the real unsupported ticker.
    "it", "its", "isnt", "isn", "arent", "aren", "wasnt", "dont", "doesnt", "didnt",
})


def _within_edit_distance_one(token: str, target: str) -> bool:
    """True when `token` is `target`, or one substitution/insertion/deletion away from it."""
    if token == target:
        return True
    length_token, length_target = len(token), len(target)
    if abs(length_token - length_target) > 1:
        return False
    if length_token == length_target:  # one substitution
        return sum(1 for a, b in zip(token, target, strict=False) if a != b) == 1
    shorter, longer = (token, target) if length_token < length_target else (target, token)
    index_short = index_long = 0
    skipped = False
    while index_short < len(shorter) and index_long < len(longer):
        if shorter[index_short] == longer[index_long]:
            index_short += 1
            index_long += 1
            continue
        if skipped:
            return False
        skipped = True
        index_long += 1
    return True


def _is_non_entity_filler(token: str) -> bool:
    """True when a token that failed alias resolution is ordinary filler, not an unknown asset."""
    word = str(token or "").strip().lower()
    if not word or not word.isalpha():
        return False
    if word in _NON_ENTITY_FILLER:
        return True
    if len(word) < 3:
        return False
    # A transposition ("nwo" for "now") is two substitutions under plain edit distance, so compare
    # sorted letters for equal-length candidates as well -- transposed typos are the common case.
    for filler in _NON_ENTITY_FILLER:
        if len(filler) < 3:
            continue
        if _within_edit_distance_one(word, filler):
            return True
        if len(word) == len(filler) and sorted(word) == sorted(filler):
            return True
    return False


def _residual_mentions(
    candidate: str, owned_asset_keys: set[str]
) -> list[tuple[str, tuple[str, str, str] | None]]:
    """Decompose a raw candidate segment into obligation identities: (mention, resolution).

    ONE SOURCE MENTION -> ONE SEMANTIC OBLIGATION. A segment that resolves as a whole is one
    mention. A fused segment is judged per word against the same membership authorities:
    a word whose canonical id is already an obligation in this plan is CONSUMED by it --
    never a second row, and never fused into a neighbor's row; a word that resolves but is
    not yet planned is its own servable mention; a run of words that resolve nowhere is ONE
    unsupported mention under its own identity. Measured live at d5cc8637: judging the whole
    fused segment produced "Xrp Hype -- not a recognized market entity" beside the executed
    ripple and hyperliquid subtasks, and "Bch Usde" beside the executed bitcoin-cash one --
    one requested item both fulfilled and unresolved, and a valid item fused with its unknown
    neighbor. The unknown neighbor's identity is the mention that remains ("usde"), never the
    span that was already served.
    """
    whole = _resolve_price_alias(candidate)
    if whole is not None:
        return [(candidate, whole)]
    mentions: list[tuple[str, tuple[str, str, str] | None]] = []
    unresolved: list[str] = []
    for word in candidate.split():
        # Filler that survived upstream trimming (usually misspelled) is noise attached to a real
        # request, never a second asset. Measured live 2026-08-29: "price of gold nwo" planned an
        # `unsupported_market_entity` for "nwo", which rendered a phantom "Nwo -- not a recognized
        # market entity" row AND, by making the turn look like a two-asset comparison, attached a
        # "Largest absolute 24-hour mover" line the operator never asked for.
        if _is_non_entity_filler(word):
            continue
        resolved = _resolve_price_alias(word)
        if resolved is not None:
            if resolved[0] in owned_asset_keys:
                continue
            if unresolved:
                mentions.append((" ".join(unresolved), None))
                unresolved = []
            mentions.append((word, resolved))
            continue
        unresolved.append(word)
    if unresolved:
        mentions.append((" ".join(unresolved), None))
    return mentions


def _interim_slice_id(user_text: str, *needles: str) -> str:
    """INTERIM span binding (PLAN-discharge-channel.md §4 B2) — the slice whose text contains
    the recognized entity, at word boundaries.

    INTERIM, and marked as such where it is used: the correct binding is for the entity
    recognizers to report the character offset they matched and select the slice whose span
    contains that offset. Until the recognizers speak offsets, this matches the token the
    recognizer actually fired on — at MINT time, against the request text. That is
    categorically different from the defect this change repairs (matching ANSWER PROSE
    against QUESTION PROSE at reconciliation time): this is provenance assignment on the
    input, not coverage inference over the output. It is still text matching, and it must
    never be mistaken for the receipt itself.

    No match, or nothing to match with, returns "" — the caller leaves the span unbound and
    the unit stays `indeterminate`. NEVER GUESS A SPAN: an unknown span must never become a
    receipt.
    """
    from core.agent_runtime.answer_coverage import turn_slices

    ordered: list[str] = []
    for needle in needles:
        token = " ".join(str(needle or "").lower().split())
        if token and token not in ordered:
            ordered.append(token)
    if not ordered or not str(user_text or ""):
        return ""
    slices = turn_slices(user_text)
    if not slices:
        return ""
    # Longest needle first so "gold spot" binds the clause before bare "gold" claims it;
    # within one needle, the earliest slice in the user's own order wins.
    for needle in sorted(ordered, key=len, reverse=True):
        pattern = re.compile(
            r"\b" + r"\s+".join(re.escape(part) for part in needle.split(" ")) + r"\b"
        )
        for item in slices:
            if pattern.search(item.text.lower()):
                return item.slice_id
    return ""


def _bind_unit_ids(raw_text: str, effective_text: str, inherit_kind: str, *needles: str, domain: str = "") -> tuple[str, ...]:
    """The CANONICAL demand units this subtask may claim (CT-302, the receipt-binding law).

    Unit ids are positional in the text they were minted from. The canonical set is minted from
    the RAW request; a subtask planned from an INHERITED request ("what is gold price now?" behind
    "and the weather there?") used to bind ids minted in the inherited text's space, so `u1` of the
    gold quote discharged `u1` of the raw text -- a weather question -- and the census certified a
    wrong answer as covered (the recorded incident's "1 of 4 claimed").

    A binding must be verifiable in the canonical space, by an explicit mapping:
    * no inheritance -- the raw text IS the effective text; bind as before;
    * a REBIND or a CLARIFICATION named the subject in the raw text -- bind only the raw units whose
      own text carries the entity (verbatim, or the user's one-typo spelling of it);
    * a bare RE-ASK or an AGGREGATE said nothing but "that again" / "which of those" -- the whole
      raw turn maps onto the inherited request, so every raw unit is served by its subtasks;
    * anything else -- nothing binds, and the unit is named in `unclaimed_unit_ids`.
    """
    if not inherit_kind:
        return _interim_unit_ids(effective_text, *needles)
    from core.live_data_continuation import (
        INHERIT_AGGREGATE,
        INHERIT_REASK,
        typo_tokens_for,
    )
    direct = _interim_unit_ids(raw_text, *needles)
    if direct:
        return direct
    spelled = typo_tokens_for(raw_text, *needles)
    if spelled:
        bound = _interim_unit_ids(raw_text, *spelled)
        if bound:
            return bound
    if inherit_kind in (INHERIT_REASK, INHERIT_AGGREGATE):
        # The whole raw turn maps onto the inherited request ONLY if it names nothing of its own.
        # A raw turn that names the OTHER live-data domain ("and the weather there?" behind a gold
        # quote; "and the price?" behind a weather thread) is not a re-ask of this obligation,
        # whatever the continuation module read it as (CT-201, recorded separately): the receipt
        # may not discharge a demand whose text asks for a different kind of thing.
        if domain == "market":
            from tools.web.web_research import _looks_like_weather_query
            if _looks_like_weather_query(raw_text):
                return ()
        elif domain == "weather":
            from core.market_intent import market_semantics_present
            if market_semantics_present(raw_text):
                return ()
        from core.agent_runtime.answer_coverage import demand_units
        return tuple(unit.unit_id for unit in demand_units(raw_text))
    return ()


def _interim_unit_ids(user_text: str, *needles: str) -> tuple[str, ...]:
    """INTERIM unit-grain span binding — the demand units whose own spans carry the needle.

    Same needles, same normalization and same INTERIM contract as `_interim_slice_id`
    (the recognizers do not yet report offsets), one grain finer: clause grain absolves
    co-clause siblings nothing served, because `demand_units` sub-splits a clause while
    inheriting its slice_id. No match returns () — never guess a span; a subtask with
    no unit binding renders as an unbindable dispatch, never as a receipt.
    """
    from core.agent_runtime.answer_coverage import units_matching_needle

    return units_matching_needle(user_text, *needles)


def _market_subtask(
    plan_id: str,
    asset_key: str,
    asset_name: str,
    *,
    kind: str,
    slice_id: str = "",
    unit_ids: tuple[str, ...] = (),
) -> LiveDataSubtask:
    return LiveDataSubtask(
        subtask_id=f"{plan_id}:market:{asset_key}",
        entity=asset_name,
        operation="market_quote",
        arguments={"asset_key": asset_key, "kind": kind},
        required_result_fields=("price", "currency", "change_24h_pct", "source", "retrieved_at"),
        tool="market_prices",
        tool_intent="web.research",
        slice_id=slice_id,
        unit_ids=unit_ids,
    )


def _unsupported_market_subtask(
    plan_id: str,
    candidate: str,
    *,
    slice_id: str = "",
    unit_ids: tuple[str, ...] = (),
    reason: str = "",
) -> LiveDataSubtask:
    """A named entity that does not resolve to any registered crypto/commodity target.

    `operation="unsupported_market_entity"` is a distinct value from `"market_quote"` on purpose:
    the runner must never attempt to fetch this (there is nothing to fetch), and the renderer must
    still show it, marked unavailable, rather than it disappearing between plan and answer.
    """
    safe_id = "_".join(candidate.split())
    arguments: dict[str, Any] = {"requested_text": candidate}
    if str(reason or "").strip():
        # The reader-facing reason the runner reports for this entity -- a ticker's identifying
        # question, never a guess at which asset it might be (FINDINGS F15).
        arguments["reason"] = str(reason).strip()
    return LiveDataSubtask(
        subtask_id=f"{plan_id}:market:unsupported:{safe_id}",
        entity=candidate.title() if not candidate.isupper() else candidate,
        operation="unsupported_market_entity",
        arguments=arguments,
        required_result_fields=(),
        tool="market_prices",
        tool_intent="web.research",
        slice_id=slice_id,
        unit_ids=unit_ids,
    )


def _weather_subtask(
    plan_id: str,
    location: str,
    *,
    confident: bool = True,
    slice_id: str = "",
    unit_ids: tuple[str, ...] = (),
) -> LiveDataSubtask:
    """`confident=False` marks a candidate `_extract_weather_locations_with_confidence` could not
    bound on both sides -- the trailing remainder of a list with nothing after it but the clause's
    own end (see that function's docstring, and `_split_weather_candidates`'s). Carried as
    `arguments["extraction_confidence"]` so the runner (`_run_weather_subtask`) can hold it to a
    tighter bar before ever reaching the network, rather than trusting wttr.in's own fuzzy match
    to decide whether it was real -- Mnemosyne review round 3's explicit requirement that provider
    success must never stand in for validation.
    """
    safe_id = "_".join(location.split()).replace(",", "")
    return LiveDataSubtask(
        subtask_id=f"{plan_id}:weather:{safe_id}",
        entity=location.title(),
        operation="weather_lookup",
        arguments={
            "location": location,
            "requested_text": location,
            "extraction_confidence": "structural" if confident else "tail_fallback",
        },
        required_result_fields=("condition", "source", "observed_at"),
        tool="weather",
        tool_intent="web.research",
        slice_id=slice_id,
        unit_ids=unit_ids,
    )


def _water_subtask(
    plan_id: str,
    place: str,
    *,
    slice_id: str = "",
    unit_ids: tuple[str, ...] = (),
) -> LiveDataSubtask:
    """One sea/water-temperature observation ask, as typed by the user.

    The place stays verbatim (typos included) so the ledger row shows what was
    asked; `core.fresh_data.water_temperature.resolve_water_point` owns turning
    it into a point (named-sea anchors with typo tolerance, else geocoding).
    """
    safe_id = "_".join(place.split()).replace(",", "")
    return LiveDataSubtask(
        subtask_id=f"{plan_id}:water:{safe_id}",
        entity=place,
        operation="water_temperature",
        arguments={"place": place, "requested_text": place},
        required_result_fields=("temperature_c", "label", "source"),
        tool="water",
        tool_intent="web.research",
        slice_id=slice_id,
        unit_ids=unit_ids,
    )


def evaluate_approval_policy(
    plan: LiveDataPlan,
    *,
    source_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Every subtask's live approval decision under the CURRENT operating mode, keyed by subtask id.

    A plan's typed shape (entity/operation/arguments) never changes with the operating mode; what
    IS allowed to run automatically does. This calls the real, single permission gate
    (`core.mode_permission_policy.decide_tool_call`) per subtask rather than duplicating or
    precomputing a boolean at plan-construction time, so a mode change between planning and
    execution is read correctly and this can never drift from what the gate actually decides for
    an equivalent direct tool call.
    """
    from core.mode_permission_policy import decide_tool_call

    decisions: dict[str, Any] = {}
    for index, task in enumerate(plan.subtasks):
        decisions[task.subtask_id] = decide_tool_call(
            intent=task.tool_intent,
            arguments=task.arguments,
            task_id=f"{plan.attempt_id}:{index}",
            source_context=source_context,
        )
    return decisions


# ------------------------------------------------------------------ M2 slice 3: the typed claim


#: The live-data lane's identity and its long-standing route confidence -- the two
#: literals this lane used to scatter across its call sites; the LaneProposal is
#: now their single source (M2 slice 3's displacement).
LIVE_DATA_LANE_ID = "live_data_typed_plan"
LIVE_DATA_LANE_CONFIDENCE = 0.9


def lane_proposal_for_plan(
    plan: LiveDataPlan, requirements: Any
) -> LaneProposal:
    """The typed claim the live-data lane makes for a plan it built.

    Every field is derived from what the lane already holds -- the plan's unit
    bindings (M3B), the requirements authority's toolsets, each subtask's
    checkable tool intent and result-field contract -- so the proposal is the
    lane's existing claim shape, typed; nothing is inferred that the plan does
    not already state.
    """
    claimed: list[str] = []
    effects: list[str] = []
    evidence: list[str] = []
    for task in plan.subtasks:
        for unit_id in task.unit_ids:
            if unit_id not in claimed:
                claimed.append(unit_id)
        if task.tool_intent and task.tool_intent not in effects:
            effects.append(task.tool_intent)
        for field_name in task.required_result_fields:
            if field_name not in evidence:
                evidence.append(field_name)
    return LaneProposal(
        lane_id=LIVE_DATA_LANE_ID,
        obligations_claimed=tuple(claimed),
        unclaimed_obligations=tuple(getattr(plan, "unclaimed_unit_ids", ()) or ()),
        required_capabilities=tuple(requirements.allowed_toolsets),
        effects_requested=tuple(effects),
        evidence_expected=tuple(evidence),
        confidence=LIVE_DATA_LANE_CONFIDENCE,
    )


def declined_live_data_proposal(reason: str) -> LaneProposal:
    """The typed decline: what M4's 'record why every lane declined' builds on --
    a refusal reason instead of a silent None."""
    return LaneProposal(
        lane_id=LIVE_DATA_LANE_ID,
        refusal_reason=str(reason or "declined"),
        terminal_eligibility="none",
    )
