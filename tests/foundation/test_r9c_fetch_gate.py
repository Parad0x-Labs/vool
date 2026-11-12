"""R-9c/H-8 negative test: the machine-download lane respects the turn's
remote-fetch veto via the ONE outbound door (`open_remote`).

Standing sabotage guard: re-introducing a raw urlopen into the download lane
fails the source guard.
"""
from __future__ import annotations

import inspect


def test_machine_download_refused_under_remote_fetch_veto(monkeypatch, enable_web):
    # Web lookups on, as the person would have them: with the switch off a download is refused at
    # the switch before any network door, and the turn's veto below would never be reached.
    import urllib.request as _ur

    import core.agent_runtime.fast_paths_machine as fpm
    import core.remote_fetch_policy as remote_fetch_policy
    from core.remote_fetch_policy import _REMOTE_FETCH_FORBIDDEN, RemoteFetchRefusedError

    class _Agent:
        def _fast_path_result(self, **kw):
            return kw

    # The download reaches the one door AS a lookup, so it can never pass with lookups off.
    door_calls: list[bool] = []
    real_open_remote = remote_fetch_policy.open_remote

    def _open_remote(request, *args, **kwargs):
        door_calls.append(bool(kwargs.get("lookup")))
        return real_open_remote(request, *args, **kwargs)

    monkeypatch.setattr(remote_fetch_policy, "open_remote", _open_remote)
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
    assert door_calls == [True], door_calls


def test_download_lane_source_guard_uses_the_one_door():
    """Standing guard: `maybe_handle_direct_machine_download_request` must
    reach the network ONLY through core.remote_fetch_policy.open_remote."""
    import core.agent_runtime.fast_paths_machine as fpm

    src = inspect.getsource(fpm.maybe_handle_direct_machine_download_request)
    assert "open_remote" in src, "download lane must traverse the one outbound door"
    assert "urllib_request.urlopen(" not in src, (
        "RED MUTATION detected: a raw socket bypass is back in the download lane"
    )
