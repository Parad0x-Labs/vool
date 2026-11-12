import {errorText, rightToLeft, tr} from "../i18n/index.ts";
import {chatUpdatedText, messageTimeText, stepTimeText} from "../i18n/time.ts";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, AppState, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import PhoneAbilities from "./PhoneAbilities.tsx";
import FromComputer from "./FromComputer.tsx";
import Header from "../ui/Header.tsx";
import {useLink} from "../ui/LinkBadge.tsx";
import {linkState} from "../protocol/linkState.ts";
import Icon, {type IconName} from "../ui/Icon.tsx";
import Gears from "../ui/Gears.tsx";
import {C, R} from "../ui/theme.ts";

import { CompanionError, type MobileCompanionClient } from "../protocol/client.ts";
import type { ChatMessage, ChatSummary } from "../protocol/models.ts";
import { outboxFor, type QueuedTurn } from "../protocol/outbox.ts";
import { randomBytes, toHex } from "../protocol/sha256.ts";

interface Props {
  client: MobileCompanionClient;
  onRevoked: () => void | Promise<void>;
  /** A chat picked outside this screen (side menu, an alert). `seq` changes on every pick. */
  pick?: {id: string | null; seq: number} | null;
  onMenu: () => void;
  onLink: () => void;
  menuDot: boolean;
  /** The recent chats, for the side menu. */
  onChats: (chats: ChatSummary[]) => void;
}

/** The chat list, and one chat: read it, write to the computer, attach a photo or file. */
// A first tap for someone who does not know what to ask. It fills the message box and does not send.
const STARTERS: [IconName, () => string][] = [
  ["help-circle-outline", () => tr("What can you do for me?")],
  ["folder-search-outline", () => tr("Find a file on my computer")],
  ["pencil-outline", () => tr("Help me write a message")],
  ["lightbulb-on-outline", () => tr("Plan my week")],
];

export default function ChatsScreen({ client, onRevoked, pick, onMenu, onLink, menuDot, onChats }: Props) {
  const [chats, setChats] = useState<ChatSummary[] | null>(null);
  const [activeChat, setActiveChat] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState<QueuedTurn[]>([]);
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState("");
  const [abilityNote, setAbilityNote] = useState("");
  const [sheet, setSheet] = useState(false);
  const [progress, setProgress] = useState("");
  const [progressAt, setProgressAt] = useState("");
  // The computer has this message and is working on it (its progress feed answered for this turn).
  const [received, setReceived] = useState<string | null>(null);
  // The message whose time is showing (tap or hold it); a second tap or four seconds hides it.
  const [timeShown, setTimeShown] = useState<number | null>(null);
  const [proof, setProof] = useState("");
  const [filesKey, setFilesKey] = useState(0);
  const hadHistory = useRef(false);
  const activeRef = useRef(activeChat);
  activeRef.current = activeChat;
  const scroll = useRef<ScrollView>(null);
  const chatsRef = useRef(onChats);
  chatsRef.current = onChats;

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
      const listed = await client.listChats(search);
      setChats(listed);
      if (!search) chatsRef.current(listed);
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
        : linkState() === "connecting" ? "" : tr("Can't reach your computer. Your saved messages will wait here."));
    }
  }, [client, guard, search]);

  const outbox = useMemo(() => outboxFor(client), [client]);
  const refreshRef = useRef(refreshChats);
  refreshRef.current = refreshChats;
  // The moment the computer answers (at home or away), the list is read again, so a line that
  // said it was out of reach never stays on screen while the app is connected.
  const link = useLink();
  useEffect(() => {if (link === "connected") void refreshRef.current();}, [link]);
  useEffect(() => outbox.subscribe(setQueued,
    (result, turn) => {
      if (activeRef.current === result.chat_id) {
        setMessages(previous => [...previous, {role: "user", content: turn.text}, {role: "assistant", content: result.reply, ts: new Date().toISOString()}]);
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
    if (!busy) {setProgress(""); setProgressAt(""); setReceived(null); return;}
    let after = 0;
    const timer = setInterval(() => {
      const key = outbox.activeKey;
      if (!key) return;
      void client.turnProgress(key, after).then(value => {
        after = value.next_after;
        setReceived(key);
        const last = value.events[value.events.length - 1];
        if (last?.message) {setProgress(last.message); setProgressAt(typeof last.created_at === "string" ? last.created_at : "");}
      }).catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [busy, client, outbox]);

  useEffect(() => {
    void refreshChats();
  }, [refreshChats]);

  useEffect(() => {
    if (timeShown === null) return;
    const timer = setTimeout(() => setTimeShown(null), 4000);
    return () => clearTimeout(timer);
  }, [timeShown]);

  const openChat = useCallback(
    async (chatId: string) => {
      setActiveChat(chatId); activeRef.current=chatId;
      setMessages([]); setProof(""); setTimeShown(null); setAbilityNote("");
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

  const newChat = useCallback(() => {
    const id = "mob-" + toHex(randomBytes(10));
    setActiveChat(id); activeRef.current = id;
    setMessages([]); setProof(""); setTimeShown(null); setAbilityNote("");
  }, []);

  const pickSeq = pick?.seq;
  useEffect(() => {
    if (!pick) return;
    if (pick.id) void openChat(pick.id); else newChat();
    // One run per pick: `seq` is its identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickSeq]);

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

  if (!activeChat) {
    return (
      <View style={styles.screen}>
        <Header title="VOOL" big lead="menu" onLead={onMenu} onLink={onLink} dot={menuDot} />
        <ScrollView style={styles.fill} contentContainerStyle={styles.listContent} keyboardShouldPersistTaps="handled">
          {(chats?.length || search) ? <View style={styles.search}>
            <Icon name="magnify" size={20} color={C.mute} />
            <TextInput style={styles.searchInput} placeholder={tr("Search recent chats")} placeholderTextColor={C.mute}
              value={search} onChangeText={setSearch} />
          </View> : null}
          {notice ? <Text style={styles.note}>{notice}</Text> : null}
          {queued.length ? <Text style={styles.note}>{tr("Saved messages: {count}", {count: queued.length})}</Text> : null}
          <FromComputer client={client} refreshKey={filesKey} />
          {chats === null && !notice ? <ActivityIndicator style={styles.center} color={C.mute} /> : null}
          {chats?.length === 0 && !search && !notice ? <View style={styles.empty}>
            <View style={styles.emptyMark}><Icon name="message-text-outline" size={30} color={C.soft} /></View>
            <Text style={styles.emptyTitle}>{tr("What can I help with?")}</Text>
            <View style={styles.starters}>
              {STARTERS.map(([icon, text]) => <Pressable key={icon} style={styles.starter} android_ripple={{color: C.ripple}} accessibilityRole="button"
                onPress={() => {newChat(); setDraft(text());}}>
                <Icon name={icon} size={22} color={C.soft} />
                <Text style={styles.starterText} numberOfLines={2}>{text()}</Text>
                <Icon name={rightToLeft() ? "chevron-left" : "chevron-right"} size={20} color={C.mute} />
              </Pressable>)}
            </View>
          </View> : null}
          {(chats || []).map((chat) => (
            <Pressable key={chat.chat_id} style={styles.chatRow} android_ripple={{color: C.ripple}} onPress={() => void openChat(chat.chat_id)} accessibilityRole="button">
              <View style={styles.chatMark}><Icon name="message-outline" size={20} color={C.soft} /></View>
              <View style={styles.fill}>
                <Text style={styles.chatTitle} numberOfLines={1}>
                  {chat.title || tr("New chat")}
                </Text>
                <Text style={styles.chatMeta}>
                  {tr("Replies: {count}", {count: chat.turn_count ?? 0})}{chatUpdatedText(chat.updated_at) ? " · " + chatUpdatedText(chat.updated_at) : ""}
                </Text>
              </View>
            </Pressable>
          ))}
        </ScrollView>
        <Pressable style={styles.newChat} android_ripple={{color: "rgba(17,19,20,0.12)"}} onPress={newChat} accessibilityRole="button">
          <Icon name="plus" size={20} color={C.sendInk} />
          <Text style={styles.newChatText}>{tr("New chat")}</Text>
        </Pressable>
      </View>
    );
  }

  const title = (chats || []).find(chat => chat.chat_id === activeChat)?.title || tr("New chat");
  const waiting = queued.filter(turn => turn.chatId === activeChat);
  const sending = busy ? outbox.activeKey : null;
  const canSend = !busy && (draft.trim().length > 0 || attachmentIds.length > 0);

  return (
    <View style={styles.screen}>
      <Header title={title} lead="back" onLead={() => setActiveChat(null)} onLink={onLink} />
      {notice ? <Text style={[styles.note, styles.inset]}>{notice}</Text> : null}
      <ScrollView ref={scroll} style={styles.fill} contentContainerStyle={styles.threadContent}
        onContentSizeChange={() => scroll.current?.scrollToEnd({animated: true})} keyboardShouldPersistTaps="handled">
        <FromComputer client={client} refreshKey={filesKey} />
        {messages.map((m, i) => (
          <Pressable key={i} style={m.role === "user" ? styles.userBubble : styles.agentBlock}
            onPress={() => setTimeShown(shown => shown === i ? null : i)}
            onLongPress={() => setTimeShown(i)}>
            <Text style={m.role === "user" ? styles.userText : styles.agentText}>{m.content}</Text>
            {m.attachments?.map((a) => (
              <View key={a.id} style={styles.attachmentTag}>
                <Icon name="paperclip" size={14} color={C.mute} />
                <Text style={styles.attachmentText}>{a.name}</Text>
              </View>
            ))}
            {m.role === "user" ? <View style={styles.tick}><Icon name="check-all" size={15} color={C.mute} /></View> : null}
            {timeShown === i && messageTimeText(m.ts) ? <Text style={styles.messageTime}>{messageTimeText(m.ts)}</Text> : null}
          </Pressable>
        ))}
        {waiting.map(turn => {
          const blocked = turn.state === "blocked";
          const isSending = sending === turn.key;
          const got = isSending && received === turn.key;
          return (
            <View key={turn.key}>
              <View style={[styles.userBubble, blocked && styles.blockedBubble]}>
                <Text style={styles.userText}>{turn.text}</Text>
                <View style={styles.tick}>
                  <Icon name={blocked ? "alert-circle-outline" : got ? "check-all" : isSending ? "check" : "clock-outline"}
                    size={15} color={blocked ? C.bad : got ? C.ok : C.mute} />
                </View>
              </View>
              {blocked ? <View style={styles.blocked}>
                <Text style={styles.blockedText}>{errorText({code: turn.code, message: turn.reason})}</Text>
                <View style={styles.row}>
                  <Pressable style={styles.chip} onPress={()=>{void outbox.retryBlocked(turn.key).catch(error=>setNotice(errorText(error)));}}>
                    <Icon name="refresh" size={16} color={C.ink} /><Text style={styles.chipText}>{tr("Retry")}</Text>
                  </Pressable>
                  <Pressable style={styles.chip} onPress={() => void outbox.removeBlocked(turn.key)}>
                    <Icon name="trash-can-outline" size={16} color={C.ink} /><Text style={styles.chipText}>{tr("Discard")}</Text>
                  </Pressable>
                </View>
              </View> : !isSending ? <Text style={styles.waitText}>{tr("Saved on this phone. Waiting for your computer.")}</Text> : null}
            </View>
          );
        })}
        {busy ? <View style={styles.working}>
          <Gears size={20} color={received ? C.ok : C.mute} />
          <Text style={styles.workingText} numberOfLines={2}>
            {progress ? (stepTimeText(progressAt) ? stepTimeText(progressAt) + " · " : "") + progress
              : received ? tr("Your computer is working on it…") : tr("Sending to your computer…")}
          </Text>
          <Pressable style={styles.chip} onPress={() => {
            if (outbox.activeKey) void client.cancelTurn(outbox.activeKey).then(value =>
              setNotice(value.state === "cancelled" ? tr("Stop requested.") : tr("This reply has stopped."))
            ).catch(() => setNotice(tr("Could not complete this. Check your computer.")));
          }}>
            <Icon name="stop" size={16} color={C.ink} /><Text style={styles.chipText}>{tr("Stop")}</Text>
          </Pressable>
        </View> : null}
        {proof && !busy ? <View style={styles.proof}>
          <Icon name="shield-check-outline" size={15} color={proof === "VERIFIED" ? C.ok : C.mute} />
          <Text style={styles.proofText}>{proof === "VERIFIED" ? tr("Checked") : tr("Saved to history")}</Text>
        </View> : null}
      </ScrollView>
      {abilityNote ? <Text style={[styles.note, styles.inset]}>{abilityNote}</Text> : null}
      {attachmentIds.length > 0 ? (
        <View style={[styles.proof, styles.inset]}>
          <Icon name="paperclip" size={15} color={C.soft} />
          <Text style={styles.proofText}>{tr("Attachments ready: {count}", {count: attachmentIds.length})}</Text>
        </View>
      ) : null}
      <View style={styles.composer}>
        <Pressable style={styles.plus} onPress={() => setSheet(true)} hitSlop={6} accessibilityRole="button" accessibilityLabel={tr("Add to this message")}>
          <Icon name="plus" size={26} color={C.soft} />
        </Pressable>
        <TextInput
          style={styles.draftInput}
          placeholder={tr("Message your computer…")}
          placeholderTextColor={C.mute}
          value={draft}
          onChangeText={setDraft}
          multiline
        />
        <PhoneAbilities client={client} chatId={activeChat} attachmentCount={attachmentIds.length} onChat={setActiveChat}
          sheetOpen={sheet} onSheetClose={() => setSheet(false)} onNote={setAbilityNote}
          onAttachment={id=>setAttachmentIds(previous=>[...previous,id])} onDraft={text=>setDraft(previous=>previous ? previous+"\n"+text : text)} />
        <Pressable style={[styles.sendButton, !canSend && styles.sendOff]} onPress={() => void send()} disabled={!canSend}
          accessibilityRole="button" accessibilityLabel={tr("Send")}>
          {busy ? <ActivityIndicator color={C.sendInk} /> : <Icon name="arrow-up" size={24} color={canSend ? C.sendInk : C.mute} />}
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  fill: { flex: 1 },
  center: { marginTop: 40 },
  inset: { paddingHorizontal: 18 },
  row: { flexDirection: "row", gap: 8 },
  listContent: { paddingHorizontal: 16, paddingBottom: 110 },
  search: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.raised, borderRadius: R.pill, paddingHorizontal: 16, height: 48, marginTop: 4, marginBottom: 10 },
  searchInput: { flex: 1, color: C.ink, fontSize: 16, padding: 0 },
  note: { color: C.soft, fontSize: 13, lineHeight: 19, paddingVertical: 8 },
  chatRow: { flexDirection: "row", alignItems: "center", gap: 14, minHeight: 68, paddingVertical: 10, paddingHorizontal: 8, borderRadius: 16, overflow: "hidden" },
  chatMark: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.raised, alignItems: "center", justifyContent: "center" },
  empty: { alignItems: "center", paddingTop: 56, paddingBottom: 24 },
  emptyMark: { width: 64, height: 64, borderRadius: 32, backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, alignItems: "center", justifyContent: "center", marginBottom: 20 },
  emptyTitle: { color: C.ink, fontSize: 24, lineHeight: 30, fontWeight: "600", textAlign: "center", marginBottom: 24 },
  starters: { alignSelf: "stretch", gap: 10 },
  starter: { flexDirection: "row", alignItems: "center", gap: 14, minHeight: 56, paddingHorizontal: 16, paddingVertical: 12, backgroundColor: C.panel, borderRadius: 18, borderWidth: 1, borderColor: C.line, overflow: "hidden" },
  starterText: { flex: 1, color: C.ink, fontSize: 15, lineHeight: 20 },
  chatTitle: { color: C.ink, fontSize: 16, fontWeight: "500" },
  chatMeta: { color: C.mute, fontSize: 13, marginTop: 3 },
  newChat: { position: "absolute", right: 18, bottom: 22, flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.send, borderRadius: R.pill, paddingHorizontal: 22, height: 56, elevation: 6, overflow: "hidden" },
  newChatText: { color: C.sendInk, fontSize: 16, fontWeight: "600" },
  threadContent: { paddingHorizontal: 18, paddingTop: 6, paddingBottom: 18 },
  userBubble: { alignSelf: "flex-end", maxWidth: "82%", backgroundColor: C.pill, borderRadius: R.bubble, borderBottomEndRadius: 8, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8, marginTop: 16 },
  blockedBubble: { borderWidth: 1, borderColor: C.bad },
  agentBlock: { alignSelf: "stretch", marginTop: 16 },
  userText: { color: C.ink, fontSize: 16, lineHeight: 23 },
  agentText: { color: C.ink, fontSize: 17, lineHeight: 27 },
  tick: { alignSelf: "flex-end", marginTop: -1, marginRight: -4 },
  attachmentTag: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 },
  attachmentText: { color: C.soft, fontSize: 13 },
  messageTime: { color: C.soft, fontSize: 12, marginTop: 6 },
  waitText: { color: C.mute, fontSize: 12, textAlign: "right", marginTop: 4 },
  blocked: { alignItems: "flex-end", gap: 8, marginTop: 6 },
  blockedText: { color: C.bad, fontSize: 13, textAlign: "right" },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: C.pill, borderRadius: R.pill, paddingHorizontal: 16, height: 44 },
  chipText: { color: C.ink, fontSize: 14 },
  working: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 18 },
  workingText: { flex: 1, color: C.soft, fontSize: 14 },
  proof: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 },
  proofText: { color: C.mute, fontSize: 12 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 6, backgroundColor: C.raised, borderRadius: 26, borderWidth: 1, borderColor: C.line, marginHorizontal: 12, marginBottom: 10, marginTop: 6, padding: 6, minHeight: 60 },
  plus: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  draftInput: { flex: 1, color: C.ink, fontSize: 16, lineHeight: 22, maxHeight: 132, minHeight: 48, paddingHorizontal: 4, paddingTop: 13, paddingBottom: 13, textAlignVertical: "center" },
  sendButton: { width: 48, height: 48, borderRadius: 24, backgroundColor: C.send, alignItems: "center", justifyContent: "center" },
  sendOff: { backgroundColor: C.pill },
});
