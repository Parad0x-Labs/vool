"""Provider-neutral model identity receipts. Response claims are never attestations.

Only a trusted runtime verifier may supply a Validation. Provider JSON, response
headers, model text, and route compliance flags cannot select a verification level.
No upstream attestation verifier is enabled by default.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.response_usage_details import response_usage_details


class VerificationStatus(str, Enum):
    UPSTREAM_VERIFIED = "UPSTREAM_VERIFIED"
    PROVIDER_VERIFIED = "PROVIDER_VERIFIED"
    CLAIMED = "CLAIMED"
    # No response exists at all: the dispatch was refused before anything was sent. Distinct from
    # CLAIMED, which is a statement ABOUT a response (a returned model name is a claim, never
    # proof). With no response there is no claim to report — only the absence of evidence.
    NO_RESPONSE = "NO_RESPONSE"


@dataclass(frozen=True)
class Validation:
    """Result from trusted verifier code, bound to this exact receipt's facts hash.

    Evidence references must identify inspectable upstream evidence or provider
    verification/reputation coverage. Merely echoing a provider's 'verified' field
    is not an implementation of this interface.
    """
    status: VerificationStatus
    binding_sha256: str
    verifier: str
    evidence_references: tuple[str, ...]
    expires_at: float
    explanation: str


# Installed by runtime code only; never populated from request bodies or adapter metadata.
_VERIFIERS: dict[str, Callable[[dict[str, Any], Any], Validation | None]] = {}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or len(text) > 512 or any(c in text for c in ('\n', '\r')):
        return None
    # Only identifiers, labels and public metadata belong here; no URLs carrying
    # credentials, authorization headers, prompts, or arbitrary provider objects.
    if any(s in text.lower() for s in ('bearer ', 'token=', 'api_key=', 'api-key=', '/proxy/')):
        return None
    return text


def response_body_facts(raw: Any) -> dict[str, Any]:
    rows = raw if isinstance(raw, list) else [raw]
    facts: dict[str, Any] = {}
    for row in rows:
        row = _mapping(row)
        # Anthropic stream begins with a message envelope.
        row = _mapping(row.get('message')) if isinstance(row.get('message'), dict) else row
        for key in ('id', 'model', 'provider', 'system_fingerprint', 'created', 'service_tier', 'object'):
            value = _text(row.get(key))
            if value is not None:
                facts[key] = value
    return facts


def response_header_facts(headers: Any) -> dict[str, str]:
    """Retain public request/route identifiers only, never auth or cookie headers."""
    if not isinstance(headers, Mapping):
        return {}
    allowed = {'x-request-id', 'request-id', 'x-generation-id', 'x-pod-provider-id', 'x-pod-route'}
    return {str(k).lower(): _text(v) for k, v in headers.items()
            if str(k).lower() in allowed and _text(v) is not None}


def build_verification_receipt(*, call_id: str, request_id: str, requested_model: str,
                               selected_model: str, provider_id: str, response: Any = None,
                               outcome: str = 'completed', timestamp: float | None = None) -> dict[str, Any]:
    """Malformed response metadata must never discard a usable model answer."""
    args = dict(call_id=call_id, request_id=request_id, requested_model=requested_model,
                selected_model=selected_model, provider_id=provider_id, outcome=outcome)
    try:
        return _build_verification_receipt(**args, response=response, timestamp=timestamp)
    except Exception:
        facts = _build_verification_receipt(**args, response=None)
        facts['metadata_state'] = 'unreadable'
        facts['verification'] = {
            'status': 'CLAIMED', 'verifier': None, 'evidence_references': [],
            'independent_model_proof': False,
            'explanation': 'Response metadata could not be read; model identity is unverified.',
        }
        facts['binding_sha256'] = hashlib.sha256(json.dumps(
            {k: v for k, v in facts.items() if k not in {'binding_sha256', 'verification'}},
            sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
        return facts


def _build_verification_receipt(*, call_id: str, request_id: str, requested_model: str,
                               selected_model: str, provider_id: str, response: Any = None,
                               outcome: str = 'completed', timestamp: float | None = None) -> dict[str, Any]:
    now = time.time() if timestamp is None else timestamp
    metadata = _mapping(getattr(response, 'provider_metadata', None) or getattr(response, 'provider_evidence', None))
    usepod = _mapping(metadata.get('usepod'))
    receipt = _mapping(metadata.get('receipt'))
    body = response_body_facts(usepod.get('response_metadata') or metadata.get('response_metadata'))
    body.update(response_body_facts(getattr(response, 'raw_response', None)))
    returned = _text(getattr(response, 'provider_attested_model', None)) or body.get('model') or _text(usepod.get('provider_attested_model'))
    route = _mapping(receipt.get('route'))
    price_policy = _mapping(usepod.get('route_policy'))
    cost = response_usage_details(response)
    safe_headers = response_header_facts(usepod.get('response_headers') or metadata.get('response_headers'))
    upstream_id = body.get('id') or safe_headers.get('x-request-id') or safe_headers.get('request-id')
    facts = {
        'schema': 'vool.provider-verification.v1',
        'call_id': _text(call_id), 'request_id': _text(request_id),
        'timestamp': datetime.fromtimestamp(now, timezone.utc).isoformat(),
        'outcome': outcome,
        'requested_model': _text(requested_model), 'selected_model': _text(selected_model),
        'returned_model': returned,
        'model_match': 'unreported' if returned is None else ('matches_selected' if returned == selected_model else 'different_identifier'),
        'gateway_provider_id': _text(provider_id),
        'provider_id': _text(route.get('provider_id')) or body.get('provider') or safe_headers.get('x-pod-provider-id'),
        'route': _text(route.get('class')) or safe_headers.get('x-pod-route'),
        'provider_request_id': upstream_id,
        'operation_id': _text(receipt.get('operation_id')),
        'quoted_price': {
            'state': 'recorded_listing_snapshot' if price_policy.get('eligible_prices') else 'not_reported',
            # These are eligible listing prices, not a claim of the serving provider's exact bill.
            'eligible_prices': _public_prices(price_policy.get('eligible_prices')),
            'approved_ceiling': {k: v for k, v in _mapping(receipt.get('price_ceiling_microunits_per_million')).items() if k in {'input', 'output'} and type(v) is int and v >= 0},
            'unit': 'usdc_microunits_per_million_tokens' if usepod else None,
            'snapshot_sha256': _text(receipt.get('price_snapshot_sha256')),
            'observed_at': _text(_mapping(price_policy.get('price_source')).get('fetched_at')),
        },
        'actual_cost': {'state': 'reported' if cost.get('cost_state') == 'exact' else 'not_reported',
                        'amount': cost.get('amount') if cost.get('cost_state') == 'exact' else None,
                        'currency': cost.get('currency')},
        'cost_bound': {'amount': cost.get('amount') if cost.get('cost_state') == 'upper_bound' else None,
                       'currency': cost.get('currency'), 'state': cost.get('cost_state')},
        'upstream_metadata': {'response': body, 'headers': safe_headers},
        'verification_context': ({
            'source': 'https://docs.usepod.ai/marketplace/trust/',
            'mechanisms': ['provider bonds', 'reputation', 'benchmark canaries'],
            'scope': 'Published marketplace mechanisms; no call-specific verification coverage established.',
        } if usepod else None),
    }
    facts['request_payload_sha256'] = _text(_mapping(usepod.get('envelope')).get('body_sha256'))
    raw_response = getattr(response, 'raw_response', None)
    facts['response_payload_sha256'] = (hashlib.sha256(json.dumps(raw_response, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
                                        if raw_response is not None else None)
    binding = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    facts['binding_sha256'] = binding
    verification: dict[str, Any] = {
        'status': VerificationStatus.CLAIMED.value,
        'explanation': 'Provider response claims only; model identity has not been independently verified.',
        'verifier': None, 'evidence_references': [], 'independent_model_proof': False,
    }
    verifier = _VERIFIERS.get(provider_id.split(':', 1)[0])
    if verifier is not None:
        try:
            # Give verifier its own facts, so even a buggy extension cannot alter the receipt.
            finding = verifier(json.loads(json.dumps(facts)), response)
            if (isinstance(finding, Validation) and finding.binding_sha256 == binding
                and finding.expires_at > now and finding.verifier and finding.evidence_references
                and finding.status in {VerificationStatus.UPSTREAM_VERIFIED, VerificationStatus.PROVIDER_VERIFIED}):
                verification = {**asdict(finding), 'status': finding.status.value,
                                'independent_model_proof': finding.status is VerificationStatus.UPSTREAM_VERIFIED}
        except Exception:
            verification['explanation'] = 'Verification could not be completed; provider claims remain unverified.'
    # A dispatch the adapter refused BEFORE anything was sent has NO response: reporting its
    # identity as "CLAIMED — provider response claims only" asserts a claim nobody made. The
    # refusal's own evidence (usepod.status == refused_before_send, the adapter's typed
    # pre-send refusal) is the fact; the receipt says so and stops.
    with contextlib.suppress(Exception):
        if str(usepod.get('status') or '') == 'refused_before_send':
            verification = {
                'status': VerificationStatus.NO_RESPONSE.value,
                'verifier': None, 'evidence_references': [], 'independent_model_proof': False,
                'explanation': 'No provider response exists: the request was refused before anything was sent; no model identity was claimed by anyone.',
            }
    facts['verification'] = verification
    return facts


def _public_prices(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {'route_class', 'provider', 'provider_id', 'input_microunits_per_million', 'output_microunits_per_million',
               'input_rate', 'output_rate', 'source', 'kind'}
    return [{k: v for k, v in row.items() if k in allowed and _text(v) is not None}
            for row in value[:32] if isinstance(row, dict)]
