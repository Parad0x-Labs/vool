"""Native profile authority; Cocoa classes are controlled protocol spies."""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from core import runtime_paths


def _window_module():
    path = Path(__file__).resolve().parents[1] / "installer/bundle/vool_window.py"
    spec = importlib.util.spec_from_file_location("native_webkit_profile_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Store:
    def __init__(self, persistent=False):
        self.persistent = persistent
        self.content = {}
        self.clears = 0

    def isPersistent(self):
        return self.persistent

    def removeDataOfTypes_modifiedSince_completionHandler_(self, types, since, done):
        self.clears += 1
        self.content.clear()
        done()


class _Stores:
    def __init__(self, persistent=False):
        self.persistent = persistent
        self.saved = _Store(True)
        self.saved.content["owner-cookie"] = "unchanged"
        self.default_calls = 0
        self.created = []

    def defaultDataStore(self):
        self.default_calls += 1
        return self.saved

    def nonPersistentDataStore(self):
        store = _Store(self.persistent)
        self.created.append(store)
        return store

    def allWebsiteDataTypes(self):
        return {"cookies", "local-storage"}


class _Configuration:
    def __init__(self, stores):
        self.stores = stores
        self.store = None

    def init(self):
        return self

    def setWebsiteDataStore_(self, store):
        self.store = store

    def websiteDataStore(self):
        # The default is lazy; observing it before the owner binds is a leak.
        if self.store is None:
            self.store = self.stores.defaultDataStore()
        return self.store


class _Configurations:
    def __init__(self, stores):
        self.stores = stores

    def alloc(self):
        return _Configuration(self.stores)


@pytest.fixture
def backend(monkeypatch, tmp_path):
    module = _window_module()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile-a"))
    monkeypatch.setattr(module, "_log", lambda message: None)
    stores = _Stores()
    original = SimpleNamespace(WKWebsiteDataStore=stores, WKWebViewConfiguration=_Configurations(stores),
                               untouched_constant=17)
    cocoa = ModuleType("webview.platforms.cocoa")
    cocoa.WebKit = original
    cocoa.BrowserView = SimpleNamespace(instances={})
    platforms = ModuleType("webview.platforms")
    platforms.cocoa = cocoa
    monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", cocoa)
    return module, cocoa, original, stores


def _backend_view(cocoa):
    # Same relevant ordering as Cocoa: native view copies config BEFORE the
    # backend's later defaultDataStore/setWebsiteDataStore/private-mode clear.
    config = cocoa.WebKit.WKWebViewConfiguration.alloc().init()
    native_store_at_init = config.websiteDataStore()
    datastore = cocoa.WebKit.WKWebsiteDataStore.defaultDataStore()
    config.setWebsiteDataStore_(datastore)
    datastore.removeDataOfTypes_modifiedSince_completionHandler_(
        cocoa.WebKit.WKWebsiteDataStore.allWebsiteDataTypes(), 0, lambda: None,
    )
    return SimpleNamespace(store=native_store_at_init, backend_store=datastore,
                           show=lambda: None, restore=lambda: None,
                           evaluate_js=lambda script: None, hide=lambda: None)


def test_nonpersistent_store_is_bound_before_config_is_copied_and_default_untouched(backend):
    module, cocoa, original, stores = backend
    module._install_isolated_webkit_profile()
    window = _backend_view(cocoa)
    assert window.store is stores.created[0] is window.backend_store
    assert window.store.isPersistent() is False
    assert stores.default_calls == stores.saved.clears == 0
    assert stores.saved.content == {"owner-cookie": "unchanged"}
    assert original.WKWebsiteDataStore is stores
    assert cocoa.WebKit.untouched_constant == 17
    assert module.native_webkit_profile_status() == {
        "mode": "nonpersistent", "is_persistent": False, "configured_views": 1,
    }


def test_new_config_constructor_and_all_secondary_windows_inherit_owned_store(backend):
    module, cocoa, _original, stores = backend
    module._install_isolated_webkit_profile()
    assert cocoa.WebKit.WKWebViewConfiguration.new().websiteDataStore() is stores.created[0]
    created = []

    def create(*args, **kwargs):
        window = _backend_view(cocoa)
        created.append(window)
        return window

    api = module._WindowApi()
    api.set_window(_backend_view(cocoa), SimpleNamespace(create_window=create))
    assert api.open_settings()["ok"]
    assert api.open_setup()["ok"]
    assert api.detach_companion({"state": "idle"})["ok"]
    assert len(created) == 3
    assert all(window.store is stores.created[0] for window in created)
    assert stores.default_calls == stores.saved.clears == 0


def test_separate_homes_and_new_launch_instances_do_not_inherit_content(backend, monkeypatch, tmp_path):
    module, cocoa, original, stores = backend
    module._install_isolated_webkit_profile()
    first = cocoa.WebKit.WKWebsiteDataStore.defaultDataStore()
    first.content["private-cookie"] = "profile-a"
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile-b"))
    module._install_isolated_webkit_profile()
    second = cocoa.WebKit.WKWebsiteDataStore.defaultDataStore()
    assert second is not first and second.content == {}
    # A separate host starts with the original backend namespace, as processes do.
    another = _window_module()
    monkeypatch.setattr(another, "_log", lambda message: None)
    cocoa.WebKit = original
    another._install_isolated_webkit_profile()
    third = cocoa.WebKit.WKWebsiteDataStore.defaultDataStore()
    assert third is not first and third is not second and third.content == {}
    assert stores.default_calls == stores.saved.clears == 0


@pytest.mark.parametrize("persistent", [True, None, "false", 0])
def test_unproved_or_persistent_store_refuses_without_default_access(backend, persistent):
    module, cocoa, original, stores = backend
    stores.persistent = persistent
    with pytest.raises(module.NativeWebKitProfileError, match="unverified or persistent"):
        module._install_isolated_webkit_profile()
    assert cocoa.WebKit is original
    assert stores.default_calls == stores.saved.clears == 0


def test_missing_nonpersistent_api_and_live_views_refuse(backend, monkeypatch):
    module, cocoa, original, stores = backend
    monkeypatch.setattr(original, "WKWebsiteDataStore", SimpleNamespace())
    with pytest.raises(module.NativeWebKitProfileError, match="unavailable"):
        module._install_isolated_webkit_profile()
    monkeypatch.setattr(original, "WKWebsiteDataStore", stores)
    cocoa.BrowserView.instances["existing"] = object()
    with pytest.raises(module.NativeWebKitProfileError, match="after native views"):
        module._install_isolated_webkit_profile()
    assert stores.default_calls == stores.saved.clears == 0


def test_configuration_binding_failure_is_typed_and_never_returns_unsafe_config(backend, monkeypatch):
    module, cocoa, original, stores = backend
    class RejectingConfig(_Configuration):
        def setWebsiteDataStore_(self, store):
            raise RuntimeError("unavailable selector")
    monkeypatch.setattr(original, "WKWebViewConfiguration",
                        SimpleNamespace(alloc=lambda: RejectingConfig(stores)))
    module._install_isolated_webkit_profile()
    with pytest.raises(module.NativeWebKitProfileError, match="could not bind"):
        cocoa.WebKit.WKWebViewConfiguration.alloc().init()
    assert stores.default_calls == stores.saved.clears == 0


def test_conventional_saved_profile_and_non_cocoa_platform_are_unchanged(backend, monkeypatch):
    module, cocoa, original, stores = backend
    monkeypatch.setenv("VOOL_HOME", str(runtime_paths.VOOL_HOME))
    module._install_isolated_webkit_profile()
    assert cocoa.WebKit is original and stores.created == []
    monkeypatch.setenv("VOOL_HOME", "/unused-custom-profile")
    monkeypatch.setattr(module.sys, "platform", "linux")
    module._install_isolated_webkit_profile()
    assert cocoa.WebKit is original and stores.created == []


@pytest.mark.parametrize("failure_stage", ["store", "configuration"])
def test_actual_main_refuses_before_window_start_even_with_fallback_optin(backend, monkeypatch, failure_stage):
    module, cocoa, original, stores = backend
    from installer.bundle import native_runtime_supervisor as runtime
    supervisor = SimpleNamespace(owns_runtime=False, ensure_ready=lambda: {"commit": "fixture"},
                                 shutdown=lambda: None)
    monkeypatch.setattr(runtime.NativeRuntimeSupervisor, "from_environment", lambda **kwargs: supervisor)
    for name in ("_sweep_stale_runtime_pidfile", "_claim_macos_app_name", "_install_native_frame_authority"):
        monkeypatch.setattr(module, name, lambda: None)
    monkeypatch.setattr(module, "_single_instance", lambda: True)
    monkeypatch.setattr(module, "_install_termination_handlers", lambda *args: None)
    monkeypatch.setattr(module, "_fallback", lambda: pytest.fail("profile refusal must not use fallback"))
    monkeypatch.setenv("VOOL_ALLOW_BROWSER_FALLBACK", "1")
    calls = {"created": 0, "started": 0}
    if failure_stage == "store":
        stores.persistent = True
    else:
        original.WKWebViewConfiguration = SimpleNamespace(alloc=lambda: None)
    webview = ModuleType("webview")
    def create(*args, **kwargs):
        result = _backend_view(cocoa)
        calls["created"] += 1
        return result
    def start(*args, **kwargs):
        calls["started"] += 1
    webview.create_window, webview.start = create, start
    monkeypatch.setitem(sys.modules, "webview", webview)
    assert module.main() == 1
    assert calls == {"created": 0, "started": 0}
    assert stores.default_calls == stores.saved.clears == 0


@pytest.mark.parametrize("old_menu,gui_unavailable", [(False, False), (True, False), (False, True)])
def test_main_selects_verified_cocoa_and_never_drops_gui_on_menu_retry(backend, monkeypatch,
                                                                     old_menu, gui_unavailable):
    module, cocoa, _original, stores = backend
    from installer.bundle import native_runtime_supervisor as runtime
    supervisor = SimpleNamespace(owns_runtime=False, ensure_ready=lambda: {"commit": "fixture"},
                                 shutdown=lambda: None)
    monkeypatch.setattr(runtime.NativeRuntimeSupervisor, "from_environment", lambda **kwargs: supervisor)
    for name in ("_sweep_stale_runtime_pidfile", "_claim_macos_app_name", "_install_native_frame_authority",
                 "_register_url_scheme_handler"):
        monkeypatch.setattr(module, name, lambda: None)
    monkeypatch.setattr(module, "_single_instance", lambda: True)
    monkeypatch.setattr(module, "_install_termination_handlers", lambda *args: None)
    monkeypatch.setattr(module, "_start_runtime_watchdog", lambda *args: threading.Event())
    monkeypatch.setattr(module, "_start_notification_bridge", lambda *args: None)
    monkeypatch.setattr(module, "_dock_post_start", lambda *args: None)
    monkeypatch.setattr(module, "_settings_menu", lambda *args: [])
    monkeypatch.setattr(module, "_fallback", lambda: pytest.fail("profile refusal must not use fallback"))
    monkeypatch.setenv("PYWEBVIEW_GUI", "qt")
    monkeypatch.setenv("VOOL_ALLOW_BROWSER_FALLBACK", "1")
    calls = []
    webview = ModuleType("webview")
    webview.create_window = lambda *args, **kwargs: _backend_view(cocoa)
    def start(*args, **kwargs):
        calls.append(kwargs)
        if gui_unavailable or (old_menu and "menu" in kwargs):
            raise TypeError("unsupported keyword")
    webview.start = start
    monkeypatch.setitem(sys.modules, "webview", webview)
    assert module.main() == (1 if gui_unavailable else 0)
    assert calls and all(call["gui"] == "cocoa" for call in calls)
    assert len(calls) == (2 if old_menu or gui_unavailable else 1)
    assert stores.default_calls == stores.saved.clears == 0
