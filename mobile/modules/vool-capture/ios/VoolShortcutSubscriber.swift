import ExpoModulesCore
import UIKit

/// The VOOL icon's long-press menu on iPhone and iPad: VOOL Camera and VOOL Vault.
/// iOS gives an app one home-screen icon, so these stand in for Android's separate Camera and Vault icons.
/// A pick becomes the same link the Android icons open ("vool-pair://vault"), so the app treats both alike:
/// the Vault still opens only after VOOL's unlock.
public class VoolShortcutSubscriber: ExpoAppDelegateSubscriber {
  static let vault = "systems.vool.companion.vault"
  static let camera = "systems.vool.companion.camera"
  /// This build has the private camera on iPhone.
  static var cameraBuilt = true

  private static let lock = NSLock()
  private static var pending: String?
  /// Told at once when VOOL is already running.
  static var onShortcut: ((String) -> Void)?

  static func link(for type: String) -> String? {
    switch type {
    case vault: return "vool-pair://vault"
    case camera: return cameraBuilt ? "vool-pair://capture" : nil
    default: return nil
    }
  }

  /// Kept until the app takes it, and announced: whichever comes first (the app starting or the event) gets it once.
  static func deliver(_ link: String) {
    lock.lock()
    pending = link
    let listener = onShortcut
    lock.unlock()
    if let listener { DispatchQueue.main.async { listener(link) } }
  }

  /// The latest pick not yet handled (taken once).
  static func take() -> String {
    lock.lock(); defer { lock.unlock() }
    let link = pending ?? ""
    pending = nil
    return link
  }

  static func listen(_ listener: ((String) -> Void)?) {
    lock.lock(); onShortcut = listener; lock.unlock()
  }

  public func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
    var items = [UIApplicationShortcutItem(type: Self.vault, localizedTitle: "VOOL Vault", localizedSubtitle: nil,
                                           icon: UIApplicationShortcutIcon(systemImageName: "lock.square"), userInfo: nil)]
    if Self.cameraBuilt {
      items.insert(UIApplicationShortcutItem(type: Self.camera, localizedTitle: "VOOL Camera", localizedSubtitle: nil,
                                             icon: UIApplicationShortcutIcon(systemImageName: "camera"), userInfo: nil), at: 0)
    }
    application.shortcutItems = items
    return true
  }

  public func application(_ application: UIApplication, performActionFor shortcutItem: UIApplicationShortcutItem,
                          completionHandler: @escaping (Bool) -> Void) {
    guard let link = Self.link(for: shortcutItem.type) else { completionHandler(false); return }
    // The camera opens at once, before VOOL's unlock (only the camera, never chats, wallet or settings).
    if shortcutItem.type == Self.camera { CameraLaunch.mark() }
    Self.deliver(link)
    completionHandler(true)
  }
}
