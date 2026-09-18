"""The macOS notification helper: a small Swift app that hands VOOL's notification requests to the macOS notification
center and reports what macOS said.

Why a separate app: macOS attributes notification permission, settings and notifications to an app bundle, and the
VOOL runtime is a Python process. The helper (``VOOL Notifications.app``, no Dock icon) is compiled with ``swiftc``,
signed ad hoc and shipped inside the VOOL bundle at ``Contents/Resources/bin``, or compiled on demand for a source
checkout -- the same route as the wallet's device-authentication helper.

Wire contract (protocol 1). The window host starts ``vool-notify serve`` and speaks one JSON object per line. On
stdin: ``{"cmd": "apply", "requests": [...], "authorize": bool, "settings": bool, "list": bool}``, where each request
is ``{"op": "submit", ...}`` or ``{"op": "withdraw", "identifier": ...}``. On stdout, events: ``settings`` (the
authorization and per-app settings macOS reports), ``authorization_requested`` and ``authorization_result`` (granted,
and macOS's error domain, code and text) around a permission request, ``submitted`` or ``failed`` for each request added,
``already_listed`` for an immediate request macOS already delivered (it is not added again), ``withdraw_requested``
for each removal asked of macOS, ``listing`` (the identifiers macOS holds as pending and as delivered) and ``response``
(the person clicked a notification or chose an action). The helper never reports that a banner was shown; macOS does
not tell it. When the host closes stdin the helper exits: it lives only as long as the VOOL window host.

``vool-notify selftest`` prints one JSON line with the bundle identifier and the authorization macOS reports, without
asking for permission. When macOS launches the helper because the person clicked a notification while VOOL was
closed, the helper opens the VOOL app it is inside and exits.
"""
from __future__ import annotations

import hashlib
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HELPER_APP_NAME = "VOOL Notifications.app"
HELPER_DISPLAY_NAME = "VOOL Notifications"
HELPER_EXECUTABLE = "vool-notify"
PROTOCOL_VERSION = "1"
BUNDLE_SUFFIX = ".notifications"
DEV_BUNDLE_IDENTIFIER = "ai.nulla.desktop.local.notifications"
SWIFTC = "/usr/bin/swiftc"
CODESIGN = "/usr/bin/codesign"
HELPER_MIN_MACOS = "14.0"
#: Set by the wrapper bundle's launcher to the helper it ships; checked like a shipped helper before use.
HELPER_ENV_KEY = "VOOL_NOTIFICATIONS_HELPER"

_SWIFT_SOURCE = r'''
import AppKit
import Foundation
import UserNotifications

let protocolVersion = "1"
let outputLock = NSLock()
let center = UNUserNotificationCenter.current()

func emit(_ object: [String: Any]) {
    guard JSONSerialization.isValidJSONObject(object),
          let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return }
    outputLock.lock()
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
    outputLock.unlock()
}

func authorizationName(_ status: UNAuthorizationStatus) -> String {
    switch status {
    case .notDetermined: return "not_determined"
    case .denied: return "denied"
    case .authorized: return "authorized"
    case .provisional: return "provisional"
    default: return status.rawValue == 4 ? "ephemeral" : "unknown"
    }
}

func settingName(_ setting: UNNotificationSetting) -> String {
    switch setting {
    case .notSupported: return "not_supported"
    case .disabled: return "disabled"
    case .enabled: return "enabled"
    @unknown default: return "unknown"
    }
}

func alertStyleName(_ style: UNAlertStyle) -> String {
    switch style {
    case .none: return "none"
    case .banner: return "banner"
    case .alert: return "alert"
    @unknown default: return "unknown"
    }
}

func previewsName(_ value: UNShowPreviewsSetting) -> String {
    switch value {
    case .always: return "always"
    case .whenAuthenticated: return "when_authenticated"
    case .never: return "never"
    @unknown default: return "unknown"
    }
}

func reportSettings() {
    center.getNotificationSettings { settings in
        emit([
            "event": "settings",
            "authorization": authorizationName(settings.authorizationStatus),
            "alert": settingName(settings.alertSetting),
            "sound": settingName(settings.soundSetting),
            "notification_center": settingName(settings.notificationCenterSetting),
            "lock_screen": settingName(settings.lockScreenSetting),
            "previews": previewsName(settings.showPreviewsSetting),
            "alert_style": alertStyleName(settings.alertStyle),
        ])
    }
}

func reportListing() {
    center.getPendingNotificationRequests { pending in
        center.getDeliveredNotifications { delivered in
            emit([
                "event": "listing",
                "pending": pending.map { $0.identifier },
                "delivered": delivered.map { $0.request.identifier },
            ])
        }
    }
}

func parseInstant(_ text: String) -> Date? {
    let plain = ISO8601DateFormatter()
    plain.formatOptions = [.withInternetDateTime]
    if let date = plain.date(from: text) { return date }
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return fractional.date(from: text)
}

func submit(_ request: [String: Any]) {
    guard let identifier = request["identifier"] as? String, identifier.hasPrefix("vool.") else { return }
    let content = UNMutableNotificationContent()
    content.title = (request["title"] as? String) ?? "VOOL"
    content.body = (request["body"] as? String) ?? ""
    if (request["sound"] as? Bool) ?? true { content.sound = UNNotificationSound.default }
    content.threadIdentifier = (request["thread"] as? String) ?? "vool"
    content.categoryIdentifier = (request["category"] as? String) ?? "vool.info"
    content.userInfo = ["vool_identifier": identifier]
    var trigger: UNNotificationTrigger? = nil
    if let text = request["deliver_at_utc"] as? String, !text.isEmpty {
        guard let date = parseInstant(text) else {
            emit(["event": "failed", "identifier": identifier, "detail": "the delivery time is not readable"])
            return
        }
        if date.timeIntervalSinceNow > 1 {
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = TimeZone(identifier: "UTC")!
            var wanted = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
            wanted.calendar = calendar
            wanted.timeZone = calendar.timeZone
            trigger = UNCalendarNotificationTrigger(dateMatching: wanted, repeats: false)
        }
    }
    let add = {
        center.add(UNNotificationRequest(identifier: identifier, content: content, trigger: trigger)) { error in
            if let error = error {
                emit(["event": "failed", "identifier": identifier, "detail": String(error.localizedDescription.prefix(200))])
            } else {
                emit(["event": "submitted", "identifier": identifier])
            }
        }
    }
    if trigger == nil {
        // An immediate request macOS already delivered is not added again: adding it would show it a second time.
        center.getDeliveredNotifications { delivered in
            if delivered.contains(where: { $0.request.identifier == identifier }) {
                emit(["event": "already_listed", "identifier": identifier])
            } else {
                add()
            }
        }
    } else {
        add()
    }
}

func withdraw(_ request: [String: Any]) {
    guard let identifier = request["identifier"] as? String, identifier.hasPrefix("vool.") else { return }
    center.removePendingNotificationRequests(withIdentifiers: [identifier])
    center.removeDeliveredNotifications(withIdentifiers: [identifier])
    emit(["event": "withdraw_requested", "identifier": identifier])
}

func apply(_ message: [String: Any]) {
    let requests = (message["requests"] as? [[String: Any]]) ?? []
    for request in requests {
        switch request["op"] as? String {
        case "submit": submit(request)
        case "withdraw": withdraw(request)
        default: break
        }
    }
    if (message["authorize"] as? Bool) == true {
        emit(["event": "authorization_requested"])
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            var answer: [String: Any] = ["event": "authorization_result", "granted": granted]
            if let error = error as NSError? {
                answer["error_domain"] = error.domain
                answer["error_code"] = error.code
                answer["detail"] = String(error.localizedDescription.prefix(200))
            }
            emit(answer)
            reportSettings()
        }
    } else if (message["settings"] as? Bool) == true {
        reportSettings()
    }
    if (message["list"] as? Bool) == true || !requests.isEmpty {
        DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) { reportListing() }
    }
}

func openEnclosingApp() {
    var url = Bundle.main.bundleURL
    for _ in 0..<4 { url.deleteLastPathComponent() }  // <VOOL>.app/Contents/Resources/bin/<helper>.app
    guard url.pathExtension == "app" else { return }
    NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration()) { _, _ in }
}

final class Delegate: NSObject, UNUserNotificationCenterDelegate {
    var serving = false

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let action: String
        switch response.actionIdentifier {
        case "vool.snooze": action = "snooze"
        case UNNotificationDismissActionIdentifier: action = "dismiss"
        default: action = "open"
        }
        if serving {
            emit(["event": "response", "identifier": response.notification.request.identifier, "action": action])
        } else if action == "open" {
            openEnclosingApp()
        }
        completionHandler()
    }
}

let mode = CommandLine.arguments.dropFirst().first ?? ""

if mode == "selftest" {
    let done = DispatchSemaphore(value: 0)
    center.getNotificationSettings { settings in
        emit([
            "ok": true,
            "protocol": protocolVersion,
            "bundle_identifier": Bundle.main.bundleIdentifier ?? "",
            "authorization": authorizationName(settings.authorizationStatus),
        ])
        done.signal()
    }
    if done.wait(timeout: .now() + 20) == .timedOut {
        emit(["ok": false, "error": "timeout"])
        exit(2)
    }
    exit(0)
}

let delegate = Delegate()
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
center.delegate = delegate
let snooze = UNNotificationAction(identifier: "vool.snooze", title: "Snooze 10 minutes", options: [])
center.setNotificationCategories([
    UNNotificationCategory(identifier: "vool.alert", actions: [snooze], intentIdentifiers: [], options: [.customDismissAction]),
    UNNotificationCategory(identifier: "vool.info", actions: [], intentIdentifiers: [], options: [.customDismissAction]),
])

if mode == "serve" {
    delegate.serving = true
    reportSettings()
    Thread.detachNewThread {
        while let line = readLine(strippingNewline: true) {
            guard let data = line.data(using: .utf8),
                  let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
            if (object["cmd"] as? String) == "apply" {
                apply(object)
            }
        }
        // stdin closed: the window host is gone. Let replies already started finish, then leave.
        Thread.sleep(forTimeInterval: 0.3)
        exit(0)
    }
} else {
    // Launched by macOS for a click while VOOL was closed: the delegate handles the response, then the helper leaves.
    DispatchQueue.main.asyncAfter(deadline: .now() + 15) { exit(0) }
}
application.run()
'''


def swift_source() -> str:
    return _SWIFT_SOURCE


def source_digest() -> str:
    return hashlib.sha256((PROTOCOL_VERSION + _SWIFT_SOURCE).encode("utf-8")).hexdigest()[:16]


def helper_executable(app: Path) -> Path:
    return Path(app) / "Contents" / "MacOS" / HELPER_EXECUTABLE


def toolchain_available() -> bool:
    return platform.system() == "Darwin" and os.access(SWIFTC, os.X_OK) and os.access(CODESIGN, os.X_OK)


def _target(arch: str | None, min_macos: str) -> str:
    return f"{arch or platform.machine()}-apple-macos{min_macos}"


def _info_plist(bundle_identifier: str, min_macos: str) -> bytes:
    return plistlib.dumps({
        "CFBundleIdentifier": bundle_identifier,
        "CFBundleName": HELPER_DISPLAY_NAME,
        "CFBundleDisplayName": HELPER_DISPLAY_NAME,
        "CFBundleExecutable": HELPER_EXECUTABLE,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": PROTOCOL_VERSION,
        "CFBundleVersion": PROTOCOL_VERSION,
        "CFBundleInfoDictionaryVersion": "6.0",
        "LSUIElement": True,
        "LSMinimumSystemVersion": min_macos,
        "NSHighResolutionCapable": True,
        "VOOLNotificationsProtocol": PROTOCOL_VERSION,
        "VOOLNotificationsSource": source_digest(),
    })


def build_helper_app(destination: Path, *, bundle_identifier: str, arch: str | None = None, min_macos: str | None = None) -> Path:
    """Compile, assemble and sign the helper app at ``destination`` (the bundle build, or the on-demand cache)."""
    if not toolchain_available():
        raise RuntimeError("building the notification helper needs macOS with /usr/bin/swiftc and /usr/bin/codesign")
    identifier = str(bundle_identifier or "").strip()
    if not identifier or any(char.isspace() for char in identifier):
        raise ValueError("the notification helper needs a bundle identifier without spaces")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    floor = str(min_macos or HELPER_MIN_MACOS)
    with tempfile.TemporaryDirectory(prefix="vool-notify-build-") as build_dir:
        source = Path(build_dir) / "main.swift"
        source.write_text(_SWIFT_SOURCE, encoding="utf-8")
        staged = Path(build_dir) / HELPER_APP_NAME
        helper_executable(staged).parent.mkdir(parents=True)
        compiled = subprocess.run(
            [SWIFTC, "-O", "-swift-version", "5", "-target", _target(arch, floor), str(source), "-o", str(helper_executable(staged)),
             "-framework", "UserNotifications", "-framework", "AppKit"],
            capture_output=True, timeout=900, check=False,
        )
        if compiled.returncode != 0 or not helper_executable(staged).exists():
            lines = (compiled.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError("the notification helper did not compile: " + ("; ".join(lines[-3:]) or "unknown compiler error"))
        (staged / "Contents" / "Info.plist").write_bytes(_info_plist(identifier, floor))
        signed = subprocess.run([CODESIGN, "--force", "--sign", "-", "--identifier", identifier, str(staged)],
                                capture_output=True, timeout=120, check=False)
        if signed.returncode != 0:
            detail = (signed.stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError("the notification helper could not be signed: " + detail[:300])
        pending = destination.with_name(destination.name + f".{os.getpid()}.tmp")
        if pending.exists():
            shutil.rmtree(pending)
        shutil.copytree(staged, pending, symlinks=True)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(pending, destination)
    return destination


def _usable_helper(app: Path) -> bool:
    try:
        info = plistlib.loads((Path(app) / "Contents" / "Info.plist").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    return (Path(app).name == HELPER_APP_NAME and str(info.get("CFBundleIdentifier") or "").endswith(BUNDLE_SUFFIX)
            and str(info.get("VOOLNotificationsProtocol") or "") == PROTOCOL_VERSION and os.access(helper_executable(app), os.X_OK))


def shipped_helper_app() -> Path | None:
    """The helper built into this VOOL bundle: next to the embedded interpreter (``Resources/python/bin`` ->
    ``Resources/bin``), or named by the wrapper launcher. Either must be a VOOL notification helper of this protocol."""
    candidates = [Path(sys.executable).parent.parent.parent / "bin" / HELPER_APP_NAME]
    named = str(os.environ.get(HELPER_ENV_KEY) or "").strip()
    if named:
        candidates.append(Path(named))
    for candidate in candidates:
        if _usable_helper(candidate):
            return candidate
    return None


def tools_dir() -> Path:
    try:
        from core.runtime_paths import active_data_dir

        return Path(active_data_dir()) / "notification_tools"
    except Exception:
        return Path(tempfile.gettempdir()) / "vool_notification_tools"


def cached_helper_app(*, bundle_identifier: str = DEV_BUNDLE_IDENTIFIER) -> Path:
    """For a source checkout: the helper compiled once per source version into the data directory."""
    app = tools_dir() / source_digest() / HELPER_APP_NAME
    if _usable_helper(app):
        return app
    return build_helper_app(app, bundle_identifier=bundle_identifier)


def main(argv: list[str] | None = None) -> int:
    """``python -m core.notifications_macos --build <destination.app> [--bundle-id ID] [--arch A] [--min-macos V]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "--build":
        rest = args[2:]
        # pairs by index: the bundle build may run this with an interpreter older than zip(strict=...)
        options = {rest[index]: rest[index + 1] for index in range(0, len(rest) - 1, 2)}
        built = build_helper_app(Path(args[1]), bundle_identifier=options.get("--bundle-id", DEV_BUNDLE_IDENTIFIER),
                                 arch=options.get("--arch"), min_macos=options.get("--min-macos"))
        print(str(built))
        return 0
    print("usage: --build <destination.app> [--bundle-id <id>] [--arch <arm64|x86_64>] [--min-macos <version>]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
