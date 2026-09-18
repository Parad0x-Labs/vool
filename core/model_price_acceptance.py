"""Accepted spend ceilings — the operator's authorized prices, distinct from catalog discovery.

The design authority's price law (docs/design/ux-pass1-authority-20260825.html): CATALOG PRICE ≠
USER-AUTHORIZED PRICE. Refresh/polling DISCOVERS prices; the accepted ceiling lives in
authorization state, is raised only by an explicit confirmation, and a discovered price above it
BLOCKS the pin until the operator decides again. This module is that authorization state, server-
side: one JSON record per (provider, canonical model id), written exactly when a confirmed paid
pin lands, read by the pin gate before any later pin of the same model.

Deliberately small and fail-toward-asking: a missing, corrupt, or unreadable store reads as "no
acceptance recorded", which makes the server ASK again — never silently allow. Writes are
atomic (temp + rename). Rates are captured from the same catalog composition the /api/cloud/models
surface serves, so the ceiling the operator saw is the ceiling stored.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

from core.runtime_paths import data_path

_STORE_NAME = "model_price_acceptances.json"

#: A price is "above" the ceiling only past this share — float noise in a republished identical
#: rate must not re-gate every pin.
_RISE_TOLERANCE = 0.005


def _store_path():
    return data_path(_STORE_NAME)


def _canonical(model_id: str) -> str:
    try:
        from core.cloud_model_control import canonical_model_identity

        return canonical_model_identity(model_id) or str(model_id or "").strip().lower()
    except Exception:
        return str(model_id or "").strip().lower()


def _key(provider_id: str, model_id: str) -> str:
    return f"{str(provider_id or '').strip().lower() or 'openrouter'}:{_canonical(model_id)}"


def _load() -> dict[str, dict[str, Any]]:
    try:
        with open(_store_path(), encoding="utf-8") as handle:
            raw = json.load(handle)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save(records: dict[str, dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=_STORE_NAME + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def current_rates_for(provider_id: str, model_id: str) -> dict[str, float] | None:
    """The catalog's CURRENT per-1M rates for this id, from the same rows the UI catalog serves.

    None when no readable row exists — discovery has nothing, and nothing is invented.
    """
    provider = str(provider_id or "").strip().lower() or "openrouter"
    wanted = _canonical(model_id)
    try:
        if provider == "openrouter":
            from core.openrouter_catalog import safe_all_models

            models, _age = safe_all_models(allow_network=False)
            for m in models:
                if _canonical(m.model_id) == wanted:
                    return {
                        "prompt_usd_per_m": round((m.prompt_usd_per_token or 0.0) * 1_000_000, 4),
                        "completion_usd_per_m": round(
                            (m.completion_usd_per_token or 0.0) * 1_000_000, 4
                        ),
                    }
            return None
        from core.cloud_model_catalog import curated_models

        for row in curated_models(provider):
            if _canonical(str(row.get("id") or "")) == wanted:
                return {
                    "prompt_usd_per_m": float(row.get("prompt_usd_per_m") or 0.0),
                    "completion_usd_per_m": float(row.get("completion_usd_per_m") or 0.0),
                }
        return None
    except Exception:
        return None


def acceptance_for(provider_id: str, model_id: str) -> dict[str, Any] | None:
    record = _load().get(_key(provider_id, model_id))
    return dict(record) if isinstance(record, dict) else None


def list_acceptances() -> list[dict[str, Any]]:
    out = []
    for key, record in sorted(_load().items()):
        if isinstance(record, dict):
            row = dict(record)
            row["key"] = key
            out.append(row)
    return out


def record_acceptance(provider_id: str, model_id: str, *, cost_state: str) -> dict[str, Any]:
    """Write (or REWRITE) the ceiling for this model at the catalog's current rates.

    Called exactly when a confirmed pin lands — the confirmation IS the authorization, so the
    stored ceiling is what the operator just accepted. A model with no readable rates stores
    zeroed rates with `rates_known: False`: the acceptance still records THAT the operator
    accepted unclassified cost, without inventing numbers.
    """
    rates = current_rates_for(provider_id, model_id)
    record = {
        "provider": str(provider_id or "").strip().lower() or "openrouter",
        "model": _canonical(model_id),
        "prompt_usd_per_m": float((rates or {}).get("prompt_usd_per_m") or 0.0),
        "completion_usd_per_m": float((rates or {}).get("completion_usd_per_m") or 0.0),
        "rates_known": rates is not None,
        "cost_state": str(cost_state or "unknown"),
        "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    records = _load()
    records[_key(provider_id, model_id)] = record
    _save(records)
    return record


def price_above_acceptance(provider_id: str, model_id: str) -> dict[str, Any] | None:
    """The blocking evidence when discovery moved ABOVE the recorded ceiling, else None.

    None means: no acceptance recorded, no readable current rates, or current <= accepted —
    in every one of those the ORDINARY paid-confirm path is the right gate. Only a genuine
    rise re-gates with the before/after the operator must see. Decreases never block.
    """
    accepted = acceptance_for(provider_id, model_id)
    if not accepted or not accepted.get("rates_known"):
        return None
    current = current_rates_for(provider_id, model_id)
    if current is None:
        return None
    accepted_prompt = float(accepted.get("prompt_usd_per_m") or 0.0)
    accepted_completion = float(accepted.get("completion_usd_per_m") or 0.0)
    rose = (
        current["prompt_usd_per_m"] > accepted_prompt * (1 + _RISE_TOLERANCE)
        or current["completion_usd_per_m"] > accepted_completion * (1 + _RISE_TOLERANCE)
    )
    if not rose:
        return None
    return {"accepted": dict(accepted), "current": dict(current)}


__all__ = [
    "acceptance_for",
    "current_rates_for",
    "list_acceptances",
    "price_above_acceptance",
    "record_acceptance",
]
