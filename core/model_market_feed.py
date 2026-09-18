"""The model-market feed: typed events and price history from real catalog refreshes.

The design authority's notification centre ("MODEL MARKET") needs a truthful source: this module
DIFFS each live catalog refresh against its own last snapshot and appends typed events —
`price_increased`, `price_decreased`, `free_to_paid`, `paid_to_free`, `model_delisted`,
`new_free_model` (one FULLY-IDENTIFIED event per model, deduplicated) — plus a bounded per-model
price history. Nothing here polls, invents, or extrapolates: no refresh, no events; a fresh
install has an empty feed and the UI says so.

Scope is the WATCHED set for per-row price events, so a 400-model catalog cannot spam the
operator: the currently pinned model, every model with a recorded acceptance
(core/model_price_acceptance.py), and — as fully-identified per-model events, never a bare
count — models observed becoming free. Each `new_free_model` event carries its own snapshot
(human name, id, provider, observed prices, free basis, context, capabilities, evidence time
and source), so a later catalog refresh can never relabel an old alert. Event rows are
append-only JSONL with a monotonic `seq`; history keeps the last 50 observations per watched
model.

Fail direction: any storage or diff failure is swallowed and the refresh result is returned
untouched — the market feed may never break catalog refreshing itself.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

from core.runtime_paths import data_path

_SNAPSHOT_NAME = "model_market_snapshot.json"
_EVENTS_NAME = "model_market_events.jsonl"
_HISTORY_NAME = "model_price_history.json"
_EVENTS_CAP = 200
_HISTORY_CAP = 50
_RISE_TOLERANCE = 0.005


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(name: str, default):
    try:
        with open(data_path(name), encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default


def _write_json(name: str, value) -> None:
    path = data_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _watched_ids() -> set[str]:
    watched: set[str] = set()
    try:
        from core import cloud_escalation_policy

        pinned = str(cloud_escalation_policy.load_policy().model or "").strip().lower()
        if pinned:
            watched.add(pinned)
    except Exception:
        pass
    try:
        from core.model_price_acceptance import list_acceptances

        for row in list_acceptances():
            model = str(row.get("model") or "").strip().lower()
            if model:
                watched.add(model)
    except Exception:
        pass
    return watched


def _rows_snapshot(models) -> dict[str, dict[str, Any]]:
    from core.openrouter_catalog import model_is_free

    out: dict[str, dict[str, Any]] = {}
    for m in models:
        model_id = str(getattr(m, "model_id", "") or "").strip().lower()
        if not model_id:
            continue
        prompt = round((getattr(m, "prompt_usd_per_token", 0.0) or 0.0) * 1_000_000, 4)
        completion = round((getattr(m, "completion_usd_per_token", 0.0) or 0.0) * 1_000_000, 4)
        context_length = max(0, int(getattr(m, "context_length", 0) or 0))
        supported = tuple(getattr(m, "supported_parameters", ()) or ())
        in_mods = tuple(getattr(m, "input_modalities", ()) or ())
        # The honest basis for a "free" reading, stated WITH the observation: the provider's
        # own :free variant, or every published price an explicit zero. Anything else the
        # catalog means by free is recorded as unspecified — never silently certified.
        if model_id.endswith(":free"):
            basis = "provider_free_variant"
        elif prompt == 0.0 and completion == 0.0:
            basis = "all_published_prices_zero"
        else:
            basis = ""
        out[model_id] = {
            "prompt_usd_per_m": prompt,
            "completion_usd_per_m": completion,
            "free": bool(model_is_free(m)),
            # Identification snapshot for per-model events: the alert keeps its OWN copy of
            # what the catalog said AT OBSERVATION TIME, so a later refresh can never relabel
            # an old alert. Missing rows stay honestly absent (the UI says unavailable).
            "display_name": str(getattr(m, "name", "") or ""),
            "context_length": context_length,
            "supports_tools": "tools" in supported,
            "supports_images": "image" in in_mods,
            "free_basis": basis if bool(model_is_free(m)) else "",
        }
    return out


def _append_events(new_events: list[dict[str, Any]]) -> None:
    if not new_events:
        return
    path = data_path(_EVENTS_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_events(after=0)
    seq = max((int(e.get("seq") or 0) for e in existing), default=0)
    with open(path, "a", encoding="utf-8") as handle:
        for event in new_events:
            seq += 1
            event = {"seq": seq, "ts": _now(), **event}
            handle.write(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n")
    combined = existing and (existing + new_events) or new_events
    if len(combined) > _EVENTS_CAP:
        keep = read_events(after=0)[-_EVENTS_CAP:]
        tmp_lines = "".join(
            json.dumps(e, separators=(",", ":"), ensure_ascii=False) + "\n" for e in keep
        )
        fd, tmp = tempfile.mkstemp(prefix=_EVENTS_NAME + ".", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(tmp_lines)
        os.replace(tmp, path)


def read_events(*, after: int = 0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with open(data_path(_EVENTS_NAME), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if isinstance(event, dict) and int(event.get("seq") or 0) > after:
                    out.append(event)
    except Exception:
        return out
    return out


#: Per-refresh bound on new-free alerts. The events log itself is capped at _EVENTS_CAP; this
#: keeps one catalog flip from flooding the inbox in a single poll.
_NEW_FREE_PER_REFRESH_CAP = 50

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _already_alerted_free(events: list[dict[str, Any]], model_id: str, row: dict[str, Any]) -> bool:
    """Dedupe REPEATED observations of the same free arrangement without hiding real changes.

    A `new_free_model` alert already in the log for the same model with the SAME observed
    prices and free basis is the same news: a refresh storm or a catalog row that flickered
    out and back must not re-ring the bell. A materially different price/basis is genuinely
    new news and is emitted.
    """
    for event in reversed(events):
        if event.get("type") != "new_free_model":
            continue
        if str(event.get("model") or "") != model_id:
            continue
        prices = event.get("prices") or {}
        same = (
            prices.get("input_usd_per_m") == row["prompt_usd_per_m"]
            and prices.get("output_usd_per_m") == row["completion_usd_per_m"]
            and str(event.get("free_basis") or "") == str(row.get("free_basis") or "")
        )
        return same
    return False


def price_history(model_id: str) -> list[dict[str, Any]]:
    history = _read_json(_HISTORY_NAME, {})
    rows = history.get(str(model_id or "").strip().lower())
    return list(rows) if isinstance(rows, list) else []


def record_catalog_refresh(models) -> list[dict[str, Any]]:
    """Diff this refresh against the last snapshot; append typed events + history. Best-effort."""
    try:
        current = _rows_snapshot(models)
        previous = _read_json(_SNAPSHOT_NAME, {})
        watched = _watched_ids()
        events: list[dict[str, Any]] = []

        if previous:
            for model_id in sorted(watched):
                before = previous.get(model_id)
                after_row = current.get(model_id)
                if before and not after_row:
                    events.append({"type": "model_delisted", "model": model_id})
                    continue
                if not before or not after_row:
                    continue
                if before["free"] and not after_row["free"]:
                    events.append({"type": "free_to_paid", "model": model_id,
                                   "before": before, "after": after_row})
                elif after_row["free"] and not before["free"]:
                    events.append({"type": "paid_to_free", "model": model_id,
                                   "before": before, "after": after_row})
                elif (
                    after_row["prompt_usd_per_m"] > before["prompt_usd_per_m"] * (1 + _RISE_TOLERANCE)
                    or after_row["completion_usd_per_m"]
                    > before["completion_usd_per_m"] * (1 + _RISE_TOLERANCE)
                ):
                    events.append({"type": "price_increased", "model": model_id,
                                   "before": before, "after": after_row})
                elif (
                    after_row["prompt_usd_per_m"] < before["prompt_usd_per_m"] * (1 - _RISE_TOLERANCE)
                    or after_row["completion_usd_per_m"]
                    < before["completion_usd_per_m"] * (1 - _RISE_TOLERANCE)
                ):
                    events.append({"type": "price_decreased", "model": model_id,
                                   "before": before, "after": after_row})
            newly_free = [
                model_id
                for model_id, row in current.items()
                if row["free"] and model_id in previous and not previous[model_id]["free"]
            ]
            brand_new_free = [
                model_id for model_id, row in current.items() if row["free"] and model_id not in previous
            ]
            fresh = sorted(set(newly_free) | set(brand_new_free))
            if fresh:
                # ONE FULLY-IDENTIFIED EVENT PER MODEL (product/desktop-usability-20260917):
                # the old aggregate ("N new free models") named nothing — no human name, no
                # provider, no observed prices, nothing to inspect. Each event now carries its
                # own snapshot of what the catalog said at observation time.
                existing = read_events(after=0)
                stamp = _now()
                for model_id in fresh[:_NEW_FREE_PER_REFRESH_CAP]:
                    row = current[model_id]
                    if _already_alerted_free(existing, model_id, row):
                        continue  # the same free arrangement was already reported
                    events.append({
                        "type": "new_free_model",
                        "model": model_id,
                        "display_name": row.get("display_name") or "",
                        "provider_id": "openrouter",
                        "prices": {
                            "input_usd_per_m": row["prompt_usd_per_m"],
                            "output_usd_per_m": row["completion_usd_per_m"],
                        },
                        # The observed basis, never a certification: a :free variant or an
                        # explicit zero on every published price is what was SEEN.
                        "free_basis": row.get("free_basis") or "unspecified",
                        "context_length": row.get("context_length") or 0,
                        "supports_tools": bool(row.get("supports_tools")),
                        "supports_images": bool(row.get("supports_images")),
                        "evidence_url": _OPENROUTER_MODELS_URL,
                        "source_feed": "openrouter_catalog",
                        "observed_at": stamp,
                    })

        history = _read_json(_HISTORY_NAME, {})
        stamp = _now()
        for model_id in sorted(watched):
            row = current.get(model_id)
            if not row:
                continue
            rows = history.get(model_id)
            if not isinstance(rows, list):
                rows = []
            last = rows[-1] if rows else None
            if not last or (
                last.get("prompt_usd_per_m") != row["prompt_usd_per_m"]
                or last.get("completion_usd_per_m") != row["completion_usd_per_m"]
                or bool(last.get("free")) != row["free"]
            ):
                rows.append({"ts": stamp, **row})
            history[model_id] = rows[-_HISTORY_CAP:]
        _write_json(_HISTORY_NAME, history)
        _write_json(_SNAPSHOT_NAME, current)
        _append_events(events)
        return events
    except Exception:
        return []


__all__ = ["price_history", "read_events", "record_catalog_refresh"]
