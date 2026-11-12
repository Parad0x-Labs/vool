import React, { useCallback, useEffect, useMemo, useState } from "react";
import { AppState, Linking, Platform, ScrollView, StatusBar as NativeStatusBar, StyleSheet, Text, Pressable, View } from "react-native";
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
import {deleteApprovalKey, requestPrivateTyping, setNativePrivateSession} from "./src/security/approvalKey.ts";
import {OsPreferenceStore} from "./src/notifications/preference.ts";
import {clearIdleMinutes} from "./src/security/idlePreference.ts";
import {credentialIdentity} from './src/protocol/connection.ts';
import {resetLink} from "./src/protocol/linkState.ts";
import type {ChatSummary} from "./src/protocol/models.ts";
import Header from "./src/ui/Header.tsx";
import Frame from "./src/ui/Frame.tsx";
import KeyboardTip from "./src/ui/KeyboardTip.tsx";
import {installPrivateRoute} from "./src/security/privateRoute.ts";
import {SafeAreaProvider} from "react-native-safe-area-context";
import Drawer, {type DrawerItem} from "./src/ui/Drawer.tsx";
import Icon from "./src/ui/Icon.tsx";
import {C, R} from "./src/ui/theme.ts";

// Connects the built-in private route for away-from-home addresses. Starts nothing by itself.
installPrivateRoute();

type Tab = "chats" | "approvals" | "notifications" | "wallet" | "status";
const TAB_LABELS = {chats: "Chats", approvals: "Approvals", notifications: "Alerts", wallet: "Wallet", status: "Settings"};
const TAB_ICONS = {chats: "message-outline", approvals: "shield-check-outline", notifications: "bell-outline", wallet: "wallet-outline", status: "tune-variant"} as const;
const TABS: Tab[] = ["chats", "approvals", "notifications", "wallet", "status"];
// The status bar is drawn over the app on Android; keep the first row below it.

/** Coarse, non-sensitive wording for the in-app ticker (the OS layer's law:
 * never task content; the detail lives in the screens). */
function tickerText(event: NotificationEvent): string {
  return notificationText(event);
}

export default function App() {
  return <SafeAreaProvider><Root /></SafeAreaProvider>;
}

function Root() {
  useEffect(() => {void requestPrivateTyping();}, []);
  const [, setLocaleTick] = useState(0);
  useEffect(() => subscribeLanguage(() => setLocaleTick(tick => tick + 1)), []);
  const [creds, setCreds] = useState<DeviceCredentials | null>(null);
  const [booted, setBooted] = useState(false);
  const [bootError, setBootError] = useState(false);
  const [bootAttempt, setBootAttempt] = useState(0);
  const [startTab, setStartTab] = useState<Tab>("chats");
  const [incomingLink, setIncomingLink] = useState<string | null>(null);
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
  useEffect(() => {resetLink();}, [client]);
  useEffect(() => {if (lock.locked) setStartTab("chats");}, [lock.locked]);

  const onPaired = useCallback(
    async (next: DeviceCredentials) => {
      await store.saveDeviceCredentials(next);
      setCreds(next);
      setStartTab("status");
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

  if (!booted) {
    return (
      <Frame style={[styles.centered, {direction: rightToLeft() ? "rtl" : "ltr"}]}>
        <StatusBar style="light" />
        <Text style={styles.brand}>VOOL</Text>
        {bootError && <><Text style={styles.lockText}>{tr("Could not open saved access. Unlock your phone and try again.")}</Text>
          <Pressable style={styles.button} onPress={() => {setBootError(false); setBootAttempt(n => n + 1);}}><Text style={styles.buttonText}>{tr("Try again")}</Text></Pressable></>}
      </Frame>
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

  if (lock.locked) return <Frame style={styles.centered}>
    <StatusBar style="light" />
    <Icon name="lock-outline" size={40} color={C.soft} />
    <Text style={styles.brand}>VOOL</Text>
    <Text style={styles.lockText}>{tr("Unlock VOOL")}</Text>
    {lock.noScreenLock && <Text style={styles.lockText}>{tr("Set a screen lock on your phone before unlocking VOOL.")}</Text>}
    {lock.failed && <Text style={styles.lockText}>{tr("Unlock failed. Try your fingerprint, face or phone passcode again.")}</Text>}
    <Pressable style={styles.button} onPress={() => void lock.unlock()}><Text style={styles.buttonText}>{tr("Unlock")}</Text></Pressable>
  </Frame>;

  return <Shell client={client} onRevoked={onRevoked} idleMinutes={lock.idleMinutes} onIdleMinutes={lock.setIdleMinutes}
    onTouch={lock.touch} startTab={startTab} onConnection={async address => {
      if (creds) {const next = {...creds, baseUrl: address}; await store.saveDeviceCredentials(next); client.setConnectionAddress(address); setCreds(next);}
    }} onAwayConnection={async address => {
      if (creds) {
        const next = {...creds}; if (address) next.awayUrl = address; else delete next.awayUrl;
        await store.saveDeviceCredentials(next); client.setAwayAddress(address); setCreds(next);
      }
    }} />;
}


interface ShellProps {
  client: SealedMobileCompanionClient;
  onRevoked: () => Promise<void>;
  idleMinutes: number;
  onIdleMinutes: (value: number) => Promise<void>;
  onTouch: () => void;
  onConnection: (address: string) => Promise<void>;
  onAwayConnection: (address: string) => Promise<void>;
  startTab?: Tab;
}

/** Everything a paired, unlocked phone shows: the side menu and the five screens. */
export function Shell({client, onRevoked, idleMinutes, onIdleMinutes, onTouch, onConnection, onAwayConnection, startTab = "chats"}: ShellProps) {
  const [tab, setTab] = useState<Tab>(startTab);
  const [pick, setPick] = useState<{id: string | null; seq: number} | null>(null);
  const [menu, setMenu] = useState(false);
  const [recent, setRecent] = useState<ChatSummary[]>([]);
  // Screenshots work in chats and settings. Only the money screens stay out of screenshots and the app switcher.
  const moneyScreen = tab === "wallet" || tab === "approvals";
  useEffect(() => {
    if (moneyScreen) void ScreenCapture.preventScreenCaptureAsync("money-screen").catch(() => {});
    return () => {void ScreenCapture.allowScreenCaptureAsync("money-screen").catch(() => {});};
  }, [moneyScreen]);

  // Home or away: ask which way reaches the computer when the app opens and each time it comes back to the front.
  useEffect(() => {
    void client.findRoute().catch(() => {});
    const sub = AppState.addEventListener("change", state => {if (state === "active") void client.findRoute().catch(() => {});});
    return () => sub.remove();
  }, [client]);

  const notifications = useNotifications(client);
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
      setPick(previous => ({id: target.id as string, seq: (previous?.seq ?? 0) + 1})); selectTab("chats");
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

  const items: DrawerItem[] = TABS.map(name => ({
    key: name, label: tr(TAB_LABELS[name]), icon: TAB_ICONS[name],
    badge: name === "notifications" ? notifications.unseen : name === "approvals" ? (notifications.attention ? 1 : 0) : 0,
  }));
  const menuDot = notifications.unseen > 0 || notifications.attention;
  const openChat = (id: string | null) => {
    setPick(previous => ({id, seq: (previous?.seq ?? 0) + 1}));
    selectTab("chats"); setMenu(false);
  };

  return (
    <Frame onTouchStart={onTouch} style={{direction: rightToLeft() ? "rtl" : "ltr"}}>
      <StatusBar style="light" />
      {tab === "chats"
        ? <ChatsScreen client={client} onRevoked={onRevoked} pick={pick} onMenu={() => setMenu(true)}
            onLink={() => selectTab("status")} menuDot={menuDot} onChats={setRecent} />
        : <>
          <Header title={tr(TAB_LABELS[tab])} lead="menu" onLead={() => setMenu(true)} onLink={() => selectTab("status")} dot={menuDot} />
          {showTicker && lastEvent ? (
            <Pressable style={styles.ticker} onPress={() => openAlert(lastEvent)}>
              <Icon name="bell-outline" size={16} color={C.soft} />
              <Text style={styles.tickerText}>{tickerText(lastEvent)}</Text>
            </Pressable>
          ) : null}
          <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent} keyboardShouldPersistTaps="handled">
            {tab === "approvals" && <ApprovalsScreen client={client} onRevoked={onRevoked} />}
            {tab === "status" && <StatusScreen client={client} idleMinutes={idleMinutes} onIdleMinutes={onIdleMinutes} onRevoked={onRevoked} onConnection={onConnection} onAwayConnection={onAwayConnection} />}
            {tab === "wallet" && <WalletScreen client={client} onRevoked={onRevoked} />}
            {tab === "notifications" && <NotificationsScreen client={client} onOpen={openAlert} osEnabled={notifications.osEnabled}
              enableOs={notifications.enableOsNotifications} disableOs={notifications.disableOsNotifications} />}
          </ScrollView>
        </>}
      <KeyboardTip />
      <Drawer open={menu} onClose={() => setMenu(false)} items={items} active={tab}
        onSelect={key => {selectTab(key as Tab); setMenu(false);}}
        chats={recent.slice(0, 12).map(chat => ({id: chat.chat_id, title: chat.title || chat.chat_id}))}
        onChat={openChat} onNewChat={() => openChat(null)} />
    </Frame>
  );
}


const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  centered: { alignItems: "center", justifyContent: "center", gap: 14, padding: 32 },
  brand: { color: C.ink, fontSize: 28, fontWeight: "700", letterSpacing: 3 },
  lockText: { color: C.soft, fontSize: 15, textAlign: "center", lineHeight: 22 },
  button: { backgroundColor: C.send, borderRadius: R.pill, paddingHorizontal: 28, height: 50, alignItems: "center", justifyContent: "center", marginTop: 8 },
  buttonText: { color: C.sendInk, fontSize: 16, fontWeight: "600" },
  ticker: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.raised, borderRadius: R.card, marginHorizontal: 16, marginBottom: 6, paddingHorizontal: 14, paddingVertical: 10 },
  tickerText: { flex: 1, color: C.soft, fontSize: 13 },
  body: { flex: 1 },
  bodyContent: { paddingHorizontal: 18, paddingTop: 6, paddingBottom: 40 },
});
