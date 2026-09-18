from __future__ import annotations

from core.cloud_provider_contract import CloudModelMetadata, PricingState
from storage.cloud_model_catalog import get_catalog_model, list_provider_catalog, replace_provider_catalog
from storage.db import get_connection
from storage.migrations import run_migrations


def _model(model_id: str, *, expires_at: str = "2099-01-01T00:00:00+00:00") -> CloudModelMetadata:
    return CloudModelMetadata(
        provider_id="provider-a",
        model_id=model_id,
        display_name=model_id,
        pricing_state=PricingState.FREE,
        input_usd_per_token=0.0,
        output_usd_per_token=0.0,
        request_usd=0.0,
        discovered_at="2026-07-14T00:00:00+00:00",
        expires_at=expires_at,
    )


def test_catalog_replace_removes_models_that_disappeared() -> None:
    replace_provider_catalog("provider-a", (_model("one"), _model("two")))
    assert {model.model_id for model in list_provider_catalog("provider-a")} == {"one", "two"}
    replace_provider_catalog("provider-a", (_model("two"),))
    assert get_catalog_model("provider-a", "one") is None
    assert [model.model_id for model in list_provider_catalog("provider-a")] == ["two"]


def test_stale_catalog_is_excluded_by_default_but_inspectable() -> None:
    replace_provider_catalog("provider-a", (_model("old", expires_at="2020-01-01T00:00:00+00:00"),))
    assert list_provider_catalog("provider-a") == ()
    assert [model.model_id for model in list_provider_catalog("provider-a", include_stale=True)] == ["old"]


def test_catalog_rejects_cross_provider_snapshot() -> None:
    foreign = CloudModelMetadata(provider_id="provider-b", model_id="m", display_name="m")
    try:
        replace_provider_catalog("provider-a", (foreign,))
    except ValueError as exc:
        assert "different provider" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("cross-provider catalog was accepted")


def test_central_migration_owns_cloud_catalog_schema() -> None:
    run_migrations()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'cloud_model_catalog'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
