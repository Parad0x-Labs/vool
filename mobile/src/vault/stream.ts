import type {MobileCompanionClient} from "../protocol/client.ts";
import type {MediaItem} from "../protocol/models.ts";
import {vaultNative} from "./native.ts";

const live = new Map<string, VaultStream>();
let wants: {remove(): void} | null = null;

/** The player waits for a piece: fetch from there next. */
function listen() {
  if (wants || !vaultNative) return;
  wants = vaultNative.addListener("onWant", event => live.get(event.stream)?.jump(event.index));
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export type StreamState = {held: number; pieces: number; failed: string; done: boolean};

/**
 * One item coming from the computer into the Vault, piece by piece, in order from wherever the player is. A break in
 * the connection waits and carries on from the first missing piece; nothing is fetched twice. Photos also keep their
 * pieces as text for the picture (in memory only); everything else lives sealed in the native stream.
 */
export class VaultStream {
  readonly id: string;
  readonly pieces: number;
  private held = new Set<number>();
  private cursor = 0;
  private closed = false;
  private text: string[] | null;
  failed = "";

  constructor(private client: MobileCompanionClient, readonly item: MediaItem, private changed: (state: StreamState) => void) {
    if (!vaultNative) throw new Error("unsupported");
    listen();
    this.id = vaultNative.open(item.size, item.media_type);
    this.pieces = Math.max(1, Math.ceil(item.size / vaultNative.pieceBytes));
    this.text = item.kind === "photo" ? new Array(this.pieces).fill("") : null;
    live.set(this.id, this);
    void this.run();
  }

  get done() {return this.held.size >= this.pieces;}

  jump(index: number) {if (index >= 0 && index < this.pieces && !this.held.has(index)) this.cursor = index;}

  /** The whole photo, once every piece is here: base64 text joins because each piece is a multiple of 3 bytes. */
  photoBase64(): string {return this.done && this.text ? this.text.join("") : "";}

  private next(): number {
    for (let step = 0; step < this.pieces; step++) {
      const index = (this.cursor + step) % this.pieces;
      if (!this.held.has(index)) return index;
    }
    return -1;
  }

  private report() {this.changed({held: this.held.size, pieces: this.pieces, failed: this.failed, done: this.done});}

  private async run() {
    const piece = vaultNative!.pieceBytes;
    let pause = 1000;
    while (!this.closed && !this.done) {
      const index = this.next();
      if (index < 0) break;
      try {
        const answer = await this.client.mediaRead(this.item.id, index * piece, piece);
        if (this.closed) return;
        await vaultNative!.put(this.id, index, answer.content_b64);
        if (this.text) this.text[index] = answer.content_b64;
        this.held.add(index);
        if (this.cursor === index) this.cursor = index + 1;
        this.failed = "";
        pause = 1000;
        this.report();
      } catch (error) {
        if (this.closed) return;
        const code = String((error as {code?: string})?.code || "");
        if (code === "phone_right_off_or_expired" || code.startsWith("media_")) {this.failed = code || "failed"; this.report(); return;}
        // The computer is out of reach for now (a call, a tunnel, sleep): wait, then carry on from the same piece.
        this.failed = "away";
        this.report();
        await sleep(pause);
        pause = Math.min(pause * 2, 15_000);
      }
    }
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.text = null;
    live.delete(this.id);
    vaultNative?.close(this.id);
  }
}
