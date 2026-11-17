import Darwin
import ExpoModulesCore
import Foundation
import Tor

/// Away from home on iPhone and iPad: reaches the paired computer's hidden address through Tor running inside VOOL,
/// the same as on Android.
///
/// Tor starts only when the app asks for a hidden address (the owner switched "away from home" on and the pairing
/// code carried one). iOS cannot start Tor twice in one run of an app, so "stop" takes Tor off the network instead
/// and the next request puts it back. Only http://<56 chars>.onion is ever sent here, and the name is handed to Tor
/// unresolved over Tor's SOCKS socket, a file inside VOOL that no other app can reach: VOOL makes no lookup and
/// no connection of its own on the phone's network.
public class VoolPrivateRouteModule: Module {
  private static let hidden = try! NSRegularExpression(pattern: "^http://([a-z2-7]{56}\\.onion)(/[A-Za-z0-9/_.-]*)?$")
  private static let replyLimit = 16 * 1024 * 1024

  private let work = DispatchQueue(label: "vool.route", qos: .userInitiated, attributes: .concurrent)
  private let lock = NSLock()
  private var thread: TorThread?
  private var controller: TorController?
  private var configuration: TorConfiguration?
  private var online = false

  public func definition() -> ModuleDefinition {
    Name("VoolPrivateRoute")

    AsyncFunction("request") { (url: String, method: String, headers: [String: String], body: String?, timeoutMs: Double, promise: Promise) in
      self.work.async {
        do {
          let (host, path) = try Self.parse(url)
          guard method == "GET" || method == "POST" else { throw VaultRouteError("method") }
          let deadline = Date().addingTimeInterval(min(max(timeoutMs, 5_000), 600_000) / 1000)
          let socks = try self.readySocket(deadline)
          NSLog("VoolPrivateRoute: asking the computer over the private route")
          let (status, text) = try Self.exchange(socks: socks, host: host, path: path, method: method, headers: headers,
                                                 body: body ?? "", deadline: deadline)
          NSLog("VoolPrivateRoute: answer over the private route: %d", status)
          promise.resolve(["status": status, "body": text])
        } catch {
          NSLog("VoolPrivateRoute: private route failed: %@", error.localizedDescription)
          promise.reject("private_route_failed", error.localizedDescription)
        }
      }
    }

    /// Wake the route and wait until it can carry a request, so the request itself is sent fresh.
    AsyncFunction("ready") { (timeoutMs: Double, promise: Promise) in
      self.work.async {
        do {
          _ = try self.readySocket(Date().addingTimeInterval(min(max(timeoutMs, 5_000), 600_000) / 1000))
          promise.resolve(true)
        } catch {
          promise.reject("private_route_failed", error.localizedDescription)
        }
      }
    }

    AsyncFunction("stop") { () in self.goOffline() }

    AsyncFunction("running") { () -> Bool in
      self.lock.lock(); defer { self.lock.unlock() }
      return self.online
    }

    OnDestroy { self.goOffline() }
  }

  private static func parse(_ url: String) throws -> (String, String) {
    let range = NSRange(url.startIndex..., in: url)
    guard let match = hidden.firstMatch(in: url, range: range), let host = Range(match.range(at: 1), in: url) else {
      throw VaultRouteError("not a hidden address")
    }
    let path = Range(match.range(at: 2), in: url).map { String(url[$0]) } ?? "/"
    return (String(url[host]), path.isEmpty ? "/" : path)
  }

  // MARK: Tor inside VOOL

  private func folder(_ base: FileManager.SearchPathDirectory, _ name: String) -> URL {
    let url = FileManager.default.urls(for: base, in: .userDomainMask)[0].appendingPathComponent(name, isDirectory: true)
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    var values = URLResourceValues(); values.isExcludedFromBackup = true
    var marked = url; try? marked.setResourceValues(values)
    return url
  }

  /// Starts Tor once in this run of VOOL and connects to its control socket.
  private func started(_ deadline: Date) throws -> (TorController, TorConfiguration) {
    lock.lock()
    if let controller, let configuration { lock.unlock(); return (controller, configuration) }
    if thread == nil {
      let config = TorConfiguration()
      config.ignoreMissingTorrc = true
      config.cookieAuthentication = true
      config.avoidDiskWrites = true
      config.clientOnly = true
      // Tor's directory cache survives between runs, so it connects faster next time; it holds no messages.
      config.dataDirectory = folder(.cachesDirectory, "vool-route")
      // A control socket path must stay short: the app's temporary folder keeps it within the limit.
      let temporary = URL(fileURLWithPath: NSTemporaryDirectory())
      config.controlSocket = temporary.appendingPathComponent("vr.sock")
      config.socksURL = temporary.appendingPathComponent("vs.sock")
      config.options = NSMutableDictionary(dictionary: ["ClientUseIPv6": "1"])
      // Leftovers of an earlier run: an old cookie would be read before Tor writes the new one.
      for old in [config.dataDirectory!.appendingPathComponent("control_auth_cookie"), config.controlSocket!, config.socksURL!] {
        try? FileManager.default.removeItem(at: old)
      }
      NSLog("VoolPrivateRoute: starting the private route")
      let next = TorThread(configuration: config)
      next.start()
      thread = next
      pendingConfiguration = config
    }
    let config = pendingConfiguration!
    lock.unlock()

    // The cookie appears once Tor is listening.
    var cookie: Data?
    while cookie == nil {
      cookie = config.cookie
      if cookie != nil { break }
      if Date() > deadline { throw VaultRouteError("private route did not start in time") }
      Thread.sleep(forTimeInterval: 0.2)
    }
    let made = TorController(socketURL: config.controlSocket!)
    while true {
      do { try made.connect(); break } catch {
        if Date() > deadline { throw VaultRouteError("private route did not start in time") }
        Thread.sleep(forTimeInterval: 0.2)
      }
    }
    let wait = DispatchSemaphore(value: 0)
    var allowed = false
    made.authenticate(with: cookie!) { ok, _ in allowed = ok; wait.signal() }
    guard wait.wait(timeout: .now() + max(1, deadline.timeIntervalSinceNow)) == .success, allowed else {
      made.disconnect()
      throw VaultRouteError("private route could not start")
    }
    lock.lock()
    controller = made
    configuration = config
    lock.unlock()
    return (made, config)
  }

  private var pendingConfiguration: TorConfiguration?

  private func info(_ controller: TorController, _ key: String, _ deadline: Date) -> String {
    let wait = DispatchSemaphore(value: 0)
    var answer = ""
    controller.getInfoForKeys([key]) { values in answer = values.first ?? ""; wait.signal() }
    _ = wait.wait(timeout: .now() + min(5, max(0.5, deadline.timeIntervalSinceNow)))
    return answer.trimmingCharacters(in: .whitespacesAndNewlines)
  }

  private func setNetwork(_ controller: TorController, on: Bool) {
    let wait = DispatchSemaphore(value: 0)
    controller.setConfForKey("DisableNetwork", withValue: on ? "0" : "1") { _, _ in wait.signal() }
    _ = wait.wait(timeout: .now() + 5)
  }

  /// Tor's SOCKS socket, once Tor has a working path to the network.
  private func readySocket(_ deadline: Date) throws -> String {
    let (controller, config) = try started(deadline)
    guard let socks = config.socksURL?.path else { throw VaultRouteError("private route could not start") }
    lock.lock(); let wasOnline = online; online = true; lock.unlock()
    if !wasOnline { setNetwork(controller, on: true) }
    while true {
      if FileManager.default.fileExists(atPath: socks) && info(controller, "status/circuit-established", deadline) == "1" {
        NSLog("VoolPrivateRoute: private route ready")
        return socks
      }
      if Date() > deadline { throw VaultRouteError("private route did not connect in time") }
      Thread.sleep(forTimeInterval: 0.4)
    }
  }

  /// Back home: Tor stays in VOOL but leaves the network until the next away request.
  private func goOffline() {
    lock.lock()
    let wasOnline = online
    online = false
    let current = controller
    lock.unlock()
    if wasOnline, let current { setNetwork(current, on: false) }
  }

  // MARK: One HTTP request through Tor's SOCKS port

  private static func exchange(socks: String, host: String, path: String, method: String, headers: [String: String],
                               body: String, deadline: Date) throws -> (Int, String) {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { throw VaultRouteError("no socket") }
    defer { close(fd) }
    var on: Int32 = 1
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
    let left = max(1, deadline.timeIntervalSinceNow)
    var limit = timeval(tv_sec: Int(left), tv_usec: Int32((left - floor(left)) * 1_000_000))
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &limit, socklen_t(MemoryLayout<timeval>.size))
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &limit, socklen_t(MemoryLayout<timeval>.size))
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let pathBytes = Array(socks.utf8)
    guard pathBytes.count < MemoryLayout.size(ofValue: address.sun_path) else { throw VaultRouteError("private route path") }
    withUnsafeMutableBytes(of: &address.sun_path) { room in
      for (i, b) in pathBytes.enumerated() { room[i] = b }
      room[pathBytes.count] = 0
    }
    let joined = withUnsafePointer(to: &address) {
      $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
    }
    guard joined == 0 else { throw VaultRouteError("private route is not listening") }

    // SOCKS5, no login, the hidden name handed over unresolved.
    try send(fd, [5, 1, 0])
    guard try receive(fd, 2) == [5, 0] else { throw VaultRouteError("private route refused") }
    let name = Array(host.utf8)
    try send(fd, [5, 1, 0, 3, UInt8(name.count)] + name + [0, 80])
    let reply = try receive(fd, 4)
    guard reply.count == 4, reply[0] == 5 else { throw VaultRouteError("private route refused") }
    guard reply[1] == 0 else { throw VaultRouteError("the computer could not be reached (\(reply[1]))") }
    switch reply[3] {
    case 1: _ = try receive(fd, 4 + 2)
    case 4: _ = try receive(fd, 16 + 2)
    case 3: let n = try receive(fd, 1); _ = try receive(fd, Int(n.first ?? 0) + 2)
    default: throw VaultRouteError("private route refused")
    }

    let content = Array(body.utf8)
    var head = "\(method) \(path) HTTP/1.1\r\nHost: \(host)\r\n"
    let reserved: Set<String> = ["host", "connection", "content-length", "transfer-encoding"]
    for (key, value) in headers where !reserved.contains(key.lowercased()) {
      if key.contains(where: { $0 == "\r" || $0 == "\n" || $0 == ":" }) || value.contains(where: { $0 == "\r" || $0 == "\n" }) { continue }
      head += "\(key): \(value)\r\n"
    }
    if method == "POST" {
      if !headers.keys.contains(where: { $0.lowercased() == "content-type" }) { head += "Content-Type: application/json\r\n" }
      head += "Content-Length: \(content.count)\r\n"
    }
    head += "Connection: close\r\n\r\n"
    try send(fd, Array(head.utf8) + (method == "POST" ? content : []))

    // The computer closes the connection after its answer; a reply larger than the app ever expects is refused.
    var raw = [UInt8]()
    var chunk = [UInt8](repeating: 0, count: 64 * 1024)
    while true {
      let n = read(fd, &chunk, chunk.count)
      if n == 0 { break }
      if n < 0 { throw VaultRouteError("the answer did not arrive in time") }
      raw.append(contentsOf: chunk[0..<n])
      if raw.count > replyLimit + 64 * 1024 { throw VaultRouteError("reply too large") }
    }
    return try parseReply(raw)
  }

  private static func send(_ fd: Int32, _ bytes: [UInt8]) throws {
    var at = 0
    while at < bytes.count {
      let n = bytes[at...].withUnsafeBufferPointer { write(fd, $0.baseAddress, $0.count) }
      if n <= 0 { throw VaultRouteError("the request could not be sent") }
      at += n
    }
  }

  private static func receive(_ fd: Int32, _ count: Int) throws -> [UInt8] {
    var out = [UInt8](repeating: 0, count: count)
    var at = 0
    while at < count {
      let n = out[at...].withUnsafeMutableBufferPointer { read(fd, $0.baseAddress, $0.count) }
      if n <= 0 { throw VaultRouteError("private route closed") }
      at += n
    }
    return out
  }

  private static func parseReply(_ raw: [UInt8]) throws -> (Int, String) {
    let gap: [UInt8] = [13, 10, 13, 10]
    guard let split = (0...(max(0, raw.count - 4))).first(where: { raw.count >= $0 + 4 && Array(raw[$0..<($0 + 4)]) == gap }) else {
      throw VaultRouteError("the answer was not complete")
    }
    let lines = String(decoding: raw[0..<split], as: UTF8.self).components(separatedBy: "\r\n")
    let parts = lines.first?.split(separator: " ") ?? []
    guard parts.count >= 2, let status = Int(parts[1]) else { throw VaultRouteError("the answer was not understood") }
    var fields: [String: String] = [:]
    for line in lines.dropFirst() {
      guard let colon = line.firstIndex(of: ":") else { continue }
      fields[line[..<colon].lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
    }
    var body = Array(raw[(split + 4)...])
    if fields["transfer-encoding"]?.lowercased().contains("chunked") == true {
      body = try unchunk(body)
    } else if let length = fields["content-length"].flatMap(Int.init) {
      guard body.count >= length else { throw VaultRouteError("the answer was not complete") }
      body = Array(body[0..<length])
    }
    if body.count > replyLimit { throw VaultRouteError("reply too large") }
    return (status, String(decoding: body, as: UTF8.self))
  }

  private static func unchunk(_ data: [UInt8]) throws -> [UInt8] {
    var out = [UInt8]()
    var at = 0
    while at < data.count {
      guard let end = (at..<max(at, data.count - 1)).first(where: { data[$0] == 13 && data[$0 + 1] == 10 }) else { break }
      let sizeText = String(decoding: data[at..<end], as: UTF8.self).split(separator: ";").first.map(String.init) ?? ""
      guard let size = Int(sizeText.trimmingCharacters(in: .whitespaces), radix: 16) else { throw VaultRouteError("the answer was not understood") }
      if size == 0 { break }
      let start = end + 2
      guard start + size <= data.count else { throw VaultRouteError("the answer was not complete") }
      out.append(contentsOf: data[start..<(start + size)])
      at = start + size + 2
    }
    return out
  }
}

struct VaultRouteError: LocalizedError {
  let errorDescription: String?
  init(_ text: String) { errorDescription = text }
}
