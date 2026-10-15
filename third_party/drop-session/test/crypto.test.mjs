import test from 'node:test';
import assert from 'node:assert/strict';
import { encapsulate, decapsulate } from 'node:crypto';
import {
  b64, pair, privateKey, publicKey, dh, identity, publicIdentity, fingerprint,
  signatures, checkSignatures, hkdf, seal, open, encode, decode,
} from '../src/crypto.mjs';

test('RFC 7748 section 6.1 X25519 independent known-answer vector', () => {
  const priv = Buffer.from('302e020100300506032b656e04220420' +
    '77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a', 'hex');
  const pub = Buffer.from('302a300506032b656e032100' +
    'de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f', 'hex');
  assert.equal(dh({ private: b64(priv) }, b64(pub)).toString('hex'),
    '4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742');
  const zero = Buffer.from('302a300506032b656e032100' + '00'.repeat(32), 'hex');
  assert.throws(() => dh({ private: b64(priv) }, b64(zero)), /INVALID_KEY/);
});

test('RFC 5869 appendix A.1 HKDF-SHA256 independent known-answer vector', () => {
  const out = hkdf(Buffer.alloc(22, 11), Buffer.from('000102030405060708090a0b0c', 'hex'),
    Buffer.from('f0f1f2f3f4f5f6f7f8f9', 'hex'), 42);
  assert.equal(out.toString('hex'),
    '3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865');
});

test('AES-256-GCM known-answer empty/zero vector and authenticated-data rejection', () => {
  const box = seal(Buffer.alloc(32), Buffer.alloc(0), Buffer.alloc(0), Buffer.alloc(12));
  assert.equal(Buffer.from(box.tag, 'base64url').toString('hex'), '530f8afbc74536b9a963b4f1c4cb738b');
  assert.deepEqual(open(Buffer.alloc(32), box, Buffer.alloc(0)), Buffer.alloc(0));
  assert.throws(() => open(Buffer.alloc(32), box, Buffer.from('changed')), /AUTH_FAILED/);
});

test('native ML-KEM-768 genuine encapsulation and implicit rejection of damaged ciphertext', () => {
  const k = pair('ml-kem-768');
  const { sharedKey, ciphertext } = encapsulate(publicKey(k.public, 'ml-kem-768'));
  assert.equal(ciphertext.length, 1088);
  assert.deepEqual(decapsulate(privateKey(k.private, 'ml-kem-768'), ciphertext), sharedKey);
  ciphertext[7] ^= 1;
  assert.notDeepEqual(decapsulate(privateKey(k.private, 'ml-kem-768'), ciphertext), sharedKey);
});

test('both identity signatures required; substituted key or removed PQ signature fails', () => {
  const keys = identity(), pub = publicIdentity(keys), object = { purpose: 'fresh invite' };
  const sig = signatures(keys, 'test', object);
  checkSignatures(pub, 'test', object, sig);
  assert.equal(fingerprint(pub).length, 43);
  assert.throws(() => checkSignatures(pub, 'other-domain', object, sig), /AUTH_FAILED/);
  assert.throws(() => checkSignatures(pub, 'test', { purpose: 'changed' }, sig), /AUTH_FAILED/);
  assert.throws(() => checkSignatures(pub, 'test', object, { ed: sig.ed }), /INVALID_FORMAT/);
  assert.throws(() => checkSignatures({ ...pub, pq: identity().pq.public }, 'test', object, sig), /AUTH_FAILED/);
});

test('wire canonicalization rejects duplicate keys, whitespace, extras, floats and invalid UTF8', () => {
  assert.deepEqual(decode(encode({ z: [1, true], a: 'x' })), { a: 'x', z: [1, true] });
  for (const raw of ['{"a":1,"a":1}', '{ "a":1}', '{"a":1.1}', '{"a":1e0}', '{"a":-1}'])
    assert.throws(() => decode(Buffer.from(raw)));
  assert.throws(() => decode(Buffer.from([123,34,120,34,58,34,255,34,125])));
  assert.throws(() => decode(Buffer.from('['.repeat(10000) + '0' + ']'.repeat(10000))), /INVALID_FORMAT/);
});
