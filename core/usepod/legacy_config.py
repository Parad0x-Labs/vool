"""Retained monetary-lane facts; absent from the built-in provider catalog."""
from core.cloud_providers import AUTH_URL_PATH_TOKEN, ProviderConfig, _stored_endpoint, resolved_config_pair

ORIGIN_SLOT = "llm.cloud.usepod_origin"
CONFIG = ProviderConfig(
        provider_id="usepod",
        label="UsePod (inference marketplace)",
        # The ORIGIN only. The token is a URL path segment (/proxy/<token>/v1) and never lives in
        # this table, a manifest, or a URL built from it here -- see core.usepod.descriptor.
        base_url="https://api.usepod.ai",
        probe_path="/balance",  # under /proxy/<token>: an auth-gated read that spends nothing
        # UsePod rejects the urllib default client with 403 even for a valid token. Save, Test
        # and discovery share these headers through the provider verification descriptor.
        extra_headers={"User-Agent": "VOOL/0.5"},
        model_id_style="bare",
        model_catalog_source="usepod_marketplace",
        default_model="",  # none: a model is chosen from the live marketplace, with a price bound
        key_prefixes=(),  # a UUID token has no vendor prefix; never auto-detected from a key alone
        env_names=(),
        auth_placement=AUTH_URL_PATH_TOKEN,
        credential_path_template="/proxy/{credential}",
        endpoint_slot=ORIGIN_SLOT,
        # GET /proxy/<token>/balance answers {"usdc_balance": <integer µUSDC>} (docs.usepod.ai
        # api/deposit-on-chain, read 2026-09-15). A genuine answer carries that field; a zero balance
        # is a valid token with no credit (exhausted), kept apart from the token's validity.
        probe_response_path=("usdc_balance",),
        probe_response_kind="value",
        probe_account_fields={"usdc_balance_microunits": ("usdc_balance",)},
        probe_exhausted_when_zero=("usdc_balance",),
    )


def origin() -> str:
    return _stored_endpoint(CONFIG)


def resolved_pair() -> tuple[str, str]:
    return resolved_config_pair(CONFIG)
