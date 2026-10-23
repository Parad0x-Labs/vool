import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import {errorText, tr} from "../i18n/index.ts";
import { CompanionError, type MobileCompanionClient } from "../protocol/client.ts";
import type { PendingApproval } from "../protocol/models.ts";

interface Props {
  client: MobileCompanionClient;
  onRevoked: () => void | Promise<void>;
}

/** Needs-approval state: the typed action cards the desktop is holding for a
 *  human decision, with Approve / Refuse. */
export default function ApprovalsScreen({ client, onRevoked }: Props) {
  const [approvals, setApprovals] = useState<PendingApproval[] | null>(null);
  const [error, setError] = useState("");

  const guard = useCallback(
    (exc: unknown) => {
      if (exc instanceof CompanionError && (exc.code === "device_revoked" || exc.code === "signature_invalid")) {
        Alert.alert(tr("Unpaired"), tr("This phone is unpaired. Pair it again on your computer."), [
          { text: tr("OK"), onPress: () => void onRevoked() },
        ]);
        return true;
      }
      return false;
    },
    [onRevoked],
  );

  const refresh = useCallback(async () => {
    try {
      setApprovals(await client.pendingApprovals());
      setError("");
    } catch (exc) {
      if (!guard(exc)) setError(errorText(exc));
    }
  }, [client, guard]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (error) return <View><Text style={styles.resource}>{error}</Text>
    <Pressable style={styles.button} onPress={() => void refresh()}><Text style={styles.approveText}>{tr("Refresh")}</Text></Pressable></View>;

  if (approvals === null) {
    return <ActivityIndicator style={styles.center} color="#7d8794" />;
  }

  if (approvals.length === 0) {
    return (
      <View style={styles.empty}>
        <Text style={styles.emptyTitle}>{tr("Nothing waiting for your approval")}</Text>
        <Text style={styles.emptyHint}>
          {tr("Payments and actions must be approved on your computer in this version.")}</Text>
      </View>
    );
  }

  return (
    <ScrollView>
      {approvals.map((a) => (
        <View key={a.approval_id} style={styles.card}>
          <Text style={styles.intent}>{a.intent || "action"}</Text>
          {a.action ? <Text style={styles.action}>{a.action}</Text> : null}
          {(a.affected_resources || []).slice(0, 4).map((r) => (
            <Text key={r} style={styles.resource} numberOfLines={1}>
              {r}
            </Text>
          ))}
          {a.planned_action_count ? (
            <Text style={styles.planned}>{tr("Planned changes: {count}", {count: a.planned_action_count})}</Text>
          ) : null}
          <Text style={a.reversible === false ? styles.irreversible : styles.reversible}>
            {a.reversible === false ? tr("Irreversible") : tr("Reversible")}
          </Text>
          {a.diff_preview ? <Text style={styles.diff} numberOfLines={8}>{a.diff_preview}</Text> : null}
          <Text style={styles.resource}>{tr("Payments and actions must be approved on your computer in this version.")}</Text>
        </View>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  center: { marginTop: 40 },
  empty: { alignItems: "center", marginTop: 60, padding: 24 },
  emptyTitle: { color: "#e8ecf1", fontSize: 18, fontWeight: "600", marginBottom: 8 },
  emptyHint: { color: "#5b6570", fontSize: 13, textAlign: "center", lineHeight: 19 },
  card: { backgroundColor: "#12161b", borderRadius: 10, padding: 14, marginBottom: 12 },
  intent: { color: "#e8ecf1", fontSize: 16, fontWeight: "700" },
  action: { color: "#8fa0b2", fontSize: 12, marginTop: 2 },
  resource: { color: "#aeb9c4", fontSize: 12, marginTop: 4, fontFamily: "menlo" },
  planned: { color: "#8fa0b2", fontSize: 12, marginTop: 4 },
  reversible: { color: "#4ade80", fontSize: 11, marginTop: 6 },
  irreversible: { color: "#f59e0b", fontSize: 11, marginTop: 6, fontWeight: "600" },
  diff: { color: "#7d8794", fontSize: 11, marginTop: 8, fontFamily: "menlo", backgroundColor: "#0b0d10", padding: 8, borderRadius: 6 },
  buttons: { flexDirection: "row", gap: 10, marginTop: 12 },
  button: { flex: 1, borderRadius: 8, padding: 12, alignItems: "center" },
  refuse: { backgroundColor: "#2a1416", borderWidth: 1, borderColor: "#7f1d1d" },
  approve: { backgroundColor: "#14532d" },
  refuseText: { color: "#fca5a5", fontWeight: "700" },
  approveText: { color: "#bbf7d0", fontWeight: "700" },
});
