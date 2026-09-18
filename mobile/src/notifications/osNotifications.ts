// OS-level completion/failure notifications — OPT-IN, privacy-safe.
//
// HONEST SCOPE: this module is real code against expo-notifications (local
// notifications; no push server is involved or needed for the local-network
// companion), but it has NOT been proven on an installed device — no iOS/
// Android build exists in this environment. In-app polling remains the
// verified channel; OS notifications are the opt-in enhancement on top.
//
// PRIVACY LAW: notification TEXT never contains task content by default —
// generic outcome wording only ("a turn finished"), because iOS/Android
// notification text can surface on lock screens. Content lives in the app.
import type { NotificationEvent } from "../protocol/models.ts";

export interface OsNotificationBridge {
  /** Returns true when notifications are enabled AND permission granted. */
  ensureEnabled(): Promise<boolean>;
  isEnabled(): boolean;
  disable(): void;
  notify(kind: "completion" | "failure" | "attention"): Promise<void>;
}

const NON_SENSITIVE_TEXT: Record<"completion" | "failure" | "attention", { title: string; body: string }> = {
  completion: { title: "VOOL", body: "A turn finished on your desktop." },
  failure: { title: "VOOL", body: "A turn failed on your desktop." },
  attention: { title: "VOOL", body: "An action needs your decision." },
};

export class ExpoOsNotifications implements OsNotificationBridge {
  private enabled = false;

  async ensureEnabled(): Promise<boolean> {
    try {
      const Notifications = await import("expo-notifications");
      const settings = await Notifications.getPermissionsAsync();
      let granted = settings.granted;
      if (!granted && settings.canAskAgain) {
        granted = (await Notifications.requestPermissionsAsync()).granted;
      }
      this.enabled = granted;
      if (granted) {
        await Notifications.setNotificationHandler({
          handleNotification: async () => ({
            shouldShowAlert: true,
            shouldPlaySound: false,
            shouldSetBadge: false,
            shouldShowBanner: true,
            shouldShowList: true,
          }),
        });
      }
      return granted;
    } catch {
      this.enabled = false;
      return false;
    }
  }

  isEnabled(): boolean { return this.enabled; }

  disable(): void { this.enabled = false; }

  async notify(kind: "completion" | "failure" | "attention"): Promise<void> {
    if (!this.enabled) return;
    try {
      const Notifications = await import("expo-notifications");
      const text = NON_SENSITIVE_TEXT[kind];
      await Notifications.scheduleNotificationAsync({
        content: { title: text.title, body: text.body },
        trigger: null,
      });
    } catch {
      // Never let a notification failure break the polling loop.
    }
  }
}

/** Map a polled event to the OS channel — coarse kinds only, no task content. */
export function osKindFor(event: NotificationEvent): "completion" | "failure" | "attention" | null {
  if (String(event.event_type || "").includes("permission") && String(event.event_type || "").includes("required")) {
    return "attention";
  }
  if (event.mobile_kind === "failure") return "failure";
  if (event.mobile_kind === "completion") return "completion";
  return null;
}
