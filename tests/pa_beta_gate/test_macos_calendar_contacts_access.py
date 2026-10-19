"""pa_beta_gate -- Apple Calendar and Apple Contacts in the packaged Mac app.

The packaged app could never reach either: Info.plist carried no Calendars or Contacts purpose string, the
bundle shipped neither pyobjc binding, the signed build's entitlements did not allow either resource, and VOOL
never asked macOS for Calendars access at all, so the state stayed "not determined" and VOOL never appeared under
Privacy & Security.

* Access is asked for once, from the user's own step in Settings (Add account, Refresh calendar list); sync,
  agenda reads and alerts never ask.
* Denied, restricted and write-only answers are typed refusals that name the System Settings pane; macOS shows
  its prompt only once, so none of them prompts again.
* The bundle declares the purpose strings, ships both bindings from the lock and carries the hardened-runtime
  entitlements the signed build needs.

LABELLED: the EventKit binding here is a double of pyobjc's class surface (authorization status, the two request
selectors, the store's calendar list), driven by a scripted user answer. The real prompt needs the packaged app on
a Mac; that check is recorded with the delivery.
"""
from __future__ import annotations

import plistlib
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._pc_calendar_rig import T0, Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

REPO = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
ENTITLEMENTS = REPO / "installer" / "bundle" / "VOOL.entitlements"
PANE = "System Settings > Privacy & Security > Calendars"


class _Calendar:
    def __init__(self, identifier: str, title: str) -> None:
        self._identifier, self._title = identifier, title

    def calendarIdentifier(self):
        return self._identifier

    def title(self):
        return self._title

    def source(self):
        return SimpleNamespace(title=lambda: "iCloud")

    def allowsContentModifications(self):
        return True


class _EventKitBinding:
    """LABELLED double of pyobjc's EventKit module. ``answer`` is what the user picks in the macOS prompt
    (None: the prompt is never answered)."""

    EKEntityTypeEvent = 0

    def __init__(self, status: int, *, answer: int | None = 3, full_access_selector: bool = True) -> None:
        binding = self
        self.status, self.answer, self.prompts, self.stores = status, answer, [], []
        self.calendars = [_Calendar("EK-HOME", "Home")]

        class _Store:
            def __init__(self) -> None:
                binding.stores.append(self)

            def respondsToSelector_(self, selector: str) -> bool:
                return full_access_selector and selector == "requestFullAccessToEventsWithCompletion:"

            def _ask(self, kind: str, handler) -> None:
                binding.prompts.append(kind)
                if binding.answer is None:
                    return
                binding.status = binding.answer
                # EventKit answers on its own queue, never on the caller's thread.
                threading.Thread(target=handler, args=(binding.answer == 3, None)).start()

            def requestFullAccessToEventsWithCompletion_(self, handler) -> None:
                self._ask("full_access", handler)

            def requestAccessToEntityType_completion_(self, entity, handler) -> None:
                assert entity == binding.EKEntityTypeEvent
                self._ask("legacy", handler)

            def calendarsForEntityType_(self, entity):
                return list(binding.calendars)

        class _EKEventStore:
            @staticmethod
            def authorizationStatusForEntityType_(entity):
                return binding.status

            @staticmethod
            def alloc():
                return SimpleNamespace(init=_Store)

        self.EKEventStore = _EKEventStore


def _store(binding: _EventKitBinding):
    from core.kas.adapters.eventkit_mac import EventKitStore

    store = EventKitStore.__new__(EventKitStore)
    store._ek = binding
    store._foundation = SimpleNamespace()
    store._store = binding.EKEventStore.alloc().init()
    return store


def test_the_first_ask_shows_the_macos_prompt_once_and_reads_through_a_fresh_store():
    binding = _EventKitBinding(0, answer=3)
    store = _store(binding)
    first_store = store._store

    assert store.request_access() == "authorized"
    assert binding.prompts == ["full_access"]
    assert store._store is not first_store, "a store made before the grant keeps its empty view of the database"
    assert [c.calendarIdentifier() for c in store.calendars()] == ["EK-HOME"]

    assert store.request_access() == "authorized"
    assert binding.prompts == ["full_access"], "an answered prompt is never shown again"


@pytest.mark.parametrize(("status", "state"), [(1, "restricted"), (2, "denied"), (4, "write_only")])
def test_an_answer_only_system_settings_can_change_never_prompts_and_reads_nothing(status, state):
    from core.kas.contract import CalendarRefusedError

    binding = _EventKitBinding(status)
    store = _store(binding)

    assert store.request_access() == state
    assert binding.prompts == []
    with pytest.raises(CalendarRefusedError) as refused:
        store.calendars()
    assert refused.value.status_code == 403 and refused.value.reason == "os_permission_denied"
    detail = refused.value.detail
    assert "Nothing was read or changed." in detail
    assert not re.search(r"not_determined|write_only|\b[0-4]\b", detail), f"raw state reached the user: {detail}"
    if state != "restricted":  # a device profile is not something the user can switch in System Settings
        assert PANE in detail, detail
    if state == "write_only":
        assert "Full Access" in detail, "write-only access cannot read a calendar; the step is to choose Full Access"


def test_an_unanswered_prompt_stays_not_determined_and_reads_nothing(monkeypatch):
    from core.kas.adapters import eventkit_mac
    from core.kas.contract import CalendarRefusedError

    monkeypatch.setattr(eventkit_mac, "_ACCESS_WAIT_SECONDS", 0.05)
    binding = _EventKitBinding(0, answer=None)
    store = _store(binding)

    assert store.request_access() == "not_determined"
    assert binding.prompts == ["full_access"]
    with pytest.raises(CalendarRefusedError) as refused:
        store.calendars()
    assert "Refresh calendar list" in refused.value.detail and PANE in refused.value.detail


def test_a_mac_before_14_asks_through_the_older_request():
    binding = _EventKitBinding(0, answer=3, full_access_selector=False)
    assert _store(binding).request_access() == "authorized"
    assert binding.prompts == ["legacy"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


def _factory(store):
    from core.kas.adapters.eventkit_mac import EventKitCalendarAdapter
    from core.kas.contract import AdapterConfig

    def factory(_account):
        return EventKitCalendarAdapter(transport=lambda request: None,
                                       config=AdapterConfig(provider_id="eventkit", base_url="eventkit://local"), store=store)

    return factory


def test_adding_apple_calendar_in_settings_asks_and_sync_never_does(home, monkeypatch):
    from core.operator import calendar_accounts, calendar_alerts

    clock = Clock(T0, monkeypatch)
    binding = _EventKitBinding(0, answer=3)
    factory = _factory(_store(binding))
    account = calendar_accounts.add_account(provider="eventkit", label="This Mac")
    assert binding.prompts == [], "adding the account row alone asks nothing"

    found = calendar_accounts.discover_calendars(account["account_id"], now_fn=clock, adapter_factory=factory)
    assert found["ok"] and found["status"] == "connected", found
    assert [c["calendar_id"] for c in found["calendars"]] == ["EK-HOME"]
    assert binding.prompts == ["full_access"]

    assert calendar_accounts.select_calendar(account["account_id"], "EK-HOME", selected=True)["ok"]
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=True)
    binding.status = 0  # the permission was reset on this Mac (tccutil reset Calendar) after setup
    report = calendar_alerts.sync_account(account["account_id"], now_fn=clock, adapter_factory=factory)
    assert not report["ok"], report
    assert binding.prompts == ["full_access"], "a background sync must never show the macOS prompt"


def test_a_declined_prompt_shows_the_system_settings_step_and_not_a_provider_reconnect(home, monkeypatch):
    from core.operator import calendar_accounts

    clock = Clock(T0, monkeypatch)
    binding = _EventKitBinding(0, answer=2)
    factory = _factory(_store(binding))
    account = calendar_accounts.add_account(provider="eventkit", label="This Mac")

    declined = calendar_accounts.discover_calendars(account["account_id"], now_fn=clock, adapter_factory=factory)
    assert not declined["ok"] and declined["status"] == "denied", declined
    assert PANE in declined["detail"] and "HTTP" not in declined["detail"], declined

    again = calendar_accounts.discover_calendars(account["account_id"], now_fn=clock, adapter_factory=factory)
    assert again["status"] == "denied" and binding.prompts == ["full_access"], "macOS answers a declined prompt only once"

    shown = calendar_accounts.settings_view(now_fn=clock)["accounts"][0]
    assert shown["status"] == "denied" and PANE in shown["status_detail"], shown
    assert shown["recovery"] == "", "the generic provider line ('reconnect it in Settings') points the wrong way here"


def _info_plist() -> dict:
    """The Info.plist the build writes, with its build-time variables filled."""
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    body = src.split('cat >"${APP}/Contents/Info.plist" <<PLIST\n', 1)[1].split("\nPLIST\n", 1)[0]
    filled = re.sub(r"\$\{[A-Z_]+\}", "x", body)
    return plistlib.loads(filled.encode("utf-8"))


def test_the_bundle_declares_the_calendar_and_contacts_purpose_strings():
    plist = _info_plist()
    for key in ("NSCalendarsFullAccessUsageDescription", "NSCalendarsUsageDescription", "NSContactsUsageDescription"):
        text = plist.get(key, "")
        assert isinstance(text, str) and len(text.split()) >= 8, f"Info.plist lacks a readable {key}: {text!r}"
        assert not re.search(r"EventKit|TCC|pyobjc|binding", text), f"developer words in {key}: {text}"


def test_the_signed_build_may_reach_calendars_and_contacts_under_the_hardened_runtime():
    entitlements = plistlib.loads(ENTITLEMENTS.read_bytes())
    assert entitlements.get("com.apple.security.personal-information.calendars") is True
    assert entitlements.get("com.apple.security.personal-information.addressbook") is True


def test_the_bundle_ships_both_bindings_pinned_by_the_lock():
    from ops import bundle_dependency_provenance as prov

    lean = {re.sub(r"[-_.]+", "-", name).lower() for name in prov.lean_requirements()}
    lock = prov.lock_artifacts()
    for package in ("pyobjc-framework-eventkit", "pyobjc-framework-contacts"):
        assert package in lean, f"the bundle's lean install dropped {package}"
        assert lock[package]["__version__"] == "12.2.2", f"{package} is not pinned in uv.lock"
        # The bundle embeds CPython 3.11 for arm64 and x86_64: a universal2 or pure wheel serves both.
        assert any(("cp311" in name and "universal2" in name) or name.endswith("-none-any.whl") for name in lock[package]), package
