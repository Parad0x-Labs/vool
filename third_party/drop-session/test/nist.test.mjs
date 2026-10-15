import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createPrivateKey, createPublicKey, decapsulate } from 'node:crypto';
import { b64, privateKey } from '../src/crypto.mjs';

// Imported DATA ONLY, from the pinned NIST corpus in fixtures/nist-acvp.json.
const corpus = JSON.parse(readFileSync(new URL('./fixtures/nist-acvp.json', import.meta.url)));
function der(tag, bytes) {
  let length;
  if (bytes.length < 128) length = Buffer.from([bytes.length]);
  else {
    let hex = bytes.length.toString(16); if (hex.length % 2) hex = '0' + hex;
    const encoded = Buffer.from(hex, 'hex'); length = Buffer.concat([Buffer.from([0x80 | encoded.length]), encoded]);
  }
  return Buffer.concat([Buffer.from([tag]), length, bytes]);
}
function pkcs8(algorithm, content) {
  const oid = Buffer.from(algorithm === 'kem' ? '300b0609608648016503040402' : '300b0609608648016503040312', 'hex');
  return der(0x30, Buffer.concat([Buffer.from('020100', 'hex'), oid, der(4, content)]));
}
test('NIST ACVP ML-KEM-768 seed-to-public-key known answer (not a full keygen validation)', () => {
  const v = corpus.cases.find(c => c.suite === 'ML-KEM-keyGen-FIPS203');
  const key = privateKey(b64(pkcs8('kem', der(0x80, Buffer.from(v.d + v.z, 'hex')))), 'ml-kem-768');
  const pub = createPublicKey(key).export({ type: 'spki', format: 'der' }).subarray(-1184);
  assert.equal(pub.toString('hex'), v.ek.toLowerCase());
});
test('NIST ACVP ML-KEM-768 10 independent decapsulation answers', () => {
  const cases = corpus.cases.filter(c => c.suite === 'ML-KEM-encapDecap-FIPS203');
  assert.equal(cases.length, 10);
  for (const v of cases) {
    const key = createPrivateKey({ key: pkcs8('kem', der(4, Buffer.from(v.dk, 'hex'))), type: 'pkcs8', format: 'der' });
    assert.equal(decapsulate(key, Buffer.from(v.c, 'hex')).toString('hex'), v.k.toLowerCase(), `NIST tcId ${v.tcId}`);
  }
});
test('NIST ACVP ML-DSA-65 seed-to-public-key known answer (not a full keygen validation)', () => {
  const v = corpus.cases.find(c => c.suite === 'ML-DSA-keyGen-FIPS204');
  const key = privateKey(b64(pkcs8('dsa', der(0x80, Buffer.from(v.seed, 'hex')))), 'ml-dsa-65');
  const pub = createPublicKey(key).export({ type: 'spki', format: 'der' }).subarray(-1952);
  assert.equal(pub.toString('hex'), v.pk.toLowerCase());
});
