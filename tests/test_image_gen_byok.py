"""Image generation BYOK (fal.ai) — key acceptance, un-gate, NL intent, and a mocked generate call."""

from __future__ import annotations

import json

import pytest

from core import media_tools
from core.agent_runtime import fast_command_surface as fcs
from core.execution.constants import image_generation_intent


@pytest.fixture
def mem_store(monkeypatch):
    """In-memory credential store so no real key is written to disk."""
    store: dict[str, str] = {}
    monkeypatch.setattr(media_tools.credential_store, "store_credential", lambda name, value, label="": store.__setitem__(name, value))
    monkeypatch.setattr(media_tools.credential_store, "get_credential", lambda name: store.get(name))
    monkeypatch.setattr(media_tools.credential_store, "has_credential", lambda name: name in store)
    monkeypatch.setattr(media_tools.credential_store, "delete_credential", lambda name: bool(store.pop(name, None) is not None))
    return store


def test_image_generation_intent_positive_and_negative() -> None:
    assert image_generation_intent("generate an image of a goldfish holding a credit card") == "a goldfish holding a credit card"
    assert image_generation_intent("draw a logo for web0") == "web0"
    # advisory / file-op / no-subject must NOT trigger a generation
    assert image_generation_intent("how do i generate images?") is None
    assert image_generation_intent("show me the image on my disk") is None
    assert image_generation_intent("generate a report") is None
    assert image_generation_intent("make an image") is None
    assert image_generation_intent(
        "give me opus vs gpt vs kimi and tell me which is best for coding, picture generation and so on"
    ) is None


def test_detect_fal_key() -> None:
    assert media_tools.detect_fal_key("b1c2d3e4-5678-90ab-cdef-1234567890ab:9f8e7d6c5b4a39281706abcdef012345") is True
    assert media_tools.detect_fal_key("sk-or-v1-deadbeef") is False
    assert media_tools.detect_fal_key("") is False


def test_configure_has_forget_image_service_roundtrip(mem_store) -> None:
    assert media_tools.has_image_service() is False
    ok, last4 = media_tools.configure_image_service(api_key="ID-0000:secretsecretsecret01", provider="fal")
    assert ok is True and last4 == "et01"
    assert media_tools.has_image_service() is True
    blob = json.loads(mem_store["image.api.default"])
    assert blob["provider"] == "fal"
    assert blob["endpoint"] == "https://fal.run/fal-ai/flux/schnell"  # default model
    assert media_tools.forget_image_service() is True
    assert media_tools.has_image_service() is False


def test_image_key_command_seals_and_forgets(mem_store) -> None:
    reply = fcs.maybe_handle_image_key_command("image key ID-0000:secretsecretsecret01", owner_local=True)
    assert "turned image generation on" in reply
    assert media_tools.has_image_service() is True
    # a remote (non-owner) session can neither set nor wipe it
    assert "own local session" in fcs.maybe_handle_image_key_command("image key forget", owner_local=False)
    assert media_tools.has_image_service() is True
    assert "removed" in fcs.maybe_handle_image_key_command("image key forget", owner_local=True)
    assert media_tools.has_image_service() is False


def test_image_generate_ungates_when_service_present(mem_store, monkeypatch) -> None:
    from core import runtime_tool_contracts as rtc

    def _image_contract():
        return next(c for c in rtc.runtime_tool_contracts() if c.intent == "image.generate")

    # no service -> unsupported
    assert _image_contract().supported is False
    media_tools.configure_image_service(api_key="ID-0000:secretsecretsecret01", provider="fal")
    # a sealed image key is itself the opt-in -> supported
    assert _image_contract().supported is True


def test_generate_image_reads_fal_response(mem_store, monkeypatch) -> None:
    media_tools.configure_image_service(api_key="ID-0000:secretsecretsecret01", provider="fal")

    class _FakeResp:
        def read(self, n: int = -1) -> bytes:
            return json.dumps({"images": [{"url": "https://fal.media/files/out.png"}]}).encode("utf-8")

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    from core import usage_quota

    monkeypatch.setattr(usage_quota, "check_quota", lambda *a, **k: type("Q", (), {"allowed": True, "used": 0, "limit": 100})())
    monkeypatch.setattr(usage_quota, "consume_quota", lambda *a, **k: None)

    result = media_tools.generate_image(prompt="a goldfish", opener=lambda req, timeout=None: _FakeResp())
    assert result.ok is True
    assert result.image_ref == "https://fal.media/files/out.png"
