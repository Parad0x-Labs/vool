import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

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
  const [busyId, setBusyId] = useState<string | null>(null);

  const guard = useCallback(
    (exc: unknown) => {
      if (exc instanceof CompanionError && (exc.code === "device_revoked" || exc.code === "signature_invalid")) {
        Alert.alert("Device unpaired", "This device was revoked on the desktop.", [
          { text: "OK", onPress: () => void onRevoked() },
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
    } catch (exc) {
      if (!guard(exc)) setApprovals([]);
    }
  }, [client, guard]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  const decide = useCallback(
    async (approvalId: string, decision: "allow" | "deny") => {
      setBusyId(approvalId);
      try {
        await client.resolveApproval(approvalId, decision, "once");
        await refresh();
      } catch (exc) {
        if (!guard(exc)) Alert.alert("Decision failed", String((exc as Error).message || exc));
      } finally {
        setBusyId(null);
      }
    },
    [client, guard, refresh],
  );

  if (approvals === null) {
    return <ActivityIndicator style={styles.center} color="#7d8794" />;
  }

  if (approvals.length === 0) {
    return (
      <View style={styles.empty}>
        <Text style={styles.emptyTitle}>Nothing needs you</Text>
        <Text style={styles.emptyHint}>
          When the desktop holds an action for approval — a file write, a paid call — it appears here.
        </Text>
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
            <Text style={styles.planned}>{a.planned_action_count} planned change(s)</Text>
          ) : null}
          <Text style={a.reversible === false ? styles.irreversible : styles.reversible}>
            {a.reversible === false ? "Irreversible" : "Reversible"}
          </Text>
          {a.diff_preview ? <Text style={styles.diff} numberOfLines={8}>{a.diff_preview}</Text> : null}
          <View style={styles.buttons}>
            <Pressable
              style={[styles.button, styles.refuse]}
              disabled={busyId === a.approval_id}
              onPress={() => void decide(a.approval_id, "deny")}
            >
              <Text style={styles.refuseText}>Refuse</Text>
            </Pressable>
            <Pressable
              style={[styles.button, styles.approve]}
              disabled={busyId === a.approval_id}
              onPress={() => void decide(a.approval_id, "allow")}
            >
              <Text style={styles.approveText}>Approve</Text>
            </Pressable>
          </View>
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
