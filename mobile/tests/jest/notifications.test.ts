// Jest — the notification loop's app logic: the poller's cursor discipline,
// the coarse OS-kind mapping (privacy law: no task content), and that the
// opt-in OS bridge is only ever consulted for mapped kinds.
import type { MobileCompanionClient } from "../../src/protocol/client.ts";
import type { NotificationEvent } from "../../src/protocol/models.ts";
import { NotificationPoller } from "../../src/protocol/notifications.ts";
import { osKindFor } from "../../src/notifications/osNotifications.ts";

function event(partial: Partial<NotificationEvent> & { seq: number }): NotificationEvent {
  return {
    event_type: partial.event_type ?? "turn_finalized",
    mobile_kind: partial.mobile_kind ?? "completion",
    ...partial,
  } as NotificationEvent;
}

function fakeClient(events: NotificationEvent[], nextAfter: number): MobileCompanionClient {
  return {
    pollNotifications: async () => ({ events, nextAfter }),
  } as unknown as MobileCompanionClient;
}

describe("NotificationPoller.runOnce", () => {
  it("advances the cursor from next_after and forwards each event to the sink", async () => {
    const seen: NotificationEvent[] = [];
    const askedAfter: number[] = [];
    let call = 0;
    // One stateful client: the first poll returns two events (next_after 8),
    // the second returns none — the SAME poller must ask after 8 the second time.
    const client = {
      pollNotifications: async (after: number) => {
        askedAfter.push(after);
        call += 1;
        if (call === 1) return { events: [event({ seq: 7 }), event({ seq: 8 })], nextAfter: 8 };
        return { events: [], nextAfter: after };
      },
    } as unknown as MobileCompanionClient;
    const poller = new NotificationPoller(client, { onEvent: async (e) => { seen.push(e); } }, () => new Promise(() => undefined));

    expect(await poller.runOnce()).toBe(2);
    expect(seen.map((e) => e.seq)).toEqual([7, 8]);
    expect(await poller.runOnce()).toBe(0);
    expect(askedAfter).toEqual([0, 8]);
  });

  it("routes only mapped kinds to the OS bridge", async () => {
    const notified: string[] = [];
    const osKinds: (string | null)[] = [];
    const poller = new NotificationPoller(
      fakeClient(
        [
          event({ seq: 1, mobile_kind: "completion", event_type: "turn_finalized" }),
          event({ seq: 2, mobile_kind: "failure", event_type: "turn_failed" }),
          event({ seq: 3, mobile_kind: "info", event_type: "model_changed" }),
          event({ seq: 4, mobile_kind: "info", event_type: "permission_required" }),
        ],
        4,
      ),
      { onEvent: async () => undefined },
      () => new Promise(() => undefined),
      1000,
      {
        osBridge: { notify: async (kind) => { notified.push(kind); } },
        osKindFor: (e) => { const k = osKindFor(e); osKinds.push(k); return k; },
      },
    );

    await poller.runOnce();

    // completion + failure map; a plain info event does not; permission_required
    // is attention even though its coarse mobile_kind is info.
    expect(notified).toEqual(["completion", "failure", "attention"]);
    expect(osKinds).toEqual(["completion", "failure", null, "attention"]);
  });

  it("an empty result keeps the cursor and notifies nothing", async () => {
    const notified: string[] = [];
    const poller = new NotificationPoller(
      fakeClient([], 0),
      { onEvent: async () => { throw new Error("no events expected"); } },
      () => new Promise(() => undefined),
      1000,
      { osBridge: { notify: async (kind) => { notified.push(kind); } }, osKindFor },
    );
    expect(await poller.runOnce()).toBe(0);
    expect(notified).toEqual([]);
  });
});

describe("osKindFor (the privacy mapping)", () => {
  it("maps permission-required events to attention and never carries task text", () => {
    expect(osKindFor(event({ seq: 1, event_type: "permission_required", mobile_kind: "info" }))).toBe("attention");
    expect(osKindFor(event({ seq: 2, event_type: "turn_failed", mobile_kind: "failure" }))).toBe("failure");
    expect(osKindFor(event({ seq: 3, event_type: "turn_finalized", mobile_kind: "completion" }))).toBe("completion");
    expect(osKindFor(event({ seq: 4, event_type: "model_changed", mobile_kind: "info" }))).toBeNull();
  });
});
