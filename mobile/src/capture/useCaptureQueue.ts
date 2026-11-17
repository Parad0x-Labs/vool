import {useCallback, useEffect, useRef, useState} from "react";
import {AppState} from "react-native";
import type {MobileCompanionClient} from "../protocol/client.ts";
import {capture, vault} from "./native.ts";
import {sendWaiting, type SendProblem} from "./sender.ts";
import {tr} from "../i18n/index.ts";

const RETRY_MS = 30_000;

export type CaptureQueue = ReturnType<typeof useCaptureQueue>;

/**
 * Keeps sending what the private capture holds: now, when the app comes back, and every 30 s while
 * anything waits. Lives at the top of the app (not inside the unlocked screens), and while it sends
 * Android keeps VOOL running with a quiet notice, so a long video goes on when the phone locks.
 */
export function useCaptureQueue(client: MobileCompanionClient | null) {
  const [waiting, setWaiting] = useState(0);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<SendProblem>("");
  // captures still being locked away (optional compression can take a minute) and whether the last one failed
  const [saving, setSaving] = useState(() => capture?.savingCount() ?? 0);
  const [saveFailed, setSaveFailed] = useState(false);
  const alive = useRef(true);
  const run = useCallback(async () => {
    if (!vault || !client) return;
    setSending(true);
    let kept = false;
    try {
      // the notice only appears when there is something to send
      if ((await vault.list()).length) kept = !!capture?.keepSending(true, tr("Sending to your computer…"));
      const report = await sendWaiting(vault, client);
      if (!alive.current) return;
      setWaiting(report.waiting);
      setProblem(report.problem);
    } catch {
      if (alive.current) setProblem("failed");
    } finally {
      if (kept) capture?.keepSending(false, "");
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
