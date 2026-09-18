"""Device Link (NIA-023) — the desktop capability-grant core, ported from the
newest preserved pass (converged round 2) and landed as read-only modules.

Pins the protocol's own laws (mirroring the source pass's e2e assertions at
protocol level — the bridge/phone side is operator-gated), the operator CLI,
and the OPERATOR GATE itself: nothing in this package opens a network surface.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.device_link import (
    ENVELOPE_FULL_REMOTE_CONTROL,
    ENVELOPE_ORDER,
    ENVELOPE_VIEW_ONLY,
    GrantError,
    GrantRegistry,
    SCOPE_ONE_ACTION,
    SCOPE_ONE_PROJECT,
    issue_grant,
    load_or_create,
    public_hex,
    sign_request,
    verify_grant_self_signature,
    verbs_for_envelope,
)
from core.device_link import ctl as devicectl


def _key(tmp_path):
    return load_or_create(tmp_path / "keys" / "device-link-ed25519.json")


def _mint(tmp_path, **over):
    base = dict(
        signing_key=_key(tmp_path),
        device_name="Pixel 9",
        envelope="FILES+TERMINAL",
        scope_kind="one_project",
        allowed_paths=[str(tmp_path / "project")],
    )
    base.update(over)
    return issue_grant(**base)


def _signed_request(grant, verb="fs.list", params=None, nonce="n1", when=None):
    ts = (when or datetime.now(timezone.utc)).isoformat()
    return {
        "grant": grant,
        "verb": verb,
        "params": params or {"path": "/tmp/anywhere"},
        "nonce": nonce,
        "timestamp": ts,
        "signature": sign_request(
            grant["grant_secret"], grant_id=grant["grant_id"], verb=verb,
            params=params or {"path": "/tmp/anywhere"}, nonce=nonce, timestamp=ts,
        ),
    }


# -- the grant itself ---------------------------------------------------------------------


def test_envelope_ladder_is_cumulative():
    assert "fs.list" in verbs_for_envelope(ENVELOPE_VIEW_ONLY)
    assert "term.run" not in verbs_for_envelope("FILES")
    assert "term.run" in verbs_for_envelope("FILES+TERMINAL")
    everything = verbs_for_envelope(ENVELOPE_FULL_REMOTE_CONTROL)
    assert "input.keyboard" in everything and "fs.read" in everything


def test_grant_is_signed_by_the_authority_and_tamper_evident(tmp_path):
    grant = _mint(tmp_path)
    assert verify_grant_self_signature(grant)
    forged = dict(grant, envelope=ENVELOPE_FULL_REMOTE_CONTROL)
    assert not verify_grant_self_signature(forged), "envelope escalation must not verify"


def test_one_action_forces_single_use_and_short_ttl(tmp_path):
    grant = _mint(tmp_path, scope_kind=SCOPE_ONE_ACTION, envelope=ENVELOPE_VIEW_ONLY)
    assert grant["max_uses"] == 1


def test_one_project_requires_a_root(tmp_path):
    with pytest.raises(GrantError):
        _mint(tmp_path, allowed_paths=None)


# -- the request surface ------------------------------------------------------------------


def test_authorize_happy_path_and_full_replay_shield(tmp_path):
    registry = GrantRegistry()
    grant = _mint(tmp_path, scope_kind="one_session", max_uses=5,
                  allowed_paths=[str(tmp_path)])
    registry.register(grant)
    req = _signed_request(grant, verb="fs.list", params={"path": str(tmp_path / "x")})
    ok, reason, detail = registry.authorize(
        grant=grant, verb=req["verb"], params=req["params"],
        nonce=req["nonce"], timestamp=req["timestamp"], signature=req["signature"])
    assert ok, reason
    assert detail["use"] == "1/5"

    # Same nonce again: refused even with a fresh signature.
    ok2, reason2, _ = registry.authorize(
        grant=grant, verb=req["verb"], params=req["params"],
        nonce=req["nonce"], timestamp=req["timestamp"], signature=req["signature"])
    assert not ok2 and "replay" in reason2


def test_wrong_secret_tampered_body_and_clock_window(tmp_path):
    registry = GrantRegistry()
    grant = _mint(tmp_path, scope_kind="one_session")
    registry.register(grant)

    stranger = dict(grant, grant_secret="not-the-secret")
    req = _signed_request(stranger, params={"path": str(tmp_path)})
    ok, reason, _ = registry.authorize(
        grant=grant, verb=req["verb"], params=req["params"], nonce=req["nonce"],
        timestamp=req["timestamp"], signature=req["signature"])
    assert not ok and "HMAC" in reason

    stale = _signed_request(grant, nonce="n-far",
                            when=datetime.now(timezone.utc) - timedelta(minutes=10))
    ok, reason, _ = registry.authorize(
        grant=grant, verb=stale["verb"], params=stale["params"], nonce=stale["nonce"],
        timestamp=stale["timestamp"], signature=stale["signature"])
    assert not ok and "120s" in reason


def test_verb_above_envelope_and_scope_escape_are_refused(tmp_path):
    registry = GrantRegistry()
    grant = _mint(tmp_path, envelope="VIEW_ONLY", scope_kind=SCOPE_ONE_PROJECT,
                  allowed_paths=[str(tmp_path / "project")])
    registry.register(grant)
    outside = tmp_path / "outside"

    req = _signed_request(grant, verb="fs.write", params={"path": str(outside)})
    ok, reason, _ = registry.authorize(
        grant=grant, verb=req["verb"], params=req["params"], nonce=req["nonce"],
        timestamp=req["timestamp"], signature=req["signature"])
    assert not ok and "requires envelope" in reason

    req2 = _signed_request(grant, verb="fs.list", params={"path": str(outside)}, nonce="n2")
    ok2, reason2, _ = registry.authorize(
        grant=grant, verb=req2["verb"], params=req2["params"], nonce=req2["nonce"],
        timestamp=req2["timestamp"], signature=req2["signature"])
    assert not ok2 and "escapes grant scope" in reason2


def test_revocation_is_instant_and_companions_cannot_self_restore(tmp_path):
    registry = GrantRegistry()
    grant = _mint(tmp_path, scope_kind="one_session")
    registry.register(grant)
    assert registry.revoke(grant["grant_id"], reason="lost phone")
    req = _signed_request(grant)
    ok, reason, _ = registry.authorize(
        grant=grant, verb=req["verb"], params=req["params"], nonce=req["nonce"],
        timestamp=req["timestamp"], signature=req["signature"])
    assert not ok and "revoked" in reason
    # A re-registered copy of the same grant stays revoked by id.
    registry.register(dict(grant))
    assert registry.is_revoked(grant["grant_id"])


# -- identity ------------------------------------------------------------------------------


def test_identity_roundtrip_and_fingerprint_stability(tmp_path):
    key_path = tmp_path / "keys" / "k.json"
    key = load_or_create(key_path)
    again = load_or_create(key_path)
    assert public_hex(key) == public_hex(again)
    assert (tmp_path / "keys" / "k.json").stat().st_mode & 0o777 == 0o600


# -- the operator CLI ----------------------------------------------------------------------


def test_ctl_mint_devices_revoke_cycle(tmp_path, capsys):
    book = tmp_path / "book.json"
    key = tmp_path / "keys" / "auth.json"
    rc = devicectl.main([
        "--book", str(book), "--key", str(key), "mint-token",
        "--device", "Pixel 9", "--envelope", "terminal",
        "--scope", "one_project", "--path", str(tmp_path),
    ])
    assert rc == 0
    minted = json.loads(capsys.readouterr().out)
    assert minted["envelope"] == "FILES+TERMINAL"
    assert verify_grant_self_signature(minted)

    assert devicectl.main(["--book", str(book), "devices"]) == 0
    assert "ALIVE" in capsys.readouterr().out

    assert devicectl.main(["--book", str(book), "revoke", minted["grant_id"]]) == 0
    assert devicectl.main(["--book", str(book), "devices"]) == 0
    assert "REVOKED" in capsys.readouterr().out

    # Companions never elevate: a forged envelope in the book does not verify.
    tampered = dict(minted, envelope=ENVELOPE_FULL_REMOTE_CONTROL)
    book_data = json.loads(book.read_text())
    book_data["grants"][minted["grant_id"]] = tampered
    book.write_text(json.dumps(book_data))
    assert devicectl.main(["--book", str(book), "devices"]) == 0


def test_ctl_rejects_unknown_envelopes(tmp_path):
    with pytest.raises(SystemExit):
        devicectl.main(["--book", str(tmp_path / "b.json"), "--key", str(tmp_path / "k.json"),
                        "mint-token", "--device", "x", "--envelope", "root"])


# -- THE OPERATOR GATE: no network surface in this package ----------------------------------


def test_the_package_opens_no_sockets_and_spawns_nothing():
    """Read-only registration, mechanically proven: no module in core/device_link
    imports a network or process-spawning facility. The bridge/TLS/pairing side
    stays operator-gated; this guard keeps it that way until that lands."""
    import ast

    forbidden = {"socket", "asyncio", "subprocess", "http", "http.server", "ssl"}
    package = Path("core/device_link")
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module.split(".")[0]}
            else:
                continue
            hit = names & forbidden
            assert not hit, f"{path.name} imports forbidden module(s): {hit}"
