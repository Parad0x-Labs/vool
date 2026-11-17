package systems.vool.capture

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * VOOL's private capture store: photos, videos and recordings kept encrypted inside the app.
 *
 * Nothing here is ever written to the gallery, MediaStore or shared storage. Each capture gets its
 * own random file key; that key is sealed by a key that lives in the phone's Android Keystore and
 * can never leave it. The file is encrypted in 768 KiB pieces (AES-256-GCM, a fresh nonce per
 * piece, the piece number bound in), so a long video is never held in memory and any piece can be
 * read back on its own when it is sent to the computer. The SHA-256 of the original bytes is
 * computed while encrypting; the computer must answer with the same hash before the phone deletes.
 *
 * File layout: "VCAP1\n" | sealed-key nonce (12) | sealed key (48) | piece... where each piece is
 * nonce (12) | ciphertext | tag (16).
 */
object CaptureStore {
  const val PIECE = 768 * 1024
  private const val NONCE = 12
  private const val TAG = 16
  private const val SEALED_KEY = 32 + TAG
  private const val KEY_ALIAS = "vool_capture_v1"
  private val MAGIC = "VCAP1\n".toByteArray()
  private val HEADER = MAGIC.size + NONCE + SEALED_KEY
  private val random = SecureRandom()

  /**
   * One place to change a capture before it is locked away (for example optional compression).
   * Gets the plain file and its media type; returns the file to store (the same one, or a new one,
   * in which case the original is deleted). Null means store exactly what was captured.
   */
  @Volatile var beforeStore: ((File, String) -> File)? = null

  data class Item(val id: String, val mediaType: String, val kind: String, val size: Long, val sha256: String, val takenAt: Long) {
    fun toMap(): Map<String, Any> = mapOf(
      "id" to id, "mediaType" to mediaType, "kind" to kind, "size" to size.toDouble(), "sha256" to sha256,
      "takenAt" to takenAt.toDouble(),
    )
  }

  fun dir(context: Context): File = File(context.noBackupFilesDir, "vool-capture").apply { mkdirs() }

  /** Where the camera or recorder writes before the bytes are locked away. Never shared storage. */
  fun scratch(context: Context, ending: String): File = File(dir(context), "raw-${UUID.randomUUID()}.$ending")

  /** Working files for a change made in beforeStore (for example a compressed copy). Swept at start. */
  fun work(context: Context, ending: String): File = File(dir(context), "work-${UUID.randomUUID()}.$ending")

  /** The media type a stored file really is, from its ending (a change may turn a JPEG into HEIC). */
  fun typeFor(file: File, fallback: String): String = when (file.extension.lowercase()) {
    "jpg", "jpeg" -> "image/jpeg"
    "heic" -> "image/heic"
    "mp4" -> "video/mp4"
    "m4a" -> "audio/mp4"
    "ogg" -> "audio/ogg"
    else -> fallback
  }

  private val saver = Executors.newSingleThreadExecutor()
  private val saving = AtomicInteger()

  /** How many captures are still being locked away (a change such as compression can take a minute). */
  val savingCount: Int get() = saving.get()

  /** Told when a capture has been locked away ("ok" true with the item) or could not be ("ok" false). */
  @Volatile var onStored: ((Map<String, Any>) -> Unit)? = null

  private fun jobFor(raw: File) = File(raw.parentFile, raw.name + ".job")

  /**
   * Hands a fresh capture to the saver and returns at once, so the camera never waits for a change
   * such as compression. A small note next to the capture lets it be finished after the app was closed.
   */
  fun keep(context: Context, captured: File, mediaType: String, kind: String, takenAt: Long) {
    val job = jobFor(captured)
    if (!job.isFile) {
      job.writeText(JSONObject(mapOf("mediaType" to mediaType, "kind" to kind, "takenAt" to takenAt.toDouble())).toString())
    }
    saving.incrementAndGet()
    saver.execute {
      try {
        val item = ingest(context, captured, mediaType, kind, takenAt)
        onStored?.invoke(mapOf("ok" to true) + item.toMap())
      } catch (error: Exception) {
        onStored?.invoke(mapOf("ok" to false, "kind" to kind, "message" to (error.message ?: "not stored")))
      } finally {
        job.delete()
        saving.decrementAndGet()
      }
    }
  }

  /**
   * At start: finish captures whose saving was cut short, and clear working files left behind.
   * Only while nothing is being saved, so a running save is never touched.
   */
  @Synchronized
  fun resume(context: Context) {
    if (saving.get() > 0) return
    val folder = dir(context)
    val now = System.currentTimeMillis()
    val files = folder.listFiles().orEmpty()
    files.filter { it.name.startsWith("raw-") && it.name.endsWith(".job") }.forEach { job ->
      val raw = File(folder, job.name.removeSuffix(".job"))
      try {
        if (!raw.isFile || raw.length() == 0L) { job.delete(); raw.delete(); return@forEach }
        val o = JSONObject(job.readText())
        keep(context, raw, o.getString("mediaType"), o.getString("kind"), o.getDouble("takenAt").toLong())
      } catch (_: Exception) {
        job.delete()
      }
    }
    files.forEach { f ->
      val queued = File(folder, f.name + ".job").isFile
      when {
        f.name.startsWith("work-") || f.name.endsWith(".part") -> f.delete()
        // a raw capture with no note was cut off while recording; a live recording is never this old
        f.name.startsWith("raw-") && !f.name.endsWith(".job") && !queued && now - f.lastModified() > 10 * 60_000L -> f.delete()
      }
    }
  }

  private fun masterKey(): SecretKey {
    val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (store.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    generator.init(
      KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
        .setKeySize(256)
        .build()
    )
    return generator.generateKey()
  }

  private fun aad(id: String, index: Long): ByteArray =
    (id + ":").toByteArray() + ByteBuffer.allocate(8).putLong(index).array()

  private fun readFully(input: InputStream, buffer: ByteArray): Int {
    var total = 0
    while (total < buffer.size) {
      val n = input.read(buffer, total, buffer.size - total)
      if (n < 0) break
      total += n
    }
    return total
  }

  /** Encrypts a freshly captured plain file into the store, then deletes the plain file. */
  @Synchronized
  fun ingest(context: Context, captured: File, mediaType: String, kind: String, takenAt: Long): Item {
    var plain = captured
    try {
      beforeStore?.let { step ->
        // A failed or empty change never loses the capture: the original is stored instead.
        val changed = try { step(plain, mediaType) } catch (_: Exception) { plain }
        if (changed != plain && changed.isFile && changed.length() > 0L) { plain.delete(); plain = changed }
        else if (changed != plain) changed.delete()
      }
      if (!plain.isFile || plain.length() == 0L) throw IllegalStateException("Nothing was captured.")
      val storedType = if (plain != captured) typeFor(plain, mediaType) else mediaType
      val id = UUID.randomUUID().toString().replace("-", "")
      val folder = dir(context)
      val sealing = File(folder, "$id.enc.part")
      val fileKey = ByteArray(32).also { random.nextBytes(it) }
      val digest = MessageDigest.getInstance("SHA-256")
      var size = 0L
      try {
        BufferedOutputStream(FileOutputStream(sealing), 1 shl 16).use { out ->
          out.write(MAGIC)
          val wrap = Cipher.getInstance("AES/GCM/NoPadding")
          wrap.init(Cipher.ENCRYPT_MODE, masterKey())
          wrap.updateAAD(id.toByteArray())
          val sealedKey = wrap.doFinal(fileKey)
          out.write(wrap.iv)
          out.write(sealedKey)
          val key = SecretKeySpec(fileKey, "AES")
          val buffer = ByteArray(PIECE)
          val nonce = ByteArray(NONCE)
          var index = 0L
          FileInputStream(plain).use { input ->
            while (true) {
              val n = readFully(input, buffer)
              if (n <= 0) break
              digest.update(buffer, 0, n)
              size += n
              random.nextBytes(nonce)
              val cipher = Cipher.getInstance("AES/GCM/NoPadding")
              cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(TAG * 8, nonce))
              cipher.updateAAD(aad(id, index))
              out.write(nonce)
              out.write(cipher.doFinal(buffer, 0, n))
              index++
              if (n < PIECE) break
            }
          }
        }
      } finally {
        fileKey.fill(0)
      }
      val sha = digest.digest().joinToString("") { "%02x".format(it) }
      val item = Item(id, storedType, kind, size, sha, takenAt)
      val meta = JSONObject(item.toMap()).toString().toByteArray()
      val metaPart = File(folder, "$id.json.part")
      metaPart.writeBytes(meta)
      if (!sealing.renameTo(File(folder, "$id.enc")) || !metaPart.renameTo(File(folder, "$id.json"))) {
        throw IllegalStateException("The capture could not be stored.")
      }
      return item
    } finally {
      plain.delete()
      if (captured != plain) captured.delete()
    }
  }

  fun list(context: Context): List<Item> {
    val folder = dir(context)
    val now = System.currentTimeMillis()
    folder.listFiles()?.forEach { leftover ->
      // a capture interrupted before it was locked away (the app was killed mid-way)
      val stale = now - leftover.lastModified() > 6 * 3600_000L
      val queued = leftover.name.endsWith(".job") || File(folder, leftover.name + ".job").isFile
      if (stale && !queued && saving.get() == 0 && (leftover.name.endsWith(".part") || leftover.name.startsWith("raw-") || leftover.name.startsWith("work-"))) {
        leftover.delete()
      }
    }
    return folder.listFiles { f -> f.name.endsWith(".json") }.orEmpty().mapNotNull { file ->
      try {
        val o = JSONObject(file.readText())
        val id = o.getString("id")
        if (!File(folder, "$id.enc").isFile) null
        else Item(id, o.getString("mediaType"), o.getString("kind"), o.getDouble("size").toLong(), o.getString("sha256"), o.getDouble("takenAt").toLong())
      } catch (_: Exception) {
        null
      }
    }.sortedBy { it.takenAt }
  }

  private fun checkedId(id: String): String {
    require(Regex("[0-9a-f]{32}").matches(id)) { "Unknown capture." }
    return id
  }

  /** The original bytes from `offset` to the end of the piece that holds it, as base64. */
  fun readFrom(context: Context, rawId: String, offset: Long): String {
    val id = checkedId(rawId)
    val file = File(dir(context), "$id.enc")
    RandomAccessFile(file, "r").use { handle ->
      val magic = ByteArray(MAGIC.size)
      handle.readFully(magic)
      require(magic.contentEquals(MAGIC)) { "This capture cannot be read." }
      val wrapNonce = ByteArray(NONCE).also { handle.readFully(it) }
      val sealedKey = ByteArray(SEALED_KEY).also { handle.readFully(it) }
      val unwrap = Cipher.getInstance("AES/GCM/NoPadding")
      unwrap.init(Cipher.DECRYPT_MODE, masterKey(), GCMParameterSpec(TAG * 8, wrapNonce))
      unwrap.updateAAD(id.toByteArray())
      val fileKey = unwrap.doFinal(sealedKey)
      try {
        val index = offset / PIECE
        val start = HEADER + index * (NONCE + PIECE + TAG)
        require(offset >= 0 && start < handle.length()) { "Nothing left to send." }
        handle.seek(start)
        val nonce = ByteArray(NONCE).also { handle.readFully(it) }
        val length = minOf((PIECE + TAG).toLong(), handle.length() - start - NONCE).toInt()
        val sealed = ByteArray(length).also { handle.readFully(it) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(fileKey, "AES"), GCMParameterSpec(TAG * 8, nonce))
        cipher.updateAAD(aad(id, index))
        val plain = cipher.doFinal(sealed)
        val skip = (offset - index * PIECE).toInt()
        return Base64.encodeToString(plain, skip, plain.size - skip, Base64.NO_WRAP)
      } finally {
        fileKey.fill(0)
      }
    }
  }

  fun remove(context: Context, rawId: String) {
    val id = checkedId(rawId)
    File(dir(context), "$id.enc").delete()
    File(dir(context), "$id.json").delete()
  }
}
