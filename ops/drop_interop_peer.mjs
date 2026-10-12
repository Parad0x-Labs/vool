// Line-JSON peer over the reference JavaScript DropClient, for ops/drop_interop.py.
import { randomBytes } from 'node:crypto';
import { createInterface } from 'node:readline';
import { DropClient } from '../third_party/drop-session/src/index.mjs';

const client = new DropClient({ directory: process.argv[2], vaultKey: randomBytes(32) });
const bytes = v => Buffer.from(v, 'base64url');
const out = v => Buffer.isBuffer(v) ? v.toString('base64url') : v;
for await (const line of createInterface({ input: process.stdin })) {
  try {
    const a = JSON.parse(line); let r;
    if (a.op === 'fingerprint') r = { fingerprint: client.fingerprint };
    else if (a.op === 'invite') r = { wire: client.issueInvite({ now: a.now, ttl: a.ttl }) };
    else if (a.op === 'start') r = client.start({ invite: bytes(a.invite), peerFingerprint: a.peer, plaintext: bytes(a.plaintext), now: a.now });
    else if (a.op === 'accept') r = client.accept({ wire: bytes(a.wire), peerFingerprint: a.peer, now: a.now });
    else if (a.op === 'send') r = client.send({ session: a.session, plaintext: bytes(a.plaintext), now: a.now });
    else if (a.op === 'receive') r = client.receive({ wire: bytes(a.wire), now: a.now });
    else throw new Error('op');
    const o = {}; for (const [k, v] of Object.entries(r)) o[k] = out(v);
    process.stdout.write(JSON.stringify({ ok: true, out: o }) + '\n');
  } catch (e) { process.stdout.write(JSON.stringify({ ok: false, code: e.code || 'ERROR' }) + '\n'); }
}
client.close();
