import {errorText, tr} from "../i18n/index.ts";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, AppState, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import PhoneAbilities from "./PhoneAbilities.tsx";
import FromComputer from "./FromComputer.tsx";

import { CompanionError, type MobileCompanionClient } from "../protocol/client.ts";
import type { ChatMessage, ChatSummary } from "../protocol/models.ts";
import { outboxFor, type QueuedTurn } from "../protocol/outbox.ts";
import { randomBytes, toHex } from "../protocol/sha256.ts";

interface Props {
  client: MobileCompanionClient;
  onRevoked: () => void | Promise<void>;
  initialChatId?: string | null;
}

/** View chats, send messages, attach a photo or file. */
export default function ChatsScreen({ client, onRevoked, initialChatId }: Props) {
  const [chats, setChats] = useState<ChatSummary[] | null>(null);
  const [activeChat, setActiveChat] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState<QueuedTurn[]>([]);
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState("");
  const [progress, setProgress] = useState("");
  const [proof, setProof] = useState("");
  const [filesKey, setFilesKey] = useState(0);
  const hadHistory = useRef(false);
  const activeRef = useRef(activeChat);
  activeRef.current = activeChat;

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

  const refreshChats = useCallback(async () => {
    try {
      setChats(await client.listChats(search));
      hadHistory.current = true;
      setNotice("");
    } catch (exc) {
      if (exc instanceof CompanionError && exc.code === "phone_right_off_or_expired") {
        setChats([]);
        if (hadHistory.current) {setMessages([]);setActiveChat(null);setProof("");}
        hadHistory.current = false;
      }
      if (!guard(exc)) setNotice(exc instanceof CompanionError && exc.code === "phone_right_off_or_expired"
        ? tr("Change this phone’s permissions on your computer.")
        : tr("Can't reach your computer. Your saved messages will wait here."));
    }
  }, [client, guard, search]);

  const outbox = useMemo(() => outboxFor(client), [client]);
  const refreshRef = useRef(refreshChats);
  refreshRef.current = refreshChats;
  useEffect(() => outbox.subscribe(setQueued,
    (result, turn) => {
      if (activeRef.current === result.chat_id) {
        setMessages(previous => [...previous, {role: "user", content: turn.text}, {role: "assistant", content: result.reply}]);
        void client.chatHistory(result.chat_id).then(setMessages).catch(() => {});
        if (result.commit?.request_id) void client.turnProof(result.chat_id, result.commit.request_id)
          .then(value => setProof(value.state)).catch(() => setProof(""));
      }
      void refreshRef.current();
      setFilesKey(key => key + 1);
    }), [client, outbox]);

  useEffect(() => {
    void outbox.load().catch(error => setNotice(errorText(error)));
    const timer = setInterval(() => {
      if (AppState.currentState === "active") void outbox.flush().catch(error => setNotice(errorText(error)));
    }, 4000);
    return () => clearInterval(timer);
  }, [outbox]);

  useEffect(() => {
    if (!busy) {setProgress(""); return;}
    let after = 0;
    const timer = setInterval(() => {
      if (!outbox.activeKey) return;
      void client.turnProgress(outbox.activeKey, after).then(value => {
        after = value.next_after;
        const last = value.events[value.events.length - 1];
        if (last?.message) setProgress(last.message);
      }).catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [busy, client, outbox]);

  useEffect(() => {
    void refreshChats();
  }, [refreshChats]);

  const openChat = useCallback(
    async (chatId: string) => {
      setActiveChat(chatId); activeRef.current=chatId;
      setMessages([]); setProof("");
      try {
        const history=await client.chatHistory(chatId);
        if (activeRef.current === chatId) setMessages(history);
      } catch (exc) {
        if (guard(exc)) return;
        setNotice(tr("Couldn't load this chat. Try again when your computer is reachable."));
      }
    },
    [client, guard],
  );

  useEffect(() => {if (initialChatId) void openChat(initialChatId);}, [initialChatId, openChat]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text && attachmentIds.length === 0) return;
    setBusy(true);
    try {
      const turn = await outbox.enqueue(activeChat, text || "(attachment)", attachmentIds);
      setActiveChat(turn.chatId);
      activeRef.current = turn.chatId;
      setDraft("");
      setAttachmentIds([]);
      await outbox.flush();
    } catch (exc) {
      if (!guard(exc)) Alert.alert(tr("The request could not finish."), errorText(exc));
    } finally {
      setBusy(false);
    }
  }, [activeChat, attachmentIds, draft, guard, outbox]);

  if (chats === null && !notice) {
    return <ActivityIndicator style={styles.center} color="#7d8794" />;
  }

  if (!activeChat) {
    return (
      <View>
        <TextInput style={styles.draftInput} placeholder={tr("Search recent chats")} placeholderTextColor="#5b6570"
          value={search} onChangeText={setSearch} />
        {notice ? <Text style={styles.pendingAttachments}>{notice}</Text> : null}
        {queued.length ? <Text style={styles.pendingAttachments}>{tr("Saved messages: {count}", {count: queued.length})}</Text> : null}
        <FromComputer client={client} refreshKey={filesKey} />
        <Pressable
          style={styles.newChat}
          onPress={() => {
            setActiveChat("mob-" + toHex(randomBytes(10)));
            setMessages([]);
          }}
        >
          <Text style={styles.newChatText}>{tr("New chat")}</Text>
        </Pressable>
        {(chats || []).map((chat) => (
          <Pressable key={chat.chat_id} style={styles.chatRow} onPress={() => void openChat(chat.chat_id)}>
            <Text style={styles.chatTitle} numberOfLines={1}>
              {chat.title || chat.chat_id}
            </Text>
            <Text style={styles.chatMeta}>
              {tr("Turns: {count}", {count: chat.turn_count ?? 0})} · {chat.updated_at || ""}
            </Text>
          </Pressable>
        ))}
      </View>
    );
  }

  return (
    <View style={styles.thread}>
      <Pressable onPress={() => setActiveChat(null)}>
        <Text style={styles.back}>{tr("Chats")}</Text>
      </Pressable>
      {notice ? <Text style={styles.pendingAttachments}>{notice}</Text> : null}
      <FromComputer client={client} refreshKey={filesKey} />
      <ScrollView style={styles.messages}>
        {messages.map((m, i) => (
          <View key={i} style={[styles.bubble, m.role === "user" ? styles.userBubble : styles.agentBubble]}>
            <Text style={m.role === "user" ? styles.userText : styles.agentText}>{m.content}</Text>
            {m.attachments?.map((a) => (
              <Text key={a.id} style={styles.attachmentTag}>
                {a.name}
              </Text>
            ))}
            {m.answer_state ? <Text style={styles.answerState}>{m.answer_state}</Text> : null}
          </View>
        ))}
      </ScrollView>
      {queued.filter(turn => turn.chatId === activeChat).map(turn => (
        <View key={turn.key}>
          <Text style={styles.pendingAttachments}>
            {turn.state === "blocked" ? errorText({message:turn.reason}) : tr("Saved on this phone. Waiting for your computer.")}
          </Text>
          {turn.state === "blocked" ? <View>
            <Pressable onPress={()=>{void outbox.retryBlocked(turn.key).catch(error=>setNotice(errorText(error)));}}><Text style={styles.back}>{tr("Retry")}</Text></Pressable>
            <Pressable onPress={() => void outbox.removeBlocked(turn.key)}><Text style={styles.back}>{tr("Discard")}</Text></Pressable>
          </View> : null}
        </View>
      ))}
      {progress ? <Text style={styles.pendingAttachments}>{progress}</Text> : null}
      {proof ? <Text style={styles.pendingAttachments}>{proof === "VERIFIED" ? tr("Activity verified") : tr("Activity recorded")}</Text> : null}
      {busy ? <Pressable onPress={() => {
        if (outbox.activeKey) void client.cancelTurn(outbox.activeKey).then(value =>
          setNotice(value.state === "cancelled" ? tr("Stop requested.") : tr("This turn is no longer running."))
        ).catch(() => setNotice(tr("Could not complete this. Check your computer.")));
      }}><Text style={styles.back}>{tr("Stop")}</Text></Pressable> : null}
      {attachmentIds.length > 0 ? (
        <Text style={styles.pendingAttachments}>{tr("Attachments ready: {count}", {count: attachmentIds.length})}</Text>
      ) : null}
      <PhoneAbilities client={client} chatId={activeChat} attachmentCount={attachmentIds.length} onChat={setActiveChat}
        onAttachment={id=>setAttachmentIds(previous=>[...previous,id])} onDraft={text=>setDraft(previous=>previous ? previous+"\n"+text : text)} />
      <View style={styles.composer}>
        <TextInput
          style={styles.draftInput}
          placeholder={tr("Message your desktop…")}
          placeholderTextColor="#5b6570"
          value={draft}
          onChangeText={setDraft}
          multiline
        />
        <Pressable style={styles.sendButton} onPress={() => void send()} disabled={busy}>
          {busy ? <ActivityIndicator color="#0b0d10" /> : <Text style={styles.sendButtonText}>{tr("Send")}</Text>}
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
