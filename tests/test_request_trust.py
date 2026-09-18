"""Owner-local request trust signal + the adapter's insecure-transport key guard.

These pin the server-side boundary that keeps a caller-supplied body from claiming owner
privileges: the loopback peer is owner-local, a forged surface is not, reserved trust keys
are stripped, and a BYOK bearer key is never sent over a plaintext non-loopback transport.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.request_trust import (
    OWNER_LOCAL_KEY,
    is_loopback_host,
    request_is_owner_local,
    strip_reserved_trust_keys,
)


def test_is_loopback_host():
    for host in ("127.0.0.1", "::1", "localhost", "127.0.0.5", "::ffff:127.0.0.1"):
        assert is_loopback_host(host) is True, host
    for host in ("", "10.0.0.4", "192.168.1.9", "example.com", "0.0.0.0"):
        assert is_loopback_host(host) is False, host


def test_strip_reserved_trust_keys():
    cleaned = strip_reserved_trust_keys(
        {
            "_owner_local": True,
            "cloud_escalation_approved": True,
            "access_policy": "forged",
            "_context_access_policy": "forged",
            "grant_confirmed_profile": True,
            "surface": "openclaw",
            "keep": 1,
        }
    )
    assert cleaned == {"surface": "openclaw", "keep": 1}
    assert strip_reserved_trust_keys(None) == {}


def test_owner_local_prefers_stamped_flag():
    # Server-stamped flag is authoritative even when the (forgeable) surface says otherwise.
    assert request_is_owner_local({OWNER_LOCAL_KEY: True, "surface": "channel"}) is True
    assert request_is_owner_local({OWNER_LOCAL_KEY: False, "surface": "cli"}) is False


def test_owner_local_fallback_by_in_process_surface():
    # No stamped flag -> in-process caller, surface is trustworthy.
    assert request_is_owner_local({"surface": "cli"}) is True
    assert request_is_owner_local({}) is True          # absent context -> in-process owner
    assert request_is_owner_local({"surface": "channel"}) is False
    assert request_is_owner_local({"surface": "api"}) is False


# ── adapter: withhold the bearer key over an insecure transport ──────────────

def _adapter_stub(base_url: str):
    return SimpleNamespace(manifest=SimpleNamespace(provider_id="openrouter-byok",
                                                    runtime_config={"base_url": base_url}))


def test_key_transport_safe_over_https():
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    assert OpenAICompatibleAdapter._key_transport_is_safe(_adapter_stub("https://openrouter.ai/api/v1")) is True


def test_key_transport_safe_over_loopback_http():
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    for url in ("http://127.0.0.1:1234/v1", "http://localhost:8000/v1"):
        assert OpenAICompatibleAdapter._key_transport_is_safe(_adapter_stub(url)) is True, url


def test_key_transport_unsafe_over_plaintext_remote():
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    # Key must NOT be sent in cleartext to an arbitrary remote host.
    assert OpenAICompatibleAdapter._key_transport_is_safe(_adapter_stub("http://evil.example.com/v1")) is False
    assert OpenAICompatibleAdapter._key_transport_is_safe(_adapter_stub("")) is False


def test_channel_context_is_never_owner_local_even_with_a_spoofed_surface():
    """A channel is a REMOTE surface, so build_source_context must stamp owner-local False.

    Without the stamp, request_is_owner_local falls back to the `surface` string, and a
    ChannelRequest carrying surface="cli"/"local"/"desktop"/"" would hand a remote user the
    owner's privileges (cloud key set/clear, model switch, and any future paid spend).
    """
    from core.channel_gateway import ChannelRequest, build_source_context
    from core.request_trust import OWNER_LOCAL_KEY, request_is_owner_local

    for spoofed in ("cli", "local", "desktop", "", "channel"):
        ctx = build_source_context(
            ChannelRequest(platform="telegram", user_id="u1", channel_id="c1", text="hi", surface=spoofed)
        )
        assert ctx[OWNER_LOCAL_KEY] is False
        assert request_is_owner_local(ctx) is False, f"surface={spoofed!r} must not be owner-local"
