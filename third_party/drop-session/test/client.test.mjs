import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, statSync, symlinkSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { DropClient, freshId, LIMITS } from '../src/index.mjs';
import { decode, encode } from '../src/crypto.mjs';
import { EncryptedStore } from '../src/store.mjs';

function setup(t) {
  const root = mkdtempSync(join(tmpdir(), 'drop-store-'));
  const akey = randomBytes(32), bkey = randomBytes(32);
  let a = new DropClient({ directory: join(root, 'alice'), vaultKey: akey });
  let b = new DropClient({ directory: join(root, 'bob'), vaultKey: bkey });
  t.after(() => { a.close(); b.close(); akey.fill(0); bkey.fill(0); });
  return { root, akey, bkey, get a() { return a; }, get b() { return b; },
    restart() {
      a.close(); b.close();
      a = new DropClient({ directory: join(root, 'alice'), vaultKey: akey });
      b = new DropClient({ directory: join(root, 'bob'), vaultKey: bkey });
    } };
}
function connect(env) {
  const invite = env.b.issueInvite();
  const start = env.a.start({ invite, peerFingerprint: env.b.fingerprint, plaintext: Buffer.from('offline encrypted task') });
  const accepted = env.b.accept({ wire: start.wire, peerFingerprint: env.a.fingerprint });
  return { ...start, accepted };
}

test('durable public API: restart, exact retry, task/result and persisted unacknowledged receive', t => {
  const env = setup(t), aPin = env.a.fingerprint;
  const { session, accepted } = connect(env);
  env.restart();
  assert.equal(env.a.fingerprint, aPin);
  assert.equal(env.b.pendingReceives()[0].plaintext.toString(), 'offline encrypted task');
  const reply = env.b.send({ session, plaintext: Buffer.from('result: ready'), request: freshId() });
  env.restart();
  const retry = env.b.send({ session, plaintext: Buffer.from('result: ready'), request: reply.request });
  assert.deepEqual(retry.wire, reply.wire);
  assert.throws(() => env.b.send({ session, plaintext: Buffer.from('changed payload'), request: reply.request }), /REQUEST_CONFLICT/);
  const r = env.a.receive({ wire: retry.wire });
  assert.equal(r.plaintext.toString(), 'result: ready');
  env.restart();
  assert.throws(() => env.a.receive({ wire: retry.wire }), /REPLAY/);
  assert.equal(env.a.pendingReceives()[0].delivery, r.delivery);
  env.a.acknowledgeReceive(r.delivery); env.b.acknowledgeReceive(accepted.delivery);
  env.b.acknowledgeSend(reply.request);
  assert.equal(env.a.pendingReceives().length, 0); assert.equal(env.b.pendingReceives().length, 0);
  assert.equal(env.b.pendingSends().length, 0);
});

test('failed first-message auth does not consume one-time prekey; valid acceptance consumes it atomically', t => {
  const env = setup(t), invite = env.b.issueInvite();
  const started = env.a.start({ invite, peerFingerprint: env.b.fingerprint, plaintext: Buffer.from('untouched') });
  const forged = decode(started.wire); forged.signatures.pq = forged.signatures.pq.replace(/^./, forged.signatures.pq[0] === 'A' ? 'B' : 'A');
  assert.throws(() => env.b.accept({ wire: encode(forged), peerFingerprint: env.a.fingerprint }), /AUTH_FAILED/);
  assert.equal(env.b.pendingReceives().length, 0);
  env.restart();
  assert.equal(env.b.accept({ wire: started.wire, peerFingerprint: env.a.fingerprint }).plaintext.toString(), 'untouched');
  const another = env.a.start({ invite, peerFingerprint: env.b.fingerprint, plaintext: Buffer.from('reuse attempt') });
  assert.throws(() => env.b.accept({ wire: another.wire, peerFingerprint: env.a.fingerprint }), /PREKEY_UNAVAILABLE/);
});

test('authentication rejection leaves real durable state unchanged across restart', t => {
  const env = setup(t), { session } = connect(env);
  const reply = env.b.send({ session, plaintext: Buffer.from('real response') });
  const forged = decode(reply.wire); forged.box.tag = forged.box.tag.replace(/^./, forged.box.tag[0] === 'A' ? 'B' : 'A');
  assert.throws(() => env.a.receive({ wire: encode(forged) }), /AUTH_FAILED/);
  env.restart();
  assert.equal(env.a.receive({ wire: reply.wire }).plaintext.toString(), 'real response');
});

test('vault encryption, wrong key, tampered record and private file modes', t => {
  const env = setup(t), { session } = connect(env);
  const directory = join(env.root, 'alice');
  for (const file of readdirSync(directory)) {
    const path = join(directory, file);
    assert.equal(statSync(path).mode & 0o077, 0);
    assert.ok(!readFileSync(path).includes(Buffer.from('offline encrypted task')));
  }
  assert.throws(() => new DropClient({ directory, vaultKey: randomBytes(32) }), /CORRUPT_STORE_OR_WRONG_KEY/);
  env.a.close();
  const db = new DatabaseSync(join(directory, 'drop.sqlite'));
  const row = db.prepare("SELECT blob FROM records WHERE kind='session' AND id=?").get(session);
  const blob = JSON.parse(row.blob); blob.tag = blob.tag.replace(/^./, blob.tag[0] === 'A' ? 'B' : 'A');
  db.prepare("UPDATE records SET blob=? WHERE kind='session' AND id=?").run(JSON.stringify(blob), session); db.close();
  const damaged = new DropClient({ directory, vaultKey: env.akey });
  t.after(() => damaged.close());
  assert.throws(() => damaged.send({ session, plaintext: Buffer.from('cannot use corrupted state') }), /CORRUPT_STORE_OR_WRONG_KEY/);
});

test('real SQLite rollback never exposes a partial write', t => {
  const root = mkdtempSync(join(tmpdir(), 'drop-rollback-'));
  const store = new EncryptedStore(join(root, 'store'), randomBytes(32)); t.after(() => store.close());
  assert.throws(() => store.transaction(() => { store.put('revoked', freshId(), { value: 'partial' }); throw new Error('aborted write'); }), /aborted write/);
  assert.equal(store.transaction(() => store.count('revoked')), 0);
  assert.throws(() => store.transaction(() => Promise.resolve('async is unsafe')), /ASYNC_TRANSACTION_FORBIDDEN/);
  assert.equal(store.transaction(() => store.count('revoked')), 0);
});

test('persistent forget barrier deletes queued content and blocks resumed/replayed session', t => {
  const env = setup(t), started = connect(env);
  const reply = env.b.send({ session: started.session, plaintext: Buffer.from('late response') });
  env.a.forgetSession(started.session); env.restart();
  assert.equal(env.a.pendingSends().length, 0); assert.equal(env.a.pendingReceives().length, 0);
  assert.throws(() => env.a.receive({ wire: reply.wire }), /SESSION_REVOKED/);
  assert.throws(() => env.a.send({ session: started.session, plaintext: Buffer.from('cannot resurrect') }), /SESSION_REVOKED/);
  env.b.forgetSession(started.session); env.restart();
  assert.throws(() => env.b.accept({ wire: started.wire, peerFingerprint: env.a.fingerprint }), /SESSION_REVOKED/);
});

test('full payload survives encrypted persistence, restart and delivery', t => {
  const env = setup(t), body = randomBytes(LIMITS.plaintextBytes);
  const sent = env.a.start({ invite: env.b.issueInvite(), peerFingerprint: env.b.fingerprint, plaintext: body });
  env.restart();
  const accepted = env.b.accept({ wire: env.a.pendingSends()[0].wire, peerFingerprint: env.a.fingerprint });
  assert.deepEqual(accepted.plaintext, body);
  env.restart(); assert.deepEqual(env.b.pendingReceives()[0].plaintext, body);
});

test('unsafe public/symlink store directory rejected without following link', () => {
  const root = mkdtempSync(join(tmpdir(), 'drop-path-'));
  mkdirSync(join(root, 'unsafe'), { mode: 0o755 });
  assert.throws(() => new DropClient({ directory: join(root, 'unsafe'), vaultKey: randomBytes(32) }), /UNSAFE_STORE_PERMISSIONS/);
  symlinkSync(join(root, 'unsafe'), join(root, 'link'));
  assert.throws(() => new DropClient({ directory: join(root, 'link'), vaultKey: randomBytes(32) }), /UNSAFE_STORE_PATH/);
});
