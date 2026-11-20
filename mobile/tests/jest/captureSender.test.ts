import {createHash} from "node:crypto";
import {CompanionError} from "../../src/protocol/client";
import {sendOne, sendWaiting, type CaptureItem, type CaptureVault, type CaptureLink} from "../../src/capture/sender";

const PIECE = 768 * 1024;

function capture(bytes: Buffer, id = "a".repeat(32)): CaptureItem {
  return {id, mediaType: "video/mp4", kind: "video", size: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"), takenAt: 1795000000000};
}

class Vault implements CaptureVault {
  reads: number[] = [];
  constructor(public items: Map<string, Buffer>, public metas: CaptureItem[]) {}
  async list() { return this.metas.filter(m => this.items.has(m.id)); }
  async readFrom(id: string, offset: number) {
    this.reads.push(offset);
    const data = this.items.get(id)!;
    const end = (Math.floor(offset / PIECE) + 1) * PIECE;
    return data.subarray(offset, Math.min(end, data.length)).toString("base64");
  }
  async remove(id: string) { this.items.delete(id); }
}

/** A computer that keeps what it is sent and hashes it, like core.mobile_companion.incoming_media. */
class Computer implements CaptureLink {
  parts = new Map<string, Buffer>();
  saved = new Map<string, Buffer>();
  offline = false;
  corrupt = false;
  refuse = false;
  dropAfterChunks = -1;
  meta = new Map<string, {sha: string; size: number}>();
  async captureBegin(item: {sha256: string; sizeBytes: number}) {
    if (this.offline) throw new TypeError("Network request failed");
    if (this.refuse) throw new CompanionError(403, "phone_right_off_or_expired", "");
    if (this.saved.has(item.sha256)) return {done: true as const, sha256: item.sha256, saved_as: "x", size_bytes: item.sizeBytes};
    const id = "cap_" + item.sha256.slice(0, 40);
    this.meta.set(id, {sha: item.sha256, size: item.sizeBytes});
    if (!this.parts.has(id)) this.parts.set(id, Buffer.alloc(0));
    return {done: false as const, upload_id: id, next_offset: this.parts.get(id)!.length, chunk_bytes: PIECE};
  }
  async captureChunk(id: string, offset: number, b64: string) {
    if (this.dropAfterChunks === 0) { this.offline = true; throw new TypeError("Network request failed"); }
    this.dropAfterChunks--;
    const have = this.parts.get(id)!;
    if (offset !== have.length) throw new CompanionError(409, "wrong_offset", "");
    let piece = Buffer.from(b64, "base64");
    if (this.corrupt) { piece = Buffer.from(piece); piece[0] ^= 1; }
    this.parts.set(id, Buffer.concat([have, piece]));
    return {next_offset: have.length + piece.length};
  }
  async captureFinish(id: string) {
    const data = this.parts.get(id)!;
    const sha = createHash("sha256").update(data).digest("hex");
    this.parts.delete(id);
    if (sha !== this.meta.get(id)!.sha) throw new CompanionError(422, "capture_hash_mismatch", "");
    this.saved.set(sha, data);
    return {done: true as const, sha256: sha, saved_as: "x", size_bytes: data.length};
  }
}

const big = Buffer.alloc(PIECE * 3 + 12345, 7).map((_, i) => (i * 31) & 255) as Buffer;

test("a capture is removed from the phone only after the computer returns the same hash", async () => {
  const item = capture(big);
  const vault = new Vault(new Map([[item.id, big]]), [item]);
  const pc = new Computer();
  expect(await sendWaiting(vault, pc)).toEqual({sent: 1, waiting: 0, problem: ""});
  expect(pc.saved.get(item.sha256)!.equals(big)).toBe(true);
  expect(vault.items.size).toBe(0);
  expect(vault.reads).toEqual([0, PIECE, PIECE * 2, PIECE * 3]);
});

test("offline: the capture stays and nothing is lost", async () => {
  const item = capture(big);
  const vault = new Vault(new Map([[item.id, big]]), [item]);
  const pc = new Computer();
  pc.offline = true;
  expect(await sendWaiting(vault, pc)).toEqual({sent: 0, waiting: 1, problem: "offline"});
  expect(vault.items.has(item.id)).toBe(true);
});

test("a transfer cut off half way carries on from where the computer got to", async () => {
  const item = capture(big);
  const vault = new Vault(new Map([[item.id, big]]), [item]);
  const pc = new Computer();
  pc.dropAfterChunks = 2;
  expect((await sendWaiting(vault, pc)).problem).toBe("offline");
  expect(vault.items.has(item.id)).toBe(true);
  pc.offline = false;
  pc.dropAfterChunks = -1;
  vault.reads = [];
  expect((await sendWaiting(vault, pc)).sent).toBe(1);
  expect(vault.reads).toEqual([PIECE * 2, PIECE * 3]);
  expect(pc.saved.get(item.sha256)!.equals(big)).toBe(true);
});

test("damaged on the way: the computer refuses it and the phone keeps its copy", async () => {
  const item = capture(big);
  const vault = new Vault(new Map([[item.id, big]]), [item]);
  const pc = new Computer();
  pc.corrupt = true;
  expect(await sendWaiting(vault, pc)).toEqual({sent: 0, waiting: 1, problem: "damaged"});
  expect(vault.items.has(item.id)).toBe(true);
  pc.corrupt = false;
  expect((await sendWaiting(vault, pc)).sent).toBe(1);
});

test("a computer answering with another hash never makes the phone delete", async () => {
  const item = capture(big);
  const vault = new Vault(new Map([[item.id, big]]), [item]);
  const liar: CaptureLink = {
    captureBegin: async () => ({done: true, sha256: "0".repeat(64), saved_as: "x", size_bytes: big.length}),
    captureChunk: async () => ({next_offset: 0}),
    captureFinish: async () => ({done: true, sha256: "0".repeat(64), saved_as: "x", size_bytes: 0}),
  };
  expect(await sendOne(item, vault, liar)).toBe(false);
  expect(vault.items.has(item.id)).toBe(true);
});

test("camera switched off on the computer: stops and says so, keeps everything", async () => {
  const a = capture(big, "a".repeat(32));
  const b = capture(Buffer.from("small"), "b".repeat(32));
  const vault = new Vault(new Map([[a.id, big], [b.id, Buffer.from("small")]]), [a, b]);
  const pc = new Computer();
  pc.refuse = true;
  expect(await sendWaiting(vault, pc)).toEqual({sent: 0, waiting: 2, problem: "not_allowed"});
  expect(vault.items.size).toBe(2);
});

test("already on the computer (the answer was lost last time): removed without sending again", async () => {
  const item = capture(big);
  const vault = new Vault(new Map([[item.id, big]]), [item]);
  const pc = new Computer();
  pc.saved.set(item.sha256, big);
  expect((await sendWaiting(vault, pc)).sent).toBe(1);
  expect(vault.reads).toEqual([]);
});
