import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { randomBytes } from 'node:crypto';
import { DropClient, LIMITS } from '../src/index.mjs';
import { identity, publicIdentity, fingerprint, encode } from '../src/crypto.mjs';
import { makePrekey, initiateDraft, acceptDraft, inspectInit, sendDraft, receiveDraft } from '../src/session.mjs';

test('global skipped-key budget is shared across chains; valid delayed keys free capacity', () => {
  const a = identity(), b = identity(), now = Date.now(), prekey = makePrekey(b, now);
  const initial = initiateDraft(a, prekey.bundle, fingerprint(publicIdentity(b)), Buffer.from('start'), now);
  let alice = initial.state, bob = acceptDraft(b, prekey, inspectInit(initial.wire), fingerprint(publicIdentity(a)), now).state;
  const old = [];
  for (let epoch = 0; epoch < 2; epoch++) {
    const batch = [];
    for (let i = 0; i < 33; i++) { const r = sendDraft(alice, Buffer.from(`epoch ${epoch} n ${i}`), now); alice = r.state; batch.push(r.frame); }
    bob = receiveDraft(bob, batch[32], now).state; old.push(batch[0]);
    const reply = sendDraft(bob, Buffer.from('rotate'), now); bob = reply.state;
    alice = receiveDraft(alice, reply.frame, now).state;
  }
  assert.equal(Object.keys(bob.skipped).length, LIMITS.skippedKeys);
  const first = sendDraft(alice, Buffer.from('gap zero'), now); alice = first.state;
  const second = sendDraft(alice, Buffer.from('gap one'), now); alice = second.state;
  const before = encode(bob);
  assert.throws(() => receiveDraft(bob, second.frame, now), /SKIP_LIMIT/);
  assert.deepEqual(encode(bob), before);
  bob = receiveDraft(bob, old[0], now).state;
  bob = receiveDraft(bob, second.frame, now).state;
  assert.equal(receiveDraft(bob, first.frame, now).plaintext.toString(), 'gap zero');
});

test('invite pool rejects exhaustion, discard frees capacity, expiry and malformed input fail closed', t => {
  const root = mkdtempSync(join(tmpdir(), 'drop-bounds-'));
  const a = new DropClient({ directory: join(root, 'a'), vaultKey: randomBytes(32) });
  const b = new DropClient({ directory: join(root, 'b'), vaultKey: randomBytes(32) });
  t.after(() => { a.close(); b.close(); });
  const now = Date.now();
  const invite = b.issueInvite({ now, ttl: 10 });
  for (let i = 1; i < LIMITS.prekeys; i++) b.issueInvite();
  assert.throws(() => b.issueInvite(), /STORE_LIMIT/);
  assert.throws(() => a.start({ invite, peerFingerprint: b.fingerprint, plaintext: Buffer.from('expired'), now: now + 10 }), /EXPIRED/);
  assert.throws(() => a.start({ invite, peerFingerprint: b.fingerprint, plaintext: 'must be a Buffer', now }), /PAYLOAD_LIMIT/);
  assert.throws(() => a.receive({ wire: Buffer.alloc(LIMITS.wireBytes + 1) }), /INVALID_FORMAT/);
  assert.throws(() => a.receive({ wire: Buffer.from('{"profile":"unknown"}') }));
  const prekey = JSON.parse(invite.toString()).body.id; b.discardInvite(prekey);
  assert.ok(b.issueInvite().length > 0);
  assert.equal(a.pendingSends().length, 0); assert.equal(a.pendingReceives().length, 0);
});

test('outbox cap rolls back ratchet advancement and subsequent valid send uses the correct counter', t => {
  const root = mkdtempSync(join(tmpdir(), 'drop-cap-')), key = randomBytes(32), peerKey = randomBytes(32);
  const directory = join(root, 'a');
  const a = new DropClient({ directory, vaultKey: key });
  const b = new DropClient({ directory: join(root, 'b'), vaultKey: peerKey });
  t.after(() => { a.close(); b.close(); key.fill(0); peerKey.fill(0); });
  const started = a.start({ invite: b.issueInvite(), peerFingerprint: b.fingerprint, plaintext: Buffer.from('first') });
  const accepted = b.accept({ wire: started.wire, peerFingerprint: a.fingerprint });
  b.acknowledgeReceive(accepted.delivery);
  for (let i = 1; i < LIMITS.queued; i++) {
    const queued = a.send({ session: started.session, plaintext: Buffer.from(`real queued message ${i}`) });
    const delivered = b.receive({ wire: queued.wire }); b.acknowledgeReceive(delivered.delivery);
  }
  assert.throws(() => a.send({ session: started.session, plaintext: Buffer.from('must roll back') }), /STORE_LIMIT/);
  a.acknowledgeSend(started.request);
  const sent = a.send({ session: started.session, plaintext: Buffer.from('counter still correct') });
  assert.equal(JSON.parse(sent.wire.toString()).n, LIMITS.queued);
  assert.equal(b.receive({ wire: sent.wire }).plaintext.toString(), 'counter still correct');
});

test('full real inbox rolls back a new-session acceptance without consuming its prekey', t => {
  const root = mkdtempSync(join(tmpdir(), 'drop-inbox-cap-'));
  const a = new DropClient({ directory: join(root, 'a'), vaultKey: randomBytes(32) });
  const b = new DropClient({ directory: join(root, 'b'), vaultKey: randomBytes(32) });
  t.after(() => { a.close(); b.close(); });
  const started = a.start({ invite: b.issueInvite(), peerFingerprint: b.fingerprint, plaintext: Buffer.from('fill first slot') });
  const first = b.accept({ wire: started.wire, peerFingerprint: a.fingerprint }); a.acknowledgeSend(started.request);
  for (let i = 1; i < LIMITS.queued; i++) {
    const sent = a.send({ session: started.session, plaintext: Buffer.from(`fill inbox ${i}`) });
    b.receive({ wire: sent.wire }); a.acknowledgeSend(sent.request);
  }
  assert.equal(b.pendingReceives().length, LIMITS.queued);
  const another = a.start({ invite: b.issueInvite(), peerFingerprint: b.fingerprint, plaintext: Buffer.from('new handshake must wait') });
  assert.throws(() => b.accept({ wire: another.wire, peerFingerprint: a.fingerprint }), /STORE_LIMIT/);
  b.acknowledgeReceive(first.delivery);
  assert.equal(b.accept({ wire: another.wire, peerFingerprint: a.fingerprint }).plaintext.toString(), 'new handshake must wait');
  assert.equal(b.pendingReceives().length, LIMITS.queued);
});
