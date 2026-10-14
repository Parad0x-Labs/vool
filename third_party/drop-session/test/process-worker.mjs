import { readFileSync } from 'node:fs';
import { DropClient } from '../src/index.mjs';
import { EncryptedStore } from '../src/store.mjs';

// Disposable keys travel on stdin, never process arguments or test output.
const input = JSON.parse(readFileSync(0, 'utf8'));
const key = Buffer.from(input.key, 'base64url');
if (input.mode === 'exit-uncommitted') {
  const store = new EncryptedStore(input.directory, key);
  store.transaction(() => {
    store.put('revoked', input.session, { revoked: true });
    process.exit(24); // Actual process exit while SQLite owns an uncommitted write.
  });
} else {
  const client = new DropClient({ directory: input.directory, vaultKey: key });
  const sent = client.send({ session: input.session, request: input.request, plaintext: Buffer.from(input.plaintext) });
  if (input.mode === 'exit-committed') process.exit(23); // No caller receives the committed ciphertext.
  process.stdout.write(JSON.stringify({ request: sent.request, wire: sent.wire.toString('base64url') }));
  client.close();
}
