import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Image, Platform, Pressable, Share, StyleSheet, Text, View} from 'react-native';
import * as FileSystem from 'expo-file-system';
import type {MobileCompanionClient} from '../protocol/client.ts';
import type {SentFile} from '../protocol/models.ts';
import {tr} from '../i18n/index.ts';

function sizeText(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function safeName(name: string): string {
  return name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 120) || 'file';
}

// Android hands out one folder the owner picks (usually Download) and keeps it for this run of the app.
let androidFolder: string | null = null;

async function keepOnPhone(file: SentFile, base64: string): Promise<string> {
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
export default function FromComputer({client, refreshKey}: {client: MobileCompanionClient; refreshKey: number}) {
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

  if (!files.length) return null;
  return (
    <View style={styles.box}>
      <Text style={styles.title}>{tr("From your computer")}</Text>
      {files.map(file => (
        <View key={file.id} style={styles.row}>
          <View style={styles.meta}>
            <Text style={styles.name} numberOfLines={1}>{file.name}</Text>
            <Text style={styles.detail}>{state[file.id] || sizeText(file.size_bytes)}</Text>
          </View>
          <Pressable style={styles.save} onPress={() => void save(file)}>
            <Text style={styles.saveText}>{tr("Save")}</Text>
          </Pressable>
          {previews[file.id] ? <Image source={{uri: previews[file.id]}} style={styles.preview} resizeMode="cover" /> : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  box: {backgroundColor: '#12161b', borderRadius: 8, padding: 10, marginBottom: 10},
  title: {color: '#aeb9c4', fontSize: 12, marginBottom: 6},
  row: {flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', paddingVertical: 6, gap: 8},
  meta: {flex: 1},
  name: {color: '#e8ecf1', fontSize: 14},
  detail: {color: '#8fa0b2', fontSize: 12, marginTop: 2},
  save: {backgroundColor: '#e8ecf1', borderRadius: 8, paddingVertical: 8, paddingHorizontal: 12},
  saveText: {color: '#0b0d10', fontWeight: '700'},
  preview: {width: '100%', height: 180, borderRadius: 8, marginTop: 6},
});
