"""Session metadata writers, recovery copies, and final erasure verification."""
from __future__ import annotations

import copy
import json

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


@pytest.fixture(autouse=True)
def _isolated_metadata_home(tmp_path):
    from core import runtime_paths

    previous = runtime_paths._VOOL_HOME_OVERRIDE
    runtime_paths.configure_runtime_home(_profile(tmp_path))
    try:
        yield
    finally:
        runtime_paths.configure_runtime_home(previous)


def _forget(profile, chat, token):
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    handled, reply = maybe_handle_memory_command(
        f"forget {token}", session_id=chat,
        access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"chat_id": chat, "runtime_home": str(profile), "client_turn_id": "metadata-forget"},
    )
    assert handled
    return str(reply)


def _leaves(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, (*path, index))
    else:
        yield path


def test_every_field_emitted_by_metadata_writers_is_swept(tmp_path):
    """A new serialized writer field adds a required coverage case here."""
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import set_session_meta
    from core.memory.files import chat_session_meta_path
    from core.session_portability.importer import _stamp_import_trust

    profile = _profile(tmp_path)
    _ingest(profile, "metadata-shape", [(1760000000, "The notebook is blue.")])
    set_session_meta("metadata-shape", title="Notebook", archived=False,
                     project_id="project-blue", emoji="🔒", color="#112233")
    _stamp_import_trust("metadata-shape", "owner-export")
    path = chat_session_meta_path()
    shape = json.loads(path.read_text())["metadata-shape"]
    assert shape and shape["import_trust"] == "owner-export"
    for index, leaf in enumerate(_leaves(shape)):
        chat = f"metadata-field-{index}"
        token = f"METADATAFIELD-{index}-TANGERINE"
        ensure_chat_namespace(chat, grant_current_receipts=False)
        row = copy.deepcopy(shape)
        parent = row
        for key in leaf[:-1]:
            parent = parent[key]
        parent[leaf[-1]] = token
        document = json.loads(path.read_text())
        document[chat] = row
        # Exercise the actual persisted writer shape, independently of its
        # input validation and the eraser's field names.
        path.write_text(json.dumps(document))
        path.with_name(path.name + ".bak").write_text(json.dumps(document))
        assert token in path.read_text()

        reply = _forget(profile, chat, token)

        assert reply.startswith("Forget applied"), (leaf, reply)
        for carrier in path.parent.glob(path.name + "*"):
            assert token not in carrier.read_text(), (leaf, carrier.name)


def test_unknown_nested_metadata_is_removed_from_all_recovery_copies(tmp_path):
    from core.memory.entries import set_session_meta
    from core.memory.files import chat_session_meta_path

    profile = _profile(tmp_path)
    chat = "nested-metadata"
    _ingest(profile, chat, [(1760000000, "The locker code is MINT-84.")])
    set_session_meta(chat, title="Blue notebook", emoji="🔒")
    path = chat_session_meta_path()
    document = json.loads(path.read_text())
    document[chat]["future_metadata"] = {
        "nested": {"answer": "MINT-84", "keep": "blue"},
        "values": ["MINT-84", "survives"],
        "MINT-84": "key is a carrier too",
    }
    document["foreign-chat"] = {"title": "Foreign MINT-84"}
    paths = [path, path.with_name(path.name + ".bak"),
             path.with_name(path.name + ".corrupt-legacy"), path.with_name(path.name + ".tmp-old")]
    for carrier in paths:
        carrier.write_text(json.dumps(document))

    reply = _forget(profile, chat, "MINT-84")

    assert reply.startswith("Forget applied"), reply
    for carrier in paths:
        result = json.loads(carrier.read_text())
        assert "MINT-84" not in str(result[chat])
        assert result[chat]["title"] == "Blue notebook"
        assert result[chat]["emoji"] == "🔒"
        assert result[chat]["future_metadata"] == {"nested": {"keep": "blue"}, "values": ["survives"]}
        assert result["foreign-chat"] == document["foreign-chat"]


def test_unreadable_metadata_copy_names_incomplete_erasure(tmp_path):
    from core.memory.entries import load_session_meta, set_session_meta
    from core.memory.files import chat_session_meta_path

    profile = _profile(tmp_path)
    chat = "corrupt-metadata"
    _ingest(profile, chat, [(1760000000, "The locker code is MINT-84.")])
    set_session_meta(chat, title="Locker MINT-84")
    path = chat_session_meta_path()
    path.with_name(path.name + ".corrupt-old").write_text('{"corrupt-metadata":{"title":"MINT-84"')

    reply = _forget(profile, chat, "MINT-84")

    assert not reply.startswith("Forget applied"), reply
    assert "session metadata" in reply.lower(), reply
    assert "MINT-84" not in str(load_session_meta())


def test_final_verifier_detects_new_metadata_copy_after_sweep(tmp_path, monkeypatch):
    from core.memory import entries
    from core.memory.files import chat_session_meta_path

    profile = _profile(tmp_path)
    chat = "late-metadata"
    _ingest(profile, chat, [(1760000000, "The locker code is MINT-84.")])
    entries.set_session_meta(chat, title="Blue notebook")
    path = chat_session_meta_path()
    late_copy = path.with_name(path.name + ".future-recovery-copy")
    original = entries.forget_profile_derivatives

    def append_after_sweep(*args, **kwargs):
        original(*args, **kwargs)
        late_copy.write_text(json.dumps({chat: {"future_metadata": {"value": "MINT-84"}}}))

    monkeypatch.setattr(entries, "forget_profile_derivatives", append_after_sweep)

    reply = _forget(profile, chat, "MINT-84")

    assert "MINT-84" in late_copy.read_text()
    assert not reply.startswith("Forget applied"), reply
    assert "session metadata" in reply.lower(), reply
    assert "MINT-84" not in str(entries.load_session_meta())


def test_import_metadata_writer_refuses_completed_revocation(tmp_path):
    from core.memory.entries import set_session_meta
    from core.memory.files import chat_session_meta_path
    from core.session_portability.importer import _stamp_import_trust

    profile = _profile(tmp_path)
    chat = "import-metadata"
    _ingest(profile, chat, [(1760000000, "The locker code is MINT-84.")])
    set_session_meta(chat, title="Blue notebook")
    reply = _forget(profile, chat, "MINT-84")
    assert reply.startswith("Forget applied"), reply

    with pytest.raises(RuntimeError):
        _stamp_import_trust(chat, "MINT-84")

    path = chat_session_meta_path()
    for carrier in path.parent.glob(path.name + "*"):
        assert "MINT-84" not in carrier.read_text()


def test_backup_write_failure_prevents_verified_metadata_erasure(tmp_path, monkeypatch):
    from core.memory import entries
    from core.memory.files import chat_session_meta_path

    profile = _profile(tmp_path)
    chat = "denied-metadata-backup"
    _ingest(profile, chat, [(1760000000, "The locker code is MINT-84.")])
    entries.set_session_meta(chat, title="Locker MINT-84")
    path = chat_session_meta_path()
    backup = path.with_name(path.name + ".bak")
    original = entries.write_text_atomic

    def deny_backup(target, text):
        if target == backup:
            raise PermissionError("backup cannot be replaced")
        return original(target, text)

    monkeypatch.setattr(entries, "write_text_atomic", deny_backup)

    reply = _forget(profile, chat, "MINT-84")

    assert "MINT-84" in backup.read_text()
    assert not reply.startswith("Forget applied"), reply
    assert "session metadata" in reply.lower(), reply
    assert "MINT-84" not in str(entries.load_session_meta())
