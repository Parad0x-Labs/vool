import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Linking, SafeAreaView, ScrollView, StyleSheet, Text, Pressable, View } from "react-native";
import { StatusBar } from "expo-status-bar";

import { MobileCompanionClient, type DeviceCredentials } from "./src/protocol/client.ts";
import { credentialStoreForEnvironment } from "./src/store/credentials.ts";
import PairScreen from "./src/screens/PairScreen.tsx";
import ChatsScreen from "./src/screens/ChatsScreen.tsx";
import ApprovalsScreen from "./src/screens/ApprovalsScreen.tsx";
import StatusScreen from "./src/screens/StatusScreen.tsx";

type Tab = "chats" | "approvals" | "status";

export default function App() {
  const [creds, setCreds] = useState<DeviceCredentials | null>(null);
  const [booted, setBooted] = useState(false);
  const [tab, setTab] = useState<Tab>("chats");
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
      setCreds(await store.loadDeviceCredentials());
      setBooted(true);
    })();
  }, [store]);

  const client = useMemo(() => (creds ? new MobileCompanionClient(creds) : null), [creds]);

  const onPaired = useCallback(
    async (next: DeviceCredentials) => {
      await store.saveDeviceCredentials(next);
      setCreds(next);
    },
    [store],
  );

  const onRevoked = useCallback(async () => {
    await store.clearDeviceCredentials();
    setCreds(null);
  }, [store]);

  if (!booted) {
    return (
      <SafeAreaView style={styles.root}>
        <StatusBar style="light" />
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

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="light" />
      <View style={styles.header}>
        <Text style={styles.brand}>VOOL</Text>
        <Text style={styles.headerSub}>
          {client.desktopFingerprint.slice(0, 16)}…
        </Text>
      </View>
      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {tab === "chats" && <ChatsScreen client={client} onRevoked={onRevoked} />}
        {tab === "approvals" && <ApprovalsScreen client={client} onRevoked={onRevoked} />}
        {tab === "status" && <StatusScreen client={client} onRevoked={onRevoked} />}
      </ScrollView>
      <View style={styles.tabs}>
        {(["chats", "approvals", "status"] as Tab[]).map((name) => (
          <Pressable key={name} style={[styles.tab, tab === name && styles.tabActive]} onPress={() => setTab(name)}>
            <Text style={[styles.tabLabel, tab === name && styles.tabLabelActive]}>{name}</Text>
          </Pressable>
        ))}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0b0d10" },
  header: { paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: "#1b2027" },
  brand: { color: "#e8ecf1", fontSize: 20, fontWeight: "700", letterSpacing: 2 },
  headerSub: { color: "#7d8794", fontSize: 11, marginTop: 2 },
  body: { flex: 1 },
  bodyContent: { padding: 16, paddingBottom: 40 },
  tabs: { flexDirection: "row", borderTopWidth: 1, borderTopColor: "#1b2027" },
  tab: { flex: 1, paddingVertical: 14, alignItems: "center" },
  tabActive: { backgroundColor: "#12161b" },
  tabLabel: { color: "#7d8794", fontSize: 13, textTransform: "capitalize" },
  tabLabelActive: { color: "#e8ecf1", fontWeight: "600" },
});
