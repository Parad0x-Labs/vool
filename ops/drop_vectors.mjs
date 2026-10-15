// Regenerates tests/fixtures/drop/js_transcript.json from the reference JavaScript
// DROP-HYBRID-1 package. Needs Node >= 24.7. The keys inside are throwaway test keys.
// Usage: node ops/drop_vectors.mjs > tests/fixtures/drop/js_transcript.json
import { identity, publicIdentity, fingerprint, canonical, b64, encode } from '../third_party/drop-session/src/crypto.mjs';
import { makePrekey, initiateDraft, inspectInit, acceptDraft, sendDraft, receiveDraft } from '../third_party/drop-session/src/session.mjs';

const now = 1791000000000;
const alice = identity(), bob = identity();
const alicePin = fingerprint(publicIdentity(alice)), bobPin = fingerprint(publicIdentity(bob));
const prekey = makePrekey(bob, now, 7 * 86400000);
const text = s => Buffer.from(s, 'utf8');
const init = initiateDraft(alice, prekey.bundle, bobPin, text('hello from A'), now);
let a = init.state;
const aliceAfterInit = structuredClone(a);
const accepted = acceptDraft(bob, prekey, inspectInit(init.wire), alicePin, now);
let b = accepted.state;
const bobAfterAccept = structuredClone(b);
const frames = [];
const send = (who, plain) => {
  const r = sendDraft(who === 'A' ? a : b, text(plain), now + frames.length + 1);
  if (who === 'A') a = r.state; else b = r.state;
  const f = { from: who, wire: b64(encode(r.frame)), plaintext: plain };
  frames.push(f); return f;
};
const recv = (who, f) => {
  const r = receiveDraft(who === 'A' ? a : b, JSON.parse(Buffer.from(f.wire, 'base64url')), now + 100);
  if (who === 'A') a = r.state; else b = r.state;
  if (r.plaintext.toString('utf8') !== f.plaintext) throw new Error('self-check');
};
const m1 = send('A', 'first chain, message 1'), m2 = send('A', 'first chain, message 2 é☃😀');
const r0 = send('B', 'reply 0'), r1 = send('B', 'reply 1');
recv('A', r1); recv('A', r0);
const m3 = send('A', 'second chain after the DH ratchet');
recv('B', m2); recv('B', m1); recv('B', m3);
const r2 = send('B', 'reply after B ratchets again');
recv('A', r2);
const canon = [
  { k: 'plain', v: { b: 1, a: [true, false, null, 0, 9007199254740991], c: 'x' } },
  { k: 'escapes', v: { s: '"\\\b\f\n\r\t\u0000\u001f\u007f é😀' } },
  { k: 'keys', v: { 'Z': 1, 'a': 2, 'é': 3, '😀': 4, '￿': 5, '': 6 } },
];
console.log(JSON.stringify({
  note: 'Throwaway test vectors from third_party/drop-session (reference JS). UNREVIEWED protocol.',
  now, alicePin, bobPin, bobKeys: bob, bobPrekey: prekey, initWire: b64(init.wire),
  initPlaintext: 'hello from A', aliceStateAfterInit: aliceAfterInit,
  bobStateAfterAccept: bobAfterAccept,
  // Order in which each side processes the other's frames (indexes into frames), starting
  // from the dumped JS state; a ratchet key is random, so each side resumes from JS state.
  bobReceives: [frames.indexOf(m2), frames.indexOf(m1), frames.indexOf(m3)],
  aliceReceives: [frames.indexOf(r1), frames.indexOf(r0)],
  frames, canonical: canon.map(c => ({ name: c.k, value: c.v, text: canonical(c.v) })),
}, null, 1));
