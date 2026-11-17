import AVFoundation
import AVKit
import ExpoModulesCore
import ImageIO
import UIKit
import UniformTypeIdentifiers

/// Feeds AVFoundation from a sealed Vault stream ("vaultstream://<id>"): it asks for the byte ranges it needs and
/// only those pieces are fetched and opened, in memory. No network, no cache, nothing plain on disk.
final class VaultLoader: NSObject, AVAssetResourceLoaderDelegate {
  static let scheme = "vaultstream"
  private let stream: VaultStreams.Stream
  let queue = DispatchQueue(label: "vool.vault.loader")

  init(stream: VaultStreams.Stream) { self.stream = stream }

  static func asset(for stream: VaultStreams.Stream) -> (AVURLAsset, VaultLoader) {
    let asset = AVURLAsset(url: URL(string: "\(scheme)://\(stream.id)")!)
    let loader = VaultLoader(stream: stream)
    asset.resourceLoader.setDelegate(loader, queue: loader.queue)
    return (asset, loader)
  }

  func resourceLoader(_ resourceLoader: AVAssetResourceLoader, shouldWaitForLoadingOfRequestedResource request: AVAssetResourceLoadingRequest) -> Bool {
    if let info = request.contentInformationRequest {
      info.contentType = UTType(mimeType: stream.mediaType)?.identifier ?? stream.mediaType
      info.contentLength = stream.size
      info.isByteRangeAccessSupported = true
    }
    guard let data = request.dataRequest else { request.finishLoading(); return true }
    let stream = self.stream
    // Waiting for a piece can take a while: never on the loader's own queue.
    DispatchQueue.global(qos: .userInitiated).async {
      var position = data.currentOffset != 0 ? data.currentOffset : data.requestedOffset
      let end = data.requestsAllDataToEndOfResource ? stream.size : min(stream.size, data.requestedOffset + Int64(data.requestedLength))
      while position < end {
        if request.isCancelled { return }
        guard let chunk = VaultStreams.read(stream, position, Int(min(Int64(VaultStreams.piece), end - position))), !chunk.isEmpty else {
          request.finishLoading(with: VaultError("piece did not arrive"))
          return
        }
        data.respond(with: chunk)
        position += Int64(chunk.count)
      }
      request.finishLoading()
    }
    return true
  }
}

/// Small pictures made on the phone from a Vault stream, in memory only: a photo at screen size or as a card
/// preview (HEIC included), a frame from a video, a recording's cover art. A JPEG as base64, or "" when none.
enum VaultPreview {
  private static let photoLimit: Int64 = 96 * 1024 * 1024

  static func make(_ id: String, _ maxEdge: Int, _ kind: String) throws -> String {
    guard let stream = VaultStreams.get(id) else { throw VaultError("closed") }
    let edge = min(max(maxEdge, 64), 4096)
    let image: CGImage?
    switch kind {
    case "photo": image = photo(stream, edge)
    case "video": image = frame(stream, edge)
    case "audio": image = cover(stream, edge)
    default: image = nil
    }
    guard let image else { return "" }
    var quality: CGFloat = edge <= 512 ? 0.78 : 0.88
    var jpeg = UIImage(cgImage: image).jpegData(compressionQuality: quality) ?? Data()
    // a card preview stays small enough for the computer to keep beside the item
    while edge <= 512 && jpeg.count > 150 * 1024 && quality > 0.4 {
      quality -= 0.15
      jpeg = UIImage(cgImage: image).jpegData(compressionQuality: quality) ?? jpeg
    }
    return jpeg.base64EncodedString()
  }

  private static func photo(_ stream: VaultStreams.Stream, _ edge: Int) -> CGImage? {
    guard stream.size <= photoLimit else { return nil }
    var bytes = Data(capacity: Int(stream.size))
    var at: Int64 = 0
    while at < stream.size {
      guard let chunk = VaultStreams.read(stream, at, VaultStreams.piece), !chunk.isEmpty else { return nil }
      bytes.append(chunk); at += Int64(chunk.count)
    }
    defer { bytes.resetBytes(in: 0..<bytes.count) }
    return downscaled(bytes, edge)
  }

  /// Decodes at about `edge` on the long side, upright, without holding the full-size picture.
  static func downscaled(_ bytes: Data, _ edge: Int) -> CGImage? {
    guard let source = CGImageSourceCreateWithData(bytes as CFData, [kCGImageSourceShouldCache: false] as CFDictionary) else { return nil }
    let options: [CFString: Any] = [
      kCGImageSourceCreateThumbnailFromImageAlways: true,
      kCGImageSourceCreateThumbnailWithTransform: true,
      kCGImageSourceThumbnailMaxPixelSize: edge,
      kCGImageSourceShouldCacheImmediately: true,
    ]
    return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
  }

  private static func frame(_ stream: VaultStreams.Stream, _ edge: Int) -> CGImage? {
    let (asset, loader) = VaultLoader.asset(for: stream)
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.maximumSize = CGSize(width: edge, height: edge)
    let image = try? generator.copyCGImage(at: CMTime(seconds: 1, preferredTimescale: 600), actualTime: nil)
    withExtendedLifetime(loader) {}
    return image
  }

  private static func cover(_ stream: VaultStreams.Stream, _ edge: Int) -> CGImage? {
    let (asset, loader) = VaultLoader.asset(for: stream)
    defer { withExtendedLifetime(loader) {} }
    let items = AVMetadataItem.metadataItems(from: asset.commonMetadata, filteredByIdentifier: .commonIdentifierArtwork)
    guard let data = items.first?.dataValue else { return nil }
    return downscaled(data, edge)
  }
}

/// Plays a video or a recording from a Vault stream, with the system player's own controls.
final class VaultPlayerView: ExpoView {
  let onPlayerError = EventDispatcher()
  let onPlayerReady = EventDispatcher()
  private let controller = AVPlayerViewController()
  private var loader: VaultLoader?
  private var watch: NSKeyValueObservation?
  private var streamId = ""

  required init(appContext: AppContext? = nil) {
    super.init(appContext: appContext)
    clipsToBounds = true
    controller.view.frame = bounds
    controller.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
    addSubview(controller.view)
  }

  func setStream(_ id: String) {
    if id == streamId { return }
    release()
    streamId = id
    guard !id.isEmpty, let stream = VaultStreams.get(id) else { return }
    let (asset, loader) = VaultLoader.asset(for: stream)
    self.loader = loader
    let item = AVPlayerItem(asset: asset)
    watch = item.observe(\.status) { [weak self] item, _ in
      DispatchQueue.main.async {
        if item.status == .readyToPlay { self?.onPlayerReady(["ready": true]) }
        if item.status == .failed { self?.onPlayerError(["message": item.error?.localizedDescription ?? "error"]) }
      }
    }
    let player = AVPlayer(playerItem: item)
    controller.player = player
    player.play()
  }

  func release() {
    controller.player?.pause()
    controller.player = nil
    watch = nil
    loader = nil
  }

  override func removeFromSuperview() {
    release()
    streamId = ""
    super.removeFromSuperview()
  }
}
