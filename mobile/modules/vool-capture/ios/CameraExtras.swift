import AVFoundation
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Remembers that the VOOL Camera shortcut just opened VOOL, so the app opens the camera at once instead of
/// asking for Face ID first. Only the camera opens this way: chats, wallet and settings stay behind VOOL's lock.
enum CameraLaunch {
  private static var at: Date?
  /// The shortcut ran in this run of VOOL at least once.
  static var used = false

  static func mark() { at = Date(); used = true }
  /// True for a few seconds after the shortcut was used; reading it does not clear it.
  static func recent() -> Bool { at.map { Date().timeIntervalSince($0) < 5 } ?? false }
  static func clear() { at = nil }
}

/// The camera's own choices (flash, timer, grid, filter ...), kept as one small JSON text the app shapes.
enum CameraPrefs {
  private static let key = "vool_camera_prefs"
  static func get() -> String { UserDefaults.standard.string(forKey: key) ?? "" }
  static func set(_ json: String) { UserDefaults.standard.set(String(json.prefix(4_000)), forKey: key) }
}

/// Which camera VOOL opens: on the back the one that moves between its lenses as you zoom.
enum CameraDevices {
  static func pick(_ facing: String) -> AVCaptureDevice? {
    let front = facing == "front"
    let types: [AVCaptureDevice.DeviceType] = front
      ? [.builtInTrueDepthCamera, .builtInWideAngleCamera]
      : [.builtInTripleCamera, .builtInDualWideCamera, .builtInDualCamera, .builtInWideAngleCamera]
    for type in types {
      if let device = AVCaptureDevice.default(type, for: .video, position: front ? .front : .back) { return device }
    }
    return nil
  }

  /// The zoom factor that shows as "1x": the main lens, when the camera also has a wider one.
  static func base(_ device: AVCaptureDevice) -> CGFloat {
    guard device.deviceType == .builtInTripleCamera || device.deviceType == .builtInDualWideCamera,
          let first = device.virtualDeviceSwitchOverVideoZoomFactors.first else { return 1 }
    return CGFloat(truncating: first)
  }

  static func photoDimensions(_ format: AVCaptureDevice.Format) -> [CMVideoDimensions] {
    if #available(iOS 16.0, *) { return format.supportedMaxPhotoDimensions }
    return [format.highResolutionStillImageDimensions]
  }

  static func ratio(_ format: AVCaptureDevice.Format) -> Double {
    let d = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
    return d.height == 0 ? 0 : Double(max(d.width, d.height)) / Double(min(d.width, d.height))
  }
}

/// The photo sizes this iPhone hands to apps for one camera, largest first, and the largest of all.
enum PhotoSizes {
  static func of(_ facing: String) -> [String: Any] {
    guard let device = CameraDevices.pick(facing) else { return ["sizes": [String](), "sensorMax": ""] }
    var seen = Set<String>()
    var sizes: [(Int64, String)] = []
    for format in device.formats {
      for d in CameraDevices.photoDimensions(format) where d.width > 0 && d.height > 0 {
        let name = "\(max(d.width, d.height))x\(min(d.width, d.height))"
        if seen.insert(name).inserted { sizes.append((Int64(d.width) * Int64(d.height), name)) }
      }
    }
    sizes.sort { $0.0 > $1.0 }
    return ["sizes": sizes.prefix(12).map { $0.1 }, "sensorMax": sizes.first?.1 ?? ""]
  }
}

/// Colour filters for photos, the same numbers as on Android. The viewfinder shows them live; the saved photo
/// gets the filter after the shot, before it is locked away. Video keeps its true colours.
enum PhotoFilters {
  /// 4 rows of 5 (red, green, blue, alpha, offset 0-255), as Android's ColorMatrix.
  static func matrix(_ name: String) -> [Double]? {
    switch name {
    case "warm": return [1.08, 0, 0, 0, 8, 0, 1.01, 0, 0, 2, 0, 0, 0.9, 0, 0, 0, 0, 0, 1, 0]
    case "cool": return [0.92, 0, 0, 0, 0, 0, 1.0, 0, 0, 2, 0, 0, 1.08, 0, 8, 0, 0, 0, 1, 0]
    case "vivid": return then(saturation(1.35), contrast(1.08))
    case "fade":
      return then(then(saturation(0.8), contrast(0.86)), [1, 0, 0, 0, 14, 0, 1, 0, 0, 12, 0, 0, 1, 0, 10, 0, 0, 0, 1, 0])
    case "mono": return saturation(0)
    case "noir": return then(saturation(0), contrast(1.3))
    default: return nil
    }
  }

  private static func saturation(_ s: Double) -> [Double] {
    let r = 0.213 * (1 - s), g = 0.715 * (1 - s), b = 0.072 * (1 - s)
    return [r + s, g, b, 0, 0, r, g + s, b, 0, 0, r, g, b + s, 0, 0, 0, 0, 0, 1, 0]
  }

  private static func contrast(_ c: Double) -> [Double] {
    let t = 128 * (1 - c)
    return [c, 0, 0, 0, t, 0, c, 0, 0, t, 0, 0, c, 0, t, 0, 0, 0, 1, 0]
  }

  /// `first` and then `next` (Android's postConcat).
  private static func then(_ first: [Double], _ next: [Double]) -> [Double] {
    var out = [Double](repeating: 0, count: 20)
    for i in 0..<4 {
      for j in 0..<5 {
        var sum = 0.0
        for k in 0..<4 { sum += next[i * 5 + k] * first[k * 5 + j] }
        out[i * 5 + j] = sum + (j == 4 ? next[i * 5 + 4] : 0)
      }
    }
    return out
  }

  /// The filter on a picture, worked out in sRGB as on Android.
  static func filtered(_ image: CIImage, _ name: String) -> CIImage {
    guard let m = matrix(name), let filter = CIFilter(name: "CIColorMatrix") else { return image }
    filter.setValue(image, forKey: kCIInputImageKey)
    filter.setValue(CIVector(x: m[0], y: m[1], z: m[2], w: m[3]), forKey: "inputRVector")
    filter.setValue(CIVector(x: m[5], y: m[6], z: m[7], w: m[8]), forKey: "inputGVector")
    filter.setValue(CIVector(x: m[10], y: m[11], z: m[12], w: m[13]), forKey: "inputBVector")
    filter.setValue(CIVector(x: m[15], y: m[16], z: m[17], w: m[18]), forKey: "inputAVector")
    filter.setValue(CIVector(x: m[4] / 255, y: m[9] / 255, z: m[14] / 255, w: m[19] / 255), forKey: "inputBiasVector")
    return filter.outputImage ?? image
  }

  static let context: CIContext = {
    let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
    return CIContext(options: [.workingColorSpace: srgb, .outputColorSpace: srgb, .cacheIntermediates: false])
  }()

  /// Puts the filter into a saved JPEG, in place, keeping its date, orientation and camera details (never a place).
  /// Any trouble leaves the photo exactly as the camera took it.
  static func apply(_ url: URL, _ name: String) {
    guard matrix(name) != nil else { return }
    let out = url.deletingLastPathComponent().appendingPathComponent(url.lastPathComponent + ".filter")
    autoreleasepool {
      guard let source = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
            var properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
            let image = CIImage(contentsOf: url, options: [.applyOrientationProperty: false]),
            let picture = context.createCGImage(filtered(image, name), from: image.extent),
            let target = CGImageDestinationCreateWithURL(out as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else { return }
      properties.removeValue(forKey: kCGImagePropertyGPSDictionary)
      properties[kCGImageDestinationLossyCompressionQuality] = 0.95
      CGImageDestinationAddImage(target, picture, properties as CFDictionary)
      guard CGImageDestinationFinalize(target) else { CaptureStore.wipe(out); return }
      if (try? FileManager.default.replaceItemAt(url, withItemAt: out)) == nil { CaptureStore.wipe(out) }
    }
  }
}

/// Sound recordings for the private capture: AAC in an M4A file, written only inside VOOL.
enum SoundRecorder {
  private static var recorder: AVAudioRecorder?
  private static var file: URL?
  private static var startedAt: Int64 = 0
  private static let lock = NSLock()

  static var recording: Bool { lock.lock(); defer { lock.unlock() }; return recorder != nil }

  static func start() throws {
    lock.lock(); defer { lock.unlock() }
    if recorder != nil { return }
    guard AVAudioSession.sharedInstance().recordPermission == .granted else { throw VaultError("The microphone is off for VOOL.") }
    let target = CaptureStore.scratch("m4a")
    let audio = AVAudioSession.sharedInstance()
    try audio.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetooth])
    try audio.setActive(true)
    let settings: [String: Any] = [
      AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 48_000, AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 256_000,
    ]
    let next = try AVAudioRecorder(url: target, settings: settings)
    guard next.record() else {
      try? FileManager.default.removeItem(at: target)
      throw VaultError("The recording could not start.")
    }
    recorder = next
    file = target
    startedAt = Int64(Date().timeIntervalSince1970 * 1000)
  }

  static func stop() throws {
    lock.lock()
    guard let current = recorder, let target = file else { lock.unlock(); throw VaultError("Nothing is recording.") }
    let took = current.currentTime
    recorder = nil
    file = nil
    lock.unlock()
    current.stop()
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    let written = ((try? FileManager.default.attributesOfItem(atPath: target.path))?[.size] as? NSNumber)?.int64Value ?? 0
    guard took > 0.3 && written > 0 else {
      CaptureStore.wipe(target)
      throw VaultError("The recording was too short.")
    }
    CaptureStore.keep(target, mediaType: "audio/mp4", kind: "sound", takenAt: startedAt)
  }
}
