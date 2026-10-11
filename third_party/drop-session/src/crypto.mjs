import {
  createCipheriv, createDecipheriv, createHash, createHmac,
  createPrivateKey, createPublicKey, diffieHellman, generateKeyPairSync,
  hkdfSync, randomBytes, sign, timingSafeEqual, verify,
} from 'node:crypto';

export const PROFILE = 'DROP-HYBRID-1';
export const LIMITS = Object.freeze({
  wireBytes: 110000, plaintextBytes: 65536, skipGap: 32, skippedKeys: 64,
  chainMessages: 4096, sessionMessages: 8192, sessionMs: 7 * 86400000,
  prekeys: 32, sessions: 64, queued: 256,
});

export class DropError extends Error {
  constructor(code) { super(code); this.name = 'DropError'; this.code = code; }
}
export function demand(ok, code = 'INVALID_FORMAT') {
  if (!ok) throw new DropError(code);
}
export const b64 = value => Buffer.from(value).toString('base64url');
export function unb64(value, size, max = size) {
  demand(typeof value === 'string' && /^[A-Za-z0-9_-]*$/.test(value));
  demand(value.length <= Math.ceil(max * 4 / 3));
  const out = Buffer.from(value, 'base64url');
  demand(out.length >= size && out.length <= max && b64(out) === value);
  return out;
}
export function canonical(value, depth = 0) {
  demand(depth <= 12, 'INVALID_FORMAT');
  if (value === null || typeof value === 'boolean' || typeof value === 'string')
    return JSON.stringify(value);
  if (typeof value === 'number') {
    demand(Number.isSafeInteger(value) && value >= 0);
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(v => canonical(v, depth + 1)).join(',')}]`;
  demand(value && Object.getPrototypeOf(value) === Object.prototype);
  return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k], depth + 1)}`).join(',')}}`;
}
export const encode = value => Buffer.from(canonical(value), 'utf8');
export function decode(wire) {
  demand(Buffer.isBuffer(wire) && wire.length <= LIMITS.wireBytes);
  let obj;
  try { obj = JSON.parse(wire.toString('utf8')); } catch { throw new DropError('INVALID_FORMAT'); }
  demand(encode(obj).equals(wire), 'NON_CANONICAL');
  return obj;
}
export function fields(value, names) {
  demand(value && Object.getPrototypeOf(value) === Object.prototype);
  demand(Object.keys(value).sort().join(',') === [...names].sort().join(','));
}
export function integer(value, max = Number.MAX_SAFE_INTEGER) {
  demand(Number.isSafeInteger(value) && value >= 0 && value <= max);
  return value;
}
export function clock(now) { return integer(now); }
export function id(value) { unb64(value, 16); return value; }
export const freshId = () => b64(randomBytes(16));
export const hash = data => createHash('sha256').update(data).digest();
export const hkdf = (input, salt, info, size) => Buffer.from(hkdfSync('sha256', input, salt, info, size));
export const domain = (label, data) => Buffer.concat([Buffer.from(`${PROFILE}/${label}\0`), data]);
export function equal(a, b) { return a.length === b.length && timingSafeEqual(a, b); }

export function pair(type) {
  const k = generateKeyPairSync(type);
  return {
    public: b64(k.publicKey.export({ type: 'spki', format: 'der' })),
    private: b64(k.privateKey.export({ type: 'pkcs8', format: 'der' })),
  };
}
const SPKI_SIZES = { x25519: 44, ed25519: 44, 'ml-kem-768': 1206, 'ml-dsa-65': 1974 };
export function publicKey(text, type) {
  const bytes = unb64(text, SPKI_SIZES[type]);
  let key;
  try { key = createPublicKey({ key: bytes, type: 'spki', format: 'der' }); }
  catch { throw new DropError('INVALID_KEY'); }
  demand(key.asymmetricKeyType === type, 'INVALID_KEY');
  demand(key.export({ type: 'spki', format: 'der' }).equals(bytes), 'INVALID_KEY');
  return key;
}
export function privateKey(text, type) {
  let key;
  try { key = createPrivateKey({ key: unb64(text, 1, 5000), type: 'pkcs8', format: 'der' }); }
  catch { throw new DropError('INVALID_KEY'); }
  demand(key.asymmetricKeyType === type, 'INVALID_KEY');
  return key;
}
export function dh(keypair, remote) {
  try {
    const result = diffieHellman({ privateKey: privateKey(keypair.private, 'x25519'), publicKey: publicKey(remote, 'x25519') });
    demand(!result.equals(Buffer.alloc(32)), 'INVALID_KEY');
    return result;
  } catch { throw new DropError('INVALID_KEY'); }
}
export function identity() {
  return { dh: pair('x25519'), ed: pair('ed25519'), pq: pair('ml-dsa-65') };
}
export function publicIdentity(keys) {
  return { dh: keys.dh.public, ed: keys.ed.public, pq: keys.pq.public };
}
export function checkIdentity(pub) {
  fields(pub, ['dh', 'ed', 'pq']);
  publicKey(pub.dh, 'x25519'); publicKey(pub.ed, 'ed25519'); publicKey(pub.pq, 'ml-dsa-65');
}
export function fingerprint(pub) {
  checkIdentity(pub);
  return b64(hash(domain('identity', encode(pub))));
}
export function pin(pub, expected) {
  const got = fingerprint(pub);
  demand(equal(unb64(got, 32), unb64(expected, 32)), 'IDENTITY_MISMATCH');
  return got;
}
export function signatures(keys, label, object) {
  const msg = domain(label, encode(object));
  return {
    ed: b64(sign(null, msg, privateKey(keys.ed.private, 'ed25519'))),
    pq: b64(sign(null, msg, privateKey(keys.pq.private, 'ml-dsa-65'))),
  };
}
export function checkSignatures(pub, label, object, sig) {
  fields(sig, ['ed', 'pq']);
  const msg = domain(label, encode(object));
  demand(verify(null, msg, publicKey(pub.ed, 'ed25519'), unb64(sig.ed, 64)), 'AUTH_FAILED');
  demand(verify(null, msg, publicKey(pub.pq, 'ml-dsa-65'), unb64(sig.pq, 3309)), 'AUTH_FAILED');
}
export function seal(key, plaintext, aad, nonce = randomBytes(12), limit = LIMITS.plaintextBytes) {
  demand(Buffer.isBuffer(plaintext) && plaintext.length <= limit, 'PAYLOAD_LIMIT');
  demand(key.length === 32 && nonce.length === 12);
  const cipher = createCipheriv('aes-256-gcm', key, nonce, { authTagLength: 16 });
  cipher.setAAD(aad);
  const body = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return { nonce: b64(nonce), body: b64(body), tag: b64(cipher.getAuthTag()) };
}
export function open(key, box, aad, limit = LIMITS.plaintextBytes) {
  fields(box, ['nonce', 'body', 'tag']);
  const nonce = unb64(box.nonce, 12), tag = unb64(box.tag, 16);
  const body = unb64(box.body, 0, limit);
  try {
    const cipher = createDecipheriv('aes-256-gcm', key, nonce, { authTagLength: 16 });
    cipher.setAAD(aad); cipher.setAuthTag(tag);
    return Buffer.concat([cipher.update(body), cipher.final()]);
  } catch { throw new DropError('AUTH_FAILED'); }
}
export function rootStep(root, secret) {
  const out = hkdf(secret, unb64(root, 32), domain('ratchet-root', Buffer.alloc(0)), 64);
  const result = [b64(out.subarray(0, 32)), b64(out.subarray(32))];
  out.fill(0); secret.fill(0);
  return result;
}
export function chainStep(chain) {
  demand(chain !== null, 'NO_SENDING_CHAIN');
  const key = unb64(chain, 32);
  const next = b64(createHmac('sha256', key).update(Buffer.from([2])).digest());
  const message = createHmac('sha256', key).update(Buffer.from([1])).digest();
  key.fill(0);
  return [next, message];
}
