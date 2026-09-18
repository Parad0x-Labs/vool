"""Per-capability free/paid usage quotas: daily caps, reset, unlimited tiers, operator override."""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core import usage_quota as uq


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_TIER", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    uq.reset_usage()
    yield
    runtime_paths.configure_runtime_home(None)


def test_free_image_gen_is_three_per_day() -> None:
    for i in range(3):
        r = uq.consume_quota("image.generate", tier="free", today="2026-07-08")
        assert r.allowed and r.used == i + 1
    blocked = uq.consume_quota("image.generate", tier="free", today="2026-07-08")
    assert not blocked.allowed and blocked.remaining == 0 and blocked.used == 3


def test_check_quota_never_increments() -> None:
    uq.consume_quota("image.generate", tier="free", today="D")
    assert uq.check_quota("image.generate", tier="free", today="D").used == 1
    assert uq.check_quota("image.generate", tier="free", today="D").used == 1  # still 1


def test_daily_reset() -> None:
    for _ in range(3):
        uq.consume_quota("image.generate", tier="free", today="day1")
    assert not uq.check_quota("image.generate", tier="free", today="day1").allowed
    tomorrow = uq.check_quota("image.generate", tier="free", today="day2")
    assert tomorrow.allowed and tomorrow.used == 0


def test_video_free_tier_is_blocked() -> None:
    r = uq.consume_quota("video.generate", tier="free", today="D")
    assert not r.allowed and r.limit == 0


def test_operator_tier_is_unlimited() -> None:
    for _ in range(50):
        assert uq.consume_quota("image.generate", tier="operator", today="D").allowed
    c = uq.check_quota("image.generate", tier="operator", today="D")
    assert c.allowed and c.limit is None and c.remaining is None


def test_star_fallback_for_unknown_capability() -> None:
    assert uq.capability_limit("some.random.cap", "free") == 50  # tier '*' default


def test_active_tier_default_and_env(monkeypatch) -> None:
    assert uq.active_tier() == "free"
    monkeypatch.setenv("VOOL_TIER", "paid")
    assert uq.active_tier() == "paid"
    monkeypatch.setenv("VOOL_TIER", "bogus")
    assert uq.active_tier() == "free"  # unknown falls back


def test_operator_config_override() -> None:
    cfg = runtime_paths.active_config_home_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "quota_limits.json").write_text(
        json.dumps({"free": {"image.generate": {"per_day": 5}}}), encoding="utf-8"
    )
    assert uq.capability_limit("image.generate", "free") == 5
    for _ in range(5):
        assert uq.consume_quota("image.generate", tier="free", today="D").allowed
    assert not uq.consume_quota("image.generate", tier="free", today="D").allowed


def test_usage_persists() -> None:
    uq.consume_quota("email.send", tier="free", today="D")
    assert uq.usage_today("email.send", today="D") == 1
