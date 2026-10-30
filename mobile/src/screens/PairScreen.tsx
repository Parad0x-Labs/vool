import {errorText, rightToLeft, tr} from "../i18n/index.ts";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { Linking } from "react-native";

import {
  fetchPairingChallenge,
  type DeviceCredentials,
} from "../protocol/client.ts";
import { parsePairingUri, pairingUriIsFresh } from "../protocol/pairing.ts";
import { generateDeviceKeypair, keypairFromSeed } from "../protocol/ed25519.ts";
import { claimSealedPairing } from "../protocol/sealed.ts";
import { credentialStoreForEnvironment } from "../store/credentials.ts";
import {prepareApprovalKey, deleteApprovalKey} from "../security/approvalKey.ts";

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>("");
  // A scanned QR pairs at once; there is nothing left to type, so waiting for a button read as "nothing happened".
  const [pairAfterScan, setPairAfterScan] = useState(false);
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
      setFingerprint(parsed.desktopFingerprint);
      if (!pairingUriIsFresh(parsed)) {
        setError(tr("This pairing QR has expired — start a new pairing on the computer."));
      }
    } else {
      setError(tr("The computer’s identity could not be verified. Check it before pairing again."));
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
    setFingerprint(parsed.desktopFingerprint);
    if (!pairingUriIsFresh(parsed)) {
      setError(tr("This pairing QR has expired — start a new pairing on the computer."));
      return;
    }
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
    let approvalAlias: string | null = null;
    try {
      // 1. Fetch the server-held challenge (also pins the desktop identity
      //    for the manual-entry path, where no QR fingerprint was scanned).
      const challenge = await fetchPairingChallenge(base, pairingId.trim());
      // 2. The phone's identity: generated once, seed stays in the keystore.
      const keypair = generateDeviceKeypair();
      const approvalKey = await prepareApprovalKey(keypair.publicKeyHex, pairingId.trim(), challenge.challenge);
      if (approvalKey) approvalAlias = keypair.publicKeyHex;
      // 3. Claim with proof of possession; the desktop verifies the
      //    signature over challenge/pid/code/public-key/expiry BEFORE any
      //    grant is issued.
      const credentials = await claimSealedPairing(base, {
        pairingId: pairingId.trim(),
        code: code.trim(),
        deviceName: (deviceName.trim() || "VOOL companion").slice(0, 64),
        platform: "react-native",
        deviceSeedHex: keypair.seedHex,
        devicePublicKeyHex: keypair.publicKeyHex,
        challenge,
        pinnedDesktopFingerprint: fingerprint.trim(),
        approvalKey,
      });
      await onPaired({...credentials, ...(approvalAlias ? {approvalKeyAlias: approvalAlias} : {})});
      approvalAlias = null;
    } catch (exc) {
      if (approvalAlias) await deleteApprovalKey(approvalAlias).catch(() => {});
      setError(errorText(exc));
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

  return (
    <ScrollView style={[styles.root,{direction:rightToLeft()?"rtl":"ltr"}]} contentContainerStyle={styles.content}>
      <Text style={styles.title}>{tr("Pair a phone")}</Text>
      <Text style={styles.hint}>
        {tr("On your computer, open Settings, choose Phone and chat apps, then Pair a phone. Scan its code or enter the details below.")}</Text>
      <Pressable style={styles.scanButton} onPress={() => void startScanning()}>
        <Text style={styles.scanButtonText}>{tr("Scan pairing QR")}</Text>
      </Pressable>
      <TextInput
        style={[styles.input,{writingDirection:"ltr",textAlign:"left"}]}
        placeholder={tr("Computer address")}
        placeholderTextColor="#5b6570"
        autoCapitalize="none"
        autoCorrect={false}
        value={desktopUrl}
        onChangeText={setDesktopUrl}
      />
      <TextInput
        style={[styles.input,{writingDirection:"ltr",textAlign:"left"}]}
        placeholder={tr("Pairing ID")}
        placeholderTextColor="#5b6570"
        autoCapitalize="none"
        value={pairingId}
        onChangeText={setPairingId}
      />
      <TextInput
        style={[styles.input, styles.codeInput]}
        placeholder={tr("8-character code")}
        placeholderTextColor="#5b6570"
        autoCapitalize="characters"
        value={code}
        onChangeText={setCode}
      />
      <TextInput
        style={[styles.input,{writingDirection:"ltr",textAlign:"left"}]}
        placeholder={tr("Computer check code")}
        placeholderTextColor="#5b6570"
        autoCapitalize="none"
        autoCorrect={false}
        value={fingerprint}
        onChangeText={setFingerprint}
      />
      <TextInput
        style={[styles.input,{writingDirection:"ltr",textAlign:"left"}]}
        placeholder={tr("Device name")}
        placeholderTextColor="#5b6570"
        value={deviceName}
        onChangeText={setDeviceName}
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable style={styles.button} onPress={pair} disabled={busy}>
        {busy ? <ActivityIndicator color="#0b0d10" /> : <Text style={styles.buttonText}>{tr("Pair")}</Text>}
      </Pressable>
      <Text style={styles.footnote}>
        {tr("The connection is encrypted. Your wallet and model credentials stay on your computer.")}</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0b0d10" },
  content: { padding: 20, paddingTop: 60 },
  title: { color: "#e8ecf1", fontSize: 24, fontWeight: "700", marginBottom: 8 },
  hint: { color: "#7d8794", fontSize: 13, lineHeight: 18, marginBottom: 16 },
  scanButton: { backgroundColor: "#14532d", borderRadius: 8, padding: 14, alignItems: "center", marginBottom: 16 },
  scanButtonText: { color: "#bbf7d0", fontWeight: "700" },
  input: { backgroundColor: "#12161b", color: "#e8ecf1", borderRadius: 8, padding: 14, marginBottom: 12, fontSize: 15 },
  codeInput: { letterSpacing: 3, fontFamily: "menlo" },
  error: { color: "#ff6b6b", marginBottom: 12, fontSize: 13 },
  button: { backgroundColor: "#e8ecf1", borderRadius: 8, padding: 16, alignItems: "center" },
  buttonText: { color: "#0b0d10", fontWeight: "700", fontSize: 16 },
  footnote: { color: "#5b6570", fontSize: 11, lineHeight: 16, marginTop: 16 },
  scanRoot: { flex: 1, backgroundColor: "#000" },
  scanFrame: { position: "absolute", left: 40, right: 40, top: 140, bottom: 260, borderWidth: 2, borderColor: "#e8ecf1", borderRadius: 12 },
  scanCancel: { position: "absolute", bottom: 60, alignSelf: "center", padding: 12 },
  scanCancelText: { color: "#e8ecf1", fontSize: 16 },
});
