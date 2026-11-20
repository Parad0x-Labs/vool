import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View, Platform } from "react-native";

import {errorText, permissionText, tr} from "../i18n/index.ts";
import { CompanionError, type MobileCompanionClient } from "../protocol/client.ts";
import type { ChatMessage, StatusSnapshot, TurnProof, DeviceInfo } from "../protocol/models.ts";
import {connectionAddress} from "../protocol/connection.ts";
import {useLink} from "../ui/LinkBadge.tsx";
import Icon from "../ui/Icon.tsx";
import {RevealScrollView} from "../ui/Reveal.tsx";
import SettingsCard, {cardRow} from "../ui/SettingsCard.tsx";
import {C} from "../ui/theme.ts";
import { CameraView, useCameraPermissions } from "expo-camera";
import { parsePairingUri } from "../protocol/pairing.ts";

interface Props {
  client: MobileCompanionClient;
  onRevoked: () => void | Promise<void>;
  idleMinutes:number;
  onIdleMinutes:(value:number)=>Promise<void>;
  onConnection?: (address: string) => Promise<void>;
  onAwayConnection?: (address: string) => Promise<void>;
  /** The private camera's settings, shown under the lock time. */
  captureSettings?: React.ReactNode;
}

/** Current model, local/cloud status, Proof Chip, and the device's own standing. */
export default function StatusScreen({ client, onRevoked, onConnection, onAwayConnection, idleMinutes, onIdleMinutes, captureSettings }: Props) {
  const [snapshot, setSnapshot] = useState<StatusSnapshot | null>(null);
  const [device,setDevice]=useState<DeviceInfo|null>(null);
  const [proof, setProof] = useState<TurnProof | null>(null);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState(client.baseUrl);
  const [away, setAway] = useState(client.awayUrl);
  const [route, setRoute] = useState(client.route);
  const link = useLink();
  useEffect(() => {setRoute(client.route);}, [client, link, snapshot]);
  const [scanning, setScanning] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  // Which way reaches the computer is asked when this page opens and after every address change.
  const [looking, setLooking] = useState<"" | "looking" | "none">("");
  const probe = useCallback(() => {
    if (!client.awayUrl) {setLooking(""); return;}
    setLooking("looking");
    void client.findRoute().then(found => {setRoute(client.route); setLooking(found ? "" : "none");}).catch(() => setLooking("none"));
  }, [client]);
  useEffect(() => {probe();}, [probe]);
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

  const stateLine = looking === "looking" ? tr("Looking for your computer, at home and away. Away from home this can take a minute.")
    : link === "connected" ? (client.awayUrl ? (route === "away" ? tr("Connected from away") : tr("Connected over your home network")) : tr("Connected to your computer"))
    : link === "connecting" ? tr("Connecting to your computer…")
    : client.awayUrl ? tr("Your computer did not answer at home or away. Check that it is on and VOOL is open.")
    : tr("Can't reach your computer. Check that it is on and VOOL is open.");
  const stateColor = link === "connected" ? C.ok : link === "offline" && looking !== "looking" ? C.bad : C.wait;

  // One block for the connection: how it stands, and one button. A scanned code saves itself.
  const computerCard = <SettingsCard icon="monitor" title={tr("Your computer")}><View style={cardRow}>
    <View style={styles.stateRow}>
      <View style={[styles.dot, {backgroundColor: stateColor}]} />
      <Text style={[styles.stateText, {color: stateColor}]}>{stateLine}</Text>
    </View>
    {snapshot ? <Text style={styles.detail}>{snapshot.desktop.display_name} · {snapshot.model.current || tr("No model")} · {snapshot.model.cloud_state === "ok" ? tr("Cloud connected") : tr("Local")}</Text> : null}
    {saved ? <Text style={[styles.detail, {color: C.ok}]}>{tr("Saved. Your phone is ready.")}</Text> : null}
    {/* The computer moved to a new address: its pairing QR carries the new one. Only a QR from the
        SAME computer (its pinned fingerprint) may change where this phone connects. */}
    <Pressable style={styles.proofButton} android_ripple={{color: C.ripple}} accessibilityRole="button" onPress={() => {void (async () => {
      if (!cameraPermission?.granted && !(await requestCameraPermission()).granted) {
        setError(tr("Camera permission is needed to scan your computer’s pairing QR."));
        return;
      }
      setSaved(false); setScanning(true);
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
          try {const next = connectionAddress(address); await onConnection?.(next); setConnection(next); setError("");
            if (parsed.awayAddress) {await onAwayConnection?.(parsed.awayAddress); setAway(parsed.awayAddress);}
            setSaved(true); probe();}
          catch {setError(tr("Could not complete this. Check your computer."));}
        })();
      }} />
      <Pressable style={styles.scanCancel} onPress={() => setScanning(false)}><Text style={styles.proofButtonText}>{tr("Cancel")}</Text></Pressable>
    </View>}
    {error ? <Text style={styles.error}>{error}</Text> : null}
  </View></SettingsCard>;

  // The addresses are filled in by the scan. They stay out of the way for the rare manual change.
  const advanced = <SettingsCard icon="tune-variant" title={tr("Advanced")} open={showAdvanced} onToggle={() => setShowAdvanced(value => !value)}>
    {showAdvanced ? <View style={cardRow}>
      <Text style={styles.fieldLabel}>{tr("Computer’s address on your home network")}</Text>
      <TextInput style={[styles.address,{writingDirection:"ltr",textAlign:"left"}]} accessibilityLabel={tr("Computer’s address on your home network")} autoCapitalize="none" autoCorrect={false} value={connection} onChangeText={setConnection}/>
      <Text style={[styles.fieldLabel, {marginTop: 14}]}>{tr("Address away from home")}</Text>
      <Text style={styles.footnote}>{tr("Optional. VOOL uses it by itself when your home network is out of reach.")}</Text>
      <TextInput style={[styles.address,{writingDirection:"ltr",textAlign:"left", marginTop: 6}]} accessibilityLabel={tr("Address away from home")} autoCapitalize="none" autoCorrect={false} value={away} onChangeText={setAway}/>
      <Pressable style={styles.proofButton} android_ripple={{color: C.ripple}} accessibilityRole="button" onPress={() => {void (async () => {
        try {
          const home = connectionAddress(connection); const next = away.trim() ? connectionAddress(away) : "";
          await onConnection?.(home); setConnection(home);
          await onAwayConnection?.(next); setAway(next);
          setError(""); setSaved(true); probe();
        } catch {setSaved(false); setError(tr("Could not complete this. Check your computer."));}
      })();}}><Text style={styles.proofButtonText}>{tr("Save")}</Text></Pressable>
      <Text style={[styles.fingerprint, {marginTop: 14}]}>{tr("Computer check code")}: {snapshot?.desktop.short_fingerprint || client.desktopFingerprint.slice(0, 24)}</Text>
      {snapshot ? <View style={{marginTop: 12}}>
        {proof ? (
          <View>
            <Text style={styles.label}>{tr("Latest reply")}</Text>
            <View style={styles.row}>
              <View
                style={[
                  styles.pill,
                  proof.state === "VERIFIED" ? styles.pillGreen : proof.state === "RECORDED" ? styles.pillBlue : styles.pillAmber,
                ]}
              >
                <Text style={styles.pillText}>{proof.state === "VERIFIED" ? tr("Checked") : proof.state === "RECORDED" ? tr("Saved to history") : tr("not recorded")}</Text>
              </View>
            </View>
            <Text style={styles.detail}>
              {proof.compact.actions} {tr("Actions")} · {proof.compact.sources} {tr("Sources")}</Text>
                      </View>
        ) : (
          <Pressable style={styles.proofButton} android_ripple={{color: C.ripple}} onPress={() => void loadLatestProof()}>
            <Text style={styles.proofButtonText}>{tr("Refresh")}</Text>
          </Pressable>
        )}
      </View> : null}
    </View> : null}
  </SettingsCard>;

  const danger = <SettingsCard icon="lifebuoy" title={tr("If something goes wrong")}><View style={[cardRow, {paddingTop: 0}]}>
        <Pressable
          style={styles.unpairButton}
          onPress={() => {
            Alert.alert(
              tr("Unpair this phone"),
              tr("Disconnect this phone. If your computer cannot be reached, unpair it there too."),
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
              ? tr("Payments were frozen and your phones were unpaired. Unfreeze on your recovered computer.")
              : tr("VOOL couldn’t confirm that payments were frozen and your phones unpaired. Get to your computer and freeze payments there."));
          })();}}])}>
          <Text style={styles.unpairButtonText}>{tr("My computer is lost")}</Text>
        </Pressable>
  </View></SettingsCard>;

  if (!snapshot) return <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.page}>
    {computerCard}
    {!error && link !== "offline" ? <ActivityIndicator style={styles.center} color={C.mute} /> : null}
    {advanced}
    {danger}
  </ScrollView>;

  return (
    <RevealScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.page}>
      {computerCard}

      <SettingsCard icon="shield-check-outline" title={tr("Permissions")}>
        {Object.entries(device?.rules || {}).map(([right,value])=>{
          const on = !!value && (!(device?.right_expiry?.[right]) || (device!.right_expiry![right] > Date.now()/1000));
          return <View key={right} style={[styles.listRow, styles.listRule]}>
            <View style={styles.fill}>
              <Text style={styles.listText}>{permissionText(right)}</Text>
              {device?.right_expiry?.[right] ? <Text style={styles.listNote}>{tr("Expires")} {new Date(device.right_expiry[right]*1000).toLocaleString()}</Text> : null}
            </View>
            <Text style={[styles.listValue, on && {color: C.ok}]}>{on ? tr("On") : tr("Off")}</Text>
          </View>;
        })}
        <View style={cardRow}><Text style={styles.cardNote}>{tr("Change this phone’s permissions on your computer.")}</Text></View>
      </SettingsCard>

      <SettingsCard icon="lock-clock" title={tr("Screen lock")}>
        <View style={cardRow}>
          <Text style={styles.cardNote}>{tr("Lock after {count} min", {count:idleMinutes})}</Text>
          <View style={[styles.segment, {marginTop: 10}]}>{[1,5,15].map(value=><Pressable key={value} accessibilityLabel={tr("Lock after {count} min",{count:value})}
            style={[styles.segmentItem, idleMinutes===value && styles.segmentOn]} onPress={()=>{void onIdleMinutes(value).catch(error=>setError(errorText(error)));}}>
            <Text style={[styles.proofButtonText, idleMinutes===value && styles.segmentOnText]}>{value}</Text>
          </Pressable>)}</View>
        </View>
      </SettingsCard>

      {captureSettings}
      {advanced}
      {danger}
    </RevealScrollView>
  );
}

const styles = StyleSheet.create({
  page: { paddingBottom: 28 },
  cardNote: { color: C.mute, fontSize: 13, lineHeight: 18 },
  dot: { width: 9, height: 9, borderRadius: 5 },
  center: { marginTop: 40 },
  fill: { flex: 1 },
  list: { backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 20, overflow: "hidden" },
  listRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, minHeight: 52, paddingVertical: 12 },
  listRule: { borderTopWidth: 1, borderTopColor: C.line },
  listText: { color: C.ink, fontSize: 15 },
  listNote: { color: C.mute, fontSize: 12, marginTop: 2 },
  listValue: { color: C.mute, fontSize: 14, fontWeight: "600" },
  label: { color: C.ink, fontSize: 15, fontWeight: "600", marginBottom: 8 },
  row: { flexDirection: "row", gap: 8, marginBottom: 6 },
  pill: { borderRadius: 12, paddingHorizontal: 10, paddingVertical: 5 },
  pillGreen: { backgroundColor: C.okBg },
  pillBlue: { backgroundColor: C.pill },
  pillAmber: { backgroundColor: C.tint },
  pillGrey: { backgroundColor: C.pill },
  pillText: { color: C.ink, fontSize: 12, fontWeight: "600" },
  fingerprint: { color: C.mute, fontSize: 11, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" },
  detail: { color: C.soft, fontSize: 13, lineHeight: 19, marginTop: 4 },
  footnote: { color: C.mute, fontSize: 12, marginTop: 10, lineHeight: 16 },
  proofButton: { backgroundColor: C.pill, borderWidth: 1, borderColor: C.line, borderRadius: 24, minHeight: 48, padding: 12, alignItems: "center", justifyContent: "center", marginTop: 12, overflow: "hidden" },
  segment: { flexDirection: "row", backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 24, padding: 4, gap: 4 },
  segmentItem: { flex: 1, borderRadius: 20, minHeight: 48, justifyContent: "center", alignItems: "center" },
  segmentOn: { backgroundColor: C.send },
  segmentOnText: { color: C.sendInk },
  proofButtonText: { color: C.ink, fontWeight: "600" },
  scanBox: { height: 320, marginTop: 12, borderRadius: 16, overflow: "hidden", backgroundColor: "#000" },
  scanCancel: { position: "absolute", bottom: 12, alignSelf: "center", padding: 10 },
  unpairButton: { backgroundColor: C.badBg, borderWidth: 1, borderColor: "rgba(226,112,92,0.25)", borderRadius: 24, minHeight: 48, padding: 13, alignItems: "center", justifyContent: "center", marginTop: 12 },
  unpairButtonText: { color: C.bad, fontWeight: "600" },
  error: { color: C.bad, fontSize: 13, marginTop: 8 },
  stateRow: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 4 },
  stateText: { flex: 1, fontSize: 15, fontWeight: "600", lineHeight: 21 },
  fieldLabel: { color: C.soft, fontSize: 13, marginBottom: 6 },
  address: { color: C.ink, fontSize: 14, backgroundColor: C.bg, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 },
});
