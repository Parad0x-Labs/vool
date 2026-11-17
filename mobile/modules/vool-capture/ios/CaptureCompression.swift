import AVFoundation
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Optional compression for private captures on iPhone and iPad, the same settings and rules as on Android. It runs
/// on the plain working copy just before it is sealed, with the iPhone's own encoders only (HEIC photos, HEVC or
/// H.264 video, AAC sound): no FFmpeg, nothing leaves the phone.
///
/// Off by default. When nothing would get much smaller, the quality check fails, or anything goes wrong, the
/// original is stored (with only its place data removed when the person asked for that).
enum CaptureCompression {
  private static let key = "vool_capture_compression_v1"
  private static let receiptsKey = "vool_capture_compression_receipts_v1"
  private static let minPsnr = 30.0
  private static let oneAtATime = NSLock()

  static func install() {
    CaptureStore.beforeStore = { url, mime in process(url, mime) }
  }

  // MARK: Settings (same JSON as Android)

  struct Tuning {
    var photoFormat = "auto", photoQuality = 90, photoMaxEdge = 0
    var videoCodec = "auto", videoBitsPerPixel = 0.10, videoSourceCap = 0.60, videoMbps = 0.0, videoMaxHeight = 0
    var audioCodec = "auto", audioKbps = 96

    static let best = Tuning()
    static let balanced = Tuning(photoQuality: 80, videoBitsPerPixel: 0.07, videoSourceCap: 0.45, audioKbps: 64)
    static let small = Tuning(photoQuality: 70, photoMaxEdge: 2560, videoBitsPerPixel: 0.05, videoSourceCap: 0.30,
                              videoMaxHeight: 1080, audioKbps: 32)

    static func from(_ o: [String: Any]?, _ base: Tuning) -> Tuning {
      guard let o else { return base }
      var t = base
      func num(_ k: String) -> Double? { (o[k] as? NSNumber)?.doubleValue }
      if let v = o["photoFormat"] as? String { t.photoFormat = v }
      if let v = num("photoQuality") { t.photoQuality = Int(v) }
      if let v = num("photoMaxEdge") { t.photoMaxEdge = Int(v) }
      if let v = o["videoCodec"] as? String { t.videoCodec = v }
      if let v = num("videoBitsPerPixel") { t.videoBitsPerPixel = v }
      if let v = num("videoSourceCap") { t.videoSourceCap = v }
      if let v = num("videoMbps") { t.videoMbps = v }
      if let v = num("videoMaxHeight") { t.videoMaxHeight = Int(v) }
      if let v = o["audioCodec"] as? String { t.audioCodec = v }
      if let v = num("audioKbps") { t.audioKbps = Int(v) }
      return t.sanitized()
    }

    func sanitized() -> Tuning {
      var t = self
      if !["auto", "heic", "jpeg"].contains(t.photoFormat) { t.photoFormat = "auto" }
      t.photoQuality = min(max(t.photoQuality, 1), 100)
      t.photoMaxEdge = t.photoMaxEdge <= 0 ? 0 : min(max(t.photoMaxEdge, 640), 16384)
      if !["auto", "hevc", "avc", "av1"].contains(t.videoCodec) { t.videoCodec = "auto" }
      t.videoBitsPerPixel = t.videoBitsPerPixel.isFinite ? min(max(t.videoBitsPerPixel, 0.01), 0.5) : 0.10
      t.videoSourceCap = t.videoSourceCap.isFinite ? min(max(t.videoSourceCap, 0.05), 1.0) : 0.60
      t.videoMbps = t.videoMbps.isFinite && t.videoMbps > 0 ? min(max(t.videoMbps, 0.3), 200) : 0
      t.videoMaxHeight = t.videoMaxHeight <= 0 ? 0 : min(max(t.videoMaxHeight, 240), 4320)
      if !["auto", "opus", "aac"].contains(t.audioCodec) { t.audioCodec = "auto" }
      t.audioKbps = min(max(t.audioKbps, 16), 256)
      return t
    }

    var json: [String: Any] {
      ["photoFormat": photoFormat, "photoQuality": photoQuality, "photoMaxEdge": photoMaxEdge, "videoCodec": videoCodec,
       "videoBitsPerPixel": videoBitsPerPixel, "videoSourceCap": videoSourceCap, "videoMbps": videoMbps,
       "videoMaxHeight": videoMaxHeight, "audioCodec": audioCodec, "audioKbps": audioKbps]
    }
  }

  struct Settings {
    var enabled = false
    var preset = "best"
    var custom = Tuning.best
    var stripLocation = true
    var stripCameraInfo = false
    var minSavingPercent = 10

    var tuning: Tuning {
      switch preset {
      case "balanced": return .balanced
      case "small": return .small
      case "custom": return custom.sanitized()
      default: return .best
      }
    }

    static func from(_ o: [String: Any]) -> Settings {
      var s = Settings()
      s.enabled = (o["enabled"] as? Bool) ?? false
      let p = ((o["preset"] as? String) ?? "best").lowercased()
      s.preset = ["best", "balanced", "small", "custom"].contains(p) ? p : "best"
      s.custom = Tuning.from(o["custom"] as? [String: Any], .best)
      s.stripLocation = (o["stripLocation"] as? Bool) ?? true
      s.stripCameraInfo = (o["stripCameraInfo"] as? Bool) ?? false
      s.minSavingPercent = min(max(((o["minSavingPercent"] as? NSNumber)?.intValue) ?? 10, 0), 90)
      return s
    }

    var json: [String: Any] {
      ["enabled": enabled, "preset": preset, "custom": custom.json, "effective": tuning.json, "stripLocation": stripLocation,
       "stripCameraInfo": stripCameraInfo, "minSavingPercent": minSavingPercent]
    }
  }

  static func load() -> Settings {
    guard let raw = UserDefaults.standard.string(forKey: key), let data = raw.data(using: .utf8),
          let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return Settings() }
    return Settings.from(o)
  }

  static var capabilities: [String: Any] {
    // Every iPhone VOOL runs on (iOS 15.1) encodes HEIC and HEVC in hardware; none encodes AV1 or Ogg Opus.
    ["heic": true, "hevc": true, "hevcHardware": true, "avc": true, "av1": false, "opus": false, "aac": true]
  }

  static func getJson() -> String {
    var o = load().json
    o["capabilities"] = capabilities
    o["summary"] = summary()
    return text(o)
  }

  /// Saves and answers the stored state. Anything unreadable leaves the old settings as they were.
  static func setJson(_ json: String) -> String {
    if let data = json.data(using: .utf8), let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
      UserDefaults.standard.set(text(Settings.from(o).json), forKey: key)
    }
    return getJson()
  }

  static func capabilitiesJson() -> String { text(capabilities) }

  private static func text(_ o: Any) -> String {
    guard let data = try? JSONSerialization.data(withJSONObject: o) else { return "" }
    return String(decoding: data, as: UTF8.self)
  }

  // MARK: Receipts (sizes, codec, time, outcome; no names, paths or content)

  private static func record(_ r: [String: Any]) {
    CaptureStore.log.info("compress: \(text(r), privacy: .public)")
    oneAtATime.lock(); defer { oneAtATime.unlock() }
    var all = (UserDefaults.standard.array(forKey: receiptsKey) as? [[String: Any]]) ?? []
    all.append(r)
    if all.count > 200 { all.removeFirst(all.count - 200) }
    UserDefaults.standard.set(all, forKey: receiptsKey)
  }

  static func summary() -> [String: Any] {
    let all = (UserDefaults.standard.array(forKey: receiptsKey) as? [[String: Any]]) ?? []
    var inB: Int64 = 0, outB: Int64 = 0, done = 0, kept = 0, failed = 0
    for r in all {
      inB += (r["inBytes"] as? NSNumber)?.int64Value ?? 0
      outB += (r["outBytes"] as? NSNumber)?.int64Value ?? 0
      switch r["outcome"] as? String {
      case "compressed": done += 1
      case "failed": failed += 1
      default: kept += 1
      }
    }
    return ["items": all.count, "compressed": done, "keptOriginal": kept, "failed": failed, "inBytes": inB, "outBytes": outB,
            "savedBytes": inB - outB]
  }

  // MARK: The step before sealing

  private static func size(_ url: URL) -> Int64 {
    ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.int64Value ?? 0
  }

  private struct Skip: Error { let reason: String }

  static func process(_ src: URL, _ mime: String) -> URL {
    let s = load()
    guard s.enabled else { return src }
    let kind = mime.hasPrefix("image/") ? "photo" : mime.hasPrefix("video/") ? "video" : mime.hasPrefix("audio/") ? "audio" : ""
    if kind.isEmpty { return src }
    // The hardware encoders are shared: one job at a time (the saver runs one capture at a time anyway).
    let started = Date()
    let inBytes = size(src)
    var receipt: [String: Any] = ["at": Date().timeIntervalSince1970 * 1000, "kind": kind, "inMime": mime, "preset": s.preset,
                                  "inBytes": inBytes]
    var made: [URL] = []
    func newFile(_ ending: String) -> URL { let u = CaptureStore.work(ending); made.append(u); return u }
    var keep: URL?
    defer {
      for u in made where u != keep { CaptureStore.wipe(u) }
      receipt["ms"] = Int(Date().timeIntervalSince(started) * 1000)
      if receipt["outBytes"] == nil { receipt["outBytes"] = size(keep ?? src) }
      record(receipt)
    }
    let t = s.tuning
    do {
      guard inBytes > 0 else { receipt["outcome"] = "kept_original"; receipt["reason"] = "empty input"; return src }
      let out: URL
      do {
        switch kind {
        case "photo":
          let r = try photo(src, t, s, newFile)
          receipt["codec"] = r.format; receipt["width"] = r.width; receipt["height"] = r.height
          receipt["psnrDb"] = round1(try photoPsnr(src, r.url))
          out = r.url
        case "video":
          let r = try video(src, t, s, newFile)
          receipt["codec"] = r.codec; receipt["targetBitrate"] = r.target; receipt["sourceBitrate"] = r.source
          receipt["width"] = r.width; receipt["height"] = r.height; receipt["durationMs"] = r.durationMs
          receipt["psnrDb"] = round1(try videoPsnr(src, r.url, r.durationMs))
          out = r.url
        default:
          let r = try audio(src, t, s, newFile)
          receipt["codec"] = "aac"; receipt["kbps"] = r.kbps
          out = r.url
        }
      } catch let skip as Skip {
        keep = keepOriginal(src, kind, s, &receipt, skip.reason, newFile)
        return keep ?? src
      }
      let outBytes = size(out)
      guard outBytes > 0 else { throw Skip(reason: "no output") }
      if let psnr = receipt["psnrDb"] as? Double, psnr < minPsnr {
        receipt["triedBytes"] = outBytes
        keep = keepOriginal(src, kind, s, &receipt, "quality check: \(psnr) dB < \(minPsnr) dB", newFile)
        return keep ?? src
      }
      if outBytes > inBytes * Int64(100 - s.minSavingPercent) / 100 {
        receipt["triedBytes"] = outBytes
        keep = keepOriginal(src, kind, s, &receipt, "saved only \(100 - outBytes * 100 / inBytes)% (< \(s.minSavingPercent)%)", newFile)
        return keep ?? src
      }
      receipt["outcome"] = "compressed"; receipt["outBytes"] = outBytes; receipt["ending"] = out.pathExtension
      receipt["locationRemoved"] = s.stripLocation
      keep = out
      return out
    } catch {
      receipt["outcome"] = "failed"; receipt["reason"] = error.localizedDescription; receipt["outBytes"] = inBytes
      keep = nil
      return src
    }
  }

  private static func round1(_ v: Double) -> Double { (v * 10).rounded() / 10 }

  /// No worthwhile saving: the original goes, with only its place data taken out when that was asked for.
  private static func keepOriginal(_ src: URL, _ kind: String, _ s: Settings, _ receipt: inout [String: Any], _ reason: String,
                                   _ newFile: (String) -> URL) -> URL {
    receipt["outcome"] = "kept_original"; receipt["reason"] = reason
    guard s.stripLocation else { return src }
    do {
      let out: URL
      if kind == "photo" {
        out = newFile(src.pathExtension.isEmpty ? "jpg" : src.pathExtension)
        try photoWithoutPlace(src, out)
      } else {
        out = newFile(kind == "video" ? "mp4" : "m4a")
        try passthroughWithoutPlace(src, out, video: kind == "video")
      }
      receipt["locationRemoved"] = true; receipt["outBytes"] = size(out)
      return out
    } catch {
      receipt["locationRemoved"] = false; receipt["locationError"] = error.localizedDescription
      return src
    }
  }

  // MARK: Photos: HEIC (or JPEG), metadata rebuilt from a short list

  private static let keepExif: [CFString] = [
    kCGImagePropertyExifDateTimeOriginal, kCGImagePropertyExifDateTimeDigitized, kCGImagePropertyExifOffsetTime,
    kCGImagePropertyExifOffsetTimeOriginal, kCGImagePropertyExifSubsecTimeOriginal, kCGImagePropertyExifExposureTime,
    kCGImagePropertyExifFNumber, kCGImagePropertyExifISOSpeedRatings, kCGImagePropertyExifFocalLength,
    kCGImagePropertyExifFocalLenIn35mmFilm, kCGImagePropertyExifFlash, kCGImagePropertyExifWhiteBalance,
    kCGImagePropertyExifExposureBiasValue, kCGImagePropertyExifApertureValue,
  ]

  private static func metadata(_ source: [CFString: Any], _ s: Settings, _ quality: Int) -> [CFString: Any] {
    var out: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: Double(quality) / 100]
    if let o = source[kCGImagePropertyOrientation] { out[kCGImagePropertyOrientation] = o }
    if let exif = source[kCGImagePropertyExifDictionary] as? [CFString: Any] {
      var kept: [CFString: Any] = [:]
      for k in keepExif { if let v = exif[k] { kept[k] = v } }
      if !s.stripCameraInfo {
        for k in [kCGImagePropertyExifLensMake, kCGImagePropertyExifLensModel] { if let v = exif[k] { kept[k] = v } }
      }
      out[kCGImagePropertyExifDictionary] = kept
    }
    if let tiff = source[kCGImagePropertyTIFFDictionary] as? [CFString: Any] {
      var kept: [CFString: Any] = [:]
      if let v = tiff[kCGImagePropertyTIFFDateTime] { kept[kCGImagePropertyTIFFDateTime] = v }
      if let v = tiff[kCGImagePropertyTIFFOrientation] { kept[kCGImagePropertyTIFFOrientation] = v }
      if !s.stripCameraInfo {
        for k in [kCGImagePropertyTIFFMake, kCGImagePropertyTIFFModel] { if let v = tiff[k] { kept[k] = v } }
      }
      out[kCGImagePropertyTIFFDictionary] = kept
    }
    if !s.stripLocation, let gps = source[kCGImagePropertyGPSDictionary] { out[kCGImagePropertyGPSDictionary] = gps }
    return out
  }

  private static func photo(_ src: URL, _ t: Tuning, _ s: Settings, _ newFile: (String) -> URL) throws -> (url: URL, format: String, width: Int, height: Int) {
    guard let source = CGImageSourceCreateWithURL(src as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
          let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any] else { throw Skip(reason: "not a decodable image") }
    let w = (props[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue ?? 0
    let h = (props[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue ?? 0
    guard w > 0 && h > 0 else { throw Skip(reason: "not a decodable image") }
    let image: CGImage?
    if t.photoMaxEdge > 0 && max(w, h) > t.photoMaxEdge {
      // smaller, pixels kept as stored (the orientation tag still turns them)
      image = CGImageSourceCreateThumbnailAtIndex(source, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceThumbnailMaxPixelSize: t.photoMaxEdge, kCGImageSourceCreateThumbnailWithTransform: false] as CFDictionary)
    } else {
      image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCacheImmediately: false] as CFDictionary)
    }
    guard let picture = image else { throw Skip(reason: "image decode failed") }
    let meta = metadata(props, s, t.photoQuality)
    for format in t.photoFormat == "jpeg" ? ["jpeg"] : t.photoFormat == "heic" ? ["heic"] : ["heic", "jpeg"] {
      let out = newFile(format == "heic" ? "heic" : "jpg")
      let type = (format == "heic" ? UTType.heic : UTType.jpeg).identifier as CFString
      if let target = CGImageDestinationCreateWithURL(out as CFURL, type, 1, nil) {
        CGImageDestinationAddImage(target, picture, meta as CFDictionary)
        if CGImageDestinationFinalize(target) && size(out) > 0 { return (out, format, picture.width, picture.height) }
      }
      CaptureStore.wipe(out)
    }
    throw VaultError("the photo could not be written")
  }

  /// The same picture, untouched, with its place data left out.
  private static func photoWithoutPlace(_ src: URL, _ out: URL) throws {
    guard let source = CGImageSourceCreateWithURL(src as CFURL, nil), let type = CGImageSourceGetType(source),
          let target = CGImageDestinationCreateWithURL(out as CFURL, type, 1, nil) else { throw VaultError("photo") }
    var error: Unmanaged<CFError>?
    let options: [CFString: Any] = [kCGImageMetadataShouldExcludeGPS: true]
    guard CGImageDestinationCopyImageSource(target, source, options as CFDictionary, &error) else {
      throw (error?.takeRetainedValue() as Error?) ?? VaultError("photo")
    }
  }

  private static func lumaSmall(_ url: URL) -> (w: Int, h: Int, y: [Double])? {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceThumbnailMaxPixelSize: 1024, kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary) else { return nil }
    return luma(image)
  }

  private static func luma(_ image: CGImage) -> (w: Int, h: Int, y: [Double])? {
    let w = image.width, h = image.height
    var pixels = [UInt8](repeating: 0, count: w * h * 4)
    let drawn: Bool = pixels.withUnsafeMutableBytes { room in
      guard let ctx = CGContext(data: room.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return false }
      ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
      return true
    }
    guard drawn else { return nil }
    var y = [Double](repeating: 0, count: w * h)
    for i in 0..<(w * h) {
      y[i] = 0.299 * Double(pixels[i * 4]) + 0.587 * Double(pixels[i * 4 + 1]) + 0.114 * Double(pixels[i * 4 + 2])
    }
    return (w, h, y)
  }

  /// PSNR on brightness at about 1024 px; the bigger picture is scaled to the smaller one's size.
  private static func psnr(_ a: (w: Int, h: Int, y: [Double]), _ b: (w: Int, h: Int, y: [Double])) throws -> Double {
    guard abs(Double(a.w) / Double(a.h) - Double(b.w) / Double(b.h)) < 0.02 else { throw VaultError("different shape") }
    let w = min(a.w, b.w), h = min(a.h, b.h)
    var sum = 0.0
    for row in 0..<h {
      for col in 0..<w {
        let pa = a.y[(row * a.h / h) * a.w + col * a.w / w]
        let pb = b.y[(row * b.h / h) * b.w + col * b.w / w]
        sum += (pa - pb) * (pa - pb)
      }
    }
    let mse = sum / Double(w * h)
    return mse <= 0 ? 99 : 10 * log10(255 * 255 / mse)
  }

  private static func photoPsnr(_ original: URL, _ compressed: URL) throws -> Double {
    guard let a = lumaSmall(original) else { throw VaultError("original does not decode") }
    guard let b = lumaSmall(compressed) else { throw VaultError("compressed photo does not decode") }
    return try psnr(a, b)
  }

  // MARK: Video: HEVC (or H.264) at a bitrate from bits per pixel, never above a share of the source

  private static func video(_ src: URL, _ t: Tuning, _ s: Settings, _ newFile: (String) -> URL) throws
    -> (url: URL, codec: String, target: Int, source: Int, width: Int, height: Int, durationMs: Int) {
    let asset = AVURLAsset(url: src)
    guard let track = asset.tracks(withMediaType: .video).first else { throw Skip(reason: "no video track") }
    let natural = track.naturalSize
    let w = Int(abs(natural.width)), h = Int(abs(natural.height))
    guard w > 0 && h > 0 else { throw Skip(reason: "no video size") }
    let fps = Double(track.nominalFrameRate > 0 ? track.nominalFrameRate : 30)
    let sourceRate = Double(track.estimatedDataRate)
    let durationMs = Int(asset.duration.seconds * 1000)
    // output size: the short side at most videoMaxHeight, even numbers
    var outW = w, outH = h
    let short = min(w, h)
    if t.videoMaxHeight > 0 && short > t.videoMaxHeight {
      let k = Double(t.videoMaxHeight) / Double(short)
      outW = Int(Double(w) * k) / 2 * 2; outH = Int(Double(h) * k) / 2 * 2
    }
    let codec = t.videoCodec == "avc" ? "avc" : "hevc"
    let efficiency = codec == "avc" ? 1.5 : 1.0
    let wanted = t.videoMbps > 0 ? t.videoMbps * 1_000_000 : t.videoBitsPerPixel * Double(outW * outH) * fps * efficiency
    let pixelRatio = min(1.0, Double(outW * outH) / Double(w * h))
    let cap = sourceRate > 0 ? sourceRate * t.videoSourceCap * pixelRatio : Double.greatestFiniteMagnitude
    let target = Int(min(max(min(wanted, cap), 300_000), 200_000_000))
    let sameSize = outW == w && outH == h
    if sameSize && sourceRate > 0 && Double(target) >= sourceRate * 0.9 { throw Skip(reason: "source already at or below target bitrate") }

    let out = newFile("mp4")
    let reader = try AVAssetReader(asset: asset)
    let writer = try AVAssetWriter(outputURL: out, fileType: .mp4)
    writer.shouldOptimizeForNetworkUse = false
    if !s.stripLocation { writer.metadata = asset.metadata }

    let videoOut = AVAssetReaderTrackOutput(track: track, outputSettings: [
      kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange])
    videoOut.alwaysCopiesSampleData = false
    guard reader.canAdd(videoOut) else { throw VaultError("video reader") }
    reader.add(videoOut)
    let videoIn = AVAssetWriterInput(mediaType: .video, outputSettings: [
      AVVideoCodecKey: codec == "avc" ? AVVideoCodecType.h264 : AVVideoCodecType.hevc,
      AVVideoWidthKey: outW, AVVideoHeightKey: outH,
      AVVideoScalingModeKey: AVVideoScalingModeResizeAspectFill,
      AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: target, AVVideoExpectedSourceFrameRateKey: fps,
                                        AVVideoAllowFrameReorderingKey: true],
    ])
    videoIn.transform = track.preferredTransform
    videoIn.expectsMediaDataInRealTime = false
    guard writer.canAdd(videoIn) else { throw VaultError("video writer") }
    writer.add(videoIn)

    var audioPair: (AVAssetReaderTrackOutput, AVAssetWriterInput)?
    if let sound = asset.tracks(withMediaType: .audio).first {
      let audioOut = AVAssetReaderTrackOutput(track: sound, outputSettings: [AVFormatIDKey: kAudioFormatLinearPCM])
      let audioIn = AVAssetWriterInput(mediaType: .audio, outputSettings: [
        AVFormatIDKey: kAudioFormatMPEG4AAC, AVNumberOfChannelsKey: 2, AVSampleRateKey: 48_000, AVEncoderBitRateKey: 160_000])
      audioIn.expectsMediaDataInRealTime = false
      if reader.canAdd(audioOut) && writer.canAdd(audioIn) {
        reader.add(audioOut); writer.add(audioIn); audioPair = (audioOut, audioIn)
      }
    }
    try run(reader, writer, [(videoOut, videoIn)] + (audioPair.map { [$0] } ?? []))
    return (out, codec, target, Int(sourceRate), outW, outH, durationMs)
  }

  /// Copies every sample through, all inputs side by side, and waits for the file to be finished.
  private static func run(_ reader: AVAssetReader, _ writer: AVAssetWriter, _ pairs: [(AVAssetReaderTrackOutput, AVAssetWriterInput)]) throws {
    guard reader.startReading() else { throw reader.error ?? VaultError("could not read") }
    guard writer.startWriting() else { throw writer.error ?? VaultError("could not write") }
    writer.startSession(atSourceTime: .zero)
    let group = DispatchGroup()
    for (index, pair) in pairs.enumerated() {
      group.enter()
      let queue = DispatchQueue(label: "vool.compress.\(index)")
      var finished = false
      pair.1.requestMediaDataWhenReady(on: queue) {
        while pair.1.isReadyForMoreMediaData && !finished {
          if let sample = pair.0.copyNextSampleBuffer() {
            if !pair.1.append(sample) { finished = true; pair.1.markAsFinished(); group.leave() }
          } else {
            finished = true; pair.1.markAsFinished(); group.leave()
          }
        }
      }
    }
    group.wait()
    if reader.status == .failed || writer.status == .failed {
      reader.cancelReading(); writer.cancelWriting()
      throw writer.error ?? reader.error ?? VaultError("compression failed")
    }
    let done = DispatchSemaphore(value: 0)
    writer.finishWriting { done.signal() }
    done.wait()
    guard writer.status == .completed else { throw writer.error ?? VaultError("compression failed") }
  }

  private static func videoPsnr(_ original: URL, _ compressed: URL, _ durationMs: Int) throws -> Double {
    func frames(_ url: URL) -> [CGImage] {
      let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url))
      generator.appliesPreferredTrackTransform = true
      generator.maximumSize = CGSize(width: 1024, height: 1024)
      generator.requestedTimeToleranceBefore = .zero
      generator.requestedTimeToleranceAfter = .zero
      let times = durationMs > 0 ? [0.1, 0.5, 0.9].map { Double(durationMs) * $0 / 1000 } : [0]
      return times.compactMap { try? generator.copyCGImage(at: CMTime(seconds: $0, preferredTimescale: 600), actualTime: nil) }
    }
    let a = frames(original), b = frames(compressed)
    guard !b.isEmpty else { throw VaultError("compressed video does not decode") }
    guard a.count == b.count else { throw VaultError("compressed video is shorter") }
    var total = 0.0
    for (x, y) in zip(a, b) {
      guard let la = luma(x), let lb = luma(y) else { throw VaultError("frame") }
      total += try psnr(la, lb)
    }
    return total / Double(a.count)
  }

  // MARK: Sound: AAC in M4A

  private static func audio(_ src: URL, _ t: Tuning, _ s: Settings, _ newFile: (String) -> URL) throws -> (url: URL, kbps: Int) {
    let asset = AVURLAsset(url: src)
    guard let track = asset.tracks(withMediaType: .audio).first else { throw Skip(reason: "no sound track") }
    let sourceRate = Double(track.estimatedDataRate)
    let kbps = t.audioKbps
    if sourceRate > 0 && Double(kbps * 1000) >= sourceRate * 0.9 { throw Skip(reason: "source already at or below target bitrate") }
    let out = newFile("m4a")
    let reader = try AVAssetReader(asset: asset)
    let writer = try AVAssetWriter(outputURL: out, fileType: .m4a)
    if !s.stripLocation { writer.metadata = asset.metadata }
    let pcm = AVAssetReaderTrackOutput(track: track, outputSettings: [AVFormatIDKey: kAudioFormatLinearPCM])
    let aac = AVAssetWriterInput(mediaType: .audio, outputSettings: [
      AVFormatIDKey: kAudioFormatMPEG4AAC, AVNumberOfChannelsKey: 1, AVSampleRateKey: kbps >= 64 ? 48_000 : 32_000,
      AVEncoderBitRateKey: kbps * 1000])
    aac.expectsMediaDataInRealTime = false
    guard reader.canAdd(pcm), writer.canAdd(aac) else { throw VaultError("sound") }
    reader.add(pcm); writer.add(aac)
    try run(reader, writer, [(pcm, aac)])
    return (out, kbps)
  }

  /// A video or a recording copied as it is, without its place data.
  private static func passthroughWithoutPlace(_ src: URL, _ out: URL, video: Bool) throws {
    let asset = AVURLAsset(url: src)
    guard let export = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetPassthrough) else { throw VaultError("copy") }
    export.outputURL = out
    export.outputFileType = video ? .mp4 : .m4a
    export.metadataItemFilter = .forSharing()
    let done = DispatchSemaphore(value: 0)
    export.exportAsynchronously { done.signal() }
    done.wait()
    guard export.status == .completed else { throw export.error ?? VaultError("copy") }
  }
}
