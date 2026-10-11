# Security status and review boundaries

**Unreviewed candidate. No independent audit, formal proof, interoperability claim,
FIPS validation or production approval exists for DROP-HYBRID-1.** Testing confirms
specific behaviors, not general cryptographic security. Report vulnerabilities through
the repository's private security channels. Do not publish private session material.

Threat target: a malicious relay may change, replay, reorder, duplicate or drop bounded
frames. Peers have separately verified fingerprints, native cryptographic randomness,
trusted clocks and intact endpoints. A recipient needs a fresh one-time invite. The
library has no automatic trust-on-first-use, reusable prekey fallback or empty-state
recovery. Loss of prekeys/state is an explicit failed session, not a downgrade.

Authentication uses both Ed25519 and ML-DSA-65. Initial confidentiality combines ML-KEM
and three X25519 DH results. Identity signatures sacrifice deniability of handshake
participation. This profile needs independent analysis of authentication, key binding,
hybrid combining, nonce use, transcript encoding and the ratchet composition.

Per-message keys and consumed prekeys disappear from the active logical state. Native
temporary buffers are zeroed where feasible; JavaScript strings, garbage collection,
SQLite pages, WAL, SSD remapping, swap and backups prevent a physical-erasure guarantee.
Keep endpoints and backups protected. The vault key remains in caller-managed memory
and key storage. Compromise of that key plus an old snapshot can recover old state.

`forgetSession` is a durable local refusal barrier. It deletes queued content and active
state; it does not erase prior deliveries, relay copies, caller effects or backups.
Restoring an older database can resurrect consumed keys/counters and defeat replay/
forget guarantees. **Do not restore old snapshots for continued use.** Rotate identity,
vault and sessions after rollback or uncertain recovery; no trusted external monotonic
counter is implemented. Concurrent processes against one current database are serialized;
forked copies are not. Filesystem/SQLite durability still depends on honest storage.

Clear database metadata includes queue-to-session ownership; tampering with that
ownership causes AEAD verification to fail. Revocation deletes by this index, never
by decrypting unrelated queues. Corruption still fails closed; an obsolete or unknown
store schema is refused without resetting state. Existing scratch databases from an
earlier provisional schema cannot be reopened by this candidate.

Directory ownership and POSIX modes are checked; final directory/database symlinks and
database hardlinks are refused. Parent paths must be trusted. This does not defend
against a process with the same user privileges replacing files during an operation.
Windows is refused; equivalent ACL protection has not been built. Native runtime
vulnerabilities and timing/side-channel behavior belong in the review as well.

No network, discovery, wallet, registry, on-chain authentication or task authority is
provided. Session encryption does not grant permission to run decrypted task text.
Application adapters must enforce capabilities, quotas, verified pin exchange, clock
integrity and authenticated acknowledgements. Use delivery ids to deduplicate effects;
crash-safe inbox delivery alone cannot make external side effects exactly once.

The ongoing X25519 ratchet has no continuous PQ compromise recovery. Fresh hybrid
session establishment can refresh PQ entropy; automatic replacement, device revocation,
multi-device routing and account recovery need separate application contracts.

Before production: independent cryptographer review and implementation audit; adversarial
interoperability vectors for this exact profile; endpoint/key-store integration and
rollback policy; VOOL permission/forget integration tests; and required repository CI.
No review approval may be inferred from a passing demo or NIST primitive vectors.
