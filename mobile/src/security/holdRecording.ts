/** A released or backgrounded press must never start a late microphone take. */
export interface RecordingTake {
  prepare(): Promise<void>;
  start(): Promise<void>;
  finish(): Promise<Uint8Array>;
  discard(): Promise<void>;
}
export class HoldRecording {
  private epoch = 0;
  private pressed = false;
  private taking: RecordingTake | null = null;
  private starting = false;
  private recording = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  constructor(private allow: () => Promise<boolean>, private create: () => RecordingTake,
      private transcript: (bytes: Uint8Array) => Promise<string>, private draft: (text: string) => void, private changed: (recording: boolean) => void,
      private failed: (error: unknown) => void, private maxMs = 120_000) {}
  async begin() {
    if (this.starting || this.taking) return;
    this.pressed = true; this.starting = true;
    const epoch = ++this.epoch;
    const current = () => this.pressed && epoch === this.epoch;
    let take: RecordingTake | null = null;
    try {
      if (!await this.allow() || !current()) return;
      take = this.create(); this.taking = take;
      await take.prepare(); if (!current()) return;
      await take.start(); if (!current()) return;
      this.recording = true; this.changed(true);
      this.timer = setTimeout(() => {void this.end();}, this.maxMs);
    } catch (error) {if (current()) this.failed(error);}
    finally {
      this.starting = false;
      if (!this.recording && take) {
        try {await take.discard();} catch (error) {this.failed(error);}
        if (this.taking === take) this.taking = null;
      }
    }
  }
  async end(cancel = false) {
    this.pressed = false;
    if (this.timer) clearTimeout(this.timer);
    if (cancel) this.epoch++;
    if (!this.recording) return; // Startup owns cleanup if release raced it.
    this.recording = false; this.changed(false);
    const take = this.taking; this.taking = null;
    if (!take) return;
    const epoch = this.epoch;
    try {
      if (cancel) await take.discard();
      else {
        const bytes = await take.finish();
        if (epoch !== this.epoch) return;
        if (!bytes.length || bytes.length > 3 * 1024 * 1024) throw new Error('Keep the recording under three megabytes.');
        const text = await this.transcript(bytes);
        if (epoch === this.epoch) this.draft(text);
      }
    } catch (error) {if (epoch === this.epoch) this.failed(error);}
  }
}
