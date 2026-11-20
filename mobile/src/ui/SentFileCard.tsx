import React, {useCallback, useEffect, useRef, useState} from "react";
import {Image, Linking, Platform, Pressable, StyleSheet, Text, View} from "react-native";
import type {MobileCompanionClient} from "../protocol/client.ts";
import type {SentFile} from "../protocol/models.ts";
import {tr} from "../i18n/index.ts";
import {vaultNative} from "../vault/native.ts";
import {savedPlace} from "../vault/saved.ts";
import {isPicture, sizeText} from "../chat/sentFiles.ts";
import {keepOnPhone} from "../screens/FromComputer.tsx";
import Icon from "./Icon.tsx";
import {C} from "./theme.ts";

// The computer sends files in 768 KiB pieces, the Vault's own piece size, so each piece goes straight into a sealed
// stream that never touches the phone's storage in plain: the picture shows from it, and Save or Share copy it out.
const PIECE = 768 * 1024;
const PREVIEW_MAX_BYTES = 20 * 1024 * 1024;
const streams = new Map<string, string>();
const previews = new Map<string, string>();

function sealedStreamsWork(): boolean {
  return !!vaultNative && vaultNative.pieceBytes === PIECE;
}

async function streamOf(client: MobileCompanionClient, file: SentFile): Promise<string> {
  const vault = vaultNative!;
  const pieces = Math.ceil(file.size_bytes / PIECE);
  const known = streams.get(file.id);
  if (known && vault.held(known).length === pieces) return known;
  if (known) {vault.close(known); streams.delete(file.id);}
  const stream = vault.open(file.size_bytes, file.media_type || "application/octet-stream");
  try {
    let offset = 0;
    for (let index = 0; index < pieces; index += 1) {
      const piece = await client.fetchSentPiece(file.id, offset);
      await vault.put(stream, index, piece.content_b64);
      if (piece.done) break;
      offset = piece.next_offset;
    }
  } catch (error) {
    vault.close(stream);
    throw error;
  }
  streams.set(file.id, stream);
  return stream;
}

/** A file the computer sent in answer to this chat: the picture itself, or a file card, with Save and Share. */
export default function SentFileCard({client, file}: {client: MobileCompanionClient; file: SentFile}) {
  const picture = isPicture(file);
  const [preview, setPreview] = useState(previews.get(file.id) ?? "");
  const [note, setNote] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => {alive.current = false;}, []);

  useEffect(() => {
    if (!picture || preview || file.size_bytes <= 0 || file.size_bytes > PREVIEW_MAX_BYTES || !sealedStreamsWork()) return;
    let stop = false;
    void (async () => {
      try {
        const stream = await streamOf(client, file);
        const jpeg = await vaultNative!.preview(stream, 960, "photo");
        if (!jpeg || stop) return;
        const uri = "data:image/jpeg;base64," + jpeg;
        previews.set(file.id, uri);
        setPreview(uri);
      } catch {
        // The file card below still offers Save and Share.
      }
    })();
    return () => {stop = true;};
  }, [client, file, picture, preview]);

  const save = useCallback(async () => {
    setBusy(true); setNote(tr("Saving…")); setLink("");
    try {
      const media = /^(image|video|audio)\//.test(file.media_type || "");
      if (sealedStreamsWork() && file.size_bytes > 0 && (media || Platform.OS === "ios")) {
        const saved = savedPlace(await vaultNative!.save(await streamOf(client, file), file.name));
        if (alive.current) {setNote(saved.text); setLink(saved.link);}
      } else {
        const {base64} = await client.fetchSentFile(file.id);
        await keepOnPhone(file, base64);
        if (alive.current) setNote(tr("Saved on this phone."));
      }
    } catch {
      if (alive.current) setNote(tr("Could not save this file. Try again."));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [client, file]);

  const share = useCallback(async () => {
    setBusy(true); setNote(""); setLink("");
    try {
      await vaultNative!.share(await streamOf(client, file), file.name, tr("Share"));
    } catch {
      if (alive.current) setNote(tr("Could not save this file. Try again."));
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [client, file]);

  const canShare = sealedStreamsWork() && file.size_bytes > 0;
  return (
    <View style={styles.card}>
      {preview ? <Image source={{uri: preview}} style={styles.picture} resizeMode="cover" accessibilityLabel={file.name} /> : null}
      <View style={styles.row}>
        {preview ? null : <View style={styles.mark}><Icon name={picture ? "image-outline" : "file-outline"} size={22} color={C.acc} /></View>}
        <View style={styles.meta}>
          <Text style={styles.name} numberOfLines={1}>{file.name}</Text>
          <Text style={styles.detail}>{sizeText(file.size_bytes)}</Text>
        </View>
        <Pressable style={styles.button} disabled={busy} onPress={() => void save()} accessibilityRole="button" hitSlop={4}>
          <Icon name="download" size={18} color={C.ink} />
          <Text style={styles.buttonText}>{tr("Save")}</Text>
        </Pressable>
        {canShare ? <Pressable style={styles.button} disabled={busy} onPress={() => void share()} accessibilityRole="button" hitSlop={4}>
          <Icon name="share-variant" size={18} color={C.ink} />
          <Text style={styles.buttonText}>{tr("Share")}</Text>
        </Pressable> : null}
      </View>
      {note ? <View style={styles.noteRow}>
        <Text style={styles.note}>{note}</Text>
        {link ? <Pressable onPress={() => void Linking.openURL(link).catch(() => {})} accessibilityRole="button" hitSlop={6}>
          <Text style={styles.open}>{tr("Open")}</Text>
        </Pressable> : null}
      </View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {marginTop: 10, borderRadius: 16, borderWidth: 1, borderColor: C.line, backgroundColor: C.raised, overflow: "hidden"},
  picture: {width: "100%", aspectRatio: 4 / 3, backgroundColor: C.tint},
  row: {flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 12, paddingVertical: 10},
  mark: {width: 40, height: 40, borderRadius: 12, backgroundColor: C.tint, alignItems: "center", justifyContent: "center"},
  meta: {flex: 1, minWidth: 0},
  name: {color: C.ink, fontSize: 15, fontWeight: "600"},
  detail: {color: C.mute, fontSize: 13, marginTop: 2},
  button: {flexDirection: "row", alignItems: "center", gap: 6, borderRadius: 18, borderWidth: 1, borderColor: C.line, height: 36, paddingHorizontal: 12},
  buttonText: {color: C.ink, fontSize: 14, fontWeight: "600"},
  noteRow: {flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 12, paddingBottom: 10},
  note: {flex: 1, color: C.mute, fontSize: 13},
  open: {color: C.acc, fontSize: 14, fontWeight: "600"},
});
