import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import * as ImagePicker from "expo-image-picker";

import { CompanionError, type MobileCompanionClient } from "../protocol/client.ts";
import type { ChatMessage, ChatSummary } from "../protocol/models.ts";

interface Props {
  client: MobileCompanionClient;
  onRevoked: () => void | Promise<void>;
}

/** View chats, send messages, attach a photo or file. */
export default function ChatsScreen({ client, onRevoked }: Props) {
  const [chats, setChats] = useState<ChatSummary[] | null>(null);
  const [activeChat, setActiveChat] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

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

  const refreshChats = useCallback(async () => {
    try {
      setChats(await client.listChats());
    } catch (exc) {
      if (!guard(exc)) setChats([]);
    }
  }, [client, guard]);

  useEffect(() => {
    void refreshChats();
  }, [refreshChats]);

  const openChat = useCallback(
    async (chatId: string) => {
      setActiveChat(chatId);
      try {
        setMessages(await client.chatHistory(chatId));
      } catch (exc) {
        if (guard(exc)) return;
        setMessages([]);
      }
    },
    [client, guard],
  );

  const attachPhoto = useCallback(async () => {
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.All,
        quality: 0.6,            // downscale/compress BEFORE upload — the
        allowsMultipleSelection: false, // companion upload ceiling is 3 MiB
      });
      if (result.canceled || !result.assets?.[0]) return;
      const asset = result.assets[0];
      const chatId = activeChat ?? (await client.prepareChat()).chat_id;
      setActiveChat(chatId);
      // Read the (compressed) bytes and upload through the typed staging door.
      const response = await fetch(asset.uri);
      const buf = new Uint8Array(await response.arrayBuffer());
      const kind = asset.type === "image" ? "photo" : "file";
      const mediaType = asset.mimeType || (kind === "photo" ? "image/jpeg" : "application/octet-stream");
      const name = asset.fileName || (kind === "photo" ? "photo.jpg" : "attachment.bin");
      const record = await client.uploadAttachment(chatId, name, buf, { kind: kind as "photo" | "file", mediaType });
      setAttachmentIds((prev) => [...prev, record.id]);
    } catch (exc) {
      if (!guard(exc)) Alert.alert("Attachment failed", String((exc as Error).message || exc));
    }
  }, [activeChat, client, guard]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text && attachmentIds.length === 0) return;
    setBusy(true);
    try {
      const result = await client.sendMessage(activeChat, text || "(attachment)", attachmentIds);
      setActiveChat(result.chat_id);
      setDraft("");
      setAttachmentIds([]);
      setMessages(await client.chatHistory(result.chat_id));
      void refreshChats();
    } catch (exc) {
      if (!guard(exc)) Alert.alert("Send failed", String((exc as Error).message || exc));
    } finally {
      setBusy(false);
    }
  }, [activeChat, attachmentIds, client, draft, guard, refreshChats]);

  if (chats === null) {
    return <ActivityIndicator style={styles.center} color="#7d8794" />;
  }

  if (!activeChat) {
    return (
      <View>
        <Pressable
          style={styles.newChat}
          onPress={async () => {
            const { chat_id } = await client.prepareChat();
            setActiveChat(chat_id);
            setMessages([]);
          }}
        >
          <Text style={styles.newChatText}>+ New chat</Text>
        </Pressable>
        {chats.map((chat) => (
          <Pressable key={chat.chat_id} style={styles.chatRow} onPress={() => void openChat(chat.chat_id)}>
            <Text style={styles.chatTitle} numberOfLines={1}>
              {chat.title || chat.chat_id}
            </Text>
            <Text style={styles.chatMeta}>
              {chat.turn_count ?? 0} turns · {chat.updated_at || ""}
            </Text>
          </Pressable>
        ))}
      </View>
    );
  }

  return (
    <View style={styles.thread}>
      <Pressable onPress={() => setActiveChat(null)}>
        <Text style={styles.back}>‹ All chats</Text>
      </Pressable>
      <ScrollView style={styles.messages}>
        {messages.map((m, i) => (
          <View key={i} style={[styles.bubble, m.role === "user" ? styles.userBubble : styles.agentBubble]}>
            <Text style={m.role === "user" ? styles.userText : styles.agentText}>{m.content}</Text>
            {m.attachments?.map((a) => (
              <Text key={a.id} style={styles.attachmentTag}>
                📎 {a.name} ({a.kind})
              </Text>
            ))}
            {m.answer_state ? <Text style={styles.answerState}>{m.answer_state}</Text> : null}
          </View>
        ))}
      </ScrollView>
      {attachmentIds.length > 0 ? (
        <Text style={styles.pendingAttachments}>{attachmentIds.length} attachment(s) staged</Text>
      ) : null}
      <View style={styles.composer}>
        <Pressable style={styles.attachButton} onPress={() => void attachPhoto()}>
          <Text style={styles.attachButtonText}>📎</Text>
        </Pressable>
        <TextInput
          style={styles.draftInput}
          placeholder="Message your desktop…"
          placeholderTextColor="#5b6570"
          value={draft}
          onChangeText={setDraft}
          multiline
        />
        <Pressable style={styles.sendButton} onPress={() => void send()} disabled={busy}>
          {busy ? <ActivityIndicator color="#0b0d10" /> : <Text style={styles.sendButtonText}>Send</Text>}
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { marginTop: 40 },
  newChat: { backgroundColor: "#12161b", borderRadius: 8, padding: 14, marginBottom: 12, alignItems: "center" },
  newChatText: { color: "#e8ecf1", fontWeight: "600" },
  chatRow: { paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: "#161b21" },
  chatTitle: { color: "#e8ecf1", fontSize: 15 },
  chatMeta: { color: "#5b6570", fontSize: 12, marginTop: 3 },
  thread: { flex: 1 },
  back: { color: "#7d8794", marginBottom: 10 },
  messages: { flex: 1 },
  bubble: { borderRadius: 10, padding: 12, marginBottom: 10, maxWidth: "90%" },
  userBubble: { backgroundColor: "#1d4ed8", alignSelf: "flex-end" },
  agentBubble: { backgroundColor: "#12161b", alignSelf: "flex-start" },
  userText: { color: "#f4f6f9" },
  agentText: { color: "#d3d9e0" },
  attachmentTag: { color: "#aeb9c4", fontSize: 12, marginTop: 6 },
  answerState: { color: "#8fa0b2", fontSize: 10, marginTop: 6, textTransform: "uppercase" },
  pendingAttachments: { color: "#8fa0b2", fontSize: 12, paddingVertical: 6 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8 },
  attachButton: { backgroundColor: "#12161b", borderRadius: 8, padding: 12 },
  attachButtonText: { fontSize: 16 },
  draftInput: { flex: 1, backgroundColor: "#12161b", color: "#e8ecf1", borderRadius: 8, padding: 12, maxHeight: 120 },
  sendButton: { backgroundColor: "#e8ecf1", borderRadius: 8, padding: 12 },
  sendButtonText: { color: "#0b0d10", fontWeight: "700" },
});
