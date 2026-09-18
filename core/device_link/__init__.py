"""Device Link — the desktop side of the phone<->desktop capability-grant
protocol (NIA-023, landed 2026-08-30 from the preserved farm worktrees).

Lineage: the CONVERGED pass (round 2, swarm-r2-tether-converge, 2026-08-26 —
the newest of the three preserved passes) is the source; `protocol.py` is its
`tether/protocol.py` verbatim except for the identity import and de-tethered
names ("tether" already names a model provider in core/runtime_provider_defaults.py).

OPERATOR GATE — what is and is NOT here:
  IN: the grant protocol (Ed25519 authority, HMAC-SHA256 request surface,
      envelope ladder, replay shields, scope containment, revocation), the
      identity convention, and the operator CLI over a file-backed grant book.
  NOT IN (deliberately, awaiting operator): the bridge SERVER, TLS, beacons,
      QR pairing, phone/iOS companions, and every verb HANDLER — nothing in
      this package opens a socket, spawns a process, or touches the served
      path. Read-only registered modules: importable, tested, unwired.
"""
from core.device_link.identity import (
    fingerprint,
    generate,
    load,
    load_or_create,
    public_hex,
    save,
    short_fingerprint,
)
from core.device_link.protocol import (
    CONSENT_GATED_VERBS,
    ENVELOPE_DEV_MACHINE,
    ENVELOPE_FILES,
    ENVELOPE_FILES_TERMINAL,
    ENVELOPE_FULL_REMOTE_CONTROL,
    ENVELOPE_ORDER,
    ENVELOPE_VIEW_ONLY,
    ENVELOPE_VERBS,
    GrantError,
    GrantRegistry,
    SCOPE_ONE_ACTION,
    SCOPE_ONE_PROJECT,
    SCOPE_ONE_SESSION,
    SCOPE_ONE_PATHS,
    SCOPE_UNTIL_REVOKED,
    VERB_CATALOG,
    check_scope_paths,
    envelope_for_verb,
    issue_grant,
    sign_request,
    verify_grant_self_signature,
    verbs_for_envelope,
)

__all__ = [
    "CONSENT_GATED_VERBS",
    "ENVELOPE_DEV_MACHINE",
    "ENVELOPE_FILES",
    "ENVELOPE_FILES_TERMINAL",
    "ENVELOPE_FULL_REMOTE_CONTROL",
    "ENVELOPE_ORDER",
    "ENVELOPE_VIEW_ONLY",
    "ENVELOPE_VERBS",
    "GrantError",
    "GrantRegistry",
    "SCOPE_ONE_ACTION",
    "SCOPE_ONE_PATHS",
    "SCOPE_ONE_PROJECT",
    "SCOPE_ONE_SESSION",
    "SCOPE_UNTIL_REVOKED",
    "VERB_CATALOG",
    "check_scope_paths",
    "envelope_for_verb",
    "fingerprint",
    "generate",
    "issue_grant",
    "load",
    "load_or_create",
    "public_hex",
    "save",
    "short_fingerprint",
    "sign_request",
    "verify_grant_self_signature",
    "verbs_for_envelope",
]
