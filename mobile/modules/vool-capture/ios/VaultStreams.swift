import CryptoKit
import Foundation
import UIKit

/// VOOL Vault's streams on iPhone and iPad, the same design as on Android: an item from the computer's protected
/// library arrives piece by piece (768 KiB each, over VOOL's sealed link) and is kept only sealed, under a key that
/// lives in memory and dies with the stream. The player reads through `read`, which waits for a piece that has not
/// come yet and asks for it (`onWant`), so a video plays while the rest is still on its way. Nothing plain is
/// written, except a copy the person explicitly saves or shares, which leaves the Vault.
final class VaultStreams {
  static let piece = 768 * 1024
  private static let tag = 16
  private static let waitSeconds: TimeInterval = 45

  final class Stream {
    let id: String
    let size: Int64
    let mediaType: String
    let file: URL
    let key: SymmetricKey
    let pieces: Int
    var have: [Bool]
    let lock = NSCondition()
    var closed = false
    var cachedIndex = -1
    var cached: [UInt8]? = nil

    init(id: String, size: Int64, mediaType: String, file: URL, key: SymmetricKey) {
      self.id = id; self.size = size; self.mediaType = mediaType; self.file = file; self.key = key
      pieces = max(1, Int((size + Int64(VaultStreams.piece) - 1) / Int64(VaultStreams.piece)))
      have = [Bool](repeating: false, count: pieces)
    }

    func length(_ index: Int) -> Int {
      Int(min(size, Int64(index + 1) * Int64(VaultStreams.piece)) - Int64(index) * Int64(VaultStreams.piece))
    }
  }

  private static var streams: [String: Stream] = [:]
  private static let table = NSLock()

  /// Asked when the player waits for a piece that has not arrived: (stream id, piece index).
  static var onWant: ((String, Int) -> Void)?

  private static func folder(_ name: String, in base: URL) -> URL {
    let url = base.appendingPathComponent(name, isDirectory: true)
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    var values = URLResourceValues(); values.isExcludedFromBackup = true
    var marked = url; try? marked.setResourceValues(values)
    return url
  }
  private static var dir: URL {
    folder("vault-stream", in: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0])
  }
  private static var exportDir: URL { folder("vault-out", in: FileManager.default.temporaryDirectory) }

  static func get(_ id: String) -> Stream? { table.lock(); defer { table.unlock() }; return streams[id] }

  /// Leftovers from a run that ended suddenly (their keys are gone, so they are only noise) and old shared copies.
  static func sweep(exportsOlderThan seconds: TimeInterval = 600) {
    let fm = FileManager.default
    table.lock(); let live = Set(streams.values.map { $0.file.lastPathComponent }); table.unlock()
    for name in (try? fm.contentsOfDirectory(atPath: dir.path)) ?? [] where !live.contains(name) {
      try? fm.removeItem(at: dir.appendingPathComponent(name))
    }
    for name in (try? fm.contentsOfDirectory(atPath: exportDir.path)) ?? [] {
      let url = exportDir.appendingPathComponent(name)
      let changed = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
      if Date().timeIntervalSince(changed) > seconds { wipe(url) }
    }
  }

  static func open(size: Int64, mediaType: String) throws -> String {
    guard size >= 1 && size <= 16 * 1024 * 1024 * 1024 else { throw VaultError("size") }
    var raw = [UInt8](repeating: 0, count: 16)
    guard SecRandomCopyBytes(kSecRandomDefault, raw.count, &raw) == errSecSuccess else { throw VaultError("random") }
    let id = raw.map { String(format: "%02x", $0) }.joined()
    let file = dir.appendingPathComponent(id)
    FileManager.default.createFile(atPath: file.path, contents: nil,
                                   attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
    let stream = Stream(id: id, size: size, mediaType: mediaType, file: file, key: SymmetricKey(size: .bits256))
    table.lock(); streams[id] = stream; table.unlock()
    return id
  }

  private static func nonce(_ index: Int) throws -> AES.GCM.Nonce {
    var bytes = [UInt8](repeating: 0, count: 12)
    var n = UInt64(index).bigEndian
    withUnsafeBytes(of: &n) { for (i, b) in $0.enumerated() { bytes[4 + i] = b } }
    return try AES.GCM.Nonce(data: bytes)
  }

  /// One piece arrived (base64). Answers how many pieces the stream now holds.
  static func put(_ id: String, _ index: Int, _ base64: String) throws -> Int {
    guard let stream = get(id) else { throw VaultError("closed") }
    guard index >= 0 && index < stream.pieces else { throw VaultError("index") }
    guard var plain = Data(base64Encoded: base64) else { throw VaultError("data") }
    defer { plain.resetBytes(in: 0..<plain.count) }
    guard plain.count == stream.length(index) else { throw VaultError("length") }
    let box = try AES.GCM.seal(plain, using: stream.key, nonce: nonce(index), authenticating: Data(stream.id.utf8))
    let sealed = box.ciphertext + box.tag
    stream.lock.lock(); defer { stream.lock.unlock() }
    if stream.closed { throw VaultError("closed") }
    if !stream.have[index] {
      let handle = try FileHandle(forWritingTo: stream.file)
      defer { try? handle.close() }
      try handle.seek(toOffset: UInt64(index) * UInt64(piece + tag))
      try handle.write(contentsOf: sealed)
      stream.have[index] = true
    }
    stream.lock.broadcast()
    return stream.have.filter { $0 }.count
  }

  static func held(_ id: String) -> [Int] {
    guard let stream = get(id) else { return [] }
    stream.lock.lock(); defer { stream.lock.unlock() }
    return stream.have.indices.filter { stream.have[$0] }
  }

  /// The plain bytes of one piece, waiting (and asking) for it when it has not come yet; nil when it never does.
  static func piece(_ stream: Stream, _ index: Int) -> [UInt8]? {
    stream.lock.lock(); defer { stream.lock.unlock() }
    if stream.cachedIndex == index, let cached = stream.cached { return cached }
    let deadline = Date().addingTimeInterval(waitSeconds)
    while !stream.have[index] {
      if stream.closed { return nil }
      let left = deadline.timeIntervalSinceNow
      if left <= 0 { return nil }
      onWant?(stream.id, index)
      _ = stream.lock.wait(until: Date().addingTimeInterval(min(left, 3)))
    }
    guard let handle = try? FileHandle(forReadingFrom: stream.file) else { return nil }
    defer { try? handle.close() }
    let count = stream.length(index) + tag
    guard (try? handle.seek(toOffset: UInt64(index) * UInt64(piece + tag))) != nil,
          let sealed = try? handle.read(upToCount: count), sealed.count == count,
          let box = try? AES.GCM.SealedBox(nonce: nonce(index), ciphertext: sealed.prefix(count - tag), tag: sealed.suffix(tag)),
          let plain = try? AES.GCM.open(box, using: stream.key, authenticating: Data(stream.id.utf8)) else { return nil }
    let bytes = [UInt8](plain)
    stream.cached = bytes
    stream.cachedIndex = index
    return bytes
  }

  /// Up to `length` bytes at `position`; empty at the end; nil when a piece cannot be had.
  static func read(_ stream: Stream, _ position: Int64, _ length: Int) -> Data? {
    if position >= stream.size { return Data() }
    let index = Int(position / Int64(piece))
    guard let plain = piece(stream, index) else { return nil }
    let inside = Int(position - Int64(index) * Int64(piece))
    let count = min(length, plain.count - inside)
    return Data(plain[inside..<(inside + count)])
  }

  static func close(_ id: String) {
    table.lock(); let stream = streams.removeValue(forKey: id); table.unlock()
    guard let stream else { return }
    stream.lock.lock()
    stream.closed = true
    stream.cached = nil
    stream.lock.broadcast()
    stream.lock.unlock()
    try? FileManager.default.removeItem(at: stream.file)
  }

  static func closeAll() {
    table.lock(); let ids = Array(streams.keys); table.unlock()
    ids.forEach(close)
  }

  private static func safeName(_ name: String) -> String {
    let bad = CharacterSet(charactersIn: "\\/:*?\"<>|").union(.controlCharacters)
    let clean = String(name.unicodeScalars.map { bad.contains($0) ? "_" : Character($0) }).prefix(120)
    return clean.trimmingCharacters(in: .whitespaces).isEmpty ? "vool-item" : String(clean)
  }

  /// A plain copy of a whole stream, for saving or sharing at the person's request. Throws when pieces are missing.
  static func plainCopy(_ id: String, _ name: String) throws -> URL {
    guard let stream = get(id) else { throw VaultError("closed") }
    let folder = exportDir.appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let out = folder.appendingPathComponent(safeName(name))
    FileManager.default.createFile(atPath: out.path, contents: nil)
    let handle = try FileHandle(forWritingTo: out)
    defer { try? handle.close() }
    for index in 0..<stream.pieces {
      guard let plain = piece(stream, index) else { throw VaultError("incomplete") }
      try handle.write(contentsOf: Data(plain))
    }
    return out
  }

  static func wipe(_ url: URL) {
    if let handle = try? FileHandle(forUpdating: url) {
      let size = (try? handle.seekToEnd()) ?? 0
      try? handle.seek(toOffset: 0)
      let zeros = Data(count: 64 * 1024)
      var left = size
      while left > 0 { let n = min(left, UInt64(zeros.count)); try? handle.write(contentsOf: zeros.prefix(Int(n))); left -= n }
      try? handle.synchronize(); try? handle.close()
    }
    try? FileManager.default.removeItem(at: url)
    let parent = url.deletingLastPathComponent()
    if parent.deletingLastPathComponent().lastPathComponent == "vault-out" { try? FileManager.default.removeItem(at: parent) }
  }
}

struct VaultError: LocalizedError {
  let errorDescription: String?
  init(_ text: String) { errorDescription = text }
}
