import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash, randomBytes } from 'node:crypto';
import { DropClient } from './src/index.mjs';

// Separate persistent identities; untrusted relay holds only ciphertext bytes.
const root = mkdtempSync(join(tmpdir(), 'drop-demo-'));
const aKey = randomBytes(32), bKey = randomBytes(32);
let alice = new DropClient({ directory: join(root, 'alice'), vaultKey: aKey });
let bob = new DropClient({ directory: join(root, 'bob'), vaultKey: bKey });
try {
  const alicePin = alice.fingerprint, bobPin = bob.fingerprint;
  const sample = randomBytes(97);
  // This demo transfers the pins directly. Real callers use a separately verified channel.
  const invite = bob.issueInvite(); bob.close();
  const outbound = alice.start({ invite, peerFingerprint: bobPin,
    plaintext: Buffer.from(JSON.stringify({ task: 'sha256', sample: sample.toString('base64url'), nonce: randomBytes(16).toString('hex') })) });
  alice.close();
  bob = new DropClient({ directory: join(root, 'bob'), vaultKey: bKey });
  const accepted = bob.accept({ wire: outbound.wire, peerFingerprint: alicePin });
  const task = JSON.parse(accepted.plaintext.toString());
  // The demo explicitly allows only hashing the provided sample. Decryption itself
  // grants no application permission, and no arbitrary task executor is invoked.
  if (task.task !== 'sha256' || typeof task.sample !== 'string') throw new Error('unsupported demo task');
  const digest = createHash('sha256').update(Buffer.from(task.sample, 'base64url')).digest('hex');
  const reply = bob.send({ session: accepted.session,
    plaintext: Buffer.from(JSON.stringify({ digest, taskNonce: task.nonce })) });
  bob.close();
  bob = new DropClient({ directory: join(root, 'bob'), vaultKey: bKey });
  const retry = bob.send({ session: reply.session, request: reply.request,
    plaintext: Buffer.from(JSON.stringify({ digest, taskNonce: task.nonce })) });
  if (!retry.wire.equals(reply.wire)) throw new Error('durable retry changed ciphertext');
  alice = new DropClient({ directory: join(root, 'alice'), vaultKey: aKey });
  const received = alice.receive({ wire: retry.wire });
  const result = JSON.parse(received.plaintext.toString());
  if (result.taskNonce !== task.nonce) throw new Error('task/result correlation failed');
  if (result.digest !== createHash('sha256').update(sample).digest('hex')) throw new Error('peer computed the wrong result');
  let replayRejected = false;
  try { alice.receive({ wire: retry.wire }); } catch (e) { replayRejected = e.code === 'REPLAY'; }
  if (!replayRejected) throw new Error('replay was accepted');
  bob.acknowledgeReceive(accepted.delivery); bob.acknowledgeSend(reply.request);
  alice.acknowledgeReceive(received.delivery); alice.acknowledgeSend(outbound.request);
  alice.forgetSession(outbound.session);
  let forgottenBlocked = false;
  try { alice.receive({ wire: reply.wire }); } catch (e) { forgottenBlocked = e.code === 'SESSION_REVOKED'; }
  if (!forgottenBlocked) throw new Error('forgotten session resumed');
  console.log(JSON.stringify({
    offlineFirstMessage: true, restartBothSides: true, exactCiphertextRetry: true,
    taskResultCorrelated: true, peerComputedDigestVerified: true, replayRejected, forgottenBlocked,
    wireBytes: { invite: invite.length, first: outbound.wire.length, response: reply.wire.length },
  }, null, 2));
} finally { alice.close(); bob.close(); aKey.fill(0); bKey.fill(0); }
