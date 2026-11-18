package systems.vool.capture

import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Base64
import androidx.core.content.FileProvider
import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * VOOL Vault's streams: an item from the computer's protected library arrives here piece by piece (768 KiB each,
 * over VOOL's sealed link) and is kept only sealed, under a key that lives in memory and dies with the stream. The
 * player reads through [read], which waits for a piece that has not come yet and asks for it ([onWant]); so a video
 * plays while the rest is still on its way, from wherever the person jumps to. Nothing plain is written, except a
 * copy the person explicitly saves or shares ([export]), which leaves the Vault.
 */
object VaultStreams {
  const val PIECE = 768 * 1024
  private const val TAG = 16
  private const val WAIT_MS = 45_000L

  class Stream(val id: String, val size: Long, val mediaType: String, val file: File, key: ByteArray) {
    val key = SecretKeySpec(key, "AES")
    val pieces = ((size + PIECE - 1) / PIECE).toInt().coerceAtLeast(1)
    val have = BooleanArray(pieces)
    val lock = Object()
    @Volatile var closed = false
    var cachedIndex = -1
    var cached: ByteArray? = null

    fun length(index: Int): Int = (minOf(size, (index + 1).toLong() * PIECE) - index.toLong() * PIECE).toInt()
  }

  private val streams = ConcurrentHashMap<String, Stream>()
  private val random = SecureRandom()

  /** Asked when the player waits for a piece that has not arrived: (stream id, piece index). */
  @Volatile var onWant: ((String, Int) -> Unit)? = null

  private fun dir(context: Context) = File(context.noBackupFilesDir, "vault-stream").apply { mkdirs() }
  private fun exportDir(context: Context) = File(context.cacheDir, "vault-out").apply { mkdirs() }

  /** Leftovers from a run that ended suddenly: their keys are gone, so they are only noise; and old shared copies. */
  fun sweep(context: Context, exportsOlderThanMs: Long = 10 * 60_000L) {
    dir(context).listFiles()?.forEach { file -> if (streams.values.none { it.file == file }) file.delete() }
    val now = System.currentTimeMillis()
    exportDir(context).listFiles()?.forEach { if (now - it.lastModified() > exportsOlderThanMs) wipe(it) }
  }

  fun open(context: Context, size: Long, mediaType: String): String {
    require(size in 1..(16L * 1024 * 1024 * 1024)) { "size" }
    val raw = ByteArray(16).also(random::nextBytes)
    val id = raw.joinToString("") { "%02x".format(it) }
    val key = ByteArray(32).also(random::nextBytes)
    val file = File(dir(context), id)
    RandomAccessFile(file, "rw").use { it.setLength(0) }
    streams[id] = Stream(id, size, mediaType, file, key)
    key.fill(0)
    return id
  }

  fun get(id: String): Stream? = streams[id]

  private fun cipher(mode: Int, stream: Stream, index: Int): Cipher {
    val nonce = ByteBuffer.allocate(12).putInt(0).putLong(index.toLong()).array()
    return Cipher.getInstance("AES/GCM/NoPadding").apply {
      init(mode, stream.key, GCMParameterSpec(TAG * 8, nonce))
      updateAAD(stream.id.toByteArray())
    }
  }

  /** One piece arrived (base64). Answers how many pieces the stream now holds. */
  fun put(id: String, index: Int, base64: String): Int {
    val stream = streams[id] ?: throw IllegalStateException("closed")
    require(index in 0 until stream.pieces) { "index" }
    val plain = Base64.decode(base64, Base64.NO_WRAP)
    try {
      require(plain.size == stream.length(index)) { "length" }
      val sealed = cipher(Cipher.ENCRYPT_MODE, stream, index).doFinal(plain)
      synchronized(stream.lock) {
        if (stream.closed) throw IllegalStateException("closed")
        if (!stream.have[index]) {
          RandomAccessFile(stream.file, "rw").use { it.seek(index.toLong() * (PIECE + TAG)); it.write(sealed) }
          stream.have[index] = true
        }
        stream.lock.notifyAll()
        return stream.have.count { it }
      }
    } finally {
      plain.fill(0)
    }
  }

  fun held(id: String): List<Int> {
    val stream = streams[id] ?: return emptyList()
    synchronized(stream.lock) { return stream.have.indices.filter { stream.have[it] } }
  }

  /** The plain bytes of one piece, waiting (and asking) for it when it has not come yet; null when it never does. */
  private fun piece(stream: Stream, index: Int): ByteArray? {
    synchronized(stream.lock) {
      if (stream.cachedIndex == index) return stream.cached
      val deadline = System.currentTimeMillis() + WAIT_MS
      while (!stream.have[index]) {
        if (stream.closed) return null
        val left = deadline - System.currentTimeMillis()
        if (left <= 0) return null
        onWant?.invoke(stream.id, index)
        stream.lock.wait(minOf(left, 3_000L))
      }
      val sealed = ByteArray(stream.length(index) + TAG)
      RandomAccessFile(stream.file, "r").use { it.seek(index.toLong() * (PIECE + TAG)); it.readFully(sealed) }
      val plain = cipher(Cipher.DECRYPT_MODE, stream, index).doFinal(sealed)
      stream.cached?.fill(0)
      stream.cached = plain
      stream.cachedIndex = index
      return plain
    }
  }

  /** Reads up to [length] bytes at [position]; -1 at the end, throws when a piece cannot be had. */
  fun read(stream: Stream, position: Long, buffer: ByteArray, offset: Int, length: Int): Int {
    if (position >= stream.size) return -1
    val index = (position / PIECE).toInt()
    val plain = piece(stream, index) ?: throw java.io.IOException("piece $index did not arrive")
    val inside = (position - index.toLong() * PIECE).toInt()
    val count = minOf(length, plain.size - inside)
    System.arraycopy(plain, inside, buffer, offset, count)
    return count
  }

  fun close(id: String) {
    val stream = streams.remove(id) ?: return
    synchronized(stream.lock) {
      stream.closed = true
      stream.cached?.fill(0)
      stream.cached = null
      stream.lock.notifyAll()
    }
    stream.file.delete()
  }

  fun closeAll() { streams.keys.toList().forEach(::close) }

  private fun safeName(name: String): String =
    name.replace(Regex("[\\\\/:*?\"<>|\\u0000-\\u001f]"), "_").take(120).ifBlank { "vool-item" }

  /** A plain copy of a whole stream, for saving or sharing at the person's request. Throws when pieces are missing. */
  private fun plainCopy(context: Context, id: String, name: String): File {
    val stream = streams[id] ?: throw IllegalStateException("closed")
    val out = File(exportDir(context), safeName(name))
    RandomAccessFile(out, "rw").use { file ->
      file.setLength(0)
      for (index in 0 until stream.pieces) {
        val plain = piece(stream, index) ?: throw IllegalStateException("incomplete")
        file.write(plain)
      }
    }
    return out
  }

  private fun wipe(file: File) {
    try {
      RandomAccessFile(file, "rw").use { handle ->
        val zeros = ByteArray(64 * 1024)
        var left = handle.length()
        while (left > 0) { val n = minOf(left, zeros.size.toLong()).toInt(); handle.write(zeros, 0, n); left -= n }
        handle.fd.sync()
      }
    } catch (_: Exception) {}
    file.delete()
  }

  /** Shares a copy through Android's share sheet (messages, mail, social apps). */
  fun share(context: Context, id: String, name: String, title: String) {
    val stream = streams[id] ?: throw IllegalStateException("closed")
    val file = plainCopy(context, id, name)
    val uri = FileProvider.getUriForFile(context, context.packageName + ".FileSystemFileProvider", file)
    val send = Intent(Intent.ACTION_SEND).setType(stream.mediaType).putExtra(Intent.EXTRA_STREAM, uri)
      .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    context.startActivity(Intent.createChooser(send, title).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION))
  }

  /** One share sheet for several whole streams (each a plain copy, swept from the phone's cache within minutes). */
  fun shareMany(context: Context, ids: List<String>, names: List<String>, title: String) {
    require(ids.isNotEmpty() && ids.size == names.size) { "nothing" }
    val uris = ArrayList<Uri>()
    val types = mutableSetOf<String>()
    ids.forEachIndexed { at, id ->
      val stream = streams[id] ?: throw IllegalStateException("closed")
      types.add(stream.mediaType)
      uris.add(FileProvider.getUriForFile(context, context.packageName + ".FileSystemFileProvider", plainCopy(context, id, names[at])))
    }
    val heads = types.map { it.substringBefore('/') }.toSet()
    val type = when { types.size == 1 -> types.first(); heads.size == 1 -> heads.first() + "/*"; else -> "*/*" }
    val send = Intent(Intent.ACTION_SEND_MULTIPLE).setType(type).putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris)
      .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    context.startActivity(Intent.createChooser(send, title).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION))
  }

  /** Saves a copy into the phone's own Pictures, Movies or Music (folder "VOOL"), where the gallery sees it. Answers "folder|link". */
  fun save(context: Context, id: String, name: String): String {
    val stream = streams[id] ?: throw IllegalStateException("closed")
    val file = plainCopy(context, id, name)
    try {
      if (Build.VERSION.SDK_INT < 29) throw IllegalStateException("unsupported")
      val (collection, folder) = when {
        stream.mediaType.startsWith("video/") -> MediaStore.Video.Media.EXTERNAL_CONTENT_URI to Environment.DIRECTORY_MOVIES
        stream.mediaType.startsWith("audio/") -> MediaStore.Audio.Media.EXTERNAL_CONTENT_URI to Environment.DIRECTORY_MUSIC
        else -> MediaStore.Images.Media.EXTERNAL_CONTENT_URI to Environment.DIRECTORY_PICTURES
      }
      val values = ContentValues().apply {
        put(MediaStore.MediaColumns.DISPLAY_NAME, file.name)
        put(MediaStore.MediaColumns.MIME_TYPE, stream.mediaType)
        put(MediaStore.MediaColumns.RELATIVE_PATH, "$folder/VOOL")
        put(MediaStore.MediaColumns.IS_PENDING, 1)
      }
      val resolver = context.contentResolver
      val target: Uri = resolver.insert(collection, values) ?: throw IllegalStateException("refused")
      resolver.openOutputStream(target)!!.use { out -> file.inputStream().use { it.copyTo(out) } }
      values.clear(); values.put(MediaStore.MediaColumns.IS_PENDING, 0)
      resolver.update(target, values, null, null)
      // Where it went, and the gallery's own link to it so the phone can open it there.
      return "$folder/VOOL|$target"
    } finally {
      wipe(file)
    }
  }
}
