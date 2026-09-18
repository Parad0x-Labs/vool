"""Local Only must never make the app unstartable.

Found by driving the packaged macOS bundle: a fresh home boots `remote_only`; enabling the
pact's `local_only_composite` boundary; the next start died with a bare
`RuntimeError: No supported backend found.` and the app could not be started again. Because
the crash takes the API down, it also takes down Settings and the pact -- the only surfaces
from which the setting could be turned back off.

`policy_engine.allow_remote_only_without_backend()` is
``get(...) and not local_only_mode()``, so Local Only withdrawing the remote-only fallback is
deliberate and stays. What must not happen is the daemon refusing to BOOT because of it.
"""
from __future__ import annotations

import pytest

from core import policy_engine
from core.runtime_bootstrap import resolve_backend_selection


class _NoBackend:
    """A machine with nothing healthy: every selection fails its healthcheck."""

    def detect_hardware(self):
        return {"device": "cpu"}

    def select_backend(self, hardware):
        class _Sel:
            backend_name = "TorchCPUBackend"
            device = "cpu"
            reason = "no_supported_backend"

        return _Sel()

    def healthcheck(self, selection):
        return False


def test_local_only_with_no_backend_still_boots(monkeypatch):
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    selection = resolve_backend_selection(manager=_NoBackend(), allow_remote_only=False)
    assert selection.backend_name == "local_only_no_backend"
    # The reason must tell the operator how to get out, not just that something is missing.
    assert "Local Only" in selection.reason
    assert "Settings" in selection.reason or "local model" in selection.reason


def test_no_backend_without_local_only_still_refuses(monkeypatch):
    """The pre-existing fail-closed stays fail-closed. This widens ONLY the Local Only case."""
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    with pytest.raises(RuntimeError, match="No supported backend found"):
        resolve_backend_selection(manager=_NoBackend(), allow_remote_only=False)


def test_a_healthy_backend_is_unaffected(monkeypatch):
    class _Healthy(_NoBackend):
        def healthcheck(self, selection):
            return True

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    selection = resolve_backend_selection(manager=_Healthy(), allow_remote_only=False)
    assert selection.backend_name == "TorchCPUBackend"


def test_remote_only_is_still_chosen_when_it_is_allowed(monkeypatch):
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    selection = resolve_backend_selection(manager=_NoBackend(), allow_remote_only=True)
    assert selection.backend_name == "remote_only"


@pytest.mark.parametrize("profile", ["local-only", "local-max", "goblin-stack"])
def test_installation_local_only_profile_boots_without_a_backend(monkeypatch, tmp_path, profile):
    from core import runtime_bootstrap
    from core.runtime_context import build_runtime_context

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    context = build_runtime_context(mode="api_server", env={
        "VOOL_HOME": str(tmp_path / profile), "VOOL_INSTALL_PROFILE": profile,
    })
    assert context.feature_flags.local_only_mode
    assert not context.feature_flags.allow_remote_only_without_backend
    monkeypatch.setattr(runtime_bootstrap, "build_runtime_context", lambda **kwargs: context)
    monkeypatch.setattr(runtime_bootstrap, "bootstrap_runtime_environment", lambda **kwargs: context)
    result = runtime_bootstrap.bootstrap_runtime_mode(
        mode="api_server", resolve_backend=True, manager=_NoBackend(),
    )
    assert result.backend_selection.backend_name == "local_only_no_backend"
    assert not result.context.feature_flags.allow_remote_only_without_backend
