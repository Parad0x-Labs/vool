"""Tests for the in-chat self-update offer/apply short-circuit."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.self_update_offer import maybe_handle_update_offer


class _OfferStore:
    def __init__(self) -> None:
        self.rows: dict[str, str] = {}

    def mark(self, sid, ver) -> None:
        self.rows[sid] = ver

    def load(self, sid):
        return {"target_version": self.rows[sid]} if sid in self.rows else None

    def clear(self, sid) -> None:
        self.rows.pop(sid, None)


def _av(*, available=True, target="v0.5.0", assets=True, installed="0.4.0"):
    return SimpleNamespace(
        available=available,
        target_version=target,
        asset_url="https://x/vool-windows.zip" if assets else "",
        sha256_url="https://x/vool-windows.zip.sha256" if assets else "",
        installed_version=installed,
        changelog=["Fixed a thing", "Added another"],
    )


# The version the runtime believes it is running. Injected, never read from core.app_version: the
# gate under test compares the offered target against the INSTALLED version, so leaving that side
# live meant every one of these tests silently depended on whatever the app version happened to be.
# It broke exactly that way on the 0.5.0 bump -- the fixture target "v0.5.0" stopped being strictly
# newer than the app's own 0.5.0, and four tests failed for a reason that had nothing to do with
# the behaviour they assert.
INSTALLED = "0.4.0"


def _call(
    text,
    sid="s1",
    *,
    av=None,
    dismissed="",
    store=None,
    launcher=None,
    installed=INSTALLED,
):
    store = store or _OfferStore()
    launcher = launcher or mock.Mock(return_value=True)
    saver = mock.Mock()
    result = maybe_handle_update_offer(
        text,
        sid,
        availability_loader=lambda: av,
        check_state_loader=lambda: SimpleNamespace(dismissed_version=dismissed),
        check_state_saver=saver,
        launcher=launcher,
        mark_offered_fn=store.mark,
        load_offered_fn=store.load,
        clear_offered_fn=store.clear,
        installed_version_loader=lambda: installed,
    )
    return result, store, launcher, saver


def test_no_update_cached_returns_none() -> None:
    result, _s, launcher, _sv = _call("hi", av=None)
    assert result is None
    launcher.assert_not_called()


def test_greeting_surfaces_offer_without_applying() -> None:
    result, store, launcher, _sv = _call("hey there", av=_av())
    assert result is not None and result["intent"] == "self_update_offer"
    assert "v0.5.0" in result["response"] and "Fixed a thing" in result["response"]
    assert store.rows.get("s1") == "v0.5.0"  # marked as offered
    launcher.assert_not_called()  # surfacing never installs


def test_substantive_message_does_not_hijack() -> None:
    result, _s, launcher, _sv = _call("explain how solana rent works", av=_av())
    assert result is None
    launcher.assert_not_called()


def test_update_now_applies() -> None:
    result, _s, launcher, _sv = _call("update now", av=_av())
    assert result is not None and result["intent"] == "self_update_apply"
    launcher.assert_called_once()
    assert "background" in result["response"].lower()


def test_bare_yes_applies_only_with_pending_offer() -> None:
    store = _OfferStore()
    # no pending -> bare yes does nothing
    result, _s, launcher, _sv = _call("yes", av=_av(), store=store)
    assert result is None
    launcher.assert_not_called()
    # after an offer was shown, bare yes applies
    store.mark("s1", "v0.5.0")
    result2, _s2, launcher2, _sv2 = _call("yes", av=_av(), store=store)
    assert result2 is not None and result2["intent"] == "self_update_apply"
    launcher2.assert_called_once()


def test_no_dismisses_and_records_declined_version() -> None:
    store = _OfferStore()
    store.mark("s1", "v0.5.0")
    result, store2, launcher, saver = _call("no", av=_av(), store=store)
    assert result is not None and result["intent"] == "self_update_dismissed"
    launcher.assert_not_called()
    assert "s1" not in store2.rows  # cleared
    saver.assert_called_once()  # dismissed_version persisted


def test_dismissed_version_not_reoffered() -> None:
    result, _s, launcher, _sv = _call("hello", av=_av(target="v0.5.0"), dismissed="v0.5.0")
    assert result is None  # already declined this version
    launcher.assert_not_called()


def test_no_installable_package_does_not_apply() -> None:
    result, _s, launcher, _sv = _call("update now", av=_av(assets=False))
    assert result is None  # available but no verifiable package
    launcher.assert_not_called()


def test_launcher_failure_reports_cli_fallback() -> None:
    result, _s, _launcher, _sv = _call("update now", av=_av(), launcher=mock.Mock(return_value=False))
    assert result is not None and result["intent"] == "self_update_apply"
    assert result["success"] is False
    assert "vool update --apply" in result["response"]


# ---------------------------------------------------------------------------------------------
# The stale-cache gate itself. This is the behaviour the strictly-newer check exists for, and
# until the installed version became injectable it could not be asserted deterministically --
# it depended on the real app version, which is what broke on the 0.5.0 bump.
# ---------------------------------------------------------------------------------------------


def test_a_target_equal_to_the_installed_version_is_never_offered_or_applied() -> None:
    """The just-applied case: the update ran, the availability cache has not refreshed yet, and
    the runtime is now AT the target. Re-offering it would loop; re-applying it would reinstall
    the version already running."""
    offer, _s, launcher, _sv = _call("hey there", av=_av(target="v0.5.0"), installed="0.5.0")
    assert offer is None, "an update equal to the installed version must not be offered"
    launcher.assert_not_called()

    apply_result, _s2, launcher2, _sv2 = _call(
        "update now", av=_av(target="v0.5.0"), installed="0.5.0"
    )
    assert apply_result is None, "an update equal to the installed version must not be applied"
    launcher2.assert_not_called()


def test_a_target_older_than_the_installed_version_is_never_applied() -> None:
    """A downgrade must not be installable, even on an explicit apply intent."""
    result, _s, launcher, _sv = _call("update now", av=_av(target="v0.4.1"), installed="0.5.0")
    assert result is None
    launcher.assert_not_called()


def test_a_bare_yes_cannot_apply_a_target_the_runtime_already_has() -> None:
    """The pending-offer path has its own apply branch, so it needs the gate proved separately:
    an offer shown before the update landed must not still be actionable afterwards."""
    store = _OfferStore()
    store.mark("s1", "v0.5.0")
    result, _s, launcher, _sv = _call("yes", av=_av(target="v0.5.0"), installed="0.5.0", store=store)
    assert result is None
    launcher.assert_not_called()


def test_production_default_reads_the_live_installed_version() -> None:
    """The seam must not have silently changed what production does: with no loader injected, the
    gate resolves core.app_version.installed_version. Pinned so a future refactor cannot quietly
    swap the default to the cached av.installed_version, which is the stale value the gate rejects."""
    from core.app_version import installed_version

    store = _OfferStore()
    # av.installed_version is deliberately an ancient value; if the gate ever read THAT instead of
    # the live version, this target would look installable and a result would come back.
    av = _av(target=f"v{installed_version()}", installed="0.0.1")
    result = maybe_handle_update_offer(
        "hey there",
        "s1",
        availability_loader=lambda: av,
        check_state_loader=lambda: SimpleNamespace(dismissed_version=""),
        check_state_saver=mock.Mock(),
        launcher=mock.Mock(return_value=True),
        mark_offered_fn=store.mark,
        load_offered_fn=store.load,
        clear_offered_fn=store.clear,
    )
    assert result is None, (
        "with no injected loader the gate must compare against the LIVE installed version; "
        "a target equal to it is not strictly newer and must not be offered"
    )
