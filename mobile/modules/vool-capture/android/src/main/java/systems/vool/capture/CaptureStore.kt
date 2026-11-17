package systems.vool.capture

import android.content.Context
import android.os.Process
import android.os.SystemClock
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
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
 * File layout: "VCAP2\n" | sealed-key nonce (12) | sealed key (48) | piece... where each piece is
 * nonce (12) | ciphertext | tag (16).
 *
 * Erasing: since VCAP2 every capture's file key is sealed by its OWN Keystore key, and removing a
 * capture destroys that Keystore key first (crypto-erasure). Whatever the flash memory keeps of the
 * deleted file can then never be opened again, even by VOOL. Older VCAP1 files (sealed by the one
 * shared key) are still read and sent. After removal a small "gone" note (hash only) waits until the
 * computer has recorded it, so the computer keeps an auditable record of the deletion.
 */
object CaptureStore {
  const val PIECE = 768 * 1024
  private const val NONCE = 12
  private const val TAG = 16
  private const val SEALED_KEY = 32 + TAG
  private const val KEY_ALIAS = "vool_capture_v1"
  private val MAGIC_V1 = "VCAP1\n".toByteArray()
  private val MAGIC = "VCAP2\n".toByteArray()
  private const val FILE_ALIAS = "vool_capture_f_"
  private const val QUIET_MS = 2_500L
  private const val WIPE_LIMIT = 256L * 1024 * 1024
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

  // One capture at a time, at background priority, so the camera and the screen stay smooth.
  private val saver = Executors.newSingleThreadExecutor { work ->
    Thread({ Process.setThreadPriority(Process.THREAD_PRIORITY_BACKGROUND); work.run() }, "vool-capture-saver")
  }
  @Volatile private var lastShot = 0L

  /** The shutter was just pressed: heavy saving work waits for a quiet moment. */
  fun shot() { lastShot = SystemClock.elapsedRealtime() }

  /** While shots come in a row, let the camera have the phone; start saving work once it is quiet. */
  private fun waitForQuiet() {
    val until = SystemClock.elapsedRealtime() + 20_000
    while (SystemClock.elapsedRealtime() - lastShot < QUIET_MS && SystemClock.elapsedRealtime() < until) {
      Thread.sleep(200)
    }
  }
  private val saving = AtomicInteger()

  /** How many captures are still being locked away (a change such as compression can take a minute). */
  val savingCount: Int get() = saving.get()

  /** Told when a capture has been locked away ("ok" true with the item) or could not be ("ok" false). */
  @Volatile var onStored: ((Map<String, Any>) -> Unit)? = null

  private fun jobFor(raw: File) = File(raw.parentFile, raw.name + ".job")

  // Sealing a fresh capture is quick and never waits: one thread at normal priority, apart from the saver.
  private val sealer = Executors.newSingleThreadExecutor { work -> Thread(work, "vool-capture-sealer") }
  private const val LOG = "VoolCapture"

  /**
   * Hands a fresh capture to the store and returns at once, so the camera never waits.
   *
   * The plain file is sealed straight away into a "held" file under its own Keystore key and then
   * overwritten and deleted, so shots waiting in a queue never sit on the phone as plain files. Only
   * the step that changes a capture (filter, compression) opens one held capture at a time, in a
   * working file that is wiped right after. A note next to each step lets everything be finished
   * after the app was closed or killed: nothing is lost, and plain leftovers are wiped at the next start.
   */
  fun keep(context: Context, captured: File, mediaType: String, kind: String, takenAt: Long, filter: String = "none") {
    val note = jobFor(captured)
    if (!note.isFile) note.writeText(jobText(mediaType, kind, takenAt, filter, captured.extension))
    saving.incrementAndGet()
    sealer.execute {
      val id = try { hold(context, captured, mediaType, kind, takenAt, filter) } catch (error: Exception) {
        Log.w(LOG, "could not seal a fresh capture at once; it is saved directly")
        null
      }
      saver.execute {
        try {
          val item = if (id != null) finishHeld(context, id) else {
            waitForQuiet()
            if (filter != "none") PhotoFilters.apply(captured, filter)
            ingest(context, captured, mediaType, kind, takenAt).also { note.delete() }
          }
          onStored?.invoke(mapOf("ok" to true) + item.toMap())
        } catch (error: Exception) {
          onStored?.invoke(mapOf("ok" to false, "kind" to kind, "message" to (error.message ?: "not stored")))
        } finally {
          saving.decrementAndGet()
        }
      }
    }
  }

  private fun jobText(mediaType: String, kind: String, takenAt: Long, filter: String, ending: String): String =
    JSONObject(mapOf("mediaType" to mediaType, "kind" to kind, "takenAt" to takenAt.toDouble(), "filter" to filter, "ending" to ending)).toString()

  private fun holdKey(id: String) = "h$id"

  /** Seals a plain capture into hold-<id>.enc under its own Keystore key, then wipes the plain file. */
  private fun hold(context: Context, plain: File, mediaType: String, kind: String, takenAt: Long, filter: String): String {
    val started = SystemClock.elapsedRealtime()
    if (!plain.isFile || plain.length() == 0L) throw IllegalStateException("Nothing was captured.")
    val folder = dir(context)
    val id = UUID.randomUUID().toString().replace("-", "")
    val job = File(folder, "hold-$id.job")
    val part = File(folder, "hold-$id.enc.part")
    job.writeText(jobText(mediaType, kind, takenAt, filter, plain.extension.ifEmpty { "bin" }))
    try {
      sealFile(plain, part, holdKey(id))
      if (!part.renameTo(File(folder, "hold-$id.enc"))) throw IllegalStateException("not held")
    } catch (error: Exception) {
      part.delete(); destroyKey(holdKey(id)); job.delete()
      throw error
    }
    wipe(plain)
    jobFor(plain).delete()
    Log.i(LOG, "held: $kind sealed in ${SystemClock.elapsedRealtime() - started} ms, plain left 0")
    return id
  }

  /** Opens one held capture, applies its change (filter, compression), locks it away, drops the held copy. */
  private fun finishHeld(context: Context, id: String): Item {
    val folder = dir(context)
    val job = File(folder, "hold-$id.job")
    val o = JSONObject(job.readText())
    // Already locked away before the app was stopped: only the held copy is left to drop.
    stored(folder, id)?.let { done -> dropHold(folder, id); return done }
    waitForQuiet()
    val work = work(context, o.optString("ending", "bin"))
    try {
      unsealFile(File(folder, "hold-$id.enc"), holdKey(id), work)
      val filter = o.optString("filter", "none")
      if (filter != "none") PhotoFilters.apply(work, filter)
      val item = ingest(context, work, o.getString("mediaType"), o.getString("kind"), o.getDouble("takenAt").toLong(), fixedId = id)
      dropHold(folder, id)
      Log.i(LOG, "stored: ${item.kind}, held left ${heldCount(folder)}")
      return item
    } finally {
      if (work.exists()) wipe(work)
    }
  }

  private fun stored(folder: File, id: String): Item? = try {
    if (!File(folder, "$id.enc").isFile) null else {
      val o = JSONObject(File(folder, "$id.json").readText())
      Item(id, o.getString("mediaType"), o.getString("kind"), o.getDouble("size").toLong(), o.getString("sha256"), o.getDouble("takenAt").toLong())
    }
  } catch (_: Exception) { null }

  /** The held copy's own key is destroyed first, so what flash keeps of it can never be opened. */
  private fun dropHold(folder: File, id: String) {
    destroyKey(holdKey(id))
    File(folder, "hold-$id.enc").delete()
    File(folder, "hold-$id.job").delete()
  }

  private fun heldCount(folder: File) = folder.listFiles { f -> f.name.startsWith("hold-") && f.name.endsWith(".enc") }.orEmpty().size

  private fun isPlainLeftover(name: String) =
    name.startsWith("raw-") && !name.endsWith(".job") || name.startsWith("work-") || name.endsWith(".tmp")

  /** Overwrites a plain file before deleting it (best effort: flash memory may keep old blocks). */
  private fun wipe(file: File) {
    try {
      val length = file.length()
      if (length in 1..WIPE_LIMIT) RandomAccessFile(file, "rw").use { handle ->
        val zeros = ByteArray(1 shl 16)
        var left = length
        while (left > 0) { val n = minOf(left, zeros.size.toLong()).toInt(); handle.write(zeros, 0, n); left -= n }
        handle.fd.sync()
      }
    } catch (_: Exception) {}
    file.delete()
  }

  /**
   * At start: wipe plain leftovers, seal any plain capture that was never sealed, and finish every
   * held capture. Only while nothing is being saved, so a running save is never touched.
   */
  @Synchronized
  fun resume(context: Context) {
    if (saving.get() > 0) return
    val folder = dir(context)
    val now = System.currentTimeMillis()
    val files = folder.listFiles().orEmpty()
    val plainBefore = files.count { isPlainLeftover(it.name) }
    val heldBefore = files.count { it.name.startsWith("hold-") && it.name.endsWith(".enc") }
    var rescued = 0
    // 1. Half-done work: wiped. A held copy is only ever complete or a .part, never plain.
    files.forEach { f ->
      when {
        f.name.startsWith("work-") || f.name.endsWith(".tmp") -> wipe(f)
        f.name.endsWith(".part") -> f.delete()
      }
    }
    // 2. Held captures: finished in order. A note without its sealed file belonged to a seal cut short.
    files.filter { it.name.startsWith("hold-") && it.name.endsWith(".job") }.forEach { job ->
      val id = job.name.removePrefix("hold-").removeSuffix(".job")
      if (!File(folder, "hold-$id.enc").isFile) { job.delete(); return@forEach }
      saving.incrementAndGet()
      saver.execute {
        try {
          val item = finishHeld(context, id)
          onStored?.invoke(mapOf("ok" to true) + item.toMap())
        } catch (error: Exception) {
          Log.w(LOG, "a held capture could not be finished; it stays held for the next start")
        } finally {
          saving.decrementAndGet()
        }
      }
    }
    sweepKeys(context)
    // 3. Plain captures that were never sealed: sealed now (a photo is kept only if it is a whole photo).
    files.filter { it.name.startsWith("raw-") && !it.name.endsWith(".job") && it.isFile }.forEach { raw ->
      val job = jobFor(raw)
      try {
        when {
          raw.length() == 0L -> { raw.delete(); job.delete() }
          job.isFile -> {
            val o = JSONObject(job.readText())
            keep(context, raw, o.getString("mediaType"), o.getString("kind"), o.getDouble("takenAt").toLong(), o.optString("filter", "none")); rescued++
          }
          raw.extension == "jpg" && wholeJpeg(raw) -> { keep(context, raw, "image/jpeg", "photo", raw.lastModified()); rescued++ }
          raw.extension == "jpg" -> wipe(raw)
          // a recording cut off mid-way cannot be played; a live recording is never this old
          now - raw.lastModified() > 10 * 60_000L -> wipe(raw)
        }
      } catch (_: Exception) {
        job.delete()
      }
    }
    files.filter { it.name.startsWith("raw-") && it.name.endsWith(".job") && !File(folder, it.name.removeSuffix(".job")).isFile }.forEach { it.delete() }
    // Logged after every rescued capture has been sealed (the sealer works in order).
    sealer.execute {
      val left = folder.listFiles().orEmpty().count { isPlainLeftover(it.name) }
      Log.i(LOG, "start: plain found $plainBefore, held found $heldBefore, plain sealed now $rescued, plain left $left")
    }
  }

  private fun wholeJpeg(file: File): Boolean = try {
    RandomAccessFile(file, "r").use { h ->
      val head = ByteArray(2).also { h.readFully(it) }
      h.seek(file.length() - 2)
      val tail = ByteArray(2).also { h.readFully(it) }
      head[0] == 0xFF.toByte() && head[1] == 0xD8.toByte() && tail[0] == 0xFF.toByte() && tail[1] == 0xD9.toByte()
    }
  } catch (_: Exception) { false }

  private fun masterKey(): SecretKey = keystoreKey(KEY_ALIAS, create = true)!!

  /** The Keystore key that seals one capture's file key (VCAP2). Null when it was destroyed. */
  private fun fileSealKey(id: String, create: Boolean): SecretKey? = keystoreKey(FILE_ALIAS + id, create)

  private fun keystore(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

  private fun keystoreKey(alias: String, create: Boolean): SecretKey? {
    val store = keystore()
    (store.getKey(alias, null) as? SecretKey)?.let { return it }
    if (!create) return null
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    generator.init(
      KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
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

  /** Seals a plain file (VCAP2) under the Keystore key named [keyName]; returns its size and SHA-256. */
  private fun sealFile(plain: File, out: File, keyName: String): Pair<Long, String> {
    val fileKey = ByteArray(32).also { random.nextBytes(it) }
    val digest = MessageDigest.getInstance("SHA-256")
    var size = 0L
    try {
      val raw = FileOutputStream(out)
      val stream = BufferedOutputStream(raw, 1 shl 16)
      try {
        stream.write(MAGIC)
        val wrap = Cipher.getInstance("AES/GCM/NoPadding")
        wrap.init(Cipher.ENCRYPT_MODE, fileSealKey(keyName, create = true))
        wrap.updateAAD(keyName.toByteArray())
        val sealedKey = wrap.doFinal(fileKey)
        stream.write(wrap.iv)
        stream.write(sealedKey)
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
            cipher.updateAAD(aad(keyName, index))
            stream.write(nonce)
            stream.write(cipher.doFinal(buffer, 0, n))
            index++
            if (n < PIECE) break
          }
        }
        buffer.fill(0)
        stream.flush()
        raw.fd.sync()
      } finally {
        stream.close()
      }
    } finally {
      fileKey.fill(0)
    }
    return size to digest.digest().joinToString("") { "%02x".format(it) }
  }

  /** Opens a whole VCAP2 file sealed under [keyName] into [out]. */
  private fun unsealFile(sealed: File, keyName: String, out: File) {
    RandomAccessFile(sealed, "r").use { handle ->
      val magic = ByteArray(MAGIC.size).also { handle.readFully(it) }
      require(magic.contentEquals(MAGIC)) { "This capture cannot be read." }
      val sealer = fileSealKey(keyName, create = false) ?: throw IllegalStateException("This capture was erased.")
      val wrapNonce = ByteArray(NONCE).also { handle.readFully(it) }
      val sealedKey = ByteArray(SEALED_KEY).also { handle.readFully(it) }
      val unwrap = Cipher.getInstance("AES/GCM/NoPadding")
      unwrap.init(Cipher.DECRYPT_MODE, sealer, GCMParameterSpec(TAG * 8, wrapNonce))
      unwrap.updateAAD(keyName.toByteArray())
      val fileKey = unwrap.doFinal(sealedKey)
      try {
        val key = SecretKeySpec(fileKey, "AES")
        BufferedOutputStream(FileOutputStream(out), 1 shl 16).use { stream ->
          var index = 0L
          var at = HEADER.toLong()
          val total = handle.length()
          while (at < total) {
            val nonce = ByteArray(NONCE).also { handle.readFully(it) }
            val length = minOf((PIECE + TAG).toLong(), total - at - NONCE).toInt()
            val piece = ByteArray(length).also { handle.readFully(it) }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG * 8, nonce))
            cipher.updateAAD(aad(keyName, index))
            val plain = cipher.doFinal(piece)
            stream.write(plain)
            plain.fill(0)
            at += NONCE + length
            index++
          }
        }
      } finally {
        fileKey.fill(0)
      }
    }
  }

  /** Encrypts a plain capture into the store, then wipes the plain file. */
  @Synchronized
  fun ingest(context: Context, captured: File, mediaType: String, kind: String, takenAt: Long, fixedId: String? = null): Item {
    var plain = captured
    var sealedId: String? = null
    try {
      beforeStore?.let { step ->
        // A failed or empty change never loses the capture: the original is stored instead.
        val changed = try { step(plain, mediaType) } catch (_: Exception) { plain }
        if (changed != plain && changed.isFile && changed.length() > 0L) { wipe(plain); plain = changed }
        else if (changed != plain) wipe(changed)
      }
      if (!plain.isFile || plain.length() == 0L) throw IllegalStateException("Nothing was captured.")
      val storedType = if (plain != captured) typeFor(plain, mediaType) else mediaType
      val id = fixedId ?: UUID.randomUUID().toString().replace("-", "")
      sealedId = id
      val folder = dir(context)
      val sealing = File(folder, "$id.enc.part")
      val (size, sha) = sealFile(plain, sealing, id)
      val item = Item(id, storedType, kind, size, sha, takenAt)
      val meta = JSONObject(item.toMap()).toString().toByteArray()
      val metaPart = File(folder, "$id.json.part")
      FileOutputStream(metaPart).use { it.write(meta); it.fd.sync() }
      if (!sealing.renameTo(File(folder, "$id.enc")) || !metaPart.renameTo(File(folder, "$id.json"))) {
        sealing.delete(); metaPart.delete(); destroyKey(id)
        throw IllegalStateException("The capture could not be stored.")
      }
      sealedId = null
      return item
    } finally {
      if (plain.exists()) wipe(plain)
      if (captured != plain && captured.exists()) wipe(captured)
      // failed half way: nothing usable was kept, so its key goes too
      sealedId?.let { id -> File(dir(context), "$id.enc.part").delete(); destroyKey(id) }
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
      val sealer = when {
        magic.contentEquals(MAGIC) -> fileSealKey(id, create = false) ?: throw IllegalStateException("This capture was erased.")
        magic.contentEquals(MAGIC_V1) -> masterKey()
        else -> throw IllegalArgumentException("This capture cannot be read.")
      }
      val wrapNonce = ByteArray(NONCE).also { handle.readFully(it) }
      val sealedKey = ByteArray(SEALED_KEY).also { handle.readFully(it) }
      val unwrap = Cipher.getInstance("AES/GCM/NoPadding")
      unwrap.init(Cipher.DECRYPT_MODE, sealer, GCMParameterSpec(TAG * 8, wrapNonce))
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

  /** Destroys one capture's own Keystore key. True when it is gone (or never existed). */
  private fun destroyKey(id: String): Boolean = try {
    val store = keystore()
    if (store.containsAlias(FILE_ALIAS + id)) store.deleteEntry(FILE_ALIAS + id)
    !store.containsAlias(FILE_ALIAS + id)
  } catch (_: Exception) {
    false
  }

  /**
   * Removes a capture the computer has confirmed: its key is destroyed first (crypto-erasure), the
   * sealed key in the file is overwritten, then the files are deleted. A "gone" note with the hash and
   * whether the key was destroyed waits for the computer's record (see gone / goneRecorded).
   */
  fun remove(context: Context, rawId: String) {
    val id = checkedId(rawId)
    val folder = dir(context)
    val enc = File(folder, "$id.enc")
    val meta = File(folder, "$id.json")
    val sha = try { JSONObject(meta.readText()).getString("sha256") } catch (_: Exception) { "" }
    var v2 = false
    try {
      RandomAccessFile(enc, "rw").use { handle ->
        val magic = ByteArray(MAGIC.size)
        handle.readFully(magic)
        v2 = magic.contentEquals(MAGIC)
        handle.seek(MAGIC.size.toLong())
        handle.write(ByteArray(NONCE + SEALED_KEY))
        handle.fd.sync()
      }
    } catch (_: Exception) {}
    val keyDestroyed = v2 && destroyKey(id)
    enc.delete()
    meta.delete()
    if (sha.matches(Regex("[0-9a-f]{64}"))) {
      val note = JSONObject(mapOf("sha256" to sha, "keyDestroyed" to keyDestroyed, "removedAt" to System.currentTimeMillis().toDouble()))
      File(folder, "gone-$sha.json").writeText(note.toString())
    }
  }

  /** Deletions the computer has not recorded yet. */
  fun gone(context: Context): List<Map<String, Any>> = dir(context).listFiles { f -> f.name.startsWith("gone-") && f.name.endsWith(".json") }
    .orEmpty().mapNotNull { file ->
      try {
        val o = JSONObject(file.readText())
        mapOf("sha256" to o.getString("sha256"), "keyDestroyed" to o.getBoolean("keyDestroyed"), "removedAt" to o.getDouble("removedAt"))
      } catch (_: Exception) { file.delete(); null }
    }

  /** The computer recorded this deletion. */
  fun goneRecorded(context: Context, sha256: String) {
    if (sha256.matches(Regex("[0-9a-f]{64}"))) File(dir(context), "gone-$sha256.json").delete()
  }

  /** Keystore keys whose capture no longer exists (an interrupted removal): destroyed. */
  fun sweepKeys(context: Context) {
    try {
      val folder = dir(context)
      val store = keystore()
      for (alias in store.aliases().toList()) {
        if (!alias.startsWith(FILE_ALIAS)) continue
        val id = alias.removePrefix(FILE_ALIAS)
        val name = if (id.startsWith("h")) "hold-${id.removePrefix("h")}" else id
        val inUse = File(folder, "$name.enc").isFile || File(folder, "$name.enc.part").isFile || (id.startsWith("h") && File(folder, "$name.job").isFile)
        if (!inUse) store.deleteEntry(alias)
      }
    } catch (_: Exception) {}
  }
}
