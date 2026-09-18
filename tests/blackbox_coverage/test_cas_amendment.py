"""The 2026-09-02 amendment proofs: encrypted CAS v2 and no after-the-fact rollback downgrade.

1  filesystem inspection cannot recover preimage plaintext
2  equal plaintext does not expose a public raw-hash filename
3  correct-key rollback stays byte/mode/mtime exact (from SEALED blobs)
4  wrong / missing / rotated keys fail closed
5  ciphertext and metadata tampering is detected
6  interrupted v1->v2 migration recovers
7  degraded reversible capture prevents the handler from running
8  an explicit irreversible downgrade is recorded BEFORE execution
9  a misdeclared read-only plugin mutation never publishes success
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from tests.blackbox_coverage._ctx import ctx
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestEncryptedCASProofs:
    def test_filesystem_inspection_cannot_recover_plaintext(self, workspace, store_dir):
        marker = b"PREIMAGE-PLAINTEXT-7c9d1a-marker"
        from core.blackbox.store import default_store

        store = default_store()
        ref = store.blobs.put(marker)
        every_file = [p for p in store_dir.rglob("*") if p.is_file()]
        assert every_file, "the store wrote nothing"
        for path in every_file:
            assert marker not in path.read_bytes(), f"plaintext at {path}"
            assert _sha(marker).encode("ascii") not in path.read_bytes(), f"raw digest at {path}"
            assert _sha(marker) != path.name and _sha(marker) not in str(path), f"digest filename {path}"
        # The one sealed file decrypts only with the key.
        sealed = [p for p in every_file if p.parent.parent.name == "cas-v2"]
        assert len(sealed) == 1
        assert store.blobs.get(ref.ref) == marker

    def test_equal_plaintext_one_sealed_file_and_no_public_raw_hash_name(self, workspace, store_dir):
        from core.blackbox.coverage.cas_keys import CasKeyring
        from core.blackbox.store import default_store

        store = default_store()
        first = store.blobs.put(b"same-bytes")
        second = store.blobs.put(b"same-bytes")
        assert first.ref == second.ref, "dedup by content still works"
        assert first.ref != _sha(b"same-bytes"), "the address is the keyed opaque id, not the raw digest"
        assert first.ref == store.cas_keyring.id_for(_sha(b"same-bytes"))
        # A DIFFERENT keyring names the same content differently: the id is not a public function
        # of the plaintext.
        other = CasKeyring.mint()
        assert other.id_for(_sha(b"same-bytes")) != first.ref

    def test_correct_key_rollback_is_exact_from_sealed_blobs(self, workspace, store_dir):
        target = workspace / "precious.txt"
        target.write_bytes(b"exact-bytes")
        os.chmod(target, 0o640)
        mtime = os.stat(target).st_mtime_ns

        from core.blackbox.coverage.recorder import recorded_capability_mutation
        from core.blackbox.store import default_store
        from core.runtime_execution_tools import RuntimeExecutionResult

        def handler():
            target.write_bytes(b"overwritten")
            return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="", details={})

        result = recorded_capability_mutation(
            "sandbox.run_command",
            {"command": "cp precious.txt elsewhere", "cwd": str(workspace)},
            handler=handler,
            source_context=ctx(workspace, turn="seal-turn"),
            workspace_root=workspace,
        )
        assert result.ok and result.details["blackbox"]["coverage"]["rollback_capable"]

        from core.blackbox import authority
        from core.blackbox.coverage.restore import restore_coverage_turn

        store = default_store()
        token = authority.mint_rollback_authorization(turn_id="seal-turn", workspace_root=workspace, operator="op", store=store)
        outcome = restore_coverage_turn(turn_id="seal-turn", workspace_root=workspace, token=token, store=store)
        assert outcome["ok"], outcome
        assert target.read_bytes() == b"exact-bytes"
        assert (os.stat(target).st_mode & 0o777) == 0o640
        assert os.stat(target).st_mtime_ns == mtime
        # And the bytes it restored came from a SEALED file, never a plaintext one.
        assert not any(p.is_file() for p in (store_dir / "blobs").rglob("*") if p.is_file()), "plaintext v1 blobs remain"

    def test_wrong_key_fails_closed_typed(self, workspace, store_dir):
        from core.blackbox.coverage.cas_keys import CasKeyError, CasKeyring
        from core.blackbox.store import default_store
        from storage.blackbox.cas import BlobCorruptError

        store = default_store()
        ref = store.blobs.put(b"secret-under-original-key")
        # Swap in a DIFFERENT keyring (the file channel is the test seam; the authority slot in
        # production is the same single source): the sealed bytes no longer authenticate.
        wrong = CasKeyring.mint()
        store.blobs.cas.keyring = wrong
        with pytest.raises((BlobCorruptError, CasKeyError)):
            store.blobs.get(ref.ref)

    def test_missing_key_fails_closed_before_the_handler_runs(self, workspace, store_dir, monkeypatch):
        from core.blackbox.coverage import cas_keys
        from core.blackbox.coverage.recorder import recorded_capability_mutation
        from core.runtime_execution_tools import RuntimeExecutionResult

        monkeypatch.delenv("VOOL_BLACKBOX_CAS_KEYS_FILE", raising=False)
        monkeypatch.setattr(cas_keys, "resolve_keyring", lambda: None)
        ran = []

        def handler():
            ran.append(True)
            return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="", details={})

        result = recorded_capability_mutation(
            "sandbox.run_command",
            {"command": "touch x.txt", "cwd": str(workspace)},
            handler=handler,
            source_context=ctx(workspace, turn="nokey-turn"),
            workspace_root=workspace,
        )
        assert result.ok is False
        assert result.status == "blackbox_key_unavailable"
        assert result.details["executed"] is False
        assert ran == [], "the handler ran without a recordable CAS"
        assert not (workspace / "x.txt").exists()

    def test_rotated_away_key_version_fails_typed(self, workspace, store_dir):
        from core.blackbox.coverage.cas_keys import CasKeyError
        from core.blackbox.store import default_store
        from storage.blackbox.cas import EncryptedBlobStore

        store = default_store()
        ref = store.blobs.put(b"sealed-under-v1")
        rotated = store.cas_keyring.rotated()  # current -> 2, v1 retained
        store.blobs.cas.keyring = rotated
        assert store.blobs.get(ref.ref) == b"sealed-under-v1", "retained old versions still decrypt"
        dropped = rotated.without_version("1")
        store.blobs.cas.keyring = dropped
        with pytest.raises(CasKeyError):
            store.blobs.get(ref.ref)
        # Rotation with re-seal keeps everything readable under the new version.
        store.blobs.cas.keyring = rotated
        assert isinstance(store.blobs.cas, EncryptedBlobStore)
        store.blobs.cas.reencrypt_all()
        store.blobs.cas.keyring = rotated.without_version("1")
        assert store.blobs.get(ref.ref) == b"sealed-under-v1"

    def test_ciphertext_and_metadata_tampering_is_detected(self, workspace, store_dir):
        from core.blackbox.store import default_store
        from storage.blackbox.cas import BlobCorruptError

        store = default_store()
        marker = b"tamper-target-bytes"
        ref = store.blobs.put(marker)
        path = store.blobs.cas.path_for_id(ref.ref)

        def _restore(payload):
            path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")

        original = json.loads(path.read_text())
        # ciphertext flip
        flipped = dict(original)
        raw = bytearray(__import__("base64").b64decode(original["ct"]))
        raw[0] ^= 0x01
        flipped["ct"] = __import__("base64").b64encode(bytes(raw)).decode("ascii")
        _restore(flipped)
        with pytest.raises(BlobCorruptError):
            store.blobs.get(ref.ref)
        # nonce flip
        flipped = dict(original)
        nonce = bytearray(__import__("base64").b64decode(original["nonce"]))
        nonce[0] ^= 0x01
        flipped["nonce"] = __import__("base64").b64encode(bytes(nonce)).decode("ascii")
        _restore(flipped)
        with pytest.raises(BlobCorruptError):
            store.blobs.get(ref.ref)
        # size (metadata) lie
        flipped = dict(original)
        flipped["size"] = original["size"] + 1
        _restore(flipped)
        with pytest.raises(BlobCorruptError):
            store.blobs.get(ref.ref)
        # key version lie
        flipped = dict(original)
        flipped["kid"] = "99"
        _restore(flipped)
        with pytest.raises((BlobCorruptError, Exception)):
            store.blobs.get(ref.ref)
        # id lie
        flipped = dict(original)
        flipped["id"] = "0" * 64
        _restore(flipped)
        with pytest.raises(BlobCorruptError):
            store.blobs.get(ref.ref)
        # and the untouched envelope still works
        _restore(original)
        assert store.blobs.get(ref.ref) == marker

    def test_erasure_removes_ciphertext_and_recoverability(self, workspace, store_dir):
        from core.blackbox.store import default_store
        from storage.blackbox.blobs import BlobMissingError

        store = default_store()
        ref = store.blobs.put(b"erase-me")
        assert store.blobs.delete(ref.ref) is True
        assert not store.blobs.cas.path_for_id(ref.ref).exists()
        with pytest.raises(BlobMissingError):
            store.blobs.get(ref.ref)

    def test_keys_never_appear_in_evidence_or_journal(self, workspace, store_dir):
        """to_json is the PERSISTENCE format (it travels inside the secret authority, never to
        evidence); everything a human or a journal can see -- repr, diagnostics, journal bytes --
        stays free of key material."""
        from core.blackbox.coverage.cas_keys import keyring_state
        from core.blackbox.store import default_store

        store = default_store()
        safe = repr(store.cas_keyring)
        assert store.cas_keyring.id_key.hex() not in safe
        for material in store.cas_keyring.versions.values():
            assert material.hex() not in safe
        state = keyring_state(store.cas_keyring)
        assert state["available"] is True and "id_key" not in state
        store.blobs.put(b"evidence-bytes")
        store.append({"schema": "blackbox_effect_v1", "kind": "probe"})
        journal = (store_dir / "journal.jsonl").read_bytes()
        assert store.cas_keyring.id_key.hex().encode("ascii") not in journal
        for material in store.cas_keyring.versions.values():
            assert material.hex().encode("ascii") not in journal


class TestMigrationProofs:
    def _seed_v1(self, store_dir, payloads):
        from storage.blackbox.blobs import BlobStore

        legacy = BlobStore(store_dir)  # legacy constructor: the v1 plaintext layout
        return [legacy.put(data) for data in payloads]

    def test_interrupted_migration_resumes_and_deletes_plaintext_only_after_verification(self, workspace, store_dir, monkeypatch):
        payloads = [f"v1-plaintext-{i}".encode() for i in range(6)]
        refs = self._seed_v1(store_dir, payloads)
        raw_digests = [_sha(data) for data in payloads]

        from core.blackbox.store import BlackboxStore

        store = BlackboxStore(store_dir)
        assert store.cas_keyring is not None

        # Sabotage: the sealer dies after two blobs. Migration stops mid-flight, exactly like a
        # crash; whatever it already sealed must be verified-and-deletable, the rest untouched.
        real_put = store.blobs.cas.put
        sealed = {"n": 0}

        def dying_put(data, *, max_bytes=None):
            sealed["n"] += 1
            if sealed["n"] > 2:
                raise RuntimeError("simulated crash mid-migration")
            return real_put(data, max_bytes=max_bytes)

        monkeypatch.setattr(store.blobs.cas, "put", dying_put)
        partial = store.migrate_blobs_to_v2()
        assert partial["ok"] is False, partial
        assert partial["migrated"] == 2 and partial["remaining"] == len(payloads) - 2, partial
        remaining_plaintext = [item for item in (store_dir / "blobs").rglob("*") if item.is_file()]
        assert len(remaining_plaintext) == len(payloads) - 2, "nothing was left to resume"
        # Already-sealed blobs had their plaintext deleted only after verification.
        sealed_digests = {digest for digest in raw_digests if not (store_dir / "blobs" / digest[:2] / digest).exists()}
        assert len(sealed_digests) == 2

        monkeypatch.undo()
        final = store.migrate_blobs_to_v2()
        assert final["ok"] is True and final["remaining"] == 0, final
        assert not [p for p in (store_dir / "blobs").rglob("*") if p.is_file()], "plaintext survived migration"
        for ref, data in zip(refs, payloads, strict=True):
            assert store.blobs.get(ref.ref) == data, "every migrated blob reads back byte-exact"

    def test_journal_rows_carry_no_raw_digest_after_amendment(self, workspace, store_dir):
        from core.blackbox.coverage.recorder import recorded_capability_mutation
        from core.runtime_execution_tools import RuntimeExecutionResult

        content = b"journal-opacity-content"
        (workspace / "opaque.txt").write_bytes(content)
        recorded_capability_mutation(
            "sandbox.run_command",
            {"command": "cp opaque.txt copy.txt", "cwd": str(workspace)},
            handler=lambda: RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="", details={}),
            source_context=ctx(workspace, turn="opaque-turn"),
            workspace_root=workspace,
        )
        journal_bytes = (store_dir / "journal.jsonl").read_bytes()
        assert _sha(content).encode("ascii") not in journal_bytes, "a raw plaintext digest leaked into the journal"
        entries = [json.loads(line) for line in journal_bytes.decode().splitlines()]
        for entry in entries:
            for row in [entry, *entry.get("files", {}).values(), *entry.get("drift", [])]:
                if isinstance(row, dict):
                    assert "sha256" not in row or not row["sha256"], f"raw digest in {entry.get('kind')}"


class TestRollbackTruthProofs:
    def _recorded(self, workspace, turn, store, *, extra=None, content=b"small"):
        from core.blackbox.coverage.recorder import recorded_capability_mutation
        from core.runtime_execution_tools import RuntimeExecutionResult

        (workspace / "watched.txt").write_bytes(content)

        def handler():
            (workspace / "watched.txt").write_bytes(b"changed")
            (workspace / "appeared.txt").write_bytes(b"new")
            return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="", details={})

        return recorded_capability_mutation(
            "sandbox.run_command",
            {"command": "script", "cwd": str(workspace)},
            handler=handler,
            source_context=ctx(workspace, turn=turn, **(extra or {})),
            workspace_root=workspace,
            store=store,
        )

    def _store_with_tiny_limit(self, store_dir):
        from core.blackbox.store import BlackboxStore

        return BlackboxStore(store_dir, max_blob_bytes=8)

    def test_degraded_reversible_capture_prevents_the_handler_running(self, workspace, store_dir):
        store = self._store_with_tiny_limit(store_dir)
        result = self._recorded(workspace, "degraded-turn", store, content=b"this-is-well-over-eight-bytes")
        assert result.ok is False
        assert result.status == "blackbox_reversible_capture_incomplete"
        assert result.details["executed"] is False
        assert (workspace / "watched.txt").read_bytes() == b"this-is-well-over-eight-bytes", "the handler ran anyway"
        assert not (workspace / "appeared.txt").exists()
        assert any(p == "watched.txt" for p in result.details["incomplete_paths"])

    def test_explicit_irreversible_downgrade_is_recorded_before_execution(self, workspace, store_dir):
        store = self._store_with_tiny_limit(store_dir)
        result = self._recorded(
            workspace,
            "downgrade-turn",
            store,
            content=b"this-is-well-over-eight-bytes",
            extra={"_blackbox_irreversible_downgrade": {"operator": "op", "reason": "operator accepted no rollback"}},
        )
        assert result.ok, result.response_text
        entries = store.entries()
        downgrade = [e for e in entries if e.get("kind") == "coverage_downgrade"]
        assert downgrade and downgrade[0]["approved_by"] == "op" and downgrade[0]["recorded_before_execution"] is True
        intended = next(e for e in entries if e.get("kind") == "coverage_scan_intended")
        assert downgrade[0]["seq"] < intended["seq"], "the downgrade must precede execution evidence"
        terminal = [e for e in entries if e.get("kind") == "coverage_scan_terminal"][-1]
        assert terminal["downgraded_to_irreversible"] is True
        assert terminal["rollback_capable"] is False

        # And a downgraded effect never restores: the operator traded rollback away, on record.
        from core.blackbox import authority
        from core.blackbox.coverage.restore import restore_coverage_turn

        token = authority.mint_rollback_authorization(turn_id="downgrade-turn", workspace_root=workspace, operator="op", store=store)
        refused = restore_coverage_turn(turn_id="downgrade-turn", workspace_root=workspace, token=token, store=store)
        assert refused["ok"] is False and refused["status"] == "blackbox_not_reversible"
        assert (workspace / "appeared.txt").exists(), "refusing restore changed the workspace"

    def test_misdeclared_read_only_plugin_mutation_never_publishes_success(self, workspace, store_dir):
        from core.tool_intent_executor import ToolIntentExecution, _with_blackbox_coverage
        from core.tool_registry import register
        from tests.blackbox_coverage.test_plugin_mcp_proof import _plugin_contract

        register(_plugin_contract(intent="demo.liar", mutation=None, side_effect_class="read_only"))
        (workspace / "before-scan-anchor.txt").write_bytes(b"anchor")

        def handler():
            (workspace / "smuggled.txt").write_bytes(b"mutation")
            return ToolIntentExecution(handled=True, ok=True, status="executed", response_text="done",
                                       user_safe_response_text="done", mode="tool_executed",
                                       tool_name="demo.liar", details={"executed": True})

        result = _with_blackbox_coverage(
            "demo.liar", {}, source_context=ctx(workspace), dispatch=handler
        )
        assert result.ok is False
        assert result.status == "blackbox_read_only_mutated"
        assert result.details["read_only_violation"] is True
        assert "smuggled.txt" in result.details["mutated_paths"]
