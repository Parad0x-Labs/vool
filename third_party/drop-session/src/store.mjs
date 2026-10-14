import { DatabaseSync } from 'node:sqlite';
import { constants, openSync, closeSync, lstatSync, mkdirSync, chmodSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { randomBytes } from 'node:crypto';
import {
  PROFILE, LIMITS, DropError, demand, encode, canonical, domain, seal, open,
  freshId, id, b64, unb64,
} from './crypto.mjs';

const RECORD_LIMIT = 262144;
const KIND_LIMITS = { identity: 1, prekey: LIMITS.prekeys, session: LIMITS.sessions,
  outbox: LIMITS.queued, inbox: LIMITS.queued, revoked: 1024 };

function secureDirectory(directory) {
  demand(process.platform !== 'win32', 'UNSUPPORTED_STORE_PLATFORM');
  directory = resolve(directory);
  try { mkdirSync(directory, { mode: 0o700 }); }
  catch (error) { if (error.code !== 'EEXIST') throw error; }
  const stat = lstatSync(directory);
  demand(stat.isDirectory() && !stat.isSymbolicLink(), 'UNSAFE_STORE_PATH');
  if (process.platform !== 'win32') {
    demand((stat.mode & 0o077) === 0 && stat.uid === process.getuid(), 'UNSAFE_STORE_PERMISSIONS');
  }
  return directory;
}
function secureFile(path, create = false) {
  if (create) {
    try {
      const fd = openSync(path, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | (constants.O_NOFOLLOW || 0), 0o600);
      closeSync(fd);
    } catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  const stat = lstatSync(path);
  demand(stat.isFile() && !stat.isSymbolicLink() && stat.nlink === 1, 'UNSAFE_STORE_PATH');
  if (process.platform !== 'win32') {
    demand((stat.mode & 0o077) === 0 && stat.uid === process.getuid(), 'UNSAFE_STORE_PERMISSIONS');
  }
}

// Private authority: every client operation runs inside one synchronous write transaction.
// The key comes from the caller; it is never written into this database.
export class EncryptedStore {
  #db;
  #key;
  #storeId;
  #transaction = false;
  #closed = false;
  constructor(directory, key) {
    demand(Buffer.isBuffer(key) && key.length === 32, 'INVALID_VAULT_KEY');
    this.#key = Buffer.from(key);
    try {
      const safeDir = secureDirectory(directory), path = join(safeDir, 'drop.sqlite');
      secureFile(path, true);
      this.#db = new DatabaseSync(path);
      this.#db.exec(`PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL; PRAGMA trusted_schema=OFF; PRAGMA secure_delete=ON;
        CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL,
          owner TEXT NOT NULL, blob TEXT NOT NULL,
          PRIMARY KEY(kind,id));`);
      const columns = this.#db.prepare('PRAGMA table_info(records)').all().map(c => c.name);
      demand(columns.join(',') === 'kind,id,owner,blob', 'UNSUPPORTED_STORE_SCHEMA');
      this.#db.exec('CREATE INDEX IF NOT EXISTS record_owner ON records(owner,kind)');
      this.transaction(() => {
        const existing = this.#db.prepare("SELECT value FROM metadata WHERE name='store'").get();
        if (existing) {
          this.#storeId = id(existing.value);
          const row = this.#db.prepare("SELECT owner,blob FROM records WHERE kind='check' AND id='key'").get();
          demand(row, 'CORRUPT_STORE');
          const check = this.#decrypt('check', 'key', row.owner, row.blob);
          demand(check.store === this.#storeId && check.profile === PROFILE, 'CORRUPT_STORE');
        } else {
          demand(this.#db.prepare('SELECT count(*) AS n FROM records').get().n === 0, 'CORRUPT_STORE');
          this.#storeId = freshId();
          this.#db.prepare("INSERT INTO metadata VALUES ('store',?)").run(this.#storeId);
          this.#db.prepare("INSERT INTO records VALUES ('check','key','',?)").run(this.#encrypt('check', 'key', '', { store: this.#storeId, profile: PROFILE }));
        }
      });
      // SQLite inherits the private database mode, but verify the sidecars as well.
      for (const suffix of ['-wal', '-shm']) {
        const sidecar = path + suffix;
        try { secureFile(sidecar); chmodSync(sidecar, 0o600); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
      }
    } catch (error) { this.close(); throw error; }
  }
  #aad(kind, recordId, owner) { return domain('vault', encode({ store: this.#storeId, kind, id: recordId, owner })); }
  #encrypt(kind, recordId, owner, value) {
    return canonical(seal(this.#key, encode(value), this.#aad(kind, recordId, owner), randomBytes(12), RECORD_LIMIT));
  }
  #decrypt(kind, recordId, owner, blob) {
    demand(typeof blob === 'string' && blob.length <= RECORD_LIMIT * 2, 'CORRUPT_STORE');
    try {
      const plain = open(this.#key, JSON.parse(blob), this.#aad(kind, recordId, owner), RECORD_LIMIT);
      try { return JSON.parse(plain.toString('utf8')); } finally { plain.fill(0); }
    } catch { throw new DropError('CORRUPT_STORE_OR_WRONG_KEY'); }
  }
  transaction(operation) {
    demand(!this.#closed && !this.#transaction, 'STORE_UNAVAILABLE');
    this.#db.exec('BEGIN IMMEDIATE'); this.#transaction = true;
    try {
      const result = operation();
      demand(!result || typeof result.then !== 'function', 'ASYNC_TRANSACTION_FORBIDDEN');
      this.#db.exec('COMMIT');
      return result;
    } catch (error) {
      try { this.#db.exec('ROLLBACK'); } catch { this.close(); }
      throw error;
    } finally { this.#transaction = false; }
  }
  get(kind, recordId) {
    demand(this.#transaction, 'TRANSACTION_REQUIRED');
    const row = this.#db.prepare('SELECT owner,blob FROM records WHERE kind=? AND id=?').get(kind, recordId);
    return row ? this.#decrypt(kind, recordId, row.owner, row.blob) : null;
  }
  put(kind, recordId, value) {
    demand(this.#transaction && Object.hasOwn(KIND_LIMITS, kind), 'TRANSACTION_REQUIRED');
    const exists = this.#db.prepare('SELECT 1 FROM records WHERE kind=? AND id=?').get(kind, recordId);
    if (!exists) demand(this.count(kind) < KIND_LIMITS[kind], 'STORE_LIMIT');
    const owner = kind === 'session' ? id(value.sid)
      : kind === 'inbox' || kind === 'outbox' ? id(value.session) : '';
    this.#db.prepare('INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET owner=excluded.owner,blob=excluded.blob')
      .run(kind, recordId, owner, this.#encrypt(kind, recordId, owner, value));
  }
  count(kind) {
    demand(this.#transaction, 'TRANSACTION_REQUIRED');
    return this.#db.prepare('SELECT count(*) AS n FROM records WHERE kind=?').get(kind).n;
  }
  list(kind) {
    demand(this.#transaction, 'TRANSACTION_REQUIRED');
    return this.#db.prepare('SELECT id,owner,blob FROM records WHERE kind=? ORDER BY id').all(kind)
      .map(row => ({ id: row.id, value: this.#decrypt(kind, row.id, row.owner, row.blob) }));
  }
  remove(kind, recordId) {
    demand(this.#transaction, 'TRANSACTION_REQUIRED');
    this.#db.prepare('DELETE FROM records WHERE kind=? AND id=?').run(kind, recordId);
  }
  removeSessionQueues(session) {
    demand(this.#transaction, 'TRANSACTION_REQUIRED'); id(session);
    this.#db.prepare("DELETE FROM records WHERE owner=? AND kind IN ('inbox','outbox')").run(session);
  }
  close() {
    if (this.#closed) return;
    this.#closed = true;
    try { this.#db?.close(); } finally { this.#key?.fill(0); }
  }
}

export const vaultWire = wire => b64(wire);
export const readVaultWire = text => unb64(text, 1, LIMITS.wireBytes);
