import ExpoModulesCore
import UIKit

// Native cover is installed before the system captures the app switcher.
// Foreground removal is requested only after the JS lock screen has rendered.
public class VoolPrivacySubscriber: ExpoAppDelegateSubscriber {
  static var enabled = false
  private static var covers: [ObjectIdentifier: UIVisualEffectView] = [:]

  static func cover() {
    guard enabled else { return }
    for scene in UIApplication.shared.connectedScenes {
      guard let scene = scene as? UIWindowScene else { continue }
      for window in scene.windows where !window.isHidden && window.windowLevel == .normal {
        let identity = ObjectIdentifier(window)
        if covers[identity] != nil { continue }
        let view = UIVisualEffectView(effect: UIBlurEffect(style: .systemUltraThinMaterialDark))
        view.frame = window.bounds
        view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.isUserInteractionEnabled = false
        window.addSubview(view)
        covers[identity] = view
      }
    }
  }
  static func ready(_ privateSession: Bool) {
    enabled = privateSession
    if !enabled || UIApplication.shared.applicationState == .active {
      for view in covers.values { view.removeFromSuperview() }
      covers.removeAll()
    } else { cover() }
  }
  public func applicationWillResignActive(_ application: UIApplication) { Self.cover() }
  public func applicationDidEnterBackground(_ application: UIApplication) { Self.cover() }
}
