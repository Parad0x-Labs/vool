import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {ActivityIndicator, BackHandler, FlatList, Image, Pressable, StyleSheet, Text, useWindowDimensions, View} from "react-native";
import * as ScreenCapture from "expo-screen-capture";

import type {MobileCompanionClient} from "../protocol/client.ts";
import type {MediaItem} from "../protocol/models.ts";
import {language, rightToLeft, tr} from "../i18n/index.ts";
import {VaultStream, type StreamState} from "../vault/stream.ts";
import {trace} from "../diagnostics/trace.ts";
import {VaultPlayer, vaultNative} from "../vault/native.ts";
import Icon, {type IconName} from "../ui/Icon.tsx";
import {useEdges} from "../ui/Frame.tsx";
import {C} from "../ui/theme.ts";

type Section = "mobile" | "local";
type Kind = "photo" | "video" | "audio";
type Sort = "new" | "old" | "big" | "small";
type Size = "small" | "medium" | "large";

const SORTS: Sort[] = ["new", "old", "big", "small"];
const sortLabel = (sort: Sort) => ({new: tr("Newest first"), old: tr("Oldest first"), big: tr("Largest first"), small: tr("Smallest first")})[sort];
const SORT_QUERY: Record<Sort, {sort: "date" | "size"; order: "asc" | "desc"}> = {
  new: {sort: "date", order: "desc"}, old: {sort: "date", order: "asc"}, big: {sort: "size", order: "desc"}, small: {sort: "size", order: "asc"},
};
const SIZES: Size[] = ["small", "medium", "large"];
const COLUMNS: Record<Size, number> = {small: 4, medium: 3, large: 2};
const SIZE_ICON: Record<Size, IconName> = {small: "view-comfy-outline", medium: "view-grid-outline", large: "view-agenda-outline"};
const kindLabel = (kind: Kind) => ({photo: tr("Photos"), video: tr("Videos"), audio: tr("Sound")})[kind];
const KIND_ICON: Record<Kind, IconName> = {photo: "image-outline", video: "play-circle-outline", audio: "music-note-outline"};
const PAGE = 60;

function sizeText(bytes: number): string {
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} GB`;
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function dateText(seconds: number): string {
  if (!seconds) return "";
  try {return new Date(seconds * 1000).toLocaleDateString(language(), {year: "numeric", month: "short", day: "numeric"});}
  catch {return new Date(seconds * 1000).toDateString();}
}

/** Previews, kept in memory only while the Vault is open: the computer's, fetched three at a time; when it has none
 * (HEIC photos, videos, recordings), made here one at a time from a sealed stream and given to the computer to keep. */
const thumbs = new Map<string, string>();
const PREVIEW_EDGE = 360;
const PHOTO_PREVIEW_LIMIT = 96 * 1024 * 1024;

function lane(width: number) {
  let running = 0;
  const queue: Array<() => void> = [];
  return async function run<T>(job: () => Promise<T>): Promise<T> {
    if (running >= width) await new Promise<void>(resolve => queue.push(resolve));
    running += 1;
    try {return await job();} finally {running -= 1; queue.shift()?.();}
  };
}
const fetchLane = lane(3);
const makeLane = lane(1);

async function makePreview(client: MobileCompanionClient, item: MediaItem, edge: number): Promise<string> {
  if (!vaultNative || (item.kind === "photo" && item.size > PHOTO_PREVIEW_LIMIT)) return "";
  const started = Date.now();
  // A photo is read whole; a video or a recording only where the phone's reader looks (its index, one frame).
  const stream = new VaultStream(client, item, () => {}, item.kind !== "photo");
  try {
    const made = await vaultNative.preview(stream.id, edge, item.kind);
    trace("vault preview made", {kind: item.kind, ok: !!made, edge, ms: Date.now() - started});
    return made;
  } catch {
    trace("vault preview failed", {kind: item.kind, ms: Date.now() - started});
    return "";
  } finally {
    stream.close();
  }
}

async function thumbFor(client: MobileCompanionClient, item: MediaItem, stillWanted: () => boolean): Promise<string> {
  const known = thumbs.get(item.id);
  if (known !== undefined) return known;
  const fromComputer = await fetchLane(() => client.mediaThumb(item.id).catch(error => {
    if (String((error as {code?: string})?.code || "") === "media_no_preview") return "";
    throw error;
  }));
  if (fromComputer || item.damaged) {thumbs.set(item.id, fromComputer); return fromComputer;}
  if (!stillWanted()) return "";
  const made = await makeLane(() => (stillWanted() ? makePreview(client, item, PREVIEW_EDGE) : Promise.resolve("")));
  if (!made && !stillWanted()) return "";
  thumbs.set(item.id, made);
  if (made) void client.mediaSetThumb(item.id, made).catch(() => {});
  return made;
}

type ListState = "loading" | "ready" | "away" | "right_off";

/**
 * VOOL Vault: the computer's protected photos, videos and sound, as cards. Opening one streams it here sealed
 * (nothing plain is kept); saving or sharing a copy is a separate, explicit step with a warning. Shown only while
 * VOOL is unlocked; locking VOOL closes it and everything it holds.
 */
export default function VaultScreen({client, onClose}: {client: MobileCompanionClient; onClose: () => void}) {
  const edges = useEdges();
  const {width} = useWindowDimensions();
  const [section, setSection] = useState<Section>("mobile");
  const [kind, setKind] = useState<Kind>("photo");
  const [sort, setSort] = useState<Sort>("new");
  const [size, setSize] = useState<Size>("medium");
  const [items, setItems] = useState<MediaItem[]>([]);
  const [total, setTotal] = useState(0);
  const [state, setState] = useState<ListState>("loading");
  const [open, setOpen] = useState<MediaItem | null>(null);
  const query = useRef(0);
  const more = useRef(false);

  // Out of screenshots and the app switcher while the Vault is open; everything it holds goes when it closes.
  useEffect(() => {
    void ScreenCapture.preventScreenCaptureAsync("vool-vault").catch(() => {});
    return () => {
      void ScreenCapture.allowScreenCaptureAsync("vool-vault").catch(() => {});
      thumbs.clear();
      vaultNative?.closeAll();
    };
  }, []);

  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (open) setOpen(null); else onClose();
      return true;
    });
    return () => sub.remove();
  }, [open, onClose]);

  const load = useCallback(async (from: number) => {
    const asked = from === 0 ? ++query.current : query.current;
    if (from === 0) setState(previous => (previous === "ready" ? previous : "loading"));
    const started = Date.now();
    try {
      const page = await client.mediaList({section, kind, ...SORT_QUERY[sort], offset: from, limit: PAGE});
      trace("vault list", {section, kind, sort, from, got: page.items.length, total: page.total, ms: Date.now() - started});
      if (asked !== query.current) return;
      setItems(previous => (from === 0 ? page.items : [...previous, ...page.items.filter(item => !previous.some(p => p.id === item.id))]));
      setTotal(page.total);
      setState("ready");
    } catch (error) {
      trace("vault list failed", {code: String((error as {code?: string})?.code || "unreachable"), ms: Date.now() - started});
      if (asked !== query.current) return;
      setState(String((error as {code?: string})?.code || "") === "phone_right_off_or_expired" ? "right_off" : "away");
    } finally {
      more.current = false;
    }
  }, [client, section, kind, sort]);

  useEffect(() => {setItems([]); setTotal(0); setState("loading"); void load(0);}, [load]);

  const columns = COLUMNS[size];
  const gap = size === "small" ? 4 : 10;
  const cardWidth = Math.floor((width - 32 - gap * (columns - 1)) / columns);

  const header = <View style={[styles.top, {paddingTop: edges.top + 6}]}>
    <View style={styles.titleRow}>
      <Pressable onPress={onClose} hitSlop={10} style={styles.round} accessibilityRole="button" accessibilityLabel={tr("Back")}>
        <Icon name={rightToLeft() ? "chevron-right" : "chevron-left"} size={28} color={C.ink} />
      </Pressable>
      <Text style={styles.title} numberOfLines={1} accessibilityRole="header">{tr("VOOL Vault")}</Text>
      <Pressable onPress={() => setSort(SORTS[(SORTS.indexOf(sort) + 1) % SORTS.length])} style={styles.sort} accessibilityRole="button"
        accessibilityLabel={tr("Sort")} android_ripple={{color: C.ripple}}>
        <Icon name="sort" size={18} color={C.soft} /><Text style={styles.sortText} numberOfLines={1}>{sortLabel(sort)}</Text>
      </Pressable>
      <Pressable onPress={() => setSize(SIZES[(SIZES.indexOf(size) + 1) % SIZES.length])} hitSlop={6} style={styles.round}
        accessibilityRole="button" accessibilityLabel={tr("Card size")}>
        <Icon name={SIZE_ICON[size]} size={22} color={C.ink} />
      </Pressable>
    </View>
    <View style={styles.segment}>
      {(["mobile", "local"] as Section[]).map(name => <Pressable key={name} onPress={() => setSection(name)} accessibilityRole="tab"
        accessibilityState={{selected: section === name}} style={[styles.segmentItem, section === name && styles.segmentOn]}>
        <Text style={[styles.segmentText, section === name && styles.segmentTextOn]} numberOfLines={1}>
          {name === "mobile" ? tr("From your phone") : tr("From your computer")}</Text>
      </Pressable>)}
    </View>
    <View style={styles.chips}>
      {(["photo", "video", "audio"] as Kind[]).map(name => <Pressable key={name} onPress={() => setKind(name)} accessibilityRole="tab"
        accessibilityState={{selected: kind === name}} style={[styles.chip, kind === name && styles.chipOn]}>
        <Icon name={KIND_ICON[name]} size={16} color={kind === name ? C.sendInk : C.soft} />
        <Text style={[styles.chipText, kind === name && styles.chipTextOn]}>{kindLabel(name)}</Text>
      </Pressable>)}
    </View>
  </View>;

  let body: React.ReactNode;
  if (state === "loading") body = <View style={styles.center}><ActivityIndicator color={C.acc} /></View>;
  else if (state === "away" || state === "right_off") body = <View style={styles.center}>
    <Icon name={state === "away" ? "lan-disconnect" : "lock-outline"} size={34} color={C.mute} />
    <Text style={styles.message}>{state === "away" ? tr("Your computer is out of reach. The Vault opens when it answers.") :
      tr("To open the Vault, allow “See protected media (VOOL Vault)” for this phone in VOOL’s Settings on your computer.")}</Text>
    <Pressable style={styles.button} onPress={() => void load(0)} accessibilityRole="button" android_ripple={{color: C.rippleDark}}>
      <Text style={styles.buttonText}>{tr("Try again")}</Text></Pressable>
  </View>;
  else if (!items.length) body = <View style={styles.center}>
    <Icon name="safe-square-outline" size={34} color={C.mute} /><Text style={styles.message}>{tr("Nothing here yet.")}</Text></View>;
  else body = <FlatList key={`${size}-${section}-${kind}`} data={items} numColumns={columns} keyExtractor={item => item.id}
    contentContainerStyle={{paddingHorizontal: 16, paddingBottom: edges.bottom + 24, gap}} columnWrapperStyle={columns > 1 ? {gap} : undefined}
    onEndReachedThreshold={0.6} onEndReached={() => {
      if (more.current || items.length >= total) return;
      more.current = true; void load(items.length);
    }}
    renderItem={({item}) => <Card client={client} item={item} width={cardWidth} size={size} onOpen={() => {trace("vault item open", {kind: item.kind, kb: Math.round(item.size / 1024)}); setOpen(item);}} />} />;

  return <View style={[StyleSheet.absoluteFill, styles.screen]}>
    {header}
    {body}
    {open ? <Viewer client={client} item={open} onClose={() => setOpen(null)} /> : null}
  </View>;
}

function useThumb(client: MobileCompanionClient, item: MediaItem): string {
  const [thumb, setThumb] = useState(() => thumbs.get(item.id) || "");
  useEffect(() => {
    if (item.damaged) return;
    let alive = true;
    void thumbFor(client, item, () => alive).then(value => {if (alive) setThumb(value);}).catch(() => {});
    return () => {alive = false;};
  }, [client, item]);
  return thumb;
}

function Card({client, item, width, size, onOpen}: {client: MobileCompanionClient; item: MediaItem; width: number; size: Size; onOpen: () => void}) {
  const thumb = useThumb(client, item);
  const square = size === "large" ? width * 0.8 : width;
  return <Pressable onPress={onOpen} style={{width}} accessibilityRole="button" accessibilityLabel={item.name}>
    <View style={[styles.tile, {height: square, borderRadius: size === "small" ? 8 : 14}]}>
      {thumb ? <Image source={{uri: `data:image/jpeg;base64,${thumb}`}} style={StyleSheet.absoluteFill} resizeMode="cover" />
        : <Icon name={item.damaged ? "image-broken-variant" : KIND_ICON[item.kind]} size={size === "small" ? 22 : 30} color={C.mute} />}
      {item.kind === "video" && thumb ? <View style={styles.badge}><Icon name="play" size={14} color={C.ink} /></View> : null}
    </View>
    {size !== "small" ? <View style={styles.cardText}>
      {item.kind !== "photo" || size === "large" ? <Text style={styles.cardName} numberOfLines={1}>{item.name}</Text> : null}
      <Text style={styles.cardMeta} numberOfLines={1}>{item.damaged ? tr("Damaged") : `${dateText(item.taken_at || item.added_at)} · ${sizeText(item.size)}`}</Text>
    </View> : null}
  </Pressable>;
}

function Viewer({client, item, onClose}: {client: MobileCompanionClient; item: MediaItem; onClose: () => void}) {
  const edges = useEdges();
  const stream = useRef<VaultStream | null>(null);
  const [streamId, setStreamId] = useState("");
  const [progress, setProgress] = useState<StreamState>({held: 0, pieces: 1, failed: "", done: false});
  const [startFailed, setStartFailed] = useState(false);
  const [playFailed, setPlayFailed] = useState(false);
  // The player gave up while pieces were still on their way (a long break): start it again once more arrive.
  const [replay, setReplay] = useState({wanted: false, round: 0, at: 0});
  const [ask, setAsk] = useState<"save" | "share" | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const thumb = thumbs.get(item.id) || "";

  useEffect(() => {
    let opened: VaultStream | null = null;
    try {opened = new VaultStream(client, item, setProgress);} catch {setStartFailed(true);}
    stream.current = opened;
    setStreamId(opened?.id || "");
    return () => {opened?.close(); stream.current = null;};
  }, [client, item]);

  useEffect(() => {
    if (replay.wanted && progress.held > replay.at) setReplay(previous => ({wanted: false, round: previous.round + 1, at: 0}));
  }, [replay, progress.held]);
  const playerFailed = () => {
    if (progress.done) setPlayFailed(true);
    else setReplay(previous => ({...previous, wanted: true, at: progress.held}));
  };

  // The photo at screen size, made here from the sealed stream once it has all arrived (HEIC and 108 MP included).
  const {width: screenWidth, height: screenHeight} = useWindowDimensions();
  const [photo, setPhoto] = useState("");
  const [photoFailed, setPhotoFailed] = useState(false);
  useEffect(() => {
    if (item.kind !== "photo" || !progress.done || !streamId || !vaultNative) return;
    let alive = true;
    const started = Date.now();
    const edge = Math.min(4096, Math.round(Math.max(screenWidth, screenHeight) * 2));
    void vaultNative.preview(streamId, edge, "photo").then(made => {
      trace("vault photo shown", {ok: !!made, edge, ms: Date.now() - started});
      if (!alive) return;
      if (made) setPhoto(made); else setPhotoFailed(true);
    }).catch(() => {if (alive) setPhotoFailed(true);});
    return () => {alive = false;};
  }, [item.kind, progress.done, streamId, screenWidth, screenHeight]);

  const exportCopy = async (how: "save" | "share") => {
    const current = stream.current;
    if (!current || !vaultNative || busy) return;
    setBusy(true); setAsk(null); setNote("");
    // The computer keeps a receipt of every copy that leaves the Vault; the copy is made even when it is out of reach.
    void client.mediaExported(item.id, how).catch(() => {});
    try {
      if (how === "save") setNote(tr("Saved to {folder}.", {folder: await vaultNative.save(current.id, item.name)}));
      else await vaultNative.share(current.id, item.name, tr("Share"));
    } catch {
      setNote(tr("Could not make the copy. Try again."));
    } finally {
      setBusy(false);
    }
  };

  const percent = Math.floor((progress.held / Math.max(1, progress.pieces)) * 100);
  const line = startFailed ? tr("This phone cannot open the Vault.")
    : photoFailed ? tr("This item cannot be opened right now.")
    : playFailed ? tr("This phone cannot play this file.")
    : progress.failed === "away" ? tr("Your computer is out of reach. It carries on from here when it answers.")
    : progress.failed ? tr("This item cannot be opened right now.")
    : !progress.done ? tr("Arriving: {percent}%", {percent}) : note;

  return <View style={[StyleSheet.absoluteFill, styles.viewer]}>
    <View style={[styles.viewerTop, {paddingTop: edges.top + 6}]}>
      <Pressable onPress={onClose} hitSlop={10} style={styles.round} accessibilityRole="button" accessibilityLabel={tr("Close")}>
        <Icon name="close" size={24} color={C.ink} />
      </Pressable>
      <View style={styles.fill}>
        <Text style={styles.viewerName} numberOfLines={1}>{item.name}</Text>
        <Text style={styles.cardMeta} numberOfLines={1}>{`${dateText(item.taken_at || item.added_at)} · ${sizeText(item.size)}`}</Text>
      </View>
    </View>
    <View style={styles.stage}>
      {item.kind === "photo"
        ? (photo || thumb ? <Image source={{uri: `data:image/jpeg;base64,${photo || thumb}`}}
            style={[StyleSheet.absoluteFill, !photo && styles.dim]} resizeMode="contain" blurRadius={photo ? 0 : 6} /> : null)
        : streamId && VaultPlayer && !playFailed
          ? (replay.wanted ? null : <VaultPlayer key={replay.round} stream={streamId}
              style={item.kind === "audio" ? styles.audioPlayer : StyleSheet.absoluteFill} onPlayerError={event => {
                trace("vault player error", {code: event.nativeEvent?.message || "", held: progress.held, of: progress.pieces}); playerFailed();
              }} onPlayerReady={() => trace("vault player ready", {held: progress.held, of: progress.pieces})} />)
          : null}
      {item.kind === "audio" ? <View pointerEvents="none" style={styles.audioMark}><Icon name="music-note-outline" size={56} color={C.mute} /></View> : null}
      {item.kind === "photo" && !photo && !photoFailed && !progress.failed && !startFailed ? <ActivityIndicator style={styles.spinner} color={C.acc} /> : null}
    </View>
    <View style={[styles.viewerBottom, {paddingBottom: edges.bottom + 14}]}>
      {line ? <Text style={styles.line}>{line}</Text> : null}
      {ask ? <View style={styles.warn}>
        <Icon name="alert-outline" size={18} color={C.wait} />
        <Text style={styles.warnText}>{tr("This copy leaves the Vault, where other apps and backups can see it.")}</Text>
        <View style={styles.actions}>
          <Pressable style={styles.quiet} onPress={() => setAsk(null)} accessibilityRole="button"><Text style={styles.quietText}>{tr("Cancel")}</Text></Pressable>
          <Pressable style={styles.button} onPress={() => void exportCopy(ask)} accessibilityRole="button" android_ripple={{color: C.rippleDark}}>
            <Text style={styles.buttonText}>{tr("Continue")}</Text></Pressable>
        </View>
      </View> : <View style={styles.actions}>
        {(["save", "share"] as const).map(how => <Pressable key={how} disabled={!progress.done || busy} onPress={() => setAsk(how)}
          style={[styles.action, (!progress.done || busy) && styles.off]} accessibilityRole="button" android_ripple={{color: C.ripple}}>
          <Icon name={how === "save" ? "download-outline" : "share-variant-outline"} size={20} color={C.ink} />
          <Text style={styles.actionText}>{how === "save" ? tr("Save to phone") : tr("Share")}</Text>
        </Pressable>)}
      </View>}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  screen: {backgroundColor: C.bg},
  fill: {flex: 1},
  top: {paddingHorizontal: 16, paddingBottom: 10, gap: 12},
  titleRow: {flexDirection: "row", alignItems: "center", gap: 8},
  round: {width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center"},
  title: {flex: 1, color: C.ink, fontSize: 26, fontWeight: "700", letterSpacing: -0.4},
  sort: {flexDirection: "row", alignItems: "center", gap: 6, height: 36, paddingHorizontal: 12, borderRadius: 18, backgroundColor: C.pill, overflow: "hidden", maxWidth: 170},
  sortText: {color: C.soft, fontSize: 13, fontWeight: "600"},
  segment: {flexDirection: "row", backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 14, padding: 3},
  segmentItem: {flex: 1, height: 38, borderRadius: 11, alignItems: "center", justifyContent: "center", paddingHorizontal: 6},
  segmentOn: {backgroundColor: C.pill},
  segmentText: {color: C.mute, fontSize: 14, fontWeight: "600"},
  segmentTextOn: {color: C.ink},
  chips: {flexDirection: "row", gap: 8},
  chip: {flexDirection: "row", alignItems: "center", gap: 6, height: 36, paddingHorizontal: 14, borderRadius: 18, borderWidth: 1, borderColor: C.line},
  chipOn: {backgroundColor: C.send, borderColor: C.send},
  chipText: {color: C.soft, fontSize: 14, fontWeight: "600"},
  chipTextOn: {color: C.sendInk},
  center: {flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 32},
  message: {color: C.soft, fontSize: 15, lineHeight: 22, textAlign: "center"},
  button: {backgroundColor: C.send, borderRadius: 22, height: 44, paddingHorizontal: 22, alignItems: "center", justifyContent: "center", overflow: "hidden"},
  buttonText: {color: C.sendInk, fontSize: 15, fontWeight: "600"},
  tile: {backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, overflow: "hidden", alignItems: "center", justifyContent: "center"},
  badge: {position: "absolute", right: 6, bottom: 6, width: 24, height: 24, borderRadius: 12, backgroundColor: "rgba(11,13,17,0.7)", alignItems: "center", justifyContent: "center"},
  cardText: {paddingTop: 6, paddingHorizontal: 2},
  cardName: {color: C.ink, fontSize: 13, fontWeight: "600"},
  cardMeta: {color: C.mute, fontSize: 12, marginTop: 1},
  viewer: {backgroundColor: "#000"},
  viewerTop: {flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 8, paddingBottom: 8, backgroundColor: C.bg},
  viewerName: {color: C.ink, fontSize: 15, fontWeight: "600"},
  stage: {flex: 1, alignItems: "center", justifyContent: "center"},
  dim: {opacity: 0.55},
  spinner: {position: "absolute"},
  audioPlayer: {position: "absolute", left: 0, right: 0, bottom: 0, height: 120},
  audioMark: {position: "absolute", top: "35%"},
  viewerBottom: {paddingHorizontal: 16, paddingTop: 12, gap: 10, backgroundColor: C.bg},
  line: {color: C.soft, fontSize: 13, textAlign: "center"},
  actions: {flexDirection: "row", gap: 10, justifyContent: "center"},
  action: {flex: 1, flexDirection: "row", gap: 8, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center", backgroundColor: C.pill, overflow: "hidden"},
  actionText: {color: C.ink, fontSize: 15, fontWeight: "600"},
  off: {opacity: 0.4},
  warn: {backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 14, gap: 10},
  warnText: {color: C.ink, fontSize: 14, lineHeight: 20},
  quiet: {height: 44, paddingHorizontal: 18, alignItems: "center", justifyContent: "center"},
  quietText: {color: C.soft, fontSize: 15, fontWeight: "600"},
});
