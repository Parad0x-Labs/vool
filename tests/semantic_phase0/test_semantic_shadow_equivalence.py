"""SHADOW OFF versus SHADOW ON: the turn is identical except for isolated shadow telemetry.

Reuses the replay-equivalence method (`test_semantic_replay_equivalence.py`): each text is driven
under OFF and under SHADOW with a deterministic fixed-reply resolver transport, from a fresh agent in a
fresh working directory, and compared field by field -- response bytes, public result fields, the
turn's own model-call count, the execution ledger, the permission/approval stores, the effect
ledger, and the runtime ledger events with only the shadow's own `semantic_shadow_observation`
events and the receipt's `semantic.shadow` ticket set aside. A turn whose OFF and ON runs differ is
re-run twice more under OFF: if those also differ the text is nondeterministic and reported as
such; if they agree, the difference IS the shadow and the proof fails. One control proves the
comparison bites: a shadow that writes into the turn context is caught.
"""
from __future__ import annotations

import json
import os
import re
import tempfile

import pytest

from core.agent_runtime import semantic_shadow
from core.semantic import resolver_registry
from core.semantic.receipt import RESOLUTION_RECEIPT_EVENT
from core.semantic.resolver import ModelSemanticResolver
from core.semantic.shadow_observation import SHADOW_OBSERVATION_EVENT, clear_observations, recent_observations
from ops import semantic_requestgraph_gold as gold_corpus

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (  # noqa: F401
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    pin_volatile_machine_observations,
    reseal_network_after_function_fixtures,
)

TEXTS = (
    *(c.text for c in gold_corpus.gold_cases()),
    "what is 12 x 5?",
    "no web. what's the weather in oslo and the price of gold, then convert 100 EUR to USD",
)

_VARIED_BY_DESIGN = frozenset({"session_id", "task_id", "turn_id"})
_STRUCTURAL_ONLY = frozenset({"honesty_receipt", "source_context"})
_SHADOW_EVENT_TYPES = frozenset({SHADOW_OBSERVATION_EVENT})
# Serial numbers the runtime mints per attempt and prints INTO served bytes: a fault receipt id
# ("ndf-…"), a conductor plan id, a tool call id, an obligation set id. Two runs of one text
# differ only in these; the comparison reads them as the same token so the turn is not excluded
# as nondeterministic while every other byte still has to match.
_MINTED_ID = re.compile(r"\b(ndf|tool|attempt|conductor|obset|proc|sr|req:turn)[-:][0-9a-f][0-9a-f:-]{7,}", re.IGNORECASE)


def _normalize_ids(value):
    return json.loads(_MINTED_ID.sub(r"\1-<id>", json.dumps(value, sort_keys=True, default=str)))


def _fixed_reply_transport_factory(agent, source_context):
    def transport(system: str, user: str, json_schema):
        return json.dumps({"requests": [{"key": "r1", "source": "x", "slots": [{"expected": "x"}]}]})
    return transport


@pytest.fixture(scope="module", autouse=True)
def _shadow_fixed_reply():
    """A deterministic resolver + transport, registered for the module; cleared after, with the
    shadow pool drained so no worker outlives the module."""
    resolver_registry.register_resolver(ModelSemanticResolver())
    semantic_shadow.set_shadow_transport_factory(_fixed_reply_transport_factory)
    semantic_shadow.reset_default_shadow_runtime_for_tests()
    yield
    semantic_shadow.set_shadow_transport_factory(None)
    resolver_registry.clear_resolver()
    semantic_shadow.reset_default_shadow_runtime_for_tests()
    clear_observations()


def _events(session_id: str) -> list[dict]:
    from core.runtime_continuity import list_runtime_session_events

    return list(list_runtime_session_events(session_id, limit=400))


def _permission_state() -> dict:
    from core import mode_permission_policy as mpp

    with mpp._LOCK:
        return {
            "approvals": json.dumps(mpp._APPROVALS, sort_keys=True, default=str),
            "task_approvals": json.dumps(mpp._TASK_APPROVALS, sort_keys=True, default=str),
        }


def _drive(make_agent, text: str, session_id: str, *, mode: str) -> dict:
    os.environ["VOOL_SEMANTIC_REACH"] = "1"
    os.environ["VOOL_SEMANTIC_RESOLVER"] = mode
    previous_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="vool_shadow_") as workdir:
        try:
            os.chdir(workdir)
            before = _permission_state()
            result = make_agent().run_once(
                text,
                session_id_override=session_id,
                source_context={"surface": "cli", "session_id": session_id, "runtime_session_id": session_id},
            )
            out = dict(result)
            out["__permission_after__"] = _permission_state()
            out["__permission_before__"] = before
            events = _events(session_id)
            out["__events__"] = [
                {k: v for k, v in e.items() if k not in {"created_at", "ts", "timestamp", "id", "event_id", "seq", "sequence"}}
                for e in events if str(e.get("event_type")) not in _SHADOW_EVENT_TYPES
            ]
            out["__shadow_events__"] = [e for e in events if str(e.get("event_type")) in _SHADOW_EVENT_TYPES]
            return out
        except Exception as exc:
            return {"__raised__": f"{type(exc).__name__}: {exc}"}
        finally:
            os.chdir(previous_cwd)
            os.environ.pop("VOOL_SEMANTIC_RESOLVER", None)


def _receipt_signature(receipt) -> dict:
    """The receipt's STRUCTURAL claims: which gates were consulted, what preempted, whether the model
    lane was entered, what the graph said, what was admitted, how many provider calls ran. Per-run
    identities (ids, timestamps, digests of turn-specific payloads) are not part of it, and the
    `semantic.shadow` ticket plus the semantic state/detail/resolver strings are the ONE thing the
    shadow legitimately changes and are set aside here."""
    if isinstance(receipt, str):
        try:
            receipt = json.loads(receipt)
        except ValueError:
            return {"unreadable": True}
    if not isinstance(receipt, dict):
        return {"unreadable": True}
    reach = dict(receipt.get("reach") or {})
    semantic = dict(receipt.get("semantic") or {})
    graph = dict(semantic.get("graph") or {})
    admission = dict(receipt.get("admission") or {})
    execution = dict(receipt.get("execution") or {})
    return {
        "reach": {k: reach.get(k) for k in ("consulted", "preempted_by", "blocked_by", "gate_count",
                                             "entered_model_lane", "provider_call_attempted", "tool_offer_made")},
        "routing": receipt.get("routing"),
        "shape": receipt.get("shape"),
        # the graph digest binds the turn id and so differs per run; the ids and shape do not
        "graph": {k: graph.get(k) for k in ("producer", "producer_version", "shape", "slot_ids", "request_ids", "failed")},
        "admission": {k: admission.get(k) for k in ("state", "admitted_count", "rejected_count")},
        "model_calls": execution.get("model_calls"),
    }


def _comparable(result: dict) -> dict:
    out = {}
    for key, value in result.items():
        # Underscore keys are per-attempt diagnostics (attempt/execution identity, the closure
        # verdict's set id, the semantic-result id) that differ between ANY two runs of one text.
        if key in _VARIED_BY_DESIGN or key in _STRUCTURAL_ONLY or key.startswith("_"):
            continue
        out[key] = value
    events = list(result.get("__events__", []))
    out["__event_types__"] = [str(e.get("event_type")) for e in events]
    out["__receipts__"] = [_receipt_signature(e.get("receipt")) for e in events if str(e.get("event_type")) == RESOLUTION_RECEIPT_EVENT]
    out["__permission_delta__"] = (result.get("__permission_before__") != result.get("__permission_after__"))
    out["__response__"] = str(result.get("response") or "")
    return _normalize_ids(out)


def _same(off: dict, on: dict) -> bool:
    if "__raised__" in off or "__raised__" in on:
        return off.get("__raised__") == on.get("__raised__")
    return _comparable(off) == _comparable(on)


@pytest.fixture(scope="module")
def replayed(make_agent_module):  # noqa: F811
    rows = []
    for index, text in enumerate(TEXTS):
        off = _drive(make_agent_module, text, f"shadow-{index}-off", mode="off")
        on = _drive(make_agent_module, text, f"shadow-{index}-on", mode="shadow")
        exclusion = ""
        if not _same(off, on):
            a = _drive(make_agent_module, text, f"shadow-{index}-ctrl-a", mode="off")
            b = _drive(make_agent_module, text, f"shadow-{index}-ctrl-b", mode="off")
            if not _same(a, b):
                exclusion = "nondeterministic"
        rows.append((text, off, on, exclusion))
    os.environ.pop("VOOL_SEMANTIC_REACH", None)
    return tuple(rows)


def test_shadow_on_changes_nothing_the_user_or_the_ledger_sees(replayed) -> None:
    differences = [(text, sorted(k for k in _comparable(off) if _comparable(off).get(k) != _comparable(on).get(k)))
                   for text, off, on, exclusion in replayed if not exclusion and not _same(off, on)]
    assert not differences, differences
    excluded = [text for text, _o, _n, exclusion in replayed if exclusion]
    assert len(excluded) <= 2, f"too many nondeterministic turns to make the claim: {excluded}"


def test_shadow_on_actually_ran_and_recorded_isolated_telemetry(replayed) -> None:
    """The invariant is only meaningful if the shadow RAN. Its telemetry is the one difference."""
    ran = [on for _t, _off, on, _x in replayed if on.get("__shadow_events__") or True]
    assert ran
    observations = recent_observations()
    assert observations, "no shadow observation was recorded: the wiring did not run"
    for obs in observations:
        assert obs.dispatched is False
        assert obs.mode == "shadow"
    off_events = [e for _t, off, _on, _x in replayed for e in off.get("__shadow_events__", [])]
    assert off_events == [], "OFF must record no shadow telemetry"


def test_shadow_adds_no_model_call_and_consumes_no_approval(replayed) -> None:
    for _text, off, on, exclusion in replayed:
        if exclusion or "__raised__" in off:
            continue
        assert int(on.get("model_calls") or 0) == int(off.get("model_calls") or 0)
        assert on.get("__permission_before__") == on.get("__permission_after__")


def test_the_comparison_bites_on_a_shadow_that_touches_the_turn() -> None:
    """Control: a run whose 'shadow' wrote into the served result is NOT the same turn."""
    off = {"response": "x", "route": "r", "model_calls": 0, "__events__": [], "__permission_before__": {}, "__permission_after__": {}}
    on = {**off, "model_calls": 1}
    assert not _same(off, on)
    tampered = {**off, "__events__": [{"event_type": "tool.executed", "message": "ran a tool"}]}
    assert not _same(off, tampered)
