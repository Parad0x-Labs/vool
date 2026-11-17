import ExpoModulesCore
import UIKit

/// How the screen may turn: an iPhone stays upright, an iPad turns freely, and the camera stays upright on both.
public class VoolScreenSubscriber: ExpoAppDelegateSubscriber {
  /// Set while VOOL's camera is open.
  static var cameraUp = false

  public func application(_ application: UIApplication, supportedInterfaceOrientationsFor window: UIWindow?) -> UIInterfaceOrientationMask {
    if Self.cameraUp || UIDevice.current.userInterfaceIdiom != .pad { return .portrait }
    return .all
  }

  /// Asks iOS to apply the rule again now (the camera opened or closed).
  static func refresh() {
    DispatchQueue.main.async {
      for scene in UIApplication.shared.connectedScenes {
        guard let scene = scene as? UIWindowScene else { continue }
        if #available(iOS 16.0, *) {
          scene.windows.first?.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations()
          let mask: UIInterfaceOrientationMask = cameraUp || UIDevice.current.userInterfaceIdiom != .pad ? .portrait : .all
          scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { _ in }
        } else {
          UIViewController.attemptRotationToDeviceOrientation()
        }
      }
    }
  }
}
