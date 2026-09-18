"""Who answered a retrieval, whether a key paid for it, and whether it may be cited.

Three surfaces reach the web for the user — the settings "Test search provider"
button, the typed live-data lane, and the generic keyless fallback — and at base
none of them could say which provider had run:

* `retrieval/web_adapter.py::_provider_source_label` named three keyless
  scrapers and fell through to the CALLER's label for everything else. On the
  live-info lane that label is the hardcoded string `"duckduckgo.com"`, so a
  Brave-served result was stored, receipted and displayed as DuckDuckGo. That is
  worse than an absent label: it is a false provenance claim the UI repeats.
* `core/retrieval_observability.py::finish_web_retrieval` read only
  `origin_domain` off each note, so a receipt could say three sources came back
  and never say who returned them — while `search_provider` sat unread on every
  note.
* The effect ledger named the HOST. A host is not a provider:
  `api.search.brave.com` (the paid API) and the keyless scraper that reads
  Brave's HTML are different facts about what the user's key did.

This module is the one place those three answers are computed, so a provider
cannot be named one way in a receipt and another way in the panel.

`retrieval_supports_current_claims` is the other half: the rule that a fallback
may rescue a failed typed tool ONLY if the fallback itself left a successful
governed receipt. Without it the product had a hidden split — the typed tool
reported failure, an unreceipted generic search returned snippets anyway, and
the answer spoke as though the typed tool had worked.
"""
from __future__ import annotations

from typing import Any

#: Terminal states a `vool.web_retrieval_receipt.v1` can carry, and the
#: lifecycle each one means. Named here so the receipt writers, the settings
#: probe and the citation gate cannot drift into three different vocabularies.
STATUS_AVAILABLE = "available"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "failed"
STATUS_REFUSED = "refused"
STATUS_STARTED = "started"

LIFECYCLE_STARTED = "started"
LIFECYCLE_SUCCEEDED = "succeeded"
LIFECYCLE_FAILED = "failed"
LIFECYCLE_DENIED = "denied"

_STATUS_LIFECYCLE = {
    STATUS_STARTED: LIFECYCLE_STARTED,
    STATUS_AVAILABLE: LIFECYCLE_SUCCEEDED,
    # "Reached the provider, it had nothing" is NOT a success and NOT a failure.
    # Collapsing it into either one sends the user to fix a key that works, or
    # lets an empty answer be cited as evidence.
    STATUS_UNAVAILABLE: LIFECYCLE_FAILED,
    STATUS_FAILED: LIFECYCLE_FAILED,
    STATUS_REFUSED: LIFECYCLE_DENIED,
}

KEYED = "keyed"
KEYLESS = "keyless"

#: Display names for the providers that need no credential. They are listed
#: rather than derived because the honest name of a keyless engine is not always
#: its internal id: `google_html` is the keyless scraper, and calling it "Brave"
#: because it happens to read Brave's HTML would borrow the identity of a
#: provider the user pays for — the precise lie this module exists to stop.
KEYLESS_PROVIDER_LABELS: dict[str, str] = {
    "google_html": "Keyless search",
    "searxng": "SearXNG",
    "ddg_instant": "DuckDuckGo Instant Answer",
    "ddg": "DuckDuckGo Instant Answer",
    "duckduckgo_html": "DuckDuckGo",
    "duckduckgo": "DuckDuckGo",
    "bing": "Bing",
    "browser_search": "Browser search",
}

#: What a provider slot says when nothing ran. Distinct from "" so a reader can
#: tell "no retrieval" from "a retrieval whose provider we failed to record".
PROVIDER_NONE = "none"


def _keyed_config(provider_id: str):
    try:
        from core.search_providers import config_for

        return config_for(provider_id)
    except Exception:
        return None


def _has_stored_key(provider_id: str) -> bool:
    cfg = _keyed_config(provider_id)
    if cfg is None:
        return False
    try:
        from core import credential_store

        return bool(credential_store.has_credential(cfg.credential_slot))
    except Exception:
        # A credential store that cannot be read is not evidence of a key. The
        # safe reading is keyless: claiming "keyed" would assert the user's
        # credential was used when nothing established that.
        return False


def provider_attribution(provider_id: str, *, has_key: bool | None = None) -> dict[str, str]:
    """The one answer to "who answered, and did a key pay for it".

    `has_key` is an override for callers that already know (the settings probe
    read the key to build the request) and for hermetic tests; left None it is
    resolved from the credential store.
    """
    clean = str(provider_id or "").strip().lower()
    if not clean or clean in {PROVIDER_NONE, "disabled"}:
        return {
            "provider_id": clean or PROVIDER_NONE,
            "provider_label": "No provider",
            "keyed_or_keyless": KEYLESS,
        }

    cfg = _keyed_config(clean)
    keyed = bool(has_key) if has_key is not None else _has_stored_key(clean)
    if cfg is not None and keyed:
        return {"provider_id": clean, "provider_label": cfg.label, "keyed_or_keyless": KEYED}
    if cfg is not None:
        # A key-backed provider in the table that holds no key did not run on a
        # credential, whatever the table says it is capable of.
        return {"provider_id": clean, "provider_label": cfg.label, "keyed_or_keyless": KEYLESS}

    label = KEYLESS_PROVIDER_LABELS.get(clean)
    if label is None:
        # An engine nobody registered. Name the id rather than inventing a
        # friendly label that could collide with a real provider's name.
        label = f"Keyless search ({clean})"
    return {"provider_id": clean, "provider_label": label, "keyed_or_keyless": KEYLESS}


def lifecycle_for_status(status: str) -> str:
    """The lifecycle a terminal status means. Unknown statuses are failures.

    Fail-closed on the vocabulary too: a status this module has never heard of
    must not be able to read as SUCCEEDED and authorize a current claim.
    """
    return _STATUS_LIFECYCLE.get(str(status or "").strip().lower(), LIFECYCLE_FAILED)


def attribution_summary(receipt: dict[str, Any] | None) -> str:
    """The one-line provenance a surface shows: "Brave Search · 3 sources".

    Empty when there is nothing truthful to say, so a caller cannot render a
    confident-looking line for a retrieval that never named its provider.
    """
    if not isinstance(receipt, dict):
        return ""
    label = str(receipt.get("provider_label") or "").strip()
    if not label:
        label = str(provider_attribution(str(receipt.get("provider_id") or ""))["provider_label"])
    if not label or label == "No provider":
        return ""
    try:
        count = max(0, int(receipt.get("source_count") or 0))
    except (TypeError, ValueError):
        count = 0
    if lifecycle_for_status(str(receipt.get("status") or "")) != LIFECYCLE_SUCCEEDED:
        reason = str(receipt.get("failure_class") or receipt.get("status") or "failed").strip()
        return f"{label} · {reason}"
    return f"{label} · {count} source{'' if count == 1 else 's'}"


def successful_retrieval_receipts(source_context: Any) -> list[dict[str, Any]]:
    """Every receipt on the turn that proves a retrieval actually delivered.

    Both receipt lists are read — `web_retrieval_receipts` (generic search, the
    live-data lane and the live-info fast path) and `fresh_data_retrieval_receipts`
    (FX) — because a caller asking "did anything ground this turn" must not have
    to know which lane happened to run.
    """
    if not isinstance(source_context, dict):
        return []
    found: list[dict[str, Any]] = []
    for key in ("web_retrieval_receipts", "fresh_data_retrieval_receipts"):
        for receipt in list(source_context.get(key) or []):
            if not isinstance(receipt, dict):
                continue
            lifecycle = str(receipt.get("lifecycle") or "").strip().lower()
            if not lifecycle:
                lifecycle = lifecycle_for_status(str(receipt.get("status") or ""))
            if lifecycle != LIFECYCLE_SUCCEEDED:
                continue
            try:
                count = int(receipt.get("source_count") or 0)
            except (TypeError, ValueError):
                count = 0
            if count <= 0:
                # A succeeded receipt with nothing behind it grounds nothing.
                continue
            found.append(dict(receipt))
    return found


def retrieval_supports_current_claims(source_context: Any) -> bool:
    """Whether this turn may state a current fact as retrieved.

    THE RESCUE RULE. A fallback may cover for a failed typed tool only when the
    fallback itself left a successful governed receipt. Snippets appearing in
    the answer are not the test, and neither is the presence of a receipt: a
    receipt that says `failed` is proof the opposite way. Without this the
    product had a hidden split — the typed tool reported failure, an unreceipted
    generic search returned results anyway, and the answer read as though the
    typed tool had worked.
    """
    return bool(successful_retrieval_receipts(source_context))


__all__ = [
    "KEYED",
    "KEYLESS",
    "KEYLESS_PROVIDER_LABELS",
    "LIFECYCLE_DENIED",
    "LIFECYCLE_FAILED",
    "LIFECYCLE_STARTED",
    "LIFECYCLE_SUCCEEDED",
    "PROVIDER_NONE",
    "STATUS_AVAILABLE",
    "STATUS_FAILED",
    "STATUS_REFUSED",
    "STATUS_STARTED",
    "STATUS_UNAVAILABLE",
    "attribution_summary",
    "lifecycle_for_status",
    "provider_attribution",
    "retrieval_supports_current_claims",
    "successful_retrieval_receipts",
]
