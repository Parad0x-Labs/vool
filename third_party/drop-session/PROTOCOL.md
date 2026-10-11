# DROP-HYBRID-1 review profile

Version is fixed. Unknown algorithms, versions, fields, encodings and counters fail
closed; no negotiation or downgrade path. This is an independently authored profile
informed by [PQXDH](https://signal.org/docs/specifications/pqxdh/) and
[Double Ratchet section 3](https://signal.org/docs/specifications/doubleratchet/).
No libsignal or SPQR source is included. It is **not either protocol's wire format**.

## Transcript and authentication

An identity holds separate X25519, Ed25519 and ML-DSA-65 keys. The peer fingerprint is
SHA-256 of `DROP-HYBRID-1/identity\0` plus canonical public identity JSON. Public keys
are canonical DER SPKI, represented as unpadded base64url. Private keys are PKCS8.
Both identity signatures are mandatory; neither may replace the other.

The recipient signs a bundle containing the profile, random 128-bit prekey id,
issued/expiry milliseconds, identity, one-time X25519 key and ML-KEM-768 key. Signatures
use `DROP-HYBRID-1/bundle\0` plus canonical bundle-body JSON. An authenticated channel
must establish the expected fingerprint independently of this bundle.

The initiator checks the pinned identity, both signatures, formats and expiry. It
generates an ephemeral X25519 key, a separate initial ratchet key and a real ML-KEM
encapsulation. Its intro contains the profile, random session id, prekey id, SHA-256
of the entire signed bundle, sender identity, recipient fingerprint, ephemeral key,
KEM ciphertext and bundle expiry. It signs canonical `{intro, firstHash}` under
`DROP-HYBRID-1/init\0`; `firstHash` binds the entire initial encrypted ratchet frame.

Shared input, in order, is three 32-byte DH outputs followed by the 32-byte KEM secret:
`DH(IKa, SPKb) || DH(EKa, IKb) || DH(EKa, SPKb) || SS_KEM`.
The initial root is 32 bytes from HKDF-SHA256. Salt is SHA-256 of
`DROP-HYBRID-1/transcript\0` plus canonical intro. Info is
`DROP-HYBRID-1/initial-root\0`. Roles, keys, KEM ciphertext and expiry are transcript
bound. Invalid/low-order X25519 inputs are rejected by the native DH operation.

The recipient checks the pinned sender, recipient binding, its stored bundle equality,
intro and both signatures before decapsulation. It accepts only after first-message
AEAD authentication. Session state, durable inbox and removal of both private prekeys
share one transaction. A failed or rolled-back acceptance consumes nothing.

## Ratchet and message encryption

The initial recipient ratchet key is the one-time X25519 prekey. The initiator starts
a sending chain with a fresh ratchet DH. State follows the published Double Ratchet
state machine: new remote DH replaces receiving/sending chains; skipped keys are
indexed by remote DH and message number. Every operation drafts a copy; failed
authentication discards it. The full draft is saved atomically on success.

Root update: HKDF-SHA256(DH output, salt=current root, info=
`DROP-HYBRID-1/ratchet-root\0`, length=64), split into root then chain.
Chain update: next chain = HMAC-SHA256(chain, byte `02`); message key =
HMAC-SHA256(chain, byte `01`). Each message uses AES-256-GCM, random 96-bit nonce,
128-bit tag. AAD is `DROP-HYBRID-1/message\0` followed by canonical JSON containing
profile, session id, transcript digest, expiry, sender/recipient fingerprints and
the complete header `{profile,type,sid,dh,pn,n}`. Header fields remain public.

Canonical JSON recursively sorts object keys, retains array order and accepts only
nonnegative safe-integer numbers. Received bytes must exactly equal reserialization;
duplicate keys, alternative numeric encodings, extra whitespace and invalid UTF-8
are rejected. Every structure has an exact field set and bounded binary lengths.

Limits: 64 KiB plaintext; 110000-byte wire; 32 skipped keys per gap and 64 total;
4096 messages per sending chain; 8192 combined sends/receives per session; seven-day
maximum invite/session lifetime. Store caps: 32 prekeys, 64 sessions, 256 pending
sends, 256 pending receives, 1024 revocation tombstones. No silent key eviction or
automatic state reset. Failures leave state unchanged. Expired invites are explicitly
discarded; exhausted sessions require a new pinned handshake and retirement of old state.

## Persistence and deviations requiring review

SQLite `BEGIN IMMEDIATE`, WAL and `synchronous=FULL` serialize processes. All record
values are AES-256-GCM encrypted with an external 32-byte vault key and random nonce;
AAD binds random store id, record kind, id and session owner. A clear indexed owner
lets revocation delete its queues without decrypting unrelated records. Queue retries return stored ciphertext,
never re-encrypt an old message key. A durable plaintext inbox supports crash recovery
before application acknowledgement. Clear SQL metadata remains observable.

Differences from PQXDH: separate signing/DH identities; Ed25519 plus ML-DSA signatures;
only one-time bundles (no reusable signed or last-resort prekeys); three DHs with a
one-time SPK instead of a distinct optional OPK; explicit transcript-salted SHA-256
KDF; signature binding to the first frame; new canonical JSON encoding. These are
new composition choices, not claims that PQXDH's proof transfers to this profile.
Ratchet scope is section 3 only: no header encryption, SPQR or Triple Ratchet.

The profile targets hybrid establishment and protection of recorded traffic under
the primitive assumptions. It **does not provide continuous post-quantum healing**
after session-state compromise. Classical DH recovery is the only ongoing asymmetric
ratchet. Refresh post-quantum entropy by completing a new authenticated one-time
hybrid handshake. Protocol review must assess this composition before production use.
