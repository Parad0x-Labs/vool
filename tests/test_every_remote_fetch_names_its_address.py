"""A turn must be able to say WHERE its answer came from, and no lane may open a private socket.

TWO DEFECTS, ONE DOOR.

1. THE LEDGER HELD A BARE INTEGER. `note_remote_fetch_attempt()` took no arguments, so a turn could
   report `web_calls: 7` and nothing else. Measured 2026-08-18: a weather turn for "berln" timed out
   on wttr.in, fell back to a search that returned three pages about Charlotte, North Carolina, and
   refused -- correctly. Whether that refusal was correct could not be established from the store,
   because the only record of the three pages was a SHA-256 of each URL. The address was in scope at
   every call site; nothing had to be discovered to record it.

2. A SECOND SOCKET. `tools/web/web_research.py` routed its eight outbound paths through one door
   whose docstring claimed the property was true by construction -- "a new fetch added later cannot
   forget to report or to ask, because there is nowhere else to open a socket". There was somewhere
   else: `tools/web/searxng_client.py` called `urllib.request.urlopen` itself. Since
   `tools/registry.py` registers `web.search` straight onto `client.search`, and
   `core/execution/web_tools.py` gates only on the GLOBAL `allow_web_fallback()` switch rather than
   the per-turn veto, a turn that forbade remote fetching still reached the SearXNG endpoint
   carrying the user's query.

The door now lives in `core.remote_fetch_policy`, beside the veto and the ledger it enforces, which
is what lets a `tools/` client use it without an import cycle.

NOTHING HERE ISSUES A REQUEST. `urlopen` is replaced by a recorder; what is asserted is whether the
production path ARRIVES at the socket, and what the turn recorded about it.
"""

from __future__ import annotations

import io
import json
import urllib.request

import pytest

from core.remote_fetch_policy import (
    RemoteFetchRefusedError,
    note_remote_fetch_attempt,
    open_remote,
    remote_fetch_attempt_count,
    remote_fetch_attempts,
    remote_fetch_policy_scope,
)

ALLOWED = {"allow_remote_fetch": True}
FORBIDDEN = {"allow_remote_fetch": False}


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._body.read()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


SEARX_PAYLOAD = {
    "results": [
        {"title": "DNS", "url": "https://en.wikipedia.org/wiki/DNS",
         "content": "the domain name system", "engine": "wikipedia", "score": 1.0}
    ]
}


@pytest.fixture
def socket_recorder(monkeypatch):
    """Replace the socket with a recorder. Returns the list of addresses ARRIVED at."""
    reached: list[str] = []

    def fake_urlopen(request, timeout=None):
        reached.append(str(getattr(request, "full_url", request)))
        return _Response(SEARX_PAYLOAD)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return reached


# ----------------------------------------------------------------------------- the ledger


def test_the_ledger_records_the_address_not_only_a_count() -> None:
    with remote_fetch_policy_scope(ALLOWED):
        note_remote_fetch_attempt("https://wttr.in/berln?format=j1", status=200, duration_ms=8100.0)
        note_remote_fetch_attempt("https://weather.com/us/nc/charlotte", status=200, duration_ms=412.0)

        assert remote_fetch_attempt_count() == 2
        entries = remote_fetch_attempts()
        assert [item["host"] for item in entries] == ["wttr.in", "weather.com"]
        assert entries[0]["duration_ms"] == 8100.0
        assert entries[0]["status"] == 200


def test_a_caller_that_passes_nothing_is_still_counted() -> None:
    """Every argument is optional so the change could not break a caller. The cost of omitting the
    address is visible -- an entry with no host -- rather than silent."""
    with remote_fetch_policy_scope(ALLOWED):
        note_remote_fetch_attempt()

        assert remote_fetch_attempt_count() == 1
        assert remote_fetch_attempts()[0]["host"] == ""


def test_outside_a_scope_the_ledger_is_empty_and_that_proves_nothing() -> None:
    assert remote_fetch_attempt_count() == 0
    assert remote_fetch_attempts() == ()


def test_the_detail_is_bounded_while_the_count_keeps_rising() -> None:
    from core.remote_fetch_policy import remote_fetch_attempts_truncated

    with remote_fetch_policy_scope(ALLOWED):
        for index in range(300):
            note_remote_fetch_attempt(f"https://example.invalid/{index}")

        assert remote_fetch_attempt_count() == 300
        assert len(remote_fetch_attempts()) == 256
        assert remote_fetch_attempts_truncated() is True


# ----------------------------------------------------------------------------- the door


def test_the_door_refuses_a_forbidden_turn_before_opening_a_socket(socket_recorder) -> None:
    request = urllib.request.Request("https://example.invalid/x")
    with remote_fetch_policy_scope(FORBIDDEN):
        with pytest.raises(RemoteFetchRefusedError):
            open_remote(request, timeout=1.0)

    assert socket_recorder == [], "the veto must be enforced before the socket, not after"


def test_the_door_reports_the_address_it_opens(socket_recorder) -> None:
    request = urllib.request.Request("https://example.invalid/page?q=1")
    with remote_fetch_policy_scope(ALLOWED):
        open_remote(request, timeout=1.0)

        assert remote_fetch_attempts()[0]["url"] == "https://example.invalid/page?q=1"
    assert socket_recorder == ["https://example.invalid/page?q=1"]


# ------------------------------------------------- the searxng lane, through the production tool


def _call_web_search(query: str = "what is dns"):
    from tools.registry import call_tool, load_builtin_tools

    load_builtin_tools()
    return call_tool("web.search", query=query, max_results=3)


def test_a_forbidden_turn_never_reaches_the_searxng_endpoint(socket_recorder) -> None:
    """The measured bypass. `web.search` is registered straight onto the client, and the web-tool
    executor checks only the global switch -- so before the door, this reached the socket."""
    with remote_fetch_policy_scope(FORBIDDEN):
        with pytest.raises(RemoteFetchRefusedError):
            _call_web_search()

    assert socket_recorder == [], "a self-hosted endpoint must not be a way around the turn's rules"


def test_an_allowed_turn_still_searches_and_is_recorded(socket_recorder) -> None:
    with remote_fetch_policy_scope(ALLOWED):
        results = _call_web_search()

        assert len(results) == 1
        assert results[0].url == "https://en.wikipedia.org/wiki/DNS"
        entries = remote_fetch_attempts()
        assert len(entries) == 1, "a searxng call must appear in web_calls like every other provider"
        assert "/search?" in entries[0]["url"]
        assert "format=json" in entries[0]["url"]
    assert socket_recorder, "the allowed turn must actually reach the endpoint"


# ----------------------------------------------------------------------------- sabotage


def test_sabotage_giving_the_client_its_own_socket_restores_the_bypass(monkeypatch, socket_recorder) -> None:
    """Revert `searxng_client` to opening its own socket and the forbidden turn reaches the
    endpoint again. This is the whole defect, so it must be the thing that goes red."""
    import tools.web.searxng_client as client_module

    def private_socket(request, *, timeout):
        return urllib.request.urlopen(request, timeout=timeout)

    monkeypatch.setattr(client_module, "open_remote", private_socket)
    with remote_fetch_policy_scope(FORBIDDEN):
        _call_web_search()

    assert socket_recorder, "sabotage did not bite: the client no longer opens its own socket at all"
    assert "format=json" in socket_recorder[0]


def test_sabotage_dropping_the_address_argument_blinds_the_turn(monkeypatch) -> None:
    """Revert the ledger to a bare tally and the turn can no longer say where it went."""
    import core.remote_fetch_policy as policy

    real = policy._FetchLedger.note
    monkeypatch.setattr(policy._FetchLedger, "note",
                        lambda self, url="", **kw: real(self, "", host="", status=None, duration_ms=None))
    with remote_fetch_policy_scope(ALLOWED):
        note_remote_fetch_attempt("https://wttr.in/berln?format=j1")

        assert remote_fetch_attempt_count() == 1
        assert remote_fetch_attempts()[0]["host"] == ""
