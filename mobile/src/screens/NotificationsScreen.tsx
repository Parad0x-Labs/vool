import {notificationText, tr} from "../i18n/index.ts";
import React, {useCallback, useEffect, useState} from "react";
import {ActivityIndicator, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View} from "react-native";
import type {MobileCompanionClient} from "../protocol/client.ts";
import type {NotificationEvent, NotificationPreferences} from "../protocol/models.ts";

const LABELS: Record<string, string> = {
  task_completed: "Task finished", task_failed: "Task failed", task_pending_approval: "VOOL needs an approval",
  task_cancelled: "Task stopped", payment_pending: "Payment waiting for approval", payment_done: "Payment done",
  payment_refused: "Payment refused or failed", desktop_inbox: "New item in your computer’s inbox", calendar_alert: "Calendar alert",
  calendar_catch_up: "Missed calendar alerts", reminder: "Reminder", model_offer: "Model price offer",
  model_market: "New model available", market_watch: "Market watch",
};
interface Props {client: MobileCompanionClient; onOpen(event: NotificationEvent): void;
  osEnabled?: boolean; enableOs?: () => Promise<boolean>; disableOs?: () => Promise<void>;}

export default function NotificationsScreen({client, onOpen, osEnabled=false, enableOs, disableOs}: Props) {
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const [mandatory, setMandatory] = useState<string[]>([]);
  const [allowed, setAllowed] = useState(false);
  const [events, setEvents] = useState<NotificationEvent[]>([]);
  const [older, setOlder] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [start, setStart] = useState("22:00");
  const [end, setEnd] = useState("07:00");
  const refresh = useCallback(async () => {
    const [settings, feed] = await Promise.all([client.notificationPreferences(), client.notificationFeed()]);
    setPreferences(settings.preferences); setMandatory(settings.mandatory); setAllowed(settings.alerts_allowed);
    setStart(settings.preferences.quiet.start); setEnd(settings.preferences.quiet.end); setEvents(feed.events);
    setOlder(feed.next_before || 0);
  }, [client]);
  useEffect(() => {void refresh().catch(() => setNotice(tr("Could not complete this. Check your computer.")));}, [refresh]);
  const save = async (change: Record<string, unknown>) => {
    setBusy(true); setNotice("");
    try {await client.setNotificationPreferences(change); await refresh();}
    catch (_) {setNotice(tr("Could not complete this. Check your computer."));}
    finally {setBusy(false);}
  };
  if (!preferences) return <View><ActivityIndicator color="#7d8794" />{notice ? <Text style={styles.note}>{notice}</Text> : null}</View>;
  const row = (label: string, value: boolean, changed: (value: boolean) => void, fixed = false) => (
    <View style={styles.row}><Text style={styles.text}>{tr(label)}</Text><Switch accessibilityLabel={tr(label)}
      value={value} disabled={busy || fixed} onValueChange={changed} /></View>
  );
  return <ScrollView contentContainerStyle={styles.root}>
    <Text style={styles.title}>{tr("Notifications")}</Text>
    {row(tr("Phone notifications"), osEnabled, value => {void (async () => {
      try {
        if (value) {if (!await enableOs?.()) setNotice(tr("Phone notifications are blocked or unavailable. Check your phone’s settings."));}
        else await disableOs?.();
      } catch {setNotice(tr("Could not save this choice. Switch off notifications in your phone’s settings."));}
    })();})}
    <Text style={styles.note}>{tr("Your phone can mute or delay notifications. This feed keeps the record.")}</Text>
    {!allowed ? <Text style={styles.note}>{tr("Alerts are paused for this phone on your computer.")}</Text> : null}
    <View style={styles.presets}>{[["everything", "Everything"], ["important", "Important only"], ["off", "Off"]].map(([value, label]) =>
      <Pressable key={value} disabled={busy} onPress={() => void save({preset: value})}
        style={[styles.button, preferences.preset === value && styles.chosen]}>
        <Text style={styles.text}>{tr(label)}</Text>
      </Pressable>)}</View>
    {Object.entries(preferences.types).map(([kind, value]) => <View key={kind} style={styles.group}>
      {row(LABELS[kind] || kind, value.enabled,
        enabled => void save({types: {[kind]: {enabled}}}))}
      {mandatory.includes(kind) ? <Text style={styles.note}>{tr("Payment records always stay in this feed.")}</Text> : null}
      {row(tr("Details on lock screen"), value.details, details => void save({types: {[kind]: {details}}}))}
    </View>)}
    <Text style={styles.title}>{tr("Quiet hours")}</Text>
    {row(tr("Quiet hours"), preferences.quiet.enabled, enabled => void save({quiet: {enabled, offset_minutes: -new Date().getTimezoneOffset()}}))}
    <View style={styles.row}><TextInput accessibilityLabel={tr("Quiet hours start")} value={start} onChangeText={setStart} style={styles.input} />
      <TextInput accessibilityLabel={tr("Quiet hours end")} value={end} onChangeText={setEnd} style={styles.input} />
      <Pressable disabled={busy} style={styles.button} onPress={() => void save({quiet: {start, end, offset_minutes: -new Date().getTimezoneOffset()}})}>
        <Text style={styles.text}>{tr("Save")}</Text></Pressable></View>
    {row(tr("Allow approvals through"), preferences.quiet.approvals_through, approvals_through => void save({quiet: {approvals_through}}))}
    {row(tr("Allow payments through"), preferences.quiet.payments_through, payments_through => void save({quiet: {payments_through}}))}
    {notice ? <Text style={styles.note}>{notice}</Text> : null}
    <Text style={styles.title}>{tr("Recent alerts")}</Text>
    {events.length === 0 ? <Text style={styles.note}>{tr("No alerts here yet.")}</Text> : events.map(event =>
      <Pressable key={String(event.seq)} style={styles.group} onPress={() => onOpen(event)}>
        <Text style={styles.text}>{notificationText(event)}</Text>
        {typeof event.detail === "string" ? <Text style={styles.note}>{event.detail}</Text> : null}
      </Pressable>)}
    {older > 0 && <Pressable style={styles.button} disabled={busy} onPress={() => {void (async () => {
      setBusy(true);
      try {
        const page = await client.notificationFeed(older);
        setEvents(previous => [...page.events, ...previous]); setOlder(page.next_before || 0);
      } catch {setNotice(tr("Could not complete this. Check your computer."));}
      finally {setBusy(false);}
    })();}}><Text style={styles.text}>{tr("Earlier alerts")}</Text></Pressable>}
  </ScrollView>;
}

const styles = StyleSheet.create({
  root: {paddingBottom: 32}, title: {color: "#e8ecf1", fontSize: 20, fontWeight: "600", marginVertical: 14},
  row: {flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10, paddingVertical: 6},
  text: {color: "#e8ecf1", fontSize: 14, flexShrink: 1}, note: {color: "#aeb9c4", fontSize: 12, lineHeight: 18, marginVertical: 6},
  group: {paddingVertical: 12, borderBottomColor: "#28313b", borderBottomWidth: 1}, presets: {flexDirection: "row", gap: 8},
  button: {padding: 10, borderRadius: 8, backgroundColor: "#28313b"}, input: {color: "#e8ecf1", backgroundColor: "#12161b", padding: 10, minWidth: 65},
  chosen: {borderWidth: 1, borderColor: "#aeb9c4"},
});
