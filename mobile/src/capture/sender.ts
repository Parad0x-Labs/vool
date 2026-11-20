import {CompanionError, type CaptureBegin, type CaptureSaved} from "../protocol/client.ts";

/** One capture locked inside VOOL, waiting for the computer. */
export interface CaptureItem { id: string; mediaType: string; kind: "photo" | "video" | "sound"; size: number; sha256: string; takenAt: number }

/** The encrypted store on the phone (the native VoolCapture module, or a stand-in in tests). */
export interface CaptureVault {
  list(): Promise<CaptureItem[]>;
  /** The original bytes from `offset` to the end of the piece that holds it, as base64. */
  readFrom(id: string, offset: number): Promise<string>;
  remove(id: string): Promise<void>;
}

export interface CaptureLink {
  captureBegin(item: {sha256: string; sizeBytes: number; mediaType: string; takenAt: number}): Promise<CaptureBegin>;
  captureChunk(uploadId: string, offset: number, contentB64: string): Promise<{next_offset: number}>;
  captureFinish(uploadId: string): Promise<CaptureSaved>;
}

export type SendProblem = "" | "offline" | "not_allowed" | "damaged" | "failed";
export interface SendReport { sent: number; waiting: number; problem: SendProblem }

function byteLength(base64: string): number {
  const padding = base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0;
  return (base64.length / 4) * 3 - padding;
}

/**
 * Send one capture. The phone's copy is removed ONLY when the computer answers with the very hash the
 * phone computed when it locked the capture away; any other ending keeps it for the next try.
 * Pieces are read from the encrypted store one at a time, so a long video is never held in memory.
 */
export async function sendOne(item: CaptureItem, vault: CaptureVault, link: CaptureLink): Promise<boolean> {
  let begun = await link.captureBegin({sha256: item.sha256, sizeBytes: item.size, mediaType: item.mediaType, takenAt: item.takenAt / 1000});
  if (!begun.done) {
    const uploadId = begun.upload_id;
    let offset = begun.next_offset;
    let resynced = false;
    while (offset < item.size) {
      const piece = await vault.readFrom(item.id, offset);
      try {
        const next = (await link.captureChunk(uploadId, offset, piece)).next_offset;
        if (next !== offset + byteLength(piece)) throw new CompanionError(409, "wrong_offset", "");
        offset = next;
      } catch (error) {
        // The computer holds a different amount than expected: ask once where to carry on.
        if (error instanceof CompanionError && error.code === "wrong_offset" && !resynced) {
          resynced = true;
          begun = await link.captureBegin({sha256: item.sha256, sizeBytes: item.size, mediaType: item.mediaType, takenAt: item.takenAt / 1000});
          if (begun.done) break;
          offset = begun.next_offset;
          continue;
        }
        throw error;
      }
    }
    if (!begun.done) begun = await link.captureFinish(uploadId);
  }
  if (begun.done && begun.sha256 === item.sha256) {
    await vault.remove(item.id);
    return true;
  }
  return false;
}

let running: Promise<SendReport> | null = null;

/** Send everything waiting, oldest first. One run at a time; a second call joins the running one. */
export function sendWaiting(vault: CaptureVault, link: CaptureLink): Promise<SendReport> {
  if (running) return running;
  running = (async () => {
    const items = await vault.list();
    let sent = 0;
    let problem: SendProblem = "";
    for (const item of items) {
      try {
        if (await sendOne(item, vault, link)) sent++;
        else problem = "damaged";
      } catch (error) {
        if (!(error instanceof CompanionError)) { problem = "offline"; break; }
        if (error.status === 403) { problem = "not_allowed"; break; }
        problem = error.code === "capture_hash_mismatch" ? "damaged" : "failed";
      }
    }
    return {sent, waiting: items.length - sent, problem};
  })().finally(() => {running = null;});
  return running;
}
