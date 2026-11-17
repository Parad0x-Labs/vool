import AVFoundation
import ExpoModulesCore
import UIKit

/// VOOL's private camera on iPhone and iPad: the same store, camera and recorder the app uses on Android.
/// iOS has one home-screen icon per app and no quick settings tiles, so the camera is reached from VOOL and from
/// the long-press shortcut on its icon.
public class VoolCaptureModule: Module {
  private let work = DispatchQueue(label: "vool.capture.calls", qos: .userInitiated)
  private var sendingTask: UIBackgroundTaskIdentifier = .invalid

  private func off(_ promise: Promise, _ code: String, _ job: @escaping () throws -> Any?) {
    work.async {
      do { promise.resolve(try job()) } catch { promise.reject(code, error.localizedDescription) }
    }
  }

  public func definition() -> ModuleDefinition {
    Name("VoolCapture")

    Constants(["pieceBytes": CaptureStore.piece])

    Events("onStored", "onAppIcon")

    OnCreate {
      CaptureStore.onStored = { [weak self] note in self?.sendEvent("onStored", note) }
      CaptureStore.resume()
    }

    OnDestroy {
      CaptureStore.onStored = nil
      self.keepSending(false)
    }

    /// Sets how the screen may turn: free on an iPad, upright on an iPhone or while the camera is open.
    Function("fitScreen") { (camera: Bool) -> Int in
      VoolScreenSubscriber.cameraUp = camera
      VoolScreenSubscriber.refresh()
      return UIDevice.current.userInterfaceIdiom == .pad ? 768 : 390
    }

    Function("savingCount") { CaptureStore.savingCount }
    // One line to the system log (VoolCapture) for tracing a test run: steps and timings only.
    Function("trace") { (line: String) in CaptureStore.log.info("\(String(line.prefix(300)), privacy: .public)") }

    /// The VOOL Camera shortcut just opened VOOL: show the camera without asking for the unlock.
    Function("cameraLaunchRecent") { CameraLaunch.recent() }
    Function("clearCameraLaunch") { CameraLaunch.clear() }
    Function("iconUsed") { (which: String) -> Bool in which == "vault" ? true : CameraLaunch.used }

    /// Asks for the camera or the microphone the way iOS does (once; after that only Settings can change it).
    AsyncFunction("allow") { (kind: String, promise: Promise) in
      let media: AVMediaType = kind == "microphone" ? .audio : .video
      switch AVCaptureDevice.authorizationStatus(for: media) {
      case .authorized: promise.resolve(true)
      case .notDetermined: AVCaptureDevice.requestAccess(for: media) { promise.resolve($0) }
      default: promise.resolve(false)
      }
    }

    /// The camera's own settings, as JSON text the app shapes.
    Function("getCameraPrefs") { CameraPrefs.get() }
    Function("setCameraPrefs") { (json: String) in CameraPrefs.set(json) }

    /// Photo sizes this iPhone gives apps (largest first), and the largest of all.
    AsyncFunction("photoSizes") { (facing: String, promise: Promise) in
      self.off(promise, "ERR_CAPTURE_SIZES") { PhotoSizes.of(facing) }
    }

    /// On while captures are being sent: iOS gives VOOL a little time to finish when it leaves the screen.
    Function("keepSending") { (on: Bool, _: String) -> Bool in self.keepSending(on) }

    // Smaller files: not on iPhone yet (the app hides the setting when these answer "").
    Function("getCompressionSettings") { "" }
    Function("setCompressionSettings") { (_: String) in "" }
    Function("getCompressionCapabilities") { "" }

    AsyncFunction("list") { (promise: Promise) in self.off(promise, "ERR_CAPTURE_LIST") { CaptureStore.list().map { $0.map } } }
    AsyncFunction("readFrom") { (id: String, offset: Double, promise: Promise) in
      self.off(promise, "ERR_CAPTURE_READ") { try CaptureStore.readFrom(id, Int64(offset)) }
    }
    AsyncFunction("remove") { (id: String, promise: Promise) in
      self.off(promise, "ERR_CAPTURE_REMOVE") { try CaptureStore.remove(id); return nil }
    }
    /// Deletions (hash, key destroyed, when) the computer has not recorded yet; and marking one recorded.
    AsyncFunction("gone") { (promise: Promise) in self.off(promise, "ERR_CAPTURE_GONE") { CaptureStore.gone() } }
    AsyncFunction("goneRecorded") { (sha256: String, promise: Promise) in
      self.off(promise, "ERR_CAPTURE_GONE") { CaptureStore.goneRecorded(sha256); return nil }
    }

    AsyncFunction("startSound") { (promise: Promise) in
      self.off(promise, "ERR_CAPTURE_SOUND") { try SoundRecorder.start(); return nil }
    }
    AsyncFunction("stopSound") { (promise: Promise) in
      self.off(promise, "ERR_CAPTURE_SOUND") { try SoundRecorder.stop(); return ["saving": true, "kind": "sound"] }
    }
    Function("soundRecording") { SoundRecorder.recording }

    // No second home-screen icon and no quick settings tile on iOS: the long-press menu has VOOL Camera instead.
    Function("homeIconOn") { false }
    AsyncFunction("setHomeIcon") { (_: Bool) in }
    Function("canAddTile") { false }
    AsyncFunction("addTile") { () -> String in "unsupported" }

    View(PrivateCameraView.self) {
      Events("onModes", "onCaptured", "onCameraError", "onZoom", "onFocus", "onTurn")
      Prop("facing") { (view: PrivateCameraView, value: String) in view.setFacing(value) }
      Prop("mode") { (view: PrivateCameraView, value: String) in view.setMode(value) }
      Prop("video") { (view: PrivateCameraView, value: Bool) in view.setVideo(value) }
      Prop("flash") { (view: PrivateCameraView, value: String) in view.setFlash(value) }
      Prop("aspect") { (view: PrivateCameraView, value: String) in view.setAspect(value) }
      Prop("photoSize") { (view: PrivateCameraView, value: String) in view.setPhotoSize(value) }
      Prop("videoQuality") { (view: PrivateCameraView, value: String) in view.setVideoQuality(value) }
      Prop("steady") { (view: PrivateCameraView, value: Bool) in view.setSteady(value) }
      Prop("mirror") { (view: PrivateCameraView, value: Bool) in view.setMirror(value) }
      Prop("filter") { (view: PrivateCameraView, value: String) in view.setFilter(value) }
      AsyncFunction("setZoom") { (view: PrivateCameraView, ratio: Double) in view.setZoom(ratio) }
      AsyncFunction("takePhoto") { (view: PrivateCameraView, promise: Promise) in view.takePhoto(promise) }
      AsyncFunction("startVideo") { (view: PrivateCameraView, promise: Promise) in view.startVideo(promise) }
      AsyncFunction("stopVideo") { (view: PrivateCameraView, promise: Promise) in view.stopVideo(promise) }
    }
  }

  /// iOS keeps an app running for a short while after it leaves the screen when it asks; that is all it allows.
  @discardableResult
  private func keepSending(_ on: Bool) -> Bool {
    let run = {
      if on {
        if self.sendingTask != .invalid { return }
        self.sendingTask = UIApplication.shared.beginBackgroundTask(withName: "vool-capture-send") { [weak self] in
          guard let self else { return }
          UIApplication.shared.endBackgroundTask(self.sendingTask)
          self.sendingTask = .invalid
        }
      } else if self.sendingTask != .invalid {
        UIApplication.shared.endBackgroundTask(self.sendingTask)
        self.sendingTask = .invalid
      }
    }
    if Thread.isMainThread { run() } else { DispatchQueue.main.sync(execute: run) }
    return on && sendingTask != .invalid
  }
}
