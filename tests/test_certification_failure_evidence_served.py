"""Served: a certification 500 must arrive self-describing, and its evidence must be safe.

The 2026-09-26 CI events (run 36259697477, job 108453319659; earlier job 108425959066)
failed a fresh daemon's early certification with HTTP 500 and BARE body
``Internal Server Error`` -- uvicorn's own error text, which means the exception escaped
the certification route's handler entirely (the route's except-Exception answers JSON).
Its traceback existed only in the ephemeral home's ``data/logs/vool_api.log``, which dies
with the test, so the failure stayed nameless.

Two repairs, proven here together against a controlled escape rather than the next field
event. The route now reads the model manifest inside its own redacted-500 contract (the
read used to sit before the try, which is what made the escape possible), and the reader
rig captures a redacted daemon-log tail on any failed certification. A fault-injected
daemon raises from exactly that first store read -- the arm file keeps boot itself
unpatched -- and this file asserts:

1. the door answers its JSON 500 (NOT the bare text), carrying the exception class in a
   server-side-redacted body;
2. the failure artifact names the raising frame from the daemon's own log tail;
3. neither the synthetic secret planted in the exception message nor this machine's home
   paths ride the public artifact;
4. the fault is surgical: disarmed, the same daemon certifies successfully, and a
   successful certification carries no capture fields.

A certification failure stays a failure; capture and the redacted 500 only name it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tests._reader_served_rig as rig

FAULT_DIR = Path(__file__).resolve().parent / "daemon_fault_inject"


@pytest.mark.timeout(600)
def test_a_pre_route_500_names_its_exception_in_the_failure_artifact_without_leaking(tmp_path):
    arm_file = tmp_path / "fault-armed"
    env_extra = {
        "PYTHONPATH": os.pathsep.join([str(FAULT_DIR), str(rig.REPO_ROOT), str(rig.READER_DEPS)]),
        "VOOL_DAEMON_FAULT": "manifest_read",
        "VOOL_DAEMON_FAULT_ARMFILE": str(arm_file),
        # The rig's own registration subprocess shares this environment; it writes the
        # manifest through a different seam (upsert), and the arm file does not exist yet,
        # so provider registration and daemon boot run entirely unpatched.
    }
    provider = rig.CapturingProvider(default="Acknowledged.")
    provider.__enter__()
    daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra=env_extra)
    try:
        daemon.register_provider()
        daemon.start()  # healthy boot with the fault NOT armed

        arm_file.write_text("armed\n", encoding="utf-8")
        cert = daemon.certify()

        # The repaired door: its own JSON 500, not uvicorn's bare text. The class name of
        # the injected escape rides the (server-side redacted) body.
        assert cert.get("state") == "error", cert
        assert cert.get("http_status") == 500, cert
        body = str(cert.get("body") or "")
        assert body != "Internal Server Error", cert
        body_json = json.loads(body)
        assert "RuntimeError" in str(body_json.get("error") or ""), cert

        # The capture names the exception class AND the raising frame, from the daemon's
        # own log tail -- the only place the traceback exists.
        tail = str(cert.get("server_log_tail") or "")
        assert "RuntimeError" in tail, cert
        assert "get_provider_manifest" in tail, cert

        # And it must not publish what the daemon's private log may legitimately contain:
        # the synthetic secret planted in the injected exception message, and this
        # machine's home/tmp paths. Redaction replacing the token is itself asserted.
        assert "VOOLFAULTSECRET0123456789" not in tail, cert
        assert "token=[redacted]" in tail, cert
        assert str(tmp_path) not in tail, cert
        assert "VOOLFAULTSECRET0123456789" not in body, cert

        # The fault is surgical: disarmed, the SAME daemon certifies successfully. A
        # successful certification's shape is unchanged (no capture fields).
        arm_file.unlink()
        clean = daemon.certify()
        assert clean.get("state") == "verified", clean
        assert "server_log_tail" not in clean, clean
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
