"""Database readiness and erasure keys must retain their own store identity."""
import pytest

from core import finalization as fin
from core.runtime_paths import configure_runtime_home
from storage.db import configure_default_db_path
from storage.migrations import run_migrations
from tests.foundation.test_a8_privacy_pass003 import _admit_finalize


@pytest.fixture
def homes(tmp_path):
    fin.reset_governance_readiness_for_tests()
    def select(name):
        home = tmp_path / name
        configure_runtime_home(home)
        configure_default_db_path(home / "store.db")
        return home
    yield select
    fin.reset_governance_readiness_for_tests()
    configure_default_db_path(None)
    configure_runtime_home(None)

def test_absent_before_migration(homes):
    homes("one")
    assert fin.governance_store_state() == "ABSENT"
    run_migrations()
    text = "governance cache migration confidentiality probe"
    row = _admit_finalize(text)
    fin.set_availability(row["finalization_id"], fin.AVAILABILITY_WITHHELD)
    assert not fin.writer_may_persist_text(text)

def test_key_reload_cannot_adopt_old_absence(homes):
    homes("legacy")
    assert fin.governance_store_state() == "ABSENT"
    homes("governed")
    run_migrations()
    fin._a8_digest_key()
    assert fin.governance_store_state() == "READY"

def test_readiness_reload_cannot_adopt_old_key(homes):
    homes("first")
    run_migrations()
    first = fin._a8_digest_key()
    homes("second")
    run_migrations()
    assert fin.governance_store_state() == "READY"
    second = fin._a8_digest_key()
    assert second != first


def test_home_switch_with_same_database_loads_its_own_key(homes, tmp_path):
    first_home = homes("first")
    first_key = fin._a8_digest_key()
    configure_runtime_home(tmp_path / "second")
    second_key = fin._a8_digest_key()
    assert second_key != first_key
    configure_runtime_home(first_home)
    assert fin._a8_digest_key() == first_key


def test_key_survives_cache_reset_and_database_switch_in_same_home(homes):
    home = homes("one")
    first_key = fin._a8_digest_key()
    fin.reset_governance_readiness_for_tests()
    assert fin._a8_digest_key() == first_key
    configure_default_db_path(home / "another.db")
    assert fin._a8_digest_key() == first_key


def test_digest_key_is_served_from_cache_without_recomputing_its_path(homes, monkeypatch):
    """The tombstone lookup consults the digest key on every governed read, so
    the cache hit must not re-derive the key file's path: data_path() runs
    ensure_runtime_dirs() (five mkdir syscalls) plus two full resolutions, and
    served journeys issue hundreds of thousands of governed reads (measured
    2026-09-25: that per-call resolution alone cost more than the fence's
    queries and pushed CI served journeys past their watchdog budgets)."""
    import core.runtime_paths as rp

    homes("one")
    fin._a8_digest_key()  # warm the cache; the counter starts from the next lookup
    calls = {"data_path": 0}
    real_data_path = rp.data_path

    def counting_data_path(*parts):
        calls["data_path"] += 1
        return real_data_path(*parts)

    monkeypatch.setattr(rp, "data_path", counting_data_path)
    for _ in range(50):
        fin._a8_digest_key()
    assert calls["data_path"] == 0, "cached digest key must not re-resolve its path per call"


def test_digest_key_reload_follows_the_runtime_home_authority(homes, monkeypatch):
    """A home switch must reload the key (another profile's erasure key must
    never be served), and the authority check is the runtime-home generation
    plus the ambient home environment — the transitions that can move the key
    file, without paying a filesystem resolution on every governed read."""
    homes("one")
    first = fin._a8_digest_key()
    homes("one")  # same home re-configured: generation moves, the key file does not
    assert fin._a8_digest_key() == first
    homes("two")
    assert fin._a8_digest_key() != first
    configure_runtime_home(None)  # fall back to the environment home authority
    monkeypatch.setenv("VOOL_HOME", str(homes("one")))
    assert fin._a8_digest_key() == first
