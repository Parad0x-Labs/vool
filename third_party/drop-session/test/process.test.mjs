import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import { DropClient, freshId } from '../src/index.mjs';

function worker(input) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [new URL('./process-worker.mjs', import.meta.url).pathname], { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', d => { stdout += d; }); child.stderr.on('data', d => { stderr += d; });
    child.on('error', reject); child.on('close', code => resolve({ code, stdout, stderr, pid: child.pid }));
    child.stdin.end(JSON.stringify(input));
  });
}
function setup(t) {
  const root = mkdtempSync(join(tmpdir(), 'drop-process-'));
  const key = randomBytes(32), peerKey = randomBytes(32), directory = join(root, 'sender');
  let sender = new DropClient({ directory, vaultKey: key });
  const receiver = new DropClient({ directory: join(root, 'receiver'), vaultKey: peerKey });
  const started = sender.start({ invite: receiver.issueInvite(), peerFingerprint: receiver.fingerprint, plaintext: Buffer.from('prepare worker session') });
  receiver.accept({ wire: started.wire, peerFingerprint: sender.fingerprint });
  sender.acknowledgeSend(started.request);
  t.after(() => { sender.close(); receiver.close(); key.fill(0); peerKey.fill(0); });
  return { directory, key, receiver, session: started.session,
    get sender() { return sender; }, restart() { sender.close(); sender = new DropClient({ directory, vaultKey: key }); } };
}
test('actual process exit after commit: pending ciphertext recovers once without reusing a message key', async t => {
  const env = setup(t), request = freshId();
  env.sender.close();
  const child = await worker({ mode: 'exit-committed', directory: env.directory, key: env.key.toString('base64url'), session: env.session, request, plaintext: 'committed before crash' });
  assert.equal(child.code, 23, child.stderr); assert.equal(child.stdout, '');
  env.restart();
  const pending = env.sender.pendingSends(); assert.equal(pending.length, 1); assert.equal(pending[0].request, request);
  const retry = env.sender.send({ session: env.session, request, plaintext: Buffer.from('committed before crash') });
  assert.deepEqual(retry.wire, pending[0].wire);
  assert.equal(env.receiver.receive({ wire: retry.wire }).plaintext.toString(), 'committed before crash');
  const next = env.sender.send({ session: env.session, plaintext: Buffer.from('new counter after recovery') });
  assert.equal(env.receiver.receive({ wire: next.wire }).plaintext.toString(), 'new counter after recovery');
  assert.throws(() => env.receiver.receive({ wire: retry.wire }), /REPLAY/);
});
test('actual process exit before commit: journal rolls back the unfinished transaction', async t => {
  const env = setup(t); env.sender.close();
  const child = await worker({ mode: 'exit-uncommitted', directory: env.directory, key: env.key.toString('base64url'), session: env.session });
  assert.equal(child.code, 24, child.stderr); env.restart();
  const sent = env.sender.send({ session: env.session, plaintext: Buffer.from('uncommitted revoke was rolled back') });
  assert.equal(env.receiver.receive({ wire: sent.wire }).plaintext.toString(), 'uncommitted revoke was rolled back');
});
test('eight genuine concurrent process writers serialize and all messages decrypt out of order', async t => {
  const env = setup(t); env.sender.close();
  const results = await Promise.all(Array.from({ length: 8 }, (_, i) => worker({
    mode: 'send', directory: env.directory, key: env.key.toString('base64url'), session: env.session,
    request: freshId(), plaintext: `independent writer ${i}`,
  })));
  const bodies = new Set();
  for (const result of results.toReversed()) {
    assert.equal(result.code, 0, result.stderr);
    const sent = JSON.parse(result.stdout), wire = Buffer.from(sent.wire, 'base64url');
    bodies.add(env.receiver.receive({ wire }).plaintext.toString());
  }
  assert.equal(bodies.size, 8); env.restart(); assert.equal(env.sender.pendingSends().length, 8);
  const one = env.sender.pendingSends()[0]; assert.throws(() => env.receiver.receive({ wire: one.wire }), /REPLAY/);
});

test('concurrent retry of the same request creates one ciphertext and advances one counter', async t => {
  const env = setup(t), request = freshId(); env.sender.close();
  const input = { mode: 'send', directory: env.directory, key: env.key.toString('base64url'), session: env.session,
    request, plaintext: 'same request racing twice' };
  const [one, two] = await Promise.all([worker(input), worker(input)]);
  assert.equal(one.code, 0, one.stderr); assert.equal(two.code, 0, two.stderr);
  assert.equal(JSON.parse(one.stdout).wire, JSON.parse(two.stdout).wire);
  env.restart(); assert.equal(env.sender.pendingSends().length, 1);
  const sent = env.sender.pendingSends()[0];
  assert.equal(env.receiver.receive({ wire: sent.wire }).plaintext.toString(), input.plaintext);
  const next = env.sender.send({ session: env.session, plaintext: Buffer.from('next unique request') });
  assert.equal(env.receiver.receive({ wire: next.wire }).plaintext.toString(), 'next unique request');
});
