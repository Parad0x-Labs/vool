import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Linking, SafeAreaView, ScrollView, StyleSheet, Text, Pressable, View } from "react-native";
import { StatusBar } from "expo-status-bar";

import { type DeviceCredentials } from "./src/protocol/client.ts";
import { isSealedCredentials, SealedMobileCompanionClient } from "./src/protocol/sealed.ts";
import {SecureProposalStore} from "./src/store/paymentDraft.ts";
import { discardOutbox } from "./src/protocol/outbox.ts";
import { credentialStoreForEnvironment } from "./src/store/credentials.ts";
import { useNotifications } from "./src/hooks/useNotifications.ts";
import {NotificationCursorStore} from "./src/notifications/cursor.ts";
import type {NotificationEvent} from "./src/protocol/models.ts";
import PairScreen from "./src/screens/PairScreen.tsx";
import ChatsScreen from "./src/screens/ChatsScreen.tsx";
import ApprovalsScreen from "./src/screens/ApprovalsScreen.tsx";
import StatusScreen from "./src/screens/StatusScreen.tsx";
import {notificationText, rightToLeft, subscribeLanguage, tr} from "./src/i18n/index.ts";
import WalletScreen from "./src/screens/WalletScreen.tsx";
import NotificationsScreen from "./src/screens/NotificationsScreen.tsx";
import {useSessionLock} from "./src/security/useSessionLock.ts";
import * as ScreenCapture from "expo-screen-capture";
import {deleteApprovalKey, setNativePrivateSession} from "./src/security/approvalKey.ts";
import {OsPreferenceStore} from "./src/notifications/preference.ts";
import {clearIdleMinutes} from "./src/security/idlePreference.ts";
import {credentialIdentity} from './src/protocol/connection.ts';

type Tab = "chats" | "approvals" | "notifications" | "wallet" | "status";
const TAB_LABELS = {chats: "Chats", approvals: "Approvals", notifications: "Alerts", wallet: "Wallet", status: "Status"};

/** Coarse, non-sensitive wording for the in-app ticker (the OS layer's law:
 * never task content; the detail lives in the screens). */
function tickerText(event: NotificationEvent): string {
  return notificationText(event);
}

export default function App() {
  const [, setLocaleTick] = useState(0);
  useEffect(() => subscribeLanguage(() => setLocaleTick(tick => tick + 1)), []);
  const [creds, setCreds] = useState<DeviceCredentials | null>(null);
  const [booted, setBooted] = useState(false);
  const [bootError, setBootError] = useState(false);
  const [bootAttempt, setBootAttempt] = useState(0);
  const [tab, setTab] = useState<Tab>("chats");
  const [incomingLink, setIncomingLink] = useState<string | null>(null);
  const [selectedChat, setSelectedChat] = useState<string | null>(null);
  const store = useMemo(() => credentialStoreForEnvironment(false), []);

  // vool-pair:// deep links (QR scanned outside the app, link tapped in a
  // browser): the SAME typed payload the in-app camera parser reads.
  useEffect(() => {
    const handle = (event: { url: string }) => setIncomingLink(event.url);
    const sub = Linking.addEventListener("url", handle);
    Linking.getInitialURL().then((url) => { if (url) setIncomingLink(url); });
    return () => sub.remove();
  }, []);

  useEffect(() => {
    (async () => {
      const loaded = await store.loadDeviceCredentials();
      if (loaded && !isSealedCredentials(loaded)) await store.clearDeviceCredentials();
      setCreds(loaded && isSealedCredentials(loaded) ? loaded : null);
      setBooted(true);
      setBootError(false);
    })().catch(() => {setBootError(true);});
  }, [store, bootAttempt]);

  const identity = creds ? credentialIdentity(creds) : '';
  const client = useMemo(() => (creds && isSealedCredentials(creds) ? new SealedMobileCompanionClient(creds) : null), [identity]);
  const lock = useSessionLock(client?.deviceId || null);
  useEffect(() => {void setNativePrivateSession(!!client).catch(() => {});}, [client, lock.locked, lock.activity]);
  useEffect(() => {
    if (client) void ScreenCapture.preventScreenCaptureAsync("private-session").catch(() => {});
    return () => {void ScreenCapture.allowScreenCaptureAsync("private-session").catch(() => {});};
  }, [client]);

  const onPaired = useCallback(
    async (next: DeviceCredentials) => {
      await store.saveDeviceCredentials(next);
      setCreds(next);
      setTab("status");
    },
    [store],
  );

  const onRevoked = useCallback(async () => {
    try {
      if (client) {
        const cleared = await Promise.allSettled([discardOutbox(client), clearIdleMinutes(client.deviceId), new NotificationCursorStore(client.deviceId).clear(), new OsPreferenceStore(client.deviceId).clear(), new SecureProposalStore(client.deviceId).clear(), ...(creds?.approvalKeyAlias ? [deleteApprovalKey(creds.approvalKeyAlias)] : [])]);
        if (cleared.some(result => result.status === "rejected")) throw new Error(tr("Some saved data could not be removed. Clear this app’s data on your phone."));
      }
    } finally {
      try {await store.clearDeviceCredentials();}
      finally {setCreds(null);}
    }
  }, [client, store, creds]);

  const notifications = useNotifications(lock.locked ? null : client);
  const clearUnseen = notifications.clearUnseen;
  const clearAttention = notifications.clearAttention;
  const selectTab = useCallback(
    (next: Tab) => {
      setTab(next);
      // Opening a tab is the acknowledgement of what it shows.
      if (next === "notifications") clearUnseen();
      if (next === "approvals") clearAttention();
    },
    [clearUnseen, clearAttention],
  );

  useEffect(() => {if (notifications.revoked && client) void onRevoked().catch(() => {});}, [notifications.revoked, client, onRevoked]);
  const openAlert = useCallback((event: NotificationEvent) => {
    const target = event.target as {screen?: unknown; id?: unknown} | undefined;
    if (target?.screen === "chats" && typeof target.id === "string" && target.id) {
      setSelectedChat(target.id); selectTab("chats");
    } else if (target?.screen === "wallet") selectTab("wallet");
    else if (target?.screen === "approvals") selectTab("approvals");
    else selectTab("notifications");
  }, [selectTab]);
  useEffect(() => {
    if (!client) return;
    let disposed = false;
    let subscription: {remove(): void} | undefined;
    void import("expo-notifications").then(async module => {
      if (disposed) return;
      const handle = (response: import("expo-notifications").NotificationResponse) => {
        if (!disposed) openAlert({event_type: "notification_opened", mobile_kind: "info", target: response.notification.request.content.data?.target});
        void module.clearLastNotificationResponseAsync();
      };
      subscription = module.addNotificationResponseReceivedListener(handle);
      const previous = await module.getLastNotificationResponseAsync();
      if (previous && !disposed) handle(previous);
    }).catch(() => {});
    return () => {disposed = true; subscription?.remove();};
  }, [client, openAlert]);

  const lastEvent = notifications.lastDelivered;
  const showTicker =
    notifications.unseen > 0 &&
    lastEvent?.os_notification_allowed !== false &&
    tab !== "chats" &&
    (lastEvent?.mobile_kind === "failure" || lastEvent?.mobile_kind === "completion");

  if (!booted) {
    return (
      <SafeAreaView style={[styles.root, {direction: rightToLeft() ? "rtl" : "ltr"}]}>
        <StatusBar style="light" />
        {bootError && <><Text style={styles.tickerText}>{tr("Could not open saved access. Unlock your phone and try again.")}</Text>
          <Pressable style={styles.tab} onPress={() => {setBootError(false); setBootAttempt(n => n + 1);}}><Text style={styles.brand}>{tr("Try again")}</Text></Pressable></>}
      </SafeAreaView>
    );
  }

  if (!client) {
    return (
      <PairScreen
        onPaired={onPaired}
        incomingLink={incomingLink}
        onLinkConsumed={() => setIncomingLink(null)}
      />
    );
  }

  if (lock.locked) return <SafeAreaView style={styles.root}>
    <Text style={styles.brand}>VOOL</Text>
    <Text style={styles.tickerText}>{tr("Unlock VOOL")}</Text>
    {lock.noScreenLock && <Text style={styles.tickerText}>{tr("Set a screen lock on your phone before unlocking VOOL.")}</Text>}
    {lock.failed && <Text style={styles.tickerText}>{tr("Unlock failed. Try your fingerprint, face or phone passcode again.")}</Text>}
    <Pressable style={styles.tab} onPress={() => void lock.unlock()}><Text style={styles.brand}>{tr("Unlock")}</Text></Pressable>
  </SafeAreaView>;

  return (
    <SafeAreaView onTouchStart={lock.touch} style={[styles.root, {direction: rightToLeft() ? "rtl" : "ltr"}]}>
      <StatusBar style="light" />
      <View style={styles.header}>
        <Text style={styles.brand}>VOOL</Text>
        <Text style={styles.headerSub}>
          {client.desktopFingerprint.slice(0, 16)}…
        </Text>
      </View>
      {showTicker && lastEvent ? (
        <Pressable style={styles.ticker} onPress={() => openAlert(lastEvent)}>
          <Text style={styles.tickerText}>{tickerText(lastEvent)}</Text>
        </Pressable>
      ) : null}
      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {tab === "chats" && <ChatsScreen client={client} onRevoked={onRevoked} initialChatId={selectedChat} />}
        {tab === "approvals" && <ApprovalsScreen client={client} onRevoked={onRevoked} />}
        {tab === "status" && <StatusScreen client={client} idleMinutes={lock.idleMinutes} onIdleMinutes={lock.setIdleMinutes} onRevoked={onRevoked} onConnection={async address => {
          if (creds) {const next = {...creds, baseUrl: address}; await store.saveDeviceCredentials(next); client.setConnectionAddress(address); setCreds(next);}
        }} />}
        {tab === "wallet" && <WalletScreen client={client} onRevoked={onRevoked} />}
        {tab === "notifications" && <NotificationsScreen client={client} onOpen={openAlert} osEnabled={notifications.osEnabled}
          enableOs={notifications.enableOsNotifications} disableOs={notifications.disableOsNotifications} />}
      </ScrollView>
      <View style={styles.tabs}>
        {(["chats", "approvals", "notifications", "wallet", "status"] as Tab[]).map((name) => {
          const badge =
            name === "notifications"
              ? notifications.unseen
              : name === "approvals"
                ? (notifications.attention ? 1 : 0)
                : 0;
          return (
            <Pressable key={name} style={[styles.tab, tab === name && styles.tabActive]} onPress={() => selectTab(name)}>
              <Text style={[styles.tabLabel, tab === name && styles.tabLabelActive]}>{tr(TAB_LABELS[name])}</Text>
              {badge > 0 ? (
                <View style={styles.badge} aria-label={tr("New: {count}", {count: badge})}>
                  <Text style={styles.badgeText}>{badge > 9 ? "9+" : String(badge)}</Text>
                </View>
              ) : null}
            </Pressable>
          );
        })}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0b0d10" },
  header: { paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: "#1b2027" },
  brand: { color: "#e8ecf1", fontSize: 20, fontWeight: "700", letterSpacing: 2 },
  headerSub: { color: "#7d8794", fontSize: 11, marginTop: 2 },
  ticker: { backgroundColor: "#12161b", borderBottomWidth: 1, borderBottomColor: "#1b2027", paddingHorizontal: 16, paddingVertical: 8 },
  tickerText: { color: "#aeb9c4", fontSize: 12 },
  body: { flex: 1 },
  bodyContent: { padding: 16, paddingBottom: 40 },
  tabs: { flexDirection: "row", borderTopWidth: 1, borderTopColor: "#1b2027" },
  tab: { flex: 1, paddingVertical: 14, alignItems: "center" },
  tabActive: { backgroundColor: "#12161b" },
  tabLabel: { color: "#7d8794", fontSize: 13, textTransform: "capitalize" },
  tabLabelActive: { color: "#e8ecf1", fontWeight: "600" },
  badge: { position: "absolute", top: 6, right: "22%", backgroundColor: "#c2452d", borderRadius: 8, minWidth: 16, paddingHorizontal: 4, paddingVertical: 1 },
  badgeText: { color: "#f4f6f9", fontSize: 10, fontWeight: "700", textAlign: "center" },
});
