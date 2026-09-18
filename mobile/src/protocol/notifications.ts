// Completion/failure notification poller — IN-APP POLLING by design (the
// verified channel for this local-network companion; no push server exists).
// One loop, injectable sleep, no platform timers in the logic. The phone
// badges "turn finished" / "turn failed" / "needs your approval" from the
// desktop's own typed event stream. OS-level notifications are an OPT-IN
// layer on top (src/notifications/osNotifications.ts), not this poller.
import type { MobileCompanionClient } from "./client.ts";
import type { NotificationEvent } from "./models.ts";

export type Sleep = (ms: number) => Promise<void>;

export interface NotificationSink {
  onEvent(event: NotificationEvent): void | Promise<void>;
}

export interface PollerOptions {
  /** Optional OS bridge: coarse, non-sensitive outcome notifications. */
  osBridge?: { notify(kind: "completion" | "failure" | "attention"): Promise<void> };
  osKindFor?(event: NotificationEvent): "completion" | "failure" | "attention" | null;
}

export class NotificationPoller {
  private stopped = false;
  private cursor = 0;
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
    const { events, nextAfter } = await this.client.pollNotifications(this.cursor);
    this.cursor = nextAfter;
    for (const event of events) {
      await this.sink.onEvent(event);
      if (this.os?.osBridge && this.os.osKindFor) {
        const kind = this.os.osKindFor(event);
        if (kind) await this.os.osBridge.notify(kind);
      }
    }
    return events.length;
  }

  async run(): Promise<void> {
    while (!this.stopped) {
      try {
        await this.runOnce();
      } catch {
        // Transient (desktop asleep, LAN hiccup): the loop retries at the
        // next tick; a revoked device surfaces as a stop-worthy error the
        // app layer observes via its own client call.
      }
      await this.sleep(this.intervalMs);
    }
  }
}
