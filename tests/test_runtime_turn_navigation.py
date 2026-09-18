"""Turn navigation state and the one offer per model round (revision 6, Gate B).

A model's ``capability.expand_family`` call used to be read-and-clear: the first consumer in a model
round (the prompt catalog) took it, and the native tool definitions the model can actually call never
carried the family. ``tests/test_runtime_release_gates.py`` reproduces that unchanged. The contract
pinned here:

* an expansion is navigation state owned by its turn scope -- session, turn id and the scope token of
  the loop that owns the turn's model rounds (``core.tool_offer_state``). Reading it never clears it,
  a later round of the same turn still has it, and closing the scope drops exactly that scope's state;
* one offer is materialized per round (``core.tool_offer_assembly.assemble_tool_offer``) and both the
  prompt catalog and the native tool definitions render that object;
* it is never permission: policy and availability are re-read for every round, the family cap holds,
  and a family the runtime cannot offer seats nothing.

ORIGINAL cases use the families and wordings of the review's reproduction (email, knowledge); NOVEL
cases use other families, other wordings and other data.
"""
from __future__ import annotations

import re
import threading
import uuid
from typing import Any

import pytest

ORIGINAL = [("email", "Please continue with that."), ("knowledge", "Help me with the saved notes.")]
NOVEL = [("pdf", "Carry on with the quarterly report."), ("filesystem", "Keep going with the garden photos folder.")]
CATALOG_LINE = re.compile(r"(?:^|\s)- ([a-z0-9_]+(?:\.[a-z0-9_]+)+)\(")


@pytest.fixture(autouse=True)
def _email_enabled_and_fresh_state(monkeypatch):
    """Email enabled on a copy of the loaded policy. The cache is put back exactly as it was found -- unloaded
    included -- because a policy left loaded behind changes the tool surface a later suite sees
    (tests/test_email_action_routing.py measured it on the gauntlet's live catalog snapshot)."""
    from core import policy_engine
    from core.tool_offer_state import reset_offer_state

    previous = policy_engine._POLICY_CACHE
    policy = dict(policy_engine.load())
    policy["email"] = {**(policy.get("email") or {}), "read_enabled": True, "send_enabled": True}
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", policy)
    reset_offer_state()
    yield
    reset_offer_state()
    monkeypatch.undo()
    policy_engine._POLICY_CACHE = previous


def _set_policy(monkeypatch, dotted: str, value: Any) -> None:
    """Change one policy value the way a reload would present it to every later read."""
    from core import policy_engine

    policy = dict(policy_engine._POLICY_CACHE or policy_engine.load())
    section, key = dotted.split(".", 1)
    policy[section] = {**(policy.get(section) or {}), key: value}
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", policy)


def _context(*, session: str = "", turn: str = "") -> dict[str, Any]:
    session = session or "openclaw:navigation-" + uuid.uuid4().hex
    return {
        "surface": "openclaw",
        "platform": "openclaw",
        "runtime_session_id": session,
        "session_id": session,
        "turn_id": turn or "turn-" + uuid.uuid4().hex,
    }


def _offer(text: str, context: dict[str, Any]):
    from core.tool_offer_assembly import assemble_tool_offer

    return assemble_tool_offer(user_text=text, task_class="unknown", source_context=context)


def _catalog(text: str, context: dict[str, Any], **kwargs: Any) -> set[str]:
    from core.prompt_normalizer import _tool_intent_catalog_text

    rendered = _tool_intent_catalog_text(user_text=text, task_class="unknown", source_context=context, **kwargs)
    return set(CATALOG_LINE.findall(rendered))


def _added(family: str, text: str) -> set[str]:
    """What expanding `family` adds to this wording's offer; a probe that adds nothing is invalid."""
    from core.tool_offer_state import record_family_expansion

    baseline = set(_offer(text, _context()).intents)
    probe = _context()
    record_family_expansion(probe, family)
    added = set(_offer(text, probe).intents) - baseline
    assert added, f"invalid probe: expanding {family!r} adds nothing to the offer for {text!r}"
    return added


def _governed_by(dotted: str, monkeypatch) -> set[str]:
    """The contracted intents whose availability a policy value switches (computed, not listed)."""
    from core import policy_engine
    from core.runtime_tool_contracts import runtime_tool_contracts

    original = policy_engine._POLICY_CACHE
    _set_policy(monkeypatch, dotted, True)
    enabled = {contract.intent for contract in runtime_tool_contracts() if contract.supported}
    _set_policy(monkeypatch, dotted, False)
    disabled = {contract.intent for contract in runtime_tool_contracts() if contract.supported}
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", original)
    governed = enabled - disabled
    assert governed, f"invalid probe: {dotted} switches no contracted tool"
    return governed


# --------------------------------------------------------------------------- one turn, many renderings


@pytest.mark.parametrize(("family", "text"), ORIGINAL + NOVEL)
def test_both_renderings_keep_the_expansion_in_either_order(family: str, text: str) -> None:
    from core.tool_offer_state import record_family_expansion

    added = _added(family, text)
    catalog_first = _context()
    record_family_expansion(catalog_first, family)
    assert added <= _catalog(text, catalog_first)
    assert added <= set(_offer(text, catalog_first).intents)

    native_first = _context()
    record_family_expansion(native_first, family)
    assert added <= set(_offer(text, native_first).intents)
    assert added <= _catalog(text, native_first)


@pytest.mark.parametrize(("family", "text"), [ORIGINAL[0], NOVEL[0]])
def test_repeated_rendering_is_identical_and_keeps_the_expansion(family: str, text: str) -> None:
    from core.tool_offer_state import record_family_expansion, turn_family_expansions

    added = _added(family, text)
    context = _context()
    record_family_expansion(context, family)
    offers = [_offer(text, context) for _ in range(3)]
    catalogs = [_catalog(text, context) for _ in range(3)]
    assert len({offer.fingerprint for offer in offers}) == 1
    assert all(added <= set(offer.intents) for offer in offers)
    assert all(catalog == catalogs[0] and added <= catalog for catalog in catalogs)
    assert turn_family_expansions(context) == (family,)


@pytest.mark.parametrize(("family", "text"), [ORIGINAL[0], NOVEL[1]])
def test_a_later_round_of_the_turn_keeps_the_family_until_the_scope_closes(family: str, text: str) -> None:
    from core.tool_offer_state import begin_turn_navigation, end_turn_navigation, record_family_expansion

    added = _added(family, text)
    context = _context()
    scope = begin_turn_navigation(context)
    record_family_expansion(context, family)
    first_round = _offer(text, context)
    context["code_task_completion_feedback"] = "a later round carries other per-round keys"
    second_round = _offer(text, context)
    assert added <= set(first_round.intents) and added <= set(second_round.intents)
    assert first_round.expanded_families == second_round.expanded_families == (family,)

    end_turn_navigation(context, scope)
    after = _offer(text, context)
    assert not (added & set(after.intents)) and after.expanded_families == ()


def test_no_expansion_leaves_the_offer_exactly_as_it_was() -> None:
    from core.tool_offer_state import begin_turn_navigation, end_turn_navigation

    text = ORIGINAL[0][1]
    plain = _offer(text, _context())
    context = _context()
    scope = begin_turn_navigation(context)
    scoped = _offer(text, context)
    end_turn_navigation(context, scope)
    assert scoped.expanded_families == ()
    assert scoped.fingerprint == plain.fingerprint


# --------------------------------------------------------------------------- bounded and idempotent


@pytest.mark.parametrize(("family", "text"), [ORIGINAL[0], NOVEL[0]])
def test_a_duplicate_expansion_changes_nothing(family: str, text: str) -> None:
    from core.tool_offer_state import record_family_expansion

    once = _context()
    record_family_expansion(once, family)
    twice = _context()
    assert record_family_expansion(twice, family) == (family,)
    assert record_family_expansion(twice, family) == (family,)
    assert _offer(text, twice).fingerprint == _offer(text, once).fingerprint


def test_the_family_cap_holds_and_the_navigator_tool_says_what_happened() -> None:
    from core.capability_graph import _HARD_MAX_CANDIDATES
    from core.runtime_execution_tools import _capability_expand_family
    from core.tool_offer_state import record_family_expansion

    context = _context()
    assert record_family_expansion(context, "email") == ("email",)
    assert record_family_expansion(context, "filesystem") == ("email", "filesystem")
    assert record_family_expansion(context, "pdf") == ("email", "filesystem")
    offer = _offer(ORIGINAL[0][1], context)
    assert offer.expanded_families == ("email", "filesystem")
    assert len(offer.specs) <= _HARD_MAX_CANDIDATES

    refused = _capability_expand_family({"family": "workspace"}, source_context=context)
    assert not refused.ok and refused.status == "expansion_limit_reached"
    assert "`email`" in refused.response_text and "`filesystem`" in refused.response_text
    assert "Seated" not in refused.response_text

    repeated = _capability_expand_family({"family": "filesystem"}, source_context=context)
    assert repeated.ok and repeated.details["turn_families"] == ["email", "filesystem"]

    no_turn = _capability_expand_family({"family": "email"}, source_context={"surface": "openclaw"})
    assert not no_turn.ok and no_turn.status == "no_turn_to_expand"


def test_an_expanded_family_the_family_cap_truncates_earns_no_bonus_seats() -> None:
    from core.capability_graph import _DEFAULT_MAX_CANDIDATES, model_visible_specs

    def intents(specs: list[dict[str, Any]]) -> list[str]:
        return [str(spec.get("intent") or "") for spec in specs]

    seated = model_visible_specs(expanded_families=("filesystem",))
    assert "machine.write_file" in intents(seated)  # the bonus seats the expanded family's long tail
    assert len(seated) > _DEFAULT_MAX_CANDIDATES

    three = ("workspace", "web", "media")
    plain = model_visible_specs(family_hints=three)
    truncated = model_visible_specs(family_hints=three, expanded_families=("filesystem",))
    assert intents(truncated) == intents(plain)
    assert len(truncated) <= _DEFAULT_MAX_CANDIDATES


# --------------------------------------------------------------------------- isolation


def test_the_same_visible_turn_id_in_another_session_sees_no_expansion() -> None:
    from core.tool_offer_state import record_family_expansion

    text = ORIGINAL[0][1]
    added = _added("email", text)
    first = _context(session="openclaw:navigation-first", turn="turn-shared")
    second = _context(session="openclaw:navigation-second", turn="turn-shared")
    record_family_expansion(first, "email")
    assert added <= set(_offer(text, first).intents)
    assert not (added & set(_offer(text, second).intents))


def test_two_requests_presenting_one_turn_keep_separate_state_and_close_separately() -> None:
    from core.tool_offer_state import (
        TURN_SCOPE_KEY,
        begin_turn_navigation,
        end_turn_navigation,
        record_family_expansion,
        turn_family_expansions,
    )

    text = NOVEL[0][1]
    added = _added("pdf", text)
    session = "openclaw:navigation-" + uuid.uuid4().hex
    paused = _context(session=session, turn="turn-resumed-after-approval")
    resumed = dict(paused)
    paused_scope = begin_turn_navigation(paused)
    resumed_scope = begin_turn_navigation(resumed)
    assert paused_scope and resumed_scope and paused_scope != resumed_scope

    record_family_expansion(paused, "pdf")
    assert added <= set(_offer(text, paused).intents)
    assert not (added & set(_offer(text, resumed).intents))

    record_family_expansion(resumed, "knowledge")
    stale = dict(paused)
    end_turn_navigation(paused, paused_scope)
    assert TURN_SCOPE_KEY not in paused
    assert turn_family_expansions(stale) == ()
    assert turn_family_expansions(resumed) == ("knowledge",)


def test_a_nested_loop_joins_the_open_scope_and_only_the_opener_closes_it() -> None:
    from core.tool_offer_state import (
        TURN_SCOPE_KEY,
        begin_turn_navigation,
        end_turn_navigation,
        record_family_expansion,
        turn_family_expansions,
    )

    context = _context()
    outer = begin_turn_navigation(context)
    inner = begin_turn_navigation(context)
    assert outer and inner == ""
    record_family_expansion(context, "email")
    end_turn_navigation(context, inner)
    assert turn_family_expansions(context) == ("email",)
    end_turn_navigation(context, outer)
    assert TURN_SCOPE_KEY not in context and turn_family_expansions(context) == ()


def test_concurrent_turns_in_distinct_sessions_keep_their_own_families() -> None:
    from core.tool_offer_state import begin_turn_navigation, end_turn_navigation, record_family_expansion

    text = ORIGINAL[0][1]
    added = {family: _added(family, text) for family in ("email", "pdf")}
    exclusive = {"email": added["email"] - added["pdf"], "pdf": added["pdf"] - added["email"]}
    assert exclusive["email"] and exclusive["pdf"], exclusive
    barrier = threading.Barrier(2)
    failures: list[str] = []

    def run(family: str, other: str) -> None:
        for _ in range(8):
            context = _context()
            scope = begin_turn_navigation(context)
            try:
                barrier.wait(timeout=60)
                record_family_expansion(context, family)
                intents = set(_offer(text, context).intents)
                if not exclusive[family] <= intents:
                    failures.append(f"{family} lost its own tools")
                if intents & exclusive[other]:
                    failures.append(f"{family} received {other}'s tools")
            except Exception as exc:  # recorded, so the other thread's failure is visible too
                failures.append(f"{family}: {type(exc).__name__}: {exc}")
                barrier.abort()
            finally:
                end_turn_navigation(context, scope)

    threads = [threading.Thread(target=run, args=("email", "pdf")), threading.Thread(target=run, args=("pdf", "email"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)
    assert not any(thread.is_alive() for thread in threads)
    assert not failures, failures


# --------------------------------------------------------------------------- lifetime


def test_an_abandoned_scope_expires_and_a_turn_in_use_keeps_its_set(monkeypatch) -> None:
    from core import tool_offer_state
    from core.tool_offer_state import begin_turn_navigation, record_family_expansion, turn_family_expansions

    now = [10_000.0]
    monkeypatch.setattr(tool_offer_state, "_clock", lambda: now[0])
    ttl = tool_offer_state._EXPANSION_TTL_SECONDS
    context = _context()
    begin_turn_navigation(context)
    record_family_expansion(context, "email")
    now[0] += ttl - 1
    assert turn_family_expansions(context) == ("email",)  # a read is use
    now[0] += ttl - 1
    assert turn_family_expansions(context) == ("email",)  # last used ttl-1 ago: still live
    now[0] += ttl + 1
    assert turn_family_expansions(context) == ()
    assert _offer(ORIGINAL[0][1], context).expanded_families == ()


def _loop_arguments(context: dict[str, Any]) -> dict[str, Any]:
    return dict(task=None, effective_input="", classification={}, interpretation=None, context_result=None,
                persona=None, session_id=context["session_id"], source_context=context, surface="openclaw")


@pytest.mark.parametrize(
    "outcome",
    [
        {"status": "completed"},
        {"status": "awaiting_approval"},
        {"status": "cancelled"},
        RuntimeError("the provider failed mid-turn"),
    ],
    ids=["completed", "paused", "cancelled", "failed"],
)
def test_the_tool_loop_closes_its_turn_scope_on_every_exit(outcome: Any) -> None:
    """The scope the tool loop runs in (the facade's `_within_turn_navigation`) is closed however the loop ends:
    an answer, a pause for approval, a cancellation or a failure. The loop body is scripted."""
    from core.agent_runtime.research_tool_loop_facade import _within_turn_navigation
    from core.tool_offer_state import TURN_SCOPE_KEY, record_family_expansion, turn_family_expansions

    seen: dict[str, Any] = {}

    @_within_turn_navigation
    def scripted_loop(self: Any, **arguments: Any) -> Any:
        context = arguments["source_context"]
        record_family_expansion(context, "email")
        seen.update(context)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    context = _context()
    if isinstance(outcome, BaseException):
        with pytest.raises(type(outcome)):
            scripted_loop(object(), **_loop_arguments(context))
    else:
        assert scripted_loop(object(), **_loop_arguments(context)) == outcome
    assert seen.get(TURN_SCOPE_KEY), "the loop body did not run inside a scope"
    assert turn_family_expansions(seen) == ()
    assert TURN_SCOPE_KEY not in context


@pytest.mark.parametrize("exit_by", ["return", "raise"])
def test_the_real_tool_loop_entry_point_runs_in_its_turn_scope_without_host_services(monkeypatch, exit_by: str) -> None:
    """`ResearchToolLoopFacadeMixin._maybe_execute_model_tool_intent` itself opens and closes the scope, so every
    caller gets it, and a host without the facade's services (a bare namespace, as the presentation contract
    uses) can still call it. The loop's first decision, the turn's action policy, is scripted to record an
    expansion inside the scope and then end the loop: a forbidden policy returns, a failing read raises."""
    from types import SimpleNamespace

    from core.agent_runtime import intent_claims
    from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin
    from core.tool_offer_state import TURN_SCOPE_KEY, record_family_expansion, turn_family_expansions

    seen: dict[str, Any] = {}

    def scripted_policy(source_context: Any) -> Any:
        record_family_expansion(source_context, "email")
        seen.update(source_context)
        if exit_by == "raise":
            raise RuntimeError("the policy read failed")
        return intent_claims.ActionPolicy.FORBIDDEN

    monkeypatch.setattr(intent_claims, "action_policy_from_context", scripted_policy)
    context = _context()
    loop_entry = ResearchToolLoopFacadeMixin._maybe_execute_model_tool_intent
    if exit_by == "raise":
        with pytest.raises(RuntimeError):
            loop_entry(SimpleNamespace(), **_loop_arguments(context))
    else:
        assert loop_entry(SimpleNamespace(), **_loop_arguments(context)) is None
    assert seen.get(TURN_SCOPE_KEY), "the tool loop entry point did not open a scope"
    assert turn_family_expansions(seen) == ()
    assert TURN_SCOPE_KEY not in context


def test_the_legal_path_census_leaves_no_navigation_state_behind() -> None:
    from core import tool_offer_state
    from core.capability_graph import legal_offer_paths_map

    paths = legal_offer_paths_map()
    assert any("expansion" in found for found in paths.values())
    assert tool_offer_state._EXPANSIONS == {}


# --------------------------------------------------------------------------- never permission


@pytest.mark.parametrize(
    ("family", "text", "dotted"),
    [
        ("email", ORIGINAL[0][1], "email.read_enabled"),
        ("workspace", "Keep going through the kiln glaze notes.", "filesystem.allow_read_workspace"),
    ],
    ids=["original-email-read", "novel-workspace-read"],
)
def test_policy_revocation_between_rounds_takes_tools_back_but_keeps_navigation(
    family: str, text: str, dotted: str, monkeypatch
) -> None:
    from core.tool_offer_state import record_family_expansion, turn_family_expansions

    governed = _governed_by(dotted, monkeypatch)
    context = _context()
    record_family_expansion(context, family)
    first_round = set(_offer(text, context).intents)
    assert first_round & governed, f"invalid probe: the expanded offer carries none of {sorted(governed)}"

    _set_policy(monkeypatch, dotted, False)
    revoked_round = _offer(text, context)
    assert not (set(revoked_round.intents) & governed)
    assert not (_catalog(text, context) & governed)
    assert turn_family_expansions(context) == (family,)

    _set_policy(monkeypatch, dotted, True)
    assert set(_offer(text, context).intents) & governed


def test_an_expanded_family_whose_tools_are_disabled_seats_none_of_them(monkeypatch) -> None:
    from core.runtime_execution_tools import _capability_expand_family
    from core.tool_offer_state import record_family_expansion, turn_family_expansions

    governed = _governed_by("email.read_enabled", monkeypatch) | _governed_by("email.send_enabled", monkeypatch)
    _set_policy(monkeypatch, "email.read_enabled", False)
    _set_policy(monkeypatch, "email.send_enabled", False)
    recorded = _context()
    record_family_expansion(recorded, "email")
    assert not (set(_offer(ORIGINAL[0][1], recorded).intents) & governed)

    # The model's own path says so and spends nothing: no seat is claimed and the turn records nothing.
    asked = _context()
    before = _offer(ORIGINAL[0][1], asked)
    reported = _capability_expand_family({"family": "email"}, source_context=asked)
    assert not reported.ok and reported.status == "family_unavailable"
    assert "Seated" not in reported.response_text
    assert turn_family_expansions(asked) == ()
    assert _offer(ORIGINAL[0][1], asked).fingerprint == before.fingerprint


@pytest.mark.parametrize(("family", "text"), [("email", ORIGINAL[0][1]), ("skill", "Keep going with the glaze notes.")])
def test_an_available_family_reports_only_its_own_seats_and_the_next_offer_carries_them(family: str, text: str) -> None:
    from core.capability_graph import capability_for_intent, family_for_capability
    from core.runtime_execution_tools import _capability_expand_family
    from core.tool_offer_state import turn_family_expansions

    context = _context()
    result = _capability_expand_family({"family": family}, source_context=context)
    assert result.ok and result.status == "ok", result.response_text
    seated = list(result.details["seated_intents"])
    assert seated
    assert all(family_for_capability(capability_for_intent(intent)) == family for intent in seated), seated
    assert turn_family_expansions(context) == (family,)
    assert set(seated) <= set(_offer(text, context).intents)


# --------------------------------------------------------------------------- one offer, both renderings


def test_the_catalog_renders_the_round_offer_it_is_given_without_building_another(monkeypatch) -> None:
    from core import tool_offer_assembly
    from core.prompt_normalizer import _tool_intent_catalog_text
    from core.tool_offer_assembly import offer_fingerprint
    from core.tool_offer_state import record_family_expansion

    text = NOVEL[1][1]
    context = _context()
    record_family_expansion(context, "filesystem")
    offer = _offer(text, context)

    def second_offer(**_kwargs: Any) -> Any:
        raise AssertionError("the catalog built a second offer instead of rendering the round's")

    monkeypatch.setattr(tool_offer_assembly, "assemble_tool_offer", second_offer)
    rendered = _tool_intent_catalog_text(user_text=text, task_class="unknown", source_context=context, offer=offer)
    assert set(CATALOG_LINE.findall(rendered)) == set(offer.intents)
    assert offer_fingerprint(offer.specs) == offer.fingerprint  # rendering changed nothing in the offer
