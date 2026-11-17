import AVFoundation
import CoreImage
import CoreMotion
import ExpoModulesCore
import MetalKit
import UIKit

/// The private camera's viewfinder on iPhone and iPad. Photos are taken at the highest quality the iPhone gives
/// apps (its own processing chooses night and HDR work by itself); videos record at the quality chosen. Every
/// capture is written inside VOOL and locked into CaptureStore at once; nothing reaches Photos.
final class PrivateCameraView: ExpoView, AVCaptureVideoDataOutputSampleBufferDelegate, MTKViewDelegate {
  let onModes = EventDispatcher()
  let onCaptured = EventDispatcher()
  let onCameraError = EventDispatcher()
  let onZoom = EventDispatcher()
  let onFocus = EventDispatcher()
  let onTurn = EventDispatcher()

  private let session = AVCaptureSession()
  private let queue = DispatchQueue(label: "vool.camera.session")
  private let frameQueue = DispatchQueue(label: "vool.camera.frames")
  private let preview = AVCaptureVideoPreviewLayer()
  private let photoOutput = AVCapturePhotoOutput()
  private let movieOutput = AVCaptureMovieFileOutput()
  private let frames = AVCaptureVideoDataOutput()
  private var device: AVCaptureDevice?
  private var zoomBase: CGFloat = 1
  private var zoomWatch: NSKeyValueObservation?
  private var pinchStart: CGFloat = 1
  private let motion = CMMotionManager()
  private var turn = 0
  private var attached = false
  private var pending = false
  private var photoJobs: [Int64: PhotoJob] = [:]
  private var movieJob: MovieJob?

  // The live filter: frames drawn through the same colour matrix the photo gets.
  private var filterView: MTKView?
  private var commands: MTLCommandQueue?
  private var renderer: CIContext?
  private let frameLock = NSLock()
  private var latest: CIImage?

  // Camera settings (the app keeps them; these follow its props)
  private var facing = "back"
  private var mode = "standard"
  private var video = false
  private var flash = "off"            // off | auto | on (in video: on = torch)
  private var aspect = "4:3"           // 4:3 | 16:9
  private var photoSize = "max"        // max | WIDTHxHEIGHT
  private var videoQuality = "highest" // highest | 2160 | 1080 | 720
  private var steady = true
  private var mirror = false
  private var filter = "none"

  required init(appContext: AppContext? = nil) {
    super.init(appContext: appContext)
    clipsToBounds = true
    backgroundColor = .black
    // FIT, not FILL: what the viewfinder shows is exactly what the photo holds.
    preview.videoGravity = .resizeAspect
    preview.session = session
    layer.addSublayer(preview)
    addGestureRecognizer(UIPinchGestureRecognizer(target: self, action: #selector(pinched(_:))))
    addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(tapped(_:))))
  }

  override func layoutSubviews() {
    super.layoutSubviews()
    CATransaction.begin(); CATransaction.setDisableActions(true)
    preview.frame = bounds
    CATransaction.commit()
    filterView?.frame = bounds
  }

  func setFacing(_ value: String) { if value != facing { facing = value; rebuild() } }
  func setMode(_ value: String) { mode = value }
  func setVideo(_ value: Bool) { if value != video { video = value; rebuild() } }
  func setFlash(_ value: String) {
    if value == flash { return }
    flash = value
    queue.async { self.torch() }
  }
  func setAspect(_ value: String) { if value != aspect { aspect = value; rebuild() } }
  func setPhotoSize(_ value: String) { if value != photoSize { photoSize = value; rebuild() } }
  func setVideoQuality(_ value: String) { if value != videoQuality { videoQuality = value; rebuild() } }
  func setSteady(_ value: Bool) { if value != steady { steady = value; rebuild() } }
  func setMirror(_ value: Bool) { if value != mirror { mirror = value; rebuild() } }
  func setFilter(_ value: String) { if value != filter { filter = value; rebuild() } }

  func setZoom(_ ratio: Double) {
    queue.async { self.zoom(to: CGFloat(ratio) * self.zoomBase) }
  }

  // MARK: Start and stop

  override func didMoveToWindow() {
    super.didMoveToWindow()
    if window != nil && !attached {
      attached = true
      watchTurn(true)
      rebuild()
    } else if window == nil && attached {
      attached = false
      watchTurn(false)
      queue.async {
        if self.movieOutput.isRecording { self.movieOutput.stopRecording() }
        if self.session.isRunning { self.session.stopRunning() }
      }
    }
  }

  /// Many props arrive at once: the session is set up once for all of them.
  private func rebuild() {
    guard attached, !pending else { return }
    pending = true
    DispatchQueue.main.async {
      self.pending = false
      self.showFilterView(!self.video && self.filter != "none")
      let wanted = (facing: self.facing, video: self.video, aspect: self.aspect, photoSize: self.photoSize, quality: self.videoQuality,
                    steady: self.steady, mirror: self.mirror, live: !self.video && self.filter != "none")
      self.queue.async { self.configure(wanted) }
    }
  }

  private func configure(_ w: (facing: String, video: Bool, aspect: String, photoSize: String, quality: String, steady: Bool, mirror: Bool, live: Bool)) {
    if movieOutput.isRecording { return }
    guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else { return fail("The camera is off for VOOL.") }
    guard let camera = CameraDevices.pick(w.facing) else { return fail("No camera was found.") }
    session.beginConfiguration()
    session.inputs.forEach { session.removeInput($0) }
    session.outputs.forEach { session.removeOutput($0) }
    do {
      session.sessionPreset = w.video ? .high : .photo
      let input = try AVCaptureDeviceInput(device: camera)
      guard session.canAddInput(input) else { throw VaultError("camera input") }
      session.addInput(input)
      if w.video {
        let presets: [AVCaptureSession.Preset] = w.quality == "720" ? [.hd1280x720]
          : w.quality == "1080" ? [.hd1920x1080, .hd1280x720] : [.hd4K3840x2160, .hd1920x1080, .hd1280x720]
        if let preset = presets.first(where: { session.canSetSessionPreset($0) }) { session.sessionPreset = preset }
        if AVCaptureDevice.authorizationStatus(for: .audio) == .authorized, let mic = AVCaptureDevice.default(for: .audio),
           let sound = try? AVCaptureDeviceInput(device: mic), session.canAddInput(sound) {
          session.addInput(sound)
        }
        guard session.canAddOutput(movieOutput) else { throw VaultError("video output") }
        session.addOutput(movieOutput)
        if let link = movieOutput.connection(with: .video) {
          if movieOutput.availableVideoCodecTypes.contains(.h264) {
            movieOutput.setOutputSettings([AVVideoCodecKey: AVVideoCodecType.h264], for: link)
          }
          if link.isVideoStabilizationSupported { link.preferredVideoStabilizationMode = w.steady ? .auto : .off }
          if link.isVideoMirroringSupported {
            link.automaticallyAdjustsVideoMirroring = false
            link.isVideoMirrored = w.facing == "front" && w.mirror
          }
        }
      } else {
        if let chosen = photoFormat(camera, w.aspect, w.photoSize) {
          try camera.lockForConfiguration()
          camera.activeFormat = chosen
          camera.unlockForConfiguration()
        }
        guard session.canAddOutput(photoOutput) else { throw VaultError("photo output") }
        session.addOutput(photoOutput)
        photoOutput.maxPhotoQualityPrioritization = .quality
        if #available(iOS 16.0, *) {
          if let biggest = wantedDimensions(camera.activeFormat, w.photoSize) { photoOutput.maxPhotoDimensions = biggest }
        } else {
          photoOutput.isHighResolutionCaptureEnabled = true
        }
        if w.live {
          frames.alwaysDiscardsLateVideoFrames = true
          frames.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
          frames.deliversPreviewSizedOutputBuffers = true
          frames.setSampleBufferDelegate(self, queue: frameQueue)
          if session.canAddOutput(frames) {
            session.addOutput(frames)
            if let link = frames.connection(with: .video) {
              if link.isVideoOrientationSupported { link.videoOrientation = .portrait }
              if link.isVideoMirroringSupported { link.automaticallyAdjustsVideoMirroring = false; link.isVideoMirrored = w.facing == "front" }
            }
          }
        }
      }
      session.commitConfiguration()
    } catch {
      session.commitConfiguration()
      return fail(error.localizedDescription)
    }
    device = camera
    zoomBase = CameraDevices.base(camera)
    if (try? camera.lockForConfiguration()) != nil {
      camera.videoZoomFactor = max(camera.minAvailableVideoZoomFactor, min(zoomBase, camera.maxAvailableVideoZoomFactor))
      if camera.isFocusModeSupported(.continuousAutoFocus) { camera.focusMode = .continuousAutoFocus }
      camera.unlockForConfiguration()
    }
    if !session.isRunning { session.startRunning() }
    torch()
    watchZoom(camera)
    DispatchQueue.main.async { self.onModes(["modes": [String]()]) }
  }

  private func fail(_ message: String) {
    DispatchQueue.main.async { self.onCameraError(["message": message]) }
  }

  /// The format whose photos match the chosen shape and size (the largest when "max").
  private func photoFormat(_ camera: AVCaptureDevice, _ aspect: String, _ size: String) -> AVCaptureDevice.Format? {
    let parts = size.split(separator: "x").compactMap { Int64($0) }
    let ratio = parts.count == 2 ? Double(max(parts[0], parts[1])) / Double(min(parts[0], parts[1])) : (aspect == "16:9" ? 16.0 / 9 : 4.0 / 3)
    if parts.count != 2 && aspect != "16:9" { return nil } // the .photo setting is already the largest 4:3
    let wanted = parts.count == 2 ? parts[0] * parts[1] : Int64.max
    var best: (format: AVCaptureDevice.Format, area: Int64, full: Bool)?
    for format in camera.formats {
      guard abs(CameraDevices.ratio(format) - ratio) < 0.05,
            format.videoSupportedFrameRateRanges.contains(where: { $0.maxFrameRate >= 29 }) else { continue }
      let full = CMFormatDescriptionGetMediaSubType(format.formatDescription) == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange
      for d in CameraDevices.photoDimensions(format) {
        let area = Int64(d.width) * Int64(d.height)
        guard area <= wanted || wanted == Int64.max else { continue }
        if best == nil || area > best!.area || (area == best!.area && full && !best!.full) { best = (format, area, full) }
      }
    }
    return best?.format
  }

  @available(iOS 16.0, *)
  private func wantedDimensions(_ format: AVCaptureDevice.Format, _ size: String) -> CMVideoDimensions? {
    let parts = size.split(separator: "x").compactMap { Int64($0) }
    let wanted = parts.count == 2 ? parts[0] * parts[1] : Int64.max
    let list = format.supportedMaxPhotoDimensions.sorted { Int64($0.width) * Int64($0.height) > Int64($1.width) * Int64($1.height) }
    return list.first { Int64($0.width) * Int64($0.height) <= wanted } ?? list.last
  }

  private func torch() {
    guard let camera = device, camera.hasTorch, (try? camera.lockForConfiguration()) != nil else { return }
    let on = video && flash == "on"
    if camera.isTorchModeSupported(on ? .on : .off) { camera.torchMode = on ? .on : .off }
    camera.unlockForConfiguration()
  }

  // MARK: Zoom, focus and how the phone is held

  private func zoom(to factor: CGFloat) {
    guard let camera = device, (try? camera.lockForConfiguration()) != nil else { return }
    camera.videoZoomFactor = max(camera.minAvailableVideoZoomFactor, min(factor, maxZoom(camera)))
    camera.unlockForConfiguration()
  }

  private func maxZoom(_ camera: AVCaptureDevice) -> CGFloat { min(camera.maxAvailableVideoZoomFactor, zoomBase * 15) }

  private func watchZoom(_ camera: AVCaptureDevice) {
    zoomWatch = camera.observe(\.videoZoomFactor, options: [.initial, .new]) { [weak self] camera, _ in
      guard let self else { return }
      let base = self.zoomBase
      let note: [String: Any] = ["ratio": Double(camera.videoZoomFactor / base), "min": Double(camera.minAvailableVideoZoomFactor / base),
                                 "max": Double(self.maxZoom(camera) / base)]
      DispatchQueue.main.async { self.onZoom(note) }
    }
  }

  @objc private func pinched(_ gesture: UIPinchGestureRecognizer) {
    guard let camera = device else { return }
    if gesture.state == .began { pinchStart = camera.videoZoomFactor }
    let target = pinchStart * gesture.scale
    queue.async { self.zoom(to: target) }
  }

  @objc private func tapped(_ gesture: UITapGestureRecognizer) {
    let at = gesture.location(in: self)
    let point = preview.captureDevicePointConverted(fromLayerPoint: at)
    onFocus(["x": Double(at.x), "y": Double(at.y)])
    queue.async {
      guard let camera = self.device, (try? camera.lockForConfiguration()) != nil else { return }
      if camera.isFocusPointOfInterestSupported && camera.isFocusModeSupported(.autoFocus) {
        camera.focusPointOfInterest = point; camera.focusMode = .autoFocus
      }
      if camera.isExposurePointOfInterestSupported && camera.isExposureModeSupported(.autoExpose) {
        camera.exposurePointOfInterest = point; camera.exposureMode = .autoExpose
      }
      camera.unlockForConfiguration()
    }
  }

  /// The screen stays upright, so this follows the phone itself: a photo or video taken sideways is saved sideways.
  private func watchTurn(_ on: Bool) {
    guard motion.isAccelerometerAvailable else { return }
    if !on { motion.stopAccelerometerUpdates(); return }
    motion.accelerometerUpdateInterval = 0.2
    motion.startAccelerometerUpdates(to: .main) { [weak self] data, _ in
      guard let self, let g = data?.acceleration else { return }
      let next: Int
      if abs(g.y) >= abs(g.x) { if abs(g.y) < 0.55 { return }; next = g.y < 0 ? 0 : 180 }
      else { if abs(g.x) < 0.55 { return }; next = g.x < 0 ? 90 : 270 }
      if next == self.turn { return }
      self.turn = next
      self.onTurn(["degrees": next])
    }
  }

  private func orientation() -> AVCaptureVideoOrientation {
    switch turn {
    case 90: return .landscapeRight
    case 180: return .portraitUpsideDown
    case 270: return .landscapeLeft
    default: return .portrait
    }
  }

  // MARK: Photos

  func takePhoto(_ promise: Promise) {
    let held = orientation()
    let chosenFilter = filter
    let flashWanted = flash
    queue.async {
      guard !self.video, self.session.isRunning, self.session.outputs.contains(self.photoOutput) else {
        promise.reject("camera_not_ready", "The camera is not ready yet."); return
      }
      let settings = self.photoOutput.availablePhotoCodecTypes.contains(.jpeg)
        ? AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg]) : AVCapturePhotoSettings()
      settings.photoQualityPrioritization = .quality
      if #available(iOS 16.0, *) { settings.maxPhotoDimensions = self.photoOutput.maxPhotoDimensions }
      else { settings.isHighResolutionPhotoEnabled = true }
      let wantedFlash: AVCaptureDevice.FlashMode = flashWanted == "on" ? .on : flashWanted == "auto" ? .auto : .off
      if self.photoOutput.supportedFlashModes.contains(wantedFlash) { settings.flashMode = wantedFlash }
      if let link = self.photoOutput.connection(with: .video) {
        if link.isVideoOrientationSupported { link.videoOrientation = held }
        if link.isVideoMirroringSupported {
          link.automaticallyAdjustsVideoMirroring = false
          link.isVideoMirrored = self.facing == "front" && self.mirror
        }
      }
      let id = settings.uniqueID
      let job = PhotoJob(promise: promise, filter: chosenFilter, lostLater: { [weak self] in
        DispatchQueue.main.async { self?.onCaptured(["ok": false, "kind": "photo"]) }
      }, done: { [weak self] in self?.queue.async { self?.photoJobs[id] = nil } })
      self.photoJobs[id] = job
      CaptureStore.shot()
      CaptureStore.log.info("photo: pressed, size \(self.photoSize, privacy: .public), saving queue \(CaptureStore.savingCount)")
      self.photoOutput.capturePhoto(with: settings, delegate: job)
    }
  }

  // MARK: Videos

  func startVideo(_ promise: Promise) {
    let held = orientation()
    queue.async {
      guard self.video, self.session.isRunning, self.session.outputs.contains(self.movieOutput) else {
        promise.reject("camera_not_ready", "The camera is not ready yet."); return
      }
      if self.movieOutput.isRecording { promise.resolve(true); return }
      if let link = self.movieOutput.connection(with: .video), link.isVideoOrientationSupported { link.videoOrientation = held }
      let job = MovieJob(started: promise, takenAt: Int64(Date().timeIntervalSince1970 * 1000)) { [weak self] note in
        DispatchQueue.main.async {
          guard let self else { return }
          if let note { self.onCaptured(note) } else { self.onCameraError(["message": "The video could not be stored."]) }
        }
      }
      self.movieJob = job
      self.movieOutput.startRecording(to: CaptureStore.scratch("mov"), recordingDelegate: job)
    }
  }

  func stopVideo(_ promise: Promise) {
    queue.async {
      guard self.movieOutput.isRecording, let job = self.movieJob else {
        promise.reject("not_recording", "Nothing is recording."); return
      }
      job.stopped = promise
      self.movieOutput.stopRecording()
    }
  }

  // MARK: The live filter

  private func showFilterView(_ on: Bool) {
    if !on {
      filterView?.removeFromSuperview(); filterView = nil
      frameLock.lock(); latest = nil; frameLock.unlock()
      return
    }
    if filterView != nil { return }
    guard let metal = MTLCreateSystemDefaultDevice() else { return }
    let view = MTKView(frame: bounds, device: metal)
    view.framebufferOnly = false
    view.isUserInteractionEnabled = false
    view.preferredFramesPerSecond = 30
    view.clearColor = MTLClearColorMake(0, 0, 0, 1)
    view.delegate = self
    commands = metal.makeCommandQueue()
    let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
    renderer = CIContext(mtlDevice: metal, options: [.workingColorSpace: srgb, .cacheIntermediates: false])
    addSubview(view)
    filterView = view
  }

  func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
    guard let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
    let image = PhotoFilters.filtered(CIImage(cvPixelBuffer: pixels), filter)
    frameLock.lock(); latest = image; frameLock.unlock()
  }

  func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {}

  func draw(in view: MTKView) {
    frameLock.lock(); let frame = latest; frameLock.unlock()
    guard let frame, let renderer, let drawable = view.currentDrawable, let buffer = commands?.makeCommandBuffer() else { return }
    let size = view.drawableSize
    let extent = frame.extent
    guard extent.width > 0 && extent.height > 0 else { return }
    let scale = min(size.width / extent.width, size.height / extent.height)
    let fitted = frame.transformed(by: CGAffineTransform(translationX: -extent.minX, y: -extent.minY))
      .transformed(by: CGAffineTransform(scaleX: scale, y: scale))
      .transformed(by: CGAffineTransform(translationX: (size.width - extent.width * scale) / 2, y: (size.height - extent.height * scale) / 2))
    let picture = fitted.composited(over: CIImage(color: .black).cropped(to: CGRect(origin: .zero, size: size)))
    let target = CIRenderDestination(mtlTexture: drawable.texture, commandBuffer: buffer)
    target.isFlipped = true
    _ = try? renderer.startTask(toRender: picture, from: CGRect(origin: .zero, size: size), to: target, at: .zero)
    buffer.present(drawable)
    buffer.commit()
  }
}

/// One photo on its way: the shutter answers as soon as the picture is taken; writing, the filter and locking it
/// away all happen behind, in order, so shots in a row never wait for each other.
private final class PhotoJob: NSObject, AVCapturePhotoCaptureDelegate {
  private let promise: Promise
  private let filter: String
  private let lostLater: () -> Void
  private let done: () -> Void
  private let takenAt = Int64(Date().timeIntervalSince1970 * 1000)
  private let pressed = Date()
  private var answered = false
  private let lock = NSLock()

  init(promise: Promise, filter: String, lostLater: @escaping () -> Void, done: @escaping () -> Void) {
    self.promise = promise; self.filter = filter; self.lostLater = lostLater; self.done = done
  }

  private func since() -> Int { Int(Date().timeIntervalSince(pressed) * 1000) }

  private func answer() {
    lock.lock(); let first = !answered; answered = true; lock.unlock()
    if first { promise.resolve(["saving": true, "kind": "photo"]) }
  }

  private func lost(_ code: String, _ message: String) {
    lock.lock(); let first = !answered; answered = true; lock.unlock()
    if first { promise.reject(code, message) } else { lostLater() }
  }

  func photoOutput(_ output: AVCapturePhotoOutput, willCapturePhotoFor resolvedSettings: AVCaptureResolvedPhotoSettings) {
    CaptureStore.log.info("photo: taken after \(self.since()) ms")
    CaptureStore.shot()
    answer()
  }

  func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
    if let error { lost("photo_failed", error.localizedDescription); return }
    guard let data = photo.fileDataRepresentation() else { lost("photo_failed", "The photo could not be taken."); return }
    let target = CaptureStore.scratch("jpg")
    do {
      try data.write(to: target, options: [.completeFileProtectionUntilFirstUserAuthentication])
      CaptureStore.log.info("photo: written after \(self.since()) ms, \(data.count / 1024) KB")
      CaptureStore.keep(target, mediaType: "image/jpeg", kind: "photo", takenAt: takenAt, filter: filter)
      answer()
    } catch {
      CaptureStore.wipe(target)
      lost("capture_not_stored", error.localizedDescription)
    }
  }

  func photoOutput(_ output: AVCapturePhotoOutput, didFinishCaptureFor resolvedSettings: AVCaptureResolvedPhotoSettings, error: Error?) {
    if let error { lost("photo_failed", error.localizedDescription) }
    done()
  }
}

/// One video: recorded inside VOOL, made into a plain MP4 (no re-encoding), then locked away like a photo.
private final class MovieJob: NSObject, AVCaptureFileOutputRecordingDelegate {
  private let started: Promise
  private let takenAt: Int64
  private let later: ([String: Any]?) -> Void
  private var didStart = false
  var stopped: Promise?

  init(started: Promise, takenAt: Int64, later: @escaping ([String: Any]?) -> Void) {
    self.started = started; self.takenAt = takenAt; self.later = later
  }

  func fileOutput(_ output: AVCaptureFileOutput, didStartRecordingTo fileURL: URL, from connections: [AVCaptureConnection]) {
    didStart = true
    started.resolve(true)
  }

  func fileOutput(_ output: AVCaptureFileOutput, didFinishRecordingTo outputFileURL: URL, from connections: [AVCaptureConnection], error: Error?) {
    let waiting = stopped
    stopped = nil
    // Some endings (VOOL left the screen, the disk filled up) still leave a usable file.
    let usable = error == nil || ((error as NSError?)?.userInfo[AVErrorRecordingSuccessfullyFinishedKey] as? Bool ?? false)
    let written = ((try? FileManager.default.attributesOfItem(atPath: outputFileURL.path))?[.size] as? NSNumber)?.int64Value ?? 0
    guard usable && written > 0 else {
      CaptureStore.wipe(outputFileURL)
      let message = error?.localizedDescription ?? "The video could not be recorded."
      if !didStart { started.reject("video_failed", message) }
      if let waiting { waiting.reject("video_failed", message) } else { later(nil) }
      return
    }
    let note: [String: Any] = ["saving": true, "kind": "video"]
    CaptureStore.working(1)
    if let waiting { waiting.resolve(note) } else { later(note) }
    let at = self.takenAt
    MovieJob.toMp4(outputFileURL) { ready in
      if let ready {
        CaptureStore.wipe(outputFileURL)
        CaptureStore.keep(ready, mediaType: "video/mp4", kind: "video", takenAt: at)
      } else {
        // An MP4 holding QuickTime's own layout: still a normal video file for the computer.
        CaptureStore.keep(outputFileURL, mediaType: "video/mp4", kind: "video", takenAt: at)
      }
      CaptureStore.working(-1)
    }
  }

  static func toMp4(_ movie: URL, _ done: @escaping (URL?) -> Void) {
    let asset = AVURLAsset(url: movie)
    guard let export = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetPassthrough) else { done(nil); return }
    let out = CaptureStore.scratch("mp4")
    export.outputURL = out
    export.outputFileType = .mp4
    export.exportAsynchronously {
      if export.status == .completed { done(out) } else { CaptureStore.wipe(out); done(nil) }
    }
  }
}
