"""Live "does this search key actually work" probe for the settings surface.

The probe runs a real, minimal search through the SAME client the search chain uses
(``tools/web/search_api_client.search``) rather than pinging some cheaper health path. That is
deliberate: a probe that exercises a different path can pass while real searches fail -- wrong auth
header, wrong endpoint, a response shape the parser cannot read -- and the user is then told their
key is fine while every answer stays ungrounded. Here, a green Test means one real query was sent
with this key and results came back in a shape the runtime could parse.

Rate-limited per provider like the cloud probe, so a user clicking Test repeatedly cannot burn
their free-tier quota.
"""
from __future__ import annotations

import time
from typing import Any

STATE_OK = "ok"
STATE_NO_KEY = "no_key"
STATE_UNAUTHORIZED = "unauthorized"
STATE_RATE_LIMITED = "rate_limited"
STATE_QUOTA = "quota_exhausted"
STATE_UNREACHABLE = "unreachable"
STATE_FAILED = "failed"
#: The turn/runtime was not PERMITTED to fetch. A policy fact, not a verdict on
#: the key -- and the state this module could not express, which is how a
#: refusal at the outbound door was reported to the user as
#: `failed / RemoteFetchRefusedError` and read as "your key is broken".
STATE_REFUSED = "refused"

#: The scope name this probe runs under. `named_background_effect_scope` requires
#: a specific name on purpose: a nameless exemption is a general fail-open, and
#: this one is narrow -- it authorizes exactly one minimal search with a stored
#: key, initiated by the user pressing Test.
PROBE_EFFECT_SCOPE = "settings.search_provider_test"

#: One live probe per provider per this window; repeats serve the cached verdict.
_PROBE_MIN_INTERVAL_SECONDS = 5.0
#: A query with a stable, boring answer on every engine. Never the user's text: a probe must not
#: put anything from the conversation on the wire.
_PROBE_QUERY = "example"

_last_probe_ts: dict[str, float] = {}
_last_verdict: dict[str, dict[str, Any]] = {}

# Client reasons that are a verdict about the KEY, mapped to the state the UI shows. Anything
# unlisted is a transport or provider problem, which must not be reported as a bad key.
_REASON_STATES = {
    "unauthorized": STATE_UNAUTHORIZED,
    "rate_limited": STATE_RATE_LIMITED,
    "quota_exhausted": STATE_QUOTA,
    "no_key": STATE_NO_KEY,
    "timeout": STATE_UNREACHABLE,
    "unreachable": STATE_UNREACHABLE,
}


def _label(provider_id: str) -> str:
    from core.search_providers import config_for

    cfg = config_for(provider_id)
    return cfg.label if cfg else provider_id


def probe_search_provider(provider_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Run one live search with the stored key. Returns a status dict, never any key material."""
    ts = time.time() if now is None else now
    provider = str(provider_id or "").strip().lower()
    base = {"provider": provider, "label": _label(provider)}

    from core.search_providers import config_for

    cfg = config_for(provider)
    if cfg is None:
        return {**base, "state": STATE_FAILED, "detail": "unknown provider", "checked_at": None}

    from core.credential_store import get_credential

    key = (get_credential(cfg.credential_slot) or "").strip()
    if not key:
        return {**base, "state": STATE_NO_KEY, "detail": "no key stored", "checked_at": None}

    cached = _last_verdict.get(provider)
    if cached is not None and (ts - _last_probe_ts.get(provider, 0.0)) < _PROBE_MIN_INTERVAL_SECONDS:
        return {**base, **cached}
    _last_probe_ts[provider] = ts

    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import RemoteFetchRefusedError
    from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval
    from core.retrieval_provenance import KEYED
    from tools.web.search_api_client import SearchApiError
    from tools.web.search_api_client import search as run_search

    # THE PROBE ENTERS THE SAME EFFECT GATEWAY AS EVERY OTHER RETRIEVAL.
    #
    # It did not, and that was the whole defect. `open_remote` denies typed and
    # before any socket when no effect ledger is active -- an unattributable
    # fetch must not be authorized -- so this probe never reached the network at
    # all. The bare `except Exception` below then reported that refusal as
    # `state=failed, detail="RemoteFetchRefusedError"`, which the settings panel
    # renders as "The search did not come back usable", and a paid, working key
    # looked dead.
    #
    # The scope is the sanctioned non-turn path and is opened HERE, at the
    # owning entry point, with a specific name -- never inside the door, which
    # would be the door manufacturing the authority required to pass itself.
    # A `source_context` carrying the receipts list means the probe leaves the
    # same durable, provider-attributed truth a chat retrieval leaves.
    probe_context: dict[str, Any] = {"surface": "api", "platform": "settings"}
    receipt: dict[str, Any] = {}
    with named_background_effect_scope(PROBE_EFFECT_SCOPE, source_context=probe_context):
        started = begin_web_retrieval(
            probe_context,
            kind="settings_search_provider_test",
            query=_PROBE_QUERY,
            task_id=PROBE_EFFECT_SCOPE,
            action="provider_test",
            provider_id=provider,
            keyed_or_keyless=KEYED,
        )
        try:
            hits = run_search(cfg, _PROBE_QUERY, key, max_hits=3, timeout_s=10.0)
        except RemoteFetchRefusedError as exc:
            # A turn-level or gateway refusal is a POLICY verdict, not a verdict
            # on the key. Kept distinct so the panel can say why nothing ran
            # instead of accusing a credential that was never used.
            receipt = finish_web_retrieval(probe_context, started, refused=True)
            verdict = {
                "state": STATE_REFUSED,
                "detail": _refusal_detail(exc),
                "checked_at": ts,
            }
        except SearchApiError as exc:
            receipt = finish_web_retrieval(probe_context, started, failure=exc)
            state = _REASON_STATES.get(exc.reason, STATE_FAILED)
            verdict = {"state": state, "detail": exc.detail or exc.reason, "checked_at": ts}
        except Exception as exc:  # transport surprises stay distinct from "bad key"
            receipt = finish_web_retrieval(probe_context, started, failure=exc)
            verdict = {"state": STATE_FAILED, "detail": type(exc).__name__, "checked_at": ts}
        else:
            receipt = finish_web_retrieval(
                probe_context,
                started,
                notes=[
                    {"origin_domain": _domain_of(hit.url), "search_provider": provider}
                    for hit in hits
                ],
            )
            if hits:
                verdict = {"state": STATE_OK, "detail": f"{len(hits)} result(s)", "checked_at": ts}
            else:
                # Authenticated but nothing parsed back. That is not a broken key, and saying so would
                # send the user to regenerate a key that is fine.
                verdict = {"state": STATE_FAILED, "detail": "no parsable results", "checked_at": ts}

    # The probe now answers the question the panel actually needs: WHICH provider
    # ran, whether a key paid for it, how many sources came back, and the durable
    # receipt that proves it. Key material appears in none of them.
    verdict.update(
        {
            "keyed_or_keyless": str(receipt.get("keyed_or_keyless") or KEYED),
            "source_count": int(receipt.get("source_count") or 0),
            "receipt": receipt,
        }
    )
    _last_verdict[provider] = verdict
    return {**base, **verdict}


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        netloc = (urlparse(str(url or "")).netloc or "").lower()
    except Exception:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _refusal_detail(exc: BaseException) -> str:
    """One bounded line naming why the fetch was refused, never the exception type alone.

    `RemoteFetchRefusedError` carries the gateway's own reason, and that reason
    is the only actionable part -- "denied by the permission gateway: Local Only
    mode" tells the user what to change, while `RemoteFetchRefusedError` (which
    is what shipped) tells them nothing and reads like a crash.
    """
    text = " ".join(str(exc).split()).strip()
    return text[:200] or type(exc).__name__


def connection_rows(has_key=None) -> list[dict[str, Any]]:
    """Presence-only rows for the settings list: which providers hold a key. No live calls."""
    from core.search_providers import SEARCH_PROVIDERS

    if has_key is None:
        try:
            from core import credential_store

            has_key = credential_store.has_credential
        except Exception:
            def has_key(_slot: str) -> bool:
                return False

    rows: list[dict[str, Any]] = []
    for provider_id, cfg in SEARCH_PROVIDERS.items():
        try:
            connected = bool(has_key(cfg.credential_slot))
        except Exception:
            connected = False
        rows.append(
            {
                "provider": provider_id,
                "label": cfg.label,
                "slot": cfg.credential_slot,
                "connected": connected,
                "signup_url": cfg.signup_url,
                "free_tier": cfg.free_tier,
            }
        )
    return rows


__all__ = [
    "STATE_FAILED",
    "STATE_NO_KEY",
    "STATE_OK",
    "STATE_QUOTA",
    "STATE_RATE_LIMITED",
    "STATE_UNAUTHORIZED",
    "STATE_UNREACHABLE",
    "connection_rows",
    "probe_search_provider",
]
