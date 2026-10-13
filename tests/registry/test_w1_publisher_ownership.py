import pytest

from core.registry.codec import RegistryError
from registry_service.store import Store
from tests.registry.audit_helpers import package, publish
from tests.registry.helpers import NOW


def test_w1_preclaimant_cannot_become_direct_publisher_creator(tmp_path):
    raw, _, _ = package(tmp_path, publisher="bob")
    store = Store(tmp_path / "accounts.sqlite")
    claimant = store.identity("google", "unlinked-claimant", now=NOW)
    store.profile(claimant, {"namespace": "bob", "bio": "Not the GitHub publisher", "handles": {"github": "bob"}})
    publish(store, raw, tmp_path / "trust.sqlite")
    assert store.public_profile("bob") == {"namespace": "bob", "bio": "", "handles": {}}
    with pytest.raises(RegistryError, match=r"publisher|namespace"):
        store.upload(claimant, raw, now=NOW)


def test_w1_known_pending_publication_prevents_unlinked_first_claim(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    store.reserve_publication("bob/pending")
    account = store.identity("github", "unlinked-newcomer", now=NOW)
    with pytest.raises(RegistryError, match="reserved"):
        store.profile(account, {"namespace": "bob", "bio": "", "handles": {}})
    assert store.profile(account, {"namespace": "available", "bio": "", "handles": {}})["namespace"] == "available"
