import {errorText, rightToLeft, tr} from "../i18n/index.ts";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View, Platform } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { Linking } from "react-native";
import Icon from "../ui/Icon.tsx";
import Frame from "../ui/Frame.tsx";

import {
  reachForPairing,
  type DeviceCredentials,
} from "../protocol/client.ts";
import { parsePairingUri, pairingUriIsFresh } from "../protocol/pairing.ts";
import { generateDeviceKeypair, keypairFromSeed } from "../protocol/ed25519.ts";
import { claimSealedPairing } from "../protocol/sealed.ts";
import { credentialStoreForEnvironment } from "../store/credentials.ts";
import {prepareApprovalKey, deleteApprovalKey} from "../security/approvalKey.ts";
import {C} from "../ui/theme.ts";
import Face from "../ui/Face.tsx";

interface Props {
  onPaired: (creds: DeviceCredentials) => void | Promise<void>;
  /** Deep link payload (vool-pair://…) arriving from the OS handler. */
  incomingLink?: string | null;
  onLinkConsumed?: () => void;
}

/** Pair with the desktop: scan its QR (camera), open a vool-pair:// deep link,
 *  or type the address + pairing id + code by hand. Whichever door, the claim
 *  carries an Ed25519 proof of possession over the server-held challenge. */
export default function PairScreen({ onPaired, incomingLink, onLinkConsumed }: Props) {
  const [scanning, setScanning] = useState(false);
  const [desktopUrl, setDesktopUrl] = useState("");
  const [pairingId, setPairingId] = useState("");
  const [code, setCode] = useState("");
  const [deviceName, setDeviceName] = useState("");
  const [fingerprint, setFingerprint] = useState("");
  // The computer's away-from-home address, when its pairing code carries one.
  const [awayAddress, setAwayAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>("");
  // A scanned QR pairs at once; there is nothing left to type, so waiting for a button read as "nothing happened".
  const [pairAfterScan, setPairAfterScan] = useState(false);
  // After a scan there is nothing to type: the screen only says it is connecting. The typed
  // details stay folded away for the rare time a code cannot be scanned.
  const [scanned, setScanned] = useState(false);
  const [manual, setManual] = useState(false);
  const [slow, setSlow] = useState(false);
  // Pairing went the long way (the private route), so the wait can be long.
  const [goingAway, setGoingAway] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const store = useMemo(() => credentialStoreForEnvironment(false), []);
  const handledLinkRef = useRef<string | null>(null);

  // Deep link arrival: parse and prefill the manual fields from the SAME
  // signed payload the camera path reads.
  useEffect(() => {
    if (!incomingLink || handledLinkRef.current === incomingLink) return;
    handledLinkRef.current = incomingLink;
    const parsed = parsePairingUri(incomingLink);
    if (parsed) {
      setDesktopUrl(`http://${parsed.host}${parsed.port ? ":" + parsed.port : ""}`);
      setPairingId(parsed.pairingId);
      setCode(parsed.code);
      setFingerprint(parsed.desktopFingerprint); setAwayAddress(parsed.awayAddress);
      setManual(true);
      if (!pairingUriIsFresh(parsed)) {
        setError(tr("This pairing QR has expired — start a new pairing on the computer."));
      }
    } else {
      setError(tr("This pairing code no longer works. Start a new pairing on the computer."));
    }
    onLinkConsumed?.();
  }, [incomingLink, onLinkConsumed]);

  const onQrScanned = useCallback(({ data }: { data: string }) => {
    const parsed = parsePairingUri(data);
    if (!parsed) return; // keep scanning non-pairing codes
    setScanning(false);
    setDesktopUrl(`http://${parsed.host}${parsed.port ? ":" + parsed.port : ""}`);
    setPairingId(parsed.pairingId);
    setCode(parsed.code);
    setFingerprint(parsed.desktopFingerprint); setAwayAddress(parsed.awayAddress);
    if (!pairingUriIsFresh(parsed)) {
      setError(tr("This pairing QR has expired — start a new pairing on the computer."));
      return;
    }
    setScanned(true);
    setPairAfterScan(true);
  }, []);

  const pair = async () => {
    setError("");
    const base = desktopUrl.trim().replace(/\/$/, "").replace(/^vool-pair:\/\//, "http://");
    if (!base || !pairingId.trim() || !code.trim()) {
      setError(tr("Enter the computer address, pairing id and code."));
      return;
    }
    setBusy(true);
    setGoingAway(false);
    let approvalAlias: string | null = null;
    try {
      // 1. Fetch the server-held challenge (also pins the desktop identity
      //    for the manual-entry path, where no QR fingerprint was scanned).
      //    Away from home the pairing code's away address is used, so pairing works anywhere.
      // On iPhone and iPad the first try at the home network fails while the system asks whether VOOL
      // may find devices on it, so home is tried again for a while before the long way.
      const reached = await reachForPairing(base, awayAddress, pairingId.trim(), undefined,
        {homePatienceMs: Platform.OS === "ios" ? 30_000 : 0, onAway: () => setGoingAway(true)});
      const challenge = reached.challenge;
      // 2. The phone's identity: generated once, seed stays in the keystore.
      const keypair = generateDeviceKeypair();
      const approvalKey = await prepareApprovalKey(keypair.publicKeyHex, pairingId.trim(), challenge.challenge);
      if (approvalKey) approvalAlias = keypair.publicKeyHex;
      // 3. Claim with proof of possession; the desktop verifies the
      //    signature over challenge/pid/code/public-key/expiry BEFORE any
      //    grant is issued.
      const credentials = await claimSealedPairing(reached.base, {
        pairingId: pairingId.trim(),
        code: code.trim(),
        deviceName: (deviceName.trim() || "VOOL phone").slice(0, 64),
        platform: "react-native",
        deviceSeedHex: keypair.seedHex,
        devicePublicKeyHex: keypair.publicKeyHex,
        challenge,
        pinnedDesktopFingerprint: fingerprint.trim(),
        approvalKey,
      });
      // The saved home address is always the home one, whichever way the pairing went.
      await onPaired({...credentials, baseUrl: base, ...(awayAddress ? {awayUrl: awayAddress} : {}), ...(approvalAlias ? {approvalKeyAlias: approvalAlias} : {})});
      approvalAlias = null;
    } catch (exc) {
      if (approvalAlias) await deleteApprovalKey(approvalAlias).catch(() => {});
      setScanned(false);
      // No answer at all while pairing: the usual cause is another Wi-Fi, so say that.
      const said = errorText(exc);
      setError(said === tr("Can't reach your computer. Check that it is on and VOOL is open.")
        ? tr("Your computer did not answer. Check that this device is on the same Wi-Fi as your computer, then scan again.") : said);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!pairAfterScan || busy || !desktopUrl || !pairingId || !code) return;
    setPairAfterScan(false);
    void pair();
    // pair() reads the fields this scan just set; it runs once per scan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairAfterScan, busy, desktopUrl, pairingId, code, fingerprint]);

  const startScanning = async () => {
    if (!cameraPermission?.granted) {
      const result = await requestCameraPermission();
      if (!result.granted) {
        setError(tr("Camera permission is needed to scan your computer’s pairing QR."));
        return;
      }
    }
    setError("");
    setScanning(true);
  };

  // Every hook runs on every render: the scanner below returns early.
  const connecting = scanned && (busy || pairAfterScan);
  useEffect(() => {
    if (!connecting) {setSlow(false); return;}
    const timer = setTimeout(() => setSlow(true), 5000);
    return () => clearTimeout(timer);
  }, [connecting]);
  if (scanning) {
    return (
      <View style={styles.scanRoot}>
        <CameraView
          style={StyleSheet.absoluteFill}
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          onBarcodeScanned={onQrScanned}
        />
        <View style={styles.scanFrame} />
        <Pressable style={styles.scanCancel} onPress={() => setScanning(false)}>
          <Text style={styles.scanCancelText}>{tr("Cancel")}</Text>
        </Pressable>
      </View>
    );
  }

  if (connecting) return <Frame style={styles.connecting}>
    <ActivityIndicator color={C.ink} size="large" />
    <Text style={styles.connectingText}>{tr("Connecting to your computer…")}</Text>
    {Platform.OS === "ios" && !goingAway ? <Text style={styles.connectingHint}>{tr("If VOOL asks to find devices on your network, tap Allow. That is how it reaches your computer on your Wi-Fi.")}</Text> : null}
    {slow && awayAddress && (Platform.OS !== "ios" || goingAway) ? <Text style={styles.connectingHint}>{tr("Looking for your computer, at home and away. Away from home this can take a minute.")}</Text> : null}
  </Frame>;

  return (
    <Frame><ScrollView style={[styles.root,{direction:rightToLeft()?"rtl":"ltr"}]} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <View style={styles.face}><Face size={124} /></View>
      <Text style={styles.title}>{tr("Pair a phone")}</Text>
      <Text style={styles.hint}>
        {tr("On your computer, open Settings, choose Phone and chat apps, then Pair a phone. Scan its code or enter the details below.")}</Text>
      <Pressable style={styles.scanButton} android_ripple={{color: C.rippleDark}} onPress={() => void startScanning()}>
        <Icon name="qrcode-scan" size={20} color={C.sendInk} />
        <Text style={styles.scanButtonText}>{tr("Scan pairing QR")}</Text>
      </Pressable>
      {error && !manual ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable style={styles.advanced} accessibilityRole="button" accessibilityState={{expanded: manual}} onPress={() => setManual(value => !value)}>
        <Text style={styles.advancedText}>{tr("Advanced")}</Text>
        <Icon name={manual ? "chevron-up" : "chevron-down"} size={20} color={C.mute} />
      </Pressable>
      {manual ? <View>
      <TextInput
        style={[styles.input,{writingDirection:"ltr",textAlign:"left"}]}
        placeholder={tr("Computer address")}
        placeholderTextColor={C.mute}
        autoCapitalize="none"
        autoCorrect={false}
        value={desktopUrl}
        onChangeText={setDesktopUrl}
      />
      <TextInput
        style={[styles.input,{writingDirection:"ltr",textAlign:"left"}]}
        placeholder={tr("Pairing ID")}
        placeholderTextColor={C.mute}
        autoCapitalize="none"
        value={pairingId}
        onChangeText={setPairingId}
      />
      <TextInput
        style={[styles.input, styles.codeInput]}
        placeholder={tr("8-character code")}
        placeholderTextColor={C.mute}
        autoCapitalize="characters"
        value={code}
        onChangeText={setCode}
      />
      <TextInput
        style={[styles.input,{writingDirection:"ltr",textAlign:"left"}]}
        placeholder={tr("Computer check code")}
        placeholderTextColor={C.mute}
        autoCapitalize="none"
        autoCorrect={false}
        value={fingerprint}
        onChangeText={setFingerprint}
      />
      <TextInput
        style={[styles.input,{writingDirection:"ltr",textAlign:"left"}]}
        placeholder={tr("Device name")}
        placeholderTextColor={C.mute}
        value={deviceName}
        onChangeText={setDeviceName}
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable style={styles.button} onPress={pair} disabled={busy}>
        {busy ? <ActivityIndicator color={C.sendInk} /> : <Text style={styles.buttonText}>{tr("Pair")}</Text>}
      </Pressable>
      </View> : null}
      <Text style={styles.footnote}>
        {tr("The connection is encrypted. Your wallet and model credentials stay on your computer.")}</Text>
    </ScrollView></Frame>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  content: { padding: 22, paddingTop: 40, paddingBottom: 40 },
  face: { alignItems: "center", marginBottom: 6 },
  title: { color: C.ink, fontSize: 30, fontWeight: "700", letterSpacing: -0.6, marginBottom: 10, textAlign: "center" },
  hint: { color: C.soft, fontSize: 15, lineHeight: 22, marginBottom: 26, textAlign: "center" },
  scanButton: { backgroundColor: C.send, borderRadius: 28, overflow: "hidden", minHeight: 56, padding: 16, alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 10, marginBottom: 12 },
  scanButtonText: { color: C.sendInk, fontWeight: "700", fontSize: 16 },
  input: { backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, color: C.ink, borderRadius: 16, padding: 14, marginBottom: 12, fontSize: 15 },
  codeInput: { letterSpacing: 3, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" },
  error: { color: C.bad, marginBottom: 12, fontSize: 13 },
  button: { backgroundColor: C.send, borderRadius: 28, padding: 16, alignItems: "center" },
  buttonText: { color: C.sendInk, fontWeight: "600", fontSize: 16 },
  footnote: { color: C.mute, fontSize: 12, lineHeight: 18, marginTop: 18, textAlign: "center" },
  advanced: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, minHeight: 48, marginBottom: 8 },
  advancedText: { color: C.mute, fontSize: 14, fontWeight: "600" },
  connecting: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32, gap: 18 },
  connectingText: { color: C.ink, fontSize: 18, fontWeight: "600", textAlign: "center" },
  connectingHint: { color: C.soft, fontSize: 14, lineHeight: 21, textAlign: "center" },
  scanRoot: { flex: 1, backgroundColor: "#000" },
  scanFrame: { position: "absolute", left: 40, right: 40, top: 140, bottom: 260, borderWidth: 2, borderColor: C.ink, borderRadius: 24 },
  scanCancel: { position: "absolute", bottom: 60, alignSelf: "center", padding: 12 },
  scanCancelText: { color: C.ink, fontSize: 16 },
});
