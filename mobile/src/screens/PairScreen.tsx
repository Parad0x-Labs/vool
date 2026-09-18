import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { Linking } from "react-native";

import {
  claimPairing, credentialsFromClaim, fetchPairingChallenge,
  type DeviceCredentials,
} from "../protocol/client.ts";
import { parsePairingUri, pairingUriIsFresh } from "../protocol/pairing.ts";
import { generateDeviceKeypair, keypairFromSeed } from "../protocol/ed25519.ts";
import { credentialStoreForEnvironment } from "../store/credentials.ts";

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>("");
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
      if (!pairingUriIsFresh(parsed)) {
        setError("This pairing QR/link has expired — start a new pairing on the desktop.");
      }
    } else {
      setError("That link is not a valid VOOL pairing payload.");
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
    if (!pairingUriIsFresh(parsed)) {
      setError("This pairing QR has expired — start a new pairing on the desktop.");
    }
  }, []);

  const pair = async () => {
    setError("");
    const base = desktopUrl.trim().replace(/\/$/, "").replace(/^vool-pair:\/\//, "http://");
    if (!base || !pairingId.trim() || !code.trim()) {
      setError("Enter the desktop address, pairing id and code.");
      return;
    }
    setBusy(true);
    try {
      // 1. Fetch the server-held challenge (also pins the desktop identity
      //    for the manual-entry path, where no QR fingerprint was scanned).
      const challenge = await fetchPairingChallenge(base, pairingId.trim());
      // 2. The phone's identity: generated once, seed stays in the keystore.
      const keypair = generateDeviceKeypair();
      // 3. Claim with proof of possession; the desktop verifies the
      //    signature over challenge/pid/code/public-key/expiry BEFORE any
      //    grant is issued.
      const { claim, identityOk } = await claimPairing(base, {
        pairingId: pairingId.trim(),
        code: code.trim(),
        deviceName: (deviceName.trim() || "VOOL companion").slice(0, 64),
        platform: "react-native",
        deviceSeedHex: keypair.seedHex,
        devicePublicKeyHex: keypair.publicKeyHex,
        challenge,
      });
      if (!identityOk) {
        throw new Error("desktop identity proof failed verification — not storing credentials");
      }
      await onPaired(credentialsFromClaim(base, claim, keypair.seedHex));
    } catch (exc) {
      setError(String((exc as Error).message || exc));
    } finally {
      setBusy(false);
    }
  };

  const startScanning = async () => {
    if (!cameraPermission?.granted) {
      const result = await requestCameraPermission();
      if (!result.granted) {
        setError("Camera permission is needed to scan the desktop's pairing QR.");
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
          <Text style={styles.scanCancelText}>Cancel</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Pair with your desktop</Text>
      <Text style={styles.hint}>
        On the desktop, open the pairing page (POST /api/mobile/pairing/start → pairing_page) and
        scan the QR — or open its vool-pair:// link — or type the short code below. The code is
        single-use and expires in minutes.
      </Text>
      <Pressable style={styles.scanButton} onPress={() => void startScanning()}>
        <Text style={styles.scanButtonText}>Scan pairing QR</Text>
      </Pressable>
      <TextInput
        style={styles.input}
        placeholder="Desktop address (http://192.168.1.20:11435)"
        placeholderTextColor="#5b6570"
        autoCapitalize="none"
        autoCorrect={false}
        value={desktopUrl}
        onChangeText={setDesktopUrl}
      />
      <TextInput
        style={styles.input}
        placeholder="Pairing id (pair_…)"
        placeholderTextColor="#5b6570"
        autoCapitalize="none"
        value={pairingId}
        onChangeText={setPairingId}
      />
      <TextInput
        style={[styles.input, styles.codeInput]}
        placeholder="8-character code"
        placeholderTextColor="#5b6570"
        autoCapitalize="characters"
        value={code}
        onChangeText={setCode}
      />
      <TextInput
        style={styles.input}
        placeholder="Device name (e.g. Kitchen iPhone)"
        placeholderTextColor="#5b6570"
        value={deviceName}
        onChangeText={setDeviceName}
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable style={styles.button} onPress={pair} disabled={busy}>
        {busy ? <ActivityIndicator color="#0b0d10" /> : <Text style={styles.buttonText}>Pair</Text>}
      </Pressable>
      <Text style={styles.footnote}>
        This phone keeps its own Ed25519 identity (seed in the platform keystore) plus one grant
        secret. Every request is signed by the device key — a stolen grant alone cannot act.
        Model keys and wallet keys never leave the desktop.
      </Text>
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
