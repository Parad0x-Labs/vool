import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { CompanionError, type MobileCompanionClient } from "../protocol/client.ts";
import type { ChatMessage, StatusSnapshot, TurnProof } from "../protocol/models.ts";

interface Props {
  client: MobileCompanionClient;
  onRevoked: () => void | Promise<void>;
}

/** Current model, local/cloud status, Proof Chip, and the device's own standing. */
export default function StatusScreen({ client, onRevoked }: Props) {
  const [snapshot, setSnapshot] = useState<StatusSnapshot | null>(null);
  const [proof, setProof] = useState<TurnProof | null>(null);
  const [error, setError] = useState("");

  const guard = useCallback(
    (exc: unknown) => {
      if (exc instanceof CompanionError && (exc.code === "device_revoked" || exc.code === "signature_invalid")) {
        void onRevoked();
        return true;
      }
      return false;
    },
    [onRevoked],
  );

  useEffect(() => {
    (async () => {
      try {
        setSnapshot(await client.statusSnapshot());
      } catch (exc) {
        if (!guard(exc)) setError(String((exc as Error).message || exc));
      }
    })();
  }, [client, guard]);

  const loadLatestProof = useCallback(async () => {
    setError("");
    setProof(null);
    try {
      // Proof Chip of the newest turn: newest chat, newest assistant row that
      // carries a request_id.
      const chats = await client.listChats();
      const newest = chats.find((c) => !c.archived) || chats[0];
      if (!newest) {
        setError("No chats yet — send one first.");
        return;
      }
      const history: ChatMessage[] = await client.chatHistory(newest.chat_id, 200);
      for (let i = history.length - 1; i >= 0; i--) {
        const rid = history[i].request_id;
        if (rid) {
          setProof(await client.turnProof(newest.chat_id, rid));
          return;
        }
      }
      setError("No turn on this desktop carries a proof binding yet.");
    } catch (exc) {
      if (!guard(exc)) setError(String((exc as Error).message || exc));
    }
  }, [client, guard]);

  if (error && !snapshot) {
    return <Text style={styles.error}>{error}</Text>;
  }
  if (!snapshot) {
    return <ActivityIndicator style={styles.center} color="#7d8794" />;
  }

  const cloudOn = snapshot.model.cloud_state === "ok";

  return (
    <ScrollView>
      <Text style={styles.section}>Desktop</Text>
      <View style={styles.card}>
        <Text style={styles.label}>
          {snapshot.desktop.display_name} · {snapshot.model.current || "no model"}
        </Text>
        <View style={styles.row}>
          <View style={[styles.pill, cloudOn ? styles.pillGreen : styles.pillGrey]}>
            <Text style={styles.pillText}>{cloudOn ? "CLOUD OK" : "LOCAL"}</Text>
          </View>
          <View style={[styles.pill, snapshot.relay.enabled ? styles.pillAmber : styles.pillGrey]}>
            <Text style={styles.pillText}>{snapshot.relay.mode}</Text>
          </View>
        </View>
        <Text style={styles.fingerprint}>authority {snapshot.desktop.short_fingerprint}</Text>
        {snapshot.model.cloud_state && snapshot.model.cloud_state !== "ok" ? (
          <Text style={styles.detail}>cloud: {snapshot.model.cloud_state}</Text>
        ) : null}
      </View>

      <Text style={styles.section}>Proof Chip</Text>
      <View style={styles.card}>
        {proof ? (
          <View>
            <Text style={styles.label}>Latest turn</Text>
            <View style={styles.row}>
              <View
                style={[
                  styles.pill,
                  proof.state === "VERIFIED" ? styles.pillGreen : proof.state === "RECORDED" ? styles.pillBlue : styles.pillAmber,
                ]}
              >
                <Text style={styles.pillText}>{proof.state.toUpperCase()}</Text>
              </View>
            </View>
            <Text style={styles.detail}>
              {proof.compact.actions} actions · {proof.compact.sources} sources
            </Text>
            <Text style={styles.detail}>request {proof.request_id.slice(0, 18)}…</Text>
          </View>
        ) : (
          <Pressable style={styles.proofButton} onPress={() => void loadLatestProof()}>
            <Text style={styles.proofButtonText}>Load latest turn's chip</Text>
          </Pressable>
        )}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </View>

      <Text style={styles.section}>This device</Text>
      <View style={styles.card}>
        <Text style={styles.detail}>{client.deviceId}</Text>
        <Text style={styles.detail}>pinned desktop {client.desktopFingerprint.slice(0, 24)}…</Text>
        <Text style={styles.footnote}>
          One secret on this phone (the device grant, in the platform keystore). Revoking it on the
          desktop kills its authority immediately.
        </Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  center: { marginTop: 40 },
  section: { color: "#7d8794", fontSize: 12, textTransform: "uppercase", letterSpacing: 1.2, marginVertical: 10 },
  card: { backgroundColor: "#12161b", borderRadius: 10, padding: 14, marginBottom: 6 },
  label: { color: "#e8ecf1", fontSize: 15, fontWeight: "600", marginBottom: 8 },
  row: { flexDirection: "row", gap: 8, marginBottom: 6 },
  pill: { borderRadius: 6, paddingHorizontal: 8, paddingVertical: 4 },
  pillGreen: { backgroundColor: "#14532d" },
  pillBlue: { backgroundColor: "#1e3a5f" },
  pillAmber: { backgroundColor: "#713f12" },
  pillGrey: { backgroundColor: "#1b2027" },
  pillText: { color: "#d3d9e0", fontSize: 11, fontWeight: "700", letterSpacing: 0.5 },
  fingerprint: { color: "#8fa0b2", fontSize: 11, fontFamily: "menlo" },
  detail: { color: "#aeb9c4", fontSize: 12, marginTop: 4 },
  footnote: { color: "#5b6570", fontSize: 11, marginTop: 10, lineHeight: 16 },
  proofButton: { backgroundColor: "#1b2027", borderRadius: 8, padding: 12, alignItems: "center" },
  proofButtonText: { color: "#d3d9e0", fontWeight: "600" },
  error: { color: "#ff6b6b", fontSize: 13, marginTop: 8 },
});
