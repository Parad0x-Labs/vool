# Drop Session

MIT-licensed, local review candidate for asynchronous encrypted two-party messaging.
Native ML-KEM-768 + X25519 establish the session. Ed25519 **and** ML-DSA-65 authenticate
the identities. An X25519 Double Ratchet advances per-message keys. Encrypted SQLite
transactions own session state, one-time invitations and retry queues.

**Unreviewed protocol profile. Not production cleared.** This is a standalone library;
VOOL transport and task execution do not import it. It is not Signal wire compatible
and does not implement continuous post-quantum compromise recovery. Read
[PROTOCOL.md](PROTOCOL.md) and [SECURITY.md](SECURITY.md) before integration.

Node >=24.7 with native ML-KEM, ML-DSA and SQLite is required. Tested with Node 26.1.0 /
OpenSSL 3.5.6 on macOS arm64. No npm dependencies or installation step.
The file-store API refuses Windows until equivalent ACL handling is implemented.

```sh
node --test test/*.test.mjs
node demo.mjs
```

The demo creates disposable encrypted databases in the runtime's temporary directory.
Use a private temporary directory on the intended volume. It exchanges a fresh task
payload carrying a fresh sample, verifies the peer's computed SHA-256 result,
restarts both parties, retries identical ciphertext,
rejects replay and blocks a forgotten session. It performs no network or chain action.

```js
import { randomBytes } from 'node:crypto';
import { DropClient } from './src/index.mjs';

const aliceKey = randomBytes(32), bobKey = randomBytes(32);
const alice = new DropClient({ directory: './private-alice', vaultKey: aliceKey });
const bob = new DropClient({ directory: './private-bob', vaultKey: bobKey });
try {
  // Authenticate these fingerprints through an independently trusted channel.
  const alicePin = alice.fingerprint, bobPin = bob.fingerprint;
  const invite = bob.issueInvite();
  const sent = alice.start({ invite, peerFingerprint: bobPin,
    plaintext: Buffer.from('read-only task payload') });
  const accepted = bob.accept({ wire: sent.wire, peerFingerprint: alicePin });
  const reply = bob.send({ session: accepted.session, plaintext: Buffer.from('result') });
  const result = alice.receive({ wire: reply.wire });
  alice.acknowledgeReceive(result.delivery);
  bob.acknowledgeReceive(accepted.delivery);
  bob.acknowledgeSend(reply.request);
  alice.acknowledgeSend(sent.request);
} finally { alice.close(); bob.close(); aliceKey.fill(0); bobKey.fill(0); }
```

Keep vault keys in the caller's protected key store and reload the same key after a
restart. The library never saves the vault key. Lost key means lost state; opening
existing state with another key fails closed. Never restore an older state snapshot.

| Operation | Durable behavior |
|---|---|
| `issueInvite({now, ttl})` | Saves a fresh one-time prekey before returning canonical invite bytes. Default TTL 24 h. |
| `discardInvite(prekeyId)` | Deletes an unused/expired prekey. No reusable fallback exists. |
| `start({invite, peerFingerprint, plaintext, request, now})` | Pins recipient; saves advanced state plus exact initial ciphertext atomically. |
| `accept({wire, peerFingerprint, now})` | Pins initiator; authenticates first message, consumes prekey and saves plaintext delivery atomically. |
| `send({session, plaintext, request, now})` | Saves advanced state and exact ciphertext before returning. A repeated pending request must bind identical arguments. |
| `receive({wire, now})` | Verifies before committing; saves accepted plaintext delivery before returning. Replays fail. |
| `pendingSends()` / `pendingReceives()` | Recovery queues after a crash; deliveries carry stable ids. |
| `acknowledgeSend(request)` / `acknowledgeReceive(delivery)` | Drops a queue item after authenticated delivery/application handling. |
| `forgetSession(session)` | Atomically tombstones the id and deletes session state plus queued content. Survives restart. |
| `close()` | Closes SQLite and zeroes the private copy of the vault key. |

`request` defaults to a random 128-bit id; allocate and persist one before calling if
the caller must recover a result lost before it receives that id. Reuse a request only
for retrying the same pending operation. Acknowledgement removes that request's
idempotency record. Serialize application effects with delivery ids: the library
provides a durable inbox, not exactly-once execution of external effects.

All plaintexts are Buffers <=64 KiB. Wire frames are canonical JSON bytes; pass bytes,
not parsed objects. Caller-supplied `now` is a trusted clock override, never a remote
field. Expired messages cannot be accepted even if ciphertext remains queued.
Limits are exported as `LIMITS`; capacity errors roll back the entire operation.
Do not catch authentication/storage errors and substitute empty state.

Public fingerprints, session ids, headers, sizes and timing remain visible. The
transport must supply delivery, quotas and independently authenticated pin exchange.
Decrypted task text is data: execution still requires the application's permissions.

Original library source: [MIT](LICENSE). Runtime dependencies and imported test DATA:
[THIRD-PARTY.md](THIRD-PARTY.md). A published primitive standard and passing test
vectors do not establish security of this new protocol composition.
