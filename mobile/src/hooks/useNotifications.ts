// useNotifications — the app-shell wiring for the companion notification loop.
//
// The pure poller (src/protocol/notifications.ts) existed but nothing drove it;
// this hook is that missing piece. It keeps:
//   * a capped in-app event feed (newest last),
//   * an unseen count for the tab badge, cleared when the app is foregrounded
//     to the chats tab,
//   * an attention flag when an approval is waiting (badges the approvals tab),
// and routes coarse kinds to the OPT-IN OS layer (never on by default; the
// user enables it from Status, which calls enableOsNotifications).
// A revoked/unpaired desktop stops the loop (the client is gone); transient
// network errors retry at the next tick, exactly as the poller already does.
import { useCallback, useEffect, useRef, useState } from "react";

import type { MobileCompanionClient } from "../protocol/client.ts";
import type { NotificationEvent } from "../protocol/models.ts";
import {NotificationCursorStore} from "../notifications/cursor.ts";
import {OsPreferenceStore} from "../notifications/preference.ts";
import { NotificationPoller, type Sleep } from "../protocol/notifications.ts";
import {
  ExpoOsNotifications,
  osKindFor,
  type OsNotificationBridge,
} from "../notifications/osNotifications.ts";

const POLL_INTERVAL_MS = 5000;
const MAX_KEPT_EVENTS = 50;

const sleep: Sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export interface NotificationFeed {
  events: NotificationEvent[];
  unseen: number;
  attention: boolean;
  osEnabled: boolean;
  revoked: boolean;
  lastDelivered: NotificationEvent | null;
  clearUnseen: () => void;
  clearAttention: () => void;
  enableOsNotifications: () => Promise<boolean>;
  disableOsNotifications: () => Promise<void>;
}

export function useNotifications(client: MobileCompanionClient | null): NotificationFeed {
  const [events, setEvents] = useState<NotificationEvent[]>([]);
  const [unseen, setUnseen] = useState(0);
  const [attention, setAttention] = useState(false);
  const [osEnabled, setOsEnabled] = useState(false);
  const [revokedClient, setRevokedClient] = useState<MobileCompanionClient|null>(null);
  const activeClient=useRef(client); activeClient.current=client;
  const [lastDelivered, setLastDelivered] = useState<NotificationEvent | null>(null);
  const bridgeRef = useRef<OsNotificationBridge | null>(null);

  useEffect(() => {
    let disposed = false;
    setEvents([]);setUnseen(0);setAttention(false);setLastDelivered(null);setOsEnabled(false);
    if (!client) {setEvents([]); setUnseen(0); setAttention(false); setRevokedClient(null); setLastDelivered(null); return;}
    setRevokedClient(null);
    void client.notificationFeed().then(feed => {if(!disposed){setEvents(feed.events); setUnseen(feed.badge);}}).catch(() => {});
    const bridge = new ExpoOsNotifications();
    bridgeRef.current = bridge;
    const poller = new NotificationPoller(
      client,
      {
        onEvent: (event: NotificationEvent) => {
          if(disposed)return;
          setLastDelivered(event);
          setEvents((prev) => {
            if (event.source_id && prev.some(value => value.source_id === event.source_id)) return prev;
            const next = [...prev, event];
            return next.length > MAX_KEPT_EVENTS ? next.slice(next.length - MAX_KEPT_EVENTS) : next;
          });
          if (osKindFor(event) === "attention") setAttention(true);
        },
      },
      sleep,
      POLL_INTERVAL_MS,
      {
        osBridge: bridge,
        osKindFor,
        cursorStore: new NotificationCursorStore(client.deviceId),
        onBadge: (badge, needsAttention) => {if(disposed)return;setUnseen(badge); if (typeof needsAttention === "boolean") setAttention(needsAttention);},
        onRevoked: () => {if(disposed)return;setEvents([]); setUnseen(0); setAttention(false); setRevokedClient(client);},
      },
    );
    const running = (async () => {
      try {
        const wanted = await new OsPreferenceStore(client.deviceId).load();
        const enabled = wanted && !!await bridge.restoreEnabled?.();
        if (disposed) return;
        setOsEnabled(enabled);
        if (!enabled) bridge.disable();
      } catch {bridge.disable(); if (!disposed) setOsEnabled(false);}
      if (!disposed) await poller.run();
    })();
    return () => {
      disposed = true;
      poller.stop();bridge.disable();
      if(bridgeRef.current===bridge)bridgeRef.current=null;
      void running.catch(() => undefined);
    };
  }, [client]);

  const clearUnseen = useCallback(() => {
    if (client) void client.markNotificationsRead().then(result => {if(activeClient.current===client)setUnseen(result.badge);}).catch(() => {});
  }, [client]);
  const clearAttention = useCallback(() => setAttention(false), []);
  const enableOsNotifications = useCallback(async () => {
    if (!client) return false;
    const bridge = bridgeRef.current ?? new ExpoOsNotifications();
    bridgeRef.current = bridge;
    const granted = await bridge.ensureEnabled();
    if(activeClient.current!==client){bridge.disable();return false;}
    try {await new OsPreferenceStore(client.deviceId).save(granted);}
    catch (error) {bridge.disable(); setOsEnabled(false); throw error;}
    setOsEnabled(granted);
    return granted;
  }, [client]);
  const disableOsNotifications = useCallback(async () => {
    bridgeRef.current?.disable();
    setOsEnabled(false);
    if (client) {
      const preference = new OsPreferenceStore(client.deviceId);
      try {await preference.save(false);}
      catch (error) {await preference.clear().catch(() => {}); throw error;}
    }
  }, [client]);

  return {
    events,
    unseen,
    attention,
    osEnabled,
    revoked:!!client && revokedClient===client,
    lastDelivered,
    clearUnseen,
    clearAttention,
    enableOsNotifications,
    disableOsNotifications,
  };
}
