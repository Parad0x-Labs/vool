import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import {errorText, permissionText, tr} from "../i18n/index.ts";
import { CompanionError, type MobileCompanionClient } from "../protocol/client.ts";
import type { ChatMessage, StatusSnapshot, TurnProof, DeviceInfo } from "../protocol/models.ts";
import {connectionAddress} from "../protocol/connection.ts";
import { CameraView, useCameraPermissions } from "expo-camera";
import { parsePairingUri } from "../protocol/pairing.ts";

interface Props {
  client: MobileCompanionClient;
  onRevoked: () => void | Promise<void>;
  idleMinutes:number;
  onIdleMinutes:(value:number)=>Promise<void>;
  onConnection?: (address: string) => Promise<void>;
}

/** Current model, local/cloud status, Proof Chip, and the device's own standing. */
export default function StatusScreen({ client, onRevoked, onConnection, idleMinutes, onIdleMinutes }: Props) {
  const [snapshot, setSnapshot] = useState<StatusSnapshot | null>(null);
  const [device,setDevice]=useState<DeviceInfo|null>(null);
  const [proof, setProof] = useState<TurnProof | null>(null);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState(client.baseUrl);
  const [scanning, setScanning] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();

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
        const [status,record]=await Promise.all([client.statusSnapshot(),client.devicesMe()]);setSnapshot(status);setDevice(record);
      } catch (exc) {
        if (!guard(exc)) setError(errorText(exc));
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
        setError(tr("Nothing here yet."));
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
      setError(tr("not recorded"));
    } catch (exc) {
      if (!guard(exc)) setError(errorText(exc));
    }
  }, [client, guard]);

  const connectionForm = <View style={styles.card}>
    <Text style={styles.label}>{tr("Phone connection address")}</Text>
    <TextInput style={[styles.detail,{writingDirection:"ltr",textAlign:"left"}]} accessibilityLabel={tr("Phone connection address")} autoCapitalize="none" autoCorrect={false} value={connection} onChangeText={setConnection}/>
    <Pressable style={styles.proofButton} onPress={() => {void (async () => {
      try {await onConnection?.(connectionAddress(connection));}
      catch {setError(tr("Could not complete this. Check your computer."));}
    })();}}><Text style={styles.proofButtonText}>{tr("Save")}</Text></Pressable>
    {/* The computer moved to a new address: its pairing QR carries the new one. Only a QR from the
        SAME computer (its pinned fingerprint) may change where this phone connects. */}
    <Pressable style={[styles.proofButton, {marginTop: 8}]} onPress={() => {void (async () => {
      if (!cameraPermission?.granted && !(await requestCameraPermission()).granted) {
        setError(tr("Camera permission is needed to scan the desktop's pairing QR."));
        return;
      }
      setScanning(true);
    })();}}><Text style={styles.proofButtonText}>{tr("Scan pairing QR")}</Text></Pressable>
    {scanning && <View style={styles.scanBox}>
      <CameraView style={StyleSheet.absoluteFill} barcodeScannerSettings={{ barcodeTypes: ["qr"] }} onBarcodeScanned={({data}) => {
        const parsed = parsePairingUri(data);
        if (!parsed) return;
        setScanning(false);
        if (parsed.desktopFingerprint !== client.desktopFingerprint.toLowerCase()) {
          setError(tr("The computer’s identity could not be verified. Check it before pairing again."));
          return;
        }
        const address = `http://${parsed.host}${parsed.port ? ":" + parsed.port : ""}`;
        void (async () => {
          try {const next = connectionAddress(address); await onConnection?.(next); setConnection(next); setError("");}
          catch {setError(tr("Could not complete this. Check your computer."));}
        })();
      }} />
      <Pressable style={styles.scanCancel} onPress={() => setScanning(false)}><Text style={styles.proofButtonText}>{tr("Cancel")}</Text></Pressable>
    </View>}
  </View>;
  if (error && !snapshot) return <View><Text style={styles.error}>{error}</Text>{connectionForm}</View>;
  if (!snapshot) {
    return <View><ActivityIndicator style={styles.center} color="#7d8794" />{connectionForm}</View>;
  }

  const cloudOn = snapshot.model.cloud_state === "ok";

  return (
    <ScrollView>
      {connectionForm}
      <Text style={styles.section}>{tr("Desktop")}</Text>
      <View style={styles.card}>
        <Text style={styles.label}>
          {snapshot.desktop.display_name} · {snapshot.model.current || tr("No model")}
        </Text>
        <View style={styles.row}>
          <View style={[styles.pill, cloudOn ? styles.pillGreen : styles.pillGrey]}>
            <Text style={styles.pillText}>{cloudOn ? tr("Cloud connected") : tr("Local")}</Text>
          </View>
          <View style={[styles.pill, snapshot.relay.enabled ? styles.pillAmber : styles.pillGrey]}>
            <Text style={styles.pillText}>{snapshot.relay.mode}</Text>
          </View>
        </View>
        <Text style={styles.fingerprint}>{tr("Computer check code")}{snapshot.desktop.short_fingerprint}</Text>
        {snapshot.model.cloud_state && snapshot.model.cloud_state !== "ok" ? (
          <Text style={styles.detail}>{tr("Cloud untested")}</Text>
        ) : null}
      </View>

      <Text style={styles.section}>{tr("Activity")}</Text>
      <View style={styles.card}>
        {proof ? (
          <View>
            <Text style={styles.label}>{tr("Latest turn")}</Text>
            <View style={styles.row}>
              <View
                style={[
                  styles.pill,
                  proof.state === "VERIFIED" ? styles.pillGreen : proof.state === "RECORDED" ? styles.pillBlue : styles.pillAmber,
                ]}
              >
                <Text style={styles.pillText}>{proof.state === "VERIFIED" ? tr("Activity verified") : proof.state === "RECORDED" ? tr("Activity recorded") : tr("not recorded")}</Text>
              </View>
            </View>
            <Text style={styles.detail}>
              {proof.compact.actions} {tr("Actions")} · {proof.compact.sources} {tr("Sources")}</Text>
            <Text style={styles.detail}>{tr("request")}{proof.request_id.slice(0, 18)}…</Text>
          </View>
        ) : (
          <Pressable style={styles.proofButton} onPress={() => void loadLatestProof()}>
            <Text style={styles.proofButtonText}>{tr("Refresh")}</Text>
          </Pressable>
        )}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </View>

      <Text style={styles.detail}>{tr("Change this phone’s permissions on your computer.")}</Text>
      {Object.entries(device?.rules || {}).map(([right,value])=><Text key={right} style={styles.detail}>
        {permissionText(right)}: {value && (!(device?.right_expiry?.[right]) || (device.right_expiry[right] > Date.now()/1000)) ? tr("On") : tr("Off")}
        {device?.right_expiry?.[right] ? " · "+tr("Expires")+" "+new Date(device.right_expiry[right]*1000).toLocaleString() : ""}
      </Text>)}
      <Text style={styles.section}>{tr("Lock after {count} min", {count:idleMinutes})}</Text>
      {[1,5,15].map(value=><Pressable key={value} style={styles.proofButton} onPress={()=>{void onIdleMinutes(value).catch(error=>setError(errorText(error)));}}>
        <Text style={styles.proofButtonText}>{tr("Lock after {count} min",{count:value})}</Text>
      </Pressable>)}
      <Text style={styles.section}>{tr("This device")}</Text>
      <View style={styles.card}>
        <Text style={styles.detail}>{client.deviceId}</Text>
        <Text style={styles.detail}>{tr("Computer check code")}{client.desktopFingerprint.slice(0, 24)}…</Text>
        <Pressable
          style={styles.unpairButton}
          onPress={() => {
            Alert.alert(
              tr("Unpair this phone"),
              tr("Disconnect this phone. If your computer cannot be reached, revoke it there too."),
              [
                { text: tr("Cancel"), style: "cancel" },
                {
                  text: tr("Unpair this phone"),
                  style: "destructive",
                  onPress: () => {
                    void (async () => {
                      try {
                        await client.unpair();
                      } catch {
                        // Refused, already revoked or unreachable: either way this
                        // phone must drop its credentials — the desktop is the truth.
                      }
                      await onRevoked();
                    })();
                  },
                },
              ],
            );
          }}
        >
          <Text style={styles.unpairButtonText}>{tr("Unpair this phone")}</Text>
        </Pressable>
        <Pressable style={styles.unpairButton} onPress={() => Alert.alert(tr("My computer is lost"),
          tr("Freeze payments and switch this phone off if your computer is reachable. Then forget this phone’s connection. Your other phones are switched off in Settings on the computer. Completed payments cannot be undone."), [
          {text: tr("Cancel"), style: "cancel"}, {text: tr("Freeze and disconnect"), style: "destructive", onPress: () => {void (async () => {
            let reached = false;
            let timer: ReturnType<typeof setTimeout> | undefined;
            try {
              await Promise.race([client.lostComputer(), new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error("unreachable")), 8000);
              })]); reached = true;
            } catch {} finally {if (timer) clearTimeout(timer);}
            try {await onRevoked();}
            catch {Alert.alert(tr("Unpair this phone"), tr("Local removal failed. Clear VOOL’s app data in your phone’s settings."));return;}
            Alert.alert(reached ? tr("Unpaired") : tr("Computer not reached"), reached
              ? tr("Payments were frozen and phones revoked. Unfreeze on your recovered computer.")
              : tr("This phone forgot its connection. Remote freezing and revocation are unconfirmed. Secure or recover your computer, then freeze payments there."));
          })();}}])}>
          <Text style={styles.unpairButtonText}>{tr("My computer is lost")}</Text>
        </Pressable>
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
  scanBox: { height: 320, marginTop: 12, borderRadius: 8, overflow: "hidden", backgroundColor: "#000" },
  scanCancel: { position: "absolute", bottom: 12, alignSelf: "center", padding: 10 },
  unpairButton: { backgroundColor: "#2a1416", borderRadius: 8, padding: 12, alignItems: "center", marginTop: 12, borderWidth: 1, borderColor: "#5b2027" },
  unpairButtonText: { color: "#ff8a8a", fontWeight: "600" },
  error: { color: "#ff6b6b", fontSize: 13, marginTop: 8 },
});
