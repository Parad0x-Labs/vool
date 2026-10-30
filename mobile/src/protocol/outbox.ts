import {CompanionError, type MobileCompanionClient} from "./client.ts";
import type {SendResult} from "./models.ts";
import {randomBytes, toHex} from "./sha256.ts";

export interface QueuedTurn {
  key: string;
  chatId: string;
  text: string;
  attachments: string[];
  state: "queued" | "blocked";
  reason?: string;
}
export interface OutboxStore {
  load(): Promise<QueuedTurn[]>;
  save(turns: QueuedTurn[]): Promise<void>;
  clear?(): Promise<void>;
}
export class MemoryOutboxStore implements OutboxStore {
  private turns: QueuedTurn[] = [];
  async load() {return structuredClone(this.turns);}
  async save(turns: QueuedTurn[]) {this.turns = structuredClone(turns);}
}

/** Keep native UTF-8 conversion from replacing a split UTF-16 surrogate pair. */
export function outboxChunks(raw:string):string[] {
  const chunks:string[]=[];
  for(let start=0;start<raw.length;){
    let end=Math.min(start+400,raw.length);
    if(end<raw.length && raw.charCodeAt(end-1)>=0xd800 && raw.charCodeAt(end-1)<=0xdbff && raw.charCodeAt(end)>=0xdc00 && raw.charCodeAt(end)<=0xdfff)end--;
    chunks.push(raw.slice(start,end));start=end;
  }
  return chunks;
}

/** Native cache uses the already-installed keychain module. Chunks stay below
 * the platform value limit, with a manifest switched only after every write.
 * An interrupted save leaves the previous committed queue recoverable. */
export class SecureOutboxStore implements OutboxStore {
  private readonly prefix: string;
  constructor(deviceId: string) { this.prefix = "vool.outbox." + deviceId.replace(/[^a-zA-Z0-9_-]/g, "_"); }
  private async manifest() {
    const store = await import("expo-secure-store");
    const raw = await store.getItemAsync(this.prefix);
    if (!raw) return null;
    const value = JSON.parse(raw) as {generation: string; count: number};
    if (!["a", "b"].includes(value.generation) || !Number.isInteger(value.count) || value.count < 1 || value.count > 400) {
      throw new Error("This phone's saved messages could not be read.");
    }
    return value;
  }
  async load(): Promise<QueuedTurn[]> {
    const store = await import("expo-secure-store");
    const manifest = await this.manifest();
    if (!manifest) return [];
    let raw = "";
    for (let i = 0; i < manifest.count; i++) {
      const chunk = await store.getItemAsync(`${this.prefix}.${manifest.generation}.${i}`);
      if (chunk === null) throw new Error("A saved message is incomplete. Nothing was sent.");
      raw += chunk;
    }
    const value = JSON.parse(raw);
    if (!Array.isArray(value) || value.length > 16 || value.some(turn =>
      !turn || typeof turn.key !== "string" || typeof turn.chatId !== "string" ||
      typeof turn.text !== "string" || !Array.isArray(turn.attachments) ||
      !["queued", "blocked"].includes(turn.state))) throw new Error("Saved messages could not be verified.");
    return value;
  }
  async save(turns: QueuedTurn[]): Promise<void> {
    const raw = JSON.stringify(turns);
    if (turns.length > 16 || raw.length > 128000) throw new Error("The saved-message queue is full.");
    const store = await import("expo-secure-store");
    const previous = await this.manifest();
    const generation = previous?.generation === "a" ? "b" : "a";
    const chunks=outboxChunks(raw);
    const count = chunks.length;
    const stagedRaw = await store.getItemAsync(`${this.prefix}.${generation}.count`);
    const stagedCount = Number(stagedRaw || 0);
    if (!Number.isInteger(stagedCount) || stagedCount < 0 || stagedCount > 400) throw new Error("Saved-message staging could not be verified.");
    // Two bounded slots also bound debris from interrupted writes. Record the
    // staged chunk count so unpair can erase an interrupted generation too.
    await store.setItemAsync(`${this.prefix}.${generation}.count`, String(Math.max(count, stagedCount)),
      {keychainAccessible: store.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
    for (let i = 0; i < count; i++) {
      await store.setItemAsync(`${this.prefix}.${generation}.${i}`, chunks[i],
        {keychainAccessible: store.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
    }
    for (let i = count; i < stagedCount; i++) await store.deleteItemAsync(`${this.prefix}.${generation}.${i}`);
    await store.setItemAsync(`${this.prefix}.${generation}.count`, String(count),
      {keychainAccessible: store.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
    await store.setItemAsync(this.prefix, JSON.stringify({generation, count}),
      {keychainAccessible: store.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
    if (previous) {
      for (let i = 0; i < previous.count; i++) await store.deleteItemAsync(`${this.prefix}.${previous.generation}.${i}`);
      await store.deleteItemAsync(`${this.prefix}.${previous.generation}.count`);
    }
  }
  async clear() {
    const store = await import("expo-secure-store");
    for (const slot of ["a", "b"]) {
      const raw = await store.getItemAsync(`${this.prefix}.${slot}.count`);
      const count = Number(raw || 0);
      if (!Number.isInteger(count) || count < 0 || count > 400) throw new Error("Saved-message cleanup could not be verified.");
      for (let i = 0; i < count; i++) await store.deleteItemAsync(`${this.prefix}.${slot}.${i}`);
      await store.deleteItemAsync(`${this.prefix}.${slot}.count`);
    }
    await store.deleteItemAsync(this.prefix);
  }
}

export class Outbox {
  private running: Promise<void> | null = null;
  private mutations: Promise<unknown> = Promise.resolve();
  activeKey: string | null = null;
  private stopped = false;
  private listeners = new Set<{changed: (turns: QueuedTurn[]) => void; delivered: (result: SendResult, turn: QueuedTurn) => void}>();
  private readonly store: OutboxStore;
  private readonly client: MobileCompanionClient;
  private readonly changed: (turns: QueuedTurn[]) => void;
  private readonly delivered: (result: SendResult, turn: QueuedTurn) => void;
  constructor(store: OutboxStore, client: MobileCompanionClient,
    changed: (turns: QueuedTurn[]) => void = () => {},
    delivered: (result: SendResult, turn: QueuedTurn) => void = () => {}) {
    this.store = store; this.client = client; this.changed = changed; this.delivered = delivered;
  }

  subscribe(changed: (turns: QueuedTurn[]) => void, delivered: (result: SendResult, turn: QueuedTurn) => void) {
    const listener = {changed, delivered}; this.listeners.add(listener);
    return () => {this.listeners.delete(listener);};
  }
  private emit(turns: QueuedTurn[]) {this.changed(turns); for (const listener of this.listeners) listener.changed(turns);}
  stop() {this.stopped = true;}

  private serialize<T>(work: () => Promise<T>): Promise<T> {
    const next = this.mutations.then(work, work);
    this.mutations = next.catch(() => {});
    return next;
  }
  async load() {const turns = await this.store.load(); this.emit(turns); return turns;}
  enqueue(chatId: string | null, text: string, attachments: string[] = []): Promise<QueuedTurn> {
    return this.serialize(async () => {
      if (this.stopped) throw new Error("Pair this phone again before saving another message.");
      const key = toHex(randomBytes(16));
      const turn: QueuedTurn = {key, chatId: chatId || "mob-" + key, text, attachments: [...attachments], state: "queued"};
      const turns = await this.store.load();
      turns.push(turn);
      await this.store.save(turns); // persist BEFORE any request or composer clear
      this.emit(turns);
      return turn;
    });
  }
  removeBlocked(key: string) {
    return this.serialize(async () => {
      const turns = await this.store.load();
      const turn = turns.find(item => item.key === key);
      if (!turn || turn.state !== "blocked") throw new Error("This message has not been refused. Check its status before removing it.");
      const remaining = turns.filter(item => item.key !== key);
      await this.store.save(remaining); this.emit(remaining);
    });
  }
  async retryBlocked(key:string) {
    await this.serialize(async()=>{
      if(this.stopped)throw new Error("Pair this phone again before saving another message.");
      const turns=await this.store.load();const turn=turns.find(value=>value.key===key);
      if(!turn || turn.state!=="blocked")return;
      turn.state="queued";delete turn.reason;
      await this.store.save(turns);this.emit(turns);
    });
    await this.flush();
  }
  async clear() {await this.serialize(async () => {
    if (this.store.clear) await this.store.clear(); else await this.store.save([]);
    this.emit([]);
  });}
  flush(): Promise<void> {
    if (this.stopped) return Promise.resolve();
    if (this.running) return this.running;
    this.running = this.drain().finally(() => {this.running = null; this.activeKey = null;});
    return this.running;
  }
  private async drain() {
    for (;;) {
      if (this.stopped) return;
      const turn = (await this.store.load())[0];
      if (!turn || turn.state === "blocked") return;
      this.activeKey = turn.key;
      let result: SendResult;
      try {
        result = await this.client.sendMessage(turn.chatId, turn.text, turn.attachments, turn.key);
      } catch (error) {
        if (error instanceof CompanionError && error.status >= 400 && error.status < 500 &&
            (![408, 409, 425, 429].includes(error.status) || error.code === "idempotency_conflict")) {
          await this.serialize(async () => {
            const turns = await this.store.load();
            const current = turns.find(item => item.key === turn.key);
            if (current) {current.state = "blocked"; current.reason = error.message;}
            await this.store.save(turns); this.emit(turns);
          });
        }
        return; // offline/unknown delivery stays queued with the SAME identity
      }
      if (this.stopped) return;
      if (result.pending) return;
      await this.serialize(async () => {
        const turns = (await this.store.load()).filter(item => item.key !== turn.key);
        await this.store.save(turns); this.emit(turns);
      });
      this.delivered(result, turn);
      for (const listener of this.listeners) listener.delivered(result, turn);
    }
  }
}

// One queue owner per paired client, across tab unmounts. A remounted screen
// subscribes to it; it never starts a second writer for the same keychain cache.
const queues = new WeakMap<MobileCompanionClient, Outbox>();
export function outboxFor(client: MobileCompanionClient): Outbox {
  let value = queues.get(client);
  if (!value) {value = new Outbox(new SecureOutboxStore(client.deviceId), client); queues.set(client, value);}
  return value;
}
export async function discardOutbox(client: MobileCompanionClient) {
  const value = outboxFor(client);
  value.stop();
  await value.clear();
  queues.delete(client);
}
