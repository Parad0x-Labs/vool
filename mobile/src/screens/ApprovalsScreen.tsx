import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View, Platform } from "react-native";

import Icon from "../ui/Icon.tsx";
import {errorText, tr} from "../i18n/index.ts";
import { CompanionError, isNoAnswer, type MobileCompanionClient } from "../protocol/client.ts";
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

  const shown = useRef(false);
  const refresh = useCallback(async () => {
    try {
      setApprovals(await client.pendingApprovals()); shown.current = true;
      setError("");
    } catch (exc) {
      // A short drop keeps the list on screen; the link sign at the top already says it.
      if (isNoAnswer(exc) && shown.current) return;
      if (!guard(exc)) setError(errorText(exc));
    }
  }, [client, guard]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  const [busy, setBusy] = useState("");
  const answer = useCallback(async (approvalId: string, decision: "allow" | "deny") => {
    setBusy(approvalId);
    try {
      await client.resolveApproval(approvalId, decision, "once");
      await refresh();
    } catch (exc) {
      if (!guard(exc)) setError(errorText(exc));
    } finally {
      setBusy("");
    }
  }, [client, guard, refresh]);

  if (error) return <View><Text style={styles.resource}>{error}</Text>
    <Pressable style={[styles.button, styles.approve, {flex: 0, marginTop: 12}]} onPress={() => void refresh()}><Text style={styles.approveText}>{tr("Refresh")}</Text></Pressable></View>;

  if (approvals === null) {
    return <ActivityIndicator style={styles.center} color="#868b90" />;
  }

  if (approvals.length === 0) {
    return (
      <View style={styles.empty}>
        <Icon name="shield-check-outline" size={40} color="#5d6166" />
        <Text style={styles.emptyTitle}>{tr("Nothing waiting for your approval")}</Text>
        <Text style={styles.emptyHint}>
          {tr("Actions your phone asked for can be allowed here. Payments are approved on your computer.")}</Text>
      </View>
    );
  }

  return (
    <ScrollView>
      {approvals.map((a) => (
        <View key={a.approval_id} style={styles.card}>
          <Text style={styles.intent}>{a.intent || tr("VOOL needs an approval")}</Text>
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
          {a.phone_may_answer ? (
            <View style={styles.buttons}>
              <Pressable style={[styles.button, styles.refuse]} disabled={busy === a.approval_id}
                onPress={() => void answer(a.approval_id, "deny")}>
                <Text style={styles.refuseText}>{tr("Deny")}</Text>
              </Pressable>
              <Pressable style={[styles.button, styles.approve]} disabled={busy === a.approval_id}
                onPress={() => void answer(a.approval_id, "allow")}>
                <Text style={styles.approveText}>{tr("Allow once")}</Text>
              </Pressable>
            </View>
          ) : (
            <Text style={styles.resource}>{tr("Approve this on your computer. Your phone answers only what it asked for itself.")}</Text>
          )}
        </View>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  center: { marginTop: 40 },
  empty: { alignItems: "center", marginTop: 60, padding: 24 },
  emptyTitle: { color: "#edece7", fontSize: 18, fontWeight: "600", marginBottom: 8, marginTop: 14, textAlign: "center" },
  emptyHint: { color: "#868b90", fontSize: 13, textAlign: "center", lineHeight: 19 },
  card: { backgroundColor: "#1f2224", borderRadius: 16, padding: 14, marginBottom: 12 },
  intent: { color: "#edece7", fontSize: 16, fontWeight: "700" },
  action: { color: "#868b90", fontSize: 12, marginTop: 2 },
  resource: { color: "#bfc2c4", fontSize: 12, marginTop: 4, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" },
  planned: { color: "#868b90", fontSize: 12, marginTop: 4 },
  reversible: { color: "#6fbf8f", fontSize: 11, marginTop: 6 },
  irreversible: { color: "#d8a24a", fontSize: 11, marginTop: 6, fontWeight: "600" },
  diff: { color: "#868b90", fontSize: 11, marginTop: 8, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace", backgroundColor: "#111314", padding: 8, borderRadius: 6 },
  buttons: { flexDirection: "row", gap: 10, marginTop: 12 },
  button: { flex: 1, borderRadius: 24, padding: 13, alignItems: "center" },
  refuse: { backgroundColor: "#26292c" },
  approve: { backgroundColor: "#edece7" },
  refuseText: { color: "#d86a56", fontWeight: "700" },
  approveText: { color: "#111314", fontWeight: "700" },
});
