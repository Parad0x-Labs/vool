package systems.vool.capture;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Base64;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * The phone's sealed link, done in native code instead of JavaScript: the same NaCl secretbox (XSalsa20-Poly1305),
 * SHA-256 and HMAC-SHA256 the app already uses, byte for byte, so the computer sees no difference. In JavaScript
 * these took one to three seconds per 768 KB piece and held up every tap; here they take milliseconds.
 */
public final class FastCrypto {
  private FastCrypto() {}

  private static final char[] HEX = "0123456789abcdef".toCharArray();

  static String hex(byte[] bytes) {
    char[] out = new char[bytes.length * 2];
    for (int i = 0; i < bytes.length; i++) { out[2 * i] = HEX[(bytes[i] >> 4) & 15]; out[2 * i + 1] = HEX[bytes[i] & 15]; }
    return new String(out);
  }

  static byte[] unhex(String text) {
    if (text.length() % 2 != 0) throw new IllegalArgumentException("hex");
    byte[] out = new byte[text.length() / 2];
    for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(text.substring(2 * i, 2 * i + 2), 16);
    return out;
  }

  public static String sha256Hex(String text) {
    try {
      return hex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
    } catch (Exception error) { throw new IllegalStateException(error); }
  }

  public static String hmacSha256Hex(String key, String text) {
    try {
      Mac mac = Mac.getInstance("HmacSHA256");
      byte[] k = key.getBytes(StandardCharsets.UTF_8);
      // a zero-length key is not accepted by SecretKeySpec; HMAC treats it as 64 zero bytes
      mac.init(new SecretKeySpec(k.length == 0 ? new byte[64] : k, "HmacSHA256"));
      return hex(mac.doFinal(text.getBytes(StandardCharsets.UTF_8)));
    } catch (Exception error) { throw new IllegalStateException(error); }
  }

  /** nacl.secretbox(utf8(text), nonce, key), as base64 (16-byte tag, then the ciphertext). */
  public static String sealBase64(String text, String nonceHex, String keyHex) {
    byte[] plain = text.getBytes(StandardCharsets.UTF_8);
    byte[] sealed = seal(plain, unhex(nonceHex), unhex(keyHex));
    Arrays.fill(plain, (byte) 0);
    return Base64.getEncoder().encodeToString(sealed);
  }

  /** nacl.secretbox.open of base64 input, as UTF-8 text; null when it does not authenticate. */
  public static String openText(String base64, String nonceHex, String keyHex) {
    byte[] box;
    try { box = Base64.getDecoder().decode(base64); } catch (IllegalArgumentException error) { return null; }
    byte[] plain = open(box, unhex(nonceHex), unhex(keyHex));
    if (plain == null) return null;
    String text = new String(plain, StandardCharsets.UTF_8);
    Arrays.fill(plain, (byte) 0);
    return text;
  }

  // -- XSalsa20-Poly1305 -----------------------------------------------------------------------------------------

  static byte[] seal(byte[] m, byte[] nonce, byte[] key) {
    check(nonce, key);
    byte[] subkey = hsalsa20(Arrays.copyOfRange(nonce, 0, 16), key);
    byte[] n8 = Arrays.copyOfRange(nonce, 16, 24);
    byte[] out = new byte[16 + m.length];
    byte[] polyKey = new byte[32];
    xorStream(subkey, n8, m, out, 16, polyKey);
    byte[] tag = poly1305(polyKey, out, 16, m.length);
    System.arraycopy(tag, 0, out, 0, 16);
    Arrays.fill(subkey, (byte) 0); Arrays.fill(polyKey, (byte) 0);
    return out;
  }

  static byte[] open(byte[] box, byte[] nonce, byte[] key) {
    check(nonce, key);
    if (box.length < 16) return null;
    byte[] subkey = hsalsa20(Arrays.copyOfRange(nonce, 0, 16), key);
    byte[] n8 = Arrays.copyOfRange(nonce, 16, 24);
    byte[] polyKey = firstBlock(subkey, n8);
    byte[] tag = poly1305(polyKey, box, 16, box.length - 16);
    if (!MessageDigest.isEqual(tag, Arrays.copyOfRange(box, 0, 16))) { Arrays.fill(subkey, (byte) 0); return null; }
    byte[] c = Arrays.copyOfRange(box, 16, box.length);
    byte[] plain = new byte[c.length];
    xorStream(subkey, n8, c, plain, 0, new byte[32]);
    Arrays.fill(subkey, (byte) 0); Arrays.fill(polyKey, (byte) 0);
    return plain;
  }

  private static void check(byte[] nonce, byte[] key) {
    if (nonce.length != 24 || key.length != 32) throw new IllegalArgumentException("nonce or key size");
  }

  private static int load(byte[] b, int i) {
    return (b[i] & 0xff) | (b[i + 1] & 0xff) << 8 | (b[i + 2] & 0xff) << 16 | (b[i + 3] & 0xff) << 24;
  }

  private static void store(byte[] b, int i, int v) {
    b[i] = (byte) v; b[i + 1] = (byte) (v >>> 8); b[i + 2] = (byte) (v >>> 16); b[i + 3] = (byte) (v >>> 24);
  }

  private static final int[] SIGMA = {0x61707865, 0x3320646e, 0x79622d32, 0x6b206574};

  /** The Salsa20 core: 20 rounds over x; with feed-forward when [add] (Salsa20), without for HSalsa20. */
  private static void rounds(int[] x) {
    for (int i = 0; i < 20; i += 2) {
      x[4] ^= Integer.rotateLeft(x[0] + x[12], 7); x[8] ^= Integer.rotateLeft(x[4] + x[0], 9);
      x[12] ^= Integer.rotateLeft(x[8] + x[4], 13); x[0] ^= Integer.rotateLeft(x[12] + x[8], 18);
      x[9] ^= Integer.rotateLeft(x[5] + x[1], 7); x[13] ^= Integer.rotateLeft(x[9] + x[5], 9);
      x[1] ^= Integer.rotateLeft(x[13] + x[9], 13); x[5] ^= Integer.rotateLeft(x[1] + x[13], 18);
      x[14] ^= Integer.rotateLeft(x[10] + x[6], 7); x[2] ^= Integer.rotateLeft(x[14] + x[10], 9);
      x[6] ^= Integer.rotateLeft(x[2] + x[14], 13); x[10] ^= Integer.rotateLeft(x[6] + x[2], 18);
      x[3] ^= Integer.rotateLeft(x[15] + x[11], 7); x[7] ^= Integer.rotateLeft(x[3] + x[15], 9);
      x[11] ^= Integer.rotateLeft(x[7] + x[3], 13); x[15] ^= Integer.rotateLeft(x[11] + x[7], 18);
      x[1] ^= Integer.rotateLeft(x[0] + x[3], 7); x[2] ^= Integer.rotateLeft(x[1] + x[0], 9);
      x[3] ^= Integer.rotateLeft(x[2] + x[1], 13); x[0] ^= Integer.rotateLeft(x[3] + x[2], 18);
      x[6] ^= Integer.rotateLeft(x[5] + x[4], 7); x[7] ^= Integer.rotateLeft(x[6] + x[5], 9);
      x[4] ^= Integer.rotateLeft(x[7] + x[6], 13); x[5] ^= Integer.rotateLeft(x[4] + x[7], 18);
      x[11] ^= Integer.rotateLeft(x[10] + x[9], 7); x[8] ^= Integer.rotateLeft(x[11] + x[10], 9);
      x[9] ^= Integer.rotateLeft(x[8] + x[11], 13); x[10] ^= Integer.rotateLeft(x[9] + x[8], 18);
      x[12] ^= Integer.rotateLeft(x[15] + x[14], 7); x[13] ^= Integer.rotateLeft(x[12] + x[15], 9);
      x[14] ^= Integer.rotateLeft(x[13] + x[12], 13); x[15] ^= Integer.rotateLeft(x[14] + x[13], 18);
    }
  }

  private static int[] state(byte[] key, byte[] in16) {
    return new int[]{
      SIGMA[0], load(key, 0), load(key, 4), load(key, 8), load(key, 12), SIGMA[1],
      load(in16, 0), load(in16, 4), load(in16, 8), load(in16, 12),
      SIGMA[2], load(key, 16), load(key, 20), load(key, 24), load(key, 28), SIGMA[3]};
  }

  static byte[] hsalsa20(byte[] in16, byte[] key) {
    int[] x = state(key, in16);
    rounds(x);
    byte[] out = new byte[32];
    int[] pick = {0, 5, 10, 15, 6, 7, 8, 9};
    for (int i = 0; i < 8; i++) store(out, 4 * i, x[pick[i]]);
    return out;
  }

  private static void block(byte[] key, byte[] n8, long counter, byte[] out64) {
    byte[] in16 = new byte[16];
    System.arraycopy(n8, 0, in16, 0, 8);
    for (int i = 0; i < 8; i++) in16[8 + i] = (byte) (counter >>> (8 * i));
    int[] j = state(key, in16);
    int[] x = j.clone();
    rounds(x);
    for (int i = 0; i < 16; i++) store(out64, 4 * i, x[i] + j[i]);
  }

  private static byte[] firstBlock(byte[] key, byte[] n8) {
    byte[] b = new byte[64];
    block(key, n8, 0, b);
    return Arrays.copyOf(b, 32);
  }

  /** XORs [in] with the Salsa20 stream from byte 32 on (bytes 0..31 of the stream are the Poly1305 key). */
  private static void xorStream(byte[] key, byte[] n8, byte[] in, byte[] out, int outOffset, byte[] polyKey) {
    byte[] b = new byte[64];
    block(key, n8, 0, b);
    System.arraycopy(b, 0, polyKey, 0, 32);
    int done = 0;
    for (int i = 0; i < 32 && done < in.length; i++, done++) out[outOffset + done] = (byte) (in[done] ^ b[32 + i]);
    long counter = 1;
    while (done < in.length) {
      block(key, n8, counter++, b);
      int n = Math.min(64, in.length - done);
      for (int i = 0; i < n; i++) out[outOffset + done + i] = (byte) (in[done + i] ^ b[i]);
      done += n;
    }
    Arrays.fill(b, (byte) 0);
  }

  // -- Poly1305 (26-bit limbs, as poly1305-donna) -----------------------------------------------------------------

  static byte[] poly1305(byte[] key, byte[] m, int off, int len) {
    long r0 = (load(key, 0)) & 0x3ffffff;
    long r1 = (load(key, 3) >>> 2) & 0x3ffff03;
    long r2 = (load(key, 6) >>> 4) & 0x3ffc0ff;
    long r3 = (load(key, 9) >>> 6) & 0x3f03fff;
    long r4 = (load(key, 12) >>> 8) & 0x00fffff;
    long s1 = r1 * 5, s2 = r2 * 5, s3 = r3 * 5, s4 = r4 * 5;
    long h0 = 0, h1 = 0, h2 = 0, h3 = 0, h4 = 0;
    byte[] last = new byte[16];
    int pos = off, end = off + len;
    while (pos < end) {
      byte[] src = m; int at = pos; long hibit = 1L << 24;
      int n = Math.min(16, end - pos);
      if (n < 16) {
        Arrays.fill(last, (byte) 0);
        System.arraycopy(m, pos, last, 0, n);
        last[n] = 1;
        src = last; at = 0; hibit = 0;
      }
      long t0 = load(src, at) & 0xffffffffL, t1 = load(src, at + 4) & 0xffffffffL;
      long t2 = load(src, at + 8) & 0xffffffffL, t3 = load(src, at + 12) & 0xffffffffL;
      h0 += t0 & 0x3ffffff;
      h1 += ((t0 >>> 26) | (t1 << 6)) & 0x3ffffff;
      h2 += ((t1 >>> 20) | (t2 << 12)) & 0x3ffffff;
      h3 += ((t2 >>> 14) | (t3 << 18)) & 0x3ffffff;
      h4 += (t3 >>> 8) | hibit;
      long d0 = h0 * r0 + h1 * s4 + h2 * s3 + h3 * s2 + h4 * s1;
      long d1 = h0 * r1 + h1 * r0 + h2 * s4 + h3 * s3 + h4 * s2;
      long d2 = h0 * r2 + h1 * r1 + h2 * r0 + h3 * s4 + h4 * s3;
      long d3 = h0 * r3 + h1 * r2 + h2 * r1 + h3 * r0 + h4 * s4;
      long d4 = h0 * r4 + h1 * r3 + h2 * r2 + h3 * r1 + h4 * r0;
      long c = d0 >>> 26; h0 = d0 & 0x3ffffff;
      d1 += c; c = d1 >>> 26; h1 = d1 & 0x3ffffff;
      d2 += c; c = d2 >>> 26; h2 = d2 & 0x3ffffff;
      d3 += c; c = d3 >>> 26; h3 = d3 & 0x3ffffff;
      d4 += c; c = d4 >>> 26; h4 = d4 & 0x3ffffff;
      h0 += c * 5; c = h0 >>> 26; h0 &= 0x3ffffff;
      h1 += c;
      pos += n;
    }
    long c = h1 >>> 26; h1 &= 0x3ffffff;
    h2 += c; c = h2 >>> 26; h2 &= 0x3ffffff;
    h3 += c; c = h3 >>> 26; h3 &= 0x3ffffff;
    h4 += c; c = h4 >>> 26; h4 &= 0x3ffffff;
    h0 += c * 5; c = h0 >>> 26; h0 &= 0x3ffffff;
    h1 += c;
    long g0 = h0 + 5; c = g0 >>> 26; g0 &= 0x3ffffff;
    long g1 = h1 + c; c = g1 >>> 26; g1 &= 0x3ffffff;
    long g2 = h2 + c; c = g2 >>> 26; g2 &= 0x3ffffff;
    long g3 = h3 + c; c = g3 >>> 26; g3 &= 0x3ffffff;
    long g4 = h4 + c - (1L << 26);
    long mask = (g4 >>> 63) - 1; // all ones when g4 >= 0 (h >= p): take g
    h0 = (h0 & ~mask) | (g0 & mask);
    h1 = (h1 & ~mask) | (g1 & mask);
    h2 = (h2 & ~mask) | (g2 & mask);
    h3 = (h3 & ~mask) | (g3 & mask);
    h4 = (h4 & ~mask) | (g4 & mask);
    long f0 = ((h0) | (h1 << 26)) & 0xffffffffL;
    long f1 = ((h1 >>> 6) | (h2 << 20)) & 0xffffffffL;
    long f2 = ((h2 >>> 12) | (h3 << 14)) & 0xffffffffL;
    long f3 = ((h3 >>> 18) | (h4 << 8)) & 0xffffffffL;
    f0 += load(key, 16) & 0xffffffffL;
    f1 += (load(key, 20) & 0xffffffffL) + (f0 >>> 32);
    f2 += (load(key, 24) & 0xffffffffL) + (f1 >>> 32);
    f3 += (load(key, 28) & 0xffffffffL) + (f2 >>> 32);
    byte[] tag = new byte[16];
    store(tag, 0, (int) f0); store(tag, 4, (int) f1); store(tag, 8, (int) f2); store(tag, 12, (int) f3);
    return tag;
  }
}
