import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import CaptureScreen from "./src/screens/CaptureScreen.tsx";
import PrivateCaptureSettings from "./src/screens/PrivateCaptureSettings.tsx";
import CompressionSettings from "./src/screens/CompressionSettings.tsx";
import VaultScreen from "./src/screens/VaultScreen.tsx";
import VaultSettings from "./src/screens/VaultSettings.tsx";
import ScreenRecordingSettings from "./src/screens/ScreenRecordingSettings.tsx";
import {useScreenGuard} from "./src/security/screenRecording.ts";
import {nativeFastCrypto, vaultNative} from "./src/vault/native.ts";
import {setFastCrypto} from "./src/protocol/fast.ts";
import {useCaptureQueue, type CaptureQueue} from "./src/capture/useCaptureQueue.ts";
import {capture} from "./src/capture/native.ts";
import SettingsCard from "./src/ui/SettingsCard.tsx";
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
import {reportLink, resetLink} from "./src/protocol/linkState.ts";
import type {ChatSummary} from "./src/protocol/models.ts";
import Header from "./src/ui/Header.tsx";
import Frame from "./src/ui/Frame.tsx";
import KeyboardTip from "./src/ui/KeyboardTip.tsx";
import {installPrivateRoute} from "./src/security/privateRoute.ts";
import {SafeAreaProvider} from "react-native-safe-area-context";
import TabBar, {type TabItem} from "./src/ui/TabBar.tsx";
import Face from "./src/ui/Face.tsx";
import Icon from "./src/ui/Icon.tsx";
import {C, R} from "./src/ui/theme.ts";
import {READ_WIDTH} from "./src/ui/wide.ts";

// Seal and sign on the phone's native side: in JavaScript each file piece held up every tap.
setFastCrypto(nativeFastCrypto());

// Connects the built-in private route for away-from-home addresses. Starts nothing by itself.
installPrivateRoute();

type Tab = "chats" | "approvals" | "notifications" | "wallet" | "status";
const TAB_LABELS = {chats: "Chats", approvals: "Approvals", notifications: "Alerts", wallet: "Wallet", status: "Settings"};
const TAB_ICONS = {chats: "message-outline", approvals: "shield-check-outline", notifications: "bell-outline", wallet: "wallet-outline", status: "cog-outline"} as const;
const TAB_ICONS_ON = {chats: "message", approvals: "shield-check", notifications: "bell", wallet: "wallet", status: "cog"} as const;
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

/** The VOOL Camera home icon and quick settings tile open this link; it never carries pairing data. */
function isCaptureLink(url: string): boolean {
  return /^vool-pair:\/\/capture\/?(\?.*)?$/i.test(url.trim());
}

/** The VOOL Vault home icon opens this link: the Vault, only after VOOL's own unlock. */
function isVaultLink(url: string): boolean {
  return /^vool-pair:\/\/vault\/?(\?.*)?$/i.test(url.trim());
}

function Root() {
  useEffect(() => {void requestPrivateTyping();}, []);
  // A tablet turns with the device; a phone stays upright.
  useEffect(() => {try {capture?.fitScreen?.(false);} catch {/* older build */}}, []);
  const [, setLocaleTick] = useState(0);
  useEffect(() => subscribeLanguage(() => setLocaleTick(tick => tick + 1)), []);
  const [creds, setCreds] = useState<DeviceCredentials | null>(null);
  const [booted, setBooted] = useState(false);
  const [bootError, setBootError] = useState(false);
  const [bootAttempt, setBootAttempt] = useState(0);
  const [startTab, setStartTab] = useState<Tab>("chats");
  const [incomingLink, setIncomingLink] = useState<string | null>(null);
  // The VOOL Camera home icon and quick settings tile open the private camera. While VOOL is locked
  // it opens alone (no fingerprint): only the camera, never the chats, wallet or settings.
  const [cameraAsked, setCameraAsked] = useState(() => capture?.cameraLaunchRecent() ?? false);
  const [cameraOnly, setCameraOnly] = useState(false);
  // The VOOL Vault home icon: never opens anything while locked; after the unlock VOOL opens at the Vault.
  const [vaultAsked, setVaultAsked] = useState(false);
  // Counts taps on the plain VOOL icon: the Shell closes its camera and Vault screens on each.
  const [homeAsked, setHomeAsked] = useState(0);
  const cameraWanted = useRef(false);
  cameraWanted.current = cameraAsked || cameraOnly;
  const store = useMemo(() => credentialStoreForEnvironment(false), []);

  // vool-pair:// deep links (QR scanned outside the app, link tapped in a
  // browser): the SAME typed payload the in-app camera parser reads.
  useEffect(() => {
    const take = (url: string) => {
      if (isCaptureLink(url)) {setVaultAsked(false); setCameraAsked(true); cameraWanted.current = true;}
      else if (isVaultLink(url)) {
        // The Vault icon while the camera is open (locked or not): the camera closes, so the unlock (or the Vault) shows.
        capture?.clearCameraLaunch(); setCameraAsked(false); setCameraOnly(false); cameraWanted.current = false;
        setVaultAsked(true);
      }
      else {setIncomingLink(url);}
    };
    const sub = Linking.addEventListener("url", (event: { url: string }) => take(event.url));
    // When Android restarts VOOL it can hand back the link VOOL was first opened with (the camera or the Vault),
    // so that one counts only when its icon really ran this time.
    Linking.getInitialURL().then((url) => {
      if (!url) return;
      const which = isCaptureLink(url) ? "camera" : isVaultLink(url) ? "vault" : null;
      if (which && capture?.iconUsed && !capture.iconUsed(which)) {return;}
      take(url);
    });
    // The plain VOOL icon opens VOOL itself: a camera or Vault left open by the other icons closes.
    const icon = capture?.addListener("onAppIcon", () => {
      capture?.clearCameraLaunch(); setCameraAsked(false); setCameraOnly(false); cameraWanted.current = false;
      setVaultAsked(false); setHomeAsked(seq => seq + 1);
    });
    return () => {sub.remove(); icon?.remove();};
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
  const lock = useSessionLock(client?.deviceId || null, () => cameraWanted.current || (capture?.cameraLaunchRecent() ?? false));
  // Sending private captures carries on while VOOL is locked; only the screens need the unlock.
  const captureQueue = useCaptureQueue(client);
  useEffect(() => {void setNativePrivateSession(!!client).catch(() => {});}, [client, lock.locked, lock.activity]);
  useEffect(() => {resetLink();}, [client]);
  useEffect(() => {if (lock.locked) setStartTab("chats");}, [lock.locked]);

  const onPaired = useCallback(
    async (next: DeviceCredentials) => {
      await store.saveDeviceCredentials(next);
      setCreds(next);
      setStartTab("chats");
      setCameraAsked(false);
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
        <Face size={132} />
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

  // Camera only, without the unlock: closing it shows the Unlock screen.
  if (lock.locked && (cameraAsked || cameraOnly) && captureQueue.available) return <Frame style={styles.root}>
    <CaptureScreen waiting={captureQueue.waiting} sending={captureQueue.sending} problem={captureQueue.problem} saving={captureQueue.saving}
      saveFailed={captureQueue.saveFailed} onCaptured={captureQueue.captured}
      onClose={() => {capture?.clearCameraLaunch(); setCameraAsked(false); setCameraOnly(false);}} extraSettings={<SmallerFiles />} />
  </Frame>;

  if (lock.locked) return <Frame style={styles.centered}>
    <StatusBar style="light" />
    <Face size={132} />
    <Text style={styles.brand}>{tr("Unlock VOOL")}</Text>
    {lock.noScreenLock && <Text style={styles.lockText}>{tr("Set a screen lock on your phone before unlocking VOOL.")}</Text>}
    {lock.failed && <Text style={styles.lockText}>{tr("Unlock failed. Try your fingerprint, face or phone passcode again.")}</Text>}
    <Pressable style={styles.button} android_ripple={{color: C.rippleDark}} accessibilityRole="button" onPress={() => void lock.unlock()}>
      <Icon name="fingerprint" size={22} color={C.sendInk} /><Text style={styles.buttonText}>{tr("Unlock")}</Text></Pressable>
    {captureQueue.available ? <Pressable style={styles.cameraButton} android_ripple={{color: C.ripple}} accessibilityRole="button" onPress={() => setCameraOnly(true)}>
      <Icon name="camera-iris" size={20} color={C.acc} /><Text style={styles.cameraButtonText}>{tr("Open private camera")}</Text></Pressable> : null}
  </Frame>;

  return <Shell client={client} onRevoked={onRevoked} idleMinutes={lock.idleMinutes} onIdleMinutes={lock.setIdleMinutes}
    onTouch={lock.touch} startTab={startTab} queue={captureQueue} cameraAsked={cameraAsked} onCameraShown={() => {capture?.clearCameraLaunch(); setCameraAsked(false);}}
    vaultAsked={vaultAsked} onVaultShown={() => setVaultAsked(false)} homeAsked={homeAsked} onConnection={async address => {
      if (creds) {const next = {...creds, baseUrl: address}; await store.saveDeviceCredentials(next); client.setConnectionAddress(address); setCreds(next);}
    }} onAwayConnection={async address => {
      if (creds) {
        const next = {...creds}; if (address) next.awayUrl = address; else delete next.awayUrl;
        await store.saveDeviceCredentials(next); client.setAwayAddress(address); setCreds(next);
      }
    }} />;
}


/** The optional smaller files, as a card in the camera's own settings. */
function SmallerFiles() {
  return capture ? <SettingsCard icon="archive-arrow-down-outline" title={tr("Smaller files")}><CompressionSettings /></SettingsCard> : null;
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
  queue: CaptureQueue;
  /** The camera was asked for from outside (home icon, quick settings). */
  cameraAsked?: boolean;
  onCameraShown?: () => void;
  /** The Vault was asked for from its home icon (only reaches here unlocked). */
  vaultAsked?: boolean;
  /** Goes up each time the plain VOOL icon is tapped while VOOL runs. */
  homeAsked?: number;
  onVaultShown?: () => void;
}

/** Everything a paired, unlocked phone shows: the five screens and the row of tabs under them. */
export function Shell({client, onRevoked, idleMinutes, onIdleMinutes, onTouch, onConnection, onAwayConnection, startTab = "chats", queue, cameraAsked = false, onCameraShown, vaultAsked = false, onVaultShown, homeAsked = 0}: ShellProps) {
  const [tab, setTab] = useState<Tab>(startTab);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [vaultOpen, setVaultOpen] = useState(false);
  useEffect(() => {
    if (!vaultAsked) return;
    if (vaultNative) {setCameraOpen(false); setVaultOpen(true);}
    onVaultShown?.();
  }, [vaultAsked, onVaultShown]);
  const openVault = vaultNative ? () => {setCameraOpen(false); setVaultOpen(true);} : undefined;
  useEffect(() => {
    if (!cameraAsked) return;
    if (queue.available) {setVaultOpen(false); setCameraOpen(true);}
    onCameraShown?.();
  }, [cameraAsked, queue.available, onCameraShown]);
  useEffect(() => {
    if (!homeAsked) return;
    setCameraOpen(false); setVaultOpen(false);
  }, [homeAsked]);
  const openCamera = queue.available ? () => {setVaultOpen(false); setCameraOpen(true);} : undefined;
  const [pick, setPick] = useState<{id: string | null; seq: number} | null>(null);
  const [recent, setRecent] = useState<ChatSummary[]>([]);
  // Inside one chat the message box takes the place of the tabs.
  const [inChat, setInChat] = useState(false);
  // Screenshots work in chats and settings. Only the money screens stay out of screenshots and the app switcher.
  const moneyScreen = tab === "wallet" || tab === "approvals";
  useScreenGuard("money-screen", moneyScreen);

  // Home or away: ask which way reaches the computer when the app opens and each time it comes back to the front.
  useEffect(() => {
    // Neither way answered at start: say so plainly instead of staying on "connecting".
    void client.findRoute().then(found => {if (!found && client.awayUrl) reportLink(false);}).catch(() => {});
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

  const items: TabItem[] = TABS.map(name => ({
    key: name, label: tr(TAB_LABELS[name]), icon: TAB_ICONS[name], iconOn: TAB_ICONS_ON[name],
    badge: name === "notifications" ? notifications.unseen : name === "approvals" ? (notifications.attention ? 1 : 0) : 0,
  }));

  return (
    <Frame onTouchStart={onTouch} style={{direction: rightToLeft() ? "rtl" : "ltr"}}>
      <StatusBar style="light" />
      {tab === "chats"
        ? <ChatsScreen client={client} onRevoked={onRevoked} pick={pick}
            onLink={() => selectTab("status")} onChats={setRecent} onThread={setInChat} onCamera={openCamera} onVault={openVault} />
        : <>
          <View style={styles.column}>
            <Header title={tr(TAB_LABELS[tab])} onLink={() => selectTab("status")} onCamera={openCamera} onVault={openVault} />
            {showTicker && lastEvent ? (
              <Pressable style={styles.ticker} onPress={() => openAlert(lastEvent)}>
                <Icon name="bell-outline" size={16} color={C.acc} />
                <Text style={styles.tickerText}>{tickerText(lastEvent)}</Text>
              </Pressable>
            ) : null}
          </View>
          <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent} keyboardShouldPersistTaps="handled">
            {tab === "approvals" && <ApprovalsScreen client={client} onRevoked={onRevoked} />}
            {tab === "status" && <StatusScreen client={client} idleMinutes={idleMinutes} onIdleMinutes={onIdleMinutes} onRevoked={onRevoked} onConnection={onConnection} onAwayConnection={onAwayConnection}
              captureSettings={<>
                {openCamera ? <PrivateCaptureSettings waiting={queue.waiting} saving={queue.saving} onOpen={openCamera}><CompressionSettings /></PrivateCaptureSettings> : null}
                {openVault ? <VaultSettings onOpen={openVault} /> : null}
                <ScreenRecordingSettings />
              </>} />}
            {tab === "wallet" && <WalletScreen client={client} onRevoked={onRevoked} />}
            {tab === "notifications" && <NotificationsScreen client={client} onOpen={openAlert} osEnabled={notifications.osEnabled}
              enableOs={notifications.enableOsNotifications} disableOs={notifications.disableOsNotifications} />}
          </ScrollView>
        </>}
      {tab === "chats" && inChat ? null : <TabBar items={items} active={tab} onSelect={key => selectTab(key as Tab)} />}
      {recent.length > 0 && <KeyboardTip />}
      {cameraOpen ? <View style={StyleSheet.absoluteFill}>
        <CaptureScreen waiting={queue.waiting} sending={queue.sending} problem={queue.problem} saving={queue.saving} saveFailed={queue.saveFailed}
          onCaptured={queue.captured} onClose={() => {setCameraOpen(false);}} extraSettings={<SmallerFiles />} />
      </View> : null}
      {vaultOpen ? <VaultScreen client={client} onClose={() => {setVaultOpen(false);}} /> : null}
    </Frame>
  );
}


const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  centered: { alignItems: "center", justifyContent: "center", gap: 14, padding: 32 },
  brand: { color: C.ink, fontSize: 26, fontWeight: "700", letterSpacing: -0.4, textAlign: "center", marginTop: 6 },
  lockText: { color: C.soft, fontSize: 15, textAlign: "center", lineHeight: 22 },
  cameraButton: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 18, minHeight: 48, borderRadius: 24, borderWidth: 1, borderColor: C.line, backgroundColor: C.raised, overflow: "hidden" },
  cameraButtonText: { color: C.ink, fontSize: 15, fontWeight: "600" },
  button: { flexDirection: "row", gap: 8, backgroundColor: C.send, borderRadius: R.pill, paddingHorizontal: 34, height: 52, alignItems: "center", justifyContent: "center", marginTop: 14, overflow: "hidden" },
  buttonText: { color: C.sendInk, fontSize: 16, fontWeight: "600" },
  ticker: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 16, marginHorizontal: 20, marginBottom: 8, paddingHorizontal: 14, paddingVertical: 11 },
  tickerText: { flex: 1, color: C.soft, fontSize: 13 },
  body: { flex: 1 },
  // On a tablet, settings and lists keep to a readable column in the middle.
  column: { width: "100%", maxWidth: READ_WIDTH, alignSelf: "center" },
  bodyContent: { width: "100%", maxWidth: READ_WIDTH, alignSelf: "center", paddingHorizontal: 20, paddingTop: 4, paddingBottom: 32 },
});
