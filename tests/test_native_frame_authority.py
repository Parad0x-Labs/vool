import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from installer.bundle.native_frame_authority import (
    guard_handler,
    install_macos_frame_authority,
    is_main_frame_message,
)


def message(flag):
    return SimpleNamespace(frameInfo=lambda: SimpleNamespace(isMainFrame=lambda: flag))


@pytest.mark.parametrize('flag', [False, 0, None, 'true', 2])
def test_subframes_and_non_native_flags_do_not_dispatch(flag):
    handler = Mock()
    assert guard_handler(handler)(object(), object(), message(flag)) is None
    handler.assert_not_called()


@pytest.mark.parametrize('flag', [True, 1])
def test_main_frame_dispatch_retains_identity_arguments_and_return(flag):
    receiver, controller, event = object(), object(), message(flag)
    handler = Mock(return_value='unchanged')
    assert guard_handler(handler)(receiver, controller, event) == 'unchanged'
    handler.assert_called_once_with(receiver, controller, event)


def test_missing_or_faulted_native_frame_metadata_is_not_authority():
    assert not is_main_frame_message(object())
    faulty = Mock()
    faulty.frameInfo.side_effect = RuntimeError('frame gone')
    assert not is_main_frame_message(faulty)


def test_caller_payload_does_not_promote_a_subframe():
    event = message(False)
    event.body = lambda: '{"isMainFrame": true, "role": "owner"}'
    assert not is_main_frame_message(event)


@pytest.fixture
def backend(monkeypatch):
    selector_name = 'userContentController_didReceiveScriptMessage_'
    def selector(fn, **kwargs):
        return SimpleNamespace(callable=fn, **kwargs)
    classes = [type(name, (), {}) for name in ('Action', 'Print')]
    originals = [Mock(), Mock()]
    for cls, original in zip(classes, originals, strict=True):
        setattr(cls, selector_name, selector(original, selector=b'handler:', signature=b'v@:@@'))
    def add(cls, methods):
        setattr(cls, selector_name, methods[0])
    cocoa = SimpleNamespace(BrowserView=SimpleNamespace(
        JSBridge=classes[0], BrowserDelegate=classes[1], instances={}))
    monkeypatch.setitem(sys.modules, 'objc', SimpleNamespace(selector=selector, classAddMethods=add))
    monkeypatch.setitem(sys.modules, 'webview.platforms', SimpleNamespace(cocoa=cocoa))
    return cocoa, classes, originals, selector_name


def test_installation_wraps_both_channels_once_and_detects_later_replacement(backend):
    _, classes, originals, name = backend
    install_macos_frame_authority()
    installed = [getattr(cls, name).callable for cls in classes]
    install_macos_frame_authority()
    assert [getattr(cls, name).callable for cls in classes] == installed
    for fn, original in zip(installed, originals, strict=True):
        fn(None, None, message(False))
        original.assert_not_called()
        event = message(True)
        fn(None, None, event)
        original.assert_called_once_with(None, None, event)
    setattr(classes[0], name, SimpleNamespace(callable=originals[0]))
    with pytest.raises(RuntimeError, match='changed after installation'):
        install_macos_frame_authority()


def test_late_installation_is_rejected(backend):
    cocoa, _, _, _ = backend
    cocoa.BrowserView.instances['already-loading'] = object()
    with pytest.raises(RuntimeError, match='precede window creation'):
        install_macos_frame_authority()


def test_unsupported_second_handler_does_not_partially_install(backend):
    _, classes, originals, name = backend
    setattr(classes[1], name, object())
    with pytest.raises(RuntimeError, match='unsupported native bridge'):
        install_macos_frame_authority()
    assert getattr(classes[0], name).callable is originals[0]
