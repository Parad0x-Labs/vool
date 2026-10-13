"""Real database changes during outbox verification invalidate its captured state."""
import contextlib
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from core.registry.codec import RegistryError, digest
from registry_service.store import Store
from tests.registry.helpers import NOW, artifact_fixture, evidence_fixture, signed, trust_fixture


class SnapshotBoundaryStore(Store):
    """Pause after the real snapshot connection closes; no query or verifier is replaced."""

    def __init__(self, path):
        self.selected = Event()
        self.changed = Event()
        self.pause_once = False
        super().__init__(path)

    @contextlib.contextmanager
    def connection(self):
        with super().connection() as db:
            yield db
        if self.pause_once:
            self.pause_once = False
            self.selected.set()
            assert self.changed.wait(5), "database mutation did not complete"


@pytest.mark.parametrize("change", ["delete-account", "failed-review", "new-review", "prune", "reconsented-review"])
def test_export_rechecks_state_after_real_concurrent_mutation(tmp_path, change):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    store = SnapshotBoundaryStore(tmp_path / "accounts.sqlite")
    writer = Store(store.path)
    account = store.identity("github", "export-owner", now=NOW)
    store.profile(account, {"namespace": "alice", "bio": "", "handles": {}})
    submission = store.upload(account, raw, now=NOW)
    original = evidence_fixture(raw, policy)
    store.review(submission["id"], signed(original, keys["review"]), policy, root["signed"], now=NOW)
    store.consent(account, submission["id"], submission["sha256"], public=True)

    def mutate():
        assert store.selected.wait(5), "export snapshot was not read"
        try:
            if change == "delete-account":
                writer.delete_account(account)
            elif change == "prune":
                writer.prune(now=NOW + 7 * 86400 + 1)
            else:
                evidence = copy.deepcopy(original)
                if change == "failed-review":
                    evidence["checks"].pop()
                elif change == "reconsented-review":
                    report = evidence["reports"]["quality"]
                    report["result"] = {"revision": "distinct signed local fixture"}
                    check = next(item for item in evidence["checks"] if item["name"] == "quality")
                    check["report_sha256"] = digest(report)
                writer.review(submission["id"], signed(evidence, keys["review"]), policy, root["signed"], now=NOW)
                if change == "reconsented-review":
                    writer.consent(account, submission["id"], submission["sha256"], public=True)
                    assert writer.submission(account, submission["id"])["state"] == "ready_for_github"
        finally:
            store.changed.set()

    store.pause_once = True
    with ThreadPoolExecutor(max_workers=1) as worker:
        mutation = worker.submit(mutate)
        with pytest.raises(RegistryError, match=r"changed|export"):
            store.export_submission(submission["id"], policy, root["signed"], now=NOW)
        mutation.result(timeout=5)
