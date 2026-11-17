import CryptoKit
import Foundation
import os
import Security

/// VOOL's private capture store on iPhone and iPad, the same design and file format as on Android (VCAP2).
///
/// Nothing here is ever written to Photos or shared storage. Each capture gets its own random file key; that key
/// is sealed by a key of its own kept in the iPhone's Keychain (this device only, never in a backup). The file is
/// encrypted in 768 KiB pieces (AES-256-GCM, a fresh nonce per piece, the piece number bound in), so a long video
/// is never held in memory and any piece can be read back on its own when it is sent to the computer. The SHA-256
/// of the original bytes is computed while encrypting; the computer must answer with the same hash before the
/// phone deletes.
///
/// File layout: "VCAP2\n" | sealed-key nonce (12) | sealed key (48) | piece... where each piece is
/// nonce (12) | ciphertext | tag (16).
///
/// Erasing: removing a capture destroys its Keychain key first (crypto-erasure), so whatever the flash keeps of
/// the deleted file can never be opened again, even by VOOL. A small "gone" note (hash only) then waits until the
/// computer has recorded the deletion.
final class CaptureStore {
  static let piece = 768 * 1024
  private static let nonceBytes = 12
  private static let tagBytes = 16
  private static let sealedKeyBytes = 32 + 16
  private static let magic = Data("VCAP2\n".utf8)
  private static let header = magic.count + nonceBytes + sealedKeyBytes
  private static let service = "systems.vool.capture"
  private static let fileAlias = "vool_capture_f_"
  private static let quiet: TimeInterval = 2.5
  private static let wipeLimit: Int64 = 256 * 1024 * 1024
  static let log = Logger(subsystem: "systems.vool.companion", category: "VoolCapture")

  struct Item {
    let id: String
    let mediaType: String
    let kind: String
    let size: Int64
    let sha256: String
    let takenAt: Int64

    var map: [String: Any] {
      ["id": id, "mediaType": mediaType, "kind": kind, "size": Double(size), "sha256": sha256, "takenAt": Double(takenAt)]
    }
  }

  /// One place to change a capture before it is locked away (for example optional compression). Gets the plain
  /// file and its media type; answers the file to store (the same one, or a new one). Nil stores what was captured.
  static var beforeStore: ((URL, String) throws -> URL)?

  /// Told when a capture has been locked away ("ok" true with the item) or could not be ("ok" false).
  static var onStored: (([String: Any]) -> Void)?

  static var dir: URL {
    let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    let url = base.appendingPathComponent("vool-capture", isDirectory: true)
    if !FileManager.default.fileExists(atPath: url.path) {
      try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true,
                                               attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
    }
    var values = URLResourceValues(); values.isExcludedFromBackup = true
    var marked = url; try? marked.setResourceValues(values)
    return url
  }

  private static func file(_ name: String) -> URL { dir.appendingPathComponent(name) }
  private static func exists(_ url: URL) -> Bool { FileManager.default.fileExists(atPath: url.path) }
  private static func size(_ url: URL) -> Int64 {
    ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.int64Value ?? 0
  }
  private static func modified(_ url: URL) -> Date {
    ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date) ?? .distantPast
  }
  private static func remove(_ url: URL) { try? FileManager.default.removeItem(at: url) }
  private static func names() -> [String] { (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? [] }
  private static func newId() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }
  private static func nowMs() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }

  /// Where the camera or recorder writes before the bytes are locked away. Never shared storage.
  static func scratch(_ ending: String) -> URL { file("raw-\(newId()).\(ending)") }

  /// Working files for a change made in beforeStore (for example a compressed copy). Swept at start.
  static func work(_ ending: String) -> URL { file("work-\(newId()).\(ending)") }

  /// The media type a stored file really is, from its ending (a change may turn a JPEG into HEIC).
  static func typeFor(_ url: URL, _ fallback: String) -> String {
    switch url.pathExtension.lowercased() {
    case "jpg", "jpeg": return "image/jpeg"
    case "heic": return "image/heic"
    case "mp4": return "video/mp4"
    case "m4a": return "audio/mp4"
    case "ogg": return "audio/ogg"
    default: return fallback
    }
  }

  // Sealing a fresh capture is quick and never waits; the slower work (filter, compression) runs one at a time
  // at background priority, so the camera and the screen stay smooth.
  private static let sealer = DispatchQueue(label: "vool.capture.sealer", qos: .userInitiated)
  private static let saver = DispatchQueue(label: "vool.capture.saver", qos: .utility)
  private static let guardLock = NSRecursiveLock()
  private static let countLock = NSLock()
  private static var saving = 0
  private static var lastShot = Date.distantPast

  /// How many captures are still being locked away.
  static var savingCount: Int { countLock.lock(); defer { countLock.unlock() }; return saving }
  /// Work outside the store that will end in keep() (a video being made ready), counted as saving meanwhile.
  static func working(_ change: Int) { countLock.lock(); saving = max(0, saving + change); countLock.unlock() }

  /// The shutter was just pressed: heavy saving work waits for a quiet moment.
  static func shot() { countLock.lock(); lastShot = Date(); countLock.unlock() }

  private static func waitForQuiet() {
    let until = Date().addingTimeInterval(20)
    while true {
      countLock.lock(); let last = lastShot; countLock.unlock()
      if Date().timeIntervalSince(last) >= quiet || Date() >= until { return }
      Thread.sleep(forTimeInterval: 0.2)
    }
  }

  private static func jobFor(_ raw: URL) -> URL { raw.deletingLastPathComponent().appendingPathComponent(raw.lastPathComponent + ".job") }

  private static func jobText(_ mediaType: String, _ kind: String, _ takenAt: Int64, _ filter: String, _ ending: String) -> Data {
    let o: [String: Any] = ["mediaType": mediaType, "kind": kind, "takenAt": Double(takenAt), "filter": filter, "ending": ending]
    return (try? JSONSerialization.data(withJSONObject: o)) ?? Data()
  }

  private static func readJson(_ url: URL) -> [String: Any]? {
    guard let data = try? Data(contentsOf: url) else { return nil }
    return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
  }

  private static func number(_ value: Any?) -> Int64 { Int64((value as? NSNumber)?.doubleValue ?? 0) }

  /// Hands a fresh capture to the store and returns at once, so the camera never waits. The plain file is sealed
  /// straight away into a "held" file under its own Keychain key and wiped; the step that changes a capture
  /// (filter, compression) opens one held capture at a time in a working file that is wiped right after. A note
  /// next to each step lets everything finish after VOOL was closed: nothing is lost, plain leftovers are wiped.
  static func keep(_ captured: URL, mediaType: String, kind: String, takenAt: Int64, filter: String = "none") {
    let note = jobFor(captured)
    if !exists(note) { try? jobText(mediaType, kind, takenAt, filter, captured.pathExtension).write(to: note) }
    working(1)
    sealer.async {
      var id: String?
      do { id = try hold(captured, mediaType, kind, takenAt, filter) } catch {
        log.warning("could not seal a fresh capture at once; it is saved directly")
      }
      saver.async {
        defer { working(-1) }
        do {
          let item: Item
          if let id { item = try finishHeld(id) } else {
            waitForQuiet()
            if filter != "none" { PhotoFilters.apply(captured, filter) }
            item = try ingest(captured, mediaType: mediaType, kind: kind, takenAt: takenAt)
            remove(note)
          }
          onStored?(item.map.merging(["ok": true]) { a, _ in a })
        } catch {
          onStored?(["ok": false, "kind": kind, "message": error.localizedDescription])
        }
      }
    }
  }

  private static func holdKey(_ id: String) -> String { "h\(id)" }

  /// Seals a plain capture into hold-<id>.enc under its own Keychain key, then wipes the plain file.
  private static func hold(_ plain: URL, _ mediaType: String, _ kind: String, _ takenAt: Int64, _ filter: String) throws -> String {
    let started = Date()
    guard exists(plain), size(plain) > 0 else { throw VaultError("Nothing was captured.") }
    let id = newId()
    let job = file("hold-\(id).job")
    let part = file("hold-\(id).enc.part")
    let ending = plain.pathExtension.isEmpty ? "bin" : plain.pathExtension
    try jobText(mediaType, kind, takenAt, filter, ending).write(to: job)
    do {
      _ = try sealFile(plain, part, holdKey(id))
      try FileManager.default.moveItem(at: part, to: file("hold-\(id).enc"))
    } catch {
      remove(part); _ = destroyKey(holdKey(id)); remove(job)
      throw error
    }
    wipe(plain)
    remove(jobFor(plain))
    log.info("held: \(kind, privacy: .public) sealed in \(Int(Date().timeIntervalSince(started) * 1000)) ms, plain left 0")
    return id
  }

  /// Opens one held capture, applies its change (filter, compression), locks it away, drops the held copy.
  private static func finishHeld(_ id: String) throws -> Item {
    let job = file("hold-\(id).job")
    guard let o = readJson(job) else { throw VaultError("The capture note is damaged.") }
    // Already locked away before VOOL was stopped: only the held copy is left to drop.
    if let done = stored(id) { dropHold(id); return done }
    waitForQuiet()
    let work = work((o["ending"] as? String) ?? "bin")
    defer { if exists(work) { wipe(work) } }
    try unsealFile(file("hold-\(id).enc"), holdKey(id), work)
    let filter = (o["filter"] as? String) ?? "none"
    if filter != "none" { PhotoFilters.apply(work, filter) }
    let item = try ingest(work, mediaType: (o["mediaType"] as? String) ?? "application/octet-stream", kind: (o["kind"] as? String) ?? "photo",
                          takenAt: number(o["takenAt"]), fixedId: id)
    dropHold(id)
    log.info("stored: \(item.kind, privacy: .public), held left \(heldCount())")
    return item
  }

  private static func itemFrom(_ o: [String: Any]) -> Item? {
    guard let id = o["id"] as? String, let mediaType = o["mediaType"] as? String, let kind = o["kind"] as? String,
          let sha = o["sha256"] as? String else { return nil }
    return Item(id: id, mediaType: mediaType, kind: kind, size: number(o["size"]), sha256: sha, takenAt: number(o["takenAt"]))
  }

  private static func stored(_ id: String) -> Item? {
    guard exists(file("\(id).enc")), let o = readJson(file("\(id).json")) else { return nil }
    return itemFrom(o)
  }

  /// The held copy's own key is destroyed first, so what the flash keeps of it can never be opened.
  private static func dropHold(_ id: String) {
    _ = destroyKey(holdKey(id))
    remove(file("hold-\(id).enc"))
    remove(file("hold-\(id).job"))
  }

  private static func heldCount() -> Int { names().filter { $0.hasPrefix("hold-") && $0.hasSuffix(".enc") }.count }

  private static func isPlainLeftover(_ name: String) -> Bool {
    (name.hasPrefix("raw-") && !name.hasSuffix(".job")) || name.hasPrefix("work-") || name.hasSuffix(".tmp")
  }

  /// Overwrites a plain file before deleting it (best effort: flash memory may keep old blocks).
  static func wipe(_ url: URL) {
    let length = size(url)
    if length > 0 && length <= wipeLimit, let handle = try? FileHandle(forUpdating: url) {
      let zeros = Data(count: 1 << 16)
      var left = length
      while left > 0 {
        let n = Int(min(left, Int64(zeros.count)))
        do { try handle.write(contentsOf: zeros.prefix(n)) } catch { break }
        left -= Int64(n)
      }
      try? handle.synchronize(); try? handle.close()
    }
    remove(url)
  }

  /// At start: wipe plain leftovers, seal any plain capture that was never sealed, and finish every held capture.
  /// Only while nothing is being saved, so a running save is never touched.
  static func resume() {
    guardLock.lock(); defer { guardLock.unlock() }
    if savingCount > 0 { return }
    let all = names()
    let plainBefore = all.filter(isPlainLeftover).count
    let heldBefore = all.filter { $0.hasPrefix("hold-") && $0.hasSuffix(".enc") }.count
    var rescued = 0
    // 1. Half-done work: wiped. A held copy is only ever complete or a .part, never plain.
    for name in all {
      if name.hasPrefix("work-") || name.hasSuffix(".tmp") { wipe(file(name)) } else if name.hasSuffix(".part") { remove(file(name)) }
    }
    // 2. Held captures: finished in order. A note without its sealed file belonged to a seal cut short.
    for name in all where name.hasPrefix("hold-") && name.hasSuffix(".job") {
      let id = String(name.dropFirst("hold-".count).dropLast(".job".count))
      if !exists(file("hold-\(id).enc")) { remove(file(name)); continue }
      working(1)
      saver.async {
        defer { working(-1) }
        do { onStored?(try finishHeld(id).map.merging(["ok": true]) { a, _ in a }) } catch {
          log.warning("a held capture could not be finished; it stays held for the next start")
        }
      }
    }
    sweepKeys()
    // 3. Plain captures that were never sealed: sealed now (a photo only if it is a whole photo).
    let now = Date()
    for name in all where name.hasPrefix("raw-") && !name.hasSuffix(".job") {
      let raw = file(name)
      let job = jobFor(raw)
      if size(raw) == 0 { remove(raw); remove(job); continue }
      if let o = readJson(job) {
        keep(raw, mediaType: (o["mediaType"] as? String) ?? "application/octet-stream", kind: (o["kind"] as? String) ?? "photo",
             takenAt: number(o["takenAt"]), filter: (o["filter"] as? String) ?? "none")
        rescued += 1
      } else if exists(job) {
        remove(job)
      } else if raw.pathExtension == "jpg" && wholeJpeg(raw) {
        keep(raw, mediaType: "image/jpeg", kind: "photo", takenAt: Int64(modified(raw).timeIntervalSince1970 * 1000)); rescued += 1
      } else if raw.pathExtension == "jpg" {
        wipe(raw)
      } else if now.timeIntervalSince(modified(raw)) > 600 {
        // a recording cut off mid-way cannot be played; a live recording is never this old
        wipe(raw)
      }
    }
    for name in all where name.hasPrefix("raw-") && name.hasSuffix(".job") && !exists(file(String(name.dropLast(".job".count)))) {
      remove(file(name))
    }
    let found = rescued
    sealer.async {
      let left = names().filter(isPlainLeftover).count
      log.info("start: plain found \(plainBefore), held found \(heldBefore), plain sealed now \(found), plain left \(left)")
    }
  }

  private static func wholeJpeg(_ url: URL) -> Bool {
    guard let handle = try? FileHandle(forReadingFrom: url) else { return false }
    defer { try? handle.close() }
    let length = size(url)
    guard length > 4, let head = try? handle.read(upToCount: 2) else { return false }
    guard (try? handle.seek(toOffset: UInt64(length - 2))) != nil, let tail = try? handle.read(upToCount: 2) else { return false }
    return head == Data([0xFF, 0xD8]) && tail == Data([0xFF, 0xD9])
  }

  // MARK: Keychain keys, one per capture

  private static func keychainKey(_ name: String, create: Bool) throws -> SymmetricKey? {
    let account = fileAlias + name
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account,
      kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var out: CFTypeRef?
    if SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess, let data = out as? Data, data.count == 32 {
      return SymmetricKey(data: data)
    }
    guard create else { return nil }
    var bytes = [UInt8](repeating: 0, count: 32)
    guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { throw VaultError("random") }
    defer { for i in bytes.indices { bytes[i] = 0 } }
    let add: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account,
      kSecValueData as String: Data(bytes), kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
    ]
    let status = SecItemAdd(add as CFDictionary, nil)
    guard status == errSecSuccess else { throw VaultError("The capture key could not be made (\(status)).") }
    return SymmetricKey(data: Data(bytes))
  }

  /// Destroys one capture's own Keychain key. True when it is gone (or never existed).
  @discardableResult
  private static func destroyKey(_ name: String) -> Bool {
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: fileAlias + name]
    let status = SecItemDelete(query as CFDictionary)
    return status == errSecSuccess || status == errSecItemNotFound
  }

  /// Keychain keys whose capture no longer exists (an interrupted removal, or VOOL deleted and installed again).
  static func sweepKeys() {
    let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecReturnAttributes as String: true, kSecMatchLimit as String: kSecMatchLimitAll]
    var out: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess, let rows = out as? [[String: Any]] else { return }
    for row in rows {
      guard let account = row[kSecAttrAccount as String] as? String, account.hasPrefix(fileAlias) else { continue }
      let id = String(account.dropFirst(fileAlias.count))
      let held = id.hasPrefix("h")
      let name = held ? "hold-\(id.dropFirst())" : id
      let inUse = exists(file("\(name).enc")) || exists(file("\(name).enc.part")) || (held && exists(file("\(name).job")))
      if !inUse { destroyKey(id) }
    }
  }

  // MARK: Sealing

  private static func aad(_ name: String, _ index: Int64) -> Data {
    var data = Data((name + ":").utf8)
    var big = index.bigEndian
    withUnsafeBytes(of: &big) { data.append(contentsOf: $0) }
    return data
  }

  private static func readUpTo(_ handle: FileHandle, _ count: Int) throws -> Data {
    var data = Data()
    while data.count < count {
      guard let more = try handle.read(upToCount: count - data.count), !more.isEmpty else { break }
      data.append(more)
    }
    return data
  }

  private static func hex(_ digest: SHA256.Digest) -> String { digest.map { String(format: "%02x", $0) }.joined() }

  /// Seals a plain file (VCAP2) under the Keychain key named `keyName`; answers its size and SHA-256.
  private static func sealFile(_ plain: URL, _ out: URL, _ keyName: String) throws -> (Int64, String) {
    guard let sealer = try keychainKey(keyName, create: true) else { throw VaultError("no key") }
    let fileKey = SymmetricKey(size: .bits256)
    let wrapped = try AES.GCM.seal(fileKey.withUnsafeBytes { Data($0) }, using: sealer, authenticating: Data(keyName.utf8))
    FileManager.default.createFile(atPath: out.path, contents: nil,
                                   attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
    let output = try FileHandle(forWritingTo: out)
    defer { try? output.close() }
    let wrapNonce = wrapped.nonce.withUnsafeBytes { Data($0) }
    try output.write(contentsOf: magic + wrapNonce + wrapped.ciphertext + wrapped.tag)
    let input = try FileHandle(forReadingFrom: plain)
    defer { try? input.close() }
    var hasher = SHA256()
    var total: Int64 = 0
    var index: Int64 = 0
    while true {
      let done: Bool = try autoreleasepool {
        var chunk = try readUpTo(input, piece)
        defer { chunk.resetBytes(in: 0..<chunk.count) }
        if chunk.isEmpty { return true }
        hasher.update(data: chunk)
        total += Int64(chunk.count)
        let box = try AES.GCM.seal(chunk, using: fileKey, nonce: AES.GCM.Nonce(), authenticating: aad(keyName, index))
        let pieceNonce = box.nonce.withUnsafeBytes { Data($0) }
        try output.write(contentsOf: pieceNonce + box.ciphertext + box.tag)
        index += 1
        return chunk.count < piece
      }
      if done { break }
    }
    try output.synchronize()
    return (total, hex(hasher.finalize()))
  }

  /// The file key of a VCAP2 file sealed under `keyName`, read from its header.
  private static func fileKey(_ handle: FileHandle, _ keyName: String) throws -> SymmetricKey {
    let head = try readUpTo(handle, header)
    guard head.count == header, head.prefix(magic.count) == magic else { throw VaultError("This capture cannot be read.") }
    guard let sealer = try keychainKey(keyName, create: false) else { throw VaultError("This capture was erased.") }
    let at = magic.count
    let box = try AES.GCM.SealedBox(nonce: AES.GCM.Nonce(data: head.subdata(in: at..<(at + nonceBytes))),
                                    ciphertext: head.subdata(in: (at + nonceBytes)..<(at + nonceBytes + 32)),
                                    tag: head.subdata(in: (at + nonceBytes + 32)..<header))
    return SymmetricKey(data: try AES.GCM.open(box, using: sealer, authenticating: Data(keyName.utf8)))
  }

  private static func openPiece(_ sealed: Data, _ key: SymmetricKey, _ keyName: String, _ index: Int64) throws -> Data {
    guard sealed.count > nonceBytes + tagBytes else { throw VaultError("This capture is damaged.") }
    let box = try AES.GCM.SealedBox(nonce: AES.GCM.Nonce(data: sealed.prefix(nonceBytes)),
                                    ciphertext: sealed.subdata(in: (sealed.startIndex + nonceBytes)..<(sealed.endIndex - tagBytes)),
                                    tag: sealed.suffix(tagBytes))
    return try AES.GCM.open(box, using: key, authenticating: aad(keyName, index))
  }

  /// Opens a whole VCAP2 file sealed under `keyName` into `out`.
  private static func unsealFile(_ sealed: URL, _ keyName: String, _ out: URL) throws {
    let input = try FileHandle(forReadingFrom: sealed)
    defer { try? input.close() }
    let key = try fileKey(input, keyName)
    FileManager.default.createFile(atPath: out.path, contents: nil,
                                   attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
    let output = try FileHandle(forWritingTo: out)
    defer { try? output.close() }
    var index: Int64 = 0
    while true {
      let done: Bool = try autoreleasepool {
        let sealedPiece = try readUpTo(input, nonceBytes + piece + tagBytes)
        if sealedPiece.isEmpty { return true }
        var plain = try openPiece(sealedPiece, key, keyName, index)
        defer { plain.resetBytes(in: 0..<plain.count) }
        try output.write(contentsOf: plain)
        index += 1
        return false
      }
      if done { break }
    }
  }

  /// Encrypts a plain capture into the store, then wipes the plain file.
  static func ingest(_ captured: URL, mediaType: String, kind: String, takenAt: Int64, fixedId: String? = nil) throws -> Item {
    guardLock.lock(); defer { guardLock.unlock() }
    var plain = captured
    var sealedId: String?
    defer {
      if exists(plain) { wipe(plain) }
      if captured != plain && exists(captured) { wipe(captured) }
      // failed half way: nothing usable was kept, so its key goes too
      if let id = sealedId { remove(file("\(id).enc.part")); destroyKey(id) }
    }
    if let step = beforeStore {
      // A failed or empty change never loses the capture: the original is stored instead.
      let changed = (try? step(plain, mediaType)) ?? plain
      if changed != plain && exists(changed) && size(changed) > 0 { wipe(plain); plain = changed } else if changed != plain { wipe(changed) }
    }
    guard exists(plain), size(plain) > 0 else { throw VaultError("Nothing was captured.") }
    let storedType = plain != captured ? typeFor(plain, mediaType) : mediaType
    let id = fixedId ?? newId()
    sealedId = id
    let sealing = file("\(id).enc.part")
    let (total, sha) = try sealFile(plain, sealing, id)
    let item = Item(id: id, mediaType: storedType, kind: kind, size: total, sha256: sha, takenAt: takenAt)
    let metaPart = file("\(id).json.part")
    try JSONSerialization.data(withJSONObject: item.map).write(to: metaPart, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    do {
      try FileManager.default.moveItem(at: sealing, to: file("\(id).enc"))
      try FileManager.default.moveItem(at: metaPart, to: file("\(id).json"))
    } catch {
      remove(metaPart); remove(file("\(id).enc"))
      throw VaultError("The capture could not be stored.")
    }
    sealedId = nil
    return item
  }

  static func list() -> [Item] {
    let now = Date()
    let all = names()
    for name in all {
      // a capture interrupted before it was locked away (VOOL was stopped mid-way)
      let url = file(name)
      let stale = now.timeIntervalSince(modified(url)) > 6 * 3600
      let queued = name.hasSuffix(".job") || exists(file(name + ".job"))
      if stale && !queued && savingCount == 0 && (name.hasSuffix(".part") || name.hasPrefix("raw-") || name.hasPrefix("work-")) {
        remove(url)
      }
    }
    return all.filter { $0.hasSuffix(".json") && !$0.hasPrefix("gone-") && !$0.hasPrefix("hold-") }.compactMap { name in
      guard let o = readJson(file(name)), let item = itemFrom(o), exists(file("\(item.id).enc")) else { return nil }
      return item
    }.sorted { $0.takenAt < $1.takenAt }
  }

  private static func checkedId(_ id: String) throws -> String {
    guard id.range(of: "^[0-9a-f]{32}$", options: .regularExpression) != nil else { throw VaultError("Unknown capture.") }
    return id
  }

  /// The original bytes from `offset` to the end of the piece that holds it, as base64.
  static func readFrom(_ rawId: String, _ offset: Int64) throws -> String {
    let id = try checkedId(rawId)
    let url = file("\(id).enc")
    let input = try FileHandle(forReadingFrom: url)
    defer { try? input.close() }
    let key = try fileKey(input, id)
    let index = offset / Int64(piece)
    let start = Int64(header) + index * Int64(nonceBytes + piece + tagBytes)
    guard offset >= 0, start < size(url) else { throw VaultError("Nothing left to send.") }
    try input.seek(toOffset: UInt64(start))
    var plain = try openPiece(try readUpTo(input, nonceBytes + piece + tagBytes), key, id, index)
    defer { plain.resetBytes(in: 0..<plain.count) }
    let skip = Int(offset - index * Int64(piece))
    return plain.subdata(in: (plain.startIndex + skip)..<plain.endIndex).base64EncodedString()
  }

  /// Removes a capture the computer has confirmed: its key is destroyed first (crypto-erasure), the sealed key in
  /// the file is overwritten, then the files are deleted. A "gone" note with the hash waits for the computer's record.
  static func remove(_ rawId: String) throws {
    let id = try checkedId(rawId)
    let enc = file("\(id).enc")
    let meta = file("\(id).json")
    let sha = (readJson(meta)?["sha256"] as? String) ?? ""
    if let handle = try? FileHandle(forUpdating: enc) {
      try? handle.seek(toOffset: UInt64(magic.count))
      try? handle.write(contentsOf: Data(count: nonceBytes + sealedKeyBytes))
      try? handle.synchronize(); try? handle.close()
    }
    let keyDestroyed = destroyKey(id) && (try? keychainKey(id, create: false)) == nil
    remove(enc)
    remove(meta)
    if sha.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil {
      let note: [String: Any] = ["sha256": sha, "keyDestroyed": keyDestroyed, "removedAt": Double(nowMs())]
      try? JSONSerialization.data(withJSONObject: note).write(to: file("gone-\(sha).json"))
    }
  }

  /// Deletions the computer has not recorded yet.
  static func gone() -> [[String: Any]] {
    names().filter { $0.hasPrefix("gone-") && $0.hasSuffix(".json") }.compactMap { name in
      guard let o = readJson(file(name)), let sha = o["sha256"] as? String else { remove(file(name)); return nil }
      return ["sha256": sha, "keyDestroyed": (o["keyDestroyed"] as? Bool) ?? false, "removedAt": (o["removedAt"] as? NSNumber)?.doubleValue ?? 0]
    }
  }

  /// The computer recorded this deletion.
  static func goneRecorded(_ sha256: String) {
    if sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil { remove(file("gone-\(sha256).json")) }
  }
}
