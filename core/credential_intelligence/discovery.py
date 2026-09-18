"""Provider capability discovery — assertions with provenance, freshness and honest invalidation.

What this module owns (Task 01's generic discovery scope; UsePod specifics stay Task 02's):

* **An assertion is evidence, not a guess.** Every model row carries where it came from
  (``evidence``: a live keyed observation, a documented static fact, or explicitly ``unknown``),
  when it was last observed, and when that observation expires. Registry presence is never a
  live observation; a discovered public model catalogue does not validate a key.
* **Assertions bind to generations.** An assertion records a one-way digest of the key and of
  the resolved endpoint it was observed under. A key OR base-URL change invalidates the
  capability evidence — previous rows degrade to ``unknown`` rather than silently remaining
  "available" for a combination nobody observed — and a refresh that races a settings change
  (the generations moved while the requests were in flight) is DISCARDED, never written over
  the newer settings.
* **Refresh never destroys.** A provider outage during refresh keeps the last catalogue with
  the refusal's own name in ``last_refresh_status``. A model that disappeared from a fresh
  listing is marked ``missing`` and retained — the operator still sees what was there, labelled
  as gone — never silently deleted.
* **Both documented shapes.** The OpenAI models list (``id``, ``context_length``,
  ``top_provider.max_completion_tokens``, ``supported_parameters``, ``architecture``) and the
  Anthropic models list (``display_name``, ``max_input_tokens``, ``max_tokens``,
  ``capabilities``) parse into the same rows; anything the provider did not publish stays
  unknown instead of being invented. Web-search providers bind capabilities, not models, and
  are out of scope here by design.

Persistence is one JSON file per data dir (``discovery_assertions.json``), keyed by provider —
plain restart keeps everything. No key material is ever stored: generations are digests and the
endpoint fingerprint is scheme://host[:port] with no path or query (a token path stays secret).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

DISCOVERY_FILE = "discovery_assertions.json"

EVIDENCE_OBSERVED = "observed"
EVIDENCE_DOCUMENTED = "documented"
EVIDENCE_UNKNOWN = "unknown"

MODEL_AVAILABLE = "available"
MODEL_MISSING = "missing"

REFRESH_TTL_SECONDS = 24 * 3600.0
_MODELS_TIMEOUT_S = 15.0
_MAX_LIST_BYTES = 4_000_000


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _origin_of(url: str) -> str:
    """scheme://host[:port] — no path or query, which may carry a token."""
    try:
        parts = urlsplit(str(url or ""))
        if not parts.scheme or not parts.hostname:
            return ""
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}"
    except Exception:
        return ""


def _models_url(verify_endpoint: str) -> str:
    """The models list next to the verification endpoint: a path ending in ``models`` is used
    as is; any other final segment (OpenRouter's ``/api/v1/key``) is replaced by ``models``."""
    parts = urlsplit(str(verify_endpoint or ""))
    segments = [segment for segment in str(parts.path or "").split("/") if segment]
    if not segments or segments[-1] != "models":
        segments = [*(segments[:-1] or []), "models"]
    path = "/" + "/".join(segments)
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme}://{parts.netloc}{path}{query}"


@dataclass(frozen=True)
class DiscoveredModel:
    model_id: str
    display_name: str
    context_window: int = 0
    max_output_tokens: int = 0
    capabilities: tuple[str, ...] = ()
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    status: str = MODEL_AVAILABLE
    evidence: str = EVIDENCE_UNKNOWN
    first_seen_at: str = ""
    last_observed_at: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["capabilities"] = list(self.capabilities)
        payload["input_modalities"] = list(self.input_modalities)
        payload["output_modalities"] = list(self.output_modalities)
        return payload


@dataclass(frozen=True)
class DiscoveryAssertion:
    provider_id: str
    #: scheme://host[:port] of the endpoint the observation belongs to. Never a token path.
    endpoint_fingerprint: str
    #: One-way generations this evidence binds to; a change invalidates the capabilities.
    key_generation: str
    endpoint_generation: str
    account: str = ""
    protocol: str = ""            # "openai_models_list" | "anthropic_models_list" | ""
    evidence: str = EVIDENCE_UNKNOWN
    source: str = ""              # "model_list_endpoint" when observed live
    observed_at: str = ""
    expires_at: str = ""
    models: tuple[DiscoveredModel, ...] = ()
    #: The last refresh attempt's outcome: "verified"/"ok", a verifier refusal status, "no_key".
    last_refresh_status: str = ""
    last_refresh_at: str = ""
    refreshed_generations_match: bool = True
    #: True when the listing was paginated and only part of it was scanned: absence beyond the
    #: scanned page was never inferred, so previously seen models keep their state.
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["models"] = [m.to_dict() for m in self.models]
        return payload

    @property
    def fresh(self) -> bool:
        if not self.observed_at or self.evidence != EVIDENCE_OBSERVED:
            return False
        try:
            return datetime.now(timezone.utc) < datetime.strptime(self.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return False


# ------------------------------------------------------------------ persistence

def _state_path() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / DISCOVERY_FILE


def load_assertions() -> dict[str, DiscoveryAssertion]:
    rows = _read_rows(_state_path())
    out: dict[str, DiscoveryAssertion] = {}
    for pid, row in rows.items():
        assertion = _row_to_assertion(str(pid), row)
        if assertion is not None:
            out[str(pid)] = assertion
    return out


def save_assertion(assertion: DiscoveryAssertion, *, expect_generations: tuple[str, str] | None = None) -> bool:
    """Atomically merge one provider's assertion into the file.

    The whole read-modify-write runs under the cross-process state lock: two concurrent saves
    for DIFFERENT providers both survive (atomic ``os.replace`` alone made each write a lost
    update -- measured by the 2026-09-15 review probe), and so do concurrent drops and
    invalidations.

    ``expect_generations`` turns the write into a compare-and-set on the provider's stored
    key/endpoint generations: when the stored row already carries DIFFERENT generations (a
    newer settings change or completed refresh won), this write is stale and is DISCARDED
    (returns False) instead of overwriting newer state. ``None`` writes unconditionally (the
    operator-facing paths that already hold their own generation guard)."""
    from core.credential_intelligence._state_lock import state_lock

    path = _state_path()
    with state_lock(path):
        rows = _read_rows(path)
        current = rows.get(assertion.provider_id)
        if expect_generations is not None and isinstance(current, dict):
            stored = (str(current.get("key_generation") or ""), str(current.get("endpoint_generation") or ""))
            if stored != tuple(expect_generations):
                return False
        rows[assertion.provider_id] = assertion.to_dict()
        _write_rows(path, rows)
    return True


def drop_assertion(provider_id: str) -> None:
    from core.credential_intelligence._state_lock import state_lock

    path = _state_path()
    with state_lock(path):
        rows = _read_rows(path)
        if rows.pop(str(provider_id), None) is not None:
            _write_rows(path, rows)


def _read_rows(path: Path) -> dict[str, dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_rows(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(rows, sort_keys=True))
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


# ------------------------------------------------------------------ parsing (both documented shapes)

def _int_or_zero(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def listing_truncated(payload: object) -> bool:
    """Whether the provider said the listing continues beyond this page (Anthropic's
    ``has_more``; an OpenAI-style ``next_page`` cursor). A truncated listing is NOT coverage:
    absence beyond the scanned page is never inferred from it."""
    if not isinstance(payload, dict):
        return False
    if payload.get("has_more") is True:
        return True
    next_page = payload.get("next_page")
    return bool(isinstance(next_page, str) and next_page.strip())


def parse_models_list(payload: object, *, expected_protocol: str = "openai_models_list") -> tuple[tuple[DiscoveredModel, ...], str, int]:
    """One provider models-list body -> rows + the protocol they parsed under.

    The dialect comes from the CONFIGURED contract (``expected_protocol``, carried by the
    provider table through the descriptor), never inferred from row keys: a body that does not
    speak the configured dialect makes the listing unreadable ("" protocol), which the caller
    reports as itself. Unpublished fields stay unknown (0 / empty tuples) -- in particular a
    row with no architecture/modality block yields UNKNOWN modalities, never "text": those
    capabilities were not published.

    Returns (rows, protocol, unreadable_rows). ``unreadable_rows`` counts entries the page
    carried that could not be read as models (nulls, non-dicts, id-less rows): a page with
    any is CORRUPT/INCOMPLETE coverage, never authoritative evidence that absent models are
    gone -- ``{data:[null,{}]}`` and a genuinely empty ``{data:[]}`` must never share a
    meaning (review F3)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return (), "", 0
    rows: list[DiscoveredModel] = []
    unreadable = 0
    now = _utcnow()
    if expected_protocol == "anthropic_models_list":
        for item in payload["data"]:
            if not isinstance(item, dict):
                unreadable += 1
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                unreadable += 1
                continue
            capabilities_obj = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
            rows.append(DiscoveredModel(
                model_id=model_id,
                display_name=str(item.get("display_name") or model_id),
                context_window=_int_or_zero(item.get("max_input_tokens")),
                max_output_tokens=_int_or_zero(item.get("max_tokens")),
                capabilities=tuple(sorted(
                    name for name, spec in capabilities_obj.items()
                    if isinstance(spec, dict) and spec.get("supported") is True
                )),
                # unknown until the capabilities block publishes them
                input_modalities=tuple(sorted(
                    name for name, spec in capabilities_obj.items()
                    if isinstance(spec, dict) and spec.get("supported") and "input" in str(name)
                )),
                output_modalities=tuple(sorted(
                    name for name, spec in capabilities_obj.items()
                    if isinstance(spec, dict) and spec.get("supported") and "output" in str(name)
                )),
                status=MODEL_AVAILABLE,
                evidence=EVIDENCE_OBSERVED,
                first_seen_at=now,
                last_observed_at=now,
            ))
        return tuple(rows), "anthropic_models_list", unreadable
    for item in payload["data"]:
        if not isinstance(item, dict):
            unreadable += 1
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            unreadable += 1
            continue
        if "display_name" in item or "max_tokens" in item or item.get("type") == "model":
            # a first-party anthropic row under an openai-configured contract (or vice versa):
            # the provider is not speaking its documented dialect here
            return (), "", 0
        architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
        top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
        rows.append(DiscoveredModel(
            model_id=model_id,
            display_name=str(item.get("name") or model_id),
            context_window=_int_or_zero(item.get("context_length")),
            max_output_tokens=_int_or_zero(top_provider.get("max_completion_tokens")),
            capabilities=_strings(item.get("supported_parameters")),
            # unknown until the architecture block publishes them
            input_modalities=_strings(architecture.get("input_modalities")),
            output_modalities=_strings(architecture.get("output_modalities")),
            status=MODEL_AVAILABLE,
            evidence=EVIDENCE_OBSERVED,
            first_seen_at=now,
            last_observed_at=now,
        ))
    return tuple(rows), "openai_models_list", unreadable


# ------------------------------------------------------------------ refresh

def _resolve_key(descriptor) -> str:
    """The key this provider's lane would use: env aliases first, then the encrypted store."""
    from core.cloud_providers import key_env_names

    for name in key_env_names(_bare_provider_id(descriptor.provider_id)):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    try:
        from core import credential_store

        return str(credential_store.get_credential(descriptor.credential_slot) or "").strip()
    except Exception:
        return ""


def _bare_provider_id(provider_id: str) -> str:
    """search.<id> ids keep their table key without the prefix this layer added."""
    return provider_id.removeprefix("search.")


def _generations(provider_id: str, descriptor, key: str) -> tuple[str, str]:
    """(key generation, endpoint generation) an assertion binds to."""
    from core.cloud_providers import resolved_base_url

    base = resolved_base_url(_bare_provider_id(provider_id))
    if not base:
        base = _origin_of(descriptor.verify_endpoint)
    return _digest(key), _digest(base)


def invalidate_stale_capabilities(provider_id: str, *, key_generation: str, endpoint_generation: str) -> DiscoveryAssertion | None:
    """After a key or base change, previous capability evidence is not silently retained: every
    model row degrades to ``unknown`` evidence (ids kept, honestly unobserved for the new
    combination), and the assertion records that its generations no longer match. The whole
    read-compare-write is one locked section, so it cannot lose a concurrent provider's row."""
    from core.credential_intelligence._state_lock import state_lock

    path = _state_path()
    with state_lock(path):
        rows = _read_rows(path)
        current = _row_to_assertion(provider_id, rows.get(provider_id))
        if current is None:
            return None
        if (current.key_generation, current.endpoint_generation) == (key_generation, endpoint_generation):
            return current
        degraded = DiscoveryAssertion(
            provider_id=provider_id,
            endpoint_fingerprint=current.endpoint_fingerprint,
            key_generation=key_generation,
            endpoint_generation=endpoint_generation,
            account="",
            protocol="",
            evidence=EVIDENCE_UNKNOWN,
            source=current.source,
            observed_at="",
            expires_at="",
            models=tuple(replace(m, evidence=EVIDENCE_UNKNOWN, status=MODEL_MISSING if m.status == MODEL_AVAILABLE else m.status) for m in current.models),
            last_refresh_status=current.last_refresh_status,
            last_refresh_at=current.last_refresh_at,
            refreshed_generations_match=False,
        )
        rows[provider_id] = degraded.to_dict()
        _write_rows(path, rows)
        return degraded


def _row_to_assertion(provider_id: str, row: object) -> DiscoveryAssertion | None:
    if not isinstance(row, dict):
        return None
    models = tuple(
        DiscoveredModel(
            model_id=str(m.get("model_id") or ""),
            display_name=str(m.get("display_name") or ""),
            context_window=int(m.get("context_window") or 0),
            max_output_tokens=int(m.get("max_output_tokens") or 0),
            capabilities=tuple(m.get("capabilities") or ()),
            input_modalities=tuple(m.get("input_modalities") or ()),
            output_modalities=tuple(m.get("output_modalities") or ()),
            status=str(m.get("status") or MODEL_AVAILABLE),
            evidence=str(m.get("evidence") or EVIDENCE_UNKNOWN),
            first_seen_at=str(m.get("first_seen_at") or ""),
            last_observed_at=str(m.get("last_observed_at") or ""),
        )
        for m in row.get("models") or [] if isinstance(m, dict) and str(m.get("model_id") or "").strip()
    )
    return DiscoveryAssertion(
        provider_id=str(row.get("provider_id") or provider_id),
        endpoint_fingerprint=str(row.get("endpoint_fingerprint") or ""),
        key_generation=str(row.get("key_generation") or ""),
        endpoint_generation=str(row.get("endpoint_generation") or ""),
        account=str(row.get("account") or ""),
        protocol=str(row.get("protocol") or ""),
        evidence=str(row.get("evidence") or EVIDENCE_UNKNOWN),
        source=str(row.get("source") or ""),
        observed_at=str(row.get("observed_at") or ""),
        expires_at=str(row.get("expires_at") or ""),
        models=models,
        last_refresh_status=str(row.get("last_refresh_status") or ""),
        last_refresh_at=str(row.get("last_refresh_at") or ""),
        refreshed_generations_match=bool(row.get("refreshed_generations_match", True)),
        truncated=bool(row.get("truncated", False)),
    )


def _merge_models_url(built_url: str, models_url: str) -> str:
    """The request the verifier builds (its query params, if any) re-pointed at the models path."""
    from urllib.parse import urlsplit, urlunsplit

    built = urlsplit(built_url)
    models = urlsplit(models_url)
    query = f"?{built.query}" if built.query else ""
    return urlunsplit((models.scheme, models.netloc, models.path, "", "")) + query


def _fetch_models(descriptor, key: str, *, timeout_s: float = _MODELS_TIMEOUT_S) -> tuple[int, bytes, object]:
    """One keyed models-list request built exactly like the verifier builds its own: the same
    auth placement, the same unredirected key header, the one outbound door, proxy-free."""
    import urllib.error

    from core.credential_intelligence.verification import _credential_header_names, _place_credential
    from core.remote_fetch_policy import open_remote_url

    placed = _place_credential(key, descriptor)
    if placed is None:
        raise RuntimeError("unsupported key placement")
    _verify_url, headers, data = placed
    # The key rides the request exactly as the verifier places it; the models list is the
    # sibling of the verification path (/api/v1/key -> /api/v1/models).
    url = _merge_models_url(_verify_url, _models_url(descriptor.verify_endpoint))
    try:
        response = open_remote_url(
            url,
            data=data,
            headers=headers,
            method=descriptor.verify_method,
            timeout=timeout_s,
            no_proxy=True,
            provider_id=descriptor.provider_id,
            keyed_or_keyless="keyed",
            credential_headers=_credential_header_names(descriptor),
        )
    except urllib.error.HTTPError as exc:
        response = exc
    status = int(getattr(response, "status", None) or getattr(response, "code", None) or 0)
    try:
        body = response.read(_MAX_LIST_BYTES) or b""
    except Exception:
        body = b""
    return status, body, getattr(response, "headers", None)


def refresh_discovery(provider_id: str, *, registry=None, timeout_s: float = _MODELS_TIMEOUT_S) -> DiscoveryAssertion | None:
    """Re-verify the stored key against the provider's own descriptor, then observe the models
    list under that verified key. Search providers bind capabilities, not models: refused here.

    Laws enforced end to end: the public catalogue alone never verifies anything (verification
    runs first); every outcome -- verified, refused, no key, unreadable listing -- is written
    through a compare-and-set on the generations observed when the refresh STARTED, so a
    settings change or a newer completed refresh that lands anywhere along the way makes this
    result discard itself instead of overwriting newer state; an inconclusive verification
    keeps the previous catalogue and records the refusal's own name; an AUTHORITATIVE empty
    complete listing marks every previously available model missing (the provider said the
    catalogue is empty), while a TRUNCATED (paginated) listing never infers absence beyond the
    scanned page; and the listing is parsed under the protocol the provider's configured
    contract documents, never guessed from row keys."""
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.verification import verify_provider_credential

    table = registry if registry is not None else default_registry()
    descriptor = table.get(provider_id)
    if descriptor is None:
        return None
    if descriptor.kind != "llm_cloud":
        return load_assertions().get(provider_id)
    key = _resolve_key(descriptor)
    key_generation, endpoint_generation = _generations(provider_id, descriptor, key)
    observed = (key_generation, endpoint_generation)
    now = _utcnow()

    def _record(**fields: object) -> DiscoveryAssertion:
        previous = load_assertions().get(provider_id)
        keep = fields.pop("keep_previous", False)
        base: dict[str, object] = {
            "provider_id": provider_id,
            "endpoint_fingerprint": _origin_of(descriptor.verify_endpoint),
            "key_generation": key_generation,
            "endpoint_generation": endpoint_generation,
        }
        if keep and previous is not None:
            base.update({
                "account": previous.account,
                "protocol": previous.protocol,
                "evidence": previous.evidence,
                "source": previous.source,
                "observed_at": previous.observed_at,
                "expires_at": previous.expires_at,
                "models": previous.models,
                "truncated": previous.truncated,
            })
        base.update(fields)
        assertion = DiscoveryAssertion(**base)  # type: ignore[arg-type]
        # The one write boundary: compare-and-set on the generations this refresh observed.
        saved = save_assertion(assertion, expect_generations=observed)
        return assertion if saved else load_assertions().get(provider_id)

    if not key:
        previous = invalidate_stale_capabilities(provider_id, key_generation=key_generation, endpoint_generation=endpoint_generation)
        return _record(
            models=previous.models if previous else (),
            evidence=previous.evidence if previous and previous.refreshed_generations_match else EVIDENCE_UNKNOWN,
            observed_at=previous.observed_at if previous and previous.refreshed_generations_match else "",
            expires_at=previous.expires_at if previous and previous.refreshed_generations_match else "",
            last_refresh_status="no_key",
            last_refresh_at=now,
            refreshed_generations_match=False,
        )
    # Invalidate first: an observation for older generations can never survive a key/base change.
    invalidate_stale_capabilities(provider_id, key_generation=key_generation, endpoint_generation=endpoint_generation)

    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("discovery.refresh"):
        outcome = verify_provider_credential(key, descriptor, scope="discovery.refresh")
        if outcome.status != "verified":
            return _record(keep_previous=True, last_refresh_status=outcome.status, last_refresh_at=now,
                           refreshed_generations_match=True)
        status, body, _headers = _fetch_models(descriptor, key, timeout_s=timeout_s)

    # Race guard: if the settings moved while the requests were in flight, this observation
    # belongs to a combination nobody configured anymore -- _record's compare-and-set discards it.
    if not 200 <= status < 300:
        from core.credential_intelligence.verification import classify_verification_response

        judged = classify_verification_response(descriptor, status=status, body=body)
        return _record(keep_previous=True, account=outcome.account,
                       last_refresh_status=judged.status, last_refresh_at=now, refreshed_generations_match=True)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        payload = None
    expected_protocol = str(getattr(descriptor, "models_protocol", "") or "openai_models_list")
    observed_rows, protocol, unreadable = (
        parse_models_list(payload, expected_protocol=expected_protocol) if payload is not None else ((), "", 0))
    if protocol != expected_protocol:
        # not the configured dialect (or not a models list at all): named, never guessed
        return _record(keep_previous=True, account=outcome.account,
                       last_refresh_status="malformed_response" if payload is None else "protocol_mismatch",
                       last_refresh_at=now, refreshed_generations_match=True)
    truncated = listing_truncated(payload)
    incomplete = truncated or unreadable > 0

    previous = load_assertions().get(provider_id)
    previous_rows = {m.model_id: m for m in previous.models} if previous else {}
    now2 = _utcnow()
    expires = datetime.fromtimestamp(time.time() + REFRESH_TTL_SECONDS, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not observed_rows and not previous_rows:
        # nothing before, nothing now: an honestly empty observed catalogue -- unless the page
        # was corrupt/paginated, which is named as itself, never presented as emptiness
        clean = not incomplete
        status = "empty_catalogue" if clean else ("unreadable_rows" if unreadable else "truncated")
        return _record(account=outcome.account, protocol=protocol,
                       evidence=EVIDENCE_OBSERVED if clean else EVIDENCE_UNKNOWN,
                       source="model_list_endpoint" if clean else "",
                       observed_at=now2 if clean else "", expires_at=expires if clean else "",
                       models=(), last_refresh_status=status, last_refresh_at=now2,
                       refreshed_generations_match=True, truncated=truncated)
    if not observed_rows and not incomplete:
        # AUTHORITATIVE empty complete listing: the provider says the catalogue is empty. Every
        # previously available model is gone (missing), never silently retained as available.
        gone = tuple(replace(m, status=MODEL_MISSING) for m in (previous.models if previous else ()))
        return _record(account=outcome.account, protocol=protocol, evidence=EVIDENCE_OBSERVED,
                       source="model_list_endpoint", observed_at=now2, expires_at=expires, models=gone,
                       last_refresh_status="empty_catalogue", last_refresh_at=now2,
                       refreshed_generations_match=True, truncated=False)
    if incomplete:
        # PARTIAL coverage (pagination, or a page carrying unreadable rows): scanned rows
        # update; absence beyond the readable coverage is NOT inferred, so previously seen
        # models keep their state and the assertion says exactly why it is incomplete
        merged = [replace(row, first_seen_at=previous_rows[row.model_id].first_seen_at)
                  if row.model_id in previous_rows else row for row in observed_rows]
        seen = {row.model_id for row in merged}
        merged.extend(old for _old_id, old in sorted(previous_rows.items()) if _old_id not in seen)
        return _record(account=outcome.account, protocol=protocol, evidence=EVIDENCE_OBSERVED,
                       source="model_list_endpoint", observed_at=now2, expires_at=expires, models=tuple(merged),
                       last_refresh_status="unreadable_rows" if unreadable else "truncated",
                       last_refresh_at=now2, refreshed_generations_match=True, truncated=truncated)

    merged: list[DiscoveredModel] = []
    for row in observed_rows:
        old = previous_rows.get(row.model_id)
        merged.append(replace(row, first_seen_at=old.first_seen_at if old else row.first_seen_at))
    seen = {row.model_id for row in merged}
    for old_id, old_row in sorted(previous_rows.items()):
        if old_id not in seen:
            merged.append(replace(old_row, status=MODEL_MISSING))
    return _record(account=outcome.account, protocol=protocol, evidence=EVIDENCE_OBSERVED,
                   source="model_list_endpoint", observed_at=now2, expires_at=expires, models=tuple(merged),
                   last_refresh_status="verified", last_refresh_at=now2,
                   refreshed_generations_match=True, truncated=False)


# ------------------------------------------------------------------ projections

def _live_generations(provider_id: str, descriptor) -> tuple[str, str]:
    """The generations the provider's lane would use RIGHT NOW (offline: env aliases, the
    encrypted store, the resolved base URL). Comparing them with a stored assertion's
    generations is what keeps the snapshot honest without any network: a key deleted, rotated
    or repointed, or a base URL change, degrades the projected evidence immediately instead of
    waiting for the next refresh."""
    return _generations(provider_id, descriptor, _resolve_key(descriptor))


def discovery_snapshot(provider_ids: list[str] | None = None, *, registry=None) -> dict[str, dict[str, object]]:
    """The Settings- and selection-facing truth: one entry per provider with provenance,
    freshness, refresh status and the model rows -- missing ones labelled, unobserved ones
    unknown. Generation freshness is evaluated LIVE (no network, no refresh): when the current
    key/base no longer match the generations an assertion was observed under, the projected
    evidence degrades to unknown and every row to unobserved, so stale capability evidence can
    never look fresh after a key or endpoint change -- or after the key was deleted -- without
    an explicit refresh."""
    assertions = load_assertions()
    table = registry
    out: dict[str, dict[str, object]] = {}
    for provider_id, assertion in sorted(assertions.items()):
        if provider_ids is not None and provider_id not in provider_ids:
            continue
        evidence = assertion.evidence
        fresh = assertion.fresh
        generations_match = assertion.refreshed_generations_match
        models = [m.to_dict() for m in assertion.models]
        descriptor = None
        if table is None:
            from core.credential_intelligence.provider_registry import default_registry

            table = default_registry()
        descriptor = table.get(provider_id)
        if descriptor is not None and descriptor.kind == "llm_cloud":
            try:
                live = _live_generations(provider_id, descriptor)
            except Exception:
                live = None
            if live is not None and live != (assertion.key_generation, assertion.endpoint_generation):
                generations_match = False
                fresh = False
                evidence = EVIDENCE_UNKNOWN
                models = [{**m, "evidence": EVIDENCE_UNKNOWN} for m in models]
        out[provider_id] = {
            **{k: v for k, v in assertion.to_dict().items() if k != "models"},
            "evidence": evidence,
            "fresh": fresh,
            "generations_match": generations_match,
            "models": models,
        }
    return out


def observed_capability(provider_id: str, model_id: str, *, registry=None) -> tuple[int, int] | None:
    """(context_window, max_output_tokens) for a model from THIS provider's observed discovery
    evidence, or None when there is none worth using. This is the seam the existing model
    capability authority (``core.model_capability_catalog``) consumes: only a NON-truncated,
    generation-current, unexpired observation counts, and unpublished numbers stay unknown
    (0) rather than guessed. Offline by construction."""
    table = registry
    if table is None:
        from core.credential_intelligence.provider_registry import default_registry

        table = default_registry()
    descriptor = table.get(provider_id)
    if descriptor is None or descriptor.kind != "llm_cloud":
        return None
    assertion = load_assertions().get(provider_id)
    if assertion is None or assertion.truncated or assertion.evidence != EVIDENCE_OBSERVED or not assertion.fresh:
        return None
    if assertion.last_refresh_status in ("unreadable_rows", "truncated", "protocol_mismatch", "malformed_response"):
        # the last observation's coverage was incomplete or unreadable: not evidence a model
        # selection may budget against
        return None
    try:
        if _live_generations(provider_id, descriptor) != (assertion.key_generation, assertion.endpoint_generation):
            return None
    except Exception:
        return None
    wanted = str(model_id or "").strip().lower()
    for m in assertion.models:
        if m.model_id.strip().lower() == wanted and m.status == MODEL_AVAILABLE:
            return (m.context_window, m.max_output_tokens)
    return None


__all__ = [
    "DISCOVERY_FILE",
    "EVIDENCE_DOCUMENTED",
    "EVIDENCE_OBSERVED",
    "EVIDENCE_UNKNOWN",
    "MODEL_AVAILABLE",
    "MODEL_MISSING",
    "DiscoveredModel",
    "DiscoveryAssertion",
    "discovery_snapshot",
    "drop_assertion",
    "invalidate_stale_capabilities",
    "listing_truncated",
    "load_assertions",
    "observed_capability",
    "parse_models_list",
    "refresh_discovery",
    "save_assertion",
]
