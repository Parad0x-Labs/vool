"""pa_beta_gate -- chat calendar actions follow the account chosen in Settings, not an unrelated env default.

Rows 7 and 10 via Settings accounts: an explicit Settings choice (account + selected default write
calendar, sync on) must not silently lose to VOOL_CALENDAR_* environment variables, and a legacy
environment-only setup keeps working unchanged until Settings chooses something.

LABELLED: two disposable loopback CalDAV fixtures reached through the real VOOL transport, driven at
the exact seam the served turn uses (parse_operator_action_intent + dispatch_operator_action).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)  # Monday 10:00 Vilnius
SETTINGS_CAL = "/calendars/team/"
ENV_CAL = "/calendars/vilnius/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    prepared = prepare_home(tmp_path, monkeypatch)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Athens")))
    return prepared


@pytest.fixture
def env_server(home, monkeypatch):
    server, state, base, _port = start_caldav_fixture(calendars={ENV_CAL: "Vilnius"})
    monkeypatch.setenv("VOOL_CALENDAR_PROVIDER", "caldav")
    monkeypatch.setenv("VOOL_CALENDAR_URL", base)
    monkeypatch.setenv("VOOL_CALENDAR_ID", ENV_CAL)
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    yield state, base
    server.shutdown()
    server.server_close()


@pytest.fixture
def settings_server(home):
    server, state, base, _port = start_caldav_fixture(calendars={SETTINGS_CAL: "Team"})
    yield state, base
    server.shutdown()
    server.server_close()


def _choose_settings_account(base: str, *, sync=True) -> str:
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="caldav", base_url=base, label="Settings team server")
    discovered = calendar_accounts.discover_calendars(account["account_id"])
    assert discovered["ok"], discovered
    chosen = calendar_accounts.select_calendar(account["account_id"], SETTINGS_CAL, selected=True, default_write=True)
    assert chosen["ok"], chosen
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=sync, alerts_enabled=False)
    return account["account_id"]


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"operator parser missed a calendar request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def test_settings_selected_account_wins_over_the_environment_default(env_server, settings_server, monkeypatch):
    """ORIGINAL: with a Settings account chosen (default write calendar, sync on) while VOOL_CALENDAR_*
    points at a different server, a chat proposal routes to the Settings account's calendar and the
    approved event lands there -- not on the environment server."""
    env_state, _env_base = env_server
    settings_state, settings_base = settings_server
    Clock(T0, monkeypatch)
    _choose_settings_account(settings_base)

    from core.operator.calendar_provider import load_provider_config

    config = load_provider_config()
    assert config is not None and config.base_url.rstrip("/") == settings_base.rstrip("/"), config
    assert config.calendar_id == SETTINGS_CAL, config

    session = "settings-authority"
    proposal = _run('propose "Roadmap review" on 2026-09-22 15:00 Europe/Athens for 30m', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    approved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert approved.ok, approved.response_text
    uid = approved.details["uid"]

    settings_events = settings_state.snapshot()[SETTINGS_CAL.rstrip("/") + "/"]
    env_events = env_state.snapshot()[ENV_CAL.rstrip("/") + "/"]
    assert uid in settings_events and settings_events[uid]["summary"] == "Roadmap review"
    assert not env_events, "the environment server must not receive the Settings account's event"


def test_environment_still_serves_while_settings_has_no_choice(env_server, settings_server, monkeypatch):
    """NOVEL/preservation: with no Settings account configured, the legacy environment setup keeps
    working; and a Settings account that was only disconnected falls back to the environment rather
    than refusing."""
    env_state, _env_base = env_server
    settings_state, settings_base = settings_server
    Clock(T0, monkeypatch)

    session = "env-legacy"
    proposal = _run('propose "Legacy env event" on 2026-09-22 15:00 Europe/Athens for 30m', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    approved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert approved.ok, approved.response_text
    uid = approved.details["uid"]
    assert uid in env_state.snapshot()[ENV_CAL.rstrip("/") + "/"]
    assert settings_state.snapshot()[SETTINGS_CAL.rstrip("/") + "/"] == {}

    account_id = _choose_settings_account(settings_base)
    from core.operator import calendar_accounts

    disconnected = calendar_accounts.disconnect_account(account_id)
    assert disconnected["ok"], disconnected

    after = _run('propose "Back to env" on 2026-09-23 09:00 Europe/Athens for 30m', session_id=session + "b")
    assert after.status == "approval_required", after.response_text
    approved2 = _run(f"approve calendar {after.details['action_id']}", session_id=session + "b")
    assert approved2.ok, approved2.response_text
    assert approved2.details["uid"] in env_state.snapshot()[ENV_CAL.rstrip("/") + "/"]
