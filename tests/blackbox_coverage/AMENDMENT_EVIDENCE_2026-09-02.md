# Blackbox Amendment Evidence — Encrypted CAS v2 + Rollback Truth (2026-09-02)

Branch `build/blackbox-coverage-p1-20260902`, amendment on top of commits `9922959a`/`7e7811f1`/`2301de22`.
All proofs live in `tests/blackbox_coverage/test_cas_amendment.py` (14 tests) and the amended
base pack; commands below were run with `/tmp/vool-venv312/bin/python -m pytest` from the
worktree root.

## Blocker 1 — plaintext CAS → encrypted CAS v2

**Design.** `storage/blackbox/cas.py`: AES-256-GCM (`cryptography` 50.0.1; no homemade cipher),
random 96-bit nonce per seal, AAD binding schema version + opaque blob id + key version +
plaintext size. Filenames are `HMAC(id_key, sha256(plaintext))` — dedup preserves, raw digests
never reach a path. Keys: `core/blackbox/coverage/cas_keys.py` — versioned keyring
(`versions`/`current` + a long-lived naming `id_key`), acquired through the canonical secret
authority (`core.credential_store`, name `blackbox.cas.keys`, minted and read-back verified on
first use); an explicit key-file channel (`VOOL_BLACKBOX_CAS_KEYS_FILE`) serves tests/dev with
the SAME schema and crypto. No key source → `UnavailableCAS`: writes raise
`BlobKeyUnavailableError`, nothing is ever stored plaintext instead (proof 4). `BlobStore` is
the facade: v2 writes sealed, v1 files readable during the migration window, erasure removes
ciphertext + plaintext twin.

**Proofs (test file → test).**
1. `test_filesystem_inspection_cannot_recover_plaintext` — every file under the store (blobs,
   cas-v2, journal, HEAD) contains neither the plaintext bytes nor the raw digest, in content or
   filename.
2. `test_equal_plaintext_one_sealed_file_and_no_public_raw_hash_name` — equal plaintext → one
   sealed file, address = keyed id ≠ raw digest; a different keyring names the same content
   differently (the id is not a public function of the plaintext).
3. `test_correct_key_rollback_is_exact_from_sealed_blobs` — bytes + mode 0o640 + mtime_ns
   restored exactly; no plaintext v1 blob exists afterwards.
4. `test_wrong_key_fails_closed_typed` / `test_missing_key_fails_closed_before_the_handler_runs` /
   `test_rotated_away_key_version_fails_typed` — wrong keyring: typed auth failure; no key
   source: typed `blackbox_key_unavailable` refusal BEFORE the handler (the handler-run sentinel
   list stays empty); rotation retains old versions until `without_version`, re-seal makes
   everything readable under the new version.
5. `test_ciphertext_and_metadata_tampering_is_detected` — flips of ciphertext, nonce, size,
   key-version and id each fail typed; the untouched envelope still decrypts.
6. `test_interrupted_migration_resumes_and_deletes_plaintext_only_after_verification` — a
   sabotaged sealer dies mid-pass; 2 of 6 blobs sealed (plaintext deleted only after verified
   read-back), 4 remain; re-run completes; `blobs/` holds no plaintext; every blob reads back
   byte-exact. (Sabotage: `EncryptedBlobStore.put` monkeypatched to raise after N seals.)
7. `test_keys_never_appear_in_evidence_or_journal` — keyring repr/diagnostics/journal carry no
   key material (`to_json` exists only as the authority-persistence format).

## Blocker 2 — after-the-fact rollback downgrade

**Design.** `core/blackbox/coverage/recorder.py`: a REVERSIBLE capability may execute only when
every path it could mutate holds a captured restorable preimage or a proven missing-state record
(scan strategy: no degraded bounds, every existing file captured — `.git` internals included,
no capture holes; declared-path strategy: every declared target captured or missing). Incomplete
→ typed `blackbox_reversible_capture_incomplete` refusal BEFORE the handler, or an
operator-approved irreversible downgrade (`_blackbox_irreversible_downgrade` = {operator,
reason}) journaled as `coverage_downgrade` with `recorded_before_execution` and a seq BEFORE the
intended/terminal pair; the execution then runs postimage-only and the terminal says
`downgraded_to_irreversible: true`, `rollback_capable: false`. There is no code path that flips
reversibility after execution; a degraded AFTER scan is reported (`degraded_after_scan`) and
forfeits the rollback claim without changing the capability. Read-only plugin/MCP contracts run
with ZERO writable roots in the confined child (`run_plugin_tool(read_only=True)`) and are
scan-checked at the executor: local mutation → `blackbox_read_only_mutated`, success never
publishes.

**Proofs.**
7. `test_degraded_reversible_capture_prevents_the_handler_running` — an over-limit preimage file
   makes the reversible shell effect refuse pre-execution; the workspace is untouched.
8. `test_explicit_irreversible_downgrade_is_recorded_before_execution` — with the operator
   approval the effect runs; the `coverage_downgrade` entry precedes the intended entry by seq;
   the terminal claims no rollback; a later restore answers `blackbox_not_reversible`.
9. `test_misdeclared_read_only_plugin_mutation_never_publishes_success` — a read-only plugin
   contract whose handler writes → ok=False, `read_only_violation`, mutated paths named.

## RED→GREEN

The amendment proofs were written against the pre-amendment tree where applicable: proofs 1–6
and 8 fail on `2301de22` by construction (no encrypted CAS existed; degraded scans executed and
reported rollback_capable=false afterwards — exactly the inspector's finding). The base pack's
`test_secret_bytes_never_enter_the_journal_only_the_cas` was strengthened from "plaintext not in
journal" to "blob is sealed, address is opaque, right key decrypts" — it now pins both blockers.
Sabotage runs are the tamper matrix (5 flips), the dying-sealer migration crash, the
key-unavailable refusal (resolve patched to None), the lost-capability dispatch pin and the
journal-tamper/truncation pins from the base pack — all fail loudly when their guard is severed.

## Regression truth (2026-09-02, this worktree)

- `tests/blackbox_coverage` — **75 passed** (61 base, assertions updated to the opaque
  content-id journal truth + 14 amendment proofs).
- `tests/test_blackbox_flight_recorder.py` — **36 passed**; two assertions updated to the sealed
  truth (blob ref is the opaque address; corrupted-CAS test corrupts the envelope instead of a
  plaintext file). Intent of every updated test preserved.
- Adjacent packs — tool registry 19; plugin tools/executor + mcp bridge 81 (+2 skipped);
  executor + permissions + sandbox 129; runtime execution tools + effect gateway 43 (+2
  skipped); capability graph/registry contracts 33 (+2 skipped). **Zero failures.**
- `tests/test_runtime_execution_tools.py` `TrustedLocalOnlyChannelTests` — harness pinned to a
  scratch workspace (its subject is the trust channel; the default workspace is the repo, which
  the amendment's completeness law correctly refuses to preimage).
- Shared `tests/conftest.py` gained one autouse fixture pinning `VOOL_BLACKBOX_CAS_KEYS_FILE`
  to a session key file, following the existing credential/keychain pinning precedent (vault
  key derivation is signer-state dependent in tests; availability must not be order-dependent
  because the CAS fails closed).
- ruff: `core/blackbox/coverage`, `storage/blackbox/cas.py`, `storage/blackbox/blobs.py`,
  `tests/blackbox_coverage` — clean.

## Threat-model statement (the honest term)

The system is **tamper-evident and confidential against filesystem inspection**: an attacker
with only the disk (imaging, backups, another account) gets authenticated-encryption
ciphertext, opaque names, and no plaintext digests in v2 rows. It is NOT protected against a
process running as the same live user, which can reach the key through the same secret
authority the daemon uses — the same limit the v1 journal MAC already documents. "Tamper-proof"
would be a false claim; "tamper-evident" is the demonstrated term.
