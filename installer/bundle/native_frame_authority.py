"""Admit desktop bridge messages at the native frame boundary, before dispatch."""
from __future__ import annotations


def is_main_frame_message(message: object) -> bool:
    """Use WebKit's sender identity, never the caller's JSON or JavaScript realm."""
    try:
        flag = message.frameInfo().isMainFrame()
        return flag is True or (type(flag) is int and flag == 1)
    except Exception:
        return False


def guard_handler(handler):
    def main_frame_only(self, controller, message):
        if is_main_frame_message(message):
            return handler(self, controller, message)
        return None

    return main_frame_only


def install_macos_frame_authority() -> None:
    """Install before any native view can load content; never mutate library files.

    pywebview exposes two WKScriptMessage handlers: the action bridge and printing.
    Retain each Python implementation rather than redispatching a replaced selector.
    This keeps the library's action protocol and return handling unchanged.
    """
    import objc
    from webview.platforms import cocoa

    selector_name = 'userContentController_didReceiveScriptMessage_'
    previous = getattr(cocoa, '_vool_frame_authority', None)
    if previous is not None:
        if all(getattr(cls, selector_name).callable is fn for cls, fn in previous):
            return
        raise RuntimeError('native frame authority changed after installation')
    if cocoa.BrowserView.instances:
        raise RuntimeError('native frame authority must precede window creation')
    replacements = []
    for cls in (cocoa.BrowserView.JSBridge, cocoa.BrowserView.BrowserDelegate):
        original = getattr(cls, selector_name)
        implementation = getattr(original, 'callable', None)
        if not callable(implementation):
            raise RuntimeError('unsupported native bridge implementation')
        guarded = guard_handler(implementation)
        replacement = objc.selector(guarded, selector=original.selector, signature=original.signature)
        replacements.append((cls, guarded, replacement))
    for cls, _, replacement in replacements:
        objc.classAddMethods(cls, [replacement])
    cocoa._vool_frame_authority = tuple((cls, guarded) for cls, guarded, _ in replacements)
    if not all(getattr(cls, selector_name).callable is fn for cls, fn in cocoa._vool_frame_authority):
        raise RuntimeError('native frame authority installation could not be verified')
