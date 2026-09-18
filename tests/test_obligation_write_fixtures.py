"""Test INFRASTRUCTURE, not a repair: the instrument a future fix can be sabotage-tested against.

Why this file exists
---------------------
`core.live_data_continuation.continuation_inherits_live_data` resolves a follow-up ("What about
Polkadot?") against a typed OBLIGATION that turn 1 was supposed to have recorded
(`core.runtime_continuity.remember_live_data_obligation`, read back through `recall_live_data_obligation`
/ `_recorded_obligation` at `core/live_data_continuation.py:352`). That side of the contract is
covered thoroughly by `tests/test_a_follow_up_rebinds_the_obligation_not_the_text.py` -- but every
one of its ~17 call sites of `remember_live_data_obligation` writes the obligation ITSELF, by hand,
before ever calling `continuation_inherits_live_data`. No test in that file, or anywhere else found
by grep, drives a real turn 1 and checks that PRODUCTION wrote the row.

It does, sometimes, and does not, other times -- both verified by reading the source, not assumed:

* `apps/vool_agent.py:1497` (`_answer_single_live_data_turn`) and `apps/vool_agent.py:1631`
  (`_answer_multipart_live_data_turn`) both call `self._remember_live_data_obligation(...)`
  unconditionally once their typed plan produces at least one `outcome.ok` subtask. These are the
  ONLY two production call sites of `remember_live_data_obligation` (confirmed by
  `grep -rn remember_live_data_obligation apps/ core/ tools/ storage/ adapters/`). This file's
  `test_the_driver_observes_a_real_write_from_the_known_writing_lane` drives exactly this lane for
  real and confirms it writes -- the STABLE, already-correct half of the contract.

* `_maybe_answer_live_data_turn` (`apps/vool_agent.py:1004`) is tried FIRST (GATE_LIVE_DATA, wired
  at `apps/vool_agent.py:866`), but it is not the only lane that can render a price/weather answer.
  When it declines or its typed plan finds nothing, the turn falls through, unconditionally, to
  `self._handle_turn_frontdoor(...)` (`apps/vool_agent.py:887`), which -- when live-info coverage
  claims the whole turn (`core/agent_runtime/turn_frontdoor.py:1738`,
  `agent._maybe_handle_live_info_fast_path`) -- can render a price/weather reply through an entirely
  separate, older fetch path: `core.agent_runtime.fast_live_info.maybe_handle_live_info_fast_path` ->
  `live_info_search_notes` -> `agent._try_live_quote_notes` (`core/agent_runtime/fast_live_info_search.py:74`)
  -> `tools.web.web_research.lookup_live_quotes`. That whole chain never imports or calls
  `remember_live_data_obligation`. A turn 1 answered THIS way leaves nothing on the table for turn
  2 to rebind against, which is the reported defect ("What about Polkadot?" replays turn 1's table).

This file does not assert that gap into a red test -- CLAUDE.md's suite-must-stay-green rule and this
task's own instructions forbid it, and it would be exactly the "verdict smuggled into an instrument"
this file is not supposed to be. What it delivers instead is the INSTRUMENT: a way to drive a real
turn 1 through `agent.run_once` (the same method `apps/vool_api_server.py` calls) and read back,
from the real store, whether an obligation now exists for that session -- so a future repair that
teaches the frontdoor price lane to record one can write:

    observation = drive_turn_and_observe_obligation(agent, "price of polkadot?", session_id=sid)
    assert observation["obligation"] is not None

and a revert of that repair will fail it, by name, at the exact seam described above.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from core.live_quote_contract import LiveQuoteResult
from core.mode_permission_policy import reset_mode_permission_state
from core.runtime_continuity import recall_live_data_obligation, remember_live_data_obligation


def _session_id(label: str) -> str:
    """A fresh, collision-proof session id per test/call -- never reused, so no test here can be
    made to pass by reading a row an earlier test (or run) left behind."""
    return f"obligation-fixture:{label}:{uuid.uuid4().hex}"


def _fake_quote(asset_key: str, value: float, *, kind: str = "crypto") -> LiveQuoteResult:
    """The same fixture shape `tests/test_live_data_turn_integration.py` already proved the typed
    plan's market subtask accepts -- not invented here."""
    return LiveQuoteResult(
        asset_key=asset_key,
        asset_name=asset_key.title(),
        symbol=asset_key.upper(),
        value=value,
        currency="USD",
        as_of="2026-08-17 12:00 UTC",
        source_label="test",
        source_url="https://x.invalid",
        kind=kind,
        change_percent=0.0,
    )


@pytest.fixture(autouse=True)
def _clean_mode_state():
    """Matches `tests/test_live_data_turn_integration.py`'s own autouse fixture: the typed plan's
    approval gate reads process-global mode-permission state, and this file drives that same plan
    for real in one of its self-tests."""
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


# =============================================================================================
# THE INSTRUMENT
# =============================================================================================


def read_recorded_obligation(session_id: str) -> dict[str, object] | None:
    """What a live-data obligation looks like for `session_id`, read straight from the production
    store (`core.runtime_continuity.recall_live_data_obligation`) -- the exact same read
    `core.live_data_continuation._recorded_obligation` performs when a follow-up asks "is there
    something open to rebind against". Returns `None` when nothing is on the table.

    This function never writes anything. It is the read half of the instrument; see
    `drive_turn_and_observe_obligation` for the half that drives a real turn first.
    """
    return recall_live_data_obligation(session_id)


def drive_turn_and_observe_obligation(
    agent,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None = None,
) -> dict[str, object]:
    """Drive ONE real turn through `agent.run_once` -- the production entrypoint, not an isolated
    module call -- then report whether the turn left a live-data obligation behind for `session_id`.

    Nothing here hand-writes a row. `session_id` is threaded through as `session_id_override` so the
    caller can read back afterward with the exact key production would have used
    (`apps/vool_agent.py:_run_once_inner` uses `session_id_override or runtime_session_id(...)` for
    every write inside the turn, including `_remember_live_data_obligation`). Whatever the real
    routing (`GATE_LIVE_DATA` typed plan, the frontdoor's older price/weather fast path, or something
    else entirely) does with that turn is what gets reported: a write, or nothing.

    Network access is NOT stubbed by this function -- the suite's network seal
    (`tests/_network_seal.py`, installed by the root conftest) blocks it, and any live-data turn a
    caller drives through this helper needs its own fetch boundary mocked
    (`tools.web.web_research.*`), exactly as `tests/test_live_data_turn_integration.py` already does.
    That is a deliberate choice: this helper drives the REAL routing/write logic, and only the actual
    network call is a stub, not the obligation write it is trying to observe.

    Returns a dict with three keys: `turn_result` (whatever `run_once` returned, for the caller to
    inspect reason/response/route), `obligation` (`read_recorded_obligation(session_id)`'s result --
    the thing this instrument exists to report), and `session_id` (echoed back for convenience).
    """
    context = dict(source_context or {})
    turn_result = agent.run_once(user_input, session_id_override=session_id, source_context=context)
    return {
        "turn_result": turn_result,
        "obligation": read_recorded_obligation(session_id),
        "session_id": session_id,
    }


# =============================================================================================
# SELF-TEST: does the instrument itself work?
#
# Nothing below asserts anything about whether the reported production defect is fixed. Every
# assertion here is about a fact independently verified by reading the source (cited in the module
# docstring above), never about the disputed frontdoor-lane gap itself.
# =============================================================================================


def test_read_recorded_obligation_reports_absence_for_an_untouched_session() -> None:
    """The trivial control: a session nobody has ever written anything for has nothing to read."""
    session_id = _session_id("untouched")
    assert read_recorded_obligation(session_id) is None


def test_read_recorded_obligation_reports_a_row_that_genuinely_exists() -> None:
    """The reader half, proven against a REAL row.

    `remember_live_data_obligation` is called directly here -- and ONLY here, inside the
    instrument's own self-test, to prove the READER works. This is never a substitute for a
    production write inside a behaviour test; see the module docstring's explicit warning against
    exactly that substitution.
    """
    session_id = _session_id("seeded")
    remember_live_data_obligation(
        session_id,
        operation="market_quote",
        slots=["polkadot"],
        request_text="what is the price of polkadot",
        absorbed_text="what is the price of polkadot",
    )
    obligation = read_recorded_obligation(session_id)
    assert obligation is not None, "a row that was genuinely written was not read back"
    assert obligation["operation"] == "market_quote"
    assert [slot.lower() for slot in obligation["slots"]] == ["polkadot"]
    assert obligation["request_text"] == "what is the price of polkadot"


def test_the_driver_observes_a_real_write_from_the_known_writing_lane(make_agent) -> None:
    """The DRIVE half, proven end to end against a REAL, currently-working production write.

    `_answer_single_live_data_turn` (`apps/vool_agent.py:1407`) unconditionally calls
    `self._remember_live_data_obligation(...)` at line 1497 once its typed plan produces at least
    one successful subtask -- read directly in the source, not assumed. Driving a real single-asset
    price turn through `agent.run_once`, with ONLY the network fetch stubbed (the exact pattern
    `tests/test_live_data_turn_integration.py::test_a_single_asset_request_is_unaffected_and_still_answers`
    already proved reaches this lane and answers), must leave a row behind.

    This is the control that makes the instrument trustworthy: a driver that always reported "no
    obligation" regardless of what production actually did would let a real regression on the
    KNOWN-good lane (1497/1631) slip through silently, and would make every future repair test built
    on `drive_turn_and_observe_obligation` worthless. This is NOT the disputed defect (the frontdoor
    price lane, described in the module docstring) -- it is proof the observer would have caught a
    write, on a lane independently confirmed to make one today.
    """
    agent = make_agent()
    session_id = _session_id("known-writing-lane")
    with mock.patch(
        "tools.web.web_research._crypto_price_fallback_multi",
        return_value=[_fake_quote("bitcoin", 64000.0)],
    ):
        observation = drive_turn_and_observe_obligation(
            agent,
            "what is the current price of bitcoin",
            session_id=session_id,
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
        )

    response = str((observation["turn_result"] or {}).get("response") or "")
    assert "Bitcoin" in response, f"the stubbed turn never reached the typed price plan: {response!r}"
    # `route_reason` is the field `_fast_path_result(..., reason=...)` actually lands in on the
    # dict `run_once` returns (confirmed by printing a real turn result -- `reason` itself is not a
    # top-level key on the returned dict).
    assert (observation["turn_result"] or {}).get("route_reason") == "live_data_typed_plan", (
        "a different lane than the typed live-data plan answered this turn -- "
        f"route_reason was {(observation['turn_result'] or {}).get('route_reason')!r}"
    )
    assert observation["obligation"] is not None, (
        "the known-writing lane (apps/vool_agent.py:1497) left no obligation behind for a turn "
        "it answered -- the driver failed to observe a write it should have caught"
    )
    assert observation["obligation"]["operation"] == "market_quote"
    assert "bitcoin" in [slot.lower() for slot in observation["obligation"]["slots"]]


def test_the_driver_reports_no_obligation_for_an_ordinary_non_live_data_turn(make_agent) -> None:
    """The negative control on the DRIVE half: an ordinary turn that never touches the live-data
    lanes at all must leave nothing behind, and the observer must report that correctly rather than
    finding a stale or unrelated row.

    Deliberately says nothing about price or weather, so it can never be read as a claim about the
    disputed frontdoor-lane gap in either direction -- only that the driver+reader pairing does not
    fabricate a write that never happened.
    """
    agent = make_agent()
    session_id = _session_id("ordinary-turn")
    observation = drive_turn_and_observe_obligation(
        agent,
        "What is 2 + 2?",
        session_id=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
    )
    assert observation["obligation"] is None, (
        f"an ordinary arithmetic turn left a live-data obligation behind: {observation['obligation']!r}"
    )
