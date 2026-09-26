"""Test-only daemon-side fault injection for served-boundary evidence tests.

Placed FIRST on a scratch daemon's ``PYTHONPATH`` it is imported by ``site`` at interpreter
start. It does nothing unless ``VOOL_DAEMON_FAULT`` names a mode, and even then a fault only
FIRES once the file named by ``VOOL_DAEMON_FAULT_ARMFILE`` exists: daemon boot itself calls
the patched seams (provider registration reads manifests), so the arming file is how a test
lets the daemon come up healthy and then forces one exact request to fail.

Modes (``VOOL_DAEMON_FAULT``):

  manifest_read  -> ``storage.model_provider_manifest.get_provider_manifest`` raises
                    RuntimeError. That read is the certification route's FIRST store touch,
                    BEFORE the route's try/except, so the exception escapes the whole ASGI
                    request path and uvicorn answers its bare-text 500 -- the exact escape
                    family of the 2026-09-26 CI certification failures (run 36259697477,
                    body 'Internal Server Error'). The message deliberately carries a
                    synthetic secret token so the test can prove the captured evidence is
                    redacted before it rides a public pytest artifact.

This module never patches anything when the environment is absent, and it must stay the ONLY
``sitecustomize`` on the path it is given (the fixture-transport shim is one too; combining
them in one PYTHONPATH requires chaining, which no current test does).
"""

from __future__ import annotations

import os
import sys

MODE = os.environ.get("VOOL_DAEMON_FAULT", "")

if MODE:
    sys.stderr.write(f"[daemon-fault] sitecustomize active, mode={MODE!r}\n")
    repo = os.environ.get("CERT500_REPO", ".")
    sys.path.insert(0, repo)

    if MODE == "manifest_read":
        arm_file = os.environ.get("VOOL_DAEMON_FAULT_ARMFILE", "")

        def _armed() -> bool:
            try:
                return bool(arm_file) and os.path.exists(arm_file)
            except OSError:
                return False

        import storage.model_provider_manifest as _manifests

        _real_get = _manifests.get_provider_manifest

        def _raise_when_armed(provider_name, model_name):
            if _armed():
                raise RuntimeError(
                    "injected pre-route manifest read failure "
                    f"(provider={provider_name} model={model_name} "
                    "token=VOOLFAULTSECRET0123456789)"
                )
            return _real_get(provider_name, model_name)

        _manifests.get_provider_manifest = _raise_when_armed
    else:
        sys.stderr.write(f"[daemon-fault] unknown mode {MODE!r}; no patch applied\n")
