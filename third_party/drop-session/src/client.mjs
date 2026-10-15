import {
  LIMITS, demand, freshId, id, b64, hash, fingerprint, identity,
  publicIdentity, decode, encode, clock, unb64,
} from './crypto.mjs';
import { makePrekey, validateBundle, initiateDraft, inspectInit, acceptDraft, sendDraft, receiveDraft } from './session.mjs';
import { EncryptedStore, vaultWire, readVaultWire } from './store.mjs';

function requestId(value) { return id(value); }
function inputBytes(value, max, code) {
  demand(Buffer.isBuffer(value) && value.length <= max, code);
  return Buffer.from(value);
}

export class DropClient {
  #store;
  #identity;
  #public;
  #pin;
  #closed = false;
  constructor({ directory, vaultKey }) {
    this.#store = new EncryptedStore(directory, vaultKey);
    try {
      this.#identity = this.#store.transaction(() => {
        const prior = this.#store.get('identity', 'self');
        if (prior) return prior;
        const keys = identity(); this.#store.put('identity', 'self', keys); return keys;
      });
      this.#public = publicIdentity(this.#identity);
      this.#pin = fingerprint(this.#public);
    } catch (error) { this.close(); throw error; }
  }
  #ready() { demand(!this.#closed, 'CLIENT_CLOSED'); }
  get fingerprint() { this.#ready(); return this.#pin; }
  get publicIdentity() { this.#ready(); return structuredClone(this.#public); }
  issueInvite({ now = Date.now(), ttl = 86400000 } = {}) {
    this.#ready();
    return this.#store.transaction(() => {
      const record = makePrekey(this.#identity, now, ttl);
      this.#store.put('prekey', record.bundle.body.id, record);
      return encode(record.bundle);
    });
  }
  discardInvite(prekeyId) {
    this.#ready(); id(prekeyId);
    this.#store.transaction(() => { this.#store.remove('prekey', prekeyId); });
  }
  start({ invite, peerFingerprint, plaintext, request = freshId(), now = Date.now() }) {
    this.#ready(); requestId(request); clock(now);
    plaintext = inputBytes(plaintext, LIMITS.plaintextBytes, 'PAYLOAD_LIMIT');
    invite = inputBytes(invite, LIMITS.wireBytes, 'INVALID_FORMAT');
    const bundle = decode(invite);
    validateBundle(bundle, peerFingerprint, now);
    return this.#store.transaction(() => {
      const old = this.#store.get('outbox', request);
      if (old) {
        const binding = b64(hash(encode({ type: 'start', inviteHash: b64(hash(invite)), peerFingerprint, payloadHash: b64(hash(plaintext)) })));
        demand(old.binding === binding, 'REQUEST_CONFLICT');
        return { request, session: old.session, wire: readVaultWire(old.wire) };
      }
      const result = initiateDraft(this.#identity, bundle, peerFingerprint, plaintext, now);
      demand(!this.#store.get('revoked', result.state.sid), 'SESSION_REVOKED');
      this.#store.put('session', result.state.sid, result.state);
      const binding = b64(hash(encode({ type: 'start', inviteHash: b64(hash(invite)), peerFingerprint, payloadHash: b64(hash(plaintext)) })));
      this.#store.put('outbox', request, { session: result.state.sid, wire: vaultWire(result.wire), binding });
      return { request, session: result.state.sid, wire: result.wire };
    });
  }
  send({ session, plaintext, request = freshId(), now = Date.now() }) {
    this.#ready(); id(session); requestId(request); clock(now);
    plaintext = inputBytes(plaintext, LIMITS.plaintextBytes, 'PAYLOAD_LIMIT');
    return this.#store.transaction(() => {
      this.#unrevoked(session);
      const binding = b64(hash(encode({ type: 'send', session, payloadHash: b64(hash(plaintext)) })));
      const old = this.#store.get('outbox', request);
      if (old) {
        demand(old.binding === binding, 'REQUEST_CONFLICT');
        return { request, session, wire: readVaultWire(old.wire) };
      }
      const state = this.#store.get('session', session); demand(state, 'UNKNOWN_SESSION');
      const result = sendDraft(state, plaintext, now), wire = encode(result.frame);
      this.#store.put('session', session, result.state);
      this.#store.put('outbox', request, { session, wire: vaultWire(wire), binding });
      return { request, session, wire };
    });
  }
  #unrevoked(session) { demand(!this.#store.get('revoked', session), 'SESSION_REVOKED'); }
  accept({ wire, peerFingerprint, now = Date.now() }) {
    this.#ready(); clock(now);
    wire = inputBytes(wire, LIMITS.wireBytes, 'INVALID_FORMAT');
    const packet = inspectInit(wire), session = packet.intro.sid;
    return this.#store.transaction(() => {
      this.#unrevoked(session);
      demand(!this.#store.get('session', session), 'REPLAY');
      const prekey = this.#store.get('prekey', packet.intro.prekey); demand(prekey, 'PREKEY_UNAVAILABLE');
      const result = acceptDraft(this.#identity, prekey, packet, peerFingerprint, now);
      this.#store.put('session', session, result.state);
      const delivery = this.#recordInbox(session, wire, result.plaintext);
      this.#store.remove('prekey', packet.intro.prekey);
      return { session, delivery, plaintext: result.plaintext };
    });
  }
  receive({ wire, now = Date.now() }) {
    this.#ready(); clock(now);
    wire = inputBytes(wire, LIMITS.wireBytes, 'INVALID_FORMAT');
    const frame = decode(wire); id(frame.sid);
    return this.#store.transaction(() => {
      this.#unrevoked(frame.sid);
      const state = this.#store.get('session', frame.sid); demand(state, 'UNKNOWN_SESSION');
      const result = receiveDraft(state, frame, now);
      this.#store.put('session', frame.sid, result.state);
      const delivery = this.#recordInbox(frame.sid, wire, result.plaintext);
      return { session: frame.sid, delivery, plaintext: result.plaintext };
    });
  }
  #recordInbox(session, wire, plaintext) {
    const delivery = b64(hash(wire));
    demand(!this.#store.get('inbox', delivery), 'REPLAY');
    this.#store.put('inbox', delivery, { session, plaintext: b64(plaintext) });
    return delivery;
  }
  pendingSends() {
    this.#ready();
    return this.#store.transaction(() => this.#store.list('outbox').map(r => {
      this.#unrevoked(r.value.session);
      return { request: r.id, session: r.value.session, wire: readVaultWire(r.value.wire) };
    }));
  }
  acknowledgeSend(request) {
    this.#ready(); requestId(request);
    this.#store.transaction(() => this.#store.remove('outbox', request));
  }
  pendingReceives() {
    this.#ready();
    return this.#store.transaction(() => this.#store.list('inbox').map(r => {
      this.#unrevoked(r.value.session);
      return { delivery: r.id, session: r.value.session,
        plaintext: unb64(r.value.plaintext, 0, LIMITS.plaintextBytes) };
    }));
  }
  acknowledgeReceive(delivery) {
    this.#ready(); unb64(delivery, 32);
    this.#store.transaction(() => this.#store.remove('inbox', delivery));
  }
  forgetSession(session) {
    this.#ready(); id(session);
    this.#store.transaction(() => {
      this.#store.put('revoked', session, { revoked: true });
      this.#store.remove('session', session);
      this.#store.removeSessionQueues(session);
    });
  }
  close() {
    if (this.#closed) return;
    this.#closed = true;
    this.#identity = null; this.#public = null;
    this.#store?.close();
  }
}
