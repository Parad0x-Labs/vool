import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { identity, publicIdentity, fingerprint, encode, decode, LIMITS, pair } from '../src/crypto.mjs';
import { makePrekey, initiateDraft, acceptDraft, inspectInit, sendDraft, receiveDraft } from '../src/session.mjs';

function peers() {
  const a = identity(), b = identity(), now = Date.now();
  const prekey = makePrekey(b, now);
  const started = initiateDraft(a, prekey.bundle, fingerprint(publicIdentity(b)), Buffer.from('first offline task'), now);
  const accepted = acceptDraft(b, prekey, inspectInit(started.wire), fingerprint(publicIdentity(a)), now);
  assert.equal(accepted.plaintext.toString(), 'first offline task');
  return { a, b, alice: started.state, bob: accepted.state, started, prekey, now };
}

test('offline hybrid initiation and repeated genuine two-direction DH ratchets', () => {
  let { alice, bob, now } = peers();
  for (let i = 0; i < 12; i++) {
    const body = randomBytes(257 + i);
    const reply = sendDraft(bob, body, now); bob = reply.state;
    const received = receiveDraft(alice, reply.frame, now); alice = received.state;
    assert.deepEqual(received.plaintext, body);
    const answer = sendDraft(alice, randomBytes(17), now); alice = answer.state;
    bob = receiveDraft(bob, answer.frame, now).state;
  }
  assert.equal(alice.total, 25); assert.equal(bob.total, 25);
});

test('out of order within/across ratchets, skipped keys consumed once', () => {
  let { alice, bob, now } = peers();
  const delayed = sendDraft(alice, Buffer.from('delayed old chain'), now); alice = delayed.state;
  const b1 = sendDraft(bob, Buffer.from('reply'), now); bob = b1.state;
  alice = receiveDraft(alice, b1.frame, now).state;
  const next = sendDraft(alice, Buffer.from('new chain'), now); alice = next.state;
  bob = receiveDraft(bob, next.frame, now).state;
  assert.equal(receiveDraft(bob, delayed.frame, now).plaintext.toString(), 'delayed old chain');
  const once = receiveDraft(bob, delayed.frame, now).state;
  assert.throws(() => receiveDraft(once, delayed.frame, now));
  const batch = [];
  for (let i = 0; i < 8; i++) {
    const r = sendDraft(alice, Buffer.from(`batch:${i}`), now); alice = r.state; batch.push(r.frame);
  }
  bob = once;
  for (const n of [7, 2, 0, 4, 1, 6, 3, 5]) {
    const r = receiveDraft(bob, batch[n], now); bob = r.state;
    assert.equal(r.plaintext.toString(), `batch:${n}`);
  }
  assert.equal(Object.keys(bob.skipped).length, 0);
});

test('forgeries, wrong identities and unknown profiles never advance input states', () => {
  const { a, b, alice, bob, now, prekey, started } = peers();
  const good = sendDraft(alice, Buffer.from('unchanged on rejection'), now).frame;
  const before = encode(bob);
  for (const mutate of [
    f => { f.box.tag = f.box.tag.replace(/^./, f.box.tag[0] === 'A' ? 'B' : 'A'); },
    f => { f.n = 3; }, f => { f.pn = 3; }, f => { f.dh = pair('x25519').public; },
    f => { f.profile = 'downgrade'; }, f => { f.extra = true; },
  ]) {
    const changed = structuredClone(good); mutate(changed);
    assert.throws(() => receiveDraft(bob, changed, now));
    assert.deepEqual(encode(bob), before);
  }
  assert.equal(receiveDraft(bob, good, now).plaintext.toString(), 'unchanged on rejection');
  assert.throws(() => initiateDraft(a, prekey.bundle, fingerprint(publicIdentity(identity())), Buffer.from('bad pin'), now), /IDENTITY_MISMATCH/);
  assert.throws(() => acceptDraft(b, prekey, inspectInit(started.wire), fingerprint(publicIdentity(identity())), now), /IDENTITY_MISMATCH/);
  const bad = decode(started.wire); bad.intro.kem = bad.intro.kem.replace(/^./, bad.intro.kem[0] === 'A' ? 'B' : 'A');
  assert.throws(() => acceptDraft(b, prekey, inspectInit(encode(bad)), fingerprint(publicIdentity(a)), now), /AUTH_FAILED/);
});

test('payload, expiry, skip gap and chain/session exhaustion fail closed', () => {
  const { alice, bob, now } = peers();
  assert.throws(() => sendDraft(alice, Buffer.alloc(LIMITS.plaintextBytes + 1), now), /PAYLOAD_LIMIT/);
  assert.throws(() => sendDraft(alice, Buffer.from('expired'), alice.expires), /EXPIRED/);
  assert.throws(() => sendDraft({ ...alice, ns: LIMITS.chainMessages }, Buffer.from('full'), now), /CHAIN_EXHAUSTED/);
  assert.throws(() => sendDraft({ ...alice, total: LIMITS.sessionMessages }, Buffer.from('full'), now), /SESSION_EXHAUSTED/);
  const good = sendDraft(alice, Buffer.from('gap'), now).frame;
  assert.throws(() => receiveDraft(bob, { ...good, n: LIMITS.skipGap + 2 }, now), /SKIP_LIMIT/);
});
