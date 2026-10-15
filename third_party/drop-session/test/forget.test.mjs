import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { randomBytes } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { DropClient } from '../src/index.mjs';

test('forget survives corrupt queues from another session and deletes corrupt owned queues', t => {
  const root = mkdtempSync(join(tmpdir(), 'drop-forget-corrupt-')), key = randomBytes(32);
  const directory = join(root, 'a'); let a = new DropClient({ directory, vaultKey: key });
  const b = new DropClient({ directory: join(root, 'b'), vaultKey: randomBytes(32) });
  t.after(() => { a.close(); b.close(); key.fill(0); });
  const one = a.start({ invite: b.issueInvite(), peerFingerprint: b.fingerprint, plaintext: Buffer.from('first session') });
  b.accept({ wire: one.wire, peerFingerprint: a.fingerprint });
  const two = a.start({ invite: b.issueInvite(), peerFingerprint: b.fingerprint, plaintext: Buffer.from('second session') });
  b.accept({ wire: two.wire, peerFingerprint: a.fingerprint }); a.close();
  const db = new DatabaseSync(join(directory, 'drop.sqlite'));
  db.prepare("UPDATE records SET blob='corrupt' WHERE kind='outbox' AND id=?").run(two.request); db.close();
  a = new DropClient({ directory, vaultKey: key });
  a.forgetSession(one.session); a.close();
  a = new DropClient({ directory, vaultKey: key });
  assert.throws(() => a.send({ session: one.session, plaintext: Buffer.from('forbidden') }), /SESSION_REVOKED/);
  a.forgetSession(two.session);
  assert.equal(a.pendingSends().length, 0);
  assert.throws(() => a.send({ session: two.session, plaintext: Buffer.from('also forbidden') }), /SESSION_REVOKED/);
});

test('queue ownership is authenticated, so redirecting a row to another session fails closed', t => {
  const root = mkdtempSync(join(tmpdir(), 'drop-owner-corrupt-')), key = randomBytes(32), directory = join(root, 'a');
  let a = new DropClient({ directory, vaultKey: key });
  const b = new DropClient({ directory: join(root, 'b'), vaultKey: randomBytes(32) });
  t.after(() => { a.close(); b.close(); key.fill(0); });
  const one = a.start({ invite: b.issueInvite(), peerFingerprint: b.fingerprint, plaintext: Buffer.from('first') });
  const two = a.start({ invite: b.issueInvite(), peerFingerprint: b.fingerprint, plaintext: Buffer.from('second') });
  a.close();
  const db = new DatabaseSync(join(directory, 'drop.sqlite'));
  db.prepare("UPDATE records SET owner=? WHERE kind='outbox' AND id=?").run(two.session, one.request); db.close();
  a = new DropClient({ directory, vaultKey: key });
  assert.throws(() => a.pendingSends(), /CORRUPT_STORE_OR_WRONG_KEY/);
});

test('obsolete database layout is refused without clearing existing encrypted identity state', () => {
  const root = mkdtempSync(join(tmpdir(), 'drop-schema-corrupt-')), key = randomBytes(32), directory = join(root, 'a');
  const client = new DropClient({ directory, vaultKey: key }); client.close();
  const path = join(directory, 'drop.sqlite'), db = new DatabaseSync(path);
  const before = db.prepare("SELECT blob FROM records WHERE kind='identity'").get().blob;
  db.exec('DROP INDEX record_owner; ALTER TABLE records DROP COLUMN owner'); db.close();
  assert.throws(() => new DropClient({ directory, vaultKey: key }), /UNSUPPORTED_STORE_SCHEMA/);
  const after = new DatabaseSync(path);
  try { assert.equal(after.prepare("SELECT blob FROM records WHERE kind='identity'").get().blob, before); }
  finally { after.close(); key.fill(0); }
});
