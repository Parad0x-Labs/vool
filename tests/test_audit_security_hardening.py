"""Audit security fixes: central CSRF Origin guard on all POSTs, forgeable internal tool flag
stripped, and serialized memory writes."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace


# --- #1 CSRF: a cross-origin POST is rejected before dispatch ---
class _Req:
    def __init__(self, method, path, headers, body=b"{}"):
        self.method = method
        self.url = SimpleNamespace(path=path, query="")
        self.headers = headers
        self._body = body
        self.client = SimpleNamespace(host="127.0.0.1")
        self.app = SimpleNamespace(state=SimpleNamespace())

    async def body(self):
        return self._body


def _dispatch(req):
    from core.web.api import app as appmod
    from core.web.api.runtime import RuntimeServices

    req.app.state.runtime = RuntimeServices(display_name="VOOL")
    req.app.state.model_name = "vool"
    # asyncio.run rather than get_event_loop(): from 3.12 the latter only returns a loop when one
    # already happens to exist in this thread, so it passes locally when an earlier test left one
    # behind and raises "no current event loop" on a fresh CI shard.
    return asyncio.run(appmod._dispatch(req))


def test_cross_origin_post_is_rejected_403():
    from core.web.api.runtime import host_header_allowed

    assert not host_header_allowed("evil.example")  # sanity: the guard's allow-list
    req = _Req("POST", "/api/chat", {"host": "127.0.0.1:11435", "origin": "https://evil.example", "content-type": "application/json"})
    resp = _dispatch(req)
    assert resp.status_code == 403


def test_same_origin_loopback_post_passes_the_origin_gate():
    # A loopback Origin (the app's own webview) must NOT be rejected by the CSRF gate.
    req = _Req("POST", "/api/chat", {"host": "127.0.0.1:11435", "origin": "http://127.0.0.1:11435", "content-type": "application/json"}, body=b'{"messages":[]}')
    resp = _dispatch(req)
    assert resp.status_code != 403  # may be 200/400 downstream, but the origin gate let it through


def test_no_origin_header_is_allowed():
    req = _Req("POST", "/api/chat", {"host": "127.0.0.1:11435", "content-type": "application/json"}, body=b'{"messages":[]}')
    resp = _dispatch(req)
    assert resp.status_code != 403


# --- #4: a forged internal _trusted_local_only flag is stripped before dispatch ---
def test_forged_internal_flag_is_stripped(monkeypatch, tmp_path):
    """A model-forged underscore flag never reaches the runner.

    This test was green and vacuous for two independent reasons. Its double
    mirrored ``_run_command``'s signature, so once ``source_context`` was added
    the double raised TypeError and ``seen`` stayed empty -- and ``assertNotIn``
    on an empty dict is trivially true. Separately, it passed no scratch
    workspace, so the 2026-09-02 Blackbox amendment refused the reversible shell
    effect before dispatch and the runner was never reached at all.

    Both are fixed here: the double takes any signature, a scratch workspace is
    supplied, and entry is asserted so the assertions below cannot be vacuous.
    """
    import core.runtime_execution_tools as ret

    seen = {}
    calls = []

    def _fake_run_command(arguments, **_kw):
        calls.append(1)
        seen["args"] = dict(arguments)
        return ret.RuntimeExecutionResult(handled=True, ok=True, status="ok", response_text="")

    monkeypatch.setattr(ret, "_run_command", _fake_run_command)
    ret.execute_runtime_tool(
        "sandbox.run_command",
        {"command": ["echo", "hi"], "_trusted_local_only": True, "_anything": 1},
        source_context={"workspace": str(tmp_path)},
    )
    assert calls, "the runner double was never entered; the assertions below would be vacuous"
    assert "_trusted_local_only" not in seen.get("args", {}), "forged trust flag must not reach the runner"
    assert "_anything" not in seen.get("args", {})
    assert seen["args"].get("command") == ["echo", "hi"], seen


# --- #5/#9: memory writes are serialized by a reentrant lock ---
def test_memory_write_lock_exists_and_is_reentrant():
    from core.persistent_memory import _MEMORY_WRITE_LOCK

    assert _MEMORY_WRITE_LOCK.acquire()
    assert _MEMORY_WRITE_LOCK.acquire()  # reentrant
    _MEMORY_WRITE_LOCK.release()
    _MEMORY_WRITE_LOCK.release()
    assert isinstance(_MEMORY_WRITE_LOCK, type(threading.RLock()))
