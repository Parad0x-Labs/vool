import { encapsulate, decapsulate } from 'node:crypto';
import {
  PROFILE, LIMITS, demand, fields, clock, integer, id, freshId, b64, unb64,
  encode, decode, domain, hash, hkdf, pair, publicKey, privateKey, dh,
  publicIdentity, pin, signatures, checkSignatures, seal, open, rootStep, chainStep,
} from './crypto.mjs';

// Internal draft operations. Only DropClient commits these before exposing results.
export function makePrekey(keys, now, ttl = 86400000) {
  clock(now); integer(ttl, LIMITS.sessionMs); demand(ttl > 0, 'EXPIRED');
  const x = pair('x25519'), pq = pair('ml-kem-768');
  const body = {
    profile: PROFILE, id: freshId(), issued: now, expires: now + ttl,
    identity: publicIdentity(keys), x: x.public, pq: pq.public,
  };
  return { x, pq, bundle: { body, signatures: signatures(keys, 'bundle', body) } };
}

export function validateBundle(bundle, expectedPin, now) {
  clock(now); fields(bundle, ['body', 'signatures']);
  const b = bundle.body;
  fields(b, ['profile', 'id', 'issued', 'expires', 'identity', 'x', 'pq']);
  demand(b.profile === PROFILE, 'UNSUPPORTED_PROFILE'); id(b.id);
  const peer = pin(b.identity, expectedPin);
  integer(b.issued); integer(b.expires);
  demand(b.issued <= now && now < b.expires && b.expires - b.issued <= LIMITS.sessionMs, 'EXPIRED');
  publicKey(b.x, 'x25519'); publicKey(b.pq, 'ml-kem-768');
  checkSignatures(b.identity, 'bundle', b, bundle.signatures);
  return peer;
}

function initialRoot(parts, intro) {
  const combined = Buffer.concat(parts);
  try {
    return b64(hkdf(combined, hash(domain('transcript', encode(intro))),
      domain('initial-root', Buffer.alloc(0)), 32));
  } finally { combined.fill(0); for (const p of parts) p.fill(0); }
}

function stateFor(intro, self, peer, root, ownDh, remoteDh) {
  return {
    profile: PROFILE, sid: intro.sid, self, peer, expires: intro.expires,
    transcript: b64(hash(domain('transcript', encode(intro)))),
    root, ownDh, remoteDh, sendChain: null, receiveChain: null,
    ns: 0, nr: 0, pn: 0, total: 0, skipped: {},
  };
}

function alive(state, now) {
  clock(now);
  demand(state.profile === PROFILE, 'UNSUPPORTED_PROFILE');
  demand(now < state.expires, 'EXPIRED');
  demand(state.total < LIMITS.sessionMessages, 'SESSION_EXHAUSTED');
}
function aad(state, header, sending) {
  return domain('message', encode({
    profile: PROFILE, sid: state.sid, transcript: state.transcript, expires: state.expires,
    sender: sending ? state.self : state.peer, recipient: sending ? state.peer : state.self,
    header,
  }));
}

export function sendDraft(input, plaintext, now) {
  alive(input, now);
  demand(Buffer.isBuffer(plaintext) && plaintext.length <= LIMITS.plaintextBytes, 'PAYLOAD_LIMIT');
  demand(input.ns < LIMITS.chainMessages, 'CHAIN_EXHAUSTED');
  const state = structuredClone(input);
  const header = { profile: PROFILE, type: 'message', sid: state.sid, dh: state.ownDh.public, pn: state.pn, n: state.ns };
  const [next, key] = chainStep(state.sendChain);
  try {
    const frame = { ...header, box: seal(key, plaintext, aad(state, header, true)) };
    state.sendChain = next; state.ns++; state.total++;
    return { state, frame };
  } finally { key.fill(0); }
}

export function checkFrame(frame) {
  fields(frame, ['profile', 'type', 'sid', 'dh', 'pn', 'n', 'box']);
  demand(frame.profile === PROFILE, 'UNSUPPORTED_PROFILE');
  demand(frame.type === 'message', 'INVALID_FORMAT'); id(frame.sid);
  integer(frame.n, LIMITS.chainMessages - 1); integer(frame.pn, LIMITS.chainMessages);
  publicKey(frame.dh, 'x25519');
  fields(frame.box, ['nonce', 'body', 'tag']);
  unb64(frame.box.nonce, 12); unb64(frame.box.tag, 16); unb64(frame.box.body, 0, LIMITS.plaintextBytes);
}
function skippedId(remote, n) { return `${remote}:${n}`; }
function skip(state, until) {
  demand(until - state.nr <= LIMITS.skipGap, 'SKIP_LIMIT');
  if (state.receiveChain === null) { demand(until === 0, 'INVALID_COUNTER'); return; }
  demand(until >= state.nr, 'REPLAY');
  demand(Object.keys(state.skipped).length + until - state.nr <= LIMITS.skippedKeys, 'SKIP_LIMIT');
  while (state.nr < until) {
    const [next, key] = chainStep(state.receiveChain);
    state.skipped[skippedId(state.remoteDh, state.nr)] = b64(key);
    key.fill(0); state.receiveChain = next; state.nr++;
  }
}

export function receiveDraft(input, frame, now) {
  alive(input, now); checkFrame(frame); demand(frame.sid === input.sid, 'WRONG_SESSION');
  const state = structuredClone(input);
  const header = { profile: frame.profile, type: frame.type, sid: frame.sid, dh: frame.dh, pn: frame.pn, n: frame.n };
  const slot = skippedId(frame.dh, frame.n);
  let key;
  if (Object.hasOwn(state.skipped, slot)) {
    key = unb64(state.skipped[slot], 32);
    delete state.skipped[slot];
  } else {
    if (frame.dh !== state.remoteDh) {
      skip(state, frame.pn);
      state.pn = state.ns; state.ns = 0; state.nr = 0; state.remoteDh = frame.dh;
      [state.root, state.receiveChain] = rootStep(state.root, dh(state.ownDh, state.remoteDh));
      state.ownDh = pair('x25519');
      [state.root, state.sendChain] = rootStep(state.root, dh(state.ownDh, state.remoteDh));
    }
    skip(state, frame.n);
    [state.receiveChain, key] = chainStep(state.receiveChain);
    state.nr++;
  }
  try {
    const plaintext = open(key, frame.box, aad(state, header, false));
    state.total++;
    return { state, plaintext };
  } finally { key.fill(0); }
}

export function initiateDraft(keys, bundle, expectedPin, plaintext, now) {
  const recipient = validateBundle(bundle, expectedPin, now);
  const self = pin(publicIdentity(keys), pinSelf(keys));
  demand(self !== recipient, 'SELF_SESSION');
  const ephemeral = pair('x25519'), ratchet = pair('x25519');
  const kem = encapsulate(publicKey(bundle.body.pq, 'ml-kem-768'));
  const intro = {
    profile: PROFILE, sid: freshId(), prekey: bundle.body.id,
    bundleHash: b64(hash(encode(bundle))), sender: publicIdentity(keys), recipient,
    ephemeral: ephemeral.public, kem: b64(kem.ciphertext), expires: bundle.body.expires,
  };
  const root = initialRoot([
    dh(keys.dh, bundle.body.x), dh(ephemeral, bundle.body.identity.dh),
    dh(ephemeral, bundle.body.x), kem.sharedKey,
  ], intro);
  const initial = stateFor(intro, self, recipient, root, ratchet, bundle.body.x);
  [initial.root, initial.sendChain] = rootStep(initial.root, dh(ratchet, bundle.body.x));
  const sent = sendDraft(initial, plaintext, now);
  const signed = { intro, firstHash: b64(hash(encode(sent.frame))) };
  const wire = encode({
    profile: PROFILE, type: 'init', bundle, intro, message: sent.frame,
    signatures: signatures(keys, 'init', signed),
  });
  demand(wire.length <= LIMITS.wireBytes, 'PAYLOAD_LIMIT');
  return { state: sent.state, wire };
}

function pinSelf(keys) { return b64(hash(domain('identity', encode(publicIdentity(keys))))); }

export function inspectInit(wire) {
  const packet = decode(wire);
  fields(packet, ['profile', 'type', 'bundle', 'intro', 'message', 'signatures']);
  demand(packet.profile === PROFILE, 'UNSUPPORTED_PROFILE');
  demand(packet.type === 'init', 'INVALID_FORMAT');
  const i = packet.intro;
  fields(i, ['profile', 'sid', 'prekey', 'bundleHash', 'sender', 'recipient', 'ephemeral', 'kem', 'expires']);
  demand(i.profile === PROFILE, 'UNSUPPORTED_PROFILE'); id(i.sid); id(i.prekey);
  unb64(i.bundleHash, 32); unb64(i.recipient, 32); unb64(i.kem, 1088); integer(i.expires);
  publicKey(i.ephemeral, 'x25519'); checkFrame(packet.message);
  demand(packet.message.sid === i.sid && packet.message.n === 0 && packet.message.pn === 0, 'INVALID_COUNTER');
  return packet;
}

export function acceptDraft(keys, prekey, packet, expectedPin, now) {
  const i = packet.intro, self = pinSelf(keys);
  demand(i.recipient === self, 'IDENTITY_MISMATCH');
  const peer = pin(i.sender, expectedPin); demand(peer !== self, 'SELF_SESSION');
  validateBundle(packet.bundle, self, now);
  demand(encode(prekey.bundle).equals(encode(packet.bundle)), 'UNKNOWN_PREKEY');
  demand(i.prekey === prekey.bundle.body.id && i.bundleHash === b64(hash(encode(packet.bundle))), 'UNKNOWN_PREKEY');
  demand(i.expires === packet.bundle.body.expires, 'EXPIRED');
  checkSignatures(i.sender, 'init', { intro: i, firstHash: b64(hash(encode(packet.message))) }, packet.signatures);
  const pq = decapsulate(privateKey(prekey.pq.private, 'ml-kem-768'), unb64(i.kem, 1088));
  const root = initialRoot([
    dh(prekey.x, i.sender.dh), dh(keys.dh, i.ephemeral), dh(prekey.x, i.ephemeral), pq,
  ], i);
  return receiveDraft(stateFor(i, self, peer, root, prekey.x, null), packet.message, now);
}
