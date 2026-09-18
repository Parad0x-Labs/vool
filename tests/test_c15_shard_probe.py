"""C15 shard-child probe: a real test file the parallel-shard proof drives.

Kept under tests/ deliberately: a shard child must go through the SAME conftest +
preflight boot any real test file gets. With ``VOOL_TEST_MODE=1`` (what
``ops/pytest_shards.py`` exports) the preflight pins non-interactive storage before
these asserts run, so the file doubles as a pin assertion inside a live child.
"""
import os


def test_shard_child_storage_is_pinned_non_interactive_and_local() -> None:
    assert os.environ.get("VOOL_CREDENTIAL_STORE") == "vault"
    assert os.environ.get("VOOL_KEY_STORAGE_MODE") == "file"
    from core import credential_store

    credential_store.store_credential("llm.cloud.openrouter", "skk-shard-probe")
    assert credential_store.get_credential("llm.cloud.openrouter") == "skk-shard-probe"
    assert credential_store.active_backend() == "vault"
