from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


class _FetchLedger:
    """One turn's remote-call tally, owned by reference so worker threads can reach it.

    An ``int`` in a ContextVar cannot be reported into from a worker: a pool thread that inherits
    the turn's context and calls ``.set()`` rebinds only its OWN copy, and the increment dies with
    the task. That is not theoretical -- `run_live_data_plan` fetches inside a ThreadPoolExecutor,
    so a Local Only turn quoted live wttr.in data while its trace reported ``web_calls: 0``
    (reproduced 2026-08-13 with two never-queried cities returning distinct conditions).

    Holding a mutable object instead means every context that inherits the binding -- parent and
    workers alike -- mutates the SAME tally. The object is created per scope, so two concurrent
    turns own two ledgers and can never see each other's calls.
    """

    #: A turn cannot make more remote calls than this and still be a turn; the cap exists so a
    #: runaway retry loop cannot grow the ledger without bound. The COUNT keeps rising past it --
    #: only the per-call detail stops being kept, and `truncated()` says so rather than letting a
    #: short list read as the whole story.
    _MAX_ENTRIES = 256

    __slots__ = ("_count", "_entries", "_lock", "_truncated")

    def __init__(self) -> None:
        self._count = 0
        self._entries: list[dict[str, Any]] = []
        self._truncated = False
        self._lock = threading.Lock()

    def note(
        self,
        url: str = "",
        *,
        host: str = "",
        status: int | None = None,
        duration_ms: float | None = None,
    ) -> None:
        with self._lock:
            self._count += 1
            if len(self._entries) >= self._MAX_ENTRIES:
                self._truncated = True
                return
            # The address is evidence, and a credential can live IN the address (UsePod's token is a
            # path segment). The turn ledger keeps where a call went, never the key it carried.
            from core.secret_redaction import redact_secrets

            self._entries.append(
                {
                    "url": redact_secrets(str(url or "")),
                    "host": str(host or "") or _host_of(url),
                    "status": int(status) if isinstance(status, int) else None,
                    "duration_ms": round(float(duration_ms), 1) if duration_ms is not None else None,
                }
            )

    def count(self) -> int:
        with self._lock:
            return self._count

    def entries(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(item) for item in self._entries)

    def truncated(self) -> bool:
        with self._lock:
            return self._truncated


_REMOTE_FETCH_FORBIDDEN: ContextVar[bool] = ContextVar("vool_remote_fetch_forbidden", default=False)
_REMOTE_FETCH_ATTEMPTS: ContextVar[_FetchLedger | None] = ContextVar(
    "vool_remote_fetch_attempts", default=None
)
# Whether the per-turn counter above is currently being kept. Outside the scope the counter reads 0
# because nothing has incremented it, which is indistinguishable from "the turn made no remote
# call" — and a truth binder that cannot tell those apart would turn "I never opened the counter"
# into a proof that no network call happened. Only inside the scope is 0 a fact.
_REMOTE_FETCH_SCOPE_ACTIVE: ContextVar[bool] = ContextVar("vool_remote_fetch_scope_active", default=False)

_TRUSTED_LIVE_SURFACES = frozenset({"channel", "openclaw", "api"})
_TRUSTED_LIVE_PLATFORMS = frozenset(
    {"openclaw", "web_companion", "telegram", "discord"}
)


def explicit_remote_fetch_disabled(source_context: dict[str, Any] | None) -> bool:
    context = dict(source_context or {})
    return "allow_remote_fetch" in context and not bool(context.get("allow_remote_fetch"))


def remote_fetch_allowed_by_context(source_context: dict[str, Any] | None) -> bool:
    """Resolve the caller-side remote-fetch boundary for one turn.

    The web API deliberately omits ``allow_remote_fetch``: absence means "use the
    trusted live-surface default", while an explicit false is an operator/privacy
    veto.  Fresh-data lanes used to require literal ``True`` and were therefore
    unreachable from the shipped app even though the established research lane
    could browse from the same context.

    This helper resolves caller authority only.  Callers must still consult the
    runtime web policy before performing network I/O.

    P0 POLICY CONSERVATION: the turn's own frozen constraints are part of the
    caller-side boundary.  A canonical request carrying a web/tools freeze
    (minted from the WHOLE user text at ingress and inherited by every child
    sub-turn) reads as not-allowed here, whatever lane asks — a child slice
    cannot re-derive policy from its own clean text and fetch what the parent
    forbade.  This only ever NARROWS: a request that froze nothing (the common
    case, and every parent turn whose own text already carries the veto) leaves
    the boundary exactly as it was.
    """

    context = dict(source_context or {})
    if "allow_remote_fetch" in context:
        return bool(context.get("allow_remote_fetch"))
    from core.turn_prohibitions import (
        FAMILY_TOOLS,
        FAMILY_WEB,
        conserved_request_prohibitions,
    )

    prohibitions = conserved_request_prohibitions(context)
    if prohibitions.prohibits_family(FAMILY_WEB) or prohibitions.prohibits_family(
        FAMILY_TOOLS
    ):
        return False
    surface = str(context.get("surface") or "").strip().casefold()
    platform = str(context.get("platform") or "").strip().casefold()
    return surface in _TRUSTED_LIVE_SURFACES or platform in _TRUSTED_LIVE_PLATFORMS


#: M5: the owning turn's source_context, set by the scope so the door's
#: gateway consult sees the SAME turn the fetch belongs to (worker threads
#: inherit it via copy_context, like the ledger).
_SCOPE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "remote_fetch_scope_context", default=None
)


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _active_mode_label() -> str:
    """The mode this TURN was frozen with — not whatever the mode is right now.

    This read the live mode state through a call that took a required argument
    and was given none, so it raised on every call and every receipt in the
    product recorded an empty mode. It now reads the turn's one frozen policy,
    which is both correct and stable for the length of the turn.
    """
    try:
        from core.effect_gateway import current_turn_policy

        policy = current_turn_policy()
        return str(getattr(policy, "mode", "") or "")
    except Exception:
        return ""


#: The one sentence the product says when Local Only refuses to leave the machine.
#: Named once so the served UI, the CLI and the receipts cannot drift from each other.
LOCAL_ONLY_EGRESS_DENIAL = (
    "Local Only is in force for this turn: no request may leave this machine, so no "
    "socket was opened. Loopback and operator-owned LAN endpoints stay available. "
    "To use the web, leave Local Only by changing the model selection."
)


def local_only_active(source_context: dict[str, Any] | None = None) -> bool:
    """Whether Local Only binds THIS turn — by operator selection or by profile.

    Two independent sources, either sufficient:

    * the operator picked the Local Only Auto lane for this turn
      (``core.auto_local_only_mode.turn_is_local_only``), or
    * the machine is configured local-only
      (``core.policy_engine.local_only_mode``), which is how a local-only install
      or profile binds turns that never named a lane.
    """
    context = dict(source_context if source_context is not None else (_SCOPE_CONTEXT.get() or {}))
    with contextlib.suppress(Exception):
        from core.auto_local_only_mode import turn_is_local_only

        if turn_is_local_only(context):
            return True
    try:
        from core.policy_engine import local_only_mode

        return bool(local_only_mode())
    except Exception:
        return False


def _local_only_egress_forbidden(
    source_context: dict[str, Any] | None, url: str
) -> tuple[bool, str]:
    """Is this URL public egress under an active Local Only?

    Local Only means ZERO public-network egress — not "no cloud models". The
    product has always said so: the composer entry reads "this machine only ·
    cloud blocked". The policy did not, because `allow_web_fallback` stayed true
    under `local_only_mode`, so search, direct fetch, browser navigation to public
    origins, fallback scrapers and attachment URL retrieval all remained reachable
    in the one mode whose whole promise is that they are not.

    Bound HERE because this is the single outbound door every one of those lanes
    already converges on; a door added later cannot forget to ask.

    "Local" is decided by the EXISTING endpoint-classification law in
    ``core.auto_local_only_mode`` (loopback names, loopback addresses, private
    ranges, CGNAT). This adds no second opinion about what local means, so
    loopback and operator-owned LAN model endpoints keep working exactly as before.
    """
    if not local_only_active(source_context):
        return False, ""
    try:
        from core.auto_local_only_mode import endpoint_is_local

        if endpoint_is_local(url):
            return False, ""
    except Exception:
        # A classifier that cannot answer has not said "local". Fail CLOSED: in
        # Local Only the safe default is refusing to leave the machine.
        return True, LOCAL_ONLY_EGRESS_DENIAL
    return True, LOCAL_ONLY_EGRESS_DENIAL


def effective_web_available(source_context: dict[str, Any] | None = None) -> bool:
    """Can THIS turn actually reach the web?

    The capability surface used to answer with the ambient ``allow_web_fallback``
    flag alone, which is a machine-wide default and not a per-turn truth. A turn
    running under Local Only cannot reach the web whatever that flag says, so a
    surface reporting the flag reports something the operator cannot act on.
    """
    if local_only_active(source_context):
        return False
    try:
        from core.policy_engine import allow_web_fallback

        return bool(allow_web_fallback())
    except Exception:
        return False


def remote_fetch_forbidden() -> bool:
    return bool(_REMOTE_FETCH_FORBIDDEN.get())


class RemoteFetchRefusedError(RuntimeError):
    """This turn is not permitted to reach the network, so no socket was opened."""


def open_remote(
    request: Any,
    *,
    timeout: float,
    context: Any = None,
    retry_of: str = "",
    provider_id: str = "",
    keyed_or_keyless: str = "",
    no_proxy: bool = False,
    redirect_policy: str = "follow",
    pinned_addresses: tuple[str, ...] | None = None,
) -> Any:
    """The ONE outbound HTTP door: enforce the veto, then report, then open.

    This lived in `tools/web/web_research.py` and made a repo-wide claim from a module-scoped
    position -- "a new fetch added later cannot forget to report or to ask, because there is
    nowhere else to open a socket". There was somewhere else. `tools/web/searxng_client.py` opened
    its own socket, so on 2026-08-18 a turn that FORBADE remote fetching still reached the SearXNG
    endpoint with the user's query through the `web.search` tool intent, and no SearXNG call ever
    appeared in `web_calls`.

    It sits here now because this module already owns both halves the door enforces, and because
    `core` is importable from `tools` without the cycle that kept the client out of it.

    `no_proxy=True` (credential intelligence, 2026-09-02) sends the request through an opener
    with an EMPTY proxy handler, so ambient HTTP(S)_PROXY vars cannot position themselves
    between a pasted API key and the pinned provider host. Default False keeps every existing
    caller's transport byte-identical.

    `pinned_addresses` retains a caller-validated DNS answer through socket connect,
    preserving the URL hostname for TLS verification. It requires refused redirects,
    disables proxies, and does not bypass permission or effect accounting.

    Refusal raises rather than returning empty, so a caller that ignores it fails closed instead of
    silently reporting "no results" for a request that was never allowed to run.

    R2b1, AMENDED -- THE DOOR DOES NOT AUTHORIZE ITSELF. With no active turn or
    background ledger the door denies, typed, BEFORE any socket: an arbitrary
    unscoped caller cannot become trusted merely by using the canonical door.
    (R2b1 first opened a generic background scope here for the no-turn lanes --
    that was the fail-open wearing an exemption's name, and it is gone.)
    Legitimate background services open `named_background_effect_scope` at
    their OWN entry point, with a specific name; this door grants nothing.

    R2b2b -- ONE DURABLE LIFECYCLE PER ATTEMPT. Every transport attempt this
    door opens runs the full state machine into the owning ledger:
    authorized → started (immediately before the socket) → exactly one of
    succeeded | failed | cancelled — exceptions, timeouts and cancellation
    included. `retry_of` names the LOGICAL effect a retry belongs to: the
    retry is a new attempt identity under the same effect id, never a second
    effect.

    `provider_id` / `keyed_or_keyless` let a caller that KNOWS which service it
    is reaching say so, exactly as it already states the host. The door records
    the declaration; it does not infer one. This exists because `host` cannot
    answer the question the user asks of a search: `api.search.brave.com`
    reached with their key, and the keyless scraper that reads Brave's HTML,
    are the same host and opposite facts about whether the credential they pay
    for did any work.
    """

    from core.effect_gateway import current_effect_ledger

    if current_effect_ledger() is None:
        raise RemoteFetchRefusedError(NO_ACTIVE_LEDGER_DENIAL)
    return _open_enforced(
        request,
        timeout=timeout,
        context=context,
        retry_of=retry_of,
        provider_id=provider_id,
        keyed_or_keyless=keyed_or_keyless,
        no_proxy=no_proxy,
        redirect_policy=redirect_policy,
        pinned_addresses=pinned_addresses,
    )


#: The typed denial for a fetch attempt with no active ledger. Named once so
#: tests and audits can pin the exact reason the door states.
NO_ACTIVE_LEDGER_DENIAL = (
    "no active turn or background effect ledger: a fetch outside any turn scope "
    "is denied before any socket. Legitimate background services open "
    "named_background_effect_scope at their owning entry point; using the "
    "canonical door grants no authority."
)


def _file_transport_fault(exc: BaseException, *, url: str, provider_id: str) -> None:
    """File the typed fault for the two transport outcomes THIS door owns: timeout, cancellation.

    The door is the transport's owning boundary, so the mapping happens here, once, before
    the exception propagates untouched. Every other transport failure stays accounted for
    by the effect lifecycle (which already says attempted-then-failed); typing those as
    catalog faults here would either guess or drown the plane in noise.
    """
    try:
        from core.effect_gateway import _is_cancellation as _door_is_cancellation

        if _door_is_cancellation(exc):
            code = "cancelled"
        elif isinstance(exc, TimeoutError):
            # socket.timeout IS TimeoutError since 3.10; asyncio.TimeoutError since 3.11.
            code = "timeout"
        else:
            return
        from core.faults.recorder import identity_from_context, record_fault
        from core.faults.records import FaultRecord

        turn_key, session_id = identity_from_context(_SCOPE_CONTEXT.get())
        host = _host_of(url)
        record_fault(
            FaultRecord.for_code(
                code,
                authority="core.remote_fetch_policy",
                turn_key=turn_key,
                session_id=session_id,
                dedupe=f"transport:{host}:{type(exc).__name__}",
                context={
                    "host": host,
                    "provider_id": provider_id,
                    "error_class": type(exc).__name__,
                    "operation": "remote_fetch",
                },
            )
        )
    except Exception:
        # The fault record must never change the exception's path: it propagates untouched.
        pass


def _open_enforced(
    request: Any,
    *,
    timeout: float,
    context: Any = None,
    retry_of: str = "",
    provider_id: str = "",
    keyed_or_keyless: str = "",
    no_proxy: bool = False,
    redirect_policy: str = "follow",
    pinned_addresses: tuple[str, ...] | None = None,
) -> Any:
    """The door's enforcement body: veto, gateway consult, lifecycle, open.

    R2b2b — the authorized half of the account is no longer the END of it.
    Every outcome below appends under ONE logical effect id on the owning
    ledger (the `EffectLifecycle` handle from `open_effect`):

    * veto / gateway denial → one `denied` entry, no socket, no attempt;
    * authorized → `started` immediately BEFORE the urlopen call, then exactly
      one terminal: `succeeded` when the call returns, `failed` for every
      exception outside the CancelledError family (timeouts included, named),
      `cancelled` for the CancelledError family — all re-raised as raised.
    """
    import urllib.request

    from core.effect_gateway import (
        DECISION_ALLOWED,
        DECISION_DENIED,
        EFFECT_NETWORK_FETCH,
        LIFECYCLE_AUTHORIZED,
        LIFECYCLE_DENIED,
        EffectReceipt,
        _is_cancellation,
        current_effect_ledger,
        decide_network_fetch,
        safe_transport_failure_reason,
    )

    url = str(getattr(request, "full_url", "") or "")
    ledger = current_effect_ledger()
    # M5 SLICE 1 — the door consults the ONE gateway and emits a typed receipt
    # for BOTH outcomes. The legacy veto stays (it is the explicit per-turn
    # `allow_remote_fetch: false` signal); the gateway adds the mode-matrix
    # deny. A denial now leaves a RECORD, not just an exception: deny is a
    # first-class fact (A6 reconciliation and the M6 taxonomy consume it).
    # LOCAL ONLY — zero public egress, checked before anything else this door does.
    # The receipt carries the HOST and nothing else from the request: evidence
    # without transmission.
    _lo_forbidden, _lo_reason = _local_only_egress_forbidden(_SCOPE_CONTEXT.get() or {}, url)
    if _lo_forbidden:
        ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_DENIED,
                lifecycle=LIFECYCLE_DENIED,
                reason=_lo_reason,
                host=_host_of(url),
                provider_id=str(provider_id or ""),
                keyed_or_keyless=str(keyed_or_keyless or ""),
                mode=_active_mode_label(),
                decided_by="remote_fetch_policy.local_only_egress",
                recorded_at=_utcnow_iso(),
            ),
            retry_of=retry_of,
        )
        raise RemoteFetchRefusedError(_lo_reason)
    if remote_fetch_forbidden():
        ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_DENIED,
                lifecycle=LIFECYCLE_DENIED,
                reason="remote fetch is not permitted for this turn",
                host=_host_of(url),
                provider_id=str(provider_id or ""),
                keyed_or_keyless=str(keyed_or_keyless or ""),
                mode=_active_mode_label(),
                decided_by="remote_fetch_policy.explicit_veto",
                recorded_at=_utcnow_iso(),
            ),
            retry_of=retry_of,
        )
        raise RemoteFetchRefusedError("remote fetch is not permitted for this turn")
    _m5_decision, _m5_reason = decide_network_fetch(_SCOPE_CONTEXT.get() or {})
    if _m5_decision == DECISION_DENIED:
        ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_DENIED,
                lifecycle=LIFECYCLE_DENIED,
                reason=_m5_reason,
                host=_host_of(url),
                provider_id=str(provider_id or ""),
                keyed_or_keyless=str(keyed_or_keyless or ""),
                mode=_active_mode_label(),
                decided_by="mode_permission_policy.decide_tool_call",
                recorded_at=_utcnow_iso(),
            ),
            retry_of=retry_of,
        )
        raise RemoteFetchRefusedError(f"network fetch denied by the permission gateway: {_m5_reason}")
    from core.effect_budget import EffectBudgetRefusedError

    try:
        effect = ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason=_m5_reason,
                host=_host_of(url),
                provider_id=str(provider_id or ""),
                keyed_or_keyless=str(keyed_or_keyless or ""),
                mode=_active_mode_label(),
                decided_by="mode_permission_policy.decide_tool_call",
                recorded_at=_utcnow_iso(),
            ),
            retry_of=retry_of,
        )
    except EffectBudgetRefusedError as budget_refusal:
        # P1 — the budget gate refused at the one real gate; the ledger holds
        # the DENIED receipt, the caller gets this door's typed refusal.
        raise RemoteFetchRefusedError(
            f"network fetch refused by the effect budget: {budget_refusal.code}: "
            f"{budget_refusal.detail}"
        )
    note_remote_fetch_attempt(url)
    effect.begin_attempt()
    try:
        if pinned_addresses is not None:
            if redirect_policy != "refuse":
                raise ValueError("Pinned addresses require per-hop redirect validation")
            response = _no_redirect_opener(pinned_addresses=pinned_addresses, context=context).open(request, timeout=timeout)
        elif redirect_policy == "refuse":
            # Origin-pinned callers (the wallet's outbound confinement) take each redirect
            # hop back through THIS door for re-validation instead of letting urllib's
            # default handler follow it beyond the validated origin. A request whose URL
            # itself carries a credential (a path token) also refuses ambient proxies.
            response = _no_redirect_opener(no_proxy=no_proxy).open(request, timeout=timeout)
        elif no_proxy:
            # Keyed credential traffic (verification): an ambient proxy env var must never
            # sit between the key and the pinned host. Same lifecycle, proxy-free transport.
            response = _no_proxy_opener().open(request, timeout=timeout)
        elif context is None:   # keep the no-context call shape byte-identical for existing callers/stubs
            response = urllib.request.urlopen(request, timeout=timeout)
        else:
            response = urllib.request.urlopen(request, timeout=timeout, context=context)
    except BaseException as exc:
        # every started attempt ends in exactly one terminal — the exception
        # (or cancellation) still propagates untouched; the account just stops
        # lying by omission about where the attempt ended
        if _is_cancellation(exc):
            effect.cancel(reason=f"cancelled during transport: {safe_transport_failure_reason(exc)}")
        else:
            effect.fail(exc=exc)
        _file_transport_fault(exc, url=url, provider_id=provider_id)
        raise
    effect.succeed(status=getattr(response, "status", None))
    return response


_NO_REDIRECT_OPENER: Any = None
_NO_REDIRECT_NO_PROXY_OPENER: Any = None


def _no_redirect_opener(*, no_proxy: bool = False, pinned_addresses: tuple[str, ...] | None = None, context: Any = None) -> Any:
    """An opener whose redirect handler REFUSES: a redirect is a new origin the caller has
    not validated. With redirect_policy="refuse" the door returns the 3xx itself so the
    origin-pinned caller can re-validate every hop through this door. ``no_proxy`` also empties
    the proxy handler, for a request whose URL itself carries a credential."""
    global _NO_REDIRECT_OPENER, _NO_REDIRECT_NO_PROXY_OPENER
    cached = None if pinned_addresses is not None else (_NO_REDIRECT_NO_PROXY_OPENER if no_proxy else _NO_REDIRECT_OPENER)
    if cached is None:
        import urllib.error
        import urllib.request

        class _RefuseRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                fp.close()
                raise urllib.error.HTTPError(req.full_url, code, f"redirect_refused:{newurl}", headers, fp)

        handlers: list[Any] = [_RefuseRedirectHandler()]
        if pinned_addresses is not None:
            from core.pinned_http import pinned_http_handlers

            handlers.extend(pinned_http_handlers(pinned_addresses, context=context))
        if no_proxy or pinned_addresses is not None:
            handlers.insert(0, urllib.request.ProxyHandler({}))
        cached = urllib.request.build_opener(*handlers)
        if pinned_addresses is not None:
            return cached  # Per-request approval, never cache across targets or DNS answers.
        if no_proxy:
            _NO_REDIRECT_NO_PROXY_OPENER = cached
        else:
            _NO_REDIRECT_OPENER = cached
    return cached


_NO_PROXY_OPENER: Any = None


def _no_proxy_opener() -> Any:
    """A lazily built opener with an EMPTY ProxyHandler — it consults neither proxy env vars
    nor system proxy config. Used only for keyed credential verification requests."""
    global _NO_PROXY_OPENER
    if _NO_PROXY_OPENER is None:
        import urllib.request

        _NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _NO_PROXY_OPENER


def open_remote_url(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str = "",
    timeout: float = 30.0,
    context: Any = None,
    retry_of: str = "",
    provider_id: str = "",
    keyed_or_keyless: str = "",
    no_proxy: bool = False,
    credential_headers: tuple[str, ...] | None = None,
) -> Any:
    """Same ONE door, convenience form: build the urllib Request here so a
    caller with `(method, url, body, headers)` needs no raw urlopen/requests
    call of its own. Veto is enforced BEFORE any network I/O; the fetch is
    reported into the owning turn's ledger and runs the full R2b2b lifecycle.
    `retry_of` continues a prior logical effect (a retry is a new attempt on
    the same effect, not a new effect). `no_proxy=True` bypasses proxy env
    routing for keyed credential traffic (see open_remote).

    `credential_headers` marks a request that carries a key. The named headers
    (the ones holding it; none when the key rides the body) are attached
    UNREDIRECTED, and the exchange runs under `open_keyed_request`, so the key
    can never follow a redirect to another origin. Left at None, the request is
    built and sent exactly as before."""
    import urllib.request

    plain = dict(headers or {})
    sealed_names = {str(name).strip().lower() for name in (credential_headers or ()) if str(name).strip()}
    sealed = {name: plain.pop(name) for name in list(plain) if str(name).lower() in sealed_names}
    req = urllib.request.Request(
        str(url or ""),
        data=data,
        headers=plain,
        method=str(method or ("POST" if data is not None else "GET")) or None,
    )
    for name, value in sealed.items():
        req.add_unredirected_header(name, value)

    def _send(request: Any) -> Any:
        return open_remote(
            request,
            timeout=timeout,
            context=context,
            retry_of=retry_of,
            provider_id=provider_id,
            keyed_or_keyless=keyed_or_keyless,
            no_proxy=no_proxy,
        )

    if credential_headers is None:
        return _send(req)
    return open_keyed_request(req, _send)


class CredentialRedirectRefusedError(OSError):
    """A request carrying a key was redirected where the key may not follow: to another origin,
    or a second time. Nothing further was sent. ``redirect_origin`` names where the redirect
    pointed as scheme://host[:port] only, because a path or query can carry a token."""

    def __init__(self, final_url: str) -> None:
        self.redirect_origin = _origin_text(final_url)
        super().__init__(f"redirect refused: {self.redirect_origin or 'unknown origin'}")


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int] | None:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(str(url or ""))
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    if not scheme or not host or port is None:
        return None
    return scheme, host, port


def _origin_text(url: str) -> str:
    origin = _origin(url)
    if origin is None:
        return ""
    scheme, host, port = origin
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}" + ("" if _DEFAULT_PORTS.get(scheme) == port else f":{port}")


def _same_origin(first: str, second: str) -> bool:
    one = _origin(first)
    return one is not None and one == _origin(second)


def _followed_redirect_url(request: Any, answer: Any) -> str:
    """The URL urllib ended at when its redirect handler followed a redirect for `request`, else "".

    urllib records every redirect it follows in ``request.redirect_dict`` (its loop guard). That
    record, not a comparison with whatever URL an answer reports, is the evidence a hop happened,
    so a transport that never redirects can never be mistaken for one that did."""
    visited = getattr(request, "redirect_dict", None)
    if not visited:
        return ""
    getter = getattr(answer, "geturl", None)
    try:
        final = str(getter() or "") if callable(getter) else ""
    except Exception:
        final = ""
    return final or str(list(visited)[-1])


def _close_quietly(answer: Any) -> None:
    close = getattr(answer, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()


def _request_at(request: Any, url: str) -> Any:
    """`request` addressed to `url`, with its key headers attached unredirected again."""
    import urllib.request

    again = urllib.request.Request(url, data=request.data, headers=dict(request.headers), method=request.get_method())
    for name, value in request.unredirected_hdrs.items():
        # urllib adds these itself for whatever URL it sends to.
        if name.lower() not in ("host", "content-length", "content-type"):
            again.add_unredirected_header(name, value)
    return again


def open_keyed_request(request: Any, send: Any) -> Any:
    """Exchange one key-bearing request through `send` (a call into this door) so the key only
    ever reaches the origin the request was addressed to.

    The caller attaches the key with ``Request.add_unredirected_header`` or in a POST body; urllib's
    redirect handler copies neither onto a redirect hop, and the transport stays the plain
    ``urllib.request.urlopen`` the door always calls. When urllib nevertheless followed a redirect,
    the answer came from a hop that carried no key and says nothing about the key:

    * a hop to another origin -> ``CredentialRedirectRefusedError``; nothing more is sent;
    * a hop within the same origin -> the request is sent once more, with its key, to the URL urllib
      ended at; a redirect on that second request is refused as well.

    Non-2xx answers arrive as ``urllib.error.HTTPError`` and go through the same judgement; one that
    involved no redirect is re-raised untouched."""
    import urllib.error

    current = request
    followed = False
    while True:
        try:
            answer = send(current)
        except urllib.error.HTTPError as exc:
            final = _followed_redirect_url(current, exc)
            if not final:
                raise
            _close_quietly(exc)
        else:
            final = _followed_redirect_url(current, answer)
            if not final:
                return answer
            _close_quietly(answer)
        if followed or not _same_origin(current.full_url, final):
            raise CredentialRedirectRefusedError(final)
        followed = True
        current = _request_at(current, final)


def _host_of(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        from urllib.parse import urlsplit

        return str(urlsplit(text).hostname or "")
    except Exception:
        return ""


def note_remote_fetch_attempt(
    url: str = "",
    *,
    host: str = "",
    status: int | None = None,
    duration_ms: float | None = None,
) -> None:
    """Report one remote call into the owning turn, from whichever thread makes it.

    WHAT was fetched, not merely how many times. Every argument is optional so no existing caller
    breaks, but a caller that omits the address leaves the turn unable to say where its answer came
    from -- which was the state until 2026-08-18, when the ledger held a bare integer and a weather
    turn that read three pages about the wrong city recorded only `web_calls: 7`. The URL was
    already in scope at every call site; nothing had to be discovered to record it.
    """

    ledger = _REMOTE_FETCH_ATTEMPTS.get()
    if ledger is None:
        # No scope is open, so there is no turn to report into. `remote_fetch_scope_active()`
        # already tells callers a count read here proves nothing; silently tallying into a
        # process-wide total would instead let one turn's number be read as another's.
        return
    ledger.note(url, host=host, status=status, duration_ms=duration_ms)


def remote_fetch_attempt_count() -> int:
    ledger = _REMOTE_FETCH_ATTEMPTS.get()
    return ledger.count() if ledger is not None else 0


def remote_fetch_attempts() -> tuple[dict[str, Any], ...]:
    """Every remote call this turn made, in order, with the address it went to.

    Empty outside a scope for the same reason `remote_fetch_attempt_count()` reads 0 there --
    nothing is being kept, which is not the same as nothing having happened. Check
    `remote_fetch_scope_active()` before reading a quiet result as proof.
    """

    ledger = _REMOTE_FETCH_ATTEMPTS.get()
    return ledger.entries() if ledger is not None else ()


def remote_fetch_attempts_truncated() -> bool:
    """Whether the per-call detail stopped being kept while the count kept rising."""

    ledger = _REMOTE_FETCH_ATTEMPTS.get()
    return ledger.truncated() if ledger is not None else False


def remote_fetch_scope_active() -> bool:
    """Whether `remote_fetch_attempt_count()` is being maintained for the current turn.

    Read by `core.runtime_evidence`: a count of 0 is only evidence that nothing was fetched when
    something was actually counting. Every remote HTTP path funnels through
    `note_remote_fetch_attempt` before its own policy check, so inside the scope a zero is a
    complete account of the turn's remote calls and a negative claim can be proven from it.
    """

    return bool(_REMOTE_FETCH_SCOPE_ACTIVE.get())


@contextmanager
def remote_fetch_turn_scope(source_context: dict[str, Any] | None) -> Iterator[None]:
    """Open per-turn accounting unless an outer channel boundary already owns it."""

    if remote_fetch_scope_active():
        yield
        return
    with remote_fetch_policy_scope(source_context):
        yield


@contextmanager
def remote_fetch_policy_scope(source_context: dict[str, Any] | None) -> Iterator[None]:
    attempts_token = _REMOTE_FETCH_ATTEMPTS.set(_FetchLedger())
    scope_token = _REMOTE_FETCH_SCOPE_ACTIVE.set(True)
    context_token = _SCOPE_CONTEXT.set(dict(source_context or {}))
    # M5 slice 1: the turn-scoped EFFECT LEDGER rides the same scope — one
    # ledger per turn, thread-inherited like the fetch ledger. R2: it is handed
    # the LIVE context, because the turn mints its identity after the HTTP door
    # opens this scope and a copy taken here would never see it.
    from core.effect_gateway import (
        EFFECT_RECEIPTS_CONTEXT_KEY,
        EFFECT_RECEIPTS_DROPPED_CONTEXT_KEY,
        EFFECT_RECEIPTS_TRUNCATED_CONTEXT_KEY,
        close_effect_receipt_scope,
        open_effect_receipt_scope,
    )

    effect_ledger = open_effect_receipt_scope(source_context)
    forbidden_token = None
    if explicit_remote_fetch_disabled(source_context):
        forbidden_token = _REMOTE_FETCH_FORBIDDEN.set(True)
    try:
        yield
    finally:
        # R2: the receipts are HANDED TO THE TURN, not dropped. This call's
        # return value used to be discarded here, which made the whole account
        # unreadable the instant the scope closed — including for the passes
        # that run after it, which is exactly where the turn's honesty checks
        # live. Truncation rides beside them so a short list cannot read as a
        # complete account.
        finalized_receipts = close_effect_receipt_scope()
        if isinstance(source_context, dict):
            source_context[EFFECT_RECEIPTS_CONTEXT_KEY] = finalized_receipts
            source_context[EFFECT_RECEIPTS_TRUNCATED_CONTEXT_KEY] = effect_ledger.truncated()
            # R2b1: the truncation flag rides with the EXACT dropped count, so
            # a short account can be quantified, not merely flagged.
            source_context[EFFECT_RECEIPTS_DROPPED_CONTEXT_KEY] = effect_ledger.dropped()
        _SCOPE_CONTEXT.reset(context_token)
        if forbidden_token is not None:
            _REMOTE_FETCH_FORBIDDEN.reset(forbidden_token)
        _REMOTE_FETCH_SCOPE_ACTIVE.reset(scope_token)
        _REMOTE_FETCH_ATTEMPTS.reset(attempts_token)


__all__ = [
    "LOCAL_ONLY_EGRESS_DENIAL",
    "NO_ACTIVE_LEDGER_DENIAL",
    "CredentialRedirectRefusedError",
    "RemoteFetchRefusedError",
    "effective_web_available",
    "explicit_remote_fetch_disabled",
    "local_only_active",
    "note_remote_fetch_attempt",
    "open_keyed_request",
    "open_remote",
    "open_remote_url",
    "remote_fetch_allowed_by_context",
    "remote_fetch_attempt_count",
    "remote_fetch_attempts",
    "remote_fetch_attempts_truncated",
    "remote_fetch_forbidden",
    "remote_fetch_policy_scope",
    "remote_fetch_scope_active",
    "remote_fetch_turn_scope",
]
