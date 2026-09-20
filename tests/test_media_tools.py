"""Image + video generation: creds gate, prompt gate, quota, provider shaping, disabled-by-default."""
from __future__ import annotations

import json
import types

import pytest

from core import credential_store, media_tools, runtime_paths, usage_quota
from core.effect_gateway import named_background_effect_scope


@pytest.fixture(autouse=True)
def _media_calls_run_under_the_production_scope():
    # Media generation is a priced effect: the gateway denies network fetches outside a
    # turn/named scope (R2b1 fail-closed), and production always reaches these tools via
    # execute_runtime_tool, which wraps them in named_background_effect_scope. These unit
    # drives run under the SAME authority, so the tool's own behavior (meters, quotas,
    # runpod wrapping) is what is under test; the scope grants nothing a real caller lacks.
    with named_background_effect_scope("tests.media_tools"):
        yield

from core.runtime_execution_tools import _image_generate, _video_generate, execute_runtime_tool


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_TIER", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    usage_quota.reset_usage()
    yield
    runtime_paths.configure_runtime_home(None)


def _store_service(account: str = "default", endpoint: str = "https://img.example.com/generate") -> None:
    credential_store.store_credential(
        f"image.api.{account}",
        json.dumps({"endpoint": endpoint, "api_key": "k"}),
        label="test image svc",
    )


def _fake_urlopen(monkeypatch, payload: dict) -> None:
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, *_a):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(media_tools.urllib.request, "urlopen", lambda _req, timeout=None: FakeResp())


def test_no_service_configured() -> None:
    r = media_tools.generate_image(prompt="a cat")
    assert not r.ok and r.status == "needs_credentials"


def test_no_prompt(monkeypatch) -> None:
    _store_service()
    _fake_urlopen(monkeypatch, {"url": "x"})
    r = media_tools.generate_image(prompt="")
    assert not r.ok and r.status == "invalid_prompt"


def test_generate_ok_and_meters(monkeypatch) -> None:
    _store_service()
    _fake_urlopen(monkeypatch, {"url": "https://img.example.com/out/1.png"})
    r = media_tools.generate_image(prompt="a neon skyline")
    assert r.ok and r.status == "executed"
    assert r.image_ref == "https://img.example.com/out/1.png"
    assert usage_quota.usage_today("image.generate") == 1


def test_free_tier_three_per_day(monkeypatch) -> None:
    _store_service()
    _fake_urlopen(monkeypatch, {"url": "x"})
    for _ in range(3):
        assert media_tools.generate_image(prompt="p").ok
    r = media_tools.generate_image(prompt="p")
    assert not r.ok and r.status == "quota_exceeded"


def test_generate_failure_is_not_metered(monkeypatch) -> None:
    _store_service()

    def boom(_req, timeout=None):
        raise OSError("down")

    monkeypatch.setattr(media_tools.urllib.request, "urlopen", boom)
    r = media_tools.generate_image(prompt="p")
    assert not r.ok and r.status == "generate_failed"
    assert usage_quota.usage_today("image.generate") == 0


def test_handler_delegates(monkeypatch) -> None:
    _store_service()
    _fake_urlopen(monkeypatch, {"image_url": "ref-123"})
    res = _image_generate({"prompt": "p"})
    assert res.ok and res.status == "executed" and res.details["image_ref"] == "ref-123"


def test_disabled_by_default() -> None:
    r = execute_runtime_tool("image.generate", {"prompt": "p"})
    assert r is not None and r.handled and not r.ok and r.status == "disabled"


def test_consume_quota_failure_does_not_crash(monkeypatch) -> None:
    _store_service()
    _fake_urlopen(monkeypatch, {"url": "https://img.example.com/out/1.png"})

    def boom(*_a, **_k):
        raise OSError("usage file read-only")

    monkeypatch.setattr(media_tools.usage_quota, "consume_quota", boom)
    r = media_tools.generate_image(prompt="a cat")  # paid call already succeeded
    assert r.ok and r.status == "executed" and r.image_ref == "https://img.example.com/out/1.png"


# --- video generation + provider shaping ---

def _store_video_service(account: str = "default", provider: str = "runpod") -> None:
    credential_store.store_credential(
        f"video.api.{account}",
        json.dumps({"endpoint": "https://vid.example.com/run", "api_key": "k", "provider": provider}),
        label="test video svc",
    )


def test_video_generate_runpod_wraps_input_and_parses_output(monkeypatch) -> None:
    _store_video_service(provider="runpod")
    monkeypatch.setattr(media_tools.usage_quota, "check_quota",
                        lambda *a, **k: types.SimpleNamespace(allowed=True, used=0, limit=30))
    monkeypatch.setattr(media_tools.usage_quota, "consume_quota", lambda *a, **k: None)
    captured: dict = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, *_a):
            return json.dumps({"output": {"url": "https://vid.example.com/out.mp4"}}).encode("utf-8")

    def fake(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(media_tools.urllib.request, "urlopen", fake)
    r = media_tools.generate_video(prompt="a neon studio scene")
    assert r.ok and r.ref == "https://vid.example.com/out.mp4" and r.provider == "runpod"
    assert captured["body"] == {"input": {"prompt": "a neon studio scene"}}   # runpod wraps under input
    assert captured["auth"] == "Bearer k"


def test_video_no_service_configured() -> None:
    r = media_tools.generate_video(prompt="x")
    assert not r.ok and r.status == "needs_credentials"


def test_video_generate_disabled_by_default() -> None:
    r = execute_runtime_tool("video.generate", {"prompt": "x"})
    assert r is not None and r.handled and not r.ok and r.status == "disabled"


def test_video_handler_delegates(monkeypatch) -> None:
    monkeypatch.setattr(media_tools, "generate_video",
                        lambda **k: media_tools.MediaResult(True, "executed", "ok",
                                                            ref="https://v.mp4", provider="runpod"))
    res = _video_generate({"prompt": "a scene"})
    assert res.ok and res.details["video_ref"] == "https://v.mp4" and res.details["provider"] == "runpod"
