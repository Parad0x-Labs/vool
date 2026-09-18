from __future__ import annotations

from core.self_update_check import (
    CHECK_INTERVAL_SECONDS,
    UpdateCheckState,
    changelog_lines,
    check_for_update,
    evaluate_release,
    is_newer_version,
    parse_version,
    should_check,
)


def _release(**overrides):
    base = {
        "tag_name": "v0.5.0",
        "name": "VOOL 0.5.0",
        "body": "- Added self-update\n- Fixed .null dead-end\n## Notes\n- Faster boot",
        "draft": False,
        "prerelease": False,
        "html_url": "https://github.com/Parad0x-Labs/vool/releases/tag/v0.5.0",
        "assets": [
            {"name": "VOOL-Windows-0.5.0.zip", "browser_download_url": "https://x/VOOL-Windows-0.5.0.zip"},
            {"name": "VOOL-Windows-0.5.0.sha256", "browser_download_url": "https://x/VOOL-Windows-0.5.0.sha256"},
        ],
    }
    base.update(overrides)
    return base


def test_parse_and_compare_versions() -> None:
    assert parse_version("v0.5.0") == (0, 5, 0)
    assert parse_version("0.4.0-closed-test") == (0, 4, 0)
    assert is_newer_version("v0.5.0", "0.4.0-closed-test") is True
    assert is_newer_version("0.4.0", "0.4.0") is False
    assert is_newer_version("0.3.9", "0.4.0") is False  # never a downgrade


def test_changelog_strips_bullets_and_headers() -> None:
    lines = changelog_lines("- Added self-update\n* Fixed dead-end\n## Notes\n1. Faster boot", limit=8)
    assert lines == ["Added self-update", "Fixed dead-end", "Faster boot"]
    assert changelog_lines("", limit=8) == []


def test_changelog_respects_limit() -> None:
    body = "\n".join(f"- change {i}" for i in range(20))
    assert len(changelog_lines(body, limit=5)) == 5


def test_should_check_gates_on_24h() -> None:
    state = UpdateCheckState(last_check_utc=1000.0)
    assert should_check(state, now=1000.0 + CHECK_INTERVAL_SECONDS) is True
    assert should_check(state, now=1000.0 + CHECK_INTERVAL_SECONDS - 1) is False


def test_evaluate_release_available_extracts_assets_and_changelog() -> None:
    result = evaluate_release("0.4.0-closed-test", _release(), dismissed_version="")
    assert result.available is True
    assert result.target_version == "v0.5.0"
    assert result.asset_url.endswith("VOOL-Windows-0.5.0.zip")
    assert result.sha256_url.endswith(".sha256")
    assert "Added self-update" in result.changelog


def test_evaluate_release_not_newer_is_unavailable() -> None:
    result = evaluate_release("0.5.0", _release(tag_name="v0.5.0"), dismissed_version="")
    assert result.available is False
    assert result.reason == "already up to date"


def test_evaluate_release_skips_draft_and_prerelease() -> None:
    assert evaluate_release("0.4.0", _release(draft=True), "").available is False
    assert evaluate_release("0.4.0", _release(prerelease=True), "").available is False
    assert evaluate_release("0.4.0", None, "").available is False


def test_evaluate_release_respects_dismissed_version() -> None:
    result = evaluate_release("0.4.0", _release(tag_name="v0.5.0"), dismissed_version="v0.5.0")
    assert result.available is False
    assert result.dismissed is True


def test_check_for_update_skips_when_checked_recently() -> None:
    state = UpdateCheckState(last_check_utc=1000.0)
    calls = {"n": 0}

    def fetcher():
        calls["n"] += 1
        return _release()

    availability, new_state = check_for_update(
        installed_version="0.4.0", state=state, now=1000.0 + 60, release_fetcher=fetcher
    )
    assert availability.available is False
    assert calls["n"] == 0  # no network call inside the 24h window
    assert new_state.last_check_utc == 1000.0  # unchanged


def test_check_for_update_available_updates_state() -> None:
    state = UpdateCheckState(last_check_utc=0.0)
    availability, new_state = check_for_update(
        installed_version="0.4.0-closed-test",
        state=state,
        now=float(CHECK_INTERVAL_SECONDS + 5),
        release_fetcher=lambda: _release(),
    )
    assert availability.available is True
    assert availability.target_version == "v0.5.0"
    assert new_state.last_check_utc == float(CHECK_INTERVAL_SECONDS + 5)
    assert new_state.last_offered_version == "v0.5.0"


def test_check_for_update_handles_no_release_info() -> None:
    availability, _ = check_for_update(
        installed_version="0.4.0",
        state=UpdateCheckState(last_check_utc=0.0),
        now=float(CHECK_INTERVAL_SECONDS + 5),
        release_fetcher=lambda: None,
    )
    assert availability.available is False
    assert "no release information" in availability.reason


def test_state_round_trips_through_dict() -> None:
    state = UpdateCheckState(last_check_utc=123.0, last_offered_version="v0.5.0", dismissed_version="v0.4.9")
    restored = UpdateCheckState.from_dict(state.to_dict())
    assert restored == state
