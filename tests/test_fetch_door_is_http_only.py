"""The one outbound door is an HTTP door — no other scheme may be opened through it.

urllib installs handlers for every scheme it knows (file:, ftp:, data:, ...), so before this
law a Request built from a non-web reference was opened by the door and its target's bytes
came back with ``status: ok`` while being recorded as a NETWORK fetch — reproduced with
``file:///etc/hosts`` through media ingestion's evidence fetch: the reference classified as
plain text, the page transport read the local file, and the browser lane's own file: refusal
did not scrub the already-fetched text out of the evidence.

The law binds at ``core.remote_fetch_policy._open_enforced`` — the single door every
outbound caller already converges on — so a fetch added later cannot forget to ask, and a
scheme the door has never heard of fails closed (only http/https are open).

Nothing here asserts prose: every check drives the real door and reads the typed refusal,
the receipt, and (for the allowed side) a transport error as proof the scheme gate passed.
"""
from __future__ import annotations

import urllib.request

import pytest

from core.remote_fetch_policy import (
    RemoteFetchRefusedError,
    open_remote,
    remote_fetch_policy_scope,
)

NON_WEB_URLS = [
    "file:///etc/hosts",
    "file:///Users/someone/.ssh/id_rsa",
    "ftp://example.com/pub/file",
    "data:text/plain,hello",
    "gopher://example.com/1",
    "blob:https://example.com/deadbeef",
]


def _open(url: str):
    return open_remote(urllib.request.Request(url), timeout=1.0)


# ── non-web schemes are refused typed, before any handler runs ───────────────


@pytest.mark.parametrize("url", NON_WEB_URLS)
def test_no_non_web_scheme_can_be_opened_through_the_door(url: str) -> None:
    with remote_fetch_policy_scope({"session_id": "http-door-scheme"}):
        with pytest.raises(RemoteFetchRefusedError) as caught:
            _open(url)
    message = str(caught.value).lower()
    assert "http/https" in message, message
    assert "no active turn" not in message, message


def test_the_scheme_refusal_happens_before_any_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not 'the fetch failed': the fetch was never attempted. If urllib's opener is ever
    reached for a file: URL, local bytes have already left the door's custody."""

    def _must_not_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("the door reached a transport for a non-web scheme")

    monkeypatch.setattr(urllib.request, "urlopen", _must_not_open)
    with remote_fetch_policy_scope({"session_id": "http-door-scheme"}):
        with pytest.raises(RemoteFetchRefusedError):
            _open("file:///etc/hosts")


def test_the_scheme_refusal_leaves_a_denied_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deny is a first-class fact: the refusal is on the ledger with its own decider."""
    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY

    context = {"session_id": "http-door-scheme"}
    with remote_fetch_policy_scope(context):
        with pytest.raises(RemoteFetchRefusedError):
            _open("file:///etc/hosts")

    receipts = context.get(EFFECT_RECEIPTS_CONTEXT_KEY) or ()
    denied = [r for r in receipts if "remote_fetch_policy.http_scheme" in repr(r)]
    assert denied, f"no scheme-denial receipt among {receipts!r}"
    assert "deni" in repr(denied[0]).lower()


# ── the web keeps working ────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/one", "https://127.0.0.1:9/two"])
def test_http_and_https_urls_pass_the_scheme_gate(url: str) -> None:
    """A connection error here is the proof the door let the WEB request through: nothing
    listens on that port, and only the scheme law is under test."""
    with remote_fetch_policy_scope({"session_id": "http-door-scheme"}):
        try:
            _open(url)
        except RemoteFetchRefusedError as refusal:
            assert "http/https" not in str(refusal).lower(), (
                f"{url} is a web request refused by the scheme law"
            )
        except Exception:
            pass  # transport error: the scheme gate passed


# ── the evidence lane that demonstrated the defect ──────────────────────────


def test_media_evidence_fetch_of_a_file_reference_yields_no_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reproducer, pinned: a file: reference classified as text must not produce the
    target's bytes in the evidence — and neither the page transport nor the browser lane
    may even be consulted for it."""
    import core.media_ingestion as mi

    calls: list[str] = []

    def _no_fetch(url: str, **kwargs: object) -> dict[str, str]:
        calls.append(f"http:{url}")
        return {"status": "ok", "text": "SHOULD NEVER APPEAR", "html": "", "final_url": url}

    def _no_render(url: str, **kwargs: object) -> dict[str, object]:
        calls.append(f"browser:{url}")
        return {"status": "disabled_by_policy", "final_url": url}

    monkeypatch.setattr(mi, "http_fetch_text", _no_fetch)
    monkeypatch.setattr(mi, "browser_render", _no_render)

    out = mi._fetch_reference_text("file:///etc/hosts")
    assert out["status"] == "not_a_web_reference"
    assert out["text"] == ""
    assert out["used_browser"] is False
    assert calls == [], f"the non-web reference reached a transport: {calls}"

    evidence = mi._normalize_item({"url": "file:///etc/hosts"}, fetch_text_reference=True)
    assert evidence is not None
    assert evidence.text == ""
    assert evidence.metadata.get("fetch_status") == "not_a_web_reference"
    assert "SHOULD NEVER APPEAR" not in repr(evidence.to_dict())


def test_media_evidence_fetch_of_a_web_reference_still_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repair must not take the text lane's legitimate workflow with it: an http
    reference is page-fetched through the same seam as before."""
    import core.media_ingestion as mi

    seen: list[str] = []

    def _fetch(url: str, **kwargs: object) -> dict[str, str]:
        seen.append(url)
        return {"status": "ok", "text": "x" * 700, "html": "", "final_url": url}

    monkeypatch.setattr(mi, "http_fetch_text", _fetch)
    out = mi._fetch_reference_text("https://example.com/story")
    assert seen == ["https://example.com/story"]
    assert out["status"] == "ok"
    assert out["text"] == "x" * 700
