import ExpoModulesCore
import Photos
import UIKit

/// VOOL Vault's native side on iPhone and iPad: sealed in-memory-keyed streams, the player, saving or sharing a
/// copy, and the link's fast crypto. iOS has one home-screen icon per app, so the Vault is reached from VOOL and
/// from the long-press shortcut on its icon instead of an icon of its own.
public class VoolVaultModule: Module {
  /// The Vault's work runs on its own queue: a preview waits for pieces that arrive through put().
  private let work = DispatchQueue(label: "vool.vault", qos: .userInitiated, attributes: .concurrent)

  private func off(_ promise: Promise, _ code: String, _ job: @escaping () throws -> Any?) {
    work.async {
      do { promise.resolve(try job()) } catch { promise.reject(code, error.localizedDescription) }
    }
  }

  public func definition() -> ModuleDefinition {
    Name("VoolVault")

    Constants(["pieceBytes": VaultStreams.piece])

    Events("onWant", "onShortcut")

    OnCreate {
      VaultStreams.sweep()
      VaultStreams.onWant = { [weak self] stream, index in self?.sendEvent("onWant", ["stream": stream, "index": index]) }
      VoolShortcutSubscriber.listen { [weak self] link in self?.sendEvent("onShortcut", ["url": link]) }
    }

    OnDestroy {
      VoolShortcutSubscriber.listen(nil)
      VaultStreams.onWant = nil
      VaultStreams.closeAll()
    }

    Function("open") { (size: Double, mediaType: String) throws -> String in
      try VaultStreams.open(size: Int64(size), mediaType: mediaType)
    }
    AsyncFunction("put") { (stream: String, index: Int, base64: String, promise: Promise) in
      self.off(promise, "ERR_VAULT_PUT") { try VaultStreams.put(stream, index, base64) }
    }
    Function("held") { (stream: String) -> [Int] in VaultStreams.held(stream) }

    // The link's hashing and sealing, here instead of in JavaScript (same bytes). Present only when the
    // built-in known answer checks out; otherwise the app keeps its JavaScript version.
    Function("cryptoReady") { FastCrypto.selfCheck }
    Function("cryptoSha256Hex") { (text: String) in FastCrypto.sha256Hex(text) }
    Function("cryptoHmacSha256Hex") { (key: String, text: String) in FastCrypto.hmacSha256Hex(key, text) }
    Function("cryptoSeal") { (text: String, nonceHex: String, keyHex: String) in FastCrypto.sealBase64(text, nonceHex, keyHex) }
    Function("cryptoOpen") { (data: String, nonceHex: String, keyHex: String) in FastCrypto.openText(data, nonceHex, keyHex) ?? "" }

    Function("close") { (stream: String) in VaultStreams.close(stream) }
    Function("closeAll") { VaultStreams.closeAll() }

    AsyncFunction("share") { (stream: String, name: String, title: String, promise: Promise) in
      self.off(promise, "ERR_VAULT_SHARE") {
        let file = try VaultStreams.plainCopy(stream, name)
        self.present([file])
        return nil
      }
    }
    AsyncFunction("shareMany") { (ids: [String], names: [String], title: String, promise: Promise) in
      self.off(promise, "ERR_VAULT_SHARE") {
        guard !ids.isEmpty && ids.count == names.count else { throw VaultError("nothing") }
        let files = try ids.enumerated().map { try VaultStreams.plainCopy($0.element, names[$0.offset]) }
        self.present(files)
        return nil
      }
    }
    /// Photos and videos go to the Photos app; a recording goes through the share sheet (Save to Files).
    AsyncFunction("save") { (stream: String, name: String, promise: Promise) in
      self.off(promise, "ERR_VAULT_SAVE") {
        guard let found = VaultStreams.get(stream) else { throw VaultError("closed") }
        let file = try VaultStreams.plainCopy(stream, name)
        let video = found.mediaType.hasPrefix("video/")
        guard video || found.mediaType.hasPrefix("image/") else { self.present([file]); return "Files" }
        defer { VaultStreams.wipe(file) }
        try Self.addToPhotos(file, video: video)
        return "Photos"
      }
    }

    /// iOS has no second home-screen icon: the Vault is in the long-press menu of the VOOL icon instead.
    Function("homeIconOn") { false }
    /// The long-press pick that opened VOOL ("" when none), taken once.
    Function("takeShortcut") { VoolShortcutSubscriber.take() }
    AsyncFunction("setHomeIcon") { (on: Bool) -> String in
      _ = on
      return "off"
    }

    AsyncFunction("preview") { (stream: String, maxEdge: Int, kind: String, promise: Promise) in
      self.off(promise, "ERR_VAULT_PREVIEW") { try VaultPreview.make(stream, maxEdge, kind) }
    }

    View(VaultPlayerView.self) {
      Events("onPlayerError", "onPlayerReady")
      Prop("stream") { (view: VaultPlayerView, value: String) in view.setStream(value) }
    }
  }

  private static func addToPhotos(_ file: URL, video: Bool) throws {
    let wait = DispatchSemaphore(value: 0)
    var allowed = false
    PHPhotoLibrary.requestAuthorization(for: .addOnly) { status in allowed = status == .authorized || status == .limited; wait.signal() }
    wait.wait()
    guard allowed else { throw VaultError("Photos access is off for VOOL. Allow adding photos in Settings.") }
    var failure: Error?
    let done = DispatchSemaphore(value: 0)
    PHPhotoLibrary.shared().performChanges({
      let request = PHAssetCreationRequest.forAsset()
      request.addResource(with: video ? .video : .photo, fileURL: file, options: nil)
    }) { ok, error in if !ok { failure = error ?? VaultError("not saved") }; done.signal() }
    done.wait()
    if let failure { throw failure }
  }

  /// The system share sheet over VOOL; the copies are wiped when it closes (and swept within minutes anyway).
  private func present(_ files: [URL]) {
    DispatchQueue.main.async {
      guard let top = Self.topController() else { files.forEach(VaultStreams.wipe); return }
      let sheet = UIActivityViewController(activityItems: files, applicationActivities: nil)
      sheet.completionWithItemsHandler = { _, _, _, _ in files.forEach(VaultStreams.wipe) }
      if let pop = sheet.popoverPresentationController {
        pop.sourceView = top.view
        pop.sourceRect = CGRect(x: top.view.bounds.midX, y: top.view.bounds.maxY - 40, width: 1, height: 1)
      }
      top.present(sheet, animated: true)
    }
  }

  static func topController() -> UIViewController? {
    let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
    var top = scenes.flatMap { $0.windows }.first { $0.isKeyWindow }?.rootViewController
    while let next = top?.presentedViewController { top = next }
    return top
  }
}
