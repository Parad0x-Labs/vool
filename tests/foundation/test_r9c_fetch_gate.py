"""R-9c/H-8 negative test: the machine-download lane respects the turn's
remote-fetch veto via the ONE outbound door (`open_remote`).

Standing sabotage guard: re-introducing a raw urlopen into the download lane
fails the source guard.
"""
from __future__ import annotations

import inspect


def test_machine_download_refused_under_remote_fetch_veto(monkeypatch):
    import urllib.request as _ur

    import core.agent_runtime.fast_paths_machine as fpm
    from core.remote_fetch_policy import _REMOTE_FETCH_FORBIDDEN, RemoteFetchRefusedError

    class _Agent:
        def _fast_path_result(self, **kw):
            return kw

    token = _REMOTE_FETCH_FORBIDDEN.set(True)
    try:
        def refused_open(request, *, timeout):
            raise RemoteFetchRefusedError("remote fetch is not permitted for this turn")

        monkeypatch.setattr(_ur, "urlopen", refused_open)
        result = fpm.maybe_handle_direct_machine_download_request(
            _Agent(),
            user_input="please download https://example.com/file.txt into ~/Downloads/notes.txt",
            session_id="s-r9c",
            source_surface="api",
            source_context={"surface": "api"},
        )
    finally:
        _REMOTE_FETCH_FORBIDDEN.reset(token)
    assert result is not None
    assert "couldn't download" in str(result.get("response") or "")


def test_download_lane_source_guard_uses_the_one_door():
    """Standing guard: `maybe_handle_direct_machine_download_request` must
    reach the network ONLY through core.remote_fetch_policy.open_remote."""
    import core.agent_runtime.fast_paths_machine as fpm

    src = inspect.getsource(fpm.maybe_handle_direct_machine_download_request)
    assert "open_remote" in src, "download lane must traverse the one outbound door"
    assert "urllib_request.urlopen(" not in src, (
        "RED MUTATION detected: a raw socket bypass is back in the download lane"
    )
