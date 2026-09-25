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
