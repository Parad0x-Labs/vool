import {errorText, rightToLeft, tr} from "../i18n/index.ts";
import {chatUpdatedText, messageTimeText, stepTimeText} from "../i18n/time.ts";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, AppState, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import PhoneAbilities from "./PhoneAbilities.tsx";
import FromComputer from "./FromComputer.tsx";
import SentFileCard from "../ui/SentFileCard.tsx";
import {filesByAnswer} from "../chat/sentFiles.ts";
import Header from "../ui/Header.tsx";
import {useLink} from "../ui/LinkBadge.tsx";
import {linkState} from "../protocol/linkState.ts";
import Icon, {type IconName} from "../ui/Icon.tsx";
import Gears from "../ui/Gears.tsx";
import Face from "../ui/Face.tsx";
import {C, R} from "../ui/theme.ts";
import {LIST_PANE, THREAD_WIDTH, useWide} from "../ui/wide.ts";
import SwipeRow from "../ui/SwipeRow.tsx";
import Sheet from "../ui/Sheet.tsx";

import { CompanionError, type MobileCompanionClient } from "../protocol/client.ts";
import type { ChatMessage, ChatSummary, PendingApproval, SentFile } from "../protocol/models.ts";
import { outboxFor, type QueuedTurn } from "../protocol/outbox.ts";
import { randomBytes, toHex } from "../protocol/sha256.ts";

interface Props {
  client: MobileCompanionClient;
  onRevoked: () => void | Promise<void>;
  /** A chat picked outside this screen (an alert). `seq` changes on every pick. */
  pick?: {id: string | null; seq: number} | null;
  onLink: () => void;
  /** The recent chats, once the computer has listed them. */
  onChats: (chats: ChatSummary[]) => void;
  /** Tells the app when one chat is open, so the tabs make room for the message box. */
  onThread?: (open: boolean) => void;
  /** Opens the private camera. */
  onCamera?: () => void;
  onVault?: () => void;
}

/** The chat list, and one chat: read it, write to the computer, attach a photo or file. */
// A first tap for someone who does not know what to ask. It fills the message box and does not send.
const STARTERS: [IconName, () => string][] = [
  ["help-circle-outline", () => tr("What can you do for me?")],
  ["folder-search-outline", () => tr("Find a file on my computer")],
  ["pencil-outline", () => tr("Help me write a message")],
  ["lightbulb-on-outline", () => tr("Plan my week")],
];

export default function ChatsScreen({ client, onRevoked, pick, onLink, onChats, onThread, onCamera, onVault }: Props) {
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
  // Files the computer sent; each shows in the answer that sent it.
  const [sentFiles, setSentFiles] = useState<SentFile[]>([]);
  // A question this chat's turn stopped on (an approval): shown under the answer, never only on the computer.
  const [asking, setAsking] = useState<PendingApproval | null>(null);
  const [askNote, setAskNote] = useState("");
  const [askBusy, setAskBusy] = useState(false);
  // Managing chats: the archived list, the hold menu, renaming, and the short Undo after an archive.
  const [showArchived, setShowArchived] = useState(false);
  const [menuFor, setMenuFor] = useState<ChatSummary | null>(null);
  const [renaming, setRenaming] = useState<ChatSummary | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [undo, setUndo] = useState<{chat: ChatSummary; text: string} | null>(null);
  const hadHistory = useRef(false);
  const activeRef = useRef(activeChat);
  activeRef.current = activeChat;
  const scroll = useRef<ScrollView>(null);
  const chatsRef = useRef(onChats);
  chatsRef.current = onChats;
  const threadRef = useRef(onThread);
  threadRef.current = onThread;
  const inThread = activeChat !== null;
  // On a wide screen the list stays beside the chat, and the tabs stay too.
  const {width, tablet, twoPane} = useWide();
  useEffect(() => {threadRef.current?.(inThread && !twoPane);}, [inThread, twoPane]);

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
        ? tr("On your computer, open Settings, then Phone and chat apps, choose what this phone may do and press Done.")
        : linkState() === "connecting" ? "" : tr("Can't reach your computer. Your saved messages will wait here."));
    }
  }, [client, guard, search]);

  useEffect(() => {
    let stop = false;
    void client.listSentFiles().then(files => {if (!stop) setSentFiles(files);}).catch(() => {});
    return () => {stop = true;};
  }, [client, filesKey, activeChat]);
  const placedFiles = useMemo(() => filesByAnswer(messages, sentFiles), [messages, sentFiles]);
  const shownInline = useMemo(() => new Set([...placedFiles.values()].flat().map(file => file.id)), [placedFiles]);

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
        setMessages(previous => [...previous, {role: "user", content: turn.text},
          ...(result.reply ? [{role: "assistant", content: result.reply, ts: new Date().toISOString()}] : [])]);
        setAsking(result.approval || null); setAskNote("");
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
      setMessages([]); setProof(""); setTimeShown(null); setAbilityNote(""); setAsking(null); setAskNote("");
      try {
        const history=await client.chatHistory(chatId);
        if (activeRef.current === chatId) setMessages(history);
        const turns = history.map(m => String(m.request_id || "")).filter(Boolean);
        if (turns.length) void client.pendingApprovals().then(list => {
          const open = list.find(a => !!a.task_id && turns.some(id => id.endsWith(String(a.task_id))));
          if (open && activeRef.current === chatId) setAsking(open);
        }).catch(() => {});
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
    setMessages([]); setProof(""); setTimeShown(null); setAbilityNote(""); setAsking(null); setAskNote("");
  }, []);

  // Changes show at once; if the computer refuses, the list goes back and says so.
  const patchChat = useCallback((chatId: string, patch: Partial<ChatSummary> | null) => {
    setChats(previous => previous && (patch ? previous.map(chat => chat.chat_id === chatId ? {...chat, ...patch} : chat)
      : previous.filter(chat => chat.chat_id !== chatId)));
  }, []);
  const setArchived = useCallback(async (chat: ChatSummary, on: boolean, offerUndo = true) => {
    patchChat(chat.chat_id, {archived: on});
    if (activeRef.current === chat.chat_id && on) {setActiveChat(null); activeRef.current = null;}
    if (offerUndo) setUndo({chat, text: on ? tr("Chat archived.") : tr("Chat restored.")});
    try {await client.archiveChat(chat.chat_id, on);}
    catch (exc) {
      patchChat(chat.chat_id, {archived: !on}); setUndo(null);
      if (!guard(exc)) setNotice(tr("Could not change this chat. Check your computer."));
    }
  }, [client, guard, patchChat]);
  const rename = useCallback(async () => {
    const chat = renaming; const title = newTitle.replace(/\s+/g, " ").trim();
    setRenaming(null);
    if (!chat || !title || title === chat.title) return;
    patchChat(chat.chat_id, {title});
    try {await client.renameChat(chat.chat_id, title);}
    catch (exc) {
      patchChat(chat.chat_id, {title: chat.title});
      if (!guard(exc)) setNotice(tr("Could not change this chat. Check your computer."));
    }
  }, [client, guard, newTitle, patchChat, renaming]);
  const remove = useCallback((chat: ChatSummary) => {
    Alert.alert(tr("Delete this chat?"), tr("It is deleted on your computer too, with everything VOOL kept from it. This can't be undone."), [
      {text: tr("Cancel"), style: "cancel"},
      {text: tr("Delete"), style: "destructive", onPress: () => void (async () => {
        try {
          await client.deleteChat(chat.chat_id);
          patchChat(chat.chat_id, null);
          if (activeRef.current === chat.chat_id) {setActiveChat(null); activeRef.current = null;}
        } catch (exc) {
          if (!guard(exc)) setNotice(tr("Could not change this chat. Check your computer."));
        }
      })()},
    ]);
  }, [client, guard, patchChat]);
  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(() => setUndo(null), 5000);
    return () => clearTimeout(timer);
  }, [undo]);

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

  const answer = useCallback(async (decision: "allow" | "deny") => {
    const question = asking; const chatId = activeRef.current;
    if (!question || !chatId) return;
    setAskBusy(true);
    try {
      const done = await client.answerApproval(question.approval_id, decision, "once");
      setAsking(null);
      if (decision === "deny") {setAskNote(tr("Denied. Nothing was done.")); return;}
      setAskNote(tr("Allowed. Your computer is working on it…"));
      // The answer is written to this chat on the computer; read the chat until it is there (one read at a time).
      const before = messages.filter(m => m.role === "assistant").length;
      const until = Date.now() + 5 * 60_000;
      while (done.resumed && Date.now() < until && activeRef.current === chatId) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        const history = await client.chatHistory(chatId).catch(() => null);
        if (history && history.filter(m => m.role === "assistant").length > before) {
          if (activeRef.current === chatId) {setMessages(history); setAskNote("");}
          return;
        }
      }
      if (activeRef.current === chatId) setAskNote(tr("Allowed. The answer will be in this chat when your computer has it."));
    } catch (exc) {
      if (!guard(exc)) setAskNote(errorText(exc));
    } finally {
      setAskBusy(false);
    }
  }, [asking, client, guard, messages]);

  const archivedCount = (chats || []).filter(chat => chat.archived).length;
  const shown = (chats || []).filter(chat => !!chat.archived === showArchived);
  const listView = (
      <View style={styles.screen}>
        {showArchived ? <Header title={tr("Archived")} onBack={() => setShowArchived(false)} onLink={onLink} />
          : <Header title={tr("Chats")} onLink={onLink} onCamera={onCamera} onVault={onVault} />}
        <ScrollView style={styles.fill} contentContainerStyle={styles.listContent} keyboardShouldPersistTaps="handled">
          {(chats?.length || search) ? <View style={styles.search}>
            <Icon name="magnify" size={20} color={C.mute} />
            <TextInput style={styles.searchInput} placeholder={tr("Search recent chats")} placeholderTextColor={C.mute}
              value={search} onChangeText={setSearch} />
          </View> : null}
          {notice ? <Text style={styles.note}>{notice}</Text> : null}
          {queued.length ? <Pressable onPress={() => {const first = queued.find(turn => turn.state === "blocked") || queued[0];
              // Its chat may not exist on the computer yet: show the saved message itself, not a load error.
              setActiveChat(first.chatId); activeRef.current = first.chatId; setMessages([]); setProof(""); setNotice("");}}
            accessibilityRole="button" hitSlop={6}>
            <Text style={styles.note}>{queued.some(turn => turn.state === "blocked")
              ? tr("A message was not sent. Tap to see it.") : tr("Saved messages: {count}", {count: queued.length})}</Text>
          </Pressable> : null}
          <FromComputer client={client} refreshKey={filesKey} />
          {chats === null && !notice ? <ActivityIndicator style={styles.center} color={C.mute} /> : null}
          {showArchived && !shown.length && chats !== null ? <Text style={styles.note}>{tr("No archived chats.")}</Text> : null}
          {!showArchived && shown.length === 0 && !search && !notice && chats !== null ? <View style={styles.empty}>
            <Face size={104} />
            <Text style={styles.emptyTitle}>{tr("What can I help with?")}</Text>
            <View style={styles.starters}>
              {STARTERS.map(([icon, text]) => <Pressable key={icon} style={styles.starter} android_ripple={{color: C.ripple}} accessibilityRole="button"
                onPress={() => {newChat(); setDraft(text());}}>
                <Icon name={icon} size={22} color={C.acc} />
                <Text style={styles.starterText} numberOfLines={2}>{text()}</Text>
                <Icon name={rightToLeft() ? "chevron-left" : "chevron-right"} size={20} color={C.mute} />
              </Pressable>)}
            </View>
          </View> : null}
          {shown.map((chat) => (
            <SwipeRow key={chat.chat_id} label={showArchived ? tr("Restore") : tr("Archive")} icon={showArchived ? "archive-arrow-up-outline" : "archive-arrow-down-outline"}
              onSwipe={() => void setArchived(chat, !showArchived)}>
            <Pressable style={styles.chatRow} android_ripple={{color: C.ripple}} onPress={() => void openChat(chat.chat_id)}
              onLongPress={() => setMenuFor(chat)} delayLongPress={350} accessibilityRole="button">
              <View style={styles.chatMark}><Text style={styles.chatLetter}>{Array.from((chat.title || tr("New chat")).trim())[0]?.toLocaleUpperCase() || "•"}</Text></View>
              <View style={styles.fill}>
                <Text style={[styles.chatTitle, twoPane && chat.chat_id === activeChat && styles.chatTitleOn]} numberOfLines={1}>
                  {chat.title || tr("New chat")}
                </Text>
                <Text style={styles.chatMeta}>
                  {tr("Replies: {count}", {count: chat.turn_count ?? 0})}{chatUpdatedText(chat.updated_at) ? " · " + chatUpdatedText(chat.updated_at) : ""}
                </Text>
              </View>
            </Pressable>
            </SwipeRow>
          ))}
          {!showArchived && archivedCount > 0 ? <Pressable style={styles.archivedRow} android_ripple={{color: C.ripple}} onPress={() => setShowArchived(true)} accessibilityRole="button">
            <Icon name="archive-outline" size={20} color={C.mute} />
            <Text style={styles.archivedText}>{tr("Archived ({count})", {count: archivedCount})}</Text>
            <Icon name={rightToLeft() ? "chevron-left" : "chevron-right"} size={20} color={C.mute} />
          </Pressable> : null}
        </ScrollView>
        {undo ? <View style={styles.undoBar}>
          <Text style={styles.undoText} numberOfLines={1}>{undo.text}</Text>
          <Pressable onPress={() => {const last = undo; setUndo(null); void setArchived(last.chat, !!last.chat.archived, false);}} hitSlop={8} accessibilityRole="button">
            <Text style={styles.undoAction}>{tr("Undo")}</Text>
          </Pressable>
        </View> : null}
        <Sheet open={menuFor !== null} onClose={() => setMenuFor(null)} actions={menuFor ? [
          {key: "rename", label: tr("Rename"), icon: "pencil-outline", onPress: () => {setNewTitle(menuFor.title || ""); setRenaming(menuFor);}},
          menuFor.archived
            ? {key: "restore", label: tr("Restore"), icon: "archive-arrow-up-outline", onPress: () => void setArchived(menuFor, false)}
            : {key: "archive", label: tr("Archive"), icon: "archive-arrow-down-outline", onPress: () => void setArchived(menuFor, true)},
          {key: "delete", label: tr("Delete"), icon: "trash-can-outline", onPress: () => remove(menuFor)},
        ] : []} />
        <Modal visible={renaming !== null} transparent animationType="fade" onRequestClose={() => setRenaming(null)} statusBarTranslucent>
          <View style={styles.dialogScrim}>
            <View style={styles.dialog}>
              <Text style={styles.dialogTitle}>{tr("Rename chat")}</Text>
              <TextInput style={styles.dialogInput} value={newTitle} onChangeText={setNewTitle} autoFocus maxLength={200}
                selectTextOnFocus returnKeyType="done" onSubmitEditing={() => void rename()} placeholderTextColor={C.mute} />
              <View style={styles.dialogRow}>
                <Pressable style={styles.chip} onPress={() => setRenaming(null)}><Text style={styles.chipText}>{tr("Cancel")}</Text></Pressable>
                <Pressable style={[styles.chip, styles.chipMain]} onPress={() => void rename()} disabled={!newTitle.trim()}>
                  <Text style={[styles.chipText, styles.chipMainText]}>{tr("Save")}</Text></Pressable>
              </View>
            </View>
          </View>
        </Modal>
        {showArchived ? null : <Pressable style={styles.newChat} android_ripple={{color: C.rippleDark}} onPress={newChat} accessibilityRole="button">
          <Icon name="plus" size={20} color={C.sendInk} />
          <Text style={styles.newChatText}>{tr("New chat")}</Text>
        </Pressable>}
      </View>
  );

  const title = (chats || []).find(chat => chat.chat_id === activeChat)?.title || tr("New chat");
  const waiting = queued.filter(turn => turn.chatId === activeChat);
  const sending = busy ? outbox.activeKey : null;
  const canSend = !busy && (draft.trim().length > 0 || attachmentIds.length > 0);
  // Messages and the message box keep to a readable width on a wide screen.
  const paneWidth = twoPane ? width - LIST_PANE : width;
  const column = paneWidth > THREAD_WIDTH ? {width: THREAD_WIDTH, alignSelf: "center" as const} : null;

  const threadView = (
    <View style={styles.screen}>
      <Header title={title} onBack={() => setActiveChat(null)} onLink={onLink} />
      {notice ? <Text style={[styles.note, styles.inset]}>{notice}</Text> : null}
      <ScrollView ref={scroll} style={styles.fill} contentContainerStyle={[styles.threadContent, column]}
        onContentSizeChange={() => scroll.current?.scrollToEnd({animated: true})} keyboardShouldPersistTaps="handled">
        <FromComputer client={client} refreshKey={filesKey} hide={shownInline} />
        {messages.map((m, i) => (
          <Pressable key={i} style={m.role === "user" ? styles.userBubble : styles.agentBlock}
            onPress={() => setTimeShown(shown => shown === i ? null : i)}
            onLongPress={() => setTimeShown(i)}>
            {m.content || m.role === "user" ? <Text style={m.role === "user" ? styles.userText : styles.agentText}>{m.content}</Text>
              : <Text style={styles.noteInline}>{m.answer_state === "unavailable_by_policy" ? tr("This answer was removed.")
                : tr("No answer came back. Send it again.")}</Text>}
            {m.attachments?.map((a) => (
              <View key={a.id} style={styles.attachmentTag}>
                <Icon name="paperclip" size={14} color={m.role === "user" ? C.sendSoft : C.mute} />
                <Text style={[styles.attachmentText, m.role === "user" && styles.onSend]}>{a.name}</Text>
              </View>
            ))}
            {placedFiles.get(i)?.map(file => <SentFileCard key={file.id} client={client} file={file} />)}
            {m.role === "user" ? <View style={styles.tick}><Icon name="check-all" size={15} color={C.sendSoft} /></View> : null}
            {timeShown === i && messageTimeText(m.ts) ? <Text style={[styles.messageTime, m.role === "user" && styles.onSend]}>{messageTimeText(m.ts)}</Text> : null}
          </Pressable>
        ))}
        {waiting.map(turn => {
          const blocked = turn.state === "blocked";
          const isSending = sending === turn.key;
          const got = isSending && received === turn.key;
          return (
            <View key={turn.key}>
              <View style={[styles.userBubble, blocked && styles.blockedBubble]}>
                <Text style={[styles.userText, blocked && styles.blockedUserText]}>{turn.text}</Text>
                <View style={styles.tick}>
                  <Icon name={blocked ? "alert-circle-outline" : got ? "check-all" : isSending ? "check" : "clock-outline"}
                    size={15} color={blocked ? C.bad : got ? C.sendInk : C.sendSoft} />
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
        {asking ? <View style={styles.ask}>
          <View style={styles.row}>
            <Icon name="shield-alert-outline" size={18} color={C.acc} />
            <Text style={styles.askTitle}>{tr("Waiting for your OK")}</Text>
          </View>
          <Text style={styles.askText}>{asking.action_plain || asking.intent || tr("VOOL needs an approval")}</Text>
          {(asking.affected_resources || []).slice(0, 3).map(r => <Text key={r} style={styles.noteInline} numberOfLines={1}>{r}</Text>)}
          {asking.phone_may_answer ? <View style={styles.row}>
            <Pressable style={styles.chip} disabled={askBusy} onPress={() => void answer("deny")}>
              <Text style={styles.chipText}>{tr("Deny")}</Text></Pressable>
            <Pressable style={[styles.chip, styles.chipMain]} disabled={askBusy} onPress={() => void answer("allow")}>
              <Text style={[styles.chipText, styles.chipMainText]}>{tr("Allow once")}</Text></Pressable>
          </View> : <Text style={styles.noteInline}>{tr("Allow or deny this in VOOL on your computer.")}</Text>}
        </View> : null}
        {askNote ? <Text style={styles.note}>{askNote}</Text> : null}
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
      <View style={[styles.composer, column && {width: THREAD_WIDTH - 24, alignSelf: "center"}]}>
        <Pressable style={styles.plus} onPress={() => setSheet(true)} hitSlop={6} accessibilityRole="button" accessibilityLabel={tr("Add to this message")}>
          <Icon name="plus" size={24} color={C.acc} />
        </Pressable>
        <TextInput
          style={styles.draftInput}
          placeholder={tr("Message your computer…")}
          placeholderTextColor={C.mute}
          value={draft}
          onChangeText={setDraft}
          multiline
          // On a tablet the keyboard's Return sends, as on a computer; on a phone it starts a new line.
          returnKeyType={tablet ? "send" : "default"}
          submitBehavior={tablet ? "submit" : "newline"}
          onSubmitEditing={tablet ? () => {if (canSend) void send();} : undefined}
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

  if (twoPane) return (
    <View style={[styles.screen, styles.panes]}>
      <View style={styles.listPane}>{listView}</View>
      <View style={styles.fill}>
        {activeChat ? threadView : <View style={styles.pickChat}>
          <Face size={104} />
          <Text style={styles.emptyTitle}>{tr("What can I help with?")}</Text>
          <Pressable style={styles.pickNew} android_ripple={{color: C.rippleDark}} onPress={newChat} accessibilityRole="button">
            <Icon name="plus" size={20} color={C.sendInk} />
            <Text style={styles.newChatText}>{tr("New chat")}</Text>
          </Pressable>
        </View>}
      </View>
    </View>
  );
  return activeChat ? threadView : listView;
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  ask: { gap: 8, padding: 14, marginVertical: 8, borderRadius: 16, borderWidth: 1, borderColor: C.acc, backgroundColor: C.raised },
  askTitle: { color: C.ink, fontSize: 15, fontWeight: "700" },
  askText: { color: C.ink, fontSize: 15, lineHeight: 21 },
  noteInline: { color: C.soft, fontSize: 13, lineHeight: 19, fontStyle: "italic" },
  fill: { flex: 1 },
  center: { marginTop: 40 },
  inset: { paddingHorizontal: 20 },
  row: { flexDirection: "row", gap: 8 },
  listContent: { paddingHorizontal: 20, paddingBottom: 96 },
  search: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: R.pill, paddingHorizontal: 16, height: 48, marginBottom: 10 },
  searchInput: { flex: 1, color: C.ink, fontSize: 16, padding: 0 },
  note: { color: C.soft, fontSize: 13, lineHeight: 19, paddingVertical: 8 },
  chatRow: { flexDirection: "row", alignItems: "center", gap: 14, minHeight: 70, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: C.line, overflow: "hidden" },
  chatMark: { width: 44, height: 44, borderRadius: 14, backgroundColor: C.tint, alignItems: "center", justifyContent: "center" },
  chatLetter: { color: C.acc, fontSize: 17, fontWeight: "700" },
  empty: { alignItems: "center", paddingTop: 28, paddingBottom: 24 },
  emptyTitle: { color: C.ink, fontSize: 25, lineHeight: 31, fontWeight: "700", letterSpacing: -0.4, textAlign: "center", marginTop: 10, marginBottom: 24 },
  starters: { alignSelf: "stretch", gap: 10 },
  starter: { flexDirection: "row", alignItems: "center", gap: 14, minHeight: 58, paddingHorizontal: 16, paddingVertical: 12, backgroundColor: C.raised, borderRadius: 18, borderWidth: 1, borderColor: C.line, overflow: "hidden" },
  starterText: { flex: 1, color: C.ink, fontSize: 15, lineHeight: 20 },
  chatTitle: { color: C.ink, fontSize: 16, fontWeight: "600", letterSpacing: -0.2 },
  chatTitleOn: { color: C.acc },
  archivedRow: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 56, paddingVertical: 12, overflow: "hidden" },
  archivedText: { flex: 1, color: C.soft, fontSize: 15, fontWeight: "500" },
  undoBar: { position: "absolute", left: 16, right: 16, bottom: 84, flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.raised,
    borderWidth: 1, borderColor: C.line, borderRadius: 16, paddingHorizontal: 16, minHeight: 52, elevation: 6 },
  undoText: { flex: 1, color: C.ink, fontSize: 14 },
  undoAction: { color: C.acc, fontSize: 15, fontWeight: "700" },
  dialogScrim: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", alignItems: "center", justifyContent: "center", padding: 24 },
  dialog: { width: "100%", maxWidth: 420, backgroundColor: C.panel, borderRadius: 22, padding: 20, gap: 14 },
  dialogTitle: { color: C.ink, fontSize: 18, fontWeight: "700" },
  dialogInput: { color: C.ink, fontSize: 16, backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 14, paddingHorizontal: 14, height: 50 },
  dialogRow: { flexDirection: "row", justifyContent: "flex-end", gap: 10 },
  chipMain: { backgroundColor: C.send, borderColor: C.send },
  chipMainText: { color: C.sendInk },
  panes: { flexDirection: "row" },
  listPane: { width: LIST_PANE, borderEndWidth: 1, borderEndColor: C.line },
  pickChat: { flex: 1, alignItems: "center", justifyContent: "center", padding: 32 },
  pickNew: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.send, borderRadius: R.pill, paddingHorizontal: 22, height: 54, overflow: "hidden" },
  chatMeta: { color: C.mute, fontSize: 13, marginTop: 3 },
  newChat: { position: "absolute", right: 20, bottom: 18, flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.send, borderRadius: R.pill, paddingHorizontal: 22, height: 54, elevation: 8,
    shadowColor: C.acc, shadowOpacity: 0.28, shadowRadius: 14, shadowOffset: {width: 0, height: 8}, overflow: "hidden" },
  newChatText: { color: C.sendInk, fontSize: 16, fontWeight: "600" },
  threadContent: { paddingHorizontal: 18, paddingTop: 8, paddingBottom: 18 },
  userBubble: { alignSelf: "flex-end", maxWidth: "82%", backgroundColor: C.send, borderRadius: R.bubble, borderBottomEndRadius: 6, paddingHorizontal: 15, paddingTop: 10, paddingBottom: 8, marginTop: 14 },
  blockedBubble: { backgroundColor: C.badBg, borderWidth: 1, borderColor: C.bad },
  agentBlock: { alignSelf: "flex-start", maxWidth: "88%", backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: R.bubble, borderBottomStartRadius: 6, paddingHorizontal: 15, paddingVertical: 11, marginTop: 14 },
  userText: { color: C.sendInk, fontSize: 16, lineHeight: 23 },
  blockedUserText: { color: C.ink },
  agentText: { color: C.ink, fontSize: 16, lineHeight: 24 },
  onSend: { color: C.sendSoft },
  tick: { alignSelf: "flex-end", marginTop: -1, marginRight: -4 },
  attachmentTag: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 },
  attachmentText: { color: C.soft, fontSize: 13 },
  messageTime: { color: C.mute, fontSize: 12, marginTop: 6 },
  waitText: { color: C.mute, fontSize: 12, textAlign: "right", marginTop: 4 },
  blocked: { alignItems: "flex-end", gap: 8, marginTop: 6 },
  blockedText: { color: C.bad, fontSize: 13, textAlign: "right" },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: C.pill, borderWidth: 1, borderColor: C.line, borderRadius: R.pill, paddingHorizontal: 16, height: 44 },
  chipText: { color: C.ink, fontSize: 14, fontWeight: "500" },
  working: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 18 },
  workingText: { flex: 1, color: C.soft, fontSize: 14 },
  proof: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 },
  proofText: { color: C.mute, fontSize: 12 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 6, backgroundColor: C.raised, borderRadius: 29, borderWidth: 1, borderColor: C.line, marginHorizontal: 12, marginBottom: 10, marginTop: 6, padding: 6, minHeight: 58 },
  plus: { width: 44, height: 44, borderRadius: 22, backgroundColor: C.tint, alignItems: "center", justifyContent: "center" },
  draftInput: { flex: 1, color: C.ink, fontSize: 16, lineHeight: 22, maxHeight: 132, minHeight: 44, paddingHorizontal: 6, paddingTop: 11, paddingBottom: 11, textAlignVertical: "center" },
  sendButton: { width: 44, height: 44, borderRadius: 22, backgroundColor: C.send, alignItems: "center", justifyContent: "center" },
  sendOff: { backgroundColor: C.pill },
});
