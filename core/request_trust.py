"""
core/request_trust.py
=====================
Server-side "owner-local" trust signal for privileged runtime actions.

The runtime's ``/api/chat`` HTTP endpoint is unauthenticated and binds loopback by
default. The only place a caller can forge a trust label is the HTTP request body:
channels deliver a remote user's plain text (the adapter builds ``source_context`` in
trusted server code), and the CLI runs in-process, so neither crosses a forgery boundary.
An HTTP body, by contrast, can claim any ``surface`` ("openclaw", "cli", ...) or set any
field it likes.

So privileged, spend- or shutdown-touching gates must NOT trust the caller-supplied
``surface``. Instead the API dispatcher stamps a server-controlled ``_owner_local`` flag on
``source_context`` from the real TCP peer address (loopback == the machine owner's own
session) after stripping any caller-supplied copy, and every privileged gate consults THIS
flag via :func:`request_is_owner_local`.

Honest limit: on the default loopback bind, *any* local process is treated as the owner —
that is the accepted trust boundary for a local-first, single-user runtime, the same
boundary any local daemon draws. What this closes is the *remote* forgery path: a caller on
a non-loopback peer (an exposed ``--bind 0.0.0.0`` API, or a channel message) can no longer
present itself as owner-local by setting a body field.
"""
from __future__ import annotations

from typing import Any

# Server-stamped key: True only when the request provably came from the owner's local
# session. Callers must never set it over the wire — the dispatcher strips and re-stamps it.
OWNER_LOCAL_KEY = "_owner_local"

# source_context keys a caller must never supply; the server owns them. Stripped from every
# inbound body before the trusted values are stamped. "authorized_paid_call" is a server-built paid
# reservation (see ModelOrchestrator.authorize_paid) and participates in the spend decision, so a
# caller-supplied one is stripped here rather than relying on downstream structural checks to reject it.
RESERVED_TRUST_KEYS = (
    OWNER_LOCAL_KEY,
    # M2: the typed turn contract is SERVER-authored at the run_once intake. An inbound
    # body carrying this key is stripped here; only core.turn_contract writes it.
    "turn_request",
    "turn_state",
    "turn_proposals",
    # The turn's RequestGraph projection is written by the turn door (core.semantic.turn_graph);
    # a caller-supplied one would let a client label its own slot set onto the receipt.
    "request_graph",
    "_semantic_shadow",
    # The root-cause diagnosis contract is core-authored (planner/finalizer
    # seams in core.root_cause_contract); a caller-supplied one would let a
    # client forge "Root cause repaired" status onto any turn.
    "root_cause_contract",
    "cloud_escalation_approved",
    "autopilot_allow_heavy_model",
    "authorized_paid_call",
    # The operator's Authorize Push is SERVER-stamped by the owner-local operator surface.
    # A request body carrying it would let a model author its own consent to move a remote ref.
    "repo_push_authorization",
    # The operator's forge-action authorization (draft PR create/update, comment) is minted the
    # same way, by the same owner-local surface. Reserved for the identical reason: a turn must
    # not be able to write its own consent to put content on a forge.
    "repo_forge_action_authorization",
    # Server-authored, redacted receipts for the bounded paid planner/conductor scope.  They do
    # not grant authority, but accepting caller-written rows would make the returned trace lie.
    "pinned_paid_helper_receipts",
    # Joined into model.call_* evidence by the provider boundary.  A caller must not be able to
    # label an ordinary answer as a bounded conductor generation.
    "model_call_role",
    "access_policy",
    "context_access_policy",
    "_context_access_policy",
    "context_grants",
    "grant_confirmed_profile",
    "trusted_private_local",
    "allow_project_context",
    "allow_user_profile_context",
    "allow_action_receipts",
    "allow_shared_context",
    "allow_cross_chat",
    "allow_archived_context",
    "allow_cold_context",
    "chat_archived",
    "chat_id",
    "project_id",
    "_trusted_project_id",
    # The concrete further calls one model reply asked for. The tool loop writes it from the
    # provider's own batch and the permission controller turns it into a bounded batch approval, so
    # a caller-supplied one would be a caller choosing what a single click authorizes.
    "pending_batch_calls",
    # A9 P0: the turn's routing plan is minted server-side BEFORE model spend (core.turn_routing)
    # and enforced at the broker seam. A caller-supplied one would be a client choosing its own
    # provider fences — exactly what the paid/locality ceilings exist to prevent.
    "turn_routing_plan",
    "turn_routing_retry",
    # VOOL School: the server-stamped school principal + resolved policy for a
    # school session. Stamped ONLY by the /api/chat ingress gate after the
    # signed session token verifies; a caller-supplied copy is stripped here.
    "school_policy",
)

# In-process surfaces that identify the machine owner's own session. Only consulted as a
# fallback for callers that never crossed the HTTP boundary (the dispatcher always stamps
# ``_owner_local`` for HTTP requests, so this fallback is reached only in-process, where the
# surface is set by trusted local code and cannot be forged).
_OWNER_SURFACES = frozenset({"cli", "desktop", "local", ""})

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def is_loopback_host(host: str) -> bool:
    """True when ``host`` is a loopback address (the machine owner's own session).

    Empty/unknown -> False (fail closed): a request whose peer we cannot confirm as loopback
    is not treated as owner-local.
    """
    h = str(host or "").strip().lower()
    if not h:
        return False
    if h in _LOOPBACK_HOSTS:
        return True
    # 127.0.0.0/8 is entirely loopback.
    return h.startswith("127.")


def strip_reserved_trust_keys(source_context: Any) -> dict[str, Any]:
    """Return a copy of ``source_context`` with every caller-forgeable trust key removed."""
    sc = dict(source_context) if isinstance(source_context, dict) else {}
    for key in RESERVED_TRUST_KEYS:
        sc.pop(key, None)
    return sc


def request_is_owner_local(source_context: Any) -> bool:
    """Whether this request is from the machine owner's own local session.

    Prefers the server-stamped ``_owner_local`` (authoritative for HTTP requests). When that
    key is absent the request never crossed the HTTP forgery boundary, so the in-process
    ``surface`` is trustworthy and used as the fallback.
    """
    sc = source_context if isinstance(source_context, dict) else {}
    if OWNER_LOCAL_KEY in sc:
        return bool(sc.get(OWNER_LOCAL_KEY))
    surface = str(sc.get("surface") or "").strip().lower()
    return surface in _OWNER_SURFACES


__all__ = [
    "OWNER_LOCAL_KEY",
    "RESERVED_TRUST_KEYS",
    "is_loopback_host",
    "request_is_owner_local",
    "strip_reserved_trust_keys",
]
