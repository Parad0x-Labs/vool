// Completion/failure notification poller — IN-APP POLLING by design (the
// verified channel for this local-network companion; no push server exists).
// One loop, injectable sleep, no platform timers in the logic. The phone
// badges "turn finished" / "turn failed" / "needs your approval" from the
// desktop's own typed event stream. OS-level notifications are an OPT-IN
// layer on top (src/notifications/osNotifications.ts), not this poller.
import {CompanionError, type MobileCompanionClient} from "./client.ts";
import type { NotificationEvent } from "./models.ts";

export type Sleep = (ms: number) => Promise<void>;

export interface NotificationSink {
  onEvent(event: NotificationEvent): void | Promise<void>;
}

export interface PollerOptions {
  /** Optional OS bridge: coarse, non-sensitive outcome notifications. */
  osBridge?: { notify(kind: "completion" | "failure" | "attention"): Promise<void>; notifyEvent?(event: NotificationEvent): Promise<void> };
  osKindFor?(event: NotificationEvent): "completion" | "failure" | "attention" | null;
  cursorStore?: {load(): Promise<number>; save(cursor: number): Promise<void>};
  onBadge?(badge: number, attention?: boolean): void;
  onRevoked?(): void | Promise<void>;
}

export class NotificationPoller {
  private stopped = false;
  private cursor = 0;
  private loaded = false;
  private readonly client: MobileCompanionClient;
  private readonly sink: NotificationSink;
  private readonly sleep: Sleep;
  private readonly intervalMs: number;
  private readonly os?: PollerOptions;

  constructor(
    client: MobileCompanionClient,
    sink: NotificationSink,
    sleep: Sleep,
    intervalMs = 3000,
    os?: PollerOptions,
  ) {
    this.client = client;
    this.sink = sink;
    this.sleep = sleep;
    this.intervalMs = intervalMs;
    this.os = os;
  }

  stop(): void { this.stopped = true; }

  async runOnce(): Promise<number> {
    if (this.stopped) return 0;
    if (!this.loaded) {this.cursor = await this.os?.cursorStore?.load() ?? 0; this.loaded = true;}
    if (this.stopped) return 0;
    const { events, nextAfter, badge, attention } = await this.client.pollNotifications(this.cursor);
    if (this.stopped) return 0;
    if (typeof badge === "number") this.os?.onBadge?.(badge, attention);
    for (const event of events) {
      if (this.stopped) return 0;
      if (this.os?.cursorStore && typeof event.seq === "number") {
        if (event.seq <= this.cursor) continue;
        // Persist before presentation: reconnect cannot schedule the same
        // OS banner again. A crash in this window may lose a banner; the PC
        // feed retains the unread item for the in-app view.
        await this.os.cursorStore.save(event.seq); this.cursor = event.seq;
      }
      if (this.stopped) return 0;
      await this.sink.onEvent(event);
      if (this.stopped) return 0;
      if (event.os_notification_allowed === false) continue;
      if (this.os?.osBridge?.notifyEvent && event.target) {
        await this.os.osBridge.notifyEvent(event);
      } else if (this.os?.osBridge && this.os.osKindFor) {
        const kind = this.os.osKindFor(event);
        if (kind) await this.os.osBridge.notify(kind);
      }
    }
    if (this.stopped) return 0;
    if (this.os?.cursorStore) await this.os.cursorStore.save(nextAfter);
    this.cursor = nextAfter;
    return events.length;
  }

  async run(): Promise<void> {
    while (!this.stopped) {
      try {
        await this.runOnce();
      } catch (error) {
        if (this.stopped) return;
        if (error instanceof CompanionError && ["device_revoked", "device_unknown"].includes(error.code)) {
          this.stopped = true; await this.os?.onRevoked?.(); return;
        }
        // Transient (desktop asleep, LAN hiccup): the loop retries at the
        // next tick; a revoked device surfaces as a stop-worthy error the
        // app layer observes via its own client call.
      }
      await this.sleep(this.intervalMs);
    }
  }
}
