// UNREVIEWED: not for real messages. Use the pinned library; no new primitives.
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { DropClient } from '../../third_party/drop-session/src/index.mjs';

if (process.env.VOOL_DROP_FIT !== '1') throw new Error('DROP_FIT_DISABLED');
const key = readFileSync(process.argv[3]);
const client = new DropClient({ directory: process.argv[2], vaultKey: key });
key.fill(0);
const bytes = value => Buffer.from(value, 'base64url');
const encode = value => Buffer.isBuffer(value) ? value.toString('base64url') : value;
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
try {
  for await (const line of lines) {
    try {
      if (line.length > 200000) throw new Error('RPC_LIMIT');
      const a = JSON.parse(line);
      let out;
      switch (a.op) {
        case 'identity': out = { fingerprint: client.fingerprint }; break;
        case 'invite': out = { wire: client.issueInvite({ now: a.now, ttl: a.ttl }) }; break;
        case 'discardInvite': client.discardInvite(a.prekey); out = {}; break;
        case 'start': out = client.start({ invite: bytes(a.invite), peerFingerprint: a.peer,
          plaintext: bytes(a.plaintext), request: a.request, now: a.now }); break;
        case 'send': out = client.send({ session: a.session, plaintext: bytes(a.plaintext),
          request: a.request, now: a.now }); break;
        case 'accept': out = client.accept({ wire: bytes(a.wire), peerFingerprint: a.peer, now: a.now }); break;
        case 'receive': out = client.receive({ wire: bytes(a.wire), now: a.now }); break;
        case 'pendingSend': out = client.pendingSends(); break;
        case 'pendingReceive': out = client.pendingReceives(); break;
        case 'ackSend': client.acknowledgeSend(a.request); out = {}; break;
        case 'ackReceive': client.acknowledgeReceive(a.delivery); out = {}; break;
        case 'forget': client.forgetSession(a.session); out = {}; break;
        default: throw new Error('RPC_OPERATION');
      }
      process.stdout.write(JSON.stringify({ ok: true, out }, (_, v) =>
        v && v.type === 'Buffer' && Array.isArray(v.data) ? encode(Buffer.from(v.data)) : encode(v)) + '\n');
    } catch (e) {
      const code = typeof e.code === 'string' && /^[A-Z_]+$/.test(e.code) ? e.code : 'CRYPTO_REFUSED';
      process.stdout.write(JSON.stringify({ ok: false, code }) + '\n');
    }
  }
} finally { client.close(); }
