import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Image, Platform, Pressable, Share, StyleSheet, Text, View} from 'react-native';
import * as FileSystem from 'expo-file-system';
import type {MobileCompanionClient} from '../protocol/client.ts';
import type {SentFile} from '../protocol/models.ts';
import {tr} from '../i18n/index.ts';
import Icon from '../ui/Icon.tsx';
import {C} from "../ui/theme.ts";

function sizeText(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function safeName(name: string): string {
  return name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 120) || 'file';
}

// Android hands out one folder the owner picks (usually Download) and keeps it for this run of the app.
let androidFolder: string | null = null;

export async function keepOnPhone(file: SentFile, base64: string): Promise<string> {
  const folder = (FileSystem.documentDirectory || '') + 'From computer/';
  await FileSystem.makeDirectoryAsync(folder, {intermediates: true}).catch(() => undefined);
  const local = folder + safeName(file.name);
  await FileSystem.writeAsStringAsync(local, base64, {encoding: FileSystem.EncodingType.Base64});
  if (Platform.OS === 'android') {
    const saf = FileSystem.StorageAccessFramework;
    if (!androidFolder) {
      const grant = await saf.requestDirectoryPermissionsAsync();
      if (!grant.granted) return local;
      androidFolder = grant.directoryUri;
    }
    const target = await saf.createFileAsync(androidFolder, safeName(file.name), file.media_type || 'application/octet-stream');
    await saf.writeAsStringAsync(target, base64, {encoding: FileSystem.EncodingType.Base64});
  } else {
    await Share.share({url: local});
  }
  return local;
}

/** Files the computer sent to this phone ("send day4.jpg to my phone"), each one tap to save. */
export default function FromComputer({client, refreshKey, hide}: {client: MobileCompanionClient; refreshKey: number; hide?: ReadonlySet<string>}) {
  const [files, setFiles] = useState<SentFile[]>([]);
  const [state, setState] = useState<Record<string, string>>({});
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const alive = useRef(true);
  useEffect(() => () => {alive.current = false;}, []);

  const refresh = useCallback(async () => {
    try {
      const listed = await client.listSentFiles();
      if (alive.current) setFiles(listed);
    } catch {
      // The chat screen already says when the computer cannot be reached.
    }
  }, [client]);
  useEffect(() => {void refresh();}, [refresh, refreshKey]);

  const save = useCallback(async (file: SentFile) => {
    setState(previous => ({...previous, [file.id]: tr("Saving…")}));
    try {
      const {base64} = await client.fetchSentFile(file.id);
      const local = await keepOnPhone(file, base64);
      if (!alive.current) return;
      if (file.media_type.startsWith('image/')) setPreviews(previous => ({...previous, [file.id]: local}));
      setState(previous => ({...previous, [file.id]: tr("Saved on this phone.")}));
    } catch {
      if (alive.current) setState(previous => ({...previous, [file.id]: tr("Could not save this file. Try again.")}));
    }
  }, [client]);

  const shown = hide ? files.filter(file => !hide.has(file.id)) : files;
  if (!shown.length) return null;
  return (
    <View style={styles.box}>
      <Text style={styles.title}>{tr("From your computer")}</Text>
      {shown.map(file => (
        <View key={file.id} style={styles.row}>
          <View style={styles.mark}><Icon name={file.media_type.startsWith('image/') ? "image-outline" : "file-outline"} size={22} color={C.acc} /></View>
          <View style={styles.meta}>
            <Text style={styles.name} numberOfLines={1}>{file.name}</Text>
            <Text style={styles.detail}>{state[file.id] || sizeText(file.size_bytes)}</Text>
          </View>
          <Pressable style={styles.save} android_ripple={{color: C.rippleDark}} accessibilityRole="button" onPress={() => void save(file)}>
            <Text style={styles.saveText}>{tr("Save")}</Text>
          </Pressable>
          {previews[file.id] ? <Image source={{uri: previews[file.id]}} style={styles.preview} resizeMode="cover" /> : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  box: {backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 10, marginTop: 4, marginBottom: 6},
  title: {color: C.mute, fontSize: 12, fontWeight: '600', letterSpacing: 1, textTransform: 'uppercase', marginTop: 2, marginBottom: 4},
  row: {flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', paddingVertical: 6, gap: 12},
  mark: {width: 42, height: 42, borderRadius: 14, backgroundColor: C.tint, alignItems: 'center', justifyContent: 'center'},
  meta: {flex: 1},
  name: {color: C.ink, fontSize: 15, fontWeight: '600'},
  detail: {color: C.mute, fontSize: 13, marginTop: 2},
  save: {backgroundColor: C.send, borderRadius: 20, height: 40, paddingHorizontal: 18, alignItems: 'center', justifyContent: 'center', overflow: 'hidden'},
  saveText: {color: C.sendInk, fontSize: 14, fontWeight: '600'},
  preview: {width: '100%', height: 180, borderRadius: 16, marginTop: 6},
});
