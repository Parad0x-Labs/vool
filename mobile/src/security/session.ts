/** A foreground unlock never authorizes an action or survives a background trip. */
export class SessionLock {
  locked = true;
  private epoch = 0;
  private active = true;
  private lastTouch = 0;
  private authenticating = false;
  private systemPrompt = false;
  constructor(private changed: () => void, private now = Date.now, readonly idleMs = 300_000) {}
  touch() { if (!this.locked && this.active) this.lastTouch = this.now(); }
  background(state: string) {
    this.active = state === "active";
    if (!this.active) {
      this.locked = true;
      if (state === "background" && !(this.authenticating && this.systemPrompt)) this.epoch++;
    }
    this.changed();
  }
  tick() {
    if (!this.locked && this.now() - this.lastTouch >= this.idleMs) {
      this.locked = true; this.epoch++; this.changed();
    }
  }
  async unlock(authenticate: () => Promise<boolean>, systemPrompt = false) {
    if (this.authenticating || !this.active) return false;
    this.authenticating = true;
    this.systemPrompt = systemPrompt;
    const started = this.now();
    const epoch = this.epoch;
    try {
      const success = await authenticate();
      if (success && epoch === this.epoch && this.active && this.now()-started <= 60_000) {
        this.locked = false; this.lastTouch = this.now(); this.changed(); return true;
      }
      return false;
    } finally {this.authenticating = false; this.systemPrompt = false;}
  }
}
