import CryptoKit
import Foundation

/// The phone's sealed link in native code: the same NaCl secretbox (XSalsa20-Poly1305), SHA-256 and HMAC-SHA256
/// the app already uses, byte for byte, so the computer sees no difference. A port of the Android FastCrypto.
/// `selfCheck` runs a known answer once; if it ever disagrees, the app keeps using its JavaScript code instead.
enum FastCrypto {
  static func sha256Hex(_ text: String) -> String {
    hex(Data(SHA256.hash(data: Data(text.utf8))))
  }

  static func hmacSha256Hex(_ key: String, _ text: String) -> String {
    // An empty key works the same as HMAC's 64 zero bytes.
    let mac = HMAC<SHA256>.authenticationCode(for: Data(text.utf8), using: SymmetricKey(data: Data(key.utf8)))
    return hex(Data(mac))
  }

  /// nacl.secretbox(utf8(text), nonce, key), as base64 (16-byte tag, then the ciphertext). "" on bad sizes.
  static func sealBase64(_ text: String, _ nonceHex: String, _ keyHex: String) -> String {
    guard let nonce = unhex(nonceHex), let key = unhex(keyHex), nonce.count == 24, key.count == 32 else { return "" }
    return Data(seal(Array(text.utf8), nonce, key)).base64EncodedString()
  }

  /// nacl.secretbox.open of base64 input, as UTF-8 text; nil when it does not authenticate.
  static func openText(_ base64: String, _ nonceHex: String, _ keyHex: String) -> String? {
    guard let box = Data(base64Encoded: base64), let nonce = unhex(nonceHex), let key = unhex(keyHex),
          nonce.count == 24, key.count == 32, let plain = open(Array(box), nonce, key) else { return nil }
    return String(bytes: plain, encoding: .utf8)
  }

  /// A known answer from the JavaScript library (tweetnacl), checked once.
  static let selfCheck: Bool = {
    let key = "686be7a68a8543e901f84c606255801f5046b992e20b35fa59e535282d4ff23d"
    let nonce = "734338ee326a46c0496cea5d59054553e0b7d92060002446"
    let text = String(repeating: "x", count: 200)
    let sealed = "hqZA4TbF0rjo1daKpqg4vIxtAM14+d8FVCRyU0nmc9D32oY02ml7Hmc3aI4LLkQCTyUEvyzK0A2f8Nud+OOxyPs7yPB4SJGYfQ2JUFGe8IEN3/UYktTPkQXY0zZSl5Ng9vhTN5HboKo/Xi8phJ7VmWxzY4A4Hc2gOLojBSOI+Q6ZIVaMuPvcjE7GouWRISn3KLyUd4g+1savA7pv+8OOU9n6OtlIuwxyHFJMZCb/IMi/Wp9OtAY0lEspC31HApjOgH4cc0bK38lCzFeEPwV7s6slxSqOZueK"
    return sealBase64(text, nonce, key) == sealed && openText(sealed, nonce, key) == text
      && sha256Hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
  }()

  // MARK: - Hex

  static func hex(_ data: Data) -> String {
    let digits = Array("0123456789abcdef".utf8)
    var out = [UInt8](); out.reserveCapacity(data.count * 2)
    for b in data { out.append(digits[Int(b >> 4)]); out.append(digits[Int(b & 15)]) }
    return String(decoding: out, as: UTF8.self)
  }

  static func unhex(_ text: String) -> [UInt8]? {
    let chars = Array(text.utf8)
    guard chars.count % 2 == 0 else { return nil }
    func value(_ c: UInt8) -> UInt8? {
      switch c {
      case 48...57: return c - 48
      case 97...102: return c - 87
      case 65...70: return c - 55
      default: return nil
      }
    }
    var out = [UInt8](); out.reserveCapacity(chars.count / 2)
    var i = 0
    while i < chars.count {
      guard let hi = value(chars[i]), let lo = value(chars[i + 1]) else { return nil }
      out.append(hi << 4 | lo); i += 2
    }
    return out
  }

  // MARK: - XSalsa20-Poly1305

  static func seal(_ m: [UInt8], _ nonce: [UInt8], _ key: [UInt8]) -> [UInt8] {
    var subkey = hsalsa20(Array(nonce[0..<16]), key)
    let n8 = Array(nonce[16..<24])
    var out = [UInt8](repeating: 0, count: 16 + m.count)
    var polyKey = [UInt8](repeating: 0, count: 32)
    xorStream(subkey, n8, m, &out, 16, &polyKey)
    let tag = poly1305(polyKey, out, 16, m.count)
    for i in 0..<16 { out[i] = tag[i] }
    wipe(&subkey); wipe(&polyKey)
    return out
  }

  static func open(_ box: [UInt8], _ nonce: [UInt8], _ key: [UInt8]) -> [UInt8]? {
    guard box.count >= 16 else { return nil }
    var subkey = hsalsa20(Array(nonce[0..<16]), key)
    let n8 = Array(nonce[16..<24])
    var polyKey = firstBlock(subkey, n8)
    let tag = poly1305(polyKey, box, 16, box.count - 16)
    var diff: UInt8 = 0
    for i in 0..<16 { diff |= tag[i] ^ box[i] }
    guard diff == 0 else { wipe(&subkey); wipe(&polyKey); return nil }
    let c = Array(box[16...])
    var plain = [UInt8](repeating: 0, count: c.count)
    var unused = [UInt8](repeating: 0, count: 32)
    xorStream(subkey, n8, c, &plain, 0, &unused)
    wipe(&subkey); wipe(&polyKey)
    return plain
  }

  private static func wipe(_ bytes: inout [UInt8]) { for i in bytes.indices { bytes[i] = 0 } }

  @inline(__always) private static func load(_ b: [UInt8], _ i: Int) -> UInt32 {
    UInt32(b[i]) | UInt32(b[i + 1]) << 8 | UInt32(b[i + 2]) << 16 | UInt32(b[i + 3]) << 24
  }

  @inline(__always) private static func store(_ b: inout [UInt8], _ i: Int, _ v: UInt32) {
    b[i] = UInt8(truncatingIfNeeded: v); b[i + 1] = UInt8(truncatingIfNeeded: v >> 8)
    b[i + 2] = UInt8(truncatingIfNeeded: v >> 16); b[i + 3] = UInt8(truncatingIfNeeded: v >> 24)
  }

  @inline(__always) private static func rotl(_ v: UInt32, _ n: UInt32) -> UInt32 { (v << n) | (v >> (32 - n)) }

  private static let sigma: [UInt32] = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]

  private static func rounds(_ x: inout [UInt32]) {
    for _ in stride(from: 0, to: 20, by: 2) {
      x[4] ^= rotl(x[0] &+ x[12], 7); x[8] ^= rotl(x[4] &+ x[0], 9)
      x[12] ^= rotl(x[8] &+ x[4], 13); x[0] ^= rotl(x[12] &+ x[8], 18)
      x[9] ^= rotl(x[5] &+ x[1], 7); x[13] ^= rotl(x[9] &+ x[5], 9)
      x[1] ^= rotl(x[13] &+ x[9], 13); x[5] ^= rotl(x[1] &+ x[13], 18)
      x[14] ^= rotl(x[10] &+ x[6], 7); x[2] ^= rotl(x[14] &+ x[10], 9)
      x[6] ^= rotl(x[2] &+ x[14], 13); x[10] ^= rotl(x[6] &+ x[2], 18)
      x[3] ^= rotl(x[15] &+ x[11], 7); x[7] ^= rotl(x[3] &+ x[15], 9)
      x[11] ^= rotl(x[7] &+ x[3], 13); x[15] ^= rotl(x[11] &+ x[7], 18)
      x[1] ^= rotl(x[0] &+ x[3], 7); x[2] ^= rotl(x[1] &+ x[0], 9)
      x[3] ^= rotl(x[2] &+ x[1], 13); x[0] ^= rotl(x[3] &+ x[2], 18)
      x[6] ^= rotl(x[5] &+ x[4], 7); x[7] ^= rotl(x[6] &+ x[5], 9)
      x[4] ^= rotl(x[7] &+ x[6], 13); x[5] ^= rotl(x[4] &+ x[7], 18)
      x[11] ^= rotl(x[10] &+ x[9], 7); x[8] ^= rotl(x[11] &+ x[10], 9)
      x[9] ^= rotl(x[8] &+ x[11], 13); x[10] ^= rotl(x[9] &+ x[8], 18)
      x[12] ^= rotl(x[15] &+ x[14], 7); x[13] ^= rotl(x[12] &+ x[15], 9)
      x[14] ^= rotl(x[13] &+ x[12], 13); x[15] ^= rotl(x[14] &+ x[13], 18)
    }
  }

  private static func state(_ key: [UInt8], _ in16: [UInt8]) -> [UInt32] {
    [sigma[0], load(key, 0), load(key, 4), load(key, 8), load(key, 12), sigma[1],
     load(in16, 0), load(in16, 4), load(in16, 8), load(in16, 12),
     sigma[2], load(key, 16), load(key, 20), load(key, 24), load(key, 28), sigma[3]]
  }

  static func hsalsa20(_ in16: [UInt8], _ key: [UInt8]) -> [UInt8] {
    var x = state(key, in16)
    rounds(&x)
    var out = [UInt8](repeating: 0, count: 32)
    for (i, p) in [0, 5, 10, 15, 6, 7, 8, 9].enumerated() { store(&out, 4 * i, x[p]) }
    return out
  }

  private static func block(_ key: [UInt8], _ n8: [UInt8], _ counter: UInt64, _ out64: inout [UInt8]) {
    var in16 = n8 + [UInt8](repeating: 0, count: 8)
    for i in 0..<8 { in16[8 + i] = UInt8(truncatingIfNeeded: counter >> (8 * UInt64(i))) }
    let j = state(key, in16)
    var x = j
    rounds(&x)
    for i in 0..<16 { store(&out64, 4 * i, x[i] &+ j[i]) }
  }

  private static func firstBlock(_ key: [UInt8], _ n8: [UInt8]) -> [UInt8] {
    var b = [UInt8](repeating: 0, count: 64)
    block(key, n8, 0, &b)
    return Array(b[0..<32])
  }

  /// XORs `input` with the Salsa20 stream from byte 32 on (bytes 0..31 of the stream are the Poly1305 key).
  private static func xorStream(_ key: [UInt8], _ n8: [UInt8], _ input: [UInt8], _ out: inout [UInt8], _ outOffset: Int,
                                _ polyKey: inout [UInt8]) {
    var b = [UInt8](repeating: 0, count: 64)
    block(key, n8, 0, &b)
    for i in 0..<32 { polyKey[i] = b[i] }
    var done = 0
    var i = 0
    while i < 32 && done < input.count { out[outOffset + done] = input[done] ^ b[32 + i]; i += 1; done += 1 }
    var counter: UInt64 = 1
    while done < input.count {
      block(key, n8, counter, &b); counter += 1
      let n = min(64, input.count - done)
      for k in 0..<n { out[outOffset + done + k] = input[done + k] ^ b[k] }
      done += n
    }
    wipe(&b)
  }

  // MARK: - Poly1305 (26-bit limbs, as poly1305-donna)

  static func poly1305(_ key: [UInt8], _ m: [UInt8], _ off: Int, _ len: Int) -> [UInt8] {
    let r0 = UInt64(load(key, 0)) & 0x3ffffff
    let r1 = UInt64(load(key, 3) >> 2) & 0x3ffff03
    let r2 = UInt64(load(key, 6) >> 4) & 0x3ffc0ff
    let r3 = UInt64(load(key, 9) >> 6) & 0x3f03fff
    let r4 = UInt64(load(key, 12) >> 8) & 0x00fffff
    let s1 = r1 * 5, s2 = r2 * 5, s3 = r3 * 5, s4 = r4 * 5
    var h0: UInt64 = 0, h1: UInt64 = 0, h2: UInt64 = 0, h3: UInt64 = 0, h4: UInt64 = 0
    var last = [UInt8](repeating: 0, count: 16)
    var pos = off
    let end = off + len
    while pos < end {
      let n = min(16, end - pos)
      var t0: UInt64, t1: UInt64, t2: UInt64, t3: UInt64
      var hibit: UInt64 = 1 << 24
      if n < 16 {
        for k in 0..<16 { last[k] = k < n ? m[pos + k] : 0 }
        last[n] = 1
        hibit = 0
        t0 = UInt64(load(last, 0)); t1 = UInt64(load(last, 4)); t2 = UInt64(load(last, 8)); t3 = UInt64(load(last, 12))
      } else {
        t0 = UInt64(load(m, pos)); t1 = UInt64(load(m, pos + 4)); t2 = UInt64(load(m, pos + 8)); t3 = UInt64(load(m, pos + 12))
      }
      h0 += t0 & 0x3ffffff
      h1 += ((t0 >> 26) | (t1 << 6)) & 0x3ffffff
      h2 += ((t1 >> 20) | (t2 << 12)) & 0x3ffffff
      h3 += ((t2 >> 14) | (t3 << 18)) & 0x3ffffff
      h4 += (t3 >> 8) | hibit
      let d0 = h0 * r0 + h1 * s4 + h2 * s3 + h3 * s2 + h4 * s1
      var d1 = h0 * r1 + h1 * r0 + h2 * s4 + h3 * s3 + h4 * s2
      var d2 = h0 * r2 + h1 * r1 + h2 * r0 + h3 * s4 + h4 * s3
      var d3 = h0 * r3 + h1 * r2 + h2 * r1 + h3 * r0 + h4 * s4
      var d4 = h0 * r4 + h1 * r3 + h2 * r2 + h3 * r1 + h4 * r0
      var c = d0 >> 26; h0 = d0 & 0x3ffffff
      d1 += c; c = d1 >> 26; h1 = d1 & 0x3ffffff
      d2 += c; c = d2 >> 26; h2 = d2 & 0x3ffffff
      d3 += c; c = d3 >> 26; h3 = d3 & 0x3ffffff
      d4 += c; c = d4 >> 26; h4 = d4 & 0x3ffffff
      h0 += c * 5; c = h0 >> 26; h0 &= 0x3ffffff
      h1 += c
      pos += n
    }
    var c = h1 >> 26; h1 &= 0x3ffffff
    h2 += c; c = h2 >> 26; h2 &= 0x3ffffff
    h3 += c; c = h3 >> 26; h3 &= 0x3ffffff
    h4 += c; c = h4 >> 26; h4 &= 0x3ffffff
    h0 += c * 5; c = h0 >> 26; h0 &= 0x3ffffff
    h1 += c
    var g0 = h0 + 5; c = g0 >> 26; g0 &= 0x3ffffff
    var g1 = h1 + c; c = g1 >> 26; g1 &= 0x3ffffff
    var g2 = h2 + c; c = g2 >> 26; g2 &= 0x3ffffff
    var g3 = h3 + c; c = g3 >> 26; g3 &= 0x3ffffff
    let g4 = h4 &+ c &- (1 << 26)
    let mask = (g4 >> 63) &- 1 // all ones when g4 did not go below zero (h >= p): take g
    h0 = (h0 & ~mask) | (g0 & mask)
    h1 = (h1 & ~mask) | (g1 & mask)
    h2 = (h2 & ~mask) | (g2 & mask)
    h3 = (h3 & ~mask) | (g3 & mask)
    h4 = (h4 & ~mask) | (g4 & mask)
    var f0 = (h0 | (h1 << 26)) & 0xffffffff
    var f1 = ((h1 >> 6) | (h2 << 20)) & 0xffffffff
    var f2 = ((h2 >> 12) | (h3 << 14)) & 0xffffffff
    var f3 = ((h3 >> 18) | (h4 << 8)) & 0xffffffff
    f0 += UInt64(load(key, 16))
    f1 += UInt64(load(key, 20)) + (f0 >> 32)
    f2 += UInt64(load(key, 24)) + (f1 >> 32)
    f3 += UInt64(load(key, 28)) + (f2 >> 32)
    var tag = [UInt8](repeating: 0, count: 16)
    store(&tag, 0, UInt32(truncatingIfNeeded: f0)); store(&tag, 4, UInt32(truncatingIfNeeded: f1))
    store(&tag, 8, UInt32(truncatingIfNeeded: f2)); store(&tag, 12, UInt32(truncatingIfNeeded: f3))
    return tag
  }
}
