"""Local Only means zero public egress. Every door, for the whole turn.

The composer offers "VOOL Auto Local Only — this machine only · cloud blocked".
The README and the runtime capability copy say the same. The policy did not:
`allow_web_fallback` stayed true under `local_only_mode`, and
`tests/test_local_only_policy.py` asserted that as the intended contract, so web
search, direct URL fetch, browser navigation to public origins, fallback
scrapers and attachment URL retrieval all remained reachable in the mode whose
whole promise is that they are not.

This file encodes the stronger, user-facing meaning as the product law:

    LOCAL ONLY = no public-network egress of any kind.

Bound at ONE place — ``core.remote_fetch_policy._open_enforced``, the single
outbound door ~45 call sites already converge on — so a door added later cannot
forget to ask. Loopback and operator-owned LAN endpoints stay reachable through
the EXISTING endpoint-classification law in ``core.auto_local_only_mode``; this
adds no second opinion about what "local" means.

Nothing here asserts prose. Every check drives the real door and reads the real
typed refusal and the real receipt.
"""
from __future__ import annotations

import contextlib
import urllib.request

import pytest

from core.auto_local_only_mode import CONTEXT_KEY, SELECTED_KEY, SELECTOR_VALUE
from core.remote_fetch_policy import (
    RemoteFetchRefusedError,
    open_remote,
    remote_fetch_policy_scope,
)

PUBLIC_URLS = [
    "https://duckduckgo.com/html/?q=weather",
    "https://api.openai.com/v1/chat/completions",
    "https://openrouter.ai/api/v1/chat/completions",
    "https://example.com/some/article",
    "http://93.184.216.34/raw",
    "https://raw.githubusercontent.com/x/y/main/z.txt",
]

LOCAL_URLS = [
    "http://127.0.0.1:11434/api/chat",
    "http://localhost:11435/api/chat",
    "http://[::1]:11434/api/chat",
    "http://192.168.1.42:11434/api/chat",
    "http://10.0.0.7:8080/v1/chat/completions",
]


def _local_only_context() -> dict:
    """The context a turn carries when the operator picked the Local Only lane."""
    return {
        "session_id": "local-only-egress",
        CONTEXT_KEY: True,
        SELECTED_KEY: True,
        "requested_model": SELECTOR_VALUE,
    }


def _open(url: str):
    return open_remote(urllib.request.Request(url), timeout=1.0)


# ── the public doors are shut ────────────────────────────────────────────────


@pytest.mark.parametrize("url", PUBLIC_URLS)
def test_no_public_url_can_be_opened_in_local_only(url: str) -> None:
    """Cloud model APIs, search, raw fetch and scrapers all use this one door."""
    with remote_fetch_policy_scope(_local_only_context()):
        with pytest.raises(RemoteFetchRefusedError) as caught:
            _open(url)
    assert "local only" in str(caught.value).lower(), str(caught.value)


def test_the_refusal_names_local_only_not_a_generic_denial(  ) -> None:
    """An operator must be able to tell WHY, and it must not read as an outage."""
    with remote_fetch_policy_scope(_local_only_context()):
        with pytest.raises(RemoteFetchRefusedError) as caught:
            _open(PUBLIC_URLS[0])
    message = str(caught.value).lower()
    assert "local only" in message
    assert "no active turn" not in message, message


def test_the_refusal_leaves_a_receipt_that_carries_no_request_content() -> None:
    """Evidence without transmission: the host is recorded, the payload never is."""
    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY

    secret_query = "ROMEO-9999-do-not-transmit"
    context = _local_only_context()
    with remote_fetch_policy_scope(context):
        with pytest.raises(RemoteFetchRefusedError):
            _open(f"https://duckduckgo.com/html/?q={secret_query}")

    receipts = context.get(EFFECT_RECEIPTS_CONTEXT_KEY) or ()
    assert receipts, "the refusal recorded nothing"
    blob = repr(receipts)
    assert "duckduckgo.com" in blob, "the receipt must name the host it refused"
    assert secret_query not in blob, (
        "the refusal receipt carried the request's own content off the decision path"
    )
    denied = [r for r in receipts if "deni" in repr(r).lower()]
    assert denied, f"no DENIED receipt among {receipts!r}"


# ── the local doors stay open ────────────────────────────────────────────────


@pytest.mark.parametrize("url", LOCAL_URLS)
def test_loopback_and_operator_owned_lan_endpoints_are_not_blocked(url: str) -> None:
    """Local Only is about leaving the machine, not about refusing to work.

    These must NOT raise the local-only refusal. They may fail to connect —
    nothing is listening in a test — and a connection error is the proof the door
    let them through.
    """
    with remote_fetch_policy_scope(_local_only_context()):
        try:
            _open(url)
        except RemoteFetchRefusedError as refusal:
            assert "local only" not in str(refusal).lower(), (
                f"{url} is an operator-owned local endpoint and was refused as public egress"
            )
        except Exception:
            pass  # a transport error means the door allowed it, which is the point


# ── a normal turn is unaffected ──────────────────────────────────────────────


def test_a_normal_auto_turn_is_not_blocked_by_this_law() -> None:
    """VOOL Auto keeps its receipted web lookup. This law binds Local Only only."""
    context = {"session_id": "ordinary-turn"}
    with remote_fetch_policy_scope(context):
        try:
            _open(PUBLIC_URLS[0])
        except RemoteFetchRefusedError as refusal:
            assert "local only" not in str(refusal).lower(), (
                "an ordinary Auto turn was refused by the Local Only law"
            )
        except Exception:
            pass


# ── the env cannot open the door ─────────────────────────────────────────────


def test_enable_web_env_cannot_override_an_active_local_only_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VOOL_ENABLE_WEB=1 must not silently re-open egress inside Local Only.

    Leaving Local Only is an explicit, visible operator action — changing the
    composer selection — not an environment variable set once and forgotten.
    """
    monkeypatch.setenv("VOOL_ENABLE_WEB", "1")
    monkeypatch.setenv("VOOL_ALLOW_WEB", "1")
    with remote_fetch_policy_scope(_local_only_context()):
        with pytest.raises(RemoteFetchRefusedError) as caught:
            _open(PUBLIC_URLS[0])
    assert "local only" in str(caught.value).lower()


def test_the_profile_policy_binds_the_same_veto(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine installed local-only gets the law without a per-turn selection."""
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    with remote_fetch_policy_scope({"session_id": "profile-local-only"}):
        with pytest.raises(RemoteFetchRefusedError) as caught:
            _open(PUBLIC_URLS[0])
    assert "local only" in str(caught.value).lower()


# ── capability truth ─────────────────────────────────────────────────────────


def test_capability_output_reports_effective_web_availability_not_the_ambient_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`allow_web_fallback` alone is not the answer to "can this turn use the web?".

    The ambient flag is pinned ON for both halves, so the only thing that differs
    between them is Local Only — otherwise this would pass on a machine that
    simply has web disabled, and prove nothing.
    """
    from core import policy_engine
    from core.remote_fetch_policy import effective_web_available

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    assert effective_web_available(_local_only_context()) is False
    assert effective_web_available({"session_id": "ordinary"}) is True


# ── the veto is load-bearing ─────────────────────────────────────────────────


def test_sabotage_disabling_the_local_only_veto_reopens_every_public_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neutralize the one check and the public URLs stop being refused."""
    from core import remote_fetch_policy as rfp

    monkeypatch.setattr(rfp, "_local_only_egress_forbidden", lambda context, url: (False, ""))
    with remote_fetch_policy_scope(_local_only_context()):
        for url in PUBLIC_URLS[:2]:
            try:
                _open(url)
            except RemoteFetchRefusedError as refusal:
                assert "local only" not in str(refusal).lower(), (
                    "sabotage no-op: the local-only refusal survived with its check "
                    "disabled, so that check is not what closes the door"
                )
            except Exception:
                pass


# ── the socket boundary ──────────────────────────────────────────────────────


def test_no_socket_is_opened_to_a_public_host_in_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The strongest form of the claim: measured at the socket, not at the door.

    A refusal that still opened a connection would leak the fact of the request —
    DNS, SNI, the operator's IP at the destination — even if no body followed. So
    this counts real ``socket.connect`` calls rather than trusting the exception.
    """
    import socket

    connects: list = []
    real_connect = socket.socket.connect

    def _counting_connect(self, address):
        connects.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _counting_connect)

    with remote_fetch_policy_scope(_local_only_context()):
        for url in PUBLIC_URLS:
            with pytest.raises(RemoteFetchRefusedError):
                _open(url)

    assert connects == [], f"Local Only opened {len(connects)} socket(s): {connects}"


def test_the_same_probe_does_open_a_socket_without_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control that makes the test above non-vacuous.

    Without it, a counter that never increments because the probe was broken would
    read exactly like a counter that never increments because egress was blocked.
    Aimed at a closed loopback port: the connect is ATTEMPTED (which is the
    measurement) and then refused by the OS, so nothing leaves the machine here
    either.
    """
    import socket

    connects: list = []
    real_connect = socket.socket.connect

    def _counting_connect(self, address):
        connects.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _counting_connect)

    with remote_fetch_policy_scope({"session_id": "control-turn"}), contextlib.suppress(Exception):
        # The connection is expected to fail — port 9 is closed. What is asserted is
        # that a connect() was ATTEMPTED, so the outcome is deliberately discarded.
        _open("http://127.0.0.1:9/definitely-closed")

    assert connects, (
        "the probe never reached a connect() at all, so the zero-socket assertion "
        "above would have passed for the wrong reason"
    )
