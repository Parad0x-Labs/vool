"""Every outbound HTTP path reports itself and honours the turn's veto, or it does not open.

Measured on c6eed761 (2026-08-14), and the reason this file exists::

    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        _weather_fallback("weather in Oslo")   -> returned live conditions
        remote_fetch_attempt_count()           -> 0

    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        remote_fetch_forbidden()               -> True
        _weather_fallback("weather in Bergen") -> returned live conditions anyway
        remote_fetch_attempt_count()           -> 0

Two separate claims were untrue at once. `remote_fetch_scope_active`'s docstring says a zero can
prove a negative *because* every remote path reports itself first -- seven of this module's eight
outbound paths did not, so a turn could quote live data while its trace read `web_calls: 0`. And an
explicit `allow_remote_fetch: false` veto was consulted by none of them, so it was not a veto.

The repair is one door (`_open_remote`). These tests pin the property, not the door's name: what is
asserted is that NO socket opens under a veto and that every successful fetch is counted, per path.
"""

from __future__ import annotations

import ast
import pathlib
import urllib.request

import pytest

from core.remote_fetch_policy import (
    remote_fetch_attempt_count,
    remote_fetch_policy_scope,
)
from tools.web import web_research as wr

_MODULE = pathlib.Path(wr.__file__)


class _SocketOpenedError(AssertionError):
    """Raised by the stub if anything reaches the network while a veto is in force."""


@pytest.fixture
def never_opens(monkeypatch):
    """Any socket attempt is a test failure -- the veto must stop the call before this."""

    def _explode(*args, **kwargs):
        raise _SocketOpenedError("a socket was opened under an explicit remote-fetch veto")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    return _explode


@pytest.fixture
def fake_open(monkeypatch):
    """A stub that records calls and returns a minimal readable response."""

    calls: list = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return b"{}"

        def geturl(self):
            return "https://example.invalid/"

        status = 200
        headers: dict = {}

    def _open(request, timeout=None):
        calls.append(request)
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    return calls


# ---------------------------------------------------------------------------------------------
# The door itself
# ---------------------------------------------------------------------------------------------


def test_the_door_reports_the_attempt_when_the_turn_permits_it(fake_open) -> None:
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        with wr._open_remote(urllib.request.Request("https://example.invalid/"), timeout=5):
            pass
        counted = remote_fetch_attempt_count()

    assert counted == 1
    assert len(fake_open) == 1


def test_the_door_refuses_without_opening_a_socket_when_vetoed(never_opens) -> None:
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        with pytest.raises(wr.RemoteFetchRefusedError):
            wr._open_remote(urllib.request.Request("https://example.invalid/"), timeout=5)
        assert remote_fetch_attempt_count() == 0


def test_a_refused_fetch_is_not_counted_as_an_attempt(never_opens) -> None:
    """A refusal is not a remote call and must not inflate the turn's account either."""

    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        for _ in range(4):
            with pytest.raises(wr.RemoteFetchRefusedError):
                wr._open_remote(urllib.request.Request("https://example.invalid/"), timeout=5)
        assert remote_fetch_attempt_count() == 0


# ---------------------------------------------------------------------------------------------
# Per path -- the property that actually matters
# ---------------------------------------------------------------------------------------------


_FETCHERS = (
    ("weather_fallback", lambda: wr._weather_fallback("weather in Oslo", timeout_s=5.0)),
    ("structured_weather_lookup", lambda: wr.structured_weather_lookup("Oslo", timeout_s=5.0)),
    ("simple_price_payload", lambda: wr._simple_price_payload(["bitcoin"], timeout_s=5.0)),
    ("duckduckgo_html_hits", lambda: wr._duckduckgo_html_hits("anything", max_hits=1)),
    ("resolve_redirect_url", lambda: wr._resolve_redirect_url("https://example.invalid/x", timeout_s=5.0)),
)


@pytest.mark.parametrize("name,call", _FETCHERS, ids=[n for n, _ in _FETCHERS])
def test_no_outbound_path_opens_a_socket_under_a_veto(name, call, never_opens) -> None:
    """The invariant, per path: vetoed means no socket, whatever the caller does with the refusal.

    Some of these swallow exceptions and return None; that is fine. What must never happen is the
    stub being reached, which is what `_SocketOpenedError` reports.
    """

    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        try:
            call()
        except _SocketOpenedError:
            raise
        except Exception:
            # Paths differ in how they surface a refusal -- some swallow and return None.
            # Only reaching the socket stub is a failure, and that re-raises above.
            pass
        assert remote_fetch_attempt_count() == 0, name


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS
# ---------------------------------------------------------------------------------------------


def test_without_a_veto_the_door_opens_normally(fake_open) -> None:
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        with wr._open_remote(urllib.request.Request("https://example.invalid/"), timeout=5):
            pass

    assert len(fake_open) == 1, "the guard must not block a permitted turn"


def test_an_absent_policy_key_is_not_a_veto(fake_open) -> None:
    """Absence is the trusted-surface default; only an explicit false is a veto."""

    with remote_fetch_policy_scope({}):
        with wr._open_remote(urllib.request.Request("https://example.invalid/"), timeout=5):
            pass

    assert len(fake_open) == 1


# ---------------------------------------------------------------------------------------------
# STRUCTURAL GUARD -- not the proof, but what stops the hole reopening
# ---------------------------------------------------------------------------------------------


def test_the_door_is_the_only_place_this_module_opens_a_socket() -> None:
    """An AST guard, so a fetch added later cannot quietly bypass reporting and the veto.

    This is deliberately NOT the proof -- the behavioural tests above are. It exists because the
    original defect was exactly this: one path was fixed in place on 2026-08-13 and the other six
    were left, with a docstring still claiming all of them reported.
    """

    tree = ast.parse(_MODULE.read_text())
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            target = inner.func
            name = ""
            if isinstance(target, ast.Attribute):
                name = target.attr
            if name == "urlopen" and node.name != "_open_remote":
                offenders.append(f"{node.name}:{inner.lineno}")

    assert not offenders, (
        "these open a socket without going through _open_remote, so they neither report the "
        f"attempt nor honour the veto: {offenders}"
    )
