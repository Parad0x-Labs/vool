"""A pre-existing token_usage table (from before usd_actual, then before the reported/missing
columns existed) must upgrade cleanly in place, keep its old rows readable, and never turn silence
into a fabricated number.

Exact mechanism (core/usage_meter.py::_ensure_schema): three idempotent `ALTER TABLE ... ADD
COLUMN` statements, each wrapped in `contextlib.suppress(Exception)` so a column that already
exists is a silent no-op rather than an error on every subsequent boot. No destructive migration,
no data rewrite, no separate migration runner -- the same lazy-schema function that creates a
brand-new table also upgrades an old one, the first time either is touched in a process.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

import core.usage_meter as um
from core.usage_meter import COST_PAID_CLOUD, record_usage, usage_summary
from storage.db import get_connection

# The schema exactly as it shipped before ANY of the additive migrations existed -- no usd_actual,
# no prompt_tokens_reported, no output_tokens_reported. Hand-written deliberately (not derived
# from _CREATE_TABLE_SQL) so this test cannot silently start passing just because the "current"
# schema constant changed underneath it.
_ANCIENT_SCHEMA_SQL = """
CREATE TABLE token_usage (
    entry_id      TEXT NOT NULL PRIMARY KEY,
    created_at    TEXT NOT NULL,
    created_ts    REAL NOT NULL,
    provider_id   TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    cost_class    TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL
)
"""


@pytest.fixture()
def ancient_db():
    um._SCHEMA_READY_PATHS.clear()
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS token_usage")
        conn.execute(_ANCIENT_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    yield
    um._SCHEMA_READY_PATHS.clear()


def _insert_ancient_row(*, provider_id: str, model_id: str, prompt_tokens: int, output_tokens: int) -> str:
    entry_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).timestamp()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO token_usage
                (entry_id, created_at, created_ts, provider_id, model_id, cost_class, prompt_tokens, output_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entry_id, datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(), ts, provider_id, model_id, COST_PAID_CLOUD, prompt_tokens, output_tokens),
        )
        conn.commit()
    finally:
        conn.close()
    return entry_id


def test_ensure_schema_upgrades_an_ancient_table_without_raising(ancient_db) -> None:
    _insert_ancient_row(provider_id="openrouter-byok", model_id="old/model", prompt_tokens=100, output_tokens=50)

    # Any public entry point triggers _ensure_schema. record_usage is the most direct.
    assert record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD, prompt_tokens=1, output_tokens=1) is True


def test_old_rows_stay_readable_after_the_upgrade(ancient_db) -> None:
    _insert_ancient_row(provider_id="openrouter-byok", model_id="old/model", prompt_tokens=100, output_tokens=50)
    record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD, prompt_tokens=1, output_tokens=1)  # triggers upgrade

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT provider_id, model_id, prompt_tokens, output_tokens, usd_actual, "
            "prompt_tokens_reported, output_tokens_reported FROM token_usage WHERE provider_id = 'openrouter-byok'"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    provider_id, model_id, prompt_tokens, output_tokens, usd_actual, prompt_reported, output_reported = rows[0]
    assert (provider_id, model_id, prompt_tokens, output_tokens) == ("openrouter-byok", "old/model", 100, 50)
    # usd_actual: no fabricated number -- the column did not exist when this row was written, so
    # it is genuinely unknown, not zero.
    assert usd_actual is None
    # reported-vs-zero: DEFAULT 1 for pre-existing rows -- "reported" is what these rows already
    # displayed as before the distinction existed; retroactively marking them "missing" would
    # silently change history's own meaning, not just add new information.
    assert prompt_reported == 1
    assert output_reported == 1


def test_a_migrated_old_row_is_not_counted_as_missing_usage(ancient_db) -> None:
    _insert_ancient_row(provider_id="openrouter-byok", model_id="old/model", prompt_tokens=100, output_tokens=50)
    record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD, prompt_tokens=1, output_tokens=1)

    summary = usage_summary()

    assert summary["calls_missing_usage"] == 0
    assert summary[COST_PAID_CLOUD]["responses"] == 2  # the ancient row + the new one
    assert summary[COST_PAID_CLOUD]["prompt_tokens"] == 101
    assert summary[COST_PAID_CLOUD]["output_tokens"] == 51


def test_a_new_call_with_missing_usage_is_still_correctly_flagged_after_migrating_old_data(ancient_db) -> None:
    """The migration does not paper over genuinely missing usage on NEW data -- only old,
    pre-distinction rows default to "reported"."""
    _insert_ancient_row(provider_id="openrouter-byok", model_id="old/model", prompt_tokens=100, output_tokens=50)
    record_usage(
        provider_id="new-provider", model_id="new/model", cost_class=COST_PAID_CLOUD,
        prompt_tokens=10, output_tokens=0, prompt_tokens_reported=True, output_tokens_reported=False,
    )

    summary = usage_summary()

    assert summary["calls_missing_usage"] == 1  # only the new call, not the migrated old one
    assert summary[COST_PAID_CLOUD]["responses"] == 2


def test_no_cost_estimate_is_fabricated_from_a_migrated_rows_absent_usd_actual(ancient_db) -> None:
    """A migrated row's usd_actual is NULL (genuinely unknown, not a real $0 charge). The paid
    bucket's blended-rate ESTIMATE must still price it -- that estimate is explicitly labelled an
    estimate (usd_estimate / usd, not usd_actual) precisely so it is never confused with a real
    provider-reported charge."""
    _insert_ancient_row(provider_id="openrouter-byok", model_id="old/model", prompt_tokens=1_000_000, output_tokens=0)
    record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD, prompt_tokens=1, output_tokens=1)  # triggers upgrade

    summary = usage_summary()
    paid = summary[COST_PAID_CLOUD]

    assert paid["usd_actual"] == 0.0  # nothing REPORTED as actually charged
    assert paid["usd"] > 0.0  # the blended estimate still prices the tokens
    assert paid["all_actual"] is False  # honestly flagged as partly estimated, not partly invented


def test_ensure_schema_is_idempotent_across_repeated_calls(ancient_db) -> None:
    """Calling _ensure_schema (via record_usage) many times against the same ancient table must
    never raise -- the ADD COLUMN statements have to stay silent no-ops once the columns exist."""
    _insert_ancient_row(provider_id="p", model_id="m", prompt_tokens=1, output_tokens=1)
    for _ in range(5):
        um._SCHEMA_READY_PATHS.clear()  # force _ensure_schema to run its ALTER TABLE block again
        assert record_usage(provider_id="p2", model_id="m2", cost_class=COST_PAID_CLOUD, prompt_tokens=1, output_tokens=1) is True
