"""D and E — what the SURFACE shows, hermetically.

`tests/test_live_search_is_one_governed_path.py` proves the receipt and the
attribution objects are truthful. It does not prove that the surfaces a person
actually looks at — the JSON the settings panel consumes, and the Activity row
text — carry that truth through. Those are different seams, and a correct
receipt rendered by a lying surface is still a lie to the user.

So this file drives the REAL server dispatch (`/api/search/test` through
`core.web.api.service`, the same function the HTTP handler calls) and the REAL
row-message writer (`finish_web_retrieval`), and asserts on what comes out.

D — FAILURE TRUTH. With remote fetch denied, no surface may report success and
no current claim may be publishable.

E — KEYLESS TRUTH. With Brave absent, whatever answers must name itself and say
it was keyless. It must never display Brave.
"""
from __future__ import annotations

import urllib.request

import pytest

PROVIDER_TEST_PATH = "/api/search/test"


# --------------------------------------------------------------------------- stubs


class _SocketOpenedError(AssertionError):
    """A socket opened in a test that says none may."""


@pytest.fixture
def no_socket(monkeypatch):
    """Any real socket is a test failure, not a slow test."""

    def _boom(*args, **kwargs):
        raise _SocketOpenedError("a hermetic test opened a socket")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def brave_key(monkeypatch):
    import core.credential_store as credential_store

    monkeypatch.setattr(credential_store, "get_credential", lambda slot: "BSAItest-key-value")
    monkeypatch.setattr(
        credential_store, "has_credential", lambda slot: slot == "search.web.brave"
    )
    return "search.web.brave"


@pytest.fixture
def no_key(monkeypatch):
    """Brave absent: nothing stored, for any slot."""
    import core.credential_store as credential_store

    monkeypatch.setattr(credential_store, "get_credential", lambda slot: "")
    monkeypatch.setattr(credential_store, "has_credential", lambda slot: False)


@pytest.fixture
def rows(monkeypatch):
    """Capture what the runtime-event STORE is handed.

    `emit_runtime_event` funnels every event into `append_runtime_event`, which is
    what `/api/runtime/events` reads back and what the Activity panel renders. So
    intercepting exactly there gives the row text and details a person would see,
    without a store on disk.
    """
    captured: list[dict] = []

    def _append(*, session_id, event_type, message, details=None):
        payload = {"session_id": session_id, "event_type": event_type, "message": message}
        payload.update(dict(details or {}))
        captured.append(payload)
        return payload

    monkeypatch.setattr("core.runtime_task_events.append_runtime_event", _append)
    return captured


def _messages(rows: list[dict]) -> list[str]:
    return [str(r.get("message") or "") for r in rows]


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    from core import search_connection_state as scs

    scs._last_probe_ts.clear()
    scs._last_verdict.clear()
    yield
    scs._last_probe_ts.clear()
    scs._last_verdict.clear()


def _post_provider_test(provider: str) -> dict:
    """Call the settings probe through the SERVER's own POST dispatch.

    Not `probe_search_provider` directly: the panel reads whatever this returns,
    so the dispatch layer — validation, status, envelope — is part of the surface
    under test.
    """
    import json

    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    response = dispatch_post(
        path=PROVIDER_TEST_PATH,
        body={"provider": provider},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )
    assert int(response.status) == 200, response.status
    return json.loads(bytes(response.body or b"{}").decode("utf-8"))


def _refuse_remote(monkeypatch):
    from core.remote_fetch_policy import RemoteFetchRefusedError

    def _refuse(*args, **kwargs):
        raise RemoteFetchRefusedError("network fetch denied by the permission gateway: test")

    monkeypatch.setattr("tools.web.search_api_client.open_remote", _refuse)


# ------------------------------------------------------------------ D: failure truth


def test_a_denied_fetch_is_never_reported_as_success_by_the_panel_api(
    no_socket, brave_key, monkeypatch
):
    """D. The payload the settings panel renders must not say ok/succeeded when
    the gateway refused the fetch. This is asserted on the SERVER RESPONSE, not
    on the probe's return value, because the response is what the browser sees."""
    _refuse_remote(monkeypatch)

    payload = _post_provider_test("brave")

    assert payload["state"] == "refused", payload
    assert payload["state"] != "ok", payload
    assert not payload.get("source_count"), payload
    receipt = payload.get("receipt") or {}
    assert receipt.get("lifecycle") != "succeeded", receipt
    assert receipt.get("status") != "available", receipt


def test_a_denied_fetch_is_not_blamed_on_the_key(no_socket, brave_key, monkeypatch):
    """D. A refusal must stay distinct from `unauthorized`/`no_key`. Collapsing
    them is what sent people to regenerate a working, paid credential."""
    _refuse_remote(monkeypatch)

    payload = _post_provider_test("brave")

    # Not a key verdict...
    assert payload["state"] not in {"unauthorized", "no_key", "quota_exhausted"}, payload
    # ...and not the generic `failed` either, which is what the panel renders as
    # "The search did not come back usable (RemoteFetchRefusedError)" -- the exact
    # sentence that sent people to regenerate a working, paid credential. The state
    # must be the one `_explainSearchError` maps to "The key was not used."
    assert payload["state"] == "refused", payload
    assert "key" not in str(payload.get("detail") or "").lower(), payload


def test_a_refused_retrieval_row_says_refused_and_names_no_provider_count(rows):
    """D. The Activity row TEXT is what a person reads before expanding anything.
    A refused retrieval must not render as `Brave Search - N sources`."""
    from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval

    context: dict = {"surface": "openclaw", "session_id": "d-refused"}
    started = begin_web_retrieval(
        context,
        kind="live_info_fast_path",
        query="what is the current stable linux kernel",
        task_id="d-refused",
        action="live_info_search",
        provider_id="brave",
        keyed_or_keyless="keyed",
    )
    terminal = finish_web_retrieval(context, started, refused=True)

    assert terminal["lifecycle"] != "succeeded", terminal
    assert terminal["status"] == "refused", terminal
    assert terminal["source_count"] == 0, terminal
    messages = _messages(rows)
    assert any("refused" in m.lower() for m in messages), messages
    assert not any("sources" in m for m in messages), messages
    terminals = [r for r in rows if r.get("event_type", "").startswith("web_retrieval_")
                 and r.get("event_type") != "web_retrieval_started"]
    assert terminals, rows
    assert all(r.get("lifecycle") != "succeeded" for r in terminals), terminals


def test_a_refused_retrieval_cannot_ground_a_current_claim():
    """D. Refusal must also close the claim gate: with no successful receipt the
    turn holds no current evidence, so it may not publish a current fact."""
    from core.unsourced_current_claim import turn_has_current_evidence

    context: dict = {
        "web_retrieval_receipts": [
            {
                "schema": "vool.web_retrieval_receipt.v1",
                "status": "refused",
                "lifecycle": "refused",
                "provider_id": "brave",
                "source_count": 0,
            }
        ]
    }
    # A web-derived snippet with a real reading on it: at base this alone grounded a
    # current claim, so an un-receipted rescue could speak as though a governed
    # search had worked.
    notes = [
        {
            "origin_domain": "example.com",
            "source_type": "web_derived",
            "summary": "The current stable kernel is 6.19.",
        }
    ]

    assert (
        turn_has_current_evidence(notes=notes, source_context=context) is False
    ), context
    # Control: the SAME note with a succeeded receipt does ground the claim, so the
    # refusal is what closed the gate -- not a gate that is closed for everything.
    grounded = dict(context)
    grounded["web_retrieval_receipts"] = [
        {
            "schema": "vool.web_retrieval_receipt.v1",
            "status": "available",
            "lifecycle": "succeeded",
            "provider_id": "brave",
            "source_count": 3,
        }
    ]
    assert turn_has_current_evidence(notes=notes, source_context=grounded) is True


# ------------------------------------------------------------------ E: keyless truth


def test_with_brave_absent_the_panel_api_says_no_key_and_never_claims_brave_ran(
    no_socket, no_key
):
    """E. Brave absent: the panel's payload must say so plainly, must not be a
    success, and must not report a source count it never retrieved."""
    payload = _post_provider_test("brave")

    assert payload["state"] == "no_key", payload
    assert payload["state"] != "ok", payload
    assert not payload.get("source_count"), payload
    assert not payload.get("keyed_or_keyless"), payload


def test_with_brave_absent_the_keyless_row_names_its_own_provider(no_key, rows):
    """E. Whatever answers instead must identify itself as keyless AND name the
    provider that ran. A bare 'search' row is what let a keyless scraper's work
    be read as the paid provider's."""
    from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval

    context: dict = {"surface": "openclaw", "session_id": "e-keyless"}
    started = begin_web_retrieval(
        context,
        kind="live_info_fast_path",
        query="latest news on rust",
        task_id="e-keyless",
        action="live_info_search",
    )
    terminal = finish_web_retrieval(
        context,
        started,
        notes=[
            {"origin_domain": "news.google.com", "search_provider": "google_news_rss"},
            {"origin_domain": "news.google.com", "search_provider": "google_news_rss"},
        ],
    )

    assert terminal["lifecycle"] == "succeeded", terminal
    assert terminal["keyed_or_keyless"] == "keyless", terminal
    assert "google_news_rss" in terminal["provider_label"], terminal
    assert "brave" not in terminal["provider_label"].lower(), terminal

    messages = _messages(rows)
    row = next((m for m in messages if "Web retrieval:" in m), "")
    assert "Keyless search (google_news_rss)" in row, messages
    assert "Brave" not in row, messages


def test_a_keyless_rescue_never_inherits_the_keyed_providers_name(no_key, rows):
    """E, the worst case. A retrieval STARTED as Brave that is answered by the
    keyless chain must be reported as keyless — the identity comes from what
    answered, never from what was attempted."""
    from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval

    context: dict = {"surface": "openclaw", "session_id": "e-rescue"}
    started = begin_web_retrieval(
        context,
        kind="web_search_tool",
        query="latest news on rust",
        task_id="e-rescue",
        action="search",
        provider_id="brave",
        keyed_or_keyless="keyed",
    )
    terminal = finish_web_retrieval(
        context,
        started,
        notes=[{"origin_domain": "news.google.com", "search_provider": "google_news_rss"}],
    )

    assert terminal["provider_id"] == "google_news_rss", terminal
    assert terminal["keyed_or_keyless"] == "keyless", terminal
    assert "brave" not in terminal["provider_label"].lower(), terminal
    messages = _messages(rows)
    assert not any("Brave" in m for m in messages), messages
