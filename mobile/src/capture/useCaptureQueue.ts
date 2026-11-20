import {useCallback, useEffect, useRef, useState} from "react";
import {AppState} from "react-native";
import type {MobileCompanionClient} from "../protocol/client.ts";
import {capture, vault} from "./native.ts";
import {sendWaiting, type SendProblem} from "./sender.ts";

const RETRY_MS = 30_000;

/** Keeps sending what the private capture holds: now, when the app comes back, and every 30 s while anything waits. */
export function useCaptureQueue(client: MobileCompanionClient) {
  const [waiting, setWaiting] = useState(0);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<SendProblem>("");
  // captures still being locked away (optional compression can take a minute) and whether the last one failed
  const [saving, setSaving] = useState(() => capture?.savingCount() ?? 0);
  const [saveFailed, setSaveFailed] = useState(false);
  const alive = useRef(true);
  const run = useCallback(async () => {
    if (!vault) return;
    setSending(true);
    try {
      const report = await sendWaiting(vault, client);
      if (!alive.current) return;
      setWaiting(report.waiting);
      setProblem(report.problem);
    } catch {
      if (alive.current) setProblem("failed");
    } finally {
      if (alive.current) setSending(false);
    }
  }, [client]);
  useEffect(() => {
    alive.current = true;
    void run();
    const sub = AppState.addEventListener("change", state => {if (state === "active") void run();});
    return () => {alive.current = false; sub.remove();};
  }, [run]);
  useEffect(() => {
    if (!capture) return;
    const sub = capture.addListener("onStored", event => {
      if (!alive.current) return;
      setSaving(capture?.savingCount() ?? 0);
      setSaveFailed(!event.ok);
      if (event.ok) void run();
    });
    return () => sub.remove();
  }, [run]);
  /** Call right after a capture: the saver now holds one more. */
  const captured = useCallback(() => {
    setSaveFailed(false);
    setSaving(capture?.savingCount() ?? 0);
  }, []);
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => {void run();}, RETRY_MS);
    return () => clearInterval(timer);
  }, [waiting, run]);
  return {waiting, sending, problem, saving, saveFailed, captured, sendNow: run, available: !!vault};
}
